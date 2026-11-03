# Watch2Whoop iOS（原生 SwiftUI）

纯 SwiftUI 的 iOS app（#340），从 2.0.0 起替换 Flutter 版（`apps/watch2whoop/`；1.0.9 没有发布，它的内容并入 2.0.0）。架构、本地数据库、同步、算法包和各个页面的分工都写在 [`docs/ios-native-app.md`](../../docs/ios-native-app.md)，改代码前先读那份文档。

## 构建

需要 Xcode 26 和 XcodeGen 2.46.0（`brew install xcodegen`）。工程文件由 `project.yml` 生成，不进 git。

```bash
cd apps/ios
xcodegen generate
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcodebuild -project Watch2Whoop.xcodeproj -scheme Watch2Whoop \
  -destination 'platform=iOS Simulator,name=iPhone 17' test
```

- **签名**：把 `Config/Watch2Whoop.local.xcconfig.example` 复制成 `Config/Watch2Whoop.local.xcconfig`（不进 git），填上和 Flutter 版**相同**的 Bundle ID 和 Team ID。值相同，TestFlight 才会覆盖升级，Keychain 里的登录凭据也才能迁过来。
- **不用 Xcode 的检查**：
  - `python3 scripts/check_sources.py`：双语、颜色 token；
  - `python3 scripts/lucide_assets.py --check`：图标；
  - `scripts/sync_flutter_sources.sh --check`：拷进来的同步代码。
- **模拟器自动登录**（只对 Debug 版有效）：`SIMCTL_CHILD_W2W_DEV_SERVER=http://127.0.0.1:<端口> SIMCTL_CHILD_W2W_DEV_TOKEN=<token> SIMCTL_CHILD_W2W_DEV_LANG=zh xcrun simctl launch <设备> <bundle id>`。只连用合成数据的测试服务器（`quickstart/`）。
- **模拟器截图用的开关**（只对 Debug 版有效）：`SIMCTL_CHILD_W2W_DEV_SKIP_HEALTH=1` 跳过健康权限弹层；`SIMCTL_CHILD_W2W_DEV_LINK=/app/?tab=friends&feed=<id>` 等于点了一条通知；`SIMCTL_CHILD_W2W_DEV_CIRCLES=members|board|feed[:圈子 id]` 让朋友 tab 打开在指定的视图（`docs/ios-native-app.md` 第 9.2 节）。
- **模拟器里走设置链接登录**（Debug 版）：`SIMCTL_CHILD_W2W_DEV_SETUP='watch2whoop://setup?today=…&login=<登录码>'`，和相机扫码走同一条路（`body-engine login-code` 发码）。`simctl openurl` 也行，但要在模拟器里点一次「打开」。
- **Jarvis 演示，不用数据库，不调 AI**（Debug 版）：先 `python3 scripts/fake_jarvis_server.py`，再用上面的自动登录，`W2W_DEV_SERVER` 填 `http://127.0.0.1:18766`、`W2W_DEV_TOKEN` 随便填。再加 `SIMCTL_CHILD_W2W_JARVIS_DEMO=dial|open|hold|chat|cards|posture|posture-filled|posture-send`（`cards` = 每种卡片各一张，再加 `SIMCTL_CHILD_W2W_JARVIS_OPEN=<页面键>` 会按下那个按钮看落在哪；`posture` 打开体态分析，`posture-filled` 里面放好 3 张纯色假照片，`posture-send` 直接发出去），`W2W_PERF=1` 时打印帧率。假服务器的环境变量 `W2W_FAKE_FLAGS=friends,…` 给 `/v1/web/me` 的 `flags` 再加几个开关。详见 `docs/ios-native-app.md` 9.2。
- **薄肌模式的截图开关**（Debug 版）：`SIMCTL_CHILD_W2W_DEV_ME_SHEET=appearance` 打开「我 → 外观」；再加 `SIMCTL_CHILD_W2W_DEV_THEME_FLIP=<秒>`，打开那么久之后切换一次主题。开关 `slim_mode` 要在测试服务器上打开。
- **看别人的数据（只读）**（Debug 版）：`SIMCTL_CHILD_W2W_DEV_MEMBER=<用户 id>[:today|trends|sleep|food|record|health]` 打开那个人的只读页面（服务器要说他共享给我）。
- **饮食和训练的截图开关**（Debug 版）：`SIMCTL_CHILD_W2W_DEV_FOOD=shopping|diet|pick|scan|products|prices|product|history|grams|allmeals|combine`、`W2W_DEV_TODAYFOOD=day`（今天页的饮食卡）、`W2W_DEV_TRAINING=library|custom|split|goals|log|exercise:<slug>|plan:<日期>|muscle:<肌群>`；滚动锚点等细节在 `docs/ios-native-app.md` 第 9.3 节。
- **模拟器里测 Keychain / 覆盖升级**：不能用 `CODE_SIGNING_ALLOWED=NO`（没签名的 app 写不进 Keychain）。用 `CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual W2W_TEAM_ID=FAKETEAMID` 构建：Flutter 的模拟器构建用的就是 `FAKETEAMID`，两边的 Keychain 访问组一致，才能读到 Flutter 版存的 token。
- **模拟器里写测试健康数据**（Debug 版、只在模拟器）：`SIMCTL_CHILD_W2W_DEV_SEED_HEALTH=21` 启动时先弹一次写入授权，再写 21 天合成数据（心率、HRV、静息心率、睡眠阶段、步数、能量、隔天一次力量训练、逐拍心跳；元数据 `W2WTestData=1`，体重 99.9 kg）。模拟器里已经写过就只加一条新心率。全量回归的步骤见 `docs/ios-native-1.0.9-regression.md`。

## TestFlight

给编译上传的人：只从 `app-ios-v<版本>` 的 GitHub Release 拿源码（zip 里除了 `apps/ios/`，还有它要打进 app 的两个文件，路径和仓库一样），版本号和构建号在 `Config/Watch2Whoop.xcconfig` 里，不要改。签名、能力（HealthKit、Sign in with Apple、Push、Background Modes）、Keychain、App Review 备注的完整步骤在交接单模板 [`docs/ios-native-1.0.9-handover-template.md`](../../docs/ios-native-1.0.9-handover-template.md)。

## English

Pure-SwiftUI iOS app (#340), replacing the Flutter app (`apps/watch2whoop/`) from 2.0.0 (1.0.9 was never released; its content ships in 2.0.0). The architecture and the work split between pieces are in `docs/ios-native-app.md` (Chinese, with an English summary).

**Build**
- Run `xcodegen generate` (XcodeGen 2.46.0), then `xcodebuild … test` on a simulator.
- Signing values go in the git-ignored `Config/Watch2Whoop.local.xcconfig`. Use the same bundle ID and team as the Flutter app, so TestFlight upgrades it in place.

**TestFlight**: build only from the `app-ios-v<version>` GitHub Release (its zip holds `apps/ios/` plus the two files the
project builds in from elsewhere, at their repository paths). The version and build number are in
`Config/Watch2Whoop.xcconfig`; do not change them. Signing, capabilities, Keychain and App Review notes:
`docs/ios-native-1.0.9-handover-template.md`.

Licence: MIT ([LICENSE](LICENSE)).
