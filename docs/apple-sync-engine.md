# 自建 Apple 健康同步引擎：替换 Open Wearables 的迁移设计

> 状态：设计稿，等维护者确认（#219）。只描述方案，不含代码改动。标「待查」的地方写这份文档时没查清，拆 PR 时先确认。
> 文中出现的数字都是示例或估算，不是任何真人的数据。
> 进度：S1（#226）、S2（#234：派生 + 脏天队列 + 同步模式）、S3（#242：读取层 + 两条链路逐日对照）、S4–S6（影子对比、按用户切换和回退、OW 历史本地存档，迁移 800）、同步状态接口和 S5b（新开户走引擎、切换前体检）已实现，和设计不同的地方见第 3.2 节开头的说明。app 侧的接口要求：`docs/apple-sync-engine-app-contract.md`。完全不用 OW 的评估和时间表：第 9 节；OW 下线时要删的代码：第 10 节。

## 0. 摘要

- 现在 iPhone app 的后台同步走 Open Wearables（OW）：OW iOS SDK → OW 后端 → `import-ow` 每小时拉进 Body Engine。我们自己的 RawSync 只管 OW 不支持的逐拍心跳、心电图、饮水。
- 目标：app 里一个原生 Swift 同步引擎读所有类型（含训练、睡眠），直接上传到 Body Engine 的 `/v1/ingest/healthkit`，服务器收到就入库、几分钟内算分；OW 分阶段下线。
- 四个阶段：① 同步中心（进行中）→ ② 完整同步引擎（开关后面，先给 beta 组）→ ③ 影子对比 1–2 周 → ④ 切换、回退期、OW 只读、下线。
- **最大的风险**不是 app 端，而是服务器对 OW 的「活读取」：`compute-scores`（每小时、每晚全量）、压力、训练心率、今天页睡眠阶段图都在运行时直接读 OW 的原始样本。下线 OW 之前必须把这些读取点改成读 Body Engine 自己的原始样本表，并把只存在于 OW 的历史原始样本迁进来，否则重算历史分数时会丢输入（见第 5.4 节、第 8 节）。
- 拆成 12 个 PR（服务器 7 个、app 5 个），合计约 40–50 人日（第 6 节）。

## 待维护者确认的决定

| 编号 | 问题 | 本文的建议 |
|---|---|---|
| D1 | 累计型（步数、能量、距离…）传原始样本还是统计值 | 传 15 分钟统计桶（`HKStatisticsCollectionQuery`，苹果自己的多设备去重，和「健康」app 显示的一致）；步数、距离另外传原始样本（压力算法要分钟级步数）；活动 / 基础能量只传统计桶（量最大，原始样本没有别的用途） |
| D2 | 训练路线（`HKWorkoutRoute`） | 不读、不申请权限（位置敏感，同意书第 1 节写了「不主动收集位置」，产品也没有地图功能） |
| D3 | 原始样本存新表还是复用 `shortcut_samples` / `export_samples` | 新表 `hk_samples`（按 HealthKit UUID 去重、能处理删除），旧表不动 |
| D4 | `app` 来源的优先级 | 影子期低于 `ow` 且不参与选值；切换后高于 `ow`（第 3.2.3 节） |
| D5 | 回退期 / 只读期多长 | 切换后 30 天回退期（OW 照常运行，只是没人往里写），再 60 天只读，然后下线 |
| D6 | 新增类型（站立、日照、音频暴露、步行稳定性等）要不要改同意书 | **已定（2026-10-23）**：维护者认为同意书第 1 节「心率、心率变异性、睡眠、步数……等每日指标」已经涵盖同步中心补的类型（站立、锻炼分钟、日照、体能消耗、睡眠手腕温度、环境 / 耳机音量、步行平均心率、上下楼速度、步行稳定性），不改同意书，iOS 1.0.7 照常收。同步引擎注册表里更敏感的类型（血压、血糖等）在 `apple_sync_engine` 开给内测组之前再确认一次 |

D1–D5：维护者 2026-10-23 决定按本文排期，先按上表的建议做，之后可以调整。

---

## 1. 现状盘点

### 1.1 OW 链路怎么走

```
iPhone：OW Flutter 包 open_wearables_health_sdk 0.0.25 → 原生 OpenWearablesHealthSDK 0.15.0
   │  POST {OW}/api/v1/sdk/users/{ow_user}/sync（JSON：records / sleep / workouts）
   ▼
OW 后端：返回 202，Celery 异步入库（data_point_series、event_record、睡眠状态机）
   │  Body Engine 用 API key 读 /timeseries（resolution=raw）、/events/sleep?include=stages、/events/workouts
   ▼
Body Engine：import-ow（每小时 + app 打开时按需）→ daily_metric_sources / daily_metrics、sleep_sessions、workouts
             另外 compute-scores、压力、训练心率、今天页睡眠图在运行时直接读 OW
```

app 侧：`apps/watch2whoop/ios/Runner/AppDelegate.swift` 在启动完成前原生拉起 SDK（注册 BGTask、恢复观察者）；`lib/src/health_sync.dart` 包装连接 / 授权 / 同步；`lib/src/health_range.dart` 管 90 天 / 全部历史；`lib/src/sync_on_open.dart` 每次回到前台同步一轮后让今天页调 `POST /v1/users/{user}/sync/refresh`（`sync/refresh.py`：后台线程跑一次 `importers/ow.run`，再排队重算最近 3 天）。

### 1.2 Body Engine 从 OW 拿了什么

**日值**（`body_engine/src/body_engine/importers/ow.py`）

- `run()`：按 30 天窗口分页拉 `/timeseries` 原始样本，按用户时区切天；增量游标存在 `sync_state(source='ow').cursor`，每次从游标往前回看 `LOOKBACK = 2` 天整天重算。
- 类型映射在 `SPECS`（OW 名 → Body Engine 指标名、单位、`sum`/`avg`、换算系数），例如 `steps → step_count`、`active_energy → active_energy_burned`、`distance_walking_running → km（×0.001）`、`body_temperature → apple_sleeping_wrist_temperature`、`walking_speed → km/hr（×3.6）`。没列的类型用 OW 名、按平均。`PERCENT_100`（血氧存 0–100）、`FRACTION`（步行稳定性等存 0–1）、`ENERGY`（XML 导入的 kJ 当 kcal 存）单独处理（`_value`）。
- 累计型：`dedupe_cumulative()` 按 HealthKit 的规则去重（手表 > 手机 > 其他，重叠时间只算高优先级）。OW 只存样本开始时间，结束时间推断为「同一来源下一条的开始，最多 10 分钟」（`CUMULATIVE_CAP`）；文件头记了这个推断的校准误差（几个百分点，平均偏低）。
- 非累计型：当天全部样本的平均 / 最小 / 最大。
- 去重：同一类型同一时刻有 app 实时样本时丢掉 XML 副本（`drop_xml_duplicates`）；`is_daily_total` 的整天汇总跳过；只有 XML 数据的最后一天（导出当天）不写。
- 写入：`importers/common.py upsert_daily()` 写 `daily_metric_sources(source='ow')`，再 `resolve_daily()` 选出 `daily_metrics`：非累计型和睡眠按 `SOURCE_PRIORITY = {shortcut: 1, apple_export: 2, ow: 3}`；累计型在「干净」候选里取最大（`hourly_sum`、`incomplete` 的降级候选只在没有干净候选时用，`incomplete_days()` 用基础代谢和当天训练能量识别只同步了一部分的天）。

**睡眠**

- 拉 `/events/sleep?include=stages`，多拉前后各一天；`sleep_segments()` 把阶段（`light→core`、`deep`、`rem`、`awake`、`sleeping→unspecified`，`in_bed` / `unknown` 不算）转到用户时区（OW 返回的时区偏移不可靠，`_local()` 用每个时刻的固定偏移，跨夏令时也对）；和更高优先级（手表 > 手机 > 其他，app 实时 > XML）的会话重叠的整段丢掉。
- 然后和其他来源同一个规则切段：`common.sessionize()`（只看睡着段之间的间隔，> 60 分钟切开；不足 15 分钟睡眠的丢掉；归到「醒来 − 4 小时」那天），`common.replace_sleep()` 写 `sleep_sessions`（一天只留一个来源，最长的一段是主睡眠）。

**训练**（`training/importer.py`）

- `import_ow()` 拉 `/events/workouts`，`parse_ow()` 映射：`type`（OW 统一类型，就是 `training/params.py TYPE_NAMES` 的键；OW 只知道 `other` 时从名字里猜 HealthKit 类型）、`duration_seconds`（HealthKit 的训练时长，扣掉暂停）、`calories_kcal`（OW 存的是活动 + 基础的总能量）、`distance_meters`、`avg/max_heart_rate_bpm`、来源 / 设备。唯一键 `(user_id, source, external_id)`，OW 里删了的这里也删。
- `refresh_duplicates()`：同一次训练的多份副本（OW XML + 实时、OW + 快捷指令、Hevy）用 `dup_of` 标记，按 `_rank()`（真实类型优先、`SOURCE_RANK = {ow: 0, shortcut: 1, apple_export: 2, hevy: 3}`、非 XML、手表）留一份。
- `enrich()`：**运行时再拉 OW** 训练时段的原始心率、活动 / 基础能量样本：算 Edwards TRIMP 和五区分钟、运动后 1 分钟心率恢复；活动能量 = 总能量按样本中活动 / 基础的比例拆，样本不全时用「总能量 − 估算基础代谢」并标 `active_kcal_estimated`。

**来源信息**：OW 样本的 `source.device_type`（watch / phone）、`device`、`source`（`apple_health_xml` = XML 导入）用于去重优先级；训练的 `device` 用于来源标签（`training/history.py source_label`）。

**运行时直接读 OW 的地方**（下线前都要改，第 5.4 节）

| 位置 | 读什么 |
|---|---|
| `scores/inputs.py load_ow()` | 心率、HRV（SDNN）、呼吸、血氧、手腕温度原始样本 + 睡眠阶段；`compute-scores` 每次从评分起点前一年读到今天（每小时任务也一样，只是只写最近 3 天） |
| `insights/stress.py _ow_samples()` / `_workouts()` | 心率、步数、HRV 原始样本和训练时段 |
| `training/importer.py window_samples()` | 训练时段的心率、能量样本 |
| `web/views.py ow_segments()` | 今天页睡眠阶段图，每次打开页面实时读 |
| `rawdata/compare.py` | `hrv-compare`（可 `--no-ow`） |
| `sync/refresh.py`、`sync/history.py` | 按需导入、「全部历史」回填（都调 `importers/ow.run`） |
| `account/provision.py _ow_step`、`account/api.py /v1/account/ow-invitation`、`web/activation.py mint_ow_invitation`、`account/delete.py` | 开户建 OW 用户、邀请码、设置链接里的 `code`、删号时删 OW 用户 |

### 1.3 我们自己的链路

- **RawSync**（`ios/Runner/RawSync.swift` + `lib/src/raw_sync.dart`）：逐拍心跳（`HKHeartbeatSeriesSample`）、心电图、饮水。每类一个 `HKAnchoredObjectQuery` 锚点（UserDefaults `w2w.raw.anchor.<kind>`），Swift 读一页交给 Dart，Dart 上传 `POST /v1/ingest/healthkit`，**服务器 200 之后才提交锚点**（`rawCommit`）。只在前台、每次同步一轮之后跑；有时间预算（2 分钟、40 批）、页太大自动减半、409（新版同意书没确认）跳过心跳 / 心电图继续传饮水。教训写在文件头：第二个权限弹窗在第一个还没关时弹不出来，曾导致类型一直「未询问」、读取报错整轮静默失败（#214）。
- **服务器** `rawdata/ingest.py` + `rawdata/api.py`：Bearer token（账号 token 或只能上传的 token，`ingest_user`）、请求 ≤ 2 MB、`batch_id` 幂等（表 `rawdata_uploads`，迁移 430）、按 HealthKit UUID 去重、`deleted[]` 删除、同意书版本检查、饮水跟着 `water` 开关。
- **快捷指令导入器**（`importers/shortcut.py`，格式 `docs/shortcut-sync-spec.md`）：样本存 `shortcut_samples`（按类型 + 开始时间 + 来源去重），日值 / 睡眠 / 推断训练都从表里整天重算；累计型按「最大的单一来源」。
- **Apple 全量导出**（`importers/apple_zip.py`）：流式解析 export.zip，写日值、睡眠、训练（`source=apple_export`）和评分要的原始样本 `export_samples`、逐拍心跳、心电图；累计型同样「最大的单一来源」。
- **数据状态**（`sync/data_status.py`，#163）：按类型给出服务器最近收到的时间，`channel` 区分 `ow` / `app`。

---

## 2. OW iOS SDK 是怎么做后台同步的（参考）

读的是 `~/.pub-cache/hosted/pub.dev/open_wearables_health_sdk-0.0.25`（Flutter 包，只是桥接）和它依赖的 CocoaPods `OpenWearablesHealthSDK 0.15.0`（`Sources/OpenWearablesHealthSDK/`：`OpenWearablesHealthSDK.swift`、`Internal/Background.swift`、`Anchors.swift`、`Session.swift`、`Outbox.swift`、`Types.swift`），服务端读的是 `vendor/open-wearables/backend/app`。

| 项目 | OW 的做法 |
|---|---|
| 后台推送 | `startBackgroundDelivery()`：对每个跟踪的类型建 `HKObserverQuery`，`enableBackgroundDelivery(for:frequency: .immediate)`。回调里 `triggerCombinedSync()`：开 `beginBackgroundTask`、防抖 2 秒后同步**全部类型**一轮，然后**马上**调用 HealthKit 的 completionHandler（不是等同步完） |
| 系统后台任务 | `BGAppRefreshTask`（`earliestBeginDate` 15 分钟）和 `BGProcessingTask`（要网络、不要求充电），处理时各自只等 20 / 25 秒就结束任务（`group.wait(timeout:)`），过期时取消正在上传的请求。标识符在 Info.plist 的 `BGTaskSchedulerPermittedIdentifiers` |
| 锚点存哪 | UserDefaults suite `com.openwearables.healthsdk.state`，键 `anchor.user.<id>.<HK 类型>`、`fullDone.user.<id>`；会话进度 `Application Support/health_sync_state/state.json`（每类已发条数、完成的类型、首次导出的 `olderThan` 游标、待提交的锚点） |
| 分批 | 每一轮从每个未完成类型各取一块（`前台 2000 / 后台 100` 条 ÷ 类型数），合成一个 JSON 上传；后台剩余时间 < 5 秒就暂停。每轮 2000 条约 1.3 MB（SDK 注释） |
| 重试 | 0.14 起不再写本地 outbox：游标和锚点**只在服务器 2xx 后**前进，失败的这一轮下次从 HealthKit 重新读（只剩旧版 outbox 的清理代码）。401 刷新 token 后重试一次；网络恢复（`NWPathMonitor`）、解锁（`protectedDataDidBecomeAvailable`）、回到前台时续传 |
| 首次同步 | `syncDaysBack`（我们首次传 14，1.0.8 起；补传 90 / 全部由后台补全负责；0 = 全部历史），从「那天 0 点」起。首次用 `HKSampleQuery` **按结束时间从新到旧**一块块往回爬（`olderThan` 游标，可续传），某类爬完后再用 `HKAnchoredObjectQuery` 从空锚点一路读到底「捕获」当前锚点，之后才转增量 |
| 去重 | SDK 不去重；服务端 `data_point_series` 唯一键是（数据源、类型、`recorded_at` = 开始时间），训练按（数据源、开始、结束）唯一 |
| 训练格式 | `_mapWorkoutEfficient`：`type`（HKWorkoutActivityType 转 snake_case）、开始 / 结束、时区偏移（来自 `HKMetadataKeyTimeZone`）、来源、`values`（时长、活动 / 基础能量、距离、步数、划水、心率最小 / 平均 / 最大、跑步 / 骑行功率等、海拔、室内标记、天气）、`laps`（圈 / 段 / 标记事件）；`route`、`samples` 固定传空。服务端把活动 + 基础能量加成 `energy_burned`，类型经 `constants/workout_types/sdk.py` 转成统一类型 |
| 睡眠格式 | 每个 `HKCategorySample` 一条：`stage`（0 in_bed、1 sleeping、2 awake、3 light、4 deep、5 rem）、开始 / 结束、来源。服务端用 Redis 里的睡眠状态机按 2 小时间隔拼成一晚 |
| 删除 | 增量查询拿到的 `deletedObjects` 只用来算分页，**不上传**：「健康」里删掉的样本在 OW 里永远留着 |

**值得照搬**

1. 锚点 / 游标只在服务器 2xx 后前进，不要本地 outbox：HealthKit 本身就是队列，丢一轮下次重读，服务器按 UUID 去重。
2. 启动完成前原生注册 BGTask 和观察者（Flutter 在后台唤醒时不一定在跑）。
3. 每次唤醒同步全部类型（一个类型触发，所有类型一起读），防抖合并多次唤醒。
4. 锁屏读不到（HealthKit 错误码 6）就暂停，解锁 / 网络恢复 / 回到前台时续传。
5. 后台按剩余时间决定还发不发下一批，分块大小随前后台变。
6. 训练直接用 `HKWorkout.statistics(for:)` 的统计值。

**踩过的坑 / 可以改进的地方**

1. **首次同步可能有缺口（从源码推断，待真机验证）**：从新到旧爬完之后才捕获锚点，锚点包含了爬取期间新写入的样本、以及爬取期间才同步过来的旧时间样本，这些样本既不在爬取结果里，也不会在增量里出现。首次同步跨好几次启动时缺口更长。
2. **202 之后异步入库**：服务器先回 202 再由 Celery 处理，SDK 收到 202 就推进锚点；后台任务里校验失败的记录只报给 Sentry，数据就丢了。
3. **删除不同步**（见上表）。
4. **只存开始时间**：累计型去重只能推断结束时间；同一来源同一开始时间的两条样本会被合并。
5. **completionHandler 提前调用**：HealthKit 要求处理完再调用；连续 3 次不调用会停止推送。OW 提前调用、靠 `beginBackgroundTask` 撑 30 秒，能用但不稳。
6. **BGProcessingTask 只用 25 秒**：浪费了充电过夜时可以跑几分钟的窗口，首次同步只能靠用户开着 app。
7. **每个类型都 `.immediate`**：心率、能量这类高频类型频繁唤醒；iOS 对部分类型（如步数）本来就限制为每小时。
8. SDK 0.14 首次同步会被打断（`pubspec.yaml` 注释）、能量只同步一部分（`importers/common.py` 的 `incomplete` 规则就是为它加的）。
9. SDK 不支持的类型：手腕温度、HRV RMSSD（iOS 27）、站立、体力消耗、日照、音频暴露、锻炼分钟、步行平均心率、上下楼速度、步行稳定性、逐拍心跳、心电图（#219、`docs/raw-data-plan.md`）。

---

## 3. 新同步引擎的设计

### 3.1 app 端（Swift + Dart）

**分工**：后台被 HealthKit / BGTask 唤醒时 Flutter 不一定在跑，所以读取、上传、锚点、后台调度全部放在原生 Swift（`ios/Runner/SyncEngine/`，文件名示意：`TypeRegistry.swift`、`AnchorStore.swift`、`Reader.swift`、`Uploader.swift`、`Engine.swift`、`Background.swift`、`Status.swift`）。Dart 只做配置和界面：通过 `watch2whoop/native` 通道传服务器地址和 token、开始 / 停止、按范围重新同步、读状态和日志（`lib/src/sync_engine.dart`，同步中心界面）。现在 `raw_sync.dart` 的上传逻辑并入 Swift 引擎，心跳 / 心电图 / 饮水成为注册表里的三个类型。
第 1 阶段「同步中心」PR 正在做类型注册表、按类型锚点、按范围回退；本设计在它上面扩展，最终接口以合并的代码为准，第 2 阶段第一个 PR 先把本文和它对齐。

**token**：Dart 把账号 token（或只能上传的 token）交给 Swift，Swift 存自己的 Keychain 项，`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`（开机解锁过一次后后台可读，和现在 `first_unlock_this_device` 一致）。退出登录时一起清掉。

#### 3.1.1 类型注册表

一份 JSON（建议 `apps/watch2whoop/assets/healthkit_types.json`），Dart 读它告诉 Swift 读什么，服务器有对应的 Python 注册表；CI 里一个测试比对两边（要把这个 JSON 路径加进 Body Engine 测试的 CI 路径过滤）。每个类型的字段：

| 字段 | 说明 |
|---|---|
| `key` | 服务器名，等于 Body Engine 指标名（例如 `heart_rate`、`step_count`、`apple_sleeping_wrist_temperature`），和 `importers/ow.py SPECS`、`apple_zip`（`common.snake()`）一致 |
| `hk` | HealthKit 标识符；`kind` = quantity / category / workout / heartbeat / ecg |
| `unit` | 读取用的 HKUnit（app 换算好再传），服务器再按 `factor` 换成 Body Engine 单位（距离 km、步行速度 km/hr、血氧 0–100、比例 0–1） |
| `upload` | `raw` / `stats` / `raw+stats`（D1） |
| `agg` | `sum` / `avg` |
| `parents` | 申请权限时必须一起申请的父类型（逐拍心跳 → HRV SDNN；漏了会崩溃，见 `RawSync.swift`） |
| `min_ios` | 最低系统版本（例如 HRV RMSSD 是 iOS 27） |
| `bg` | 后台推送频率：`immediate` / `hourly` / `none` |
| `page` | 前台 / 后台每页条数 |
| `order` | 首次同步顺序 |
| `consent` / `flag` | 需要的同意书版本（心跳、心电图）/ 服务器开关（饮水 `water`） |
| `status_kind` | 对应 `sync/data_status.py KINDS` 的哪一项 |

**初始类型**：OW 现在申请的全部（`health_sync.dart requestedTypes`：步数、距离、爬楼、步行指标、活动 / 基础能量、心率、静息心率、HRV SDNN、VO₂max、血氧、呼吸、身高体重、体脂、体温、血糖、血压、正念、跑步 / 骑行指标、训练、训练强度分，不含生殖健康和饮食）+ OW 缺的（手腕温度、HRV RMSSD、站立、锻炼分钟、体力消耗、日照、环境 / 耳机音量、环境降噪、步行平均心率、上下楼速度、步行稳定性、游泳距离 / 划水、水温）+ 现有 RawSync 三类。**权限一次申请**：所有新类型合在一个弹窗里（不要第二个弹窗，见 #214）；app 升级加了新类型时，用 `getRequestStatusForAuthorization` 判断要不要再弹一次。

#### 3.1.2 按类型的锚点

- `AnchorStore`：UserDefaults suite `w2w.sync`，键 `anchor.<服务器用户 id>.<key>`；切换账号时换前缀；第一次运行时把 RawSync 的 `w2w.raw.anchor.*` 迁过来。
- **只在服务器 200 后提交**（沿用 RawSync 和 OW 0.14+ 的做法），不用本地 outbox。
- 每个类型还存：首次同步阶段、最近一次成功时间、本机最新样本时间、最近错误（给同步中心用）。

#### 3.1.3 后台唤醒

1. `didFinishLaunching` 里（和现在拉起 OW SDK 同一个位置）注册 BGTask，给注册表里 `bg != none` 的类型建 `HKObserverQuery` + `enableBackgroundDelivery`。频率建议：睡眠、训练、HRV、静息心率、呼吸、血氧、手腕温度、心电图 `immediate`；心率、能量、步数、距离等高频类型 `hourly`。**唤醒按重要类型，读取按全部类型**。
2. 观察者回调：`beginBackgroundTask`，防抖 3 秒合并多个回调，跑一轮增量；**这一轮结束后**再统一调用所有挂着的 completionHandler。
3. `BGAppRefreshTask`（最早 30 分钟后）：一轮增量。
4. `BGProcessingTask`（要网络、**要求充电**）：首次同步、全部历史、按范围重新同步，充电过夜时跑满系统给的时间，过期处理器里停在当前页（锚点已经提交到最后一个 200）。
5. 回到前台 / 冷启动：一轮（替代现在 `SyncOnOpen` 触发的 OW 同步）。
6. 锁屏读不到（错误码 6）→ 暂停，`protectedDataDidBecomeAvailable` 后续传；`NWPathMonitor` 网络恢复后续传。
7. 后台剩余时间 < 8 秒不再发新的一批。
8. BGTask 标识符（示意）`watch2whoop.sync.refresh` / `watch2whoop.sync.backfill` 加进 Info.plist；影子期和 OW 的两个并存。

#### 3.1.4 首次补 90 天和断点续传

避开 OW 的缺口（第 2 节坑 1）：

1. **先快**：每个重要类型用 `HKSampleQuery` 读最近 48 小时（不动锚点）上传，今天页一分钟内就有数据。
2. **再全**：每个类型从空锚点开始 `HKAnchoredObjectQuery`，谓词 = 起点之后（90 天或全部历史），分页读；**锚点本身就是断点**，读到哪、提交到哪，中断后从这里接着读，期间新写入的样本也会在后面读到，没有缺口。第 1 步的 48 小时会再传一次，服务器按 UUID 跳过。
3. 各类型轮流（round-robin），按 `order`（睡眠、训练、HRV、静息心率……心率最后）。
4. 进度：每类最近一页的样本日期 / 起点（插入顺序近似时间顺序，是估算）。
5. 「全部历史」= 谓词去掉起点，走同一套；从 90 天切到全部历史时重置锚点（同 `health_range.dart` 现在的做法）。
6. 重装 app 锚点丢失 → 重新读 90 天，服务器按 UUID 去重，只多费流量。

#### 3.1.5 按范围回退（重新同步）

同步中心选类型 + 日期范围：`HKSampleQuery` 按范围分页（不动锚点），请求带 `range_id`；全部页成功后发 `range_done`。服务器在这个范围里删掉这次没出现的样本（处理漏掉的删除），重算受影响的天。用于：权限重新打开后、怀疑缺数据时、影子对比发现差异时。

#### 3.1.6 训练和睡眠

- **训练**（`HKWorkout`）：UUID、`workoutActivityType`（原始整数 + 名字）、开始 / 结束、`duration`（扣暂停）、`statistics(for:)` 的活动能量、基础能量、距离（按类型）、步数、划水、心率最小 / 平均 / 最大、跑步 / 骑行功率；`workoutEvents`（暂停 / 圈 / 段）；元数据：室内、时区、海拔、平均 MET、训练强度分（如有）；`workoutActivities`（iOS 16 多段训练）待查是否要拆。训练时段的心率不单独传（心率样本本来就在同步）。**路线不读**（D2）。
- **睡眠**（`HKCategorySample` sleepAnalysis）：每条阶段样本原样传（值 0–5 + 来源 + 时区元数据），服务器切段。

#### 3.1.7 来源信息

每批一个来源字典（名字、bundle id、`productType`、设备名 / 型号 / 厂商、系统版本），样本里只放下标。设备类型按 `productType` / 设备名判断 watch / phone（照搬 OW `_inferDeviceType`）。

#### 3.1.8 电量和流量控制

- 增量读不到新东西就不联网。
- 列式 JSON + `Content-Encoding: gzip`，每个请求压缩后 ≤ 1 MB（公网反向代理限 2 MB）；前台每页 2000 条、后台 200 条（注册表可按类型改）。
- 首次补历史默认只用 Wi-Fi（`allowsExpensiveNetworkAccess = false`、`allowsConstrainedNetworkAccess = false`），同步中心有「用蜂窝数据补历史」开关；增量允许蜂窝。
- 低电量模式只同步 `immediate` 类型，暂停补历史；`thermalState` ≥ serious 暂停补历史。
- 两次后台轮之间至少 5 分钟（睡眠 / 训练触发的除外）。
- 每轮记录：触发方式、耗时、批数、字节、结果（只有计数，没有健康数值），上传到服务器表 `sync_runs`，用来量后台可靠性；电量用 Xcode Energy / MetricKit（本机，不接第三方）。
- 服务器回 429 时按 `Retry-After` 等待，锚点不动。

### 3.2 服务端

代码放新模块 `body_engine/src/body_engine/healthkit/`：`registry.py`（类型表 + 单位换算，和 app 的 JSON 比对）、`ingest.py`（扩展现有 `rawdata/ingest.py`）、`store.py`、`derive.py`、`samples.py`（读取接口）、`shadow.py`、`params.py`（阈值和出处）。

> **S1 已实现（迁移 690，开关 `apple_sync_engine`）**，和下文不同的地方以代码为准：
> - 没有新建 `healthkit/` 模块：注册表在第 1 阶段的 `rawdata/hk_types.py`（`ENGINE_TYPES`，包含第 1 阶段的 `HK_TYPES`），接收在 `rawdata/engine.py`，接口和模型仍是 `rawdata/api.py`、`rawdata/ingest.py`。
> - `hk_samples` 沿用迁移 675 的表，`type` 是 HealthKit 标识符（不是指标名），加了 `source_id`、`value_text`、`tz`、`meta`、`origin`、`range_id`、`batch_id` 列；训练也存在这里（`value` = 分钟）。
> - 限流不另建 `ingest_usage`：直接按 `rawdata_uploads`（已有 `(user_id, received_at)` 索引）算每分钟批数和每个 UTC 日的字节数；只限带引擎字段的批次，旧版 app 不受影响。
> - 按范围重新同步：每页带 `range: {id, start, end, types}`，最后 `range_done: id`；删的是范围开始前收到、这次没再出现的样本（范围进行中增量上传的新样本不会被误删）。
> - `mode` 目前只有 `ow` / `shadow`；`app` 模式和 `apple_sync_primary` 开关留给 S5。
> - 第 1 阶段的直传类型（站立、锻炼分钟……）经 `samples[]` 上传时，日值照旧立即重算、`app_types_pause` 照旧生效；其他类型只记 `hk_dirty_days`，日值 / 睡眠 / 训练由 S2 派生。
>
> **S2 已实现（`rawdata/derive.py`、参数和出处 `rawdata/params.py`，命令 `hk-derive-run`，没有新迁移）**，和下文不同的地方以代码为准：
> - 模块放在 `rawdata/derive.py`（不是 `healthkit/derive.py`）。队列按天处理：每天一个事务（锁住这一天的 `hk_dirty_days` 行 + 每个用户一把 advisory 锁），派生完删掉这一行；一次最多 240 秒，剩下的留给下一次；没同意的用户留在队列里。
> - 日值：非累计型 = 当天全部原始样本的平均 / 最小 / 最大 / 条数（和 `importers/ow.daily_rows` 一样）；累计型 = 当天 15 分钟统计桶之和。**第 1 阶段的直传类型不由 S2 派生**：上传时 `rawdata/quantities.rebuild_days` 已经按它们自己的口径算过，在 `resolve_daily` 里的位置也不随模式变。
> - 睡眠：OW 的状态机按**用户**拼一晚（不分来源，会话来源取第一条样本），我们按**来源**分组，用 OW 自己的规则（间隔 > 2 小时切开、会话只能由睡着 / 在床开始、阶段清理：有核心 / 深睡 / REM 时去掉「在床」「睡着」，只有在床时在床算睡着，重叠的阶段从上一段结束处截断）拼成会话，再走同一个 `ow.sleep_segments`（和更高优先级的会话重叠就整段丢掉：手表 > 手机 > 其他）+ `common.sessionize`。只有手表、或手机「在床」包着手表阶段时两边一样；多个来源互相穿插时的差异算 `source_priority`。
> - 训练：类型表在 `training/params.py`（`HK_WORKOUT_ACTIVITIES`：HealthKit 原始值 → OW SDK 的名字；`OW_SDK_WORKOUT_TYPES`：SDK 名字 → OW 统一类型，`hk_workout_type()`）；`training/importer.parse_app` 按 `parse_ow` 的口径（HealthKit 时长、总能量 = 活动 + 基础、活动能量直接用训练统计值）。训练心率 / TRIMP（`enrich`）还是读 OW，留给 S3。
> - 同步模式：`shadow`（以及开关关掉后的 `ow`）下日值只写 `daily_metric_sources`、不重选 `daily_metrics`，不写 `sleep_sessions`，训练 `excluded = 'shadow'`；`resolve_daily` 遇到派生类型的 `app` 候选时才查模式，非 `app` 模式跳过它们（基础代谢参考值同样跳过），只剩这种候选的那一项删掉以前选中的 `app` 值（从 `app` 回退时用）。`app` 模式下 `SOURCE_PRIORITY` 照旧（app > ow），训练 `SOURCE_RANK_APP`（app 第一），写睡眠、重选日值、派生完排队算分。`app` 模式已经在派生、`resolve_daily`、`refresh_duplicates` 里预留好，`engine.mode()` 还只返回 ow / shadow，`apple_sync_primary` 留给 S5。
> - 接入：`deploy/ops/ops.py` 每小时任务先跑 `hk-derive-run`；`sync/refresh.run_scores`（`sync-refresh-run` 和 inline 模式）算分前先派生这个用户的脏天（60 秒预算）。部署机每分钟的导入任务要加一条「`hk_dirty_days` 有行时跑 `hk-derive-run`」（部署者自己的运维脚本，开 `apple_sync_engine` 给内测组之前加）。
> - 影子期用户看到的东西不变：`water/store.py` 训练日加水量也排除 `excluded = 'shadow'`（其他读 `workouts` 的地方本来就要 `excluded IS NULL`）；数据状态页（`sync/data_status.py`）的「来源」不列不参与选值的 `app` 值。
>
> **S3 已实现（`rawdata/samples.py` 读取层、`rawdata/side_by_side.py` 逐日对照，没有新迁移）**，和下文不同的地方以代码为准：
> - 模块是 `rawdata/samples.py`（不是 `healthkit/samples.py`）。返回值做成 OW 读取的样子（OW 的序列名、`importers/ow.Sample`、睡眠走 OW 的会话规则 + `ow.sleep_segments`），调用方的算法不动；活动 / 基础能量只有 15 分钟统计桶，按和窗口重叠的比例折算。
> - 只有 `app` 模式读它：`scores/inputs.load`（`compute-scores`、`hrv-compare`）、`insights/stress`、`training/importer.enrich`（训练心率、TRIMP、能量拆分）、今天页睡眠阶段图（`web/views.night_stages`，`stages_source = app`）。`app` 模式下新引擎优先，OW 只补它没有的（新引擎没有的那一晚、那一类样本、那一天；OW 时代的训练两边都没有能量样本时保留已存的活动能量），直到 S6 把 OW 的历史原始样本迁进来；所以 S3 不会造成第 8 节风险 1。`ow` / `shadow` 模式完全不读新引擎的样本，结果和以前一样（`tests/test_apple_sync_read.py`）。
> - 影子对比的读取能力（第 4 节的输入，判定和每晚任务留给 S4）：同一用户每天两条链路并排——`daily_metric_sources` 的 `ow` / `app` 日值（各自的平均 / 最小 / 最大 / 条数、app − ow、相对差、`daily_metrics` 选中了谁）、`sleep_sessions` 里存的 OW 睡眠和从阶段样本现算的 app 睡眠（按醒来日期）、两份训练。命令 `body-engine sync-shadow-day --user-id N --date D [--end D] [--json]`（中文 Markdown，含健康数值，只到终端或 `health_data/reports/`），接口 `GET /v1/users/{user}/sync/engine/days?date=&end=`（只能看自己的，最多 31 天，`apple_sync_engine` 对这个用户关着时 404）。
> - 部署机每分钟的导入任务在 `hk_dirty_days` 有行时跑 `hk-derive-run`、每小时任务也先跑一次（部署者自己的运维脚本；开关关着时上传被拒、队列一直为空，任务空跑）。
>
> **S4–S6 和同步状态已实现（迁移 800，参数和出处 `rawdata/params.py`）**，和下文不同的地方以代码为准：
> - S4 影子对比 `rawdata/shadow.py`：判定规则就是第 4 节的表；每项一行写 `sync_shadow_diffs`（verdict：ok / explained + 原因代码 / mismatch / missing_app / new），只计数的 `sync_shadow_summary`（连续一致天数、覆盖率、能不能切换和原因）给切换门槛、doctor 和部署者的运维插件读。分数一项用 `scores/inputs.load(sync_mode='app')` 不写库重算（app 的样本优先、OW 补缺，app 的睡眠替换存着的 OW 睡眠，和切换后一模一样），和存着的分数比。命令 `sync-shadow-compare`（每晚 import-ow 之后）、`sync-shadow-report`（`--summary` 只有计数），接口 `GET /v1/users/{user}/sync/engine/shadow`。
> - S5 切换 `rawdata/cutover.py`：`apple_sync_primary` 是总开关（默认关），**真正的切换按用户做**：`sync-mode switch --user-id N` 一步完成（把人加进 `sync_app` 分组、过门槛、存切换前 14 天分数快照、`sync_mode_state.mode = app`、app 训练去掉 shadow 标记、最近 90 天按 app 模式重派生、排队算分）；第一次算完分后 `sync-mode apply` 做切换后检查（快照 vs 新分数）。`engine.mode()` 只有在 state = app、总开关开着、没有固定在 ow 时才返回 app，所以关总开关或 `sync-mode rollback` 下一次读取就回到 shadow；rollback 再把 app 训练改回 shadow、删 app 睡眠并从 OW 重新导入这几晚、重选日值、排队算分。原始数据两边都不删，可以来回切。版本门槛看 `rawdata_uploads.app_version`（引擎批次，≥ 1.0.8），`--force` 只跳过影子和分数门槛。`app` 模式下引擎上传自己排队算分，`POST /sync/refresh` 不再导 OW。开户 / 设置链接 / 删号不碰 OW 的部分没有做（第 9 节）。
> - S6 历史 `rawdata/ow_archive.py`：**没有把 OW 样本搬进 `hk_samples(origin='ow')`**，而是复用 #287 的本地副本 `ow_sample_cache`（它存的就是评分要的 OW 原始样本，带来源、优先级、XML 标记；OW 样本没有结束时间、XML 样本没有 UUID，搬过去反而要编 UUID、存两份）。`ow-archive --user-id N` 把副本补全到「评分起点 − 365 天 − 压力基线」到现在（每 60 天一窗、从新到旧、每窗提交，可续跑），睡眠阶段（以前每次实时读 OW）存进 `ow_archive_segments`。存档过、且在 app 模式的用户：评分输入、压力、训练心率、今天页睡眠阶段图都读存档，不再问 OW；ow / shadow 模式忽略存档。
> - 同步状态 `rawdata/sync_status.py`：被拒的上传按原因记进 `ingest_failures`（auth / format / refused / limit / server，只有代码，同一原因 10 分钟内合并计数，吊销的 token 也能认出是谁的）；`GET /v1/users/{user}/sync/engine/status` 和 `GET /v1/ingest/sync-status` 给今天页面板和 app 用（字段见 app 接口文档第 6 节）。引擎批次的回答里 `status` 不再数全部样本（`counts: false`）。
>
> **S5b 已实现（没有迁移）**：
> - 新开关 `apple_sync_new_accounts`（全局：和 `open_signup` 一样只认 `--state all`，而且 `apple_sync_engine` 也得是 `all` 才生效，`rawdata/cutover.new_accounts()`）。开着时开户（`account/provision.py` 的 `ow` 步骤）不建 OW 用户，改调 `cutover.start_on_engine()`：账号一开就是 `app` 模式（不过门槛：没有可对比的、也没有会丢的 OW 历史），步骤照样记 done。关掉只影响之后的新开户。
> - **没有 OW 账号的人（`users.ow_user_id` 为空）一旦是 `app` 就一直是 `app`**：`engine.mode()` / `cutover.desired()` 对他们不看总开关 `apple_sync_primary`，`sync-mode apply` 不会把他们回退，`sync-mode rollback` 不加 `--force` 拒绝（`no_ow`）——shadow 对他们只会把数据藏起来，没有 OW 可回。
> - 不发 OW 邀请码：`app` 模式的人兑换激活码得到的设置链接不带 `ow` 也不带 `code`（`note = sync_engine`）；`POST /v1/account/ow-invitation` 回 409 `sync_engine`。
> - 强制重新同步（`sync/resync.run_step`）在 `app` 模式不导 OW、不问 OW：`queued` 时如果这次请求之后 app 开的范围重新同步还没 `range_done`，或者引擎的脏天还没派生完（先在这里派生一次），就保持排队等下一分钟；最多等 `RESYNC_ENGINE_WAIT`（`sync/params.py`，phone_done 之后 30 分钟），然后从范围起点算分。`importing` 途中被切到 `app` 的直接去算分。
> - 数据状态（`sync/data_status.py`）的 `channel` 跟着模式：`app` 模式下原来标 `ow` 的类型都标 `app`，手腕温度去掉 `sdk_unsupported`；回答多一个 `mode`。
> - 「手机同步已断开」（`sync/broken.py`）在 `app` 模式看引擎批次（`rawdata_uploads` 里带 `phase` 的）而不是 OW：没有 `server_stale`（服务器不拉数据），回答多 `channel`；今天页的「重新连接」在 `channel = app` 时直接打开 app 的同步页，不再要 OW 邀请码。`sync-broken` 也列出 `app` 模式的人。
> - 切换前体检 `body-engine sync-mode readiness [--user-id N] [--json] [--out 文件]`（`rawdata/readiness.py`，只读、不重算分数）：顶部是三个开关的状态，下面每人一行——模式（当前 → 想要的）、app 版本、连续一致天数 / 门槛、覆盖率、分数一致性（影子对比存下的分数项，和 `score_check` 同样的阈值）、结论（已切换 / 可以切换 / 还不能切 / 引擎没开）和「还差什么」（同意书、固定在 OW、app 版本、最近没有引擎上传、还没有影子对比、连续一致、覆盖率、分数）。没有 OW 账号的人只看 app 版本。含名字和分数差，只到终端或 `health_data/reports/`。精确门槛（会重算分数）仍是 `sync-mode switch --dry-run --user-id N`。
>
> **上线顺序**：① 本 PR 部署（新开关默认关，零影响）；② 每晚 `sync-shadow-compare` 跑满 7 天后看 `sync-mode readiness`，过了门槛的人逐个 `sync-mode switch`（或 `flags set apple_sync_primary --state cohort --cohort sync_app` + 每小时 `sync-mode apply`）；③ **app 端先发一版在没有 OW 账号时也算「已连接」的版本**（现在 1.0.8 / 1.0.9 的引导、同步页、`connectSyncViaAccount` 还要求连 OW，碰到 409 `sync_engine` 会提示连接失败），等 TestFlight 上能装到的都是这一版；④ `flags set apple_sync_new_accounts --state all`（`apple_sync_engine` 已经是 `all`），新开户从此不建 OW 用户；⑤ 全员切完、`ow-archive --check` 全部完整后进入 S7（停写 → 只读 → 下线）和 S8（第 10 节）。

#### 3.2.1 接口

沿用 `POST /v1/ingest/healthkit`，`IngestIn` 加可选字段（旧版 app 不受影响）：

```json
{
  "batch_id": "uuid", "device": "iPhone", "app_version": "1.0.8", "tz": "<手机的 IANA 时区>",
  "phase": "recent | backfill | incremental | range", "range_id": null,
  "sources": [{"name": "…", "bundle": "…", "product": "Watch…", "device_type": "watch"}],
  "samples": [{"type": "heart_rate", "uuid": ["…"], "start": [1700000000.0], "end": [1700000000.0],
               "value": [60.0], "src": [0]}],
  "categories": [{"type": "sleep_analysis", "uuid": ["…"], "start": [], "end": [], "value": [3], "src": [], "tz": []}],
  "workouts": [{"uuid": "…", "activity": 50, "activity_name": "traditionalStrengthTraining", "start": 0, "end": 0,
                "duration_s": 0, "stats": {"active_kcal": 0, "basal_kcal": 0, "distance_m": null, "avg_hr": null},
                "events": [], "meta": {"indoor": true, "tz": "…"}, "src": 0}],
  "stats": [{"type": "step_count", "bucket_s": 900, "start": 1700000000, "values": [0, 12, 0]}],
  "heartbeats": [], "ecgs": [], "water": [],
  "deleted": ["uuid"],
  "range_done": null
}
```

（数值都是示例。）`GET /v1/ingest/healthkit` 的回答加 `engine: {accepts: [...], mode: "ow|shadow|app"}`：app 只发服务器声明接受的字段，并按 `mode` 决定开不开 OW SDK（第 5 节）。

#### 3.2.2 原始样本存哪里（迁移 690）

`NNN_healthkit_samples.sql`（待定）：

```sql
CREATE TABLE hk_sources (id BIGSERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT, bundle TEXT, product TEXT, device_type TEXT, device_name TEXT,
  UNIQUE (user_id, name, bundle, product, device_name));
CREATE TABLE hk_samples (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  hk_uuid UUID NOT NULL,
  type TEXT NOT NULL,                 -- 注册表 key；训练是 'workout'
  start_at TIMESTAMPTZ NOT NULL, end_at TIMESTAMPTZ NOT NULL,
  value DOUBLE PRECISION,             -- Body Engine 单位
  value_text TEXT,                    -- 睡眠阶段 / 训练类型
  source_id BIGINT REFERENCES hk_sources(id),
  tz TEXT,                            -- HKMetadataKeyTimeZone
  meta JSONB,                         -- 只有训练和少数元数据
  origin TEXT NOT NULL DEFAULT 'app', -- app / ow（下线前从 OW 迁入的历史，第 5.4 节）
  range_id BIGINT, batch_id UUID, received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, hk_uuid));
CREATE INDEX hk_samples_user_type_start_idx ON hk_samples (user_id, type, start_at);
CREATE TABLE hk_stats (user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE, type TEXT NOT NULL,
  bucket_start TIMESTAMPTZ NOT NULL, value DOUBLE PRECISION NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, type, bucket_start));
CREATE TABLE hk_dirty_days (user_id INT, day DATE, kinds TEXT[], queued_at TIMESTAMPTZ, PRIMARY KEY (user_id, day));
CREATE TABLE sync_ranges (...);  CREATE TABLE sync_runs (...);  CREATE TABLE ingest_usage (user_id, day, batches, bytes);
GRANT SELECT ON hk_samples, hk_stats TO grafana_ro;   -- 如果 Grafana 要读
```

- 15 分钟桶按 UTC 对齐，覆盖所有半小时 / 45 分钟时区；只存非零桶。
- 复用：逐拍心跳 → `heartbeat_series`、心电图 → `ecg_recordings`、饮水 → `water_intake`、批次幂等 → `rawdata_uploads`，都不变。
- 体量（估算，按 Apple Watch 典型采样频率）：每人每天约 1–2 千条原始样本（心率最多）+ 约 1 千个统计桶；10 人一年约千万行级，要在 doctor 里看表大小。以后可以按月分区或把老心率压成数组（同 `heartbeat_series.beats_ms`），先不做。

#### 3.2.3 聚合成日值，`app` 来源的优先级

- `healthkit/derive.py` 按「脏天」整天重算（同快捷指令导入器的做法）：
  - 非累计型：当天全部原始样本的平均 / 最小 / 最大（和 OW 链路同口径）；
  - 累计型：当天（用户时区）15 分钟桶求和 = 苹果自己的多设备去重；
  - 写 `daily_metric_sources(source='app')`，再 `resolve_daily()`。
- **每人一个同步模式**，由两个灰度开关决定（`rollout/registry.py`，状态和审计都现成）：
  - `apple_sync_engine` 关 → `ow`；
  - `apple_sync_engine` 开、`apple_sync_primary` 关 → `shadow`；
  - 两个都开 → `app`。
- `resolve_daily` / `choose`：`shadow` 模式下 `app` 候选不参与选值（只存着做对比）；`app` 模式下非累计型和睡眠的优先级 `app` > `ow` > `apple_export` > `shortcut`（`SOURCE_PRIORITY` 改成按用户取），累计型照旧「干净候选里取最大」。

#### 3.2.4 训练和睡眠写进现有表

- **睡眠**：阶段样本按来源分组 → 和 `importers/ow.py sleep_segments()` 同样的规则（高优先级会话重叠时丢低的）→ `common.sessionize()` → `replace_sleep(source='app')`。同一批 HealthKit 阶段样本、同一个切段函数，结果应该和 OW 链路一致。`shadow` 模式不写 `sleep_sessions`（`replace_sleep` 一天只留一个来源），对比任务直接从 `hk_samples` 现算。
- **训练**：`workouts(source='app', external_id = HK UUID)`，经 `training/importer.upsert()`（用户的修改照样生效）、`refresh_duplicates()`（`SOURCE_RANK` 加 `app`：`shadow` 时排在 `ow` 后，`app` 模式排第一）。类型映射：把 OW 的 `_workoutTypeString` 和 `constants/workout_types/sdk.py`（都是 MIT）移植成 `training/params.py HK_WORKOUT_TYPES`，保证和 OW 导入的 `type` 一样（`THIRD_PARTY_NOTICES.md` 加出处）。`total_kcal` = 活动 + 基础（和 OW 口径一致），`active_kcal` 直接用训练统计值（比 OW 链路按样本比例拆更准）。`enrich()` 的心率从 `hk_samples` 读；`shadow` 模式下 app 训练 `excluded = 'shadow'`，不显示、不参与去重，切换时清掉。
- 删除：`deleted[]` 里的 UUID 从 `hk_samples` 和 `workouts(source='app')` 删掉，对应的天标脏。

#### 3.2.5 写入后马上算分

接口里只存原始数据、记脏天，回 200（几十毫秒）。每分钟的导入定时任务（或 `BODY_SYNC_SCORES=inline`）跑 `body-engine hk-derive-run`：对每个有脏天的用户派生日值 / 睡眠 / 训练，然后复用 `sync/refresh.py` 的算分队列（`scores_queued` → `sync-refresh-run`）、规则、状态卡。同步到算分约 1 分钟等待 + 1–2 分钟计算。`app` 模式下 `POST /sync/refresh` 不再调 `import-ow`，只在有脏天时排队。

#### 3.2.6 幂等

- 批次：`batch_id` → `rawdata_uploads`，重发直接返回上次的结果。
- 样本：主键 `(user_id, hk_uuid)`，`ON CONFLICT DO NOTHING`（HealthKit 样本不可变，改动 = 删除 + 新 UUID）。
- 统计桶：请求里给的范围先删后插，同一事务。
- 训练：`(user_id, source, external_id)` upsert。派生：整天重算；睡眠 `replace_sleep`。

#### 3.2.7 鉴权和限流

- 沿用 `rawdata/api.py ingest_user`（Bearer：账号 token 或只能上传的 token）、`account_api.gate`、同意书检查（普通数据要普通同意书，心跳 / 心电图要 `RAW_CONSENT_VERSION`）。`user_id` 只来自 token，绝不来自请求体。
- 限流（`healthkit/params.py`）：每人每分钟批数上限 + 每天字节上限（`ingest_usage`），首次 / 补历史批次额度更大；超了回 429 + `Retry-After`。解压后大小上限（防压缩炸弹），条数上限沿用 `MAX_*` 的写法。

---

## 4. 影子对比（第 3 阶段）

**两条链路同时写，来源分开**：OW 照旧 `import-ow` 写 `source='ow'`；新引擎写 `hk_samples`、`daily_metric_sources(source='app')`、`workouts(source='app', excluded='shadow')`。用户看到的结果不变。

**每天的对比任务** `body-engine sync-shadow-compare`（每晚在 `import-ow` 之后跑，只看结束超过 36 小时的「已收口」的天，最近 7 天）写表 `sync_shadow_diffs(user_id, date, kind, key, ow_value, app_value, verdict, reason)`：

| 对比项 | 一致 | 可以解释（`reason` 代码） |
|---|---|---|
| 非累计日值（每项的平均 / 最小 / 最大 / 条数） | 相对差 ≤ 0.1% | `ow_same_ts`（OW 合并了同一来源同一时刻的样本，条数不同）、`deleted_in_health`、`late_sample`（OW 增量只回看 2 天，迟到的旧样本没进去）、`xml_overlap` |
| 累计日值 | 相对差 ≤ 1% | `method`：在 OW 推断结束时间的误差范围内（阈值写在 `params.py`，出处 `importers/ow.py` 文件头）；服务器再用步数原始样本按 HealthKit 规则算一遍做第三方核对 |
| 睡眠（每晚） | 入睡 / 醒来差 ≤ 1 分钟，睡眠和各阶段分钟差 ≤ 1 | `source_priority`、`ow_split`、`deleted_in_health` |
| 训练 | 按 `_is_dup()` 配对；次数相同，时长差 ≤ 1 分钟，总能量差 ≤ 2%，平均 / 最大心率差 ≤ 1，类型相同 | `active_kcal_method`（活动能量口径不同，预期）、`deleted_in_health`、`xml_copy` |
| 分数（建议做） | 用 app 输入不写库重算恢复 / 负荷 / 睡眠分（同 `rawdata/compare.py` 的做法），差 ≤ 1 分 | 能追到上面某个可解释的输入差异 |

其余：OW 有、app 没有 → `missing_app`（不一致）；app 有、OW 没有（OW 不支持的类型）→ 只列为「新增」，不判。

**切换标准（每人）**：连续 7 个已收口的天没有「不一致」；OW 有的项 app 覆盖 ≥ 95%；睡眠和训练没有 `missing_app`。

**怎么看结果**：
- `body-engine sync-shadow-report [--user-id N] [--since 日期]`：中文 Markdown，含健康数值，只输出到终端或 `health_data/reports/`（不进 git，同 `hrv-compare`）。
- doctor：对比任务写一份只有计数的摘要（每人：一致 / 可解释 / 不一致的项数、最近一次对比时间），`deploy/ops/ops.py` 的 doctor 加一行「影子对比」；部署者自己的 doctor / 私有运维插件读同一份摘要。
- 先例：`docs/system-design.md` 第 17 节「快捷指令 → OW」的并行对账。

---

## 5. 切换和下线（第 4 阶段）

### 5.1 切换开关

- `body-engine flags set apple_sync_primary --state cohort --cohort beta`（先 beta 组，再 `all`）。
- 新命令 `body-engine sync-mode apply`（每小时任务里也跑，幂等）：找出模式变了的用户，处理派生数据：
  - `app` 训练清掉 `excluded='shadow'`，`refresh_duplicates`；
  - 最近 N 天（默认 90）用 `app` 重派生睡眠和日值；
  - 重算分数。
  
  用户的 app 版本低于要求（从 `rawdata_uploads.app_version` 看）时拒绝切换。
- app 收到 `mode = app` 后调 `OpenWearablesHealthSdk.stopBackgroundSync()`（**OW 停写**，凭据和锚点保留，便于回退）。新开户（`account/provision.py`）在 `app` 模式下不建 OW 用户，设置链接不带 `code`。

### 5.2 回退办法

- 关 `apple_sync_primary`（或把人移出分组）→ `sync-mode apply` 把 `app` 训练改回 `excluded='shadow'`，删掉 `sleep_sessions(source='app')`，跑 `import-ow` 补回这段时间的 OW 睡眠，`resolve_daily`、重算分数。
- app 收到 `mode = shadow` 后重新 `startBackgroundSync`：OW 的锚点还在，会把停写期间的数据增量补上；`import-ow` 的游标从停写前回看 2 天，能接上。前提：用户没重装 app（重装后 OW 首次同步 90 天）。
- 每一步都是开关 + 幂等命令，不删原始数据。

### 5.3 OW 停写、只读保留多久

1. **回退期 30 天**（D5）：OW 容器、对外同步入口、`import-ow` 都照常运行（没有新数据），随时能回退。
2. **只读期 60 天**：
   - 关闭 OW 的对外同步入口（反向代理里删掉对应的站点或路径）；
   - 停掉 OW 的 Celery worker / beat / Flower；
   - OW 数据库和 API 只在内网保留，供抽查和第 5.4 节的迁移；
   - `import-ow` 从定时任务里去掉。
3. **下线**：
   - 最后一次 pg_dump OW 库并放进备份仓库，验证能恢复；
   - 删掉 OW 容器和数据卷，释放约 1.3 GB 内存（#219 的实测）。

### 5.4 下线要动的地方

**数据**（最先做，见第 8 节风险 1）

- 已经导入 Body Engine 的数据保留不动：`daily_metric_sources` / `daily_metrics` / `sleep_sessions` / `workouts` 里 `source='ow'` 的行永久保留，过去的天照旧从它们选值。
- **只存在于 OW 的历史原始样本要迁进来**：`body-engine ow-archive --user-id N`（新命令，可续跑）。
  - 从 OW 数据库导出心率、HRV、呼吸、血氧、手腕温度、步数原始样本和睡眠阶段，写进 `hk_samples(origin='ow')`。
  - OW 的 `data_point_series.external_id` 就是 HealthKit UUID，和 app 同步的同一条样本自然去重；XML 导入的没有 UUID，用确定性 UUID v5。
  - OW 没存结束时间，迁入时 `end_at = start_at` 并在 `meta` 标出。
  - 另外鼓励每人在 `app` 模式下做一次「全部历史」读取（充电过夜），HealthKit 本身是最全的来源。
- 读取点改成读 `healthkit/samples.py`：`scores/inputs.py`（`load_ow` → `load_hk`，`_Merger` 里 app 优先于迁入的 OW 样本）、`insights/stress.py`（训练时段改读 `workouts` 表）、`training/importer.py enrich`（OW 时代的训练在拿不到能量样本时保留已存的 `active_kcal`）、`web/views.py`（睡眠阶段图）、`rawdata/compare.py`。

**服务器和部署**（只写通用说法）

- 容器：OW 的 8 个容器（`deploy/open-wearables/`、部署机上的 compose 项目）。
- 对外同步入口：反向代理里给 OW iOS SDK 开的那组接口。
- 定时任务：`deploy/ops/ops.py job_scores` 里的 `import-ow`，部署机上对应的定时任务。
- 备份：`deploy/ops/ops.py DATABASES` 里的 `open-wearables`（最后一次 dump 之后去掉），部署机的备份脚本同步改。
- doctor 检查项：`EXPECTED_SERVICES`、`PORTS`、`TAILSCALE_SERVE` 里的 OW 项，部署机 doctor 里的 OW 项。
- Grafana：OW 数据源和 `deploy/grafana/ow_grafana_ro.sql`，看板里读 OW 的面板。
- 代码（只读期结束后一个单独的 PR）：
  - `ow_client.py`、`importers/ow.py`、`training/importer.import_ow`；
  - `cli.py` 里的 `import-ow` / `ow-link` / `import-workouts`；
  - `sync/refresh.py` / `sync/history.py` 的 OW 部分；
  - `account/*` 和 `web/activation.py` 的 OW 部分；
  - `sync/data_status.py` 的 `channel='ow'`；
  - `users.ow_user_id` 列保留（历史记录），不再写。
- app：
  - 删 `open_wearables_health_sdk` 依赖和 Podfile；
  - `AppDelegate` 里的 SDK 初始化和 `SyncLogBuffer`；
  - Info.plist 里 OW 的 BGTask 标识符；
  - `health_sync.dart`、`sync_stats.dart` 的日志解析；
  - 设置流程里的 OW 邀请码和 `owHost`。

---

## 6. 拆 PR 和工作量

前提：第 1 阶段同步中心 PR 已合并（它改 `RawSync.swift`、`raw_sync.dart`、`rawdata/ingest.py`）。工作量是一个熟悉代码的人的人日，含测试和 review 修改。app 版本按现在的发版节奏估。

| # | PR | 范围 | 依赖 | 开关 | 工作量 | app 版本 |
|---|---|---|---|---|---|---|
| S1 | 服务器：原始样本表 + 注册表 + 接收接口 | 迁移（待定号）、`healthkit/registry.py`、`ingest.py`、gzip、`accepts`/`mode`、限流、`sync_runs` | 同步中心 PR | `apple_sync_engine`（关时 `accepts` 不含新字段） | 4–5 | — |
| S2 | 服务器：派生 + 脏天队列 + 同步模式 | `derive.py`（日值、睡眠、训练）、`hk-derive-run`、`resolve_daily` 按模式、`SOURCE_RANK`/`SOURCE_PRIORITY` 加 `app`、训练类型表移植 | S1 | 同上 | 5–6 | — |
| S3 | 服务器：读取接口，去掉运行时读 OW | `healthkit/samples.py`，改 `scores/inputs`、`stress`、`enrich`、`web/views`、`rawdata/compare`（`ow` 模式行为不变） | S1 | 按用户模式 | 3–4 | — |
| A1 | app：Swift 同步引擎内核（前台） | 注册表 JSON、`AnchorStore`（迁 RawSync 锚点）、读取器、列式 + gzip 上传、48 小时 + 锚点爬取、断点续传、Keychain token；RawSync 三类并入 | 同步中心 PR、S1 | 服务器 `accepts` + `mode` | 5–6 | 1.0.8 |
| A2 | app：后台 | 观察者 + 分频率推送、BGAppRefresh / BGProcessing、锁屏 / 网络 / 前台续传、电量和流量控制、`sync_runs` 上报 | A1 | 同上 | 4–5 | 1.0.8 |
| A3 | app：训练、睡眠、累计型统计桶 | `HKWorkout` 统计 / 事件 / 元数据、睡眠阶段、`HKStatisticsCollectionQuery` 15 分钟桶、按范围重新同步 | A1 | 同上 | 3–4 | 1.0.8 或 1.0.9 |
| A4 | app：同步中心接新引擎 | 按类型「没采集 / 没同步」（本机最新样本 vs 服务器最新）、首次同步进度、蜂窝开关、日志；不再解析 OW 日志 | A1–A3 | 同上 | 2–3 | 1.0.9 |
| S4 | 服务器：影子对比 | `shadow.py`、`sync_shadow_diffs`、`sync-shadow-compare` / `report`、不写库重算分数、doctor 摘要 | S2、S3 | — | 4–5 | — |
| — | **第 3 阶段：影子对比 1–2 周** | beta 组开 `apple_sync_engine`；看报告、修差异 | S4、app ≥ 1.0.9 | | （运营） | |
| S5 | 服务器：切换和回退 | `sync-mode apply`、版本门槛、开户 / 设置链接 / 删号不再碰 OW、`sync/refresh`、`sync/history` 的 `app` 模式、数据状态 `channel` | S2、S4 | `apple_sync_primary` | 3–4 | — |
| S6 | 服务器：OW 历史原始样本迁入 | `ow-archive` 命令、可续跑、核对行数 | S1、S3 | — | 2–3 | — |
| S7 | 部署和运维：OW 停写 → 只读 → 下线 | `deploy/ops`、Grafana、备份、doctor 项；部署者私有部分在 ops 仓库 | S5、S6、回退期结束 | — | 2–3 | — |
| A5 | app：移除 OW SDK | 依赖、AppDelegate、Info.plist、设置流程、旧同步页 | 只读期开始后 | — | 2–3 | 1.0.10 或之后 |
| S8 | 服务器：删除 OW 代码 | 第 5.4 节代码清单 | 下线后 | — | 1–2 | — |

合计：服务器约 24–32 人日，app 约 16–21 人日，**总计约 40–50 人日**；外加第 3 阶段 1–2 周、回退期 30 天、只读期 60 天。S1–S3 可以和 A1 并行。

---

## 7. 测试方案

**单元测试**
- 服务器（pytest，合成数据，数值标「示例」）：
  - 同一批重发、同一样本两批、删除、gzip、大小 / 条数上限、429、同意书、用户隔离；
  - 注册表单位换算和 app JSON 比对；
  - 「同一份合成样本」分别做成 OW 接口格式和 app 格式，派生出的日值 / 睡眠 / 训练要一样（第 4 节「一致」的定义）；
  - 范围对账删除；`sync-mode apply` 切换 / 回退往返后数据一致。
- app：
  - 纯逻辑抽成可测的 Swift / Dart 函数：分桶数学、锚点只在 200 后提交、预算、页减半、模式切换；
  - Dart 沿用 `test/raw_sync_test.dart` 注入假源的写法；
  - HealthKit 本身在 XCTest 里难以模拟，靠模拟器集成测试。

**模拟器里往「健康」写样本**
- 扩展现有调试种子（`AppDelegate` 的 `watch2whoop/debug` 通道 `DebugHealthSeeder`，只在 DEBUG + 模拟器编译）：
  - 每个注册表类型写一段；
  - 睡眠阶段（core / deep / rem / awake）；
  - 用 `HKWorkoutBuilder` 写带心率和能量的训练；
  - 删除；
  - 带 `HKDevice` 模拟手表 / 手机设备（来源都会是本 app，跨来源优先级只能在服务器测试里覆盖）。
- 也可以在模拟器的「健康」app 里手动添加数据，触发观察者。

**后台唤醒怎么测**
- BGTask：Xcode 调试器里暂停后执行
  - `e -l objc -- (void)[[BGTaskScheduler sharedScheduler] _simulateLaunchForTaskWithIdentifier:@"watch2whoop.sync.refresh"]`
  - 过期用 `_simulateExpirationForTaskWithIdentifier`
- HealthKit 推送：app 退到后台后在「健康」app 里加一条数据，看同步日志和服务器 `sync_runs`。模拟器的推送行为和真机不完全一样（待查），最后以真机为准。
- 真机（beta 组，TestFlight）检查：
  - 锁屏一夜后睡眠多久到服务器；
  - 训练结束到出现在今天页的延迟；
  - 划掉后台后的行为；
  - 低电量模式；
  - 首次 90 天用时和流量；
  - 一周的 `sync_runs` 统计（触发方式分布、失败率）。

**对比任务的测试数据**
- `body_engine/tests/fixtures/shadow/`：两个虚构用户各 7 天合成数据，同时生成 OW 格式和 app 格式。
- 人为注入已知差异：
  - OW 缺一条迟到样本；
  - 「健康」里删了一条；
  - 同一时刻两条样本；
  - 累计型方法差；
  - 一晚 XML 副本；
  - 一次训练只在一边。
- 断言每项的 `verdict` 和 `reason`。
- 压力测试：合成 90 天 × 1 人的回填量，测接收吞吐、库大小和 API 内存。

---

## 8. 风险

1. **下线 OW 后历史分数丢输入（最大风险）**：
   - `compute-scores` 每次都从 OW 读整段历史的原始样本，每晚还要全量重算；
   - 只停 OW 不迁数据，第二天所有历史分数就会按缺数据重算。
   - 对策：S3（读取接口）和 S6（历史迁入）是 S7 的硬前提，下线前在一个测试库里全量重算，对比 `daily_scores` 不变。
2. **iOS 后台同步的稳定性**：
   - 唤醒时机由 iOS 决定；被划掉的 app 不再被唤醒；低电量模式推迟后台任务；部分类型最多每小时推送一次；completionHandler 处理不当会被停推送。
   - 对策：多种触发（推送、BGTask、前台、解锁、网络恢复）；`sync_runs` 量化；数据状态里「很久没收到」提醒。
3. **首次同步的量**：
   - 90 天、多类型、心率最多，按 Apple Watch 典型频率是几十万条级（估算）。
   - 对策：先 48 小时；列式 + gzip；能量只传统计桶；默认 Wi-Fi；充电时 BGProcessing；断点续传；服务器限流和批量写入。
   - 数据库增长要在 doctor 里看。
4. **电量**：
   - 影子期 OW SDK 和新引擎同时跑，beta 用户耗电和流量大约翻倍，两套 BGTask 还会抢系统给的后台额度（OW 数据变慢 → 只比较已收口的天）。
   - 对策：影子期只给 beta 组、1–2 周；高频类型 `hourly`；合并唤醒；低电量 / 发热时暂停补历史；用 MetricKit 量。
5. **HealthKit 权限变化**：
   - iOS 不告诉 app 读权限被拒；被拒的类型读出来是空的，和「没采集」分不开。
   - 权限关掉期间锚点会不会越过那些样本，待真机验证。
   - 新版本加类型要再弹一次权限。
   - 对策：一次弹窗申请全部；同步中心对比「本机最新样本」和「服务器最新」，区分「没同步」和「没采集或没授权」；权限重新打开后提示按范围重新同步。
6. **多设备**：
   - iPhone + 手表同一个来源账号：累计型用苹果的统计去重；非累计型按来源存，日均值口径和现在一样。
   - 第三方 app 写的心率会混进来（现在也一样）。
   - 同一账号两台 iPhone（「健康」iCloud 同步）：样本 UUID 一样，服务器去重，只多费流量。
7. **时区**：
   - 按资料里的用户时区切天（和所有导入器一致）。
   - 出差时手机时区和资料时区不同：存 `HKMetadataKeyTimeZone` 和每批的手机时区，数据状态里提示。
   - 夏令时用每个时刻的固定偏移（同 `ow._local`）。
   - 半小时时区靠 15 分钟桶。
8. **版本门槛**：TestFlight 用户不一定马上更新；按 `app_version` 拒绝切换，旧版本用户继续走 OW 直到更新。
9. **同意书**：新增类型是否在同意书范围内，见 D6（已定：同步中心的类型已涵盖；血压、血糖等开给内测组前再确认）。

---

## 9. 完全不用 OW：评估、时间表、风险（2026-10-26）

**结论：可以完全不用 OW，包括新用户的首次 90 天导入，不需要再 fork OW 的代码。** 服务器侧 S1–S6 已经齐了：接收、派生、读取层、影子对比、按用户切换 / 回退、OW 历史本地存档。剩下的关键路径在 app（A1–A3）和三处还会主动找 OW 的服务器代码（下面「还差什么」）。OW 里我们需要的规则（训练类型表、睡眠切段规则）已经移植并注明出处（`THIRD_PARTY_NOTICES.md`）；OW iOS SDK 里值得借鉴的后台调度写法（第 2 节「值得照搬」）iOS 开发可以参考，照抄代码时同样是 MIT，要在 `THIRD_PARTY_NOTICES.md` 加一行出处。

**新用户首次 90 天**：app 的引擎 `phase=backfill` 从空锚点按类型分页读 90 天（第 3.1.4 节），服务器按 UUID 去重、`backfill` 阶段限流放宽（每分钟 120 批、每天 512 MB），本地实测一批 5000 条样本 + 2880 个统计桶（gzip）入库 0.34 秒。没有 OW 账号的用户切换门槛只看 app 版本（没有可对比的、也没有会丢的 OW 历史）。90 天以前的历史照旧用「导入健康历史」上传 export.zip（`history/`，本来就不经过 OW）。

**还差什么（按顺序）**

| # | 内容 | 位置 | 估计 |
|---|---|---|---|
| A1–A3 | app：引擎内核、后台、训练 / 睡眠 / 统计桶（接口要求见 `docs/apple-sync-engine-app-contract.md`） | `apps/` | iOS 开发，随 1.0.8 / 1.0.9 |
| S5b（**已实现**，见第 3.2 节开头） | 开户不再建 OW 用户、设置链接不带 OW 邀请码（`account/provision._ow_step`、`web/activation.mint_ow_invitation`、`account/api` 的 `ow-invitation`），按「新开户默认走引擎」的全局开关；`sync/resync.run_step` 在 app 模式不导 OW、改成等 app 的按范围重新同步；`sync/data_status` 的 `channel` 跟着模式变；`sync/broken.py` 在 app 模式看引擎上传而不是 OW | 服务器 | 2–3 人日 |
| S7 | 运维：OW 停写 → 只读 → 下线（第 5.3 节），`import-ow` 和 OW 备份从定时任务去掉，Grafana 的 OW 数据源 | `deploy/`、ops 仓库 | 2 人日 |
| S8 | 删 OW 代码（第 5.4 节清单） | 服务器 | 1–2 人日 |

**时间表（建议，按每周日发版）**

| 日期 | 事项 |
|---|---|
| 10-27 → 10-31 | 本 PR 合并部署（开关全关，零影响）；内测组开 `apple_sync_engine`（只收、只对比） |
| 11-01（周日） | iOS 1.0.8：A1（前台引擎）。内测组开始影子期；每晚 `sync-shadow-compare` |
| 11-08 | iOS 1.0.9：A2 / A3（后台、训练、睡眠、统计桶）——影子对比要有睡眠和训练才有意义，**真正的 7 天连续一致从这里开始算** |
| 11-15 前后 | 内测组逐个 `sync-mode switch`（门槛不过的人留在 shadow，报告里看原因）；同时做 S5b |
| 11-22 前后 | `apple_sync_primary` 开给所有人，`sync-mode apply` 每小时把过了门槛的人切过去；新开户默认走引擎 |
| 切完后 | 每人跑 `ow-archive`（OW 还在时做，可续跑）；回退期（D5 建议 30 天，有了本地存档可以缩到 14 天）→ OW 只读 → 下线，释放约 1.3 GB 内存 |

最快的路径：1.0.9 在 11-08 带上 A2 / A3，11-15 内测切换，11-22 全员，12 月中 OW 下线。

**风险**

1. **iOS 后台可靠性**（第 8 节风险 2）：只能靠 app 端多种触发 + 服务器看 `sync_runs` 和同步状态面板量化；切换门槛里不含后台可靠性，建议内测组切换后看一周 `sync_runs` 再开给所有人。
2. **影子期耗电和抢后台额度**：OW SDK 和引擎同时跑；影子期只给内测组、只 1–2 周。
3. **多来源睡眠**：OW 的状态机按用户合并来源，我们按来源分开（S2 说明）；手表 + 手机 / 第三方 app 交叉的晚上会被判为「可解释（source_priority）」，差异大于 20 分钟会是「不一致」，要人看报告。
4. **数据库增长**：每人每天约 1–2 千条原始样本 + 约 1 千个统计桶；10 人一年千万行级；doctor 要加 `hk_samples` 表大小（S7）。
5. **切换后分数漂移**：切换前快照 + 第一次重算后自动对比（`post_check`），超过阈值看报告、必要时 `sync-mode rollback`（一步回退，原始数据两边都在）。
6. **OW 下线后才发现的缺口**：下线前在测试库全量重算一次对比 `daily_scores`（第 8 节风险 1 的对策），`ow-archive --check` 必须全部 complete。

---

## 10. OW 下线时要删的代码（S8 准备，2026-10-29）

> 只是清单，**现在不删**。S8 在只读期结束、OW 下线之后单独一个 PR（第 5.3 节）。行号会变，以符号为准。
> 前提（缺一不可）：每个有 `ow_user_id` 的人都已在 `app` 模式（`sync-mode readiness` 里没有「还不能切」），`ow-archive --check` 全部 complete，测试库里全量重算一次 `daily_scores` 不变（第 8 节风险 1）。

**先要知道的四点**
1. `importers/ow.py` **不能整个删**：`Sample`、`SPECS`、`spec_for`、`parse_sample`、`sleep_segments`、`dedupe_cumulative`、`drop_xml_duplicates`、`DEVICE_PRIORITY`、`XML_SOURCE` 是引擎自己的规则（`rawdata/derive.py`、`samples.py`、`shadow.py`、`ow_archive.py`、`training/importer.py` 都在用）。先把它们挪进一个纯规则模块（MIT 出处照留），再删联网部分（`link_user`、`_first_sample_time`、`run`、`daily_rows`）。
2. OW 历史存在 `ow_sample_cache` / `ow_sample_cache_range` / `ow_archive` / `ow_archive_segments`（迁移 770 / 800），不是 `hk_samples(origin='ow')`（第 3.2 节 S6 的说明）。这四张表、`importers/ow_cache._read` / `_ranges` / `CACHE_TYPES` / `_src_flags`、`rawdata/ow_archive.get` / `reads_archive` / `check` / `samples` / `sleep_segments` / `workout_windows` / `window_samples` 都要留；只删要连 OW 的 `ow_archive.archive()` 和 `ow_cache.fetch` / `samples` / `store` / `store_safe`。`reads_archive` 的「必须是 app 模式」可以放宽成「有存档就读」。
3. 算分只有「app 模式 + 有存档」时读存档；否则拿 `client` 读 OW，`client=None` 时这个人的 OW 原始样本整段不读，分数会漂。所以第 1 点的前提必须先满足。
4. 几处直接 `OWClient()`、没有保护：`cli.py` 的 `compute-scores`、`import-apple-zip`、`hrv-compare`（不加 `--no-ow` 时）、`ow-archive`；`account/provision.run` 的 `ow` 步骤（开关 `apple_sync_new_accounts` 开着时已经不走）。拿掉 OW 的 API key 之前先改掉，否则这些命令直接报错。

**服务端 `body_engine/src/body_engine/`**

| 处理 | 文件 | 内容 |
|---|---|---|
| 删 | `ow_client.py` | `OWClient`、`OWError` |
| 删 | `sync/ow_watch.py` | OW 上传轮询器；`api.py` lifespan 里的 `ow_watch.watcher()` 一起删 |
| 拆 | `importers/ow.py`、`importers/ow_cache.py`、`rawdata/ow_archive.py` | 见上面第 1、2 点 |
| 改 | `config.py` | `ow_url`（`BODY_OW_URL`）、`ow_api_key`（`BODY_OW_API_KEY_FILE`） |
| 改 | `batch.py` | `make_ow_client`、`make_ow_client_safe`、`_step_import_ow`、`STEPS` / `DEFAULT_STEPS` 里的 `import-ow`、`_step_compute_scores` 的 `ow_cache` |
| 改 | `account/provision.py` | `_ow_step`、`STEPS` 的 `ow`、`OWError` / `OWClient`、`run_pending` 的 `ow_client`；`_engine_step` 变成唯一路径后也可以删 |
| 改 | `account/delete.py` | `delete_account` 里删除 OW 用户那段和 `ow_client` 参数 |
| 改 | `account/api.py` | `POST /v1/account/ow-invitation`（`ow_invitation`） |
| 改 | `account/stats.py` | 「最近从 OW 拉取」那项（`sync_state` 的 `ow` 行），改读 `rawdata_uploads` |
| 改 | `web/activation.py`、`web/api.py` | `ow_signing`、`mint_ow_invitation`、`sign_jwt`、`activate()` 的 `mint_ow` / `ow_code`、`setup_link` 的 `ow` / `code`、`BODY_OW_PUBLIC_URL`、`BODY_OW_INVITE_SIGNING_FILE`；`web/api.py` 的 `mint_ow=not body.web` |
| 改 | `web/views.py` | `ow_segments`；`night_stages` 的 `ow` 一路只留存档 |
| 改 | `scores/inputs.py`、`scores/engine.py` | `load_ow` 和 `load()` 里读 OW 的分支、`ow_cache` 参数；`run()` 的 `client` / `ow_cache`。留 `load_ow_archive`、`OW_BASE`、`ow_types` |
| 改 | `insights/stress.py`、`insights/jobs.py` | `_ow_samples`、`_workouts`（OW 的 events 接口）、`compute` 里读 OW 的分支；`run()` 的 `ow_cache` |
| 改 | `training/importer.py` | `import_ow`、`window_samples`、`enrich` 读 OW 的分支、`enrich_pending` 的 `client`；`source == "ow"` 的保留逻辑和 `archived` 分支留着 |
| 改 | `sync/refresh.py` | `_run_import` 的 OW 导入、`import_queued`、`run_queued` 的 `import_queued_at` 分支、`_score` 的 `OWClient`；`status()` 的 `linked` |
| 改 | `sync/resync.py` | `run_step` 的 OW 导入（`_first_day`、`ow.run`、`importer`、`OWClient`、`importing` 状态）；只剩本 PR 的 app 模式路径 |
| 改 | `sync/history.py`、`history/run.py` | 按窗口从 OW 导入全部历史；`history/run.py` 的「关联了 OW 就全量拉一次」和 `ow_cache=`。`sync/history.py` 是否整个删待定（app 模式改由引擎全量读取） |
| 改 | `sync/broken.py`、`sync/overview.py`、`sync/received.py`、`sync/data_status.py`、`rawdata/sync_status.py` | 只留 `app` 一路：OW 凭据判断、`ow_watch` / `sync_state(ow)` 子查询、`channels.ow`、`ow_last_sample_at`、`ow_last_sync_at`、`channel = 'ow'` |
| 改 | `validation/__main__.py` | `OWClient` 和 `--no-ow` |
| 待定 | `rawdata/cutover.py`、`shadow.py`、`side_by_side.py`、`compare.py`、`engine.py` 的 `ow` / `shadow` 模式 | OW 下线后没法回退、也没有新的对比数据；三模式要不要并成只有 `app`，看开关体系是否保留（第二步再删） |
| 留 | `importers/common.py` 的 `SOURCE_PRIORITY["ow"]`、`sync/gaps.py` / `training/params.py` 的 `SOURCE_RANK["ow"]` | 过去的天照旧从 `source='ow'` 的行选值 |
| 留 | 所有迁移文件、`users.ow_user_id` 列 | 历史记录，不再写。`ow_watch` 表、`sync_refresh.import_queued_at` 列以后可以用新迁移去掉 |

**CLI（`cli.py`）**：删 `ow-link`、`import-ow`、`import-workouts`、`ow-watch`、`sync-broken`（或改成只看引擎）、`history-backfill-run`（待定）、`_ow_or_none`；`CONSENT_GATED` 去掉 `import-ow` / `import-workouts`；`hourly-batch --steps` 默认值；`ow-archive` 只留 `--check`；`sync-shadow-*`、`sync-mode` 第二步再定。

**今天页（`web/static/`）**：`sync-repair.js` 的 OW 重连整段（连同 `index.html` 的 `<script>`、`sw.js` 的 `SHELL_FILES`、`i18n-en.js` 里对应的英文）；`sync-status.js` 的 `connectLink` / `connect()` / 连接码卡片；`data-status.js` 读 `ow_last_sample_at` 的那行改读 app 上传时间。`app.js` 里 `stages_source === "ow"` 的显示留着（那些夜确实来自 OW 存档）。

**测试（`body_engine/tests/`）**：整个删 `test_ow_importer.py`（`sleep_segments` / 去重的用例先挪到新规则模块的测试）、`test_ow_cache.py`、`test_ow_watch.py`、`test_ow_refresh_replay.py`；大改 `test_activation.py`、`test_sync_broken.py`、`test_sync_refresh.py`、`test_batch.py`、`test_training.py`、`test_account.py`、`test_open_signup.py`、`test_apple_sync_cutover.py` / `_derive.py` / `_read.py`；`FakeOW` 当夹具的（`test_scores_db.py`、`test_insights_db.py`、`test_web.py`、`test_sleep_night.py`、`test_friends.py`、`test_sync_overview.py`、`test_today_first_screen.py`、`test_data_status.py`、`test_sync_center.py`、`test_history_backfill.py`、`test_health_history.py`）换成引擎上传的合成数据；`tools/bench_hourly_batch.py` 重写或删。

**iOS app（`apps/watch2whoop/`，A5）**：第 5.4 节的 app 清单之外，还有 `lib/src/health_sync.dart`（整个）、`sync_stats.dart` 的 SDK 日志解析、`sync_repair.dart`、`config.dart` 的 `owHost` / `owUserId` / `owConnected` / `UrlKind.ow`、`setup_flow.dart`、`account_flow.dart` 的 `connectSyncViaAccount`、`account_api.dart` 的 `owInvitation`、`sync_bridge.dart`、`resync_flow.dart` / `resync.dart` / `backfill.dart` / `health_range.dart` 的 OW 部分、`main.dart` 的 `HealthSync.start`、`pages/` 下的引导 / 同步 / 设置链接 / 权限页；`pubspec.yaml` 的 `open_wearables_health_sdk`、`Podfile(.lock)`、`AppDelegate.swift` 的 SDK 初始化和 `SyncLogBuffer`、`Info.plist` 的 `com.openwearables.healthsdk.task.*`；对应的 Dart 测试。

**部署和其他**：`deploy/open-wearables/` 整个目录（含 `invite.py`：开户包以后只发今天页登录码）；`deploy/ops/ops.py` 的 `DATABASES`（最后一次 dump 之后）、`EXPECTED_SERVICES`、`PORTS`、`TAILSCALE_SERVE`、`GRAFANA_DATASOURCES`、`job_scores` / `batch_step` 的 `import-ow`、doctor 的 OW 项；`deploy/grafana/` 的 OW 数据源、`ow_grafana_ro.sql`、`build_dashboards.py` 的 OW 面板；根目录和 `quickstart/` 的 compose 里 OW 的服务、密钥和环境变量、`quickstart/run.py` 的 `setup_ow()`；`.gitmodules` 和 `vendor/open-wearables`；`tools/export_public.py` 的 `.gitmodules` 规则；CI `ios-pods` 的注释。`THIRD_PARTY_NOTICES.md` / `docs/LICENSES.md` 的 OW 署名**留着**（移植过来的规则还在），去掉子模块后写明上游 commit。文档：`CLAUDE.md`、两份 README、`docs/system-design.md`、`hourly-batch.md`、`raw-data-plan.md`、`admin-guide.md`、`apps.md`、本文件第 5 节。
