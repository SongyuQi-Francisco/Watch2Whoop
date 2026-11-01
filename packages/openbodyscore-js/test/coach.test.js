// Cross-language goldens of package M4 (docs/ios-native-2.0.md section 2.1): src/34_coach.js on the inputs of
// cases_coach.py, compared with test/golden/coach.json and coach_local.json (written by
// body_engine/tests/test_js_golden_coach.py) within the float tolerance of harness.js. Texts in both languages exactly.
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
const K = O.coach;

function cases() {
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "obs-coach-")), "cases.json");
  execFileSync(process.env.PYTHON || "python3", [path.join(__dirname, "cases_coach.py"), "--dump", out], { stdio: "pipe" });
  return JSON.parse(fs.readFileSync(out, "utf8"));
}
const C = cases();
const want = golden("packages/openbodyscore-js/test/golden/coach.json");
const check = (got, w, p) => { const m = mismatch(plain(got), w, p); assert.strictEqual(m, null, m); };
const unstamp = (x) => { delete x.algo_version; delete x.bundle_version; return x; };
const withLang = (lang, fn) => { O.setLanguage(lang); try { return fn(); } finally { O.setLanguage("zh"); } };

/** test_js_golden_coach.timeline_of */
function timelineOf(row, segs, tz) {
  if (!row) return null;
  const stages = K.today.hypnogram(segs || [], row.start_at, row.end_at, tz);
  return stages.length ? [stages, "app", null] : [[], null, "这晚没有阶段明细"];
}

test("coach: version and every threshold are the Python's", () => {
  assert.strictEqual(O.ALGO_VERSIONS.coach, want.version);
  const p = want.params;
  check(K.params, p.coach, "params.coach");
  const gp = Object.assign({}, K.goals.params);
  check(gp, p.goals, "params.goals");
  check(K.sleep_night.params, p.sleep_night, "params.sleep_night");
  check(K.today.params, p.today, "params.today");
  check(K.TODAY_PARAMS, p.today_params, "params.today_params");
});

test("coach: insights/coach strain_coach_compute (zones, illness, TSB shift, missing strain)", () => {
  assert.strictEqual(C.strain.length, want.strain.length);
  C.strain.forEach((x, i) => check(K.strain_coach_compute(x.today, x.yday, x.d), want.strain[i], `strain[${i}]`));
  const x = C.strain[0];
  check(unstamp(O.strainCoach(x.today, x.yday, x.d)), want.strain[0], "strainCoach");
});

test("coach: insights/coach sleep_coach_compute (goal / need / recent wake times, DST)", () => {
  C.sleep_coach.forEach((x, i) => check(K.sleep_coach_compute(x.rows, x.d, C.tz, x.goal), want.sleep_coach[i], `sleep_coach[${i}]`));
  const x = C.sleep_coach[1];
  check(unstamp(O.sleepCoach(x.rows, { date: x.d, tz: C.tz, goal: x.goal })), want.sleep_coach[1], "sleepCoach");
});

test("coach: insights/coach health_monitor_compute (both languages)", () => {
  C.monitor.forEach((x, i) => withLang(x.lang, () => check(K.health_monitor_compute(x.rows, x.d), want.monitor[i], `monitor[${i}]`)));
  check(unstamp(O.healthMonitor(C.monitor[0].rows, C.monitor[0].d)), want.monitor[0], "healthMonitor");
});

test("coach: goals view_compute and the goals/sleep helpers", () => {
  C.goals.forEach((x, i) => check(K.goals.view_compute(x.goal ? K.goals.with_plan(x.goal) : null, x.sessions, C.tz, x.night, x.need),
    want.goals[i], `goals[${i}]`));
  const fns = { to_off: K.goals.to_off, fmt: K.goals.fmt, round5: K.goals.round5, build_plan: K.goals.build_plan,
    night_of: (t, tz) => K.goals.night_of(t, tz) };
  C.goal_calls.forEach((x, i) => check(fns[x.fn].apply(null, x.args), want.goal_calls[i], `goal_calls[${i}] ${x.fn}`));
  const x = C.goals[1];
  check(unstamp(O.sleepGoal(x.goal, x.sessions, { tz: C.tz, night: x.night, need: x.need })), want.goals[1], "sleepGoal");
});

test("coach: sleep_night compute and notes (both languages)", () => {
  C.sleep_night.forEach((x, i) => withLang(x.lang, () => {
    const row = K.today.main_rows(x.sessions, x.day, x.day)[x.day] || null;
    const data = Object.assign({}, x, { timeline: timelineOf(row, x.segments, x.tz) });
    check(K.sleep_night.compute(data), want.sleep_night[i], `sleep_night[${i}] ${x.day}`);
  }));
  C.notes.forEach((x, i) => withLang(x.lang, () => check(K.sleep_night.notes(x.night, x.score, x.base, x.vitals, x.flags),
    want.notes[i], `notes[${i}]`)));
});

test("coach: the Today composition (scores, recovery_empty, sleep, hypnogram, frequent journal)", () => {
  const t = C.today, w = want.today;
  t.scores.forEach((x, i) => withLang(x.lang, () => check(K.today.scores_today(x.rows, x.day, x.last, x.last_sleep, x.computed_at, x.empty),
    w.scores[i], `today.scores[${i}] ${x.day}`)));
  t.empty.forEach((x, i) => withLang(x.lang, () => check(K.today.recovery_empty(x.day, x.today, x.local_today, x.needs_consent, x.origin,
    x.any_data, x.recent_watch_sleep), w.empty[i], `today.empty[${i}]`)));
  t.sleep.forEach((x, i) => {
    const row = K.today.latest_main(x.sessions, x.day);
    check(K.today.sleep_today(x.sessions, x.day, t.tz, timelineOf(row, x.segments, t.tz)), w.sleep[i], `today.sleep[${i}]`);
  });
  t.hypnogram.forEach((x, i) => check(K.today.hypnogram(x.segments, x.start, x.end, t.tz), w.hypnogram[i], `today.hypnogram[${i}]`));
  t.journal.forEach((x, i) => check(K.today.frequent_journal(x.items, x.counts, x.days, x.limit, x.min_items), w.journal[i],
    `today.journal[${i}]`));
});

test("coach: healthspan_store from_parts + weekly_local (body age from the phone's tables)", () => {
  Object.keys(want.healthspan).forEach((v) => check(K.healthspan.weekly_local(C.healthspan[v], C.end), want.healthspan[v], `healthspan.${v}`));
  const rows = O.healthspanLocal(C.healthspan.full, C.end);
  rows.forEach((r) => { assert.strictEqual(r.algo_version, O.ALGO_VERSIONS.coach); unstamp(r); });
  check(rows, want.healthspan.full, "healthspanLocal");
});

test("coach: the iOS fixture (body-age parts -> rows, the Today thresholds)", () => {
  const ios = golden("packages/openbodyscore-js/test/golden/coach_local.json");
  check(O.healthspanLocal(ios.parts, ios.today).map(unstamp), ios.rows, "coach_local.rows");
  check(unstamp(O.strainCoach(ios.strain.today, ios.strain.yday, ios.strain.d)), ios.strain_out, "coach_local.strain");
  check(K.TODAY_PARAMS, ios.today_params, "coach_local.today_params");
});

test("coach: Python formatting helpers", () => {
  assert.deepStrictEqual([82, 82.5, 0.65, 1234567, 0.00001234, 100, -0.75].map(K.fmtG),
    ["82", "82.5", "0.65", "1.23457e+06", "1.234e-05", "100", "-0.75"]);
  assert.deepStrictEqual([[-0.3, true], [0.5, false], [1.5, false], [-12.4, true], [7, true]].map((a) => K.fmt0(a[0], a[1])),
    ["-0", "0", "2", "-12", "+7"]);
});
