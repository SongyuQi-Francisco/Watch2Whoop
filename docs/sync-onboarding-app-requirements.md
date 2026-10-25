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
| `credentials` | `sync/broken.py` 判定断开，或 app 报 `last_error=auth` 且已落后 | 同步连接失效 | 重新连接 |
| `server` | 服务器 6 小时没连上 OW | 服务器同步延迟 | 立即重试 |
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
- 服务端 `overview._engine()` 是扩展点：引擎进入 `app` 模式后跳过 OW 的断开判断，以 app 直传为准；引擎的状态接口出来后在这里接上。

## 9. 发版检查清单

- [ ] `kFeaturesJs` / `kFeaturesNoMicJs` 加上实际做了的能力（没做的不要宣布）。
- [ ] `_onNavigation` 处理 `sync-connect` / `sync-now` / `app-settings` / `health-settings`，未知 host 保持拦截。
- [ ] `onApp` 进度在页面重新加载后不丢（app 保留最近一次进度，`onPageFinished` 时补发一次）。
- [ ] 第 4 节上报：真机分别打开低电量模式、关掉后台 App 刷新、锁屏等后台同步，今天页显示对应原因。
- [ ] 中英文案（`tr('中文', 'English')`）。
