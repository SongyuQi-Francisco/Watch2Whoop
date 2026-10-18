# Open Wearables local deployment

Open Wearables (OW) 0.9.0 runs in local Docker and holds the raw wearable data of all family members. The source in `vendor/open-wearables` is not modified: it is the upstream compose file plus the `compose.override.yaml` here.

| Service | Address (bound to 127.0.0.1 only) | Notes |
|---|---|---|
| API | http://localhost:8200 (docs at /docs) | Official image `themomentum/open-wearables-backend:0.9.0` |
| Developer portal | http://localhost:8201 | Admin account: see below |
| Flower | http://localhost:8202 | Celery task monitor |
| S3 (SeaweedFS 4.48) | http://localhost:8203 | Used for chunked upload of export.xml. MinIO no longer publishes images on Docker Hub, so SeaweedFS replaces it |
| Postgres 18.4 | 127.0.0.1:5434 | Database / user `open-wearables` |
| Redis, Svix | Not exposed | |

All images are pinned by version + digest. Configuration and passwords are in `secrets/open_wearables.env`, `secrets/open_wearables_s3.json` and `secrets/open_wearables_api_key` (none of them in git). Memory: about 1.5GB idle; the worker peaks at about 2GB while importing a 700MB export.xml.

## First deployment
```bash
python3 deploy/open-wearables/init-secrets.py   # generate random passwords (skipped if they already exist)
deploy/open-wearables/ow.sh up -d               # wait 1 minute, http://localhost:8200/docs should open
deploy/open-wearables/bootstrap.sh              # create the S3 bucket; generate the OW API key for Body Engine
cd body_engine && uv run body-engine ow-link --user-id 1   # create the matching user in OW and record it in users.ow_user_id
```
Day to day: `deploy/open-wearables/ow.sh ps / logs -f app / down` (`down` does not delete data; only adding `-v` deletes the volumes).

## Importing a full Apple Health export
OW's direct-upload endpoint reads the whole file into memory and then puts it on the task queue, which does not work for 700MB, so the upload goes through S3 multipart upload:
```bash
python3 deploy/open-wearables/upload_xml.py --ow-user-id <OW user UUID> health_data/full_export/apple_health_export/export.xml
cd body_engine && uv run body-engine import-ow --user-id 1 --full   # make Body Engine recompute with OW as the source of truth
```
OW de-duplicates on (device, type, start time), so uploading the same file twice does not create duplicate data.

Result of the 2026-10-16 import (export taken on the morning of 2026-10-14): 1.55 million time-series records, 102 workouts, 10190 sleep records (796 merged segments), taking about 1.5 minutes. Compared with `health_data/processed/record_counts.csv`:
- Supported types match closely; the few to few dozen missing records (headphone audio level −88, steps −25, heart rate −16, ...) are duplicate samples from the same device with the same start time that OW merged.
- Not supported by OW and not imported: stand hours (AppleStandHour), audio events, toothbrushing, mindfulness, sleep goal.
- **Known OW issues**:
  - XML import does not convert units: the data is labelled meters / m_per_s / percent / kcal, but what is stored is actually the km, km/hr, 0–1 ratios and **kJ** from the export file. Body Engine's OW importer treats XML-sourced data in the export's units (for energy, use `import-ow --xml-energy-unit`, default kJ).
  - XML import does not store the time zone (`zone_offset` is empty); the source is kept only down to device type (iPhone / Watch / no device), not the watch name; sleep keeps no source.
  - Only the sample start time is stored, not the end time (this affects de-duplication of cumulative metrics, see `importers/ow.py` in Body Engine).

## Connecting the OW app on iPhone to this Mac (you do this yourself)
OW is bound to 127.0.0.1 only, so the iPhone reaches it through Tailscale:
1. Install Tailscale on the iPhone, sign in with the same account as the Mac, and check both devices are online.
2. Run this in a Mac terminal (it exposes OW's API to your own devices over Tailscale HTTPS, not to the public internet):
   ```bash
   tailscale serve --bg --https=8443 http://127.0.0.1:8200
   ```
   The terminal prints an address like `https://your-mac.<your tailnet>.ts.net:8443`; that is the **API URL**. The first time, it will tell you to enable HTTPS certificates in the Tailscale admin console; click through as prompted.
3. Open http://localhost:8201 in a browser on the Mac and log in to the portal: email `owner@example.com`, password in `ADMIN_PASSWORD` in `secrets/open_wearables.env` (run `grep ADMIN_PASSWORD secrets/open_wearables.env` in a terminal to see it). After logging in, change the password once in the portal and save the new one in a password manager.
4. Portal → Users → Alex → **Connect Mobile App** gives you a one-time **Invitation Code** (the API URL the portal shows is localhost; do not use it, use the address from step 2).
5. Install the OW app on the iPhone from the public TestFlight link (see section 11 of `docs/system-design.md`), open it, enter the API URL and Invitation Code → Connect → allow reading health data (turn everything on).
6. Back on the Mac: `cd body_engine && uv run body-engine import-ow --user-id 1`. After that, new dates in `daily_metrics` should have source `ow`.

When a family member joins: `uv run body-engine user-add ...` → `uv run body-engine ow-link --user-id <new id>` → generate an invitation code for that user in the portal.

## Upgrading
OW is pre-1.0: read the release notes first, then change the submodule and the image versions and digests in `compose.override.yaml` together. `TELEMETRY_ENABLED=false` is already in the configuration (the 0.9.0 backend has no anonymous telemetry yet; it is a placeholder); Sentry is disabled.
