# SparkyFitness local deployment (optional, no longer needed)

> Since issue #252 food, food library, weight and workout logs live in Body Engine's own tables. A **new deployment does
> not need SparkyFitness**: turn on `own_food_only` for everyone (`body-engine flags set own_food_only --state all`);
> sign-up then no longer creates a SparkyFitness account. Keep this service only while an older deployment still has
> data in it: migrate with `body-engine sparky-migrate` and switch with the flags as described in
> [`docs/own-food-weight-storage.md`](../../docs/own-food-weight-storage.md) §13, then keep it read-only for 4 weeks
> and remove it.

[SparkyFitness](https://github.com/CodeWithCJ/SparkyFitness) v1.8.0 (stable release of 2026-10-08) records workouts, meals and weight, and runs in local Docker. It is not forked and its source is not modified: only the official images + the `compose.yaml` here.
The license is the author's own **non-commercial license**: personal use and free use by family members are fine, commercial use is not (see section 3 of `docs/system-design.md`).

| Service | Address | Notes |
|---|---|---|
| Web / API | http://localhost:8300 (bound to 127.0.0.1 only) | Front-end nginx, forwards `/api` to the backend. Image `codewithcj/sparkyfitness:v1.8.0` |
| HTTPS inside the tailnet | https://your-server.your-tailnet.ts.net:8444 | `tailscale serve`; reachable only from devices in your Tailscale network, not the public internet |
| Backend | Not exposed | `codewithcj/sparkyfitness_server:v1.8.0` |
| Postgres 18.6 | Not exposed | Database `sparkyfitness_db`, superuser `sparky`, application user `sparky_app` (row-level security) |

- All images are pinned by version + digest; there are no automatic updates (watchtower is not run). SparkyFitness has no anonymous telemetry; the web UI checks GitHub for new versions (it only shows a notice, it never upgrades by itself).
- Registration is off by default; it is turned on temporarily only while `bootstrap.py` creates accounts.
- Memory limits: server 768MB, db 512MB, front end 128MB; about 320MB measured when idle.
- Data lives in the docker volumes `sparkyfitness_sparkyfitness_db`, `_uploads`, `_backup`.
- Configuration and passwords are all in `secrets/` (not in git):

| File | Content |
|---|---|
| `secrets/sparkyfitness.env` | Database password, encryption key, `BETTER_AUTH_SECRET`, public address, admin email. **If `BETTER_AUTH_SECRET` is lost or changed, everyone is logged out and two-factor authentication stops working; changing the database password here has no effect (it only applies at first initialisation)** |
| `secrets/sparkyfitness_owner_password` | Login password of the owner's account (owner@example.com, administrator) |
| `secrets/sparkyfitness_api_key_owner_rw` | Read-write API key, for the migration script |
| `secrets/sparkyfitness_api_key_owner_ro` | Read-only API key (the server enforces read-only), for Body Engine |

## First deployment
```bash
python3 deploy/sparkyfitness/init-secrets.py          # generate passwords (skipped if they exist); the public address is taken from the Tailscale name + 8444
deploy/sparkyfitness/sf.sh up -d                     # wait half a minute, http://localhost:8300/api/health returns {"status":"UP"}
tailscale serve --bg --https=8444 http://127.0.0.1:8300
python3 deploy/sparkyfitness/bootstrap.py             # create the owner's account + two API keys (skipped if they exist)
python3 deploy/sparkyfitness/migrate_body_md.py       # migrate the data from body-management.md (can be re-run)
cd body_engine && uv run body-engine migrate && uv run body-engine sparky-link --user-id 1 --key-name owner
```
Day to day: `deploy/sparkyfitness/sf.sh ps / logs -f sparkyfitness-server / down` (`down` does not delete data; only adding `-v` deletes the volumes).
When running these scripts inside a git worktree, add `BODY_SECRETS_DIR=<main repo>/secrets`.
The `tailscale serve` configuration persists (also across reboots); check it with `tailscale serve status`, and turn it off with `tailscale serve --https=8444 off`. 8443 is Open Wearables, leave it alone.

## Using it on the iPhone (you do this yourself)
Prerequisite: the iPhone has Tailscale installed, is signed in to the same account as the Mac, the switch at the top right shows "connected", and the Mac is awake.
1. **Get the password**: run the line below in a Mac terminal, save the password it shows in your password manager (username `owner@example.com`), then you can close the terminal window:
   ```bash
   cat ~/Watch2Whoop/secrets/sparkyfitness_owner_password
   ```
2. **Install the app**: open the App Store on the iPhone, search for **SparkyFitness** (developer CodeWithCJ, link <https://apps.apple.com/us/app/sparkyfitness/id6757314392>), and install it.
3. **Enter the server address**: open the app; on the first page "前端 URL / Frontend URL" enter
   `https://your-server.your-tailnet.ts.net:8444`
   (do not use localhost, and do not leave out `:8444`) → tap "下一步 / Next". If it cannot connect, first check that Tailscale shows connected, then try opening the same address in Safari.
4. **Log in**: choose email login, enter `owner@example.com` as the email and the password from step 1 → "登录" (Log in). (Do not choose API-key login: the keys in secrets are for Body Engine and the migration script.)
5. **Apple Health permission**: the app asks whether to sync Apple Health. **I recommend leaving it off for now** (or enabling only "Weight"): the watch's heart rate, sleep, steps etc. are collected by Open Wearables (`docs/system-design.md` section 5, "each type of data has exactly one source"), and syncing them into SparkyFitness as well would give you two sets of numbers. If you later want steps inside SparkyFitness, turn it on in the app's Settings → Sync.
6. **Check the data**: Workouts → History should show the 10 workouts from 9-13 to 10-15; Workouts → Plans has "训练计划 V3.0" (Training plan V3.0); the diet targets change with the day type, 2400 kcal on training days / 2200 kcal on rest days; Weight has 4 entries.
7. **Language**: the migration script already set the account to Simplified Chinese, kg, week starting Monday, 24-hour clock; if the app still shows English, change it in Settings → Language.
8. To change the password: use the web version (open http://localhost:8300 or the 8444 address above in a Mac browser) → Settings → Account → change password, then save the new password in the password manager and update `secrets/sparkyfitness_owner_password` (the migration script does not use the password; only `bootstrap.py` does).

When logging a workout: start the workout from a template such as "上肢 A" (Upper A), tick off each set, fill in weight and reps, **and fill in RIR too** (double progression needs it, see body-management.md 4.5). On the Apple Watch, still start "Traditional Strength Training" as usual.

## Migrating body-management.md (`migrate_body_md.py`)
- The data is in `body_md_data.py`, curated by hand from body-management.md (copied only, the original file is not changed); the top of that file documents the conventions for weight, rep range and RIR.
- It goes through the SparkyFitness API (read-write key). For each item it finds the existing record by a stable identifier: exercises `source_id = bm:<key>`, workout logs by date + the description "迁移自 body-management.md" ("migrated from body-management.md"), foods `provider_external_id = bm:...`, everything else by name or date. If missing it creates, if present and different it updates, if identical it skips; it **deletes nothing**. So it can be re-run safely.
- Note: once you have switched to logging in SparkyFitness, if you rename a template / recipe in the app, re-running the script creates another one under the old name; if you edit the content of an exercise or workout log, re-running changes it back to the body-management.md version. Do not re-run it after the migration unless body-management.md has new historical data to add.
- `--dry-run` is read-only; `--meta-out x.json` writes out the exercises' SparkyFitness IDs, banned / caution status, unilateral flag and key points (for Body Engine's `exercise_meta`).

Result of the 2026-10-16 migration (the check table printed at the end of the script; the "body-management.md" column is counted by the script directly from the original text, independently of the curated data):

| Item | body-management.md | Curated | SparkyFitness |
|---|---|---|---|
| Workout logs (sessions) | 10 (`### date` headings) | 10 | 10 |
| Workout exercise entries | 32 lines, 5 of which are "A + B" supersets → 37 | 37 | 37 |
| Sets | 145 (summed from "weight × reps × sets"; cardio counts as 1 set) | 145 | 145 |
| Sets with RIR | 0 (the log did not record RIR) | 0 | 0 |
| V3.0 plan exercise rows (4 training days) | 28 (section 4.4 table rows) | 28 | 28 |
| Workout templates | 4 training days + a shoulder routine | 5 | 5 |
| Weekly training plan | 1 (4.1) | 1 | 1 |
| Exercise library | plan + log + routine + cardio + banned, de-duplicated | 46 | 46 |
| Nutrition goal templates | 2 (5.2 V2.1 training day / rest day) | 2 | 2 |
| Recipes | 21 (5.3.2 table rows) + 4 combos (5.3.1b, 5.2.2) | 25 | 25 |
| Food log entries | 6 rows (5.5), only the 1 row actually eaten imported | 1 | 1 |
| Weight | 4 (section 8 table rows) | 4 | 4 |

Curation decisions:
- **Not imported**: meals that were only planned (not confirmed as eaten), daily total rows, and symptoms (these belong in the health records' events). Supplements and lab results are not here (the health-records session handles them).
- **Weights**: dumbbells are recorded per dumbbell; cable machine "18kg×2" is recorded as 18 (per side); barbell "12.5kg per side" is recorded as 12.5 (plates per side, excluding the bar); all are noted in the set notes.
- **Exercise merging**: "正手宽距高位下拉" → lat pulldown (高位下拉); "坐姿窄距划船 / 坐姿划船" → seated row, narrow grip (坐姿划船（窄握）); "反手窄距下拉" → reverse narrow-grip lat pulldown (反手窄距高位下拉); "龙门架中胸" → cable fly, mid position (龙门架夹胸（中位）); "坐姿抬腿" → seated leg extension (坐姿腿屈伸). The original wording is kept in the exercise notes.
- **Unclear equipment**: a logged press that does not say whether it is a single dumbbell, two dumbbells combined, or a machine is recorded separately as "（器械未注明）" (equipment not stated) instead of being merged into the planned dumbbell exercise. After you confirm, you can change it in the app.
- **Weight dates**: a weigh-in noted for a date range ("around the 21st to 25th") is recorded on the middle day.
- **Plan**: the rep range is recorded as the lower bound + rep_goal as the upper bound (double progression); the starting-weight range is recorded as the lower bound with the original range in the set notes; increments are 2.5kg for upper body and 2.5kg for lower body (leg press and hip thrust 5kg). Nutrition goal ranges use the midpoint with the original range kept in the name; training days and rest days switch by weekday.
- **Banned exercises**: SparkyFitness has no "banned" field, so "（禁用）" (banned) is appended to the exercise name and 【禁用】 (banned) / 【注意】 (caution) is put at the start of the description; the structured status goes in Body Engine's `exercise_meta`.

## Family members joining
```bash
python3 deploy/sparkyfitness/bootstrap.py --who mum --email her-email --name Name   # account + two keys
cd body_engine && uv run body-engine sparky-link --user-id <her Body Engine id> --key-name mum
```
Then give her the Tailscale invitation, the 8444 address and `secrets/sparkyfitness_mum_password`, and have her follow "Using it on the iPhone" above. To see each other's data, use SparkyFitness's own family sharing (beta), or Body Engine's `shares` (scopes: training / nutrition / weight).

## Backup and upgrading
- Backup: `deploy/sparkyfitness/sf.sh exec -T sparkyfitness-db pg_dump -U sparky -Fc sparkyfitness_db > backup-file.dump` (for the overall backup scheme see section 9 of the system design). The SparkyFitness web UI's Admin → Backup can also export.
- Upgrading: read the release notes first (single-maintainer project, frequent incompatible changes; the interfaces Body Engine uses are in `body_engine/src/body_engine/sparky_client.py`), change the version and digest in `compose.yaml` together, then `sf.sh pull && sf.sh up -d`. Back up before upgrading.
