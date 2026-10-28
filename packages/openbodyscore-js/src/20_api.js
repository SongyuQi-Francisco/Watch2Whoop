/* api: the entry points the iOS app calls through JavaScriptCore (evaluateScript, then OpenBodyScore.<fn>(…)).

Every result carries `algo_version` (the server's version string of that algorithm, so rows computed here and on the server
can be reconciled) and `bundle_version` (this JS package). Instants may be epoch milliseconds or ISO 8601 strings with an
offset; local dates are 'YYYY-MM-DD'; the time zone is an IANA name or a fixed offset.
*/
(function (U, P, I, E, SD, HSS, T, HV, ST) {
  "use strict";
  OBS.VERSION = OBS.BUNDLE_VERSION;
  OBS.ALGO_VERSIONS = { scores: P.ALGO_VERSION, healthspan: HSS.CURRENT.ALGO_VERSION, hrv: HV.ALGO, stress: ST.STRESS_VERSION,
    trends: T.params.TREND_VERSION };
  OBS.lang = "zh";
  /** "zh" (default, the stored / AI-facing texts) or "en" for the Trends labels. */
  OBS.setLanguage = function (lang) { OBS.lang = lang === "en" ? "en" : "zh"; };

  function stamp(x, algo) { x.algo_version = algo; x.bundle_version = OBS.VERSION; return x; }
  function pairs(xs) {
    return (xs || []).map(function (s) { return Array.isArray(s) ? [U.toUs(s[0]), s[1]] : [U.toUs(s.t), s.value]; })
      .sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
  }
  function spans(xs) {
    return (xs || []).map(function (s) { return Array.isArray(s) ? [U.toUs(s[0]), U.toUs(s[1])] : [U.toUs(s.start), U.toUs(s.end)]; });
  }

  /** Raw local data -> scoring inputs (see inputs.js for the shape and the source rules). */
  OBS.buildInputs = function (src) { return I.build(src); };

  /**
   * Daily scores (the server's daily_scores rows) for [first, last].
   * inputs: OBS.buildInputs(...) output, or the same shape with instants as milliseconds / ISO strings.
   * opts: {first (default: scoring origin = first day with a resting heart rate), last (default: today), today}.
   * Pass all data from `first − 365 days` (HRmax window) on; days before `first` are not scored but feed the baselines only
   * through the samples (the engine recomputes from `first`, as the server does from the user's scoring origin).
   */
  OBS.computeDays = function (inputs, opts) {
    opts = opts || {};
    var inp = I.normalize(inputs), tz = U.tzOf(inp.tz);
    var today = opts.today || U.localDate(Date.now() * 1000, tz);
    var first = opts.first || E.scoring_origin(inp.daily_rhr);
    if (!first) return [];
    var last = opts.last || today;
    return E.score_days(inp, first, last, today).map(function (row) { return stamp(row, P.ALGO_VERSION); });
  };

  /** One day's row (computed from the scoring origin up to that day, so baselines and CTL are the full-history ones). */
  OBS.computeDay = function (inputs, date, opts) {
    opts = opts || {};
    var rows = OBS.computeDays(inputs, { first: opts.first, last: date, today: opts.today });
    return rows.length && rows[rows.length - 1].date === date ? rows[rows.length - 1] : null;
  };

  /**
   * Stress rows (the server's stress_daily) for [start, end]. src: {tz, sleep: [[start, end]], workouts: [[start, end]],
   * hr, steps, hrv: [[t, value]], hrv_night: {date: ln HRV} (computeDays rows' hrv_night_ln), shortcut_dates}.
   * Give samples from start − 14 days (calm baseline) and hrv_night from start − 60 days.
   */
  OBS.stressDays = function (src, start, end) {
    return SD.score_range(src.tz, start, end, spans(src.sleep), spans(src.workouts), pairs(src.hr), pairs(src.steps),
      pairs(src.hrv), src.hrv_night || {}, src.shortcut_dates || [])
      .map(function (row) { return stamp(row, ST.STRESS_VERSION); });
  };

  /** Body age on one day / the weekly rows (healthspan_weekly). data: see healthspan_snapshot.js. */
  OBS.healthspanSnapshot = function (data, asOf) { return stamp(HSS.snapshot(data, asOf), HSS.CURRENT.ALGO_VERSION); };
  OBS.healthspanWeekly = function (data, origin, today) {
    return HSS.weekly(data, origin, today).map(function (row) { return stamp(row, HSS.CURRENT.ALGO_VERSION); });
  };
  /** data.sleep_days from watch sleep intervals [[start, end]] and the wake dates with a main sleep. */
  OBS.sleepDays = function (intervals, wakeDates, tz) { return HSS.sleep_days(spans(intervals), wakeDates, tz); };

  /** RMSSD / SDNN of one beat-to-beat reading (seconds since its start, gap flags). */
  OBS.analyzeHrv = function (beats, gaps) { return stamp(HV.analyze(beats, gaps), HV.ALGO); };

  /** One Trends analysis by name (OBS.trends.<name>), e.g. trend("hrv_status", {date: ms}, "2026-08-31"). */
  OBS.trend = function (name) {
    var fn = T[name];
    if (typeof fn !== "function") throw new Error("no trends function " + name);
    var args = Array.prototype.slice.call(arguments, 1);
    if (name === "chronotype") args[0] = spans(args[0]);
    return { value: fn.apply(null, args), algo_version: T.params.TREND_VERSION, bundle_version: OBS.VERSION };
  };

  /** Converters for callers that build inputs themselves. */
  OBS.toMicros = U.toUs;
})(OBS.util, OBS.params, OBS.inputs, OBS.engine, OBS.stress_days, OBS.healthspan_snapshot, OBS.trends, OBS.hrv, OBS.stress);
