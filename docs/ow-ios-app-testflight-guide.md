# （已被替代）OW iOS app 编译和 TestFlight 分发指南

> **2026-10-17 起改为自己的 Watch2Whoop iOS app**：一个 app 同时做后台同步（同一个 OW Flutter SDK，iOS SDK 0.15）和内嵌今天页，取代「OW 官方示例 app + 快捷指令 + Safari 今天页」三件套。
>
> **给帮忙编译上传的朋友的步骤（中英双语）在 [`apps/watch2whoop/README.md`](../apps/watch2whoop/README.md)**：环境、填自己的 Bundle ID / Team ID（`ios/Flutter/Watch2Whoop.local.xcconfig`，不改工程文件）、App Store Connect 建 app、`flutter build ipa` + Transporter 上传、TestFlight 内部测试员、90 天过期续发。

下面是旧方案（编译 OW 官方 `example/`）的要点，只留作记录：
- 原因：OW 公开 TestFlight 版打包的是 iOS SDK 0.14.0，有同步竞态（每次只传约 1000 条就被新的同步打断）；0.15.0 修复（SDK 更新日志 #26）。Watch2Whoop 的 `Podfile.lock` 钉的就是 0.15.0。
- 旧方案要在 Xcode 里改 `example/` 的 Team（原值是 OW 官方的）和 Bundle ID，不传 `SENTRY_DSN` 关掉崩溃上报；Watch2Whoop 没有任何第三方上报。
- RMSSD（iOS 27）：iOS SDK 只在未发布的 master 分支支持，且服务器要 OW ≥ 0.10.0；等两边都发版后，Watch2Whoop 把 OW SDK 升级、用 Xcode 27 重新编译上传。
