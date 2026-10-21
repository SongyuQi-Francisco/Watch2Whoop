# Client apps

Thin clients of the Watch2Whoop server: each one syncs the device's health data, shows the Today page in a WebView and
sets itself up from one setup QR code. Platforms, the shared client contract, the planned layout and how app releases
are versioned: [docs/apps.md](../docs/apps.md).

| Platform | Directory | Status | Health data | Build / release |
|---|---|---|---|---|
| iOS | [`watch2whoop/`](watch2whoop/README.md) (Flutter) | TestFlight | HealthKit (Open Wearables SDK) | [README](watch2whoop/README.md) · [CHANGELOG](watch2whoop/CHANGELOG.md) · tags `app-ios-v*` |
| Android | same Flutter app, `android/` target not added yet | Planned | Health Connect / Samsung Health (Open Wearables SDK) | tags `app-android-v*` |
| HarmonyOS NEXT | `harmony/` (not created yet) | Idea | Huawei Health Service Kit | tags `app-harmony-v*` |
| Apple Watch / Wear OS | inside the iOS / Android projects | Later | — | ships with the phone app |

The Flutter app will move to `apps/flutter/` once the open PRs that change it are merged (steps in
[docs/apps.md](../docs/apps.md#when-to-rename-appswatch2whoop--appsflutter)).

Licence: each app has its own `LICENSE` (the Flutter app is MIT); the rest of the repository is AGPL-3.0, see [CONTRIBUTING.md](../CONTRIBUTING.md).

客户端 app 索引：iOS 是现在的 Flutter app；Android 用同一套 Flutter 代码（计划中）；鸿蒙 NEXT 单独做（设想）。平台、共用部分、目录规划和版本发布见 [docs/apps.md](../docs/apps.md)。
