// Cross-language goldens of package M5 (docs/ios-native-2.0.md section 2.1): src/35_reports.js on the inputs of
// cases_reports.py, compared with test/golden/reports.json (written by body_engine/tests/test_js_golden_reports.py) within
// the float tolerance of harness.js. Rounded outputs (effect, se, p, q, z, the report's numbers) must be identical.
// Run: node --test packages/openbodyscore-js/test/
"use strict";
const assert = require("assert");
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { loadBundle, golden, mismatch, plain, worst } = require("./harness");

const O = loadBundle();
const RG = O.regression, AN = O.analysis, R = O.reports;

function cases() {
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "obs-reports-")), "cases.json");
  execFileSync(process.env.PYTHON || "python3", [path.join(__dirname, "cases_reports.py"), "--dump", out], { stdio: "pipe" });
  return JSON.parse(fs.readFileSync(out, "utf8"));
}
const C = cases();
const want = golden("packages/openbodyscore-js/test/golden/reports.json");
const check = (got, w, p) => { const m = mismatch(plain(got), w, p); assert.strictEqual(m, null, m); };

/** cases_reports.variant */
function variant(data, name) {
  const v = C.variants[name], out = {};
  Object.keys(data).forEach((k) => { if (!(v.drop || []).includes(k)) out[k] = data[k]; });
  Object.assign(out, v.set || {});
  return out;
}

test("reports: versions and parameters are the Python's", () => {
  assert.strictEqual(O.ALGO_VERSIONS.reports, want.version);
  assert.deepStrictEqual(plain(R.params), want.params.reports);
  assert.deepStrictEqual(plain(AN.params), want.params.analysis);
});

test("reports: records/regression (inverse, normal p, Benjamini-Hochberg, ols_hac)", () => {
  const rg = C.regression, w = want.regression;
  rg.inverse.forEach((m, i) => {
    let got;
    try { got = RG.inverse(m, AN.params.SINGULAR_TOL); } catch (e) { if (e instanceof RG.Singular) got = { singular: e.column }; else throw e; }
    check(got, w.inverse[i], `inverse[${i}]`);
  });
  check(rg.norm_p.map(RG.norm_p), w.norm_p, "norm_p");
  check(rg.bh.map(RG.bh), w.bh, "bh");
  rg.ols.forEach((o, i) => check(RG.ols_hac(o.days, o.y, o.x, o.names), w.ols[i], `ols[${i}] ${o.name}`));
});

test("reports: records/analysis.regress (usual, few days, collinear, zero residuals, all-zero, empty)", () => {
  C.regress.forEach((k, i) => {
    const res = AN.regress(k.obs, k.higher);
    delete res._higher_is_better;
    check(res, want.regress[i], `regress[${i}] ${k.name}`);
  });
});

test("reports: records/analysis.analyze_data (730 days of journal, 14 / 90 / 730-day windows, BH)", () => {
  assert.strictEqual(C.analysis_runs.length, want.analysis.length);
  C.analysis_runs.forEach((r, i) => {
    check(AN.analyze_data(C.journal, r.end, r.days, r.regression, r.key, r.threshold), want.analysis[i],
      `analysis[${i}] ${r.days}d${r.regression ? " regression" : ""}${r.key ? " " + r.key : ""}`);
  });
  const r = C.analysis_runs[5], doc = O.journalAnalysis(C.journal, { end: r.end, days: r.days, regression: true });
  assert.strictEqual(doc.algo_version, O.ALGO_VERSIONS.reports);
  assert.strictEqual(doc.bundle_version, O.VERSION);
  check(doc.results, want.analysis[5], "journalAnalysis");
});

test("reports: insights/reports.compute (week / month, every block and its edges)", () => {
  assert.strictEqual(C.report_runs.length, want.reports.length);
  C.report_runs.forEach((r, i) => {
    check(R.compute(variant(C.report, r.variant), r.period, r.end), want.reports[i], `reports[${i}] ${r.period} ${r.end} ${r.variant}`);
  });
  const r = C.report_runs[0], doc = O.report(C.report, { period: r.period, end: r.end });
  assert.strictEqual(doc.algo_version, O.ALGO_VERSIONS.reports);
  delete doc.algo_version; delete doc.bundle_version;
  check(doc, want.reports[0], "report entry point");
  if (worst.count) console.log(`# reports: ${worst.count} floats differ in the last bits, largest ${worst.rel.toExponential(2)} relative at ${worst.path}`);
});

test("reports: erfc is accurate on both branches", () => {
  // values of erfc from the C library (glibc and macOS agree to the last digit shown)
  [[0.5, 0.4795001221869535], [1.5, 0.033894853524689274], [2.5, 0.000406952017444959], [6, 2.1519736712498913e-17]]
    .forEach(([x, v]) => assert.ok(Math.abs(RG.erfc(x) - v) <= 1e-13 * v, `erfc(${x})`));
  assert.strictEqual(RG.erfc(0), 1);
  assert.strictEqual(RG.erfc(30), 0);
});
