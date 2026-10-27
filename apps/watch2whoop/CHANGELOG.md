# Changelog — Watch2Whoop Flutter app (iOS and Android)

One section per store build, newest first: `## <marketing version> — <date>` (e.g. `## 1.0.6 — 2026-10-22`; the build
number after `+` in `pubspec.yaml` is not part of the public name). Bump `version:` in `pubspec.yaml` (both parts) and
add the section in the same pull request; after it is merged a maintainer tags the commit with
`tools/app_release.sh --push` (`app-ios-v<marketing version>`, `app-android-v<marketing version>`), and the workflow `app-release.yml`
publishes the GitHub pre-release with the source zip; this section becomes its notes. Builds are made only from that
Release (zip checked against its `.sha256`) or `git clone --branch <tag>`, never from `main` or a zip sent by hand.
Versioning rules: [docs/apps.md](../../docs/apps.md#4-versioning-and-releases).
A platform-only section is `## Android <marketing version> — <date>`: `tools/app_release.sh android` takes it before the
shared `## <marketing version>` section, and the iOS release never sees it.

| Version | Tag | Commit | Store |
|---|---|---|---|
| 1.0.8 | `app-ios-v1.0.8` | (set when tagged) | TestFlight (iOS) |
| Android 1.0.7 | `app-android-v1.0.7` | (set when tagged) | APK on the GitHub Release (Android, sideloaded) |
| 1.0.7 | `app-ios-v1.0.7` | (set when tagged) | TestFlight (iOS) |
| 1.0.6 | `app-ios-v1.0.6` | (set when tagged) | TestFlight (iOS) |
| 1.0.5+5 | `app-ios-v1.0.5+5` | `e1a93cc` | TestFlight (iOS) |
| 1.0.0+2 | `app-ios-v1.0.0+2` | `2f4a02f` | TestFlight (iOS), uploaded as build 4; crashes on open (fixed in 1.0.5+5) |
| 1.0.0+1 | (not tagged; source = release `v0.1.0` asset `watch2whoop-ios-app-v0.1.0.zip`) | `42a8f0f` | TestFlight (iOS), sent to the builder 2026-10-18 |

## 1.0.8 — 2026-10-27
这一版主要让「手机同步」更稳、更快、出问题时说得清楚。还在用 1.0.6 的测试用户请直接升到 1.0.8（1.0.7 的修复也都在里面）。

**同步**
- **第一次同步只读最近 14 天**（以前是 90 天）：几分钟就传完，今天页很快就有数据，不会再出现「首次同步好几天、新数据一直进不来」的情况。已经在用的测试用户保持原来的范围。
- **「补全历史」**：在今天页或「手机同步」页选「近 90 天」或「全部历史」，app 先照常传最新的数据，再在后台补更早的历史；app 切到后台、被系统挂起也会继续传，中途被打断（包括 app 被关掉）下次从断点接着传，不会重复，进度在页面上显示。手机空闲、连着 Wi-Fi 时补得最快，不用蜂窝流量。
- 今天页上的「一键开启同步」：点一下，app 自己连接同步、弹出「健康」授权、马上开始第一次上传，卡片上显示进度（首次同步时显示百分比）。登录时点了「跳过」也没关系，之后从今天页一步开启。
- 今天页上的「立即上传」「去设置」「去授权」直接在 app 里完成，不用再跳到「手机同步」页。
- 新用户确认同意书后，今天页马上出现「开启数据同步」卡片和弹窗（以前要等下一次刷新才出现）。
- 同步连接失效时 app 会自己重新连接，并且**重新连上后马上同步一轮**（以前要等下次打开 app）。
- 如果「健康」授权窗口没有弹出来（以前偶尔会被别的窗口挡住），app 会发现并再弹一次。
- app 会把手机这边影响同步的状态告诉服务器：后台 App 刷新是否打开、低电量模式、锁屏读不到健康数据、哪些健康类型还没授权、上次同步和上次成功上传的时间。今天页因此能说出具体原因，比如「后台刷新被关」「手机锁着」，而不只是「很久没收到」。不包含任何健康数值。
- 「手机同步」页把「只收到 iPhone 的数据、没有手表数据」（多半是手表没配对 / 没戴，或者没授权心率和睡眠）和「同步断了」分开显示。
- 网络很差（连着 Wi-Fi 但上不了网、VPN 丢包）时，打开 app 不会再卡好几分钟；没传完的下次打开接着传。
- 即使后台同步还没连上，逐拍心率、心电图和「站立时间」等 app 自己读的数据，打开 app 时也会照常上传。

**Jarvis（AI 助手）**
- 按住说话改用这台 iPhone 上的语音识别：边说边出字；中文松手后再交你自己的服务器定稿，更准。识别在手机本机完成，也不保存录音。第一次使用会弹「语音识别」权限。
- 按 Jarvis 键时有轻微震动反馈。

**登录**
- 今天页里的「通过 Apple 登录」和「我 → 登录方式 → 绑定 Apple 登录」在 app 里可以直接用了。

**技术说明（给开发者）**
- 页面同步协议见 `docs/sync-onboarding-app-requirements.md`（`sync-connect` / `sync-now` / `app-settings` / `health-settings`，`BodySyncStatus.onApp` 进度，`PUT /v1/ingest/sync-device` 手机状态）；另有 `Watch2WhoopSync.enableSync / syncNow / syncStatus`（`lib/src/sync_bridge.dart`）。
- OW 登录失效改用 SDK 的 `authErrorStream`（日志匹配只作兜底）；RawSync 的账号 token 被 401 时改用今天页签发的只上传 token；status 请求 10 秒超时、每轮最多一次（#295 A2 / A3）。
- 自己打包时旧的 `config/defaults.json` 要删掉 `"W2W_SYNC_DAYS_BACK"` 那一行（旧示例里是 90，会覆盖 14 天的默认值）；`config/defaults.example.json` 已去掉这一行。
- 首次范围和补传参数在 `lib/src/sync_params.dart`（`kFirstSyncDays = 14`、`kBackfillDays = 90`）。补传：今天页「补全历史」按 `docs/sync-onboarding-app-requirements.md` 第 10 节——宣布 `Watch2WhoopFeatures.backfill = true`，页面建好服务器的重新同步任务后打开 `watch2whoop://backfill?days=90|all`，app `GET /v1/ingest/resync` 是 `phone` 时做手机部分、`PUT` 进度（马上报第一次）、完成时 `phone_done`，并用 `onApp({kind: "backfill", …})` 通知页面（`lib/src/backfill.dart`、`resync_flow.dart joinPageBackfill`）；服务器开了自建引擎（shadow / app）时由原生 `ios/Runner/Backfill.swift` 按天往回补：后台 URLSession 上传、BGProcessingTask `watch2whoop.sync.backfill`、游标 200 后才前移、同一批重发用同一个 batch_id；开关关着时走 Open Wearables SDK 自己的全量导出（重置锚点后最新的先传，SDK 自己的后台任务续传；导出期间新数据跟着导出一起上来，没有单独的增量轮次）。
- 自建同步引擎 A1（#219，`lib/src/sync_engine.dart`、`ios/Runner/SyncEngine.swift`、`assets/healthkit_types.json`）：服务器开关 `apple_sync_engine` 默认关，关着时不读、不传。
- 本机语音识别 `ios/Runner/VoiceInput.swift`（#53）；Apple 登录桥 `lib/src/apple_bridge.dart`；新增权限说明 `NSSpeechRecognitionUsageDescription`。
- 依赖：`wakelock_plus` 1.8.2、`webview_flutter_wkwebview` 3.27.2；`ios/Podfile.lock` 与 `pubspec.lock` 一致（CI「iOS Podfile.lock」检查）；`.github/dependabot.yml`。
- 推送（#175）的 app 侧在 1.0.7 已经做好；APNs key 交给服务器后才会真正推送，没有 key 不影响构建和其他功能。

## Android 1.0.7 — (set when tagged)
First Android build (#253), for Huawei phones on HarmonyOS 4.x and older (Android compatible, no Google services),
installed as an APK from the GitHub Release. Same code base and version as iOS 1.0.7 plus the Android target; the iOS
app is unchanged. Needs Android 10 / HarmonyOS 2 or newer (32- or 64-bit ARM).
- Works: e-mail sign-in, setup QR code (in-app scanner) / pasted setup text / `watch2whoop://` links, invite and circle
  codes, the Today page with all tabs, the AI chat (Jarvis) with hold-to-talk (microphone asked on first use; without a
  microphone the page keeps the keyboard's dictation key), photos and videos for the AI and the meal log (gallery, or
  the camera for the camera buttons), Chinese / English following the system or the profile, the Android back button
  (back in the page first).
- Not yet: phone health sync (the sync page says 「华为手表数据接入开发中」 / "Huawei watch data support is in
  development"; how to read Huawei watch data is #253), push notifications (no FCM without Google services), Sign in
  with Apple (iOS only). A setup link's sync invitation code is left unused.
- No server is compiled in: the first start asks for the setup QR code / link. No cloud backup of the app's data.
- Built by GitHub Actions (`.github/workflows/android-apk.yml`), signed with the project's fixed release key so later
  APKs install over it.

## 1.0.7 — 2026-10-22
Sync center (rollout flag `sync_center`, server side in #221) and the Health types the Open Wearables SDK cannot read.
- Fix: stand time, exercise minutes, time in daylight, physical effort, sleeping wrist temperature, environmental /
  headphone sound, walking heart rate, stair speed and walking steadiness stopped reaching the server once people synced
  only through the app (OW SDK 0.15 has none of them). RawSync now reads them too (registry `lib/src/hk_types.dart`, the
  same table as the server's `rawdata/hk_types.py`, checked by tests on both sides), each type with its own HealthKit
  anchor, and uploads them as `quantities[]` to `POST /v1/ingest/healthkit`, only the types the server lists and only
  while it takes them (emergency flag `app_types_pause`: the anchors stay and the samples go later). They are asked for in
  the second, small Health sheet, which shows once more for people who already answered it (automatic asks still stop
  after 3). Not behind a flag.
- Sync center (flag on; the Today page's 「我」 then has one row 「同步中心」 / "Sync center" instead of 手机同步 / 数据更新 /
  数据状态, opened as `watch2whoop://sync-center`; the app tells the page `Watch2WhoopFeatures.syncCenter`):
  - 「立即重新同步」 / "Re-sync now" with 7 / 30 / 90 days, all, or a custom number of days (1–3650): the OW SDK stops,
    clears its anchors and exports that range again (`startBackgroundSync(syncDaysBack:)` + `resetAnchors()`, checked in
    its 0.15 source; every other sync of the app keeps that range until the export is done, then the usual one is stored
    again); RawSync reads every kind of the range again with a separate set of anchors (the regular ones do not move); the
    server then re-imports and re-scores the range. Progress and result on the page; all history asks first and warns
    about time and battery; the screen stays on while it runs there; a closed app continues on the next open.
  - Per data type the newest sample on the phone next to the newest on the server: 「没采集」 / "Not recorded" (none on the
    phone, or no permission), 「没同步」 / "Not synced", 「已同步」 / "Synced"; VO₂ max notes that it is only made outdoors.
    The row shows `phone / server` dates in the order of the group title (with the words, the server date was cut off on
    a phone).
  - Strength-training sources (#210) and when the server data was last updated; everything else of Phone sync stays.
- Flag off: the page is exactly as before.
- Fix (simulator end-to-end test): before sync is connected, 「允许读取健康数据」 / "Allow Health data" and 「重新申请健康权限」
  / "Ask for Health access again" showed the raw `NotConfiguredException` and asked for nothing (the Open Wearables SDK is
  only configured by connecting). They now ask for the app's own types; connecting asks for the rest, as before.
- Debug builds only: the simulator test-data button also writes the new types HealthKit lets an app write (daylight,
  physical effort, sound levels, stair speeds); the Apple-computed ones (stand, exercise minutes, sleeping wrist
  temperature, walking heart rate, walking steadiness) can only be checked on a device.

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

Push notifications (#175; server side: the notification module `notify/` and the Today page bell, rollout flag
`notifications`). **The builder has to switch on Push Notifications for the Bundle ID once** (README, step 3, item 5);
the APNs key goes to the server owner, never into the app or git.
- New capability Push Notifications (`aps-environment` in `ios/Runner/Runner.entitlements`; Xcode signs TestFlight builds
  as `production`). No new pod or Dart package: the native side is `PushBridge` in `AppDelegate.swift` (channel
  `watch2whoop/push`), the Dart side `lib/src/push.dart`.
- The app never asks at launch: the Today page asks it the first time someone opens the Friends tab or the notification
  settings (`push:ask`); iOS shows its dialog once. Once allowed, the device token is registered for the signed-in
  account (`POST /v1/users/me/push-devices`) on every start (sent again when it changes or after a day); sign-out removes
  it before the account token is revoked.
- Tapping a notification opens its page in the Today page (a path on the same server only; anything else opens the
  notification list). Notifications that arrive while the app is open show as a banner and refresh the page's unread
  count; the app icon badge follows the page's unread count.
- Works with older servers: without the notification module nothing is asked or registered (the page never sends
  `push:ask`), and a 404 from the registration is ignored.

Hold-to-talk with our own speech recognition (#171): inside the app 🎤 on the Jarvis chat pointed to the keyboard's
dictation, because the WebView never let the Today page use the microphone.
- The app answers WebKit's media capture request (`WKUIDelegate`, iOS 15+): the microphone only, and only for the Today
  page's own origin (not the kept offline copy, never cameras or other sites); then tells the page
  `window.Watch2WhoopFeatures.mic`. The page's existing recorder works as on the web: hold to talk, release to recognise
  on the user's own server (`POST /v1/assistant/transcribe`, local SenseVoice, audio not kept, the same daily limit), the
  text goes into the box to be checked before sending. Nothing goes to Apple's or any other speech service.
- iOS asks for the microphone the first time 🎤 is held (`NSMicrophoneUsageDescription` now names hold-to-talk). When it
  was declined the page shows 「麦克风没打开」 / "The microphone is off" with 「打开设置」 / "Open Settings" (this app's
  page in iOS Settings).
- The permissions step no longer says the app never uses the microphone.
- Older apps (1.0.6 and before) do not set the flag, so the page keeps pointing them to the keyboard's dictation.

Fix: no app ever uploaded a beat-to-beat HRV reading, ECG or drink (the server had 0 rows from the app). Reproduced in the
simulator:
- The second, small Health sheet (beat-to-beat, ECG, water) was asked for 10 ms after the Open Wearables sheet closed.
  iOS cannot present a sheet while another one is being dismissed ("Attempt to present
  HKHealthPrivacyHostAuthorizationViewController … whose view is not in the window hierarchy"): the sheet never came up
  and HealthKit never answered, so the permissions step could spin forever. The app had already noted the ask as done
  and never asked again.
- Those types therefore stayed undetermined; every upload pass failed reading them ("Authorization not determined",
  HealthKit error 5) and the error ended the whole pass silently, before any request reached the server.
- Now the native side waits until nothing is presented before asking, and answers instead of waiting when no sheet came
  up. Whether to ask is what HealthKit says (`rawAuthStatus`), not a flag of our own: people stuck in this state see the
  sheet once after the next sync round on open (automatic asks are counted and stop after 3, so a HealthKit crash cannot
  repeat on every open).
- A kind that cannot be read no longer ends the pass: the other kinds still go, and the reason shows on the sync page.
- Phone sync → 「心跳间期、心电图和饮水」 / "Beat-to-beat HRV, ECG and water": 「上次上传」 / "Last upload" now also shows how
  many readings that upload added (before this phone's first upload, the server's last accepted batch of the account),
  plus 「上次检查」 / "Last check". When iOS never asked, 「允许读取」 / "Allow reading" shows the sheet and uploads right
  away. Each pass also writes a line to the sync log.

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
