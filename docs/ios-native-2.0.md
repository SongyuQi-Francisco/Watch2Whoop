# iOS 2.0.0: every own-data computation on the phone (S0 contract)

Status: contract for the agents that implement 2.0.0 (#337 local-first, #340 native app, #219 own sync engine).
Read with [ios-native-app.md](ios-native-app.md) (local DB, sync, algorithm bundle), [apple-sync-engine.md](apple-sync-engine.md)
(OW retirement, §5 and §10) and [rollout.md](rollout.md) (flags, cohorts). Language of this file: English.

## 0. Decisions this contract implements (owner, 2026-10-29)

| # | Decision |
|---|---|
| D1 | 2.0.0 ships only after **every computation of a user's own data that the server still does for the app** ("三-A") runs on the phone. The native app is renumbered 2.0.0; the internal 1.0.9 build never ships. |
| D2 | After 2.0.0, Open Wearables (OW) sync and the iOS Shortcut upload are discontinued. HealthKit through the app's own engine is the only live source. |
| D3 | Every user is force-upgraded: 7-day in-app notice, then blocking. Blocking covers the native app and the web Today page (also inside 1.0.8); **never MCP or API tokens**. |
| D4 | The server keeps a synced copy of every user's raw data and computed results (backup, restore on reinstall, AI / MCP, circles). |
| D5 | Algorithms are defined by the server: one JS bundle (`packages/openbodyscore-js`), the server's Python is the reference twin; cross-language golden tests keep them equal. Prefer JS for algorithms; Swift only for I/O, local SQL and UI. |
| D6 | Research rule: experiments, tuning and detailed comparisons use only user 1's data and synthetic data. Other users' data is backup + product features only. Consent text unchanged. |
| D7 | Thresholds live in `*/params.py` (Python) with their source, mirrored in JS params; nothing is tuned to one person's data. |

## 1. Inventory: what the native app still reads from server computation

### 1.1 Already on the phone (N2b / N4; not re-done here)

| What | Where on the phone |
|---|---|
| Daily scores (`daily_scores`: recovery, sleep score / need / debt, strain, TRIMP, CTL / ATL / TSB, HRmax), seeded and full-history | `computeDays` in `Sync/ScoringSync.swift`, upload `PUT /v1/users/me/scores/app` |
| Beat-to-beat HRV (RMSSD / SDNN) | `analyzeHrv` in `Sync/LocalRawStore.swift` |
| Status cards: HRV status, training status, load focus, illness risk, recovery index, recovery time, chronotype | `trend(...)` in `Features/Trends/TrendsLocal.swift` |
| Today anomalies ("需要注意") | `Features/Today/TodayParams.swift` (Swift, own params with sources) |

### 1.2 Moves to the phone

`JS` = new file in `packages/openbodyscore-js/src/`, ported from the Python named in the first column (that Python stays the reference).
`Swift` = local SQL / view assembly only, no thresholds.

| Server module (functions) | Endpoint the app reads today | What the app shows | Target | Pkg |
|---|---|---|---|---|
| `rawdata/derive.py` `daily_rows` | `daily` pull stream (server-derived `daily_metrics`) | steps, energy, exercise min, vitals day values | JS `30_derive.js` | M1 |
| `importers/common.py` `resolve_daily`, `incomplete_days`, `choose`, `normalize` | same | the chosen value per (date, metric), `flags.incomplete` | JS `30_derive.js` | M1 |
| `rawdata/derive.py` `clean_stages`, `ow_sessions`, `nights`; `importers/ow.sleep_segments`; `importers/common.sessionize` | `sleep` pull stream | sleep sessions, stages, hypnogram, wake-day assignment | JS `30_derive.js` | M1 |
| `rawdata/derive.py` `workout_rows`; `training/importer.py` `parse_app`, `refresh_duplicates` rules | `records` stream kind `workout`, `/training/day/{d}` | watch workouts, duplicates | JS `31_workouts.js` | M1c |
| `training/importer.py` `session_trimp`, `avg_hr_trimp`, `hr_max_for`, `session_hr_recovery`, `enrich` (zones) | `/training/day/{d}`, `/training/calendar` | TRIMP, five-zone minutes, HR recovery per workout | JS `31_workouts.js` | M1c |
| `cardio/hrr.py` `hrr60`, `compute` | (feeds `workout_hrr`, `/trends` `hr_recovery`) | 60-s HR recovery | JS `31_workouts.js` | M1c |
| `training/history.py` (day merge, intensity), `training/muscle_load.py` (`aggregate`, `aggregate_sports`, `sport_load`) | `/training/calendar`, `/training/day/{d}`, `/training/muscle-load` | training calendar, day detail, muscle map | JS `31_workouts.js` | M1c |
| `insights/stress.py` `compute` (loader), `score_range` (JS already: `stressDays`) | `/trends` series `stress`, `/stress` | stress score / level / hours | Swift loader + existing JS | M2 |
| `trends/calc.py` `resilience`, `body_battery`, `hr_recovery`, `hr_recovery_level` (JS already) | `/trends` status cards | resilience, body battery, HR-recovery card | Swift (`TrendsLocal`) | M2 |
| `cardio/views.py` `cardio` | `/cardio` (AI only today) | HRR trend for the AI card | JS `32_stress.js` | M2 |
| `trends/series.py` `build` (pure part), `baseline_band`, `rolling`, `hr_zone_summary` | `/trends?days=&end=` | every Trends series, `prev_mean`, baseline bands, zones, weekly aerobic min | JS `33_series.js` | M3 |
| `web/views.py` `month_days`, `sleep_nights` | `/days?month=`, `/sleep/nights` | month calendar colours, last 14 nights | JS `33_series.js` | M3 |
| `insights/sleep_detail.py` `asleep_minutes`, `sri`, `consistency_pct`, `nights` | `/sleep/details` | sleep regularity card | JS `33_series.js` | M3 |
| `insights/fitness.py` `hr_zones`, `vo2max`, `level` | `/hr-zones`, `/vo2max` | zone totals, VO₂max level | JS `33_series.js` | M3 |
| `insights/coach.py` `strain_coach`, `sleep_coach`, `sleep_need_tonight`, `health_monitor`, `monitor_range` | `/strain-coach`, `/sleep-coach`, `/health-monitor` | load target, sleep coach, health monitor ranges | JS `34_coach.js` | M4 |
| `goals/sleep.py` (`build_plan`, `progress`, `target_for`, `trend_per_week`), `goals/store.py` `compute` | `/goals/sleep` | bedtime goal plan and progress | JS `34_coach.js` | M4 |
| `sleep_night/view.py` `notes`, `_baseline`, `_vitals`, `_efficiency` | `/sleep/night` | sleep detail sheet | JS `34_coach.js` | M4 |
| `insights/healthspan_store.py` `load`, `weekly`, `strength_sources` (snapshot already JS) | `/healthspan`, `/trends` `body_age` | body age, pace of aging | Swift loader + existing JS | M4 |
| `web/views.py` `reasons`, `load_status`, `load_text`, `scores_today`, `sleep_today` | `/today` | ring reasons, load word, hypnogram block | JS `34_coach.js` | M4 |
| `insights/reports.py` `report` and blocks | `/reports?period=&end=` | weekly / monthly report | JS `35_reports.js` | M5 |
| `records/analysis.py` `compare`, `regress`, `apply_bh`; `records/regression.py` `ols_hac`, `bh` | `/journal/analysis` | behaviour → next-day impact | JS `35_reports.js` | M5 |
| `web/views.py` `frequent_journal` | `/journal/frequent` | frequent journal items | JS `34_coach.js` (owner of `web/views.py` in wave 3) | M4 |
| `rules/*` (`engine.evaluate_ctx`, `nutrition`, `progression`, `safety`, `sleep_goal`, `hydration`), `rules/context.load` | `/reminders`, `/today` training block, `/training/week` | reminders, load suggestions, cautions | JS `36_rules.js` + Swift context loader | M6 |
| `goals/nutrition.py` `version_for`, `day_type`, `resolve`; `web/views.nutrition_today` | `/today` nutrition block, `/goals/nutrition` | day targets and progress rings | JS `36_rules.js` | M6 |
| `explain/personal.py` (`calc_*`, `explain_metric`, `explain_status`, `explain_lab`), `records/lab_history.py` `trend`, `card` | `/explain/metric/{k}`, `/explain/status/{k}`, `/explain/lab/{k}`, `/labs/summary` | explanation sheets, lab arrows | JS `37_explain.js`; static texts from the static bundle (`glossary` module) | M6 |

### 1.3 Stays on the server (not 三-A)

| What | Why |
|---|---|
| Account, consent, profile writes, AI memory (`/memory`), AI providers / tokens, Jarvis chat, fallback AI, ASR | not a computation of watch data; online-only features (ios-native-app.md §5.5) |
| Circles, shares, leaderboards, feed | cross-user; others' data never reaches the phone |
| `hrv_kind` choice (`scores/hrv_kind.py`), scoring origin | per-user configuration, read once via `/v1/sync/scoring`; the phone may recompute the origin from local `daily_metrics` but uses the server's |
| `export.zip` history import (`history/`, `importers/apple_zip.py`) | one-time ingest, admin path; its days reach the phone as `raw_history` and as non-`app` source rows (M1) |
| Static content (glossary texts, exercise catalog, sport → muscle map) | shipped through the static bundle (`static_bundle/`); M1c and M6 add modules there |
| Sync status (`/sync/overview`, `/data-status`) | server-side knowledge of what arrived |
| AI / MCP on-demand views (`get_strain_coach`, `get_report`, `analyze_journal` …) | the AI path, not the app path: tool calls compute on demand with the Python twin at the same `algo_version`, reading the tables the phone uploads (§3.2). This is not "computing for the app" (decision 4, §6) |

## 2. Shared rules for every package

### 2.1 Twins and goldens
- One reference function per algorithm in Python. Where today's Python mixes SQL and logic, the package first splits it into
  `load(conn, …) -> data` + a pure `compute(data, …)`; the HTTP view calls both, unchanged output (existing tests stay green).
- The JS port uses the Python names (`OBS.<module>.<fn>`) and adds one entry point per page need, stamped with `algo_version` and `bundle_version`.
- Goldens: per package `packages/openbodyscore-js/test/cases_<pkg>.py` (synthetic inputs, standard library only),
  `body_engine/tests/test_js_golden_<pkg>.py` (writes `test/golden/<pkg>.json` from the Python with `BODY_WRITE_GOLDEN=1`),
  `packages/openbodyscore-js/test/<pkg>.test.js` (replays them on the bundle). Float tolerance 1e-9 relative unless the package says otherwise; keys, nulls, strings, integers identical.
- Texts (labels, notes, reminder texts) are part of the output and compared exactly, both languages (`setLanguage`).
- Synthetic data only in goldens and Swift tests (`quickstart/demo_data.py` style); never real exports.

### 2.2 File ownership and P0 (scaffolding PR, coordinator, before wave 1)
Only generated files may overlap between parallel packages: `packages/openbodyscore-js/dist/*`. The PR that merges second rebases and re-runs `node build.js`.
P0 creates every shared seam once, so packages only add their own files:

| Seam | P0 change |
|---|---|
| JS load order | packages add `src/3N_<pkg>.js` (after `20_api.js`); each file extends `OBS.ALGO_VERSIONS.<key>` and never reassigns it |
| Algorithm versions | `body_engine/src/body_engine/algo/versions.py`: planned keys `derive`, `workouts`, `stress_cards`, `series`, `coach`, `reports`, `rules`, `explain` → Python constant path; `tests/test_algo_bundle.py` checks only keys present in the bundle |
| CI | `algo_js` filter widened to `rawdata|importers|training|cardio|rules|explain|goals|sleep_night|records|web/views.py|static_bundle` and `tests/test_js_golden[^/]*\.py`; Node step runs `test/*.test.js` |
| Server migrations | `db.migrate` sorts by the numeric prefix (string sort puts `1000_` before `001_`; test: `1000_x.sql` sorts after `999_x.sql`, existing order unchanged); ranges: M1 1000–1009, M1c 1010–1019, M2 1020–1029, M4 1030–1039, M5 1040–1049, M6 1050–1059, R1 1060–1069, R2 1070–1079, S9 1080–1089 (M3 adds none) |
| Upload / pull per kind | `nativesync/derived.py`: `PUT /v1/users/me/derived/app` + kind registry auto-loading `nativesync/derived_kinds/<kind>.py` (each module: row validation, table write, Python recompute for the spot check, its pull-stream body); `pull.py` / `bodies.py` iterate the registry, so later kinds never edit them; generic spot check and state (§3.2) |
| Flags | all keys of §5 registered in `rollout/registry.py`, default `off` |
| iOS compute pipeline | `Sync/Compute/LocalCompute.swift`: ordered stages `derive → workouts → scores (existing ScoringSync) → stress → healthspan → rules`, one empty stage file per package (`Sync/Compute/<Stage>Stage.swift`) |
| iOS schema | `Storage/Migrations/<Pkg>Schema.swift` stubs called from `Schema.swift` in the order above; nothing ships externally before 2.0.0, so the order is final at release |
| iOS endpoints | `Endpoint.derivedUpload`, `Endpoint.compute` (`GET /v1/sync/compute`, superset of `/v1/sync/scoring`), `Endpoint.extRecords` |
| `ext_records` doc kind (pull only) | Server `nativesync/ext_records.py`: `GET /v1/users/me/ext-records?from=&to=` returns per day `{date, fingerprint, strength_sets, weights, nutrition_totals}`: the person's **raw** SparkyFitness rows (strength sets with exercise, reps, weight, duration; weight entries; nutrition day totals) or the own-store rows when `own_food_only` is on, read with the read-only key, no computation; `?fingerprints=1` returns only `{date: fingerprint}` (sha256 of the day's canonical JSON). iOS `Sync/ExtRecords.swift`: a `DocPuller` kind storing `docs` key `ext_records:<date>`, refetching only days whose fingerprint changed; never written by the phone |

## 3. Data contract

### 3.1 Local tables (GRDB, per account) written / read by each package

| Pkg | Writes (new tables in bold) | Reads |
|---|---|---|
| M1 | `hk_samples` (all `ENGINE_TYPES`, not only the 7 scoring kinds), `hk_stat_buckets` (15-min sums), **`daily_metric_sources`** (date, metric, source, value, unit, agg, n, min, max, flags), `daily_metrics` (chosen, `source`, `flags`), `sleep_sessions` (source `app`, `stages`), `score_dirty` | HealthKit, `hist_*`, `hk_workouts.stats` (workout kcal for `incomplete`), pulled non-`app` source rows |
| M1c | `hk_workouts`, **`workouts`** (uuid, local_date, type, start, end, duration_min, active_kcal, total_kcal, avg_hr, max_hr, trimp, zone_min[5], hr_recovery_bpm, intensity, dup_of, excluded, source, flags, algo_version), **`workout_hrr`** | `hk_samples` hr, `hist_samples` hr, `daily_scores.hr_max`, `records` (`strength`, `training_session`), `ext_records`, static bundle (catalog, sport map) |
| M2 | **`stress_daily`** (date, score, level, hours, algo_version) | `hk_samples` / `hist_samples` hr + hrv + steps, `sleep_sessions`, `workouts`, `daily_scores.details.recovery.hrv_night_ln` |
| M3 | nothing persistent; caches `docs` keys `trends.local.series:<days>[:<end>]`, `days.local:<month>`, `sleep.local:nights:<end>` | `daily_scores`, `daily_metrics`, `sleep_sessions`, `records` (`weight`), `ext_records`, and when present `stress_daily`, **`healthspan_weekly`**, `workouts` |
| M4 | **`healthspan_weekly`**, `docs` `coach.local:<date>`, `sleepnight.local:<date>` | M1–M3 tables, `records`, `ext_records`, profile (birth date, sex, height from `/profile` doc) |
| M5 | `docs` `report.local:<period>:<end>`, `journal.analysis.local:<end>` | `daily_scores`, `stress_daily`, `workouts`, `records` (`journal`, `food`, `weight`), `ext_records` |
| M6 | **`reminders`** (id, date, rule_key, level, payload, status, algo_version, uploaded), `docs` `explain.local:*` | everything above, `records`, nutrition goals doc, `exercise:rules` doc, labs docs |

Rules: every computed row carries `algo_version` + `bundle_version`; pages read only local tables / docs (ios-native-app.md §5.1); a
package must keep the server doc as fallback while its kind is not `primary` (§5) and hide a block, never show stale numbers as today's.

### 3.2 Upload: what the server copy keeps

`PUT /v1/users/me/derived/app` `{"kind", "algo_version", "bundle_version", "days": [{"date", "rows": […]}]}`, ≤ 400 days, newest batch first, only "settled"
days (same `waiting_sync` rule as scores: after `HealthSyncService.uploadedEverything`).

| kind | Server table written (`origin = 'app'`, `bundle_version`) | Who reads it on the server |
|---|---|---|
| `daily` | `daily_metric_sources` (source `app`) + `daily_metrics` (chosen row) | AI tools, circles, Grafana, spot checks |
| `sleep` | `sleep_sessions` (source `app`, segments) | AI, circles (sleep share), `raw_history` |
| `workouts` | `workouts` enrichment columns by `external_id` (trimp, zones, hrr, dup_of), `workout_hrr` | AI `get_training_*`, circles feed |
| `stress` | `stress_daily` | AI `get_stress`, reports for the AI |
| `healthspan` | `healthspan_weekly` | AI `get_healthspan`, circles |
| `reminders` | `reminders` (status changes stay ordinary writes: `PATCH /reminders/{id}` via the offline queue) | AI `get_reminders` |
| (scores) | unchanged: `PUT /v1/users/me/scores/app` | — |

Views (`series`, coach texts, reports, explain, journal analysis) are **not** uploaded: they are recomputed on demand from the tables above
by the server's Python for the AI (§1.3). Downstream: `daily`, `sleep`, `workouts`, `stress` are inputs of later kinds, so the server spot check
of a kind uses the phone's uploaded inputs of the kinds below it.

Pull (restore): the existing streams carry the uploaded rows back; M1 adds `sources` to the `daily` body (per metric: every source row, so
the phone can re-resolve days that mix `ow` / `apple_export` / `shortcut` / `app`); M1c adds record kind `workout` enrichment; M2 / M4 / M6 add
stream kinds `stress`, `healthspan`, `reminders` to `records` (whole-day replace, as today), each through its own `derived_kinds` module.

### 3.3 Algorithm versions
- Each key in `OBS.ALGO_VERSIONS` equals one Python constant (`algo/versions.py`); `test_algo_bundle.py` fails on any difference.
- The server accepts an upload only when `algo_version` equals its own for that kind, else `stale_algo` (the app fetches the bundle and recomputes). Changing an algorithm = Python + JS + goldens + version bump in one PR (openbodyscore-js README "Changing an algorithm").
- A bundle change marks the affected days dirty per kind (`score_dirty` gains a `kind` column in M1's migration; default `scores`).
- Bundle releases happen at wave ends (coordinator): bump `package.json` (0.3.0 after wave 1 … ), `build.js`, sign (`body-engine algo-sign`). Package PRs do not bump the version.

### 3.4 Shadow compare
Two layers; neither moves values of non-research users into analysis. The automatic spot check is product-safety maintenance, like `app_score_checks`, and runs for every shadow / primary user, inner cohort included; it stores only pass / fail and field names. Detailed comparisons and any analysis use user 1 + synthetic data only (decision 5).

| Layer | Who | What is kept |
|---|---|---|
| Automatic spot check (product safety, like `app_score_checks`) | every user whose kind is `shadow` or `primary` | table `app_compute_checks` (user, kind, day, bundle_version, ok, fields_over: names only); the server recomputes one settled day per kind (first upload of a bundle, then ≤ every 6 h, newest day within 14 days) |
| Developer comparison (tuning, regressions) | user 1 + synthetic only | `body-engine native-compare --user-id N --kind K --days D` runs the bundle in Node and the Python on the same rows and prints per-field max differences; refuses any user not in `BODY_RESEARCH_USERS` (default `1`) |

Tolerances: `nativesync/params.COMPUTE_TOLERANCE[kind][field]` (absolute unless noted), sources in the comment:

| kind | field → tolerance |
|---|---|
| `daily` | avg values 1e-4 relative; sum values 0.5 unit (rounding of 15-min buckets); `n`, `source`, `flags` exact |
| `sleep` | session start / end ±1 s; stage minutes ±0.5; wake date, `is_main` exact |
| `workouts` | trimp 0.1; each zone ±0.1 min; hr_recovery_bpm 0.5; dup_of, intensity exact |
| `stress` | score 0.01; level exact; hours: level per hour exact |
| `healthspan` | body_age 0.1 y; pace 0.01; contributor keys exact |
| `reminders` | the set of (rule_key, level) exact; payload numbers 1e-6; texts exact |
| views (dev compare only) | series values and `prev_mean` 1e-9 rel; coach target 0.1; sleep need 1 min; report numbers at their display rounding; analysis effect / p / q 1e-6 |

## 4. Work packages

| Wave | Packages (‖ = parallel) |
|---|---|
| 0 | **P0** scaffolding (§2.2) |
| 1 | **M1** daily values + sleep derivation ‖ **M3** Trends series |
| 2 | **M1c** workouts / TRIMP / zones / HRR ‖ **M2** stress, resilience, body battery, HR recovery |
| 3 | **M4** coaches, body age, health-monitor ranges ‖ **M5** reports + journal analysis |
| 4 | **M6** rules + explain texts; **R1** OW and Shortcut retirement; **R2** forced upgrade |
| 5 | **S9** server stops computing for app-mode users + regression |
| 6 | release 2.0.0 |

Every package: flags off by default (§5), synthetic tests, `CHANGELOG` line in `apps/ios/CHANGELOG.md` under 2.0.0, one device-checklist entry
(collected into `docs/ios-native-2.0-device-checklist.md` by S9). "Files" lists what the package may touch; anything else needs the coordinator.

### M1 — daily values and sleep derivation (producer only, no page code)
| | |
|---|---|
| Scope | Read every `ENGINE_TYPES` type and 15-min stats into local tables; add **steps** (15-min stats buckets) to `raw_history` and to the phone's history area (`hist_samples` kind `steps`), so derivation, stress (M2) and body age (M4) have steps for the whole history; derive per-source daily rows (`daily_rows`), resolve (`resolve_daily` incl. `incomplete` rules and `SOURCE_PRIORITY`), derive sleep (`ow_sessions` → `clean_stages` → `sleep_segments` → `sessionize`, wake day = end − 4 h) for the app source; upload kinds `daily`, `sleep` |
| Inputs | HealthKit anchored queries (add types to the reader), `hist_*` (now with steps), pulled `daily.sources` |
| Outputs | `daily_metric_sources`, `daily_metrics`, `sleep_sessions`; `score_dirty` for changed days (ScoringSync recomputes) |
| Files | JS `src/30_derive.js`; server `rawdata/derive.py`, `importers/common.py`, `importers/ow.py` (pure rule split only), `nativesync/bodies.py` + `pull.py` (`sources`), `nativesync/history.py` (steps in `raw_history`), `nativesync/derived_kinds/{daily,sleep}.py`, migrations 1000–1009; iOS `Features/Sync/HealthKitScoringReader.swift`, `Sync/LocalRawStore.swift`, `Sync/LocalHistoryStore.swift`, `Sync/LocalHistoryParams.swift`, `Sync/DeltaRemote.swift`, `Sync/Compute/DeriveStage.swift`, `Storage/Migrations/DeriveSchema.swift`; tests `test_js_golden_derive.py`, `cases_derive.py`, `derive.test.js`, `Watch2WhoopTests/LocalDeriveTests.swift` |
| Goldens | ≥ 60 synthetic days: iPhone + watch overlap, DST change, a day with only hourly sums, basal < 50 % day, workout kcal > active kcal, gaps > 2 h and > 60 min, nap vs main sleep, deleted samples |
| Acceptance | Python and JS equal on goldens; Swift test: 30 synthetic days through HealthKit fixtures give the same rows as the server's `derive_day` + `resolve_daily` on the same samples; uploads pass the spot check |
| Device | Airplane mode after a night: steps, energy and last night's sleep appear from local derivation; reconnect: no duplicate rows after pull |

### M3 — Trends series (reader of local tables; no migration, no upload)
| | |
|---|---|
| Scope | Port the pure part of `trends/series.build` (every `SERIES` entry, `prev_mean`, baseline bands, recovery colours and contributors, strain target band, weekly aerobic minutes, hr zone summary), `month_days`, `sleep_nights`, `sleep_detail` (SRI), `fitness.vo2max` / `hr_zones`; switch Trends, Today's "今日核心" / activity numbers and the month picker to local tables |
| Inputs | local `daily_scores`, `daily_metrics`, `sleep_sessions`, `records(weight)` + `ext_records` weights (`series.weights` reads SparkyFitness today); until M2 / M4 are primary, `stress`, `resilience`, `body_age` come from the cached server doc |
| Outputs | JSON identical to `/trends` (minus share authorisation), cached in `docs` |
| Files | JS `src/33_series.js`; server `trends/series.py`, `web/views.py` (`month_days`, `sleep_nights` split only), `insights/sleep_detail.py`, `insights/fitness.py`; iOS `Features/Trends/*`, `Features/Today/TodayStore.swift`, `Features/Today/TodayModel.swift`, `Features/Today/MonthPicker.swift`; tests `test_js_golden_series.py`, `cases_series.py`, `series.test.js`, `Watch2WhoopTests/TrendsSeriesTests.swift` |
| Goldens | ranges 7 / 30 / 90 / 365, end = today and in the past, missing days, weight from records, < 14 baseline days |
| Acceptance | Swift: `/trends` doc vs local series on 120 synthetic days, every value within 1e-9; Trends 365-day scroll stays ≥ 55 fps (ios-native-app.md §9.3 method) |
| Device | Trends opens offline with all ranges; Today "全部指标" matches Trends "今天" |

M1 and M3 share no file except `dist/*`.

### M1c — workouts, TRIMP, zones, HR recovery, training views
| | |
|---|---|
| Scope | Workouts from `hk_workouts` (`parse_app`: OW type map, total = active + basal), duplicate rules (`refresh_duplicates`, `SOURCE_RANK`), `enrich` (TRIMP from raw HR, else `avg_hr_trimp`; five-zone minutes; `session_hr_recovery`; `hrr60`), training calendar / day merge with strength records and intensity (`training/history.py`), muscle-load map (`muscle_load.aggregate*`, `sport_load`); upload kind `workouts` |
| Inputs | `hk_workouts`, HR samples (hot + history), `hr_max` from scores, `records` strength / sessions, `ext_records` (strength sets: muscle load and the strength merge), static bundle (exercise catalog, new module `sport_muscles`) |
| Outputs | `workouts`, `workout_hrr`; feeds strain (`computeDays` workout logs) and `score_dirty` |
| Files | JS `src/31_workouts.js`; server `training/importer.py`, `training/history.py`, `training/muscle_load.py`, `training/sport_muscles.py`, `cardio/hrr.py` (pure split), `static_bundle/build.py` (`sport_muscles` module), `nativesync/derived_kinds/workouts.py`, migrations 1010–1019; iOS `Sync/Compute/WorkoutsStage.swift`, `Storage/Migrations/WorkoutsSchema.swift`, `Features/Record/` training views (`training:calendar`, `training:day`, `training:muscle` readers); tests `*_workouts.*` |
| Goldens | strength + cardio same day, watch + phone duplicate, workout without HR, HR gap at the end (HRR null), DST, user correction of a type |
| Acceptance | TRIMP / zones / HRR within tolerance on goldens; strain in `daily_scores` unchanged vs server on 90 synthetic days |
| Device | A real workout recorded offline shows TRIMP, zones and HRR on the day sheet within one sync round |

### M2 — stress, resilience, body battery, HR recovery
| | |
|---|---|
| Scope | Local stress loader (port of `insights/stress.compute` input assembly: sleep / workout windows, HR, steps, HRV, 14-day calm baseline, 60 nights of `hrv_night_ln`) calling `stressDays`; resilience, body battery, HR-recovery series and cards in `TrendsLocal`; `cardio` view; upload kind `stress` |
| Inputs | M1 tables incl. steps history (**depends on M1**), `workouts` (contract only: the integration test runs after M1c merges), `daily_scores` |
| Outputs | `stress_daily`; local cards replacing the server's |
| Files | JS `src/32_stress.js`; server `insights/stress.py` (split loader), `cardio/views.py`, `nativesync/derived_kinds/stress.py`, migrations 1020–1029; iOS `Sync/Compute/StressStage.swift`, `Storage/Migrations/StressSchema.swift`, `Features/Trends/TrendsLocal.swift`; tests `*_stress.*` |
| Goldens | existing `stress.json` cases + the loader on 30 synthetic days (sleep crossing midnight, workout windows, steps-only hours) |
| Acceptance | stress rows equal the server's on 60 synthetic days; cards equal `/trends` status |
| Device | Stress and body battery update during the day without network |

Note: with steps in the history area (M1) the phone recomputes stress for the whole history on a bundle change, like scores.

### M4 — coaches, body age, health-monitor ranges, Today composition
| | |
|---|---|
| Scope | `strain_coach` (`strain_target` already JS), `sleep_coach` + `sleep_need_tonight` + bedtime goal (`goals/sleep`), `health_monitor` / `monitor_range`, `sleep_night` notes / baseline / vitals, `/today` composition (`reasons`, `load_status`, `load_text`, hypnogram), `frequent_journal`, body age loader (`healthspan_store.load` / `weekly`; steps history from M1, **depends on M1**; `strength_sources` from local records + `ext_records` strength sets and weights) with existing `healthspanSnapshot` / `healthspanWeekly`; upload kind `healthspan`; move the thresholds of `TodayParams.swift` into a JS params block with the same sources (D7) |
| Files | JS `src/34_coach.js`; server `insights/coach.py`, `insights/healthspan_store.py`, `goals/sleep.py`, `goals/store.py`, `sleep_night/view.py`, `web/views.py` (`scores_today`, `sleep_today`, `reasons` split), `nativesync/derived_kinds/healthspan.py`, migrations 1030–1039; iOS `Features/Today/*` (except files owned by M3 in wave 1, now free), `Features/Sleep/*`, `Sync/Compute/HealthspanStage.swift`; tests `*_coach.*` |
| Goldens | recovery zones edge values, illness flag, no goal / goal mid-plan, < 14 nights, body age with and without strength logs |
| Acceptance | all Today numbers come from local tables; coach texts identical in zh and en |
| Device | Today fully renders in airplane mode the morning after a synced night (rings, coach, monitor, sleep sheet) |

### M5 — reports and journal analysis
| | |
|---|---|
| Scope | `reports.report` (scores, stress, training, weight, nutrition, journal blocks, deltas, clock means), `analysis.compare` / `regress` / `apply_bh`, `regression.ols_hac` / `bh` |
| Inputs | local `daily_scores`, `stress_daily`, `workouts`, `records` (`journal`), `ext_records` (strength sets, weights, nutrition day totals) |
| Files | JS `src/35_reports.js`; server `insights/reports.py`, `records/analysis.py`, `records/regression.py`; iOS `Features/Reports/*`, the journal-impact block in `Features/Trends/` (wave 3: M3 is merged); tests `*_reports.*` |
| Goldens | week / month, empty blocks, a singular design matrix (`Singular`), 730 days, gaps in the journal (lags in calendar days) |
| Acceptance | analysis effect / se / p / q within 1e-6 (matrix inversion), report numbers exact at display rounding |
| Device | Weekly report and "行为影响" open offline |

### M6 — rules, nutrition targets, explain texts
| | |
|---|---|
| Scope | Rules context loader in Swift (port of `rules/context.load` reading local tables + records), `evaluate_ctx` and every rule in JS, `rules/params.py` mirrored; reminder storage with status; training suggestions / cautions for Today and the week; nutrition goal resolution; `explain/personal.py` (`calc_*`, `explain_*`) and lab trend / card; upload kind `reminders` |
| Files | JS `src/36_rules.js`, `src/37_explain.js`; server `rules/*` (pure split), `goals/nutrition.py`, `explain/personal.py`, `records/lab_history.py`, `nativesync/derived_kinds/reminders.py`, migrations 1050–1059; iOS `Sync/Compute/RulesStage.swift`, `Features/Today/ExplainSheet.swift`, `Features/Record/` plan suggestions, `Features/Health/` labs; tests `*_rules.*`, `*_explain.*` |
| Goldens | each rule fires / does not fire / missing input (`common.need`), banned and caution exercises, every `calc_*` key in both languages |
| Acceptance | reminders identical to `evaluate-rules` on 60 synthetic days; dismiss on phone survives a pull |
| Device | A reminder appears without network after logging a workout; explain sheet shows personal advice offline |

If `explain/personal.py` (~785 lines of bilingual rules) does not fit one PR, split into M6a (rules) and M6b (explain).

### R1 — OW and Shortcut retirement
| | |
|---|---|
| Scope | Per user: `ow-archive --user-id N` until `--check` is complete; Shortcut history stays in `shortcut_samples` (already merged into `raw_history`); new `body-engine retire-check --user-id N`: every day of `raw_history` is `complete`, and a full score recompute with `client=None` + archive equals stored `daily_scores` within `CHECK_TOLERANCE`; after the cutoff the Shortcut receiver answers **410** (`health_sync/health_server.py`, `BODY_SHORTCUT_CUTOFF`), `import-ow` leaves the jobs; OW stop-write per apple-sync-engine.md §5.3 |
| Files | `rawdata/ow_archive.py`, `cli.py` (`retire-check`), `health_sync/health_server.py`, `batch.py` steps, `deploy/ops/ops.py` jobs, migrations 1060–1069; docs `apple-sync-engine.md` §5 |
| Acceptance | `retire-check` green for every user with OW or Shortcut data; code deletion (S8 list, apple-sync-engine.md §10) is a later PR |

### R2 — forced upgrade
| | |
|---|---|
| Scope | Server: `client/params.py` (`MIN_APP_VERSION = "2.0.0"`, `NOTICE_DAYS = 7`, `notice_from`, `block_from`, store URL from config); flag `force_upgrade` (global kill switch: off = no notice, no block); `GET /v1/web/me` gains `upgrade: {state: none|notice|block, min, block_from}`; after `block_from`, requests with `X-App-Client: ios-native` and `X-App-Version` < min, and web-session (cookie) requests, get **426** `upgrade_required` on `/v1/users/*` and `/v1/sync/*`; Bearer API tokens without `X-App-Client`, MCP tokens and `/mcp` are never blocked; `/v1/ingest/*` keeps accepting uploads (no data loss) |
| Clients | Today page (`web/static/`, the 1.0.8 WebView): full-screen notice plugin (notice: dismissible banner once a day; block: full screen, no dismiss); native app: same two states from `upgrade` / 426; push notice through `notify/` to registered devices at `notice_from` and `block_from − 1 day`; `whats-new` entry |
| Files | `body_engine/src/body_engine/client/` (new), `web/api.py` (`web_me`), `web/static/upgrade.js` (+ `index.html`, `sw.js` shell), `notify/`, iOS `App/UpgradeGate.swift`; migrations 1070–1079 (notice log) |
| Acceptance | tests for every auth path (native old / new, cookie, Bearer, MCP, ingest); flag off restores today's behaviour in ≤ 30 s |

### S9 — server stops computing for app-mode users
| | |
|---|---|
| Scope | Meaning (decision 4): the server stops **scheduled / background compute and the app-serving view endpoints** for app-mode users; AI / MCP tool calls keep computing on demand with the Python twin (same `algo_version`). Generalise `appscores.plan` to `app_compute_state` per (user, kind): when a kind is `primary` and trusted, hourly / nightly / `sync-refresh-run` skip `hk-derive-run`, `resolve_daily` for the `app` source, `enrich_pending`, stress and healthspan jobs, `evaluate-rules` for that user, except "dirty days" the app did not upload within 2 h (fallback, as for scores); remove the app's reads of the server views; regression |
| Acceptance | 14 days on user 1 + synthetic users with the server only spot-checking; full Body Engine suite and iOS suite green; `native-compare` on user 1 clean for every kind; device checklist file complete |

## 5. Rollout

| Flag (P0, default off) | Effect when on for a user |
|---|---|
| `native_derive`, `native_workouts`, `native_stress`, `native_series`, `native_coach`, `native_reports`, `native_rules` | the app computes that package locally in **shadow**: shows server values, uploads its rows, the server spot-checks |
| `native_compute_primary` | kinds whose last spot check passed (and ≥ 3 settled days uploaded) become **primary**: the app shows its own values, the server stores the uploads as the user's rows |
| `force_upgrade` | R2 notice / block (global) |

`GET /v1/sync/compute` returns per kind `off | shadow | primary` plus server versions; a kind is never enabled for a client below the
app version that implements it (`nativesync/params.MIN_APP_VERSION_BY_KIND`, all `2.0.0`, internal builds by build number).

| Step | When | Do |
|---|---|---|
| 1 | each package merged | flag stays off; internal TestFlight build for the owner only |
| 2 | wave end | `flags set native_<pkg> --state cohort --cohort inner`; watch `app_compute_checks` (pass / fail counts only for non-research users) |
| 3 | after S9 regression | `native_compute_primary` cohort `inner`, 3–7 days, then all package flags and primary `--state all` |
| 4 | release (wave 6) | 2.0.0 to the external group; `force_upgrade` on with `notice_from` = release day, `block_from` = +7 days |
| 5 | `block_from` | Shortcut 410; OW stop-write after `retire-check` is green for everyone (R1) |

Rollback per kind: turn the package flag off (app falls back to server docs, server recomputes the user via `app-scores server`-style command `app-compute server --kind K --user-id N`); global: `native_compute_primary` off; upgrade block: `force_upgrade` off.

## 6. Decisions (2026-10-30, coordinator)

| # | Question | Decision | Where |
|---|---|---|---|
| 1 | `db.migrate` sorts names as strings (`1000_` before `001_`) | P0 switches to a numeric sort, with a test that `1000_x.sql` sorts after `999_x.sql` | §2.2 |
| 2 | SparkyFitness inputs (production has `own_food_only` off) | not a prerequisite: the server passes the person's raw SparkyFitness rows (strength sets, weight entries, nutrition day totals) as the pull-only doc kind `ext_records` with a per-day fingerprint; the phone computes muscle load, body-age strength minutes and reports from it | §2.2 (P0), M1c, M4, M5 |
| 3 | Steps missing from the phone's history | `raw_history` and `hist_samples` carry steps; in M1's scope; M2 and M4 depend on it | M1, M2, M4 |
| 4 | AI / MCP tools after S9 | allowed to compute on demand on the server with the Python twin (same `algo_version`); S9 stops scheduled / background compute and app-serving endpoints for app-mode users only | §1.3, S9 |
| 5 | Spot checks for inner-cohort members | allowed: product-safety maintenance like `app_score_checks`, storing only pass / fail + field names; detailed comparisons and any analysis stay user 1 + synthetic | §3.4 |

Kept as proposals inside the packages: `/v1/ingest/*` stays open after the block (R2), the M6a / M6b split (M6), `TodayParams.swift` thresholds into JS params (M4).
