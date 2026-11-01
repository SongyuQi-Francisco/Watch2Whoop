/* stress_days: the day-by-day stress rows. Port of score_range() in body_engine insights/stress.py (stress_daily rows). */
OBS.stress_days = (function (U, T) {
  "use strict";

  /**
   * tz; start, end: local dates; sleep / workouts: [[start, end]]; hr / steps / hrv: [[t, value]] sorted (microseconds);
   * hrv_night: {date: night ln HRV} (the engine's hrv_night_ln); shortcut: [dates] that only have Shortcut data.
   */
  function score_range(tz, start, end, sleep, workouts, hr, steps, hrv, hrv_night, shortcut) {
    tz = U.tzOf(tz);
    var sc = {};
    (shortcut || []).forEach(function (d) { sc[d] = true; });
    var calm = T.calm_samples(hr, sleep, workouts, steps), awakeHrv = T.calm_samples(hrv, sleep, workouts, steps);
    var calmByDay = {}, hrvByDay = {}, hrDays = {};
    calm.forEach(function (s) { var d = U.localDate(s[0], tz); (calmByDay[d] = calmByDay[d] || []).push(s); });
    awakeHrv.forEach(function (s) { var d = U.localDate(s[0], tz); (hrvByDay[d] = hrvByDay[d] || []).push(s); });
    hr.forEach(function (s) { hrDays[U.localDate(s[0], tz)] = true; });
    var hrvKeys = Object.keys(hrv_night || {});
    return U.dateRange(start, end).map(function (d) {
      var flags = {}, baseVals = [];
      for (var k = 1; k <= T.BASELINE_DAYS; k++) (calmByDay[U.addDays(d, -k)] || []).forEach(function (s) { baseVals.push(s[1]); });
      var hrBase = T.robust_baseline(baseVals);
      var lo = U.addDays(d, -60);
      var hist = hrvKeys.filter(function (x) { return lo <= x && x < d; }).map(function (x) { return hrv_night[x]; });
      var hrvBase = hist.length >= 14 ? [U.fmean(hist), U.stdev(hist)] : null;
      var res;
      if (!hrDays[d]) {
        flags.no_raw_hr = sc[d] ? "这天只有快捷指令数据：心率是按小时求和的，算不了压力" : "没有原始心率（手表没戴或没同步）";
        res = T.score_day([], [], d, tz, [0, 1], null);
      } else if (hrBase === null) {
        flags.baseline_insufficient = "前 " + T.BASELINE_DAYS + " 天醒着不动的心率读数只有 " + baseVals.length + " 条（要 ≥ " + T.MIN_BASELINE_SAMPLES + "）";
        res = T.score_day([], [], d, tz, [0, 1], null);
      } else {
        res = T.score_day(calmByDay[d] || [], hrvByDay[d] || [], d, tz, hrBase, hrvBase);
        if (res.score === null) flags.no_calm_hours = "醒着不动、每小时至少 3 条心率的时段一个都没有";
        if (!steps.length) flags.no_motion_data = "没有步数数据，走路时的心率没法排除，压力可能偏高";
      }
      res.baseline = hrBase === null ? null : { calm_hr: U.round(hrBase[0], 1), spread: U.round(hrBase[1], 2),
        hrv_ln: hrvBase === null ? null : U.round(hrvBase[0], 3) };
      var row = { date: d };
      Object.keys(res).forEach(function (key) { row[key] = res[key]; });
      row.flags = flags;
      return row;
    });
  }

  return { score_range: score_range };
})(OBS.util, OBS.stress);
