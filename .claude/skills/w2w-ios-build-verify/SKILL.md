---
name: w2w-ios-build-verify
description: Build, test and verify a change to the native iOS / Apple Watch app (apps/ios) on this one shared Mac — disk check, the one-build-at-a-time lock, DEVELOPER_DIR, DerivedData in the scratchpad, xcodegen + check_sources.py, only the relevant tests, the synthetic demo server for screenshots, installing on the owner's acceptance simulator, and cleaning up. Use before any xcodebuild / simctl run. Triggers: build iOS, xcodebuild, simulator, 模拟器, 截图, 验收模拟器, 手表模拟器, watch simulator, xcodegen, check_sources, 编译, 跑 iOS 测试, screenshots, install on simulator.
---

# Build and verify the iOS / watch app

Distilled from the 2.0.1 / 2.1.0 sessions (#438–#475: iPhone redesign, Jarvis panel, Apple Watch phases 1–2,
complications, voice UI). Several sessions build on the same 16 GB Mac at once; most failures were disk, a second
concurrent `xcodebuild`, or a screenshot that leaked real data.

## 1. Before building

1. `df -h /` — under **15 GB free: stop and report** (under 8 GB is never acceptable). Do not delete anything you did
   not create to make room.
2. Take the build lock: `mkdir /private/tmp/w2w-ios-build.lock` (atomic). If it exists, another session is building:
   wait (`until mkdir /private/tmp/w2w-ios-build.lock 2>/dev/null; do sleep 30; done`), do not poll in a tight loop.
   Release it with `rmdir` when your build/test step finishes, also on failure.
3. Prefix every Xcode command with `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer` (no sudo for
   `xcode-select`). Missing watchOS platform: `xcodebuild -downloadPlatform watchOS` (check disk first).
4. Work in your own worktree, never the main checkout (it may hold someone else's uncommitted changes). Some tools crash
   on the non-ASCII repo path: copy the needed tree to an ASCII path in your scratchpad if that happens.

## 2. Build and test

```bash
cd apps/ios
python3 scripts/check_sources.py          # bilingual tr(), colour tokens, watch tokens, licences — same as CI
xcodegen generate -q                      # the .xcodeproj is generated, never committed
S=<your scratchpad>
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild \
  -project Watch2Whoop.xcodeproj -scheme Watch2Whoop \
  -destination "platform=iOS Simulator,id=$SIM" -derivedDataPath "$S/dd" \
  CODE_SIGNING_ALLOWED=NO build-for-testing
DEVELOPER_DIR=... xcodebuild ... test-without-building \
  -only-testing:Watch2WhoopTests/<SuiteYouTouched> -only-testing:Watch2WhoopTests/ReachabilityTests
```

- `-derivedDataPath` always in your scratchpad, never `~/Library/Developer/Xcode/DerivedData` (other sessions' caches
  live there; leave them alone).
- Run only the suites your change touches plus `ReachabilityTests` when you touched navigation. The full suite runs in
  CI (`iOS native`). Never end a turn just to "wait for CI"; the coordinator uses Auto-fix.
- Use a simulator **you created** (`xcrun simctl create ...`). Never boot, erase or reinstall someone else's.

## 3. Screenshots and demo data

- Real data never appears in the repo, PR text, comments or screenshots. Use the throw-away server with synthetic data:
  `W2W_SHOTS_DIR=$S/shots python3 apps/ios/scripts/appstore_demo_server.py up` (prints `{"server", "token", ...}`),
  `... down` afterwards. It runs on its own container/port and never touches the real database on 5433.
- Synthetic nickname 「小林」 / the demo accounts only. The demo server may have no score for today: pick a past date
  that has one and say so.
- Launch straight onto a tab for screenshots: `SIMCTL_CHILD_W2W_DEV_SKIP_HEALTH=1 SIMCTL_CHILD_W2W_DEV_TAB=today
  xcrun simctl launch $SIM org.watch2whoop.app`, then `xcrun simctl io $SIM screenshot --type=png <file>`.
- `W2W_WATCH_DEMO` freezes the watch clock: fine for single-screen shots, wrong for click-through flows (timers stop).
- Shoot what the change affects in: dark + light, smallest screen, Chinese + English; before and after side by side
  for UI changes. Screenshots go on a separate `assets/pr-<N>-screenshots` branch referenced from the PR, not into the
  feature branch.

## 4. Hand-over to the owner

- When the coordinator asks for it, install the PR's Debug build on the owner's **acceptance simulator** (iPhone and,
  for watch work, the watch one; ask the coordinator which UDIDs — they are not written in the public repo), log in the
  demo account and leave it on 「今天」. Report the head SHA installed.
- In the PR and the report, list plainly what was **not** verified: real device, real HealthKit, background refresh,
  push, watch hardware. Device-only checks go to the release gap list (see `w2w-ios-release-prep`).

## 5. Clean up

- `appstore_demo_server.py down`, delete the simulators you created (`xcrun simctl delete <udid>`), delete your
  scratchpad DerivedData, release the lock. Say in the report how much space you freed and what you left on purpose
  (e.g. the screenshot branch).
