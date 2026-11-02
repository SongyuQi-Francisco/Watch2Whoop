// Cross-language goldens of package M2 (docs/ios-native-2.0.md section 2.1): src/32_stress.js on the inputs of
// cases_stress_cards.py, compared with test/golden/stress_cards.json and stress_local.json (written by
// body_engine/tests/test_js_golden_stress_cards.py) within the float tolerance of harness.js.
// Run: node --test packages/openbodyscore-js/test/
"use strict";
const assert = require("assert");
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { loadBundle, golden, mismatch, plain } = require("./harness");

const O = loadBundle();
const S = O.stress_cards;

function cases() {
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "obs-stress-")), "cases.json");
  execFileSync(process.env.PYTHON || "python3", [path.join(__dirname, "cases_stress_cards.py"), "--dump", out], { stdio: "pipe" });
  return JSON.parse(fs.readFileSync(out, "utf8"));
}
const C = cases();
const want = golden("packages/openbodyscore-js/test/golden/stress_cards.json");
const check = (got, w, p) => { const m = mismatch(plain(got), w, p); assert.strictEqual(m, null, m); };
const unstamp = (x) => { delete x.algo_version; delete x.bundle_version; return x; };

/** cases_stress_cards.variant_src */
function variantSrc(src, run) {
  const out = Object.assign({}, src, { start: run.start, end: run.end });
  (run.drop || []).forEach((k) => {
    out.app = Object.fromEntries(Object.entries(out.app).filter(([g]) => g !== k));
    out.other = Object.fromEntries(Object.entries(out.other).filter(([g]) => g !== k));
  });
  return out;
}

test("stress_cards: version and the heart-rate-recovery parameters are the Python's", () => {
  assert.strictEqual(O.ALGO_VERSIONS.stress_cards, want.version);
  assert.strictEqual(S.STRESS_CARDS_VERSION, want.version);
  assert.deepStrictEqual(plain(S.params), want.params);
});

test("stress_cards: insights/stress.days_local (the phone's tables -> merge -> stress_daily rows)", () => {
  const l = C.local;
  assert.strictEqual(l.runs.length, want.local.length);
  l.runs.forEach((r, i) => check(S.days_local(variantSrc(l.src, r)), want.local[i], `local[${i}] ${r.name}`));
});

test("stress_cards: the entry point stressLocal is stamped and equals the iOS fixture", () => {
  const ios = golden("packages/openbodyscore-js/test/golden/stress_local.json");
  const rows = O.stressLocal(ios.src);
  rows.forEach((r) => {
    assert.strictEqual(r.algo_version, O.ALGO_VERSIONS.stress_cards);
    assert.strictEqual(r.bundle_version, O.VERSION);
    unstamp(r);
  });
  check(rows, ios.rows, "stress_local");
});

test("stress_cards: insights/stress.cards (resilience, body battery, heart-rate recovery; both languages)", () => {
  const k = C.cards;
  let i = 0;
  [null].concat(Object.keys(k.variants).sort()).forEach((name) => {
    const data = Object.assign({}, k.data, name ? k.variants[name] : {});
    (name === null ? k.runs : k.runs.slice(0, 1)).forEach((r) => {
      O.setLanguage(r.lang);
      check(S.cards(data, r.end, r.today, k.tz, r.now_hour), want.cards[i], `cards[${i}] ${name || "full"} ${r.end}`);
      i += 1;
    });
  });
  O.setLanguage("zh");
  assert.strictEqual(i, want.cards.length);
  const r = k.runs[0];
  check(unstamp(O.stressCards(k.data, { end: r.end, today: r.today, tz: k.tz, now_hour: r.now_hour })), want.cards[0], "stressCards");
});

test("stress_cards: cardio/views.compute (HRR60 means, recovery index)", () => {
  const d = C.cardio;
  d.runs.forEach((r, i) => check(S.cardio(d, r.start, r.end), want.cardio[i], `cardio[${i}]`));
  check(unstamp(O.cardioView(d, d.runs[0])), want.cardio[0], "cardioView");
});

test("stress_cards: statistics.mean is exact (correctly rounded rational mean)", () => {
  assert.strictEqual(S.mean([0.1, 0.2]), 0.15000000000000002);
  assert.strictEqual(S.mean([1e16, 1, -1e16]), 1 / 3);
  assert.strictEqual(S.mean([20.1, 20.2, 19.7]), 20);
});
