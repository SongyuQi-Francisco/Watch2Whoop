# iOS 1.0.9 交接单（模板）

> **2.1.0 起用 [`ios-native-2.1.0-handover.md`](ios-native-2.1.0-handover.md)**（多了手表 app、表盘小组件的 App ID 和 App Group）；这份留作 2.0.0 交接单的来源。

> **文件名里的 1.0.9 是历史**：1.0.9 从未发布，这份模板从 2.0.0 起一直是现行的 TestFlight 交接单模板（换成当次的版本号和构建号填写），所以没有归档。名字对照见 [`ios-native-app.md`](ios-native-app.md) 开头的表。

> 模板（#340 N9）。发布当天由中控复制一份到私有的 `dist/交接单-iOS-1.0.9.md`，把尖括号里的值填上再发。这份公开文件里不写任何人的名字、Apple ID、Team ID、Bundle ID、域名。
> 和以前的交接单最大的不同：**1.0.9 是全新的原生工程**（`apps/ios/`，SwiftUI），不再用 Flutter、CocoaPods，也不用 `config/defaults.json`。

## 给编译上传的朋友（可以整段转发）

你好！Watch2Whoop **1.0.9** 可以打包了。这一版整个 app 换成了 iOS 原生界面（不再是网页套壳），所以**工程换了**，打包步骤和以前不一样，第一次大概要多花半小时。测试用户那边不用做任何事：TestFlight 覆盖更新，不用重新登录。

**源码**（只从这个 Release 拿，不要用 main 或别人发的 zip）
- Release：仓库 Releases 页面里的 `app-ios-v1.0.9`（名字「iOS app 1.0.9」）：<Release 链接>
- commit：`<commit>`　源码 zip 的 sha256：`<sha256>`
- 版本号 **1.0.9**、构建号 **10** 已经写在 `apps/ios/Config/Watch2Whoop.xcconfig` 里，不用改，也不要在自己的配置里覆盖。TestFlight 里应该显示「1.0.9 (10)」。

**需要准备**
- Xcode 26（iOS 26 SDK；液态玻璃只有用它编译才有）。
- XcodeGen **2.46.0**：`brew install xcodegen`，`xcodegen --version` 应该打印 `Version: 2.46.0`（不是这个版本就到 XcodeGen 的 GitHub Releases 下 2.46.0）。
- 第一次打开工程要联网一次：Xcode 用 Swift Package Manager 下载 GRDB 7.11.1（唯一的第三方库）。

**步骤**
1. 下载 Release 的 zip，校验：`shasum -a 256 -c watch2whoop-ios-app-v1.0.9.zip.sha256` 打印 OK，`unzip -z watch2whoop-ios-app-v1.0.9.zip` 打印上面的 commit。解压后里面除了 `apps/ios/`，还有 `packages/` 和 `body_engine/` 下的两个文件，是 app 要打进去的资源（算分的算法包、肌肉图），**目录结构不要动**。
2. 签名配置（不进 git）：
   ```bash
   cd watch2whoop-ios-app-v1.0.9/apps/ios
   cp Config/Watch2Whoop.local.xcconfig.example Config/Watch2Whoop.local.xcconfig
   open -e Config/Watch2Whoop.local.xcconfig
   ```
   - `W2W_BUNDLE_ID`、`W2W_TEAM_ID`：**和 Flutter 版一字不差**，直接从你上次用的 `apps/watch2whoop/ios/Flutter/Watch2Whoop.local.xcconfig` 抄过来。只有同一个 Bundle ID 和 Team，TestFlight 才会把 1.0.8 覆盖升级成 1.0.9，大家才不用重新登录（登录凭据存在钥匙串里，钥匙串按 Team + Bundle ID 区分）。
   - `W2W_DISPLAY_NAME`：主屏幕上的名字，和以前一样。
   - `W2W_DEFAULT_HOST`：登录页预填的服务器，只写主机名（不要 `https://`，xcconfig 里两个斜杠是注释）。就是你 `config/defaults.json` 里今天页地址的主机名。
3. 生成工程：`xcodegen generate`，然后 `open Watch2Whoop.xcodeproj`（是 `.xcodeproj`，没有 `.xcworkspace`）。工程文件是生成的，不进 git；以后拿到新版本都要重新跑这一步。
4. **Signing & Capabilities**（Watch2Whoop target，第一次做一次）：
   - 勾「Automatically manage signing」，Team 应该已经是你的。**不要**在这个页面改 Team / Bundle ID（改上面的文件就行）。
   - 应该能看到这些能力（都来自工程配置，不用自己加）：
     - **HealthKit**，勾着 **Background Delivery**（后台收到手表新数据）；
     - **Sign in with Apple**；
     - **Push Notifications**；
     - **Background Modes**：Background fetch、Background processing、**Remote notifications**（这个是新的：服务器数据变了时发一条不显示的静默推送，app 在后台拉新数据）。
   - 后台任务的标识（`watch2whoop.sync.backfill`、`watch2whoop.sync.refresh`、`watch2whoop.sync.process`）已经写在 Info.plist 里，不用管。
   - **App Groups：不要加**（1.0.9 没有小组件 / 扩展，不需要）。**Keychain Sharing：不要加**。登录凭据存在钥匙串的默认访问组（Team ID + Bundle ID，和 1.0.8 一样，代码里没有指定别的组）；加了 Keychain Sharing 而第一个组名和默认的不一样，就读不到 1.0.8 存的凭据，大家得重新登录。
   - 如果 Xcode 提示 App ID 缺能力（比如 Push），点「Register」/「Try Again」让它更新描述文件。
   - **从 2.1.0 起 iPhone app 里带着 Apple Watch app**（target `Watch2WhoopWatch`，打包时自动嵌进去，一次上传两个都带上）。第一次要给它也做一次：
     - 它的 Bundle ID 是 **`<iPhone 的 Bundle ID>.watchkitapp`**（工程自动拼，不用填）。选 Watch2WhoopWatch target → Signing & Capabilities，同样勾「Automatically manage signing」、同一个 Team；Xcode 会在开发者账号里自动注册这个 App ID 和它的描述文件。账号不让自动注册时，到 developer.apple.com → Identifiers 手动加 `<iPhone 的 Bundle ID>.watchkitapp`（类型 App IDs → App），勾 **HealthKit**，再回 Xcode 点「Try Again」。
     - 它应该显示的能力：**HealthKit**（手表上开训练、读心率、把力量训练存进「健康」）；Background Modes 里的 **Workout processing**（训练中手腕放下也继续计时）。不用加 App Groups、Keychain Sharing、Push。
     - 版本号、构建号和 iPhone app 是同一个来源（`Config/Watch2Whoop.xcconfig`），两个 target 一样，不要单独改（不一样 App Store Connect 会拒收）。
     - 手表 app 有自己的图标（和 iPhone 同一张图），不用另外准备。
5. 打包上传：顶部选 **Watch2Whoop** scheme 和 **Any iOS Device (arm64)** → **Product → Archive** → Organizer 里确认是 **1.0.9 (10)** → **Distribute App → App Store Connect → Upload**。（从 2.1.0 起：Archive 前 Xcode 要装了 watchOS 平台，Settings → Components → watchOS，没有就编不过；Organizer 里展开归档应该能看到 `Watch/Watch2WhoopWatch.app`，上传时两个 App ID 的描述文件都要有效。）`Info.plist` 已声明不使用非豁免加密，不会再问出口合规。推送的环境 Xcode 导出时会自动改成正式环境。
6. TestFlight：新构建先加到**内部组**（我们先自己用几天），等我说可以了再加到带公开链接的**外部组**。

**Beta App Review**：这一版换了整个工程，外部组大概率要重新审核。审核备注用我另外发你的最新 `App-Review-Notes.txt` 整份粘贴（演示账号我会先续期）。里面新加了下面这一段，供你核对：

```
WHAT'S NEW IN 1.0.9
The app is now fully native (SwiftUI); the content is the same as before. Permissions it may ask for:
- Health (read only): heart rate, HRV and beat-to-beat intervals, ECG recordings, sleep, workouts, steps and more, synced
  only to the user's own server. The app never writes to Health in release builds (the write description in Info.plist
  is only used by debug builds on the simulator).
- Camera: scanning the setup QR code, and, only when the user chooses, a photo for the in-app assistant or a profile
  picture.
- Microphone and Speech Recognition: only while the user holds the assistant button to talk; recognition runs on the
  iPhone; audio is not stored.
- Notifications: likes / comments from circles and reminders; silent pushes (no visible content) tell the app to fetch
  new data in the background.
- Background: HealthKit background delivery, background app refresh and processing for the sync.
Apple Watch app (from 2.1.0, inside the same upload): logs strength-training sets during a workout started on the
watch. Health on the watch: reads heart rate during that workout and saves the strength workout to Health (this is
the only place the app writes to Health). It needs the iPhone app; sets are handed to the iPhone, which syncs them.
Sign-in: Sign in with Apple, an e-mail code, or the setup QR code / link below. Account deletion: Me → Delete account.
Demo account: <演示账号的设置链接 / 激活码，由中控填>
```

做完回我一句「1.0.9 build 10 已在 TestFlight 内部组」就行。

## 中控自己的清单（不转发）

**打 tag 前**
- [ ] N9 的 PR 已合并，CI 全绿（包括 iOS native）。
- [ ] 作废旧的 1.0.9：按 `docs/ios-native-app.md` 第 12.1 节删掉旧 tag 和 Release，再 `tools/app_release.sh ios` 预演（应该只剩全部 ✓）。
- [ ] `tools/app_release.sh ios --push`，workflow 发布 pre-release；把 Release 链接、commit、zip 的 sha256 填进上面。
- [ ] 算法包：服务器上的 `dist/manifest.json` 有签名，`key_id` 是 app 内置的两把之一（Release 版不接受没签名的包）。
- [ ] `review_demo.py renew`，审核备注加上「WHAT'S NEW IN 1.0.9」一段。
- [ ] 推送：服务器 `body-engine notify status` 正常（静默推送要 APNs 配好）。

**朋友上传之后**
- [ ] 负责人自己先按 `docs/archive/ios-native-1.0.9-device-checklist.md`（1.0.9 的清单，已归档；2.x 的清单是 `docs/ios-native-2.0-device-checklist.md`） 的 A 节（从 1.0.8 覆盖升级）验一遍，再开其他节给内测组。
- [ ] 按 `docs/ios-native-app.md` 第 12 节的顺序开开关、切 app 模式。
- [ ] 一天后：`sync_runs` 里有后台轮次；`app-compute status --user-id N` 有用户变成 app 算分；doctor 正常。
