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

## New data within minutes (migration 770)
The hourly job alone meant: the phone uploads the night's data to Open Wearables at 07:00, Body Engine pulls it at 08:10, the
recovery score follows. Two pieces make it minutes; the hourly job stays as the safety net.

**1. Noticing uploads: `sync/ow_watch.py`.** Inside the API process, every 30 s, one request to OW: `GET /users` sorted by
`last_synced_at` (OW stamps it after every saved SDK batch), newest first, stopping at 6 hours back. A user has new data when OW's
time is later than both the import last queued for them (`ow_watch.queued_through`) and the start of the last OW import or probe
from any path (`sync_state.last_synced_at`). Once their uploads went quiet for 45 s (an upload round is several batches seconds
apart) and at most once per 3 minutes per user, `sync_refresh` is set to `scores_queued` with `import_queued_at`. Nothing else
happens while nobody uploads: no container, no write. Users without consent, without an OW link, or with a refresh running are left
alone. Settings in `sync/params.py` (`OW_WATCH_*`); `BODY_OW_WATCH=off` disables it; `body-engine ow-watch` runs one round by hand.

**2. Doing the work: the existing queue.** The per-minute job that already runs `sync-refresh-run` when something is queued (on the
server: the imports timer, a one-off container only then) now imports from OW first when `import_queued_at` is set (`import-ow` for
that user under the same per-user lock as the app's refresh), then recomputes the scores, insights and rules as before. Afterwards
the hourly job's fingerprints are stored, so the hourly job skips the user. The Today page shows 「同步中…」 while queued and
reloads when the scores are in (`web/static/sync.js`, unchanged); there is no server-side cache of the Today data.

**3. Making the recompute short: `importers/ow_cache.py`.** The recompute was slow because the scoring engine reads every raw heart
rate / HRV / respiratory rate / SpO2 / wrist temperature sample from a year before the scoring start, and the stress score 104 days
of heart rate / steps / HRV, over OW's HTTP API. Profiled read-only on the production server for the user with the most history
(14 months of watch data, 2026-10-25): 94 s in total, of which 80 s were 356 requests to OW's `/timeseries` (225 ms per 1000
samples), 9 s the day-by-day scoring, 1 s body age. Now:
- A local copy of those samples (`ow_sample_cache`, per user / type / UTC day / source, arrays of epoch microseconds and values;
  measured locally: 475 000 samples = 6 MB, 300 000 read back in 0.4 s). A recompute reads the copy and fetches from OW only the
  last 2 days (late uploads land there). `import-ow` stores what it pulls anyway, so late samples a few days old reach the copy.
- Full recomputes (`hourly-batch --full` at night, `compute-scores` without `--start`, a new user's history) re-read everything
  from OW and replace the copy, so changes deep in the history arrive within a day, as before for anything older than the hourly
  window. A re-linked OW user or a new `CACHE_VERSION` invalidates the copy. A broken copy (missing table) falls back to reading OW.
- Scores are the same: the copy hands out exactly the samples OW returns (`tests/test_ow_cache.py` compares samples and the
  `daily_scores` rows computed both ways).
- The day loop no longer scans the whole history per night (raw-series time lists and the sleep-stage segments are indexed
  once): 3.5 s → 0.6 s for 14 months of synthetic data, identical output.

Expected: about 15–25 s per recompute for that user (container start, import of the last days, copy read, two days from OW,
scoring, stress, body age, rules) instead of 90–200 s, and **at most about 3 minutes from the end of an upload to the new
scores** (45 s quiet + up to 30 s until the next poll + up to 60 s until the per-minute job + the recompute). The first recompute
of each user after the deploy fills the copy and takes as long as before.

## 中文摘要
每小时 / 每晚任务原来对每个用户、每一步各起一个容器（启动约 2 秒），19 个用户 275 秒，300 个用户约 72 分钟。现在 `body-engine hourly-batch`
在一个进程里循环所有用户：每个用户独立连接和异常隔离，`--workers N` 并发，上次成功后输入（按内容指纹 + 本地日期）没变的步骤跳过（至少每 2 小时
重算一次，doctor 把超过 3 小时的评分判为失败），`--full` 用于夜间。日志仍是一步一行的老格式，最后一行是 `BATCH_RESULT {json}`。

新数据几分钟内出分（迁移 770）：API 进程每 30 秒问一次 OW 哪些用户刚上传过（`GET /users` 按 `last_synced_at` 排序，一个请求），上传停了
45 秒的用户在 `sync_refresh` 里排队（同一用户最多 3 分钟一次）；每分钟任务已有的 `sync-refresh-run` 先 import-ow 再算分，算完记下每小时任务的
指纹（每小时那次就跳过）。算分慢是因为每次都经 HTTP 从 OW 读一年多的原始样本（生产实测 94 秒里 80 秒是 356 次请求），现在原始样本在本地有一份
副本（`ow_sample_cache`），只从 OW 读最近 2 天，夜间完整重算时整份刷新；逐天循环也不再每晚扫全部历史。预计单次重算 15–25 秒，上传结束到出分
最多约 3 分钟。每小时任务保留做兜底。
