// Cross-language goldens of package M3 (docs/ios-native-2.0.md section 2.1): src/33_series.js on the inputs of
// cases_series.py, compared with test/golden/series.json (written by body_engine/tests/test_js_golden_series.py) within the
// float tolerance of harness.js. Run: node --test packages/openbodyscore-js/test/
"use strict";
const assert = require("assert");
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { loadBundle, golden, mismatch, plain } = require("./harness");

const O = loadBundle();
const S = O.series;

function cases() {
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "obs-series-")), "cases.json");
  execFileSync(process.env.PYTHON || "python3", [path.join(__dirname, "cases_series.py"), "--dump", out], { stdio: "pipe" });
  return JSON.parse(fs.readFileSync(out, "utf8"));
}
const C = cases();
const want = golden("packages/openbodyscore-js/test/golden/series.json");
const check = (got, w, p) => { const m = mismatch(plain(got), w, p); assert.strictEqual(m, null, m); };

/** cases_series.variant */
function variant(data, spec) {
  const out = {};
  Object.keys(data).forEach((k) => { if (!(spec.drop || []).includes(k)) out[k] = data[k]; });
  out.can = Object.assign({}, data.can, spec.can || {});
  return Object.assign(out, spec.set || {});
}

test("series: trends/series.compute (every range, end today / past, variants, both languages)", () => {
  const s = C.series;
  assert.strictEqual(s.runs.length, want.series.length);
  s.runs.forEach((r, i) => {
    O.setLanguage(r.lang);
    const got = S.compute(variant(s.data, s.variants[r.data]), r.end, r.days, r.today, s.tz, r.now_hour);
    check(got, want.series[i], `[${i}] ${r.data} ${r.days}d ${r.end}`);
  });
  O.setLanguage("zh");
});

test("series: the entry point is stamped and in_bed may come from start / end (the phone has no in_bed column)", () => {
  const s = C.series, r = s.runs[1];
  const data = variant(s.data, {});
  data.sleep = data.sleep.map((x) => ({ wake_date: x.wake_date, start_at: x.start_at, end_at: x.end_at, asleep_min: x.asleep_min }));
  const got = O.trendsSeries(data, { end: r.end, days: r.days, today: r.today, tz: s.tz, now_hour: r.now_hour });
  assert.strictEqual(got.algo_version, O.ALGO_VERSIONS.series);
  assert.strictEqual(got.bundle_version, O.VERSION);
  delete got.algo_version; delete got.bundle_version;
  check(got, want.series[1], "no in_bed");
});

test("series: web/views month_days and sleep_nights", () => {
  C.month.forEach((m, i) => check(S.month_days(m.month, m.today, m.scores, m.present, m.firsts, m.hidden), want.month[i], `month[${i}]`));
  C.nights.forEach((n, i) => check(S.sleep_nights(n.sessions, n.scores, n.can_scores, n.end, n.days, C.series.tz), want.nights[i], `nights[${i}]`));
});

test("series: insights/sleep_detail (SRI 7 / 30 days, nights)", () => {
  const D = S.sleep_detail;
  C.sleep_detail.forEach((d, i) => {
    check(D.details(D.watch_sessions(d.rows), D.nights_from(d.nights), C.series.tz, d.start, d.end), want.sleep_detail[i], `sleep_detail[${i}]`);
  });
  const e = C.sleep_detail[0];
  const viaApi = O.sleepDetails(e.rows, e.nights, { end: e.end, tz: C.series.tz });
  assert.strictEqual(viaApi.algo_version, O.ALGO_VERSIONS.series);
  delete viaApi.algo_version; delete viaApi.bundle_version;
  check(viaApi, want.sleep_detail[0], "sleepDetails");
});

test("series: insights/fitness (VO₂max level, zone totals, level edges)", () => {
  const F = S.fitness, f = C.fitness;
  f.vo2.forEach((v, i) => {
    O.setLanguage(v.lang);
    check(F.vo2max(v.rows, v.birth, v.sex, v.end), want.vo2[i], `vo2[${i}]`);
  });
  O.setLanguage("zh");
  f.zones.forEach((z, i) => check(F.hr_zones(z.rows, z.workouts, f.zones_start, f.end, z.with), want.zones[i], `zones[${i}]`));
  check(f.levels.map((p) => F.level(p)), want.levels, "levels");
  // the API maps users.sex like the server (SEXES)
  const v = f.vo2[0], api = O.vo2max(v.rows, v.birth, " M ", v.end);
  delete api.algo_version; delete api.bundle_version;
  check(api, want.vo2[0], "vo2max api");
});

test("series: algo version is the Python constant's (algo/versions.py: trends/series.py SERIES_VERSION)", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "..", "body_engine", "src", "body_engine", "trends", "series.py"), "utf8");
  const m = /^SERIES_VERSION = "([^"]+)"/m.exec(src);
  assert.ok(m, "SERIES_VERSION not found in trends/series.py");
  assert.strictEqual(O.ALGO_VERSIONS.series, m[1]);
});
