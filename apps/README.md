# Client apps

**Development is iOS native only** (decided 2026-11-18). [`ios/`](ios/README.md) is the only client in development: a
SwiftUI iPhone app with a local database, its own HealthKit sync engine, the scoring algorithms running on the phone, and
the Apple Watch app embedded in it (2.1.0, in progress). Architecture and code names: [docs/ios-native-app.md](../docs/ios-native-app.md).
Versioning and release tags: [docs/apps.md](../docs/apps.md) section 4.

| Platform | Directory | Status | Health data | Build / release |
|---|---|---|---|---|
| iOS + Apple Watch (native) | [`ios/`](ios/README.md) (SwiftUI, #340) | **Active.** 2.0.0 on TestFlight, 2.1.0 in progress | HealthKit, own sync engine (#219) | [README](ios/README.md) · [CHANGELOG](ios/CHANGELOG.md) · tags `app-ios-v*` |
| iOS (Flutter, up to 1.0.8) | [`watch2whoop/`](watch2whoop/README.md) | **Historical, unmaintained.** Kept for reference; no fixes, no new work (exception: the sync files that `ios/scripts/sync_flutter_sources.sh` copies into `ios/`, see the note below) | HealthKit through the Open Wearables SDK | [README](watch2whoop/README.md) · [CHANGELOG](watch2whoop/CHANGELOG.md) |
| Android | — | Not planned | — | — |
| HarmonyOS NEXT | — | Not planned | — | — |

The one live dependency on the Flutter directory: `ios/Runner/{SyncEngine,RawSync,Backfill}.swift` and `assets/healthkit_types.json` are still the originals of files that `apps/ios/scripts/sync_flutter_sources.sh` copies byte for byte into `apps/ios` (CI compares them, but only warns). Until those originals move into `ios/`, a change to the sync engine is made there and copied.

Licence: each app has its own `LICENSE` (both iOS apps are MIT); the rest of the repository is AGPL-3.0, see [CONTRIBUTING.md](../CONTRIBUTING.md).

客户端 app 索引：目前只开发 iOS 原生 app（`ios/`，内嵌 Apple Watch app）；Flutter 旧版（`watch2whoop/`）仅作历史保留、不再维护；Android 和鸿蒙都没有计划。版本号和发版规则见 [docs/apps.md](../docs/apps.md) 第 4 节，架构和代号见 [docs/ios-native-app.md](../docs/ios-native-app.md)。
