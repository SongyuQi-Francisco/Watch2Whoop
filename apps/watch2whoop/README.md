# Watch2Whoop app (iOS, and an Android APK for Huawei phones)

[English](#english) · [中文步骤](#中文步骤)

---

## English

The main Watch2Whoop product: one app that does everything (the "single entry point"), also used by family members.

1. **Background sync of Apple Health data** to your own self-deployed [Open Wearables](https://github.com/the-momentum/open-wearables) (OW, the "sync server"): raw heart rate samples, HRV, resting heart rate, sleep stages, workouts, steps, energy, blood oxygen, respiration, weight / body fat, VO₂max and more. It uses OW's official Flutter SDK ([open_wearables_health_sdk](https://github.com/the-momentum/open_wearables_health_sdk), MIT), with HealthKit background delivery + BGTask incremental sync (native iOS SDK 0.15).
2. **The Today page embedded full-screen** (Body Engine's web front end); viewing, logging and "Ask the butler" (「问管家」) all happen there.
3. **Self-service sign-up**: "Sign in with Apple" or an e-mail code (no password) → an invite code during the beta (only for new, not yet activated accounts) → Health permission; sync is connected silently with a server-minted invitation code and the Today page is logged in automatically. Changing phones = sign in with the same Apple ID / e-mail again.
4. **Or scan one "setup QR code"** ("I have a setup link" on the first screen, for accounts opened by an admin): server addresses + sync invitation code + a one-time Today-page login code.

The app only connects to the servers given in the setup information, and has **no third-party analytics or crash reporting**. The person who builds and uploads it cannot see any health data.

### Layout
| Path | Purpose |
|---|---|
| `lib/main.dart` | Entry point: onboarding not finished → onboarding page, otherwise → Today page; receives `watch2whoop://setup?…` setup links |
| `lib/src/pages/onboarding_page.dart` | First screen: server address → Sign in with Apple / e-mail code → invite code (pending accounts) → Health permission + silent sync connection → Today page |
| `lib/src/pages/setup_link_page.dart` | "I have a setup link": scan a QR code / paste the setup info (one step) → Health permissions → Today page (auto login); "manual entry" (「手动填写」) is collapsed |
| `lib/src/account_api.dart`, `account_flow.dart`, `account_store.dart` | Self-service account API client (pure Dart, unit-tested), session / sync / sign-out glue, the account token in the iOS Keychain (`flutter_secure_storage`, this device only) |
| `lib/src/pages/home_page.dart` | Full-screen WebView of the Today page; same-origin links stay in the app, others go to the system; `watch2whoop://sync` opens the sync page; opens `/app/#login=…` when a one-time login code is present (account users: a fresh code is minted when the page is not logged in); `watch2whoop://account-deleted` (sent by the page after 「我」→「删除账号」) stops sync, clears the token and returns to onboarding |
| `lib/src/pages/sync_page.dart` | Native "phone sync" (「手机同步」) page: status, record counts per type, sync now, retry on failure, scan / paste new setup info, server settings, sign out, sync log |
| `lib/src/pages/scan_page.dart` | In-app setup QR scanner (mobile_scanner; on iOS it uses the system AVFoundation, without Google ML Kit) |
| `lib/src/setup_flow.dart` | Applies setup info in one step (server → connect sync → store the login code); receives `watch2whoop://` links |
| `lib/src/health_sync.dart` | Wraps the OW SDK: invitation code redemption, authorization, start / stop sync, status |
| `lib/src/raw_sync.dart`, `lib/src/hk_types.dart` | HealthKit types the OW SDK cannot read (beat-to-beat, ECG, water, and the registry: stand time, exercise minutes, wrist temperature …) read by the app itself with one anchor per type and uploaded to Body Engine |
| `lib/src/resync.dart`, `lib/src/resync_flow.dart`, `lib/src/pages/sync_center_section.dart` | Sync center (server flag `sync_center`): forced re-sync of a range, phone / server newest sample per type |
| `lib/src/sync_stats.dart`, `lib/src/config.dart` | Sync log statistics; configuration, address validation, invitation / login codes, setup-info parsing (pure Dart, unit-tested) |
| `ios/Runner/AppDelegate.swift` | Starts the OW SDK natively first (BGTask, background wake-up); SDK log buffer; setup-link inbox; the simulator debug build writes test data |
| `ios/Flutter/Watch2Whoop.xcconfig` | Bundle ID / Team ID / display name (**placeholder values in the repository**) |
| `config/defaults.example.json` | Default server addresses compiled into the app: the project's own public servers (`body.example.com` / `sync.example.com`); people who self-host change them to their own domains |

### Setup QR code (done by the server admin)
```bash
# A family member's first use: create the user in OW, issue the sync invitation code and the Today-page login code, print the QR code (needs qrencode)
python3 deploy/open-wearables/invite.py --create-user "Mum" --body-user-id <Body Engine user ID> --label "Mum's iPhone" \
    --today-url https://body.example.com --ow-url https://sync.example.com \
    --body-cmd deploy/ali/engine.sh --qr mom-setup.png      # run on Ali in /opt/body-management
```
- The QR code contains `watch2whoop://setup?today=…&ow=…&code=…&login=…`. **There is no long-lived token**: the sync invitation code is single-use and expires after a few days; the Today-page login code is single-use and lasts 72 hours by default (when redeemed, the server issues this iPhone its own dedicated token; revoking it signs this device out). Even so, only give it to the person it is for.
- The family member scans it with the **iPhone Camera** → taps "Open in Watch2Whoop" → the server is filled in and sync is connected automatically → tap "Continue" (「继续」), in the Health permissions tap "Turn On All" and "Allow" → "Open Today page" (「打开今天页」) logs in automatically. They can also tap "Scan setup QR code" (「扫描设置二维码」) in the app, or be sent the link and use "Paste setup info" (「粘贴设置信息」).
- Create the Body Engine user first (`body-engine user-add`) and record the OW user's UUID in `users.ow_user_id` (`body-engine ow-link`, or the UUID printed by invite.py).
- If the server is only on a private network, the iPhone must connect to the VPN first (the app shows a one-line hint); a public https domain does not need that.

### Building and shipping via TestFlight (for the person with the Apple Developer account)

#### 0. Prerequisites
- A paid Apple Developer Program account (a free account cannot use TestFlight).
- A Mac with Xcode 26 or newer (install the iOS platform in Xcode → Settings → Components).
- Flutter **3.47.7** (`brew install --cask flutter`, confirm with `flutter --version`) and CocoaPods 1.17 (`brew install cocoapods`).
- If `xcode-select -p` prints `/Library/Developer/CommandLineTools`, run `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer` once (required: a dependency's native build hook calls `xcrun --sdk iphoneos` and fails with "Bad state: No element" otherwise).

#### 1. Get the code — only from the GitHub Release of a tag, never from `main` or a zip sent by hand
Every store build is made from a tag the project owner creates, named after the version, e.g. `app-ios-v1.0.6`
(the build number, e.g. 6, is only in `pubspec.yaml`: `version: 1.0.6+6`;
[docs/apps.md](../../docs/apps.md#4-versioning-and-releases)). Older tags still carry it in the name (`app-ios-v1.0.5+5`). Pushing the tag makes GitHub Actions publish a
**pre-release** of that name on the repository's **Releases** page, with a source zip of this directory, its `.sha256`
and the changes ([CHANGELOG.md](CHANGELOG.md)). The owner sends you the tag and the Release link. `main` may contain
unfinished work and a version number that was never released; a zip from anywhere else is not a source for a build.

**A. From the Release zip** (no git needed):
```bash
gh release download app-ios-v1.0.6 --repo <owner>/<repo> --dir w2w-1.0.6   # or download both files from the Release page
cd w2w-1.0.6
shasum -a 256 -c watch2whoop-ios-app-v1.0.6.zip.sha256   # must print "OK"
unzip -z watch2whoop-ios-app-v1.0.6.zip | tail -1        # must print the commit shown on the Release page
unzip -q watch2whoop-ios-app-v1.0.6.zip && cd watch2whoop-ios-app-v1.0.6/apps/watch2whoop
```
**B. With git:**
```bash
git clone --branch app-ios-v1.0.6 --depth 1 <repository URL> watch2whoop-src
cd watch2whoop-src/apps/watch2whoop
git describe --tags --exact-match        # must print the tag you were given
```
Then, either way:
```bash
grep '^version:' pubspec.yaml            # 1.0.6+6: the part before + matches the tag, + is the build number; do not edit it
flutter pub get
cd ios && pod install && cd ..
grep -A1 "OpenWearablesHealthSDK (" ios/Podfile.lock   # should be 0.15.x, not 0.14
```

#### 2. Fill in your own Bundle ID, Team ID and default servers (none of it goes into git)
```bash
cp ios/Flutter/Watch2Whoop.local.xcconfig.example ios/Flutter/Watch2Whoop.local.xcconfig
open -e ios/Flutter/Watch2Whoop.local.xcconfig
cp config/defaults.example.json config/defaults.json
open -e config/defaults.json
```
- `W2W_BUNDLE_ID`: your bundle ID, e.g. `com.<your name>.watch2whoop` (globally unique, cannot be changed after upload).
- `W2W_TEAM_ID`: the 10-character Team ID under developer.apple.com → Account → Membership details.
- `W2W_DISPLAY_NAME`: the name on the home screen, default Watch2Whoop.
- `config/defaults.json`: the example already holds the project's public servers (Today page `https://body.example.com`, sync `https://sync.example.com`); just copy it. **If you still have an older `config/defaults.json`, delete its `W2W_SYNC_DAYS_BACK` line** (older examples had `"90"`, which makes the first sync read 90 days again; without the line the app uses the 14-day default in `lib/src/sync_params.dart`). People who deploy their own server change it to their own domains. The setup QR code carries the addresses as well, so this step only decides what is pre-filled for "manual entry".

**Do not** change Team / Bundle ID on Xcode's Signing page (that writes into the project file); edit the files above instead.

#### 3. Confirm signing and capabilities in Xcode (once, the first time)
1. `open ios/Runner.xcworkspace` (note: `.xcworkspace`).
2. **Runner** target → **Signing & Capabilities**: tick "Automatically manage signing"; the Team should already be yours; you should see **HealthKit** (with Background Delivery ticked) and **Background Modes** (Background fetch, Background processing). They come from the repository; you do not add them.
3. When it asks to register the App ID, click "Register" / "Try Again"; Xcode creates an App ID with HealthKit.
4. **Sign in with Apple** (needed since build 1.0.0+2; the entitlement `com.apple.developer.applesignin` is already in `ios/Runner/Runner.entitlements`):
   - developer.apple.com → **Certificates, Identifiers & Profiles** → **Identifiers** → your app's bundle ID → tick **Sign In with Apple** (choose "Enable as a primary App ID") → **Save**.
   - Refresh the provisioning profile: with "Automatically manage signing" Xcode does it on the next build (if it still complains, untick and re-tick the checkbox, or Xcode → Settings → Accounts → Download Manual Profiles). With manual signing, regenerate the App Store profile for this App ID and download it.
   - Tell the server owner your bundle ID: the server's `BODY_APPLE_AUDIENCES` must equal it exactly, otherwise Apple sign-ins are rejected (401).
   - You should now also see **Sign in with Apple** under Signing & Capabilities.

5. **Push Notifications** (needed since 1.0.7; the entitlement `aps-environment` is already in `ios/Runner/Runner.entitlements`):
   - developer.apple.com → **Certificates, Identifiers & Profiles** → **Identifiers** → your app's bundle ID → tick **Push Notifications** → **Save**. If it offers to create certificates, skip that: the server uses a key (next point), not certificates.
   - Refresh the provisioning profile as for Sign in with Apple (automatic signing does it on the next build). You should then see **Push Notifications** under Signing & Capabilities (if not: **+ Capability** → Push Notifications).
   - Create the APNs key (once per team; one key works for all your apps): **Keys** → **+** → name it e.g. `Watch2Whoop push` → tick **Apple Push Notifications service (APNs)** → if asked, environment **Sandbox & Production** and **Team Scoped (All Topics)** → **Continue** → **Register** → **Download** `AuthKey_<KeyID>.p8` (Apple lets you download it **only once**). Note the **Key ID** (10 characters, shown on the key) and your **Team ID** (Membership details).
   - The key is for the server, not the app: building does not need it. Hand it to the server owner as described in "Handing over the APNs key" below.

#### Handing over the APNs key (push)
The server owner needs four things: the `.p8` file, its **Key ID**, your **Team ID** and the app's **Bundle ID**. Only the `.p8` is secret; the other three can go in any message.
- **Never** put the `.p8` (or its text) in git, a chat (WeChat, iMessage, Slack …), an e-mail body, a shared-folder link, a screenshot or an issue / pull request.
- Send it one of these ways: a password manager share that expires and opens once (1Password "Share", Bitwarden Send: 1 view, 1 day), or AirDrop in person. Without those: `zip -e apns.zip AuthKey_<KeyID>.p8`, send the zip, and tell the password over a phone call (never in the same channel).
- Afterwards keep the `.p8` only in your password manager (or delete it). If it may have leaked: **Keys** → the key → **Revoke**, create a new one and hand that over; old devices keep working once the server has the new key.
- Server owner: put it at `secrets/apns_key.p8` (mode 600) on the server, mount it as a docker secret and set `BODY_APNS_KEY_FILE`, `BODY_APNS_KEY_ID`, `BODY_APNS_TEAM_ID`, `BODY_APNS_BUNDLE_ID` (example in `compose.yaml`), then check with `body-engine notify status` and `body-engine notify test --user-id N`. TestFlight builds use Apple's production push service, Xcode runs the sandbox; the app tells the server which one.

#### 4. Create the app in App Store Connect
App Store Connect → **Apps** → **+** → **New App**: platform iOS; name unique across the App Store (e.g. `Watch2Whoop <your name>`); bundle ID the one from step 2; SKU anything. TestFlight only, no App Store release.

#### 5. Build and upload
Do **not** change the version: the tag already has the right `version:` in `pubspec.yaml`, build number included (a
new build needs a new tag from the project owner). TestFlight shows it as "1.0.6 (6)". Upload exactly the tag you were given and tell the owner when it is in TestFlight.
```bash
flutter build ipa --release --dart-define-from-file=config/defaults.json
```
Open `build/ios/ipa/*.ipa` with Apple's **Transporter** and click "Deliver" (or in Xcode: **Product → Archive → Distribute App → App Store Connect → Upload**). `Info.plist` already declares that it uses no non-exempt encryption, so export compliance is not asked again. After 10 to 30 minutes the build becomes "Ready to Test" in TestFlight.

#### 6. Add the owner and family members as testers
**A. The project owner: internal testing (no review, fastest)**
App Store Connect → **Users and Access** → **+**, invite them by their Apple ID email, choose the role with the fewest permissions (e.g. "Customer Support") and tick only this app → after they accept, under **TestFlight → Internal Testing** create a group, add them, and tick "automatic distribution". Internal testers become members of your team (they can see this app's App Store Connect page), so this is only suitable for the project owner.

**B. Family members (recommended): external testing, invited by email**
1. **TestFlight → External Testing** → **+** to create a group (e.g. "Family") and tick "automatic distribution".
2. The first time you add a build to an external group you must fill in the "Test Information" and submit it for **Beta App Review** (usually within 1 day):
   - Description: "Watch2Whoop syncs Apple Health data to a server the user deploys themselves, and shows recovery score, sleep, workouts and more. Accounts are invited by the server administrator."
   - If review needs sign-in information: ask the project owner to generate a **demo** setup link (a test account with deletable data), put it in the "Review Notes", and have the project owner revoke it once review is done.
3. After approval, under **Testers → +** in the group, enter each family member's Apple ID email. They receive an email: first install the **TestFlight** app on the iPhone, then tap "View in TestFlight" → "Accept" → "Install" in the email. After that, they finish setup with the setup QR code from the project owner.
4. Later builds: a new build under the same version number usually needs no new review; changing the version number (`1.0.0` → `1.1.0`) goes through Beta review again.
- You can also turn on a "public link", but anyone with the link can install (it is useless without a setup QR code, though); inviting family by email is safer.

#### 7. Maintenance
- **TestFlight builds expire after 90 days**: before that, ask the project owner for a new tag (they bump to the next
  patch version and build number in a PR, e.g. 1.0.6+6 → 1.0.7+7, and tag it; you get a new Release link), then do steps 1 and 5 again. The Bundle ID stays the same; after family members update, their data, login and sync progress are kept, and TestFlight reminds them to update.
- To follow a new version: get the new Release (step 1 A, or `git fetch --tags && git checkout <tag>` in your existing clone) → `flutter pub get` → `cd ios && pod install` → step 5. Keep your local `Watch2Whoop.local.xcconfig` and `config/defaults.json` (they are not in git).

#### What changed in 1.0.0+2 (self-service sign-up)
- New first screen: Sign in with Apple / e-mail code → invite code → Health. Existing setup links / QR codes keep working ("I have a setup link").
- Needs the server version with the self-service account API (`/v1/auth/*`, `/v1/account/*`); ask the server owner before uploading.
- A new TestFlight build is required (build number already bumped to `+2` in `pubspec.yaml`; bump it again for every further upload).
- Beta App Review: the reviewer can sign in with Apple or e-mail; provide an invite code in the Review Notes (ask the server owner for a one-off code). Accounts can be deleted inside the app (Today page → 我 → 删除账号), as App Review guideline 5.1.1(v) requires.

### System limits (the app also shows a one-line hint)
- **iOS does not let apps read health data while the phone is locked**: sync pauses and continues after unlock.
- **After the app is swiped away from the background it does not sync automatically** (iOS no longer wakes a force-quit app); it catches up the next time it is opened.
- The timing of background sync is decided by iOS (HealthKit delivery + BGTask) and can be delayed by tens of minutes.
- Wrist temperature and HRV RMSSD (iOS 27) are not yet supported by SDK 0.15.
- There is no service worker in WKWebView, so the Today page inside the app has no offline cache.

### Development
```bash
flutter test                       # unit tests: configuration, address validation, setup info / links, login codes, sync log statistics, invitation code redemption
flutter analyze
flutter build ios --simulator --debug
xcrun simctl install booted build/ios/iphonesimulator/Runner.app
xcrun simctl openurl booted "watch2whoop://setup?today=…&ow=…&code=…&login=…"   # simulates a "Camera scan"
```
- The simulator can use `http://localhost:<port>` (plain http is only allowed for localhost / LAN, see `checkServerUrl`); a real device needs https.
- Logging in to the Today page in the simulator: WKWebView does not keep `Secure` cookies on http://localhost. The local Body Engine dev server needs `BODY_WEB_INSECURE_LOCAL_COOKIE=1` (see "Today page" (「今天页」) in `body_engine/README.md`); for login-code tests use a separate development database (`BODY_DB_NAME=…`) and do not run unmerged migrations against the production database.
- The simulator has no camera, so in-app scanning can only be tested on a real device; test setup links with `simctl openurl`.
- The debug build's "phone sync" page has "Write test data to the simulator's Health app" at the bottom (compiled only under `DEBUG && simulator`). Use a dedicated OW test user for testing (`invite.py --create-user`) and delete it afterwards.
- When the path contains Chinese characters, the `flutter analyze` analysis server crashes (a known Dart LSP issue); copy the project to a pure-English path and run it there.
- Android: see the next section; Android code is gated on `isAndroid` (`lib/src/platform_features.dart`), so iOS behaves as before.

### Android (Huawei phones on HarmonyOS 4.x and older, installed as an APK)
The first Android build (#253) is for family members with Huawei phones that run HarmonyOS 4.x or older (Android
compatible, **no Google services**). It is installed directly from an APK, not from a store. HarmonyOS NEXT (5+) has no
Android runtime and needs its own app later ([docs/apps.md](../../docs/apps.md)). Family install guide (Chinese, one
page): [android-install.zh-CN.md](android-install.zh-CN.md).

| Works on Android | Not yet |
|---|---|
| E-mail sign-in, invite / circle codes, setup QR code (in-app scanner, bundled ML Kit without Google services), pasted setup text, `watch2whoop://` links | Phone health sync: the sync page shows 「华为手表数据接入开发中」; reading Huawei watch data is #253 |
| Today page (all tabs), the AI chat with hold-to-talk (RECORD_AUDIO asked on first use; no microphone → the page keeps the keyboard's dictation key) | Push notifications (no FCM without Google services) |
| Photos / videos for the AI and the meal log: system picker with the camera next to it, the camera directly for camera buttons (`MainActivity.kt`) | Sign in with Apple (iOS only) |
| Chinese / English following the system or the profile, back button (page history first) | |

Requirements: Android 10 / HarmonyOS 2 or newer (`minSdk 29`, the Open Wearables plugin's minimum), 32- or 64-bit ARM.
No server is compiled in: on first start the person scans the setup QR code or pastes the setup link (an admin makes
it with `invite.py` / the onboarding tools, as for iOS). The app keeps no cloud backup (`allowBackup=false`).

**Building: GitHub Actions only, no Android SDK on a Mac needed** (installing the SDK means accepting Google's licence).
Workflow [`android-apk.yml`](../../.github/workflows/android-apk.yml) builds `flutter build apk --release` on ubuntu:
- every pull request that changes this app: a test APK as an artifact of the CI run (job "Android APK"), signed with the
  runner's throw-away debug key (`…-debug-signed.apk`) — fine for a test phone, **not for family** (the properly signed
  APK cannot update it; uninstall first);
- Actions → "Android APK" → Run workflow (any branch): the same, signed with the release key when the secrets are set;
- a release: add a `## Android X.Y.Z — date` section to [CHANGELOG.md](CHANGELOG.md) (the version is the shared one in
  `pubspec.yaml`), then `tools/app_release.sh android --push`. The tag `app-android-vX.Y.Z` publishes the source
  pre-release as for iOS, and `app-release.yml` builds the signed APK from the tag and attaches it and its `.sha256` to
  that release. A tag build fails without the release key.

**Signing key (the project owner, once).** Every APK must be signed with the same key, or it cannot install over the
previous one. `keytool` comes with any JDK (no Google licence):
```bash
keytool -genkeypair -v -storetype PKCS12 -keystore watch2whoop-release.jks -alias watch2whoop \
  -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=Watch2Whoop"
# PKCS12 uses one password for the store and the key: give the same one to both secrets below
gh secret set W2W_ANDROID_KEYSTORE_BASE64 --repo <owner>/<repo> < <(base64 < watch2whoop-release.jks)
gh secret set W2W_ANDROID_KEYSTORE_PASSWORD --repo <owner>/<repo>     # prompts for the password
gh secret set W2W_ANDROID_KEY_PASSWORD --repo <owner>/<repo>          # the same password
gh secret set W2W_ANDROID_KEY_ALIAS --repo <owner>/<repo> --body watch2whoop
```
Keep the `.jks` and its password in your password manager and delete other copies; never commit them (`*.jks`,
`*.keystore` and `Watch2Whoop.local.properties` are git-ignored). Losing the key means every family member has to
uninstall and set up again for the next update. Pull requests never get the secrets (forks do not, and `ci.yml` does not
pass them), so only maintainers' manual runs and tags produce release-signed APKs.

**Application ID.** The repository holds the placeholder `org.watch2whoop.app` (`android/Watch2Whoop.properties`). To use
your own, set the repository *variable* (not a secret) `W2W_ANDROID_APPLICATION_ID`, or put it in
`android/Watch2Whoop.local.properties` (copy the `.example`) for a local build. Decide before the first family install:
a different ID is a different app (installed next to the old one, login lost).

**Local build (optional).** Needs the Android SDK (Android Studio or the command-line tools; accept its licence
yourself). `cp android/Watch2Whoop.local.properties.example android/Watch2Whoop.local.properties`, fill it in, then
`flutter build apk --release --target-platform android-arm,android-arm64` (add `--dart-define-from-file=config/defaults.json`
for a build with your servers pre-filled; never for the public APK).

License: not chosen yet (the repository is private for now; the owner decides before open-sourcing). The OW SDK is MIT-licensed.

---

## 中文步骤

以下是给帮忙编译上传 TestFlight 的朋友看的中文步骤（与上面英文部分内容一致）。

### 0. 需要准备
- 付费的 Apple Developer Program 账号（免费账号不能用 TestFlight）。
- Mac + Xcode 26 或更新（Xcode → Settings → Components 里装好 iOS 平台）。
- Flutter **3.47.7**（`brew install --cask flutter`，`flutter --version` 确认）、CocoaPods 1.17（`brew install cocoapods`）。
- `xcode-select -p` 显示 `/Library/Developer/CommandLineTools` 时运行一次 `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`（必须：有个依赖的原生构建脚本会调 `xcrun --sdk iphoneos`，不设会报「Bad state: No element」）。

### 1. 拿代码——只从 tag 的 GitHub Release 拿，不用 `main`，也不用别人手发的 zip
每个商店构建都对应项目所有者打的一个 tag，tag 只写版本号，例如 `app-ios-v1.0.6`；构建号（例如 6）只在 `pubspec.yaml` 里：`version: 1.0.6+6`。以前的 tag 名字里还带构建号（`app-ios-v1.0.5+5`）。tag 推上去后，GitHub Actions
会在仓库的 **Releases** 页面自动发布同名的 **pre-release**：附带这个目录的源码 zip、它的 `.sha256` 和改动说明
（[CHANGELOG.md](CHANGELOG.md)）。项目所有者会把 tag 和 Release 链接发给你。`main` 上可能有没做完的改动和没发布过的
版本号；其他来源的 zip 都不能拿来编译。

**A. 用 Release 里的 zip**（不需要 git）：
```bash
gh release download app-ios-v1.0.6 --repo <owner>/<repo> --dir w2w-1.0.6   # 或者在 Release 页面下载那两个文件
cd w2w-1.0.6
shasum -a 256 -c watch2whoop-ios-app-v1.0.6.zip.sha256   # 必须显示 OK
unzip -z watch2whoop-ios-app-v1.0.6.zip | tail -1        # 必须是 Release 页面上写的那个 commit
unzip -q watch2whoop-ios-app-v1.0.6.zip && cd watch2whoop-ios-app-v1.0.6/apps/watch2whoop
```
**B. 用 git：**
```bash
git clone --branch app-ios-v1.0.6 --depth 1 <repository URL> watch2whoop-src
cd watch2whoop-src/apps/watch2whoop
git describe --tags --exact-match        # 必须显示给你的那个 tag
```
两种方式之后都一样：
```bash
grep '^version:' pubspec.yaml            # 1.0.6+6：+ 前面和 tag 一致，+ 后面是构建号；不要改
flutter pub get
cd ios && pod install && cd ..
grep -A1 "OpenWearablesHealthSDK (" ios/Podfile.lock   # 应该是 0.15.x，不能是 0.14
```

### 2. 填你自己的 Bundle ID、Team ID 和默认服务器（都不进 git）
```bash
cp ios/Flutter/Watch2Whoop.local.xcconfig.example ios/Flutter/Watch2Whoop.local.xcconfig
open -e ios/Flutter/Watch2Whoop.local.xcconfig
cp config/defaults.example.json config/defaults.json
open -e config/defaults.json
```
- `W2W_BUNDLE_ID`：你的套装 ID，例如 `com.<你的名字>.watch2whoop`（全球唯一，上传后不能改）。
- `W2W_TEAM_ID`：developer.apple.com → Account → Membership details 里的 10 位 Team ID。
- `W2W_DISPLAY_NAME`：主屏幕上的名字，默认 Watch2Whoop。
- `config/defaults.json`：示例里已经是我们的公网服务器（今天页 `https://body.example.com`、同步 `https://sync.example.com`），直接复制就行。**旧的 `config/defaults.json` 要删掉 `W2W_SYNC_DAYS_BACK` 那一行**（旧示例里是 `"90"`，会让首次同步又读 90 天；没有这行时 app 用 `lib/src/sync_params.dart` 里的 14 天默认值）。自己部署服务器的人改成自己的域名。设置二维码里也带地址，所以这一步只决定「手动填写」时预填什么。

**不要**在 Xcode 的 Signing 页面里改 Team / Bundle ID（会写进工程文件）；改上面的文件就行。

### 3. 用 Xcode 确认签名和能力（第一次做一次）
1. `open ios/Runner.xcworkspace`（注意是 `.xcworkspace`）。
2. **Runner** target → **Signing & Capabilities**：勾「Automatically manage signing」，Team 应该已经是你的；能看到 **HealthKit**（勾着 Background Delivery）和 **Background Modes**（Background fetch、Background processing）——来自仓库，不用自己加。
3. 提示要注册 App ID 时点「Register」/「Try Again」，Xcode 会建好带 HealthKit 的 App ID。
4. **通过 Apple 登录**（1.0.0+2 起需要；权限 `com.apple.developer.applesignin` 已经写在 `ios/Runner/Runner.entitlements` 里）：
   - developer.apple.com → **Certificates, Identifiers & Profiles** → **Identifiers** → 点这个 app 的 Bundle ID → 勾 **Sign In with Apple**（选「Enable as a primary App ID」）→ **Save**。
   - 更新描述文件（provisioning profile）：勾了「Automatically manage signing」时 Xcode 下次编译会自动更新（还报错就把这个勾去掉再勾上，或 Xcode → Settings → Accounts → Download Manual Profiles）；手动签名的话给这个 App ID 重新生成 App Store 描述文件并下载。
   - 把你的 Bundle ID 告诉服务器管理员：服务器的 `BODY_APPLE_AUDIENCES` 必须和它一字不差，否则 Apple 登录会被拒（401）。
   - 之后在 Signing & Capabilities 里也能看到 **Sign in with Apple**。

5. **推送通知**（1.0.7 起需要；权限 `aps-environment` 已经写在 `ios/Runner/Runner.entitlements` 里）：
   - developer.apple.com → **Certificates, Identifiers & Profiles** → **Identifiers** → 点这个 app 的 Bundle ID → 勾 **Push Notifications** → **Save**。如果提示创建证书（Certificates），跳过：服务器用的是 key（下一条），不用证书。
   - 和「通过 Apple 登录」一样更新描述文件（自动签名下次编译会自动更新）。之后在 Signing & Capabilities 里能看到 **Push Notifications**（没有的话点 **+ Capability** → Push Notifications）。
   - 生成 APNs key（每个团队做一次，一个 key 所有 app 都能用）：**Keys** → **+** → 名字例如 `Watch2Whoop push` → 勾 **Apple Push Notifications service (APNs)** → 如果要选，环境选 **Sandbox & Production**、选 **Team Scoped (All Topics)** → **Continue** → **Register** → **Download** 下载 `AuthKey_<KeyID>.p8`（Apple **只让下载一次**）。记下 **Key ID**（10 位，key 页面上有）和你的 **Team ID**（Membership details 里）。
   - 这个 key 给服务器用，不放进 app，编译不需要它。按下面「APNs key 怎么交给服务器管理员」交出去。

### APNs key 怎么交给服务器管理员（推送用）
服务器管理员需要四样：`.p8` 文件、它的 **Key ID**、你的 **Team ID**、app 的 **Bundle ID**。只有 `.p8` 是机密，另外三样可以随便发。
- `.p8`（或里面的文字）**绝对不要**放进 git、聊天（微信、iMessage、Slack……）、邮件正文、共享文件夹链接、截图、issue / PR。
- 用下面任一种方式交：密码管理器的限时分享（1Password「共享」、Bitwarden Send：只能打开 1 次、1 天过期），或者见面时 AirDrop。都没有的话：`zip -e apns.zip AuthKey_<KeyID>.p8` 加密压缩后发压缩包，密码**打电话**告诉对方（不要和压缩包走同一个渠道）。
- 交完以后 `.p8` 只留在你的密码管理器里（或者删掉）。怀疑泄露时：**Keys** → 这个 key → **Revoke**，再生成一个新的按同样方式交；服务器换上新 key 后，已有的手机照常收推送。
- 服务器管理员：放到服务器的 `secrets/apns_key.p8`（权限 600），挂成 docker secret，设好 `BODY_APNS_KEY_FILE`、`BODY_APNS_KEY_ID`、`BODY_APNS_TEAM_ID`、`BODY_APNS_BUNDLE_ID`（`compose.yaml` 里有示例），然后用 `body-engine notify status`、`body-engine notify test --user-id N` 检查。TestFlight 的构建走 Apple 正式推送环境，Xcode 直接运行的走沙盒环境；app 会告诉服务器用哪个。

### 4. 在 App Store Connect 建 app
App Store Connect → **App** → **+** → **新建 App**：平台 iOS；名称全 App Store 唯一（例如 `Watch2Whoop <你的名字>`）；套装 ID 选第 2 步那个；SKU 随便填。只走 TestFlight，不上架。

### 5. 编译和上传
**1.0.0+2 的变化（自助注册）**：首屏换成「通过 Apple 登录 / 用邮箱登录 → 输入激活码 → 健康权限」，原来的设置链接 / 二维码仍可用（首屏「我有设置链接」）；需要服务器已经部署了自助账号接口（`/v1/auth/*`、`/v1/account/*`），上传前先问服务器管理员；需要新的 TestFlight 构建（`pubspec.yaml` 的构建号已经改成 `+2`）。Beta 审核时在「审核备注」里给审核员一个激活码（问服务器管理员要），账号可以在 app 里删除（今天页「我」→「删除账号」，审核指南 5.1.1(v)）。

**不要改**版本号：tag 里的 `pubspec.yaml` 已经是正确的 `version:`，构建号也在里面（要新构建就请项目所有者打新 tag）。TestFlight 里显示为「1.0.6 (6)」。上传给你的那个 tag，进 TestFlight 后告诉项目所有者。
```bash
flutter build ipa --release --dart-define-from-file=config/defaults.json
```
用 Apple 的 **Transporter** 打开 `build/ios/ipa/*.ipa` 点「交付」（或 Xcode **Product → Archive → Distribute App → App Store Connect → Upload**）。`Info.plist` 已声明不使用非豁免加密，不会再问出口合规。10–30 分钟后 TestFlight 里变成「可供测试」。

### 6. 把用户和家人加成测试员
**A. 项目所有者本人：内部测试（不用审核，最快）**
App Store Connect → **用户和访问** → **+**，用对方的 Apple ID 邮箱邀请，角色选权限最小的（例如「客户支持」），只勾这个 app → 对方接受后，在 **TestFlight → 内部测试** 新建组、加人、勾「自动分发」。内部测试员会成为你团队的成员（能看到这个 app 的 App Store Connect 页面），只适合项目所有者。

**B. 家人（推荐）：外部测试，按邮箱邀请**
1. **TestFlight → 外部测试** → **+** 新建组（例如「家人」），勾「自动分发」。
2. 第一次给外部组加构建时要填「测试信息」并提交 **Beta 版 App 审核**（通常 1 天内）：
   - 描述：「Watch2Whoop 把 Apple 健康数据同步到用户自己部署的服务器，并显示恢复分、睡眠、训练等。账号由服务器管理员邀请。」
   - 审核需要登录信息时：请项目所有者生成一个**演示用**设置链接（测试账号、数据可删），写在「审核备注」里；审核完让项目所有者吊销。
3. 审核通过后，在组里 **测试员 → +** 输入家人的 Apple ID 邮箱。家人会收到邮件：先在 iPhone 装 **TestFlight** app，再点邮件里的「查看测试」→「接受」→「安装」。之后再用项目所有者给的设置二维码完成设置。
4. 以后的新构建：同一版本号下的新构建一般不用再审；改了版本号（`1.0.0` → `1.1.0`）会再走一次 Beta 审核。
- 也可以开「公开链接」，但任何拿到链接的人都能装（没有设置二维码也用不了），家人用邮箱邀请更稳妥。

### 7. 维护
- **TestFlight 构建 90 天过期**：过期前请项目所有者打一个新 tag（他在 PR 里升一个补丁版本，例如 1.0.6 → 1.0.7，构建号也加 1，合并后打 tag，你会收到新的 Release 链接），再做第 1、5 步。Bundle ID 不变，家人更新后数据、登录、同步进度都保留；TestFlight 会提醒他们更新。
- 跟新版本：拿到新的 Release（第 1 步 A，或在已有目录里 `git fetch --tags && git checkout <tag>`）→ `flutter pub get` → `cd ios && pod install` → 第 5 步。本地的 `Watch2Whoop.local.xcconfig` 和 `config/defaults.json` 不在 git 里，保留即可。

### Android 安装包（华为手机，HarmonyOS 4.x 及更早）
给用华为手机（HarmonyOS 4.x 及更早，兼容安卓、没有谷歌服务）的家人直接装 APK，不经过应用市场。能用的：邮箱登录、设置二维码 / 设置链接、今天页全部页面、Jarvis 对话和按住说话、拍照记餐；暂时不能用：手表数据同步（同步页显示「华为手表数据接入开发中」，#253）、推送、Apple 登录。
- 安装包只在 GitHub Actions 上构建（不用在 Mac 上装 Android SDK）：发布时在 `CHANGELOG.md` 加 `## Android X.Y.Z — 日期` 一节，`tools/app_release.sh android --push`，tag 的 Release 上会自动附上签好名的 APK 和 `.sha256`。
- 签名用固定的 release keystore（生成命令和要设的 4 个仓库 secret 见上面英文 "Signing key"）；没设 secret 时只能出调试签名的测试包，不要发给家人。
- 家人怎么装、怎么扫码登录：[android-install.zh-CN.md](android-install.zh-CN.md)。

## License
MIT — see [LICENSE](LICENSE). The rest of the repository (server side) is AGPL-3.0, see [docs/LICENSES.md](../../docs/LICENSES.md).
