// The cases of packages/openbodyscore/tests/golden_cases.py, call for call, against the JS bundle. `data` is that file's
// generate() output (dumped by cases.py: instants in milliseconds). The result must match tests/golden/scores.json and
// healthspan_v12.json. Keep this file in step with golden_cases.py.
"use strict";

module.exports = function goldenCases(O, raw) {
  const U = O.util, S = O.sleep, R = O.recovery, L = O.strain, P = O.params, HS = O.healthspan, H = O.healthspan_params;
  const H12 = O.healthspan_params_v12, X = O.hrv, T = O.stress;
  const TZ = U.tzOf(600), FIRST = "2026-08-01", DAYS = 60, BIRTH = "1990-01-01";
  const us = (x) => U.toUs(x);
  const iso = (t) => (t === null ? null : U.isoformat(t, TZ));
  const at = (d, hh, mm) => U.combine(d, hh * U.US_HOUR + (mm || 0) * U.US_MIN, TZ);
  const pairs = (xs) => xs.map((p) => [us(p[0]), p[1]]);
  const data = {
    nights: raw.nights.map((n) => ({ wake_date: n.wake_date, segments: n.segments.map((s) => [us(s[0]), us(s[1]), s[2]]),
      hr: pairs(n.hr), hrv: pairs(n.hrv), resp: n.resp, spo2: n.spo2, temp: n.temp, beats: n.beats })),
    days: raw.days.map((d) => ({ date: d.date, hr: pairs(d.hr), steps: pairs(d.steps), hrv: pairs(d.hrv), sessions: d.sessions,
      workout: d.workout ? [us(d.workout[0]), us(d.workout[1])] : null })),
  };
  const error = (fn) => { try { fn(); } catch (e) { return ["ValueError", e.message]; } return null; };
  const mins = (segs, pred) => U.pySum(segs.filter(pred).map((s) => ((s[1] - s[0]) / 1e6) / 60));

  function strainTrimps() {
    const hist = new L.HrHistogram(), window = [], rows = [];
    data.days.forEach((d, i) => {
      const samples = data.nights[i].hr.concat(d.hr);
      const c = L.counter(samples.map((s) => Math.trunc(s[1])));
      hist.add(c); window.push(c);
      if (window.length > 30) hist.add(window.shift(), -1);
      const p995 = hist.percentile(P.HRMAX_PERCENTILE), age = L.age_on(BIRTH, d.date), hrMax = L.hrmax(age, p995);
      const minutes = L.sample_minutes(samples.map((s) => s[0]));
      const tz = L.edwards_trimp(samples, minutes, hrMax);
      rows.push({ age, p995, hrmax: hrMax, minutes_total: U.pySum(minutes), trimp: tz[0], zones: tz[1], strain: L.strain(tz[0]),
        strain_k100: L.strain(tz[0], 100.0) });
    });
    return rows;
  }

  function sleepCases() {
    const durations = {};
    [3, 4.5, 5, 6, 6.99, 7, 8, 9, 9.5, 10, 11, 12, 14].forEach((h) => { durations[String(h)] = S.duration_score(h); });
    const out = { duration: durations,
      stages: [[0, 0], [45, 30], [90, 90], [120, 10], [-5, 200]].map((a) => S.stages_score(a[0], a[1])),
      invalid: [error(() => S.sleep_score(0, 0, 0, at(FIRST, 23), [], 0, [], TZ)), error(() => S.sleep_score(1441, 0, 0, at(FIRST, 23), [], 0, [], TZ))],
      nights: [] };
    const bedtimes = [], actual = [], trimps = strainTrimps();
    data.nights.forEach((n, i) => {
      const segs = n.segments, w = S.waso(segs);
      const asleep = mins(segs, (s) => s[2] !== "awake"), deep = mins(segs, (s) => s[2] === "deep"), rem = mins(segs, (s) => s[2] === "rem");
      const bed = segs.find((s) => s[2] !== "awake")[0];
      const history = bedtimes.slice(-P.CONSISTENCY_WINDOW_NIGHTS);
      const debt = S.sleep_debt(actual.slice().reverse().slice(0, P.DEBT_NIGHTS).concat(i % 7 === 3 ? [null] : []));
      const prevStrain = i ? trimps[i - 1].strain : null;
      const need = S.sleep_need(debt, prevStrain);
      out.nights.push({ waso: [w[0], w[1]], score: S.sleep_score(asleep, deep, rem, bed, history, w[0], w[1], TZ),
        hours_past_noon: S.hours_past_noon(bed, TZ), debt, need, performance: S.performance(asleep, need.need),
        consistency_empty: S.consistency_score([], bed, TZ),
        interruptions: S.interruptions_score(w[0] + 30 * (i % 3), w[1].concat(Array(i % 4).fill(6.0))) });
      bedtimes.push(bed);
      actual.push(i % 11 !== 5 ? asleep : null);
    });
    out.waso_only_awake = S.waso([[at(FIRST, 1), at(FIRST, 2), "awake"]]);
    return out;
  }

  function recoveryCases() {
    const sp = R.lowest_window([[at(FIRST, 1), 60.0], [at(FIRST, 2), 55.0]], at(FIRST, 0), at(FIRST, 3));
    const out = { errors: [error(() => R.recovery_score({ rhr: 1.0 }))],
      baseline_short: R.baseline(Array(P.MIN_BASELINE_NIGHTS - 1).fill(1.0)),
      zscore: [[60, 5], [40, 0.01], [500, 1], [-500, 1]].map((a) => R.zscore(a[0], 50, a[1], 0.05)),
      sparse: [sp[0], iso(sp[1])], empty: R.lowest_window([], at(FIRST, 0), at(FIRST, 3)),
      spo2_empty: R.spo2_flag([]), spo2_low: [R.spo2_flag([95.0, 89.0, 88.5, 97.0]), R.spo2_flag([93.0, 93.5, 95.0])],
      temp_short: R.wrist_temp_flag(36.5, Array(P.MIN_TEMP_BASELINE - 1).fill(35.5)), nights: [] };
    const hist = { hrv: [], rhr: [], resp: [], temp: [] };
    data.nights.forEach((n, i) => {
      const start = n.segments[0][0], end = n.segments[n.segments.length - 1][1];
      const hn = R.night_hrv_ln(n.hrv, start, end), lw = R.lowest_window(n.hr, start, end);
      const meanOnly = R.lowest_window_mean(n.hr, start, end);
      const resp = n.resp.slice().sort((a, b) => a - b)[Math.floor(n.resp.length / 2)];
      const z = {}, bases = {};
      [["hrv", hn[0]], ["rhr", lw[0]], ["resp_rate", resp]].forEach(([key, x]) => {
        const b = R.baseline(hist[key === "resp_rate" ? "resp" : key].slice(-P.BASELINE_DAYS));
        bases[key] = b;
        if (b && x !== null) z[key] = P.RECOVERY_SIGNS[key] * R.zscore(x, b[0], b[1], P.SD_FLOOR[key]);
      });
      if (i % 4 === 0 && "hrv" in z) z.sleep_performance = 0.3 * ((i % 9) - 4) / 4;
      out.nights.push({ hrv_ln: [hn[0], hn[1]], rhr: [lw[0], iso(lw[1])], rhr_mean: meanOnly, baselines: bases, z,
        score: "hrv" in z ? R.recovery_score(z) : null,
        illness: R.illness_warning(lw[0], bases.rhr ? bases.rhr[0] : null, hn[0], bases.hrv ? bases.hrv[0] : null),
        illness_forced: R.illness_warning(70.0, 60.0, Math.log(30), Math.log(50)),
        temp: R.wrist_temp_flag(n.temp, hist.temp.slice(-P.BASELINE_DAYS)), spo2: R.spo2_flag(n.spo2) });
      hist.hrv.push(hn[0]); hist.rhr.push(lw[0]); hist.resp.push(resp); hist.temp.push(n.temp);
    });
    return out;
  }

  function strainCases() {
    const rows = strainTrimps(), trimps = rows.map((r, i) => (i % 13 === 12 ? null : r.trimp));
    return { days: rows, load: L.training_load(trimps), load_empty: L.training_load([null, null]),
      formula: [20, 36.5, 70].map(L.hrmax_formula), age_on: [L.age_on("2000-02-29", "2026-02-28"), L.age_on("2000-02-29", "2026-03-01")],
      zone_weight: [80, 95, 114, 133, 152, 171, 200].map((hr) => L.zone_weight(hr, 190)),
      percentile_empty: new L.HrHistogram().percentile(99.5) };
  }

  function muscleCases() {
    const sessions = [];
    data.days.forEach((d) => d.sessions.forEach((s) => sessions.push(s)));
    sessions.push(Object.assign({}, sessions[0], { name: "Second session" }));
    return O.muscle.daily_muscle_load(sessions);
  }

  function healthspanCases() {
    const asOf = U.addDays(FIRST, DAYS - 1), vals = {};
    data.days.forEach((d) => { vals[d.date] = U.pySum(d.steps.map((s) => s[1])); });
    vals[U.addDays(FIRST, -400)] = 1e9;
    const hist = [];
    for (let k = 40; k >= 0; k--) hist.push([U.addDays(asOf, -7 * k), k === 5 ? null : 0.8 - 0.03 * k + 0.1 * Math.sin(k)]);
    return {
      weighted_mean: HS.weighted_mean(vals, asOf), weighted_mean_empty: HS.weighted_mean({}, asOf),
      interp: [50, 65.1, 70, 81, 88, 95].map((x) => HS.interp_log_hr(x, H.SRI_KNOTS)),
      self_report: [4, 6.5, 9].map((h) => HS.self_report_hours(h)),
      sleep: [4, 5.5, 6.5, 7, 8, 10].map((h) => HS.hr_sleep_duration(h)),
      regularity: [55, 70, 81, 92].map((x) => HS.hr_regularity(x)),
      steps: [35, 60, 72].map((a) => [2000, 5000, 8078, 12000].map((s) => HS.hr_steps(s, a))),
      mvpa: [0, 75, 150, 300, 900].map((x) => HS.hr_mvpa(x)),
      strength: [0, 20, 40, 90, 200].map((x) => HS.hr_strength(x)),
      vo2_norm: ["male", "female", "other"].map((s) => [18, 25, 36.5, 90].map((a) => HS.vo2_norm(a, s))),
      vo2_pct: [[25, "male"], [52, "female"]].map((as) => [10, 30, 40.2, 70].map((v) => HS.vo2_percentile(v, as[0], as[1])))
        .concat([HS.vo2_percentile(40, 30, "other")]),
      vo2: [20, 35, 39.7, 45, 60].map((v) => HS.hr_vo2(v, 39.7)),
      rhr: [40, 45, 65, 80].map((x) => HS.hr_rhr(x)),
      bmi: [16, 22, 25, 31, 50].map((x) => HS.hr_bmi(x)),
      whtr: [0.4, 0.5, 0.55, 0.9].map((x) => HS.hr_whtr(x)),
      years: [0.2, 0.9, 1.0, 1.2, 5.0].map((x) => HS.years_from_hr(x)),
      combine: [[], [1.0, -0.5], [4, 4, 4, 4, 4]].map((ys) => HS.combine(ys.map((y) => ({ years: y })))),
      chrono_age: HS.chrono_age(BIRTH, asOf),
      smooth: HS.smooth(hist), smooth_2: HS.smooth(hist, 2),
      pace: HS.pace(hist, asOf), pace_short: HS.pace(hist.slice(-6), asOf),
      pace_flat: HS.pace(Array(9).fill([asOf, 0.5]), asOf),
      pace_past: HS.pace(hist, U.addDays(asOf, -70)),
    };
  }

  function hrvCases() {
    const strip = (r) => { const o = Object.assign({}, r); delete o.artifact_ratio; return o; };
    const out = [];
    data.nights.slice(0, 20).forEach((n) => n.beats.forEach((bg) => {
      const r = X.analyze(bg[0], bg[1]);
      out.push(Object.assign(strip(r), { artifact_ratio: r.artifact_ratio, no_gaps: strip(X.analyze(bg[0])) }));
    }));
    return { readings: out, rr: X.rr_intervals([0, 0.8, 0.8, 1.7, 2.5], [false, false, false, true, false]),
      clean_none: X.clean([null, 100.0, 5000.0]),
      clock: X.beats_from_clock(["23:59:58.10", "23:59:59.05", "bad", "00:00:00.01", "00:00:00.95", null]),
      parse: ["01:02:03.5", "x", null].map(X.parse_clock), short: strip(X.analyze([0.0, 1.0])) };
  }

  function stressCases() {
    const sleep = data.nights.map((n) => [n.segments[0][0], n.segments[n.segments.length - 1][1]]);
    const workouts = data.days.filter((d) => d.workout).map((d) => d.workout);
    const hr = [], steps = [], hrv = [];
    data.nights.forEach((n, i) => n.hr.concat(data.days[i].hr).forEach((p) => hr.push(p)));
    data.days.forEach((d) => { d.steps.forEach((p) => steps.push(p)); d.hrv.forEach((p) => hrv.push(p)); });
    [hr, steps, hrv].forEach((xs) => xs.sort(U.cmp));
    const calm = T.calm_samples(hr, sleep, workouts, steps), calmHrv = T.calm_samples(hrv, sleep, workouts, steps);
    const byDay = {};
    calm.forEach((s) => { const d = U.localDate(s[0], TZ); (byDay[d] = byDay[d] || []).push(s); });
    const days = data.days.map((d, i) => {
      const base = [];
      for (let k = 1; k <= T.BASELINE_DAYS; k++) (byDay[U.addDays(d.date, -k)] || []).forEach((s) => base.push(s[1]));
      const hrBase = T.robust_baseline(base), hrvBase = i % 2 ? [3.9, 0.2] : null;
      return { base: hrBase, day: hrBase === null ? null : T.score_day(byDay[d.date] || [], calmHrv, d.date, TZ, hrBase, hrvBase) };
    });
    const isoPairs = (xs) => xs.map((s) => [iso(s[0]), s[1]]);
    return { calm_n: calm.length, calm_first: isoPairs(calm.slice(0, 20)), calm_hrv: isoPairs(calmHrv.slice(0, 20)), days,
      bands: [null, 0.0, 0.99, 1.0, 2.5].map((x) => T.band(x)), empty_day: T.score_day([], [], FIRST, TZ, [60.0, 5.0], null) };
  }

  function v12Cases() {
    return {
      steps: [35, 60, 72].map((a) => [2000, 4000, 6000, H12.STEPS_ANCHOR_UNDER60, 12000, 20000].map((s) => HS.hr_steps(s, a, H12))),
      moderate_equiv: [2.0, 2.99, 3.0, 3.5, 6.0, 8.0].map((met) => HS.moderate_equiv_minutes(30, met, H12)),
      step_equiv: [0, 30, 60].map((m) => HS.step_equivalents(m, H12.STRENGTH_MET, H12))
        .concat(H12.NONSTEP_TYPES.slice().sort().map((t) => HS.step_equivalents(45, H12.WORKOUT_MET[t], H12))),
      activity: [[4000, null, 30], [6000, 0, 30], [6000, 40, 30], [9000, 200, 30], [5000, 40, 70]].map((a) => HS.hr_activity(a[0], a[1], a[2], H12)),
      years: [0.3, 0.8, 1.0, 1.3, 3.0].map((x) => HS.years_from_hr(x, H12.ACTIVITY_CAP_YEARS, H12)),
      version: H12.ALGO_VERSION,
    };
  }

  return {
    scores: { sleep: sleepCases(), recovery: recoveryCases(), strain: strainCases(), muscle: muscleCases(),
      healthspan: healthspanCases(), hrv: hrvCases(), stress: stressCases(),
      versions: { scores: P.ALGO_VERSION, healthspan: H.ALGO_VERSION, hrv: X.ALGO, stress: T.STRESS_VERSION } },
    v12: v12Cases(),
  };
};
