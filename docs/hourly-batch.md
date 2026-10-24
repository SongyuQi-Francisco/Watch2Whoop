# Hourly / nightly job at 300 users: `body-engine hourly-batch`

Issue #264. 中文摘要在最后。

## Problem
The hourly job (`deploy/ops/ops.py hourly` on the Mac, the server's job runner on Ali) ran `import-ow`, `compute-scores --start <3 days ago>`
and `evaluate-rules` **per user, each in its own one-off container** (about 2 s start-up each). Measured on the overseas production
server: 19 users = 275 s, about 14.5 s per user, so 300 users ≈ 72 minutes — longer than the hourly interval, runs pile up.

## What it does now
`body-engine hourly-batch [--workers N] [--full] [--steps …] [--user-id N] [--no-skip] [--max-age-hours H]`

**Main idea: only process what changed. Parallelism is a helper.**

- **Dirty detection by content fingerprint, not a queue.** Every writer (OW import, Shortcut upload, the app sync engine's
  derive, manual logs, labs, weigh-ins…) ends up in the same tables, so instead of hooking each write path to enqueue
  "user + date" (and forgetting one), the job fingerprints the inputs per user and step and stores it after each successful run
  (`batch_state`, migration 740). Unchanged fingerprint = nothing to do for that user: no computation at all, the user costs a few
  small queries. The fingerprint is: the user's local date; an order-independent hash of daily values, sleep sessions and workouts
  *by content* (`import-ow` rewrites the last days every hour and bumps `updated_at` even when nothing changed, so timestamps would
  always look dirty); the sync cursors; the Shortcut upload / history import counters. `evaluate-rules` and `refresh-status` add the
  user's own logs of the last 14 days (food, weigh-ins, logged workouts, journal, training-day changes).
- **`import-ow` is probed first.** One small request: does Open Wearables have any raw sample newer than our sync cursor?
  No → the import is skipped (and `sync_state.last_synced_at` is stamped, the doctor watches it). Any doubt (no cursor, error) → import.
- **Reuse.** Skipped users keep their `daily_scores`, baselines and trends untouched. A changed user is recomputed with the same
  functions the single commands use. Note that `compute-scores` loads the whole history from the scoring start on purpose (CTL,
  baselines and the 365-day HRmax window depend on it) and writes only the last 3 days (`--start`); recomputing only the dirty dates
  would not make it cheaper, so a truly incremental scoring engine (state snapshots) is a separate, bigger piece of work.
- **Full recomputes only when asked**: nightly (`--full`), a changed `ALGO_VERSION` (run `compute-scores` for all, as before), or by hand.
- **Safety net.** Some inputs are not in the fingerprint (SparkyFitness, late samples older than the cursor). An unchanged step is
  therefore recomputed from 4 hours of age on, in the user's turn (one hour in four, by user id, so the work is spread out), and at the
  latest after `--max-age-hours` (default 6). A skip stamps `batch_state.checked_at`; the doctor counts `max(daily_scores.computed_at,
  checked_at)` as the freshness of the scores. A failed step is not remembered, so it is retried next hour.
- **One process, all users, isolated.** Each user has its own database connection; a failing step (exception, bad data, OW down)
  is logged, rolled back and the next step / user carries on. All errors are summarised. `--workers N` (default 1) starts N worker
  processes (`spawn`; scoring is Python CPU work, so processes, not threads).
- **Idempotent.** Same functions as the single commands (`import-ow`, `compute-scores` + insights, `evaluate-rules`, `refresh-status`);
  users without consent are skipped like there.
- **Output.** One line per step in the old log format (`✓ compute-scores user 3（2026-10-22 起）（1 秒）…`, `✗ …`, `· … 跳过`), then
  a summary line and the machine-readable `BATCH_RESULT {json}` as the last line. Exit code 1 when any step failed. The ops scripts copy the
  lines into their log and put `errors` into `state.json`.
- **Nightly** = `hourly-batch --full` (full `compute-scores`, nothing skipped), then the backup as before.

Not in the batch (a handful of global commands, each one container per run): `provision-run`, `oauth-cleanup`, `ai-cards periodic`,
`hk-derive-run`, `history-import-run`, `memory-review-run`.

## Settings
| | Mac (`deploy/ops/ops.py`) | Server (private job runner) |
|---|---|---|
| workers | `BODY_BATCH_WORKERS`, default 2 | `BODY_BATCH_WORKERS`, default 3 |
| memory of the container | – | `BODY_BATCH_MEMORY`, default 1536m (each worker ≈ 150–250 MB) |
| steps | import-ow, compute-scores, evaluate-rules, refresh-status | import-ow, compute-scores, evaluate-rules |

## Measurements (300 synthetic users)
`tools/bench_hourly_batch.py`: 300 made-up users with 120 days of history each (daily values, sleep, ≈ 190 000 raw heart-rate /
HRV samples served by an in-memory fake of Open Wearables, optionally with a delay per 1000-sample page standing in for HTTP),
step `compute-scores` (+ insights) and `evaluate-rules`, in a throwaway database `body_test_bench`. Machine: 10-core Apple-silicon laptop
(the servers have fewer cores — divide the speed-up accordingly), local Postgres.

| 300 users, 120 days of history each | OW delay | 1 process | 4 processes |
|---|---|---|---|
| **Everything recomputed** (like `--full`, or the old way minus container start-ups) | 0 ms | 740 s (2.5 s / user) | 198 s |
| | 20 ms / page | – | 510 s |
| **Nothing changed** (all steps skipped) | – | 5 s | – |
| **Typical hour: 10 % of the users (30) have new data** | 0 ms | 78 s | **23 s** |
| | 20 ms / page | – | **56 s** |

On top of the typical hour comes the safety net: unchanged steps older than 4 h are recomputed in the user's turn (about a quarter of the
users per hour once they are older than 4 h, so roughly 1/6 of the unchanged users per hour in steady state, ≈ 45 users): about +1 minute
with 4 processes. So a normal hour for 300 users is **about 1–2 minutes** and the worst case (everything recomputed) is **3–9 minutes with 4 processes**,
both within the 10-minute goal; the old way needed about 72 minutes.

The old way is measured by the production run: 14.5 s per user, of which about 6 s are container start-ups (3 × ≈ 2 s).
`import-ow` (a two-day window) is not in the synthetic runs; it is network-bound and runs in the same worker.

## Rollout
1. Merge, rebuild the `body-engine` image on the server (the job runner starts it as a one-off container), run `migrate` (adds `batch_state`).
2. `ops_jobs.py` / `ops.py` already call the new command; nothing else to switch. Rollback: redeploy the previous ops files (the
   per-user commands still exist).
3. Look at the first hourly log: the summary line shows users, skipped steps, failures, seconds.

## 中文摘要
每小时 / 每晚任务原来对每个用户、每一步各起一个容器（启动约 2 秒），19 个用户 275 秒，300 个用户约 72 分钟。现在 `body-engine hourly-batch`
在一个进程里循环所有用户：每个用户独立连接和异常隔离，`--workers N` 并发，上次成功后输入（按内容指纹 + 本地日期）没变的步骤跳过（至少每 2 小时
重算一次，doctor 把超过 3 小时的评分判为失败），`--full` 用于夜间。日志仍是一步一行的老格式，最后一行是 `BATCH_RESULT {json}`。
