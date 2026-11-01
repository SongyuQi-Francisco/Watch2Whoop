// 2.0.0 package M1 (docs/ios-native-2.0.md section 4): the phone's derivation of daily values and sleep (src/30_derive.js)
// against the Python reference (body_engine rawdata/derive.derive_days), on the synthetic inputs of cases_derive.py.
// Python side: body_engine/tests/test_js_golden_derive.py writes test/golden/derive.json. Run: node --test test/
"use strict";
const assert = require("assert");
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { loadBundle, golden, mismatch, plain } = require("./harness");

const O = loadBundle();
const G = golden("packages/openbodyscore-js/test/golden/derive.json");
const check = (got, want) => { const m = mismatch(plain(got), want); assert.strictEqual(m, null, m); };

let cached = null;
function inputs() {
  if (cached) return cached;
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "obs-derive-")), "cases.json");
  execFileSync(process.env.PYTHON || "python3", [path.join(__dirname, "cases_derive.py"), "--dump", out], { stdio: "pipe" });
  cached = JSON.parse(fs.readFileSync(out, "utf8"));
  return cached;
}

test("derive: algorithm version and thresholds mirror the Python (D7)", () => {
  assert.strictEqual(O.ALGO_VERSIONS.derive, G.params.ALGO_VERSION);
  const P = O.derive.PARAMS;
  for (const [k, v] of Object.entries(G.params)) {
    const mine = Array.isArray(v) ? plain(P[k]).slice().sort() : plain(P[k]);
    check(mine, v);
  }
  assert.deepStrictEqual(Object.keys(plain(P)).sort(), Object.keys(G.params).sort());
});

test("derive: the HealthKit registry rows equal rawdata/hk_types.ENGINE_TYPES", () => {
  const mine = O.derive.TYPES.map((t) => ({ hk: t.hk, key: t.key, kind: t.kind, agg: t.agg, factor: t.factor,
    store_unit: t.store_unit, lo: t.lo, hi: t.hi, stats: t.stats, phase1: t.phase1, phase1_unit: t.phase1 ? t.store_unit : null }));
  check(mine, G.registry);
});

test("derive: deriveDays equals derive_days on every case (daily values, source choice, sleep)", () => {
  const cases = inputs().cases;
  assert.strictEqual(cases.length, G.cases.length);
  cases.forEach((c, i) => {
    assert.strictEqual(c.name, G.cases[i].name);
    const got = O.deriveDays(c.src);
    assert.strictEqual(got.bundle_version, O.VERSION);
    delete got.bundle_version;
    check(got, G.cases[i].want);
  });
});

test("derive: the iOS fixture in the golden file (columnar) gives the same rows", () => {
  const c = G.cases.find((x) => x.src);
  assert.ok(c, "one case carries its inputs");
  const got = O.deriveDays(c.src);
  delete got.bundle_version;
  check(got, c.want);
});

test("derive: helpers on pulled rows (normalize, snake)", () => {
  for (const c of G.calls) check(O.derive[c.fn](...c.args), c.want);
});

test("derive: units 'stored' skip the factor; same day twice gives the same rows (idempotent)", () => {
  const c = inputs().cases.find((x) => x.name === "new_york");
  const a = O.deriveDays(c.src), b = O.deriveDays(c.src);
  check(a, plain(b));
  const spo2 = c.src.samples.filter((s) => s.type === "HKQuantityTypeIdentifierOxygenSaturation");
  const stored = { ...c.src, units: "stored", samples: spo2.map((s) => ({ ...s, value: s.value * 100 })), buckets: [] };
  const d = stored.days[0];
  const row = O.deriveDays(stored).daily[d].sources.find((r) => r.metric === "oxygen_saturation");
  const want = a.daily[d].sources.find((r) => r.metric === "oxygen_saturation");
  check(row, plain(want));
});
