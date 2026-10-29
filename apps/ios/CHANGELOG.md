# Changelog — native iOS app (apps/ios)

Releases are named by version only (docs/apps.md section 4): one section `## X.Y.Z` per store build, newest first. The
build number is `W2W_BUILD_NUMBER` in `Config/Watch2Whoop.xcconfig` (bumped in the same PR); `tools/app_release.sh ios`
tags it and the workflow publishes this section as the release notes. The Flutter app's changelog stops at 1.0.8
(`apps/watch2whoop/CHANGELOG.md`).

| Version | Tag | Build | Store |
|---|---|---|---|
| 1.0.9 | `app-ios-v1.0.9` (on the release day, replacing the voided one: docs/ios-native-app.md section 12) | 10 | TestFlight (iOS) |

## 2.0.0

（开发中，docs/ios-native-2.0.md；各包的开关默认关闭）

- 某一部分转为手机计算（主模式）以后，服务器不再定时重算这部分，直接用手机上传的结果；手机还没传的时候服务器临时算一次补上。app 本身不变（服务器端 S9，按包逐个转正）。 / Once a part is computed on the phone (primary), the server stops recomputing it on its schedule and uses the phone's uploads; until the phone has uploaded, the server fills in on demand. No change in the app itself (server side S9, switched package by package).

- 服务器可以要求旧版本升级（R2，开关 `force_upgrade`，默认关闭）：提示期在顶部显示一条可以关掉的提醒（每天最多一次），停用日之后整页提示去升级；以后的版本也能这样强制升级。数据上传不受影响。 / The server can ask old versions to update (R2, flag `force_upgrade`, off by default): during the notice a banner at the top that can be closed (at most once a day), after the cut-off a full-screen page that points to the update; later versions can be forced the same way. Uploads keep working.

- 规则提醒、饮食目标和讲解弹层里的个人建议可以在手机上算（M6，开关 `native_rules`：影子模式只在后台对比，界面仍是服务器的；主模式后没网记了训练也会出提醒，讲解弹层没网也有个人建议），和服务器同一套规则、中英文一字不差，提醒由服务器抽查；在手机上点「不再提醒」会同步回服务器，AI 和其他设备都能看到。周报的饮食那一块改成在手机上按饮食目标算，只有添加糖和含糖饮料还用服务器的。 / Rule reminders, nutrition goals and the personal advice of the explanation sheets can be computed on the phone (M6, flag `native_rules`: in shadow only compared in the background, the pages keep the server's; once primary a reminder appears offline after logging a workout and the sheets give personal advice offline), with the server's rules and identical Chinese and English texts, reminders spot-checked by the server; 「Dismiss」 on the phone syncs back, so the AI and other devices see it. The report's nutrition block now resolves the goals on the phone; only added sugar and sugary drinks still come from the server.

- 周报 / 月报和「行为影响」可以在手机上算（M5，开关 `native_reports`：影子模式只在后台对比；主模式后没网也能打开），和服务器同一套算法；「行为影响」在开关 `journal_regression` 打开时显示控制了负荷、睡眠和星期几以后的影响（Newey-West 标准误 + BH 多重检验校正）。饮食那一块暂时仍用服务器的。 / The weekly and monthly report and 「Behaviour」 can be computed on the phone (M5, flag `native_reports`: shadow compares in the background; once primary they open offline) with the server's algorithms; with flag `journal_regression` 「Behaviour」 shows the effect adjusted for strain, sleep and weekday (Newey-West standard errors, Benjamini-Hochberg correction). The nutrition block still comes from the server.
- 身体年龄、负荷目标、健康监测、睡眠详情和今天页三环下的睡眠 / 评分块可以在手机上算（M4，开关 `native_coach`：影子模式只在后台对比，界面仍是服务器的；主模式后睡了一晚、没网也能完整打开今天页），和服务器同一套算法，身体年龄由服务器抽查。 / Body age, the strain target, the health monitor, the sleep detail and the Today rings' score / sleep blocks can be computed on the phone (M4, flag `native_coach`: in shadow only compared in the background, the pages keep the server's; once primary Today opens in full offline the morning after a synced night), with the server's algorithms, body age spot-checked by the server.

- 训练在手机上算：读「健康」里的体能训练，算 TRIMP、五个心率区间的分钟、训练后心率恢复和重复记录，训练月历和单日详情也能在手机上生成（M1c，开关 `native_workouts`，先只在后台对比，界面仍是服务器的），和服务器同一套算法、服务器抽查。 / Workouts are computed on the phone: the Health workouts with TRIMP, five-zone minutes, post-workout heart-rate recovery and duplicates, and the training calendar and day detail (M1c, flag `native_workouts`; shadow only for now, the pages still show the server's), with the server's algorithms, spot-checked by the server.

- 趋势、月历、最近 14 晚、睡眠规律性和 VO₂max 等级可以在手机上算（M3，开关 `native_series`），和服务器同一套算法；没网时也能看全部范围。 / Trends, the month calendar, the last 14 nights, sleep regularity and the VO₂max level can be computed on the phone (M3, flag `native_series`) with the server's algorithms, so every range opens offline.
- 每天的压力和趋势里的韧性、身体电量、运动后心率恢复可以在手机上算（M2，开关 `native_stress`：影子模式只在后台对比，界面仍是服务器的数；主模式后没网也会随时更新），和服务器同一套算法、服务器抽查。 / Daily stress and the Trends cards resilience, body battery and heart-rate recovery can be computed on the phone (M2, flag `native_stress`: in shadow only compared in the background, the pages keep the server's values; once primary they update during the day without network), with the server's algorithms, spot-checked by the server.
- 每天的步数、能量、锻炼分钟等数值和每晚的睡眠可以在手机上从「健康」派生（M1，开关 `native_derive`，先只在后台对比，界面仍是服务器的数），规则和服务器同一套、服务器抽查。 / Daily steps, energy, exercise minutes and the other day values, and each night's sleep, can be derived on the phone from Health (M1, flag `native_derive`; shadow only for now, the pages still show the server's values), with the server's rules, spot-checked by the server.

## 1.0.9

整个 app 换成了 iOS 原生界面（不再用网页），打开更快、滑动更顺。从 TestFlight 直接更新就行，**不用重新登录**；1.0.8 里的设置和同步进度都会接着用。

**更快、离线也能看**
- 今天、趋势、记录、饮食、朋友、我，全部是原生页面。打开就是上一次的数据，再在后台更新；没网的时候也能看。
- 记喝水、体重、日记、训练、点赞和评论，没网时先存在手机上，联网后自动发出去；没发出去的能在「我 → 没提交的修改」里重试或丢掉。

**分数在手机上算**
- 恢复、睡眠、负荷这些分数直接在你的 iPhone 上算，算法和服务器是同一份，服务器会抽查。删掉 app 重装后，从云端取回数据，在手机上接着算。

**同步**
- 同步中心：一键同步、卡在哪里一句话说清楚、补全历史（90 天 / 全部，后台也会继续）、按天数重新同步、每类数据手机和服务器各自最新的一条。
- 手表有新数据时 app 会在后台同步（睡眠、训练、HRV 等）；手机锁着读不到「健康」时，解锁后接着传。
- 不再需要 Open Wearables：新账号扫码就能同步。

**页面**
- 今天：睡眠分、恢复、昨天负荷三个环并排；当天有异常时三环下面列出「需要注意」（最多 3 项）；「今日核心」一眼看负荷 / 目标、恢复 vs 负荷、压力、生病风险、身体电量、HRV 状态、训练状态；核心指标、健康监测、提醒；「全部指标 →」直接看当天的全部数字。点开看睡眠详情和每个指标的讲解。
- 趋势：默认「今天」，当天的全部指标和相对你自己基线的方向；7 天 / 30 天 / 90 天 / 365 天的趋势和「比上期」，卡片上写明是「N 天均」，点开同时看到区间均值和当天的值；状态卡（HRV 状态、训练状态等），最近 14 晚，周报 / 月报。
- 记录：训练（肌肉负荷图、今天的计划逐组记录、动作限制、训练日历）、日记、饮食（今天吃了、快捷记餐、食谱、采购清单）、喝水、体重；「我」里有补剂、化验趋势、身体事件。
- Jarvis：底部中间的键，点开是对话 / 语音 / 拍照 / 快记；按住直接说话（往左上滑取消、往右上滑锁定）；回答里有卡片，删除前会先问你。
- 朋友：训练动态、点赞评论、成员卡片、排行榜、邀请朋友（邀请码、链接、图片）、管理圈子、头像。
- 登录：通过 Apple 登录、邮箱验证码、扫设置二维码 / 设置链接、激活码。

**第一次打开**
- 会弹一次「健康」授权（一个窗口问全部类型），请点「全部打开」再「允许」。
- 用语音、拍照时 iOS 会分别问一次麦克风、语音识别、相机。

**English**

The whole app is now native iOS (no web pages inside): it opens faster and scrolls more smoothly. Update from TestFlight
as usual; **you stay signed in**, and your settings and sync progress from 1.0.8 carry over.

- **Faster, and readable offline**: Today, Trends, Log, Food, Friends and Me are native pages that open on your last data
  and refresh in the background. Water, weight, journal, training, likes and comments are kept on the phone while you
  are offline and sent when you are back; anything that could not be sent can be retried or dropped in Me → Unsent changes.
- **Scores computed on your iPhone**: recovery, sleep and strain are computed on the phone with the same algorithm the
  server uses, and the server spot-checks them. After reinstalling, your data comes back from the cloud and the phone
  carries on.
- **Sync**: a sync center with one-tap sync, a plain-words reason when something is stuck, history backfill (90 days or
  everything, continuing in the background), re-sync by days, and the newest reading per type on the phone and on the
  server. New watch data syncs in the background; when the phone is locked, it continues after unlocking. Open
  Wearables is no longer needed: new accounts sync right after scanning the setup code.
- **Pages**: Today (three rings side by side, "Needs attention" when something is off, "Today's core" cards, core
  numbers, health monitor, reminders, "All metrics →", sleep detail and explanations), Trends (Today by default: the
  day's full metric list against your own baseline; every metric over 7 / 30 / 90 / 365 days labelled as an N-day mean,
  with the range mean and the day's value side by side in its sheet; status cards, the last 14 nights, weekly and
  monthly reports), Log (training with the muscle map and set-by-set logging, journal, food, water, weight; supplements,
  lab trends and body events under Me), Jarvis (tap for chat / voice / photo / quick log, hold to talk, cards, asks
  before deleting), Friends (feed, likes and comments, members, leaderboards, invites, circle settings, pictures).
- **Sign-in**: Sign in with Apple, an e-mail code, the setup QR code / link, or an activation code.
- **First launch**: one Health sheet asks for every type (tap Turn On All, then Allow). iOS asks for the microphone,
  speech recognition and camera the first time you use voice or photos.

### 开发说明 / Developer notes (#340 N0–N9)
- Project skeleton (#340 N0): SwiftUI app generated by XcodeGen. Includes:
  - five tabs (Friends in place of Food while circles are on), the design system from the Today page, Lucide icons;
  - API client (Bearer token, 401, retries) and Keychain migration from the Flutter app;
  - a GRDB database per account, the sync coordinator with an offline queue, the JavaScriptCore algorithm runtime;
  - the copied HealthKit sync code;
  - a minimal Today tab drawing the three rings from `/v1/users/me/today`.
- Native Today tab (#340 N3):
  - three rings side by side, core numbers, strain target, health monitor, body status (weight opens a native weigh-in sheet), reminders, sync and circles cards;
  - look-back calendar and pull to refresh;
  - the sleep detail page (stage chart in Swift Charts) and explain sheets with an 「问 AI」 hook;
  - everything is read from the local database; the N1 scoring bundle runs in JavaScriptCore and fills days the server has not scored once the phone has raw samples.
- Sign-in, sync center and Me (#340 N8):
  - sign in with Apple, an e-mail code, the setup QR code / `watch2whoop://setup` link, or an activation code; invite code for pending accounts; the consent (text and version from the server), the profile and Health access (one sheet for every type) follow once;
  - the sync center: one-tap sync, the overall state, where it is stuck in plain words with a button to fix it, first-sync progress, history backfill (90 days / all, continues in the background), re-sync of 7 / 30 / 90 days / all, phone vs server per type;
  - the sync engine's background work (BGTasks, HealthKit observers) and the beat-to-beat / ECG / water uploads run in the native app; no Open Wearables SDK;
  - Me: profile, AI settings (which AI, AI tokens), notifications, language, appearance (testers), what's new, data status, unsent changes, sign-out, account deletion; health record entries wait for N5;
  - upgrading from the Flutter app keeps the sign-in and the sync anchors.
- Circles (#340 N7), the Friends tab:
  - training feed (stats, heart-rate zones, exercises and sets), likes with a bounce and double-tap, comments (delete your own, report others'), pull to refresh, endless scrolling, a red dot for new likes / comments;
  - member cards (today's recovery / sleep / strain, the last 7 days, cheers, private nicknames) and leaderboards; switch between circles;
  - my circles: invites addressed to me, new circle, join with a code or a pasted link / message, invite with a code, a link and a picture card, revoke codes, share settings, pin / mute / archive, rename, logo, remove members, leave, dissolve; my picture;
  - opens on the last saved state, offline too; likes and comments wait in the offline queue; a like / comment notification opens the post with its comments;
  - a circle summary card other pages can show.
- Jarvis (#340 N6):
  - the Jarvis key at the bottom centre: a tap opens a Mission-style dial (chat / voice / photo / quick log); Liquid Glass on iOS 26, material before;
  - hold to talk, recognised on the phone: slide up-left to cancel, up-right to lock;
  - the chat streams its answer as Markdown, shows native cards (generic card for the other types), asks before deletes, keeps conversations, switches the AI at the top, asks for the fallback AI's terms first, shows quick questions for the current page;
  - photos (camera / library) and video frames (≤ 8);
  - `JarvisRouter.ask(prompt:)` for the explain sheets and other pages: the in-app chat, or the butler link / a copied question for an AI used elsewhere.
- Food, Log and the health record (#340 N5):
  - Food: what was eaten today against the targets (four rings) and each entry by meal, change the portion or delete an entry; water with +250 / +500 and undo; quick meals (log, log again, undo); recipes (search, by meal, detail with servings and meal, favourite); this week's shopping list (tick items); look back at another day;
  - Log: training (the muscle load map drawn natively, today's plan with set-by-set logging and submit, banned / caution exercises with the user's own reason, the week, exercise limits, the history calendar with each day's detail) and the journal (tags, numbers, body events); Food moves here while circles are on;
  - health record from Me: supplements with today's check-in, lab results with each test's trend chart, body events;
  - everything opens on the local copy, offline too; writes wait in the offline queue and show on the page at once.
- Native Trends tab and reports (#340 N4):
  - every WHOOP metric of `/trends` over 1 week / 1 month / 3 months / 1 year with "vs previous period", ending on any day (calendar); recovery, strain, sleep vs need, recovery vs strain, training load, weekly aerobic minutes and volume, heart-rate zones, sleep regularity, VO₂max fitness level;
  - Swift Charts with a tap / press-and-slide read-out of single days; a year's labels carry the year;
  - status cards as of the end date (HRV status, training status with load focus, resilience, illness risk, body battery, recovery time, lowest-HR timing, heart-rate recovery, chronotype); seven of them are computed on the phone with the algorithm bundle when the local history covers their window;
  - the last 14 nights (stages, schedule, the list opens the sleep detail page), the weekly / monthly report with the behaviour card;
  - every metric opens its explanation; with `ui_ai_first` an 「问 Jarvis」 bar on top.
- Release regression (#340 N9, `docs/ios-native-1.0.9-regression.md`):
  - on-device scoring reads HealthKit only from where the server has the phone's data (the first sync's window or as far as the backfill got); a new user's first sync uploads 14 days while the phone read 60, so every spot check failed;
  - the first-sync window is pinned when the account is attached, one hour inside the engine's own start, so the history backfill no longer stops hours short of it;
  - nothing is uploaded before the server has a scoring origin; once it has, the phone's rows before it go, and while the whole history fits in the raw window every run is a full recompute from the origin (the CTL / ATL start is the mean of the first 14 TRIMPs);
  - a setup link opened while the consent / profile / Health step is showing no longer gets lost: the step waits until the "sign in again?" question is answered;
  - simulator debug builds can write synthetic Health data (`SIMCTL_CHILD_W2W_DEV_SEED_HEALTH=21`);
  - version 1.0.9, build 10 (the voided Flutter 1.0.9 was build 9).
