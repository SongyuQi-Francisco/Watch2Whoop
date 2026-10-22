# Client apps: platforms, layout and releases

[English](#english) · [中文摘要](#中文摘要)

---

## English

Watch2Whoop has one server (Body Engine + Open Wearables) and, over time, several thin client apps. Every client does the
same three things: **sync the phone's / watch's health data** to the server, **show the Today page** full-screen in a
WebView, and **set itself up from one setup link / QR code**. Everything else (scores, logging, AI) lives on the server.
This document says which platforms are planned, what is shared, how the `apps/` directory is laid out and how app
releases are versioned.

### 1. Platform matrix

| Platform | Status | Code base | Health data source | Store / distribution |
|---|---|---|---|---|
| **iOS** (iPhone) | Shipping (TestFlight) | Flutter, `apps/watch2whoop/` | Apple HealthKit via the [Open Wearables Flutter SDK](https://github.com/the-momentum/open_wearables_health_sdk) (`open_wearables_health_sdk` 0.0.25 → native iOS SDK 0.15) | TestFlight from a friend's Apple Developer account; App Store later |
| **Android** | Planned, same Flutter code base | Flutter, same directory (`android/` target added with `flutter create --platforms android .`) | **Health Connect** or **Samsung Health** — both already supported by the same OW Flutter SDK (Android provider selection with `getAvailableProviders()` / `setProvider()`, `minSdk 29`, Health Connect permission aliases in `AndroidManifest.xml`) | Google Play (internal / closed testing first) or a signed APK |
| **Huawei, HarmonyOS NEXT** (HarmonyOS 5+, no Android runtime) | Idea | Separate app, `apps/harmony/` — recommended: **native ArkTS** (ArkUI + ArkWeb `Web` component); alternative: Flutter for OpenHarmony (community fork, plugins need `ohos` ports; the OW SDK has none) | Huawei **Health Service Kit** (`@kit.HealthServiceKit`); data scopes must be approved by Huawei | AppGallery |
| Huawei phones on HarmonyOS ≤ 4 / EMUI | Not planned | Would run the Android build | No Google services, so usually no Health Connect; would need an HMS Health Kit provider | AppGallery |
| **Apple Watch** app | Later | watchOS target inside the iOS Xcode project (`ios/`), not a separate directory | Not needed for sync (the iPhone already reads the watch's data from HealthKit); useful for on-wrist scores / live workouts | Ships with the iOS app |
| **Wear OS** app | Later | Wear OS module inside the Android project (`android/`) | Health Services on the watch; phone app syncs via Health Connect | Ships with the Android app |

**Why native ArkTS for HarmonyOS NEXT:** the client is small (a WebView, the setup flow, a sync page, a background
sync job), and its contract with the server is written down below. The only heavy part is health-data sync, and there is
no Open Wearables SDK for HarmonyOS in any language, so it has to be written natively either way. Flutter for
OpenHarmony would add a community-maintained toolchain on top without removing that work. Revisit if the OW project
publishes a HarmonyOS SDK.

**Open question for HarmonyOS (decide before starting):** where the Harmony app sends data. Either (a) to Open
Wearables' mobile-SDK sync endpoints in the same format the iOS / Android SDKs use (keeps one ingestion path, needs the
format documented / stable upstream), or (b) to a new Body Engine importer (we own the format, but scores then read two
ingestion paths). (a) is preferred.

**Server-side checks before Android ships:** OW normalizes Health Connect / Samsung Health records into the same
tables, but Body Engine's `import-ow`, the source priority rules (`importers/common.py resolve_daily`) and the sleep /
workout importers were only exercised with Apple sources. Run an Android test user end to end and compare a few days
against the phone before inviting family.

### 2. Shared vs per platform

| Shared by every client (one implementation on the server, one spec for clients) | Per platform |
|---|---|
| Server API and the Today page (`/app/`), shown full-screen in a WebView; all viewing, logging and "Ask the AI" happen there | Health data source and its permissions UI (HealthKit / Health Connect / Samsung Health / Health Service Kit) |
| Setup link / QR code `watch2whoop://setup?today=…&ow=…&code=…&login=…` (Today page URL, OW URL, one-time OW invitation code, one-time Today-page login code); parsing rules in `apps/watch2whoop/lib/src/config.dart` | Background execution rules (iOS BGTask + HealthKit delivery, Android WorkManager, HarmonyOS background tasks) |
| OW invitation code redemption: `POST {OW}/api/v1/invitation-code/redeem` → SDK access / refresh tokens | Deep-link registration (`watch2whoop://` scheme / universal links / App Links) |
| Today-page sign-in: open `/app/#login=<code>`; the page reports `logged-in` / `login-failed` over the `Watch2Whoop` JavaScript channel; the app keeps the code until then | WebView quirks (cookies, microphone permission, file upload, safe area) |
| `watch2whoop://sync` from the page opens the native sync page; `window.Watch2WhoopFeatures` tells the page which native features exist (e.g. `mic`) | Signing, bundle / package IDs, store listing, review notes |
| Same-origin links stay in the app, other links go to the system browser / apps | Release channel (TestFlight, Play testing tracks, AppGallery) |
| Account / self sign-up flow (server API), privacy: no third-party analytics or crash reporting | Versioning numbers per store (see section 4) |

A new client is "done" when it implements every row of the left column. Keep the left column in sync with the iOS app
when the contract changes (the iOS app is the reference implementation).

### 3. Directory layout

**Today:**
```
apps/
  README.md          index of client apps
  watch2whoop/       Flutter app (iOS target today; Android target to be added)
```

**Target:**
```
apps/
  README.md          index of client apps (platform, directory, status, how to build)
  flutter/           Flutter app: ios/ (+ watchOS target later) and android/ (+ Wear OS module later)
    CHANGELOG.md     one changelog for both targets (one code base, one pubspec version)
  harmony/           HarmonyOS NEXT app (ArkTS), when it starts
    CHANGELOG.md
```
The Flutter package name stays `watch2whoop` (Dart imports are `package:watch2whoop/...` and do not depend on the
directory name), so the rename only moves files.

#### When to rename `apps/watch2whoop` → `apps/flutter`
**Later, in its own PR, right after the open PRs that change `apps/watch2whoop/` are merged and before the Android
target is added.** As of 2026-10-21 these are open: #16 (app self-signup) and #20 (i18n). Renaming now would make both
conflict on every file. A rename-only PR (no other changes) keeps `git log --follow` and blame usable and is easy to
review. Doing it before `flutter create --platforms android .` means the Android files land in the final place once.

Exact steps (one PR, after #16 and #20 are merged; also check `gh pr list` for any newer PR touching `apps/`):
```bash
git switch main && git pull && git switch -c chore/rename-app-dir
git mv apps/watch2whoop apps/flutter
# Every tracked reference to the old path:
git grep -l "apps/watch2whoop" | xargs sed -i '' 's#apps/watch2whoop#apps/flutter#g'   # GNU sed: sed -i
git grep -n "apps/watch2whoop"          # afterwards only historical mentions you restore by hand in CHANGELOG.md
cd apps/flutter && flutter clean && flutter pub get && flutter test && (cd ios && pod install) && cd ../..
git add -A && git commit -m "Move the Flutter app to apps/flutter (rename only)"
```
Files that reference the path today (the `git grep` above finds them all): `.github/workflows/ci.yml` (working
directory and path filter), `.github/pull_request_template.md`, `CLAUDE.md`, `CONTRIBUTING.md` (licence table),
`README.md`, `docs/LICENSES.md`, `docs/README.md`, `docs/apps.md`, `docs/system-design.md`,
`docs/ow-ios-app-testflight-guide.md`, `docs/raw-data-plan.md`, `apps/README.md`, the app's own `README.md`,
`tools/export_public.py`, `tools/public/overrides/CLAUDE.md`. `tools/app_release.sh` finds either directory by itself.
After merging: tell whoever builds the app that the path is now `apps/flutter` (old tags keep the old path), and update
any private deployment overlay that copies app files. Relative links inside the app README (`../../docs/…`) keep
working because the depth does not change.

### 4. Versioning and releases

- **The server** keeps semantic version tags `vX.Y.Z` (e.g. `v0.1.0`) and GitHub releases.
- **Each app** has its own tags named after the **marketing version only**: `app-<platform>-v<X.Y.Z>`, e.g.
  `app-ios-v1.0.6`, `app-android-v1.0.6`, `app-harmony-v1.0.0`. The public name of a build (tag, release title, zip,
  CHANGELOG) never shows the build number. The tag points at the exact commit the store build was made from. Tags made
  before this rule keep their names (`app-ios-v1.0.0+2`, `app-ios-v1.0.5+5`).
- For the Flutter app the version comes from `pubspec.yaml` (`version: 1.0.6+6` = marketing version `1.0.6`, build
  number `6`). The build number stays there because the stores need it; the person building never touches it. iOS and
  Android share it: the build number only ever goes up (Android `versionCode` must increase forever; TestFlight needs a
  new build number for every upload). One commit can carry both an iOS and an Android tag.
- **Each marketing version is released once per platform.** The version is bumped in a PR, never by the person
  building: change both parts of `version:` in `pubspec.yaml` and add a section `## 1.0.6 — YYYY-MM-DD` to the app's
  `CHANGELOG.md` in the same PR. A new build only for the 90-day TestFlight expiry gets the next patch version
  (`1.0.6+6` → `1.0.7+7`), changelog line "Rebuild, no changes".
- **After the PR is merged** a maintainer runs [`tools/app_release.sh`](../tools/app_release.sh):
  ```bash
  tools/app_release.sh ios                 # dry run: checks + shows the tag and commit it would use
  tools/app_release.sh ios --create        # optional: annotated tag locally + dist/…zip to look at, nothing pushed
  tools/app_release.sh ios --push          # tag (or reuse the --create one) and push it
  ```
  **The GitHub Release is made only by the workflow [`app-release.yml`](../.github/workflows/app-release.yml)** on every
  pushed `app-ios-v*` / `app-android-v*` tag. It runs the same script (`--publish <tag>`), which checks the tag again:
  the marketing version in `pubspec.yaml` equals the tag, that version has no tag of this platform yet, the build number
  is higher than the build of every earlier tag of the platform (read from each tag's `pubspec.yaml`, so the older
  `…+N` tags count), the CHANGELOG section exists and the commit is on `main`. It then publishes a **pre-release**
  titled "iOS app 1.0.6" with the source zip `watch2whoop-ios-app-v1.0.6.zip`, its `.sha256` and the CHANGELOG section
  as notes (they name the build number once, for the person uploading). A failed check means no release; delete the tag
  (`git push origin :refs/tags/<tag>`) and fix it in a PR. App pre-releases are never marked "latest", so the server
  release stays the latest. The zip contains only the app directory at that commit (like the `v0.1.0` release asset)
  and carries the commit id as its zip comment (`unzip -z`).
- Test runs of the flow use a `-test` version on a throwaway commit (`pubspec.yaml` `0.0.0-test+0` → tag
  `app-ios-v0.0.0-test`): not checked against `main`, earlier versions or build numbers, titled "TEST". Delete the
  release and the tag right after
  (`gh release delete <tag> --cleanup-tag --yes`).
- **The person building** (TestFlight / Play) builds **only from that GitHub Release** (its zip, checked against the
  `.sha256` and the commit) or `git clone --branch <tag>`. Never from `main` or a zip sent by hand, and never edits
  the version. See the app README, "Get the code".
- Changelogs: [`apps/watch2whoop/CHANGELOG.md`](../apps/watch2whoop/CHANGELOG.md) (iOS + Android).

### 5. CI

`.github/workflows/ci.yml` decides per pull request which jobs are needed from the changed paths:
server jobs ("Body Engine tests") run when anything outside `apps/` changes, "Flutter tests" when the Flutter app
changes, "Syntax checks" always, and everything runs on pushes to `main` and when the workflow itself changes. Skipped
jobs report success, so the required checks of the `main` branch protection still pass. Future apps are added to the
`OTHER_APPS` list at the top of the workflow (HarmonyOS is there as a disabled placeholder) and get a step for their
toolchain in the "Other apps" job.

### 6. Issues and pull requests

Labels `platform:ios`, `platform:android`, `platform:harmony`, `platform:server`, `platform:web` (the Today page); the
issue forms ask for the platform, and the PR template has a platform checklist.

---

## 中文摘要

- **平台**：iOS = 现在的 Flutter app（`apps/watch2whoop/`，HealthKit，经 OW Flutter SDK）；**Android = 同一套 Flutter 代码**，OW Flutter SDK 0.0.25 已经支持 Health Connect 和 Samsung Health（minSdk 29），加 `android/` 目标即可；**华为 HarmonyOS NEXT = 单独的 app**（`apps/harmony/`），建议用原生 ArkTS（ArkWeb 内嵌今天页 + Health Service Kit，数据权限要华为审批），因为 OW 没有鸿蒙 SDK，同步部分怎么都得原生写；数据发到 OW 的移动端同步接口（首选）还是 Body Engine 新导入器，开工前要定。Apple Watch / Wear OS 以后做，分别放在 iOS / Android 工程里。
- **共用**：服务器 API、WebView 里的今天页、设置二维码 `watch2whoop://setup?…`、OW 邀请码兑换、今天页一次性登录码、`watch2whoop://sync`。**各平台自己的**：健康数据来源和权限、后台执行、深链注册、签名和商店发布、版本号。
- **目录**：目标是 `apps/flutter/`（iOS + Android）+ `apps/harmony/` + `apps/README.md` 索引。**现在不改名**：#16（app 自助注册）和 #20（多语言）都在改 `apps/watch2whoop/`；等它们合并后，单独开一个只改名的 PR，并在加 Android 之前做（步骤见第 3 节）。
- **版本**：服务器继续 `vX.Y.Z`；app 的 tag 只用营销版本，例如 `app-ios-v1.0.6`。tag、Release 标题、zip 名、CHANGELOG 节标题（`## 1.0.6 — 日期`）都不出现构建号；构建号只留在 `pubspec.yaml`（`version: 1.0.6+6`，商店要用它，打包的人不用管）。iOS / Android 共用 pubspec 的版本，构建号只增不减；**每个营销版本每个平台只发一次**，TestFlight 90 天过期重新打包也要升补丁版本（1.0.6 → 1.0.7）。改版本号和 CHANGELOG 走 PR，合并后维护者跑 `tools/app_release.sh`（默认只预演，`--create` 本地打 tag + 打包看看，`--push` 推送 tag）；**GitHub Release 只由 workflow `app-release.yml` 在 tag 推上去后自动生成**：会再查一遍营销版本和 tag 一致、这个版本没发过、构建号比之前所有 tag 的都大（从每个 tag 的 `pubspec.yaml` 读，以前带 `+N` 的老 tag 也算）、有 CHANGELOG 一节、commit 在 main 上，然后附上源码 zip 和 sha256。整个流程不依赖维护者的电脑。以前的 `app-ios-v1.0.0+2`、`app-ios-v1.0.5+5` 保持原名。流程测试用 `-test` 版本（pubspec `0.0.0-test+0` → tag `app-ios-v0.0.0-test`），测完立即删掉 release 和 tag。
- **帮忙编译的朋友**：只从这个 tag 的 GitHub Release 编译（zip 先对 sha256 和 commit，或 `git clone --branch <tag>`），不用 main、不用手发的 zip，不自己改版本号。
- **CI**：按改动路径决定跑哪些任务，跳过的任务也报成功，分支保护的三个必需检查照常；以后的 app 加在工作流顶部的 `OTHER_APPS` 列表里（鸿蒙占位）。
