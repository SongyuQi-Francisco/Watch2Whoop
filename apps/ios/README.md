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

## English

Pure-SwiftUI iOS app (#340), replacing the Flutter app (`apps/watch2whoop/`) from 1.0.9. The architecture and the work split between pieces are in `docs/ios-native-app.md` (Chinese, with an English summary).

**Build**
- Run `xcodegen generate` (XcodeGen 2.46.0), then `xcodebuild … test` on a simulator.
- Signing values go in the git-ignored `Config/Watch2Whoop.local.xcconfig`. Use the same bundle ID and team as the Flutter app, so TestFlight upgrades it in place.

Licence: MIT ([LICENSE](LICENSE)).
