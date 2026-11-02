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

## 中文摘要

- **现象**：生产服务器上 API 容器（当时上限 256M）7 天被内核 OOM 杀 20 次，16 次在每小时 :10（hourly）和 :40（doctor）；数据库容器被杀 11 次。每次都让正在进行的请求变 502。
- **根因（实测）**：hourly 的 `provision-run` 和 doctor 的 `ai-fallback status` / `muscle-coverage-check` 都是 `docker exec` 进 API 容器跑的，每个是又一个约 75MB 的 Python 进程，算在 API 的内存上限里；内核杀的是组里最大的进程——uvicorn。22:40 实测到两个 doctor（服务器自己的 + Mac 通过 ssh 跑的）同时在 API 容器里各起一个进程。数据库被杀同理（`pg_dump` 用 docker exec 跑在数据库容器里，加上默认 128MB shared_buffers、100 个连接）。API 本身从 213M 涨到 310M 是动作库缓存（约 33MB + 每种视图约 5MB）加 glibc 的多线程 malloc arena。
- **修复**：任务和检查一律用一次性容器（自带内存上限），不再 docker exec 进 API；Mac 读服务器自己的 doctor 结果，不再同一分钟再跑一遍；pypinyin 改成第一次语音纠错时才加载（API 导入从 166MB 降到 110MB）；镜像设 `MALLOC_ARENA_MAX=2`；`BODY_SYNC_IMPORT=queue` 让"立即同步"只排队、由每分钟的任务导入；`norm()` 缓存上限 20 万 → 4 万。目标常驻 < 250MB、384M 上限留 ≥ 30%。
- **数据库**：给出 320M 上限下的参数（shared_buffers 64MB、max_connections 40、work_mem 4MB、autovacuum 2 个 worker 各 16MB），最坏约 220MB；`pg_dump` 放到一次性容器里跑。
- **顺带**：`daily_scores` 写入加按用户的事务级 advisory 锁 + upsert（修并发回算的 UniqueViolation）；开户 provision 对临时错误（连不上、超时、5xx）在一次运行内重试 3 次，失败写 `last_error`（带异常类型）并按 10 分钟 × 2^(次数−1)、最多 6 小时退避（迁移 785 `next_attempt_at`），一个用户失败不影响其他人；响应头 `X-Body-User` 供反向代理记日志（并对客户端隐藏）；`BODY_LOG_DIR` 让 API 的应用日志按天写文件、保留 14 天，不随重建容器丢失。
