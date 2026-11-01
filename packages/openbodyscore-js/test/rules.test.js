// Cross-language goldens of package M6, rules part (docs/ios-native-2.0.md section 2.1): src/36_rules.js on the inputs of
// cases_rules.py, compared with test/golden/rules.json (written by body_engine/tests/test_js_golden_rules.py) within the
// float tolerance of harness.js. Reminder texts (title / message in Chinese, title_en / message_en) must be identical.
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
const R = O.rules;

function cases() {
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "obs-rules-")), "cases.json");
  execFileSync(process.env.PYTHON || "python3", [path.join(__dirname, "cases_rules.py"), "--dump", out], { stdio: "pipe" });
  return JSON.parse(fs.readFileSync(out, "utf8"));
}
const C = cases();
const want = golden("packages/openbodyscore-js/test/golden/rules.json");
const check = (got, w, p) => { const m = mismatch(plain(got), w, p); assert.strictEqual(m, null, m); };

test("rules: versions and parameters are the Python's", () => {
  assert.strictEqual(O.ALGO_VERSIONS.rules, want.version);
  const p = plain(R.params);
  ["WARMUP_SET_TYPES", "LEG_MUSCLES"].forEach((k) => { p.rules[k] = p.rules[k].slice().sort(); });
  assert.deepStrictEqual(p.rules, want.params.rules);
  assert.deepStrictEqual(p.goals, want.params.goals);
  assert.deepStrictEqual(p.water, want.params.water);
  assert.deepStrictEqual(p.constraints, want.params.constraints);
  assert.deepStrictEqual(p.nutrition, want.params.nutrition);
  assert.deepStrictEqual(p.context, want.params.context);
});

test("rules: every rule on the Ctx as data (fires / quiet / short of data / errors)", () => {
  assert.strictEqual(C.runs.length, want.runs.length);
  C.runs.forEach((r, i) => check(R.evaluate(r.data), want.runs[i], `runs[${i}] ${r.name}`));
  const doc = O.rulesEvaluate(C.runs[0].data);
  assert.strictEqual(doc.algo_version, O.ALGO_VERSIONS.rules);
  assert.strictEqual(doc.bundle_version, O.VERSION);
});

test("rules: the phone's raw sources (assemble: goals, weights, nights over DST, plan day changes)", () => {
  assert.strictEqual(C.raw.length, want.raw.length);
  C.raw.forEach((r, i) => {
    const data = R.assemble(r.raw);
    check(data, want.raw[i].data, `raw[${i}] ${r.name} data`);
    check(R.evaluate(data), want.raw[i].result, `raw[${i}] ${r.name} result`);
  });
  const doc = O.rulesLocal(C.raw[0].raw);
  delete doc.algo_version; delete doc.bundle_version;
  check(doc, want.raw[0].result, "rulesLocal");
});

test("rules: nutrition goals and day rows from the log's sums, both languages", () => {
  const n = C.nutrition;
  n.calls.forEach((c, i) => {
    O.setLanguage(c.lang);
    try {
      check(O.nutritionGoals(n.versions, c.from, c.to, n.sparky), want.nutrition[i].goals, `goals[${i}] ${c.lang}`);
      check(O.nutritionDays(n.totals, n.versions, c.from, c.to, n.sparky), want.nutrition[i].days, `days[${i}] ${c.lang}`);
    } finally { O.setLanguage("zh"); }
  });
  if (worst.count) console.log(`# rules: ${worst.count} floats differ in the last bits, largest ${worst.rel.toExponential(2)} relative at ${worst.path}`);
});

test("rules: ISO week of the calorie reminder's subject", () => {
  assert.deepStrictEqual(plain(R.isoWeek("2026-10-16")), [2026, 42]);
  assert.deepStrictEqual(plain(R.isoWeek("2027-01-01")), [2026, 53]);
  assert.deepStrictEqual(plain(R.isoWeek("2024-12-30")), [2025, 1]);
});
