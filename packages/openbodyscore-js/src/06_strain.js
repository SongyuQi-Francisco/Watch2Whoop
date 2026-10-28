/* strain: HRmax, Edwards TRIMP, strain (0–21), CTL / ATL / TSB. Port of openbodyscore/strain.py. */
OBS.strain = (function (U, P) {
  "use strict";

  /** Tanaka: 208 − 0.7 × age. */
  function hrmax_formula(age) { return 208 - 0.7 * age; }

  /** Whole years from birth date to date d ('YYYY-MM-DD'). */
  function age_on(birth, d) {
    var by = +birth.slice(0, 4), bm = +birth.slice(5, 7), bd = +birth.slice(8, 10);
    var y = +d.slice(0, 4), m = +d.slice(5, 7), dd = +d.slice(8, 10);
    return y - by - ((m < bm || (m === bm && dd < bd)) ? 1 : 0);
  }

  /** Heart-rate histogram (integer bpm) in a sliding window, for the 99.5th percentile of observed heart rate. */
  function HrHistogram() { this.counts = new Map(); this.n = 0; }
  /** hist: Map bpm -> count (or a plain object). */
  HrHistogram.prototype.add = function (hist, sign) {
    sign = sign === undefined ? 1 : sign;
    var self = this, total = 0;
    var each = hist instanceof Map ? function (f) { hist.forEach(function (c, b) { f(b, c); }); }
      : function (f) { Object.keys(hist).forEach(function (b) { f(Number(b), hist[b]); }); };
    each(function (bpm, c) { self.counts.set(bpm, (self.counts.get(bpm) || 0) + sign * c); total += c; });
    this.n += sign * total;
  };
  HrHistogram.prototype.percentile = function (q) {
    if (this.n <= 0) return null;
    var rank = q / 100 * (this.n - 1), seen = 0, keys = Array.from(this.counts.keys()).sort(function (a, b) { return a - b; });
    for (var i = 0; i < keys.length; i++) {
      seen += this.counts.get(keys[i]);
      if (seen > rank) return keys[i];
    }
    return keys[keys.length - 1];
  };

  /** Count of values as a Map (Python collections.Counter of integer bpm). */
  function counter(values) {
    var m = new Map();
    for (var i = 0; i < values.length; i++) m.set(values[i], (m.get(values[i]) || 0) + 1);
    return m;
  }

  /** The larger of the observed 99.5th percentile and 208 − 0.7 × age. */
  function hrmax(age, observed_p995) { return Math.max(hrmax_formula(age), observed_p995 || 0.0); }

  /** Minutes each heart-rate sample represents: until the next one, at most cap; the last one counts as half of cap. */
  function sample_minutes(times, cap, params) {
    var p = params || P;
    cap = cap === null || cap === undefined ? p.HR_SAMPLE_CAP : cap;
    var out = new Array(times.length);
    for (var i = 0; i < times.length; i++) {
      var dt = i + 1 < times.length ? times[i + 1] - times[i] : roundHalfEven(cap / 2);
      out[i] = (Math.min(dt, cap) / 1e6) / 60;
    }
    return out;
  }
  function roundHalfEven(x) { var f = Math.floor(x), d = x - f; return d > 0.5 || (d === 0.5 && f % 2 !== 0) ? f + 1 : f; }

  function zone_weight(hr, hr_max, params) {
    var p = params || P, pct = hr / hr_max;
    for (var i = 0; i < p.EDWARDS_ZONES.length; i++) if (pct >= p.EDWARDS_ZONES[i][0]) return p.EDWARDS_ZONES[i][1];
    return 0;
  }

  /** Edwards TRIMP = Σ minutes per zone × weight -> [TRIMP, minutes in zones 1–5]. */
  function edwards_trimp(samples, minutes, hr_max, params) {
    var zones = [0.0, 0.0, 0.0, 0.0, 0.0], n = Math.min(samples.length, minutes.length);
    for (var i = 0; i < n; i++) {
      var w = zone_weight(samples[i][1], hr_max, params);
      if (w) zones[w - 1] += minutes[i];
    }
    return [U.pySum(zones.map(function (m, i) { return m * (i + 1); })), zones];
  }

  function strain(trimp, k, params) {
    var p = params || P;
    k = k === null || k === undefined ? p.STRAIN_K : k;
    return p.STRAIN_MAX * (1 - Math.exp(-trimp / k));
  }

  /** CTL (42-day EWMA), ATL (7-day EWMA), TSB, day by day; null days keep the previous values and are flagged missing. */
  function training_load(trimps, params) {
    var p = params || P;
    var known = trimps.filter(function (t) { return t !== null && t !== undefined; }).slice(0, p.LOAD_SEED_DAYS);
    if (!known.length) return trimps.map(function (t) { return { ctl: null, atl: null, tsb: null, missing: t === null || t === undefined }; });
    var ctl = U.pySum(known) / known.length, atl = ctl;
    var a_ctl = 1 - Math.exp(-1 / p.CTL_DAYS), a_atl = 1 - Math.exp(-1 / p.ATL_DAYS);
    return trimps.map(function (t) {
      var missing = t === null || t === undefined;
      if (!missing) { ctl += a_ctl * (t - ctl); atl += a_atl * (t - atl); }
      return { ctl: ctl, atl: atl, tsb: ctl - atl, missing: missing };
    });
  }

  return { hrmax_formula: hrmax_formula, age_on: age_on, HrHistogram: HrHistogram, counter: counter, hrmax: hrmax,
    sample_minutes: sample_minutes, zone_weight: zone_weight, edwards_trimp: edwards_trimp, strain: strain,
    training_load: training_load };
})(OBS.util, OBS.params);
