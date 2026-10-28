/* stress: while awake and not moving, how far heart rate / HRV deviates from a personal calm baseline, scored 0–3.
Port of openbodyscore/stress.py.

- Only heart-rate readings while awake (not in a sleep segment), not during a workout, with no walking from 5 minutes before
  to 2 minutes after (steps < 20): steps and workouts stand in for an accelerometer.
- Calm baseline: median of such readings over the previous 14 days; dispersion = 1.4826 × MAD (floor 3 bpm). 1.4826 × MAD is
  the usual factor that makes the median absolute deviation comparable to a standard deviation for normal data.
- Per hour (>= 3 readings): z = (median HR − baseline) / dispersion; with daytime HRV z = 0.7 × HR z + 0.3 × HRV z;
  score = clip(0.5 + z, 0, 3). Windows, weights, offset and bands are v1 values, not yet validated (issue #118).
*/
OBS.stress = (function (U) {
  "use strict";
  var D = {
    STRESS_VERSION: "v1.0-2026-10-17",
    BASELINE_DAYS: 14,
    MIN_BASELINE_SAMPLES: 50,
    SPREAD_FLOOR: 3.0,
    STEP_WINDOW: [5 * U.US_MIN, 2 * U.US_MIN],   // from 5 minutes before to 2 minutes after the reading
    STEP_ACTIVE: 20,                             // >= 20 steps in that span counts as moving
    MIN_HOUR_SAMPLES: 3,
    HR_WEIGHT: 0.7, HRV_WEIGHT: 0.3,
    OFFSET: 0.5, MAX_SCORE: 3.0,
    BANDS: [[2.0, "high"], [1.0, "medium"], [0.0, "low"]]
  };

  function band(score, params) {
    var p = params || D;
    if (score === null || score === undefined) return null;
    for (var i = 0; i < p.BANDS.length; i++) if (score >= p.BANDS[i][0]) return p.BANDS[i][1];
    throw new Error("no stress band for " + score);
  }

  function robust_baseline(values, params) {
    var p = params || D;
    if (values.length < p.MIN_BASELINE_SAMPLES) return null;
    var med = U.median(values), mad = U.median(values.map(function (v) { return Math.abs(v - med); }));
    return [med, Math.max(1.4826 * mad, p.SPREAD_FLOOR)];
  }

  /** Heart-rate readings while awake, not in a workout, not walking. hr, steps: [[t, v]]; sleep, workouts: [[start, end]]. */
  function calm_samples(hr, sleep, workouts, steps, params) {
    var p = params || D;
    var busy = sleep.concat(workouts).sort(U.cmp);
    var out = [], j = 0, stepTimes = steps.map(function (s) { return s[0]; });
    for (var i = 0; i < hr.length; i++) {
      var t = hr[i][0], v = hr[i][1];
      while (j < busy.length && busy[j][1] < t) j++;
      var inBusy = false;
      for (var k = j; k < j + 3 && k < busy.length; k++) if (busy[k][0] <= t && t <= busy[k][1]) { inBusy = true; break; }
      if (inBusy) continue;
      var lo = U.bisectLeft(stepTimes, t - p.STEP_WINDOW[0]), hi = U.bisectRight(stepTimes, t + p.STEP_WINDOW[1]);
      var moving = U.pySum(steps.slice(lo, hi).map(function (s) { return s[1]; }));
      if (moving >= p.STEP_ACTIVE) continue;
      out.push([t, v]);
    }
    return out;
  }

  /** calm: heart-rate readings while awake and still; hrv: daytime HRV readings (ms); d: local date; tz: time zone. */
  function score_day(calm, hrv, d, tz, hr_base, hrv_base, params) {
    var p = params || D;
    tz = U.tzOf(tz);
    var byHour = {}, hrvHour = {};
    calm.forEach(function (s) { var l = U.local(s[0], tz); if (l.date === d) (byHour[l.hour] = byHour[l.hour] || []).push(s[1]); });
    hrv.forEach(function (s) {
      var l = U.local(s[0], tz);
      if (l.date === d && s[1] > 0) (hrvHour[l.hour] = hrvHour[l.hour] || []).push(Math.log(s[1]));
    });
    var hours = [];
    for (var h = 0; h < 24; h++) {
      var xs = byHour[h] || [];
      if (xs.length < p.MIN_HOUR_SAMPLES) continue;
      var med = U.median(xs), z = (med - hr_base[0]) / hr_base[1], zh = null;
      if (hrv_base && hrvHour[h] && hrvHour[h].length) {
        zh = (hrv_base[0] - U.fmean(hrvHour[h])) / Math.max(hrv_base[1], 0.05);
        z = p.HR_WEIGHT * z + p.HRV_WEIGHT * zh;
      }
      var s = Math.max(0.0, Math.min(p.MAX_SCORE, p.OFFSET + z));
      hours.push({ hour: h, score: U.round(s, 2), level: band(s, p), hr: U.round(med, 1), samples: xs.length,
        hrv_z: zh === null ? null : U.round(zh, 2) });
    }
    if (!hours.length) return { score: null, level: null, hours: [], low_h: 0, medium_h: 0, high_h: 0, peak_hour: null };
    var mean = U.fmean(hours.map(function (x) { return x.score; }));
    var peak = U.maxBy(hours, function (x) { return x.score; });
    var count = function (lv) { return hours.filter(function (x) { return x.level === lv; }).length; };
    return { score: U.round(mean, 2), level: band(mean, p), hours: hours, low_h: count("low"), medium_h: count("medium"),
      high_h: count("high"), peak_hour: peak.hour };
  }

  var M = { band: band, robust_baseline: robust_baseline, calm_samples: calm_samples, score_day: score_day, params: D,
    override: function (changes) { return U.override(D, changes, "stress"); } };
  Object.keys(D).forEach(function (k) { M[k] = D[k]; });
  return M;
})(OBS.util);
