/* reports (2.0.0 package M5, docs/ios-native-2.0.md): the weekly / monthly report and the journal analysis on the phone.
Port of the pure parts of body_engine
- records/regression.py `ols_hac` (OLS with Newey-West standard errors, lags in calendar days), `bh` (Benjamini-Hochberg),
  `_inverse` (Gauss-Jordan with partial pivoting, `Singular`), `_norm_p` (two-sided normal p, with an erfc of its own:
  JavaScript has none);
- records/analysis.py `compare` (two-group means), `regress` (the second version, #118), `apply_bh`, `analyze_rows`,
  `rows_from`, `analyze_data` (GET /journal/analysis on plain data);
- insights/reports.py `compute` and its blocks (GET /reports): scores, stress, sleep regularity, training, weight, nutrition,
  journal findings, the change against the previous period.
Thresholds: records/analysis_params.py and insights/reports_params.py (sources there), mirrored in `params` below and compared
by the golden test. Dates are 'YYYY-MM-DD' strings; the texts are the server's (Chinese: they are read by the AI).
*/
OBS.regression = (function (U) {
  "use strict";
  // records/analysis_params.py (second version)
  var P = { HAC_LAG_DAYS: 7, FDR_Q: 0.05, REG_MIN_DF: 20, SINGULAR_TOL: 1e-9 };

  function Singular(col) { this.name = "Singular"; this.column = col; this.message = "Singular(" + col + ")"; }

  /** Gauss-Jordan inverse with partial pivoting of a symmetric positive (semi)definite matrix; throws Singular. */
  function inverse(a, tol) {
    var n = a.length, scale = 0, i, j, r, c;
    for (i = 0; i < n; i++) scale = Math.max(scale, Math.abs(a[i][i]));
    scale = scale || 1.0;
    var m = a.map(function (row, ri) {
      var out = row.slice();
      for (j = 0; j < n; j++) out.push(ri === j ? 1.0 : 0.0);
      return out;
    });
    for (c = 0; c < n; c++) {
      var p = c;   // Python max(range(c, n), key=abs): the first largest
      for (r = c + 1; r < n; r++) if (Math.abs(m[r][c]) > Math.abs(m[p][c])) p = r;
      if (Math.abs(m[p][c]) <= tol * scale) throw new Singular(c);
      var tmp = m[c]; m[c] = m[p]; m[p] = tmp;
      var piv = m[c][c];
      m[c] = m[c].map(function (v) { return v / piv; });
      for (r = 0; r < n; r++) {
        if (r !== c && m[r][c] !== 0.0) {
          var f = m[r][c], mc = m[c];
          m[r] = m[r].map(function (v, k) { return v - f * mc[k]; });
        }
      }
    }
    return m.map(function (row) { return row.slice(n); });
  }

  /** erfc(x) for x >= 0 with ~1e-15 relative accuracy: 1 − the series of erf below 2, the continued fraction above. */
  function erfc(x) {
    if (x < 0) return 2 - erfc(-x);
    if (x === 0) return 1;
    if (x < 2) {
      // erf(x) = 2/√π · e^(−x²) · Σ (2x²)^n x / (1·3·…·(2n+1)): positive terms, no cancellation
      var term = x, sum = x, x2 = 2 * x * x;
      for (var n = 1; n < 200; n++) {
        term = term * x2 / (2 * n + 1);
        sum += term;
        if (term < sum * 1e-17) break;
      }
      return 1 - 2 / Math.sqrt(Math.PI) * Math.exp(-x * x) * sum;
    }
    if (x > 27.3) return 0;   // below the smallest double
    // erfc(x) = e^(−x²)/√π · 1 / (x + (1/2) / (x + 1 / (x + (3/2) / (x + …)))), modified Lentz
    var tiny = 1e-300, f = x, C = f, D = 0;
    for (var k = 1; k < 5000; k++) {
      var ak = k / 2;
      D = x + ak * D; D = D === 0 ? tiny : D; D = 1 / D;
      C = x + ak / C; C = C === 0 ? tiny : C;
      var delta = C * D;
      f *= delta;
      if (Math.abs(delta - 1) < 1e-16) break;
    }
    return Math.exp(-x * x) / Math.sqrt(Math.PI) / f;
  }

  /** Two-sided p-value of a standard normal z. */
  function norm_p(z) { return erfc(Math.abs(z) / Math.sqrt(2)); }

  /** OLS of y on x (rows of regressors; an intercept is added) with Newey-West standard errors. days: 'YYYY-MM-DD' of
   * each observation. {n, k, coef: {name: {estimate, se, z, p, ci95}}}; throws Singular for a collinear design. */
  function ols_hac(days, y, x, names, lag_days, tol) {
    lag_days = lag_days === undefined ? P.HAC_LAG_DAYS : lag_days;
    tol = tol === undefined ? P.SINGULAR_TOL : tol;
    var n = y.length, i, j, a, b, t;
    names = ["intercept"].concat(names);
    var xs = x.map(function (row) { return [1.0].concat(row); }), k = names.length;
    var xtx = [];
    for (i = 0; i < k; i++) {
      xtx.push([]);
      for (j = 0; j < k; j++) xtx[i].push(U.pySum(xs.map(function (r) { return r[i] * r[j]; })));
    }
    var inv = inverse(xtx, tol);
    var xty = [];
    for (i = 0; i < k; i++) xty.push(U.pySum(xs.map(function (r, ri) { return r[i] * y[ri]; })));
    var beta = [];
    for (i = 0; i < k; i++) beta.push(U.pySum(inv[i].map(function (v, jj) { return v * xty[jj]; })));
    var resid = xs.map(function (r, ri) { return y[ri] - U.pySum(beta.map(function (bb, jj) { return bb * r[jj]; })); });

    // S = Σ_i Σ_j w(|t_i − t_j|) u_i u_j' with u_i = x_i e_i (only pairs within lag_days have weight)
    var order = [];
    for (i = 0; i < n; i++) order.push(i);
    order.sort(function (p, q) { return days[p] < days[q] ? -1 : days[p] > days[q] ? 1 : 0; });   // stable, as sorted()
    var ords = order.map(function (ii) { return U.dayNum(days[ii]); });
    var u = order.map(function (ii) { return xs[ii].map(function (c) { return c * resid[ii]; }); });
    var s = [];
    for (i = 0; i < k; i++) { s.push([]); for (j = 0; j < k; j++) s[i].push(0.0); }
    for (a = 0; a < n; a++) {
      for (b = a; b < n; b++) {
        var lag = ords[b] - ords[a];
        if (lag > lag_days) break;
        var w = lag === 0 && a === b ? 1.0 : 1.0 - lag / (lag_days + 1);
        if (w <= 0) continue;
        var ua = u[a], ub = u[b];
        for (i = 0; i < k; i++) {
          for (j = 0; j < k; j++) {
            var v = ua[i] * ub[j];
            s[i][j] += w * (a === b ? v : v + ub[i] * ua[j]);
          }
        }
      }
    }
    var corr = n > k ? n / (n - k) : Infinity;
    var tmp = [], cov = [];
    for (i = 0; i < k; i++) {
      tmp.push([]);
      for (j = 0; j < k; j++) {
        var acc = [];
        for (t = 0; t < k; t++) acc.push(inv[i][t] * s[t][j]);
        tmp[i].push(U.pySum(acc));
      }
    }
    for (i = 0; i < k; i++) {
      cov.push([]);
      for (j = 0; j < k; j++) {
        var acc2 = [];
        for (t = 0; t < k; t++) acc2.push(tmp[i][t] * inv[t][j]);
        cov[i].push(corr * U.pySum(acc2));
      }
    }
    var coef = {};
    names.forEach(function (name, ii) {
      var se = cov[ii][ii] > 0 ? Math.sqrt(cov[ii][ii]) : 0.0;
      var z = se > 0 ? beta[ii] / se : null;
      coef[name] = { estimate: beta[ii], se: se, z: z, p: z !== null ? norm_p(z) : null,
        ci95: se > 0 ? [beta[ii] - 1.96 * se, beta[ii] + 1.96 * se] : null };
    });
    return { n: n, k: k, coef: coef };
  }

  /** Benjamini-Hochberg adjusted p-values in the order given: q_(i) = min_{j >= i} p_(j) · m / j, capped at 1. */
  function bh(pvalues) {
    var m = pvalues.length, order = [], i;
    for (i = 0; i < m; i++) order.push(i);
    order.sort(function (a, b) { return pvalues[a] - pvalues[b]; });   // stable, as sorted()
    var q = pvalues.map(function () { return 0.0; }), running = 1.0;
    for (var rank = m; rank > 0; rank--) {
      i = order[rank - 1];
      running = Math.min(running, pvalues[i] * m / rank);
      q[i] = running;
    }
    return q;
  }

  return { params: P, Singular: Singular, inverse: inverse, erfc: erfc, norm_p: norm_p, ols_hac: ols_hac, bh: bh };
})(OBS.util);

OBS.analysis = (function (U, RG) {
  "use strict";
  // records/analysis_params.py
  var P = { MIN_N: 5, RELIABLE_N: 15, HAC_LAG_DAYS: RG.params.HAC_LAG_DAYS, FDR_Q: RG.params.FDR_Q,
    REG_MIN_DF: RG.params.REG_MIN_DF, SINGULAR_TOL: RG.params.SINGULAR_TOL };
  // name -> [column of the rows, transform, label, higher is better]
  var OUTCOMES = [
    ["next_recovery", "recovery", function (v) { return v; }, "次日恢复分（0–100）", true],
    ["next_rhr", "resting_heart_rate", function (v) { return v; }, "次日静息心率（bpm）", false],
    ["next_ln_hrv", "heart_rate_variability_sdnn", function (v) { return v > 0 ? Math.log(v) : null; }, "次日 ln HRV（SDNN）", true]
  ];
  var WEEKDAYS = ["tue", "wed", "thu", "fri", "sat", "sun"];   // dummies of the behaviour day's weekday; Monday is the reference
  var METHOD = "两组均值比较（行为日 D → D+1 的恢复分 / 指标）；没记录的日子不算；各 ≥ 5 天才出结果，≥ 15 天才算可信；"
    + "区间为正态近似，没有控制负荷、睡眠和星期几，只能当线索看。";
  var REGRESSION_METHOD = "第二版：OLS，结局 = D+1 的指标，自变量 = 行为（是 1 / 否 0），控制 D 的负荷（strain）、D→D+1 那晚的睡眠时长（小时）、"
    + "D 的星期几；Newey-West 标准误（滞后 " + P.HAC_LAG_DAYS + " 天）；只用协变量齐全的日子；同时看多个行为时按结局做 "
    + "Benjamini-Hochberg（FDR " + U.round(P.FDR_Q * 100) + "%），p_adj ≤ " + pyFloat(P.FDR_Q) + " 才算有方向。睡眠时长也可能是行为影响恢复的途径，"
    + "控制它以后看到的是「睡眠以外」的影响。";

  function nn(v) { return v !== null && v !== undefined; }
  /** str() of a Python float (the values formatted here are whole numbers or short decimals). */
  function pyFloat(x) { return Number.isInteger(x) ? x.toFixed(1) : String(x); }
  function r4(v) { return U.round(v, 4); }

  function group_stats(xs) {
    return { n: xs.length, mean: xs.length ? U.round(U.fmean(xs), 4) : null, sd: xs.length >= 2 ? U.round(U.stdev(xs), 4) : null };
  }

  function compare(yes, no, higher_is_better) {
    var res = { yes: group_stats(yes), no: group_stats(no) };
    if (yes.length < P.MIN_N || no.length < P.MIN_N) {
      res.status = "insufficient";
      res.message = "样本不足：是 " + yes.length + " 天、否 " + no.length + " 天，各需要至少 " + P.MIN_N + " 天";
      return res;
    }
    var diff = U.fmean(yes) - U.fmean(no), sy = U.stdev(yes), sn = U.stdev(no);
    var se = Math.sqrt(sy * sy / yes.length + sn * sn / no.length);
    var pooled = Math.sqrt(((yes.length - 1) * sy * sy + (no.length - 1) * sn * sn) / (yes.length + no.length - 2));
    res.status = "ok";
    res.diff_yes_minus_no = r4(diff);
    res.ci95_approx = se > 0 ? [r4(diff - 1.96 * se), r4(diff + 1.96 * se)] : null;
    res.welch_t = se > 0 ? U.round(diff / se, 3) : null;
    res.cohens_d = pooled > 0 ? U.round(diff / pooled, 3) : null;
    res.reliable = Math.min(yes.length, no.length) >= P.RELIABLE_N;
    if (se > 0 && Math.abs(diff / se) >= 1.96) res.direction = (diff > 0) === higher_is_better ? "better" : "worse";
    else res.direction = "unclear";
    return res;
  }

  function set_adjusted(res, q, m) {
    res.p_adj = r4(q);
    res.bh_tests = m;
    res.significant = q <= P.FDR_Q;
    res.direction = res.significant ? ((res.estimate > 0) === res._higher_is_better ? "better" : "worse") : "unclear";
  }

  function merge(a, b) { var o = {}, k; for (k in a) o[k] = a[k]; for (k in b) o[k] = b[k]; return o; }

  /** The second version for one outcome. obs: [{date, behavior (0/1), y, strain, sleep_h}] (complete days only). */
  function regress(obs, higher_is_better) {
    var yes = 0, i;
    for (i = 0; i < obs.length; i++) yes += obs[i].behavior;
    // Python: sum() of floats is a float ("7.0") unless there is nothing to sum (the int 0)
    var no = obs.length - yes, fmt = obs.length ? pyFloat : String;
    var res = { n: obs.length, n_yes: yes, n_no: no, controls: ["prev_strain", "sleep_hours", "weekday"], hac_lag_days: P.HAC_LAG_DAYS };
    if (yes < P.MIN_N || no < P.MIN_N) {
      return merge(res, { status: "insufficient",
        message: "样本不足：有完整协变量的是 " + fmt(yes) + " 天、否 " + fmt(no) + " 天，各需要至少 " + P.MIN_N + " 天" });
    }
    var wd = obs.map(function (o) { return U.weekday(o.date); });
    var present = [];
    for (i = 1; i < 7; i++) if (wd.indexOf(i) >= 0) present.push(i);   // a weekday with no observation has no dummy
    var names = ["behavior", "prev_strain", "sleep_hours"].concat(present.map(function (d) { return "wd_" + WEEKDAYS[d - 1]; }));
    var x = obs.map(function (o, oi) {
      return [o.behavior, o.strain, o.sleep_h].concat(present.map(function (d) { return wd[oi] === d ? 1.0 : 0.0; }));
    });
    if (obs.length - (names.length + 1) < P.REG_MIN_DF) {
      return merge(res, { status: "insufficient",
        message: "样本不足：" + obs.length + " 天、" + (names.length + 1) + " 个系数，剩余自由度要 ≥ " + P.REG_MIN_DF });
    }
    var fit;
    try {
      fit = RG.ols_hac(obs.map(function (o) { return o.date; }), obs.map(function (o) { return o.y; }), x, names);
    } catch (e) {
      if (e instanceof RG.Singular) return merge(res, { status: "collinear", message: "这个行为和星期几 / 负荷 / 睡眠完全重合，分不开" });
      throw e;
    }
    var b = fit.coef.behavior;
    if (b.p === null) return merge(res, { status: "collinear", message: "残差为 0，算不了标准误" });
    res.status = "ok";
    res.estimate = r4(b.estimate);
    res.se_hac = r4(b.se);
    res.z = U.round(b.z, 3);
    res.p = r4(b.p);
    res.ci95 = b.ci95.map(r4);
    res.covariates = { prev_strain: r4(fit.coef.prev_strain.estimate), sleep_hours: r4(fit.coef.sleep_hours.estimate) };
    res.reliable = Math.min(yes, no) >= P.RELIABLE_N;
    res._higher_is_better = higher_is_better;
    set_adjusted(res, b.p, 1);
    return res;
  }

  /** Benjamini-Hochberg across the behaviours of each outcome (results of analyze_rows with regression), in place. */
  function apply_bh(results) {
    OUTCOMES.forEach(function (oc) {
      var regs = [];
      results.forEach(function (a) {
        var o = a.outcomes[oc[0]];
        if (o && o.regression && o.regression.status === "ok") regs.push(o.regression);
      });
      var q = RG.bh(regs.map(function (r) { return r.p; }));
      regs.forEach(function (r, i) { set_adjusted(r, q[i], regs.length); });
    });
  }

  function clean(results) {
    results.forEach(function (a) {
      Object.keys(a.outcomes).forEach(function (k) { if (a.outcomes[k].regression) delete a.outcomes[k].regression._higher_is_better; });
    });
  }

  /** records/analysis.analyze_rows: rows = the item's entries in [start, end] by date with the next day's values. */
  function analyze_rows(item, rows, start, end, threshold, regression) {
    var outcomes = {};
    OUTCOMES.forEach(function (oc) {
      var name = oc[0], metric = oc[1], f = oc[2], higher = oc[4], yes = [], no = [], obs = [];
      rows.forEach(function (r) {
        if (!nn(r[metric])) return;
        var y = f(r[metric]);
        if (!nn(y)) return;
        (r.value > threshold ? yes : no).push(y);
        if (regression && nn(r.prev_strain) && nn(r.sleep_actual_min)) {
          obs.push({ date: r.date, behavior: r.value > threshold ? 1.0 : 0.0, y: y, strain: r.prev_strain, sleep_h: r.sleep_actual_min / 60 });
        }
      });
      outcomes[name] = merge({ label: oc[3] }, compare(yes, no, higher));
      if (regression) outcomes[name].regression = regress(obs, higher);
    });
    var res = { item: item, start: start, end: end, threshold: threshold, days_recorded: rows.length,
      days_yes: rows.filter(function (r) { return r.value > threshold; }).length,
      days_no: rows.filter(function (r) { return r.value <= threshold; }).length,
      outcomes: outcomes, method: METHOD };
    if (regression) res.regression_method = REGRESSION_METHOD;
    return res;
  }

  /** The join of the server's query on plain data: entries [{date, value}], scores {date: {recovery, sleep_actual_min,
   * strain}}, metrics {date: {resting_heart_rate, heart_rate_variability_sdnn}}. */
  function rows_from(entries, scores, metrics) {
    var g = function (o, k) { return o && nn(o[k]) ? o[k] : null; };
    return entries.slice().sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; }).map(function (e) {
      var nxt = U.addDays(e.date, 1), m = metrics[nxt], sc = scores[nxt], prev = scores[e.date];
      return { date: e.date, value: e.value, resting_heart_rate: g(m, "resting_heart_rate"),
        heart_rate_variability_sdnn: g(m, "heart_rate_variability_sdnn"), recovery: g(sc, "recovery"),
        sleep_actual_min: g(sc, "sleep_actual_min"), prev_strain: g(prev, "strain") };
    });
  }

  /** records/analysis.analyze_data: every active item (key null) or one item, on plain data. */
  function analyze_data(data, end, days, regression, key, threshold) {
    days = days === undefined || days === null ? 90 : days;
    var start = U.addDays(end, -(days - 1)), one = key !== undefined && key !== null;
    var items = one ? data.items.filter(function (i) { return i.key === key; }) : data.items;
    if (one && !items.length) throw new Error("没有这个日记项：" + key);
    var res = items.map(function (it) {
      var ents = data.entries.filter(function (e) { return e.key === it.key && start <= e.date && e.date <= end; });
      return analyze_rows(it, rows_from(ents, data.scores || {}, data.metrics || {}), start, end,
        one && nn(threshold) ? threshold : 0.0, !!regression);
    });
    if (regression && !one) apply_bh(res);
    clean(res);
    return one ? res[0] : res;
  }

  return { params: P, OUTCOMES: OUTCOMES, METHOD: METHOD, REGRESSION_METHOD: REGRESSION_METHOD, compare: compare,
    regress: regress, apply_bh: apply_bh, analyze_rows: analyze_rows, rows_from: rows_from, analyze_data: analyze_data };
})(OBS.util, OBS.regression);

OBS.reports = (function (U, AN, SD) {
  "use strict";
  // insights/reports_params.py
  var P = { REPORTS_VERSION: "reports-1", PERIOD_DAYS: { week: 7, month: 30 }, RECOVERY_GREEN: 67, RECOVERY_RED: 34,
    SHORT_SLEEP_MIN: 360, HIGH_STRAIN: 14, JOURNAL_DAYS: 90, JOURNAL_TOP: 5 };
  var SLEEP_TIMING = ["avg_bedtime", "avg_wake", "consistency_pct"];
  var NUTRITION_SUMMARY = ["days_logged", "calories_on_target_days", "calories_judged_days", "protein_on_target_days",
    "protein_judged_days"];

  function nn(v) { return v !== null && v !== undefined; }
  function isNum(v) { return typeof v === "number"; }
  function isDict(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }
  function mean(xs) { xs = xs.filter(nn); return xs.length ? U.round(U.fmean(xs), 2) : null; }
  function count(xs, f) { var n = 0; for (var i = 0; i < xs.length; i++) if (f(xs[i])) n++; return n; }
  function pad2(n) { return n < 10 ? "0" + n : String(n); }
  function pyMod(a, b) { var m = a % b; if (m !== 0) { if ((b < 0) !== (m < 0)) m += b; } else m = 0; return m; }

  /** Circular mean of times of day (wall clock of each ISO string). */
  function clock_mean(times) {
    var angs = [];
    times.forEach(function (t) {
      if (!t) return;
      var m = /^\d{4}-\d{2}-\d{2}[T ](\d{2}):(\d{2})/.exec(t);
      if (!m) throw new Error("not an ISO date-time: " + t);
      angs.push((Number(m[1]) * 60 + Number(m[2])) / 1440 * 2 * Math.PI);
    });
    if (!angs.length) return null;
    var a = pyMod(Math.atan2(U.pySum(angs.map(Math.sin)), U.pySum(angs.map(Math.cos))), 2 * Math.PI);
    var mins = pyMod(U.round(a / (2 * Math.PI) * 1440), 1440);
    return pad2(Math.floor(mins / 60)) + ":" + pad2(mins % 60);
  }

  function window_(period, end) {
    var n = P.PERIOD_DAYS[period];
    return [U.addDays(end, -(n - 1)), U.addDays(end, -(2 * n - 1)), U.addDays(end, -n)];
  }

  function scores_block(rows) {
    var rec = rows.map(function (r) { return r.recovery; }).filter(nn);
    var last = rows.length ? rows[rows.length - 1] : null;
    var hrv = rows.filter(function (r) { return nn(r.hrv_night_ln); }).map(function (r) { return Math.exp(r.hrv_night_ln); });
    var col = function (k) { return rows.map(function (r) { return r[k]; }); };
    var strains = col("strain").filter(nn);
    return {
      recovery: { avg: mean(rec), days: rec.length, green: count(rec, function (r) { return r >= P.RECOVERY_GREEN; }),
        yellow: count(rec, function (r) { return P.RECOVERY_RED <= r && r < P.RECOVERY_GREEN; }),
        red: count(rec, function (r) { return r < P.RECOVERY_RED; }), hrv_ms_avg: mean(hrv),
        rhr_avg: mean(rows.filter(function (r) { return !r.rhr_daily; }).map(function (r) { return r.rhr_night; })) },
      sleep: { nights: count(rows, function (r) { return nn(r.sleep_actual_min); }), avg_actual_min: mean(col("sleep_actual_min")),
        avg_score: mean(col("sleep_score")), avg_performance: mean(col("sleep_performance")), avg_debt_min: mean(col("sleep_debt_min")),
        avg_bedtime: clock_mean(rows.map(function (r) { return (r.s || {}).bedtime; })),
        avg_wake: clock_mean(rows.map(function (r) { return (r.s || {}).wake_time; })),
        nights_under_6h: count(rows, function (r) { return nn(r.sleep_actual_min) && r.sleep_actual_min < P.SHORT_SLEEP_MIN; }) },
      load: { days: strains.length, avg_strain: mean(col("strain")),
        max_strain: strains.length ? strains.reduce(function (a, b) { return b > a ? b : a; }) : null,
        days_strain_14_plus: count(rows, function (r) { return (r.strain || 0) >= P.HIGH_STRAIN; }),
        total_trimp: U.round(U.pySum(rows.map(function (r) { return r.trimp || 0; })), 1),
        muscle_volume_kg: U.round(U.pySum(rows.map(function (r) { return r.muscle_load || 0; })), 1),
        ctl_end: last ? last.ctl : null, atl_end: last ? last.atl : null, tsb_end: last ? last.tsb : null }
    };
  }

  function stress_block(rows) {
    if (rows === null) return { days: 0, avg: null };
    return { days: count(rows, function (r) { return nn(r.score); }), avg: mean(rows.map(function (r) { return r.score; })),
      high_hours: rows.reduce(function (a, r) { return a + (r.high_h || 0); }, 0) };
  }

  function consistency(sessions, tz, a, b) {
    var lo = U.addDays(a, -1);
    var rows = sessions.filter(function (r) { return lo <= r.wake_date && r.wake_date <= b; });
    var w = SD.watch_sessions(rows);
    return SD.consistency_pct(SD.sri_from(w[0], w[1], tz, a, b).sri);
  }

  function training_block(t, a, b) {
    var out = { sessions: null, strength_sessions: null, workouts: null, workout_minutes: null, by_type: {}, error: null };
    if (nn(t.error)) out.error = t.error;
    else {
      var log = (t.log || []).filter(function (s) { var d = s.date.slice(0, 10); return a <= d && d <= b; });
      out.sessions = log.length;
      out.strength_sessions = count(log, function (s) {
        return (s.exercises || []).some(function (e) { return (e.category || "strength") === "strength"; });
      });
      out.session_names = log.map(function (s) { return s.date.slice(0, 10) + " " + s.name; });
    }
    if (nn(t.workouts)) {
      var rows = t.workouts.filter(function (r) { return a <= r.local_date && r.local_date <= b; });
      out.workouts = rows.length;
      out.workout_minutes = U.round(U.pySum(rows.map(function (r) { return r.duration_min; })));
      rows.forEach(function (r) { out.by_type[r.name_zh] = (out.by_type[r.name_zh] || 0) + 1; });
    }
    return out;
  }

  function weight_block(w, start, end) {
    if ("error" in w) return { error: w.error };
    var rows = (w.rows || []).filter(function (r) { return "weight_kg" in r; });
    var inside = rows.filter(function (r) { return start <= r.date && r.date <= end; });
    var before = rows.filter(function (r) { return r.date < start; });
    var first = before.length ? before[before.length - 1] : (inside.length ? inside[0] : null);
    var last = inside.length ? inside[inside.length - 1] : null;
    var both = first && last && first !== last;
    return { weigh_ins: inside.length, last_kg: last ? last.weight_kg : null, last_date: last ? last.date : null,
      change_kg: both ? U.round(last.weight_kg - first.weight_kg, 2) : null, from_date: both ? first.date : null };
  }

  function nutrition_block(n, a, b) {
    if ("error" in n) return { error: n.error };
    var days = n.days.filter(function (d) { return a <= d.date && d.date <= b; });
    var logged = days.filter(function (d) { return d.logged; });
    var judged_cal = logged.filter(function (d) { return nn(d.adherence.calories); });
    var judged_pro = logged.filter(function (d) { return nn(d.adherence.protein); });
    var sugar = logged.filter(function (d) { return nn(d.added_sugar_g); });
    var names = {};
    days.forEach(function (d) { if (d.goal && d.goal.name) names[d.goal.name] = true; });
    return { days_logged: logged.length, avg_calories: mean(logged.map(function (d) { return d.calories; })),
      avg_protein_g: mean(logged.map(function (d) { return d.protein_g; })),
      calories_on_target_days: count(judged_cal, function (d) { return d.adherence.calories; }),
      calories_judged_days: judged_cal.length,
      protein_on_target_days: count(judged_pro, function (d) { return d.adherence.protein; }),
      protein_judged_days: judged_pro.length,
      added_sugar_avg_g: mean(sugar.map(function (d) { return d.added_sugar_g; })), added_sugar_known_days: sugar.length,
      added_sugar_over_cap_days: count(sugar, function (d) { return d.goal && d.added_sugar_g > d.goal.added_sugar_max_g; }),
      sugar_avg_g: mean(logged.map(function (d) { return d.sugar_g; })),
      sugary_drinks: logged.reduce(function (acc, d) { return acc + d.sugary_drinks; }, 0),
      goal_versions: Object.keys(names).sort() };
  }

  function journal_block(results) {
    var found = [];
    (results || []).forEach(function (a) {
      var o = a.outcomes.next_recovery || {};
      if (o.status === "ok" && (o.direction === "better" || o.direction === "worse")) {
        found.push({ key: a.item.key, label: a.item.label_zh, direction: o.direction, diff_recovery: o.diff_yes_minus_no,
          cohens_d: o.cohens_d, reliable: o.reliable, days_yes: a.days_yes, days_no: a.days_no });
      }
    });
    found.sort(function (x, y) { return -Math.abs(x.cohens_d || 0) - -Math.abs(y.cohens_d || 0); });   // stable, as sorted()
    return found.slice(0, P.JOURNAL_TOP);
  }

  function delta(cur, prev) {
    if (isDict(cur) && isDict(prev)) {
      var out = {};
      Object.keys(cur).forEach(function (k) { if (isNum(cur[k]) || isDict(cur[k])) out[k] = delta(cur[k], prev[k]); });
      return out;
    }
    if (isNum(cur) && isNum(prev)) return U.round(cur - prev, 2);
    return null;
  }

  /** insights/reports.compute: data as reports.load returns it (plain JSON; a block whose key is absent is left out). */
  function compute(data, period, end) {
    if (!(period in P.PERIOD_DAYS)) throw new Error("period 要写 week 或 month");
    var w = window_(period, end), start = w[0], pstart = w[1], pend = w[2];
    var can = {};
    (data.can || []).forEach(function (s) { can[s] = true; });
    var nutrition = data.nutrition_mode || null;
    var inRange = function (rows, col, a, b) { return rows.filter(function (r) { return a <= r[col] && r[col] <= b; }); };

    function block(a, b) {
      var out = {};
      if (can.scores) {
        out = scores_block(inRange(data.scores, "date", a, b));
        if ("stress" in data) out.stress = stress_block(data.stress === null ? null : inRange(data.stress, "date", a, b));
        if (can.sleep) out.sleep.consistency_pct = consistency(data.sleep_sessions || [], data.tz, a, b);
        else SLEEP_TIMING.forEach(function (k) { delete out.sleep[k]; });
      }
      return out;
    }

    var cur = block(start, end), prev = block(pstart, pend);
    if (can.training && "training" in data) {
      cur.training = training_block(data.training, start, end);
      prev.training = training_block(data.training, pstart, pend);
    }
    if (can.weight && "weights" in data) cur.weight = weight_block(data.weights, start, end);
    if (nutrition && "nutrition" in data) {
      cur.nutrition = nutrition_block(data.nutrition, start, end);
      prev.nutrition = nutrition_block(data.nutrition, pstart, pend);
      if (nutrition === "summary") {
        [cur, prev].forEach(function (x) {
          var o = {};
          Object.keys(x.nutrition).forEach(function (k) { if (NUTRITION_SUMMARY.indexOf(k) >= 0 || k === "error") o[k] = x.nutrition[k]; });
          x.nutrition = o;
        });
      }
    }
    var change = {};
    ["recovery", "sleep", "load", "stress", "training", "nutrition"].forEach(function (k) { if (k in cur) change[k] = delta(cur[k], prev[k]); });
    var out = { period: period, start: start, end: end, previous: { start: pstart, end: pend }, current: cur,
      previous_values: prev, change: change };
    if (can.journal) {
      if ("journal_results" in data) out.journal_findings = journal_block(data.journal_results);
      else if ("journal" in data) out.journal_findings = journal_block(AN.analyze_data(data.journal, end, P.JOURNAL_DAYS));
    }
    var hidden = ["scores", "sleep", "training", "weight", "nutrition", "journal"].filter(function (s) { return !can[s]; });
    if (hidden.length) out.hidden = hidden;
    return out;
  }

  // ---------------------------------------------------------------- entry points (api.js style: stamped)
  function stamp(x) { x.algo_version = P.REPORTS_VERSION; x.bundle_version = OBS.VERSION; return x; }
  OBS.ALGO_VERSIONS.reports = P.REPORTS_VERSION;
  /** The report document (GET /reports). opts: {period: week | month, end}. */
  OBS.report = function (data, opts) { return stamp(compute(data, opts.period, opts.end)); };
  /** GET /journal/analysis: {results: [...] (or the one item's result with opts.key), algo_version, bundle_version}.
   * opts: {end, days (90), regression, key, threshold}. */
  OBS.journalAnalysis = function (data, opts) {
    return stamp({ results: AN.analyze_data(data, opts.end, opts.days, opts.regression, opts.key, opts.threshold) });
  };

  return { params: P, window: window_, mean: mean, clock_mean: clock_mean, scores_block: scores_block, stress_block: stress_block,
    consistency: consistency, training_block: training_block, weight_block: weight_block, nutrition_block: nutrition_block,
    journal_block: journal_block, delta: delta, compute: compute };
})(OBS.util, OBS.analysis, OBS.series.sleep_detail);
