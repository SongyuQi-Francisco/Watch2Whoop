/* engine: inputs -> daily_scores rows, day by day. Port of score_days() and its helpers in body_engine scores/engine.py.

Every run computes from the scoring start (the user's first day with a resting heart rate) up to `last`, so results depend
only on the data: CTL / ATL, the 60-day baselines, 14 nights of sleep debt and the 365-day HRmax window all need earlier history.
The flag texts are the server's (Chinese, read by the AI), so a row computed here is the same row the server would store.

The phone's seeded run (not in Python; the server always starts at the scoring origin): the phone keeps raw samples for a
few weeks only, so it starts at a later `first` and carries the history in through the inputs:
- inp.hr_hist {date: {bpm: count}}: the heart-rate histogram of a day without raw samples (the 365-day HRmax window);
- inp.seed_rows: the stored daily_scores rows of the days before `first`: their HRV / resting HR / respiration / wrist
  temperature / sleep performance feed the 60-day baselines, their strain the next night's sleep need, their CTL / ATL
  (details.load_state when the phone wrote the row: unrounded) and TRIMPs the load; the actual sleep of those nights is
  recomputed from inp.nights (exact), and the stored value is used only when the night is not there.
The stored values are rounded (HRV ln 4 decimals, resting HR 0.1, CTL 0.1 …), so a seeded row can differ from the full
recompute by a rounding step; nativesync/params.CHECK_TOLERANCE on the server allows exactly that (test: golden.test.js).
*/
OBS.engine = (function (U, P, R, S, L, I) {
  "use strict";
  var COLUMNS = ["sleep_score", "sleep_need_min", "sleep_debt_min", "sleep_performance", "sleep_actual_min",
    "hrv_kind", "hrv_night_ln", "rhr_night", "resp_rate_night", "recovery",
    "trimp", "strain", "hr_max", "muscle_load", "ctl", "atl", "tsb", "flags", "details", "algo_version"];
  var r = U.r;

  // Python str() of a float (repr): integers get ".0"
  function pyFloat(x) { return Number.isInteger(x) ? x.toFixed(1) : String(x); }

  function between(samples, times, start, end) {
    return samples.slice(U.bisectLeft(times, start), U.bisectRight(times, end));
  }

  function wristTemps(temps, times, night, prevEnd) {
    var near = between(temps, times, night.start - P.WRIST_TEMP_PAD, night.end + P.WRIST_TEMP_PAD);
    if (near.length) return near;
    var lo = night.start - P.WRIST_TEMP_LEAD;
    if (prevEnd !== null && prevEnd !== undefined) lo = Math.max(lo, prevEnd + P.WRIST_TEMP_PAD + 1);
    return lo < night.start - P.WRIST_TEMP_PAD ? between(temps, times, lo, night.start - P.WRIST_TEMP_PAD) : [];
  }

  function SegIndex(segments) {
    this.all = segments;
    this.starts = segments.map(function (s) { return s[0]; });
    this.max_len = segments.reduce(function (m, s) { return Math.max(m, s[1] - s[0]); }, 0);
  }
  SegIndex.prototype.starting = function (lo, hi) { return this.all.slice(U.bisectLeft(this.starts, lo), U.bisectRight(this.starts, hi)); };
  SegIndex.prototype.within = function (start, end) { return this.starting(start, end).filter(function (s) { return s[1] <= end; }); };

  /** Sleep latency ≈ the awake segments right before the first asleep one; efficiency = asleep / (latency start → wake). */
  function latencyEfficiency(segments, night, index) {
    var t = night.start, latency = 0.0, lo = night.start - 3 * U.US_HOUR;
    var near = index ? index.starting(lo - index.max_len, night.start) : segments;
    var xs = near.filter(function (s) { return s[1] <= night.start && s[1] >= lo; }).reverse();
    for (var i = 0; i < xs.length; i++) {
      var a = xs[i][0], b = xs[i][1];
      if (xs[i][2] !== "awake" || (t - b) / 1e6 > 60) break;
      latency += ((b - a) / 1e6) / 60;
      t = a;
    }
    var span = ((night.end - t) / 1e6) / 60;
    return { latency_min: latency ? U.round(latency, 1) : null, efficiency: span > 0 ? U.round(Math.min(night.asleep_min / span, 1.0), 3) : null };
  }

  function isSorted(segs) {
    for (var i = 0; i + 1 < segs.length; i++) if (U.cmp(segs[i], segs[i + 1]) > 0) return false;
    return segs.every(function (s) { return s[0] <= s[1]; });
  }

  /**
   * Compute the scores day by day for [first, last] (local dates). Pure: uses only the data in inp
   * (inputs.build / inputs.normalize output: instants in microseconds, series sorted). today: local date (default: now).
   */
  function has(v) { return v !== null && v !== undefined; }

  function score_days(inp, first, last, today, opts) {
    opts = opts || {};
    var tz = U.tzOf(inp.tz);
    today = today || U.localDate(Date.now() * 1000, tz);
    var shortcut = {};
    (inp.shortcut_dates || []).forEach(function (d) { shortcut[d] = true; });
    var nonWatch = {};
    (inp.non_watch_dates || []).forEach(function (d) { nonWatch[d] = true; });

    // ---- sleep: the longest watch sleep per wake date is the main sleep, the rest are naps
    var byWake = new Map();
    inp.nights.forEach(function (n) { if (!byWake.has(n.wake_date)) byWake.set(n.wake_date, []); byWake.get(n.wake_date).push(n); });
    var main = {}, naps = [];
    byWake.forEach(function (ns, d) { main[d] = U.maxBy(ns, function (n) { return n.asleep_min; }); });
    byWake.forEach(function (ns, d) { ns.forEach(function (n) { if (n !== main[d]) naps.push(n); }); });
    var byStart = function (a, b) { return a.start - b.start; };
    naps.sort(byStart);
    // Python sorts main.values() (wake-date insertion order) by start; a stable sort keeps that order for ties
    var mainsSorted = Array.from(byWake.keys()).map(function (d) { return main[d]; }).sort(byStart);
    var prevMainEnd = {};
    for (var i = 0; i + 1 < mainsSorted.length; i++) prevMainEnd[mainsSorted[i + 1].wake_date] = mainsSorted[i].end;
    /** Naps between the previous main sleep (at most 24 h back) and this night: they count towards its actual sleep. */
    function napMinutes(night) {
      var prevMains = mainsSorted.filter(function (n) { return n.end <= night.start; });
      var lo = Math.max(prevMains.length ? prevMains[prevMains.length - 1].end : night.start - 24 * U.US_HOUR,
        night.start - 24 * U.US_HOUR);
      return U.pySum(naps.filter(function (n) { return n.start >= lo && n.end <= night.start; }).map(function (n) { return n.asleep_min; }));
    }

    // ---- strain: heart rate by local date; the minutes each sample represents over the whole timeline
    var hrTimes = inp.hr.map(function (s) { return s[0]; });
    var hrMinutes = L.sample_minutes(hrTimes);
    var hrByDay = new Map(), hourOf = new Array(hrTimes.length);
    for (i = 0; i < hrTimes.length; i++) {
      var lp = U.local(hrTimes[i], tz);
      hourOf[i] = lp.hour;
      if (!hrByDay.has(lp.date)) hrByDay.set(lp.date, []);
      hrByDay.get(lp.date).push(i);
    }
    var histByDay = new Map();
    hrByDay.forEach(function (idx, d) { histByDay.set(d, L.counter(idx.map(function (j) { return U.round(inp.hr[j][1]); }))); });
    // seeded run: a day the phone keeps no raw heart rate for counts with its stored histogram
    Object.keys(inp.hr_hist || {}).forEach(function (d) { if (!histByDay.has(d)) histByDay.set(d, inp.hr_hist[d]); });
    var hrWindow = new L.HrHistogram();
    U.dateRange(U.addDays(first, -P.HRMAX_WINDOW_DAYS), U.addDays(first, -1)).forEach(function (d) {
      if (histByDay.has(d)) hrWindow.add(histByDay.get(d));
    });

    var actual = {}, strainByDay = {};
    var feats = { hrv: {}, rhr: {}, sleep_performance: {}, resp_rate: {}, temp: {} };
    feats.rhr_daily = Object.assign({}, inp.daily_rhr || {});
    var rows = [];
    var times = {};
    ["hr", "resp", "temp", "spo2"].forEach(function (k) { times[k] = inp[k].map(function (s) { return s[0]; }); });
    var segIndex = isSorted(inp.segments) ? new SegIndex(inp.segments) : null;

    // seeded run: the stored rows of the days before `first` stand in for the days not recomputed
    var seed = {};
    (inp.seed_rows || []).forEach(function (row) { if (row && row.date && row.date < first) seed[row.date] = row; });
    var seedDays = Object.keys(seed).sort();
    seedDays.forEach(function (d) {
      var row = seed[d], fl = row.flags || {}, rec = (row.details || {}).recovery || {};
      if (has(row.hrv_night_ln)) feats.hrv[d] = row.hrv_night_ln;
      if (has(row.rhr_night)) feats[fl.rhr_from_daily ? "rhr_daily" : "rhr"][d] = row.rhr_night;
      if (has(row.resp_rate_night)) feats.resp_rate[d] = row.resp_rate_night;
      if (has(rec.wrist_temp)) feats.temp[d] = rec.wrist_temp;
      if (has(row.sleep_performance)) feats.sleep_performance[d] = row.sleep_performance;
      if (has(row.strain)) strainByDay[d] = row.strain;
      if (has(row.sleep_actual_min)) actual[d] = main[d] ? main[d].asleep_min + napMinutes(main[d]) : row.sleep_actual_min;
    });

    U.dateRange(first, last).forEach(function (d) {
      var flags = {}, details = {}, row = {};
      COLUMNS.forEach(function (c) { row[c] = null; });
      row.date = d; row.hrv_kind = inp.hrv_kind; row.algo_version = P.ALGO_VERSION;
      details.hrv_kind = inp.hrv_kind;
      if (d === today) flags.day_in_progress = "今天还没过完，负荷是到目前为止的";

      // ================= strain
      if (histByDay.has(d)) hrWindow.add(histByDay.get(d));
      var old = U.addDays(d, -P.HRMAX_WINDOW_DAYS);
      if (histByDay.has(old)) hrWindow.add(histByDay.get(old), -1);
      var idx = hrByDay.get(d) || [];
      if (idx.length) {
        var age = inp.birth_date ? L.age_on(inp.birth_date, d) : null;
        var observed = hrWindow.percentile(P.HRMAX_PERCENTILE);
        var hrMax = age !== null ? L.hrmax(age, observed) : observed;
        var tz5 = L.edwards_trimp(idx.map(function (j) { return inp.hr[j]; }), idx.map(function (j) { return hrMinutes[j]; }), hrMax);
        var trimp = tz5[0], zones = tz5[1];
        var hourSet = {};
        idx.forEach(function (j) { hourSet[hourOf[j]] = true; });
        var hours = Object.keys(hourSet).length;
        details.load = { zone_minutes: zones.map(function (z) { return U.round(z, 1); }), hr_samples: idx.length, hr_hours: hours,
          trimp_recorded: U.round(trimp, 1), hrmax_observed_p995: observed,
          hrmax_formula: age !== null ? U.round(L.hrmax_formula(age), 1) : null };
        row.hr_max = U.round(hrMax, 1);
        if (hours >= P.MIN_HR_HOURS || d === today || trimp >= P.MIN_PARTIAL_TRIMP) {
          row.trimp = U.round(trimp, 1);
          row.strain = U.round(L.strain(trimp), 2);
          strainByDay[d] = row.strain;
          if (hours < P.MIN_HR_HOURS && d !== today)
            flags.hr_coverage_low = "只有 " + hours + " 小时有心率，负荷可能偏低（录到的部分已经有训练，照常出分）";
        } else {
          flags.no_raw_hr = "只有 " + hours + " 小时有心率（要 ≥ " + P.MIN_HR_HOURS + "），又没录到训练，负荷不出分"
            + (shortcut[d] ? "；其余时间只有快捷指令数据（心率按小时求和，用不了）" : "");
        }
      } else if (shortcut[d]) flags.no_raw_hr = "这天只有快捷指令数据：心率是按小时求和的，算不了 TRIMP / strain";
      else flags.no_raw_hr = "没有原始心率（手表没戴或没同步），算不了 TRIMP / strain";

      // ================= muscle load (workout records)
      if (inp.muscle && inp.muscle[d]) { row.muscle_load = inp.muscle[d].volume_kg; details.muscle = inp.muscle[d]; }
      else if (inp.muscle_error) flags.muscle_load_unavailable = "读不到 SparkyFitness 训练记录，肌肉负荷没算：" + inp.muscle_error;

      // ================= sleep
      var night = main[d] || null, awake_min, awakenings;
      if (night === null) flags.no_sleep = nonWatch[d] ? "只有非手表来源的睡眠（没有阶段数据），不计" : "没有手表睡眠数据";
      else {
        var segs = segIndex ? segIndex.within(night.start, night.end) : I.segments_in(inp.segments, night.start, night.end);
        if (segs.length) { var w = S.waso(segs); awake_min = w[0]; awakenings = w[1]; }
        else { awake_min = night.awake_min; awakenings = []; flags.no_stage_segments = "拿不到阶段明细：中断分用整段清醒分钟，醒来次数按 0"; }
        var hlo = U.addDays(d, -(P.CONSISTENCY_WINDOW_NIGHTS + 1));
        var hist = mainsSorted.filter(function (n) { return hlo <= n.wake_date && n.wake_date < d; })
          .slice(-P.CONSISTENCY_WINDOW_NIGHTS).map(function (n) { return n.start; });
        if (!hist.length) flags.consistency_no_history = "前 15 天没有主睡眠，规律性按 OW 规格给 0";
        var sc = S.sleep_score(night.asleep_min, night.deep_min, night.rem_min, night.start, hist, awake_min, awakenings, tz);
        var napMin = napMinutes(night);
        actual[d] = night.asleep_min + napMin;
        var back = [];
        for (var k = 1; k <= P.DEBT_NIGHTS; k++) { var a = actual[U.addDays(d, -k)]; back.push(a === undefined ? null : a); }
        var debt = S.sleep_debt(back);
        var prevStrain = strainByDay[U.addDays(d, -1)];
        prevStrain = prevStrain === undefined ? null : prevStrain;
        if (prevStrain === null) flags.prev_strain_missing = "前一天没有负荷，睡眠需求不加负荷加成";
        var need = S.sleep_need(debt, prevStrain);
        var perf = S.performance(actual[d], need.need);
        row.sleep_score = sc.score; row.sleep_need_min = U.round(need.need); row.sleep_debt_min = U.round(debt);
        row.sleep_performance = U.round(perf, 4); row.sleep_actual_min = U.round(actual[d]);
        var le = latencyEfficiency(inp.segments, night, segIndex);
        details.sleep = { duration: sc.duration, stages: sc.stages, consistency: sc.consistency, interruptions: sc.interruptions,
          asleep_min: night.asleep_min, nap_min: U.round(napMin), deep_min: night.deep_min, rem_min: night.rem_min,
          waso_min: U.round(awake_min, 1), awakenings: awakenings.filter(function (x) { return x > P.SIGNIFICANT_WAKE_MIN; }).length,
          bedtime: U.isoformat(night.start, tz, "minutes"), wake_time: U.isoformat(night.end, tz, "minutes"),
          need_from_debt: U.round(need.from_debt), need_from_strain: U.round(need.from_strain), source: night.source,
          latency_min: le.latency_min, efficiency: le.efficiency };
        feats.sleep_performance[d] = perf;
      }

      // ================= recovery
      if (night !== null) {
        var hn = R.night_hrv_ln(inp.hrv, night.start, night.end), hrvLn = hn[0], nHrv = hn[1];
        var lw = R.lowest_window(between(inp.hr, times.hr, night.start, night.end), night.start, night.end);
        var rhr = lw[0], rhrAt = lw[1], rhrKey = "rhr";
        if (rhr === null && inp.daily_rhr && inp.daily_rhr[d] !== undefined && inp.daily_rhr[d] !== null) {
          rhr = inp.daily_rhr[d]; rhrKey = "rhr_daily";
          flags.rhr_from_daily = "没有睡眠期原始心率，用当天的 resting_heart_rate（Apple 全天算法），和它自己的 60 天基线比";
        }
        var resp = between(inp.resp, times.resp, night.start, night.end).map(function (s) { return s[1]; });
        var respNight = resp.length ? U.median(resp) : null;
        var temps = wristTemps(inp.temp, times.temp, night, prevMainEnd[d]).map(function (s) { return s[1]; });
        var temp = temps.length ? U.median(temps) : null;
        var spo2 = between(inp.spo2, times.spo2, night.start, night.end).map(function (s) { return s[1]; });
        row.hrv_night_ln = r(hrvLn, 4); row.rhr_night = r(rhr, 1); row.resp_rate_night = r(respNight, 2);

        var past = [];
        for (var k2 = 1; k2 <= P.BASELINE_DAYS; k2++) past.push(U.addDays(d, -k2));
        var base = {};
        Object.keys(feats).forEach(function (key) {
          base[key] = past.filter(function (x) { return x in feats[key]; }).map(function (x) { return feats[key][x]; });
        });
        var todayVals = { hrv: hrvLn, rhr: rhr, sleep_performance: d in feats.sleep_performance ? feats.sleep_performance[d] : null,
          resp_rate: respNight };
        var baseKey = { rhr: rhrKey };
        var rec = { hrv_readings: nHrv, baseline_nights: base.hrv.length };
        if (rhrAt !== null) rec.rhr_low_offset_min = U.round(((rhrAt - night.start) / 1e6) / 60);
        var hrvBase = R.baseline(base.hrv);
        if (hrvLn === null) {
          flags.hrv_missing = inp.hrv_kind === "sdnn" ? "睡眠窗口内没有 HRV 读数，算不了恢复分"
            : "睡眠窗口内没有逐拍数据算出的 RMSSD，算不了恢复分（这个人用 RMSSD，不拿 SDNN 补）";
        } else if (hrvBase === null) {
          flags.baseline_insufficient = "前 60 天只有 " + base.hrv.length + " 晚 HRV，满 " + P.MIN_BASELINE_NIGHTS + " 晚才出恢复分";
        } else {
          var z = {};
          Object.keys(todayVals).forEach(function (key) {
            var v = todayVals[key];
            var b = key === "hrv" ? hrvBase : R.baseline(base[baseKey[key] || key]);
            if (v !== null && b !== null) {
              z[key] = P.RECOVERY_SIGNS[key] * R.zscore(v, b[0], b[1], P.SD_FLOOR[key]);
              rec[key + "_baseline"] = [U.round(b[0], 4), U.round(b[1], 4)];
            }
          });
          var rs = R.recovery_score(z);
          row.recovery = rs[0];
          var zr = {};
          Object.keys(z).forEach(function (key) { zr[key] = U.round(z[key], 3); });
          rec.z = zr; rec.weighted_z = U.round(rs[1], 3);
          var rhrBase = R.baseline(base[rhrKey]);
          if (R.illness_warning(rhr, rhrBase ? rhrBase[0] : null, hrvLn, hrvBase[0]))
            flags.illness_warning = "静息心率比基线高 5 以上且 HRV 低于基线 85%：可能在生病或过度疲劳";
        }
        var delta = R.wrist_temp_flag(temp, base.temp);
        if (delta !== null) flags.wrist_temp_deviation = "手腕温度比 60 天基线" + (delta > 0 ? "高" : "低") + " " + U.round(Math.abs(delta), 1).toFixed(1) + "°C";
        var low = R.spo2_flag(spo2);
        if (low !== null) flags.spo2_low = "当晚血氧中位数 " + pyFloat(low.median) + "%，最低 " + pyFloat(low.min) + "%，低于 90% 的 " + low.below_90 + " 条";
        rec.spo2_night = spo2.length ? r(U.median(spo2), 1) : null;
        rec.spo2_readings = spo2.length;
        rec.wrist_temp = r(temp, 2);
        details.recovery = rec;
        [["hrv", hrvLn], [rhrKey, rhr], ["resp_rate", respNight], ["temp", temp]].forEach(function (kv) {
          if (kv[1] !== null) feats[kv[0]][d] = kv[1];
        });
      }
      row.flags = flags; row.details = details;
      rows.push(row);
    });

    // ================= CTL / ATL / TSB (over the consecutive dates from first)
    // seeded run: from the last stored day with a load, through the stored TRIMPs of the days up to `first`
    var init = null, pre = [];
    for (var a = seedDays.length - 1; a >= 0 && !(has(seed[seedDays[a]].ctl) && has(seed[seedDays[a]].atl)); a--);
    if (a >= 0) {
      var anchor = seed[seedDays[a]], ls = (anchor.details || {}).load_state;
      init = ls && has(ls.ctl) && has(ls.atl) ? { ctl: ls.ctl, atl: ls.atl } : { ctl: anchor.ctl, atl: anchor.atl };
      if (seedDays[a] < U.addDays(first, -1))
        U.dateRange(U.addDays(seedDays[a], 1), U.addDays(first, -1)).forEach(function (d) { pre.push(seed[d] ? seed[d].trimp : null); });
    }
    var loads = L.training_load(pre.concat(rows.map(function (x) { return x.trimp; })), undefined, init).slice(pre.length);
    var origin = opts.origin || seedDays[0] || first;
    var offset = origin < first ? U.daysBetween(origin, first) : 0;
    rows.forEach(function (row, j) {
      var ld = loads[j];
      row.ctl = r(ld.ctl, 1); row.atl = r(ld.atl, 1); row.tsb = r(ld.tsb, 1);
      if (ld.missing && ld.ctl !== null) row.flags.load_missing = "这天没有完整的 TRIMP，CTL / ATL 沿用前一天";
      if (j + offset < P.CTL_DAYS) row.flags.ctl_warmup = "评分起点后的前 " + P.CTL_DAYS + " 天，CTL 还没稳定";
      if (seedDays.length && ld.ctl !== null) row.details.load_state = { ctl: ld.ctl, atl: ld.atl };
    });
    return rows;
  }

  /** Scoring start: the first day with a resting heart rate (= when the user started wearing the watch). */
  function scoring_origin(daily_rhr) {
    var ds = Object.keys(daily_rhr || {}).filter(function (d) { return daily_rhr[d] !== null && daily_rhr[d] !== undefined; }).sort();
    return ds.length ? ds[0] : null;
  }

  return { COLUMNS: COLUMNS, score_days: score_days, scoring_origin: scoring_origin, latency_efficiency: latencyEfficiency };
})(OBS.util, OBS.params, OBS.recovery, OBS.sleep, OBS.strain, OBS.inputs);
