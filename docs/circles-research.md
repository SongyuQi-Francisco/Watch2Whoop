# 圈子调研：同类产品怎么设计群组（2026-10-25）

> 给 [`circles-redesign.md`](circles-redesign.md) 做依据。只用公开资料（官方帮助中心为主，官方没写的用社区帖 / 媒体报道补充，
> 已在表里标注）。截至 2026-10-25 能查到的版本，产品以后可能会改。Issue：#269。

## 0. 我们现在的问题（对照用）

现有实现（`friends/store.py`、`cards.py`、`feed.py`、`leaderboard.py`）：

- 两个人之间的共享是**跨圈子取并集**（`shares.circle_scopes` = A 在所有和 B 共同的圈子里勾过的范围之和）。界面上是「每个圈子单独选」，
  但实际看到的是并集；
- 朋友页的「成员 / 动态 / 排行榜」都是「所有和我有共同圈子的人」混在一起（`cards.co_members`、`feed.visible_authors`）；
- 角色只有圈主一种：只有圈主能发邀请码；成员没法邀请，也就没有审批；
- 每人最多 10 个圈子（`MAX_CIRCLES_PER_USER`），每圈默认最多 30 人；没有置顶、静音、归档；
- 入圈来源只部分记录：推荐码的归属（`invite_redemptions.inviter_user_id`）、定向邀请的创建者（`circle_invites.created_by`）、
  圈子激活码的使用（`circle_code_uses`）都在库里，但 `circle_members` 本身不记「谁邀请的、通过什么进来的」，页面上也看不到。
  实际碰到过：一位新用户用圈主本人的个人推荐码注册，系统自动发了一张圈主名下的定向邀请，用户接受后入圈。库里能拼出来龙去脉，
  页面上看不到；推荐码是谁转发出去的，系统无从知道。
  （而且这位圈主没有个人圈子，推荐码退回到了他建的俱乐部圈子——这是 bug，个人推荐码不该带人进俱乐部圈子，#273 已修，见方案 5.3。）

## 1. 逐个产品

### 1.1 Strava 俱乐部（Clubs）
- **组织 / 分层**：创建者是 owner（转让 owner 之前不能退出），owner 可以任命 admin；admin 能审批、提升 / 撤销 admin、移除成员、
  编辑俱乐部资料，只有 owner 能删除俱乐部。[^strava-clubs]
- **动态 / 排行榜**：**每个俱乐部单独一份**。俱乐部页有自己的 Recent Activity（只放和俱乐部运动类型相关的活动；5 万人以上的俱乐部没有这个 feed）
  和自己的周排行榜（本周 / 上周，周日 23:59 按俱乐部时区重置；单项运动按距离、多项运动按时间排；网页显示前 100、手机显示前 10）。[^strava-clubs]
  俱乐部帖子（club posts）会进主页 feed，可以「Mute Posts From Club Members」按俱乐部静音（admin / owner 的帖子仍显示），
  admin 可以设成只有 admin 能发帖。[^strava-posts]
- **很多群组时**：官方没写加入俱乐部的数量上限；俱乐部讨论的通知设置**对所有俱乐部统一，不能按俱乐部设**。[^strava-clubs]
- **邀请 / 准入**：两种俱乐部——公开（谁都能加）和 invite-only（owner / admin 邀请或审批申请；非成员看得到俱乐部，看不到动态和讨论）。
  站内邀请要求双方互相关注；邮件邀请已关闭，其他方式是分享俱乐部链接。[^strava-clubs] 公开资料里没看到「谁邀请了谁」的记录。
- **隐私**：活动可见性是按活动 / 个人设的（含「Only You」），不是按俱乐部设；可以把单条活动从主页 / 俱乐部 feed 里静音。[^strava-mute]
  大俱乐部的成员列表只显示你关注的人。[^strava-clubs]
- **扩列**：俱乐部可以搜索、公开俱乐部直接加入；扩列靠公开俱乐部，社群氛围靠 invite-only 俱乐部。

### 1.2 WHOOP Teams
- **组织 / 分层**：team owner 建队，可以加人 / 移除、改队伍设置。[^whoop-teams]
- **动态 / 排行榜**：每个 team 有自己的 Strain / Recovery / Sleep 排行榜，按日 / 周 / 月切换。[^whoop-teams][^whoop-lb]
- **很多群组时**：社区回答说一个人最多同时在 10 个 team 里（非官方）。[^whoop-multi]
- **邀请 / 准入**：要收到 owner 的邀请才能加入；可以关掉「我的资料能被搜到」，只通过队伍的专用码被邀请。[^whoop-teams][^whoop-privacy]
- **隐私**：建队时由 owner 选共享哪些指标；**接受邀请之前能看到要共享哪些数据**（社区回答，非官方）。[^whoop-privacy]
- **扩列**：WHOOP 有公开的社区 team（按兴趣 / 运动），私人 team 走邀请。

### 1.3 Oura Circles
- **组织 / 分层**：圈子有 admin，可以在圈子设置里移除成员；被移除的人会在圈子页看到提示，他发出去还没用的邀请自动失效。[^oura]
- **动态 / 排行榜**：每个圈子单独看，只显示近两周的高层评分（Readiness、Sleep、Activity），可以按住拖动看以前的天；成员间只能用表情回应（24 小时消失），
  没有私信。[^oura][^oura-ac]
- **很多群组时**：**每人最多 10 个圈子，每圈最多 20 人**（官方帮助中心；早期报道写过每圈 10 人）。[^oura]
- **邀请 / 准入**：生成**一次性邀请链接，7 天有效**；只有 Oura 用户能加入。[^oura]
- **隐私**：**每个圈子单独选**共享哪几项评分、给每日分还是周平均，随时能改。[^oura] 这是我们「每圈选范围」最直接的参照。
- **扩列**：没有发现 / 推荐，纯熟人。

### 1.4 Apple 健身（活动共享）
- **组织 / 分层**：没有「群」，是一对一的好友关系（第三方指南说最多 40 个好友，Apple 官方页没写数字）。[^apple][^apple-40]
- **动态 / 排行榜**：所有好友在一个「共享」列表里，一对一发起比赛。
- **很多好友时**：**按人**「静音通知」和「隐藏我的活动」（对这个人隐藏，随时恢复），和「移除好友」分开。[^apple]
- **邀请 / 准入**：一对一邀请，对方接受。
- **隐私 / 扩列**：按人控制；没有发现机制。
- 启示：好友少的时候「一个混合列表」够用；一旦有群，混合列表就不够了——这正是我们现在的状态。

### 1.5 Garmin Connect 群组（Groups）
- **组织 / 分层**：任何人都能建群，建群的人是 admin，可以再加 admin；只有创建者能删除群。[^garmin]
- **准入 / 可见**：创建者选「谁都能加入」还是「需要批准」，以及群页对所有 Garmin Connect 用户可见还是只对成员可见。
  邀请可以站内搜人，也可以把链接分享到社交媒体 / 俱乐部网站。[^garmin]
- **动态 / 排行榜**：每个群有自己的动态、排行榜和挑战；但**排行榜是否显示一个人取决于他自己的活动隐私设置**，不是群设置，
  用户经常困惑（社区帖）。[^garmin-lb]
- 启示：「群设置」和「个人隐私」两套规则叠在一起时，一定要在界面上说清楚「你在这个群里别人能看到什么」。

### 1.6 Hevy
- **组织**：关注（follow）模型，没有群组。私密账号要别人申请关注、你批准；单条训练可以只自己可见。[^hevy]
- **扩列**：有 Discover（全球热门训练）和推荐用户，可以关掉推荐用户，但 Discover 关不掉。[^hevy]
- 启示：follow 模型扩列容易，但隐私敏感的人只能「整体私密」，粒度粗。

### 1.7 Keep
- 有跑团 / 小组类功能，但这次没找到可靠的公开说明（帮助中心、更新说明都没检到），**不作为依据**，以后有资料再补。

### 1.8 微信运动 / 微信群
- **微信运动**：所有微信好友在一个排行榜里（全混合）；可以把某个好友加到「不与他（她）排行」，双方互相看不到步数。[^wxsport]
  这是「混合列表」的极端：好友一多，排行就失去意义，只能靠黑名单。
- **微信群**：群主可以开「群聊邀请确认」，开了以后**群成员邀请新人要群主（或管理员）确认**，用来防止陌生人随意入群；
  新人入群时群主 / 管理员能收到提醒。[^wxgroup-sspai][^wxgroup-pedaily]（公开资料较旧，以当前版本为准；这次没找到官方说明里写
  「查看邀请人」的入口。）
- 启示：中国内地用户熟悉的是「**成员能邀请 + 群主确认**」这一套，而不是「只有群主能邀请」。

### 1.9 Discord
- **组织 / 分层**：服务器 → 角色（role）→ 权限，层级最细。
- **准入**：三种——仅邀请（默认）、申请加入（管理员审核申请表）、可被发现（Discovery）。邀请链接可以设过期时间和最多使用次数。[^discord-join]
- **邀请链**：管理员 / 版主看的「Members 页」显示每个成员**什么时候加入、账号多老、用的哪个邀请链接**、角色和异常标记；普通成员看不到。[^discord-members]
  这个页面出现之前，社区长期要求「显示新成员用了哪个邀请」，靠第三方 bot 比对邀请次数来猜。[^discord-req]
- **很多群组时**：服务器文件夹、按服务器静音；社区一直在要求「按文件夹静音」，说明「按群静音」是基本需求、批量静音也有需求。[^discord-folder]
- **审计**：审计日志保留 45 天，记录管理操作。[^discord-audit]

### 1.10 Telegram 群组和频道
- **很多群组时**：聊天文件夹（Folders）、归档（Archive；被静音的聊天留在归档里，没静音的来新消息会回到主列表）、置顶（文件夹里置顶不限数量）、
  批量置顶 / 静音 / 归档，多设备同步。[^tg-folders]
- **邀请 / 准入**：可以建多条**命名邀请链接**（例如按推广渠道命名），可以给链接开「需要管理员批准」（等候室）；管理员能按链接批量批准 / 拒绝。[^tg-links][^tg-api]
- **邀请链**：Bot API 的成员变动事件带 `invite_link`（他用的是哪条链接）、`via_join_request`（是否经审批）等字段。[^tg-cmu]
  即「每条链接带来了谁」是一等公民。
- 启示：命名链接 + 按链接看来源 + 可选审批，正好解决「新成员是谁拉进来的」。

## 2. 对照表

| 产品 | 群组单位 | 动态 / 排行榜 | 很多群组怎么管 | 谁能邀请 / 审批 | 邀请链记录 | 隐私粒度 | 扩列 |
|---|---|---|---|---|---|---|---|
| Strava 俱乐部 | 俱乐部（owner / admin / 成员） | **按俱乐部**（自己的 feed、周榜） | 按俱乐部静音帖子；通知不能按俱乐部设 | owner / admin；invite-only 要审批 | 未见 | 按活动 / 个人 | 公开俱乐部 + 搜索 |
| WHOOP Teams | team（owner / 成员） | **按 team**（日 / 周 / 月榜） | 最多约 10 个（非官方） | owner 邀请 / 队伍码 | 未见 | 按 team（owner 选）；入队前可见 | 公开社区 team |
| Oura Circles | 圈子（admin / 成员） | **按圈子**（近两周评分） | 最多 10 圈、每圈 20 人 | 一次性链接（7 天） | 未见 | **按圈子**、按评分、日 / 周平均 | 无 |
| Apple 健身 | 一对一好友 | 混合列表 + 一对一比赛 | 按人静音、按人隐藏 | 一对一 | — | **按人** | 无 |
| Garmin 群组 | 群（多 admin） | **按群**（动态、榜、挑战） | — | 开放 / 需批准；链接 | 未见 | 群可见性 + 个人活动隐私（易混） | 公开群 + 链接 |
| Hevy | follow | 关注流 + Discover | — | 私密账号要批准关注 | — | 账号 / 单条训练 | Discover、推荐用户 |
| 微信运动 | 全部好友 | **全混合** | 「不与他排行」黑名单 | — | — | 按人（双向） | 无 |
| 微信群 | 群（群主 / 管理员） | 群内 | 置顶 / 免打扰 | 成员邀请 +（可选）群主确认 | 入群提醒给群主 / 管理员 | 群级 | 二维码 / 拉人 |
| Discord | 服务器 + 角色 | 按服务器 / 频道 | 文件夹、按服务器静音 | 仅邀请 / 申请加入 / 发现 | **Members 页：用了哪个邀请**（管理员可见） | 角色权限 | Discovery |
| Telegram | 群 / 频道（admin） | 按群 | 文件夹、归档、置顶、批量 | 命名链接、可选审批 | **按链接看加入者**；API 带 `invite_link` | 群级 | 公开群 / 频道链接 |

## 3. 结论：借鉴什么 / 不借鉴什么

### 借鉴
1. **群组是边界**：凡是「有群」的产品（Strava、WHOOP、Oura、Garmin、Telegram、Discord），成员、动态、排行榜都**按群分开**；混合列表只出现在
   一对一好友模型（Apple、微信运动），而且好友一多就要靠黑名单补救。→ 朋友页按圈子分开，「全部」只做汇总入口。
2. **每个群单独设共享范围，入群前先看到要共享什么**（Oura、WHOOP）。→ 保留「每圈选范围」，并让各圈视图只按这个圈子的范围显示。
3. **很多群时：上限 + 静音 + 置顶 / 归档 / 文件夹**（Oura / WHOOP 10 个上限；Telegram 归档 + 置顶；Discord 文件夹 + 静音；Strava 按俱乐部静音帖子）。
   「静音」是最常用的、必须按群设（Strava 不能按俱乐部设通知是被诟病的点）。
4. **三层角色**：owner（唯一，可转让）/ admin（可多个）/ 成员（Strava、Garmin、Telegram、微信群）。
5. **成员可以邀请，但可以要求 owner / admin 审批**（微信群「群聊邀请确认」、Telegram 等候室、Strava invite-only）。这是「扩列」和「维持社群」之间的开关。
6. **邀请链给管理者看，不给成员看**（Discord Members 页、Telegram 按链接看加入者）；**命名邀请链接**（Telegram）让来源一眼可辨。
7. **一次性 / 有期限的邀请**（Oura 一次性 7 天；Discord 过期 + 次数）——我们已有，继续保留。

### 不借鉴
1. **公开发现 / 全球 Discover / 可搜索的群**（Strava 公开俱乐部、Hevy Discover、Discord Discovery）：健康数据隐私要求高，中国内地用户尤其敏感，
   不做陌生人发现；扩列只走熟人邀请链和管理员发的俱乐部码。
2. **全混合排行榜**（微信运动）：不同圈子的人互不认识，混在一起排名没有意义，还会让人觉得被「拿来比较」。
3. **Discord 式细粒度角色 / 权限系统**：对家人和小圈子太重，三层角色够用。
4. **群设置和个人隐私两套规则叠加却不说明**（Garmin 排行榜困惑）：每个圈子里要直接写明「你在这里共享了什么」。
5. **按人隐藏 + 双向黑名单**（Apple、微信运动）作为主要手段：在圈子模型里，按圈子设范围 + 退出更清楚；按人隐藏可以留作以后的补充。

## 4. 来源

[^strava-clubs]: Strava Support, "Clubs on Strava" — https://support.strava.com/hc/en-us/articles/216918347
[^strava-posts]: Strava Support, "Club Posts" — https://support.strava.com/hc/en-us/articles/18288273144973-Club-Posts
[^strava-mute]: Strava Support, "Mute Activity" — https://support.strava.com/hc/en-us/articles/4415798243597-Mute-Activity
[^whoop-teams]: WHOOP, "Join & Create Teams on WHOOP" — https://www.whoop.com/thelocker/join-create-teams-on-whoop/
[^whoop-lb]: WHOOP Community, "A member is on a team but his data not showing up in leader board?" — https://community.whoop.com/t/a-member-is-on-a-team-but-his-data-not-showing-up-in-leader-board/3694
[^whoop-multi]: WHOOP Community, "Can I be in more than one WHOOP team at a time?" — https://www.community.whoop.com/t/can-i-be-in-more-than-one-whoop-team-at-a-time/331
[^whoop-privacy]: WHOOP Community, "How does WHOOP ensure privacy within community teams?" — https://community.whoop.com/t/how-does-whoop-ensure-privacy-within-community-teams/304
[^oura]: Oura Help, "Oura Circles" — https://support.ouraring.com/hc/en-us/articles/15958088640147
[^oura-ac]: Android Central, "Oura Circles will let you track health progress with other Oura Ring owners" — https://www.androidcentral.com/wearables/oura-circles-launch
[^apple]: Apple Support, "Share your activity from Apple Watch" — https://support.apple.com/guide/watch/apd68a69f5c7
[^apple-40]: iGeeksBlog, "How to share your Activity data from Apple Watch and iPhone" — https://www.igeeksblog.com/how-to-share-apple-watch-activity-progress-from-iphone/
[^garmin]: Garmin Blog, "Garmin Connect Groups launched" — https://garmin.com/en-GB/blog/?p=101
[^garmin-lb]: Garmin Forums, "Garmin groups bug, social media interface, challenges and leaderboard use" — https://forums.garmin.com/apps-software/mobile-apps-web/f/garmin-connect-web/351215/garmin-groups-bug-social-media-interface-challanges-and-leaderboard-use
[^hevy]: Hevy Help, "Hevy App Social Guide: Connect, Follow and Share Your Workouts" — https://help.hevyapp.com/hc/en-us/articles/35688036014231
[^wxsport]: IT 之家，微信运动「不与他（她）排行」— https://www.ithome.com/0/487/421.htm
[^wxgroup-sspai]: 少数派，微信群「群聊邀请确认」— https://sspai.com/post/35849
[^wxgroup-pedaily]: 投资界，微信群确认邀请进群后的入群提醒（2022）— https://news.pedaily.cn/20220526/35242.shtml
[^discord-join]: Discord Support, server access settings (Invite Only / Apply to Join / Discoverable) — https://support.discord.com/hc/articles/29729107418519
[^discord-members]: Discord Support, "Members Page" — https://support.discord.com/hc/articles/15946797617431
[^discord-req]: Discord Support Community, "Show which invite a person used when they joined" — https://support.discord.com/hc/en-us/community/posts/360055508651
[^discord-folder]: Discord Support Community, "Feature Request: Server Folder Notification Settings" — https://support.discord.com/hc/en-us/community/posts/360051836533
[^discord-audit]: Discord Developer Docs, "Audit Log" — https://docs.discord.com/developers/resources/audit-log
[^tg-folders]: Telegram Blog, "Chat Folders, Archive, Channel Stats and More" — https://telegram.org/blog/folders
[^tg-links]: Telegram, "Flexible invitation links"（命名链接、等候室）— https://t.me/s/tginfoen?after=1281
[^tg-api]: Telegram API, "Invites" — https://core.telegram.org/api/invites
[^tg-cmu]: Bot API ChatMemberUpdated（`invite_link`、`via_join_request`）— https://docs.python-telegram-bot.org/telegram.chatmemberupdated.html
