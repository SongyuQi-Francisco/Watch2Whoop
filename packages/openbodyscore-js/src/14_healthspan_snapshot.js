/* healthspan_snapshot: body age on one day and the weekly series. Port of snapshot(), weekly() and their helpers in
body_engine insights/healthspan_store.py, plus asleep_minutes() of insights/sleep_detail.py (sleep regularity input).

`data` is what the server's healthspan_store.Data holds, as plain objects keyed by local date:
  {birth, sex ('male' | 'female' | null), height (cm), goal_kg, notes: [text],
   sleep: {d: hours}, mvpa: {d: min}, zones: {d: [5 zone minutes]}, steps: {d}, rhr: {d}, vo2: {d}, weight: {d: kg},
   waist: {d: cm}, sleep_days: {wake date: 1440 minute states (OBS.healthspan_snapshot.sleep_days)},
   strength: {d: minutes}, workouts: {d: [[type, minutes, zone minutes | null]]}, strength_from: date | null,
   strength_journal: [dates], strength_plan: {days_per_week, start_date} | null}
*/
OBS.healthspan_snapshot = (function (U, HS, H, H12) {
  "use strict";
  var DAY_MIN = 1440;
  var LABELS = {
    sleep_duration: ["平均睡眠时长", "sleep", "h"], sleep_regularity: ["作息规律性（SRI）", "sleep", ""],
    steps: ["日均步数", "activity", "步"], mvpa: ["每周中高强度运动", "activity", "分钟"], strength: ["每周力量训练", "activity", "分钟"],
    vo2max: ["VO₂max", "fitness", "mL/kg/min"], rhr: ["静息心率", "fitness", "bpm"], body_comp: ["身体成分", "fitness", ""],
    activity: ["活动（步数 + 训练）", "activity", "步当量"]
  };
  var PART_LABELS = {
    steps: ["手腕步数", "Wrist steps", "步", "steps"],
    exercise_equiv: ["不产生步数的训练（折算）", "Training without steps (converted)", "步当量", "step equiv."],
    strength: ["每周力量训练", "Weekly strength training", "分钟", "min"],
    mvpa: ["每周中高强度运动（参考，不重复计入）", "Weekly moderate-to-vigorous activity (reference, not counted again)", "分钟", "min"]
  };
  var STRENGTH_TYPES = ["strength_training", "traditional_strength_training", "functional_strength_training", "core_training",
    "cross_training"];
  var VERSIONS = { "v1.1": H, "v1.2": H12 };
  var CURRENT = H12;   // the version the server writes (healthspan_store.CURRENT)

  function has(o, k) { return o && Object.prototype.hasOwnProperty.call(o, k); }
  function getOr(o, k, dflt) { return has(o, k) ? o[k] : dflt; }

  /** Which minute of the 24-hour block [d−1 12:00, d 12:00) is asleep. intervals: [[start, end]] (microseconds). */
  function asleep_minutes(intervals, d, tz) {
    tz = U.tzOf(tz);
    var lo = U.combine(U.addDays(d, -1), 12 * U.US_HOUR, tz), hi = U.combine(d, 12 * U.US_HOUR, tz);
    var out = new Uint8Array(DAY_MIN);
    intervals.forEach(function (iv) {
      var a = iv[0], b = iv[1];
      if (b <= lo || a >= hi) return;
      var i0 = Math.max(0, Math.floor((a - lo) / U.US_MIN)), i1 = Math.min(DAY_MIN, Math.floor((b - lo) / U.US_MIN));
      for (var i = i0; i < i1; i++) out[i] = 1;
    });
    return out;
  }

  /** {wake date: minute states} for the wake dates with a watch main sleep (sleep_detail: data.sleep_days). */
  function sleep_days(intervals, wake_dates, tz) {
    var out = {};
    wake_dates.forEach(function (d) { out[d] = asleep_minutes(intervals, d, tz); });
    return out;
  }

  /** Strength minutes of one session (training_log shape): its duration, else its strength exercises', else 3 per set. */
  function session_strength_minutes(s) {
    var ex = (s.exercises || []).filter(function (e) { return (e.category || "strength") === "strength"; });
    if (!ex.length) return null;
    var minutes = s.total_duration_minutes || U.pySum(ex.map(function (e) { return e.duration_minutes || 0; }));
    if (!minutes) minutes = H.STRENGTH_MIN_PER_SET * U.pySum(ex.map(function (e) { return e.sets.length; }));
    return minutes;
  }

  function strength_coverage(data, as_of) {
    var weeks = H.STRENGTH_COVERAGE_WEEKS, lo = U.addDays(as_of, -(7 * weeks - 1));
    var recorded = {};
    Object.keys(data.strength || {}).forEach(function (d) { if (lo <= d && d <= as_of && data.strength[d] > 0) recorded[d] = true; });
    var nRecorded = Object.keys(recorded).length;
    var missed = (data.strength_journal || []).filter(function (d) { return lo <= d && d <= as_of && !recorded[d]; });
    if (missed.length >= H.STRENGTH_JOURNAL_MISSED_MIN) return "近 " + weeks + " 周日记里有 " + missed.length + " 天记了力量训练，这些天没有训练记录";
    var plan = data.strength_plan;
    if (plan && plan.start_date <= lo && nRecorded < H.STRENGTH_PLAN_MIN_SHARE * plan.days_per_week * weeks)
      return "训练计划每周练 " + plan.days_per_week + " 天，近 " + weeks + " 周只有 " + nRecorded + " 天有力量训练记录";
    return null;
  }

  function sri_weighted(days, as_of, p) {
    var lo = U.addDays(as_of, -(p.WINDOW_DAYS - 1)), recent = U.addDays(as_of, -(p.RECENT_DAYS - 1));
    var same = 0.0, total = 0.0, pairs = 0;
    Object.keys(days || {}).forEach(function (d) {
      var nx = U.addDays(d, 1), cur = days[d], nxt = days[nx];
      if (!nxt || !(lo <= d && nx <= as_of)) return;
      var w = d >= recent ? p.RECENT_WEIGHT : 1.0, eq = 0;
      for (var i = 0; i < Math.min(cur.length, nxt.length); i++) if (cur[i] === nxt[i]) eq++;
      same += w * eq; total += w * DAY_MIN; pairs += 1;
    });
    return [total ? 200 * same / total - 100 : null, pairs];
  }

  /** The newest [date, value] within max_age days (Python max over (date, value) tuples). */
  function latest(values, as_of, max_age) {
    var lo = U.addDays(as_of, -max_age), best = null;
    Object.keys(values || {}).forEach(function (d) {
      if (lo <= d && d <= as_of && (best === null || U.cmp([d, values[d]], best) > 0)) best = [d, values[d]];
    });
    return best;
  }

  function tracked_days(data, as_of, p) {
    if (!data.strength_from) return [];
    var lo = data.strength_from > U.addDays(as_of, -(p.WINDOW_DAYS - 1)) ? data.strength_from : U.addDays(as_of, -(p.WINDOW_DAYS - 1));
    return lo <= as_of ? U.dateRange(lo, as_of) : [];
  }

  function activity_domain(data, as_of, age, p, add, missing) {
    if (age === null) { missing("activity", "没有生日，没法对照同龄人"); return; }
    var sw = HS.weighted_mean(data.steps || {}, as_of, p), steps = sw[0], n = sw[1];
    var has_steps = n >= p.MIN_DAYS;
    var days = tracked_days(data, as_of, p), workouts = data.workouts || {};
    var eq = {}, strength = {}, mvpa = {};
    var nonstep = function (t) { return p.NONSTEP_TYPES.indexOf(t) >= 0; };
    days.forEach(function (d) {
      var s_min = getOr(data.strength, d, 0.0), ws = workouts[d] || [];
      strength[d] = s_min;
      eq[d] = U.pySum(ws.filter(function (w) { return nonstep(w[0]); }).map(function (w) { return HS.step_equivalents(w[1], p.WORKOUT_MET[w[0]], p); }));
      if (p.STRENGTH_IN_VOLUME) eq[d] += HS.step_equivalents(s_min, p.STRENGTH_MET, p);
    });
    Object.keys(data.zones || {}).forEach(function (d) {
      var z = data.zones[d];
      var ws = (workouts[d] || []).filter(function (w) { return STRENGTH_TYPES.indexOf(w[0]) >= 0 || nonstep(w[0]); });
      var out = [0, 1, 2, 3, 4].map(function (i) {
        return Math.max(0.0, z[i] - U.pySum(ws.map(function (w) { return (w[2] || [0.0, 0.0, 0.0, 0.0, 0.0])[i]; })));
      });
      mvpa[d] = (out[1] + out[2]) + p.VIGOROUS_FACTOR * (out[3] + out[4])
        + HS.moderate_equiv_minutes(getOr(data.strength, d, 0.0), p.STRENGTH_MET, p)
        + U.pySum(ws.filter(function (w) { return nonstep(w[0]); }).map(function (w) { return HS.moderate_equiv_minutes(w[1], p.WORKOUT_MET[w[0]], p); }));
    });
    var em = HS.weighted_mean(eq, as_of, p), eq_mean = em[0], n_eq = em[1];
    var tracked = n_eq >= p.MIN_DAYS;
    var str_week = tracked ? HS.weighted_mean(strength, as_of, p)[0] * 7 : null;
    if (!has_steps && !tracked) { missing("activity", "只有 " + n + " 天步数（要 ≥ " + p.MIN_DAYS + "），也还没有训练记录"); return; }
    var raw = function (hr) { return HS.years_from_hr(hr, Infinity, p); };
    var parts = [], total, r, y_steps;
    if (has_steps) {
      var wrist = steps * p.WRIST_STEPS_FACTOR;
      total = wrist + (tracked ? eq_mean : 0.0);
      r = HS.hr_activity(total, str_week, age, p);
      y_steps = raw(HS.hr_steps(wrist, age, p));
      var part = { key: "steps", value: U.round(steps), unit: PART_LABELS.steps[2], data_days: n, years: U.round(y_steps, 2) };
      if (p.WRIST_STEPS_FACTOR !== 1) part.wrist_factor = p.WRIST_STEPS_FACTOR;
      parts.push(part);
    } else {
      missing("steps", "只有 " + n + " 天步数（要 ≥ " + p.MIN_DAYS + "），活动只看力量训练");
      total = null;
      var hs = HS.hr_strength(str_week, p);
      r = { hr: hs, hr_volume: 1.0, hr_strength: hs };
    }
    if (tracked) {
      if (has_steps) parts.push({ key: "exercise_equiv", value: U.round(eq_mean), unit: PART_LABELS.exercise_equiv[2], data_days: n_eq,
        years: U.round(raw(r.hr_volume) - y_steps, 2) });
      parts.push({ key: "strength", value: U.round(str_week), unit: PART_LABELS.strength[2], data_days: n_eq,
        hr: U.round(r.hr_strength, 3), years: U.round(raw(r.hr_strength), 2) });
    } else missing("strength", "开始记录训练只有 " + n_eq + " 天（要 ≥ " + p.MIN_DAYS + "），活动只看步数");
    var why = tracked ? strength_coverage(data, as_of) : null;
    var cover = why ? { coverage: "possibly_incomplete", coverage_reason: why } : {};
    if (why) parts.filter(function (x) { return x.key === "strength"; }).forEach(function (x) { Object.assign(x, cover); });
    var vm = HS.weighted_mean(mvpa, as_of, p);
    if (vm[1] >= p.MIN_DAYS) parts.push({ key: "mvpa", value: U.round(vm[0] * 7), unit: PART_LABELS.mvpa[2], data_days: vm[1], counted: false });
    parts.forEach(function (x) { x.label = PART_LABELS[x.key][0]; });
    add("activity", total === null ? null : U.round(total), r.hr, has_steps ? n : n_eq,
      Object.assign({ years_uncapped: U.round(raw(r.hr), 2), parts: parts }, cover),
      HS.years_from_hr(r.hr, p.ACTIVITY_CAP_YEARS, p), null, why ? H.TIPS.strength_incomplete : null);
  }

  /** Body age on one day (window = 180 days back from as_of). params: OBS.healthspan_params(_v12) (default v1.2). */
  function snapshot(data, as_of, params) {
    var p = params || CURRENT, items = [], insufficient = [];
    function add(key, value, hr, data_days, extra, years, goal_kg, tip) {
      var lb = LABELS[key];
      years = years === null || years === undefined ? HS.years_from_hr(hr, null, p) : years;
      var item = { key: key, label: lb[0], category: lb[1], value: value, unit: lb[2], hr: U.round(hr, 3), years: U.round(years, 2),
        direction: years < 0 ? "younger" : years > 0 ? "older" : "neutral", tip: tip || HS.tip(key, years, value, goal_kg),
        data_days: data_days };
      Object.keys(extra || {}).forEach(function (k) { item[k] = extra[k]; });
      items.push(item);
    }
    function missing(key, reason) { insufficient.push({ key: key, label: LABELS[key][0], category: LABELS[key][1], reason: reason }); }

    var v = HS.weighted_mean(data.sleep || {}, as_of, p);
    if (v[1] >= p.MIN_DAYS) add("sleep_duration", U.round(v[0], 2), HS.hr_sleep_duration(v[0], p), v[1],
      { self_report_equiv_h: U.round(HS.self_report_hours(v[0], p), 2) });
    else missing("sleep_duration", "180 天里只有 " + v[1] + " 晚手表睡眠（要 ≥ " + p.MIN_DAYS + "）");
    v = sri_weighted(data.sleep_days, as_of, p);
    if (v[1] >= p.MIN_DAYS) add("sleep_regularity", U.round(v[0], 1), HS.hr_regularity(v[0], p), v[1]);
    else missing("sleep_regularity", "只有 " + v[1] + " 对相邻两晚都有手表睡眠（要 ≥ " + p.MIN_DAYS + "）");

    var age = data.birth ? HS.chrono_age(data.birth, as_of) : null;
    if ((p.ACTIVITY_MODEL || "items") === "domain") activity_domain(data, as_of, age, p, add, missing);
    else {
      v = HS.weighted_mean(data.steps || {}, as_of, p);
      if (v[1] >= p.MIN_DAYS && age !== null) add("steps", U.round(v[0]), HS.hr_steps(v[0], age, p), v[1]);
      else missing("steps", "只有 " + v[1] + " 天步数（要 ≥ " + p.MIN_DAYS + "）");
      v = HS.weighted_mean(data.mvpa || {}, as_of, p);
      if (v[1] >= p.MIN_DAYS) add("mvpa", U.round(v[0] * 7), HS.hr_mvpa(v[0] * 7, p), v[1]);
      else missing("mvpa", "只有 " + v[1] + " 天有足够的原始心率（要 ≥ " + p.MIN_DAYS + "）");
      var days = tracked_days(data, as_of, p), sv = [null, 0];
      if (days.length) { var sd = {}; days.forEach(function (d) { sd[d] = getOr(data.strength, d, 0.0); }); sv = HS.weighted_mean(sd, as_of, p); }
      if (sv[1] >= p.MIN_DAYS) {
        add("strength", U.round(sv[0] * 7), HS.hr_strength(sv[0] * 7, p), sv[1]);
        var why = strength_coverage(data, as_of);
        if (why) Object.assign(items[items.length - 1], { coverage: "possibly_incomplete", coverage_reason: why, tip: H.TIPS.strength_incomplete });
      } else missing("strength", "开始记录训练只有 " + sv[1] + " 天（要 ≥ " + p.MIN_DAYS + "）");
    }

    var wlo = U.addDays(as_of, -(p.WINDOW_DAYS - 1));
    var vo2 = Object.keys(data.vo2 || {}).filter(function (d) { return wlo <= d && d <= as_of; }).map(function (d) { return [d, data.vo2[d]]; });
    var latestVo2 = vo2.length ? vo2.reduce(function (a, b) { return U.cmp(b, a) > 0 ? b : a; })[0] : null;
    var norm = age !== null && data.sex ? HS.vo2_norm(age, data.sex, p) : null;
    if (!data.sex) missing("vo2max", "没填性别（users.sex），没法对照同龄常模");
    else if (vo2.length < p.VO2_MIN_READINGS || U.daysBetween(latestVo2, as_of) > p.VO2_MAX_AGE_DAYS)
      missing("vo2max", "180 天里只有 " + vo2.length + " 次 VO₂max 读数或最近一次超过 " + p.VO2_MAX_AGE_DAYS + " 天"
        + "（要 ≥ " + p.VO2_MIN_READINGS + " 次；户外走路 / 跑步 20 分钟以上手表才会估）");
    else {
      var vo2d = {};
      vo2.forEach(function (x) { vo2d[x[0]] = x[1]; });
      v = HS.weighted_mean(vo2d, as_of, p);
      add("vo2max", U.round(v[0], 1), HS.hr_vo2(v[0], norm, p), v[1],
        { norm_median: U.round(norm, 1), readings: vo2.length, percentile: HS.vo2_percentile(v[0], age, data.sex, p) });
    }
    v = HS.weighted_mean(data.rhr || {}, as_of, p);
    if (v[1] >= p.MIN_DAYS) add("rhr", U.round(v[0], 1), HS.hr_rhr(v[0], p), v[1]);
    else missing("rhr", "只有 " + v[1] + " 天静息心率（要 ≥ " + p.MIN_DAYS + "）");
    var w = latest(data.weight, as_of, p.WEIGHT_MAX_AGE_DAYS), waist = latest(data.waist, as_of, p.WINDOW_DAYS);
    var goal = data.goal_kg;
    goal = goal && w && goal < w[1] ? goal : null;
    if (waist && data.height) {
      var whtr = waist[1] / data.height;
      add("body_comp", U.round(whtr, 3), HS.hr_whtr(whtr, p), 1, { measure: "waist_to_height", measured_on: waist[0] }, null, goal);
    } else if (w && data.height) {
      var bmi = w[1] / Math.pow(data.height / 100, 2);
      add("body_comp", U.round(bmi, 1), HS.hr_bmi(bmi, p), 1,
        { measure: "bmi", measured_on: w[0], weight_kg: w[1], note: "没有腰围，用 BMI（分不清肌肉和脂肪）" }, null, goal);
    } else missing("body_comp", data.height ? "最近 " + p.WEIGHT_MAX_AGE_DAYS + " 天没有体重或没有身高" : "没有身高");
    var delta = items.length ? HS.combine(items, p) : null;
    var sorted = items.slice().sort(function (a, b) { return Math.abs(b.years) - Math.abs(a.years); });
    return { as_of: as_of, chrono_age: age !== null ? U.round(age, 2) : null, delta_years: delta === null ? null : U.round(delta, 2),
      body_age: delta === null || age === null ? null : U.round(age + delta, 1), contributors: sorted, insufficient: insufficient };
  }

  /** [[week_start Monday, as_of]]: one per Sunday, the last being the week containing today (as_of = today). */
  function week_ends(first, today) {
    var out = [], monday = U.addDays(first, -U.weekday(first));
    while (monday <= today) {
      var sun = U.addDays(monday, 6);
      out.push([monday, sun < today ? sun : today]);
      monday = U.addDays(monday, 7);
    }
    return out;
  }

  /** The healthspan_weekly rows from the scoring origin to today (pace of aging over the snapshots so far). */
  function weekly(data, origin, today, params) {
    var p = params || CURRENT, rows = [], history = [];
    week_ends(U.addDays(origin, p.MIN_DAYS), today).forEach(function (we) {
      var snap = snapshot(data, we[1], p);
      history.push([we[1], snap.delta_years]);
      var pc = HS.pace(history, we[1], p);
      snap.pace_of_aging = pc.pace; snap.pace_raw = pc.pace_raw; snap.pace_window_weeks = pc.window_weeks;
      snap.week_start = we[0];
      snap.notes = data.notes || [];
      rows.push(snap);
    });
    return rows;
  }

  return { DAY_MIN: DAY_MIN, LABELS: LABELS, PART_LABELS: PART_LABELS, STRENGTH_TYPES: STRENGTH_TYPES, VERSIONS: VERSIONS,
    CURRENT: CURRENT, asleep_minutes: asleep_minutes, sleep_days: sleep_days, session_strength_minutes: session_strength_minutes,
    strength_coverage: strength_coverage, snapshot: snapshot, week_ends: week_ends, weekly: weekly };
})(OBS.util, OBS.healthspan, OBS.healthspan_params, OBS.healthspan_params_v12);
