# Grafana: long-term trend dashboards for the owner

Grafana OSS 13.2.4 runs in local Docker and is **for the owner only** (docs/system-design.md section 2: Grafana cannot do "each person sees only their own data", so family members' charts live in Body Engine's Today page).

| | |
|---|---|
| This machine | http://localhost:8400 |
| Phone / other computers (HTTPS inside the tailnet) | https://your-server.your-tailnet.ts.net:8445 |
| Account | Username `admin`, password in `secrets/grafana_admin_password` (`cat ~/Watch2Whoop/secrets/grafana_admin_password`) |
| Changing the password | Avatar at the top right of the web UI → Profile → Change password. **After changing it, write the new password back to `secrets/grafana_admin_password`** (the doctor's data source check and the backup's dashboard export need it; that file only decides the initial password when the database is first created) |
| Dashboards | Left sidebar "Dashboards" (仪表板) → folder 「身体管理」 ("Body management"): 总览 (Overview), 生理指标 (Physiology), 体重和活动 (Weight and activity), 日记和提醒 (Journal and reminders), 数据质量 (Data quality) |

## Dashboards
Time zone Australia/Sydney, default range the last 90 days (change the time range at the top right); the "用户" (user) selector at the top left switches between Body Engine users (currently only the owner).

| Dashboard | Content | Data |
|---|---|---|
| Overview (总览) | Recovery score, sleep score, strain + muscular load, CTL / ATL / TSB, sleep duration and sleep need, sleep debt | `daily_scores` |
| Physiology (生理指标) | Sleeping resting heart rate, ln HRV, respiratory rate, blood oxygen, wrist temperature, VO₂max, each with a baseline band from the previous 60 days (mean ± 1 SD, same baseline the recovery score uses) | `daily_scores`, `daily_metrics` |
| Weight and activity (体重和活动) | Body weight and its 7-day mean, weigh-in records, daily steps, active energy (each with a 7-day mean) | SparkyFitness API, `daily_metrics` |
| Journal and reminders (日记和提醒) | Which behaviours were logged each day, the next-day recovery score for them, a behaviour → next-day recovery comparison table; reminder counts by rule / level, rule evaluation results, latest reminders | `journal_*`, `daily_scores`, `reminders`, `rule_runs` |
| Data quality (数据质量) | Per day, metric count per source (ow / apple_export / shortcut), sleep segment count, OW raw sample count, hours with heart rate, scoring flags, latest sync / compute time | Body Engine + OW databases |

**Why weight comes from SparkyFitness and not from Body Engine's `body_mass`**: the owner normally logs weight in SparkyFitness (the `calorie_adjust` rule also relies on it first); `daily_metrics.body_mass` comes from HealthKit, its latest row is 2025-08-28, and the last 90 days are empty. Once a smart scale writes into HealthKit, the SparkyFitness iOS app syncs it into SparkyFitness and it will show up here as well. Grafana reads SparkyFitness through its API (Infinity plugin + the owner's read-only API key) and no database role is created, for the same reason as docs/system-design.md section 9 (row-level security).

**Dashboards cannot be saved from the web UI** (provisioning, `allowUiUpdates: false`). To change a dashboard, edit `build_dashboards.py` and run
```bash
python3 deploy/grafana/build_dashboards.py
```
This generates `dashboards/*.json`; Grafana reloads them within 30 seconds; then commit to git. To experiment temporarily, use "Save as" in the web UI to create a new dashboard and change it freely (newly created dashboards are exported into the backup every night).

## First deployment (already done; repeat when moving or reinstalling)
```bash
cd ~/Watch2Whoop
cd body_engine && uv run body-engine migrate && cd ..   # migration 050: create the read-only role grafana_ro in the Body Engine database
python3 deploy/grafana/bootstrap.py                      # generate passwords; create grafana_ro in the OW database; set passwords; verify read-only
deploy/grafana/gf.sh up -d                               # the first start downloads the Infinity plugin from grafana.com (about 30MB)
tailscale serve --bg --https=8445 http://127.0.0.1:8400
deploy/ops/doctor.sh                                     # the Grafana, data source and Tailscale 8445 rows should all be ✓
```
Day to day: `deploy/grafana/gf.sh ps / logs -f grafana / restart / down` (`down` does not delete data; only adding `-v` deletes the volume). After the Mac reboots, `start-all` brings it back up.

### When `docker pull` hangs
The Docker Desktop daemon can hang when pulling images through its built-in proxy (seen since 2026-10-16). The host itself can reach the internet, so a script downloads straight from the image registry and then runs `docker load` (the digest is unchanged, so the `@sha256:` pinned in compose still matches):
```bash
python3 deploy/ops/fetch_image.py grafana/grafana:13.2.4@sha256:dbbc38ea21103cefb50396340cd88afec779c7e070b0a7bf5a75b4ce46651d3f
```

## Security settings
- The port is bound to 127.0.0.1:8400 only; remote access is only through Tailscale (inside the tailnet, not the public internet).
- Disabled: anonymous access, sign-up, creating organizations, public dashboards, external snapshots, Gravatar; telemetry (usage reporting, new-version check, plugin update check, news, feedback links); the several app plugins Grafana preinstalls by default.
- Only one plugin is installed: Infinity 4.1.1 (Grafana Labs, reads SparkyFitness's JSON API).
- All data sources are read-only:
  - **Body Engine** (`host.docker.internal:5433`, database `body`): role `grafana_ro`, created in migration `body_engine/src/body_engine/migrations/050_grafana_ro.sql` (migrations are in git and must not contain passwords, so the migration only creates a NOLOGIN role and the grants; `bootstrap.py` then sets LOGIN and the password). It can read only `daily_metrics`, `sleep_sessions`, `daily_scores`, `journal_items`, `journal_entries`, `reminders`, `rule_runs`, `sync_state`, and for `users` only id / display_name / timezone / ow_user_id; it **cannot read** `api_tokens`, `assistant_audit`, `shares`, supplements, lab results, events, etc.
  - **Open Wearables** (`host.docker.internal:5434`, database `open-wearables`): role `grafana_ro`, created by `ow_grafana_ro.sql` (OW's tables are managed by its own alembic, so migrations cannot be added to vendor). It can read only `data_point_series`, `series_type_definition`, `data_source`, `event_record`, `sleep_details`, `workout_details`, `sync_run`; it cannot read `user` (email), `api_key`, `developer`, `invitation`, `refresh_token`, etc.
  - Both roles: `default_transaction_read_only = on`, `statement_timeout = 30s`, at most 10 connections, no table creation in the public schema. `bootstrap.py` verifies every time: granted tables can be read; sensitive tables cannot; writes are rejected; even with the read-only transaction setting turned off, writes and table creation are still rejected for lack of privileges.
  - **SparkyFitness**: the owner's read-only API key (`secrets/sparkyfitness_api_key_owner_ro`, read-only enforced by the server), allowed to reach only `http://host.docker.internal:8300`.
- Grafana does not join the networks of other compose projects: it can reach only the two database ports above and SparkyFitness on 8300.
- Passwords / keys are mounted into the container as docker secrets and referenced in provisioning with `$__file{}`; they are not in git and not in environment variables.
- When a dashboard later needs a new table: add a new Body Engine migration (`GRANT SELECT ON new_table TO grafana_ro`); for OW, edit `ow_grafana_ro.sql` and re-run `bootstrap.py`. **After an OW upgrade**, if its tables are recreated the grants are lost, the doctor's "Grafana 数据源" (Grafana data sources) row reports ✗, and re-running `bootstrap.py` fixes it.

## Backup
- **Grafana's data volume (`grafana_grafana_data`) is not backed up**: the data sources and the dashboards in the 「身体管理」 folder are provisioning files in this directory (in git), and the admin password is in `secrets/` (backed up nightly). The volume otherwise holds only login sessions, preferences and the Infinity plugin (reinstalled automatically at startup).
- Dashboards you **create** in the web UI: during the nightly backup they are exported through the API to `grafana-dashboards.json` and go into restic together with the database dumps (`export_grafana_dashboards` in `deploy/ops/ops.py`). To restore: Grafana web UI → Dashboards → New → Import, and paste the matching JSON section.
- The two `grafana_ro` roles are in `pg_dumpall --globals-only`, and the restore drill restores them as well.

## Resources
Memory limit 768MB (measured on 2026-10-17: about 380MB after opening several dashboards). The image is 1.8GB.

## Upgrading
Read the release notes and change the version + digest in `compose.yaml` (`docker buildx imagetools inspect grafana/grafana:<version>` or the Docker Hub page); the Infinity plugin version is in `GF_PLUGINS_PREINSTALL_SYNC`. Then `gf.sh up -d` and open all 5 dashboards to check them.
