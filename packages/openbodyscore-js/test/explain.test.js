// Cross-language goldens of package M6, explain part (docs/ios-native-2.0.md section 2.1): src/37_explain.js on the inputs of
// cases_explain.py, compared with test/golden/explain.json (written by body_engine/tests/test_js_golden_explain.py) within
// the float tolerance of harness.js. Every text (facts, advice, ai_prompt) in Chinese and English must be identical. The
// static entry of each sheet comes from the golden file (the glossary's, as the phone takes it from the server's sheet).
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
const X = O.explain;

function cases() {
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "obs-explain-")), "cases.json");
  execFileSync(process.env.PYTHON || "python3", [path.join(__dirname, "cases_explain.py"), "--dump", out], { stdio: "pipe" });
  return JSON.parse(fs.readFileSync(out, "utf8"));
}
const C = cases();
const want = golden("packages/openbodyscore-js/test/golden/explain.json");
const check = (got, w, p) => { const m = mismatch(plain(got), w, p); assert.strictEqual(m, null, m); };
function inLang(lang, fn) { O.setLanguage(lang); try { return fn(); } finally { O.setLanguage("zh"); } }

test("explain: version and parameters are the Python's", () => {
  assert.strictEqual(O.ALGO_VERSIONS.explain, want.version);
  const p = plain(X.params), w = want.params;
  ["BASELINE_DAYS", "MIN_BASELINE_NIGHTS", "ILLNESS_RHR_DELTA", "RECOVERY_PART", "SLEEP_PART", "SLEEP_PART_TIP", "VO2_LEVEL_EN",
    "TONE", "LAB_SOURCE", "TREND", "LAB"].forEach((k) => assert.deepStrictEqual(p[k], w[k], k));
  assert.strictEqual(plain(O.coach.today.params).RECOVERY_GREEN, w.RECOVERY_GREEN);
});

test("explain: every metric sheet (calc_* and the Trends metrics), both languages", () => {
  assert.strictEqual(C.runs.length, want.metric.length);
  C.runs.forEach((r, i) => {
    const got = inLang(r.lang, () => X.metric_view(r.key, want.entries[r.lang][r.key], X.metric_out(r.key, r.data, r.day)));
    check(got, want.metric[i], `metric[${i}] ${r.key} ${r.day} ${r.lang}`);
  });
  const r = C.runs[0], doc = inLang(r.lang, () => O.explainMetric(r.key, want.entries[r.lang][r.key], r.data, r.day));
  assert.strictEqual(doc.algo_version, O.ALGO_VERSIONS.explain);
  delete doc.algo_version; delete doc.bundle_version;
  check(doc, want.metric[0], "explainMetric");
});

test("explain: lab sheets, lab history and status card", () => {
  C.labs.forEach((r, i) => {
    const got = inLang(r.lang, () => X.lab_view(r.key, want.lab_entries[r.lang][r.key], X.lab_out({ report: r.report }, r.key, r.day), null));
    check(got, want.lab[i], `lab[${i}] ${r.key} ${r.lang}`);
  });
  C.lab_history.forEach((r, i) => check(O.labHistory(r.key, r.rows), want.lab_history[i], `lab_history[${i}] ${r.key}`));
  if (worst.count) console.log(`# explain: ${worst.count} floats differ in the last bits, largest ${worst.rel.toExponential(2)} relative at ${worst.path}`);
});
