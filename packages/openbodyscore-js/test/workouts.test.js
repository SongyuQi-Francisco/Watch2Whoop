// 2.0.0 package M1c (docs/ios-native-2.0.md section 4): the phone's workouts, the training calendar / day and the
// muscle-load map (src/31_workouts.js) against the Python references (body_engine training/importer.workout_days,
// training/history.compute + calendar_view / day_view, training/muscle_load.compute_from), on the synthetic inputs of
// cases_workouts.py. Python side: body_engine/tests/test_js_golden_workouts.py writes test/golden/workouts.json.
// Run: node --test test/
"use strict";
const assert = require("assert");
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { loadBundle, golden, mismatch, plain } = require("./harness");

const O = loadBundle();
const G = golden("packages/openbodyscore-js/test/golden/workouts.json");
const check = (got, want) => { const m = mismatch(plain(got), want); assert.strictEqual(m, null, m); };
const SETS = new Set(["GENERIC_TYPES", "STRENGTH_TYPES", "CARDIO_TYPES", "SPORT_TYPES", "WARMUP_SET_TYPES"]);

let cached = null;
function inputs() {
  if (cached) return cached;
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "obs-workouts-")), "cases.json");
  execFileSync(process.env.PYTHON || "python3", [path.join(__dirname, "cases_workouts.py"), "--dump", out], { stdio: "pipe" });
  cached = JSON.parse(fs.readFileSync(out, "utf8"));
  return cached;
}
function bare(x) { delete x.algo_version; delete x.bundle_version; return x; }

test("workouts: algorithm version, thresholds and tables mirror the Python (D7)", () => {
  assert.strictEqual(O.ALGO_VERSIONS.workouts, G.params.ALGO_VERSION);
  const P = O.workouts.PARAMS;
  for (const [k, v] of Object.entries(G.params)) check(SETS.has(k) ? plain(P[k]).slice().sort() : plain(P[k]), v);
  assert.deepStrictEqual(Object.keys(plain(P)).sort(), Object.keys(G.params).sort());
  check(O.workouts.TABLES, G.tables);
});

test("workouts: workoutDays equals workout_days (parse, corrections, duplicates, TRIMP, zones, HRR, intensity)", () => {
  const cases = inputs().workouts;
  assert.strictEqual(cases.length, G.workouts.length);
  cases.forEach((c, i) => {
    assert.strictEqual(c.name, G.workouts[i].name);
    const got = O.workoutDays(c.src);
    assert.strictEqual(got.algo_version, G.params.ALGO_VERSION);
    assert.strictEqual(got.bundle_version, O.VERSION);
    delete got.bundle_version;
    check(got, G.workouts[i].want);
  });
});

test("workouts: training calendar and day equal history.compute + calendar_view / day_view (zh and en)", () => {
  const runs = inputs().training.runs;
  assert.strictEqual(runs.length, G.training.length);
  try {
    runs.forEach((r, i) => {
      assert.strictEqual(r.name, G.training[i].name);
      O.setLanguage(r.lang);
      const got = r.view === "calendar"
        ? O.trainingCalendar(r.data, { start: r.start, end: r.end, label: r.label, today: r.today })
        : O.trainingDay(r.data, { date: r.start, today: r.today });
      check(bare(got), G.training[i].want);
    });
  } finally { O.setLanguage("zh"); }
});

test("workouts: muscle-load map equals muscle_load.compute_from (zh and en)", () => {
  const runs = inputs().muscle.runs;
  assert.strictEqual(runs.length, G.muscle.length);
  try {
    runs.forEach((r, i) => {
      assert.strictEqual(r.name, G.muscle[i].name);
      O.setLanguage(r.lang);
      check(bare(O.muscleLoad(r.data, r.today)), G.muscle[i].want);
    });
  } finally { O.setLanguage("zh"); }
});

test("workouts: the same input twice gives the same rows (idempotent), the input is not changed", () => {
  const c = inputs().workouts[0];
  const before = JSON.stringify(c.src);
  const a = O.workoutDays(c.src), b = O.workoutDays(c.src);
  check(a, plain(b));
  assert.strictEqual(JSON.stringify(c.src), before);
});
