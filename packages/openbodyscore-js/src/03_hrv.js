/* hrv: RMSSD / SDNN from Apple's beat-to-beat series (HKHeartbeatSeriesSample). Port of openbodyscore/hrv.py.

RR_i = t_i − t_{i−1}; an interval is invalid when the beat was preceded by a gap, lies outside 300–2000 ms, or deviates more
than MAX_DEVIATION from the series median (ectopic beats / motion artifacts, Task Force ESC/NASPE 1996).
RMSSD = sqrt(mean((RR_i − RR_{i−1})²)) over pairs of adjacent valid intervals; SDNN = population SD of the valid intervals.
*/
OBS.hrv = (function (U) {
  "use strict";
  var D = { MIN_RR_MS: 300.0, MAX_RR_MS: 2000.0, MAX_DEVIATION: 0.25, MIN_VALID_DIFFS: 20, ALGO: "rr-v1" };

  /** Beat times (seconds since the series start) -> RR in ms; null where beat i was preceded by a gap or times go backwards. */
  function rr_intervals(beats, gaps) {
    var out = [];
    for (var i = 1; i < beats.length; i++) {
      if (gaps && i < gaps.length && gaps[i]) { out.push(null); continue; }
      var d = (beats[i] - beats[i - 1]) * 1000;
      out.push(d > 0 ? d : null);
    }
    return out;
  }

  function clean(rr, params) {
    var p = params || D;
    var ok = rr.filter(function (x) { return x !== null && p.MIN_RR_MS <= x && x <= p.MAX_RR_MS; });
    if (!ok.length) return rr.map(function () { return null; });
    var med = U.median(ok);
    return rr.map(function (x) {
      return x !== null && p.MIN_RR_MS <= x && x <= p.MAX_RR_MS && Math.abs(x - med) <= p.MAX_DEVIATION * med ? x : null;
    });
  }

  /** -> {rmssd, sdnn, mean_rr, n_beats, n_valid, n_diffs, artifact_ratio} */
  function analyze(beats, gaps, params) {
    var p = params || D;
    var rr = clean(rr_intervals(beats, gaps), p);
    var valid = rr.filter(function (x) { return x !== null; });
    var diffs = [];
    for (var i = 0; i + 1 < rr.length; i++) if (rr[i] !== null && rr[i + 1] !== null) diffs.push(rr[i + 1] - rr[i]);
    var rmssd = diffs.length >= p.MIN_VALID_DIFFS ? Math.sqrt(U.pySum(diffs.map(function (d) { return d * d; })) / diffs.length) : null;
    var sdnn = valid.length > p.MIN_VALID_DIFFS ? U.pstdev(valid) : null;
    var res = { rmssd: rmssd === null ? null : U.round(rmssd, 2), sdnn: sdnn === null ? null : U.round(sdnn, 2),
      mean_rr: valid.length ? U.round(U.fmean(valid), 1) : null, n_beats: beats.length, n_valid: valid.length, n_diffs: diffs.length };
    res.artifact_ratio = U.round(1 - res.n_valid / Math.max(res.n_beats - 1, 1), 3);
    return res;
  }

  /** 'HH:MM:SS.ss' -> seconds since midnight (null when unreadable). */
  function parse_clock(s) {
    if (typeof s !== "string") return null;
    var parts = s.split(":");
    if (parts.length !== 3) return null;
    var h = parts[0].trim(), m = parts[1].trim(), sec = parts[2].trim();
    if (!/^[+-]?\d+$/.test(h) || !/^[+-]?\d+$/.test(m) || !/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(sec)) return null;
    return parseInt(h, 10) * 3600 + parseInt(m, 10) * 60 + parseFloat(sec);
  }

  /** Clock times of consecutive beats -> seconds since the first beat (unwraps midnight). */
  function beats_from_clock(times) {
    var out = [], base = null, prev = null, day = 0.0;
    for (var i = 0; i < times.length; i++) {
      var t = parse_clock(times[i]);
      if (t === null) continue;
      if (prev !== null && t + day < prev - 43200) day += 86400;
      t += day;
      if (base === null) base = t;
      out.push(U.round(t - base, 3));
      prev = t;
    }
    return out;
  }

  var M = { rr_intervals: rr_intervals, clean: clean, analyze: analyze, parse_clock: parse_clock, beats_from_clock: beats_from_clock,
    params: D, override: function (changes) { return U.override(D, changes, "hrv"); } };
  Object.keys(D).forEach(function (k) { M[k] = D[k]; });
  return M;
})(OBS.util);
