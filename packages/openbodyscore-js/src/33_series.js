/* series (2.0.0 package M3, docs/ios-native-2.0.md): the Trends series and the views around them, computed on the phone.
Port of the pure parts of body_engine
- trends/series.py `compute` (every SERIES entry, prev_mean, baseline bands, recovery colours and contributors, strain target
  band, heart-rate zone summary, weekly aerobic minutes, the status cards as of `end`) — the JSON of GET /trends;
- web/views.py `month_days_compute` (GET /days?month=) and `sleep_nights_compute` (GET /sleep/nights);
- insights/sleep_detail.py `watch_sessions`, `sri_from`, `sri`, `consistency_pct`, `nights_from`, `details` (GET /sleep/details);
- insights/fitness.py `hr_zones_compute`, `vo2max_compute`, `level` (GET /hr-zones, GET /vo2max).
Inputs are what the Python `load` side reads (plain objects, local dates 'YYYY-MM-DD', instants as epoch milliseconds or ISO
strings with an offset); outputs are the endpoints' JSON (dates as 'YYYY-MM-DD'). Thresholds are the Python constants of those
modules (sources there). Labels follow OBS.setLanguage like the server's tl() / L().
*/
OBS.series = (function (U, T, HS, HSS) {
  "use strict";
  var SERIES_VERSION = "series-1";
  // (key, Chinese label, unit, scope, group): trends/series.py SERIES
  var SERIES = [
    ["recovery", "恢复分", "", "scores", "recovery"],
    ["hrv", "HRV（SDNN 日均）", "ms", "metrics", "recovery"],
    ["resting_hr", "静息心率（Apple）", "bpm", "metrics", "recovery"],
    ["resp_rate", "呼吸频率", "次/分", "scores", "recovery"],
    ["spo2", "血氧", "%", "scores", "recovery"],
    ["wrist_temp", "手腕温度偏差", "°C", "scores", "recovery"],
    ["illness", "生病风险", "", "scores", "recovery"],
    ["recovery_index", "最低心率出现", "%", "scores", "recovery"],
    ["sleep_hours", "实际睡眠", "h", "scores", "sleep"],
    ["sleep_need_hours", "睡眠需求", "h", "scores", "sleep"],
    ["sleep_debt_hours", "睡眠债", "h", "scores", "sleep"],
    ["sleep_performance", "睡眠表现", "%", "scores", "sleep"],
    ["sleep_perf_whoop", "睡眠表现（综合）", "", "sleep", "sleep"],
    ["restorative_hours", "恢复性睡眠", "h", "sleep", "sleep"],
    ["restorative_pct", "恢复性睡眠占比", "%", "sleep", "sleep"],
    ["sleep_efficiency", "睡眠效率", "%", "sleep", "sleep"],
    ["sleep_score", "睡眠分", "", "scores", "sleep"],
    ["sleep_consistency", "睡眠规律性", "", "sleep", "sleep"],
    ["awakenings", "夜间醒来", "次", "sleep", "sleep"],
    ["daylight", "日照时间", "分钟", "metrics", "sleep"],
    ["strain", "负荷 strain", "", "scores", "strain"],
    ["strain_combined", "负荷（含肌肉）", "", "scores", "strain"],
    ["ctl", "CTL 体能", "", "scores", "strain"],
    ["atl", "ATL 疲劳", "", "scores", "strain"],
    ["tsb", "TSB 状态", "", "scores", "strain"],
    ["acwr", "急慢性负荷比", "", "scores", "strain"],
    ["aerobic_min", "有氧分钟", "分钟", "scores", "strain"],
    ["muscle_load", "肌肉负荷", "kg", "scores", "strain"],
    ["steps", "步数", "步", "metrics", "strain"],
    ["active_kcal", "活动消耗", "kcal", "metrics", "strain"],
    ["total_kcal", "总消耗", "kcal", "metrics", "strain"],
    ["hr_recovery", "运动后心率恢复", "bpm", "training", "strain"],
    ["stress", "压力", "", "scores", "stress"],
    ["resilience", "韧性", "", "scores", "stress"],
    ["vo2max", "VO₂max", "mL/kg·min", "metrics", "long"],
    ["weight", "体重", "kg", "weight", "long"],
    ["body_age", "身体年龄", "岁", "scores", "long"]
  ];
  var SCOPE = {};
  SERIES.forEach(function (s) { SCOPE[s[0]] = s[3]; });
  var P = {
    SERIES_VERSION: SERIES_VERSION, SERIES: SERIES, SCOPE: SCOPE,
    BASELINE_DAYS: 60, BASELINE_MIN_N: 14, LOOKBACK: 90,
    METRICS: ["resting_heart_rate", "heart_rate_variability_sdnn", "vo2_max", "step_count", "active_energy_burned",
      "basal_energy_burned", "time_in_daylight", "oxygen_saturation", "respiratory_rate", "apple_sleeping_wrist_temperature"],
    // web/views.py: recovery colour bands (RECOVERY_GREEN; rules/params.RECOVERY_RED) and the month calendar's sources
    RECOVERY_GREEN: 67, RECOVERY_RED: 34, DAYS_FIELDS: ["sleep", "training", "metrics"],
    // insights/sleep_detail.py
    DAY_MIN: 1440, MIN_PAIRS: 4,
    // insights/fitness.py
    ZONES: ["Z1 50–60%", "Z2 60–70%", "Z3 70–80%", "Z4 80–90%", "Z5 ≥90%"],
    LEVELS: [[80, "excellent", "优秀"], [60, "good", "良好"], [40, "average", "中等"], [20, "fair", "一般"], [0, "poor", "较差"]],
    LEVELS_EN: { excellent: "Excellent", good: "Good", average: "Average", fair: "Fair", poor: "Poor" }
  };
  // trends/params.py EN: series labels and units (the status labels are in trends.EN)
  var EN = {
    "恢复分": "Recovery", "HRV（SDNN 日均）": "HRV (SDNN, daily mean)", "静息心率（Apple）": "Resting HR (Apple)",
    "呼吸频率": "Respiratory rate", "血氧": "Blood oxygen (SpO₂)", "手腕温度偏差": "Wrist temperature deviation",
    "生病风险": "Illness risk", "最低心率出现": "Lowest HR timing", "实际睡眠": "Sleep", "睡眠需求": "Sleep need",
    "睡眠债": "Sleep debt", "睡眠表现": "Sleep performance", "睡眠表现（综合）": "Sleep performance (overall)",
    "恢复性睡眠": "Restorative sleep", "恢复性睡眠占比": "Restorative sleep share", "睡眠效率": "Sleep efficiency",
    "睡眠分": "Sleep score", "睡眠规律性": "Sleep consistency", "夜间醒来": "Awakenings", "日照时间": "Time in daylight",
    "负荷 strain": "Strain", "负荷（含肌肉）": "Strain (with muscle load)", "CTL 体能": "Fitness (CTL)",
    "ATL 疲劳": "Fatigue (ATL)", "TSB 状态": "Form (TSB)", "急慢性负荷比": "Acute:chronic load ratio",
    "有氧分钟": "Aerobic minutes", "肌肉负荷": "Muscle load", "步数": "Steps", "活动消耗": "Active energy",
    "总消耗": "Total energy", "运动后心率恢复": "Heart rate recovery", "压力": "Stress", "韧性": "Resilience",
    "体重": "Weight", "身体年龄": "Body age",
    "次/分": "/min", "次": "times", "分钟": "min", "步": "steps", "岁": "yrs"
  };
  function tl(zh) { return OBS.lang === "en" && zh ? (EN[zh] || T.EN[zh] || zh) : zh; }
  function L(zh, en) { return OBS.lang === "en" ? en : zh; }

  function nn(v) { return v !== null && v !== undefined; }
  function g(o, k) { var v = o ? o[k] : undefined; return v === undefined ? null : v; }
  function obj(o) { return o === null || o === undefined ? {} : o; }
  function keys(o) { return Object.keys(o); }
  function us(x) { return U.toUs(x); }
  function hm(t, tz) { var p = U.local(us(t), tz); return U.pad(p.hour, 2) + ":" + U.pad(p.minute, 2); }
  function pyMin(xs) { var m = xs[0]; for (var i = 1; i < xs.length; i++) if (xs[i] < m) m = xs[i]; return m; }
  function pyMax(xs) { var m = xs[0]; for (var i = 1; i < xs.length; i++) if (xs[i] > m) m = xs[i]; return m; }

  // ---------------------------------------------------------------- trends/series.py
  /** [start, prev_start, lo]: the range, the previous period of the same length, the first day read (baselines). */
  function window_(end, days) {
    var start = U.addDays(end, -(days - 1)), prev_start = U.addDays(start, -days);
    return [start, prev_start, U.addDays(prev_start, -P.LOOKBACK)];
  }

  function col(rows, k, f) {
    var out = {};
    keys(rows).forEach(function (d) { var v = rows[d][k]; if (nn(v)) out[d] = f ? f(v) : v; });
    return out;
  }
  function metric_map(rows, metric) {
    var out = {};
    rows.forEach(function (r) { if (r.metric === metric && nn(r.value)) out[r.date] = r.value; });
    return out;
  }

  /**
   * The Trends document (GET /trends) from the data trends/series.load reads:
   * {can: {scores, metrics, sleep, weight, training}, scores: [daily_scores rows with flags and details, by date],
   *  stress: [{date, score, level, hours}], body_age: [{as_of, body_age}], metrics: [{date, metric, value}],
   *  sleep: [{wake_date, start_at, end_at, asleep_min, in_bed?}] (the longest main session per wake date),
   *  hrr_apple: {date: bpm}, hrr_workouts: {date: bpm}, weights: {date: kg} | weights_error: text}.
   * A missing block counts as no rows. now_hour: the local hour for today's body battery.
   */
  function compute(data, end, days, today, tz, now_hour) {
    tz = U.tzOf(tz);
    var w = window_(end, days), start = w[0], prev_start = w[1], lo = w[2];
    var dates = [];
    for (var k = 0; k < days; k++) dates.push(U.addDays(start, k));
    var can = data.can;
    var raw = {}, unavailable = {}, extra = {}, status = {}, bands = {}, zones = {}, rows = {}, trimp = {};

    if (can.scores) {
      (data.scores || []).forEach(function (r) { rows[r.date] = r; });
      var det = {}, sl = {}, rc = {};
      keys(rows).forEach(function (d) {
        det[d] = rows[d].details || {};
        sl[d] = det[d].sleep || {};
        rc[d] = det[d].recovery || {};
      });
      var r2 = function (v) { return U.round(v / 60, 2); };
      raw.recovery = col(rows, "recovery");
      raw.sleep_score = col(rows, "sleep_score");
      raw.sleep_hours = col(rows, "sleep_actual_min", r2);
      raw.sleep_need_hours = col(rows, "sleep_need_min", r2);
      raw.sleep_debt_hours = col(rows, "sleep_debt_min", r2);
      raw.sleep_performance = col(rows, "sleep_performance", function (v) { return U.round(100 * v, 1); });
      raw.resp_rate = col(rows, "resp_rate_night");
      raw.strain = col(rows, "strain");
      raw.muscle_load = col(rows, "muscle_load");
      ["ctl", "atl", "tsb"].forEach(function (c) { raw[c] = col(rows, c); });
      trimp = col(rows, "trimp");
      raw.sleep_consistency = {}; raw.awakenings = {}; raw.restorative_hours = {}; raw.restorative_pct = {};
      keys(sl).forEach(function (d) {
        var s = sl[d];
        if (nn(s.consistency)) raw.sleep_consistency[d] = s.consistency;
        if (nn(s.awakenings)) raw.awakenings[d] = s.awakenings;
        if (nn(s.deep_min) || nn(s.rem_min)) {
          var rest = (s.deep_min || 0) + (s.rem_min || 0);
          raw.restorative_hours[d] = U.round(rest / 60, 2);
          if (s.asleep_min) raw.restorative_pct[d] = U.round(100 * rest / s.asleep_min, 1);
        }
      });
      raw.sleep_perf_whoop = {};
      keys(rows).forEach(function (d) {
        var v = T.sleep_performance_whoop(rows[d].sleep_performance, g(sl[d], "consistency"), g(sl[d], "efficiency"));
        if (nn(v)) raw.sleep_perf_whoop[d] = v;
      });
      raw.spo2 = {};
      var temps = {};
      keys(rc).forEach(function (d) {
        if (nn(rc[d].spo2_night)) raw.spo2[d] = rc[d].spo2_night;
        if (nn(rc[d].wrist_temp)) temps[d] = rc[d].wrist_temp;
      });
      raw.wrist_temp = {};
      keys(temps).forEach(function (d) {
        var from = U.addDays(d, -P.BASELINE_DAYS);
        var past = keys(temps).filter(function (x) { return from <= x && x < d; }).map(function (x) { return temps[x]; });
        if (past.length >= 3) raw.wrist_temp[d] = U.round(temps[d] - U.fmean(past), 2);
      });
      var illness = {}, ri = {};
      raw.illness = {}; raw.recovery_index = {};
      keys(rows).forEach(function (d) {
        illness[d] = T.illness_risk(g(rc[d], "z"), g(raw.wrist_temp, d), g(raw.spo2, d));
        if (illness[d]) raw.illness[d] = illness[d].score;
        ri[d] = T.recovery_index(g(rc[d], "rhr_low_offset_min"), g(sl[d], "asleep_min"));
        if (ri[d]) raw.recovery_index[d] = ri[d].pct;
      });
      var ml = keys(raw.muscle_load).map(function (d) { return raw.muscle_load[d]; }).filter(function (v) { return v; });
      var med = ml.length ? U.median(ml) : null;
      raw.strain_combined = {};
      keys(rows).forEach(function (d) {
        var v = T.combined_strain(rows[d].trimp, rows[d].muscle_load, med);
        if (nn(v)) raw.strain_combined[d] = v;
      });
      keys(det).forEach(function (d) { zones[d] = g(det[d].load || {}, "zone_minutes"); });
      raw.aerobic_min = {};
      keys(zones).forEach(function (d) { var v = T.aerobic_minutes(zones[d]); if (nn(v)) raw.aerobic_min[d] = U.round(v); });
      raw.acwr = {};
      U.dateRange(lo, end).forEach(function (d) {
        if (!nn(trimp[d])) return;
        var v = T.acwr(trimp, d);
        if (!nn(v)) return;
        var n = 0;
        for (var j = 0; j < T.params.CHRONIC_DAYS; j++) if (nn(trimp[U.addDays(d, -j)])) n++;
        if (n >= T.params.MIN_TRIMP_DAYS) raw.acwr[d] = v;
      });
      var stress = {};
      (data.stress || []).forEach(function (r) { stress[r.date] = r; });
      raw.stress = {};
      keys(stress).forEach(function (d) { if (nn(stress[d].score)) raw.stress[d] = U.round(stress[d].score, 2); });
      var perf_raw = col(rows, "sleep_performance");
      raw.resilience = {};
      U.dateRange(prev_start, end).forEach(function (d) {
        var res = T.resilience(raw.recovery, perf_raw, raw.stress, d);
        if (res) raw.resilience[d] = U.round(100 * res.balance);
      });
      raw.body_age = {};
      (data.body_age || []).forEach(function (r) { raw.body_age[r.as_of] = U.round(r.body_age, 1); });

      // recovery colours, contributors, and the Strain Coach target band of each day
      var zone_of = {};
      keys(raw.recovery).forEach(function (d) { zone_of[d] = T.recovery_zone(raw.recovery[d]); });
      var cnt = { green: 0, yellow: 0, red: 0 };
      dates.forEach(function (d) { if (zone_of[d]) cnt[zone_of[d]] += 1; });
      extra.recovery_zones = cnt;
      extra.recovery_contributors = dates.map(function (d) { return g(rc[d], "z"); });
      var tgt = dates.map(function (d) {
        var r = rows[d], y = rows[U.addDays(d, -1)];
        if (!r) return null;
        var t = T.strain_target(r.recovery, y ? y.tsb : null, "illness_warning" in obj(r.flags));
        return [t.target_low, t.target_high];
      });
      var hit = 0, withStrain = 0;
      dates.forEach(function (d, i) {
        var s = raw.strain[d];
        if (nn(s)) withStrain++;
        if (tgt[i] && nn(s) && tgt[i][0] <= s && s <= tgt[i][1]) hit++;
      });
      extra.strain_target = { bands: tgt, in_range_days: hit, days_with_strain: withStrain };
      extra.hr_zones = T.hr_zone_summary(zones, dates);
      extra.aerobic_weeks = T.weekly_sum(raw.aerobic_min, dates).map(function (x) {
        return { week: x.week, value: x.value, target: T.params.AZM_WEEK_TARGET };
      });

      // status cards as of `end`
      var er = rows[end], es = stress[end];
      status.load_focus = T.load_focus(zones, end);
      status.resilience = T.resilience(raw.recovery, perf_raw, raw.stress, end);
      status.illness = g(illness, end);
      status.recovery_index = g(ri, end);
      status.recovery_time = T.recovery_time(raw.strain, raw.tsb, g(zone_of, end), end);
      var wt = g(sl[end], "wake_time");
      var wake = wt ? U.local(us(wt), tz).hour : null;
      var until = end === today ? (nn(now_hour) ? now_hour : U.local(Date.now() * 1000, tz).hour) : 23;
      status.body_battery = er ? T.body_battery(er.recovery, er.sleep_performance, wake, (es || {}).hours || [], er.strain, until) : null;
      status.strain_combined = !(end in raw.strain_combined) ? null
        : { value: raw.strain_combined[end], strain: g(raw.strain, end), muscle_median_kg: med };
      status.sleep_perf_whoop = !(end in raw.sleep_perf_whoop) ? null
        : { value: raw.sleep_perf_whoop[end], sufficiency: g(perf_raw, end), consistency: g(sl[end], "consistency"),
          efficiency: g(sl[end], "efficiency") };
    }

    if (can.metrics) {
      var m = data.metrics || [];
      raw.hrv = metric_map(m, "heart_rate_variability_sdnn");
      raw.resting_hr = metric_map(m, "resting_heart_rate");
      raw.vo2max = metric_map(m, "vo2_max");
      raw.steps = metric_map(m, "step_count");
      raw.active_kcal = metric_map(m, "active_energy_burned");
      var basal = metric_map(m, "basal_energy_burned");
      raw.total_kcal = {};
      keys(raw.active_kcal).forEach(function (d) { if (d in basal) raw.total_kcal[d] = raw.active_kcal[d] + basal[d]; });
      raw.daylight = metric_map(m, "time_in_daylight");
      if (!can.scores) {   // without the scores scope fall back to Apple's daily values
        raw.spo2 = metric_map(m, "oxygen_saturation");
        raw.resp_rate = metric_map(m, "respiratory_rate");
      }
      ["resting_hr", "hrv"].forEach(function (c) { bands[c] = T.baseline_band(raw[c], dates); });
      var hs = T.hrv_status(raw.hrv, end);
      status.hrv_status = hs;
      if (can.scores) status.training_status = T.training_status(trimp, raw.ctl, raw.vo2max, hs, end);
    }

    if (can.sleep) {   // sleep efficiency = asleep / in bed (main sleep); chronotype from the last 60 nights
      var sess = data.sleep || [];
      raw.sleep_efficiency = {};
      sess.forEach(function (r) {
        var inBed = nn(r.in_bed) ? Number(r.in_bed) : (us(r.end_at) - us(r.start_at)) / U.US_MIN;
        if (inBed && r.asleep_min) raw.sleep_efficiency[r.wake_date] = Math.min(100.0, U.round(100 * r.asleep_min / inBed, 1));
      });
      var since = U.addDays(end, -T.params.CHRONO_DAYS);
      status.chronotype = T.chronotype(sess.filter(function (r) { return r.wake_date > since; })
        .map(function (r) { return [us(r.start_at), us(r.end_at)]; }), tz);
    }

    if (can.training) {   // heart-rate recovery: Apple's one-minute value, else our own from the workout's raw heart rate
      var hrr = {}, ha = data.hrr_apple || {}, hw = data.hrr_workouts || {};
      keys(ha).forEach(function (d) { hrr[d] = ha[d]; });
      keys(hw).forEach(function (d) { if (!(d in hrr)) hrr[d] = hw[d]; });
      raw.hr_recovery = hrr;
      var last = null;
      keys(hrr).forEach(function (d) { if (d <= end && (last === null || d > last)) last = d; });
      status.hr_recovery = last === null ? null : { bpm: U.round(hrr[last], 1), date: last, level: T.hr_recovery_level(hrr[last]) };
    }

    if (can.weight) {
      if (nn(data.weights_error)) unavailable.weight = data.weights_error;
      else {
        var wts = data.weights || {}, ext = [];
        for (var b = 6; b > 0; b--) ext.push(U.addDays(start, -b));
        ext = ext.concat(dates);
        var avg = T.rolling(ext.map(function (d) { return g(wts, d); }), 7, 1).slice(6);
        raw.weight = wts;
        extra.weight_avg7 = {};
        dates.forEach(function (d, i) { extra.weight_avg7[d] = avg[i]; });
      }
    }

    keys(raw).forEach(function (key) {
      var sc = SCOPE[key];
      if (sc !== undefined && sc in can && !can[sc]) delete raw[key];
    });
    if (!can.sleep) delete status.sleep_perf_whoop;
    var series = {};
    SERIES.forEach(function (s) {
      var key = s[0], scope = s[3];
      if (!(key in raw)) {
        if (!(key in unavailable)) unavailable[key] = !can[scope] ? "没有授权查看 " + scope : "没有数据";
        return;
      }
      var by = raw[key];
      var values = dates.map(function (d) { return nn(by[d]) ? U.round(Number(by[d]), 2) : null; });
      var avg7 = key === "weight" ? dates.map(function (d) { return extra.weight_avg7[d]; }) : T.rolling(values);
      var present = values.filter(nn);
      var prev = keys(by).sort().filter(function (d) { return prev_start <= d && d < start && nn(by[d]); })
        .map(function (d) { return Number(by[d]); });
      series[key] = { label: tl(s[1]), unit: tl(s[2]), group: s[4], values: values, avg7: avg7, n: present.length,
        min: present.length ? pyMin(present) : null, max: present.length ? pyMax(present) : null,
        mean: present.length ? U.round(U.pySum(present) / present.length, 2) : null,
        prev_mean: prev.length ? U.round(U.pySum(prev) / prev.length, 2) : null, prev_n: prev.length };
      if (key in bands) series[key].baseline = bands[key];
    });
    if (series.recovery) {
      series.recovery.zones = extra.recovery_zones;
      series.recovery.contributors = extra.recovery_contributors;
    }
    if (series.strain) series.strain.target = extra.strain_target;
    return { start: start, end: end, days: days, prev_start: prev_start, dates: dates, series: series,
      unavailable: unavailable, hr_zones: g(extra, "hr_zones"), aerobic_weeks: g(extra, "aerobic_weeks"), status: status,
      version: T.params.TREND_VERSION };
  }

  // ---------------------------------------------------------------- web/views.py
  function recovery_zone(v) {
    if (!nn(v)) return null;
    return v >= P.RECOVERY_GREEN ? "green" : v >= P.RECOVERY_RED ? "yellow" : "red";
  }

  function month_range(month) {
    var y = +month.slice(0, 4), mo = +month.slice(5, 7);
    var start = y + "-" + U.pad(mo, 2) + "-01";
    var next = (mo === 12 ? y + 1 : y) + "-" + U.pad(mo === 12 ? 1 : mo + 1, 2) + "-01";
    return [start, U.addDays(next, -1)];
  }

  /** GET /days?month=: scores {date: {recovery, sleep_score, strain}}, present {field: [dates]} for each visible source,
   * firsts: the earliest date of each visible source (null = none), hidden: the scopes the viewer may not see. */
  function month_days(month, today, scores, present, firsts, hidden) {
    var r = month_range(month), sets = {};
    keys(present).forEach(function (f) { sets[f] = {}; (present[f] || []).forEach(function (d) { sets[f][d] = true; }); });
    var out = U.dateRange(r[0], r[1]).map(function (d) {
      var sc = scores[d];
      var row = { date: d, future: d > today, scores: hidden.indexOf("scores") >= 0 ? null : sc !== undefined && sc !== null };
      P.DAYS_FIELDS.forEach(function (f) { row[f] = f in sets ? d in sets[f] : null; });
      row.recovery = g(sc, "recovery"); row.sleep_score = g(sc, "sleep_score"); row.strain = g(sc, "strain");
      row.zone = recovery_zone(row.recovery);
      row.has_data = ["scores", "sleep", "training", "metrics"].some(function (f) { return !!row[f]; });
      return row;
    });
    var fs = firsts.filter(nn);
    return { month: month, start: r[0], end: r[1], today: today, first: fs.length ? fs.slice().sort()[0] : null, days: out,
      hidden: hidden };
  }

  /** web/views.reasons for one field (only sleep_score is needed here). */
  var FIELD_FLAGS = { sleep_score: ["no_sleep"] };
  function reason(row, f) {
    if (row === null || row === undefined) return "这天还没有计算评分（每小时 :10 自动算一次）";
    var flags = row.flags || {}, hit = null, cs = FIELD_FLAGS[f] || [];
    for (var i = 0; i < cs.length; i++) if (cs[i] in flags) { hit = flags[cs[i]]; break; }
    return hit || "没算出来（flags 里没有写原因）";
  }

  /** GET /sleep/nights: sessions = sleep_sessions rows of the range by start (wake_date, start_at, end_at, asleep_min,
   * deep_min, core_min, rem_min, awake_min, unspecified_min, is_main, source, device); scores {date: score row}. */
  function sleep_nights(sessions, scores, can_scores, end, days, tz) {
    tz = U.tzOf(tz);
    var start = U.addDays(end, -(days - 1)), nights = [];
    for (var k = 0; k < days; k++) {
      var d = U.addDays(start, k);
      var day = sessions.filter(function (s) { return s.wake_date === d; });
      var main = U.maxBy(day.filter(function (s) { return s.is_main; }), function (s) { return s.asleep_min || 0; });
      var naps = day.filter(function (s) { return s !== main; });
      var sc = scores[d] || null;
      var night = { date: d, main: null, nap_min: U.pySum(naps.map(function (s) { return s.asleep_min || 0; })),
        naps: naps.map(function (s) { return { start: hm(s.start_at, tz), end: hm(s.end_at, tz), asleep_min: g(s, "asleep_min") }; }),
        sleep_score: g(sc, "sleep_score"), sleep_actual_min: g(sc, "sleep_actual_min"), sleep_need_min: g(sc, "sleep_need_min"),
        sleep_debt_min: g(sc, "sleep_debt_min"), reason: null };
      if (main !== null) {
        night.main = { start: hm(main.start_at, tz), end: hm(main.end_at, tz), start_at: U.isoformat(us(main.start_at), tz),
          end_at: U.isoformat(us(main.end_at), tz) };
        ["asleep_min", "deep_min", "core_min", "rem_min", "awake_min", "unspecified_min"].forEach(function (f) { night.main[f] = g(main, f); });
        night.main.source = g(main, "source"); night.main.device = g(main, "device");
      }
      if (can_scores && night.sleep_score === null) night.reason = reason(sc, "sleep_score");
      else if (main === null) night.reason = night.sleep_score === null ? "没有手表睡眠数据" : "有睡眠分，但没有睡眠段明细";
      nights.push(night);
    }
    return { start: start, end: end, nights: nights, scores_visible: can_scores };
  }

  // ---------------------------------------------------------------- insights/sleep_detail.py
  /** rows (sleep_sessions by start: wake_date, start_at, end_at, asleep_min, core_min, deep_min, rem_min, device) ->
   * [{wake date: longest watch session}, [[start, end] of every watch session, microseconds]]. */
  function watch_sessions(rows) {
    var watch = rows.filter(function (r) {
      return (r.device && r.device.toLowerCase().indexOf("watch") >= 0) || (r.core_min || 0) + (r.deep_min || 0) + (r.rem_min || 0) > 0;
    });
    var mains = {};
    watch.forEach(function (r) { if (!(r.wake_date in mains) || r.asleep_min > mains[r.wake_date].asleep_min) mains[r.wake_date] = r; });
    return [mains, watch.map(function (r) { return [us(r.start_at), us(r.end_at)]; })];
  }

  /** days: {wake date: minute states}; only pairs of adjacent days count. [SRI, pairs]. */
  function sri(days) {
    var same = 0, total = 0, pairs = 0;
    keys(days).forEach(function (d) {
      var cur = days[d], nxt = days[U.addDays(d, 1)];
      if (!nxt) return;
      pairs += 1;
      for (var i = 0; i < P.DAY_MIN; i++) if (cur[i] === nxt[i]) same++;
      total += P.DAY_MIN;
    });
    if (pairs < P.MIN_PAIRS) return [null, pairs];
    return [U.round(200 * same / total - 100, 1), pairs];
  }

  function sri_from(mains, intervals, tz, start, end) {
    var days = {};
    keys(mains).forEach(function (d) { if (start <= d && d <= end) days[d] = HSS.asleep_minutes(intervals, d, tz); });
    var s = sri(days);
    return { sri: s[0], pairs: s[1], nights: keys(days).length, start: start, end: end };
  }

  function consistency_pct(v) { return nn(v) ? U.round(Math.max(0.0, Math.min(100.0, v))) : null; }

  /** rows {date, sleep_score, s = details.sleep} of the days whose details have a sleep block, by date. */
  function nights_from(rows) {
    return rows.map(function (r) {
      var s = r.s || {};
      return { date: r.date, sleep_score: g(r, "sleep_score"), bedtime: g(s, "bedtime"), wake_time: g(s, "wake_time"),
        asleep_min: g(s, "asleep_min"), nap_min: g(s, "nap_min"), efficiency: g(s, "efficiency"), latency_min: g(s, "latency_min"),
        waso_min: g(s, "waso_min"), awakenings: g(s, "awakenings"), deep_min: g(s, "deep_min"), rem_min: g(s, "rem_min") };
    });
  }

  var NOTES = "规律性 = 睡眠规律性指数 SRI（相隔 24 小时睡 / 醒状态相同的比例，−100～100，这里截到 0–100）；"
    + "入睡潜伏期只在睡着前有手表记的「清醒」片段时才有；效率 = 睡着 /（潜伏期开始 → 醒来）";

  /** GET /sleep/details: sessions = watch_sessions(rows of at least the 30 days before end), nights = nights_from(...). */
  function details(sessions, nights, tz, start, end) {
    tz = U.tzOf(tz);
    var r7 = sri_from(sessions[0], sessions[1], tz, U.addDays(end, -6), end);
    var r30 = sri_from(sessions[0], sessions[1], tz, U.addDays(end, -29), end);
    return { start: start, end: end, consistency_pct_7d: consistency_pct(r7.sri), sri_7d: r7.sri, sri_7d_pairs: r7.pairs,
      consistency_pct_30d: consistency_pct(r30.sri), sri_30d: r30.sri, sri_30d_pairs: r30.pairs, nights: nights, notes: NOTES };
  }

  // ---------------------------------------------------------------- insights/fitness.py
  /** rows: daily_scores {date, strain, hr_max, z = details.load.zone_minutes, missing = flags.no_raw_hr}; workouts null =
   * no workouts table on this side. */
  function hr_zones(rows, workouts, start, end, with_workouts) {
    with_workouts = with_workouts === undefined ? true : with_workouts;
    var days = rows.map(function (r) {
      return { date: r.date, zone_minutes: g(r, "z"), hr_max: g(r, "hr_max"), strain: g(r, "strain"),
        missing_reason: nn(r.z) ? null : g(r, "missing") };
    });
    var total = [0, 1, 2, 3, 4].map(function (i) {
      return U.round(U.pySum(days.map(function (d) { return (d.zone_minutes || [0, 0, 0, 0, 0])[i]; })), 1);
    });
    if (!with_workouts) return { zones: P.ZONES, start: start, end: end, days: days, total_minutes: total, hidden: ["training"] };
    return { zones: P.ZONES, start: start, end: end, days: days, total_minutes: total, workouts: nn(workouts) ? workouts : null,
      workouts_note: nn(workouts) ? null : "训练记录表（workouts）还没建，只有每天的区间分钟" };
  }

  /** [key, Chinese] of a percentile ([null, null] without one). */
  function level(pct) {
    if (!nn(pct)) return [null, null];
    for (var i = 0; i < P.LEVELS.length; i++) if (pct >= P.LEVELS[i][0]) return [P.LEVELS[i][1], P.LEVELS[i][2]];
    throw new Error("StopIteration");
  }

  var SEXES = { m: "male", male: "male", "男": "male", f: "female", female: "female", "女": "female" };

  /** GET /vo2max: rows {date, value} of the vo2_max metric in the window by date; sex "male" / "female" / null. */
  function vo2max(rows, birth_date, sex, end) {
    var months = {}, order = [];
    rows.forEach(function (r) {
      var mk = r.date.slice(0, 7);
      if (!(mk in months)) { months[mk] = []; order.push(mk); }
      months[mk].push(r.value);
    });
    var latest = rows.length ? rows[rows.length - 1] : null;
    var from = U.addDays(end, -89);
    var recent = rows.filter(function (r) { return r.date >= from; }).map(function (r) { return r.value; });
    var current = recent.length ? U.round(U.fmean(recent), 1) : (latest ? latest.value : null);
    var age = birth_date ? HS.chrono_age(birth_date, end) : null;
    var pct = nn(current) && nn(age) && sex ? HS.vo2_percentile(current, age, sex) : null;
    var lv = level(pct), trend = null;
    if (rows.length >= 3) {
      var xs = rows.map(function (r) { return U.daysBetween(end, r.date) / 365.2425; });
      var ys = rows.map(function (r) { return r.value; });
      var mx = U.fmean(xs), my = U.fmean(ys);
      var sxx = U.pySum(xs.map(function (x) { return Math.pow(x - mx, 2); }));
      trend = sxx ? U.round(U.pySum(xs.map(function (x, i) { return (x - mx) * (ys[i] - my); })) / sxx, 2) : null;
    }
    return { current: current, current_basis: recent.length ? "最近 90 天读数的平均" : "最近一次读数",
      latest: latest === null ? null : { date: latest.date, value: latest.value }, readings: rows.length, trend_per_year: trend,
      monthly: order.sort().map(function (mk) { return { month: mk, avg: U.round(U.fmean(months[mk]), 1), n: months[mk].length }; }),
      norm_median: nn(age) && sex ? U.round(HS.vo2_norm(age, sex), 1) : null,
      percentile: pct, level: lv[0], level_zh: lv[1], level_label: lv[0] ? L(lv[1], P.LEVELS_EN[lv[0]]) : null,
      norm_source: "FRIEND 2022 跑步机常模（Kaminsky 2022 Mayo Clin Proc），同龄同性别",
      missing_reason: sex ? null : "没填性别（users.sex），没法对照常模" };
  }

  // ---------------------------------------------------------------- entry points (api.js style: stamped)
  function stamp(x) { x.algo_version = SERIES_VERSION; x.bundle_version = OBS.VERSION; return x; }
  OBS.ALGO_VERSIONS.series = SERIES_VERSION;
  /** The Trends document. opts: {end, days, today, tz, now_hour}. */
  OBS.trendsSeries = function (data, opts) { return stamp(compute(data, opts.end, opts.days, opts.today, opts.tz, opts.now_hour)); };
  OBS.monthDays = function (month, today, scores, present, firsts, hidden) {
    return stamp(month_days(month, today, scores || {}, present || {}, firsts || [], hidden || []));
  };
  OBS.sleepNights = function (sessions, scores, opts) {
    return stamp(sleep_nights(sessions, scores || {}, opts.can_scores !== false, opts.end, opts.days || 14, opts.tz));
  };
  /** opts: {end, start (default end − 13), tz}; sessions: sleep_sessions rows of [end − 30, end]; nights: nights_from rows. */
  OBS.sleepDetails = function (sessions, nightRows, opts) {
    var start = opts.start || U.addDays(opts.end, -13);
    return stamp(details(watch_sessions(sessions), nights_from(nightRows || []), opts.tz, start, opts.end));
  };
  OBS.vo2max = function (rows, birth_date, sex, end) { return stamp(vo2max(rows, birth_date || null, SEXES[String(sex || "").trim().toLowerCase()] || null, end)); };
  OBS.hrZones = function (rows, workouts, start, end) { return stamp(hr_zones(rows, workouts, start, end, true)); };

  return { params: P, EN: EN, tl: tl, window: window_, compute: compute, recovery_zone: recovery_zone, month_range: month_range,
    month_days: month_days, reason: reason, sleep_nights: sleep_nights,
    sleep_detail: { watch_sessions: watch_sessions, asleep_minutes: HSS.asleep_minutes, sri: sri, sri_from: sri_from,
      consistency_pct: consistency_pct, nights_from: nights_from, details: details, NOTES: NOTES },
    fitness: { hr_zones: hr_zones, level: level, vo2max: vo2max, SEXES: SEXES } };
})(OBS.util, OBS.trends, OBS.healthspan, OBS.healthspan_snapshot);
