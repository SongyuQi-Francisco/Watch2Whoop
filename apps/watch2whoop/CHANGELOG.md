# Changelog — Watch2Whoop Flutter app (iOS; Android later)

One section per store build, newest first: `## <version from pubspec.yaml> — <date>`. Bump `version:` in
`pubspec.yaml` and add the section in the same pull request; after it is merged a maintainer tags the commit with
`tools/app_release.sh --push` (`app-ios-v<version>`, `app-android-v<version>`), and the workflow `app-release.yml`
publishes the GitHub pre-release with the source zip; this section becomes its notes. Builds are made only from that
Release (zip checked against its `.sha256`) or `git clone --branch <tag>`, never from `main` or a zip sent by hand.
Versioning rules: [docs/apps.md](../../docs/apps.md#4-versioning-and-releases).

| Build | Tag | Commit | Store |
|---|---|---|---|
| 1.0.0+1 | (not tagged; source = release `v0.1.0` asset `watch2whoop-ios-app-v0.1.0.zip`) | `42a8f0f` | TestFlight (iOS), sent to the builder 2026-10-18 |

## Unreleased
Becomes `## 1.0.0+2` (the version already in `pubspec.yaml`) once the owner has accepted it:
- Self-service sign-up: Sign in with Apple, e-mail code, invite code; existing setup links keep working (#16).
- Today-page sign-in: the one-time login code from the setup link is kept until the Today page reports `logged-in` or
  `login-failed` over the `Watch2Whoop` JavaScript channel, and an interrupted load retries on the next open (#18).

## 1.0.0+1 — 2026-10-18
First build (iOS only), the state of the public release `v0.1.0`:
- Background sync of Apple Health data to Open Wearables with the OW Flutter SDK 0.0.25 (native iOS SDK 0.15):
  HealthKit background delivery + BGTask incremental sync, first sync 90 days.
- Today page full-screen in a WebView; same-origin links stay in the app, others open in the system.
- One-step setup from the setup QR code / link `watch2whoop://setup?today=…&ow=…&code=…&login=…` (camera, in-app
  scanner or paste): server addresses, OW invitation code, one-time Today-page login code.
- Native "phone sync" page (`watch2whoop://sync`): status, record counts, sync now, retry, sync log, server settings.
- No third-party analytics or crash reporting.
