/* recovery: that night's HRV, that night's resting heart rate, 60-day personal baseline, z-score weighting + logistic,
illness / temperature / SpO2 flags. Port of openbodyscore/recovery.py. Samples are [[t_us, value], ...] sorted by time. */
OBS.recovery = (function (U, P) {
  "use strict";

  function in_window(samples, start, end) {
    var out = [];
    for (var i = 0; i < samples.length; i++) if (start <= samples[i][0] && samples[i][0] <= end) out.push(samples[i][1]);
    return out;
  }

  /** Median of ln HRV within the sleep window -> [value | null, number of readings]. */
  function night_hrv_ln(samples, start, end) {
    var xs = in_window(samples, start, end).filter(function (v) { return v > 0; }).map(Math.log);
    return [xs.length ? U.median(xs) : null, xs.length];
  }

  /** Lowest time-weighted 5-minute mean during sleep -> [value | null, window start | null]. */
  function lowest_window(samples, start, end, window, hold, params) {
    var p = params || P;
    window = window === null || window === undefined ? p.RHR_WINDOW : window;
    hold = hold === null || hold === undefined ? p.HR_HOLD_CAP : hold;
    var xs = samples.filter(function (s) { return start <= s[0] && s[0] <= end; });
    if (!xs.length) return [null, null];
    var spans = xs.map(function (s, i) {
      var stop = Math.min(s[0] + hold, end, i + 1 < xs.length ? xs[i + 1][0] : end);
      return [s[0], Math.max(stop, s[0]), s[1]];
    });
    var best = null, at = null, half = (window / 1e6) / 2;
    for (var i = 0; i < spans.length; i++) {
      var w0 = spans[i][0], w1 = w0 + window, covered = 0.0, weighted = 0.0;
      for (var j = 0; j < spans.length; j++) {
        var a = spans[j][0], b = spans[j][1];
        if (a >= w1) break;
        var overlap = (Math.min(b, w1) - Math.max(a, w0)) / 1e6;
        if (overlap > 0) { covered += overlap; weighted += overlap * spans[j][2]; }
      }
      if (covered >= half) {
        var mean = weighted / covered;
        if (best === null || mean < best) { best = mean; at = w0; }
      }
    }
    if (best === null) {   // readings too sparse: the lowest single reading
      var lo = U.minBy(xs, function (s) { return s[1]; });
      at = lo[0]; best = lo[1];
    }
    return [best, at];
  }

  function lowest_window_mean(samples, start, end, window, hold, params) {
    return lowest_window(samples, start, end, window, hold, params)[0];
  }

  /** Personal baseline [mean, sd] (null with fewer than MIN_BASELINE_NIGHTS values). */
  function baseline(values, params) {
    var p = params || P;
    if (values.length < p.MIN_BASELINE_NIGHTS) return null;
    return [U.fmean(values), U.stdev(values)];
  }

  function zscore(x, mean, sd, floor, params) {
    var p = params || P;
    var z = (x - mean) / Math.max(sd, floor);
    return Math.max(-p.Z_CLIP, Math.min(p.Z_CLIP, z));
  }

  /** z: {item: z-score, higher is better}; missing items' weights are redistributed; HRV required -> [score 0–100, weighted z]. */
  function recovery_score(z, params) {
    var p = params || P;
    if (!("hrv" in z)) throw new Error("没有 HRV 不能算恢复分");
    var keys = Object.keys(z);
    var total_w = U.pySum(keys.map(function (k) { return p.RECOVERY_WEIGHTS[k]; }));
    var s = U.pySum(keys.map(function (k) { return p.RECOVERY_WEIGHTS[k] * z[k]; })) / total_w;
    var score = 100 / (1 + Math.exp(-(p.RECOVERY_SLOPE * s + p.RECOVERY_OFFSET)));
    return [U.round(score), s];
  }

  function illness_warning(rhr, rhr_base, hrv_ln, hrv_base_ln, params) {
    var p = params || P;
    if ([rhr, rhr_base, hrv_ln, hrv_base_ln].some(function (x) { return x === null || x === undefined; })) return false;
    return rhr > rhr_base + p.ILLNESS_RHR_DELTA && Math.exp(hrv_ln) < p.ILLNESS_HRV_RATIO * Math.exp(hrv_base_ln);
  }

  /** Deviation from the 60-day median when >= 0.6 °C, else null. */
  function wrist_temp_flag(temp, history, params) {
    var p = params || P;
    if (temp === null || temp === undefined || history.length < p.MIN_TEMP_BASELINE) return null;
    var delta = temp - U.median(history);
    return Math.abs(delta) >= p.WRIST_TEMP_DELTA ? delta : null;
  }

  function spo2_flag(readings, params) {
    var p = params || P;
    if (!readings.length) return null;
    var med = U.median(readings), low = readings.filter(function (r) { return r < p.SPO2_LOW_READING; }).length;
    if (med < p.SPO2_LOW_MEDIAN || low >= p.SPO2_LOW_COUNT)
      return { median: U.round(med, 1), min: Math.min.apply(null, readings), below_90: low };
    return null;
  }

  return { in_window: in_window, night_hrv_ln: night_hrv_ln, lowest_window: lowest_window, lowest_window_mean: lowest_window_mean,
    baseline: baseline, zscore: zscore, recovery_score: recovery_score, illness_warning: illness_warning,
    wrist_temp_flag: wrist_temp_flag, spo2_flag: spo2_flag };
})(OBS.util, OBS.params);
