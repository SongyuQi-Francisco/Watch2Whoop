# Changelog — Watch2Whoop Flutter app (iOS; Android later)

One section per store build, newest first: `## <version from pubspec.yaml> — <date>`. Bump `version:` in
`pubspec.yaml` and add the section in the same pull request; after it is merged a maintainer tags the commit with
`tools/app_release.sh --push` (`app-ios-v<version>`, `app-android-v<version>`), and the workflow `app-release.yml`
publishes the GitHub pre-release with the source zip; this section becomes its notes. Builds are made only from that
Release (zip checked against its `.sha256`) or `git clone --branch <tag>`, never from `main` or a zip sent by hand.
Versioning rules: [docs/apps.md](../../docs/apps.md#4-versioning-and-releases).

| Build | Tag | Commit | Store |
|---|---|---|---|
| 1.0.5+5 | `app-ios-v1.0.5+5` | (set when tagged) | TestFlight (iOS) |
| 1.0.0+2 | `app-ios-v1.0.0+2` | `2f4a02f` | TestFlight (iOS), uploaded as build 4; crashes on open (fixed in 1.0.5+5) |
| 1.0.0+1 | (not tagged; source = release `v0.1.0` asset `watch2whoop-ios-app-v0.1.0.zip`) | `42a8f0f` | TestFlight (iOS), sent to the builder 2026-10-18 |

## Unreleased

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
