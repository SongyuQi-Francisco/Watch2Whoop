# iOS 2.0.0 device checklist (collected by S9)

> 2.1.0 (the redesigned iPhone app and the Apple Watch app) has its own section at the end: [2.1.0](#210--the-redesigned-iphone-app-and-the-apple-watch-app).

Run on the owner's internal TestFlight build before primary goes to the `inner` cohort (runbook step 3 in
[ios-native-2.0.md](ios-native-2.0.md) section 4 S9). Synthetic or owner data only; write down pass / fail and field
names, never values of other people. Each section is the package's own entry ("Device checklist entry (for S9)" in the
contract); the S9 section at the end checks the server side.

Before you start: the owner is in the `inner` cohort, `native_sync` and every package flag are on for `inner` (shadow),
the app is 2.0.0 (`X-App-Version`), `GET /v1/sync/compute` lists every package as `shadow`.

## M1 — daily values and sleep (`native_derive`)
1. After a sync round, the sync center's compute report (meta `compute.last`) shows `derive` `done` with days written and
   `daily` / `sleep` uploaded; `GET /v1/sync/compute` shows both kinds with a passing last check.
2. Airplane mode the morning after a night: the round still derives (report `waiting_sync` only for the upload); with
   primary on, steps, energy and last night's sleep come from the phone.
3. Back online: one upload of the unsent days, then nothing on the next round; a pull does not duplicate
   `sleep_sessions` or `daily_metrics` rows.

## M3 — Trends series (`native_series`)
1. Shadow: open Trends on every range, the month calendar and 睡眠; meta `series.compare` shows `ok` for each page.
2. Primary, airplane mode: Trends opens on all ranges, Today's 「全部指标」 equals Trends 「今天」, the month calendar colours
   and the last 14 nights match the server's from before.
3. Trends 365-day scroll stays at or above 55 fps (ios-native-app.md section 9.3 method).

## M1c — workouts (`native_workouts`)
1. After a sync round the compute report shows `workouts` `done`, `workouts:<day>` uploaded; `GET /v1/sync/compute` shows
   the kind with a passing check.
2. Record a workout offline: the next round (still offline) writes it to `workouts` with TRIMP, zones and HRR; meta
   `workouts.compare` shows `ok` for the calendar and the day once online.
3. Primary: the training calendar and the day sheet open offline with the same numbers as the server's before.

## M2 — stress, resilience, body battery, HR recovery (`native_stress`)
1. After a sync round the compute report shows `stress` `done` with days written and uploaded; `GET /v1/sync/compute`
   shows kind `stress` with a passing last check.
2. Open Trends: meta `stress.compare` is `ok` (resilience, body battery, heart-rate recovery equal the server's).
3. Primary, airplane mode during the day: Today's 「压力」 and 「身体电量」 and Trends 「韧性」 update after a sync round
   without network.

## M4 — coaches, body age, Today composition (`native_coach`)
1. After a sync round the compute report shows `healthspan` `done` with weeks written and uploaded; `GET /v1/sync/compute`
   shows kind `healthspan` with a passing last check.
2. Open Today and a night's sleep detail: meta `coach.compare` is `ok` for `strain_coach`, `health_monitor`,
   `today_scores`, `today_sleep`, `sleep_night`.
3. Primary, airplane mode the morning after a synced night: Today renders the rings, strain target, health monitor and
   the sleep sheet; Trends' body-age series is the phone's.

## M5 — reports and journal analysis (`native_reports`)
1. Open Trends → 周报, then 月报: meta `reports.compare` shows `ok` for `report_week`, `report_month` and
   `journal_analysis` (`…_regression` with `journal_regression` on).
2. Primary, airplane mode: the weekly and monthly report and 「行为影响」 open with the same numbers as before; the nutrition
   tiles stay the cached server values.
3. Log a journal answer online, refresh: the card's counts follow after the next pull.

## M6 — rules, nutrition targets, explain texts (`native_rules`)
1. After a sync round the compute report shows `rules` `done` with `reminders:<yesterday>` / `reminders:<today>` written
   and uploaded; `GET /v1/sync/compute` shows kind `reminders` with a passing last check; meta `rules.compare` shows `ok`
   for `reminders`.
2. Open 3 metric sheets (HRV, 睡眠时长, 恢复): meta `explain.compare` is `ok` for each.
3. Primary, airplane mode: log a workout in the app's offline queue, the next round shows today's pre-workout reminder;
   a metric sheet shows its personal advice offline; 「不再提醒」 on a reminder offline, back online: the AI's
   `get_reminders` lists it dismissed after the next upload, and the dismissal survives a pull.

## S9 — the server stops computing (server side, on the primary server)
1. `body-engine app-compute status --user-id 1`: every package `granted` and `server_primary` true after runbook step 3;
   `gates` per kind with at least 3 checks and a pass rate of 1.0 (or ≥ 0.95 with the failing field names understood).
2. The next hourly job's log: `evaluate-rules user 1 … app 计算（rules 已转正）`, `compute-scores` with
   `"app_scoring": true` and no `insights` writes (`"healthspan": "app", "stress": "app"` when it runs them); hk-derive-run
   reports `phone_primary`, `phone_days`, `waiting`, `fallback_days` (fallbacks near zero).
3. `app-compute status` → `fallbacks`: only occasional rows (the phone was asleep); a growing count for one package means
   the phone stopped uploading it: look at `GET /v1/sync/compute` (last upload, last check).
4. Ask the AI (MCP) for stress, body age, reminders and a training day: it answers with the phone's numbers.
5. Kill switch drill: `body-engine flags set native_compute_primary --state off`, wait 30 s: `GET /v1/sync/compute` shows
   every package `shadow`, the next hourly job computes the user again (`stress_days` in its log); set it back.

## 2.1.0 — the redesigned iPhone app and the Apple Watch app

Run on the owner's iPhone **and Apple Watch** (watchOS 11 or later, paired) with the internal TestFlight build of
2.1.0 (12), before the external group gets it ([ios-native-2.1.0-handover.md](ios-native-2.1.0-handover.md), 「中控自己的清单」).
Nothing of this has run on a real device yet: the 2.1.0 PRs were checked in the simulator only. Each line is
**do → expect**; write down pass / fail per id. Owner data or synthetic data only.

Before you start: the iPhone is on 2.0.0 with a synced history; the server runs the `main` that contains the release
PR (`GET /v1/algo/manifest` says `0.3.0` with a signature); the watch has a passcode and is on the wrist.

### U — upgrade
- **U1** Install 2.1.0 over 2.0.0 from TestFlight → it opens signed in (no sign-in screen), Today shows the last data at
  once, Me → 帮助与关于 → 版本 shows 2.1.0 with build 12.
- **U2** Watch: Automatic App Install on → the app appears on the watch by itself; off → Watch app on the iPhone → My
  Watch → Watch2Whoop → Install → it installs. The watch app's version is 2.1.0 (12) too.
- **U3** After the first sync round on 2.1.0 → the phone's score upload carries bundle `0.3.0`
  (`app_score_checks.bundle_version` / `app-compute status --user-id <self>`), no `stale_algo`.

### P — iPhone: tabs, the three levels, the first screens
- **P1** Look at the tab bar → 今天 · 身体 · 记录 · 圈子 · 我; no Food tab; an old Food link (a notification or a Jarvis
  card that opens Food) → opens 记录 → 饮食.
- **P2** Today, first screen → recovery dial (grey lane = your usual, last 7 days as small bars, one word) and the sleep
  and strain readings, each with 「›」; nothing hides below a blank gap; the sync capsule is in the header.
- **P3** Tap the dial → 「今日详情」 (level 2): all metrics, current status, needs attention, vitals, training · tonight ·
  reminders on the first row; ＋ on the weight row → logs a weight that shows after the next pull.
- **P4** Tap a number (HRV) → its page (level 3): today vs your usual, 7 / 30 / 90 days / 1 year switch, trend with the
  7-day mean, mean / high / low / vs before, related metrics, the explanation at the bottom; back returns to level 2.
- **P5** The three marks in the header show the level; tapping them goes one level down; a swipe up on the first screen
  opens 今日详情 → the same page as P3.
- **P6** Tap the date → month calendar; swipe left / right → another day; looking back shows 「回到今天」 → back to today.
- **P7** Body: the range menu 7 / 30 / 90 / 365 and 「选择结束日期」; tap the score → its page; tap the bars → all trends
  (指标 · 睡眠 · 报告).
- **P8** 记录 → 训练: the set ring with the body drawings; tap the ring → today's training; tick the first set → the timer
  starts and shows on the session page and under the ring; airplane mode, tick a set, back online → the set and its
  start time reach the server (the training day on the web / AI shows it).
- **P9** 记录 → 训练, muscle load → 「本周 | 4 周」 switches, 4 weeks coloured against the four-week range; the library
  shows its total; star an exercise, filter to starred → only it.
- **P10** 记录 → 饮食, 圈子: under the 「⌄」 the next block peeks above the Jarvis key; scroll to the end → the last row
  rests above the key, nothing under it.
- **P11** Circles: a comment's 「…」 → delete (own) / report and block (someone else's); Me → 消息, open one → 「隐藏这条」.
- **P12** English (Me → 偏好设置 → language): strain, the sleep chart, muscle load, the weekly plan's groups and a long
  exercise name are not cut.
- **P13** If you have a phone with a Home button (iPhone SE class) at hand: the Jarvis key does not cover the tab bar
  and the first screens fit.

### R — someone else's data (read-only member view, #474)
Written from #474's plan before it merged: check these lines against its final description.
Needs a person who shares with you (a circle member or a family share), with scores shared.
- **R1** Circles (or Me → 共享 → 共享给我的) → open that person → their first screen is the same glance as your Today
  (dial, sleep and strain readings, at most three faces, each only for what they share), with an identity bar on top
  (face, name, read-only lock, ✕) on every level; no sync capsule, no Jarvis key, no ＋ on the weight row.
- **R2** Tap their dial → their 今日详情; tap a number → its metric page (7 / 30 / 90 days / 1 year); sleep's 「整晚 ›」
  → the night page; 「健康档案 ›」 and 「周报 ›」 appear only when they share those.
- **R3** ✕ on any level → back where you came from; nothing of theirs is left on your Today.
- **R4** A person who shares no scores → no dial and no readings, only the faces they do share; nothing can be written.

### Z — zoom between levels (#474)
Written from #474's plan before it merged: check these lines against its final description.
- **Z1** iOS 18 or later, Reduce Motion off: tap the dial, a reading or a face → the next level grows out of the item you
  tapped; back (or the edge swipe) → it shrinks back into that item. Same on Body and on a member's view.
- **Z2** Settings → Accessibility → Motion → Reduce Motion on → the same taps push the page in the plain way, nothing
  grows.
- **Z3** Openings without a tapped item (a notification, a deep link, the header's level marks) → plain push.
- **Z4** Go down to level 3 and back up five times quickly → no stutter, no half-drawn page, back always returns to the
  item you came from.

### S — sync center
- **S1** Open the sync center right after a sync while the server is still computing → one answer, 「已上传，分数计算中」;
  never 「已同步」 next to 「同步连接失效」.
- **S2** Turn Background App Refresh off for Watch2Whoop (Settings → Apps → Watch2Whoop) → the sync center shows
  「后台 App 刷新关着」 as its own row (not a failure), the Today capsule and banner say the same; tap the row → this app's
  Settings page; turn it back on and return → the warning is gone at once.
- **S3** Repeat with the phone-wide switch (Settings → General → Background App Refresh) and with Low Power Mode → each
  names its own cause and path.
- **S4** Leave the app alone overnight with everything on → the next morning no background warning, and the sync
  center shows 「上次后台同步」 with a time in the night. (If iOS never woke it in 12 hours, the warning is expected:
  note it, it is the behaviour, not a failure.)
- **S5** 「最近 30 天 · 缺 N 天」 → the missing days newest first with the categories (睡眠 / 心率 / HRV / 步数);
  「重新同步」 on one day → that day only; 「全部重新同步」 asks first; a day Health has nothing of says 「健康里也没有」
  and is not prompted again.
- **S6** 「更多工具」 → the 2.0.0 sync page with every tool (sync log, range re-sync, history).

### J — Jarvis
- **J1** Tap the Jarvis key → round keys on a thin orbit (记一餐 / 记训练 / 说话 / 拍照 / 提问) with their names under
  them; a tap slightly off a key still hits it (the whole wedge), a tap far outside closes the panel.
- **J2** 记一餐 → the second ring with your two most used quick meals showing their kcal; tap one → logged, a reply card
  with undo; undo → gone. 返回 is easy to hit (44 pt).
- **J3** 说话, say a question in Chinese, stop → the words appear at once. With `jarvis_voice_direct` **off** (the
  default) they wait for 「发送」; the server's corrected text replaces them a moment later unless you edited them. Never
  sent without your tap.
- **J4** Ask the same question twice in a row, with the network dropping once in between (airplane mode for a few
  seconds) → each reply shows once, never the previous one again.
- **J5** Open and close the panel ten times quickly → no stutter, keys never pile up when closing; only the main key is
  Liquid Glass.

### M — Me
- **M1** Me, first screen → only the common entries; the rest under 健康档案 / 数据与导入 / 偏好设置 / 帮助与关于 / 账号;
  日记 is in the 数据 group (no longer under 记录 → 饮食); 通知 is under 偏好设置.
- **M2** 共享 → how many people can see your data with their faces → the full list; 共享给我的 one row below → their list.
- **M3** On the fallback AI → the AI face shows the share of this month's allowance used as a percentage (no money).
- **M4** 消息 and the circle feed with something unread → who it is from.

### W — Apple Watch app
- **W1** Open the iPhone app once, then the watch app → 「今天一眼」: recovery dial (number, arc, one word), sleep and
  strain equal to the phone's Today; the sync mark top left shows the same state as the phone's capsule.
- **W2** Leave the phone off the network for more than 6 hours (or wait) → the numbers turn grey with 「6时前」.
- **W3** Training day: tap today's plan → the start screen → start → each set one big 「重量 × 次数」; the Digital Crown
  changes it; a tap (or Double Tap) logs it → rest starts by itself (countdown, live heart rate, a tap on the wrist at
  zero) → next set … → the summary → 「完成」 returns home.
- **W4** During W3, lower the wrist for a minute → the rest countdown kept running (workout processing); afterwards the
  strength workout is in Health on the iPhone (Fitness / Health → Workouts) and the phone's strain counts it.
- **W5** Start a session, force-quit the watch app (or restart the watch) mid-session, open it again → it resumes at the
  same set.
- **W6** Phone away (iPhone in another room or Bluetooth off), log 2 sets → the sync mark says 「待传 2」; phone back →
  the sets appear in 记录 → 训练 for today, once each (no duplicates), and the training day submits at 「完成」.
- **W7** Rest day: the left key says 「记一餐」 → quick log. On any day: tap the Jarvis core → quick log, four tiles
  (three most used quick meals and water) → tap one → a receipt (kcal, protein, today's bar) → the meal / water shows
  on the phone's 记录 → 饮食; 撤销 asks first → it is removed on the phone too.
- **W8** W7 with the phone away and the watch offline → logged on the watch, sent when the phone is back (once).
- **W9** The phone has no plan for today yet (or the plan changed) → home says 「计划没同步」; tap it → asks the phone
  again; after the phone has it, the start screen shows the new plan.

### V — hold to talk on the watch
Voice only appears for an account whose AI runs in the app (own API key or the fallback AI, agreed to) **and** with
`jarvis_voice_direct` on (`WatchVoiceOffer.of`). That flag must stay off until the consent texts are changed (registry
note; [ios-native-2.1.0-handover.md](ios-native-2.1.0-handover.md)), so on the owner's own account:
- **V0** With `jarvis_voice_direct` off → holding the core gives a failure tap, the core stays dimmed; a tap still opens
  quick log. This is the expected state for every account in 2.1.0 until the owner decides otherwise.
- **V1–V6 only if the owner turns the flag on for themselves after changing the consent:**
  - **V1** First hold → the watch asks for the microphone; allow → recording while held (30 s at most); slide up →
    cancelled, nothing sent.
  - **V2** Say a meal → the words stay 1.5 s (cancel possible) → sent → receipt with kcal, protein and today's progress;
    the meal shows on the phone. 撤销 asks first → removed (with the phone away: queued, removed when it is back).
  - **V3** Ask a question → a short answer; a long one has 「在手机上继续」 → Jarvis on the phone opens the same
    conversation.
  - **V4** Ask to delete something → the watch asks before doing it.
  - **V5** Watch offline (phone away, no Wi-Fi) → its own message and a way on (retry / system dictation / home).
  - **V6** Sign out on the iPhone → the watch's voice stops working at once (its credential is revoked); sign in again →
    it pairs again by itself, no sign-in on the watch.

### C — watch-face complications
- **C1** Edit a face → Complications → Watch2Whoop offers Recovery (circular), Today (rectangular), Start workout
  (circular); add all three.
- **C2** Recovery shows today's number and arc in the zone colour, equal to the watch home; old data → grey; no recovery
  today → 「—」. Tap → the watch app's home.
- **C3** Today (rectangular) shows recovery, sleep and strain; during a session it shows the rest countdown, 「2/4」
  and the next set's weight × reps, and the countdown runs on the face with the wrist down and up (Always On).
- **C4** Start workout: dumbbell on a training day → T1; bowl on a rest day → quick log; during a session any of the
  three → the current set.
- **C5** A morning sync on the phone → the complications show the new recovery within 15 minutes (V6 in
  `apps/ios/Watch2WhoopWatch/README.md`: not measured yet; write down how long it took).

### L — phone ↔ watch link
- **L1** Change today's plan on the phone (swap an exercise) → the watch's start screen shows it after the next push
  (open the watch app to check).
- **L2** Log a set on the phone and one on the watch for the same exercise → both are in the day's draft, no duplicates.
- **L3** Sign out on the phone → the watch shows the signed-out state (S8), the complications 「—」; sign in → it comes
  back.
- **L4** Unpair or switch off the watch → the iPhone app runs as before, no errors in the sync center.
