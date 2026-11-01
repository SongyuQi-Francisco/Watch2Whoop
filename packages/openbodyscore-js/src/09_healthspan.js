/* healthspan: body age and pace of aging. Port of openbodyscore/healthspan.py (+ tip() from body_engine insights/healthspan.py).

1. Each metric is averaged over the last 180 days (the last 30 days carry day weight ×2).
2. Each metric becomes an all-cause mortality hazard ratio via the literature's dose-response curve (healthspan_params).
3. Δ years = ln(HR) / ln2 × 8 (Gompertz), capped at ±4 years per item; the sum × 0.6, total capped at ±10 years.
4. Pace of aging: weekly Δ, 4-week moving average, least squares over the last 26 weeks: pace = 1 + dΔ/dt, limited to −1 ~ 3.
Values keyed by date are plain objects {'YYYY-MM-DD': value}.
*/
OBS.healthspan = (function (U, H, H12) {
  "use strict";

  /** values: {date: value} -> [weighted mean | null, number of data days] within the window ending as_of. */
  function weighted_mean(values, as_of, params) {
    var p = params || H;
    var lo = U.addDays(as_of, -(p.WINDOW_DAYS - 1)), recent = U.addDays(as_of, -(p.RECENT_DAYS - 1));
    var num = 0.0, den = 0.0, n = 0;
    Object.keys(values).forEach(function (d) {
      var v = values[d];
      if (lo <= d && d <= as_of && v !== null && v !== undefined) {
        var w = d >= recent ? p.RECENT_WEIGHT : 1.0;
        num += w * v; den += w; n += 1;
      }
    });
    return [den ? num / den : null, n];
  }

  /** Piecewise-linear interpolation of ln(HR) over knots [[x, HR]]; flat beyond the ends. */
  function interp_log_hr(x, knots) {
    if (x <= knots[0][0]) return knots[0][1];
    if (x >= knots[knots.length - 1][0]) return knots[knots.length - 1][1];
    for (var i = 0; i + 1 < knots.length; i++) {
      var x0 = knots[i][0], h0 = knots[i][1], x1 = knots[i + 1][0], h1 = knots[i + 1][1];
      if (x0 <= x && x <= x1) {
        var f = (x - x0) / (x1 - x0);
        return Math.exp(Math.log(h0) + f * (Math.log(h1) - Math.log(h0)));
      }
    }
    throw new Error("unreachable");
  }

  function rel(x, knots, anchor) { return interp_log_hr(x, knots) / interp_log_hr(anchor, knots); }

  function self_report_hours(measured, params) { var p = params || H; return p.SLEEP_SELF_REPORT[0] + p.SLEEP_SELF_REPORT[1] * measured; }

  function hr_sleep_duration(hours_measured, params) {
    var p = params || H, h = self_report_hours(hours_measured, p);
    if (h < p.SLEEP_NADIR_H) return Math.pow(p.SLEEP_RR_PER_H_SHORT, p.SLEEP_NADIR_H - h);
    return Math.pow(p.SLEEP_RR_PER_H_LONG, h - p.SLEEP_NADIR_H);
  }

  function hr_regularity(sri, params) { var p = params || H; return rel(sri, p.SRI_KNOTS, p.SRI_ANCHOR); }

  function hr_steps(steps, age, params) {
    var p = params || H;
    if (age < 60) return rel(steps, p.STEPS_KNOTS_UNDER60, p.STEPS_ANCHOR_UNDER60);
    return rel(steps, p.STEPS_KNOTS_60PLUS, p.STEPS_ANCHOR_60PLUS);
  }

  function hr_mvpa(m, params) { var p = params || H; return rel(m, p.MVPA_KNOTS, p.MVPA_ANCHOR); }
  function hr_strength(m, params) { var p = params || H; return rel(m, p.STRENGTH_KNOTS, p.STRENGTH_ANCHOR); }

  /** v1.2: minutes counted like a questionnaire: moderate (3–6 MET) × 1, vigorous (>= 6) × VIGOROUS_FACTOR, light 0. */
  function moderate_equiv_minutes(minutes, met, params) {
    var p = params || H12;
    if (met < p.MODERATE_MET) return 0.0;
    return minutes * (met >= p.VIGOROUS_MET ? p.VIGOROUS_FACTOR : 1.0);
  }

  /** v1.2: steps a walk / run of the same MET would take in those minutes (CADENCE-Adults); light activity = 0. */
  function step_equivalents(minutes, met, params) {
    var p = params || H12;
    if (met < p.MODERATE_MET) return 0.0;
    var k = p.CADENCE_MET_KNOTS, m0 = k[0][0], s0 = k[0][1], m1 = k[1][0], s1 = k[1][1];
    return minutes * (s0 + (s1 - s0) * (Math.min(met, m1) - m0) / (m1 - m0));
  }

  /** v1.2 activity domain -> {hr, hr_volume, hr_strength}. */
  function hr_activity(steps_equiv, strength_min_week, age, params) {
    var p = params || H12;
    var hv = hr_steps(steps_equiv, age, p);
    var hs = strength_min_week === null || strength_min_week === undefined ? 1.0 : hr_strength(strength_min_week, p);
    return { hr: hv * hs, hr_volume: hv, hr_strength: hs };
  }

  /** Linear interpolation at age-band midpoints (value a number or a list). */
  function by_age(table, age) {
    var pts = Object.keys(table).map(Number).sort(function (a, b) { return a - b; }).map(function (a) { return [a, table[a]]; });
    if (age <= pts[0][0]) return pts[0][1];
    if (age >= pts[pts.length - 1][0]) return pts[pts.length - 1][1];
    for (var i = 0; i + 1 < pts.length; i++) {
      var a0 = pts[i][0], v0 = pts[i][1], a1 = pts[i + 1][0], v1 = pts[i + 1][1];
      if (a0 <= age && age <= a1) {
        var f = (age - a0) / (a1 - a0);
        if (Array.isArray(v0)) return v0.map(function (x0, j) { return x0 + f * (v1[j] - x0); });
        return v0 + f * (v1 - v0);
      }
    }
    return null;
  }

  function vo2_norm(age, sex, params) { var p = params || H, t = p.VO2_MEDIAN[sex]; return t ? by_age(t, age) : null; }

  function vo2_percentile(vo2, age, sex, params) {
    var p = params || H, t = p.VO2_DECILES[sex];
    if (!t) return null;
    var dec = by_age(t, age);
    if (vo2 <= dec[0]) return 5.0;
    if (vo2 >= dec[dec.length - 1]) return 95.0;
    for (var i = 0; i + 1 < dec.length; i++)
      if (dec[i] <= vo2 && vo2 <= dec[i + 1]) return U.round(10 * (i + 1) + 10 * (vo2 - dec[i]) / (dec[i + 1] - dec[i]), 1);
    return null;
  }

  function hr_vo2(vo2, norm, params) {
    var p = params || H;
    var met = Math.max(-p.VO2_MAX_DELTA_MET, Math.min(p.VO2_MAX_DELTA_MET, (vo2 - norm) / 3.5));
    return Math.pow(p.VO2_RR_PER_MET, met);
  }

  function hr_rhr(rhr, params) { var p = params || H; return Math.pow(p.RHR_RR_PER_10, (Math.max(rhr, p.RHR_FLOOR) - p.RHR_ANCHOR) / 10); }
  function hr_bmi(bmi, params) { var p = params || H; return rel(bmi, p.BMI_KNOTS, p.BMI_ANCHOR); }
  function hr_whtr(whtr, params) { var p = params || H; return rel(whtr, p.WHTR_KNOTS, p.WHTR_ANCHOR); }

  /** cap: the ±years limit (default ITEM_CAP_YEARS; Infinity gives the uncapped value). */
  function years_from_hr(hr, cap, params) {
    var p = params || H;
    cap = cap === null || cap === undefined ? p.ITEM_CAP_YEARS : cap;
    var y = Math.log(hr) / Math.log(2) * p.MORTALITY_DOUBLING_YEARS;
    return Math.max(-cap, Math.min(cap, y));
  }

  function combine(items, params) {
    var p = params || H;
    var total = p.SHRINKAGE * U.pySum(items.map(function (i) { return i.years; }));
    return Math.max(-p.TOTAL_CAP_YEARS, Math.min(p.TOTAL_CAP_YEARS, total));
  }

  function chrono_age(birth, d) { return U.daysBetween(birth, d) / 365.2425; }

  /** history [[date, Δ | null]] -> [[date, mean of Δ over (d − weeks weeks, d]]], nulls skipped. */
  function smooth(history, weeks, params) {
    var p = params || H;
    weeks = weeks === null || weeks === undefined ? p.PACE_SMOOTH_WEEKS : weeks;
    var pts = history.filter(function (x) { return x[1] !== null && x[1] !== undefined; });
    return pts.map(function (x) {
      var lo = U.addDays(x[0], -7 * weeks);
      var xs = pts.filter(function (y) { return lo < y[0] && y[0] <= x[0]; }).map(function (y) { return y[1]; });
      return [x[0], U.pySum(xs) / xs.length];
    });
  }

  /** -> {pace, pace_raw, window_weeks}: pace = 1 + dΔ/dt over the last 26 smoothed weekly points (>= 8), clipped to −1 ~ 3. */
  function pace(history, as_of, params) {
    var p = params || H, none = { pace: null, pace_raw: null, window_weeks: null };
    var sm = smooth(history.filter(function (x) { return x[0] <= as_of; }), null, p).filter(function (x) { return x[0] <= as_of; });
    var pts = sm.slice(-p.PACE_WEEKS);
    if (pts.length < p.PACE_MIN_WEEKS) return none;
    var xs = pts.map(function (x) { return U.daysBetween(as_of, x[0]) / 365.2425; }), ys = pts.map(function (x) { return x[1]; });
    var mx = U.pySum(xs) / xs.length, my = U.pySum(ys) / ys.length;
    var sxx = U.pySum(xs.map(function (x) { return (x - mx) * (x - mx); }));
    if (sxx === 0) return none;
    var raw = 1 + U.pySum(xs.map(function (x, i) { return (x - mx) * (ys[i] - my); })) / sxx;
    return { pace: U.round(Math.max(p.PACE_RANGE[0], Math.min(p.PACE_RANGE[1], raw)), 2), pace_raw: U.round(raw, 2),
      window_weeks: pts.length };
  }

  /** The improvement tip of an item (product text, Chinese). goal_kg only for body_comp below the current weight. */
  function tip(key, years, value, goal_kg) {
    var T = H.TIPS;
    if (key === "sleep_duration") {
      if (Math.abs(years) < 0.25 || value === null || value === undefined) return T.sleep_ok;
      return self_report_hours(value) < H.SLEEP_NADIR_H ? T.sleep_short : T.sleep_long;
    }
    if (key === "body_comp" && years > 0 && goal_kg) return T.body_comp_goal.replace("{kg}", fmtG(goal_kg));
    return years <= 0 ? T[key][0] : T[key][1];
  }
  // Python f"{x:g}" for ordinary weights (6 significant digits, no trailing zeros)
  function fmtG(x) { var s = Number(x.toPrecision(6)).toString(); return s; }

  return { weighted_mean: weighted_mean, interp_log_hr: interp_log_hr, rel: rel, self_report_hours: self_report_hours,
    hr_sleep_duration: hr_sleep_duration, hr_regularity: hr_regularity, hr_steps: hr_steps, hr_mvpa: hr_mvpa,
    hr_strength: hr_strength, moderate_equiv_minutes: moderate_equiv_minutes, step_equivalents: step_equivalents,
    hr_activity: hr_activity, by_age: by_age, vo2_norm: vo2_norm, vo2_percentile: vo2_percentile, hr_vo2: hr_vo2,
    hr_rhr: hr_rhr, hr_bmi: hr_bmi, hr_whtr: hr_whtr, years_from_hr: years_from_hr, combine: combine,
    chrono_age: chrono_age, smooth: smooth, pace: pace, tip: tip };
})(OBS.util, OBS.healthspan_params, OBS.healthspan_params_v12);
