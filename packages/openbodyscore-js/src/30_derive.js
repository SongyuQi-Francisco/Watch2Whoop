/* derive (2.0.0 package M1, docs/ios-native-2.0.md section 4): the phone's daily values and sleep from its own HealthKit
rows, a port of body_engine rawdata/derive.py (daily_values, phase1_rows, ow_sessions, clean_stages, nights_of,
session_rows, derive_days), importers/common.py (normalize, snake, sessionize, basal_reference, incomplete_days, choose,
resolve) and importers/ow.sleep_segments. The Python is the reference; goldens: test/derive.test.js against
test/golden/derive.json (body_engine/tests/test_js_golden_derive.py).

Entry point: OBS.deriveDays(src) (the twin of derive.derive_days), src = {
  tz, today, days: [YYYY-MM-DD], wake_dates: [YYYY-MM-DD],
  samples: [{uuid, type, start, end (UTC seconds), value (the registry's HealthKit unit), source, source_id, device_type,
             device_name}] or {cols, rows},
  buckets: [{type, start (UTC seconds), value}] or {cols, rows}   (15-minute statistics buckets),
  units: "hk" (default) | "stored",
  other: [daily_metric_sources rows of the other sources (date, metric, source, value, unit, agg, n, min_value, max_value,
          flags)], app_history: [the phone's own 'app' rows of earlier days], workout_kcal: {date: kcal}}
-> {algo_version, bundle_version, daily: {day: {sources, chosen}}, sleep: {wake date: [sessions]}}.
Inside, instants are integer microseconds (util); a stage segment also keeps the UTC offset of its start and end
(importers/ow._local pins each time to the offset of that moment), which decides the wake date across a DST change.
*/
OBS.derive = (function (U) {
  "use strict";
  var US_MIN = U.US_MIN, US_HOUR = U.US_HOUR, US_SEC = U.US_SEC;

  // ------------------------------------------------------------------ params (mirrors of the Python, D7)
  // importers/common.py (source choice, sleep segmentation) and rawdata/params.py (OW's sleep rules), rawdata/engine.py
  // (MAX_SAMPLE_SPAN). The golden file carries the Python values; test/derive.test.js checks these are equal.
  var PARAMS = {
    ALGO_VERSION: "derive-1-2026-10-30",                // rawdata/derive.DERIVE_VERSION
    SOURCE_PRIORITY: { shortcut: 1, apple_export: 2, ow: 3, app: 4 },
    DEGRADED_FLAGS: ["hourly_sum", "incomplete"],
    ENERGY_METRICS: ["active_energy_burned", "basal_energy_burned"],
    INCOMPLETE_BASAL_SHARE: 0.5,                         // basal < 50 % of the usual day = partial (common.py)
    BASAL_REF_DAYS: 60,
    BASAL_REF_MIN_DAYS: 7,
    WORKOUT_KCAL_SLACK: 20,
    SLEEP_GAP_MIN: 60,                                   // a gap over 60 minutes splits sleep segments
    WAKE_DAY_SHIFT_H: 4,                                 // a night belongs to the day of (wake − 4 h)
    MIN_ASLEEP_MIN: 15,
    SLEEP_STAGES: { 0: "in_bed", 1: "sleeping", 2: "awake", 3: "light", 4: "deep", 5: "rem" },
    SESSION_GAP_MIN: 120,                                // Open Wearables' sleep_end_gap_minutes
    SESSION_START: ["deep", "in_bed", "light", "rem", "sleeping"],
    DETAILED_STAGES: ["deep", "light", "rem"],
    ASLEEP_STAGES: ["deep", "light", "rem", "sleeping"],
    SLEEP_DAYS_BEFORE: 1,
    SLEEP_DAYS_AFTER: 3,
    MAX_SAMPLE_SPAN_S: 7 * 86400
  };
  var APP = "app";
  var SLEEP = "HKCategoryTypeIdentifierSleepAnalysis";
  var STAGE_MAP = { light: "core", deep: "deep", rem: "rem", awake: "awake", sleeping: "unspecified" };   // importers/ow.py
  var DEVICE_PRIORITY = { watch: 0, band: 0, ring: 0, phone: 1 };

  // rawdata/hk_types.ENGINE_TYPES (quantities and categories): [hk, key, kind, agg, factor, store_unit, lo, hi, stats, phase1]
  // phase1 = one of hk_types.HK_TYPES, whose daily value is rawdata/quantities._day_rows' (largest single source for sums)
  var Q = "HKQuantityTypeIdentifier", C = "HKCategoryTypeIdentifier";
  var TYPE_ROWS = [
    [C + "SleepAnalysis", "sleep_analysis", "category", null, 1.0, "", 0, 5, 0, 0],
    [Q + "HeartRateVariabilitySDNN", "heart_rate_variability_sdnn", "quantity", "avg", 1.0, "ms", 1, 500, 0, 0],
    [Q + "HeartRateVariabilityRMSSD", "heart_rate_variability_rmssd", "quantity", "avg", 1.0, "ms", 1, 500, 0, 0],
    [Q + "RestingHeartRate", "resting_heart_rate", "quantity", "avg", 1.0, "count/min", 20, 200, 0, 0],
    [Q + "RespiratoryRate", "respiratory_rate", "quantity", "avg", 1.0, "count/min", 4, 60, 0, 0],
    [Q + "OxygenSaturation", "oxygen_saturation", "quantity", "avg", 100.0, "%", 50, 100, 0, 0],
    [Q + "AppleSleepingWristTemperature", "apple_sleeping_wrist_temperature", "quantity", "avg", 1.0, "degC", 25, 45, 0, 1],
    [Q + "VO2Max", "vo2_max", "quantity", "avg", 1.0, "mL/min·kg", 10, 100, 0, 0],
    [Q + "BodyMass", "body_mass", "quantity", "avg", 1.0, "kg", 20, 400, 0, 0],
    [Q + "BodyFatPercentage", "body_fat_percentage", "quantity", "avg", 100.0, "%", 2, 75, 0, 0],
    [Q + "LeanBodyMass", "lean_body_mass", "quantity", "avg", 1.0, "kg", 10, 300, 0, 0],
    [Q + "BodyMassIndex", "body_mass_index", "quantity", "avg", 1.0, "count", 8, 90, 0, 0],
    [Q + "Height", "height", "quantity", "avg", 1.0, "cm", 50, 260, 0, 0],
    [Q + "BodyTemperature", "body_temperature", "quantity", "avg", 1.0, "degC", 30, 45, 0, 0],
    [Q + "BloodPressureSystolic", "blood_pressure_systolic", "quantity", "avg", 1.0, "mmHg", 40, 300, 0, 0],
    [Q + "BloodPressureDiastolic", "blood_pressure_diastolic", "quantity", "avg", 1.0, "mmHg", 20, 200, 0, 0],
    [Q + "BloodGlucose", "blood_glucose", "quantity", "avg", 1.0, "mmol/L", 0.5, 50, 0, 0],
    [C + "MindfulSession", "mindful_session", "category", null, 1.0, "", 0, 0, 0, 0],
    [Q + "WorkoutEffortScore", "workout_effort_score", "quantity", "avg", 1.0, "appleEffortScore", 0, 10, 0, 0],
    [Q + "EstimatedWorkoutEffortScore", "estimated_workout_effort_score", "quantity", "avg", 1.0, "appleEffortScore", 0, 10, 0, 0],
    [Q + "StepCount", "step_count", "quantity", "sum", 1.0, "count", 0, 100000, 1, 0],
    [Q + "DistanceWalkingRunning", "distance_walking_running", "quantity", "sum", 1.0, "km", 0, 100, 1, 0],
    [Q + "DistanceCycling", "distance_cycling", "quantity", "sum", 1.0, "km", 0, 300, 1, 0],
    [Q + "DistanceSwimming", "distance_swimming", "quantity", "sum", 1.0, "m", 0, 20000, 1, 0],
    [Q + "SwimmingStrokeCount", "swimming_stroke_count", "quantity", "sum", 1.0, "count", 0, 20000, 1, 0],
    [Q + "FlightsClimbed", "flights_climbed", "quantity", "sum", 1.0, "count", 0, 500, 1, 0],
    [Q + "ActiveEnergyBurned", "active_energy_burned", "quantity", "sum", 1.0, "kcal", 0, 5000, 1, 0],
    [Q + "BasalEnergyBurned", "basal_energy_burned", "quantity", "sum", 1.0, "kcal", 0, 5000, 1, 0],
    [Q + "AppleExerciseTime", "apple_exercise_time", "quantity", "sum", 1.0, "min", 0, 1440, 1, 1],
    [Q + "AppleStandTime", "apple_stand_time", "quantity", "sum", 1.0, "min", 0, 1440, 1, 1],
    [Q + "TimeInDaylight", "time_in_daylight", "quantity", "sum", 1.0, "min", 0, 1440, 1, 1],
    [Q + "PhysicalEffort", "physical_effort", "quantity", "sum", 1.0, "kcal/hr·kg", 0, 40, 0, 1],
    [Q + "WalkingHeartRateAverage", "walking_heart_rate_average", "quantity", "avg", 1.0, "count/min", 25, 230, 0, 1],
    [Q + "WalkingSpeed", "walking_speed", "quantity", "avg", 1.0, "km/hr", 0, 20, 0, 0],
    [Q + "WalkingStepLength", "walking_step_length", "quantity", "avg", 1.0, "cm", 10, 250, 0, 0],
    [Q + "WalkingDoubleSupportPercentage", "walking_double_support_percentage", "quantity", "avg", 1.0, "%", 0, 1, 0, 0],
    [Q + "WalkingAsymmetryPercentage", "walking_asymmetry_percentage", "quantity", "avg", 1.0, "%", 0, 1, 0, 0],
    [Q + "SixMinuteWalkTestDistance", "six_minute_walk_test_distance", "quantity", "avg", 1.0, "m", 0, 1500, 0, 0],
    [Q + "AppleWalkingSteadiness", "apple_walking_steadiness", "quantity", "avg", 1.0, "%", 0, 1, 0, 1],
    [Q + "StairAscentSpeed", "stair_ascent_speed", "quantity", "avg", 1.0, "m/s", 0, 5, 0, 1],
    [Q + "StairDescentSpeed", "stair_descent_speed", "quantity", "avg", 1.0, "m/s", 0, 5, 0, 1],
    [Q + "RunningSpeed", "running_speed", "quantity", "avg", 1.0, "km/hr", 0, 45, 0, 0],
    [Q + "RunningStrideLength", "running_stride_length", "quantity", "avg", 1.0, "m", 0.1, 4, 0, 0],
    [Q + "RunningPower", "running_power", "quantity", "avg", 1.0, "W", 0, 2500, 0, 0],
    [Q + "RunningVerticalOscillation", "running_vertical_oscillation", "quantity", "avg", 1.0, "cm", 0, 40, 0, 0],
    [Q + "RunningGroundContactTime", "running_ground_contact_time", "quantity", "avg", 1.0, "ms", 50, 2000, 0, 0],
    [Q + "CyclingSpeed", "cycling_speed", "quantity", "avg", 1.0, "km/hr", 0, 120, 0, 0],
    [Q + "CyclingPower", "cycling_power", "quantity", "avg", 1.0, "W", 0, 3000, 0, 0],
    [Q + "CyclingCadence", "cycling_cadence", "quantity", "avg", 1.0, "count/min", 0, 250, 0, 0],
    [Q + "CyclingFunctionalThresholdPower", "cycling_functional_threshold_power", "quantity", "avg", 1.0, "W", 0, 1000, 0, 0],
    [Q + "WaterTemperature", "water_temperature", "quantity", "avg", 1.0, "degC", -5, 45, 0, 0],
    [Q + "EnvironmentalAudioExposure", "environmental_audio_exposure", "quantity", "avg", 1.0, "dBASPL", 0, 160, 0, 1],
    [Q + "HeadphoneAudioExposure", "headphone_audio_exposure", "quantity", "avg", 1.0, "dBASPL", 0, 160, 0, 1],
    [Q + "EnvironmentalSoundReduction", "environmental_sound_reduction", "quantity", "avg", 1.0, "dBASPL", 0, 160, 0, 0],
    [Q + "HeartRate", "heart_rate", "quantity", "avg", 1.0, "count/min", 20, 250, 0, 0]
  ];
  var TYPES = TYPE_ROWS.map(function (r) {
    return { hk: r[0], key: r[1], kind: r[2], agg: r[3], factor: r[4], store_unit: r[5], lo: r[6], hi: r[7], stats: !!r[8],
      phase1: !!r[9] };
  });
  var BY_HK = new Map(TYPES.map(function (t) { return [t.hk, t]; }));
  /** The types whose 'app' daily value derive.daily_values makes (quantities that are not phase-1 types), registry order. */
  var DERIVED = TYPES.filter(function (t) { return t.kind === "quantity" && !t.phase1; });

  // ------------------------------------------------------------------ small helpers (importers/common.py)
  function snake(name) {
    ["HKQuantityTypeIdentifier", "HKCategoryTypeIdentifier", "HKDataType"].forEach(function (p) {
      if (name.indexOf(p) === 0) name = name.slice(p.length);
    });
    return name.replace(/([a-z0-9])(?=[A-Z])|([A-Z])(?=[A-Z][a-z])/g, function (m) { return m + "_"; }).toLowerCase();
  }

  function normalize(metric, value, unit) {
    if (unit === "kJ") return [value / 4.184, "kcal"];
    if (metric === "oxygen_saturation" && value <= 1.0) return [value * 100, "%"];
    return [value, unit];
  }

  function prio(c) { var p = PARAMS.SOURCE_PRIORITY[c.source]; return p === undefined ? 0 : p; }
  function copy(o) { var out = {}; Object.keys(o || {}).forEach(function (k) { out[k] = o[k]; }); return out; }
  /** Python f"{x:.0f}" for the non-negative values here (round half to even on the exact value). */
  function f0(x) { return String(U.round(x, 0)); }

  // ------------------------------------------------------------------ daily values
  /** derive.daily_values: values = Map(hk -> the day's values in order) -> {rows, empty}. */
  function daily_values(d, types, values) {
    var rows = [], empty = [];
    types.forEach(function (t) {
      var xs = values.get(t.hk);
      if (!xs || !xs.length) { empty.push(t.key); return; }
      if (t.agg === "sum") {
        rows.push({ date: d, metric: t.key, value: U.round(U.pySum(xs), 4), unit: t.store_unit, agg: "sum", n: xs.length,
          min_value: null, max_value: null });
      } else {
        rows.push({ date: d, metric: t.key, value: U.round(U.pySum(xs) / xs.length, 4), unit: t.store_unit, agg: "avg",
          n: xs.length, min_value: Math.min.apply(null, xs), max_value: Math.max.apply(null, xs) });
      }
    });
    return { rows: rows, empty: empty };
  }

  /** derive.phase1_rows: [[hk, source, value], ...] of the day in (start, uuid) order -> rows (quantities.WRITERS). */
  function phase1_rows(d, samples) {
    var per = new Map(), order = [];
    samples.forEach(function (x) {
      var k = JSON.stringify([x[0], x[1] || ""]), p = per.get(k), v = x[2];
      if (!p) { p = { hk: x[0], s: 0.0, n: 0, mn: v, mx: v }; per.set(k, p); order.push(k); }
      p.s += v; p.n += 1; p.mn = Math.min(p.mn, v); p.mx = Math.max(p.mx, v);
    });
    var acc = new Map();
    order.forEach(function (k) {
      var p = per.get(k), a = acc.get(p.hk);
      if (!a) { a = { s: 0.0, n: 0, mn: Infinity, mx: -Infinity, best: 0.0 }; acc.set(p.hk, a); }
      a.s += p.s; a.n += p.n; a.mn = Math.min(a.mn, p.mn); a.mx = Math.max(a.mx, p.mx); a.best = Math.max(a.best, p.s);
    });
    var out = [];
    acc.forEach(function (a, hk) {
      var t = BY_HK.get(hk);
      if (t.agg === "sum") {
        out.push({ date: d, metric: t.key, value: U.round(a.best, 4), unit: t.store_unit, agg: "sum", n: a.n, min_value: null,
          max_value: null });
      } else {
        out.push({ date: d, metric: t.key, value: U.round(a.s / a.n, 4), unit: t.store_unit, agg: "avg", n: a.n,
          min_value: U.round(a.mn, 4), max_value: U.round(a.mx, 4) });
      }
    });
    return out;
  }

  // ------------------------------------------------------------------ choosing the day's value (importers/common.py)
  /** common.basal_reference: perDay = Map(day -> value) -> Map(day -> median of the previous BASAL_REF_DAYS days). */
  function basal_reference(perDay, first, last) {
    var out = new Map();
    U.dateRange(first, last).forEach(function (d) {
      var lo = U.addDays(d, -PARAMS.BASAL_REF_DAYS), xs = [];
      perDay.forEach(function (v, k) { if (k >= lo && k < d) xs.push(v); });
      if (xs.length >= PARAMS.BASAL_REF_MIN_DAYS) out.set(d, U.median(xs));
    });
    return out;
  }

  /** common.incomplete_days -> Map("date|source" -> text). */
  function incomplete_days(cands, basalRef, workoutKcal, today) {
    var out = new Map();
    cands.forEach(function (c) {
      var d = c.date, src = c.source, key = d + "|" + src;
      if (d >= today || c.agg !== "sum") return;
      var ref = basalRef.get(d);
      if (c.metric === "basal_energy_burned" && ref && c.value < PARAMS.INCOMPLETE_BASAL_SHARE * ref) {
        out.set(key, src + " 这天的基础代谢只有 " + f0(c.value) + " kcal（平常约 " + f0(ref) + "）：只同步了一部分");
      }
      var wk = workoutKcal.get(d);
      if (c.metric === "active_energy_burned" && wk && c.value + PARAMS.WORKOUT_KCAL_SLACK < wk && !out.has(key)) {
        out.set(key, src + " 这天的全天活动能量 " + f0(c.value) + " kcal 比训练本身的 " + f0(wk) + " 还少：只同步了一部分");
      }
    });
    return out;
  }

  /** common.choose: one (date, metric) -> [the chosen candidate, its flags]. */
  function choose(cands, incomplete) {
    if (!cands.some(function (c) { return c.agg === "sum"; })) {
      var w = U.maxBy(cands, prio);
      return [w, copy(w.flags)];
    }
    function flagsOf(c) {
      var f = copy(c.flags), k = c.date + "|" + c.source;
      if (PARAMS.ENERGY_METRICS.indexOf(c.metric) >= 0 && incomplete.has(k)) f.incomplete = incomplete.get(k);
      return f;
    }
    var clean = cands.filter(function (c) {
      var f = flagsOf(c);
      return !PARAMS.DEGRADED_FLAGS.some(function (x) { return Object.prototype.hasOwnProperty.call(f, x); });
    });
    var pool = clean.length ? clean : cands, win = null;
    pool.forEach(function (c) {
      if (win === null || c.value > win.value || (c.value === win.value && prio(c) > prio(win))) win = c;
    });
    return [win, flagsOf(win)];
  }

  /** common.resolve: candidates -> [[chosen, flags], ...] per (date, metric) in the order first met. */
  function resolve(cands, basalRef, workoutKcal, today) {
    var incomplete = incomplete_days(cands, basalRef, workoutKcal, today), groups = new Map();
    cands.forEach(function (c) {
      var k = c.date + "|" + c.metric, g = groups.get(k);
      if (!g) { g = []; groups.set(k, g); }
      g.push(c);
    });
    var out = [];
    groups.forEach(function (g) { out.push(choose(g, incomplete)); });
    return out;
  }

  // ------------------------------------------------------------------ sleep
  function isoOfDay(us, off) { return U.fromDayNum(Math.floor((us + off * US_MIN) / U.US_DAY)); }

  /** derive.clean_stages: [[stage, a, b], ...] (a, b in µs) of one session as Open Wearables keeps them. */
  function clean_stages(stages) {
    var names = new Set(stages.map(function (s) { return s[0]; })), keep;
    var has = function (list) { return list.some(function (x) { return names.has(x); }); };
    if (!has(PARAMS.ASLEEP_STAGES)) {
      keep = stages.filter(function (s) { return s[0] !== "unknown"; })
        .map(function (s) { return [s[0] === "in_bed" ? "sleeping" : s[0], s[1], s[2]]; });
    } else if (has(PARAMS.DETAILED_STAGES)) {
      keep = stages.filter(function (s) { return ["in_bed", "sleeping", "unknown"].indexOf(s[0]) < 0; });
    } else {
      keep = stages.filter(function (s) { return ["in_bed", "unknown"].indexOf(s[0]) < 0; });
    }
    keep = keep.slice().sort(function (x, y) { return x[1] - y[1]; });
    var out = [], lastEnd = null;
    keep.forEach(function (s) {
      var a = s[1], b = s[2];
      if (lastEnd !== null && a < lastEnd) a = lastEnd;
      if (a >= b) return;
      out.push([s[0], a, b]);
      lastEnd = b;
    });
    return out;
  }

  /** derive.ow_sessions: stage rows {start, end (µs), value, source_id, source, name, device_type, device_name} ->
   * sessions {source: {source, device_type, device_name}, start, stages: [[stage, a, b]]} in start order. */
  function ow_sessions(rows) {
    var gap = PARAMS.SESSION_GAP_MIN * US_MIN, bySrc = new Map(), order = [];
    rows.forEach(function (r) {
      var k = JSON.stringify([r.source_id === undefined ? null : r.source_id, r.source || ""]);
      if (!bySrc.has(k)) { bySrc.set(k, []); order.push(k); }
      bySrc.get(k).push(r);
    });
    var out = [];
    order.forEach(function (k) {
      var xs = bySrc.get(k), seen = new Set(), sessions = [], cur = null;
      xs.slice().sort(function (x, y) { return x.start - y.start; }).forEach(function (r) {
        var stage = PARAMS.SLEEP_STAGES[Math.trunc(r.value)] || "unknown", key = r.start + "|" + r.end + "|" + stage;
        if (seen.has(key)) return;
        seen.add(key);
        var a = r.start, b = r.end;
        if (cur === null) {
          if (PARAMS.SESSION_START.indexOf(stage) < 0) return;
          cur = { lo: a, hi: b, stages: [] };
        } else {
          var g;
          if (a <= cur.hi && b >= cur.lo) g = 0;
          else if (b <= cur.lo) g = cur.lo - b;
          else g = a - cur.hi;
          if (g > gap) { sessions.push(cur); cur = { lo: a, hi: b, stages: [] }; }
        }
        cur.hi = Math.max(cur.hi, b); cur.lo = Math.min(cur.lo, a);
        cur.stages.push([stage, a, b]);
      });
      if (cur !== null) sessions.push(cur);
      var first = xs[0];
      var src = { source: first.name || first.source || null, device_type: first.device_type || null,
        device_name: first.device_name || first.name || first.source || null };
      sessions.forEach(function (s) {
        var st = clean_stages(s.stages);
        if (st.length) out.push({ source: src, start: st[0][1], stages: st });
      });
    });
    out.sort(function (x, y) { return x.start - y.start; });   // stable, by instant (derive.ow_sessions)
    return out;
  }

  function sessionPriority(s) {
    var p = DEVICE_PRIORITY[(s.source && s.source.device_type) || ""];
    return (p === undefined ? 2 : p) * 2 + (s.source && s.source.source ? 0 : 1);
  }

  /** importers/ow.sleep_segments: sessions -> segments {start, end, soff, eoff, stage, device} (a session overlapping a
   * higher-priority one is dropped). */
  function sleep_segments(sessions, tz) {
    var kept = [], segs = [];
    sessions.map(function (s, i) { return [sessionPriority(s), i, s]; })
      .sort(function (x, y) { return x[0] - y[0] || x[1] - y[1]; })
      .forEach(function (x) {
        var s = x[2], parts = [];
        s.stages.forEach(function (st) {
          var stage = STAGE_MAP[st[0]];
          if (stage) parts.push([stage, st[1], st[2]]);
        });
        if (!parts.length) return;
        var lo = Math.min.apply(null, parts.map(function (p) { return p[1]; }));
        var hi = Math.max.apply(null, parts.map(function (p) { return p[2]; }));
        if (kept.some(function (k) { return lo < k[1] && k[0] < hi; })) return;
        kept.push([lo, hi]);
        var device = (s.source || {}).device_name;
        if (device === undefined) device = null;
        parts.forEach(function (p) {
          segs.push({ start: p[1], end: p[2], soff: tz.offsetAt(p[1]), eoff: tz.offsetAt(p[2]), stage: p[0], device: device });
        });
      });
    return segs;
  }

  /** common.sessionize: segments -> sessions {wake_date, start, end, minutes, device, segments}. */
  function sessionize(segments) {
    var gap = PARAMS.SLEEP_GAP_MIN * US_MIN;
    var asleep = segments.filter(function (s) { return s.stage !== "awake"; }).sort(function (a, b) { return a.start - b.start; });
    var awake = segments.filter(function (s) { return s.stage === "awake"; });
    var sessions = [], cur = [];
    function maxEnd(list) { var m = -Infinity; list.forEach(function (s) { if (s.end > m) m = s.end; }); return m; }
    asleep.forEach(function (seg) {
      if (cur.length && seg.start - maxEnd(cur) > gap) { sessions.push(cur); cur = []; }
      cur.push(seg);
    });
    if (cur.length) sessions.push(cur);
    sessions = sessions.map(function (segs) {
      var lo = segs[0].start, hi = maxEnd(segs);
      return segs.concat(awake.filter(function (a) { return a.start >= lo && a.end <= hi; }))
        .sort(function (a, b) { return a.start - b.start; });
    });
    var out = [];
    sessions.forEach(function (segs) {
      var minutes = { core: 0, deep: 0, rem: 0, awake: 0, unspecified: 0 }, endSeg = null, devices = new Map();
      segs.forEach(function (s) {
        minutes[s.stage] += ((s.end - s.start) / US_SEC) / 60;
        if (endSeg === null || s.end > endSeg.end) endSeg = s;
        var k = s.device === null || s.device === undefined ? "\u0000" : s.device;
        devices.set(k, (devices.get(k) || 0) + 1);
      });
      var device = null, best = -1;
      devices.forEach(function (n, k) { if (n > best) { best = n; device = k === "\u0000" ? null : k; } });
      var asleepMin = U.round(U.pySum([minutes.core, minutes.deep, minutes.rem, minutes.unspecified]));
      if (asleepMin < PARAMS.MIN_ASLEEP_MIN) return;
      out.push({ wake_date: isoOfDay(endSeg.end - PARAMS.WAKE_DAY_SHIFT_H * US_HOUR, endSeg.eoff), start: segs[0].start,
        end: endSeg.end, minutes: minutes, asleep_min: asleepMin, device: device, segments: segs });
    });
    return out;
  }

  function midnight(d, tz) { return U.combine(d, 0, tz); }

  /** derive.night_window: [lo, hi) in µs. */
  function night_window(wakeDates, tz) {
    var ds = wakeDates.slice().sort();
    return [midnight(U.addDays(ds[0], -PARAMS.SLEEP_DAYS_BEFORE), tz), midnight(U.addDays(ds[ds.length - 1], PARAMS.SLEEP_DAYS_AFTER), tz)];
  }

  /** derive.nights_of. */
  function nights_of(rows, wakeDates, tz) {
    var want = new Set(wakeDates);
    return sessionize(sleep_segments(ow_sessions(rows), tz)).filter(function (n) { return want.has(n.wake_date); });
  }

  /** derive.session_rows: sessions of one wake date -> rows (UTC seconds). */
  function session_rows(sessions) {
    var main = -1, best = null;
    sessions.forEach(function (s, i) { if (best === null || s.asleep_min > best) { best = s.asleep_min; main = i; } });
    return sessions.map(function (s, i) {
      return { start: s.start / US_SEC, end: s.end / US_SEC, asleep_min: s.asleep_min, core_min: U.round(s.minutes.core),
        deep_min: U.round(s.minutes.deep), rem_min: U.round(s.minutes.rem), awake_min: U.round(s.minutes.awake),
        unspecified_min: U.round(s.minutes.unspecified), is_main: i === main, device: s.device,
        segments: s.segments.map(function (g) { return [g.start / US_SEC, g.end / US_SEC, g.stage]; }) };
    });
  }

  // ------------------------------------------------------------------ the phone's entry point
  function table(x) {
    if (!x) return [];
    if (Array.isArray(x)) return x;
    var cols = x.cols || [];
    return (x.rows || []).map(function (r) { var o = {}; cols.forEach(function (c, i) { o[c] = r[i]; }); return o; });
  }
  function usOf(s) { return Math.round(s * US_SEC); }

  /** derive.ingest_rows. */
  function ingest_rows(samples, buckets, tz, hkUnits) {
    var outS = [], outB = [], span = PARAMS.MAX_SAMPLE_SPAN_S * US_SEC;
    table(samples).forEach(function (r) {
      var t = BY_HK.get(r.type);
      if (!t || (t.kind !== "quantity" && t.kind !== "category")) return;
      var v = Number(r.value) * (hkUnits ? t.factor : 1.0), a = usOf(r.start), b = usOf(r.end);
      if (!(t.lo <= v && v <= t.hi) || b < a || b - a > span) return;
      outS.push({ uuid: r.uuid || "", type: t.hk, start: a, end: b, value: v, day: U.localDate(a, tz), source: r.source || "",
        name: r.source || null, source_id: r.source_id === undefined ? null : r.source_id, device_type: r.device_type,
        device_name: r.device_name });
    });
    table(buckets).forEach(function (r) {
      var t = BY_HK.get(r.type);
      if (!t || !t.stats) return;
      var v = Number(r.value) * (hkUnits ? t.factor : 1.0);
      if (v === 0 || !(t.lo <= v && v <= t.hi)) return;
      outB.push({ type: t.hk, start: usOf(r.start), value: v });
    });
    outS.sort(function (x, y) { return x.start - y.start || U.cmp(x.uuid, y.uuid); });
    outB.sort(function (x, y) { return U.cmp(x.type, y.type) || x.start - y.start; });
    return [outS, outB];
  }

  function byMetric(a, b) { return U.cmp(a.metric, b.metric); }
  function rowOut(r) {
    return { metric: r.metric, source: r.source, value: r.value, unit: r.unit, agg: r.agg, n: r.n, min_value: r.min_value,
      max_value: r.max_value, flags: r.flags };
  }

  /** derive.derive_days (see the file header). */
  function derive_days(src) {
    var tz = U.tzOf(src.tz), today = src.today;
    var ing = ingest_rows(src.samples, src.buckets, tz, (src.units || "hk") === "hk"), samples = ing[0], buckets = ing[1];
    var days = Array.from(new Set(src.days || [])).sort();
    var app = new Map();
    days.forEach(function (d) {
      var mine = samples.filter(function (r) { return r.day === d; }), values = new Map();
      mine.slice().sort(function (x, y) { return U.cmp(x.type, y.type) || x.start - y.start || U.cmp(x.uuid, y.uuid); })
        .forEach(function (r) {
          if (BY_HK.get(r.type).agg !== "avg") return;   // raw samples count for averages; sums come from the buckets
          if (!values.has(r.type)) values.set(r.type, []);
          values.get(r.type).push(r.value);
        });
      var lo = midnight(d, tz), hi = midnight(U.addDays(d, 1), tz);
      buckets.forEach(function (b) {
        if (b.start >= lo && b.start < hi) { if (!values.has(b.type)) values.set(b.type, []); values.get(b.type).push(b.value); }
      });
      var rows = daily_values(d, DERIVED, values).rows.concat(phase1_rows(d, mine
        .filter(function (r) { var t = BY_HK.get(r.type); return t.phase1; })
        .map(function (r) { return [r.type, r.source, r.value]; })));
      app.set(d, rows.map(function (r) { var o = copy(r); o.source = APP; o.flags = {}; return o; }).sort(byMetric));
    });
    function cand(r, d) { var o = copy(r); o.date = d || r.date; o.flags = copy(r.flags); return o; }
    var other = (src.other || []).filter(function (r) { return r.source !== APP; }).map(function (r) { return cand(r); });
    var history = (src.app_history || []).filter(function (r) { return r.source === APP && !app.has(r.date); })
      .map(function (r) { return cand(r); });
    var fresh = [];
    days.forEach(function (d) { app.get(d).forEach(function (r) { fresh.push(cand(r, d)); }); });
    var perDay = new Map();
    other.concat(history, fresh).forEach(function (r) {
      if (r.metric === "basal_energy_burned" && r.value > 0 && !Object.prototype.hasOwnProperty.call(r.flags, "hourly_sum")) {
        perDay.set(r.date, perDay.has(r.date) ? Math.max(perDay.get(r.date), r.value) : r.value);
      }
    });
    var wk = new Map();
    Object.keys(src.workout_kcal || {}).forEach(function (k) { var v = src.workout_kcal[k]; if (v !== null && v !== undefined) wk.set(k, v); });
    var daily = {};
    if (days.length) {
      var want = new Set(days), chosen = new Map();
      var cands = other.filter(function (r) { return want.has(r.date); }).concat(fresh);
      resolve(cands, basal_reference(perDay, days[0], days[days.length - 1]), wk, today).forEach(function (x) {
        var o = rowOut(x[0]);
        o.flags = x[1];
        if (!chosen.has(x[0].date)) chosen.set(x[0].date, []);
        chosen.get(x[0].date).push(o);
      });
      days.forEach(function (d) {
        daily[d] = { sources: app.get(d).map(function (r) { var o = rowOut(r); return o; }),
          chosen: (chosen.get(d) || []).sort(byMetric) };
      });
    }
    var sleep = {}, stage = samples.filter(function (r) { return r.type === SLEEP; });
    Array.from(new Set(src.wake_dates || [])).sort().forEach(function (w) {
      var win = night_window([w], tz);
      sleep[w] = session_rows(nights_of(stage.filter(function (r) { return r.start >= win[0] && r.start < win[1]; }), [w], tz));
    });
    return { algo_version: PARAMS.ALGO_VERSION, daily: daily, sleep: sleep };
  }

  return { PARAMS: PARAMS, TYPES: TYPES, snake: snake, normalize: normalize, daily_values: daily_values, phase1_rows: phase1_rows,
    basal_reference: basal_reference, incomplete_days: incomplete_days, choose: choose, resolve: resolve,
    clean_stages: clean_stages, ow_sessions: ow_sessions, sleep_segments: sleep_segments, sessionize: sessionize,
    night_window: night_window, nights_of: nights_of, session_rows: session_rows, ingest_rows: ingest_rows,
    derive_days: derive_days };
})(OBS.util);

OBS.ALGO_VERSIONS.derive = OBS.derive.PARAMS.ALGO_VERSION;

/** The phone's derivation of daily values and sleep (2.0.0 M1): see src/30_derive.js. Stamped with algo / bundle version. */
OBS.deriveDays = function (src) {
  var out = OBS.derive.derive_days(src);
  out.bundle_version = OBS.VERSION;
  return out;
};
