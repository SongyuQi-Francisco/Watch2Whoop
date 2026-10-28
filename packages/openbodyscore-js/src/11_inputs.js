/* inputs: from raw samples to the scoring inputs (the pure part of body_engine scores/inputs.py).

The server reads its sources from the database (OW, the app's own sync, Shortcut, export.zip); the phone has them in its
local database. Either way, build() applies the same rules:
- Sleep segments (sleep_sessions rows): only watch sleep counts — a segment with stage data (core / deep / rem) or a device
  name containing "watch"; dates with only non-watch sleep are remembered (flag text only).
- Raw samples (hr, hrv, resp, spo2, temp): one per (kind, start time); a live sample wins over an export.xml copy (xml: true).
  SpO2 given as a fraction (<= 1.0, HealthKit's unit) is turned into percent.
- Stage segments: export.zip stages only fill nights (wake date = end − 4 h, local) that no other source has; then one
  segment per (start, stage).
- hrv: the samples of the user's hrv_kind only — Apple's SDNN for "sdnn", RMSSD values (OBS.hrv.analyze over the
  beat-to-beat series) for "rmssd"; never both. The kind is chosen per user by the server (see README).
Instants may be epoch milliseconds or ISO 8601 strings; the result uses microseconds (see util).
*/
OBS.inputs = (function (U, P, M) {
  "use strict";
  var WAKE_DAY_SHIFT = 4 * U.US_HOUR;   // importers/common.py: a sleep is assigned to the day of (wake − 4h)
  var KINDS = ["hr", "hrv", "resp", "spo2", "temp"];

  function isWatchSleep(row) {
    if (row.device && String(row.device).toLowerCase().indexOf("watch") >= 0) return true;
    return (row.core_min || 0) + (row.deep_min || 0) + (row.rem_min || 0) > 0;
  }

  function samplePairs(xs) {
    return (xs || []).map(function (s) { return Array.isArray(s) ? [U.toUs(s[0]), s[1]] : [U.toUs(s.t), s.value]; })
      .sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
  }

  /**
   * src = {tz, birth_date, hrv_kind, sleep_sessions: [{wake_date, start, end, asleep_min, core_min, deep_min, rem_min,
   *   awake_min, source, device}], samples: [{kind, t, value, xml}], segments: [{start, end, stage, export}],
   *   daily_rhr: {date: bpm}, shortcut_dates: [date], muscle_sessions: [...] | muscle: {date: {...}}, muscle_error}
   * -> inputs for OBS.engine.score_days.
   */
  function build(src) {
    var inp = { units: "us", tz: src.tz, birth_date: src.birth_date || null, hrv_kind: src.hrv_kind || P.HRV_KIND, nights: [],
      non_watch_dates: [], segments: [], hr: [], hrv: [], resp: [], spo2: [], temp: [],
      daily_rhr: Object.assign({}, src.daily_rhr || {}), shortcut_dates: (src.shortcut_dates || []).slice(),
      muscle: src.muscle || (src.muscle_sessions ? M.daily_muscle_load(src.muscle_sessions) : {}), muscle_error: src.muscle_error || null };
    var tz = U.tzOf(src.tz);
    var sessions = (src.sleep_sessions || []).map(function (r) { return Object.assign({}, r, { start: U.toUs(r.start), end: U.toUs(r.end) }); })
      .sort(function (a, b) { return a.start - b.start; });
    var nonWatch = {};
    sessions.forEach(function (r) {
      if (isWatchSleep(r)) inp.nights.push({ wake_date: r.wake_date, start: r.start, end: r.end, asleep_min: r.asleep_min,
        deep_min: r.deep_min || 0, rem_min: r.rem_min || 0, awake_min: r.awake_min || 0, source: r.source === undefined ? null : r.source });
      else nonWatch[r.wake_date] = true;
    });
    inp.nights.forEach(function (n) { delete nonWatch[n.wake_date]; });
    inp.non_watch_dates = Object.keys(nonWatch);

    var seen = new Map();   // "kind|t" -> [xml, kind, t, value]
    (src.samples || []).forEach(function (s) {
      if (KINDS.indexOf(s.kind) < 0 || s.value === null || s.value === undefined) return;
      var t = U.toUs(s.t), v = s.kind === "spo2" && s.value <= 1.0 ? s.value * 100 : s.value, xml = !!s.xml;
      var k = s.kind + "|" + t, old = seen.get(k);
      if (!old || (old[0] && !xml)) seen.set(k, [xml, s.kind, t, v]);
    });
    seen.forEach(function (x) { inp[x[1]].push([x[2], x[3]]); });
    KINDS.forEach(function (k) { inp[k].sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; }); });

    var segs = (src.segments || []).map(function (s) { return { start: U.toUs(s.start), end: U.toUs(s.end), stage: s.stage, exp: !!s.export }; });
    var live = segs.filter(function (s) { return !s.exp; });
    var taken = {};
    live.forEach(function (s) { taken[U.localDate(s.end - WAKE_DAY_SHIFT, tz)] = true; });
    var all = live.concat(segs.filter(function (s) { return s.exp && !taken[U.localDate(s.end - WAKE_DAY_SHIFT, tz)]; }));
    var uniq = new Map();
    all.forEach(function (s) { uniq.set(s.start + "|" + s.stage, s); });
    inp.segments = Array.from(uniq.values()).map(function (s) { return [s.start, s.end, s.stage]; }).sort(U.cmp);
    return inp;
  }

  /** Normalise inputs: instants to microseconds, samples sorted (the engine expects sorted series). build() output
   *  (units "us") is already normalised and passes through. */
  function normalize(inp) {
    if (inp.units === "us") return inp;
    var out = Object.assign({}, inp, { units: "us" });
    out.nights = (inp.nights || []).map(function (n) { return Object.assign({}, n, { start: U.toUs(n.start), end: U.toUs(n.end) }); });
    out.segments = (inp.segments || []).map(function (s) { return Array.isArray(s) ? [U.toUs(s[0]), U.toUs(s[1]), s[2]] : [U.toUs(s.start), U.toUs(s.end), s.stage]; });
    KINDS.forEach(function (k) { out[k] = samplePairs(inp[k]); });
    out.daily_rhr = inp.daily_rhr || {};
    out.shortcut_dates = inp.shortcut_dates || [];
    out.non_watch_dates = inp.non_watch_dates || [];
    out.muscle = inp.muscle || (inp.muscle_sessions ? M.daily_muscle_load(inp.muscle_sessions) : {});
    out.muscle_error = inp.muscle_error || null;
    out.hrv_kind = inp.hrv_kind || P.HRV_KIND;
    out.birth_date = inp.birth_date || null;
    return out;
  }

  function segments_in(segments, start, end) {
    return segments.filter(function (s) { return s[0] >= start && s[1] <= end; });
  }

  return { build: build, normalize: normalize, isWatchSleep: isWatchSleep, segments_in: segments_in, WAKE_DAY_SHIFT: WAKE_DAY_SHIFT };
})(OBS.util, OBS.params, OBS.muscle);
