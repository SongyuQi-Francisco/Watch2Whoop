/* rules (2.0.0 package M6, docs/ios-native-2.0.md): rule reminders and nutrition targets on the phone. Port of body_engine
- rules/context.py `Ctx` (its methods), `assemble`, `merge_weights` (the pure end of `load`), `from_data`;
- rules/progression.py, rules/nutrition.py (`calorie_adjust`, `pre_workout_fuel`, `frequency_caps`, `sugar_caps`,
  `red_meat_estimate`), rules/safety.py (`exercise_restrictions`, `readiness`), rules/sleep_goal.py, rules/hydration.py;
- rules/engine.py `evaluate_ctx`, `reminder_rows`, `evaluate_data` (the reminders `store` writes, the rule_runs summaries);
- rules/base.py `jsonable`, rules/common.py `need`, `days_back`;
- goals/nutrition.py `version_for`, `day_type`, `goal_for`, `day_from_totals` and nutrition/daily.py `adherence`,
  nutrition/extras.py `known_sum`, `day_totals`, nutrition/constraints.py `Constraints` (the parts the rules read).
Thresholds: rules/params.py, goals/params.py, water/params.py, nutrition/constraint_params.py (sources there), mirrored in
`params` below and compared by the golden test (test/rules.test.js against body_engine/tests/test_js_golden_rules.py).
Every reminder carries its Chinese and English text (title / message, title_en / message_en): both are compared verbatim.
The bedtime-goal arithmetic is M4's OBS.coach.goals (goals/sleep.py). Dates are 'YYYY-MM-DD' strings.
*/
OBS.rules = (function (U, CG, CO) {
  "use strict";
  var RULES_VERSION = "v1.4-2026-10-22";   // rules/params.RULES_VERSION (unchanged by the 2.0.0 split)

  // ================================================================ parameters (sources in the Python files)
  var P = {   // rules/params.py
    INCREMENT_FALLBACK_KG: { upper: 2.5, lower: 2.5 }, MIN_RIR_FOR_INCREASE: 1, FAIL_STREAK: 2, REQUIRE_PLANNED_SETS: true,
    PROGRESSION_LOOKBACK_DAYS: 120, WARMUP_SET_TYPES: ["warm-up", "warmup", "warm up", "热身"],
    SLOW_LOSS_KG_PER_WEEK: 0.4, SLOW_LOSS_KCAL: -150, FAST_LOSS_KG_PER_WEEK: 1.0, FAST_LOSS_KCAL: 200,
    MIN_WEIGHINS_PER_WEEK: 2, TARGET_LOSS_RANGE: [0.5, 0.9],
    PRE_MEAL_CARBS_G: [50, 70], PRE_MEAL_PROTEIN_G: [20, 30], DAY_CARBS_WARN_G: 50, WATER_TARGET_L: 3.0,
    SYMPTOM_LOOKBACK_DAYS: 14, LEG_MUSCLES: ["quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors"],
    RED_MEAT_KEYWORDS: [["牛肋条", 135], ["short rib", 135], ["培根", 30], ["bacon", 30], ["火腿", 30], ["香肠", 50],
      ["sausage", 50], ["牛", 150], ["beef", 150], ["steak", 150], ["羊", 150], ["lamb", 150], ["猪", 150], ["pork", 150],
      ["排骨", 150], ["rump", 150], ["topside", 150]],
    RED_MEAT_EXCLUDE: ["牛奶", "牛乳", "牛油果", "牛油", "酸牛奶", "羊奶", "羊乳", "蜗牛", "牛蛙", "milk", "avocado"],
    BANNED_LOG_LOOKBACK_DAYS: 14, CAUTION_LOG_LOOKBACK_DAYS: 14, CAUTION_MAX_WEIGHT_KG: { cable_face_pull: 18.0 },
    SPARKY_STATUS_MARKERS: [["【禁用】", "banned"], ["【注意】", "caution"]],
    RECOVERY_RED: 34, HEAVY_CARDIO_KEYWORDS: ["篮球", "basketball"], SLEEP_DEBT_HIGH_MIN: 120,
    ADDED_SUGAR_CAP_G: 25, ADDED_SUGAR_WARN_G: 50, SUGARY_DRINKS_PER_WEEK: 2,
    CALORIE_ADHERENCE_TOL: 0.10, PROTEIN_ADHERENCE_MIN: 0.90
  };
  var GP = {   // goals/params.py (the bedtime-goal reminders)
    DEFAULT_DURATION_MIN: 480, ON_TARGET_TOL_MIN: 15, DEVIATION_MIN: 30, DEVIATION_NIGHTS: 3, SCREEN_OFF_BEFORE_MIN: 60,
    CAFFEINE_BEFORE_MIN: 480, CAFFEINE_NOON_CUTOFF_MIN: 720, DINNER_BEFORE_MIN: 180, HISTORY_DAYS: 21,
    MORNING_LIGHT: "起床后 30 分钟内晒太阳 10–20 分钟", MORNING_LIGHT_EN: "10–20 min of sunlight within 30 min of waking"
  };
  var WP = { REMIND_FROM_HOUR: 15, REMIND_SHARE: 0.5 };   // water/params.py
  var CP = {   // nutrition/constraint_params.py
    WARN_FRACTION: 0.8,
    FOOD_ALIASES: { "牛肋条": ["short rib", "beef rib", "牛小排"], "培根": ["bacon"], "火腿": ["ham"], "香肠": ["sausage"] }
  };
  var NG = {   // goals/nutrition.py, nutrition/daily.py
    FIELDS: ["calories", "protein_g", "carbs_g", "fat_g", "sat_fat_max_g", "added_sugar_max_g", "fiber_min_g"],
    DAY_TYPE_ZH: { training: "训练日", rest: "休息日" }, DAY_TYPE_EN: { training: "Training day", rest: "Rest day" },
    GOAL_KEYS: ["name", "day_type", "source", "calories", "protein_g", "carbs_g", "fat_g", "sat_fat_max_g",
      "added_sugar_max_g", "fiber_min_g"],
    TOTAL_KEYS: ["calories", "protein_g", "carbs_g", "fat_g", "saturated_fat_g"],
    UNKNOWN_ON_PHONE: ["sugar_g", "added_sugar_g", "fiber_g", "sodium_mg"],
    OPTIONAL: ["sugar_g", "added_sugar_g", "fiber_g", "sodium_mg"]
  };
  var CTX = { TRAINING_DAYS: 120, FOOD_DAYS: 14, WEIGHT_DAYS: 21, SCORE_DAYS: 7, JOURNAL_DAYS: 21 };   // rules/context.py
  var SCORE_FIELDS = ["recovery", "sleep_score", "sleep_debt_min", "sleep_actual_min", "sleep_need_min", "strain", "flags"];
  var SOURCE_LABELS = { sessions: "训练记录", presets: "训练模板", plans: "每周训练计划", exercises: "动作库",
    food: "饮食记录", goals: "饮食目标", weights: "体重" };

  // ================================================================ Python helpers
  function nn(v) { return v !== null && v !== undefined; }
  function g(o, k) { var v = o ? o[k] : undefined; return v === undefined ? null : v; }
  function has(o, k) { return !!o && Object.prototype.hasOwnProperty.call(o, k); }
  function or(a, b) { return truthy(a) ? a : b; }
  /** Python truthiness of a JSON value. */
  function truthy(v) {
    if (v === null || v === undefined || v === false || v === 0 || v === "") return false;
    if (Array.isArray(v)) return v.length > 0;
    if (typeof v === "object") return Object.keys(v).length > 0;
    return true;
  }
  var fmtG = CO.fmtG;
  /** Python str() of a value that is a float in Python (round(...) results, float parameters); None -> "None". */
  function pf(x) {
    if (!nn(x)) return "None";
    if (Number.isInteger(x) && Math.abs(x) < 1e16) return x.toFixed(1);
    return String(x);
  }
  /** Python str() of an int or a value as stored; None -> "None". */
  function ps(x) { return nn(x) ? String(x) : "None"; }
  /** f"{x:.nf}" (Python rounds the exact binary value half to even). */
  function fix(x, n) { return U.round(x, n).toFixed(n); }
  function sum(xs) { return U.pySum(xs); }
  function sortedUnique(xs) { return Array.from(new Set(xs)).sort(); }
  function isoWeek(d) {
    var n = U.dayNum(d), wd = U.weekday(d), thu = n - wd + 3, year = Number(U.fromDayNum(thu).slice(0, 4));
    var jan4 = U.dayNum(year + "-01-04"), firstThu = jan4 - U.weekday(year + "-01-04") + 3;
    return [year, Math.floor((thu - firstThu) / 7) + 1];
  }
  function isoweekday(d) { return U.weekday(d) + 1; }
  function startsWith(s, p) { return s.slice(0, p.length) === p; }
  function replaceAll(s, a, b) { return a ? s.split(a).join(b) : s; }
  function strip(s) { return s.replace(/^\s+|\s+$/g, ""); }

  /** rules/base.jsonable: floats rounded to 3 decimals (an integral value is an int already). */
  function jsonable(x) {
    if (Array.isArray(x)) return x.map(jsonable);
    if (x && typeof x === "object") {
      var out = {};
      Object.keys(x).forEach(function (k) { out[k] = jsonable(x[k]); });
      return out;
    }
    if (typeof x === "number" && !Number.isInteger(x)) return U.round(x, 3);
    return x;
  }

  function Reminder(rule_key, level, title, message, o) {
    o = o || {};
    return { rule_key: rule_key, level: level, title: title, message: message, subject: o.subject || "",
      evidence: o.evidence || {}, sticky: !!o.sticky, title_en: o.title_en || "", message_en: o.message_en || "" };
  }
  function Result(rule_key, status, note, reminders, details) {
    return { rule_key: rule_key, status: status, note: note || "", reminders: reminders || [], details: details || {} };
  }
  function summary(r) {
    var out = { rule_key: r.rule_key, status: r.status, note: r.note, reminders: r.reminders.length };
    if (truthy(r.details)) out.details = jsonable(r.details);
    return out;
  }

  // ================================================================ nutrition/constraints.py (Constraints)
  function norm(s) { return (s || "").toLowerCase().replace(/\s+/g, ""); }
  function Diet(rows) {
    this.rows = rows || [];
  }
  Diet.prototype._one = function (kind) {
    for (var i = 0; i < this.rows.length; i++) if (this.rows[i].kind === kind) return this.rows[i];
    return null;
  };
  Diet.prototype.red_meat_g = function () { var r = this._one("red_meat_g_per_week"); return r && r.value; };
  Diet.prototype.red_meat_meals = function () { var r = this._one("red_meat_meals_per_week"); return r && r.value; };
  Diet.prototype.food_limits = function () {
    return this.rows.filter(function (r) { return r.kind === "food_times_per_week"; }).map(function (r) {
      var t = r.target || "", names = [t].concat(CP.FOOD_ALIASES[t] || [], r.match || []).filter(truthy).map(norm);
      return { target: r.target, names: names, max: r.value, basis: g(r, "basis") };
    });
  };
  Diet.prototype.food_limit_hit = function (food) {
    var s = norm(food), lims = this.food_limits();
    for (var i = 0; i < lims.length; i++) {
      if (lims[i].names.some(function (n) { return n && s.indexOf(n) >= 0; })) return lims[i];
    }
    return null;
  };

  // ================================================================ rules/context.py (Ctx from data)
  function Ctx(data) {
    var self = this;
    this.day = data.day; this.tz = data.tz; this.now = g(data, "now");
    this.sessions = g(data, "sessions"); this.exercises = g(data, "exercises"); this.plans = g(data, "plans");
    this.presets = null;
    if (nn(data.presets)) { this.presets = new Map(); data.presets.forEach(function (p) { self.presets.set(String(p.id), p); }); }
    this.meta = data.meta || []; this.food = g(data, "food"); this.goals = g(data, "goals"); this.weights = g(data, "weights");
    this.scores = data.scores || {};
    this.journal = new Map();
    (data.journal || []).forEach(function (j) { self.journal.set(j[0] + "\u0000" + j[1], j[2]); });
    this.journal_rows = (data.journal || []).slice();
    this.sleep_goal = nn(data.sleep_goal) ? CG.with_plan(data.sleep_goal) : null;
    this.sleep_nights = data.sleep_nights || []; this.sleep_duration_min = g(data, "sleep_duration_min");
    this.overrides = data.overrides || {};
    this.diet = nn(data.diet) ? new Diet(data.diet) : null;
    this.water = g(data, "water"); this.water_goal = g(data, "water_goal");
    this.errors = data.errors || {}; this.missing = data.missing || {};
  }
  Ctx.prototype.jget = function (d, k) { var v = this.journal.get(d + "\u0000" + k); return v === undefined ? null : v; };
  Ctx.prototype.jhas = function (d, k) { return this.journal.has(d + "\u0000" + k); };
  Ctx.prototype.preset = function (id) { return this.presets ? (this.presets.get(String(id)) || null) : null; };
  Ctx.prototype.ex = function (id) {
    return this.exercises && has(this.exercises, String(id)) ? this.exercises[String(id)] : null;
  };
  Ctx.prototype.meta_by_app_id = function () {
    var m = new Map();
    this.meta.forEach(function (x) { if (truthy(x.app_exercise_id)) m.set(String(x.app_exercise_id), x); });
    return m;
  };
  Ctx.prototype.meta_by_ref = function () { var m = new Map(); this.meta.forEach(function (x) { m.set(x.exercise_ref, x); }); return m; };
  Ctx.prototype.exercise_name = function (id, fallback) {
    var ex = this.ex(id);
    return or(ex ? g(ex, "name") : null, or(fallback, id));
  };
  Ctx.prototype.status_of = function (id) {
    var m = this.meta_by_app_id().get(String(id));
    if (m !== undefined) return [m.status, m, "exercise_meta"];
    var desc = or(g(this.ex(id) || {}, "description"), "");
    for (var i = 0; i < P.SPARKY_STATUS_MARKERS.length; i++) {
      if (startsWith(desc, P.SPARKY_STATUS_MARKERS[i][0])) return [P.SPARKY_STATUS_MARKERS[i][1], null, "SparkyFitness 动作描述"];
    }
    return ["active", null, "默认"];
  };
  Ctx.prototype.muscles_of = function (id, fallback) {
    var ex = this.ex(id) || {};
    return or(g(ex, "primary_muscles"), or(fallback, [])).map(function (m) { return m.toLowerCase(); });
  };
  Ctx.prototype.is_leg_exercise = function (id, category, fallback) {
    var ex = this.ex(id) || {}, cat = or(g(ex, "category"), category === undefined ? null : category);
    if (cat !== null && cat !== "strength") return false;
    return this.muscles_of(id, fallback).some(function (m) { return P.LEG_MUSCLES.indexOf(m) >= 0; });
  };
  Ctx.prototype.active_plan = function (day) {
    day = day || this.day;
    var plans = this.plans || [];
    for (var i = 0; i < plans.length; i++) {
      var p = plans[i], start = truthy(p.start_date) ? String(p.start_date).slice(0, 10) : "0000-00-00";
      var end = truthy(p.end_date) ? String(p.end_date).slice(0, 10) : "9999-99-99";
      var st = g(p, "schedule_type");
      if (truthy(p.is_active) && (st === null || st === "weekly") && start <= day && day <= end) return p;
    }
    return null;
  };
  Ctx.prototype.planned_for = function (day) {
    day = day || this.day;
    var ov = has(this.overrides, day) ? this.overrides[day] : null, self = this;
    if (ov === null) return this.weekday_items(isoweekday(day), day);
    var out = [];
    (ov.sessions || []).forEach(function (ref) {
      if (truthy(ref.dow)) out = out.concat(self.weekday_items(Number(ref.dow), day));
      else if (truthy(ref.label)) out.push({ kind: "label", name: ref.label });
    });
    return out;
  };
  Ctx.prototype.weekday_items = function (iso_dow, day) {
    var plan = this.active_plan(day || this.day), self = this;
    if (plan === null) return [];
    var dow = iso_dow % 7, out = [];
    plan.assignments.filter(function (a) { return a.day_of_week === dow; })
      .map(function (a, i) { return [a, i]; })
      .sort(function (x, y) { return (x[0].sort_order - y[0].sort_order) || x[1] - y[1]; })
      .forEach(function (x) {
        var a = x[0];
        if (nn(a.workout_preset_id)) {
          out.push({ kind: "preset", preset: self.preset(a.workout_preset_id) ||
            { id: a.workout_preset_id, name: g(a, "workout_preset_name"), exercises: [] } });
        } else if (truthy(a.exercise_id)) {
          out.push({ kind: "exercise", exercise_id: a.exercise_id, name: self.exercise_name(a.exercise_id, g(a, "exercise_name")) });
        }
      });
    return out;
  };
  Ctx.prototype.planned_exercises = function (day) {
    var out = [], self = this;
    this.planned_for(day).forEach(function (item) {
      if (item.kind === "preset") {
        item.preset.exercises.forEach(function (x) {
          out.push({ exercise_id: x.exercise_id, name: self.exercise_name(x.exercise_id, g(x, "name")),
            category: g(x, "category"), preset: item.preset.name });
        });
      } else if (item.kind === "exercise") {
        out.push({ exercise_id: item.exercise_id, name: item.name, category: null, preset: null });
      }
    });
    return out;
  };
  Ctx.prototype.plan_prescriptions = function () {
    var plan = this.active_plan(), self = this, out = new Map();
    if (plan === null) return out;
    var ids = plan.assignments.map(function (a, i) { return [a, i]; })
      .sort(function (x, y) { return (x[0].day_of_week - y[0].day_of_week) || (x[0].sort_order - y[0].sort_order) || x[1] - y[1]; })
      .filter(function (x) { return nn(x[0].workout_preset_id); }).map(function (x) { return x[0].workout_preset_id; });
    var seenIds = new Set();
    ids.forEach(function (pid) {
      if (seenIds.has(String(pid))) return;
      seenIds.add(String(pid));
      var preset = self.preset(pid);
      ((preset || {}).exercises || []).forEach(function (x) {
        if (out.has(String(x.exercise_id))) return;
        var working = x.sets.filter(function (s) { return P.WARMUP_SET_TYPES.indexOf(or(g(s, "type"), "").toLowerCase()) < 0; });
        var lows = working.filter(function (s) { return truthy(s.reps); }).map(function (s) { return s.reps; });
        var pw = null;
        for (var i = 0; i < working.length; i++) if (truthy(working[i].weight_kg)) { pw = working[i].weight_kg; break; }
        out.set(String(x.exercise_id), { exercise_id: x.exercise_id, name: self.exercise_name(x.exercise_id, g(x, "name")),
          preset: preset.name, progression_mode: g(x, "progression_mode"),
          rep_low: lows.length ? Math.min.apply(null, lows) : null, rep_high: g(x, "rep_goal"),
          increment_kg: g(x, "increment_value"), planned_sets: working.length, planned_weight_kg: pw,
          category: g(x, "category") });
      });
    });
    return out;
  };
  Ctx.prototype.sessions_on = function (day) {
    return (this.sessions || []).filter(function (s) { return s.date.slice(0, 10) === day; });
  };
  /** Local wall-clock fields of `now` (the evaluation time; the phone always passes it). */
  Ctx.prototype.now_local = function () {
    return U.local(nn(this.now) ? U.toUs(this.now) : Date.now() * 1000, U.tzOf(this.tz));
  };

  // ================================================================ rules/common.py
  function need(ctx, rule_key, sources) {
    var failed = sources.filter(function (s) { return has(ctx.errors, s); });
    if (failed.length) {
      return Result(rule_key, "error", "读不到 " + failed.map(function (s) {
        return (SOURCE_LABELS[s] || s) + "（" + ctx.errors[s] + "）";
      }).join("、"));
    }
    if (sources.some(function (s) { return !nn(ctx[s]); })) {
      return Result(rule_key, "insufficient_data", "没有 SparkyFitness 数据：" + or(g(ctx.missing, "sparky"), "没有数据"));
    }
    return null;
  }
  function days_back(day, n) { var out = []; for (var k = n - 1; k >= 0; k--) out.push(U.addDays(day, -k)); return out; }

  // ================================================================ rules/progression.py
  var PROGRESSION = "progression";
  function working(sets) {
    return sets.filter(function (s) { return P.WARMUP_SET_TYPES.indexOf(or(g(s, "type"), "").toLowerCase()) < 0 && nn(g(s, "reps")); });
  }
  function history(ctx, exercise_id) {
    var by_day = new Map();
    (ctx.sessions || []).forEach(function (s) {
      var d = s.date.slice(0, 10);
      if (d > ctx.day) return;
      s.exercises.forEach(function (e) {
        if (String(e.exercise_id) !== String(exercise_id)) return;
        if (!by_day.has(d)) by_day.set(d, { date: d, sets: [], notes: g(s, "notes") });
        var x = by_day.get(d);
        x.sets = x.sets.concat(working(e.sets));
      });
    });
    return Array.from(by_day.keys()).sort().reverse().map(function (k) { return by_day.get(k); })
      .filter(function (x) { return x.sets.length > 0; });
  }
  function top_weight(sets) { return Math.max.apply(null, sets.map(function (s) { return or(g(s, "weight_kg"), 0); })); }
  function note_about(ctx, ex_id, name, notes) {
    if (!truthy(notes)) return null;
    var meta = ctx.meta_by_app_id().get(String(ex_id)) || {};
    var keys = [name.split("（")[0].slice(0, 4)].concat(or(g(meta, "aliases"), []))
      .filter(function (k) { return truthy(k) && k.length >= 2; });
    keys = Array.from(new Set(keys));
    var hits = notes.split(/[。；;\n]/).map(strip).filter(function (x) { return keys.some(function (k) { return x.indexOf(k) >= 0; }); });
    return hits.join("；") || null;
  }
  function wfmt(w) { return truthy(w) ? fmtG(w) + " kg" : "自重"; }
  function wfmt_en(w) { return truthy(w) ? fmtG(w) + " kg" : "bodyweight"; }

  function progression(ctx) {
    var r = need(ctx, PROGRESSION, ["sessions", "presets", "plans", "exercises"]);
    if (r !== null) return r;
    var plan = ctx.active_plan();
    if (plan === null) return Result(PROGRESSION, "insufficient_data", "SparkyFitness 里没有当天生效的每周训练计划，不知道次数区间");
    var rx = ctx.plan_prescriptions(), reminders = [], never = [], checked = [], skipped = [];
    rx.forEach(function (p, ex_id) {
      if (p.progression_mode !== "rep_goal" || !truthy(p.rep_high)) { skipped.push(p.name); return; }
      var hist = history(ctx, p.exercise_id);
      if (!hist.length) { never.push(p.name); return; }
      var last = hist[0], sets = last.sets, w = top_weight(sets);
      var low = or(p.rep_low, p.rep_high), high = p.rep_high;
      var inc = or(p.increment_kg, P.INCREMENT_FALLBACK_KG[ctx.is_leg_exercise(p.exercise_id) ? "lower" : "upper"]);
      var reps = sets.map(function (s) { return s.reps; });
      var rirs = sets.filter(function (s) { return nn(g(s, "rir")); }).map(function (s) { return s.rir; });
      checked.push(p.name);
      var evidence = { exercise_id: p.exercise_id, exercise: p.name, template: p.preset, last_session: last.date,
        sets: sets.map(function (s) { return { reps: s.reps, weight_kg: g(s, "weight_kg"), rir: g(s, "rir") }; }),
        rep_range: [low, high], planned_sets: p.planned_sets, increment_kg: inc,
        session_notes: (or(last.notes, "")).slice(0, 300) || null, source: "body-management.md 4.5" };
      var enough = !P.REQUIRE_PLANNED_SETS || sets.length >= p.planned_sets;
      var minReps = Math.min.apply(null, reps);
      if (minReps >= high && enough) {
        if (rirs.length && Math.min.apply(null, rirs) < P.MIN_RIR_FOR_INCREASE) {
          var mr = Math.min.apply(null, rirs);
          reminders.push(Reminder(PROGRESSION, "info", p.name + "：到上限但做到力竭，先保持 " + wfmt(w),
            last.date + " 每组都做到 " + high + " 次，但最低 RIR " + mr + "（力竭）。4.3 要求每组留 1–2 次余力" +
            "（力竭时容易耸肩代偿），先在 " + wfmt(w) + " 做到留 1 次余力再加重。",
            { subject: ex_id + ":" + fmtG(w) + ":hold", evidence: evidence, sticky: true,
              title_en: p.name + ": top of the range but to failure, stay at " + wfmt_en(w),
              message_en: "On " + last.date + " every set reached " + high + " reps, but the lowest RIR was " + mr + " (failure). " +
                "4.3 asks for 1–2 reps in reserve on every set (at failure you tend to shrug to compensate); " +
                "stay at " + wfmt_en(w) + " until you have 1 rep in reserve, then add weight." }));
        } else {
          var nxt = w + inc, note = note_about(ctx, p.exercise_id, p.name, last.notes);
          reminders.push(Reminder(PROGRESSION, "info", "下次加重：" + p.name + " " + wfmt(w) + " → " + wfmt(nxt),
            last.date + " 的 " + sets.length + " 个工作组都做到了区间上限 " + high + " 次（" + reps.map(String).join("/") + "）。" +
            "按双进阶，下次用 " + wfmt(nxt) + "，回到区间下限 " + low + " 次重新练（哑铃按单只、单侧动作按每侧）。" +
            (note ? "\n那次训练的备注提到这个动作：「" + note + "」。和这条建议不一致时以备注和你的感觉为准。" : ""),
            { subject: ex_id + ":" + fmtG(w) + ":up", evidence: Object.assign({}, evidence, { next_weight_kg: nxt }), sticky: true,
              title_en: "Add weight next time: " + p.name + " " + wfmt_en(w) + " → " + wfmt_en(nxt),
              message_en: "On " + last.date + " all " + sets.length + " working sets reached the top of the range, " + high + " reps " +
                "(" + reps.map(String).join("/") + "). Double progression: next time use " + wfmt_en(nxt) + " and start again " +
                "at the bottom of the range, " + low + " reps (dumbbells per dumbbell, one-sided exercises per side)." +
                (note ? "\nThe notes of that workout mention this exercise: “" + note + "”. If they disagree with this " +
                  "advice, go by the notes and how you feel." : "") }));
        }
        return;
      }
      var streak = 0;
      for (var i = 0; i < hist.length; i++) {
        var h = hist[i];
        if (top_weight(h.sets) !== w || Math.min.apply(null, h.sets.map(function (x) { return x.reps; })) >= low) break;
        streak += 1;
      }
      if (streak >= P.FAIL_STREAK) {
        var down = Math.max(w - inc, 0), dates = hist.slice(0, streak).map(function (x) { return x.date; });
        reminders.push(Reminder(PROGRESSION, "warning", "连续没做到下限：" + p.name + " 建议减到 " + wfmt(down),
          "最近 " + streak + " 次（" + dates.join("、") + "）在 " + wfmt(w) + " 都有工作组没做到 " + low + " 次。" +
          "减一档到 " + wfmt(down) + "，动作做标准、每组留 1–2 次余力，重新从区间下限往上加。",
          { subject: ex_id + ":" + fmtG(w) + ":down",
            evidence: Object.assign({}, evidence, { fail_streak: streak, next_weight_kg: down }), sticky: true,
            title_en: "Missed the bottom of the range again: " + p.name + ", drop to " + wfmt_en(down),
            message_en: "In the last " + streak + " workouts (" + dates.join(", ") + ") at " + wfmt_en(w) + " a " +
              "working set fell short of " + low + " reps. Drop one step to " + wfmt_en(down) + ", keep strict form with 1–2 " +
              "reps in reserve per set, and build back up from the bottom of the range." }));
      }
    });
    var cut = U.addDays(ctx.day, -28), off = [];
    (ctx.sessions || []).forEach(function (s) {
      var d = s.date.slice(0, 10);
      if (!(cut <= d && d <= ctx.day)) return;
      s.exercises.forEach(function (e) {
        if (!rx.has(String(e.exercise_id)) && or(g(e, "category"), "strength") === "strength") off.push(e.name);
      });
    });
    var details = { checked: checked, never_logged: never, no_progression_in_template: skipped,
      logged_but_not_in_plan_28d: sortedUnique(off) };
    if (reminders.length) return Result(PROGRESSION, "triggered", "检查了 " + checked.length + " 个动作", reminders, details);
    if (!checked.length) return Result(PROGRESSION, "insufficient_data", "当前计划里的动作还没有训练记录", [], details);
    return Result(PROGRESSION, "not_triggered", "检查了 " + checked.length + " 个动作，都还没到区间上限、也没有连续失败", [], details);
  }

  // ================================================================ rules/nutrition.py
  var CALORIES = "calorie_adjust", FUEL = "pre_workout_fuel", CAPS = "frequency_caps", SUGAR = "sugar_caps";

  function window_avg(weights, end, n) {
    n = n || 7;
    var lo = U.addDays(end, -(n - 1));
    var vals = weights.filter(function (w) { return lo <= w.date && w.date <= end; }).map(function (w) { return w.weight_kg; });
    return { start: lo, end: end, n: vals.length, avg_kg: vals.length >= P.MIN_WEIGHINS_PER_WEEK ? U.round(U.fmean(vals), 2) : null };
  }
  function goal_lines(ctx, delta) {
    var goals = new Map();
    (ctx.goals || []).forEach(function (gg) {
      if (truthy(g(gg, "calories"))) { var k = or(g(gg, "name"), "当前目标"); if (!goals.has(k)) goals.set(k, gg.calories); }
    });
    var rows = [];
    goals.forEach(function (kcal, name) { rows.push({ goal: name, calories: kcal, suggested: kcal + delta }); });
    var text = rows.map(function (r) { return r.goal.split("（")[0] + " " + fmtG(r.calories) + " → " + fmtG(r.suggested) + " kcal"; }).join("；");
    var en = rows.map(function (r) {
      return (r.goal === "当前目标" ? "Current goal" : r.goal.split("（")[0]) + " " + fmtG(r.calories) + " → " + fmtG(r.suggested) + " kcal";
    }).join("; ");
    return [text, rows, en];
  }
  function calorie_adjust(ctx) {
    if (has(ctx.errors, "weights") && !truthy(ctx.weights)) return Result(CALORIES, "error", "读不到体重：" + ctx.errors.weights);
    var weights = ctx.weights || [];
    var w0 = window_avg(weights, ctx.day), w1 = window_avg(weights, U.addDays(ctx.day, -7)), w2 = window_avg(weights, U.addDays(ctx.day, -14));
    var one_week = truthy(w0.avg_kg) && truthy(w1.avg_kg) ? U.round(w1.avg_kg - w0.avg_kg, 2) : null;
    var two_week = truthy(w0.avg_kg) && truthy(w2.avg_kg) ? U.round((w2.avg_kg - w0.avg_kg) / 2, 2) : null;
    var details = { this_week: w0, last_week: w1, two_weeks_ago: w2, loss_kg_1wk: one_week, loss_kg_per_wk_2wk: two_week,
      weights: weights };
    var evidence = Object.assign({}, details, { source: "body-management.md 5.2（V2.1）、5.2.0", weights: weights.slice(-10) });
    var iw = isoWeek(ctx.day), week = iw[0] + "-W" + U.pad(iw[1], 2), t;
    if (nn(one_week) && one_week > P.FAST_LOSS_KG_PER_WEEK) {
      t = goal_lines(ctx, P.FAST_LOSS_KCAL);
      return Result(CALORIES, "triggered", "", [Reminder(CALORIES, "warning", "体重降得太快：建议每天加 " + P.FAST_LOSS_KCAL + " kcal",
        "近 7 天平均 " + pf(w0.avg_kg) + " kg，前 7 天 " + pf(w1.avg_kg) + " kg，一周降了 " + pf(one_week) + " kg（超过 " +
        pf(P.FAST_LOSS_KG_PER_WEEK) + " kg）。按 5.2 每天加约 " + P.FAST_LOSS_KCAL + " kcal" + (t[0] ? "（" + t[0] + "）" : "") +
        "。降太快容易疲劳、掉肌肉，也可能让尿酸短期升高。只是建议，要改目标告诉管家（set_nutrition_goals）。",
        { subject: "fast:" + week, evidence: Object.assign({}, evidence, { suggested_goals: t[1] }), sticky: true,
          title_en: "Losing weight too fast: add " + P.FAST_LOSS_KCAL + " kcal a day",
          message_en: "7-day average " + pf(w0.avg_kg) + " kg, the 7 days before " + pf(w1.avg_kg) + " kg: down " + pf(one_week) +
            " kg in a week (more than " + pf(P.FAST_LOSS_KG_PER_WEEK) + " kg). Per 5.2, add about " + P.FAST_LOSS_KCAL + " kcal a day" +
            (t[2] ? " (" + t[2] + ")" : "") + ". Losing too fast tends to bring fatigue and muscle loss, and can raise uric acid for a while. " +
            "Advice only; to change your goals, tell Jarvis (set_nutrition_goals)." })], details);
    }
    if (nn(two_week) && two_week < P.SLOW_LOSS_KG_PER_WEEK) {
      t = goal_lines(ctx, P.SLOW_LOSS_KCAL);
      var trend = two_week >= 0 ? "平均每周降 " + pf(two_week) + " kg" : "平均每周反而升 " + pf(-two_week) + " kg";
      var trend_en = two_week >= 0 ? "down " + pf(two_week) + " kg a week on average" : "up " + pf(-two_week) + " kg a week on average";
      return Result(CALORIES, "triggered", "", [Reminder(CALORIES, "info", "体重降得慢：建议每天少 " + (-P.SLOW_LOSS_KCAL) + " kcal",
        "近 7 天平均 " + pf(w0.avg_kg) + " kg，两周前那 7 天 " + pf(w2.avg_kg) + " kg，" + trend + "（不到 " + pf(P.SLOW_LOSS_KG_PER_WEEK) +
        " kg）。按 5.2 每天减约 " + (-P.SLOW_LOSS_KCAL) + " kcal" + (t[0] ? "（" + t[0] + "）" : "") +
        "：先减油和零食，不先砍训练日碳水。如果最近疲劳或早醒，先别减。只是建议，要改目标告诉管家（set_nutrition_goals）。",
        { subject: "slow:" + week, evidence: Object.assign({}, evidence, { suggested_goals: t[1] }), sticky: true,
          title_en: "Losing weight slowly: eat " + (-P.SLOW_LOSS_KCAL) + " kcal less a day",
          message_en: "7-day average " + pf(w0.avg_kg) + " kg, the 7 days two weeks ago " + pf(w2.avg_kg) + " kg: " + trend_en + " " +
            "(less than " + pf(P.SLOW_LOSS_KG_PER_WEEK) + " kg). Per 5.2, eat about " + (-P.SLOW_LOSS_KCAL) + " kcal less a day" +
            (t[2] ? " (" + t[2] + ")" : "") + ": cut oil and snacks first, not the carbs on training days. If you have been tired or waking early " +
            "lately, hold off. Advice only; to change your goals, tell Jarvis (set_nutrition_goals)." })], details);
    }
    if (!nn(one_week) && !nn(two_week)) {
      return Result(CALORIES, "insufficient_data", "周均体重算不出来：每个 7 天窗口至少要 " + P.MIN_WEIGHINS_PER_WEEK + " 次称重，" +
        "近 7 天 " + w0.n + " 次、前 7 天 " + w1.n + " 次、两周前 " + w2.n + " 次", [], details);
    }
    return Result(CALORIES, "not_triggered", "一周降 " + pf(one_week) + " kg、两周平均每周降 " + pf(two_week) + " kg，没到调整线（目标每周 " +
      pf(P.TARGET_LOSS_RANGE[0]) + "–" + pf(P.TARGET_LOSS_RANGE[1]) + " kg）", [], details);
  }

  function is_training_item(ctx, id, category) {
    var ex = ctx.ex(id) || {};
    if (g(ex, "modality") === "weight_reps") return true;
    var name = or(g(ex, "name"), "");
    return or(g(ex, "category"), category === undefined ? null : category) === "cardio" &&
      ["篮球", "游泳", "basketball", "swim"].some(function (k) { return name.indexOf(k) >= 0; });
  }
  function pre_workout_fuel(ctx) {
    var r = need(ctx, FUEL, ["plans", "presets", "exercises", "sessions"]);
    if (r !== null) return r;
    var planned = ctx.planned_exercises(), logged = [];
    ctx.sessions_on(ctx.day).forEach(function (s) { logged = logged.concat(s.exercises); });
    var training = planned.filter(function (p) { return is_training_item(ctx, p.exercise_id, p.category); })
      .concat(logged.filter(function (e) { return is_training_item(ctx, e.exercise_id, g(e, "category")); }));
    if (!training.length) {
      var what = Array.from(new Set(planned.map(function (p) { return or(p.preset, p.name); }))).join("、") || "休息";
      return Result(FUEL, "not_triggered", "今天计划：" + what + "，没有力量训练或球类 / 游泳");
    }
    var legs = sortedUnique(training.filter(function (x) {
      return ctx.is_leg_exercise(x.exercise_id, g(x, "category"), g(x, "primary_muscles"));
    }).map(function (x) { return x.name; }));
    var sessions = Array.from(new Set(planned.filter(function (p) {
      return truthy(g(p, "preset")) || is_training_item(ctx, p.exercise_id, p.category);
    }).map(function (p) { return or(p.preset, p.name); }).concat(ctx.sessions_on(ctx.day).map(function (s) { return s.name; }))));
    var lo7 = U.addDays(ctx.day, -P.SYMPTOM_LOOKBACK_DAYS), symptoms = [];
    ctx.journal_rows.forEach(function (j) {
      if ((j[1] === "low_blood_sugar" || j[1] === "underfed_training") && j[2] > 0 && lo7 <= j[0] && j[0] <= ctx.day) symptoms.push([j[0], j[1]]);
    });
    symptoms.sort(U.cmp);
    var lo = P.PRE_MEAL_CARBS_G[0], hi = P.PRE_MEAL_CARBS_G[1], plo = P.PRE_MEAL_PROTEIN_G[0], phi = P.PRE_MEAL_PROTEIN_G[1];
    var lines = ["练前 1.5–2 小时吃一顿正餐：碳水 " + lo + "–" + hi + "g、蛋白 " + plo + "–" + phi + "g；离上一餐超过 4 小时，练前 30–60 分钟再加一根香蕉或一片吐司配蜂蜜。"];
    var en = ["Eat a proper meal 1.5–2 h before: carbs " + lo + "–" + hi + " g, protein " + plo + "–" + phi + " g; if your last meal was more than 4 h " +
      "earlier, add a banana or a slice of toast with honey 30–60 min before."];
    if (legs.length) {
      lines.push("不空腹练腿；手边备一根香蕉或一盒果汁（快糖）。",
        "头晕、手抖、出冷汗、心慌：立刻停组坐下，吃 15–20g 快糖，15 分钟没缓解再吃一次，当天不再做高强度。");
      en.push("Don't train legs on an empty stomach; keep a banana or a juice box (fast sugar) at hand.",
        "Dizzy, shaky, cold sweat, racing heart: stop the set and sit down, take 15–20 g of fast sugar, take more if " +
        "it hasn't eased in 15 min, and no more high intensity that day.");
    }
    lines.push("练前 2 小时喝 500ml 水，练中每 15–20 分钟小口喝 150–250ml，1 小时内不超过约 1L。");
    en.push("Drink 500 ml of water 2 h before; during training sip 150–250 ml every 15–20 min, no more than about 1 L an hour.");
    var yday = U.addDays(ctx.day, -1), water = ctx.jget(yday, "water_l");
    if (water === null && truthy(ctx.water) && truthy(g(ctx.water, yday))) water = U.round(ctx.water[yday] / 1000, 1);
    if (water !== null && water < P.WATER_TARGET_L) {
      lines.push("昨天只喝了 " + fmtG(water) + "L（吃肌酸期间目标 3–3.5L）。");
      en.push("Yesterday you drank only " + fmtG(water) + " L (target 3–3.5 L while taking creatine).");
    }
    if (symptoms.length) {
      lines.push("最近有过：" + symptoms.map(function (x) { return x[0] + " " + (x[1] === "low_blood_sugar" ? "低血糖感" : "练前没吃够"); }).join("、") + "。");
      en.push("Recently: " + symptoms.map(function (x) { return x[0] + " " + (x[1] === "low_blood_sugar" ? "low blood sugar" : "under-fuelled training"); }).join(", ") + ".");
    }
    var label = sessions.join("、") || "训练", label_en = sessions.join(", ") || "Workout";
    var evidence = { planned: planned.map(function (p) { return { exercise_id: p.exercise_id, name: p.name, template: p.preset }; }),
      logged_today: logged.map(function (e) { return e.name; }), leg_exercises: legs,
      recent_symptoms: symptoms.map(function (x) { return { date: x[0], key: x[1] }; }), water_l_yesterday: water,
      source: "body-management.md 4.2.1" };
    var reminders = [Reminder(FUEL, legs.length && symptoms.length ? "warning" : "info",
      "今天" + label + "：" + (legs.length ? "练腿，练前吃够、备快糖" : "练前记得吃正餐"), lines.join("\n"),
      { subject: "", evidence: evidence,
        title_en: label_en + " today: " + (legs.length ? "leg day, eat enough before and keep fast sugar at hand" : "eat a proper meal before training"),
        message_en: en.join("\n") })];
    var food_today = (ctx.food || []).filter(function (f) { return f.date === ctx.day; });
    if (food_today.length) {
      var carbs = sum(food_today.map(function (f) { return or(g(f, "carbs_g"), 0); }));
      if (carbs < P.DAY_CARBS_WARN_G) {
        reminders.push(Reminder(FUEL, "warning", "今天记录的碳水只有 " + fmtG(carbs) + "g",
          "今天有" + label + "，饮食记录里全天碳水 " + fmtG(carbs) + "g，还不够练前一顿正餐（" + lo + "–" + hi + "g）。" +
          "空腹或碳水太少时训练容易低血糖；练前补一顿有主食的正餐。",
          { subject: "carbs", evidence: { carbs_g_logged: carbs, entries: food_today.map(function (f) {
              return { time: g(f, "time"), food: g(f, "food"), carbs_g: g(f, "carbs_g") };
            }), source: "body-management.md 4.2.1" },
            title_en: "Carbs logged today: only " + fmtG(carbs) + " g",
            message_en: label_en + " today, and your food log shows " + fmtG(carbs) + " g of carbs for the whole day: not enough for one " +
              "pre-workout meal (" + lo + "–" + hi + " g). Training fasted or on very few carbs can bring low blood sugar; " +
              "have a proper meal with a staple before training." }));
      }
    }
    return Result(FUEL, "triggered", "", reminders, { leg_day: legs.length > 0 });
  }

  function clean(name) {
    var s = (name || "").toLowerCase();
    P.RED_MEAT_EXCLUDE.forEach(function (x) { s = replaceAll(s, x.toLowerCase(), ""); });
    return s;
  }
  var RED_MEAT_MARK = /红肉\s*(\d+(?:\.\d+)?)\s*(?:g|克)/;
  function red_meat_estimate(food, quantity, unit) {
    var m = RED_MEAT_MARK.exec(food || "");
    if (m) return ["标注", U.round(parseFloat(m[1]))];
    var s = clean(food);
    for (var i = 0; i < P.RED_MEAT_KEYWORDS.length; i++) {
      var kw = P.RED_MEAT_KEYWORDS[i][0], grams = P.RED_MEAT_KEYWORDS[i][1];
      if (s.indexOf(kw.toLowerCase()) >= 0) {
        var mult = 1;
        if (truthy(quantity) && truthy(unit) && ["g", "克", "gram", "grams", "ml"].indexOf(strip(unit).toLowerCase()) < 0) {
          mult = Math.min(Math.max(U.round(quantity), 1), 3);
        }
        return [kw, grams * mult];
      }
    }
    return null;
  }
  function basis(b) { return truthy(b) ? ["（" + b + "）", " (" + b + ")"] : ["", ""]; }
  function intish(v) { return Number.isInteger(v) ? String(v) : String(v); }   // int(v) if v.is_integer() else v

  function frequency_caps(ctx) {
    var diet = ctx.diet;
    var g_cap = diet ? diet.red_meat_g() : null, meal_cap = diet ? diet.red_meat_meals() : null;
    var food_limits = diet ? diet.food_limits() : [];
    if (!nn(g_cap) && !nn(meal_cap) && !food_limits.length) return Result(CAPS, "not_triggered", "没有设置红肉 / 食物次数的饮食约束");
    if (has(ctx.errors, "food")) return Result(CAPS, "error", "读不到饮食记录：" + ctx.errors.food);
    var window = days_back(ctx.day, 7), keys = new Set(window);
    var food = (ctx.food || []).filter(function (f) { return keys.has(f.date); });
    var journal_g = new Map();
    window.forEach(function (d) { if (ctx.jhas(d, "red_meat_g")) journal_g.set(d, ctx.jget(d, "red_meat_g")); });
    var logged_days = sortedUnique(food.map(function (f) { return f.date; }).concat(Array.from(journal_g.keys())));
    if (!logged_days.length) {
      var why = !nn(ctx.food) ? "没连 SparkyFitness" : "这 7 天没有饮食记录";
      return Result(CAPS, "insufficient_data", why + "，日记里也没记红肉克数");
    }
    var per_day = {}, meals = new Set(), matched = [], times = {};
    food_limits.forEach(function (f) { times[f.target] = []; });
    window.forEach(function (d) {
      var hits = [];
      food.filter(function (x) { return x.date === d; }).forEach(function (f) {
        var est = red_meat_estimate(or(g(f, "food"), ""), g(f, "quantity"), g(f, "unit"));
        if (est !== null) {
          hits.push({ food: g(f, "food"), meal_type: g(f, "meal_type"), keyword: est[0], grams_est: est[1] });
          meals.add(JSON.stringify([d, or(g(f, "meal_type"), or(g(f, "time"), g(f, "food")))]));
        }
        var lim = diet ? diet.food_limit_hit(or(g(f, "food"), "")) : null;
        if (lim !== null) times[lim.target].push({ date: d, food: g(f, "food") });
      });
      if (journal_g.has(d)) {
        per_day[d] = { grams: journal_g.get(d), from: "日记 red_meat_g" };
        if (journal_g.get(d) > 0 && !hits.length) meals.add(JSON.stringify([d, "journal"]));
      } else if (hits.length) {
        per_day[d] = { grams: sum(hits.map(function (h) { return h.grams_est; })), from: "饮食记录估算" };
      }
      hits.forEach(function (h) { matched.push(Object.assign({ date: d }, h)); });
    });
    var grams = sum(Object.keys(per_day).map(function (k) { return per_day[k].grams; }));
    var coverage = "这 7 天有 " + logged_days.length + " 天有饮食记录" + (logged_days.length === 7 ? "" : "，没记的天不算，实际可能更多");
    var coverage_en = "Days with a food log in these 7: " + logged_days.length + (logged_days.length === 7 ? "" :
      "; days without a log are not counted, so the real amount may be higher");
    var evidence = { window: [window[0], window[6]], red_meat_g: grams, per_day: per_day, matched_entries: matched,
      food_times: times, red_meat_meals: meals.size, logged_days: logged_days, source: "diet_constraints",
      constraints: diet.rows.map(function (r) { return r.kind + (truthy(r.target) ? ":" + r.target : ""); }) };
    var reminders = [], alt = "接下来几天的蛋白质换成别的来源（禽肉、鱼、蛋、奶、豆制品）",
      alt_en = "for the next few days get your protein elsewhere (poultry, fish, eggs, dairy, soy)", b;
    if (nn(g_cap)) {
      b = basis(g(diet._one("red_meat_g_per_week"), "basis"));
      var cap = intish(g_cap);
      if (grams > g_cap) {
        reminders.push(Reminder(CAPS, "warning", "近 7 天红肉约 " + fmtG(grams) + "g，超过 " + cap + "g",
          "你的饮食约束：红肉每周 ≤ " + cap + "g" + b[0] + "。" + alt + "。" + coverage + "。",
          { subject: "red_meat_g", evidence: evidence,
            title_en: "Red meat in the last 7 days: about " + fmtG(grams) + " g, over " + cap + " g",
            message_en: "Your diet constraint: red meat ≤ " + cap + " g a week" + b[1] + ". For the next few days get " +
              "your protein elsewhere (poultry, fish, eggs, dairy, soy). " + coverage_en + "." }));
      } else if (grams >= g_cap * CP.WARN_FRACTION) {
        reminders.push(Reminder(CAPS, "info", "近 7 天红肉约 " + fmtG(grams) + "g，快到 " + cap + "g 了",
          "还剩约 " + fmtG(g_cap - grams) + "g；" + alt + "。" + coverage + "。",
          { subject: "red_meat_g", evidence: evidence,
            title_en: "Red meat in the last 7 days: about " + fmtG(grams) + " g, close to " + cap + " g",
            message_en: "About " + fmtG(g_cap - grams) + " g left; " + alt_en + ". " + coverage_en + "." }));
      }
    }
    food_limits.forEach(function (lim) {
      var eaten = times[lim.target], mx = intish(lim.max);
      b = basis(lim.basis);
      if (eaten.length > lim.max) {
        reminders.push(Reminder(CAPS, "warning", "近 7 天吃了 " + eaten.length + " 次" + lim.target + "，超过每周 " + mx + " 次",
          "你的饮食约束：" + lim.target + "每周最多 " + mx + " 次" + b[0] + "。",
          { subject: "food:" + lim.target, evidence: evidence,
            title_en: lim.target + " " + eaten.length + " times in the last 7 days, over " + mx + " a week",
            message_en: "Your diet constraint: " + lim.target + " at most " + mx + " times a week" + b[1] + "." }));
      } else if (eaten.length && eaten.length === lim.max) {
        var nxt = U.addDays(eaten[0].date, 7);
        reminders.push(Reminder(CAPS, "info", "这周的" + lim.target + "已经吃够了",
          "近 7 天吃了 " + eaten.length + " 次，每周最多 " + mx + " 次" + b[0] + "；下一次 " + nxt + " 以后。",
          { subject: "food:" + lim.target, evidence: evidence, title_en: lim.target + ": this week's limit reached",
            message_en: eaten.length + " times in the last 7 days, at most " + mx + " a week" + b[1] + "; next time " +
              "after " + nxt + "." }));
      }
    });
    if (nn(meal_cap) && meals.size > meal_cap) {
      b = basis(g(diet._one("red_meat_meals_per_week"), "basis"));
      var mc = intish(meal_cap);
      reminders.push(Reminder(CAPS, "warning", "近 7 天吃了 " + meals.size + " 顿红肉，超过每周 " + mc + " 顿",
        "你的饮食约束：红肉每周 ≤ " + mc + " 顿" + b[0] + "，培根、火腿等加工肉也算。" + coverage + "。",
        { subject: "red_meat_meals", evidence: evidence,
          title_en: "Red meat " + meals.size + " meals in the last 7 days, over " + mc + " a week",
          message_en: "Your diet constraint: red meat ≤ " + mc + " meals a week" + b[1] + "; processed meat such " +
            "as bacon and ham counts too. " + coverage_en + "." }));
    }
    var ft = {};
    Object.keys(times).forEach(function (k) { ft[k] = times[k].length; });
    var details = { red_meat_g: grams, food_times: ft, red_meat_meals: meals.size, logged_days: logged_days.length };
    if (reminders.length) return Result(CAPS, "triggered", coverage, reminders, details);
    return Result(CAPS, "not_triggered", "近 7 天红肉约 " + fmtG(grams) + "g、红肉 " + meals.size + " 顿，都没到你设的上限；" + coverage,
      [], details);
  }

  // nutrition/extras.py
  function known_sum(items, field) {
    var vals = items.filter(function (i) { return nn(g(i, field)); }).map(function (i) { return i[field]; });
    return vals.length ? U.round(sum(vals), 1) : null;
  }
  function day_totals(entries) {
    var out = {}, known = { of: entries.length };
    NG.OPTIONAL.forEach(function (f) {
      out[f] = known_sum(entries, f);
      known[f] = entries.filter(function (e) { return nn(g(e, f)); }).length;
    });
    out.known = known;
    out.sugary_drinks = entries.filter(function (e) { return truthy(g(e, "sugary_drink")); }).length;
    return out;
  }

  function sugar_caps(ctx) {
    if (has(ctx.errors, "food")) return Result(SUGAR, "error", "读不到饮食记录：" + ctx.errors.food);
    if (!nn(ctx.food)) return Result(SUGAR, "insufficient_data", "没连 SparkyFitness，没有饮食记录");
    var window = days_back(ctx.day, 7), keys = new Set(window);
    var today = ctx.food.filter(function (f) { return f.date === ctx.day; });
    var drinks = ctx.food.filter(function (f) { return keys.has(f.date) && truthy(g(f, "sugary_drink")); });
    var goal = or((ctx.goals || [null])[0], {});
    var cap = or(g(goal, "added_sugar_max_g"), P.ADDED_SUGAR_CAP_G), warn = Math.max(P.ADDED_SUGAR_WARN_G, cap);
    var t = day_totals(today), added = t.added_sugar_g, known = t.known;
    var coverage = "今天 " + known.of + " 条记录里 " + known.added_sugar_g + " 条有添加糖的数，没数的不算";
    var coverage_en = "Today's entries with an added sugar value: " + known.added_sugar_g + " of " + known.of + "; the others are not counted";
    var cdef = has(goal, "added_sugar_cap_default") ? goal.added_sugar_cap_default : true;
    var evidence = { date: ctx.day, added_sugar_g: added, cap_g: cap, cap_from_goal: !truthy(cdef), warn_g: warn, known: known,
      entries: today.filter(function (f) { return truthy(g(f, "added_sugar_g")) || truthy(g(f, "sugar_g")); })
        .map(function (f) { return { food: g(f, "food"), added_sugar_g: g(f, "added_sugar_g"), sugar_g: g(f, "sugar_g") }; }),
      source: "WHO 2015 糖摄入指南（游离糖 < 10% 能量，最好 < 5%，约 25g）" };
    var reminders = [];
    if (nn(added) && added > cap) {
      reminders.push(Reminder(SUGAR, added > warn ? "warning" : "info", "今天添加糖约 " + fmtG(added) + "g，超过 " + fmtG(cap) + "g",
        "添加糖每天 ≤ " + fmtG(cap) + "g（WHO：最好 < 5% 能量，约 25g；> 50g 超过 10% 的上限）。" +
        "甜饮料、甜点、酱料里的糖都算；接下来换无糖饮料和水果。" + coverage + "。",
        { subject: "added_sugar", evidence: evidence,
          title_en: "Added sugar today: about " + fmtG(added) + " g, over " + fmtG(cap) + " g",
          message_en: "Added sugar ≤ " + fmtG(cap) + " g a day (WHO: ideally < 5% of energy, about 25 g; > 50 g " +
            "is over the 10% limit). Sugar in sweet drinks, desserts and sauces all counts; " +
            "switch to sugar-free drinks and fruit. " + coverage_en + "." }));
    }
    if (drinks.length > P.SUGARY_DRINKS_PER_WEEK) {
      reminders.push(Reminder(SUGAR, "warning", "近 7 天喝了 " + drinks.length + " 次含糖饮料",
        "超过每周 " + P.SUGARY_DRINKS_PER_WEEK + " 次。果糖饮料会升尿酸和甘油三酯；换成无糖版、气泡水或茶。",
        { subject: "sugary_drinks", evidence: { window: [window[0], window[6]],
            drinks: drinks.map(function (f) { return { date: f.date, food: g(f, "food") }; }), limit: P.SUGARY_DRINKS_PER_WEEK,
            source: "Choi & Curhan 2008 BMJ（含糖软饮与痛风）；ACR 2020 痛风指南" },
          title_en: "Sugary drinks " + drinks.length + " times in the last 7 days",
          message_en: "More than " + P.SUGARY_DRINKS_PER_WEEK + " a week. Fructose drinks raise uric acid and " +
            "triglycerides; switch to sugar-free versions, sparkling water or tea." }));
    }
    var details = { added_sugar_g: added, cap_g: cap, sugary_drinks_7d: drinks.length, known: known };
    var note = "今天添加糖 " + (!nn(added) ? "未知" : fmtG(added) + "g") + "（上限 " + fmtG(cap) + "g），近 7 天含糖饮料 " + drinks.length + " 次";
    if (reminders.length) return Result(SUGAR, "triggered", note, reminders, details);
    if (!today.length && !drinks.length) return Result(SUGAR, "insufficient_data", "今天还没有饮食记录", [], details);
    return Result(SUGAR, "not_triggered", note, [], details);
  }

  // ================================================================ rules/safety.py
  var EXERCISES = "exercise_restrictions", READINESS = "readiness";
  function meta_text(meta, key) {
    var t = strip(or(g(meta || {}, key), ""));
    return t && "。！？.!?".indexOf(t[t.length - 1]) < 0 ? t + "。" : t;
  }
  function rstrip(s) { return s.replace(/\s+$/, ""); }

  function exercise_restrictions(ctx) {
    var r = need(ctx, EXERCISES, ["plans", "presets", "exercises", "sessions"]);
    if (r !== null) return r;
    var reminders = [], plan = ctx.active_plan(), in_plan = new Map();
    if (plan !== null) {
      plan.assignments.forEach(function (a) {
        var items = nn(g(a, "workout_preset_id")) ? ((ctx.preset(a.workout_preset_id) || {}).exercises || [])
          : [{ exercise_id: a.exercise_id, name: g(a, "exercise_name") }];
        items.forEach(function (x) {
          if (!in_plan.has(String(x.exercise_id))) {
            in_plan.set(String(x.exercise_id), { id: x.exercise_id, name: ctx.exercise_name(x.exercise_id, g(x, "name")),
              template: g(a, "workout_preset_name") });
          }
        });
      });
    }
    in_plan.forEach(function (info, ex_id) {
      var st = ctx.status_of(info.id);
      if (st[0] === "banned") {
        var tpl = or(info.template, plan.name);
        reminders.push(Reminder(EXERCISES, "alert", "训练计划里有禁用动作：" + info.name,
          "「" + tpl + "」里有 " + info.name + "，它是永久不做的动作（4.3）。" + meta_text(st[1], "reason") +
          "请在 SparkyFitness 里把它从模板删掉。" + meta_text(st[1], "cues"),
          { subject: "plan:" + ex_id, sticky: true,
            evidence: { exercise_id: info.id, exercise: info.name, template: info.template, status_basis: st[2],
              source: "body-management.md 4.3" },
            title_en: "Banned exercise in your training plan: " + info.name,
            message_en: rstrip("“" + tpl + "” contains " + info.name + ", an exercise you never do (4.3). " +
              meta_text(st[1], "reason") + "Remove it from the template in SparkyFitness. " + meta_text(st[1], "cues")) }));
      }
    });
    var byRef = ctx.meta_by_ref(), caps = new Map();
    Object.keys(P.CAUTION_MAX_WEIGHT_KG).forEach(function (ref) {
      if (byRef.has(ref) && truthy(g(byRef.get(ref), "app_exercise_id"))) caps.set(String(byRef.get(ref).app_exercise_id), [ref, P.CAUTION_MAX_WEIGHT_KG[ref]]);
    });
    var banned_since = U.addDays(ctx.day, -(P.BANNED_LOG_LOOKBACK_DAYS - 1)), caution_since = U.addDays(ctx.day, -(P.CAUTION_LOG_LOOKBACK_DAYS - 1));
    (ctx.sessions || []).forEach(function (s) {
      var d = s.date.slice(0, 10);
      if (d > ctx.day) return;
      s.exercises.forEach(function (e) {
        var st = ctx.status_of(e.exercise_id), name = ctx.exercise_name(e.exercise_id, g(e, "name"));
        var sets = e.sets.map(function (x) { return { reps: g(x, "reps"), weight_kg: g(x, "weight_kg") }; });
        if (st[0] === "banned" && d >= banned_since) {
          reminders.push(Reminder(EXERCISES, "warning", d + " 做了禁用动作：" + name,
            name + " 是永久不做的动作（4.3）。" + meta_text(st[1], "reason") + "下次换成：" + or(meta_text(st[1], "cues"), "计划里的替代动作。"),
            { subject: "log:" + e.exercise_id + ":" + d, sticky: true,
              evidence: { exercise_id: e.exercise_id, exercise: name, date: d, sets: sets, status_basis: st[2],
                source: "body-management.md 4.3" },
              title_en: "Banned exercise on " + d + ": " + name,
              message_en: name + " is an exercise you never do (4.3). " + meta_text(st[1], "reason") + "Next time do instead: " +
                or(meta_text(st[1], "cues"), "the alternative in your plan.") }));
        }
        if (caps.has(String(e.exercise_id)) && d >= caution_since) {
          var cap = caps.get(String(e.exercise_id))[1];
          var heavy = e.sets.length ? Math.max.apply(null, e.sets.map(function (x) { return or(g(x, "weight_kg"), 0); })) : 0;
          if (heavy > cap) {
            reminders.push(Reminder(EXERCISES, "warning", d + " " + name + " 用了 " + fmtG(heavy) + " kg，超过 " + fmtG(cap) + " kg",
              "大重量绳索面拉在练上斜方，4.3 列为永久不做；计划是 " + fmtG(cap) + " kg 左右的轻重量（4.4）。" + meta_text(st[1], "cues"),
              { subject: "weight:" + e.exercise_id + ":" + d, sticky: true,
                evidence: { exercise_id: e.exercise_id, exercise: name, date: d, sets: sets, max_kg: cap,
                  source: "body-management.md 4.3、4.4" },
                title_en: d + " " + name + ": " + fmtG(heavy) + " kg, over " + fmtG(cap) + " kg",
                message_en: rstrip("Heavy cable face pulls train the upper traps, and 4.3 lists them as never-do; the plan is " +
                  "a light weight around " + fmtG(cap) + " kg (4.4). " + meta_text(st[1], "cues")) }));
          }
        }
      });
    });
    var caution = [];
    ctx.planned_exercises().forEach(function (p) {
      var st = ctx.status_of(p.exercise_id);
      if (st[0] === "caution" && !caution.some(function (c) { return String(c.exercise_id) === String(p.exercise_id); })) {
        var desc = or(g(ctx.ex(p.exercise_id) || {}, "description"), "");
        if (startsWith(desc, "【注意】")) desc = desc.slice("【注意】".length);
        caution.push({ exercise_id: p.exercise_id, exercise: p.name, reason: meta_text(st[1], "reason") || null,
          cues: meta_text(st[1], "cues") || desc.split("（迁移自")[0], status_basis: st[2] });
      }
    });
    if (caution.length) {
      reminders.push(Reminder(EXERCISES, "info", "今天计划里有 " + caution.length + " 个要小心的动作",
        caution.map(function (c) { return "· " + c.exercise + "：" + c.cues + (c.reason ? "（" + c.reason + "）" : ""); }).join("\n"),
        { subject: "", evidence: { exercises: caution, source: "body-management.md 4.3、exercise_meta" },
          title_en: "Exercises to take care with in today's plan: " + caution.length,
          message_en: caution.map(function (c) { return "· " + c.exercise + ": " + c.cues + (c.reason ? " (" + c.reason + ")" : ""); }).join("\n") }));
    }
    var details = { plan_exercises: in_plan.size, unmapped_meta: ctx.meta.filter(function (m) { return !truthy(g(m, "app_exercise_id")); }).length };
    if (reminders.length) return Result(EXERCISES, "triggered", "", reminders, details);
    var note = "计划和最近的训练记录里没有禁用动作，今天也没有要小心的动作";
    if (details.unmapped_meta) note += "（" + details.unmapped_meta + " 个 exercise_meta 还没对上 SparkyFitness 的动作 ID，先跑 link-exercises）";
    return Result(EXERCISES, "not_triggered", note, [], details);
  }

  function heavy_day(ctx) {
    var out = [];
    ctx.planned_for().forEach(function (item) {
      if (item.kind === "preset") {
        if (item.preset.exercises.some(function (x) { return g(ctx.ex(x.exercise_id) || {}, "modality") === "weight_reps"; })) out.push(item.preset.name);
      } else {
        var n = or(item.name, "").toLowerCase();
        if (P.HEAVY_CARDIO_KEYWORDS.some(function (k) { return n.indexOf(k) >= 0; })) out.push(item.name);
      }
    });
    return out;
  }

  function readiness(ctx) {
    var row = has(ctx.scores, ctx.day) ? ctx.scores[ctx.day] : null;
    if (row === null) return Result(READINESS, "insufficient_data", "daily_scores 里没有这天（先跑 compute-scores）");
    var flags = or(g(row, "flags"), {}), reminders = [], rec = g(row, "recovery"), debt = g(row, "sleep_debt_min");
    var evidence = { date: ctx.day, recovery: rec, sleep_score: g(row, "sleep_score"), sleep_debt_min: debt,
      sleep_actual_min: g(row, "sleep_actual_min"), flags: flags, source: "系统设计第 7 节、body-management.md 4.3、6.2" };
    var heavy = nn(ctx.plans) ? heavy_day(ctx) : [];
    if (nn(rec) && rec < P.RECOVERY_RED) {
      if (heavy.length) {
        reminders.push(Reminder(READINESS, "warning", "恢复分 " + rec + "（红），今天降强度",
          "今天计划「" + heavy.join("、") + "」。恢复红的日子：每个动作少做 1 组、每组多留 1–2 次余力，不冲重量；" +
          "或者换成爬坡 / 肩部 routine。身体不舒服就休息。",
          { subject: "red_heavy", evidence: Object.assign({}, evidence, { planned_heavy: heavy }),
            title_en: "Recovery " + rec + " (red): lower the intensity today",
            message_en: "Today's plan: “" + heavy.join(", ") + "”. On a red day do one set less per exercise, keep 1–2 more reps " +
              "in reserve per set and don't push the weight; or switch to incline walking / the shoulder routine. " +
              "If you feel unwell, rest." }));
      } else {
        reminders.push(Reminder(READINESS, "info", "恢复分 " + rec + "（红）", "今天计划里没有大重量训练；以散步、灵活度为主，早点睡。",
          { subject: "red", evidence: evidence, title_en: "Recovery " + rec + " (red)",
            message_en: "No heavy training in today's plan; keep to walking and mobility, and go to bed early." }));
      }
    }
    if (has(flags, "illness_warning")) {
      reminders.push(Reminder(READINESS, "warning", "可能在生病或过度疲劳",
        flags.illness_warning + "。今天别做高强度，注意体温和症状；连续几天这样考虑看 GP。",
        { subject: "illness", evidence: evidence, title_en: "Possibly getting sick or overreaching",
          message_en: "Resting HR more than 5 above baseline and HRV below 85% of baseline. No high " +
            "intensity today; watch your temperature and symptoms, and if it lasts several " +
            "days, consider seeing a GP." }));
    }
    if (nn(debt) && debt >= P.SLEEP_DEBT_HIGH_MIN) {
      var needm = or(g(row, "sleep_need_min"), "—");
      reminders.push(Reminder(READINESS, debt >= 2 * P.SLEEP_DEBT_HIGH_MIN ? "warning" : "info", "睡眠债 " + fix(debt / 60, 1) + " 小时",
        "最近 14 晚加权平均少睡 " + debt + " 分钟（睡眠需求 " + needm + " 分钟）。今晚提前上床；" +
        "午睡最多 20 分钟、下午 3 点后不睡，12 点后不喝咖啡（6.2）。",
        { subject: "sleep_debt", evidence: evidence, title_en: "Sleep debt " + fix(debt / 60, 1) + " h",
          message_en: "Over the last 14 nights you slept " + debt + " min less than you need on a weighted average (sleep need " +
            needm + " min). Go to bed earlier tonight; nap at most 20 min and not after 3 pm, " +
            "and no coffee after 12 noon (6.2)." }));
    }
    if (reminders.length) return Result(READINESS, "triggered", "", reminders);
    if (!nn(rec)) {
      var whyText = Object.keys(flags).filter(function (k) { return ["no_sleep", "hrv_missing", "baseline_insufficient"].indexOf(k) >= 0; })
        .map(function (k) { return k + "：" + flags[k]; }).join("；");
      return Result(READINESS, "insufficient_data", "今天没有恢复分（" + (whyText || "见 flags") + "）；睡眠债 " +
        (nn(debt) ? debt : "—") + " 分钟没到 " + P.SLEEP_DEBT_HIGH_MIN);
    }
    return Result(READINESS, "not_triggered", "恢复分 " + rec + "、睡眠债 " + ps(debt) + " 分钟，没到提醒线（红 < " + P.RECOVERY_RED +
      "、睡眠债 ≥ " + P.SLEEP_DEBT_HIGH_MIN + "）" + (nn(ctx.plans) ? "" : "；没连 SparkyFitness，不知道今天的计划"));
  }

  // ================================================================ rules/sleep_goal.py
  var SLEEP = "sleep_goal";
  function caffeine_cutoff(t) { return Math.min(t - GP.CAFFEINE_BEFORE_MIN, GP.CAFFEINE_NOON_CUTOFF_MIN - 1440); }
  function sleep_goal(ctx) {
    var goal = ctx.sleep_goal;
    if (goal === null) return Result(SLEEP, "insufficient_data", "还没设入睡时间目标");
    if (goal.status !== "active") return Result(SLEEP, "not_triggered", "入睡目标已暂停");
    var night = ctx.day, nights = ctx.sleep_nights, pace = goal.pace_min_per_step, plan = goal.plan_rows;
    var t = CG.target_for(plan, nights, night, pace);
    if (t === null) return Result(SLEEP, "not_triggered", "计划从 " + goal.start_date + " 那晚开始");
    var F = CG.fmt, fin = CG.to_off(goal.target_bedtime), recent = CG.window(nights, night);
    var last = recent.length ? recent[recent.length - 1] : null;
    var wake = truthy(goal.target_wake) ? CG.to_off(goal.target_wake) : t + or(ctx.sleep_duration_min, GP.DEFAULT_DURATION_MIN);
    var cut = caffeine_cutoff(t), woke = last ? last.wake_off - 1440 : null;
    var lines = [F(t - GP.SCREEN_OFF_BEFORE_MIN) + " 起停屏、调暗灯光",
      woke !== null && woke >= cut ? "今天别再喝咖啡" : "咖啡 " + F(cut) + " 前",
      "晚饭、训练 " + F(t - GP.DINNER_BEFORE_MIN) + " 前结束", F(wake) + " 起床；" + GP.MORNING_LIGHT];
    var lines_en = ["From " + F(t - GP.SCREEN_OFF_BEFORE_MIN) + ": screens off, lights dimmed",
      woke !== null && woke >= cut ? "No more coffee today" : "Coffee before " + F(cut),
      "Finish dinner and training by " + F(t - GP.DINNER_BEFORE_MIN), "Get up at " + F(wake) + "; " + GP.MORNING_LIGHT_EN];
    var stage = CG.stage_for(plan, night);
    var evidence = { night: night, tonight_target: F(t), plan_stage: F(stage.bed_off), final_target: goal.target_bedtime,
      wake_target: F(wake), last_night: last ? { bedtime: last.bedtime, wake: last.wake } : null,
      source: "body-management.md 6.1、6.2；goals/params.py" };
    var reminders = [Reminder(SLEEP, "info", "今晚 " + F(t) + " 前睡", lines.join("\n"), { subject: "", evidence: evidence,
      title_en: "Bed by " + F(t) + " tonight", message_en: lines_en.join("\n") })];
    var streak = [];
    for (var k = 1; k <= GP.DEVIATION_NIGHTS; k++) {
      var d = U.addDays(night, -k), x = null;
      for (var i = 0; i < recent.length; i++) if (recent[i].night === d) { x = recent[i]; break; }
      var tt = CG.target_for(plan, nights, d, pace);
      if (x === null || tt === null || x.bed_off <= tt + GP.DEVIATION_MIN) break;
      streak.push(x.bed_off - tt);
    }
    if (streak.length >= GP.DEVIATION_NIGHTS) {
      var late = U.round(sum(streak) / streak.length);
      reminders.push(Reminder(SLEEP, "warning", "连续 " + streak.length + " 晚比目标晚",
        "平均晚 " + late + " 分钟。\n今晚先守住 " + F(t) + "；按时起床、晒太阳。",
        { subject: "deviation", evidence: Object.assign({}, evidence, { late_by_min: streak }),
          title_en: "Later than your target " + streak.length + " nights in a row",
          message_en: late + " min late on average.\nTonight, hold " + F(t) + "; get up on time and get some sunlight." }));
    }
    var a = CG.avg(recent, "bed_off");
    if (a !== null && a <= fin + GP.ON_TARGET_TOL_MIN) {
      reminders.push(Reminder(SLEEP, "info", "达成目标：7 晚平均 " + F(a), "目标 " + goal.target_bedtime + "，保持住起床时间和早上晒太阳。",
        { subject: "final:" + goal.target_bedtime, evidence: evidence, sticky: true,
          title_en: "Goal reached: 7-night average " + F(a),
          message_en: "Goal " + goal.target_bedtime + "; keep your wake time and the morning sunlight." }));
    } else if (a !== null && stage.bed_off !== fin && stage !== plan[0] && a <= stage.bed_off + GP.ON_TARGET_TOL_MIN) {
      reminders.push(Reminder(SLEEP, "info", "到 " + F(stage.bed_off) + " 这一档了",
        "7 晚平均 " + F(a) + "。按计划继续往前提，最终 " + goal.target_bedtime + "。",
        { subject: "stage:" + F(stage.bed_off), evidence: evidence, sticky: true,
          title_en: "Reached the " + F(stage.bed_off) + " step",
          message_en: "7-night average " + F(a) + ". Keep moving earlier as planned; final goal " + goal.target_bedtime + "." }));
    }
    return Result(SLEEP, "triggered", "", reminders, { tonight_target: F(t), avg_bedtime_7d: F(a), nights_7d: recent.length });
  }

  // ================================================================ rules/hydration.py
  var WATER = "water_halfway";
  function hydration(ctx) {
    if (!nn(ctx.water) || !nn(ctx.water_goal)) return Result(WATER, "insufficient_data", "喝水记录没开放或没有目标");
    var now = ctx.now_local();
    if (ctx.day !== now.date) return Result(WATER, "not_triggered", "只提醒当天");
    if (now.hour < WP.REMIND_FROM_HOUR) return Result(WATER, "not_triggered", WP.REMIND_FROM_HOUR + ":00 之前不提醒");
    var total = has(ctx.water, ctx.day) ? ctx.water[ctx.day] : 0, goal = ctx.water_goal;
    var details = { total_ml: total, goal_ml: goal };
    if (total >= goal * WP.REMIND_SHARE) return Result(WATER, "not_triggered", "已经喝到一半", [], details);
    return Result(WATER, "triggered", "", [Reminder(WATER, "info", "下午了，水还没喝到一半",
      "今天喝了 " + total + " ml，目标 " + goal + " ml。", { evidence: details, title_en: "Afternoon, and less than half your water",
        message_en: "You have had " + total + " ml today; your goal is " + goal + " ml." })], details);
  }

  // ================================================================ rules/engine.py
  var RULES = [[PROGRESSION, progression], [CALORIES, calorie_adjust], [FUEL, pre_workout_fuel], [CAPS, frequency_caps],
    [SUGAR, sugar_caps], [EXERCISES, exercise_restrictions], [READINESS, readiness], [SLEEP, sleep_goal], [WATER, hydration]];
  function evaluate_ctx(ctx) {
    return RULES.map(function (r) {
      try { return r[1](ctx); } catch (e) { return Result(r[0], "error", (e && e.name || "Error") + ": " + (e && e.message)); }
    });
  }
  function reminder_rows(results) {
    var out = new Map();
    results.forEach(function (res) {
      if (res.status === "error") return;
      res.reminders.forEach(function (r) {
        out.set(r.rule_key + "\u0000" + r.subject, { rule_key: r.rule_key, subject: r.subject, level: r.level, title: r.title,
          message: r.message, title_en: r.title_en, message_en: r.message_en, evidence: jsonable(r.evidence), sticky: r.sticky });
      });
    });
    return Array.from(out.values());
  }
  /** engine.evaluate_data: every rule on `data` (context.to_data / assemble). */
  function evaluate(data) {
    var ctx = new Ctx(data), results = evaluate_ctx(ctx);
    return { date: ctx.day, rules_version: RULES_VERSION, rules: results.map(summary), reminders: reminder_rows(results) };
  }

  // ================================================================ goals/nutrition.py, nutrition/daily.py
  function version_for(rows, day) {
    for (var i = 0; i < rows.length; i++) if (rows[i].effective_from <= day) return rows[i];
    return null;
  }
  function day_type(version, day) { return (version.training_weekdays || []).indexOf(isoweekday(day)) >= 0 ? "training" : "rest"; }
  function goal_name(t, version_name, en) {
    if (en) return NG.DAY_TYPE_EN[t] + (truthy(version_name) ? " (" + version_name + ")" : "");
    return NG.DAY_TYPE_ZH[t] + (truthy(version_name) ? "（" + version_name + "）" : "");
  }
  /** goals/nutrition.goal_for: en = the name's language (undefined: OBS.lang). */
  function goal_for(day, versions, sparky, en) {
    if (en === undefined || en === null) en = OBS.lang === "en";
    var version = version_for(versions, day);
    var out = { date: day, source: null, version_id: null, effective_from: null, day_type: null, name: null };
    NG.FIELDS.forEach(function (f) { out[f] = null; });
    out.added_sugar_cap_default = false;
    if (version !== null) {
      var t = day_type(version, day), s = version[t] || {};
      out.source = "body_engine"; out.version_id = version.id; out.effective_from = version.effective_from;
      out.day_type = t; out.name = goal_name(t, g(version, "name"), en);
      NG.FIELDS.forEach(function (f) { out[f] = g(s, f); });
    } else if (nn(sparky)) {
      out.source = "sparkyfitness"; out.name = g(sparky, "name");
      ["calories", "protein_g", "carbs_g", "fat_g"].forEach(function (f) { out[f] = g(sparky, f); });
    }
    if (!nn(out.added_sugar_max_g)) { out.added_sugar_max_g = P.ADDED_SUGAR_CAP_G; out.added_sugar_cap_default = true; }
    return out;
  }
  function adherence(day, goal) {
    var out = { calories: null, protein: null };
    if (!truthy(day.logged) || !truthy(goal)) return out;
    if (truthy(g(goal, "calories"))) out.calories = Math.abs(day.calories - goal.calories) <= P.CALORIE_ADHERENCE_TOL * goal.calories;
    if (truthy(g(goal, "protein_g"))) out.protein = day.protein_g >= P.PROTEIN_ADHERENCE_MIN * goal.protein_g;
    return out;
  }
  function day_from_totals(day, totals, goal) {
    var t = totals || {}, d = { date: day, logged: or(g(t, "entries"), 0) > 0 };
    NG.TOTAL_KEYS.forEach(function (k) { d[k] = U.round(or(g(t, k), 0), 1); });
    NG.UNKNOWN_ON_PHONE.forEach(function (k) { d[k] = null; });
    var known = { of: or(g(t, "entries"), 0) };
    NG.UNKNOWN_ON_PHONE.forEach(function (k) { known[k] = 0; });
    d.known = known; d.sugary_drinks = 0;
    if (truthy(goal)) { var gg = {}; NG.GOAL_KEYS.forEach(function (k) { gg[k] = g(goal, k); }); d.goal = gg; } else d.goal = null;
    d.adherence = adherence(d, goal);
    return d;
  }

  // ================================================================ rules/context.py: assemble (raw -> data)
  function merge_weights(sparky, body_mass) {
    var weights = new Map();
    (sparky || []).forEach(function (r) {
      if (nn(g(r, "weight_kg"))) weights.set(r.date, { date: r.date, weight_kg: r.weight_kg, source: "sparkyfitness" });
    });
    body_mass.forEach(function (r) {
      if (!weights.has(r.date)) weights.set(r.date, { date: r.date, weight_kg: r.value, source: "body_engine" });
    });
    if (nn(sparky) || body_mass.length) {
      return Array.from(weights.values()).sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
    }
    return null;
  }
  function assemble(raw) {
    var day = raw.day, goals = null;
    if (nn(raw.nutrition_versions)) {
      var fb = raw.sparky_goals || {};
      goals = [];
      for (var k = 0; k < 7; k++) {
        var d = U.addDays(day, k);
        goals.push(goal_for(d, raw.nutrition_versions, has(fb, d) ? fb[d] : null, false));
      }
    }
    var lo = U.addDays(day, -GP.HISTORY_DAYS), hi = U.addDays(day, 1);
    var sessions = (raw.sleep_sessions || []).filter(function (s) { return lo <= s.wake_date && s.wake_date <= hi; });
    var nights = CG.nights_from_sessions(sessions, raw.tz).filter(function (n) { return n.night < day; });
    var out = {};
    ["day", "tz", "now", "sessions", "exercises", "presets", "plans", "meta", "food", "sleep_goal", "diet", "water",
      "water_goal", "errors", "missing"].forEach(function (key) { out[key] = has(raw, key) ? raw[key] : null; });
    out.goals = goals;
    out.weights = merge_weights(g(raw, "sparky_weights"), raw.body_mass || []);
    out.scores = {};
    (raw.scores || []).forEach(function (r) { var x = {}; SCORE_FIELDS.forEach(function (f) { x[f] = g(r, f); }); out.scores[r.date] = x; });
    out.journal = (raw.journal || []).map(function (r) { return [r.date, r.key, r.value]; }).sort(U.cmp);
    out.sleep_nights = nights;
    out.sleep_duration_min = CG.duration_from(g(raw, "sleep_goal"), g(raw, "sleep_need"));
    out.overrides = {};
    (raw.overrides || []).forEach(function (r) { out.overrides[r.date] = { sessions: r.sessions || [] }; });
    return out;
  }

  // ================================================================ entry points (stamped)
  function stamp(x) { x.algo_version = RULES_VERSION; x.bundle_version = OBS.VERSION; return x; }
  OBS.ALGO_VERSIONS.rules = RULES_VERSION;
  /** Every rule on `data` (the Ctx as plain data): {date, rules_version, rules, reminders}. */
  OBS.rulesEvaluate = function (data) { return stamp(evaluate(data)); };
  /** The phone's entry point: raw sources (its own tables + GET /rules/inputs) -> the day's reminders. */
  OBS.rulesLocal = function (raw) { return stamp(evaluate(assemble(raw))); };
  /** The nutrition goal of each day of [from, to] (goals/nutrition.goal_for) in OBS.lang. */
  OBS.nutritionGoals = function (versions, from, to, sparky) {
    return U.dateRange(from, to).map(function (d) { return goal_for(d, versions || [], (sparky || {})[d] || null); });
  };
  /** nutrition/daily day rows from the log's day sums (ext_records nutrition_totals) and the goal versions. */
  OBS.nutritionDays = function (totals, versions, from, to, sparky) {
    return U.dateRange(from, to).map(function (d) {
      return day_from_totals(d, (totals || {})[d] || null, goal_for(d, versions || [], (sparky || {})[d] || null));
    });
  };

  return { RULES_VERSION: RULES_VERSION, params: { rules: P, goals: GP, water: WP, constraints: CP, nutrition: NG, context: CTX },
    jsonable: jsonable, Ctx: Ctx, Diet: Diet, need: need, days_back: days_back, red_meat_estimate: red_meat_estimate,
    day_totals: day_totals, known_sum: known_sum,
    rules: { progression: progression, calorie_adjust: calorie_adjust, pre_workout_fuel: pre_workout_fuel,
      frequency_caps: frequency_caps, sugar_caps: sugar_caps, exercise_restrictions: exercise_restrictions, readiness: readiness,
      sleep_goal: sleep_goal, hydration: hydration },
    evaluate_ctx: evaluate_ctx, reminder_rows: reminder_rows, evaluate: evaluate, assemble: assemble, merge_weights: merge_weights,
    nutrition: { version_for: version_for, day_type: day_type, goal_for: goal_for, adherence: adherence, day_from_totals: day_from_totals },
    isoWeek: isoWeek, pf: pf };
})(OBS.util, OBS.coach.goals, OBS.coach);
