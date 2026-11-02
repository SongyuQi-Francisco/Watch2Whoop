/* stress_cards (2.0.0 package M2, docs/ios-native-2.0.md): stress, the stress cards and the cardio view on the phone.
Port of the pure parts of body_engine
- insights/stress.py `merge_app` (the sync mode `app`'s sample merge: the app's own samples first, the others only on days
  without any of that type) and `days_local` (the phone's local tables -> merge -> score_range, the stress_daily rows);
- insights/stress.py `cards` (resilience, body battery, heart-rate recovery as of a day, exactly as trends/series.compute);
- cardio/views.py `compute` (GET /cardio: HRR60 30-day means, recovery index per night).
The day-by-day stress itself is OBS.stress_days.score_range (13_stress_days.js), the cards' formulas OBS.trends (10_trends.js).
Instants in `stressLocal` are UTC seconds (the phone's local tables); elsewhere epoch milliseconds or ISO strings, as api.js.
The heart-rate-recovery parameters are cardio/hrr.py's (sources there), compared with the Python by the golden test.
*/
OBS.stress_cards = (function (U, T, SD, ST) {
  "use strict";
  // insights/stress.STRESS_CARDS_VERSION: this module's version + the stress algorithm it runs
  var STRESS_CARDS_VERSION = "stress-cards-1+" + ST.STRESS_VERSION;
  // cardio/hrr.py ALGO, TREND_DAYS, PEAK_MIN_PCT
  var P = { HRR_ALGO: "hrr60-v1", TREND_DAYS: 30, PEAK_MIN_PCT: 0.75 };
  // the phone's sample groups -> OW series codes (insights/stress.LOCAL_CODES)
  var CODES = { hr: "heart_rate", steps: "steps", hrv: "heart_rate_variability_sdnn" };
  var TYPES = ["heart_rate", "steps", "heart_rate_variability_sdnn"];

  function nn(v) { return v !== null && v !== undefined; }
  function sec(t) { return Math.round(t * 1e6); }

  /** {code: [[us, value, source]]} (app, time order) + {code: [[us, value]]} (other) -> {code: [[us, value]]} sorted. */
  function merge_app(app, other, tz) {
    tz = U.tzOf(tz);
    var out = {};
    TYPES.forEach(function (code) {
      var mine = app[code] || [], have = {}, xs = new Map();
      mine.forEach(function (s) {
        have[U.localDate(s[0], tz)] = true;
        xs.set(code === "steps" ? s[0] + "|" + s[2] : String(s[0]), [s[0], s[1]]);
      });
      var all = Array.from(xs.values());
      (other[code] || []).forEach(function (x) { if (!have[U.localDate(x[0], tz)]) all.push([x[0], x[1]]); });
      out[code] = all.sort(U.cmp);
    });
    return out;
  }

  function spans(xs) {
    var seen = new Map();
    (xs || []).forEach(function (s) { var a = sec(s[0]), b = sec(s[1]); seen.set(a + "|" + b, [a, b]); });
    return Array.from(seen.values()).sort(U.cmp);
  }

  /** insights/stress.days_local: src = {tz, start, end, app, other, sleep, workouts, hrv_night, shortcut}, t in UTC seconds. */
  function days_local(src) {
    var app = {}, other = {};
    Object.keys(src.app || {}).forEach(function (k) {
      if (!CODES[k]) return;
      app[CODES[k]] = src.app[k].map(function (x, i) { return [sec(x[0]), x[1], x.length > 2 ? x[2] : null, i]; })
        .sort(function (a, b) { return a[0] - b[0] || a[3] - b[3]; })   // stable, as Python's sorted(key=time)
        .map(function (x) { return [x[0], x[1], x[2]]; });
    });
    Object.keys(src.other || {}).forEach(function (k) {
      if (!CODES[k]) return;
      other[CODES[k]] = src.other[k].map(function (x) { return [sec(x[0]), x[1]]; }).sort(U.cmp);
    });
    var s = merge_app(app, other, src.tz);
    return SD.score_range(src.tz, src.start, src.end, spans(src.sleep), spans(src.workouts), s.heart_rate, s.steps,
      s.heart_rate_variability_sdnn, src.hrv_night || {}, src.shortcut || []);
  }

  /** insights/stress.cards: data = {scores, stress, hrr_apple, hrr_workouts}; dates 'YYYY-MM-DD'. */
  function cards(data, end, today, tz, now_hour) {
    tz = U.tzOf(tz);
    var rows = {}, recovery = {}, perf = {}, stress = {}, st = {};
    (data.scores || []).forEach(function (r) { rows[r.date] = r; });
    Object.keys(rows).forEach(function (d) {
      if (nn(rows[d].recovery)) recovery[d] = rows[d].recovery;
      if (nn(rows[d].sleep_performance)) perf[d] = rows[d].sleep_performance;
    });
    (data.stress || []).forEach(function (r) { stress[r.date] = r; });
    Object.keys(stress).forEach(function (d) { if (nn(stress[d].score)) st[d] = U.round(stress[d].score, 2); });
    var er = rows[end], es = stress[end];
    var wakeAt = er && er.details && er.details.sleep ? er.details.sleep.wake_time : null;
    var wake = wakeAt ? U.local(U.toUs(wakeAt), tz).hour : null;
    var until = end === today ? (nn(now_hour) ? now_hour : U.local(Date.now() * 1000, tz).hour) : 23;
    var hrr = {};
    Object.keys(data.hrr_apple || {}).forEach(function (d) { hrr[d] = data.hrr_apple[d]; });
    Object.keys(data.hrr_workouts || {}).forEach(function (d) { if (!(d in hrr)) hrr[d] = data.hrr_workouts[d]; });
    var last = null;
    Object.keys(hrr).forEach(function (d) { if (d <= end && (last === null || d > last)) last = d; });
    return {
      resilience: T.resilience(recovery, perf, st, end),
      body_battery: er ? T.body_battery(nn(er.recovery) ? er.recovery : null, nn(er.sleep_performance) ? er.sleep_performance : null,
        wake, (es || {}).hours || [], nn(er.strain) ? er.strain : null, until) : null,
      hr_recovery: last === null ? null : { bpm: U.round(hrr[last], 1), date: last, level: T.hr_recovery_level(hrr[last]) }
    };
  }

  // ---------------------------------------------------------------- statistics.mean (exact rational mean, correctly rounded)
  function bitLength(b) { return b === BigInt(0) ? 0 : b.toString(2).length; }

  /** num / den (BigInt, den > 0) -> the nearest double (ties to even), as float(Fraction). */
  function fracToFloat(num, den) {
    if (num === BigInt(0)) return 0;
    var neg = num < 0;
    if (neg) num = -num;
    var shift = 55 - (bitLength(num) - bitLength(den)), n2 = num, d2 = den;
    if (shift >= 0) n2 = num << BigInt(shift); else d2 = den << BigInt(-shift);
    var q = n2 / d2, sticky = n2 % d2 !== BigInt(0), extra = bitLength(q) - 53;
    var low = q & ((BigInt(1) << BigInt(extra)) - BigInt(1)), half = BigInt(1) << BigInt(extra - 1);
    q = q >> BigInt(extra);
    if (low > half || (low === half && (sticky || (q & BigInt(1)) === BigInt(1)))) q += BigInt(1);
    var v = Number(q) * Math.pow(2, extra - shift);
    return neg ? -v : v;
  }

  /** statistics.mean of numbers. */
  function mean(xs) {
    var rs = xs.map(U.exact), emin = 0, i, s = BigInt(0);
    for (i = 0; i < rs.length; i++) if (rs[i].m && rs[i].e < emin) emin = rs[i].e;
    for (i = 0; i < rs.length; i++) s += rs[i].m << BigInt(rs[i].e - emin);
    return fracToFloat(s, BigInt(xs.length) << BigInt(-emin));
  }

  function mean1(xs) { return xs.length ? U.round(mean(xs), 1) : null; }

  /** cardio/views.compute: data = {tz, hrr: [{workout_id, local_date, end_at, type, name_zh, duration_min, peak_hr, hr_60s,
   *  hrr60, apple_hrr60, valid, reason}] by end_at, scores: [{date, details}] by date}. */
  function cardio(data, start, end) {
    var tz = U.tzOf(data.tz), rows = data.hrr || [];
    var lastFrom = U.addDays(end, -P.TREND_DAYS), prevFrom = U.addDays(end, -2 * P.TREND_DAYS);
    var last = rows.filter(function (r) { return r.valid && r.local_date > lastFrom; }).map(function (r) { return r.hrr60; });
    var prev = rows.filter(function (r) { return r.valid && prevFrom < r.local_date && r.local_date <= lastFrom; })
      .map(function (r) { return r.hrr60; });
    var workouts = rows.filter(function (r) { return r.local_date >= start; }).map(function (r) {
      return { workout_id: r.workout_id, date: r.local_date, type: r.type, name_zh: r.name_zh, duration_min: r.duration_min,
        end_at: U.isoformat(U.toUs(r.end_at), tz, "minutes"), peak_hr: r.peak_hr, hr_60s: r.hr_60s, hrr60: r.hrr60,
        apple_hrr60: r.apple_hrr60, valid: r.valid, reason: r.reason };
    });
    var nights = [];
    (data.scores || []).forEach(function (r) {
      if (!(start <= r.date && r.date <= end)) return;
      var det = r.details || {}, rec = det.recovery || {}, sl = det.sleep || {}, off = rec.rhr_low_offset_min;
      if (!nn(off)) return;
      var pct = null;
      if (sl.bedtime && sl.wake_time) {
        var span = (U.toUs(sl.wake_time) - U.toUs(sl.bedtime)) / 1e6 / 60;
        pct = span > 0 ? U.round(Math.min(Math.max(off / span, 0), 1) * 100) : null;
      }
      nights.push({ date: r.date, offset_min: off, pct: pct });
    });
    var pcts = nights.filter(function (n) { return n.pct !== null; }).map(function (n) { return n.pct; });
    return { start: start, end: end, algo: P.HRR_ALGO,
      hrr: { workouts: workouts, last30_mean: mean1(last), last30_n: last.length, prev30_mean: mean1(prev), prev30_n: prev.length,
        peak_min_pct: P.PEAK_MIN_PCT },
      recovery_index: { nights: nights, median_pct: pcts.length ? U.round(U.median(pcts)) : null } };
  }

  // ---------------------------------------------------------------- entry points (api.js style: stamped)
  function stamp(x) { x.algo_version = STRESS_CARDS_VERSION; x.bundle_version = OBS.VERSION; return x; }
  OBS.ALGO_VERSIONS.stress_cards = STRESS_CARDS_VERSION;
  /** stress_daily rows of [src.start, src.end] (see days_local); give samples from start − 14 days, hrv_night from start − 60. */
  OBS.stressLocal = function (src) { return days_local(src).map(stamp); };
  /** {resilience, body_battery, hr_recovery} as of opts.end. opts: {end, today, tz, now_hour}. */
  OBS.stressCards = function (data, opts) { return stamp(cards(data, opts.end, opts.today, opts.tz, opts.now_hour)); };
  /** The /cardio document (own data: never `hidden`). opts: {start, end}. */
  OBS.cardioView = function (data, opts) { return stamp(cardio(data, opts.start, opts.end)); };

  return { STRESS_CARDS_VERSION: STRESS_CARDS_VERSION, params: P, merge_app: merge_app, days_local: days_local, cards: cards,
    cardio: cardio, mean: mean };
})(OBS.util, OBS.trends, OBS.stress_days, OBS.stress);
