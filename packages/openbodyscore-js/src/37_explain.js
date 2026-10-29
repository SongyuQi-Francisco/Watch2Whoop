/* explain (2.0.0 package M6, docs/ios-native-2.0.md): the personal part of the explanation sheets on the phone. Port of the
pure parts of body_engine
- explain/personal.py: the calculators `hrv_compute`, `resting_hr_compute`, `sleep_hours_compute`, `sleep_score_compute`,
  `sleep_debt_compute`, `sleep_consistency_compute`, `sleep_efficiency_compute`, `bedtime_items` (= assistant/status
  calc_bedtime), `bedtime_compute`, `recovery_compute`, `strain_compute`, `vo2max_compute`, `weight_compute`,
  `trend_compute`, `lab_compute`, the dispatch `metric_out` / `lab_out` and the sheet assembly `metric_view`, `lab_view`
  (`_entry_view`, `_apply`, `_prompt`);
- records/lab_history.py `history_of`, `trend`, `card` and records/store.py `compare_to_range` (lab arrows / status cards).
Rules only, no AI: `ai_prompt` is a question the viewer hands to their own AI. The static texts (title, what it is, how to
read it, tips) are the glossary's (explain/glossary*.py): the phone takes them from the server's cached sheet and passes
them in as `entry`; this file builds everything personal around them. Texts follow OBS.lang ("zh" / "en") like the
server's L(); `missing` stays Chinese as on the server. Thresholds: scores/params.py, web/views.py (recovery bands),
records/lab_params.py, mirrored in `params` and compared by the golden test (test/explain.test.js against
body_engine/tests/test_js_golden_explain.py).
*/
OBS.explain = (function (U, CO, TD) {
  "use strict";
  var EXPLAIN_VERSION = "explain-1";   // explain/personal.EXPLAIN_VERSION
  var P = {
    BASELINE_DAYS: 60, MIN_BASELINE_NIGHTS: 14, ILLNESS_RHR_DELTA: 5.0,   // scores/params.py (openbodyscore)
    RECOVERY_PART: { hrv: ["HRV", "HRV"], rhr: ["静息心率", "Resting HR"], sleep_performance: ["睡眠表现", "Sleep performance"],
      resp_rate: ["呼吸频率", "Respiratory rate"] },
    SLEEP_PART: { duration: ["时长", "Duration"], stages: ["睡眠阶段", "Sleep stages"], consistency: ["规律性", "Consistency"],
      interruptions: ["中途醒来", "Interruptions"] },
    SLEEP_PART_TIP: {
      duration: ["最拉分的是时长：先保证 7 小时，固定起床时间、入睡往前挪。",
        "Duration pulls the score down most: secure 7 hours first; fix your wake time and move bedtime earlier."],
      stages: ["最拉分的是睡眠阶段（深睡 / REM 少）：睡前别喝酒、别吃太撑，晚睡也会丢掉前半夜的深睡。",
        "Sleep stages pull the score down most (little deep / REM sleep): no alcohol or heavy meals before bed; " +
        "a late bedtime also loses the deep sleep of the first half of the night."],
      consistency: ["最拉分的是规律性：入睡时间和前两周差太多，先固定起床时间。",
        "Consistency pulls the score down most: your bedtime differs too much from the last two weeks; fix your wake time first."],
      interruptions: ["最拉分的是中途醒来：睡前少喝水和酒，卧室凉一点、遮光。",
        "Interruptions pull the score down most: less water and alcohol before bed, a cooler, darker bedroom."]
    },
    VO2_LEVEL_EN: { excellent: "Excellent", good: "Good", average: "Average", fair: "Fair", poor: "Poor" },
    TONE: { good: "good", warn: "mid", bad: "bad", neutral: "plain", mid: "mid", plain: "plain" },
    LAB_SOURCE: { lab: ["化验所", "Lab"], home_device: ["家用仪器", "Home device"] },
    // explain/glossary_trends.TREND_METRICS: series key, status key, digits, unit (zh, en)
    TREND: { resp_rate: ["resp_rate", null, 1, "次/分", "/min"], spo2: ["spo2", null, 1, "%", "%"],
      wrist_temp: ["wrist_temp", null, 2, "°C", "°C"], illness: ["illness", "illness", 0, "", ""],
      recovery_index: ["recovery_index", "recovery_index", 0, "%", "%"], sleep_need: ["sleep_need_hours", null, 1, "小时", "h"],
      sleep_performance: ["sleep_performance", null, 0, "%", "%"], sleep_perf_whoop: ["sleep_perf_whoop", "sleep_perf_whoop", 0, "", ""],
      restorative: ["restorative_hours", null, 1, "小时", "h"], awakenings: ["awakenings", null, 1, "次", "times"],
      daylight: ["daylight", null, 0, "分钟", "min"], recovery_vs_strain: ["recovery", null, 0, "", ""],
      training_load: ["tsb", null, 0, "", ""], acwr: ["acwr", null, 2, "", ""],
      strain_combined: ["strain_combined", "strain_combined", 1, "", ""], aerobic_min: ["aerobic_min", null, 0, "分钟", "min"],
      muscle_load: ["muscle_load", null, 0, "kg", "kg"], steps: ["steps", null, 0, "步", "steps"],
      calories: ["total_kcal", null, 0, "kcal", "kcal"], hr_recovery: ["hr_recovery", "hr_recovery", 0, "bpm", "bpm"],
      stress: ["stress", null, 1, "", ""], resilience: ["resilience", "resilience", 0, "", ""], body_age: ["body_age", null, 1, "岁", "yrs"],
      behavior: [null, null, 0, "", ""], hrv_status: [null, "hrv_status", 0, "", ""], training_status: [null, "training_status", 0, "", ""],
      body_battery: [null, "body_battery", 0, "", ""], recovery_time: [null, "recovery_time", 0, "小时", "h"],
      chronotype: [null, "chronotype", 0, "", ""] },
    // records/lab_params.py
    LAB: { TREND_FLAT_FRACTION: 0.01, SPARK_POINTS: 12, HIGH_BAD_FRACTION: 0.20, SOURCE_LABEL: { lab: "化验", home_device: "家测" },
      GOOD_DIRECTION: { uric_acid: "down", alt: "down", ast: "down", ggt: "down", triglycerides: "down", total_cholesterol: "down",
        ldl: "down", non_hdl: "down", chol_hdl_ratio: "down", hdl: "up", glucose_fasting: "down", hba1c: "down",
        insulin_fasting: "down", creatinine: "down", egfr: "up", crp: "down", vitamin_d: "up", body_weight: "down", bmi: "down",
        blood_pressure_systolic: "down", blood_pressure_diastolic: "down" } }
  };
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var SCORE_KEYS = ["sleep_hours", "sleep_score", "sleep_debt", "sleep_consistency", "recovery"];
  var DAILY_METRIC = { hrv: "heart_rate_variability_sdnn", resting_hr: "resting_heart_rate" };

  function nn(v) { return v !== null && v !== undefined; }
  function g(o, k) { var v = o ? o[k] : undefined; return v === undefined ? null : v; }
  function L(zh, en) { return OBS.lang === "en" ? en : zh; }
  function Lp(pair) { return L(pair[0], pair[1]); }
  function fix(x, n) { return U.round(x, n).toFixed(n); }
  function signed(x, n) { var s = fix(x, n); return s[0] === "-" ? s : "+" + s; }
  /** Python str() of a float (always with a decimal point). */
  function pf(x) { return Number.isInteger(x) && Math.abs(x) < 1e16 ? x.toFixed(1) : String(x); }
  var fmtG = CO.fmtG;

  function md(d) { return L(d.slice(5, 7) + "-" + d.slice(8, 10), Number(d.slice(8, 10)) + " " + MON[Number(d.slice(5, 7)) - 1]); }
  function avg7() { return L("7 天均", "7-day avg"); }
  function avg7n() { return L("7 晚均", "7-night avg"); }
  function hours() { return L("小时", "h"); }
  /** personal._fmt: fixed digits, trailing zeros dropped. */
  function fmt(v, nd) {
    if (!nn(v)) return "—";
    var s = fix(v, nd || 0);
    return s.indexOf(".") >= 0 ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
  }
  function mean(xs) { xs = xs.filter(nn); return xs.length ? U.fmean(xs) : null; }
  function fact(label, value) { return { label: label, value: value }; }
  function adv(text, tone) { return { text: text, tone: tone || "plain" }; }
  function Out() { return { current: null, facts: [], advice: [], missing: null }; }
  /** current: {value, unit, basis, tone}; `isFloat` = the value is a float in Python (the prompt prints it with str()). */
  function current(value, unit, basis, tone, isFloat) {
    var c = { value: value, unit: unit, basis: basis, tone: tone };
    Object.defineProperty(c, "_float", { value: !!isFloat, enumerable: false });
    return c;
  }
  function minKey(o) {   // Python min(d, key=d.get): the first smallest
    var best = null;
    Object.keys(o).forEach(function (k) { if (best === null || o[k] < o[best]) best = k; });
    return best;
  }

  // ================================================================ calculators
  function vs_baseline(by, day) {
    var lo7 = U.addDays(day, -6), lob = U.addDays(day, -(6 + P.BASELINE_DAYS)), recent = [], base = [], past = [];
    Object.keys(by).forEach(function (d) {
      var v = by[d];
      if (lo7 <= d && d <= day) recent.push(v);
      if (lob <= d && d < lo7) base.push(v);
      if (d <= day) past.push([d, v]);
    });
    past.sort(U.cmp);
    var m = null, sd = null;
    if (base.length >= P.MIN_BASELINE_NIGHTS) { m = U.fmean(base); sd = U.stdev(base); }
    return [mean(recent), m, sd, past.length ? past[past.length - 1] : null];
  }

  function hrv_compute(vals, day) {
    var o = Out(), b = vs_baseline(vals, day), cur = b[0], m = b[1], sd = b[2], last = b[3];
    if (cur === null) {
      o.missing = "近 7 天没有 HRV 数据";
      o.advice.push(adv(L("睡觉时戴着手表，HRV 主要在睡眠中测。", "Wear the watch to bed: HRV is measured mainly during sleep.")));
      return o;
    }
    o.facts.push(fact(avg7(), fmt(cur) + " ms"));
    if (last) o.facts.push(fact(L("最近 " + md(last[0]), "Latest " + md(last[0])), fmt(last[1]) + " ms"));
    if (m === null) {
      o.current = current(U.round(cur), "ms", avg7(), "plain");
      o.advice.push(adv(L("基线还不够（要 14 天以上的数据），先看趋势，过两周再和自己比。",
        "Not enough data for a baseline yet (needs 14+ days): watch the trend for now and compare with yourself in two weeks.")));
      return o;
    }
    o.facts.push(fact(L("你的基线", "Your baseline"), fmt(m) + " ± " + fmt(sd) + " ms"));
    var z = (cur - m) / Math.max(sd, 1e-6), pct = (cur / m - 1) * 100, tone, text;
    if (z >= 0.5) {
      tone = "good"; text = L("比你的基线高 " + fix(pct, 0) + "%：恢复状态好，可以按计划上强度。",
        fix(pct, 0) + "% above your baseline: you are recovering well and can train hard as planned.");
    } else if (z > -0.5) {
      tone = "plain"; text = L("在你的基线范围内：保持现在的作息和训练节奏。", "Within your baseline range: keep your current sleep and training rhythm.");
    } else if (z > -1) {
      tone = "mid"; text = L("比基线低 " + fix(-pct, 0) + "%：留意睡眠、饮酒和训练量，今天别冲极限。",
        fix(-pct, 0) + "% below baseline: watch sleep, alcohol and training volume; don't push to your limit today.");
    } else {
      tone = "bad"; text = L("明显低于基线（低 " + fix(-pct, 0) + "%）：今天降强度或休息，早点睡、多喝水；静息心率也升高的话，可能要生病。",
        "Clearly below baseline (" + fix(-pct, 0) + "% lower): train easier or rest today, sleep early and drink more water; " +
        "if your resting HR is up too, you may be getting sick.");
    }
    o.current = current(U.round(cur), "ms", avg7(), tone);
    o.advice.push(adv(text, tone));
    return o;
  }

  function resting_hr_compute(vals, day) {
    var o = Out(), b = vs_baseline(vals, day), cur = b[0], m = b[1], sd = b[2], last = b[3];
    if (cur === null) { o.missing = "近 7 天没有静息心率数据"; return o; }
    o.facts.push(fact(avg7(), fmt(cur) + " bpm"));
    if (last) o.facts.push(fact(L("最近 " + md(last[0]), "Latest " + md(last[0])), fmt(last[1]) + " bpm"));
    if (m === null) {
      o.current = current(U.round(cur), "bpm", avg7(), "plain");
      o.advice.push(adv(L("基线还不够（要 14 天以上的数据），先看趋势。", "Not enough data for a baseline yet (needs 14+ days): watch the trend for now.")));
      return o;
    }
    o.facts.push(fact(L("你的基线", "Your baseline"), fmt(m) + " ± " + fmt(sd) + " bpm"));
    var delta = cur - m, tone, text;
    if (delta >= P.ILLNESS_RHR_DELTA) {
      tone = "bad"; text = L("比基线高 " + fix(delta, 0) + " bpm：可能是疲劳、脱水、喝酒或生病早期，今天降强度、多喝水、早点睡。",
        fix(delta, 0) + " bpm above baseline: possibly fatigue, dehydration, alcohol or early illness; " +
        "train easier today, drink more water and sleep early.");
    } else if (delta >= 2) {
      tone = "mid"; text = L("比基线高 " + fix(delta, 0) + " bpm：留意睡眠和补水，训练量别再加。",
        fix(delta, 0) + " bpm above baseline: watch sleep and hydration, and don't add training volume.");
    } else if (delta > -2) {
      tone = "plain"; text = L("和你的基线持平。", "In line with your baseline.");
    } else {
      tone = "good"; text = L("比基线低 " + fix(-delta, 0) + " bpm：心肺状态好。", fix(-delta, 0) + " bpm below baseline: your cardiovascular system is in good shape.");
    }
    o.current = current(U.round(cur), "bpm", avg7(), tone);
    o.advice.push(adv(text, tone));
    return o;
  }

  function lastWith(rows, k) { for (var i = rows.length - 1; i >= 0; i--) if (nn(rows[i][k])) return rows[i]; return null; }

  function sleep_hours_compute(rows) {
    var o = Out(), mins = rows.map(function (r) { return g(r, "sleep_actual_min"); }).filter(nn);
    if (!mins.length) { o.missing = "近 7 晚没有睡眠数据"; return o; }
    var h = U.fmean(mins) / 60, last = lastWith(rows, "sleep_actual_min"), nr = lastWith(rows, "sleep_need_min");
    var need = nr ? nr.sleep_need_min : null, tone, text;
    o.facts.push(fact(avg7n(), fmt(h, 1) + " " + hours()), fact(md(last.date), fmt(last.sleep_actual_min / 60, 1) + " " + hours()));
    if (need) o.facts.push(fact(L("今晚需要", "Need tonight"), fmt(need / 60, 1) + " " + hours()));
    if (h < 6) {
      tone = "bad"; text = L("近 7 晚平均 " + fmt(h, 1) + " 小时，明显不够：先固定起床时间，入睡每周往前挪 15–30 分钟，直到 7 小时以上。",
        fmt(h, 1) + " h on average over the last 7 nights, clearly not enough: fix your wake time first, " +
        "then move bedtime 15–30 minutes earlier each week until you reach 7+ hours.");
    } else if (h < 7) {
      tone = "mid"; text = L("近 7 晚平均 " + fmt(h, 1) + " 小时，略少：每晚再多睡 " + fmt((7 - h) * 60) + " 分钟左右就到 7 小时。",
        fmt(h, 1) + " h on average over the last 7 nights, a bit short: about " + fmt((7 - h) * 60) + " more " +
        "minutes a night gets you to 7 hours.");
    } else {
      tone = "good"; text = L("近 7 晚平均 " + fmt(h, 1) + " 小时，够了，保持。", fmt(h, 1) + " h on average over the last 7 nights: enough, keep it up.");
    }
    o.current = current(U.round(h, 1), hours(), avg7n(), tone, true);
    o.advice.push(adv(text, tone));
    if (need && need / 60 - h >= 0.25) {
      o.advice.push(adv(L("按你昨天的负荷和睡眠债，今晚最好睡 " + fmt(need / 60, 1) + " 小时。",
        "Given yesterday's strain and your sleep debt, aim for " + fmt(need / 60, 1) + " h tonight."), "mid"));
    }
    return o;
  }

  function sleep_score_compute(all) {
    var o = Out(), rows = all.filter(function (r) { return nn(g(r, "sleep_score")); });
    if (!rows.length) { o.missing = "近 7 晚没有睡眠分"; return o; }
    var last = rows[rows.length - 1], avg = U.fmean(rows.map(function (r) { return r.sleep_score; }));
    var tone = last.sleep_score >= 80 ? "good" : last.sleep_score >= 60 ? "mid" : "bad";
    o.current = current(last.sleep_score, "", md(last.date), tone);
    o.facts.push(fact(avg7n(), fmt(avg)));
    var sl = (g(last, "details") || {}).sleep || {}, parts = {};
    Object.keys(sl).forEach(function (k) { if (k in P.SLEEP_PART && nn(sl[k])) parts[k] = sl[k]; });
    Object.keys(parts).forEach(function (k) { o.facts.push(fact(Lp(P.SLEEP_PART[k]), fmt(parts[k]))); });
    if (Object.keys(parts).length) {
      var worst = minKey(parts);
      if (parts[worst] < 80) o.advice.push(adv(Lp(P.SLEEP_PART_TIP[worst]), parts[worst] >= 50 ? "mid" : "bad"));
    }
    if (!o.advice.length) o.advice.push(adv(L("各项都不错，保持现在的作息。", "Every part looks good: keep your current schedule."), "good"));
    return o;
  }

  function sleep_debt_compute(all) {
    var o = Out(), rows = all.filter(function (r) { return nn(g(r, "sleep_debt_min")); });
    if (!rows.length) { o.missing = "还没有睡眠债数据"; return o; }
    var debt = rows[rows.length - 1].sleep_debt_min / 60, tone = debt <= 1 ? "good" : debt <= 2 ? "mid" : "bad";
    o.current = current(U.round(debt, 1), hours(), md(rows[rows.length - 1].date), tone, true);
    o.advice.push(adv({
      good: L("睡眠债很少，保持。", "Very little sleep debt: keep it up."),
      mid: L("欠了 " + fmt(debt, 1) + " 小时：接下来几晚每晚提前 30 分钟上床就能慢慢还上。",
        fmt(debt, 1) + " h of sleep debt: going to bed 30 minutes earlier for the next few nights pays it back gradually."),
      bad: L("欠了 " + fmt(debt, 1) + " 小时：这几天把训练强度降一点，优先睡觉，每晚提前 30–60 分钟上床。",
        fmt(debt, 1) + " h of sleep debt: ease off training intensity for a few days, put sleep first and " +
        "go to bed 30–60 minutes earlier each night.")
    }[tone], tone));
    return o;
  }

  function sleep_consistency_compute(rows) {
    var o = Out(), cur = mean(rows.map(function (r) { return g((g(r, "details") || {}).sleep || {}, "consistency"); }));
    if (cur === null) { o.missing = "近 7 晚没有规律性数据"; return o; }
    var tone = cur >= 80 ? "good" : cur >= 60 ? "mid" : "bad";
    o.current = current(U.round(cur), "", avg7n(), tone);
    o.advice.push(adv({
      good: L("作息很规律，保持。", "Very regular schedule: keep it up."),
      mid: L("入睡时间有些摇摆：先把起床时间固定在同一个点（差不超过 30 分钟）。",
        "Your bedtime wobbles a bit: first fix your wake time (within 30 minutes every day)."),
      bad: L("作息很不规律：每天同一时间起床最重要，入睡时间会慢慢跟上。",
        "Very irregular schedule: getting up at the same time every day matters most; bedtime will follow.")
    }[tone], tone));
    return o;
  }

  function sleep_efficiency_compute(rows) {
    var o = Out(), effs = rows.filter(function (r) { return g(r, "in_bed") && g(r, "asleep_min"); })
      .map(function (r) { return Math.min(100.0, 100 * r.asleep_min / r.in_bed); });
    if (!effs.length) { o.missing = "近 7 晚没有睡眠记录"; return o; }
    var cur = U.round(U.fmean(effs)), tone = cur >= 85 ? "good" : cur >= 75 ? "mid" : "bad";
    o.current = current(U.round(cur), "%", avg7n(), tone);
    o.advice.push(adv({
      good: L("躺下基本就睡着，保持。", "You fall asleep soon after lying down: keep it up."),
      mid: L("躺床上醒着的时间偏多：困了再上床，睡不着 20 分钟就起来做点安静的事。",
        "Quite a lot of time awake in bed: go to bed when sleepy; if you can't sleep for 20 minutes, get up and do something quiet."),
      bad: L("躺床上醒着的时间很多：别在床上刷手机，困了再上床；长期这样可以了解一下 CBT-I（失眠的认知行为治疗）。",
        "A lot of time awake in bed: don't scroll in bed and go to bed only when sleepy; if this lasts, look into " +
        "CBT-I (cognitive behavioural therapy for insomnia).")
    }[tone], tone));
    return o;
  }

  // assistant/status.py helpers of calc_bedtime
  function clock(h) {
    var m = ((U.round(h * 60) % 1440) + 1440) % 1440, hh = Math.floor(m / 60), mm = U.pad(m % 60, 2);
    if (hh < 5) return "凌晨 " + hh + ":" + mm;
    if (hh < 12) return "早上 " + hh + ":" + mm;
    if (hh < 18) return "下午 " + hh + ":" + mm;
    return hh + ":" + mm;
  }
  function trend3(cur, prev, eps) {
    if (!nn(cur) || !nn(prev)) return null;
    return Math.abs(cur - prev) < eps ? "flat" : (cur > prev ? "up" : "down");
  }
  function bedtime_items(mains, tz, today) {
    if (!mains.length) return [];
    var z = U.tzOf(tz), by = {};
    mains.forEach(function (r) {
      var p = U.local(U.toUs(r.start_at), z), h = p.hour + p.minute / 60;
      by[r.wake_date] = h >= 18 ? h - 24 : h;
    });
    var vals = [];
    for (var i = 0; i < 14; i++) { var d = U.addDays(today, -(13 - i)); vals.push(d in by ? by[d] : null); }
    var cur = mean(vals.slice(7)), prev = mean(vals.slice(0, 7));
    if (cur === null) return [];
    var tone = cur <= 0.5 ? "good" : cur <= 2 ? "warn" : "bad";
    return [[{ key: "bedtime", label: "入睡时间", value: clock(cur), unit: null, tone: tone, trend: trend3(cur, prev, 1 / 3),
      good_direction: "down", group: "作息", spark: vals.map(function (v) { return nn(v) ? U.round(v, 2) : null; }),
      note: "近 7 晚平均" }, 20]];
  }
  function bedtime_compute(items) {
    var o = Out();
    if (!items.length) { o.missing = "近 2 周没有睡眠记录"; return o; }
    var item = items[0][0], tone = P.TONE[item.tone];
    o.current = current(item.value, "", avg7n(), tone);
    o.advice.push(adv({
      good: L("入睡时间合适，保持。", "Good bedtime: keep it up."),
      mid: L("平均 " + item.value + " 才睡：每周往前挪 15–30 分钟，目标 24 点前。",
        "Asleep at " + item.value + " on average: move it 15–30 minutes earlier each week, aiming for before midnight."),
      bad: L("平均 " + item.value + " 才睡，生物钟往后推了很多：先固定起床时间、起床就晒太阳，入睡每周只提前 15–30 分钟，别一次提前太多。",
        "Asleep at " + item.value + " on average, your body clock has shifted a lot later: fix your wake time first and get " +
        "sunlight right after waking; move bedtime only 15–30 minutes earlier per week, not too much at once.")
    }[tone], tone));
    return o;
  }

  var ZONE_TONE = { green: "good", yellow: "mid", red: "bad" };
  function recovery_compute(rows, day) {
    var o = Out(), today = null;
    rows.forEach(function (r) { if (today === null && r.date === day) today = r; });
    var rec = rows.map(function (r) { return g(r, "recovery"); }).filter(nn);
    if (rec.length) o.facts.push(fact(avg7(), fmt(U.fmean(rec))));
    if (!today || !nn(g(today, "recovery"))) {
      o.missing = "这天还没有恢复分";
      if (rec.length) {
        var last = lastWith(rows, "recovery");
        o.facts.push(fact(L("上次 " + md(last.date), "Last " + md(last.date)), String(last.recovery)));
      }
      return o;
    }
    var zone = TD.recovery_zone(today.recovery), tone = ZONE_TONE[zone];
    o.current = current(today.recovery, "", md(day), tone);
    o.advice.push(adv({
      green: L("今天适合大重量或高强度，按计划练满。", "A good day for heavy weights or high intensity: do your full plan."),
      yellow: L("按计划训练，控制强度（每组留 2 次余力），不冲新纪录。", "Train as planned but control intensity (2 reps in reserve per set); no PR attempts."),
      red: L("今天以恢复为主：轻松有氧、拉伸或休息，早点睡、多喝水。", "Focus on recovery today: easy cardio, stretching or rest; sleep early and drink more water.")
    }[zone], tone));
    var z = ((g(today, "details") || {}).recovery || {}).z || {}, neg = {};
    Object.keys(z).forEach(function (k) { if (k in P.RECOVERY_PART && nn(z[k]) && z[k] < -0.3) neg[k] = z[k]; });
    if (Object.keys(neg).length) {
      var worst = minKey(neg);
      o.advice.push(adv(L("拉低最多的是" + P.RECOVERY_PART[worst][0] + "（比你的基线差）。",
        P.RECOVERY_PART[worst][1] + " pulls it down most (worse than your baseline)."), "mid"));
    }
    Object.keys(z).forEach(function (k) {
      if (k in P.RECOVERY_PART && nn(z[k])) {
        o.facts.push(fact(L(P.RECOVERY_PART[k][0] + " 对比基线", P.RECOVERY_PART[k][1] + " vs baseline"),
          z[k] >= 0.3 ? L("高", "Higher") : z[k] <= -0.3 ? L("低", "Lower") : L("持平", "Same")));
      }
    });
    return o;
  }

  function strain_compute(score_rows, day) {
    var o = Out(), rows = {};
    score_rows.forEach(function (r) { rows[r.date] = r; });
    var y = rows[U.addDays(day, -1)] || {};
    var vals = Object.keys(rows).filter(function (d) { return d < day && nn(g(rows[d], "strain")); }).map(function (d) { return rows[d].strain; });
    if (!nn(g(y, "strain")) && !vals.length) { o.missing = "近 7 天没有负荷数据"; return o; }
    if (nn(g(y, "strain"))) {
      var s = y.strain, tone = s < 10 ? "plain" : s < 14 ? "good" : s < 18 ? "mid" : "bad";
      o.current = current(U.round(s, 1), "", L("前一天", "Previous day"), tone, true);
    }
    if (vals.length) o.facts.push(fact(avg7(), fmt(U.fmean(vals), 1)));
    var rec = g(rows[day] || {}, "recovery"), zone = TD.recovery_zone(rec);
    if (zone) {
      o.facts.push(fact(L("今天恢复", "Recovery today"), String(rec)));
      o.advice.push(adv({
        green: L("今天恢复绿，可以安排高负荷（14–17）。", "Recovery is green today: you can plan high strain (14–17)."),
        yellow: L("今天恢复黄，负荷控制在 10–14。", "Recovery is yellow today: keep strain at 10–14."),
        red: L("今天恢复红，负荷控制在 10 以下，以轻松活动为主。", "Recovery is red today: keep strain under 10, mostly light activity.")
      }[zone], ZONE_TONE[zone]));
    }
    if (nn(g(y, "tsb")) && y.tsb < -30) {
      o.advice.push(adv(L("最近累积疲劳很高（TSB 很低），安排一两天低负荷日。", "Accumulated fatigue is very high (very low TSB): plan one or two low-strain days."), "bad"));
    }
    return o;
  }

  function vo2max_compute(v) {
    var o = Out();
    if (!nn(v.current)) {
      o.missing = "近一年没有 VO₂max 读数";
      o.advice.push(adv(L("户外步行、跑步或徒步时开体能训练记录，手表才会估算。",
        "Start a workout on the watch for outdoor walks, runs or hikes, so it can estimate VO₂max.")));
      return o;
    }
    var tone = { excellent: "good", good: "good", average: "plain", fair: "mid", poor: "bad" }[v.level] || "plain";
    o.current = current(v.current, "mL/kg·min", L("近 90 天均", "90-day avg"), tone, true);
    if (v.level_zh) {
      var lvl = L(v.level_zh, P.VO2_LEVEL_EN[v.level] || v.level_zh);
      o.facts.push(fact(L("同龄同性别", "Same age and sex"), nn(v.percentile)
        ? L(lvl + " · 第 " + fix(v.percentile, 0) + " 百分位", lvl + " · " + fix(v.percentile, 0) + "th percentile") : lvl));
    }
    if (nn(v.norm_median)) o.facts.push(fact(L("同龄中位数", "Median for your age"), fmt(v.norm_median, 1)));
    if (nn(v.trend_per_year)) o.facts.push(fact(L("每年变化", "Change per year"), signed(v.trend_per_year, 1)));
    if (v.missing_reason) o.advice.push(adv(L("在「我 → 个人资料」里填性别和生日，才能和同龄人比。", "Add your sex and birthday under Me → Profile to compare with people your age.")));
    if (v.level === "excellent" || v.level === "good") {
      o.advice.push(adv(L("心肺能力不错：每周 2–3 次有氧保持住。", "Good cardiorespiratory fitness: keep it with 2–3 cardio sessions a week."), "good"));
    } else {
      o.advice.push(adv(L("提升空间大：每周 2–3 次 Zone 2 有氧（如爬坡快走）30–60 分钟，加 1 次间歇，几个月能看到上升。",
        "Plenty of room to improve: 2–3 Zone 2 cardio sessions a week (e.g. incline walking) of 30–60 minutes, " +
        "plus one interval session; you should see it rise within a few months."), "mid"));
    }
    if (nn(v.trend_per_year) && v.trend_per_year < -1) {
      o.advice.push(adv(L("过去一年在下降，有氧训练要加回来。", "It has been falling over the past year: bring cardio back in."), "mid"));
    }
    return o;
  }

  function weight_compute(w, goal, day) {
    var o = Out(), days = Object.keys(w);
    if (!days.length) { o.missing = "近 3 周没有体重记录"; return o; }
    var last_day = days.slice().sort()[days.length - 1], lo7 = U.addDays(day, -7), lo14 = U.addDays(day, -14);
    var cur7 = mean(days.filter(function (d) { return d > lo7; }).map(function (d) { return w[d]; }));
    var prev7 = mean(days.filter(function (d) { return lo14 < d && d <= lo7; }).map(function (d) { return w[d]; }));
    o.current = current(U.round(w[last_day], 1), "kg", md(last_day), "plain", true);
    if (cur7 !== null) o.facts.push(fact(avg7(), fmt(cur7, 1) + " kg"));
    if (nn(goal)) o.facts.push(fact(L("目标", "Goal"), fmt(goal, 1) + " kg"));
    if (cur7 === null || prev7 === null) {
      o.advice.push(adv(L("每周至少称 3–4 次，才能算出周均变化。", "Weigh in at least 3–4 times a week so the weekly change can be computed.")));
      return o;
    }
    var change = cur7 - prev7;
    o.facts.push(fact(L("周均变化", "Weekly change"), signed(change, 1) + " kg"));
    var losing = nn(goal) && w[last_day] > goal;
    if (!losing) {
      o.advice.push(adv(L("看周均变化，单日波动 1–2 kg 很正常。", "Watch the weekly change; day-to-day swings of 1–2 kg are normal.")));
    } else if (change < -1.0) {
      o.current.tone = "mid";
      o.advice.push(adv(L("掉得偏快：适当加点碳水，避免疲劳、早醒和掉肌肉。", "Losing a bit fast: add some carbs to avoid fatigue, early waking and muscle loss."), "mid"));
    } else if (change <= -0.3) {
      o.current.tone = "good";
      o.advice.push(adv(L("速度合适，保持现在的热量和训练。", "Good pace: keep your current calories and training."), "good"));
    } else {
      o.advice.push(adv(L("周均没怎么降：每天少 200–300 kcal，或每天多走 2000–3000 步。",
        "The weekly average has barely dropped: eat 200–300 kcal less a day, or walk 2,000–3,000 more steps a day."), "mid"));
    }
    return o;
  }

  function status_text(key, st) {
    if (key === "illness") return [st.label + " · " + st.score, { low: "good", watch: "mid", high: "bad" }[st.level]];
    if (key === "body_battery") return [String(st.current), st.current >= 60 ? "good" : st.current >= 30 ? "mid" : "bad"];
    if (key === "recovery_time") return [st.hours + " " + hours(), !st.hours ? "good" : st.hours <= 24 ? "plain" : "mid"];
    if (key === "hr_recovery") return [fmt(st.bpm) + " bpm", { good: "good", fair: "plain", low: "bad" }[st.level]];
    if (key === "strain_combined" || key === "sleep_perf_whoop") return [fmt(st.value, 1), "plain"];
    var label = nn(g(st, "label")) ? st.label : "—";
    var tone = { balanced: "good", productive: "good", exceptional: "good", strong: "good", good: "good", low: "mid", strained: "mid",
      adequate: "mid", fair: "mid", poor: "bad", overreaching: "bad", limited: "bad", late: "bad" }[g(st, "status") || g(st, "level")] || "plain";
    return [label, tone];
  }
  function trend_compute(key, d, day) {
    var o = Out(), e = P.TREND[key], st = e[1] ? g(d.status, e[1]) : null, s = e[0] ? g(d.series, e[0]) : null;
    var nd = e[2], unit = L(e[3], e[4]), strip = function (x) { return x.replace(/^\s+|\s+$/g, ""); };
    if (st) {
      var vt = status_text(key, st);
      o.current = current(vt[0], "", md(day), vt[1]);
    } else if (s && s.n) {
      var last = null;
      for (var i = s.values.length - 1; i >= 0; i--) if (nn(s.values[i])) { last = s.values[i]; break; }
      o.current = current(nd ? U.round(last, nd) : U.round(last), unit, L("最近", "Latest"), "plain", !!nd);
    }
    if (s && s.n) {
      o.facts.push(fact(L("30 天均", "30-day avg"), strip(fmt(s.mean, nd) + " " + unit)));
      if (nn(g(s, "prev_mean"))) o.facts.push(fact(L("前 30 天", "Previous 30 days"), strip(fmt(s.prev_mean, nd) + " " + unit)));
    }
    if (o.current === null && !o.facts.length) o.missing = "还没有数据";
    return o;
  }

  function lab_compute(rep, key, day) {
    var o = Out();
    if (!rep || !nn(rep.latest.value)) { o.missing = "还没有化验记录"; return o; }
    var last = rep.latest, unit = last.unit || "", ab = g(rep, "above_upper_pct") || 0;
    var tone = { normal: "good", high: ab > 20 ? "bad" : "mid", low: "mid" }[rep.status] || "plain";
    o.current = current(last.value, unit, last.collected_on, tone, true);
    var how = [Lp(P.LAB_SOURCE[g(last, "source")] || ["化验", "Lab test"]),
      g(last, "fasting") === true ? L("空腹", "Fasting") : g(last, "fasting") === false ? L("饭后", "After a meal") : null];
    o.facts.push(fact(L("来源", "Source"), how.filter(function (x) { return x; }).join(" · ")));
    var lo = g(last, "ref_low"), hi = g(last, "ref_high");
    if (nn(lo) || nn(hi)) {
      var rng = nn(lo) && nn(hi) ? fmt(lo, 2) + "–" + fmt(hi, 2) : nn(hi) ? "≤ " + fmt(hi, 2) : "≥ " + fmt(lo, 2);
      o.facts.push(fact(L("参考范围", "Reference range"), (rng + " " + unit).replace(/\s+$/, "")));
    }
    var hist = rep.history.filter(function (h) { return nn(h.value); });
    if (hist.length >= 2) {
      var prev = hist[hist.length - 2];
      o.facts.push(fact(L("上次 " + prev.collected_on, "Previous " + prev.collected_on), (fmt(prev.value, 2) + " " + unit).replace(/\s+$/, "")));
    }
    if (rep.status === "high") {
      var pct = g(rep, "above_upper_pct"), p = nn(pct) ? " " + fix(pct, 0) + "%" : "";
      o.advice.push(adv(L("最近一次高于上限" + p + "：按下面的建议调整饮食和生活方式，按时复查。",
        "The latest result is" + p + " above the upper limit: adjust diet and lifestyle as suggested below and get retested on time."), tone));
    } else if (rep.status === "low") {
      o.advice.push(adv(L("最近一次低于参考范围，复查时问医生。", "The latest result is below the reference range: ask your doctor at the follow-up."), "mid"));
    } else {
      o.advice.push(adv(L("在参考范围内，保持。", "Within the reference range: keep it up."), "good"));
    }
    if (g(last, "source") === "home_device") {
      o.advice.push(adv(L("最近一次是家用仪器测的，误差比化验所大，看趋势时和同样条件（空腹 / 饭后）的家测比，下结论以化验所的结果为准。",
        "The latest reading is from a home device, which is less accurate than a lab: for trends, compare " +
        "with home readings taken under the same conditions (fasting / after a meal), and rely on lab results " +
        "for conclusions."), "plain"));
      if (key === "uric_acid" && g(last, "fasting") === false) {
        o.advice.push(adv(L("这次是饭后测的，尿酸饭后会偏高一些，下次尽量早上空腹测，才好和以前比。",
          "This reading was taken after a meal, when uric acid reads a little higher; next time test fasting " +
          "in the morning so it compares with earlier readings."), "plain"));
      }
    }
    var weeks = Math.floor(U.daysBetween(last.collected_on, day) / 7);
    if (rep.status !== "normal" && weeks >= 12) {
      o.advice.push(adv(L("已经 " + weeks + " 周没复查了，约 GP 复查。", "No retest for " + weeks + " weeks: book a follow-up with your GP."), "mid"));
    }
    return o;
  }

  // ================================================================ dispatch (metric_out, lab_out)
  function metric_out(key, data, day) {
    if (key in DAILY_METRIC) return (key === "hrv" ? hrv_compute : resting_hr_compute)(data.values || {}, day);
    if (SCORE_KEYS.indexOf(key) >= 0) {
      var rows = data.scores || [];
      if (key === "recovery") return recovery_compute(rows, day);
      return { sleep_hours: sleep_hours_compute, sleep_score: sleep_score_compute, sleep_debt: sleep_debt_compute,
        sleep_consistency: sleep_consistency_compute }[key](rows);
    }
    if (key === "strain") return strain_compute(data.scores || [], day);
    if (key === "sleep_efficiency") return sleep_efficiency_compute(data.nights || []);
    if (key === "bedtime") return bedtime_compute(bedtime_items(data.mains || [], data.tz, day));
    if (key === "vo2max") return vo2max_compute(data.vo2max);
    if (key === "weight") return weight_compute(data.weights || {}, g(data, "goal"), day);
    if (key in P.TREND) return trend_compute(key, data.series, day);
    throw new Error("no explanation for " + key);
  }
  function lab_out(data, key, day) { return lab_compute(g(data, "report"), key, day); }

  // ================================================================ the sheet (metric_view, lab_view)
  function entry_view(kind, key, e) {
    return { kind: kind, key: key, title: e.title, what: e.what,
      read: (e.read || []).map(function (r) { return { range: r[0], label: r[1], tone: r[2] }; }),
      recovery: e.recovery || null, tips: (e.tips || []).slice(), avoid: (e.avoid || []).slice(),
      current: null, facts: [], advice: [], profile: [], exercises: null, missing: null, hidden: [], source: "rules" };
  }
  function apply(view, o) {
    view.current = view.current || o.current;
    view.facts = view.facts.concat(o.facts);
    view.advice = view.advice.concat(o.advice);
    view.missing = view.missing || (o.current === null ? o.missing : null);
  }
  function prompt(title, view, subject) {
    var cur = view.current, bits = [];
    if (cur) {
      var v = cur._float ? pf(cur.value) : String(cur.value);
      bits.push(cur.basis + " " + v + (cur.unit ? " " + cur.unit : ""));
    }
    view.facts.slice(0, 4).forEach(function (f) { bits.push(f.label + " " + f.value); });
    if (OBS.lang === "en") {
      return "Please explain " + (subject || "my \"" + title + "\"") + (bits.length ? " (" + bits.join("; ") + ")" : "") +
        ": what does it mean, how am I doing right now, what exactly should I do next, and what should I avoid?";
    }
    return "帮我解读一下" + (subject || "我的「" + title + "」") + (bits.length ? "（" + bits.join("；") + "）" : "") +
      "：这是什么意思、我现在的情况怎么样、接下来具体该怎么做、要避免什么？";
  }
  function metric_view(key, e, o) {
    var view = entry_view("metric", key, e);
    apply(view, o);
    view.ai_prompt = prompt(e.title, view);
    return view;
  }
  function lab_view(key, e, o, profile) {
    var view = entry_view("lab", key, e);
    apply(view, o);
    if (nn(profile)) view.profile = profile;
    view.ai_prompt = prompt(e.title, view);
    return view;
  }

  // ================================================================ records/lab_history.py
  var ROW_FIELDS = ["id", "collected_on", "value", "value_text", "unit", "ref_low", "ref_high", "flag", "source", "fasting", "lab", "note"];
  function compare_to_range(r) {
    var v = g(r, "value"), lo = g(r, "ref_low"), hi = g(r, "ref_high");
    if (nn(v) && (nn(lo) || nn(hi))) {
      if (nn(hi) && v > hi) return "high";
      if (nn(lo) && v < lo) return "low";
      return "normal";
    }
    return { H: "high", L: "low", abnormal: "abnormal", normal: "normal" }[g(r, "flag")] || "unknown";
  }
  function lab_trend(cur, prev) {
    if (!nn(cur) || !nn(prev)) return null;
    if (Math.abs(cur - prev) <= Math.abs(prev) * P.LAB.TREND_FLAT_FRACTION) return "flat";
    return cur > prev ? "up" : "down";
  }
  function delta(cur, prev) {
    if (prev === null || !nn(cur.value) || !nn(prev.value)) return { delta: null, pct: null };
    var d = cur.value - prev.value;
    return { delta: U.round(d, 4), pct: prev.value ? U.round(d / prev.value * 100, 1) : null };
  }
  function history_of(key, rows) {
    var out = [], prev = null;
    rows.forEach(function (r) {
      var item = {};
      ROW_FIELDS.forEach(function (k) { item[k] = g(r, k); });
      item.status = compare_to_range(r);
      var dl = delta(r, prev);
      item.delta = dl.delta; item.pct = dl.pct;
      if (nn(r.value)) prev = r;
      out.push(item);
    });
    var last = rows[rows.length - 1], ref = last;
    for (var i = rows.length - 1; i >= 0; i--) if (nn(g(rows[i], "ref_low")) || nn(g(rows[i], "ref_high"))) { ref = rows[i]; break; }
    var unit = null;
    for (var j = rows.length - 1; j >= 0; j--) if (rows[j].unit) { unit = rows[j].unit; break; }
    return { test_key: key, test_name: last.test_name, category: g(last, "category"), unit: unit, ref_low: g(ref, "ref_low"),
      ref_high: g(ref, "ref_high"), good_direction: P.LAB.GOOD_DIRECTION[key] || null, rows: out };
  }
  function card(h) {
    var nums = h.rows.filter(function (r) { return nn(r.value); });
    if (!nums.length) return null;
    var last = nums[nums.length - 1], status = compare_to_range(last);
    var word = { high: "偏高", low: "偏低", normal: "正常" }[status] || "";
    var over = status === "high" && last.ref_high ? (last.value - last.ref_high) / last.ref_high : 0;
    var tone = { normal: "good", high: over > P.LAB.HIGH_BAD_FRACTION ? "bad" : "warn", low: "warn" }[status] || "neutral";
    var limit = nn(last.ref_high) ? " · 上限 " + fmtG(last.ref_high) : "";
    return { value: (word + " " + fmtG(last.value)).replace(/^\s+|\s+$/g, ""), unit: last.unit, tone: tone,
      trend: nums.length >= 2 ? lab_trend(last.value, nums[nums.length - 2].value) : null, good_direction: h.good_direction,
      spark: nums.length >= 2 ? nums.slice(-P.LAB.SPARK_POINTS).map(function (r) { return r.value; }) : null,
      note: last.collected_on.slice(5, 7) + "-" + last.collected_on.slice(8, 10) + " · " +
        (P.LAB.SOURCE_LABEL[last.source] || last.source) + limit,
      lab_key: h.test_key, lab_date: last.collected_on, lab_source: last.source };
  }

  // ================================================================ entry points (stamped)
  function stamp(x) { x.algo_version = EXPLAIN_VERSION; x.bundle_version = OBS.VERSION; return x; }
  OBS.ALGO_VERSIONS.explain = EXPLAIN_VERSION;
  /** One metric's sheet: `entry` = its static texts (glossary, the server's cached sheet), `data` = metric_data's JSON. */
  OBS.explainMetric = function (key, entry, data, day) { return stamp(metric_view(key, entry, metric_out(key, data, day))); };
  /** One lab test's sheet (profile card lines are the server's: memory). */
  OBS.explainLab = function (key, entry, data, day, profile) { return stamp(lab_view(key, entry, lab_out(data, key, day), profile)); };
  /** A lab test's history and status card from its raw readings (lab_results rows, old -> new). */
  OBS.labHistory = function (key, rows) { var h = history_of(key, rows); return { history: h, card: card(h) }; };

  return { EXPLAIN_VERSION: EXPLAIN_VERSION, params: P, fmt: fmt, md: md, metric_out: metric_out, lab_out: lab_out,
    metric_view: metric_view, lab_view: lab_view, prompt: prompt, bedtime_items: bedtime_items,
    calc: { hrv: hrv_compute, resting_hr: resting_hr_compute, sleep_hours: sleep_hours_compute, sleep_score: sleep_score_compute,
      sleep_debt: sleep_debt_compute, sleep_consistency: sleep_consistency_compute, sleep_efficiency: sleep_efficiency_compute,
      bedtime: bedtime_compute, recovery: recovery_compute, strain: strain_compute, vo2max: vo2max_compute, weight: weight_compute,
      trend: trend_compute, lab: lab_compute },
    lab: { compare_to_range: compare_to_range, trend: lab_trend, history_of: history_of, card: card } };
})(OBS.util, OBS.coach, OBS.coach.today);
