# openbodyscore-js

The Watch2Whoop scoring algorithms as **one dependency-free JavaScript file** for the iOS app, which runs it in the
system's JavaScriptCore (no WebView). Issue #340, part N1.

- **The algorithms are the server's.** The Python code (`packages/openbodyscore` and the pure scoring parts of
  `body_engine`) is the reference; this is a port of it, kept equal by cross-language golden tests. The server keeps
  computing in Python (to check the app's numbers, and for users whose app has not uploaded scores yet).
- **Versioned and served by the server.** `GET /v1/algo/manifest` names the current bundle (version, sha256, size,
  algorithm versions, download path); `GET /v1/algo/openbodyscore-<version>.js` is the file. The app downloads a new
  version, checks the sha256 and only then switches to it. The file served is `dist/openbodyscore.js` as committed here.
- **Every result carries `algo_version`** (the server's version string of that algorithm, e.g. `v1.2-2026-10-27`) and
  `bundle_version`, so scores computed on the phone and on the server can be reconciled.

`dist/openbodyscore.js` is ES2020, a classic script (no modules), no DOM, no Node APIs, no network, no timers: after
`JSContext.evaluateScript(code)` everything is under the global `OpenBodyScore`. It needs `BigInt` and `DataView`
(exact `statistics.stdev` and Python's rounding) and, for IANA time zone names, `Intl` — all in JavaScriptCore on iOS 14+.

## Entry points (for the iOS app)

Instants may be **epoch milliseconds** (numbers; fractions = sub-millisecond) or **ISO 8601 strings with an offset**;
local dates are `"YYYY-MM-DD"`; a time zone is an IANA name (`"Australia/Sydney"`) or a fixed offset (`600`, `"+10:00"`).

| Call | Returns |
|---|---|
| `OpenBodyScore.VERSION`, `.ALGO_VERSIONS` | bundle version; `{scores, healthspan, hrv, stress, trends}` |
| `buildInputs(src)` | scoring inputs from the local rows (sleep sessions, raw samples, stage segments, daily resting HR, workout logs) — the rules of `scores/inputs.py`: watch sleep only, one sample per (kind, time) with live samples winning over export.xml copies, SpO₂ fractions to percent, export.zip stages only for nights nothing else covers |
| `computeDays(inputs, {first, last, today, origin})` | the server's `daily_scores` rows for `[first, last]`: sleep score / need / debt / performance, HRV and resting HR of the night, recovery, TRIMP, strain, HRmax, muscle load, CTL / ATL / TSB, `flags`, `details` (same keys and texts as the server). With `seed_rows` / `hr_hist` in the inputs it is the phone's seeded run (below); `origin` then only places the CTL warm-up flag |
| `computeDay(inputs, date, {first, today})` | the row of one day (computed from the scoring origin, so baselines and CTL are the full-history ones) |
| `stressDays(src, start, end)` | `stress_daily` rows (score 0–3, level, hours) |
| `healthspanSnapshot(data, asOf)`, `healthspanWeekly(data, origin, today)` | body age, pace of aging, contributors (v1.2) |
| `sleepDays(intervals, wakeDates, tz)` | the minute states for sleep regularity (`data.sleep_days`) |
| `analyzeHrv(beats, gaps)` | RMSSD / SDNN of one beat-to-beat reading |
| `trend(name, ...args)` | one Trends analysis (`hrv_status`, `training_status`, `load_focus`, `resilience`, `illness_risk`, `chronotype`, `body_battery`, `recovery_time`, `recovery_index`, `baseline_band`, `rolling`, …) as `{value, algo_version, bundle_version}` |
| `trendsSeries(data, {end, days, today, tz, now_hour})`, `monthDays(…)`, `sleepNights(…)`, `sleepDetails(…)`, `vo2max(…)`, `hrZones(…)` | 2.0.0 M3 (`src/33_series.js`, `ALGO_VERSIONS.series`): the JSON of `/trends`, `/days?month=`, `/sleep/nights`, `/sleep/details`, `/vo2max`, `/hr-zones` from what the server's `load` side reads (docs/ios-native-2.0.md section 4 M3) |
| `setLanguage("en")` | English labels for the Trends texts (default Chinese, the stored / AI-facing text) |
| `workoutDays(src)`, `trainingCalendar(data, {start, end, label, today})`, `trainingDay(data, {date, today})`, `muscleLoad(data, today)` | 2.0.0 M1c (`src/31_workouts.js`, `ALGO_VERSIONS.workouts`): the app's workouts with duplicates, TRIMP, five-zone minutes, HR recovery and intensity — the twin of `body_engine` `training/importer.workout_days` — and the JSON of `/training/calendar`, `/training/day/{d}`, `/training/muscle-load` from what `history.load` / `muscle_load.load` read (goldens `test/workouts.test.js`) |
| `strainCoach(today, yday, date)`, `sleepCoach(rows, {date, tz, goal})`, `healthMonitor(rows, date)`, `sleepGoal(goal, sessions, {tz, night, need})`, `sleepNight(data)`, `todayScores(rows, {date, last, last_sleep, computed_at, empty})`, `recoveryEmpty(facts)`, `todaySleep(sessions, {date, tz, timeline})`, `hypnogram(segments, start, end, tz)`, `frequentJournal(items, counts, opts)`, `healthspanLocal(parts, today)` | 2.0.0 M4 (`src/34_coach.js`, `ALGO_VERSIONS.coach`): the JSON of `/strain-coach`, `/sleep-coach`, `/health-monitor`, `/goals/sleep`, `/sleep/night`, the `scores` / `sleep` blocks of `/today`, `/journal/frequent`, and the `healthspan_weekly` rows from what the server's loaders read (`healthspan_store.load_parts`); `OpenBodyScore.coach.TODAY_PARAMS` = the Today tab's thresholds (`body_engine/web/params.py`) (goldens `test/coach.test.js`) |
| `rulesLocal(raw)`, `rulesEvaluate(data)`, `nutritionGoals(versions, from, to, sparky)`, `nutritionDays(totals, versions, from, to, sparky)` | 2.0.0 M6 (`src/36_rules.js`, `ALGO_VERSIONS.rules`): every rule reminder of `evaluate-rules` (`{date, rules_version, rules, reminders}`, texts in Chinese and English) from the raw sources (`rules/context.assemble` + `engine.evaluate_data`; `raw` = the phone's tables + `GET /v1/users/me/rules/inputs`), the nutrition goal of each day (`goals/nutrition.goal_for`) and nutrition day rows from the log's day sums (goldens `test/rules.test.js`) |
| `explainMetric(key, entry, data, day)`, `explainLab(key, entry, data, day, profile)`, `labHistory(key, rows)` | 2.0.0 M6 (`src/37_explain.js`, `ALGO_VERSIONS.explain`): the explanation sheet of a metric / lab test (`explain/personal.py`: rules only, `entry` = the glossary's static texts) from `metric_data`'s rows, and a lab test's history and status card (`records/lab_history.py`) (goldens `test/explain.test.js`) |
| `deriveDays(src)` | 2.0.0 M1 (`src/30_derive.js`): the day's 'app' values per metric, the chosen value among all sources and the nights, from the phone's HealthKit rows — the twin of `body_engine` `rawdata/derive.derive_days` (goldens `test/derive.test.js`) |

The ported modules are also reachable one by one with the Python names (`OpenBodyScore.sleep.sleep_score`,
`.recovery`, `.strain`, `.muscle`, `.stress`, `.healthspan`, `.hrv`, `.trends`, `.engine.score_days`, …). Inside, instants
are integer microseconds (Python's datetime resolution, exact in a double), which is what makes the numbers equal.

```js
// Swift: let ctx = JSContext(); ctx.evaluateScript(bundleSource); then call through ctx.objectForKeyedSubscript("OpenBodyScore")
const inputs = OpenBodyScore.buildInputs({
  tz: "Australia/Sydney", birth_date: "1990-01-01", hrv_kind: "sdnn",
  sleep_sessions: [{ wake_date: "2026-08-02", start: "2026-08-01T23:10:00+10:00", end: "2026-08-02T06:55:00+10:00",
                     asleep_min: 420, core_min: 250, deep_min: 80, rem_min: 90, awake_min: 25, device: "Apple Watch" }],
  samples: [{ kind: "hr", t: 1785589800000, value: 54 }, { kind: "hrv", t: "2026-08-02T01:00:00+10:00", value: 48 }],
  segments: [{ start: "2026-08-01T23:10:00+10:00", end: "2026-08-01T23:40:00+10:00", stage: "core" }],
  daily_rhr: { "2026-08-02": 56 },
});
const rows = OpenBodyScore.computeDays(inputs, { today: "2026-08-02" });   // [{date, recovery, strain, …, algo_version}]
```

## The phone's seeded run (0.2.0, #340 N2b)

The server always recomputes from the scoring origin. The phone keeps raw samples for about 60 days only, so it starts at a
later `first` and brings the history in through two extra fields of `buildInputs(src)` (the server never passes them, and
without them every result is exactly Python's):

- `seed_rows`: the stored `daily_scores` rows of the ~62 days before `first`. Their HRV / resting HR / respiration / wrist
  temperature / sleep performance feed the 60-day baselines, their strain the next night's sleep need, their CTL / ATL
  (`details.load_state` when a seeded run wrote the row: unrounded) and TRIMPs the training load. The actual sleep of those
  nights is recomputed from `sleep_sessions` (exact).
- `hr_hist`: `{date: {bpm: count}}`, the heart-rate histogram of each day without raw samples (the 365-day HRmax window).

Stored values are rounded, so a seeded row can differ from the full recompute by one rounding step (CTL 0.1, recovery 1),
which is exactly what the server's spot check allows (`body_engine/src/body_engine/nativesync/params.CHECK_TOLERANCE`).
`test/golden.test.js` checks three cut points and a chain of two seeded runs against the full recompute. Rows of a seeded
run carry `details.load_state`.

## Inputs only the server can provide

The phone computes a full day from its local database when it has these; they come from the server (and are cached
on the phone with the rest of the synced data):

| Input | Why it is the server's |
|---|---|
| `hrv_kind` (`"sdnn"` / `"rmssd"`) | chosen per user by `body_engine/scores/hrv_kind.py` (switched on for the user and enough beat-to-beat coverage) and never mixed over a user's history |
| History older than the phone's local database | scores are recomputed from the **scoring origin** (first day with a resting heart rate): CTL / ATL are seeded from the first 14 days, HRmax uses a 365-day window, recovery a 60-day baseline (≥ 14 nights), sleep debt 14 nights, stress a 14-day calm baseline and 60 nights of HRV, body age 180 days. After a reinstall or on a new phone this comes back from the cloud backup (raw samples and daily summaries) |
| Data from other devices and sources | the server merges Open Wearables, the Shortcut, export.zip uploads and the app's own sync, deduplicates and picks the source per day (`importers/common.resolve_daily`); a sample another device uploaded is only in the cloud |
| Strength workout records | the muscle load and the body-age strength minutes come from SparkyFitness and imported logs (Hevy), kept on the server |
| Profile | birth date, sex, height, time zone, goal weight (`profile/`) |
| Body-age extras | manual weigh-ins and waist (SparkyFitness), the journal's strength entries and the active training plan (record-coverage label only) |
| `shortcut_dates` | which days only have Shortcut data (flag texts only) |
| Cross-user views | baselines of other people never reach the phone: a shared view (circles, family) is read from the server |

## Tests

```sh
node packages/openbodyscore-js/build.js           # rebuild dist/ after changing src/ (CI checks dist/ is up to date)
node --test packages/openbodyscore-js/test/*.test.js
cd body_engine && uv run pytest tests/test_js_golden.py tests/test_algo_bundle.py
```

- **One set of cases for both languages.** `test/cases.py` (standard library only) builds the synthetic inputs; the Node
  test dumps them with `python3 test/cases.py --dump` and runs the bundle on them. The expected results are Python's:
  `packages/openbodyscore/tests/golden/{scores,healthspan_v12}.json` (every public function of the Python package,
  replayed call for call by `test/golden_cases.js`) and `test/golden/{engine,stress,healthspan,trends}.json` (written by
  `body_engine/tests/test_js_golden.py` from the server's own code): 97 daily score rows, 70 stress rows, 41 body-age
  snapshots, 211 Trends calls and every function of the Python package — about 28,000 compared values.
- **Float tolerance**: 1e-9 relative (and absolute), floats only; keys, nulls, integers, booleans and strings must be
  identical. JavaScript's `Math.exp / log / pow / sin` are not the C library's and differ in the last bit now and then,
  and that grows through the logistic and exp / log chains to ~1e-13 at most (the test prints the largest difference it
  saw). 1e-9 is far below anything stored or shown (integer scores, values rounded to 1–3 decimals).
- `test/golden.test.js` also checks the API layer (buildInputs → computeDays gives the server's rows), the version
  stamps, IANA time zones across a DST change, and that the bundle is a single script without DOM / Node / network APIs.

## 2.0.0 packages (docs/ios-native-2.0.md section 2.2)

Every own-data computation moves to the phone in packages (M1 derive, M1c workouts, M2 stress, M3 series, M4 coach, M5
reports, M6 rules / explain). So parallel packages never touch the same file:

- **Load order.** A package adds one file `src/3N_<pkg>.js` (`30_derive.js`, `31_workouts.js`, `32_stress.js`, `33_series.js`,
  `34_coach.js`, `35_reports.js`, `36_rules.js`, `37_explain.js`). `build.js` concatenates `src/` in name order, so these run
  after `20_api.js` and can use everything it set up.
- **Algorithm versions.** Each file adds its own key: `OBS.ALGO_VERSIONS.series = "…";` and never reassigns
  `OBS.ALGO_VERSIONS` (`build.js` refuses a file that does). The string equals the Python constant that
  `body_engine/src/body_engine/algo/versions.py` names for that key. `tests/test_algo_bundle.py` compares every key in the bundle.
- **Tests.** Goldens per package: `test/cases_<pkg>.py` (synthetic inputs, standard library only),
  `body_engine/tests/test_js_golden_<pkg>.py` (writes `test/golden/<pkg>.json` with `BODY_WRITE_GOLDEN=1`),
  `test/<pkg>.test.js` (replays them on the bundle). CI runs every `test/*.test.js`.
- **dist/** is the only file packages share: the PR that merges second rebases and re-runs `node build.js`. Package PRs do
  not bump `package.json`; the coordinator releases the bundle at the end of each wave.

## Changing an algorithm

1. Change the Python code and bump its version (`ALGO_VERSION`, `STRESS_VERSION`, `TREND_VERSION`, …).
2. Rewrite the goldens: `BODY_WRITE_GOLDEN=1 uv run pytest tests/test_js_golden.py` (and the package's own goldens if they
   change).
3. Port the change to `src/` (same function names as Python), bump `version` in `package.json`, run `build.js`, run the
   Node test. `body_engine/tests/test_algo_bundle.py` fails while the bundle's algorithm versions differ from Python's.
4. Sign it on the maintainer's machine: `body-engine algo-sign --key secrets/algo_signing_key`. This writes `signature` and
   `key_id` into `dist/manifest.json`. The private key never goes into git or onto a server.
   - `build.js` keeps a signature only while the bytes it covers are unchanged.
   - Release builds of the iOS app refuse an unsigned bundle.
   - The public keys are in `signing-keys.json` and, with the same values, in the app's `AlgoSigningKeys.swift`.
   - See `docs/ios-native-app.md` section 6.3.
5. Deploy: the server serves the new `dist/`; apps pick it up from the manifest.

Licence: see [NOTICE](NOTICE).
