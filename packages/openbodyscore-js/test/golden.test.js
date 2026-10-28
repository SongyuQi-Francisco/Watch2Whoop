// Cross-language golden tests (issue #340 N1): the JS bundle on the same inputs as the Python code, compared with the same
// golden files, within the float tolerance documented in harness.js. Run: node --test packages/openbodyscore-js/test/
"use strict";
const assert = require("assert");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const { worst, loadBundle, cases, golden, mismatch, plain, ROOT } = require("./harness");
const goldenCases = require("./golden_cases");

const O = loadBundle();
const U = O.util;
const C = cases();
const TZ = U.tzOf(600);
const us = (x) => U.toUs(x);
const pairs = (xs) => xs.map((p) => [us(p[0]), p[1]]);
const spans = (xs) => xs.map((p) => [us(p[0]), us(p[1])]);
const check = (got, want) => { const m = mismatch(plain(got), want); assert.strictEqual(m, null, m); };

test("openbodyscore goldens: scores.json (every scoring function of the Python package)", () => {
  const got = goldenCases(O, C.base);
  check(got.scores, golden("packages/openbodyscore/tests/golden/scores.json"));
});

test("openbodyscore goldens: healthspan_v12.json (body age v1.2)", () => {
  check(goldenCases(O, C.base).v12, golden("packages/openbodyscore/tests/golden/healthspan_v12.json"));
});

test("engine: daily_scores rows (scores/engine.score_days)", () => {
  const inp = O.inputs.normalize(C.engine.inputs);
  const runs = C.engine.runs.map((r) => O.engine.score_days(inp, r.first, r.last, r.today));
  check({ runs }, golden("packages/openbodyscore-js/test/golden/engine.json"));
});

test("stress: stress_daily rows (insights/stress.score_range)", () => {
  const s = C.stress;
  const runs = s.runs.map((r) => O.stress_days.score_range(600, r.start, r.end, spans(s.sleep), spans(s.workouts), pairs(s.hr),
    r.steps ? pairs(s.steps) : [], pairs(s.hrv), s.hrv_night, s.shortcut));
  check({ runs }, golden("packages/openbodyscore-js/test/golden/stress.json"));
});

test("healthspan: snapshot / weekly (insights/healthspan_store)", () => {
  const h = C.healthspan, HSS = O.healthspan_snapshot;
  const sleepDays = HSS.sleep_days(spans(h.intervals), h.wake_dates, 600);
  const dataWith = (changes) => Object.assign({}, h.data, changes || {}, { sleep_days: sleepDays });
  const snapshots = [];
  h.as_of.forEach((a) => ["v1.1", "v1.2"].forEach((v) => snapshots.push([a, v, HSS.snapshot(dataWith(), a, HSS.VERSIONS[v])])));
  const variants = {};
  Object.keys(h.variants).forEach((k) => { variants[k] = HSS.snapshot(dataWith(h.variants[k]), h.today); });
  const weekly = HSS.weekly(dataWith(), h.origin, h.today);
  check({ snapshots, variants, weekly }, golden("packages/openbodyscore-js/test/golden/healthspan.json"));
});

test("trends: every Trends function (trends/calc, series helpers, coach)", () => {
  const want = golden("packages/openbodyscore-js/test/golden/trends.json");
  assert.strictEqual(C.trends.length, want.length);
  C.trends.forEach((c, i) => {
    const args = c.args.slice();
    if (c.fn === "chronotype") { args[0] = spans(args[0]); args.push(TZ); }
    const m = mismatch(plain({ fn: c.fn, value: O.trends[c.fn].apply(null, args) }), want[i], `[${i}] ${c.fn}`);
    assert.strictEqual(m, null, m);
  });
});

test("api: buildInputs from raw local rows + computeDays give the server's rows, each with algo_version", () => {
  const e = C.engine.inputs;
  // the same data the way the phone keeps it: sleep_sessions rows, one flat sample list, stage segments
  const src = {
    tz: "+10:00", birth_date: e.birth_date, hrv_kind: e.hrv_kind,
    sleep_sessions: e.nights.map((n) => ({ wake_date: n.wake_date, start: n.start, end: n.end, asleep_min: n.asleep_min,
      core_min: n.asleep_min - n.deep_min - n.rem_min, deep_min: n.deep_min, rem_min: n.rem_min, awake_min: n.awake_min,
      source: n.source, device: "Apple Watch" }))
      .concat([{ wake_date: e.non_watch_dates[0], start: e.nights[0].start, end: e.nights[0].start, asleep_min: 400,
        core_min: 0, deep_min: 0, rem_min: 0, awake_min: 0, source: "phone", device: "iPhone" }]),
    samples: [].concat(...["hr", "hrv", "resp", "spo2", "temp"].map((k) => e[k].map((p) => ({ kind: k, t: p[0], value: p[1] })))),
    segments: e.segments.map((s) => ({ start: s[0], end: s[1], stage: s[2] })),
    daily_rhr: e.daily_rhr, shortcut_dates: e.shortcut_dates, muscle_sessions: e.muscle_sessions,
  };
  // an export.xml copy of a live sample yields; SpO2 as a fraction becomes percent
  const first = e.hr[0];
  src.samples.push({ kind: "hr", t: first[0], value: 199, xml: true });
  src.samples.unshift({ kind: "hr", t: first[0], value: 198, xml: true });
  const inp = O.buildInputs(src);
  assert.deepStrictEqual(plain(inp.non_watch_dates), []);   // that date also has a watch nap
  const nw = O.buildInputs({ tz: 600, sleep_sessions: [{ wake_date: "2026-08-02", start: 0, end: 1, asleep_min: 300, device: "iPhone" }] });
  assert.deepStrictEqual(plain(nw.non_watch_dates), ["2026-08-02"]);
  assert.strictEqual(nw.nights.length, 0);
  assert.strictEqual(inp.hr[0][1], first[1]);
  const fraction = O.buildInputs({ tz: 0, samples: [{ kind: "spo2", t: "2026-08-01T00:00:00Z", value: 0.96 }] });
  assert.strictEqual(fraction.spo2[0][1], 96);
  const want = golden("packages/openbodyscore-js/test/golden/engine.json").runs[0];
  const r = C.engine.runs[0];
  const rows = O.computeDays(inp, { first: r.first, last: r.last, today: r.today });
  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "dist", "manifest.json"), "utf8"));
  rows.forEach((row) => {
    assert.strictEqual(row.algo_version, manifest.algo_versions.scores);
    assert.strictEqual(row.bundle_version, manifest.version);
    delete row.bundle_version;
  });
  check(rows, want);
  const one = O.computeDay(inp, r.last, { first: r.first, today: r.today });
  assert.strictEqual(one.date, r.last);
  assert.strictEqual(one.recovery, want[want.length - 1].recovery);
  // default first day = scoring origin (first day with a resting heart rate)
  assert.strictEqual(O.computeDays(inp, { last: r.first, today: r.today })[0].date, Object.keys(e.daily_rhr).sort()[0]);
});

test("api: every entry point stamps algo_version and bundle_version", () => {
  const s = C.stress, r = s.runs[0];
  const stress = O.stressDays({ tz: 600, sleep: s.sleep, workouts: s.workouts, hr: s.hr, steps: s.steps, hrv: s.hrv,
    hrv_night: s.hrv_night, shortcut_dates: s.shortcut }, r.start, r.end);
  const want = golden("packages/openbodyscore-js/test/golden/stress.json").runs[0];
  stress.forEach((row) => { assert.strictEqual(row.algo_version, O.ALGO_VERSIONS.stress); delete row.algo_version; delete row.bundle_version; });
  check(stress, want);
  const h = C.healthspan;
  const data = Object.assign({}, h.data, { sleep_days: O.sleepDays(h.intervals, h.wake_dates, 600) });
  const snap = O.healthspanSnapshot(data, h.today);
  assert.strictEqual(snap.algo_version, "hs-v1.2-2026-10-23");
  assert.ok(O.healthspanWeekly(data, h.origin, h.today).every((x) => x.algo_version === O.ALGO_VERSIONS.healthspan));
  const t = O.trend("recovery_zone", 70);
  assert.deepStrictEqual(plain(t), { value: "green", algo_version: "trends-1", bundle_version: O.VERSION });
  const hrv = O.analyzeHrv([0, 0.8, 1.6], null);
  assert.strictEqual(hrv.algo_version, "rr-v1");
  O.setLanguage("en");
  try { assert.strictEqual(O.trend("recovery_index", 100, 400).value.label, "First half"); } finally { O.setLanguage("zh"); }
});

test("api: IANA time zones and ISO instants (what the phone passes)", () => {
  const syd = U.tzOf("Australia/Sydney");
  // 2026-04-05 03:00 local: daylight saving ends in Sydney (+11:00 -> +10:00)
  assert.strictEqual(U.isoformat(U.toUs("2026-04-04T15:30:00Z"), syd), "2026-04-05T02:30:00+11:00");
  assert.strictEqual(U.isoformat(U.toUs("2026-04-04T16:30:00Z"), syd), "2026-04-05T02:30:00+10:00");
  assert.strictEqual(U.combine("2026-07-01", 0, syd), U.toUs("2026-06-30T14:00:00Z"));
  assert.strictEqual(U.localDate(U.toUs(Date.UTC(2026, 6, 1, 13, 59)), syd), "2026-07-01");
  assert.strictEqual(U.localDate(U.toUs(Date.UTC(2026, 6, 1, 14, 0)), syd), "2026-07-02");
  assert.strictEqual(U.toUs("2026-08-01T23:15:00.123456+10:00") % 1000000, 123456);
  assert.strictEqual(U.toUs(1785512345123.456) % 1000, 456);
});

test("bundle: one file, no dependencies, no DOM / Node APIs, manifest matches", () => {
  const code = fs.readFileSync(path.join(ROOT, "dist", "openbodyscore.js"), "utf8");
  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "dist", "manifest.json"), "utf8"));
  assert.strictEqual(crypto.createHash("sha256").update(code, "utf8").digest("hex"), manifest.sha256);
  assert.strictEqual(Buffer.byteLength(code, "utf8"), manifest.size);
  assert.strictEqual(O.VERSION, manifest.version);
  assert.deepStrictEqual(plain(O.ALGO_VERSIONS), manifest.algo_versions);
  const body = code.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  for (const bad of ["require(", "process.", "window.", "document.", "XMLHttpRequest", "fetch(", "setTimeout", "navigator."]) {
    assert.ok(!body.includes(bad), `the bundle uses ${bad}`);
  }
  assert.ok(!/^\s*(import|export)\b/m.test(body), "the bundle is a script, not a module");
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  assert.strictEqual(manifest.version, pkg.version);
  assert.ok(!pkg.dependencies && !pkg.devDependencies);
});

test.after(() => {
  // not an assertion: how far the two languages actually are apart (the tolerance note in harness.js)
  console.log(`floats that differ from Python: ${worst.count}, largest relative difference ${worst.rel.toExponential(2)} at ${worst.path}`);
});
