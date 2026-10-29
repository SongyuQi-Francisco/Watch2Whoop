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

#### P0 as built (where a package plugs in)
- **Versions**: `algo/versions.py` maps every key to a constant in a file the package owns (`derive` → `rawdata/derive.py:DERIVE_VERSION`,
  `workouts` → `training/importer.py:WORKOUTS_VERSION`, `stress_cards` → `insights/stress.py:STRESS_CARDS_VERSION`,
  `series` → `trends/series.py:SERIES_VERSION`, `coach` → `insights/coach.py:COACH_VERSION`, `reports` → `insights/reports.py:REPORTS_VERSION`,
  `rules` → `rules/params.py:RULES_VERSION` (exists already), `explain` → `explain/personal.py:EXPLAIN_VERSION`). A package is *implemented*
  when every algorithm key has its constant **and** the served bundle carries the same string, and every upload kind has its module.
- **Kind module** (`nativesync/derived_kinds/<kind>.py`, interface in that package's `__init__.py`): `KIND`, `PACKAGE`, `ALGO_KEY`,
  `TOLERANCE` (field → absolute or `("rel", x)`, with sources), `validate`, `write`, `recompute`; optional `row_key`, a `records`
  stream kind (`RECORD_KIND`, `RECORD_TABLE`, `RECORD_DAYCOL`, `record_body`) and `extend_pull` (fields added to an existing
  stream's bodies, e.g. `daily.sources`). Tolerances live in the kind module, not in `nativesync/params.py`, so no package edits a shared file.
- **State and checks**: migration `990_native_compute.sql` (tables `app_compute_state` per (user, kind), `app_compute_checks` with
  field names only). In `shadow` an upload is spot-checked and counted, never stored; only `primary` (flag `native_compute_primary`
  + last check passed + ≥ `PRIMARY_MIN_DAYS` days + same `algo_version`) writes through the kind's `write`.
- **`GET /v1/sync/compute`**: `/v1/sync/scoring`'s answer + `compute.packages.<pkg>` (`state`, `flag`, `implemented`, `app_ok`,
  per upload kind its state and last check). Keyed by package because views (series, reports) have no upload kind;
  `MIN_APP_VERSION_BY_KIND` is keyed the same way (header `X-App-Version`, optional `X-App-Build` for `"2.0.0+N"`).
- **`ext_records`**: `GET /v1/users/me/ext-records` (own data only, flag `native_sync`); rows read through
  `sparky_client.reader_for_user` (own tables when the user's reads are own). `strength_sets` = that day's sessions in the
  `training_log` shape (exercises with their sets); `available: false` = the log cannot be read now (the phone keeps its cache).
- **iOS**: stage `HealthspanStage` belongs to package `coach`. `LocalCompute` asks `/v1/sync/compute` only once a stage sets
  `implemented = true`; the `ext_records` puller is `DocPuller.pullExtRecords(from:to:)` (no caller yet).

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

#### M1 as built (2026-10-30)
- **Algorithm** `derive` = `rawdata/derive.py:DERIVE_VERSION` (`derive-1-2026-10-30`) = `OBS.ALGO_VERSIONS.derive`. Python
  reference of the phone's entry point: `derive.derive_days(src)` (pure, JSON-like rows in and out), built from the pure
  splits `daily_values` (of `daily_rows`), `phase1_rows`, `night_window` / `nights_of` (of `nights`), `session_rows`,
  `ingest_rows` (the engine's factor, plausible range, zero-bucket rules) and `importers/common.basal_reference` /
  `resolve` (of `_basal_reference` / `resolve_daily`). JS: `OBS.deriveDays(src)` and `OBS.derive.<python name>`; thresholds
  in `OBS.derive.PARAMS` and the registry rows in `OBS.derive.TYPES`, both compared with the Python by the golden test.
- **Beyond the table above**: the phase-1 direct-upload types (`hk_types.HK_TYPES`: exercise / stand / daylight minutes,
  wrist temperature, …) are derived too (`phase1_rows` = `rawdata/quantities._day_rows`), since their 'app' rows are part of
  the day's sources; sums of the sync engine's types come from the 15-minute buckets only (raw step samples are not added).
  `SleepSession` gained `segments` (sessionize keeps them; replace_sleep ignores them). `ow_sessions` now sorts sessions by
  instant instead of by ISO string (the strings' offsets follow the database session's time zone); behaviour is unchanged
  except across a DST overlap.
- **Goldens**: `test/cases_derive.py` (synthetic): `sydney` 62 days over the end of DST (iPhone + watch heart rate and exercise
  minutes, Shortcut-only hourly sums, basal < 50 %, workout kcal > active kcal, all heart-rate samples deleted, out-of-range
  samples, zero buckets, raw steps next to buckets, sleep gaps > 60 min and > 2 h, nap, iPhone in-bed copy, in-bed-only
  night, third-party night, the same sample twice, the DST night) and `new_york` 30 days over the start of DST (its inputs
  are in the golden file for the iOS test). `tests/test_js_golden_derive.py` ↔ `test/derive.test.js`; `tests/test_native_derive.py`
  checks that the server's own `derive_day` + `resolve_daily` on the same uploaded samples give `derive_days`' rows.
- **Server**: migration `1000_native_derive.sql` (`origin`, `bundle_version` on `daily_metric_sources`, `daily_metrics`,
  `sleep_sessions`; `sleep_sessions.segments`; `daily_metric_sources` joins the `daily` pull stream's change log). Kinds
  `derived_kinds/daily.py` (rows `{role: source | chosen, metric, source, value, unit, agg, n, min_value, max_value, flags}`;
  tolerance `value` / `min_value` / `max_value` 1e-4 relative, sums included: 1 step on a 10,000-step day) and
  `derived_kinds/sleep.py` (sessions with segments; start / end 1 s, minutes 0.5). `daily.extend_pull` adds `sources`
  (`{metric: [{source, value, unit, agg, n, min, max, flags}]}`) to the `daily` bodies, so `bodies.py` / `pull.py` are
  unchanged. `raw_history` bodies carry `inputs.steps` (15-minute buckets: the engine's `hk_stats`, else the server's copy
  of Open Wearables' step samples, largest single source per day).
- **iOS**: `DeriveSchema` → migration `v4_derive_tables` (`daily_metric_sources` with `origin` server / phone,
  `derived_rows` (kind, date, rows, versions, uploaded), `score_dirty` rebuilt with `(date, kind)`). `HealthKitScoringReader`
  keeps each sample's source and gains `readEngine` (the registry's other raw types + `hk_stat_buckets`); `DeriveStage`
  reads HealthKit (both), derives the dirty days, the days whose pulled sources changed and all days after a new derive
  algorithm (from the first day read in full), writes `derived_rows` and the phone's 'app' rows, and in **primary** only
  `daily_metrics` / `sleep_sessions` (source app) + `score_dirty` for the scores; it uploads unsent rows once
  `HealthSyncService.uploadedEverything`. `DeltaRemote` stores the pulled `sources`. History kind `steps` (6).
- **Deviations**: (1) the `daily` tolerance on sums is 1e-4 relative, not 0.5 unit (one tolerance per field in the kind
  module); (2) the phone's workout energy for the `incomplete` rule comes from `hk_workouts.stats` (all non-duplicate
  workouts on the server), equal once M1c lands; (3) `native-compare` (section 3.4) is not part of M1.
- **Flag**: `native_derive` (shadow only; `native_compute_primary` stays off), off by default.
- **Device checklist entry (for S9's `ios-native-2.0-device-checklist.md`)**: owner's internal build, `native_derive` on for
  the owner only. (1) After a sync round, the sync center's compute report (`meta` `compute.last`) shows `derive` `done` with
  days written and `daily` / `sleep` uploaded; `GET /v1/sync/compute` shows both kinds with a passing last check.
  (2) Airplane mode the morning after a night: the round still derives (report `waiting_sync` only for the upload), and with
  primary on (later, S9) steps, energy and last night's sleep come from the phone. (3) Back online: one upload of the
  unsent days, then nothing on the next round; a pull does not duplicate `sleep_sessions` or `daily_metrics` rows.

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

#### M3 as built
- **Python split** (outputs unchanged, DB tests green): `trends/series.py` `window` + `load(conn, viewer, owner, end, days)` +
  pure `compute(data, end, days, today, tz, now_hour)` (`build` = `compute(load(…))`; `prev_mean` now sums the previous period
  in date order, the same set as before); `web/views.py` `month_days_compute`, `month_range`, `sleep_nights_compute`;
  `insights/sleep_detail.py` `watch_sessions`, `sri_from`, `nights_from`, `details` (+ `NOTES`), `insights/views.sleep_details`
  loads once for the 7 / 30-day SRI; `insights/fitness.py` `hr_zones_compute`, `vo2max_compute`, `SEXES`.
  `SERIES_VERSION = "series-1"` in `trends/series.py`; the thresholds stay where they were (`trends/params.py`,
  `series.py` `BASELINE_DAYS` / `BASELINE_MIN_N` / `LOOKBACK`, `fitness.LEVELS`, `views.RECOVERY_GREEN`, `rules/params.RECOVERY_RED`),
  mirrored in `src/33_series.js` `OBS.series.params`.
- **JS** `src/33_series.js`: `OBS.series.compute` (the whole `/trends` JSON incl. status cards, with the existing `OBS.trends`
  functions), `month_days`, `sleep_nights`, `sleep_detail.*`, `fitness.*`; entry points `trendsSeries(data, {end, days, today,
  tz, now_hour})`, `monthDays`, `sleepNights`, `sleepDetails`, `vo2max`, `hrZones`, stamped `series-1`; `OBS.ALGO_VERSIONS.series`.
  `in_bed` is optional (the phone derives it from start / end). `GET /v1/sync/compute` now reports `series` implemented
  (views only: shadow with `native_series`, primary with `native_compute_primary`).
- **Goldens**: `test/cases_series.py` (420 synthetic days in Australia/Sydney across the 2026-10-04 DST change; ranges 7 / 30 /
  90 / 365 ending today and 50 days back, a run with < 14 baseline days, a range ending after the data, English labels,
  variants without scores / metrics + sleep / weight + training scopes, without the stress / body-age / heart-rate-recovery /
  weight blocks, a weight-log error; month calendar incl. hidden scopes and a future month; nights with naps and two mains;
  SRI 7 / 30 days and < 4 pairs; VO₂max with / without sex or birth date, < 3 readings, old readings only; zone totals with /
  without workouts; level edges) → `golden/series.json`; `golden/series_local.json` = 120 days as the phone's local tables + the
  Python answers (the Swift acceptance test). Tests: `body_engine/tests/test_js_golden_series.py`, `test/series.test.js`,
  `Watch2WhoopTests/TrendsSeriesTests.swift`.
- **iOS** `Features/Trends/TrendsSeriesLocal.swift` (`SeriesLocal`): no stage in `LocalCompute` (views are computed when a page
  refreshes, after its server pull); it asks `/v1/sync/compute` itself when the cached `compute:state` is older than an hour.
  Loader = the server's `load` on local tables: `daily_scores.payload`, `daily_metrics`, `sleep_sessions` (+ payload),
  weights = `body_mass` < records `weight` < `ext_records` weigh-ins < unsent records (it pulls `ext_records` for the window,
  the first caller of `pullExtRecords`), profile doc `profile:me` (birth date, sex) for the VO₂max level. `stress_daily`,
  `healthspan_weekly`, `workouts` are read when the table exists; until then the series `stress`, `resilience`, `body_age`,
  `hr_recovery`, the cards `resilience`, `body_battery`, `hr_recovery` and the month's `training` days come from the server's
  document of the same page (`server_only` in the phone's document; "没有数据" without one). Pages read through
  `SeriesLocal.shown` (phone's document only in `primary` and once computed): Trends (`TrendsSnapshot`), Today's 61-day
  document (`TodaySnapshot`), `MonthPicker`, `TrendsSleepView`, the regularity card and the VO₂max level.
- **Deviations**: two more cache keys than section 3.1 lists, `sleep.local:details:<end>` and `trends.local.vo2max:<end>` (the
  regularity card and the fitness level are their own documents on the server too); the shadow comparison is local only (meta
  `series.compare`: per page ok / field paths / count, no values), since views are never uploaded (section 3.2); the 365-day
  ≥ 55 fps check is left to the device checklist (the page code is unchanged, only the document it reads).
- **Device checklist (for S9)**: with `native_series` on (shadow), open Trends on every range, the month calendar and 睡眠;
  meta `series.compare` shows `ok` for each page; then with `native_compute_primary` on, airplane mode: Trends opens on all
  ranges, Today's 「全部指标」 equals Trends 「今天」, the month calendar colours and the last 14 nights match the server's
  from before.

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

#### M1c as built (2026-10-30)
- **Algorithm** `workouts` = `training/importer.py:WORKOUTS_VERSION` (`workouts-1-2026-10-30`) = `OBS.ALGO_VERSIONS.workouts`.
  Python reference of the phone's entry point: `importer.workout_days(src)` (pure, JSON-like rows in and out), built from
  the pure splits `duplicates` (of `refresh_duplicates`), `hr_enrich` (the heart-rate part of `enrich`), `heart_rate`,
  `parse_app`, `overrides.apply`, `cardio/hrr.hrr60` (read only) and `history.workout_intensity`. Views:
  `training/history.py` `load` + pure `compute` (`build` = `compute(load(…))`), `calendar_view`, `day_view`;
  `training/muscle_load.py` `load` + pure `compute_from` (`DictIndex` = `loads.Index` from plain data). JS
  `src/31_workouts.js`: `OBS.workoutDays`, `OBS.trainingCalendar`, `OBS.trainingDay`, `OBS.muscleLoad` and
  `OBS.workouts.<python name>`; thresholds in `OBS.workouts.PARAMS`, the type / sport-muscle / muscle tables in
  `OBS.workouts.TABLES`, both compared with the Python by the golden test. Static bundle module `sport_muscles` added.
- **Upload rows** (kind `workouts`, one per app workout, by its start day): parse_app's fields, `dup_of` as
  `"<source>:<external_id>"`, `trimp`, `zone1_min` … `zone5_min` (flattened so each zone has its tolerance),
  `hr_samples`, `hr_max_used`, `hr_recovery_bpm` (the server keeps it in `flags`), `intensity`, `flags`, and hrr60 as
  `hrr60`, `hrr_peak`, `hrr_60s`, `hrr_valid`, `hrr_reason`. Duplicates and excluded workouts are not enriched (as
  `enrich_pending`). External ids are lower-case (the server's `hk_uuid::text`).
- **Goldens**: `test/cases_workouts.py` (synthetic): `sydney` 8 days over the end of DST (strength + cardio the same day,
  a watch + iPhone copy and an Open Wearables copy, no heart rate, a heart-rate gap at the end, a workout across the DST
  change, a type override, a hidden workout, a type rule, a manual duplicate, a 13-hour record, low coverage, a
  winding-down walk, HRmax from the birth date) and `new_york` 4 days over the start of DST (its inputs are in the golden
  file for the iOS test); calendar / day runs (watch + SparkyFitness strength and cardio merge, SparkyFitness-only, Hevy
  on its watch copy, partial-energy flag, scores hidden, English); muscle-load runs (catalog by slug / name / SparkyFitness
  id, unmatched, warm-up, cardio, the sports layer with and without a planned target, English). Tests
  `tests/test_js_golden_workouts.py` ↔ `test/workouts.test.js`; `tests/test_native_workouts.py` (database) checks that the
  server's own import + `refresh_duplicates` + `enrich_pending` + `hrr.compute` on the same uploaded workouts equal
  `workout_days`, that `build` / `_build` equal `compute(load(…))`, the kind (shadow check, a changed TRIMP caught,
  primary write, another user untouched) and `hrr` in the pull.
- **Server**: migration `1010_native_workouts.sql` (`computed_by`, `bundle_version` on `workouts` and `workout_hrr`;
  `workouts.origin` is the source app, so not reused; `workout_hrr` joins the `records` / `workout` change log). Kind
  `derived_kinds/workouts.py`: spot check = `workout_days` on the server's hk_samples workouts of the day ± 1 day, the
  other sources' rows, `workout_overrides` / `workout_type_rules`, heart rate ±5 min (watch first), the day's HRmax and
  the birth date; tolerance trimp 0.1, each zone 0.1, HR recovery / hrr60 / peak / 60-s value 0.5, HRmax 1, start / end
  1 s, minutes 0.01, energy 0.1; primary writes the enrichment columns of the server's row of the same workout (source app,
  external_id) and its `workout_hrr`; `extend_pull` adds `hrr` to the records stream's workout items.
- **iOS**: `WorkoutsSchema` → migration `v5_workouts_tables` (`workouts` keyed by `<source>:<external_id>` with
  `computed_by` server / phone, the row in the server's shape and the server's own row in `server_row`; `workout_hrr`).
  `WorkoutsStage` reads the HKWorkouts of the read window (anchored, `EngineWorkout.map`, into `hk_workouts`), pulls the
  `records` stream filtered to kind `workout` (`kinds: {"records": ["workout"]}`, own cursor in meta `workouts.records_cursor.workout`; the native app pulls no `records` otherwise), computes dirty
  days with `workoutDays` (corrections = the server's rows of the app's workouts), writes `derived_rows`, `workouts`,
  `workout_hrr` and the duplicates of the server's rows, marks days whose kept workout energy changed dirty for `derive`,
  uploads once settled. `DeriveStage`'s workout energy for the `incomplete` rule now reads the kept `workouts` (an
  HKWorkout not computed yet counts with its own statistics): the same set the server sums. `Features/Record/TrainingLocal`
  computes the month calendar and the day detail (`training.local:calendar:<month>`, `training.local:day:<day>`), compared
  in shadow (meta `workouts.compare`, field paths only), drawn in primary. M3's `SeriesLocal` reads the training days
  and the workouts' HR recovery from `workouts` only when it has rows (`workoutsOnPhone`: the package computes),
  else from the server's documents as before.
- **Deviations**: (1) `cardio/` is M2's: the hrr60 split was not made there (the JS ports `hrr60` directly; the server's
  `hrr.compute` is unchanged). Known server bug left to its owner: `hrr.compute` does wall-clock arithmetic on the
  database's ZoneInfo, so a workout ending in the repeated hour of a DST change gets a wrong peak window (the DB test skips
  that one workout). (2) `enrich`, `_base_row` and `duplicates` now compute in UTC instants: outputs change only for
  workouts across the end of DST (they were wrong there). (3) The phone has no Hevy import, no SparkyFitness exercise
  descriptions (status from `exercise:rules` only) and no type rules of its own (the server's corrected rows stand in);
  `sparky_error` is not compared. (4) The muscle-load map is ported and golden-tested but the app keeps the server's
  document: the phone has no exercise catalog until the static bundle reaches the app. (5) `native-compare` is not part
  of M1c.
- **Flag**: `native_workouts` (shadow only; `native_compute_primary` stays off), off by default.
- **Device checklist entry (for S9)**: owner's internal build, `native_workouts` on for the owner only. (1) After a sync
  round the compute report shows `workouts` `done`, `workouts:<day>` uploaded; `GET /v1/sync/compute` shows the kind
  with a passing check. (2) Record a workout offline: the next round (still offline) writes it to `workouts` with TRIMP,
  zones and HRR; meta `workouts.compare` shows `ok` for the calendar and the day once online. (3) With primary on (S9),
  the training calendar and the day sheet open offline with the same numbers as the server's before.

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

#### M2 as built (2026-10-30)
- **Algorithm** `stress_cards` = `insights/stress.py:STRESS_CARDS_VERSION` = `"stress-cards-1+" + STRESS_VERSION`
  (`stress-cards-1+v1.0-2026-10-17`) = `OBS.ALGO_VERSIONS.stress_cards`: one key for the three entry points and the stress
  algorithm they run, so a change of either makes the phone recompute. **Python split** (outputs unchanged; the DB tests of
  `stress.compute` in `test_apple_sync_read.py`, `test_apple_sync_cutover.py`, `test_openbodyscore_golden.py` stay green):
  `insights/stress.py` `load` + `compute` (= `score_range(load(…))`), the pure merge `merge_app` (of `_with_app`), the
  phone's reference `days_local(src)` (UTC seconds; the app's samples first, the history's on days without any of a type,
  then `score_range`) and `cards(data, end, today, tz, now_hour)` (resilience, body battery, heart-rate recovery exactly as
  `trends/series.compute` builds them; a test checks the equality on M3's synthetic person); `cardio/views.py` `window`,
  `load`, pure `compute` (`cardio` = share check around them). No new thresholds; `cardio/hrr.py` `ALGO`, `TREND_DAYS`,
  `PEAK_MIN_PCT` are mirrored in `OBS.stress_cards.params` and compared by the golden test.
- **JS** `src/32_stress.js`: `OBS.stress_cards.{merge_app, days_local, cards, cardio, mean}` (`mean` = `statistics.mean`,
  exact rational mean) on top of the existing `OBS.stress_days.score_range` and `OBS.trends`; entry points `stressLocal(src)`,
  `stressCards(data, {end, today, tz, now_hour})`, `cardioView(data, {start, end})`, stamped.
- **Goldens** (`*_stress_cards.*`, since `golden/stress.json` is N1's): `test/cases_stress_cards.py` — 74 synthetic days in
  Australia/Sydney over the 2026-10-04 DST change (history-area days, app days with watch + iPhone steps at the same
  instants, a day in both, a day with app heart rate but history steps, sleep across midnight, naps, a workout twice,
  walking bursts, an hour with steps but < 3 heart-rate readings, a Shortcut-only day, a day without heart rate; runs: all,
  a start with < 50 baseline readings, no steps), the cards on M3's person (both languages, today with an hour, a day without
  a score row, without heart-rate recovery / stress), the cardio view (valid / invalid, both 30-day periods, a zero-length
  night) → `golden/stress_cards.json`; `golden/stress_local.json` = the 74 days + the Python rows of the last 60 (the iOS
  acceptance fixture). Tests `body_engine/tests/test_js_golden_stress_cards.py`, `test/stress_cards.test.js`,
  `body_engine/tests/test_native_stress.py` (DB: the server's `stress.compute` on the same uploads == `days_local`; upload
  kind off / shadow / mismatch / primary; records pull; /cardio), `Watch2WhoopTests/LocalStressTests.swift`.
- **Server**: kind `derived_kinds/stress.py` (rows: one per day, `score_range`'s row without `date`; `TOLERANCE` score 0.01;
  `recompute` = `stress.compute` from the server's tables, a day without raw heart rate is not compared; hours: only the
  (hour, level) pairs are compared — recompute takes the phone's hour rows when they agree; baseline within half its
  rounding; counts, peak hour and flag texts exact). Record kind `stress` of the `records` stream. Migration
  `1020_native_stress.sql` (`origin`, `bundle_version` on `stress_daily`; `sync_attach` for the records stream).
- **iOS**: `StressSchema` → `v6_stress_daily` (`n_hr` = the day's heart-rate samples it was computed from). `StressStage`
  (implemented): every day after a new algorithm, else the last 16 days and every day whose heart-rate count changed (+ the
  14 days after it), in chunks of ≤ 30 consecutive days; inputs = `hk_samples` (heart rate, HRV SDNN, raw steps keyed like
  `rawdata/samples._source_key`), `hist_samples` (hr / hrv / steps), `sleep_sessions`, `hk_workouts` (+ M1c's `workouts`
  when the table exists), `daily_scores.payload.hrv_night_ln`, `daily_metrics` source `shortcut`; writes `stress_daily` in
  shadow and primary, `derived_rows` kind `stress` (uploaded with `DeriveStage.upload`), and the doc `cardio.local:<today>`.
  `TrendsLocal`: `stressCards` (14 days of score / stress rows, 150 days of heart-rate recovery); primary = the three cards
  are the phone's (null hides a card), shadow = compared with the cached server status of that day (meta `stress.compare`,
  field names only; today's body battery: only its value on waking). `TrendsSeriesLocal`: `stress` / `resilience` /
  `body_battery` and `hr_recovery` from local tables when `stress` is primary, else from the server's document as before.
- **Deviations**: (1) file names `*_stress_cards.*`; (2) the spot check reads the server's own sleep and score rows, not the
  phone's uploaded `sleep` kind; (3) days before the hot window come from the history area, whose steps are 15-minute buckets
  (the server reads Open Wearables' raw step samples for those days), so pre-app days may differ slightly — the spot check
  only compares the newest uploaded day; (4) Open Wearables-only workouts reach the phone with M1c, until then only
  `hk_workouts` are left out; (5) with `stress` primary the heart-rate-recovery card uses Apple's value (and M1c's workouts
  once on the phone, as the brief says); (6) no app page reads `/cardio` today (AI only): the phone keeps the document
  `cardio.local:<today>` for a later card; it reads M1c's `workout_hrr` / `workouts` with assumed column names
  (`workout_uuid`, `local_date`, `end_at`, `peak_hr`, `hr_60s`, `hrr60`, `apple_hrr60`, `valid`, `reason`; `uuid`, `type`,
  `name_zh`, `duration_min`), no rows until those exist; (7) the phone does not pull the `records` stream yet, so the record
  kind `stress` is for a later restore; (8) local migration `v6_…` (v5 left to M1c); (9) `native-compare` is not part of M2.
- **Flag**: `native_stress` (shadow; primary needs `native_compute_primary`), off by default.
- **Device checklist entry (for S9)**: owner's internal build, `native_stress` on for the owner. (1) After a sync round the
  compute report (`meta` `compute.last`) shows `stress` `done` with days written and uploaded; `GET /v1/sync/compute`
  shows kind `stress` with a passing last check. (2) Open Trends: `meta` `stress.compare` is `ok` (resilience, body battery,
  heart-rate recovery equal the server's). (3) With `native_compute_primary` on (S9), airplane mode during the day: Today's
  「压力」 and 「身体电量」 and Trends 「韧性」 update after a sync round without network.

### M4 — coaches, body age, health-monitor ranges, Today composition
| | |
|---|---|
| Scope | `strain_coach` (`strain_target` already JS), `sleep_coach` + `sleep_need_tonight` + bedtime goal (`goals/sleep`), `health_monitor` / `monitor_range`, `sleep_night` notes / baseline / vitals, `/today` composition (`reasons`, `load_status`, `load_text`, hypnogram), `frequent_journal`, body age loader (`healthspan_store.load` / `weekly`; steps history from M1, **depends on M1**; `strength_sources` from local records + `ext_records` strength sets and weights) with existing `healthspanSnapshot` / `healthspanWeekly`; upload kind `healthspan`; move the thresholds of `TodayParams.swift` into a JS params block with the same sources (D7) |
| Files | JS `src/34_coach.js`; server `insights/coach.py`, `insights/healthspan_store.py`, `goals/sleep.py`, `goals/store.py`, `sleep_night/view.py`, `web/views.py` (`scores_today`, `sleep_today`, `reasons` split), `nativesync/derived_kinds/healthspan.py`, migrations 1030–1039; iOS `Features/Today/*` (except files owned by M3 in wave 1, now free), `Features/Sleep/*`, `Sync/Compute/HealthspanStage.swift`; tests `*_coach.*` |
| Goldens | recovery zones edge values, illness flag, no goal / goal mid-plan, < 14 nights, body age with and without strength logs |
| Acceptance | all Today numbers come from local tables; coach texts identical in zh and en |
| Device | Today fully renders in airplane mode the morning after a synced night (rings, coach, monitor, sleep sheet) |

#### M4 as built (2026-10-30)
- **Algorithm** `coach` = `insights/coach.py:COACH_VERSION` = `"coach-1+" + healthspan_store.CURRENT.ALGO_VERSION`
  (`coach-1+hs-v1.2-2026-10-23`) = `OBS.ALGO_VERSIONS.coach`: one key for the views, the body-age loader and the body-age
  formulas they run (as M2's `stress_cards`). **Python split** (outputs unchanged; the DB tests of the endpoints stay green
  and `tests/test_native_coach.py` checks the wiring on stored synthetic data): `insights/coach.py` `strain_coach_compute`,
  `sleep_coach_compute` (goal passed in), `health_monitor_compute` (+ named constant `MONITOR_HRV_MIN_HALF_LN`);
  `goals/store.py` `with_plan`, `latest_need`, `duration_from`, `view_compute` (`view` = `view_compute(load…)`);
  `sleep_night/view.py` `load` + pure `compute` (`build` = `compute(load(…))`, `_night` takes the stage timeline);
  `web/views.py` `scores_today_compute`, `main_rows` (the pure twin of `DISTINCT ON (wake_date) … asleep_min DESC NULLS
  LAST`), `latest_main`, `recent_bedtimes_compute`, `sleep_today_compute`, `frequent_journal_compute`;
  `web/recovery_empty.py` `explain_from` (facts as values or lazy callables, so the server keeps its query order);
  `insights/healthspan_store.py` `load_parts` + pure `from_parts` (`Data(conn, …)` = `from_parts(load_parts(…))`),
  `weekly_local(parts, today)` (the phone's reference), `utc_sessions`; `write` now upserts (weeks no longer computed are
  deleted) so an unchanged week is no change in the `records` stream. The Today thresholds of `TodayParams.swift` are now
  `web/params.py` (sources there), mirrored in `OBS.coach.TODAY_PARAMS`.
- **JS** `src/34_coach.js`: `OBS.coach.{strain_coach_compute, sleep_coach_compute, health_monitor_compute, monitor_range,
  goals.*, sleep_night.*, today.*, healthspan.{from_parts, weekly_local}, TODAY_PARAMS}`; entry points `strainCoach`,
  `sleepCoach`, `healthMonitor`, `sleepGoal`, `sleepNight`, `todayScores`, `recoveryEmpty`, `todaySleep`, `hypnogram`,
  `frequentJournal`, `healthspanLocal`, stamped. Reuses `OBS.trends.strain_target`, `OBS.sleep.sleep_debt / sleep_need`,
  `OBS.series.sleep_detail` and `OBS.healthspan_snapshot.weekly`. Python's wall-clock datetime arithmetic (one zone) is
  reproduced as wall-clock microseconds; `format(x, "g")` / `".0f"` are ported (`fmtG`, `fmt0`).
- **Goldens** (`*_coach.*`): `test/cases_coach.py` on M3's synthetic person (420 days, Australia/Sydney, DST change
  2026-10-04): strain coach (zone edges 33 / 34 / 66 / 67, illness, TSB < −30, missing strain, no row), sleep coach (no goal,
  goal with / without wake time, after midnight, evening wake time, the DST morning, no wake times), health monitor (both
  languages, Apple's all-day resting HR, < 14 nights, a night out of range, the DST night), bedtime goal (no goal, mid-plan,
  reached, paused, far behind, DST nights) and its helpers, sleep detail (stages / no stages / no night, naps, two mains,
  without the scores share, English) and every note, Today scores (load states, missing load days), every
  `recovery_empty` code in both languages, Today sleep + hypnogram (overlaps, merges, gaps, DST), frequent journal items,
  body age with / without strength logs, with a SparkyFitness error, without birth date / origin → `golden/coach.json`;
  `golden/coach_local.json` = the body-age parts as the phone has them + the Python rows, a strain-coach call and the Today
  thresholds (iOS fixture). Tests `body_engine/tests/test_js_golden_coach.py`, `test/coach.test.js`,
  `body_engine/tests/test_native_coach.py` (DB), `Watch2WhoopTests/LocalCoachTests.swift`.
- **Server**: kind `derived_kinds/healthspan.py` (one upload day = one week by its Monday; rows = `weekly`'s row without
  `week_start` / `notes`; `TOLERANCE` body age / delta 0.1, pace / raw pace 0.01, chronological age 0.01; contributor and
  insufficient keys exact — recompute takes the phone's lists when their keys agree; `recompute` = `healthspan_store.compute`
  up to the row's `as_of`; primary upserts the week with origin `app`). Record kind `healthspan` of the `records` stream.
  Migration `1030_native_healthspan.sql` (`origin`, `bundle_version` on `healthspan_weekly`; `sync_attach` by `week_start`).
- **iOS**: `HealthspanSchema` → `v7_healthspan_weekly`. `HealthspanStage` (implemented): after a new coach algorithm, on
  a new day or after an hour, pulls `ext_records` from the scoring origin and the journal's strength days (`records`
  stream with the kind filter of #368, `kinds: {records: [journal]}`, on its own cursor `healthspan.records_cursor.journal`
  into the document `healthspan.journal_strength`), reads the parts (`profile:me`, `daily_scores` payload, `daily_metrics`,
  `sleep_sessions`, M1c's kept `workouts`, `ext_records` strength sets / weigh-ins, the journal days + unsent journal
  records), calls `healthspanLocal`, writes `healthspan_weekly` + `derived_rows` kind `healthspan` (shadow and primary),
  uploads with `DeriveStage.upload`. `Features/Today/CoachLocal.swift`: after Today's pulls, `coach.local:<day>`
  (strain coach, health monitor, sleep coach, the today document's `scores` and `sleep` blocks with the phone's own stage
  segments) and `sleepnight.local:<day>` (after the sleep page's pull too); shadow = compared with the server's documents
  (meta `coach.compare`, field paths only), primary = `TodayStore` draws the phone's strain target / monitor / rings'
  scores and sleep, `SleepNightStore` the phone's night. `TrendsSeriesLocal` reads the body-age series from
  `healthspan_weekly` only when `coach` is primary.
- **Deviations**: (1) the server's sleep regularity in body age and the sleep detail now computes on UTC instants
  (`utc_sessions`): with rows in the user's own zone the old arithmetic was an hour off in a window holding a DST night,
  so values change only in those windows (M3's `/sleep/details` still has the old behaviour: its owner's, the phone's
  M3 port already uses UTC); (2) the phone has no Hevy import and no training plan (the plan only labels the strength
  item; the Hevy minutes are a few days a year), the spot check compares the newest week only; (3) `TodayParams.swift`
  keeps its constants (no JS call on the main thread), checked against the bundle's `TODAY_PARAMS` by `LocalCoachTests`;
  (4) the app has no bedtime-goal / sleep-coach page: both are computed (`sleep_coach` in the coach document, goal
  `null`, since the app does not cache the goal settings; `sleepGoal` is ported and golden-tested for the later page);
  `frequent_journal` is ported but the record page keeps the server's document; (5) views are not uploaded (section
  3.2), so only body age has a server spot check; (6) `native-compare` is not part of M4.
- **Flag**: `native_coach` (shadow; primary needs `native_compute_primary`), off by default.
- **Device checklist entry (for S9)**: owner's internal build, `native_coach` on for the owner. (1) After a sync round the
  compute report (`meta` `compute.last`) shows `healthspan` `done` with weeks written and uploaded; `GET /v1/sync/compute`
  shows kind `healthspan` with a passing last check. (2) Open Today and a night's sleep detail: `meta` `coach.compare` is
  `ok` for `strain_coach`, `health_monitor`, `today_scores`, `today_sleep`, `sleep_night`. (3) With
  `native_compute_primary` on (S9), airplane mode the morning after a synced night: Today renders the rings, strain
  target, health monitor and the sleep sheet; Trends' body age series is the phone's.

### M5 — reports and journal analysis
| | |
|---|---|
| Scope | `reports.report` (scores, stress, training, weight, nutrition, journal blocks, deltas, clock means), `analysis.compare` / `regress` / `apply_bh`, `regression.ols_hac` / `bh` |
| Inputs | local `daily_scores`, `stress_daily`, `workouts`, `records` (`journal`), `ext_records` (strength sets, weights, nutrition day totals) |
| Files | JS `src/35_reports.js`; server `insights/reports.py`, `records/analysis.py`, `records/regression.py`; iOS `Features/Reports/*`, the journal-impact block in `Features/Trends/` (wave 3: M3 is merged); tests `*_reports.*` |
| Goldens | week / month, empty blocks, a singular design matrix (`Singular`), 730 days, gaps in the journal (lags in calendar days) |
| Acceptance | analysis effect / se / p / q within 1e-6 (matrix inversion), report numbers exact at display rounding |
| Device | Weekly report and "行为影响" open offline |

#### M5 as built (2026-10-30)
- **Algorithm** `reports` = `insights/reports.py:REPORTS_VERSION` (`reports-1`, defined in the new `insights/reports_params.py`)
  = `OBS.ALGO_VERSIONS.reports`: one key for the report, the journal analysis and the regression. **Python split** (outputs
  unchanged): `insights/reports.py` `load(conn, user, period, end, viewer, sparky)` (every input of both periods, read once:
  score rows, stress rows or None without the table, sleep sessions, the log's sessions over both periods, kept workouts,
  weigh-ins, nutrition days, `analyze_all` results) + pure `compute(data, period, end)` (`report` = `compute(load(…))`;
  blocks `scores_block`, `stress_block`, `consistency`, `training_block`, `weight_block`, `nutrition_block`, `journal_block`;
  a block whose input key is absent is left out, which is how the phone marks the server-only blocks);
  `records/analysis.py` `_analyze` = the query + pure `analyze_rows`; new `rows_from` (the query's join on plain data),
  `analyze_data` (analyze / analyze_all on plain data, BH across the items) and `load_data` (the same inputs from the tables).
  The report's thresholds moved to `insights/reports_params.py` with sources (recovery bands 67 / 34, < 6 h nights,
  strain ≥ 14, 90-day journal window, top 5 findings); the regression's stay in `records/analysis_params.py`.
- **JS** `src/35_reports.js`: `OBS.regression` (`inverse`, `Singular`, `erfc` — JavaScript has none: series below 2,
  continued fraction above, ~1e-15 —, `norm_p`, `ols_hac`, `bh`), `OBS.analysis` (`compare`, `regress`, `apply_bh`,
  `analyze_rows`, `rows_from`, `analyze_data`; texts identical, Python's float formatting of the sample counts included),
  `OBS.reports` (`compute` and its blocks, `clock_mean` with Python's float modulo); entry points `report(data, {period, end})`
  and `journalAnalysis(data, {end, days, regression, key, threshold})` → `{results, algo_version, bundle_version}`.
- **Goldens**: `test/cases_reports.py` (synthetic, inputs rounded and drawn from `random.Random` so they do not depend on libm):
  the inverse (regular, singular, all zero), the normal p on a z grid across both erfc branches, BH (ties, one, none),
  `ols_hac` (daily, gaps of 3 days, unsorted days, two rows per day); `regress` (usual, lower-is-better, few yes days, too few
  residual degrees of freedom, a Tuesday-only behaviour = collinear, zero residuals, an all-zero behaviour, no observations);
  `analyze_data` on 730 days of journal (effect, none, a duplicate item for BH ties, Tuesday-only, all-zero, rare, every-4th-day
  logging for calendar-day lags, a numeric item with thresholds, missing scores / metrics / covariates, an HRV of 0;
  windows 14 / 90 / 365 / 730, both versions, one key); `compute` on 70 days over the end of DST (week / month, three ends and
  one before any data; no stress table, the phone's absent blocks, SparkyFitness errors, nutrition-summary scope, scores only,
  scores + sleep + training, no scope, failed journal analysis) → `golden/reports.json`; `golden/reports_local.json` = the same
  person as the phone's tables + the Python answers (week, month, both analysis versions). Largest float difference seen:
  1.2e-13 relative (a raw normal p at the erfc branch point); all rounded outputs equal. Tests
  `tests/test_js_golden_reports.py` ↔ `test/reports.test.js`; `tests/test_native_reports.py` (database: the split report equals
  the pre-split one, `tests/reports_v0.py`, for both periods, four ends, a failing log reader and three viewers' scopes;
  `analyze_all` / `analyze` on the tables equal `analyze_data(load_data(…))`; the phone's raw journal gives the server's
  findings); `Watch2WhoopTests/ReportsLocalTests.swift`.
- **Server**: nothing else (views are not uploaded, section 3.2); no migration. `GET /v1/sync/compute` reports `reports`
  implemented (shadow with `native_reports`, primary with `native_compute_primary`).
- **iOS** `Features/Reports/ReportsLocal.swift` (no compute stage, like M3: computed after the page's server pull): pulls the
  `records` stream filtered to `journal` + `journal_items` on its own cursor (`reports.records_cursor.journal,journal_items`;
  kept in `records`, non-pending, whole-day replace) and `ext_records` for [start − 30, end]; loader = `reports.load` /
  `analysis.load_data` on `daily_scores.payload`, `sleep_sessions`, `daily_metrics`, `stress_daily` (only with `stress`
  primary), `workouts` (only when M1c's rows are on the phone), `ext_records` strength sets and weigh-ins. Documents
  `report.local:<period>:<end>`, `journal.analysis.local:[r:]<end>`; shadow compares with the server's document (meta
  `reports.compare`, field paths only), primary draws the phone's. Server-only blocks (`server_only` in the document,
  ignored by the comparison): nutrition always, stress / training / weight / journal findings when their inputs are
  missing. The 行为影响 card follows flag `journal_regression` (request `regression=true`, own documents, the adjusted
  estimate and BH direction like the page's `behaviorModel`, tip 「已控制负荷、睡眠、星期」).
- **Deviations**: (1) no local table, docs only (as section 3.1 says); (2) the nutrition block stays the server's: goal
  resolution (`goals/nutrition.py`, M6) and the food extras (added sugar, sugary drinks) are not on the phone; the pure
  `nutrition_block` is ported and golden-tested for M6; (3) the phone does not read `records` `food` / `weight` (the report
  reads the log, i.e. `ext_records`); pending (unsent) journal answers and weigh-ins count once they reach the server;
  (4) order within one day: `ext_records` sorts a day's sessions / weigh-ins canonically, the log answers in its own order, so
  `session_names` or a same-day first / last weigh-in can differ (a shadow difference, not a value); (5) the server's
  `sleep_detail.asleep_minutes` subtracts datetimes that share one ZoneInfo as wall clocks, so a regularity value whose
  window crosses a DST change can differ by about a point from the phone's (instants); the goldens feed UTC instants. Left to
  `insights/sleep_detail.py`'s owner; (6) `native-compare` is not part of M5; (7) M4's `healthspan_weekly` is not read: the server's report has no body-age block (insights/reports.py), so neither does the phone's.
- **Flag**: `native_reports` (shadow; primary needs `native_compute_primary`), off by default. `journal_regression` is unchanged
  (the server's flag, now also read by the native card).
- **Device checklist entry (for S9)**: owner's internal build, `native_reports` on for the owner. (1) Open Trends → 周报, then
  月报: meta `reports.compare` shows `ok` for `report_week`, `report_month` and `journal_analysis` (`…_regression` with
  `journal_regression` on). (2) With `native_compute_primary` on (S9), airplane mode: the weekly and monthly report and
  「行为影响」 open with the same numbers as before; the nutrition tiles stay the cached server values. (3) Log a journal
  answer online, refresh: the card's counts follow after the next pull.

### M6 — rules, nutrition targets, explain texts
| | |
|---|---|
| Scope | Rules context loader in Swift (port of `rules/context.load` reading local tables + records), `evaluate_ctx` and every rule in JS, `rules/params.py` mirrored; reminder storage with status; training suggestions / cautions for Today and the week; nutrition goal resolution; `explain/personal.py` (`calc_*`, `explain_*`) and lab trend / card; upload kind `reminders` |
| Files | JS `src/36_rules.js`, `src/37_explain.js`; server `rules/*` (pure split), `goals/nutrition.py`, `explain/personal.py`, `records/lab_history.py`, `nativesync/derived_kinds/reminders.py`, migrations 1050–1059; iOS `Sync/Compute/RulesStage.swift`, `Features/Today/ExplainSheet.swift`, `Features/Record/` plan suggestions, `Features/Health/` labs; tests `*_rules.*`, `*_explain.*` |
| Goldens | each rule fires / does not fire / missing input (`common.need`), banned and caution exercises, every `calc_*` key in both languages |
| Acceptance | reminders identical to `evaluate-rules` on 60 synthetic days; dismiss on phone survives a pull |
| Device | A reminder appears without network after logging a workout; explain sheet shows personal advice offline |

If `explain/personal.py` (~785 lines of bilingual rules) does not fit one PR, split into M6a (rules) and M6b (explain).

#### M6 as built (2026-10-30)
- **Algorithms** `rules` = `rules/params.py:RULES_VERSION` (`v1.4-2026-10-22`, unchanged: the split changes no output) =
  `OBS.ALGO_VERSIONS.rules`; `explain` = `explain/personal.py:EXPLAIN_VERSION` (`explain-1`, new) = `OBS.ALGO_VERSIONS.explain`.
  One PR (no M6a / M6b split). **Python split** (outputs unchanged; `tests/test_native_rules.py` proves it on the database):
  the rules were pure functions of `Ctx` already; `rules/context.py` gains the plain-data layers — `to_data` / `from_data`
  (the Ctx as JSON), `assemble(raw)` (the pure end of `load`: goal resolution for the 7 days, `merge_weights`, the nights
  before the day, tonight's sleep duration), `load_raw(conn, …)` (`load`'s reads, nothing assembled) and `inputs(conn, …)`
  (the part of `raw` the phone does not hold, `INPUT_KEYS`; the phone's own part is `LOCAL_KEYS`: scores, journal, body mass,
  sleep sessions, the newest sleep need); `rules/engine.py` `reminder_rows`, `evaluate_data` (pure: summaries + the rows
  `store` writes), `results_from` (rows → `RuleResult`s for `store`). `goals/nutrition.py` `goal_for(day, versions, sparky,
  en)` (pure; `_goal` takes the language) and `day_from_totals` (a `nutrition/daily.summarize_day` row from the log's day
  sums). `explain/personal.py`: every `calc_*` = its query + a pure `*_compute` (`hrv_compute` … `lab_compute`,
  `bedtime_items` = the pure twin of `assistant/status.calc_bedtime`, which is not edited), `metric_view` / `lab_view` (the
  sheet from the static entry + the personal part), `metric_data` / `metric_out` (the phone's twin: plain JSON rows → the
  personal part), `lab_out`. `records/lab_history.py` was pure already (`_history_of`, `card`, `trend`).
- **JS** `src/36_rules.js`: `OBS.rules` (`Ctx`, every rule, `evaluate`, `assemble`, `jsonable`, `nutrition.*`, `params` for
  rules / goals / water / constraints / nutrition / context), entry points `rulesLocal(raw)`, `rulesEvaluate(data)`,
  `nutritionGoals(versions, from, to, sparky)`, `nutritionDays(totals, versions, from, to, sparky)`; reuses M4's
  `OBS.coach.goals` (bedtime plan) and `fmtG`. Python's `str(float)` of the texts' numbers is reproduced (`pf`: "1.0 kg").
  `src/37_explain.js`: `OBS.explain` (every calculator, `metric_out`, `metric_view`, `lab_view`, `bedtime_items`,
  `lab.{history_of, card, trend, compare_to_range}`, `params` incl. the Trends metrics' series / status / digits / units),
  entry points `explainMetric(key, entry, data, day)`, `explainLab(key, entry, data, day, profile)`, `labHistory(key, rows)`.
- **Goldens**: `test/cases_rules.py` (synthetic, the person of `tests/test_rules.py`: a weekly plan of three templates +
  basketball; 55 runs: every rule fires / is quiet / is short of data, `common.need` errors and missing SparkyFitness,
  banned exercises in the plan / the log / too heavy, caution by exercise rules and by the SparkyFitness description
  marker, unmapped exercise rules, plan day changes to another weekday and a free label, red meat by grams / meals / food
  limit with marked grams, portions and milk excluded, added sugar with the default and an own cap, sugary drinks, the
  bedtime goal's late nights / stage / final target / late wake-up, water before / after 15:00; 4 raw runs: goal versions,
  SparkyFitness fallback goals, weigh-ins + body mass, sleep across the 2026-10-04 change to daylight saving time, plan day
  changes, a weigh-in error, no SparkyFitness; nutrition goals / day rows over 31 days in both languages) →
  `golden/rules.json`; `golden/rules_local.json` = one raw input + the Python answer (iOS fixture). `test/cases_explain.py`
  (M3's synthetic person at five days — the last, 50 days back, early with < 14 baseline days, before any data, a day
  without a score row — for the ten table-backed keys + 68 handmade runs for every tone / branch, the Trends metrics on
  handmade series documents; 7 lab sheets, 5 lab histories; 236 sheets in all, each in Chinese and English, the glossary
  entries in the golden file) → `golden/explain.json`. Every text compared verbatim. Tests `tests/test_js_golden_rules.py` ↔
  `test/rules.test.js`, `tests/test_js_golden_explain.py` ↔ `test/explain.test.js`; `tests/test_native_rules.py` (DB:
  `evaluate_ctx(load)` == `evaluate_data(assemble(load_raw))` == `evaluate_data(to_data(load))` on four days; the kind;
  the records pull; the inputs endpoint; every `CALC[key]` == `metric_out(metric_data)` on M3's person; `bedtime_items` ==
  `calc_bedtime`); `Watch2WhoopTests/LocalRulesTests.swift`.
- **Server**: `GET /v1/users/{user}/rules/inputs?date=` (`rules/api.py`; flag `native_rules`, own data only: another user's id
  is 403) = `context.inputs`: raw rows, nothing computed (the phone holds the rest). Kind `derived_kinds/reminders.py`:
  rows of one day = one run row `{role: run, now, rules: [summaries]}` + one row per reminder `{role: reminder, rule_key,
  subject, level, title, message, title_en, message_en, evidence, sticky, status}` (`status` = the phone's own change or
  null); spot check = `evaluate_data(assemble(load_raw(day, now = the phone's now)))`, the (rule, subject) set and every
  field exact (evidence and summaries are rounded to 3 decimals on both sides by `jsonable`), the phone's status taken over;
  primary = `engine.store` with the phone's rows (same idempotent upsert, sticky dismissals, old rows of an errored rule
  kept, `rule_runs` written) + origin `app` / `bundle_version`, and a phone status applied only forward (new → seen →
  dismissed). Record kind `reminders` of the `records` stream. Migration `1050_native_rules.sql` (`origin`,
  `bundle_version` on `reminders`; `sync_attach` by `date`).
- **iOS**: `RulesSchema` → `v8_rules_reminders` (`reminders` keyed by (date, rule_key, subject): the row in `payload`,
  `status`, `status_dirty`, `server_id`). `RulesStage` (implemented): after a new rules algorithm, on a new day or every 15
  minutes, pulls `rules:inputs:<day>` for today and yesterday, the journal (M5's `ReportsLocal.pullJournal`) and the
  server's reminders (`records` filtered to `reminders`, own cursor `rules.records_cursor.reminders`: server ids, statuses
  changed elsewhere; a status changed here and not confirmed only moves forward), evaluates today at the run's time and
  yesterday at 23:59 (the spot check needs a settled day), writes `reminders` (shadow and primary; sticky dismissals
  inherited, rows of an errored rule kept), `derived_rows` kind `reminders` (unchanged rows — the run's time aside — keep
  their upload state) and the document `reminders.local:<day>` (the server's list shape; id = server id or minus the
  local row id), uploads with `DeriveStage.upload`; shadow comparison with the server's Today list in meta `rules.compare`.
  Primary: `TodayStore` draws `reminders.local:<day>` (English texts with the app in English); 「已看 / 不再提醒」 updates the
  local row (`RulesStage.setStatus`) and, with a server id, queues `PATCH /reminders/{id}` as before; without one the
  status rides along with the next upload. `ExplainSheet.swift` `ExplainLocal`: metric sheets from the local tables
  (`metric_data` on `daily_metrics`, `daily_scores` payload, main sleeps, weights + the profile's goal weight, M3's local
  VO₂max and 30-day series documents) with the static texts of the cached server sheet → `explain.local:metric:<key>:<day>`;
  shadow compares the personal part (meta `explain.compare`), primary shows the phone's. `ReportsLocal`: the nutrition block
  is computed (`nutritionDays` on the log's day sums and the goal versions of the newest inputs document) when the versions
  cover both periods; its added-sugar, total-sugar and sugary-drink fields stay the server's (`server_only_paths`).
- **Deviations**: (1) the phone reads the rules' non-watch inputs (exercise rules, SparkyFitness library / templates / plan /
  training log / food log with its extras / weigh-ins, goal versions, bedtime goal, day changes, diet constraints, water and
  its goal) as one raw document from the new inputs endpoint instead of separate docs (`nutrition goals doc`,
  `exercise:rules`): `ext_records` has the log's sums but not the food names, extras or templates the rules need; the
  evaluation itself, goal resolution, weights merge and nights are the phone's; (2) only today and yesterday are evaluated
  (the server's `evaluate-rules` also only evaluates the current day); (3) status / lab sheets stay the server's: status
  items and profile sentences live in the AI memory (§1.3), and no app page opens a lab sheet — `explainLab`,
  `labHistory` are ported and golden-tested for later; (4) without a cached server sheet the phone's sheet has the key as
  title and no static texts (the glossary is not on the phone: the static bundle is not in the app yet); (5) the report's
  added sugar / sugary drinks / total sugar stay the server's (food extras); a window that starts before the first goal
  version keeps the whole nutrition block the server's (its goals are SparkyFitness's); (6) the training week / plan
  suggestions of `/training/week` (Features/Record) stay the server's: no rule computes them (they are `training/` views);
  (7) a day's inputs can change after the phone evaluated it (a meal logged later): the spot check compares the newest
  settled day only; float-valued integers of SparkyFitness rows lose their type on the way through JSON (Python's
  "12.0" vs JS "12" in a text) — none in the goldens; (8) `native-compare` is not part of M6.
- **Flag**: `native_rules` (shadow; primary needs `native_compute_primary`), off by default; it also opens the inputs
  endpoint.
- **Device checklist entry (for S9)**: owner's internal build, `native_rules` on for the owner. (1) After a sync round the
  compute report (`meta` `compute.last`) shows `rules` `done` with `reminders:<yesterday>` / `reminders:<today>` written and
  uploaded; `GET /v1/sync/compute` shows kind `reminders` with a passing last check; meta `rules.compare` shows `ok` for
  `reminders`. (2) Open 3 metric sheets (HRV, 睡眠时长, 恢复): meta `explain.compare` is `ok` for each. (3) With
  `native_compute_primary` on (S9), airplane mode: log a workout in the app's offline queue, the next round shows today's
  pre-workout reminder; a metric sheet shows its personal advice offline; 「不再提醒」 on a reminder offline, back online:
  the AI's `get_reminders` lists it dismissed after the next upload, and the dismissal survives a pull.

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

### Pull kind filter (wave 2 follow-up)

`POST /v1/sync/pull` takes an optional `kinds: {stream: [kind, ...]}` (only the stream `records` has kinds; kinds are validated
against `bodies.record_kinds()`, an unknown kind, an empty list, a stream without kinds or a stream that is not in `cursors`
is 422 `bad_kinds`). Absent = everything, as before. A stage that needs one kind (M1c's `WorkoutsStage`: `workout`) no longer
downloads the whole records stream; no other M1 / M2 stage pulls a stream for one kind.

**Cursor design.** The server stays stateless: a filtered pull stamps its cursors with the sorted kind set
(`c.812.40~workout`, `r.812.2026-03-10~journal,water`). A cursor whose stamp differs from the request's kinds (an unfiltered
cursor sent with a filter, a filtered one sent without, another kind set) is 422 `bad_cursor`, and the app restores from `""`,
as for any bad cursor. So a filtered cursor can never stand in for an unfiltered pull of the same stream (which would skip every
other kind that changed before it); the app keeps one cursor per (stream, kind set) and the stage's key is its own
(`workouts.records_cursor.workout`; the old unfiltered `workouts.records_cursor` of the betas is not read any more). An empty
cursor is valid for any kind set. Tests: `test_native_sync.py::test_pull_kind_filter_and_its_cursor_namespace`,
`LocalWorkoutsTests`.

### Wave 2 follow-up: DST in `cardio/hrr`

`hrr60` and `hrr.compute` read aware datetimes that share one `ZoneInfo`; Python adds, subtracts and compares those as wall
clocks, so the 2-minute peak window and the +60 s target of a workout around a DST change were an hour off. Both now work in
UTC (same pattern as `training/importer.py`, #366). The JS twin (`31_workouts.js` `session_hr_recovery`) works in epoch
microseconds, so it was never affected: goldens and bundle unchanged. Tests: `test_rawdata.py::test_hrr60_across_dst_end`,
`test_hrr_compute_across_dst_end` (Sydney, 2026-04-05).
