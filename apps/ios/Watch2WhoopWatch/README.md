# Watch2Whoop for Apple Watch (phase 1: logging sets; phase 2a: today at a glance and quick log; complications)

The watch app of [`docs/watch-app-plan.md`](../../../docs/watch-app-plan.md), drawn after the approved mockups in
`docs/design/watch/` (both on the `claude/watch-app-plan-296f6e` branch until that PR merges).

- **Home = 「今天一眼」** (plan 2.4, mockups D1 / D2, S1–S7): the recovery dial (number, arc in its zone colour, your
  usual as a grey lane, a word of at most 4 characters), sleep and strain with their bars, and two keys: the left capsule
  (today's plan → the training start screen; 「记一餐」 on a day without training; 「计划没同步」 when today's plan has not
  reached the watch, tap = ask the phone again) and the Jarvis core (tap = quick log). Double Tap = the left capsule.
  The numbers are the phone's (its Today, computed on the phone); the watch computes nothing and draws a missing number
  as 「—」. The sync mark at the top left has the phone capsule's states: 「2分前」, syncing, old data (numbers grey,
  「6时前」), phone away (「待传 N」), catching up, failed (「重试」), offline, and the phone's own problems.
- **Training** (plan 2.1, phase 1): T1 start → set → logged → rest → … → summary, as before; T1 is now opened from home
  (back = home) and the summary's 「完成」 returns home. A running session is saved at every step and resumed when the app
  opens again (its HealthKit workout is recovered); one left for more than `WatchParams.resumeWithinHours` is ended.
- **Quick log** (plan 2.2, mockups M3–M5): up to three quick meals (the phone's most used, made and ordered on the phone)
  and water. A tap logs at once (no AI, offline OK) and shows the receipt: the meal, kcal, protein, and the day's bar
  when the phone's intake is known; 「撤销」 → the confirm screen (撤销 / 保留). Back from the receipt = home.
- **Complications** (plan 2.5, mockups C1 / C4; section 「Complications」 below): circular Recovery, rectangular Today
  (recovery, sleep, strain; the rest countdown and the next set while a session runs) and the circular Start key.
- **Not yet** (phase 2b and later): hold to talk / voice (the core is drawn dimmed, mockup S7's "voice unavailable"; a
  hold only gives a failure tap), Jarvis questions, Smart Stack relevance, corner / inline complications, controls, Siri.

## Layout

| Path | What |
|---|---|
| `apps/ios/WatchShared/` | Plain Foundation, compiled into the iPhone app and the watch app: the plan / event / context types and their WatchConnectivity encoding (`WatchLink.swift`: `WatchContext` with today's numbers `WatchToday` and the tiles `WatchQuick`; one event type for sets, the end, quick meals, drinks and undo), the home rules, the watch's quick logs and the receipt bar, routes and the saved session (`WatchHome.swift`), the session state machine (`SetSession.swift`), the rest countdown (`RestTimer.swift`), the offline queue and the sync mark's state (`WatchEventQueue.swift`), tunables (`WatchParams.swift`). Unit-tested in `Watch2WhoopTests/WatchLinkTests.swift`. |
| `apps/ios/Watch2WhoopWatch/` | The watch app (SwiftUI, watchOS 11+): home, T1–T10, M3–M5 and the sync states of `docs/design/watch/` (`WatchScreens.swift`, `WatchComponents.swift`: dial, readings, Jarvis core, tiles), HealthKit workout (`WorkoutController.swift`), WatchConnectivity (`WatchLinkClient.swift`), the glue (`WatchModel.swift`), Debug-only synthetic data (`WatchDemoData.swift`). Colours are the iPhone's dark tokens (`WatchTokens.swift`, checked by `scripts/check_sources.py`; the Jarvis ice is `WJarvis`, the phone's `CockpitPalette`). Icons are SF Symbols, as in phase 1. |
| `apps/ios/WatchShared/WatchVoice.swift` | Hold to talk, pure and unit-tested (`Watch2WhoopTests/WatchVoiceTests.swift`): the phone's voice offer, the watch's pairing state, the WAV check and the recording limits, the 1.5 s send timer, the SSE parser and one turn's accumulation (text, meal receipt, confirm, error, resume point), the short answer's headline / body / 「在手机上继续」, the error codes → screens, and the flow's state machine (`WatchVoiceFlow`: inputs in, effects out). |
| `Watch2WhoopWatch/WatchVoice*.swift`, `WatchRecorder.swift`, `WatchKeychain.swift` | The watch side of voice: the controller that carries out the flow's effects, the HTTPS client (`WatchVoiceAPI`: login-code, transcribe, chat, confirm, events; header `X-App-Client: watchos`), the recorder (AVAudioRecorder, 16 kHz mono 16-bit WAV in the temporary folder, deleted as soon as it is read), the token in the Keychain (this device only), the screens. |
| `apps/ios/Watch2WhoopWatchWidgets/` | The complications: a WidgetKit extension embedded in the watch app (`FaceWidgets.swift`: the bundle and the timeline provider; `FaceViews.swift`: the three views, also drawn by the Debug gallery; `FaceSamples.swift`: the widget gallery's synthetic sample). Their rules are `WatchShared/WatchFace.swift` (`FaceTimeline`, `WatchDeepLink`), tested in `Watch2WhoopTests/WatchFaceTests.swift`. |
| `apps/ios/Watch2Whoop/Features/Watch/PhoneWatchLink.swift` | The iPhone side: pushes today's plan, today's numbers (its own `TodayStore`: recovery, sleep, strain, its sync capsule) and the quick-log tiles (`FoodStore`'s quick meals, water when on) to the watch; merges the watch's set events into the day's draft (an `end` event submits the day); logs the watch's quick meals and drinks with `FoodStore.log` / `addWater` (the watch's event id is the write's `client_id`) and undoes them with `FoodStore.undoWrite`; voice: the voice offer in the context, the watch's codes (`POST /v1/account/watch-code`), the undo of a voice meal (`voiceUndo`) and 「在手机上继续」 (Jarvis opens the conversation). |

## Data path

```
watch: set logged ─► WatchEventQueue (kept on the watch) ─► transferUserInfo ─► iPhone: WatchEventMerge (dedupe by id)
       ─► TrainingStore.saveDraft (PUT /training/session/{day}, offline queue) ─► end event ─► TrainingStore.submit
watch: HKWorkoutSession ends ─► one strength workout in Health ─► the phone's existing Health sync ─► workouts / strain
iPhone: TrainingStore session ─► WatchPlanBuilder ─┐
iPhone: TodayStore (phone's scores) ─► WatchTodayBuilder ─┼─► updateApplicationContext ─► watch home / T1 / M5
iPhone: FoodStore quick meals ─► WatchQuickBuilder ──────┘
watch: quick meal / water ─► WatchEventQueue ─► transferUserInfo ─► iPhone: FoodStore.log / addWater (client_id = event id)
       ─► offline queue ─► POST /meal-templates/{id}/log, POST /water
watch: undo ─► (still on the watch: dropped there) or an `undo` event ─► iPhone: queued write dropped, or the same POST
       again (the server answers from its client_id store, nothing new is written) ─► DELETE /meal-logs/{id}, /water/{id}
```

Undo needs the server when the write had already gone out; without a connection the phone keeps the undo and tries again
with the next watch event or when the watch link wakes.

## Layout rules (owner's acceptance 2026-11-19)

- **Numbers in dials and rings are centred on their own**: `CenteredNumber` (its `HungUnitLayout`) puts the digits'
  ink box on the ring's centre and hangs the unit (「%」, 「/18」) after the number without moving it; other text is
  placed by its baseline from the centre (`centredBaseline`). The geometry is `DialText` in `WatchShared/WatchLayout.swift`
  (unit-tested). Used on home, the start screen, the rest countdown and the summary.
- **Home is one drawing** (`DesignCanvas`, `HomeLayout`): the mockup's coordinates scaled by the screen width, hung from
  the system clock's row. watchOS 26 gives no layout value for that row (no top safe area without toolbar items; a
  `topBarLeading` item sits about 7 pt below the clock), so `ClockRow` holds the measured row of every watch it runs
  on; the sync mark sits on it, as far from the left edge as the clock is from the right.

## Where voice and the watch-face widgets plug in (before 2.1.0)

Both come after phase 2a, each in its own PR; 2a leaves these places for them.

**Voice (hold to talk: a meal by voice, one question to Jarvis; plan 2.2 / 2.3 / 4.3 / 4.5)**

| Where | What changes |
|---|---|
| `WatchShared/WatchHome.swift` `WatchVoice.derive` | The only availability rule: from a new optional context field (the user's AI kind, voice-direct on / off), the watch's own token and the watch's network. The home screen's core and the hold already follow it. |
| `Watch2WhoopWatch/WatchModel.swift` `coreHold()` | The `.available` case starts the voice flow (M1 → M2 → M3 for a meal, J1 → J4 for a question). |
| `WatchShared/WatchLink.swift` | One optional block for voice in `WatchContext` (decoded leniently, like the others). The login code for the watch's own token goes by `sendMessage` / `transferUserInfo`, never in the context (it is kept and resent). |
| `Features/Watch/PhoneWatchLink.swift` | Fill that block from the phone's AI choice; ask `POST /v1/account/web-login-code` for the watch and hand the code over; tell the watch on sign-out. |
| new watch files | Recorder (16 kHz WAV, ≤ 30 s, foreground only), Keychain token store, the HTTPS client for `/v1/assistant/transcribe` and `/chat`, screens M1 / M2 / J1–J4. `project.yml`: the watch's `NSMicrophoneUsageDescription` (+ `en.lproj` / `zh-Hans.lproj`). |

Watch out for: a hold that also has to end (release = send, slide up = cancel) needs a press gesture with an "ended"
callback, not `onLongPressGesture(perform:)` as now; the tap (quick log) must keep working while the core is dimmed;
the watch's own network is not the phone's (`WatchSyncState.offline` is the phone's), so voice needs its own check;
backend changes (login-code purpose, revoking the watch's token on sign-out, short answers for `page: "watch"`) come
first.

**Watch-face widgets**: done, see 「Complications」 below. Smart Stack relevance (morning, training time), corner /
inline families and controls would build on the same `FaceTimeline` entries.

## Complications (plan 2.5)

Three, as approved (mockups C1 `compCircle('rec')`, `compCircle('dumbbell')`, C1 / C4 `compRect('today' | 'live')`):

| Kind | Family | Shows | Tap |
|---|---|---|---|
| `w2w.face.recovery` | circular | recovery number on the axis, its arc in the zone colour (from 12 o'clock); grey number and arc when the phone's numbers are old (S3); 「—」 over the empty track when there is no recovery today (none sent, yesterday's, signed out, no phone yet) | home |
| `w2w.face.today` | rectangular | idle: recovery ring + sleep and strain with their bars (category colours, grey when old, 「—」 when missing). During a session: the rest countdown ring and clock (both run on the face by themselves, `ProgressView` / `Text(timerInterval:)`), the exercise with 「2/4」 and its set pills, 「下一组 60 kg × 8」; during a set the dumbbell instead of the countdown and 「这一组」 | home; during a session the session |
| `w2w.face.start` | circular | the dumbbell (train colour) on a training day; a bowl (`fork.knife`) on a day without training; the dumbbell dimmed when today's plan has not reached the watch or nobody is signed in | the training start (T1); quick log (M5) on a day without training; home when there is no plan (its capsule says 「计划没同步」); the session while one runs |

**One set of rules.** The extension reads `context()`, `queue()` and `session()` from `WatchStore` and builds its
entries with `FaceTimeline` (`WatchShared/WatchFace.swift`): the numbers through `WatchHome.derive`, 「旧了」 through
`WatchSyncState.derive`, the session through `SetSession.tick` (the receipt becomes the rest, the rest the set). The
complications therefore show exactly what home would show at that moment and compute nothing. The labels follow the
phone's language (`context.lang`), like the app.

**Timeline.** One entry now, then one at each moment the drawing changes by itself: the numbers turn grey
(`dataAt + WatchParams.staleAfterMin`), the phone's 「同步中」 stops being fresh, the receipt ends, the rest ends, a
forgotten session passes `resumeWithinHours`, and local midnight (yesterday's numbers and plan are not today's). Equal
consecutive entries are merged. Policy: a new timeline just after midnight (`FaceTimeline.horizon`), so a phone that stays
silent for days still costs one request a day. The countdown is not an entry per second: the face runs it.

**Reloads.** `WatchStore.save` (context, queue, session) reloads the three kinds only when the entries it would build
change (`FaceTimeline.needsReload`): a push with the same numbers, or the app's own tick from receipt to rest, spends
nothing. What triggers one:

| Event | Path |
|---|---|
| The phone pushes a context (new scores in the morning, strain during the day, a new plan, sign-out) | `updateApplicationContext` → watchOS launches the watch app in the background (`.backgroundTask(.watchConnectivity)`, `WatchModel.receiveInBackground`, at most `WatchParams.backgroundLinkSec`) → `WatchModel.received` → `WatchStore.save` → reload if the face changes. Before this PR the context waited until someone opened the app. |
| A session step (start, set logged, rest adjusted or skipped, end) | `WatchModel.sessionChanged` → `WatchStore.save(session)` → reload (the app is in the foreground: not budgeted) |
| The queue changes in a way that changes grey (S3 is not shown while events wait) | `WatchStore.save(queue)` → reload when the entries differ |

**Budget (not measured yet).** Apple documents a daily reload budget for widgets (WidgetKit, "Keeping a widget up to
date": a frequently viewed widget typically gets 40–70 reloads a day; reloads while the containing app is in the
foreground are not counted) but publishes no number for watch-face complications. We expect about 3–10 budgeted reloads a
day (morning scores, a few strain changes, a plan change); the entries above need none. What needs a real watch (plan
4.10), not guessed here:

| # | What | How |
|---|---|---|
| V6 | How many reloads a day a complication on the active face actually gets; how long after the phone's push the face changes (morning scores within 15 minutes is the phase-3 acceptance); whether the background launch for a context arrives while the watch is on the charger / in Low Power Mode | one day on the owner's watch: log `received` and the timeline requests, compare with the phone's push times |
| V6b | Whether `ProgressView(timerInterval:)` animates the ring on the face in Always On, and how the three draw in the tinted (accented) rendering of the multicolour-off faces | look at the face with the wrist down, try two tinted faces |
| V6c | The exact slot sizes on 42 / 46 mm (the gallery uses Apple's published accessory sizes) | screenshots from the face |
| V7 | Controls (Control Center / Action button) | not in this PR (plan: phase 3, after V7) |

If V6 shows the budget is too small, the phone can use `transferCurrentComplicationUserInfo` (a separate daily allowance
for an app with a complication on the active face) for the morning scores: a change in `PhoneWatchLink` and
`WatchLinkClient`, not made here.

**App Group.** `WatchStore.shared(migrate:)` uses the suite named by the Info.plist key `W2WAppGroup` =
`$(W2W_APP_GROUP)` (`Config/Watch2Whoop.xcconfig`: `group.$(W2W_BUNDLE_ID).watch`, a placeholder like the bundle ID).
On the first launch of this build the app moves its four keys (context, queue, quick logs, session) from the standard
defaults into the suite, once (`moveKeys`; a value already in the suite wins; the queue's undelivered events are kept).
The extension never migrates; until the app has run once after the update, the complications show 「—」. A build whose
Info.plist has no group falls back to the standard defaults (the app works, the complications stay empty). The Debug
seed keeps its own suite and never touches the shared one.

**Seeing every state in the simulator (Debug).** `SIMCTL_CHILD_W2W_WATCH_FACES=1` opens a gallery that draws every
state with the extension's own views, from entries built by `FaceTimeline` on the synthetic data (小林): `circle`
(recovery: good / mid / bad, old, missing, yesterday's, signed out, no phone), `key` (training day, rest day, no plan,
session), `rect` (idle, old), `rect2` (missing, signed out) and `live` (rest countdown, in a set). Add
`-AppleLanguages "(en)" -w2w.native.lang.v1 en` for English. The real face: long-press the face in the simulator →
Edit → Complications, pick Watch2Whoop (the store must have a context, so the real app path needs a phone).

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

The `Watch2WhoopWatch` scheme alone builds just the watch app (simulator screenshots, running on a watch from Xcode):
`xcodebuild -target Watch2WhoopWatch -sdk watchsimulator CODE_SIGNING_ALLOWED=NO build`.

## Trying it in the simulator (Debug builds only)

There is no phone in a watch simulator, so Debug builds bring their own synthetic data (nickname 小林, every number made
up). Nothing of it is stored where a real phone's context lives.

**Interactive seed** (acceptance): the app runs normally with the clock going, and a synthetic phone answers: today's
context dated **today** (built at every launch, so it can never be yesterday's), recovery 82 / sleep 88 / strain 2.8, the
「上肢 A」 plan, three quick meals and water. Events are "delivered" after 0.8 s and quick logs counted into the day's
intake, so the receipt bar, the sync mark and undo behave as with a phone. It has its own storage
(`UserDefaults` suite `w2w.watch.seed`): a session started there is resumed at the next seeded launch.

```bash
xcrun simctl launch --terminate-running-process <watch-udid> org.watch2whoop.app.watchkitapp
```

with `SIMCTL_CHILD_W2W_WATCH_SEED` set in the environment of that command:

| `W2W_WATCH_SEED` | What the phone says |
|---|---|
| `1` | training day (D1) |
| `rest` | no training today (D2: 「记一餐」) |
| `noplan` | the phone's plan is yesterday's (「计划没同步」 inside home) |
| `stale` | the newest data is more than six hours old (S3: grey numbers, 「6时前」) |
| `signedout` | signed out (S8) |
| `reset` | like `1`, after clearing the seed's storage (queue, quick logs, a saved session) |

For example: `SIMCTL_CHILD_W2W_WATCH_SEED=1 xcrun simctl launch --terminate-running-process <udid> org.watch2whoop.app.watchkitapp`
(the bundle id is the placeholder's; with your own `W2W_BUNDLE_ID` it is `<that>.watchkitapp`). Add
`-AppleLanguages "(en)" -w2w.native.lang.v1 en` for English. A plain launch (no variable) is the real app.

**Frozen screens** (screenshots): `SIMCTL_CHILD_W2W_WATCH_DEMO=<id>` shows one mockup screen, the clock stopped:

| id | Screen |
|---|---|
| `d1`, `d2` | home: training day, day without training |
| `s1`, `s2`, `s3`, `s5`, `s6`, `s7` | home with the sync mark: linked, phone syncing, old data, catching up, failed, offline |
| `s9` | home with 「计划没同步」 |
| `s4` | set screen with the phone away |
| `s8` | not signed in |
| `t1`…`t10` | training: start (pushed from home), set, crown, logged, rest, next exercise, summary |
| `m5`, `m3`, `m3w`, `m4` | quick log: tiles, meal receipt, water receipt, undo confirm |

## Releasing (TestFlight)

For the person who archives and uploads (the full checklist is `docs/ios-native-1.0.9-handover-template.md`):

1. In the Apple Developer account the watch app needs its own App ID: `<iPhone bundle ID>.watchkitapp`, with
   **HealthKit**. With automatic signing Xcode registers it (select the `Watch2WhoopWatch` target → Signing &
   Capabilities, same team, "Automatically manage signing"); if the account does not allow that, add it under
   Identifiers by hand and press "Try Again" in Xcode.
2. **Complications (from 2.1.0)**: two more things in the developer account. (a) An App ID for the extension:
   `<iPhone bundle ID>.watchkitapp.widgets` (no capability other than App Groups). (b) An App Group
   `group.<iPhone bundle ID>.watch` (or the `W2W_APP_GROUP` set in your local xcconfig), enabled on **both** the watch
   app's App ID and the extension's App ID. With automatic signing Xcode does both when you select each target
   (`Watch2WhoopWatch`, `Watch2WhoopWatchWidgets`) → Signing & Capabilities and the App Groups box shows the group
   ticked; if it shows red, add the group under Identifiers → App Groups, tick it on both App IDs, and "Try Again".
3. Install the watchOS platform in Xcode before archiving.
4. Archive the `Watch2Whoop` scheme as usual; in the Organizer the archive contains `Watch/Watch2WhoopWatch.app` with
   `PlugIns/Watch2WhoopWatchWidgets.appex` inside it. All three provisioning profiles (iPhone, watch, complications) must
   be valid for the upload.
5. Do not change the version or build number of either target; they come from one place.

## Running on your own watch (development)

1. Fill in `Config/Watch2Whoop.local.xcconfig` as for the iPhone app.
2. On the watch: Settings → Privacy & Security → Developer Mode (the watch restarts). The iPhone is unlocked, paired and
   connected to the Mac.
3. Run the `Watch2Whoop` scheme on the iPhone: the watch app is installed with it (Watch app on the iPhone → My Watch →
   Watch2Whoop → Show App on Apple Watch, if it does not appear by itself). Or run the `Watch2WhoopWatch` scheme with the
   watch as destination.
4. First start on the watch: allow heart rate (read) and workouts (write) in the Health sheet.
5. Open the iPhone app once so today's plan, numbers and quick meals reach the watch.

## 中文（给发版的人）

- 2.1.0 的手表端 = 第 1 期（训练记组）+ 第 2a 期（首屏「今天一眼」、快记、导航、中途退出后接着练）+ 表盘小组件三个（圆形恢复、矩形今天 / 训练中休息倒计时、圆形开始训练）。语音（按住说话）另一个 PR。
- **表盘小组件要在开发者账号里多注册两样**：(1) 小组件扩展的 App ID **`<iPhone 的 Bundle ID>.watchkitapp.widgets`**；(2) App Group **`group.<iPhone 的 Bundle ID>.watch`**（本地 xcconfig 里改过 `W2W_APP_GROUP` 就用那个），在**手表 app 的 App ID 和小组件的 App ID 上都勾上**。自动签名时分别选 `Watch2WhoopWatch`、`Watch2WhoopWatchWidgets` 两个 target → Signing & Capabilities，App Groups 一栏里这个组打了勾就行；标红时到 developer.apple.com → Identifiers → App Groups 新建，再到两个 App ID 里勾上，回 Xcode 点 Try Again。归档里手表 app 下面应该有 `PlugIns/Watch2WhoopWatchWidgets.appex`，上传时三个描述文件（iPhone、手表、小组件）都要有效。

- 从 2.1.0 起手表 app **嵌在 iPhone app 里**，打 `Watch2Whoop` scheme 的包时自动带上，一次上传两个都有。Xcode 要先装 watchOS 平台（Settings → Components → watchOS），没装 iPhone app 也编不过。
- 开发者账号里要有手表 app 自己的 App ID：**`<iPhone 的 Bundle ID>.watchkitapp`**，勾 **HealthKit**。自动签名时 Xcode 会自己注册（选 `Watch2WhoopWatch` target → Signing & Capabilities，同一个 Team，勾 Automatically manage signing）；账号不允许时到 developer.apple.com → Identifiers 手动加，再回 Xcode 点 Try Again。
- 版本号、构建号和 iPhone app 同一个来源（`Config/Watch2Whoop.xcconfig`），不要单独改。
- Organizer 里归档应该包含 `Watch/Watch2WhoopWatch.app`；上传时 iPhone 和手表两个描述文件都要有效。
- 完整的交接单：`docs/ios-native-1.0.9-handover-template.md`。
