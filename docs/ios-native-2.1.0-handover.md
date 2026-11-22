# iOS 2.1.0 交接单

> 按 [`ios-native-1.0.9-handover-template.md`](ios-native-1.0.9-handover-template.md) 的结构写的 2.1.0 版。发布当天由中控复制一份到私有的 `dist/交接单-iOS-2.1.0.md`，把尖括号里的值填上再发。这份公开文件里不写任何人的名字、Apple ID、Team ID、Bundle ID、域名。
> 和 2.0.0 最大的不同：**iPhone app 里嵌着 Apple Watch app**（target `Watch2WhoopWatch`）和它的**表盘小组件扩展**（target `Watch2WhoopWatchWidgets`）。一次上传三个都带上，但开发者账号里要多注册 **2 个 App ID 和 1 个 App Group**，Xcode 要装 **watchOS 平台**。iPhone 这边的签名、能力和 2.0.0 一样。
> 以下的 target、Bundle ID 后缀、能力都以 `apps/ios/project.yml` 为准（各 target 的 `entitlements` / `info`），和这份不一致时以它为准。

## 给编译上传的朋友（可以整段转发）

你好！Watch2Whoop **2.1.0** 可以打包了。这一版 iPhone 界面重做，并且**第一次带上 Apple Watch app**（嵌在 iPhone app 里，一次上传）。iPhone 部分和 2.0.0 一样；手表部分第一次要在开发者账号里多注册两样东西，大概多花 20 分钟。测试用户那边不用做任何事：TestFlight 覆盖更新，不用重新登录；有 Apple Watch 的人在手表上自动装上（或在 iPhone 的「Watch」app 里点安装）。

**源码**（只从这个 Release 拿，不要用 main 或别人发的 zip）
- Release：仓库 Releases 页面里的 `app-ios-v2.1.0`（名字「iOS app 2.1.0」）：<Release 链接>
- commit：`<commit>`　源码 zip 的 sha256：`<sha256>`
- 版本号 **2.1.0**、构建号 **12** 已经写在 `apps/ios/Config/Watch2Whoop.xcconfig` 里，三个 target 共用，不用改，也不要在自己的配置里覆盖（三个不一样 App Store Connect 会拒收）。TestFlight 里应该显示「2.1.0 (12)」。

**需要准备**
- Xcode 26（iOS 26 SDK）**加 watchOS 平台**：Xcode → Settings → Components → watchOS 26.x 点下载（几个 GB）。没装的话连 iPhone app 都编不过（它依赖手表 app）。
- XcodeGen **2.46.0**：`xcodegen --version` 应该打印 `Version: 2.46.0`。
- 第一次打开工程要联网一次：Swift Package Manager 下载 GRDB 7.11.1（唯一的第三方库）。

**步骤**
1. 下载 Release 的 zip，校验：`shasum -a 256 -c watch2whoop-ios-app-v2.1.0.zip.sha256` 打印 OK，`unzip -z watch2whoop-ios-app-v2.1.0.zip` 打印上面的 commit。解压后除了 `apps/ios/`，还有 `packages/openbodyscore-js/`（算分的算法包、测试用的 golden 文件）和 `body_engine/…/muscle-body-data.js`（肌肉图），是 app 要打进去的资源，**目录结构不要动**。
2. 签名配置（不进 git）：把你 2.0.0 用的 `apps/ios/Config/Watch2Whoop.local.xcconfig` 复制到新解压的同一位置（没有就 `cp Config/Watch2Whoop.local.xcconfig.example Config/Watch2Whoop.local.xcconfig` 再填）。
   - `W2W_BUNDLE_ID`、`W2W_TEAM_ID`、`W2W_DISPLAY_NAME`、`W2W_DEFAULT_HOST`、`W2W_LEGAL_BASE_URL`：和 2.0.0 一样，不变。
   - `W2W_APP_GROUP`（新的，手表 app 和表盘小组件共用的 App Group）：**一般不用写**，默认是 `group.<iPhone 的 Bundle ID>.watch`。只有你的账号里这个名字已经被占用、想换一个时才写，写了就用你写的那个名字去注册（下一步）。
   - 不要写 `W2W_MARKETING_VERSION` / `W2W_BUILD_NUMBER`。
3. **开发者账号（第一次做一次）**：developer.apple.com → Certificates, Identifiers & Profiles。用 Xcode 自动签名的话第 5 步 Xcode 会替你注册，这一步只在它报错时手动补：

   | 是什么 | 标识 | 类型 | 要勾的能力 |
   |---|---|---|---|
   | iPhone app（已有） | `<iPhone 的 Bundle ID>` | App ID（已有，**不改**） | HealthKit、Sign in with Apple、Push Notifications（和 2.0.0 一样）。**不要**给它勾 App Groups |
   | 手表 app（新） | `<iPhone 的 Bundle ID>.watchkitapp` | Identifiers → App IDs → App | **HealthKit**、**App Groups**（勾下面这个组） |
   | 表盘小组件（新） | `<iPhone 的 Bundle ID>.watchkitapp.widgets` | Identifiers → App IDs → App | **App Groups**（勾同一个组），别的都不用 |
   | App Group（新） | `group.<iPhone 的 Bundle ID>.watch`（或你在第 2 步写的） | Identifiers → App Groups | — |

   - 手表 app 训练中手腕放下也继续计时，用的是 Background Modes 里的 **Workout processing**：它写在手表 app 的 Info.plist 里（`WKBackgroundModes`），不是 App ID 上的能力，不用在网页上勾。
   - **描述文件**：自动签名时 Xcode 自己生成三份（iPhone、手表、小组件）。手动管理的话，给两个新 App ID 各建一份 App Store 描述文件；iPhone 那份能力没变，**不用重新生成**。改了某个 App ID 的能力（比如给手表补勾 App Groups）以后，那个 App ID 的描述文件要重新生成（自动签名时在 Xcode 里点「Try Again」）。
4. 生成工程：`cd watch2whoop-ios-app-v2.1.0/apps/ios && xcodegen generate`，然后 `open Watch2Whoop.xcodeproj`（是 `.xcodeproj`，没有 `.xcworkspace`）。
5. **Signing & Capabilities**：**三个 target 都要看一遍**（左边 TARGETS 里依次选 Watch2Whoop、Watch2WhoopWatch、Watch2WhoopWatchWidgets），每个都勾「Automatically manage signing」、Team 是你的。不要在这个页面改 Team / Bundle ID（改第 2 步的文件）。应该看到的能力（都来自工程配置，不用自己加）：
   - **Watch2Whoop**（iPhone，和 2.0.0 一样）：HealthKit（勾着 Background Delivery）、Sign in with Apple、Push Notifications、Background Modes（Background fetch、Background processing、Remote notifications）。**没有** App Groups、Keychain Sharing，也不要加：登录凭据存在钥匙串的默认访问组，加了 Keychain Sharing 会读不到以前存的凭据，大家得重新登录。
   - **Watch2WhoopWatch**（手表 app）：HealthKit、App Groups（`group.<iPhone 的 Bundle ID>.watch` 打勾）、Background Modes（Workout processing）。
   - **Watch2WhoopWatchWidgets**（表盘小组件）：App Groups（同一个组打勾）。
   - App Groups 那一栏标红 / 组没打勾：按第 3 步的表在网页上建组、在两个新 App ID 上都勾上，回 Xcode 点「Try Again」。
6. 打包上传：顶部选 **Watch2Whoop** scheme（不是 Watch2WhoopWatch）和 **Any iOS Device (arm64)** → **Product → Archive** → Organizer 里确认是 **2.1.0 (12)**；在 Finder 里「显示包内容」应该能看到 `Products/Applications/Watch2Whoop.app/Watch/Watch2WhoopWatch.app/PlugIns/Watch2WhoopWatchWidgets.appex` → **Distribute App → App Store Connect → Upload**，三份描述文件都要有效。出口合规已在 Info.plist 里声明（不使用非豁免加密）；推送的环境导出时 Xcode 自动改成正式环境。
7. TestFlight：新构建先加到**内部组**，等我说可以了再加到带公开链接的**外部组**。

**常见报错**
- 「watchOS … is not installed」/ 找不到 watchOS SDK：第一节的 watchOS 平台没装。
- 「Provisioning profile … doesn't include the com.apple.security.application-groups entitlement」：App Group 没建，或没在手表 / 小组件的 App ID 上勾，见第 3、5 步。
- 「The value of CFBundleVersion … must be the same」：有人在本地配置里改了版本号或构建号，删掉。
- 上传后邮件里有 ITMS-91053（隐私清单缺 API 声明）：这一版手表 app 和小组件都自带隐私清单；还收到的话把邮件原文转我。

**Beta App Review**：这一版第一次带手表 app，外部组大概率要重新审核。审核备注用我另外发你的最新 `App-Review-Notes.txt` 整份粘贴（演示账号我会先续期）。里面新加了下面这一段，供你核对：

```
WHAT'S NEW IN 2.1.0
The iPhone app is redesigned: five tabs (Today, Body, Log, Circles, Me), new first screens, three levels on Today and
Body (a glance, today's details, one metric's page), the assistant key opens a ring of shortcuts, a reorganised Me page
and a clearer sync center. Permissions on the iPhone are unchanged.

New: an Apple Watch app inside the same upload (it needs the iPhone app; watchOS 11 or later).
- Today at a glance: recovery, sleep and strain, computed on the iPhone and sent to the watch.
- Logging strength-training sets during a workout started on the watch, with a rest timer. Health on the watch: it reads
  heart rate during that workout and saves the strength workout to Health (the only place the app writes to Health).
  The sets are handed to the iPhone, which syncs them.
- Quick log: one tap logs a saved meal or a glass of water; it is sent through the iPhone.
- Hold to talk: only for accounts whose AI runs inside the app and that turned on direct voice. While the key is held
  the watch records; the audio goes to the user's own server, is turned into text and is not kept; the text goes to the
  user's AI. The watch asks for the microphone the first time. The watch gets its own credential from the paired iPhone
  (no sign-in on the watch); signing out on the iPhone ends it.
- Three watch-face complications: recovery, today, start workout.
Watch permissions: Health (heart rate read, workouts write) and Microphone.
To see the watch app with the demo account: sign in on the iPhone, open the app once, then install the watch app from
the Watch app on the iPhone (My Watch > Watch2Whoop > Install). <中控填：演示账号有没有今天的训练计划和快捷餐、手表上有没有语音>
```

做完回我一句「2.1.0 build 12 已在 TestFlight 内部组」就行。

## 中控自己的清单（不转发）

**打 tag 前**
- [ ] 发布 PR 已合并，CI 全绿，`iOS native` 真的跑了（不是 skipped）。
- [ ] **算法包已签名**：负责人在发布 PR 的分支上跑过 `body-engine algo-sign`，只提交了 `packages/openbodyscore-js/dist/manifest.json`，`signature` 不是 `null`、`key_id` 是 `AlgoSigningKeys.swift` 里的两把之一（`body_engine/tests/test_native_sync.py` 的 `test_repository_keys_match_the_app` 会查）。没签的后果：app 内置的 0.3.0 照常用（内置包不验签名），但已经装着 2.0.0 的手机看到服务器上更新的 0.3.0 会去下载、验签失败、每 6 小时再试一次，一直停在 2.0.0 内置的 0.2.0。
- [ ] **服务器已部署到包含发布 PR 的 main**：手表语音的接口（#458：`POST /v1/account/watch-code`、`POST /v1/web/login-code` 带 `client: "watch"`、`/v1/assistant/transcribe`、`/v1/assistant/chat` 带 `page: "watch"`，迁移 1190）、2.1.0 读的新字段（#452 / #454）、算法包 0.3.0 都要在生产上。部署后 `GET /v1/algo/manifest` 返回 `"version": "0.3.0"` 和非空的 `signature`。
- [ ] `tools/app_release.sh ios` 预演全部 ✓（2.1.0 还没有 tag、构建号 12 大于已有 ios tag 的最大值 11、CHANGELOG 有 `## 2.1.0`），然后 `tools/app_release.sh ios --push`，workflow 发 pre-release；把 Release 链接、commit、zip 的 sha256 填进上面。
- [ ] 演示账号：`review_demo.py renew`；确认它有今天的训练计划和至少一个快捷餐（手表首屏和快记才有东西），把上面 `<中控填>` 换成实话；审核备注加上「WHAT'S NEW IN 2.1.0」一段。
- [ ] 推送：服务器 `body-engine notify status` 正常（静默推送要 APNs 配好）。
- [ ] 手表语音和同意书：手表上的语音只在账号开了 `jarvis_voice_direct` 时出现（`WatchVoiceOffer.of`，`apps/ios/WatchShared/WatchVoice.swift`）；这个开关的说明要求开之前先改托底 AI 同意书第 4 条和数据同意书「语音输入」（现在写的是「确认或修改后才发」）。同意书没改之前，**不要为了让手表有语音去开这个开关**（含演示账号）。

**朋友上传之后**
- [ ] 负责人先在自己的 iPhone + Apple Watch 上按 [`ios-native-2.0-device-checklist.md`](ios-native-2.0-device-checklist.md) 的「2.1.0」一节验一遍（从 2.0.0 覆盖升级，手表第一次安装），没有阻塞问题再加外部组。
- [ ] 按 [`ios-native-app.md`](ios-native-app.md) 第 12.2 节的顺序：2.1.0 不需要动任何服务器开关。
- [ ] 一天后：`rawdata_uploads` 里 `app_version` 出现 `2.1.0+12`；doctor 正常；`GET /v1/account/watch-tokens`（有手表语音的账号）里有手表的凭据；没有新的 `stale_algo` 激增（`app-compute status`）。
