# Changelog — Watch2Whoop Flutter app (iOS; Android later)

One section per store build, newest first: `## <marketing version> — <date>` (e.g. `## 1.0.6 — 2026-10-22`; the build
number after `+` in `pubspec.yaml` is not part of the public name). Bump `version:` in `pubspec.yaml` (both parts) and
add the section in the same pull request; after it is merged a maintainer tags the commit with
`tools/app_release.sh --push` (`app-ios-v<marketing version>`, `app-android-v<marketing version>`), and the workflow `app-release.yml`
publishes the GitHub pre-release with the source zip; this section becomes its notes. Builds are made only from that
Release (zip checked against its `.sha256`) or `git clone --branch <tag>`, never from `main` or a zip sent by hand.
Versioning rules: [docs/apps.md](../../docs/apps.md#4-versioning-and-releases).

| Version | Tag | Commit | Store |
|---|---|---|---|
| 1.0.7 | `app-ios-v1.0.7` | (set when tagged) | TestFlight (iOS) |
| 1.0.6 | `app-ios-v1.0.6` | (set when tagged) | TestFlight (iOS) |
| 1.0.5+5 | `app-ios-v1.0.5+5` | `e1a93cc` | TestFlight (iOS) |
| 1.0.0+2 | `app-ios-v1.0.0+2` | `2f4a02f` | TestFlight (iOS), uploaded as build 4; crashes on open (fixed in 1.0.5+5) |
| 1.0.0+1 | (not tagged; source = release `v0.1.0` asset `watch2whoop-ios-app-v0.1.0.zip`) | `42a8f0f` | TestFlight (iOS), sent to the builder 2026-10-18 |

## Unreleased

## 1.0.7 — 2026-10-22
Offline use (rollout flag `offline_mode`, server side in the Today page pull requests; design in
[docs/offline-plan.md](../../docs/offline-plan.md)).
- Opening the app without network no longer stops at 「连不上今天页」 / "Cannot reach the today page" for people with
  offline use on: the app keeps a copy of the Today page (`/app/offline-shell.html`, scripts inlined, no user data) and
  opens it when the page cannot load. The copy shows what the page saved on this phone and queues water, weight and
  training sets; back in the foreground (or when the page sees the network again) the app loads the real page, which
  sends the queue. The copy is kept only while the page reports offline use on, and deleted on logout.
- WKWebView runs no service worker in this app (no App-Bound Domains), so without this a cold start offline had no page.
- New direct dependency `path_provider` 2.1.6 (already in the lock as a transitive one; no native pod).

Health data status and permissions (#163): iOS never tells an app whether Health *read* access was granted, so users
could not see whether their data was read or where to change the permissions after the first launch.
- Phone sync → 「服务器收到的数据」 / "Data on the server": per type (heart rate, HRV, resting heart rate, sleep, steps,
  active energy, workouts, weight, blood oxygen, respiratory rate, wrist temperature, VO₂ max, beat-to-beat HRV, ECG,
  water) the last day the server received data and OK / not received lately / never received, from
  `GET /v1/ingest/data-status` (server side in its own PR; an older server shows a short note). Tapping a type shows the
  likely reasons and what helps.
- 「重新申请健康权限」 / "Ask for Health access again": shows the iOS prompt only for types it never covered (asked per
  type with `getRequestStatusForAuthorization`); when every type was asked it explains where to change them instead.
- 「打开系统设置」 / "Open Settings" with the steps (Settings → Health → Data Access & Devices → Watch2Whoop). The app
  changes nothing for the user.
- 「本机最近一次同步」 / "Last sync on this phone": time and result (OK / failed with the reason) of the latest upload.

## 1.0.6 — 2026-10-22
Crash audit of the whole iOS app after the 1.0.0 (4) launch crash (owner request); the findings and evidence are in the
pull request.
- In-page chat: back in the foreground the app tells the Today page (`watch2whoop:foreground`), so an answer cut off
  while the app was in the background is picked up again instead of showing 「连不上」 (#114, server side in the same PR).
- Fix: long press on an image on the Today page (exercise pictures, handbook screenshots, chat thumbnails) →
  「存储到照片」 / "Save to Photos" killed the app: WebKit writes to the photo library from the app process and iOS ends
  apps without `NSPhotoLibraryAddUsageDescription` (`OS_REASON_TCC`, reproduced in the simulator). The key is added.
  The Today page also turns the long-press menu off on images inside the app (server side, so 1.0.5 is covered once
  the server is deployed).
- Fix: Jarvis → video → 「拍摄视频」 / "Take Video" records sound, which needs `NSMicrophoneUsageDescription`; the key is
  added. The camera description now also names photo / video capture for the AI, not only the setup QR code.
- Apple Health water (#135): the app reads `dietaryWater` (read only; one more Health permission sheet, 「水」 / "Water")
  and uploads it with the beat-to-beat readings to `POST /v1/ingest/healthkit` `water[]`: incremental (HealthKit anchor,
  moved only after the server stored the batch), idempotent by UUID, deletions in Health passed on. Only while the
  server's `water` flag is on for the account (`water_enabled`); while it is off nothing is read or sent and nothing is
  lost. A missing new consent for beat-to-beat readings no longer holds back water.
- ECG upload: a non-finite voltage sample no longer traps when converted to an integer (`RawSync.swift`).
- Tests (`test/ios_config_test.dart`): every privacy service the app or the Today page can reach has a usage
  description in `Info.plist` and in both `InfoPlist.strings`; HealthKit series types are requested together with their
  parent types.
- Fix: every confirm() on the Today page was false inside the app (webview_flutter's default without a handler), so
  deleting an AI provider, disconnecting / revoking an AI client, withdrawing the fallback-AI consent, resetting an
  invite link, removing a member, leaving or dissolving a circle did nothing. alert() / confirm() / prompt() now show
  native iOS dialogs (`js_dialogs.dart`); the buttons follow the app language.

## 1.0.5+5 — 2026-10-22
- Fix: the app crashed on open (SIGABRT) once Health access had been asked, in build 1.0.0 (4) made from
  `app-ios-v1.0.0+2`. The permission request for beat-to-beat HRV and ECG (`RawSync.swift`) asked for the heartbeat
  series without its parent type HRV SDNN; HealthKit rejects such a request with an `NSInvalidArgumentException`
  ("Authorization for HKQuantityTypeIdentifierHeartRateVariabilitySDNN should also be requested when requesting
  authorization to read HKDataTypeIdentifierHeartbeatSeries"), which Swift cannot catch. The request now includes HRV
  SDNN. Checked every HealthKit request in the app: none of the types the OW SDK asks for has a parent type.
- The automatic ask for these two permissions is remembered before the system sheet is requested, so a failure in
  HealthKit can no longer repeat on every open; the permissions step still asks on demand.
- Marketing version 1.0.5 (a bug-fix release; TestFlight shows "1.0.5 (5)"). The build number stays above build 4, which TestFlight already has (the upload of `app-ios-v1.0.0+2`).

## 1.0.0+2 — 2026-10-21
- Self-service sign-up: Sign in with Apple, e-mail code, invite code; existing setup links keep working (#16).
  Accounts that were activated before sign in directly with Apple or an e-mail code (server side, #70).
- Today-page sign-in: the one-time login code from the setup link is kept until the Today page reports `logged-in` or
  `login-failed` over the `Watch2Whoop` JavaScript channel, and an interrupted load retries on the next open (#18).
- English: the native screens follow the language chosen on the Today page or the login screen, with a 中文 / English
  switch on the native login screen (#20, #61); iOS permission prompts localized in English and Chinese (#68).
- The official build is locked to its server (`W2W_LOCK_SERVER` in the private `config/defaults.json`): no server field,
  setup links for other servers are refused, the host is never shown (#61).
- First-run onboarding: a permissions step (Health) after sign-up; intro and tips on the Today page (#63).
- Sync on open: every time the app is opened or comes back to the foreground it syncs once (throttled) and asks the
  server to pull the new data right away; the Today page shows 「同步中… / 已更新」 (#69).
- Health read range: 「近 90 天」 (default) or 「全部历史」, chosen in the permissions step or later under Phone sync →
  Read range. The app reads the whole history from HealthKit itself (OW SDK, newest first, resumable) with progress and a
  rough time left; the server then computes the scores for the whole history and the Today page shows
  「历史数据导入中 x%」. The manual export.zip upload on the Today page is gone (admins keep `import-apple-zip`).
- Beat-to-beat HRV and ECG: the app reads the beat times of each HRV reading (`HKHeartbeatSeriesQuery`) and the ECG
  recordings (`HKElectrocardiogramQuery`: waveform, Apple's classification, average heart rate) from Health itself and
  sends them straight to the server (`POST /v1/ingest/healthkit`; OW does not carry these types). Same read range as above,
  incremental (HealthKit anchors, moved only after the server accepted a batch), on every sync on open; one extra Health
  permission sheet for the two types. Phone sync shows the counts and the last upload. Uploaded only after the person
  agreed to consent version 2026-10-22.1, which names ECGs and beat-to-beat intervals (the Today page asks once).

## 1.0.0+1 — 2026-10-18
First build (iOS only), the state of the public release `v0.1.0`:
- Background sync of Apple Health data to Open Wearables with the OW Flutter SDK 0.0.25 (native iOS SDK 0.15):
  HealthKit background delivery + BGTask incremental sync, first sync 90 days.
- Today page full-screen in a WebView; same-origin links stay in the app, others open in the system.
- One-step setup from the setup QR code / link `watch2whoop://setup?today=…&ow=…&code=…&login=…` (camera, in-app
  scanner or paste): server addresses, OW invitation code, one-time Today-page login code.
- Native "phone sync" page (`watch2whoop://sync`): status, record counts, sync now, retry, sync log, server settings.
- No third-party analytics or crash reporting.
