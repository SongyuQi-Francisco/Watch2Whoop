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

The watch app has its own scheme and is **not embedded in the iPhone app yet**, so the iPhone scheme, its tests and
TestFlight uploads are unchanged (plan 4.10 V1). Embedding it (an iPhone target dependency on `Watch2WhoopWatch`) is a
later step, after the demo has been tried on a real watch.

Compile check without a watch simulator runtime (only the watchOS SDK that ships with Xcode is needed; CI runs this):

```bash
cd apps/ios && xcodegen generate && xcodebuild -project Watch2Whoop.xcodeproj -target Watch2WhoopWatch -sdk watchsimulator CODE_SIGNING_ALLOWED=NO build
```

## Installing on your own watch (development signing)

1. Fill in `Config/Watch2Whoop.local.xcconfig` (git-ignored) as for the iPhone app: `W2W_BUNDLE_ID`, `W2W_TEAM_ID`. The
   watch app's bundle ID is `$(W2W_BUNDLE_ID).watchkitapp`.
2. Xcode → Settings → Components: install the watchOS platform (needed to run on a watch).
3. On the watch: Settings → Privacy & Security → Developer Mode on (the watch restarts). The iPhone must be unlocked,
   paired, and connected to the Mac (cable or the same Wi-Fi).
4. `cd apps/ios && xcodegen generate`, open `Watch2Whoop.xcodeproj`, scheme **Watch2WhoopWatch**, destination your
   watch, Run. With automatic signing Xcode registers the watch app's bundle ID and its HealthKit capability.
5. First start on the watch: allow heart rate (read) and workouts (write) in the Health sheet.
6. Run the iPhone app (scheme Watch2Whoop, development build) on the paired iPhone and open 记录 once, so today's plan
   reaches the watch.
