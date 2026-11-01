# iOS 2.0.0 device checklist (collected by S9)

Run on the owner's internal TestFlight build before primary goes to the `inner` cohort (runbook step 3 in
[ios-native-2.0.md](ios-native-2.0.md) section 4 S9). Synthetic or owner data only; write down pass / fail and field
names, never values of other people. Each section is the package's own entry ("Device checklist entry (for S9)" in the
contract); the S9 section at the end checks the server side.

Before you start: the owner is in the `inner` cohort, `native_sync` and every package flag are on for `inner` (shadow),
the app is 2.0.0 (`X-App-Version`), `GET /v1/sync/compute` lists every package as `shadow`.

## M1 — daily values and sleep (`native_derive`)
1. After a sync round, the sync center's compute report (meta `compute.last`) shows `derive` `done` with days written and
   `daily` / `sleep` uploaded; `GET /v1/sync/compute` shows both kinds with a passing last check.
2. Airplane mode the morning after a night: the round still derives (report `waiting_sync` only for the upload); with
   primary on, steps, energy and last night's sleep come from the phone.
3. Back online: one upload of the unsent days, then nothing on the next round; a pull does not duplicate
   `sleep_sessions` or `daily_metrics` rows.

## M3 — Trends series (`native_series`)
1. Shadow: open Trends on every range, the month calendar and 睡眠; meta `series.compare` shows `ok` for each page.
2. Primary, airplane mode: Trends opens on all ranges, Today's 「全部指标」 equals Trends 「今天」, the month calendar colours
   and the last 14 nights match the server's from before.
3. Trends 365-day scroll stays at or above 55 fps (ios-native-app.md section 9.3 method).

## M1c — workouts (`native_workouts`)
1. After a sync round the compute report shows `workouts` `done`, `workouts:<day>` uploaded; `GET /v1/sync/compute` shows
   the kind with a passing check.
2. Record a workout offline: the next round (still offline) writes it to `workouts` with TRIMP, zones and HRR; meta
   `workouts.compare` shows `ok` for the calendar and the day once online.
3. Primary: the training calendar and the day sheet open offline with the same numbers as the server's before.

## M2 — stress, resilience, body battery, HR recovery (`native_stress`)
1. After a sync round the compute report shows `stress` `done` with days written and uploaded; `GET /v1/sync/compute`
   shows kind `stress` with a passing last check.
2. Open Trends: meta `stress.compare` is `ok` (resilience, body battery, heart-rate recovery equal the server's).
3. Primary, airplane mode during the day: Today's 「压力」 and 「身体电量」 and Trends 「韧性」 update after a sync round
   without network.

## M4 — coaches, body age, Today composition (`native_coach`)
1. After a sync round the compute report shows `healthspan` `done` with weeks written and uploaded; `GET /v1/sync/compute`
   shows kind `healthspan` with a passing last check.
2. Open Today and a night's sleep detail: meta `coach.compare` is `ok` for `strain_coach`, `health_monitor`,
   `today_scores`, `today_sleep`, `sleep_night`.
3. Primary, airplane mode the morning after a synced night: Today renders the rings, strain target, health monitor and
   the sleep sheet; Trends' body-age series is the phone's.

## M5 — reports and journal analysis (`native_reports`)
1. Open Trends → 周报, then 月报: meta `reports.compare` shows `ok` for `report_week`, `report_month` and
   `journal_analysis` (`…_regression` with `journal_regression` on).
2. Primary, airplane mode: the weekly and monthly report and 「行为影响」 open with the same numbers as before; the nutrition
   tiles stay the cached server values.
3. Log a journal answer online, refresh: the card's counts follow after the next pull.

## M6 — rules, nutrition targets, explain texts (`native_rules`)
1. After a sync round the compute report shows `rules` `done` with `reminders:<yesterday>` / `reminders:<today>` written
   and uploaded; `GET /v1/sync/compute` shows kind `reminders` with a passing last check; meta `rules.compare` shows `ok`
   for `reminders`.
2. Open 3 metric sheets (HRV, 睡眠时长, 恢复): meta `explain.compare` is `ok` for each.
3. Primary, airplane mode: log a workout in the app's offline queue, the next round shows today's pre-workout reminder;
   a metric sheet shows its personal advice offline; 「不再提醒」 on a reminder offline, back online: the AI's
   `get_reminders` lists it dismissed after the next upload, and the dismissal survives a pull.

## S9 — the server stops computing (server side, on the primary server)
1. `body-engine app-compute status --user-id 1`: every package `granted` and `server_primary` true after runbook step 3;
   `gates` per kind with at least 3 checks and a pass rate of 1.0 (or ≥ 0.95 with the failing field names understood).
2. The next hourly job's log: `evaluate-rules user 1 … app 计算（rules 已转正）`, `compute-scores` with
   `"app_scoring": true` and no `insights` writes (`"healthspan": "app", "stress": "app"` when it runs them); hk-derive-run
   reports `phone_primary`, `phone_days`, `waiting`, `fallback_days` (fallbacks near zero).
3. `app-compute status` → `fallbacks`: only occasional rows (the phone was asleep); a growing count for one package means
   the phone stopped uploading it: look at `GET /v1/sync/compute` (last upload, last check).
4. Ask the AI (MCP) for stress, body age, reminders and a training day: it answers with the phone's numbers.
5. Kill switch drill: `body-engine flags set native_compute_primary --state off`, wait 30 s: `GET /v1/sync/compute` shows
   every package `shadow`, the next hourly job computes the user again (`stress_days` in its log); set it back.
