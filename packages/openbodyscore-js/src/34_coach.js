/* coach (2.0.0 package M4, docs/ios-native-2.0.md): the coaches, the bedtime goal, the sleep detail, the Today composition
and the body-age loader on the phone. Port of the pure parts of body_engine
- insights/coach.py `strain_coach_compute`, `sleep_coach_compute`, `sleep_need_tonight`, `health_monitor_compute`,
  `monitor_range` (GET /strain-coach, /sleep-coach, /health-monitor);
- goals/sleep.py (every function) and goals/store.py `with_plan`, `duration_from`, `compute`, `view_compute`, `tonight`
  (GET /goals/sleep);
- sleep_night/view.py `compute` and its blocks (`_night`, `_score`, `_avg_block`, `_baseline`, `_vitals`, `notes`,
  `ai_prompt`) (GET /sleep/night);
- web/views.py `reasons`, `recovery_zone`, `load_status`, `load_text`, `scores_today_compute`, `main_rows`, `latest_main`,
  `recent_bedtimes_compute`, `hypnogram`, `sleep_today_compute`, `frequent_journal_compute` and web/recovery_empty.py
  `explain_from` (the `scores` / `sleep` blocks of GET /today, GET /journal/frequent);
- insights/healthspan_store.py `from_parts` and `weekly_local` (the body-age loader; the formulas are
  OBS.healthspan_snapshot, 14_healthspan_snapshot.js);
- web/params.py (the Today tab's thresholds, TodayParams.swift in the app) as `TODAY_PARAMS`.
Inputs are what the Python `load` sides return: instants as epoch milliseconds or ISO strings, dates 'YYYY-MM-DD'.
Thresholds with their sources are in the Python files named above; the copies here are compared by the golden test
(test/coach.test.js against body_engine/tests/test_js_golden_coach.py).
*/
OBS.coach = (function (U, T, SL, SP, HS, HSS, SER) {
  "use strict";
  // insights/coach.COACH_VERSION: this module's version + the body-age algorithm it runs
  var COACH_VERSION = "coach-1+" + HSS.CURRENT.ALGO_VERSION;

  function nn(v) { return v !== null && v !== undefined; }
  function g(o, k) { var v = o ? o[k] : undefined; return v === undefined ? null : v; }
  function L(zh, en) { return OBS.lang === "en" ? en : zh; }
  function us(x) { return U.toUs(x); }
  function keys(o) { return Object.keys(o || {}).sort(); }
  function mod(a, b) { return ((a % b) + b) % b; }
  function pad2(n) { return U.pad(n, 2); }
  function stableSort(xs, cmp) {
    return xs.map(function (x, i) { return [x, i]; }).sort(function (a, b) { return cmp(a[0], b[0]) || a[1] - b[1]; })
      .map(function (x) { return x[0]; });
  }
  /** Python str() of a float that went through round(x, n): always with a decimal point. */
  function pyFloat(x) { return Number.isInteger(x) ? x.toFixed(1) : String(x); }
  /** Python format(x, "g"). */
  function fmtG(x) {
    if (Object.is(x, -0)) return "-0";
    if (x === 0) return "0";
    var parts = x.toExponential(5).split("e"), ex = Number(parts[1]);   // 6 significant digits
    var strip = function (s) { return s.indexOf(".") >= 0 ? s.replace(/0+$/, "").replace(/\.$/, "") : s; };
    if (ex < -4 || ex >= 6) return strip(parts[0]) + "e" + (ex < 0 ? "-" : "+") + pad2(Math.abs(ex));
    return strip(x.toFixed(5 - ex));
  }
  /** Python format(x, ".0f") / "+.0f" (round half to even, "-0" kept). */
  function fmt0(x, plus) {
    var r = U.round(x, 0), neg = r < 0 || Object.is(r, -0);
    return (neg ? "-" : plus ? "+" : "") + String(Math.abs(r));
  }
  function hhmmOf(usv, tz) { var p = U.local(usv, tz); return pad2(p.hour) + ":" + pad2(p.minute); }

  // ================================================================ insights/coach.py
  var CP = {
    LATENCY_DEFAULT_MIN: 15, MONITOR_DAYS: 60, MONITOR_MIN_NIGHTS: 14, MONITOR_K: 2.0, HRV_MIN_HALF_LN: 0.15,
    MONITOR: [["rhr", "静息心率（睡眠期最低）", "bpm", 3.0], ["hrv", "HRV（SDNN，夜间）", "ms", null],
      ["resp_rate", "呼吸频率（夜间）", "次/分", 1.0], ["spo2", "血氧（夜间中位数）", "%", 2.0],
      ["wrist_temp", "手腕温度（夜间）", "°C", 0.5]],
    MONITOR_EN: { rhr: ["Resting HR (lowest during sleep)", "bpm"], hrv: ["HRV (SDNN, night)", "ms"],
      resp_rate: ["Respiratory rate (night)", "/min"], spo2: ["Blood oxygen (night median)", "%"],
      wrist_temp: ["Wrist temperature (night)", "°C"] },
    RHR_APPLE_LABEL: ["静息心率（Apple 全天，最近一晚没有睡眠期原始心率）", "Resting HR (Apple, all day; no raw sleep HR last night)"]
  };

  function strain_coach_compute(today, yday, d) {
    var illness = !!(today && today.flags && Object.prototype.hasOwnProperty.call(today.flags, "illness_warning"));
    var tgt = T.strain_target(today ? g(today, "recovery") : null, yday ? g(yday, "tsb") : null, illness);
    var cur = today ? g(today, "strain") : null, status = null, missing = null;
    if (cur !== null) status = cur < tgt.target_low ? "below" : cur <= tgt.target_high ? "in_range" : "above";
    if (cur === null) missing = today ? g(today.flags || {}, "no_raw_hr") : "今天还没计算评分";
    return { date: d, recovery: today ? g(today, "recovery") : null, zone: tgt.zone, target_low: tgt.target_low,
      target_high: tgt.target_high, reasons: tgt.reasons, current_strain: cur,
      progress: cur === null ? null : U.round(cur / (tgt.target_low || tgt.target_high), 2),
      remaining_to_low: cur === null ? null : U.round(Math.max(0.0, tgt.target_low - cur), 1),
      status: status, current_missing_reason: missing };
  }

  function sleep_need_tonight(actual_recent, strain_today) {
    var debt = SL.sleep_debt(actual_recent), need = SL.sleep_need(debt, strain_today);
    return { need: need.need, baseline: need.baseline, from_debt: need.from_debt, from_strain: need.from_strain, debt: debt };
  }

  // wall-clock instants (microseconds since 1970-01-01 local midnight): Python datetimes of one time zone subtract by wall time
  function wallAt(hm, base) { return U.dayNum(base) * U.US_DAY + (Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3, 5))) * U.US_MIN; }
  function wallHHMM(w) { var t = mod(w, U.US_DAY); return pad2(Math.floor(t / U.US_HOUR)) + ":" + pad2(Math.floor((t % U.US_HOUR) / U.US_MIN)); }
  function wallHour(w) { return Math.floor(mod(w, U.US_DAY) / U.US_HOUR); }
  function minutesUs(m) { return U.round(m * 6e7); }

  /** rows: {date, sleep_actual_min, strain, s = details.sleep} of the 14 days up to d, newest first; goal: the bedtime-goal
   * view when it has a target tonight, else null. */
  function sleep_coach_compute(rows, d, tz, goal) {
    var by = {};
    rows.forEach(function (r) { by[r.date] = r; });
    var actual = [];
    for (var k = 0; k < SP.DEBT_NIGHTS; k++) { var dk = U.addDays(d, -k); actual.push(dk in by ? g(by[dk], "sleep_actual_min") : null); }
    var strain_today = d in by ? g(by[d], "strain") : null;
    var need = sleep_need_tonight(actual, strain_today);
    var latencies = rows.filter(function (r) { return r.s && nn(r.s.latency_min); }).map(function (r) { return r.s.latency_min; });
    var latency = latencies.length >= 3 ? U.median(latencies) : CP.LATENCY_DEFAULT_MIN;
    var wakes = rows.slice(0, 7).filter(function (r) { return r.s && r.s.wake_time; }).map(function (r) {
      var s = r.s.wake_time;   // the stored ISO string's own wall time (datetime.fromisoformat)
      return Number(s.slice(11, 13)) * 60 + Number(s.slice(14, 16));
    });
    var out = { date: d, need_min: U.round(need.need),
      need_parts: { baseline: U.round(need.baseline), from_debt: U.round(need.from_debt), from_strain: U.round(need.from_strain) },
      sleep_debt_min: U.round(need.debt), strain_today: strain_today, latency_buffer_min: U.round(latency), wake_time: null,
      wake_source: null, bedtime_for_need: null, goal_bedtime: null, recommended_bedtime: null, source: null,
      shortfall_min: null, notes: [] };
    var tomorrow = U.addDays(d, 1), goal_bed = null, wake;
    if (goal) {
      var tb = goal.tonight_target_bedtime, gh = Number(tb.slice(0, 2));
      goal_bed = wallAt(tb, gh >= 12 ? d : tomorrow);
      out.goal_bedtime = tb; out.recommended_bedtime = tb; out.source = "goal";
    }
    if (goal && goal.target_wake) { wake = wallAt(goal.target_wake, tomorrow); out.wake_source = "goal"; }
    else if (goal) { wake = goal_bed + minutesUs(latency + need.need); out.wake_source = "goal_plus_need"; }
    else if (wakes.length) {
      var m = Math.trunc(U.median(wakes.slice().sort(function (a, b) { return a - b; })));
      wake = wallAt(pad2(Math.floor(m / 60)) + ":" + pad2(m % 60), tomorrow); out.wake_source = "recent_median";
    } else {
      out.notes.push("没有最近的起床时间，也没设目标起床时间，给不出几点睡");
      return out;
    }
    if (wallHour(wake) >= 18 && out.wake_source !== "goal_plus_need") wake -= U.US_DAY;
    out.wake_time = wallHHMM(wake);
    var for_need = wake - minutesUs(need.need + latency);
    out.bedtime_for_need = wallHHMM(for_need);
    if (goal_bed === null) { out.recommended_bedtime = wallHHMM(for_need); out.source = "need"; }
    else if (out.wake_source === "goal") {
      var short = (goal_bed - for_need) / 1e6 / 60;
      if (short > 0) {
        out.shortfall_min = U.round(short);
        out.notes.push("按入睡目标 " + out.goal_bedtime + " 睡、" + out.wake_time + " 起，比需求少 " + U.round(short) + " 分钟："
          + "先守住逐步提前的目标，不够的可以晚点起或下午小睡 ≤ 20 分钟补");
      } else out.notes.push("入睡目标比睡够所需的 " + out.bedtime_for_need + " 还早，按目标睡就够");
    } else {
      out.notes.push("按入睡目标 " + out.goal_bedtime + " 睡，要睡够 " + pyFloat(U.round(need.need / 60, 1)) + " 小时，"
        + "建议 " + out.wake_time + " 起");
    }
    return out;
  }

  /** [lower, upper, centre] or null; HRV's symmetric range on the ln scale. */
  function monitor_range(values, key) {
    if (values.length < CP.MONITOR_MIN_NIGHTS) return null;
    var m, sd, half;
    if (key === "hrv") {
      var ln = values.map(Math.log);
      m = U.fmean(ln); sd = U.stdev(ln); half = Math.max(CP.MONITOR_K * sd, CP.HRV_MIN_HALF_LN);
      return [Math.exp(m - half), Math.exp(m + half), Math.exp(m)];
    }
    var minHalf = CP.MONITOR.filter(function (x) { return x[0] === key; })[0][3];
    m = U.fmean(values); sd = U.stdev(values); half = Math.max(CP.MONITOR_K * sd, minHalf);
    return [m - half, m + half, m];
  }

  var MONITOR_GET = {
    rhr: function (r) { return g(r, "rhr_value"); },
    hrv: function (r) { return nn(r.hrv_night_ln) ? Math.exp(r.hrv_night_ln) : null; },
    resp_rate: function (r) { return g(r, "resp_rate_night"); },
    spo2: function (r) { return g(r.rec || {}, "spo2_night"); },
    wrist_temp: function (r) { return g(r.rec || {}, "wrist_temp"); }
  };

  /** rows: {date, rhr_night, hrv_night_ln, resp_rate_night, rec, rhr_daily, apple_rhr} of the 60 days up to d, by date. */
  function health_monitor_compute(rows, d) {
    rows = rows.map(function (r) { return Object.assign({}, r); });
    var latest = null;
    for (var i = rows.length - 1; i >= 0; i--) if (nn(rows[i].rhr_night) || nn(rows[i].hrv_night_ln)) { latest = rows[i]; break; }
    var rhr_apple = !!(latest && latest.rhr_daily);
    rows.forEach(function (r) { r.rhr_value = rhr_apple ? g(r, "apple_rhr") : (r.rhr_daily ? null : g(r, "rhr_night")); });
    var metrics = CP.MONITOR.map(function (spec) {
      var key = spec[0], label = spec[1], unit = spec[2], get = MONITOR_GET[key];
      if (OBS.lang === "en") { label = CP.MONITOR_EN[key][0]; unit = CP.MONITOR_EN[key][1]; }
      if (key === "rhr" && rhr_apple) label = CP.RHR_APPLE_LABEL[OBS.lang === "en" ? 1 : 0];
      var cur = latest ? get(latest) : null;
      var hist = rows.filter(function (r) { return latest === null || r.date < latest.date; }).map(get).filter(nn);
      var rng = monitor_range(hist, key), status = null;
      if (cur !== null && rng !== null) status = cur < rng[0] ? "low" : cur > rng[1] ? "high" : "in_range";
      return { key: key, label: label, unit: unit, value: cur === null ? null : U.round(cur, 2),
        date: latest && cur !== null ? latest.date : null,
        low: rng === null ? null : U.round(rng[0], 2), high: rng === null ? null : U.round(rng[1], 2),
        baseline: rng === null ? null : U.round(rng[2], 2), baseline_nights: hist.length, status: status,
        missing_reason: rng !== null && cur !== null ? null : cur === null ? "最近一晚没有这项读数"
          : "前 " + CP.MONITOR_DAYS + " 天只有 " + hist.length + " 晚（要 ≥ " + CP.MONITOR_MIN_NIGHTS + "）" };
    });
    var out_of_range = metrics.filter(function (m) { return m.status === "low" || m.status === "high"; }).map(function (m) { return m.key; });
    return { date: latest ? latest.date : null, metrics: metrics, out_of_range: out_of_range,
      all_in_range: metrics.every(function (m) { return m.status === null; }) ? null : !out_of_range.length };
  }

  // ================================================================ goals/sleep.py + goals/store.py
  var GP = { DEFAULT_PACE_MIN: 20, DEFAULT_STEP_DAYS: 3, WINDOW_NIGHTS: 7, MIN_NIGHTS: 3, TREND_NIGHTS: 14,
    MIN_TREND_NIGHTS: 5, ON_TARGET_TOL_MIN: 15, DEFAULT_DURATION_MIN: 480, HISTORY_DAYS: 21 };

  function parse_hhmm(s) {
    var p = String(s).trim().split(":");
    var h = parseInt(p[0], 10), m = parseInt(p[1], 10);
    if (p.length < 2 || isNaN(h) || isNaN(m) || h < 0 || h > 23 || m < 0 || m > 59)
      throw new Error("时间要写成 HH:MM（24 小时制），收到：" + s);
    return [h, m];
  }
  function to_off(t) { var hm = parse_hhmm(t); return mod(hm[0] * 60 + hm[1] + 720, 1440) - 720; }
  function fmt(off) {
    if (!nn(off)) return null;
    var m = mod(U.round(off), 1440);
    return pad2(Math.floor(m / 60)) + ":" + pad2(m % 60);
  }
  /** The evening a night belongs to: the local date of (wall time − 12 h). */
  function night_of(t, tz) {
    var p = U.local(us(t), U.tzOf(tz));
    return U.fromDayNum(Math.floor((U.dayNum(p.date) * U.US_DAY + p.hour * U.US_HOUR + p.minute * U.US_MIN + p.second * U.US_SEC
      + p.micro - 12 * U.US_HOUR) / U.US_DAY));
  }
  function wallOf(t, tz) {
    var p = U.local(us(t), tz);
    return U.dayNum(p.date) * U.US_DAY + p.hour * U.US_HOUR + p.minute * U.US_MIN + p.second * U.US_SEC + p.micro;
  }

  function nights_from_sessions(sessions, tz) {
    tz = U.tzOf(tz);
    var best = {};
    sessions.forEach(function (s) {
      if (!s.is_main || !nn(s.start_at) || !nn(s.end_at)) return;
      var n = night_of(s.start_at, tz), mid = U.dayNum(U.addDays(n, 1)) * U.US_DAY;
      var row = { night: n, bed_off: U.round((wallOf(s.start_at, tz) - mid) / 1e6 / 60),
        wake_off: U.round((wallOf(s.end_at, tz) - mid) / 1e6 / 60), asleep_min: s.asleep_min || 0 };
      row.bedtime = fmt(row.bed_off); row.wake = fmt(row.wake_off);
      if (!(n in best) || row.asleep_min > best[n].asleep_min) best[n] = row;
    });
    return keys(best).map(function (n) { return best[n]; });
  }

  function round5(x) { return Math.trunc(5 * U.round(x / 5)); }

  function build_plan(start_off, target_off, start_date, pace, step_days) {
    if (pace <= 0 || step_days <= 0) throw new Error("pace 和 step_days 要大于 0");
    var plan = [], k = 0;
    for (;;) {
      var off = Math.max(start_off - (k + 1) * pace, target_off);
      plan.push({ from_date: U.addDays(start_date, k * step_days), bed_off: off });
      if (off === target_off) return plan;
      k += 1;
    }
  }
  function stage_for(plan, night) { var cur = null; plan.forEach(function (st) { if (st.from_date <= night) cur = st; }); return cur; }
  function window_(nights, night, n) {
    var lo = U.addDays(night, -(n === undefined ? GP.WINDOW_NIGHTS : n));
    return nights.filter(function (x) { return lo <= x.night && x.night < night; });
  }
  function avg(rows, key, min_n) {
    var vals = rows.map(function (r) { return g(r, key); }).filter(nn);
    return vals.length >= (min_n === undefined ? GP.MIN_NIGHTS : min_n) ? U.round(U.fmean(vals), 1) : null;
  }
  function target_for(plan, nights, night, pace) {
    var st = stage_for(plan, night);
    if (st === null) return null;
    var recent = avg(window_(nights, night), "bed_off");
    return recent === null ? st.bed_off : Math.max(st.bed_off, round5(recent - pace));
  }
  function trend_per_week(nights, night) {
    var rows = window_(nights, night, GP.TREND_NIGHTS);
    if (rows.length < GP.MIN_TREND_NIGHTS) return null;
    var xs = rows.map(function (r) { return U.daysBetween(night, r.night); }), ys = rows.map(function (r) { return r.bed_off; });
    var mx = U.fmean(xs), my = U.fmean(ys);
    var sxx = U.pySum(xs.map(function (x) { return (x - mx) * (x - mx); }));
    if (sxx === 0) return null;
    return U.round(U.pySum(xs.map(function (x, i) { return (x - mx) * (ys[i] - my); })) / sxx * 7, 1);
  }

  function progress(goal, nights, night) {
    var recent = window_(nights, night);
    var out = { nights_7d: recent.length, avg_bedtime_7d: fmt(avg(recent, "bed_off")), avg_wake_7d: fmt(avg(recent, "wake_off")),
      trend_min_per_week: trend_per_week(nights, night), on_target_nights_7d: null, gap_min: null, eta_date: null,
      behind_plan_min: null };
    var a = avg(recent, "bed_off");
    if (!goal) return out;
    var target = to_off(goal.target_bedtime), plan = goal.plan_rows, on = 0;
    recent.forEach(function (x) {
      var t = target_for(plan, nights, x.night, goal.pace_min_per_step);
      if (t !== null && x.bed_off <= t + GP.ON_TARGET_TOL_MIN) on += 1;
    });
    out.on_target_nights_7d = on;
    if (a !== null) {
      var gap = U.round(a - target);
      out.gap_min = Math.max(gap, 0);
      var st = stage_for(plan, night);
      if (st !== null) out.behind_plan_min = U.round(a - st.bed_off);
      var trend = out.trend_min_per_week;
      if (gap <= 0) out.eta_date = night;
      else if (trend !== null && trend < 0) out.eta_date = U.addDays(night, Math.ceil(gap / -trend * 7));
    }
    return out;
  }

  function with_plan(row) {
    var out = Object.assign({}, row);
    out.plan_rows = build_plan(row.start_bedtime_off, to_off(row.target_bedtime), row.start_date, row.pace_min_per_step, row.step_days);
    return out;
  }
  function duration_from(goal, need) {
    if (goal && goal.target_duration_min) return goal.target_duration_min;
    return nn(need) ? need : GP.DEFAULT_DURATION_MIN;
  }

  function goal_compute(goal, nights, night, duration) {
    var prog = progress(goal, nights, night), recent = {};
    window_(nights, night).forEach(function (x) { recent[x.night] = x; });
    var out = { night: night, target_bedtime: null, target_wake: null, target_duration_min: null, pace_min_per_step: null,
      step_days: null, start_date: null, start_bedtime: null, status: null, tonight_target_bedtime: null,
      tonight_target_wake: null, plan: [], plan_end_date: null, progress: prog, recent_nights: [] };
    var goal_on = !!goal && goal.status === "active", tgt = {};
    if (goal) {
      var plan = goal.plan_rows, pace = goal.pace_min_per_step;
      keys(recent).forEach(function (d) { tgt[d] = target_for(plan, nights, d, pace); });
      ["target_bedtime", "target_wake", "pace_min_per_step", "step_days", "start_date", "status"].forEach(function (k) { out[k] = g(goal, k); });
      out.target_duration_min = goal.target_duration_min || duration;
      out.start_bedtime = fmt(goal.start_bedtime_off);
      out.plan = plan.map(function (st) { return { from_date: st.from_date, bedtime: fmt(st.bed_off) }; });
      out.plan_end_date = plan[plan.length - 1].from_date;
      if (goal_on) {
        var t = target_for(plan, nights, night, pace);
        out.tonight_target_bedtime = fmt(t);
        out.tonight_target_wake = goal.target_wake || (t !== null ? fmt(t + duration) : null);
      }
    }
    out.recent_nights = keys(recent).map(function (d) {
      var x = recent[d], t = nn(tgt[d]) ? tgt[d] : null;
      return { night: d, bedtime: x.bedtime, wake: x.wake, target: fmt(t),
        on_target: t === null ? null : x.bed_off <= t + GP.ON_TARGET_TOL_MIN };
    });
    return out;
  }

  /** goals/store.view_compute: goal = with_plan(row) or null; sessions = sleep_sessions of the 21 nights before + 1. */
  function view_compute(goal, sessions, tz, night, need) {
    var nights = nights_from_sessions(sessions, tz).filter(function (n) { return n.night < night; });
    return goal_compute(goal, nights, night, duration_from(goal, need));
  }
  function tonight(tz, now) { return night_of(now, tz); }

  // ================================================================ sleep_night/view.py
  var NP = { BASELINE_SHORT: 7, BASELINE_LONG: 30, MIN_BASELINE_NIGHTS: 3, PART_WEAK: 80, SHORT_OF_NEED_MIN: 30,
    SHORT_OF_NEED_BAD_MIN: 90, DEEP_LOW_PCT: 10, REM_LOW_PCT: 15, EFFICIENCY_GOOD: 85, EFFICIENCY_POOR: 75,
    LATENCY_LONG_MIN: 30, LATENCY_POOR_MIN: 46, WASO_LONG_MIN: 20, WASO_POOR_MIN: 51, BEDTIME_SHIFT_MIN: 60,
    HRV_LOW_PCT: 15, RHR_HIGH_BPM: 5, RESP_HIGH: 1.0, TEMP_DELTA_C: 0.6, SPO2_LOW: 94.0, MAX_NOTES: 6 };
  var STAGES = ["deep", "core", "rem", "unspecified", "awake"];
  var SCORE_PARTS = ["duration", "stages", "consistency", "interruptions"];
  var SCORE_WEIGHTS = { duration: SP.DURATION_IMPACT, stages: SP.STAGES_IMPACT, consistency: SP.CONSISTENCY_IMPACT,
    interruptions: SP.INTERRUPTIONS_IMPACT };
  var TONE_ORDER = { bad: 0, mid: 1, good: 2 };
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function r_(v, nd) { return !nn(v) ? null : (nd ? U.round(v, nd) : U.round(v)); }
  function mean_(xs) { xs = xs.filter(nn); return xs.length >= NP.MIN_BASELINE_NIGHTS ? U.fmean(xs) : null; }
  function clock_min(t, tz) { var p = U.local(us(t), tz); return p.hour * 60 + p.minute; }
  function circ_mean(minutes) {
    if (minutes.length < NP.MIN_BASELINE_NIGHTS) return null;
    var x = U.pySum(minutes.map(function (m) { return Math.cos(m / 1440 * 2 * Math.PI); }));
    var y = U.pySum(minutes.map(function (m) { return Math.sin(m / 1440 * 2 * Math.PI); }));
    var a = mod(Math.atan2(y, x), 2 * Math.PI);
    return mod(U.round(a / (2 * Math.PI) * 1440), 1440);
  }
  function circ_diff(a, b) { return mod(a - b + 720, 1440) - 720; }
  function hm(minutes) { return minutes === null ? null : pad2(mod(Math.floor(minutes / 60), 24)) + ":" + pad2(mod(minutes, 60)); }
  function dur(minutes) {
    var m = U.round(Math.abs(minutes));
    return L(m >= 60 ? Math.floor(m / 60) + " 小时 " + (m % 60) + " 分" : m + " 分钟",
      m >= 60 ? Math.floor(m / 60) + "h " + pad2(m % 60) + "m" : m + " min");
  }
  function md(d) { var mo = Number(d.slice(5, 7)), da = Number(d.slice(8, 10)); return L(mo + " 月 " + da + " 日", da + " " + MON[mo - 1]); }
  function efficiency(row, in_bed) { return in_bed && row.asleep_min ? U.round(Math.min(100.0, 100 * row.asleep_min / in_bed), 1) : null; }
  function inBed(row) { return (us(row.end_at) - us(row.start_at)) / 1e6 / 60; }

  function night_block(tz, row, sc, timeline) {
    var in_bed = inBed(row), s = (sc || {}).sleep || {}, eff = g(s, "efficiency");
    var minutes = {};
    STAGES.forEach(function (k) { minutes[k] = g(row, k + "_min"); });
    var staged = U.pySum(["deep", "core", "rem"].map(function (k) { return minutes[k] || 0; })) > 0;
    var total = U.pySum(STAGES.map(function (k) { return minutes[k] || 0; }));
    var comp = total ? STAGES.filter(function (k) { return minutes[k] || (staged && k !== "unspecified"); }).map(function (k) {
      return { stage: k, min: r_(minutes[k]), pct: r_(100 * (minutes[k] || 0) / total) };
    }) : [];
    var asleep = row.asleep_min || 0;
    var out = { start: hhmmOf(us(row.start_at), tz), end: hhmmOf(us(row.end_at), tz),
      start_at: U.isoformat(us(row.start_at), tz), end_at: U.isoformat(us(row.end_at), tz),
      in_bed_min: U.round(in_bed), asleep_min: g(row, "asleep_min") };
    STAGES.forEach(function (k) { out[k + "_min"] = minutes[k]; });
    out.efficiency_pct = nn(eff) ? U.round(100 * eff, 1) : efficiency(row, in_bed);
    out.latency_min = r_(g(s, "latency_min")); out.waso_min = r_(g(s, "waso_min")); out.awakenings = g(s, "awakenings");
    out.deep_pct = staged && asleep ? r_(100 * (minutes.deep || 0) / asleep) : null;
    out.rem_pct = staged && asleep ? r_(100 * (minutes.rem || 0) / asleep) : null;
    out.source = g(row, "source"); out.device = g(row, "device"); out.staged = staged; out.composition = comp;
    out.stages = timeline[0]; out.stages_source = timeline[1]; out.stages_reason = timeline[2];
    return out;
  }

  function score_block(sc) {
    if (!sc || !nn(sc.sleep_score)) return null;
    var s = sc.sleep || {};
    return { value: sc.sleep_score,
      parts: SCORE_PARTS.map(function (k) { return { key: k, score: g(s, k), weight: SCORE_WEIGHTS[k] }; }),
      performance_pct: nn(sc.sleep_performance) ? r_(100 * sc.sleep_performance) : null,
      actual_min: g(sc, "sleep_actual_min"), need_min: g(sc, "sleep_need_min"), debt_min: g(sc, "sleep_debt_min"),
      need_from_debt: g(s, "need_from_debt"), need_from_strain: g(s, "need_from_strain"), nap_min: g(s, "nap_min") };
  }

  function avg_block(mains, scores, tz) {
    var in_bed = mains.map(inBed);
    var staged = mains.filter(function (r) { return r.asleep_min && (r.deep_min || 0) + (r.core_min || 0) + (r.rem_min || 0) > 0; });
    var deep = staged.map(function (r) { return 100 * (r.deep_min || 0) / r.asleep_min; });
    var rem = staged.map(function (r) { return 100 * (r.rem_min || 0) / r.asleep_min; });
    var out = { nights: mains.length,
      asleep_min: r_(mean_(mains.map(function (r) { return g(r, "asleep_min"); }))),
      in_bed_min: r_(mean_(in_bed)),
      efficiency_pct: r_(mean_(mains.map(function (r, i) { return efficiency(r, in_bed[i]); })), 1),
      deep_min: r_(mean_(staged.map(function (r) { return r.deep_min || 0; }))),
      rem_min: r_(mean_(staged.map(function (r) { return r.rem_min || 0; }))),
      deep_pct: r_(mean_(deep)), rem_pct: r_(mean_(rem)),
      bedtime: hm(circ_mean(mains.map(function (r) { return clock_min(r.start_at, tz); }))),
      wake: hm(circ_mean(mains.map(function (r) { return clock_min(r.end_at, tz); }))) };
    if (scores !== null) out.sleep_score = r_(mean_(scores.map(function (s) { return g(s, "sleep_score"); })));
    return out;
  }

  function baseline_block(sessions, tz, day, mains, scores) {
    var out = {};
    [NP.BASELINE_SHORT, NP.BASELINE_LONG].forEach(function (n) {
      var days = [];
      for (var k = 1; k <= n; k++) days.push(U.addDays(day, -k));
      out["avg" + n] = avg_block(days.filter(function (d) { return d in mains; }).map(function (d) { return mains[d]; }),
        scores === null ? null : days.filter(function (d) { return d in scores; }).map(function (d) { return scores[d]; }), tz);
    });
    var SD = SER.sleep_detail, watch = SD.watch_sessions(sessions);
    var r7 = SD.sri_from(watch[0], watch[1], tz, U.addDays(day, -6), day);
    var r30 = SD.sri_from(watch[0], watch[1], tz, U.addDays(day, -29), day);
    out.regularity_pct_7d = SD.consistency_pct(r7.sri);
    out.regularity_pct_30d = SD.consistency_pct(r30.sri);
    return out;
  }

  var VITALS = [
    ["hrv", "ms", 0, function (s) { return g(s, "hrv_ms"); }], ["rhr", "bpm", 0, function (s) { return g(s, "rhr_night"); }],
    ["resp_rate", "rpm", 1, function (s) { return g(s, "resp_rate_night"); }],
    ["spo2", "%", 1, function (s) { return g(s.recovery || {}, "spo2_night"); }],
    ["wrist_temp", "°C", 2, function (s) { return g(s.recovery || {}, "wrist_temp"); }]];

  function vitals_block(day, scores) {
    var sc = scores[day];
    if (!sc) return [];
    var past = [];
    for (var k = 1; k <= NP.BASELINE_LONG; k++) { var d = U.addDays(day, -k); if (d in scores) past.push(scores[d]); }
    var out = [];
    VITALS.forEach(function (v) {
      var val = v[3](sc);
      if (!nn(val)) return;
      var a = mean_(past.map(v[3]));
      out.push({ key: v[0], value: r_(val, v[2]), unit: v[1], avg30: r_(a, v[2]), delta: a === null ? null : r_(val - a, v[2]) });
    });
    return out;
  }

  function note(key, tone, text) { return { key: key, tone: tone, text: text }; }

  function notes(night, score, base, vitals, flags) {
    var out = [], avg30 = base["avg" + NP.BASELINE_LONG] || {};
    if (score && score.need_min && nn(score.actual_min)) {
      var short = score.need_min - score.actual_min;
      if (short >= NP.SHORT_OF_NEED_MIN)
        out.push(note("short_of_need", short >= NP.SHORT_OF_NEED_BAD_MIN ? "bad" : "mid",
          L("比睡眠需求少 " + dur(short), dur(short) + " short of your sleep need")));
      else out.push(note("need_met", "good", L("睡够了需求", "Sleep need met")));
    }
    if (score) {
      var weak = score.parts.filter(function (p) { return nn(p.score) && p.score < NP.PART_WEAK; });
      if (weak.length) {
        var w = U.minBy(weak, function (p) { return p.score; });
        var name = { duration: ["时长", "duration"], stages: ["睡眠阶段", "sleep stages"], consistency: ["规律性", "consistency"],
          interruptions: ["中途醒来", "interruptions"] }[w.key];
        out.push(note("weak_" + w.key, w.score >= 50 ? "mid" : "bad",
          L("睡眠分里最拉分的是" + name[0] + "（" + w.score + "）", "Weakest part of the score: " + name[1] + " (" + w.score + ")")));
      }
    }
    if (nn(night.deep_pct) && night.deep_pct < NP.DEEP_LOW_PCT)
      out.push(note("deep_low", "mid", L("深睡只占睡着时间的 " + night.deep_pct + "%", "Deep sleep only " + night.deep_pct + "% of time asleep")));
    if (nn(night.rem_pct) && night.rem_pct < NP.REM_LOW_PCT)
      out.push(note("rem_low", "mid", L("REM 只占睡着时间的 " + night.rem_pct + "%", "REM only " + night.rem_pct + "% of time asleep")));
    var eff = g(night, "efficiency_pct");
    if (eff !== null && eff < NP.EFFICIENCY_GOOD)
      out.push(note("efficiency_low", eff < NP.EFFICIENCY_POOR ? "bad" : "mid",
        L("睡眠效率 " + fmtG(eff) + "%，躺着醒着的时间偏多", "Sleep efficiency " + fmtG(eff) + "%: a lot of time awake in bed")));
    var lat = g(night, "latency_min");
    if (lat !== null && lat >= NP.LATENCY_LONG_MIN)
      out.push(note("latency_long", lat >= NP.LATENCY_POOR_MIN ? "bad" : "mid", L("入睡用了 " + lat + " 分钟", "Took " + lat + " min to fall asleep")));
    var waso = g(night, "waso_min");
    if (waso !== null && waso >= NP.WASO_LONG_MIN) {
      var n = night.awakenings;
      out.push(note("waso_long", waso >= NP.WASO_POOR_MIN ? "bad" : "mid",
        L("睡着后醒了 " + waso + " 分钟" + (n ? "（" + n + " 次）" : ""), "Awake " + waso + " min after falling asleep" + (n ? " (" + n + "×)" : ""))));
    }
    if (avg30.bedtime && night.start) {
      var a = night.start.split(":").map(Number), b = avg30.bedtime.split(":").map(Number);
      var shift = circ_diff(a[0] * 60 + a[1], b[0] * 60 + b[1]);
      if (Math.abs(shift) >= NP.BEDTIME_SHIFT_MIN)
        out.push(note(shift > 0 ? "bedtime_late" : "bedtime_early", shift > 0 ? "mid" : "good",
          L("入睡比 30 晚平均" + (shift > 0 ? "晚" : "早") + " " + dur(shift),
            "Asleep " + dur(shift) + " " + (shift > 0 ? "later" : "earlier") + " than your 30-night average")));
    }
    var by = {};
    vitals.forEach(function (v) { by[v.key] = v; });
    var hrv = by.hrv;
    if (hrv && hrv.avg30 && hrv.value < hrv.avg30 * (1 - NP.HRV_LOW_PCT / 100)) {
      var pct = U.round(100 * (1 - hrv.value / hrv.avg30));
      out.push(note("hrv_low", "mid", L("HRV 比 30 晚平均低 " + pct + "%", "HRV " + pct + "% below your 30-night average")));
    }
    var rhr = by.rhr;
    if (rhr && nn(rhr.delta) && rhr.delta >= NP.RHR_HIGH_BPM)
      out.push(note("rhr_high", "mid", L("睡眠心率比平时高 " + fmtG(rhr.delta) + " bpm", "Sleeping HR " + fmtG(rhr.delta) + " bpm above usual")));
    var resp = by.resp_rate;
    if (resp && nn(resp.delta) && resp.delta >= NP.RESP_HIGH)
      out.push(note("resp_high", "mid", L("呼吸频率比平时高 " + fmtG(resp.delta), "Respiratory rate " + fmtG(resp.delta) + " above usual")));
    var temp = by.wrist_temp;
    if (temp && nn(temp.delta) && Math.abs(temp.delta) >= NP.TEMP_DELTA_C)
      out.push(note("temp_off", "mid", L("手腕温度比平时" + (temp.delta > 0 ? "高" : "低") + " " + fmtG(Math.abs(temp.delta)) + "°C",
        "Wrist temperature " + fmtG(Math.abs(temp.delta)) + "°C " + (temp.delta > 0 ? "above" : "below") + " usual")));
    var spo2 = by.spo2;
    if (spo2 && spo2.value < NP.SPO2_LOW)
      out.push(note("spo2_low", "bad", L("血氧中位数 " + fmtG(spo2.value) + "%", "Median SpO₂ " + fmtG(spo2.value) + "%")));
    if ((flags || {}).illness_warning)
      out.push(note("illness", "bad", L("静息心率高、HRV 低：可能在生病或过度疲劳", "High resting HR and low HRV: possibly ill or overreached")));
    if (!out.some(function (x) { return x.tone !== "good"; }))
      out.push(note("all_good", "good", L("这晚各项都在正常范围", "Everything within your normal range")));
    return stableSort(out, function (x, y) { return TONE_ORDER[x.tone] - TONE_ORDER[y.tone]; }).slice(0, NP.MAX_NOTES);
  }

  function ai_prompt(day, night, score) {
    if (night === null)
      return L(md(day) + "早上醒来的那晚我没有睡眠数据，可能是什么原因？", "I have no sleep data for the night ending " + md(day) + ". Why might that be?");
    var asleep = dur(night.asleep_min || 0);
    var sc = score ? "，睡眠分 " + score.value : "", sc_en = score ? ", sleep score " + score.value : "";
    return L("详细分析一下我 " + md(day) + " 早上醒来的那晚睡眠（" + night.start + " → " + night.end + "，睡着 " + asleep + sc + "）："
      + "阶段组成、和我平时比怎么样、哪里拉分，今晚怎么调整。",
      "Analyse my sleep in detail for the night ending " + md(day) + " (" + night.start + " → " + night.end + ", asleep " + asleep + sc_en + "): "
      + "stage composition, how it compares with my usual nights, what pulled it down, and what to change tonight.");
  }

  /** sleep_night/view.compute: data = {tz, today, day, can_scores, sessions (wake dates [day − 30, day], by start),
   * scores ({date: row} or null), prev, next, timeline ([stages, source, reason] or null)}. */
  function sleep_night_compute(data) {
    var tz = U.tzOf(data.tz), day = data.day, scores = nn(data.scores) ? data.scores : null, sessions = data.sessions || [];
    var mains = main_rows(sessions, U.addDays(day, -NP.BASELINE_LONG), day);
    var row = mains[day] || null, sc = (scores || {})[day] || null;
    var night = row ? night_block(tz, row, sc, data.timeline || [[], null, null]) : null;
    if (night !== null && !data.can_scores) { night.latency_min = null; night.waso_min = null; night.awakenings = null; }
    var naps = sessions.filter(function (r) { return r.wake_date === day && !r.is_main; }).map(function (r) {
      return { start: hhmmOf(us(r.start_at), tz), end: hhmmOf(us(r.end_at), tz), asleep_min: g(r, "asleep_min") };
    });
    var score = score_block(sc), base = baseline_block(sessions, tz, day, mains, scores);
    if (scores !== null && sc !== null) base.need_min = g(sc, "sleep_need_min");
    var vitals = scores !== null ? vitals_block(day, scores) : [];
    return { date: day, today: data.today, prev: g(data, "prev"), next: g(data, "next"), is_latest: !nn(data.next),
      main: night, naps: naps, score: score, baseline: base, vitals: vitals,
      notes: night ? notes(night, score, base, vitals, (sc || {}).flags) : [], reason: night ? null : "这天没有手表睡眠数据",
      ai_prompt: ai_prompt(day, night, score), hidden: data.can_scores ? [] : ["scores"] };
  }

  // ================================================================ web/views.py (the Today composition) + recovery_empty.py
  var TP = {
    RECOVERY_GREEN: 67, RECOVERY_RED: 34, LATEST_LOOKBACK_DAYS: 7, MERGE_GAP_US: 90 * U.US_SEC, SLEEP_LOOKBACK_DAYS: 7,
    FIELD_FLAGS: { recovery: ["no_sleep", "hrv_missing", "baseline_insufficient"], sleep_score: ["no_sleep"],
      sleep_actual_min: ["no_sleep"], sleep_need_min: ["no_sleep"], sleep_debt_min: ["no_sleep"], sleep_performance: ["no_sleep"],
      hrv_night_ms: ["no_sleep", "hrv_missing"], rhr_night: ["no_sleep"], strain: ["no_raw_hr", "day_in_progress"],
      muscle_load: ["muscle_load_unavailable"], ctl: ["load_missing"], atl: ["load_missing"], tsb: ["load_missing"] },
    FIELD_FALLBACK: { muscle_load: "这天 SparkyFitness 里没有力量训练记录" },
    SCORE_FIELDS: ["recovery", "sleep_score", "sleep_performance", "sleep_actual_min", "sleep_need_min", "sleep_debt_min",
      "hrv_night_ms", "rhr_night", "strain", "muscle_load", "ctl", "atl", "tsb"],
    RECOVERY_WORD: { green: "适合高强度", yellow: "按计划训练", red: "以恢复为主" },
    RECOVERY_WORD_EN: { green: "Ready to push", yellow: "Train as planned", red: "Focus on recovery" },
    LOAD_STATUS: { fresh: "新鲜", balanced: "平衡", loading: "积累疲劳", fatigued: "疲劳" },
    LOAD_STATUS_EN: { fresh: "Fresh", balanced: "Balanced", loading: "Building fatigue", fatigued: "Fatigued" }
  };

  function reasons(row, fields) {
    var out = {};
    (fields || TP.SCORE_FIELDS).forEach(function (f) {
      if (row && nn(row[f])) return;
      if (!row) { out[f] = "这天还没有计算评分（每小时 :10 自动算一次）"; return; }
      var flags = row.flags || {}, hit = null, fl = TP.FIELD_FLAGS[f] || [];
      for (var i = 0; i < fl.length; i++) if (Object.prototype.hasOwnProperty.call(flags, fl[i])) { hit = flags[fl[i]]; break; }
      out[f] = hit || TP.FIELD_FALLBACK[f] || "没算出来（flags 里没有写原因）";
    });
    return out;
  }
  function recovery_zone(v) { return !nn(v) ? null : v >= TP.RECOVERY_GREEN ? "green" : v >= TP.RECOVERY_RED ? "yellow" : "red"; }
  function recovery_word(zone) { return zone in TP.RECOVERY_WORD ? L(TP.RECOVERY_WORD[zone], TP.RECOVERY_WORD_EN[zone]) : null; }
  function load_status(row) {
    if (!row || !nn(row.tsb)) return null;
    var tsb = row.tsb, key = tsb >= 10 ? "fresh" : tsb >= -10 ? "balanced" : tsb >= -30 ? "loading" : "fatigued";
    return { key: key, label: L(TP.LOAD_STATUS[key], TP.LOAD_STATUS_EN[key]) };
  }
  function load_text(row, missing_days) {
    if (!row || !nn(row.tsb)) return "还没有 CTL / ATL：要有连续几周的负荷数据";
    var tsb = row.tsb, s;
    if (tsb >= 10) s = "状态新鲜：最近一周的负荷低于长期水平，适合安排高质量训练";
    else if (tsb >= -10) s = "负荷平衡：最近一周和长期水平差不多";
    else if (tsb >= -30) s = "在积累疲劳：最近一周练得比平时多，属于正常训练期，注意睡眠和吃够";
    else s = "疲劳偏高：最近一周负荷远高于长期水平，考虑减量或多休息一天";
    var text = s + "（体能 CTL " + fmt0(row.ctl) + "、疲劳 ATL " + fmt0(row.atl) + "、TSB " + fmt0(tsb, true) + "）";
    if (missing_days) text += "。最近 7 天有 " + missing_days + " 天没有负荷数据（CTL / ATL 沿用前一天），新鲜度可能被高估";
    return text;
  }

  /** web/views.scores_today_compute: rows {date: score row} of the 7 days up to day. */
  function scores_today(rows, day, last, last_sleep, computed_at, empty) {
    var today = rows[day] || null, yesterday = rows[U.addDays(day, -1)] || null;
    var missing = keys(rows).filter(function (d) {
      return d < day && Object.prototype.hasOwnProperty.call(rows[d].flags || {}, "load_missing");
    }).length;
    var zone = recovery_zone(today ? g(today, "recovery") : null);
    return { date: day, today: today, yesterday: yesterday, today_reasons: reasons(today), yesterday_reasons: reasons(yesterday),
      recovery_empty: nn(empty) ? empty : null, recovery_zone: zone, recovery_word: recovery_word(zone),
      load_status: load_status(yesterday),
      last_recovery: today && nn(today.recovery) ? null : (last ? Object.assign({}, last, { zone: recovery_zone(last.recovery) }) : null),
      last_sleep: today && nn(today.sleep_score) ? null : (nn(last_sleep) ? last_sleep : null),
      load_text: load_text(yesterday, missing), load_missing_days: missing, computed_at: nn(computed_at) ? computed_at : null };
  }

  function entry(code, label, cta, action) { return { code: code, label: label, cta: cta || null, action: action || null }; }
  function is_watch_sleep(row) {
    if (row.device && row.device.toLowerCase().indexOf("watch") >= 0) return true;
    return (row.core_min || 0) + (row.deep_min || 0) + (row.rem_min || 0) > 0;
  }
  /** web/recovery_empty.explain_from: origin = the scoring origin (first day with a resting heart rate) or null. */
  function recovery_empty(day, today, local_today, needs_consent, origin, any_data, recent_watch_sleep) {
    if (today && nn(today.recovery)) return null;
    if (needs_consent) return entry("consent", L("需要先确认同意书", "Review the consent form first"), L("去确认", "Review"), "consent");
    if (!nn(origin)) {
      if (any_data) return entry("no_watch", L("还没有手表数据", "No watch data yet"), L("手机同步", "Phone sync"), "sync");
      return entry("awaiting_sync", L("等待第一次同步", "Waiting for the first sync"), L("手机同步", "Phone sync"), "sync");
    }
    if (day < origin) return entry("no_day", L("这天还没有手表数据", "No watch data for this day"));
    var flags = (today || {}).flags || {}, is_today = day >= local_today;
    if (Object.prototype.hasOwnProperty.call(flags, "no_sleep") || !today) {
      if (!recent_watch_sleep) return entry("no_sleep", L("没有睡眠数据", "No sleep data"), L("开启睡眠追踪", "Turn on sleep tracking"), "sleep_help");
      if (today) {
        if (is_today) return entry("sleep_pending", L("等待昨晚的睡眠同步", "Waiting for last night's sleep"), L("手机同步", "Phone sync"), "sync");
        return entry("no_sleep_night", L("这天没有睡眠数据", "No sleep data for this night"));
      }
    }
    if (Object.prototype.hasOwnProperty.call(flags, "hrv_missing")) return entry("no_hrv", L("夜里没有 HRV 读数", "No overnight HRV reading"));
    if (Object.prototype.hasOwnProperty.call(flags, "baseline_insufficient")) return entry("baseline", L("还在建立你的基线", "Still building your baseline"));
    return entry("pending", L("还在计算", "Still being calculated"));
  }

  /** web/views.main_rows: {wake date: the main session with the most minutes asleep} of [start, end] (sessions by start). */
  function main_rows(sessions, start, end) {
    var out = {};
    sessions.forEach(function (r) {
      if (!r.is_main || !(start <= r.wake_date && r.wake_date <= end)) return;
      var cur = out[r.wake_date];
      if (!cur || (nn(r.asleep_min) && (!nn(cur.asleep_min) || r.asleep_min > cur.asleep_min))) out[r.wake_date] = r;
    });
    var sorted = {};
    keys(out).forEach(function (d) { sorted[d] = out[d]; });
    return sorted;
  }
  function latest_main(sessions, day) {
    var m = main_rows(sessions, U.addDays(day, -TP.LATEST_LOOKBACK_DAYS), day), ks = keys(m);
    return ks.length ? m[ks[ks.length - 1]] : null;
  }
  function recent_bedtimes(mains, tz) {
    tz = U.tzOf(tz);
    return mains.map(function (r) {
      var night = U.addDays(r.wake_date, -1), evening = U.combine(night, 18 * U.US_HOUR, tz);
      return { date: r.wake_date, night: night, bedtime: hhmmOf(us(r.start_at), tz), wake: hhmmOf(us(r.end_at), tz),
        bed_offset_min: U.round((us(r.start_at) - evening) / 1e6 / 60) };
    });
  }

  /** web/views.hypnogram: segments [{start, end, stage}] clipped to [start, end], same stages < 90 s apart merged. */
  function hypnogram(segments, start, end, tz) {
    tz = U.tzOf(tz);
    var a0 = us(start), b0 = us(end);
    var xs = segments.map(function (x) { return [us(x.start), us(x.end), x.stage]; })
      .filter(function (x) { return x[1] > a0 && x[0] < b0; });
    xs = stableSort(xs, function (p, q) { return p[0] - q[0] || p[1] - q[1]; });
    var out = [];
    xs.forEach(function (x) {
      var a = Math.max(x[0], a0), b = Math.min(x[1], b0), last = out[out.length - 1];
      if (b <= a) return;
      if (last && last.stage === x[2] && a - last._end <= TP.MERGE_GAP_US) { last._end = Math.max(last._end, b); return; }
      if (last && a < last._end) { a = last._end; if (b <= a) return; }
      out.push({ stage: x[2], _start: a, _end: b });
    });
    return out.map(function (x) {
      return { stage: x.stage, start: hhmmOf(x._start, tz), end: hhmmOf(x._end, tz),
        offset_min: U.round((x._start - a0) / 1e6 / 60, 1), minutes: U.round((x._end - x._start) / 1e6 / 60, 1) };
    });
  }

  /** web/views.sleep_today_compute: sessions = main sessions of the wake dates [day − 7, day] by start. */
  function sleep_today(sessions, day, tz, timeline) {
    tz = U.tzOf(tz);
    var row = latest_main(sessions, day), m6 = main_rows(sessions, U.addDays(day, -6), day);
    var recent = recent_bedtimes(keys(m6).map(function (d) { return m6[d]; }), tz);
    if (!row) return { date: day, main: null, reason: "最近 7 天没有手表睡眠数据", recent: recent };
    var tl = timeline || [[], null, null], main = { start: hhmmOf(us(row.start_at), tz), end: hhmmOf(us(row.end_at), tz),
      start_at: U.isoformat(us(row.start_at), tz), end_at: U.isoformat(us(row.end_at), tz), in_bed_min: U.round(inBed(row)) };
    ["asleep_min", "deep_min", "core_min", "rem_min", "awake_min", "unspecified_min"].forEach(function (k) { main[k] = g(row, k); });
    main.source = g(row, "source");
    return { date: row.wake_date, latest: row.wake_date !== day, main: main, stages: tl[0], stages_source: tl[1], recent: recent,
      stages_reason: tl[2] };
  }

  /** web/views.frequent_journal_compute: items {key, label_zh, kind, unit, category, sort} by (sort, key); counts {key: n}. */
  function frequent_journal(items, counts, days, limit, min_items) {
    days = days || 30; limit = limit || 8; min_items = min_items === undefined ? 6 : min_items;
    var by = {};
    items.forEach(function (i) { by[i.key] = i; });
    var ranked = Object.keys(counts).filter(function (k) { return k in by; }).sort(function (a, b) {
      return (counts[b] - counts[a]) || ((by[a].sort || 0) - (by[b].sort || 0)) || (a < b ? -1 : a > b ? 1 : 0);
    }).slice(0, limit);
    for (var i = 0; i < items.length && ranked.length < min_items; i++) if (ranked.indexOf(items[i].key) < 0) ranked.push(items[i].key);
    return ranked.map(function (k) { return Object.assign({}, by[k], { count: counts[k] || 0, days: days }); });
  }

  // ================================================================ insights/healthspan_store.py (the loader)
  var SEXES = { m: "male", male: "male", "男": "male", f: "female", female: "female", "女": "female" };
  var METRICS = ["step_count", "resting_heart_rate", "vo2_max", "body_mass"];
  var SPARKY_NOTE = ["SparkyFitness 读不到（", "），力量训练只看手表训练记录、体重只看 Apple 健康"];

  /** healthspan_store.from_parts: load_parts' rows -> the `data` of OBS.healthspan_snapshot (snapshot / weekly). */
  function from_parts(p) {
    var start = p.start, end = p.end, tz = U.tzOf(p.tz), H = HSS.VERSIONS["v1.1"];   // healthspan_store's H
    var d = { birth: g(p, "birth"), height: g(p, "height"), sex: SEXES[String(p.sex || "").trim().toLowerCase()] || null,
      notes: [], goal_kg: g(p, "goal_kg"), sleep: {}, mvpa: {}, zones: {}, waist: {}, strength: {}, workouts: {} };
    (p.scores || []).forEach(function (r) {
      if (nn(r.sleep_actual_min)) d.sleep[r.date] = r.sleep_actual_min / 60;
      if (nn(r.strain) && r.z && r.z.length) {
        d.zones[r.date] = r.z.slice();
        d.mvpa[r.date] = (r.z[1] + r.z[2]) + H.VIGOROUS_FACTOR * (r.z[3] + r.z[4]);
      }
    });
    var m = p.metrics || {};
    d.steps = Object.assign({}, m.step_count); d.rhr = Object.assign({}, m.resting_heart_rate);
    d.vo2 = Object.assign({}, m.vo2_max); d.weight = Object.assign({}, m.body_mass);
    var watch = SER.sleep_detail.watch_sessions(p.sleep || []);
    d.sleep_days = {};
    keys(watch[0]).forEach(function (w) { if (start <= w && w <= end) d.sleep_days[w] = HSS.asleep_minutes(watch[1], w, tz); });
    (p.workouts || []).forEach(function (r) {
      if (HSS.STRENGTH_TYPES.indexOf(r.type) >= 0) d.strength[r.local_date] = (d.strength[r.local_date] || 0.0) + r.duration_min;
      (d.workouts[r.local_date] = d.workouts[r.local_date] || []).push([r.type, r.duration_min, g(r, "hr_zone_min")]);
    });
    var sparky = {};
    (p.sparky_sessions || []).forEach(function (s) {
      var minutes = HSS.session_strength_minutes(s);
      if (minutes === null) return;
      var day = s.date.slice(0, 10);
      sparky[day] = (sparky[day] || 0) + minutes;
    });
    (p.sparky_weights || []).forEach(function (r) {
      if ("weight_kg" in r) d.weight[r.date] = r.weight_kg;
      if ("waist_cm" in r) d.waist[r.date] = r.waist_cm;
      if ("height_cm" in r && !d.height) d.height = r.height_cm;
    });
    if (p.sparky_error) d.notes.push(SPARKY_NOTE[0] + p.sparky_error + SPARKY_NOTE[1]);
    [sparky, p.imported || {}].forEach(function (days) {
      Object.keys(days).forEach(function (day) { d.strength[day] = Math.max(day in d.strength ? d.strength[day] : 0.0, days[day]); });
    });
    var first = [], sk = keys(d.strength);
    if (sk.length) first.push(sk[0]);
    if (p.origin) first.push(p.origin);
    d.strength_from = first.length ? first.sort()[0] : null;
    d.strength_journal = (p.journal || []).slice().sort();
    d.strength_plan = nn(p.plan) ? p.plan : null;
    return d;
  }

  /** healthspan_store.weekly_local: the healthspan_weekly rows from the scoring origin to today ([] without one / a birth date). */
  function weekly_local(p, today) {
    if (!p.origin || !p.birth) return [];
    return HSS.weekly(from_parts(p), p.origin, today);
  }

  // ================================================================ web/params.py (TodayParams.swift)
  var TODAY_PARAMS = {
    TRENDS_DAYS: 61, BASELINE_MIN_N: 14, SPARK_DAYS: 7, FLAT_Z: 0.5, FLAT_RELATIVE: 0.03,
    WATCH: [{ key: "hrv", bad: -1, z: 1.5, min_delta: 5, absolute: null }, { key: "resting_hr", bad: 1, z: 1.5, min_delta: 3, absolute: null },
      { key: "resp_rate", bad: 1, z: 1.5, min_delta: 1, absolute: null }, { key: "spo2", bad: -1, z: 1.5, min_delta: 1.5, absolute: null },
      { key: "wrist_temp", bad: 1, z: 0, min_delta: 0, absolute: 0.5 }],
    STATUS_MIN_TONE: { illness: "mid", hrv_status: "mid", training_status: "mid", resilience: "bad", body_battery: "bad",
      recovery_index: "bad", hr_recovery: "bad" },
    SEVERITY_CAP: 4.0, AT_THRESHOLD: 1.5, STATUS_BAD: 3.0, STATUS_MID: 1.6, MONITOR_OFF: 2.0, MAX_ITEMS: 3,
    STRESS_MEDIUM: 1.0, STRESS_HIGH: 2.0
  };

  // ================================================================ entry points (api.js style: stamped)
  function stamp(x) { x.algo_version = COACH_VERSION; x.bundle_version = OBS.VERSION; return x; }
  OBS.ALGO_VERSIONS.coach = COACH_VERSION;
  /** GET /strain-coach: today's and yesterday's score rows {recovery, strain, tsb, flags} (null = none). */
  OBS.strainCoach = function (today, yday, date) { return stamp(strain_coach_compute(today || null, yday || null, date)); };
  /** GET /sleep-coach. opts: {date, tz, goal (the bedtime-goal view with a target tonight, or null)}. */
  OBS.sleepCoach = function (rows, opts) { return stamp(sleep_coach_compute(rows || [], opts.date, opts.tz, opts.goal || null)); };
  /** GET /health-monitor: the score rows of the 60 days up to date. */
  OBS.healthMonitor = function (rows, date) { return stamp(health_monitor_compute(rows || [], date)); };
  /** GET /goals/sleep. goal: the stored goal row (with_plan is applied here) or null; opts {tz, night, need}. */
  OBS.sleepGoal = function (goal, sessions, opts) {
    return stamp(view_compute(goal ? with_plan(goal) : null, sessions || [], opts.tz, opts.night, nn(opts.need) ? opts.need : null));
  };
  /** GET /sleep/night (see sleep_night_compute). */
  OBS.sleepNight = function (data) { return stamp(sleep_night_compute(data)); };
  /** GET /today `scores`. opts: {date, last, last_sleep, computed_at, empty}. */
  OBS.todayScores = function (rows, opts) {
    return stamp(scores_today(rows || {}, opts.date, opts.last || null, opts.last_sleep || null, opts.computed_at, opts.empty));
  };
  /** GET /today `scores.recovery_empty`. f: {date, today, local_today, needs_consent, origin, any_data, recent_watch_sleep}. */
  OBS.recoveryEmpty = function (f) {
    return recovery_empty(f.date, f.today || null, f.local_today, !!f.needs_consent, f.origin || null, !!f.any_data, !!f.recent_watch_sleep);
  };
  /** GET /today `sleep`. opts: {date, tz, timeline: [stages, source, reason] | null}. */
  OBS.todaySleep = function (sessions, opts) { return stamp(sleep_today(sessions || [], opts.date, opts.tz, opts.timeline || null)); };
  OBS.hypnogram = function (segments, start, end, tz) { return hypnogram(segments || [], start, end, tz); };
  /** GET /journal/frequent. opts: {days, limit, min_items}. */
  OBS.frequentJournal = function (items, counts, opts) {
    opts = opts || {};
    return frequent_journal(items || [], counts || {}, opts.days, opts.limit, opts.min_items);
  };
  /** healthspan_weekly rows from the phone's tables (healthspan_store.weekly_local). */
  OBS.healthspanLocal = function (parts, today) { return weekly_local(parts, today).map(stamp); };

  return { COACH_VERSION: COACH_VERSION, params: CP, TODAY_PARAMS: TODAY_PARAMS, fmtG: fmtG, fmt0: fmt0,
    strain_coach_compute: strain_coach_compute, sleep_need_tonight: sleep_need_tonight, sleep_coach_compute: sleep_coach_compute,
    monitor_range: monitor_range, health_monitor_compute: health_monitor_compute,
    goals: { params: GP, to_off: to_off, parse_hhmm: parse_hhmm, fmt: fmt, night_of: night_of, nights_from_sessions: nights_from_sessions,
      round5: round5, build_plan: build_plan, stage_for: stage_for, window: window_, avg: avg, target_for: target_for,
      trend_per_week: trend_per_week, progress: progress, with_plan: with_plan, duration_from: duration_from, compute: goal_compute,
      view_compute: view_compute, tonight: tonight },
    sleep_night: { params: NP, compute: sleep_night_compute, notes: notes, ai_prompt: ai_prompt, avg_block: avg_block,
      night_block: night_block, vitals_block: vitals_block, score_block: score_block },
    today: { params: TP, reasons: reasons, recovery_zone: recovery_zone, load_status: load_status, load_text: load_text,
      scores_today: scores_today, recovery_empty: recovery_empty, is_watch_sleep: is_watch_sleep, main_rows: main_rows,
      latest_main: latest_main, recent_bedtimes: recent_bedtimes, hypnogram: hypnogram, sleep_today: sleep_today,
      frequent_journal: frequent_journal },
    healthspan: { from_parts: from_parts, weekly_local: weekly_local } };
})(OBS.util, OBS.trends, OBS.sleep, OBS.params, OBS.healthspan, OBS.healthspan_snapshot, OBS.series);
