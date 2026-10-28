/* trends: the Trends tab analyses as pure functions. Port of body_engine trends/calc.py, trends/params.py, the pure helpers of
trends/series.py (baseline_band, rolling, hr_zone_summary) and of insights/coach.py (recovery_zone, strain_target).
Inputs are plain objects keyed by local date ('YYYY-MM-DD'). Labels are Chinese unless OBS.setLanguage("en") (Python: tl()).
*/
OBS.trends = (function (U) {
  "use strict";
  var P = {
    TREND_VERSION: "trends-1",
    RANGES: [7, 30, 90, 180, 365],
    BASELINE_DAYS: 60, MIN_BASELINE_DAYS: 14,
    // HRV status (Garmin "HRV Status"): 7-day mean of ln HRV against the 60-day personal band
    HRV_STATUS_WINDOW: 7, HRV_STATUS_MIN_DAYS: 3, HRV_BAND_SD: 1.0, HRV_POOR_SD: 2.0,
    // training status: acute:chronic workload ratio (Gabbett 2016, rolling averages); Apple Training Load 7 vs 28 days
    ACUTE_DAYS: 7, CHRONIC_DAYS: 28, ACWR_HIGH: 1.5, ACWR_LOW: 0.8, DETRAIN_ACWR: 0.6, DETRAIN_CTL_DROP: 0.85,
    VO2_SLOPE_DAYS: 60, VO2_SLOPE_UP: 0.5, MIN_TRIMP_DAYS: 21,
    // load focus (Garmin Load Focus), Edwards zones 50/60/70/80/90 %HRmax
    LOAD_FOCUS: [["low_aerobic", "低有氧", [0, 1]], ["high_aerobic", "高有氧", [2, 3]], ["anaerobic", "无氧", [4]]],
    // aerobic minutes (Fitbit Active Zone Minutes / WHO 150 minutes a week; 1 vigorous minute ≈ 2 moderate)
    AZM_MODERATE_ZONES: [1, 2], AZM_VIGOROUS_ZONES: [3, 4], AZM_WEEK_TARGET: 150,
    // resilience (Oura Resilience), our own first-version mapping
    RESILIENCE_DAYS: 14, RESILIENCE_MIN_DAYS: 10,
    RESILIENCE_LEVELS: [[0.62, "exceptional", "极强"], [0.52, "strong", "强"], [0.42, "solid", "稳固"],
      [0.32, "adequate", "一般"], [-9, "limited", "受限"]],
    // WHOOP-style sleep performance (no sleep-stress part here, its weight goes to the other three)
    SLEEP_PERF_WEIGHTS: { sufficiency: 0.6, consistency: 0.2, efficiency: 0.2 },
    // combined strain (WHOOP Strength Trainer): a median strength session counts as 60 TRIMP
    STRAIN_K: 170.0, MUSCLE_TRIMP_AT_MEDIAN: 60.0,
    // illness risk (Oura Symptom Radar style)
    ILLNESS_WEIGHTS: { rhr: 1.0, hrv: 1.0, resp_rate: 0.8 }, ILLNESS_DEAD_Z: 0.5, ILLNESS_TEMP_C: 0.5, ILLNESS_SPO2: 93.0,
    ILLNESS_LEVELS: [[60, "high", "高"], [30, "watch", "留意"], [0, "low", "低"]],
    // chronotype (Munich ChronoType Questionnaire: MSFsc)
    CHRONO_DAYS: 60, CHRONO_MIN_NIGHTS: 10, CHRONO_FREE_WAKE_DAYS: [5, 6],
    CHRONO_BINS: [[3 * 60, "early", "早型"], [4 * 60 + 30, "intermediate", "中间型"], [6 * 60, "late", "晚型"],
      [24 * 60, "very_late", "极晚型"]],
    // body battery (Garmin Body Battery), rough estimate
    BB_WAKE_BASE: 20.0, BB_WAKE_SPAN: 75.0, BB_DRAIN_PER_HOUR: 2.0, BB_STRESS_DRAIN: { low: 0.0, medium: 2.0, high: 4.0 },
    BB_STRAIN_DRAIN: 2.5,
    // recovery time (Garmin Recovery Time), rough estimate
    RT_STRAIN_FREE: 8.0, RT_HOURS_PER_STRAIN: 5.0, RT_MAX_HOURS: 96, RT_TSB_FATIGUE: 50.0,
    RT_ZONE_FACTOR: { green: 0.8, yellow: 1.0, red: 1.25 }, RT_WORKOUT_HOUR: 18, RT_AS_OF_HOUR: 7,
    // recovery index (Oura): lowest 5-minute heart rate in the first half = good, after 75 % = late
    RI_GOOD: 0.5, RI_LATE: 0.75,
    // heart-rate recovery one minute after exercise (Cole et al. 1999: ≤ 12 bpm abnormal)
    HRR_LOW: 12.0, HRR_GOOD: 20.0,
    // Strain Coach (insights/coach.py): first-version ranges, strain calibrated to K = 170
    STRAIN_TARGETS: { green: [14.0, 18.0], yellow: [10.0, 14.0], red: [0.0, 10.0] },
    RECOVERY_ZONES: [[67, "green"], [34, "yellow"], [0, "red"]],
    TSB_FATIGUED: -30.0, TSB_SHIFT: 2.0, ILLNESS_TARGET: [0.0, 8.0]
  };
  var EN = {
    "平衡": "Balanced", "偏高": "High", "偏低": "Low", "很低": "Very low",
    "过度": "Overreaching", "去适应": "Detraining", "恢复中": "Recovering", "疲劳": "Strained", "有效": "Productive",
    "低效": "Unproductive", "保持": "Maintaining", "低有氧": "Low aerobic", "高有氧": "High aerobic", "无氧": "Anaerobic",
    "极强": "Exceptional", "强": "Strong", "稳固": "Solid", "一般": "Adequate", "受限": "Limited",
    "高": "High", "留意": "Watch", "低": "Low", "早型": "Early", "中间型": "Intermediate", "晚型": "Late", "极晚型": "Very late",
    "前半夜": "First half", "后半夜": "Second half", "很晚": "Very late"
  };
  function tl(zh) { return OBS.lang === "en" && zh ? (EN[zh] || zh) : zh; }

  function vals(by, start, end) {
    return Object.keys(by).filter(function (d) { return start <= d && d <= end && by[d] !== null && by[d] !== undefined; })
      .map(function (d) { return by[d]; });
  }
  function band(xs) {
    if (xs.length < P.MIN_BASELINE_DAYS) return null;
    return [U.fmean(xs), U.pstdev(xs) || 1e-6];
  }
  function get(by, d) { var v = by[d]; return v === undefined ? null : v; }

  // ---------------------------------------------------------------- HRV status
  var HRV_STATUS = { balanced: "平衡", high: "偏高", low: "偏低", poor: "很低" };

  /** 7-day mean of ln HRV vs the band of the 60 days before that window. */
  function hrv_status(hrv_ms, end) {
    var w0 = U.addDays(end, -(P.HRV_STATUS_WINDOW - 1));
    var week = vals(hrv_ms, w0, end).filter(function (v) { return v > 0; }).map(Math.log);
    var base = band(vals(hrv_ms, U.addDays(w0, -P.BASELINE_DAYS), U.addDays(w0, -1)).filter(function (v) { return v > 0; }).map(Math.log));
    if (week.length < P.HRV_STATUS_MIN_DAYS || base === null) return null;
    var m = base[0], sd = base[1], avg = U.fmean(week);
    var lo = m - P.HRV_BAND_SD * sd, hi = m + P.HRV_BAND_SD * sd;
    var status = avg < m - P.HRV_POOR_SD * sd ? "poor" : avg < lo ? "low" : avg > hi ? "high" : "balanced";
    return { status: status, label: tl(HRV_STATUS[status]), avg7_ms: U.round(Math.exp(avg), 1),
      band_ms: [U.round(Math.exp(lo), 1), U.round(Math.exp(hi), 1)], baseline_ms: U.round(Math.exp(m), 1), nights: week.length };
  }

  // ---------------------------------------------------------------- training status + load focus
  /** Mean over the last `days` days; missing days count as 0 when fill_zero. */
  function rolling_mean(by, end, days, fill_zero) {
    fill_zero = fill_zero === undefined ? true : fill_zero;
    var xs = [];
    for (var k = 0; k < days; k++) {
      var x = get(by, U.addDays(end, -k));
      if (x === null) { if (fill_zero) xs.push(0.0); } else xs.push(x);
    }
    return xs.length ? U.pySum(xs) / xs.length : null;
  }

  function acwr(trimp, end) {
    var acute = rolling_mean(trimp, end, P.ACUTE_DAYS), chronic = rolling_mean(trimp, end, P.CHRONIC_DAYS);
    if (!chronic) return null;
    return U.round(acute / chronic, 2);
  }

  /** Least-squares slope over the last `days` days, as change per `days` days (null with < 3 points). */
  function slope_per(by, end, days) {
    var lo = U.addDays(end, -days), pts = [];
    Object.keys(by).forEach(function (d) {
      if (lo < d && d <= end && by[d] !== null && by[d] !== undefined) pts.push([U.daysBetween(end, d), by[d]]);
    });
    if (pts.length < 3) return null;
    var mx = U.fmean(pts.map(function (p) { return p[0]; })), my = U.fmean(pts.map(function (p) { return p[1]; }));
    var den = U.pySum(pts.map(function (p) { return Math.pow(p[0] - mx, 2); }));
    if (!den) return null;
    return U.round(U.pySum(pts.map(function (p) { return (p[0] - mx) * (p[1] - my); })) / den * days, 2);
  }

  var TRAINING_STATUS = { overreaching: "过度", detraining: "去适应", recovery: "恢复中", strained: "疲劳",
    productive: "有效", unproductive: "低效", maintaining: "保持" };

  function training_status(trimp, ctl, vo2, hrv, end) {
    var days_with = 0;
    for (var k = 0; k < P.CHRONIC_DAYS; k++) if (get(trimp, U.addDays(end, -k)) !== null) days_with++;
    if (days_with < P.MIN_TRIMP_DAYS) return null;
    var ratio = acwr(trimp, end);
    if (ratio === null) return null;
    var ctl_now = get(ctl, end), ctl_then = get(ctl, U.addDays(end, -P.CHRONIC_DAYS));
    var vo2_slope = slope_per(vo2, end, P.VO2_SLOPE_DAYS), status;
    if (ratio > P.ACWR_HIGH) status = "overreaching";
    else if (ratio < P.ACWR_LOW) {
      var dropped = ctl_now !== null && ctl_then && ctl_now < ctl_then * P.DETRAIN_CTL_DROP;
      status = ratio < P.DETRAIN_ACWR && dropped ? "detraining" : "recovery";
    } else if (hrv && (hrv.status === "low" || hrv.status === "poor")) status = "strained";
    else if (vo2_slope !== null && vo2_slope >= P.VO2_SLOPE_UP) status = "productive";
    else if (vo2_slope !== null && vo2_slope <= -P.VO2_SLOPE_UP) status = "unproductive";
    else status = "maintaining";
    return { status: status, label: tl(TRAINING_STATUS[status]), acwr: ratio,
      acute: U.round(rolling_mean(trimp, end, P.ACUTE_DAYS), 1), chronic: U.round(rolling_mean(trimp, end, P.CHRONIC_DAYS), 1),
      vo2_change_60d: vo2_slope, trimp_days: days_with };
  }

  /** Share of TRIMP (zone minutes × weight 1–5) per zone group over the last `days` days. zone_minutes: {date: [5]}. */
  function load_focus(zone_minutes, end, days) {
    days = days === undefined ? P.CHRONIC_DAYS : days;
    var tot = [0.0, 0.0, 0.0, 0.0, 0.0];
    for (var k = 0; k < days; k++) {
      var z = get(zone_minutes, U.addDays(end, -k)) || [0, 0, 0, 0, 0];
      for (var i = 0; i < 5; i++) tot[i] += (z[i] || 0) * (i + 1);
    }
    var s = U.pySum(tot);
    if (!s) return null;
    return { groups: P.LOAD_FOCUS.map(function (g) {
      var t = U.pySum(g[2].map(function (i) { return tot[i]; }));
      return { key: g[0], label: tl(g[1]), trimp: U.round(t), pct: U.round(100 * t / s) };
    }), days: days };
  }

  // ---------------------------------------------------------------- aerobic minutes per week
  function aerobic_minutes(zones) {
    if (!zones || !zones.length) return null;
    return U.pySum(P.AZM_MODERATE_ZONES.map(function (i) { return zones[i] || 0; }))
      + 2 * U.pySum(P.AZM_VIGOROUS_ZONES.map(function (i) { return zones[i] || 0; }));
  }

  /** Totals per week starting Monday (only weeks that touch `dates`). */
  function weekly_sum(by, dates) {
    var weeks = {}, order = [];
    dates.forEach(function (d) {
      var wk = U.addDays(d, -U.weekday(d));
      if (!(wk in weeks)) { weeks[wk] = 0.0; order.push(wk); }
      weeks[wk] += get(by, d) || 0;
    });
    return order.sort().map(function (wk) { return { week: wk, value: U.round(weeks[wk], 1) }; });
  }

  // ---------------------------------------------------------------- resilience
  function resilience(recovery, sleep_perf, stress, end) {
    var start = U.addDays(end, -(P.RESILIENCE_DAYS - 1));
    var rec = vals(recovery, start, end);
    if (rec.length < P.RESILIENCE_MIN_DAYS) return null;
    var sp = vals(sleep_perf, start, end).map(function (v) { return Math.min(1.0, v); });
    var st = vals(stress, start, end);
    var capacity = 0.6 * U.fmean(rec) / 100 + 0.4 * (sp.length ? U.fmean(sp) : U.fmean(rec) / 100);
    var load = st.length ? U.fmean(st) / 3 : 0.0;
    var balance = capacity - 0.5 * load;
    var lv = P.RESILIENCE_LEVELS.filter(function (x) { return balance >= x[0]; })[0];
    return { level: lv[1], label: tl(lv[2]), balance: U.round(balance, 3), capacity: U.round(capacity, 3),
      stress_load: st.length ? U.round(load, 3) : null, days: rec.length };
  }

  // ---------------------------------------------------------------- WHOOP-style sleep performance
  function sleep_performance_whoop(sufficiency, consistency, efficiency) {
    if (sufficiency === null || sufficiency === undefined) return null;
    var parts = { sufficiency: Math.min(1.0, sufficiency) * 100, consistency: consistency === undefined ? null : consistency,
      efficiency: efficiency === null || efficiency === undefined ? null : Math.min(1.0, efficiency) * 100 };
    var keys = Object.keys(parts).filter(function (k) { return parts[k] !== null; });
    var num = U.pySum(keys.map(function (k) { return parts[k] * P.SLEEP_PERF_WEIGHTS[k]; }));
    return U.round(num / U.pySum(keys.map(function (k) { return P.SLEEP_PERF_WEIGHTS[k]; })), 1);
  }

  // ---------------------------------------------------------------- combined strain (optional view)
  function strain_from_trimp(trimp) { return 21 * (1 - Math.exp(-trimp / P.STRAIN_K)); }

  function combined_strain(trimp, muscle_load, median_muscle) {
    if ((trimp === null || trimp === undefined) && !muscle_load) return null;
    var extra = muscle_load && median_muscle ? P.MUSCLE_TRIMP_AT_MEDIAN * muscle_load / median_muscle : 0.0;
    return U.round(strain_from_trimp((trimp || 0) + extra), 1);
  }

  // ---------------------------------------------------------------- illness risk
  /** z = recovery z-scores (positive is good, scores details.recovery.z). Risk 0–100 from the adverse signs. */
  function illness_risk(z, temp_dev, spo2) {
    var hasZ = z && Object.keys(z).length;
    if (!hasZ && (temp_dev === null || temp_dev === undefined)) return null;
    var zz = z || {};
    var signs = U.pySum(Object.keys(P.ILLNESS_WEIGHTS).map(function (k) {
      return Math.max(0.0, -(k in zz ? zz[k] : 0.0) - P.ILLNESS_DEAD_Z) * P.ILLNESS_WEIGHTS[k];
    }));
    if (temp_dev !== null && temp_dev !== undefined && temp_dev >= P.ILLNESS_TEMP_C) signs += 1.0;
    if (spo2 !== null && spo2 !== undefined && spo2 < P.ILLNESS_SPO2) signs += 0.5;
    var score = U.round(100 * (1 - Math.exp(-signs / 2)));
    var lv = P.ILLNESS_LEVELS.filter(function (x) { return score >= x[0]; })[0];
    return { score: score, level: lv[1], label: tl(lv[2]) };
  }

  // ---------------------------------------------------------------- chronotype
  // Python subtracts two datetimes of the same tzinfo on their wall clocks (both were .astimezone(tz)), so this works in
  // local wall-clock microseconds too: identical for fixed offsets, and the same answer as Python across DST changes.
  function wall(t, tz) { return t + tz.offsetAt(t) * U.US_MIN; }

  /** Mid-sleep in minutes after the local midnight before waking (negative when before midnight). */
  function mid_min(bed, wake, tz) {
    var b = wall(bed, tz), w = wall(wake, tz), diff = w - b, half = Math.floor(diff / 2);
    if (diff % 2 !== 0 && half % 2 !== 0) half += 1;   // timedelta / 2 rounds half to even (microseconds)
    var midnight = Math.floor(w / U.US_DAY) * U.US_DAY;
    return ((b + half - midnight) / 1e6) / 60;
  }

  /** nights = [[bedtime, wake time]] (instants, read in tz). MCTQ: MSFsc = free-day mid-sleep − half the extra free-day sleep. */
  function chronotype(nights, tz) {
    tz = U.tzOf(tz);
    if (nights.length < P.CHRONO_MIN_NIGHTS) return null;
    var free = nights.filter(function (n) { return P.CHRONO_FREE_WAKE_DAYS.indexOf(U.weekday(U.localDate(n[1], tz))) >= 0; });
    var work = nights.filter(function (n) { return P.CHRONO_FREE_WAKE_DAYS.indexOf(U.weekday(U.localDate(n[1], tz))) < 0; });
    var use = free.length >= 3 ? free : nights;
    var msf = U.median(use.map(function (n) { return mid_min(n[0], n[1], tz); }));
    if (free.length >= 3 && work.length) {
      var dur = function (n) { return ((wall(n[1], tz) - wall(n[0], tz)) / 1e6) / 60; };
      var sd_f = U.fmean(free.map(dur)), sd_w = U.fmean(work.map(dur));
      if (sd_f > sd_w) msf -= (sd_f - sd_w) / 2;
    }
    var bin = P.CHRONO_BINS.filter(function (x) { return msf < x[0]; })[0];
    if (!bin) throw new Error("StopIteration");
    var m = ((U.round(msf) % 1440) + 1440) % 1440;
    return { type: bin[1], label: tl(bin[2]), mid_sleep: U.pad(Math.floor(m / 60), 2) + ":" + U.pad(m % 60, 2),
      free_days: free.length >= 3, nights: nights.length };
  }

  // ---------------------------------------------------------------- body battery (estimate)
  function body_battery(recovery, sleep_perf, wake_hour, stress_hours, strain, until_hour) {
    until_hour = until_hour === undefined ? 23 : until_hour;
    var charge = recovery !== null && recovery !== undefined ? recovery / 100
      : (sleep_perf !== null && sleep_perf !== undefined ? Math.min(1.0, sleep_perf) : null);
    if (charge === null) return null;
    var wake = wake_hour === null || wake_hour === undefined ? 7 : Math.max(0, Math.min(16, wake_hour));
    var until = Math.max(wake, Math.min(23, until_hour));
    var level = {};
    (stress_hours || []).forEach(function (h) { if (h.score !== null && h.score !== undefined) level[h.hour] = h.level === undefined ? null : h.level; });
    var hours = Math.max(1, 23 - wake);
    var v = P.BB_WAKE_BASE + P.BB_WAKE_SPAN * charge;
    var pts = [{ hour: wake, v: U.round(v) }];
    for (var h = wake; h < until; h++) {
      var lv = level[h] || "low";
      v -= P.BB_DRAIN_PER_HOUR + (lv in P.BB_STRESS_DRAIN ? P.BB_STRESS_DRAIN[lv] : 0) + P.BB_STRAIN_DRAIN * (strain || 0) / hours;
      v = Math.max(5.0, v);
      pts.push({ hour: h + 1, v: U.round(v) });
    }
    return { wake: pts[0].v, current: pts[pts.length - 1].v, low: Math.min.apply(null, pts.map(function (p) { return p.v; })),
      points: pts, estimate: true };
  }

  // ---------------------------------------------------------------- recovery time (estimate)
  /** Hours still needed as of 07:00 on `day`, from the hardest of the last 4 days (strain above 8). */
  function recovery_time(strain, tsb, zone_today, day) {
    var best = null, as_of = U.dayNum(day) * 24 + P.RT_AS_OF_HOUR;   // naive local hours, as Python's naive datetimes
    for (var k = 1; k < 5; k++) {
      var d = U.addDays(day, -k), s = get(strain, d);
      if (s === null || s <= P.RT_STRAIN_FREE) continue;
      var need = (s - P.RT_STRAIN_FREE) * P.RT_HOURS_PER_STRAIN * (1 + Math.max(0.0, -(get(tsb, d) || 0)) / P.RT_TSB_FATIGUE);
      var f = P.RT_ZONE_FACTOR[zone_today || "yellow"];
      need = Math.min(P.RT_MAX_HOURS, need * (f === undefined ? 1.0 : f));
      var left = need - ((as_of - (U.dayNum(d) * 24 + P.RT_WORKOUT_HOUR)) * 3600) / 3600;
      if (best === null || left > best.hours) best = { hours: Math.max(0, U.round(left)), from_date: d, from_strain: U.round(s, 1), estimate: true };
    }
    if (best === null) {
      for (var j = 1; j < 5; j++) if (get(strain, U.addDays(day, -j)) !== null) return { hours: 0, from_date: null, from_strain: null, estimate: true };
    }
    return best;
  }

  // ---------------------------------------------------------------- recovery index / heart-rate recovery
  var RI_LEVELS = { good: "前半夜", fair: "后半夜", late: "很晚" };

  function recovery_index(low_offset_min, asleep_min) {
    if (low_offset_min === null || low_offset_min === undefined || !asleep_min) return null;
    var frac = Math.max(0.0, Math.min(1.0, low_offset_min / asleep_min));
    var key = frac < P.RI_GOOD ? "good" : frac < P.RI_LATE ? "fair" : "late";
    return { pct: U.round(100 * frac), level: key, label: tl(RI_LEVELS[key]) };
  }

  function hr_recovery(end_hr, after_hr) {
    if (end_hr === null || end_hr === undefined || after_hr === null || after_hr === undefined) return null;
    return U.round(end_hr - after_hr, 1);
  }

  function hr_recovery_level(bpm) {
    if (bpm === null || bpm === undefined) return null;
    return bpm <= P.HRR_LOW ? "low" : bpm < P.HRR_GOOD ? "fair" : "good";
  }

  // ---------------------------------------------------------------- series.py helpers
  /** Per day: mean and ±1 SD of the previous 60 days (excluding the day), none with fewer than 14 values. */
  function baseline_band(by_date, dates) {
    var out = { mean: [], lo: [], hi: [] };
    var items = Object.keys(by_date).filter(function (k) { return by_date[k] !== null && by_date[k] !== undefined; });
    dates.forEach(function (d) {
      var start = U.addDays(d, -60);
      var xs = items.filter(function (k) { return start <= k && k < d; }).map(function (k) { return by_date[k]; });
      if (xs.length < 14) { out.mean.push(null); out.lo.push(null); out.hi.push(null); return; }
      var m = U.pySum(xs) / xs.length;
      var sd = Math.pow(U.pySum(xs.map(function (x) { return Math.pow(x - m, 2); })) / (xs.length - 1), 0.5);
      out.mean.push(U.round(m, 2)); out.lo.push(U.round(m - sd, 2)); out.hi.push(U.round(m + sd, 2));
    });
    return out;
  }

  function rolling(values, window, min_n) {
    window = window === undefined ? 7 : window; min_n = min_n === undefined ? 3 : min_n;
    return values.map(function (_, i) {
      var w = values.slice(Math.max(0, i - window + 1), i + 1).filter(function (v) { return v !== null && v !== undefined; });
      return w.length >= min_n ? U.round(U.pySum(w) / w.length, 2) : null;
    });
  }

  /** Total minutes per zone over the period + totals per week (starting Monday). */
  function hr_zone_summary(by_date, dates) {
    var total = [0.0, 0.0, 0.0, 0.0, 0.0], weeks = {}, order = [];
    dates.forEach(function (d) {
      var z = get(by_date, d);
      if (!z || !z.length) return;
      var wk = U.addDays(d, -U.weekday(d));
      if (!(wk in weeks)) { weeks[wk] = [0.0, 0.0, 0.0, 0.0, 0.0]; order.push(wk); }
      for (var i = 0; i < 5; i++) { total[i] += z[i] || 0; weeks[wk][i] += z[i] || 0; }
    });
    if (!order.length) return null;
    return { labels: ["50–60%", "60–70%", "70–80%", "80–90%", "90–100%"], total_min: total.map(function (x) { return U.round(x); }),
      weeks: order.sort().map(function (wk) { return { week: wk, minutes: weeks[wk].map(function (x) { return U.round(x); }) }; }) };
  }

  // ---------------------------------------------------------------- Strain Coach (insights/coach.py)
  function recovery_zone(rec) {
    if (rec === null || rec === undefined) return null;
    for (var i = 0; i < P.RECOVERY_ZONES.length; i++) if (rec >= P.RECOVERY_ZONES[i][0]) return P.RECOVERY_ZONES[i][1];
    throw new Error("StopIteration");
  }

  function strain_target(recovery, tsb_yesterday, illness) {
    var zone = recovery_zone(recovery), reasons = [], lo, hi;
    if (illness) { lo = P.ILLNESS_TARGET[0]; hi = P.ILLNESS_TARGET[1]; reasons.push("有生病提示，只做轻活动"); }
    else if (zone === null) { lo = P.STRAIN_TARGETS.yellow[0]; hi = P.STRAIN_TARGETS.yellow[1]; reasons.push("今天没有恢复分，按「黄」给中等目标"); }
    else { lo = P.STRAIN_TARGETS[zone][0]; hi = P.STRAIN_TARGETS[zone][1]; reasons.push("恢复 " + recovery + "（" + { green: "绿", yellow: "黄", red: "红" }[zone] + "）"); }
    if (tsb_yesterday !== null && tsb_yesterday !== undefined && tsb_yesterday < P.TSB_FATIGUED && !illness) {
      lo = Math.max(0.0, lo - P.TSB_SHIFT); hi = Math.max(0.0, hi - P.TSB_SHIFT);
      reasons.push("近期疲劳累积（TSB " + U.round(tsb_yesterday) + "），区间下调 " + P.TSB_SHIFT);
    }
    return { zone: zone, target_low: lo, target_high: hi, reasons: reasons };
  }

  return { params: P, EN: EN, tl: tl, hrv_status: hrv_status, rolling_mean: rolling_mean, acwr: acwr, slope_per: slope_per,
    training_status: training_status, load_focus: load_focus, aerobic_minutes: aerobic_minutes, weekly_sum: weekly_sum,
    resilience: resilience, sleep_performance_whoop: sleep_performance_whoop, strain_from_trimp: strain_from_trimp,
    combined_strain: combined_strain, illness_risk: illness_risk, chronotype: chronotype, body_battery: body_battery,
    recovery_time: recovery_time, recovery_index: recovery_index, hr_recovery: hr_recovery, hr_recovery_level: hr_recovery_level,
    baseline_band: baseline_band, rolling: rolling, hr_zone_summary: hr_zone_summary, recovery_zone: recovery_zone,
    strain_target: strain_target };
})(OBS.util);
