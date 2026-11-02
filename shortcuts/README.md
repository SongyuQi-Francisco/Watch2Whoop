# iPhone Shortcut: Watch2Whoop Health Sync

> **Retired with 2.0.0** (`docs/ios-native-2.0.md` §0 D2, §4 R1). The Shortcut is no longer maintained; everyone syncs with the Watch2Whoop app's own HealthKit sync. Once a deployment sets `BODY_LEGACY_SOURCES_CUTOFF`, the receiver answers `POST /upload` with **410 Gone** (a Chinese-first message pointing to the app) and stores nothing; data already imported stays. Before the cutoff, `body-engine retire-legacy verify --all [--archive]` confirms every uploaded file was imported. The files below are kept for reference.

> An iOS Shortcut that reads Apple Health samples (Apple Watch heart rate, HRV, sleep stages, energy, …) and uploads them to a self-hosted receiver (`health_sync/health_server.py`). Import `Watch2Whoop Health Sync.shortcut`, answer the two import questions (upload URL, upload token), then add the personal automations below.

The shortcut joins Apple Health samples (watch heart rate, HRV, sleep stages, energy, steps, ...) into column-based text and POSTs it to the receiver `health_sync/health_server.py` on your own Mac; Body Engine imports it and computes the sleep / recovery / strain scores.
**The data format, the type list and how the server parses it are defined only by [`docs/shortcut-sync-spec.md`](../docs/shortcut-sync-spec.md)**; when you change the shortcut, change the spec and the importer at the same time.

| File | Description |
|---|---|
| `Watch2Whoop Health Sync.shortcut` | The exported shortcut (signed by Apple's servers, importable by "Anyone"). The upload URL and token are **import questions**; the file contains no real values |
| `check_shortcut.py` | Checks the exported file for private information before committing (unpacks the signed file, searches for denied words / tokens / addresses). **Run it every time you overwrite the file** |

## Versions
| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-10-17 | First open-source version: 14 "Find Health Samples" types; heart rate and exercise minutes ungrouped (raw samples); column output `#format=cols-v1` (no "Repeat", finishes in a few seconds); HTTPS + `X-Upload-Token` header; upload URL and token are import questions |
| 1.1 | 2026-10-18 | Steps, Active Calories and Resting Calories are no longer grouped by hour: they upload raw samples with their source (iPhone / Watch), so the server takes the largest single device per day instead of adding both devices (hour-grouped steps were about 2x). All 14 types are now ungrouped. About 1,400 more samples per run (~+140 KB). Re-signed on the Mac with `shortcuts sign --mode anyone` |

## Required Health permissions
The first time it runs, iOS asks type by type for "Allow Shortcuts to read health data". Tap **Allow** on all of them (you can change this later in the Health app → profile picture → Apps → Shortcuts):
Sleep, steps, active energy, resting energy, walking average heart rate, body weight, sleeping wrist temperature, HRV, heart rate, resting heart rate, blood oxygen, respiratory rate, VO₂max, exercise time.
The first upload also asks "Allow sending N health samples to <your host>"; choose **Always Allow** (otherwise every automation gets stuck on this dialog).

## Import
1. First set up the receiver on the Mac: `health_sync/install.sh` (listens on LAN port 8765 and local port 8767), then expose HTTPS through Tailscale (reachable only inside your tailnet; do not enable Funnel):
   ```bash
   tailscale serve --bg --https=8446 http://127.0.0.1:8767
   ```
   The upload URL is `https://<your Mac's MagicDNS name>.<tailnet>.ts.net:8446/upload?name=health` (`Self.DNSName` in `tailscale status --json`). The iPhone must also have Tailscale installed and be signed in to the same tailnet.
2. Issue an upload token for this iPhone (tokens identify the user; the plaintext is written only to `secrets/health_upload_token_<name>`, and the server stores only the sha256):
   ```bash
   python3 health_sync/health_server.py token-create --user-id 1 --name iphone-me
   ```
3. Transfer `Watch2Whoop Health Sync.shortcut` to the iPhone (AirDrop, iCloud Drive or the Files app), open it → "Add Shortcut" → answer the two import questions:
   - **Upload URL**: the upload address from step 1.
   - **Upload token**: keep the header key `X-Upload-Token` and replace the value with the token from step 2.
4. Run it once by hand and accept all the Health permissions and "Always Allow". On the Mac, `tail health_data/server.log` should show a "received health … token iphone-me" line, and `health_data/import.log` should have one line with the import result.

## Automations (set up on the iPhone yourself; they are not exported with the shortcut)
Shortcuts app → bottom "Automation" tab → top-right "+" → Personal Automation:

| Trigger | How to choose it | Run mode |
|---|---|---|
| Opening a frequently used app (e.g. WeChat) | "App" → pick the app → "Is Opened" | Run Immediately |
| Wake up | "Sleep" → "Wake Up" (requires a sleep schedule set in Health) | Run Immediately |
| Every day 12:00 | "Time of Day" → 12:00 → "Daily" | Run Immediately |
| Every day 18:00 | Same as above | Run Immediately |
| Every day 22:00 | Same as above | Run Immediately |

For each one: choose "**Run Immediately**", turn off "Notify When Run" → "Next" → search for and pick `Watch2Whoop Health Sync`.

**Limitation**: health data is encrypted while the iPhone is locked and cannot be read. A timed trigger that fires while the phone is locked reads nothing or fails. The shortcut deliberately reads overlapping ranges (2 days for heart rate etc., 7 days for sleep etc.), so the next successful run fills the gap, and the server-side import is idempotent. The "open a frequently used app" trigger is the most reliable (the phone is certainly unlocked then).

## Release process (maintainers)
1. Edit **the copy you actually use** on the iPhone, test it, and change `docs/shortcut-sync-spec.md` and the importer to match.
2. "Duplicate" it, and in the copy: change the "Get Contents of URL" address to `https://YOUR-MAC.YOUR-TAILNET.ts.net:8446/upload?name=health` and the header value to `YOUR-UPLOAD-TOKEN`; in details (ⓘ) → "Import Questions" → add one question each for the **URL** and the **Headers** (question text as in step 3 above).
3. Share → "Options" → send as "**File**", audience "**Anyone**" (signed by Apple's servers, without your Apple ID) → AirDrop to the Mac (or save to iCloud Drive; on the Mac find it under `~/Library/Mobile Documents/com~apple~CloudDocs/`).
4. Overwrite `shortcuts/Watch2Whoop Health Sync.shortcut` and run the check (it must pass with ✓):
   ```bash
   python3 shortcuts/check_shortcut.py "shortcuts/Watch2Whoop Health Sync.shortcut"
   ```
   Personal denied words (your hostname, tailnet name, your name, watch name, ...) go one per line in `secrets/shortcut_deny.txt` (not in git); the plaintext tokens in `secrets/health_upload_token_*` are checked automatically. When running in a git worktree, add `--secrets-dir <main repo>/secrets`.
5. Add a row to the "Versions" table above and commit. The copy can be deleted (or kept for next time).

Small parameter changes (ranges, grouping) can also be made on a Mac without the iPhone round trip, as for 1.1: unpack `Shortcut.wflow` the way `check_shortcut.py` does (`extract()`), edit `WFWorkflowActions` with `plistlib` (an ungrouped "Find Health Samples" has no `WFHKSampleFilteringGroupBy` key, like Heart Rate), write it back as a binary plist, then `shortcuts sign --mode anyone --input unsigned.shortcut --output "Watch2Whoop Health Sync.shortcut"` (signed by Apple's servers, no personal identity) and run step 4. People who already imported it still have to change their own copy on the iPhone (or re-import it and redo the import questions and automations).

## License
MIT — see [LICENSE](LICENSE). The rest of the repository (server side) is AGPL-3.0, see [docs/LICENSES.md](../docs/LICENSES.md).
