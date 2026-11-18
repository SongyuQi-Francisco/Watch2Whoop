# Memory of the API process (and why it was OOM-killed)

中文摘要在最后。

## What happened

On the production server (2 vCPU burstable, 7.4 GB shared with another product, every container capped) the
`body-engine` container (FastAPI under uvicorn, `mem_limit` 256 MB at the time) was killed by the kernel's cgroup OOM
killer 20 times in 7 days, the database container 11 times. 16 of the 20 API kills fell on minute :10 (hourly job) or
:40 (health check). Each kill dropped every request in flight (nginx 502 / `recv() failed (104)` bursts, ~100 5xx a day).
Raising the limit to 384 MB stopped the kills but left ~20 % headroom and no explanation.

## Measured

Python peak RSS, one fresh interpreter per line (`resource.getrusage`, macOS arm64; Linux x86-64 is within ~10 %),
and `tracemalloc` inside the API process against a test database with a synthetic user (365 days of Apple Health
export through the real importer) and the full exercise catalog (free-exercise-db + openGym, 5,647 rows):

| What | Before | After this change |
|---|---|---|
| `import body_engine.api` (RSS) | 166 MB | **110 MB** |
| of which pypinyin (voice hotword fix, `ai/voice_fix.py`) | ~55 MB, 33 MB of it JSON-decoded dictionaries | loaded on first voice-fix call only |
| `body-engine <any command>` in a new process | 72–75 MB | same, but no longer started inside the API container |
| exercise catalog cache (`training/catalog.py`, first `/training/exercises`) | +33 MB, +~5 MB per language / step / picture view | same; `norm()` cache bounded at 40k entries (was 200k) |
| largest single GET (`/trends?days=365`, a year of `/scores?details=true` or `/daily-metrics`) | 6 MB peak, ~0 kept | same |
| plugin jobs (5-minute usage refresh), OW poller round | < 1 MB | same |
| in-process "sync now" import (`sync/refresh.py`) | one 30-day window of raw OW samples (tens of MB for a new user's first sync) | `BODY_SYNC_IMPORT=queue`: imported by the per-minute job |

On the server itself (read only, 2026-10-25): a freshly started API was 213 MB resident; the first Today page start-up
bundle (`POST /v1/web/boot`, up to 24 endpoints in parallel threads, catalog loaded) took it to 265 MB; it then settled at
~310 MB. At 22:40:04 two `body-engine` processes were running **inside the API container** next to uvicorn
(`ai-fallback status` and `muscle-coverage-check`, 36→52 MB and growing): the health check runs them with
`docker exec body-engine …`, and two health checks ran at the same minute (the server's own timer and the backup Mac's
copy over ssh). The hourly job did the same at :10 with `provision-run`.

## Root cause

A `docker exec` into a container runs in that container's memory cgroup. Every `body-engine` CLI process imports the
code base (~75 MB) and then does its work; with uvicorn at 220–250 MB under a 256 MB limit, one exec was enough, and
the kernel kills the *largest* process in the cgroup: uvicorn. The database kills were the same pattern in the database
container (`pg_dump` run with `docker exec` inside it, on top of 128 MB shared buffers and up to 100 connections).
The steady growth of the API (213 → 310 MB) is the catalog cache plus glibc's per-thread malloc arenas: requests run in a
pool of ~40 threads, and with the default of 8 arenas per CPU freed memory stays spread over many arenas.

## Changes

- No `docker exec body-engine …` for jobs or checks: they run in one-off containers with their own limit
  (the deployment's `engine.sh`). The backup machine reads the server's own hourly health-check result instead of
  running a second one (`deploy/ops/ops.py`, used while younger than 75 minutes).
- `ai/voice_fix.py`: pypinyin / jellyfish imported on first use (a test checks that `import body_engine.api` does not
  load them).
- `MALLOC_ARENA_MAX=2` in the image (`body_engine/Dockerfile`).
- `BODY_SYNC_IMPORT=queue` (opt-in, `sync/refresh.py`): "sync now" only queues the import for `sync-refresh-run`.
- `training/catalog.norm`: LRU bound 40k (the catalog itself needs ~18k).
- Expected result: resident ~160 MB after start, ~210–250 MB with the catalog and typical traffic, so a 384 MB limit
  keeps ≥ 30 % headroom (target: < 250 MB resident).

## Second round (2026-11): a full-history recompute inside a request

### What happened

From 2026-11-10 the API process (`mem_limit` 384 MB) was killed by the kernel 0–5 times a day again, 32 times by
2026-11-18, always uvicorn itself (anon-rss 384–389 MB) and never at the times of the scheduled jobs. Lining up every
kill (`journalctl -k`) with the proxy's access log: **all 32** had a `PUT /v1/users/me/scores/app` in flight (the
app uploading the scores it computed with the JS bundle, #340 N2), always right after the same user's
`GET /v1/sync/scoring`; it answered 502 after 1–10 s, or the app gave up after its 15 s timeout (499). The app retries on
the next sync, so the same user killed the API again and again (whoever else was mid-request got a 502 too).

### Why

The upload's **spot check** (`nativesync/appscores.py`) recomputes one day in Python to compare it with the app's row,
and the scoring engine always loads the user's raw samples from the scoring origin minus 365 days (the HRmax window) to
today: heart rate, HRV, respiration, SpO2, temperature, sleep stages, from every source, merged into Python tuples and
dicts. For a user with a year of watch data (~460k raw samples in the synthetic test, ~660k for the user who triggered the
kills) that is **+225 MB for ~10 s** on top of whatever the process already held. A check runs for the first upload of a
bundle and at most every 6 hours, but a killed request never records its check, so after the first kill every upload of
that user was due again.

`GET /v1/sync/scoring?hist_from=&hist_to=` (the phone asks once after an install for 430 days of heart-rate histograms)
had the same shape: one load over 430 days, **+185 MB**.

What the process holds between requests made it worse: with glibc, memory freed after a big request mostly stays in the
process (free chunks inside the malloc arenas, pinned by long-lived objects such as the exercise catalog cache, +46 MB,
loaded by the first training request). After a histogram request with the catalog loaded the process stayed at 318 MB;
the next spot check took it to 425 MB, above the limit. On the server the API sat at 330–382 MB between kills
(`/proc/<pid>/smaps`: ~260 MB of it in the two malloc arenas, ~35 MB Python object arenas, 37 MB shared libraries).

### Measured (API image, Linux, glibc, `MALLOC_ARENA_MAX=2`; synthetic user with 435 days, ~460k raw samples)

| Step | Before | After |
|---|---|---|
| started (lifespan done) | 123 MB | 123 MB |
| Today page GETs incl. training (exercise catalog cache) | 182 MB | 181 MB |
| `GET /v1/sync/scoring` with 430-day histograms: peak / after | 382 / 318 MB | 191 / 191 MB |
| `PUT /v1/users/me/scores/app` with a spot check due: peak, time | 425 MB, 10 s | 191 MB, < 0.1 s (check queued) |
| 5 rounds of 32 concurrent Today requests afterwards | 275 MB | 194 MB |
| the queued check in the job container (`sync-refresh-run`, 512 MB limit): peak, time | – | 297 MB, 15 s |
| `malloc_trim(0)` after the "before" sequence | 281 → 212 MB | (done every minute) |

### Changes

- **The spot check never runs in the API process** (unless `BODY_SYNC_SCORES=inline`, single-machine setups that also
  recompute scores in the request): the upload parks its rows in `app_score_pending` (migration 1200), answers
  `{"status": "ok", "stored": 0, "check": {"date", "queued": true}}` at once and queues the per-minute job
  (`sync_refresh` → `sync-refresh-run`, a one-off container with its own limit). The job checks and then stores the rows
  (passed) or the server's row of that day (failed: the app is no longer trusted and the range is recomputed), exactly
  as the request used to; uploads arriving meanwhile join the waiting one. The hourly job runs a waiting check too (before
  its skip decision), in case the per-minute job does not run; a check started 3 times without finishing (its job died,
  e.g. at the job's own limit) is dropped so it cannot hold up the queue. The app needs no change: it treats `ok` the same as
  before and only reads `check.ok` for its report. Job logs carry status, counts, the day and field names, never values.
- **Histograms a month at a time** (`nativesync/scoring.hr_histograms`, `params.HIST_CHUNK_DAYS = 31`): memory follows
  the chunk; a day's counts do not depend on the window (the loader keeps one sample per type and time stamp, every
  copy of a sample is on the same local day; a test compares windows of 1, 7, 31 and all days).
- **`malloc_trim(0)` every minute** when the process grew by 16 MB since the last trim (`memtrim.py`, lifespan;
  `BODY_MALLOC_TRIM_S`, default 60, 0 = off; glibc only).
- What else loads a long range in a request was checked: the phone's history pages (`nativesync/history.py`) are 14 days
  with at most 2 at once; `/trends?days=365` reads stored daily rows (+5 MB). Everything else that loads the whole history
  runs in job containers (`scores_engine.run` in batch / refresh / resync / history import).

## Database container sizing

Everything in a Postgres container counts against its cgroup limit: `shared_buffers` (shared memory, counted once
touched), each connection's private memory (2–5 MB idle, plus `work_mem` per sort / hash node while a query runs,
times `hash_mem_multiplier` for hashes), autovacuum workers (`autovacuum_work_mem` each) and page cache (reclaimable: a
high `memory.failcnt` alone is harmless; real kills show in `journalctl -k`). Postgres defaults (128 MB buffers,
100 connections, 64 MB maintenance memory for 3 autovacuum workers) can exceed a 320 MB limit. For a database of a few
hundred MB with ~20 connections in use:

```
shared_buffers=64MB  max_connections=40  work_mem=4MB  hash_mem_multiplier=1
maintenance_work_mem=32MB  autovacuum_work_mem=16MB  autovacuum_max_workers=2  effective_cache_size=256MB
```

Worst realistic case ≈ 64 + 20 × 5 + sorts + 2 × 16 ≈ 220 MB, ~30 % under 320 MB. Run `pg_dump` from a one-off
container on the database container's network, not with `docker exec` inside it.

## Also in this change (robustness, observability)

- `daily_scores` writes take a per-user advisory transaction lock and upsert: two recomputes of the same user at once
  (hourly batch + per-minute refresh) failed with `UniqueViolation daily_scores_pkey`.
- Self-signup provisioning (`account/provision.py`, migration 785): transient errors (connection refused, timeouts,
  HTTP 5xx) are retried inside a run (3 tries, 2 s / 5 s); a failed run records every failing step with its exception
  type in `last_error` and waits 10 min × 2^(attempts−1), at most 6 h (`next_attempt_at`); one user's failure no longer
  stops the others.
- Every response carries `X-Body-User: <id>` of the user the request authenticated as (`obs.py`); the reverse proxy logs
  it (`$upstream_http_x_body_user`) and hides it from clients (`proxy_hide_header X-Body-User`).
- `BODY_LOG_DIR`: the API's own log (warnings, tracebacks, uvicorn errors; no access lines) also goes to
  `<dir>/engine-YYYY-MM-DD.log`, 14 days kept (`BODY_LOG_KEEP_DAYS`). `compose.yaml` mounts
  `health_data/ops/engine/`, so it survives container rebuilds and OOM restarts (`docker logs` does not).

## How to measure again

- Import cost: `python -c "import resource, body_engine.api; print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)"`.
- Per endpoint: run the app in process with `tracemalloc.start()` and FastAPI's `TestClient`, calling every
  `GET /v1/users/{user}/…` of `app.openapi()` for a seeded test user; compare `tracemalloc.get_traced_memory()` before,
  peak (`reset_peak()`) and after. Use a `body_test_*` database, never the real one.
- On a server: `docker top <api container> -o pid,rss,args` every second around :10 / :40 shows any extra process in
  the API's cgroup; `journalctl -k | grep "Killed process"` shows real kills.
- A kill's cause: list the requests in flight at each `Killed process` time (the access log has the end time and the
  duration of every request; a 502 / 499 ending at the kill whose start is before it). Every one of the 2026-11 kills
  had the same request in flight.
- What the resident memory is: `/proc/<pid>/smaps` grouped by mapping (`[heap]` and big anonymous maps = malloc
  arenas, 1 MiB maps = Python object arenas, file maps = libraries); compare `VmRSS` with `VmHWM`.

## 中文摘要

- **现象**：生产服务器上 API 容器（当时上限 256M）7 天被内核 OOM 杀 20 次，16 次在每小时 :10（hourly）和 :40（doctor）；数据库容器被杀 11 次。每次都让正在进行的请求变 502。
- **根因（实测）**：hourly 的 `provision-run` 和 doctor 的 `ai-fallback status` / `muscle-coverage-check` 都是 `docker exec` 进 API 容器跑的，每个是又一个约 75MB 的 Python 进程，算在 API 的内存上限里；内核杀的是组里最大的进程——uvicorn。22:40 实测到两个 doctor（服务器自己的 + Mac 通过 ssh 跑的）同时在 API 容器里各起一个进程。数据库被杀同理（`pg_dump` 用 docker exec 跑在数据库容器里，加上默认 128MB shared_buffers、100 个连接）。API 本身从 213M 涨到 310M 是动作库缓存（约 33MB + 每种视图约 5MB）加 glibc 的多线程 malloc arena。
- **修复**：任务和检查一律用一次性容器（自带内存上限），不再 docker exec 进 API；Mac 读服务器自己的 doctor 结果，不再同一分钟再跑一遍；pypinyin 改成第一次语音纠错时才加载（API 导入从 166MB 降到 110MB）；镜像设 `MALLOC_ARENA_MAX=2`；`BODY_SYNC_IMPORT=queue` 让"立即同步"只排队、由每分钟的任务导入；`norm()` 缓存上限 20 万 → 4 万。目标常驻 < 250MB、384M 上限留 ≥ 30%。
- **数据库**：给出 320M 上限下的参数（shared_buffers 64MB、max_connections 40、work_mem 4MB、autovacuum 2 个 worker 各 16MB），最坏约 220MB；`pg_dump` 放到一次性容器里跑。
- **顺带**：`daily_scores` 写入加按用户的事务级 advisory 锁 + upsert（修并发回算的 UniqueViolation）；开户 provision 对临时错误（连不上、超时、5xx）在一次运行内重试 3 次，失败写 `last_error`（带异常类型）并按 10 分钟 × 2^(次数−1)、最多 6 小时退避（迁移 785 `next_attempt_at`），一个用户失败不影响其他人；响应头 `X-Body-User` 供反向代理记日志（并对客户端隐藏）；`BODY_LOG_DIR` 让 API 的应用日志按天写文件、保留 14 天，不随重建容器丢失。

### 第二轮（2026-11）

- **现象**：11-10 起 API（上限 384M）每天又被内核杀 0–5 次，到 11-18 共 32 次，都是 uvicorn 本身、和定时任务时间无关。把每次被杀的时间和反向代理的访问日志对齐：**32 次全部**有一个 `PUT /v1/users/me/scores/app`（app 上传自己算的分数）正在处理，都紧跟同一个用户的 `GET /v1/sync/scoring`；被杀的请求没记下抽查，所以这个用户之后每次上传都要抽查、每次都把 API 打死。
- **原因**：上传的**抽查**在 API 进程里用 Python 重算一天，而评分引擎总是加载这个人从评分起点往前 365 天到今天的全部原始样本，一年的手表数据（合成测试约 46 万条）就是 **+225MB、约 10 秒**；`/sync/scoring` 带 430 天心率直方图也一样（+185MB）。glibc 在大请求之后不把释放的内存还给系统（动作库缓存等长期对象把它钉住），进程平时就停在 320–380MB，再来一次抽查就超过上限。
- **修复**：① 抽查不在 API 进程里做：上传的行先放进 `app_score_pending`（迁移 1200），马上回 `ok`（`check.queued = true`），由每分钟的 `sync-refresh-run`（一次性容器，有自己的上限）抽查后再存（通过 = 存 app 的行；不通过 = 存服务器那天的行、不再信任、重算），结果和原来一样，只晚约一分钟；每小时任务兜底；`BODY_SYNC_SCORES=inline` 时照旧在请求里做。app 不用改。② 心率直方图按月分块加载（结果逐天一致，有测试）。③ 每分钟一次 `malloc_trim(0)`（进程涨了 16MB 才做，`BODY_MALLOC_TRIM_S`，0 = 关）。
- **实测**（同一镜像、Linux）：直方图请求峰值 382 → 191MB；抽查上传 425MB / 10 秒 → 191MB / 不到 0.1 秒；之后并发请求 275 → 194MB；排队的抽查在任务容器里峰值 297MB / 15 秒（上限 512M）。
