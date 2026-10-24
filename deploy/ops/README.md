# Local ops: scheduled jobs, backup and restore, health check, autostart

Keeps the body management system running unattended on this Mac. Everything lives in `ops.py` (standard library only; the system `/usr/bin/python3` is enough, independent of the Body Engine virtualenv: if Body Engine is broken, doctor and backup still work). Databases are reached through `docker exec`, so no local Postgres is needed.

## Day to day
```bash
deploy/ops/doctor.sh              # all services at a glance: ✓ ok / ⚠ warning / ✗ problem
deploy/ops/ops.sh hourly          # run the hourly job once by hand
deploy/ops/ops.sh backup          # run a backup by hand
deploy/ops/ops.sh restore-drill   # restore drill (once per quarter is a good idea)
deploy/ops/ops.sh start-all       # bring all services up (also runs automatically at login after a Mac reboot)
```
- Logs: `health_data/ops/<job>-YYYY-MM-DD.log`, one file per day, kept 30 days; `launchd-<job>.log` only holds crash output (truncated above 5MB).
- State: `health_data/ops/state.json` (time and result of the latest run of each job); latest doctor report: `health_data/ops/doctor-latest.txt`.
- A macOS notification is sent when a job fails or doctor finds a ✗; the same problem is reminded at most every 6 hours, and a "back to normal" notification is sent after recovery.

## Scheduled jobs (macOS LaunchAgents, no crontab)
`deploy/ops/install.sh` installs them (running it again = reinstall), `--uninstall` removes them.

| LaunchAgent | When | What it does |
|---|---|---|
| `org.watch2whoop.body.hourly` | minute 10 of every hour | `hk-derive-run` (the app sync engine's queued days, flag `apple_sync_engine`; only if the command exists, nothing queued = nothing done); then **one** `body-engine hourly-batch` process for all users (`BODY_BATCH_WORKERS`, default 2): `import-ow` for each user linked to OW, `compute-scores --start 3 days ago` for each user with data (writes only the last few days), `evaluate-rules`, `refresh-status`; users are isolated (one failing does not stop the others), steps whose inputs did not change are skipped but recomputed at least every 2 h, the log keeps the old one-line-per-step format (see [`docs/hourly-batch.md`](../../docs/hourly-batch.md)) |
| `org.watch2whoop.body.nightly` | daily 03:30 | `import-ow` + full `compute-scores`, `muscle-coverage-check` (every workout of the last 28 days must map to muscles; a missing type fails the job and the doctor row 「肌肉图覆盖」), then the backup |
| `org.watch2whoop.body.doctor` | minute 40 of every hour | Health check, notifies on ✗ (includes the Shortcut receiver on 8765 / 8767, Tailscale 8446, and the doctor row 「快捷指令各类数据」 ("Shortcut data by type"): any of heart rate / steps / energy / sleep / HRV / resting heart rate / respiration / blood oxygen with no new sample for over 26 hours -> ⚠) |
| `org.watch2whoop.body.startup` | at login (also once at install) | `start-all`: opens Docker Desktop if it is not open and waits until ready, runs `up -d` on the four compose projects (Body Engine, OW, SparkyFitness, Grafana), loads the Shortcut receiver if it is not loaded, finally runs doctor once |
| `org.watch2whoop.body.watchdog` | every 3 minutes | Watchdog: if the Docker daemon is unreachable, `open -a Docker` and wait until it is ready; if a service in the four compose projects is not running (still missing after 1-2 minutes), `compose up` the way start-all does; sends an "recovered automatically" notification after a fix; notifies after 3 consecutive failures to fix, then reminds hourly. Pause it first for maintenance: `ops.sh watchdog --pause 30` (`--pause 0` resumes) |

- Users are read from Body Engine's `users` table, no hard-coded user_id; users not linked to OW skip `import-ow`, users without any data skip `compute-scores`.
- Times follow the local time zone (Australia/Sydney). Runs missed while the Mac was asleep are made up once by launchd after it wakes.
- hourly / nightly / backup are mutually exclusive through a file lock: while the nightly job is running, the hourly job simply skips.
- `start-all` uses `up -d --no-build --pull never --wait`: no building, no pulling images (pulling in Docker Desktop can hang), containers that are already running are left alone.

## Backup
Every night (or `ops.sh backup`):
1. For each of the three databases, **in one snapshot**: count the rows of every table, `pg_dump -Fc -Z0` (uncompressed, so restic dedups and compresses better), `pg_dumpall --globals-only` (roles, which SparkyFitness's `sparky_app` needs), then `pg_restore -l` to confirm the dump is readable.
   | Database | Container | 2026-10-16 |
   |---|---|---|
   | Body Engine `body` | body-db | 17 tables, 19 thousand rows, dump 2MB |
   | Open Wearables `open-wearables` | postgres__open-wearables | 25 tables, 1.55 million rows, dump 224MB |
   | SparkyFitness `sparkyfitness_db` | sparkyfitness-db | 113 tables, 837 rows, dump 0.8MB |
2. Grafana: only the dashboards you **created** in the web UI are exported through the API (`grafana-dashboards.json`). The Grafana data volume is not backed up: the datasources and the 「身体管理」 ("Body management") dashboards are provisioning files in `deploy/grafana/` (in git), the admin password is in `secrets/`, and the rest of the volume is just login sessions and preferences (see `deploy/grafana/README.md`). If Grafana is not running, only one ⚠ line is logged and the backup is unaffected.
3. `restic backup`: the dumps above + row-count manifests, `secrets/` (the whole directory), `health_data/archive/` (raw Shortcut uploads, small, cannot be recreated if lost).
4. `restic forget --prune`: **7 daily + 4 weekly + 6 monthly** (grouped by host `body-management` + tag `nightly`); `restic check`, plus a 10% data spot check on Sundays.
5. Delete the plaintext dumps from disk.

- Repository: `~/Backups/body-management-restic` (changeable via `BODY_RESTIC_REPO`), encrypted. After compression + dedup the first backup is about 45MB, then a few hundred KB to a few MB per day.
- Password: `secrets/restic_password` (generated automatically on the first backup). **Copy it into a password manager**: `secrets/` itself is part of the backup, so if the Mac is lost / broken, the backup cannot be opened without this password.
- Not backed up: the Grafana data volume (reason above), OW's SeaweedFS (only a temporary S3 for uploading export.xml), Redis (queue), SparkyFitness's uploads volume (currently 12KB), `health_data/full_export` (the raw export.xml, 1GB, its content is already in the OW database; copy it to an external drive yourself if you need it).

### Restore drill (`ops.sh restore-drill`)
Extract the latest snapshot from restic -> start a **throwaway container** for each database (same image as production, `--network none`, no published ports, `--rm`) -> import roles, `pg_restore` -> count rows table by table and compare with the manifest taken at backup time; compare secrets file by file by sha256 -> delete the throwaway containers and the extracted plaintext. Production databases are never touched.
Drill of 2026-10-16 (on the nightly backup produced by launchd): Body Engine 17/17 tables 19,107 rows, OW 25/25 tables 1,557,544 rows, SparkyFitness 113/113 tables 837 rows all identical, pg_restore 0 errors; secrets 11/11 files identical.

### Real restore (Mac broken / database broken)
1. New Mac: install Docker Desktop and Homebrew, `brew install restic`, clone the repo. Copy the backup repository back (external drive or offsite) and write the restic password from your password manager into `secrets/restic_password`.
2. Extract the files: `deploy/ops/ops.sh restore-extract --target ~/restore` (`--snapshot <ID>` picks a specific one; `restic snapshots` lists all).
   Once you have the secrets, copy `~/restore/<original path>/secrets/*` back into the repo's `secrets/`.
3. Start empty databases: `docker compose up -d body-db`, `deploy/open-wearables/ow.sh up -d db`, `deploy/sparkyfitness/sf.sh up -d sparkyfitness-db`.
4. Import (`S=~/restore/<original path>/health_data/ops/backup-staging`):
   ```bash
   C=$(docker ps -qf label=com.docker.compose.service=body-db)
   docker cp $S/body.dump $C:/tmp/ && docker exec $C pg_restore -U body -d body --clean --if-exists /tmp/body.dump
   # OW: container postgres__open-wearables, user / database open-wearables
   # SparkyFitness: first docker exec -i <container> psql -U sparky -d postgres < $S/sparkyfitness.globals.sql (roles), then pg_restore -U sparky -d sparkyfitness_db
   ```
   Each database's `*.manifest.json` holds the per-table row counts at backup time; compare after the import.
5. Other services: `python3 deploy/grafana/bootstrap.py` (the grafana_ro role and password are already in the restored globals; this step re-checks the read-only permissions), `deploy/ops/ops.sh start-all`, `tailscale serve --bg --https=8445 http://127.0.0.1:8400` (same for 8443 / 8444, see their READMEs), `deploy/ops/install.sh`, `health_sync/install.sh`, `deploy/ops/doctor.sh`.
   If only one database is broken and the Mac is fine: `restore-extract` first, try the import into a **new database name** (`createdb` a `*_restore`) to confirm it works, then stop the service concerned and swap the database. Confirm before overwriting a production database.

## Adding an offsite backup (your call, later)
Right now the backup repository is on the same disk as the system (doctor shows ⚠), so if the Mac is broken / lost the backup is gone too. restic can copy the same snapshots to a second repository (`restic copy`); using the same password for the second repository is easiest:
- **External drive** (simplest, free):
  ```bash
  export RESTIC_PASSWORD_FILE=~/Watch2Whoop/secrets/restic_password RESTIC_FROM_PASSWORD_FILE=~/Watch2Whoop/secrets/restic_password
  restic init --repo /Volumes/<drive name>/body-management-restic --from-repo ~/Backups/body-management-restic --copy-chunker-params
  restic copy --repo /Volumes/<drive name>/body-management-restic --from-repo ~/Backups/body-management-restic
  ```
  After that, plug the drive in once a week and run `restic copy` (then `restic forget ... --prune` again, same parameters as above).
- **Backblaze B2 / Cloudflare R2** (automatic, a few cents a month): sign up yourself, create a bucket, generate a key that can **only access this bucket**, and store it in `secrets/restic_offsite.env` (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`; R2 also needs the endpoint). The repository address looks like `s3:https://<account>.r2.cloudflarestorage.com/<bucket>`. Then add a `restic copy` step in `do_backup` of `ops.py` after `check` (not implemented yet; ask Claude to add it when you need it). The data is encrypted by restic locally before upload, so the cloud provider cannot see the content.

## Watchdog and Docker outage records
On 2026-10-17 18:56 Docker Desktop quit completely once (the main process exited, `com.docker.backend` and `com.docker.virtualization` crashed together with `NSInternalInconsistencyException`) and all services went offline. Doctor runs only hourly and only notifies without repairing, so the watchdog was added:
- **Every 3 minutes** (`org.watch2whoop.body.watchdog`, `ops.sh watchdog`; originally 5 minutes, but the measured worst case = check interval + about 1 minute of recovery, so it became 3 minutes to guarantee recovery within 5 minutes): it only checks the Docker daemon and the containers of each compose project, finishes in 1-2 seconds when all is well and writes no log.
- **Docker stuck while quitting** (measured twice on 2026-10-17: right after `quit` the VM stopped, but the UI and the engine `com.docker.backend` took almost 10 minutes to exit; during that time Docker.app still counts as "running" and `open -a Docker` has no effect - the "down for over twenty minutes" the maintainer saw was most likely this): when the daemon is unreachable, the VM has stopped and the engine is still the same instance as at the last healthy check (same start time, so a Docker that is just starting up is never killed by mistake), it waits 30 seconds first; if it is still there and the daemon has been unreachable for over 2 minutes, it ends the old instance's engine and UI processes (TERM first, KILL after 20 seconds), then runs `open -a Docker`. The VM has already stopped, so container data is unaffected.
- **Docker not running**: `open -a Docker`, wait up to 4 minutes; once it is up, wait for containers to come back by themselves under `unless-stopped` (up to 2 minutes), then run `compose up -d --no-build --pull never --wait` on the projects that have not.
- **Docker restarted by itself between two checks** (the start time of the main `com.docker.backend` process changed): also recorded as an outage.
- **Every outage is recorded** (`watchdog.outages` in `state.json`, the latest 20): the time span (from the last healthy check to recovery), how it recovered, and the reason. The reason is looked up only in places that trigger no privacy prompt: the macOS system log (`/usr/bin/log show`, for exits and crashes of Docker processes), crash reports in `~/Library/Logs/DiagnosticReports`, the Docker.app version (changed = self-update), the boot time (= Mac reboot). Docker Desktop's own logs are in `~/Library/Containers/com.docker.docker/Data/log/`; reading them pops up "access data from other apps", so they are not read.
- Doctor gains two rows: 「看门狗」 ("Watchdog"; no run for over 10 minutes / consecutive repair failures = ✗) and 「Docker 非正常退出」 ("Docker abnormal exit"; time span and reason of the latest one; within the last 24 hours it shows ⚠).
- The watchdog and start-all share one lock, so two `compose up` never run at once. **When you stop services yourself for maintenance (rebuilding images, migrating a database, quitting Docker), first run `deploy/ops/ops.sh watchdog --pause 30`**, otherwise they are brought back within minutes.

## After a Mac reboot / power loss
- All containers use the restart policy `unless-stopped` (set in the Body Engine and SparkyFitness compose files; OW's is overridden in `deploy/open-wearables/compose.override.yaml`, vendor is not modified): after Docker Desktop starts the containers come up by themselves; ones stopped manually with `docker stop` do not, and `start-all` brings them up.
- LaunchAgents only run after **login**: after a Mac reboot you have to log in once (with FileVault on, automatic login is impossible). After login `org.watch2whoop.body.startup` opens Docker Desktop and brings up all services.
- It is also recommended to turn on Docker Desktop's own start-at-login (steps below), as a double safeguard.
- Verified 2026-10-16: after a container's main process exited Docker restarted it automatically (RestartCount 0->1); after manually stopping body-engine, the OW portal and the SparkyFitness frontend, `start-all` brought them all back and doctor was all green; when body-engine was stopped, doctor (run by launchd) sent a notification, and after it was brought back a "back to normal" notification followed.

### Settings you confirm yourself (Claude does not change system settings)
1. **Docker Desktop starts at login**: click the Docker whale icon in the menu bar -> Settings (gear) -> General -> tick **Start Docker Desktop when you sign in to your computer** -> Apply at the bottom right.
2. **No sleep while plugged in**: already on (the doctor row 「Mac 睡眠」, "Mac sleep"). If it turns ✗ later: System Settings -> Battery -> Options... -> turn on "Prevent automatic sleeping on power adapter when the display is off". Closing the lid still sleeps.
3. **Notifications**: the first time a notification arrives, if macOS asks whether to allow "Script Editor" / osascript to send notifications, click allow; you can see it in System Settings -> Notifications.
4. **Store the restic password** in a password manager: `cat ~/Watch2Whoop/secrets/restic_password`.

## Paths and environment variables
| Variable | Default | Notes |
|---|---|---|
| `BODY_ROOT` | this repo | Deployment root: where `secrets/`, `health_data/`, `compose.yaml` and `vendor/` live |
| `BODY_SECRETS_DIR` | `$BODY_ROOT/secrets` | |
| `BODY_HEALTH_DATA_DIR` | `$BODY_ROOT/health_data` | Logs and state are under `ops/` |
| `BODY_ENGINE_DIR` | `$BODY_ROOT/body_engine` | `uv run --frozen body-engine` is run from here |
| `BODY_RESTIC_REPO` | `~/Backups/body-management-restic` | |

When trying things from a git worktree: `BODY_ROOT=<main repo> deploy/ops/ops.sh doctor` (same for `install.sh`, which writes BODY_ROOT into the plist).

## When Docker Desktop hangs pulling images
The daemon may hang when pulling images through its built-in proxy (direct internet access from the machine works fine). `fetch_image.py` uses only the standard library: it downloads the image layers for this machine's platform straight from Docker Hub / ghcr.io, assembles an OCI layout and runs `docker load`; the image index digest stays unchanged, so the `image:version@sha256:...` pinned in compose still works:
```bash
python3 deploy/ops/fetch_image.py grafana/grafana:13.2.4@sha256:dbbc38ea21103cefb50396340cd88afec779c7e070b0a7bf5a75b4ce46651d3f
```
Every file is checked against its sha256. Only public images are supported.
