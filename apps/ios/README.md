# Watch2Whoop iOS（原生 SwiftUI）

纯 SwiftUI 的 iOS app（#340），从 1.0.9 起替换 Flutter 版（`apps/watch2whoop/`）。架构、本地数据库、同步、算法包和各个页面的分工都写在 [`docs/ios-native-app.md`](../../docs/ios-native-app.md)，改代码前先读那份文档。

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
- **模拟器里测 Keychain / 覆盖升级**：不能用 `CODE_SIGNING_ALLOWED=NO`（没签名的 app 写不进 Keychain）。用 `CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual W2W_TEAM_ID=FAKETEAMID` 构建：Flutter 的模拟器构建用的就是 `FAKETEAMID`，两边的 Keychain 访问组一致，才能读到 Flutter 版存的 token。

## English

Pure-SwiftUI iOS app (#340), replacing the Flutter app (`apps/watch2whoop/`) from 1.0.9. The architecture and the work split between pieces are in `docs/ios-native-app.md` (Chinese, with an English summary).

**Build**
- Run `xcodegen generate` (XcodeGen 2.46.0), then `xcodebuild … test` on a simulator.
- Signing values go in the git-ignored `Config/Watch2Whoop.local.xcconfig`. Use the same bundle ID and team as the Flutter app, so TestFlight upgrades it in place.

Licence: MIT ([LICENSE](LICENSE)).
