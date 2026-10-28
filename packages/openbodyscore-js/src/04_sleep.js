/* sleep: sleep score (Open Wearables 0.9.0's Sleep Score, ported line by line), sleep need, sleep debt, sleep performance.
Port of openbodyscore/sleep.py. Instants are microseconds; local-time functions take the time zone. */
OBS.sleep = (function (U, P) {
  "use strict";
  var MAX_EXP = 709.0;

  function sigmoid(x, k, base, midpoint, anchor) {
    var num = 1 + Math.exp(Math.min(MAX_EXP, k * (anchor - midpoint)));
    var den = 1 + Math.exp(Math.min(MAX_EXP, k * (x - midpoint)));
    return base * num / den;
  }

  /** Full marks for 7–9h; too little sleep loses points on a steep sigmoid, too much loses slowly and bottoms out at 50. */
  function duration_score(hours, params) {
    var p = params || P;
    if (p.OPTIMAL_MIN_HOURS <= hours && hours <= p.OPTIMAL_MAX_HOURS) return 100;
    if (hours < p.OPTIMAL_MIN_HOURS) {
      var raw = sigmoid(hours, -p.UNDERSLEEP_K, 100, p.UNDERSLEEP_MIDPOINT, p.OPTIMAL_MIN_HOURS);
      return Math.max(0, Math.min(100, Math.trunc(raw)));
    }
    var r2 = Math.min(100, Math.trunc(sigmoid(hours, p.OVERSLEEP_K, 100, p.OVERSLEEP_MIDPOINT, p.OPTIMAL_MAX_HOURS)));
    return Math.max(50, r2);
  }

  function stage_score(minutes, target) {
    if (minutes >= target) return 100;
    if (minutes <= 0) return 0;
    return Math.trunc(minutes / target * 100);
  }

  function stages_score(deep_min, rem_min, params) {
    var p = params || P;
    var total = stage_score(deep_min, p.DEEP_TARGET_MIN) * p.DEEP_WEIGHT + stage_score(rem_min, p.REM_TARGET_MIN) * p.REM_WEIGHT;
    return Math.max(0, Math.min(100, Math.trunc(total)));
  }

  /** Local time -> hours after 12 noon (continuous across midnight: 2 a.m. = 14). Seconds count, microseconds do not (as Python). */
  function hours_past_noon(t, tz) {
    var l = U.local(t, U.tzOf(tz));
    var h = l.hour + l.minute / 60 + l.second / 3600;
    return (h < 12 ? h + 24 : h) - 12;
  }

  /** Compared with the median bedtime of the last 14 nights (OW rule); 0 points with no history. */
  function consistency_score(history, tonight, tz, params) {
    var p = params || P;
    if (!history.length) return 0;
    var med = U.median(history.map(function (t) { return hours_past_noon(t, tz); }));
    var diff = (hours_past_noon(tonight, tz) - med) * 60;
    var penalty = 0.0;
    if (diff > p.CONSISTENCY_GRACE_MIN) penalty = (diff - p.CONSISTENCY_GRACE_MIN) / p.MAX_LATE_PENALTY_WINDOW_MIN * 100;
    else if (diff < -p.CONSISTENCY_GRACE_MIN)
      penalty = Math.min(p.MAX_EARLY_PENALTY_POINTS, (Math.abs(diff) - p.CONSISTENCY_GRACE_MIN) / p.MAX_EARLY_PENALTY_WINDOW_MIN * 100);
    return Math.max(0, Math.trunc(100 - penalty));
  }

  /** Night awake time (WASO) 80 points, wake-ups (> 5 minutes) 20 points. */
  function interruptions_score(awake_min, awakenings, params) {
    var p = params || P;
    var dur = p.INTERRUPTION_DURATION_POINTS;
    if (awake_min > p.INTERRUPTIONS_GRACE_MIN) {
      var excess = awake_min - p.INTERRUPTIONS_GRACE_MIN;
      dur = Math.max(0.0, dur - excess / p.MAX_INTERRUPTION_PENALTY_WINDOW_MIN * p.INTERRUPTION_DURATION_POINTS);
    }
    var n = awakenings.filter(function (d) { return d > p.SIGNIFICANT_WAKE_MIN; }).length;
    var freq = p.INTERRUPTION_FREQUENCY_POINTS * p.FREQ_SCORE_FRACTIONS[Math.min(n, p.FREQ_SCORE_FRACTIONS.length - 1)];
    return Math.max(0, Math.min(100, Math.trunc(dur + freq)));
  }

  /** Real night awake time from stage segments [start, end, stage] -> [total awake minutes, minutes of each wake-up]. */
  function waso(segments) {
    var segs = segments.slice().sort(function (a, b) { return a[0] - b[0]; });   // stable, like Python's sort by start
    var idx = [];
    segs.forEach(function (s, i) { if (s[2] !== "awake") idx.push(i); });
    if (!idx.length) return [0.0, []];
    var awakenings = [], prevEnd = null;
    for (var i = idx[0]; i <= idx[idx.length - 1]; i++) {
      var s = segs[i];
      if (s[2] !== "awake") { prevEnd = null; continue; }
      var minutes = ((s[1] - s[0]) / 1e6) / 60;
      if (prevEnd !== null && s[0] <= prevEnd) awakenings[awakenings.length - 1] += minutes;
      else awakenings.push(minutes);
      prevEnd = s[1];
    }
    return [U.pySum(awakenings), awakenings];
  }

  /** Duration 40%, stages 20%, regularity 20%, interruptions 20%. bedtime / history: instants, read in time zone tz. */
  function sleep_score(total_sleep_min, deep_min, rem_min, bedtime, history, awake_min, awakenings, tz, params) {
    var p = params || P;
    if (!(0 < total_sleep_min && total_sleep_min <= 24 * 60)) throw new Error("睡眠时长不合理：" + total_sleep_min + " 分钟");
    var parts = {
      duration: duration_score(total_sleep_min / 60, p),
      stages: stages_score(deep_min, rem_min, p),
      consistency: consistency_score(history, bedtime, tz, p),
      interruptions: interruptions_score(awake_min, awakenings, p)
    };
    var frac = parts.duration / 100 * p.DURATION_IMPACT + parts.stages / 100 * p.STAGES_IMPACT
      + parts.consistency / 100 * p.CONSISTENCY_IMPACT + parts.interruptions / 100 * p.INTERRUPTIONS_IMPACT;
    return { score: Math.max(0, Math.min(100, Math.trunc(100 * frac))), duration: parts.duration, stages: parts.stages,
      consistency: parts.consistency, interruptions: parts.interruptions };
  }

  /** actual_by_night[0] = the previous night (k=1) …, null = no data. Weighted mean shortfall, never below 0. */
  function sleep_debt(actual_by_night, params) {
    var p = params || P, num = 0.0, den = 0.0;
    actual_by_night.slice(0, p.DEBT_NIGHTS).forEach(function (actual, k) {
      if (actual !== null && actual !== undefined) {
        var w = Math.pow(p.DEBT_DECAY, k);
        num += w * (p.SLEEP_NEED_BASELINE_MIN - actual);
        den += w;
      }
    });
    return den ? Math.max(0.0, num / den) : 0.0;
  }

  /** Baseline 8h + sleep debt (at most 2h) + previous day's strain add-on (3 minutes per point above 7). */
  function sleep_need(debt_min, prev_strain, params) {
    var p = params || P;
    var from_debt = Math.min(p.DEBT_CAP_MIN, debt_min);
    var from_strain = Math.max(0.0, (prev_strain || 0.0) - p.STRAIN_NEED_FROM) * p.STRAIN_NEED_MIN_PER_POINT;
    return { need: p.SLEEP_NEED_BASELINE_MIN + from_debt + from_strain, baseline: p.SLEEP_NEED_BASELINE_MIN,
      from_debt: from_debt, from_strain: from_strain };
  }

  function performance(actual_min, need_min) { return Math.min(actual_min / need_min, 1.0); }

  return { duration_score: duration_score, stages_score: stages_score, hours_past_noon: hours_past_noon,
    consistency_score: consistency_score, interruptions_score: interruptions_score, waso: waso, sleep_score: sleep_score,
    sleep_debt: sleep_debt, sleep_need: sleep_need, performance: performance };
})(OBS.util, OBS.params);
