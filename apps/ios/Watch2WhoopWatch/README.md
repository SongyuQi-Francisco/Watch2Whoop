# Watch2Whoop for Apple Watch (phase 1: logging sets)

The first demo of [`docs/watch-app-plan.md`](../../../docs/watch-app-plan.md) (on its own branch until that PR merges):
logging sets during a strength session. Start today's planned training → one big pair of numbers per set (weight ×
reps, the Digital Crown adjusts) → one tap or Double Tap logs it → the rest countdown with live heart rate and a haptic
at zero → next set / next exercise → summary. No voice, no complications, no "today" glance yet.

## Layout

| Path | What |
|---|---|
| `apps/ios/WatchShared/` | Plain Foundation, compiled into the iPhone app and the watch app: the plan / event / context types and their WatchConnectivity encoding (`WatchLink.swift`), the session state machine (`SetSession.swift`), the rest countdown (`RestTimer.swift`), the offline queue and the sync mark's state (`WatchEventQueue.swift`), tunables (`WatchParams.swift`). Unit-tested in `Watch2WhoopTests/WatchLinkTests.swift`. |
| `apps/ios/Watch2WhoopWatch/` | The watch app (SwiftUI, watchOS 11+): screens T1–T10 and the sync states of `docs/design/watch/`, HealthKit workout (`WorkoutController.swift`), WatchConnectivity (`WatchLinkClient.swift`), the glue (`WatchModel.swift`). Colours are the iPhone's dark tokens (`WatchTokens.swift`, checked by `scripts/check_sources.py`). |
| `apps/ios/Watch2Whoop/Features/Watch/PhoneWatchLink.swift` | The iPhone side: pushes today's plan to the watch, merges the watch's set events into the day's draft and lets the existing local-first write / offline queue send it; an `end` event submits the day. No backend change. |

## Data path

```
watch: set logged ─► WatchEventQueue (kept on the watch) ─► transferUserInfo ─► iPhone: WatchEventMerge (dedupe by id)
       ─► TrainingStore.saveDraft (PUT /training/session/{day}, offline queue) ─► end event ─► TrainingStore.submit
watch: HKWorkoutSession ends ─► one strength workout in Health ─► the phone's existing Health sync ─► workouts / strain
iPhone: TrainingStore session ─► WatchPlanBuilder ─► updateApplicationContext ─► watch start screen
```

## Building

The watch app is **embedded in the iPhone app** (owner, 2026-11-15: it ships with 2.1.0, together with the redesigned iPhone app). The iPhone target depends on
`Watch2WhoopWatch`; XcodeGen copies it into `Watch2Whoop.app/Watch/` ("Embed Watch Content"), so building, testing or
archiving the `Watch2Whoop` scheme builds the watch app too, and one TestFlight upload carries both. This needs the
watchOS platform in Xcode (Settings → Components → watchOS); without it the iPhone app does not build either.

- Bundle ID: `$(W2W_BUNDLE_ID).watchkitapp`, from the same git-ignored `Config/Watch2Whoop.local.xcconfig` as the iPhone
  app (the repository only has placeholders). `WKCompanionAppBundleIdentifier` = the iPhone app's bundle ID.
- Version and build number: the same `W2W_MARKETING_VERSION` / `W2W_BUILD_NUMBER` as the iPhone app
  (`Config/Watch2Whoop.xcconfig`); App Store Connect rejects an upload where they differ.
- A companion app (`WKRunsIndependentlyOfCompanionApp = false`): it is installed with the iPhone app and needs it.
- Capabilities: HealthKit; background mode `workout-processing`. Its own app icon (`Assets.xcassets`, the iPhone
  icon).
- Without a paired watch, or with the watch app not installed, the iPhone side does nothing (`WatchLinkGate`: no
  training store, no network pull, no push; only the one-time `WCSession.activate()`).

The `Watch2WhoopWatch` scheme alone builds just the watch app (simulator screenshots, running on a watch from Xcode).
Simulator screenshots (Debug): `SIMCTL_CHILD_W2W_WATCH_DEMO=t1…t10|s1…s9` shows one mockup screen with synthetic data.

## Releasing (TestFlight)

For the person who archives and uploads (the full checklist is `docs/ios-native-1.0.9-handover-template.md`):

1. In the Apple Developer account the watch app needs its own App ID: `<iPhone bundle ID>.watchkitapp`, with
   **HealthKit**. With automatic signing Xcode registers it (select the `Watch2WhoopWatch` target → Signing &
   Capabilities, same team, "Automatically manage signing"); if the account does not allow that, add it under
   Identifiers by hand and press "Try Again" in Xcode.
2. Install the watchOS platform in Xcode before archiving.
3. Archive the `Watch2Whoop` scheme as usual; in the Organizer the archive contains `Watch/Watch2WhoopWatch.app`.
   Both provisioning profiles (iPhone and watch) must be valid for the upload.
4. Do not change the version or build number of either target; they come from one place.

## Running on your own watch (development)

1. Fill in `Config/Watch2Whoop.local.xcconfig` as for the iPhone app.
2. On the watch: Settings → Privacy & Security → Developer Mode (the watch restarts). The iPhone is unlocked, paired and
   connected to the Mac.
3. Run the `Watch2Whoop` scheme on the iPhone: the watch app is installed with it (Watch app on the iPhone → My Watch →
   Watch2Whoop → Show App on Apple Watch, if it does not appear by itself). Or run the `Watch2WhoopWatch` scheme with the
   watch as destination.
4. First start on the watch: allow heart rate (read) and workouts (write) in the Health sheet.
5. Open 记录 once on the iPhone so today's plan reaches the watch.

## 中文（给发版的人）

- 从 2.1.0 起手表 app **嵌在 iPhone app 里**，打 `Watch2Whoop` scheme 的包时自动带上，一次上传两个都有。Xcode 要先装 watchOS 平台（Settings → Components → watchOS），没装 iPhone app 也编不过。
- 开发者账号里要有手表 app 自己的 App ID：**`<iPhone 的 Bundle ID>.watchkitapp`**，勾 **HealthKit**。自动签名时 Xcode 会自己注册（选 `Watch2WhoopWatch` target → Signing & Capabilities，同一个 Team，勾 Automatically manage signing）；账号不允许时到 developer.apple.com → Identifiers 手动加，再回 Xcode 点 Try Again。
- 版本号、构建号和 iPhone app 同一个来源（`Config/Watch2Whoop.xcconfig`），不要单独改。
- Organizer 里归档应该包含 `Watch/Watch2WhoopWatch.app`；上传时 iPhone 和手表两个描述文件都要有效。
- 完整的交接单：`docs/ios-native-1.0.9-handover-template.md`。
