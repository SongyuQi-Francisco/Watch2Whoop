# Changelog — native iOS app (apps/ios)

Releases are named by version only (docs/apps.md section 4): one section `## X.Y.Z` per store build, newest first. The
build number is `W2W_BUILD_NUMBER` in `Config/Watch2Whoop.xcconfig` (bumped in the same PR); `tools/app_release.sh ios`
tags it and the workflow publishes this section as the release notes. The Flutter app's changelog stops at 1.0.8
(`apps/watch2whoop/CHANGELOG.md`).

| Version | Tag | Build | Store |
|---|---|---|---|
| 2.0.0 | `app-ios-v2.0.0` | 11 | TestFlight (iOS) |

1.0.9 (build 10) was never released: its content ships in 2.0.0, and the `## 1.0.9` section below is a development record only. 1.0.9 从未发布：它的内容并入 2.0.0 一起发布，下面 `## 1.0.9` 一节只是开发记录。

## 2.0.1

- 交互更跟手：今天页打开弹层、进睡眠页、切 tab 不再先把整页重算一遍；按钮按下立刻有反馈；Jarvis 只有主按键保留液态玻璃，菜单收起时按键不再挤成一团。 / Snappier: opening a sheet or the sleep page and switching tabs no longer rebuild the whole Today page first; buttons react as soon as you press them; only the main Jarvis key stays Liquid Glass, and the menu no longer piles up when it closes.
- 语音输入：点「完成」后文字马上出现，服务器校对好的版本随后替换（你改过就不动）。 / Voice input: your words appear as soon as you tap Done, and the server's corrected version replaces them a moment later (unless you have edited them).
- 修复 Jarvis 有时把上一条回答再显示一遍（断线续读接到了下一问的回答上）。 / Fixed Jarvis sometimes showing a reply twice (a resumed connection picked up the next question's reply).

## 2.0.0

第一个原生 iOS 版本：整个 app 都是 iOS 原生页面（今天、趋势、记录、饮食、朋友、我、Jarvis），打开更快、滑动更顺；打开就是上一次的数据，没网也能看，记下的内容联网后自动发出去；恢复、睡眠、负荷这些分数在你的 iPhone 上算。这一版补上了网页版里还缺的主要功能：「我」页的账号和 AI 设置、饮食和采购、训练动作库、Jarvis 的卡片、今天页和睡眠的细节、圈子成员和家人共享给你的数据；还新增了消息中心、体态分析、圈子举报 / 屏蔽和上架需要的隐私说明。从 TestFlight 直接更新就行，**不用重新登录**；1.0.8 里的设置和同步进度都会接着用。第一次打开会弹一次「健康」授权，请点「全部打开」再「允许」。服务器上开关默认关闭的功能（消息中心 `inbox`、体态分析 `posture_photos`，以及下面各包的手机计算开关）要等服务器打开以后才会出现。

The first native iOS version: the whole app is native iOS pages (Today, Trends, Log, Food, Friends, Me, Jarvis) that open faster and scroll more smoothly; they open on your last data, work offline, and send what you logged when you are back online; recovery, sleep and strain are computed on your iPhone. This version adds the main features the web version had and the app lacked: account and AI settings under Me, Food and Shopping, the exercise library, Jarvis cards, the details of Today and Sleep, and viewing what circle members and family share with you; and it adds a message center, a posture check, report / block in circles and the privacy notes the App Store needs. Update from TestFlight as usual; **you stay signed in**, and your settings and sync progress from 1.0.8 carry over. The first launch asks for Health access once (tap Turn On All, then Allow). Features whose server flag is off by default (message center `inbox`, posture check `posture_photos`, and the on-phone compute flags below) appear only once the server turns them on.

- 薄肌模式（内测，开关 `slim_mode`）：我 → 外观可以换薄肌主题，今天页顶部每天一句。 / Slim mode (beta, flag `slim_mode`): Me → Appearance now has a slim theme, with a daily quote at the top of Today.

- 「我」页可以显示服务器下发的入口，在系统浏览器里打开，默认没有。 / The Me page can show links sent by the server, opened in the system browser; none by default.

- 回归修复：今天页滑动卡死、趋势页从图表上滑不动、冷启动点通知或邀请链接不跳转、饮食卡第一次不出现、克数超限后总量错误、动作库走不下去、屏蔽的邀请人无法恢复等。 / Regression fixes: Today freezing on scroll, Trends not scrolling from a chart, notifications and invite links on cold start, the food card missing on first open, wrong totals after an over-limit amount, a dead end in the exercise library, muted inviters that could not be restored, and more.

- 今天页 AI 优先（内测，开关 `ui_ai_first`，目前只对内测组开）：搜索框也能问 Jarvis，示例问题来自你自己的数据，长按卡片问 Jarvis，首页模块按关注、发现和 AI 用过排列，全部功能随时能看。 / AI-first Today (beta, flag `ui_ai_first`, on for the inner test group only): the search field also asks Jarvis, example questions come from your own data, long-press a card to ask about it, modules follow your focus, findings and what your AI used, and all features are one tap away.
- 现在可以在 app 里查看圈子成员和家人共享给你的数据（只读），他们的数据不会存在你的手机上。 / You can now view what circle members and family share with you in the app (read-only); their data is not stored on your phone.

- 今天页一键进同步中心，同步卡住时直接提示并能重试。 / The Today page takes you to the sync center in one tap, and tells you when sync is stuck so you can retry.
- 朋友发来的邀请链接现在直接打开 app，没装 app 时会告诉你怎么装。 / Invite links from friends now open the app, or show how to install it.

- 饮食页补齐多选合并、每餐推荐和饮食档案；采购补齐商品库、家里存货、价格记录和超市链接；采购页一键拍小票交给你的 AI 记价格；今天页加了饮食卡。 / The Food page gains multi-select merge, meal ideas and the diet profile; Shopping gains the product library, pantry, price records and store links; scan a receipt in one tap and your AI records the prices; Today gains a food card.

- 今天页可以看压力详情、今晚入睡建议和身体年龄详情，能前后翻日期；睡眠详情有规则要点；趋势加了 180 天和类别折叠（折叠随开关 `trends_v2`）。 / Today now has stress and body age details, tonight's bedtime advice and previous / next day; sleep detail shows key points; Trends gains 180 days and foldable categories (folding follows flag `trends_v2`).

- 「我」页新增消息中心，圈子邀请一步加入，还能把同圈子的朋友邀请进你的另一个圈子；开关 `inbox` 默认关闭，服务器打开后才会出现。 / New Messages in Me: join a circle invite in one tap, and invite friends from one circle into another of yours; flag `inbox` is off by default and the page appears only once the server turns it on.

- 原生 app 补齐训练功能：动作库和动作详情、切换分化、移除 / 恢复动作、我的动作、肌肉详情的推荐动作和让 Jarvis 分析，今天页加了训练卡和肌肉负荷卡。 / The native app now has the full training set: exercise library and details, switching splits, removing and restoring planned exercises, your own exercises, recommended exercises and Ask Jarvis on a muscle, and training and muscle load cards on Today.

- 「我」页补齐：可以在 app 里添加自己的 AI（API key）、绑定登录方式、邀请好友、查看和删除长期记忆、查看反馈记录。 / The Me page now has everything: add your own AI (API key), sign-in methods, invites, long-term memory and your feedback reports, right in the app.

- Jarvis 的所有卡片都换成原生显示，不再出现英文字段名；卡片上的按钮能直接打开对应页面。 / Every Jarvis card now has a native view with no raw field names, and its buttons open the matching page.

- Jarvis 可以看体态了：拍正面、侧面、背面 3 张，只发给你选的 AI，回答完服务器上不留原图；只对资料里满 18 岁的人显示；用内置的托底 Jarvis、或 AI 不在 app 里对话 / 不能看图时，现在只给拍照指南，可以改用文字描述；开关 `posture_photos` 默认关闭，服务器打开后才会出现。 / Jarvis can look at your posture: take front, side and back photos; they go only to the AI you picked, and the server keeps no originals after it answers. It is shown only to people whose profile says they are 18 or older; with the built-in fallback Jarvis, or an AI that does not chat in the app or cannot read pictures, it shows the photo guide only for now and you can describe it in words instead. Flag `posture_photos` is off by default and the entry appears only once the server turns it on.

- 训练计划里写成「A + B」的组合动作不再丢失（FB-21）：拆成两个动作，在记录 → 训练的今天卡片上按「超级组」放在一起，每个动作各记各的组数和重量，禁用 / 注意动作的提示各管各的。动作库认不出的名字也不会再消失，卡片上标「认不出」并给出可以换成的动作，点一下就换进周计划。 / Combination plan items written 「A + B」 no longer get lost (FB-21): they are split into two exercises and drawn together as one 「Superset」 on today's card under Record → Training, each exercise with its own sets and weights and its own banned / caution note. A name the library does not recognise no longer disappears either: the card marks it 「Not recognised」 and offers library exercises to swap it for, one tap puts it into the weekly plan.
- 采购清单可以复制 / 分享成文字（FB-19）：「饮食 → 采购」标题右边多了「复制」和「分享」两个按钮，整张清单按分类排好，每行「□ 名称 — 数量（备注）」，开头一行写明是哪一周、还差几项；已经打勾买过的不列出，复制后有提示，分享用系统的分享面板（也可以选「拷贝」），在超市里贴到备忘录或发给家人。 / The shopping list can be copied or shared as text (FB-19): 「Food → Shopping」 has 「Copy」 and 「Share」 buttons next to the title; the whole list comes grouped by category, one line per item as 「□ name — quantity (note)」, with a first line that says which week and how many are left. Items already ticked are left out, copying shows a confirmation, and sharing uses the system share sheet (which also has Copy), so you can paste it into Notes or send it at the supermarket.

- 手腕温度没有读数时说明原因，并告诉你怎么开（FB-18）：同步结果里「手机上没有新记录」的弹层中，手腕温度旁多了「怎么开」，点开是在「健康」里打开睡眠计划、睡眠专注模式和「用 Apple Watch 追踪睡眠」的步骤（iOS 没有直接打开专注设置的链接，app 只给步骤）；Apple Watch 只在戴着睡觉并开着这些的夜里才记手腕温度，第一次开启后约 5 晚才有读数，那一晚没有读数是手表没记录，不是同步丢了。健康监测里手腕温度没读数时的原因也改成这句，并有英文。 / Wrist temperature with no reading now says why and how to turn it on (FB-18): in the 「Nothing new on the phone」 sheet of the sync result, wrist temperature has a 「How to turn on」 button that shows the steps in Health to set a Sleep Schedule, turn on Sleep Focus and 「Track Sleep with Apple Watch」 (iOS has no link to the Focus settings, so the app only shows the steps). Apple Watch records wrist temperature only on nights it is worn in bed with these on, and needs about 5 nights before values appear, so a night without a reading is the watch not recording, not a lost sync. The health monitor's reason for a missing wrist temperature says the same, in both languages.

- 圈子可以举报和屏蔽（App Store 1.2）：动态右上角的「⋯」、长按评论、成员卡和成员列表的「⋯」里都能举报（选原因）或屏蔽这个人；举报后这条马上对你隐藏，屏蔽后你们在圈子里互相看不到，圈子共享暂停，可以在「我的圈子 → 已屏蔽」里取消。评论、圈子名和称呼里有不允许的词时，服务器会拒收，app 显示原因。打开开关 `circle_rules` 后，第一次建圈子、加入圈子或写评论之前先看并同意《圈子社区规则》。 / Circles can report and block (App Store 1.2): from a post's 「⋯」, a long press on a comment and the 「⋯」 of a member card or member row, report (with a reason) or block the person; a report hides the item for you at once, a block hides you from each other in circles and pauses circle sharing, undone under 「My circles → Blocked」. Comments, circle names and display names with words that are not allowed are refused by the server and the app shows why. With flag `circle_rules` on, the circle community rules are shown and accepted once before the first circle is created or joined or the first comment is written.

- 上架准备（隐私和合规）：「我」新增「关于」（隐私政策、使用条款、开源许可、版本），登录页底部可以打开使用条款和隐私政策；Jarvis 的回答下面标「AI 生成」，「生病风险」和「可能生病」旁标「非诊断」，点开是「不是医疗建议」的说明；权限说明（健康、相机、麦克风、语音识别）重写并有中英文；加了隐私清单（PrivacyInfo.xcprivacy）。通过 Apple 登录的账号删除时，服务器同时向 Apple 撤销本 app 的授权。 / App Store preparation (privacy and compliance): 「Me」 has a new 「About」 (privacy policy, terms of use, open-source licences, version), and the sign-in screen links to the terms and the privacy policy; Jarvis answers are marked 「AI-generated」 and 「Illness risk」 / 「Maybe ill」 carry 「Not a diagnosis」, each opening a short "not medical advice" note; the permission texts (Health, camera, microphone, speech recognition) are rewritten, in Chinese and English; the app has a privacy manifest (PrivacyInfo.xcprivacy). When an account that signed in with Apple is deleted, the server also revokes this app's access at Apple.

- 同步中心的结果前后一致（修复「同步按钮显示已最新，但仍提示部分数据未同步」）：同步完要么显示「已同步 · 时间」，要么列出每一项「类型 → 原因 → 一个按钮」——没同步（重新同步）、历史没补（比首次同步的 14 天还早，补全历史）、可能没开读取权限、没传上来、锁屏时读不到、「后台 App 刷新」关着（给出在 iOS 设置里自己打开的步骤）；手机上本来就没有新记录的（没戴表睡觉、没有体重秤、心电图要自己测、VO₂max 只在户外运动后有）单独一行，不算同步问题，说明在 i 弹层里。手机上最新的一条比同步读取的范围还早、服务器已经从导出或 Open Wearables 收到那一天的，不再显示「没同步」。 / The sync center's result is consistent (fixes "the sync button says up to date, yet some data is not synced"): after a sync it shows either 「Synced · time」 or one row per type with the reason and one button: not synced (re-sync), older than the synced range (older than the first sync's 14 days: fill in history), access may be off, not delivered, locked phone, Background App Refresh off (with the steps to turn it on in iOS Settings yourself). Types the phone has nothing new of (watch not worn in bed, no scale, ECG on demand, VO₂ max only after outdoor workouts) are one quiet row, not a sync problem, explained in the info sheet. A phone sample older than the synced range whose day the server already has from an export or Open Wearables is no longer "not synced".

- 新的「入睡目标」页（睡眠详情页底部和「我 → 设置」进入）：今晚和最终的入睡目标、最近 7 晚的入睡时间对照每晚目标、达标晚数和计划进度；可以设置、修改、清除目标，没网时先存在手机上，联网后自动提交。打开 `native_coach` 后目标也在手机上算，睡眠教练用的是真实的目标。 / New 「Bedtime goal」 page (from the bottom of the sleep detail and 「Me → Settings」): tonight's and the final target, the last 7 nights' bedtimes against each night's target, nights on target and the plan's progress; set, change or clear the goal, saved on the phone when offline and sent once back online. With `native_coach` on the goal is computed on the phone too, and the sleep coach uses the real goal.

- 某一部分转为手机计算（主模式）以后，服务器不再定时重算这部分，直接用手机上传的结果；手机还没传的时候服务器临时算一次补上。app 本身不变（服务器端 S9，按包逐个转正）。 / Once a part is computed on the phone (primary), the server stops recomputing it on its schedule and uses the phone's uploads; until the phone has uploaded, the server fills in on demand. No change in the app itself (server side S9, switched package by package).

- 服务器可以要求旧版本升级（R2，开关 `force_upgrade`，默认关闭）：提示期在顶部显示一条可以关掉的提醒（每天最多一次），停用日之后整页提示去升级；以后的版本也能这样强制升级。数据上传不受影响。 / The server can ask old versions to update (R2, flag `force_upgrade`, off by default): during the notice a banner at the top that can be closed (at most once a day), after the cut-off a full-screen page that points to the update; later versions can be forced the same way. Uploads keep working.

- 规则提醒、饮食目标和讲解弹层里的个人建议可以在手机上算（M6，开关 `native_rules`：影子模式只在后台对比，界面仍是服务器的；主模式后没网记了训练也会出提醒，讲解弹层没网也有个人建议），和服务器同一套规则、中英文一字不差，提醒由服务器抽查；在手机上点「不再提醒」会同步回服务器，AI 和其他设备都能看到。周报的饮食那一块改成在手机上按饮食目标算，只有添加糖和含糖饮料还用服务器的。 / Rule reminders, nutrition goals and the personal advice of the explanation sheets can be computed on the phone (M6, flag `native_rules`: in shadow only compared in the background, the pages keep the server's; once primary a reminder appears offline after logging a workout and the sheets give personal advice offline), with the server's rules and identical Chinese and English texts, reminders spot-checked by the server; 「Dismiss」 on the phone syncs back, so the AI and other devices see it. The report's nutrition block now resolves the goals on the phone; only added sugar and sugary drinks still come from the server.

- 周报 / 月报和「行为影响」可以在手机上算（M5，开关 `native_reports`：影子模式只在后台对比；主模式后没网也能打开），和服务器同一套算法；「行为影响」在开关 `journal_regression` 打开时显示控制了负荷、睡眠和星期几以后的影响（Newey-West 标准误 + BH 多重检验校正）。饮食那一块暂时仍用服务器的。 / The weekly and monthly report and 「Behaviour」 can be computed on the phone (M5, flag `native_reports`: shadow compares in the background; once primary they open offline) with the server's algorithms; with flag `journal_regression` 「Behaviour」 shows the effect adjusted for strain, sleep and weekday (Newey-West standard errors, Benjamini-Hochberg correction). The nutrition block still comes from the server.
- 身体年龄、负荷目标、健康监测、睡眠详情和今天页三环下的睡眠 / 评分块可以在手机上算（M4，开关 `native_coach`：影子模式只在后台对比，界面仍是服务器的；主模式后睡了一晚、没网也能完整打开今天页），和服务器同一套算法，身体年龄由服务器抽查。 / Body age, the strain target, the health monitor, the sleep detail and the Today rings' score / sleep blocks can be computed on the phone (M4, flag `native_coach`: in shadow only compared in the background, the pages keep the server's; once primary Today opens in full offline the morning after a synced night), with the server's algorithms, body age spot-checked by the server.

- 训练在手机上算：读「健康」里的体能训练，算 TRIMP、五个心率区间的分钟、训练后心率恢复和重复记录，训练月历和单日详情也能在手机上生成（M1c，开关 `native_workouts`，先只在后台对比，界面仍是服务器的），和服务器同一套算法、服务器抽查。 / Workouts are computed on the phone: the Health workouts with TRIMP, five-zone minutes, post-workout heart-rate recovery and duplicates, and the training calendar and day detail (M1c, flag `native_workouts`; shadow only for now, the pages still show the server's), with the server's algorithms, spot-checked by the server.

- 趋势、月历、最近 14 晚、睡眠规律性和 VO₂max 等级可以在手机上算（M3，开关 `native_series`），和服务器同一套算法；没网时也能看全部范围。 / Trends, the month calendar, the last 14 nights, sleep regularity and the VO₂max level can be computed on the phone (M3, flag `native_series`) with the server's algorithms, so every range opens offline.
- 每天的压力和趋势里的韧性、身体电量、运动后心率恢复可以在手机上算（M2，开关 `native_stress`：影子模式只在后台对比，界面仍是服务器的数；主模式后没网也会随时更新），和服务器同一套算法、服务器抽查。 / Daily stress and the Trends cards resilience, body battery and heart-rate recovery can be computed on the phone (M2, flag `native_stress`: in shadow only compared in the background, the pages keep the server's values; once primary they update during the day without network), with the server's algorithms, spot-checked by the server.
- 每天的步数、能量、锻炼分钟等数值和每晚的睡眠可以在手机上从「健康」派生（M1，开关 `native_derive`，先只在后台对比，界面仍是服务器的数），规则和服务器同一套、服务器抽查。 / Daily steps, energy, exercise minutes and the other day values, and each night's sleep, can be derived on the phone from Health (M1, flag `native_derive`; shadow only for now, the pages still show the server's values), with the server's rules, spot-checked by the server.

- 服务器打开公开注册（开关 `store_signup` 或 `open_signup`）时，登录页上的「我有设置链接或激活码」缩成一个小链接，主路径就是「通过 Apple 登录」/ 邮箱；新账号登录后直接可用，不再要激活码。 / When the server has open sign-up on (flag `store_signup` or `open_signup`), 「I have a setup link or activation code」 on the sign-in screen shrinks to a small link, so the main path is Sign in with Apple / e-mail; new accounts can use the app right after signing in, no activation code.

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
