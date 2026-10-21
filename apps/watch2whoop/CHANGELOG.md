# Changelog — Watch2Whoop Flutter app (iOS; Android later)

One section per store build, newest first: `## <version from pubspec.yaml> — <date>`. Bump `version:` in
`pubspec.yaml` and add the section in the same pull request; after it is merged a maintainer tags the commit with
`tools/app_release.sh --push` (`app-ios-v<version>`, `app-android-v<version>`), and the workflow `app-release.yml`
publishes the GitHub pre-release with the source zip; this section becomes its notes. Builds are made only from that
Release (zip checked against its `.sha256`) or `git clone --branch <tag>`, never from `main` or a zip sent by hand.
Versioning rules: [docs/apps.md](../../docs/apps.md#4-versioning-and-releases).

| Build | Tag | Commit | Store |
|---|---|---|---|
| 1.0.0+2 | `app-ios-v1.0.0+2` | (set when tagged) | TestFlight (iOS) |
| 1.0.0+1 | (not tagged; source = release `v0.1.0` asset `watch2whoop-ios-app-v0.1.0.zip`) | `42a8f0f` | TestFlight (iOS), sent to the builder 2026-10-18 |

## Unreleased

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

## 1.0.0+1 — 2026-10-18
First build (iOS only), the state of the public release `v0.1.0`:
- Background sync of Apple Health data to Open Wearables with the OW Flutter SDK 0.0.25 (native iOS SDK 0.15):
  HealthKit background delivery + BGTask incremental sync, first sync 90 days.
- Today page full-screen in a WebView; same-origin links stay in the app, others open in the system.
- One-step setup from the setup QR code / link `watch2whoop://setup?today=…&ow=…&code=…&login=…` (camera, in-app
  scanner or paste): server addresses, OW invitation code, one-time Today-page login code.
- Native "phone sync" page (`watch2whoop://sync`): status, record counts, sync now, retry, sync log, server settings.
- No third-party analytics or crash reporting.
