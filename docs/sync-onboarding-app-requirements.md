# 一键开启同步：iOS app 1.0.8 需求

> 给 iOS 1.0.8 的开发。服务端和今天页部分已在同一个 PR 里完成（`sync/overview.py`、`web/static/sync-status.js`），
> 这里只写 app 要做的事。页面对每一项都做了特性检测：app 没宣布能力时自动走 1.0.6 / 1.0.7 的旧路径，所以 app 可以分批做。

## 1. 背景

- 负责人看到的问题：新用户第一次登录时点了「跳过」（`onboarding_page.dart` / `setup_link_page.dart` 的「跳过，以后再说」），
  之后今天页没有任何数据，也没有提示他去开同步；开同步要做的操作太多。
- 同步的稳定性和速度是最核心的功能；2026-10-26 有用户同步断了好几天自己不知道。

## 2. 现在（1.0.6 / 1.0.7）的最短路径，页面已经做到

| 场景 | 用户操作 | 说明 |
|---|---|---|
| 从没同步过（点过跳过） | 今天页顶部卡片「一键开启」→ app 打开「手机同步」页 → 点「连接同步」→ iOS 健康授权表单「全部打开 → 允许」 | 3 次点击 + 系统表单。「连接同步」= `connectSyncViaAccount()`，账号自己换邀请码。页面同时向 `POST /v1/account/ow-invitation` 要了一个码，显示在卡片上（给没有账号 token 的扫码用户「用邀请码连接」用） |
| 第一次打开 | 弹一次同样的提示（每台设备每个用户一次），之后卡片一直在，直到收到第一条数据 | 今天 / 趋势 / 朋友 三个页都有卡片 |
| 同步卡住 | 今天页标题下的状态行（带颜色点）、横幅、「我 → 同步状态」打开面板：一句原因 + 按钮 | 原因见第 6 节 |
| 立即上传 | 面板「打开同步页」→ 用户在同步页点「立即同步」 | 1.0.6 / 1.0.7 没有静默上传入口 |
| 去授权 | 面板「去授权」→ 数据状态里这一类的 iOS 设置步骤 | iOS 不告诉 app 读权限有没有给，只能看服务器收没收到 |

1.0.6 / 1.0.7 的 WebView 只处理 `watch2whoop://sync` / `settings` / `account-deleted`（`home_page.dart _onNavigation`），
其他 `watch2whoop://` 链接被拦掉、什么也不发生，所以页面在没有对应 `Watch2WhoopFeatures` 时不会发新链接。

## 3. 1.0.8 一键开启：`watch2whoop://sync-connect`

**宣布**：`window.Watch2WhoopFeatures.syncConnect = true`（加进 `web_mic.dart` 的 `kFeaturesJs` / `kFeaturesNoMicJs`）。

**页面 → app**：用户点「一键开启」，页面先 `POST /v1/account/ow-invitation` 拿码，再导航到

```
watch2whoop://sync-connect?ow=<OW 公网地址，可省>&code=<一次性邀请码，可省>
```

- 只取同步部分（OW 地址 + 邀请码），和 `sync_repair.dart pageSetupLink()` 的规则一样：服务器地址、登录码不从页面来。
- 没有 `code`（拿码失败）时，有账号 token 的 app 自己走 `connectSyncViaAccount()`；没有账号 token 又没有码时报 `error`。
- 和 `syncRepair` 的 `watch2whoop://setup` 不同：**不再弹确认框**（用户刚在页面上点了「一键开启」，这就是确认），直接开始。

**app 依次做**（一个调用完成，用户只需要处理 iOS 的健康授权表单）：

1. `permission`：`requestAuthorization`（OW SDK 的类型 + RawSync 的类型，和「连接同步」一样两张表单的现有逻辑）。
2. `connecting`：兑换邀请码 / `connectSyncViaAccount()`，`HealthSync.connect()`。
3. `uploading`：`startSync(syncDaysBack)` 的首次同步（90 天，最新的先传），同时跑一次 RawSync。前台期间保持屏幕常亮（同步页现有逻辑）。
4. `done`：第一批数据已经被服务器接受（OW 返回 2xx 或 RawSync 上传成功），然后在页面里调 `BodySync.refresh("app")`（现有）。

**app → 页面（进度）**：每个阶段开始时、上传中每 ~2 秒一次，在 WebView 里执行

```js
window.BodySyncStatus && window.BodySyncStatus.onApp({phase: "uploading", percent: 42, error: null})
```

| 字段 | 取值 |
|---|---|
| `phase` | `permission` / `connecting` / `uploading` / `done` / `error` |
| `percent` | 0–100，只在 `uploading` 有意义（首次同步已发 / 估计总数；不知道总数时按天数 90 天里已传到哪天） |
| `error` | `phase=error` 时一句给人看的短句（≤ 60 字，中英按 app 语言），如「健康权限没有打开」「连不上同步服务器」 |

页面收到 `done` 会立刻重新读状态；收到第一条数据后卡片消失、提示「已开始同步」。用户中途离开今天页（去了别的 tab）不影响 app 继续同步。

**验收**
- 新装 → 登录 → 跳过 → 今天页卡片「一键开启」→ 只出现 iOS 健康表单 → 允许 → 卡片显示「上传中 x%」→ 数据到达后卡片消失，3 分钟内出分（`sync/ow_watch.py`）。
- 拒绝健康权限：`error`「健康权限没有打开」，卡片「再试一次」可重来。
- 飞行模式：`error`「连不上同步服务器」，不留下半连接（参考审计 A2：`signIn` 失败要能重试，不能静默停在「未连接」）。
- 已连接的用户误触：直接 `uploading` → `done`，不重复兑换邀请码。

## 4. 手机状态上报：`PUT /v1/users/me/sync/device`

服务器不知道手机这边为什么不同步（后台刷新被关、低电量、锁屏读不到健康数据），只能看「多久没收到」。1.0.8 把手机状态报上来，
今天页就能说出准确的原因。

**接口**（本 PR 已上线）：
- `PUT /v1/users/me/sync/device`：账号 token（Bearer，scope write）或今天页 cookie。
- `PUT /v1/ingest/sync-device`：同上，或只能上传的 ingest token（后台任务里用）。
- 也可以在 WebView 里调 `window.BodySyncStatus.device({...})`（页面用自己的登录发 PUT）。

```json
{
  "app_version": "1.0.8",
  "background_refresh": "available",
  "low_power": false,
  "health_readable": true,
  "last_attempt_at": "2026-10-26T01:02:03Z",
  "last_upload_at": "2026-10-26T01:02:05Z",
  "last_error": null,
  "unasked_types": ["HKCategoryTypeIdentifierSleepAnalysis"]
}
```

| 字段 | 来源 | 说明 |
|---|---|---|
| `background_refresh` | `UIApplication.shared.backgroundRefreshStatus` | `available` / `denied` / `restricted` |
| `low_power` | `ProcessInfo.processInfo.isLowPowerModeEnabled` | |
| `health_readable` | 上一次**后台**尝试时 `UIApplication.shared.isProtectedDataAvailable` | `false` = 锁屏，HealthKit 读不到 |
| `last_attempt_at` / `last_upload_at` | 上一次后台 / 前台同步尝试、上一次成功上传 | ISO 8601 |
| `last_error` | 上一次失败的类别 | `auth`（401、凭证失效）/ `network` / `locked` / `health_denied` / `server`（5xx）/ `other`；成功后清空 |
| `unasked_types` | `getRequestStatusForAuthorization`（同步页「数据状态」已有 `healthAskStatus`） | iOS 还没问过用户的类型，最多 60 个 |

- 每行替换上一次（一人一行，表 `sync_device_state`，迁移 830），超过 24 小时的报告服务器不再使用。多出来的字段会被忽略。
- **什么时候发**：app 进前台、每次同步结束（成功或失败）、`backgroundRefreshStatus` / 低电量模式变化时（`NSProcessInfoPowerStateDidChange`）。
  后台任务里失败（锁屏、无网）先存本机，下次能发时一起发；同一内容 10 分钟内不重复发。
- 不含健康数值，只有手机状态。

## 5. 今天页按钮需要的 app 入口

| 页面按钮 | 宣布 | 链接 | app 要做 |
|---|---|---|---|
| 立即上传 | `syncNow: true` | `watch2whoop://sync-now` | 不离开今天页，前台跑一轮同步（OW `syncNow` + RawSync），进度同第 3 节 `onApp`（`uploading` → `done` / `error`）；没连接时等同 `sync-connect` |
| 去设置 | `appSettings: true` | `watch2whoop://app-settings` | `UIApplication.openSettingsURLString`（app 自己的设置页：后台 App 刷新、健康） |
| 去授权 | `healthSettings: true` | `watch2whoop://health-settings` | 有 `unasked_types` → 再调 `requestAuthorization` 弹表单；都问过了 → 打开 app 设置页（iOS 18+ 里有「健康」）；回来后上报第 4 节 |
| 重新连接 | `syncRepair: true`（已有） | `watch2whoop://setup?ow=…&code=…` | 已在 main（#289） |
| 一键开启 | `syncConnect: true` | `watch2whoop://sync-connect?…` | 第 3 节 |

没宣布时页面的退路：立即上传 → 打开同步页；去设置 → 显示路径「设置 → Watch2Whoop → 后台 App 刷新」；去授权 → 数据状态里的步骤。

## 6. 页面怎么判断卡住的原因（服务端 `sync/overview.py`，供 app 对齐文案）

按顺序取第一个：

| code | 条件 | 一句话 | 按钮 |
|---|---|---|---|
| `consent` | 没确认同意书 | 需要先确认同意书 | 去确认 |
| `not_connected` | 从没通过任何同步收到过数据 | 还没开启数据同步 | 一键开启 |
| `credentials` | `sync/broken.py` 判定断开，或 app 报 `last_error=auth` 且已落后，或 app 直传被拒（`auth`，见第 8 节） | 同步连接失效 | 重新连接 |
| `server` | 服务器 6 小时没连上 OW，或 app 直传被服务器错误拒收 | 服务器同步延迟 | 立即重试 |
| `app_closed` | 数据停了且 48 小时没打开 app（只在 app 外判断） | 太久没打开 app | 打开 app |
| `background_off` | app 报后台刷新 `denied` / `restricted`（数据还新也提示） | 后台刷新被关 | 去设置、立即上传 |
| `low_power` | 落后 6 小时以上且 app 报低电量模式 | 低电量模式暂停了同步 | 立即上传 |
| `locked` | 落后且 app 报 `health_readable=false` 或 `last_error=locked` | 手机锁着，读不到健康数据 | 立即上传 |
| `lagging` | 落后 6 小时以上，没有 app 报告（1.0.6 / 1.0.7） | 后台同步慢了 | 立即上传、立即重试 |
| `type_missing` | 心率正常但 HRV / 静息心率 / 睡眠 / 步数 没收到（新连接 2 小时内、心率不足 3 天时不算） | 没收到睡眠 | 开启睡眠追踪、去授权 |

阈值在 `sync/params.py`（`SYNC_LAG_AFTER`、`SYNC_DEVICE_FRESH`、`SYNC_FIRST_GRACE`、`OVERVIEW_*`）。
app 自己的同步页如果也显示原因，请用同样的 code 和文案（`GET /v1/users/me/sync/overview?in_app=1` 直接拿，Bearer 账号 token 可用）。

## 7. 引导里的「跳过」

- 保留「跳过」，但跳过后**不要**停在空白：直接进今天页即可，页面会弹一次「开启数据同步」并保留卡片（本 PR）。
- 建议 1.0.8 把引导的健康授权步骤和「一键开启」合并：引导页的主按钮就调第 3 节的流程（同一段代码），跳过只是推迟。

## 8. 和 #219 自建同步引擎的关系

- 第 3、5 节的 `sync-connect` / `sync-now` 不绑定 OW：切到自建引擎后 app 内部换实现，链接和 `onApp` 进度格式不变。
- 服务端 `overview._engine()` 已接上 #304 的同步状态（`rawdata/sync_status.collect`，即 `GET /v1/users/me/sync/engine/status`）：
  app 直传被拒且之后没有成功上传时，`auth` → 「同步连接失效」（引擎 `app` 模式立刻，其他模式落后 6 小时后），`server` → 「服务器同步延迟」；
  引擎进入 `app` 模式后跳过 OW 的断开判断，以 app 直传为准。overview 的 `engine_status` 带模式、最近一次被拒（只有代码和时间）和出分流水线。

## 9. 发版检查清单

- [ ] `kFeaturesJs` / `kFeaturesNoMicJs` 加上实际做了的能力（没做的不要宣布）。
- [ ] `_onNavigation` 处理 `sync-connect` / `sync-now` / `app-settings` / `health-settings`，未知 host 保持拦截。
- [ ] `onApp` 进度在页面重新加载后不丢（app 保留最近一次进度，`onPageFinished` 时补发一次）。
- [ ] 第 4 节上报：真机分别打开低电量模式、关掉后台 App 刷新、锁屏等后台同步，今天页显示对应原因。
- [ ] 中英文案（`tr('中文', 'English')`）。

## 10. 补全历史数据：`Watch2WhoopFeatures.backfill` + `watch2whoop://backfill`

> 负责人 2026-10-26 的决定：1.0.8 首次同步只读最近 **14 天**，新数据尽快进来；之后今天页定期提醒用户把历史补到 **90 天**
> （评分的个人基线 60 天、恢复分至少 14 晚 HRV）。服务端和今天页在 `sync/backfill.py`、`web/static/sync-status.js`，这里只写 app 要做的事。

**首次同步**：`syncDaysBack` 默认从 90 改成 14（`setup_flow` / `connectSyncViaAccount` / `sync-connect` 的首次同步都一样）。
`sync-connect` 的 `uploading` 进度按 14 天算，`done` 的时机不变。

**宣布**：`window.Watch2WhoopFeatures.backfill = true`（加进 `kFeaturesJs` / `kFeaturesNoMicJs`）。没做就不要宣布；没宣布时今天页把按钮
指向旧入口（有 `syncCenter` 且服务端开着 `sync_center` → `watch2whoop://sync-center`，否则 `watch2whoop://sync`，用户在那里选
「全部历史」）。

**页面 → app**：用户点卡片上的「补全历史」。页面先 `POST /v1/users/me/sync/backfill {days: 90}`（账号 cookie），服务端创建和同步中心
一样的强制重新同步任务（`sync_resync` 表，状态 `phone`，`days=90`，`range_from` = 90 天前），然后页面导航到

```
watch2whoop://backfill?days=90      （全部历史：days=all）
```

**app 要做的**（不离开今天页，没有确认框，用户刚在页面上点过按钮）：

1. `GET /v1/ingest/resync`（Bearer：账号 token 或只上传 token）。`status == "phone"` → 读 `days`（`null` = 全部历史）；
   不是 `phone`（任务已被取消、或已经在服务器阶段）→ 什么也不做。**不要自己再 `POST`**，任务是页面建的。
2. 做手机部分，和同步中心「立即重新同步」的手机部分完全一样（`resync_flow.dart`）：清锚点、从 `days` 天前的 0 点起整段重读
   （`daysBackOf(days)`，OW SDK + RawSync 自己的类型），进度用现有的 `PUT /v1/ingest/resync {phone_percent, counts}`，读完
   `phone_done: true`。服务端接着导入这一段并重算分数（`sync/resync.py`），不需要 app 再做什么。
3. 可选：进度同时在页面里调 `window.BodySyncStatus.onApp({kind: "backfill", phase: "uploading" | "done" | "error", percent, error})`。
   页面对 `kind: "backfill"` 只在 `done` / `error` 时重新读状态（卡片的百分比以服务端为准，页面每 20 秒读一次），其余字段忽略；
   **一定要带 `kind: "backfill"`**，否则会被当成「一键开启」的进度。

**服务端对 app 的放宽**：这个任务由卡片创建后的 48 小时内，`/v1/ingest/resync` 的 GET / PUT / DELETE 即使该用户没有 `sync_center`
开关也可用（`sync/backfill.allows_resync`）。其他时候仍按 `sync_center` 开关。

**卡住怎么办**：任务建好 15 分钟内 app 一次进度也没报（没打开 / 没处理链接），页面把它当失败，卡片出「再试一次」，再点会作废旧任务重建。
所以 app 收到链接后要尽快报第一次 `phone_percent`（哪怕 0 之外的 1）。

**验收**
- 14 天首次同步后，次日今天页出现「补全历史数据 14 / 90 天」卡片，点「补全历史」→ 只看到后台读取，卡片显示「补全中 x%」→ 完成后卡片消失（或 90 天未满则 30 天内不再提醒）。
- 用户中途离开今天页 / 杀掉 app 再打开：`GET /v1/ingest/resync` 仍是 `phone` 且 `phone_percent` 不倒退，app 继续（任务可续）。
- 飞行模式 / 没有健康权限：不要宣布成功；卡片 15 分钟后出「没能补全」。
