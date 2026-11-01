# 自建同步引擎：app 侧接口要求（iOS 1.0.8 起）

> 给 iOS 开发（A1–A3，#219）。服务器侧已实现（S1–S6 + 同步状态，迁移 690 / 800），以代码为准：`body_engine/src/body_engine/rawdata/api.py`（请求模型）、`rawdata/engine.py`（检查、限流、`engine` 块）、`rawdata/sync_status.py`（状态面板）、`rawdata/cutover.py`（模式）。设计背景见 `docs/apple-sync-engine.md` 第 3.1 节。
> 文中数字都是示例或上限，不是任何真人的数据。

## 0. 一句话

app 读 HealthKit → 按类型分页 → 列式 JSON + gzip → `POST /v1/ingest/healthkit` → **只有收到 200 才提交这一页的锚点**。服务器按 HealthKit UUID 去重、按 `batch_id` 幂等，所以「丢一轮、下次从锚点重读重发」永远是安全的。`GET /v1/ingest/healthkit` 的 `engine` 块告诉 app 现在收什么、处在哪个模式。

## 1. 鉴权

- `Authorization: Bearer <token>`：账号 token（scope write）或只能上传的 token（scope ingest），存 Keychain（`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`，后台可读）。不用 cookie。
- `user_id` 只来自 token，请求体里没有。
- 建议每个请求带 `X-App-Version: 1.0.8`（格式错误时服务器能记下版本；请求体里的 `app_version` 照样要带）。

## 2. 发现：`GET /v1/ingest/healthkit`

什么时候读：冷启动、回到前台、每次 409、至少每 6 小时。每个 POST 的回答里 `status.engine` 也是同一份。

```json
{
  "water_enabled": true, "metrics_enabled": true, "metric_types": ["…"], "sync_center": true,
  "last_upload_at": "2026-11-01T08:00:00+00:00",
  "heartbeats": 0, "ecgs": 0, "water": 0, "metrics": 0,
  "engine": {
    "enabled": true,
    "mode": "shadow",
    "accepts": ["heartbeats", "ecgs", "deleted", "water", "quantities", "sources", "samples", "categories",
                "workouts", "stats", "range", "range_done", "runs", "phase", "tz"],
    "encodings": ["gzip"],
    "types": ["HKCategoryTypeIdentifierSleepAnalysis", "HKWorkoutTypeIdentifier", "…"],
    "limits": {"body_bytes": 2097152, "inflated_bytes": 16777216, "items": 20000, "group_items": 5000,
               "buckets": 10000, "workouts": 200, "batches_per_minute": 30, "big_batches_per_minute": 120}
  }
}
```

- `engine.enabled = false`：不发引擎字段（发了会 409 `engine_disabled`），照旧只用 OW SDK + RawSync。
- `engine.types`：**只读、只发这里列出的类型**（注册表 `rawdata/hk_types.ENGINE_TYPES`，app 自带同一份 `assets/healthkit_types.json`，测试比对两边）。不在列表里的类型锚点不动，以后再发。
- `engine.mode` 决定 OW SDK 开不开（第 7 节）：

| mode | 含义 | app 做什么 |
|---|---|---|
| `ow` | 引擎没开 | 只用 OW SDK（和 1.0.7 一样） |
| `shadow` | 引擎收数据，只做对比，不影响分数 | **引擎和 OW SDK 都跑** |
| `app` | 引擎的数据算分，OW 只补历史 | 引擎照跑；`OpenWearablesHealthSdk.stopBackgroundSync()`（凭据、锚点保留，回退时还能接上） |

## 3. 上传：`POST /v1/ingest/healthkit`

请求头：`Content-Type: application/json`、`Content-Encoding: gzip`（推荐；也接受 identity）。线上 ≤ 2 MB（公网反代上限），解压后 ≤ 16 MB；**目标每批压缩后 ≤ 1 MB**。

```json
{
  "batch_id": "uuid（这一批内容的 id，重试同一批必须用同一个）",
  "app_version": "1.0.8", "device": "iPhone", "tz": "<手机的 IANA 时区>",
  "phase": "recent | backfill | incremental | range",
  "sources": [{"name": "…", "bundle": "…", "product": "Watch…", "device_type": "watch | phone | other",
               "device_name": "…", "device_model": "…", "manufacturer": "…", "os_version": "…"}],
  "samples":    [{"type": "HKQuantityTypeIdentifierHeartRate", "unit": "count/min",
                  "uuid": ["…"], "start": [1761955200.0], "end": [1761955201.0], "value": [62.0],
                  "src": [0], "tz": [null]}],
  "categories": [{"type": "HKCategoryTypeIdentifierSleepAnalysis", "unit": "",
                  "uuid": ["…"], "start": [], "end": [], "value": [3], "src": [], "tz": []}],
  "workouts":   [{"uuid": "…", "activity": 50, "activity_name": "traditionalStrengthTraining",
                  "start": 0, "end": 0, "duration_s": 2700,
                  "stats": {"active_kcal": 210, "basal_kcal": 60, "distance_m": null, "avg_hr": 118, "max_hr": 150},
                  "events": [{"type": "pause", "start": 0, "end": 0}], "meta": {"indoor": true, "tz": "…"}, "src": 0}],
  "stats":      [{"type": "HKQuantityTypeIdentifierStepCount", "unit": "count", "bucket_s": 900,
                  "start": 1761955200, "values": [0, 12, 0]}],
  "deleted": ["uuid"],
  "range": null, "range_done": null,
  "runs": [{"id": "uuid", "trigger": "observer", "result": "ok", "error": null, "started": 0,
            "duration_ms": 1200, "batches": 3, "bytes": 90000, "samples": 4200}]
}
```

规则（违反 = 422，整批不存）：

- 时间都是 UTC 秒（1970 起，浮点可）。每组里每个列表一个样本一项，长度必须一致；`src` 是 `sources[]` 的下标。
- `unit` 必须等于注册表里这个类型的 `unit`（app 换算好再发；服务器再乘 `factor` 存，例如血氧 0–1 → 0–100）。
- `samples[]` 只放 `upload` 含 `raw` 的数量类型；`stats[]` 只放含 `stats` 的累计类型（活动 / 基础能量**只发统计桶**，D1）；`categories[]` 的 `value` 是 HealthKit 原始整数（睡眠 0–5）。
- 统计桶：`bucket_s = 900`，`start` 按 UTC 对齐到 900 秒；`values` 覆盖从 `start` 起连续的桶（含 0）。**服务器用这一段整体替换已存的桶**，所以重发一段旧的范围就是修正。
- 每批最多 20000 个样本 + 桶，每组 ≤ 5000 个样本 / ≤ 10000 个桶，≤ 200 个训练，≤ 80 组。
- 单个样本的值超出注册表的合理范围、时长 > 7 天、开始时间比现在晚 1 天以上：只丢这一条（回答里 `dropped` 计数），不拒整批。
- `deleted[]`：HealthKit 增量查询的 `deletedObjects` 的 UUID（OW SDK 不传删除，我们要传）。
- 训练路线不读、不申请权限（D2）。

## 4. 回答和错误码

| 状态 | `detail` | app 怎么做 | 锚点 |
|---|---|---|---|
| 200 | 计数：`samples` `workouts` `buckets` `deleted` `removed` `skipped` `dropped` `runs`…；重发的批次多 `duplicate_batch: true`；`status` 是轻量版（`counts: false`，不带四个计数） | 提交这一批涉及的锚点 | **提交** |
| 401 | token 无效 / 吊销 | 停止这一轮，**不要循环重试**；提示重新登录 / 扫码；退避 ≥ 15 分钟 | 不动 |
| 403 | `account_disabled` / `account_pending` | 停止，提示 | 不动 |
| 409 | `consent_required` | 停止引擎上传，今天页会弹同意书；同意后再传 | 不动 |
| 409 | `engine_disabled` | 重新 GET；按 `engine.enabled = false` 处理 | 不动 |
| 409 | `type_paused` | 重新 GET；把不在 `engine.types` 里的类型拿掉后**重发其余类型**（新 `batch_id`） | 被暂停类型不动 |
| 413 | 太大 | 这一页减半再发（新 `batch_id`） | 不动 |
| 400 / 415 / 422 | gzip 坏 / 编码不支持 / 字段不合约 | 是 app 的 bug：按类型拆开定位，坏的那一类停在原锚点并在 `runs[].error` 报 `contract`，其余类型照常 | 坏类型不动 |
| 429 | `rate_limited` + `Retry-After` 秒 | 等 `Retry-After` 再发**同一批**（同 `batch_id`）；不算失败、不提示用户 | 不动 |
| 5xx / 网络错误 / 超时 | — | 指数退避（2、4、8… 秒，最多 5 分钟，加随机抖动）重发**同一批** | 不动 |

被拒的请求服务器都会按原因记下来（第 6 节 `failures`），所以 app 不用为了排查另外上报。

## 5. 幂等、断点续传、弱网

- **锚点只在 200 之后提交**；不要本地 outbox：HealthKit 就是队列，丢一轮下次从锚点重读。
- **同一批内容重试用同一个 `batch_id`**：服务器第二次直接回第一次的结果（`duplicate_batch: true`），不重复写；回答丢了（弱网常见）也一样安全。内容变了（重读、减半、拿掉类型）就换新 `batch_id`。即使 `batch_id` 变了，同一个 HealthKit UUID 也只存一次。
- **断点续传 = 锚点**：首次 90 天（`phase=backfill`）从空锚点按类型分页，中断后从已提交的锚点接着读；第 3.1.4 节「先读最近 48 小时（`phase=recent`）再补全」。
- 分页：前台每页 2000 条，后台 200 条（注册表 `page` 可按类型改）；后台剩余时间 < 8 秒不再发新的一批。
- 超时建议：连接 10 秒、整个请求 30 秒。服务器处理一批的目标 < 2 秒（本地实测 5000 条样本 + 2880 个桶 gzip 一批 0.34 秒）。
- 限流（每人）：普通批次每分钟 30 批、每天 64 MB；`backfill` / `range` 每分钟 120 批、每天 512 MB。重发同一个 `batch_id` 不计入；被拒的批次不计入。
- 首次补历史默认只用 Wi-Fi，增量允许蜂窝（第 3.1.8 节）。

## 6. 同步状态：`GET /v1/ingest/sync-status`（app）和 `GET /v1/users/me/sync/engine/status`（今天页面板）

两个接口返回同一份数据，只能看自己的；任何模式都能用，不受开关控制；只有时间、计数和代码，没有健康数值。

```json
{
  "mode": "shadow", "engine_enabled": true,
  "last_upload": {"at": "2026-11-01T19:02:11+11:00", "phase": "incremental", "app_version": "1.0.8",
                  "device": "iPhone", "batches_24h": 14, "bytes_24h": 812345},
  "ow_last_sync_at": "2026-11-01T18:40:00+11:00",
  "types": [{"type": "HKQuantityTypeIdentifierHeartRate", "key": "heart_rate", "kind": "quantity",
             "status": "heart_rate", "latest_sample_at": "2026-11-01T18:58:00+11:00",
             "received_at": "2026-11-01T19:02:11+11:00", "latest_day": "2026-11-01"}],
  "failures": {"counts_24h": {"auth": 0, "format": 0, "refused": 0, "limit": 2, "server": 0},
               "recovered": true,
               "last": {"reason": "limit", "code": "rate_limited", "status": 429, "n": 2,
                        "first_at": "…", "at": "…", "phase": "backfill", "app_version": "1.0.8"}},
  "pipeline": {"pending_days": 0, "oldest_pending_at": null, "refresh_status": "idle",
               "scored_at": "2026-11-01T19:02:40+11:00", "scores_queued_at": "…", "error": null,
               "upload_to_score_s": 29, "up_to_date": true},
  "last_run": {"trigger": "observer", "result": "ok", "error": null, "duration_ms": 1200, "batches": 3,
               "samples": 4200, "started_at": "…"}
}
```

| 字段 | 说明 |
|---|---|
| `last_upload` | 最后一次**成功**的上传（任何字段，包括 RawSync 老字段）；`null` = 从没传过 |
| `types[]` | 引擎收到过的每个类型：服务器上最新样本的开始时间 `latest_sample_at`（统计桶类型取最新桶）、最近一次收到的时间。app 拿它和「本机 HealthKit 最新样本」比，就能分出「没采集」和「没同步」；`latest_day`（2026-10-31 起）：服务器经任何渠道（Apple 健康导出、Open Wearables、快捷指令、引擎）有这一类的最新一天（用户本地日期），比引擎读取范围（首次 14 天、补历史）还早的手机样本靠它判断「服务器已经有那一天」，不再算「没同步」；只有 `latest_day` 没有引擎样本的类型 `latest_sample_at` 为 `null`（老 app 会跳过）。两样都没有的类型不在列表里 |
| `failures.counts_24h` | 近 24 小时被拒的次数，按原因：`auth`（token 吊销 / scope 不对 / 账号停用）、`format`（太大、gzip 坏、字段不合约）、`refused`（409：同意书、引擎没开、类型暂停）、`limit`（429）、`server`（500） |
| `failures.last` | 最近一次被拒：`code`（`token_revoked`、`token_scope`、`validation`、`contract`、`bad_gzip`、`too_large`、`consent_required`、`engine_disabled`、`type_paused`、`rate_limited`、异常类名…）、`n` = 10 分钟内同一原因合并的次数 |
| `failures.recovered` | 最后一次失败之后有没有成功上传过（面板可以只在 `false` 时标红） |
| `pipeline` | 收到之后到出分：待派生的天数、算分队列状态、最后一次出分时间、`upload_to_score_s`（最后一次上传到最后一次出分的秒数）、`up_to_date`（最后一次上传已经算进分数） |
| `last_run` | app 通过 `runs[]` 报的最近一轮同步（触发方式、结果、耗时、批数） |

未知 token 的 401 没法归到任何人，不会出现在这里（app 自己知道收到了 401）。

## 7. 模式切换时 app 的行为

- 服务器按用户切换（`body-engine sync-mode switch`），app 从 `engine.mode` 得知：
  - 变成 `app`：调 `stopBackgroundSync()` 停 OW SDK，**不**清 OW 的凭据和锚点；引擎照常。
  - 从 `app` 回到 `shadow`（回退）：重新 `startBackgroundSync()`，OW 从自己的锚点补上停写期间的数据。
- 模式在 app 内部存一份，每次 GET 后更新；不要因为一次网络失败就改模式。
- `app` 模式下引擎每次上传，服务器会自动排队重算分数（不用再调 `POST /v1/users/{user}/sync/refresh`；调了也只是排队，不再从 OW 导入）。
- `app` 模式下「立即重新同步」（`/v1/ingest/resync`）的服务器部分不再从 OW 导入：app 要用引擎把这段范围重发一遍（第 8 节的 `range` / `range_done`），再报 `phone_done`。服务器在 `range_done` 到达、这些天派生完之后才算分（最多等 30 分钟）。

### 7.1 没有 OW 账号的账号（S5b，开关 `apple_sync_new_accounts`）

开关打开后新开户不建 OW 用户，账号一开就是 `engine.mode = app`，而且以后也不会回到 `shadow`（没有 OW 可回）。服务器这边：

- 激活码兑换出的设置链接 `watch2whoop://setup?…` **没有 `ow`、也没有 `code`**，只有 `today` 和 `login`；
- `POST /v1/account/ow-invitation` 对 `app` 模式的人回 **409 `sync_engine`**（不是 `provisioning`，不要重试）；
- 「手机同步已断开」（`GET /v1/users/me/sync/connection`）多一个 `channel`：`app` = 看的是引擎上传，今天页的「重新连接」直接打开 `watch2whoop://sync`。

**打开这个开关之前 app 要做到**（1.0.8 / 1.0.9 还没有）：`engine.mode = app` 时，「已连接」看引擎（发现接口能用、有 token），不看 OW 的 `owUserId`；引导和同步页不再要求「连接同步」、不调 `ow-invitation`（或者把 409 `sync_engine` 当成「不需要」）；设置链接没有 `code` 时不提示输入邀请码；不启动 OW SDK。

## 8. 按范围重新同步

每页带 `range: {id, start, end, types}`（同一个 `id` 贯穿整个范围），全部页 200 之后单独发一批 `range_done: id`。服务器删掉这个范围里、这些类型、范围开始前收到、这次没再出现的样本（处理漏掉的删除），重算受影响的天。`phase = "range"`（限流放宽）。

## 9. `runs[]`

每轮同步结束后在下一批里带上（只有计数，没有健康数值）：`trigger`（foreground / launch / observer / refresh / processing / range）、`result`（ok / partial / error / expired / locked / offline / nothing）、`error`（短代码）、`started`、`duration_ms`、`batches`、`bytes`、`samples`。服务器存 `sync_runs`，用来量后台可靠性。

## 10. 出分时延

app 端：观察者唤醒 → 防抖 3 秒 → 读 → 上传（< 2 秒）。服务器：`app` 模式下上传即排队；每分钟的任务（建议部署机改成每 15 秒检查一次队列）先派生再算分，约 20–30 秒。目标：手表数据到达 iPhone 后 1 分钟内出分。

## 11. 给 iOS 开发的验收清单

- [ ] GET `engine` 块驱动：`enabled=false` 不发引擎字段；只发 `types` 里的类型；按 `mode` 开停 OW SDK。
- [ ] 锚点只在 200 后提交；重试同一批用同一个 `batch_id`；断网 / 杀 app 后从锚点续传。
- [ ] 401 不循环重试；409 / 413 / 422 / 429 按第 4 节处理。
- [ ] gzip 后每批 ≤ 1 MB；单位和注册表一致；统计桶 900 秒、UTC 对齐。
- [ ] `deleted[]` 上传删除；`runs[]` 上报每轮。
- [ ] 同步中心用 `/v1/ingest/sync-status` 的 `types[].latest_sample_at` 比本机最新样本。
- [ ] 模拟器：`DebugHealthSeeder` 写每类样本、睡眠阶段、训练、删除后，服务器 `sync-shadow-day` 两边一致（第 7 节测试方案）。
