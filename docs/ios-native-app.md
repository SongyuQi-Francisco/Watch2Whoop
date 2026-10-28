# iOS 原生 app（SwiftUI）：架构和开发约定

> 状态：#340 N0（架构文档 + 工程骨架），2026-10-29。这份文档是 N1–N9 各个 agent 的共同依据：改约定先改这里，在 PR 里说明。
> 相关：#340（总纲）、#337（本地优先的决定）、#219（自建同步引擎，`apple-sync-engine.md`、`apple-sync-engine-app-contract.md`）、`ios-native-plan.md`（测量和分期）、`apps.md`（版本号和发版）。
> 文中的数字都是示例，不是任何真人的数据。

## 0. 一句话

`apps/ios/` 是纯 SwiftUI 的新 app，替换 Flutter 版（`apps/watch2whoop/` 停在 1.0.8，之后只修 bug）。Bundle ID 和 Team 不变，TestFlight 上覆盖安装就能升级，第一次启动把 Flutter 版的 token 和服务器地址迁过来。

- **页面只读本地库**：每个账号一个 SQLite 库（GRDB）。
- **同步层负责收发**：后台和服务器双向同步，写操作先落本地，再经离线队列上传。
- **算法在手机上算**：评分算法是服务器下发、带版本号的 JS 包，用 JavaScriptCore 运行，不用 WebView。

## 1. 已定的约定

| # | 约定 | 说明 |
|---|---|---|
| C1 | **最低 iOS 16** | #340 N0 的要求。`ios-native-plan.md` D8 原来建议 17。因为要支持 16：状态用 `ObservableObject` / `@Published`，不用 Observation（`@Observable` 要 17）；图表用 Swift Charts（16 起就有）。负责人改成 17 时，只需改 `project.yml` 的 `deploymentTarget`，代码不用跟着改 |
| C2 | **纯 SwiftUI，没有 WebView** | 系统组件只在 SwiftUI 没有对应能力时用 UIKit，例如 `UITabBarAppearance`、扫码的 `DataScannerViewController` |
| C3 | **工程用 XcodeGen 2.46.0 生成** | `project.yml` 是唯一来源；`.xcodeproj`、`Info.plist`、`.entitlements` 都是生成出来的，不进 git |
| C4 | **依赖只用 SPM，第三方只有 GRDB 7.11.1**（MIT，精确版本） | 需要新依赖时先在 #340 说明。系统框架随便用（Charts、JavaScriptCore、CryptoKit、HealthKit、AVFoundation、Speech、BackgroundTasks） |
| C5 | **双语** | 用户能看到的文字一律写 `tr("中文", "English")`（`Localization/I18n.swift`）。`scripts/check_sources.py` 在 CI 里检查：`tr()` 以外不能有中文，英文那一半不能有中文。服务器返回的文字靠请求头 `X-Body-Lang` 选语言 |
| C6 | **视觉沿用今天页** | 颜色 token 和 `app.css` 一一对应（`DesignSystem/Palette.swift`，CI 比对）；三环并排、恢复在中间；界面只放数字、图表和短标签，长段文字留给 AI |
| C7 | **图标只用 Lucide** | 由 `scripts/lucide_assets.py` 转成 asset，许可证写在 `THIRD_PARTY_NOTICES.md`。不自己画图标；app 界面元素也不混用 SF Symbols |
| C8 | **公开仓库** | 不写个人信息、域名、真实 Team ID；测试和示例只用明显虚构的数值 |
| C9 | **Swift 5 语言模式，并发检查设为 `minimal`** | 先保证拷进来的同步代码能编译。N9 之前评估要不要切到 Swift 6 |

## 2. 模块划分

### 2.1 目录（`apps/ios/`）

| 目录 | 内容 | 谁来改 |
|---|---|---|
| `project.yml`、`Config/` | 工程定义；签名占位 `Watch2Whoop.xcconfig`（自己的值写在不进 git 的 `Watch2Whoop.local.xcconfig`）；版本号 `W2W_MARKETING_VERSION`、`W2W_BUILD_NUMBER` | N0 / N9 |
| `Watch2Whoop/App/` | 入口 `Watch2WhoopApp`、`AppDelegate`（后台任务、后台 URLSession）、`AppModel`（启动 → 登录 / 连接中 / 已登录）、`AccountSession`（一个账号的 API + 库 + 同步）、`MainTabView`（底栏） | N0；N8 管登录流程 |
| `Watch2Whoop/DesignSystem/` | `Palette`（颜色）、`Typo`（字号）、`Metrics`（圆角、边距）、`Icon`（Lucide）、`Card`、`SectionHeader`、`ListRow`、`MetricTile`、`Chip`、`RingView`、`PrimaryButton`、`PageScaffold`、`.w2wSheet` 弹层、`PressScale` | 谁需要谁加；只加通用组件 |
| `Watch2Whoop/Networking/` | `APIClient`（Bearer、401、重试）、`APIError`、`Endpoint`（所有路径） | N0；页面加路径常量 |
| `Watch2Whoop/Auth/` | `KeychainStore`、`CredentialStore`（token、服务器地址、user id）、`FlutterMigration` | N0 / N8 |
| `Watch2Whoop/Storage/` | `AppDatabase`（每账号一个库）、`Schema`（迁移）、`Records`（GRDB 记录类型） | 页面在 `Schema` 末尾**追加**迁移 |
| `Watch2Whoop/Sync/` | `SyncCoordinator`、`RemoteSync`（`LegacyRemote` / `DeltaRemote`）、`OfflineQueue`、`DocKey` | N0 / N2 客户端 |
| `Watch2Whoop/Algorithms/` | `AlgorithmRuntime`（JavaScriptCore）、`BundleVerifier`、`algo-builtin.js`（目前是占位） | N1 |
| `Watch2Whoop/HealthSync/` | 从 Flutter 拷来的 `SyncEngine.swift`、`RawSync.swift`、`Backfill.swift`（逐字节相同），加上 `HealthSyncBridge`（async 包装） | 拷贝件用脚本同步；N8 接手 |
| `Watch2Whoop/Features/<页面>/` | 每个页面一个目录：`Today/`（N3）、`Trends/`（N4）、`Record/`、`Food/`（N5）、`Jarvis/`（N6）、`Circles/`（N7）、`Me/`、`SignIn/`、`SyncCenter/`（N8）；`Placeholders/` 是还没做的 tab | 各页面 agent |
| `Watch2Whoop/Localization/` | `tr()`、`Lang` | — |
| `Watch2Whoop/Resources/` | `Assets.xcassets`（AppIcon、`LaunchBackground`、`Icons/lucide.*`） | — |
| `Watch2WhoopTests/` | XCTest 单元测试，在模拟器上跑 | 每个 PR 都要加测试 |
| `Packages/FlutterShim/` | 本地 SPM 包，模块名就叫 `Flutter`：只有 `FlutterMethodCall`、`FlutterResult`、`FlutterError` 三个类型，让拷进来的文件不改一个字节也能编译 | N8 删掉 |
| `scripts/` | `sync_flutter_sources.sh`（拷贝 / `--check`）、`lucide_assets.py`（生成图标 / `--check`）、`check_sources.py`（双语和颜色 token） | — |

### 2.2 依赖方向

`Features → (Sync, Storage, DesignSystem, Localization)`；`Sync → (Networking, Storage, Algorithms)`；`Networking / Storage / DesignSystem` 互相不依赖。

- **页面不直接请求接口拿要显示的数据**：读本地库，需要新数据就调 `session.sync.refresh(...)`。
- **页面的写操作**：先改本地行，再 `session.sync.enqueue(...)`。只有在线功能（第 5.5 节）可以直接 `session.api.send(...)`。
- 页面之间不互相 import 内部类型，要共享就放进 `DesignSystem` 或 `Storage`。

### 2.3 拷进来的同步代码

- `SyncEngine.swift`、`RawSync.swift`、`Backfill.swift` 和 `apps/watch2whoop/ios/Runner/` 里的原文件逐字节相同。A2–A4（#219）还在原文件上改，所以这里不改拷贝件。
- 原文件更新后跑 `apps/ios/scripts/sync_flutter_sources.sh`；CI 里的 `--check` 发现不一致只给警告，不让 job 失败。
- 拷贝件仍然是 method channel 的形状：`handle(FlutterMethodCall, result)`。`HealthSyncBridge.call(.engine, "engineRead", args)` 把它包成 `await`。
- Dart 那一侧的逻辑（`lib/src/sync_engine.dart`、`raw_sync.dart`、`backfill.dart`：读什么、怎么上传、什么时候提交锚点）在 N8 改写成 Swift，见第 9 节。改写完给这三个文件换成原生接口，删掉 `FlutterShim`。
- 锚点和 backfill 状态存在 `UserDefaults` 里，键不带 `flutter.` 前缀（`w2w.sync.anchor.*`、`w2w.raw.anchor.*`、`w2w.backfill.state.v1`）；backfill 的上传 token 在 Keychain 的 `w2w.backfill` / `token`。这些和 Flutter 版是同一份，升级后直接接着用，不用迁移。
- `AppDelegate` 已经注册了 `watch2whoop.sync.backfill` 后台任务，并在每次启动时重新挂上后台 URLSession。`BGTaskSchedulerPermittedIdentifiers` 里还预留了 `watch2whoop.sync.refresh`，给 N8 的定时刷新用。

## 3. 启动和账号

```
启动 → FlutterMigration.run（只跑一次）→ 有 token + 服务器？
  否 → SignInView（N0 是 token 表单；N8 换成设置二维码 / 邮箱 / Apple）
  是 → 已经知道 user id？
        是 → 立刻打开本地库，显示 tab（先画缓存）→ 后台调 /v1/web/me 更新 flags 和名字
        否 → 「正在连接」→ /v1/web/me → 记下 user id → 打开本地库
任何请求 401 → APIClient.onUnauthorized → 清掉 token → 登录页（不重试）
退出登录 → 删掉这个账号的库目录 + Keychain（原生项和 Flutter 项都删）→ 通知服务器 /v1/account/logout
```

- `AccountSession`（`@MainActor ObservableObject`）通过 `environmentObject` 注入，里面有 `config`、`api`、`db`、`sync` 和 `flags`。页面用 `@EnvironmentObject var session: AccountSession` 拿。
- 功能开关来自 `/v1/web/me` 的 `flags`，本机缓存在 `w2w.native.flags.v1`。每个原生页面如果需要服务器开关，就读 `session.flags`。
- Debug 版可以用启动环境变量自动登录，给模拟器和截图用：`SIMCTL_CHILD_W2W_DEV_SERVER`、`SIMCTL_CHILD_W2W_DEV_TOKEN`、`SIMCTL_CHILD_W2W_DEV_LANG=zh|en`。

## 4. 本地数据库

### 4.1 文件

- 路径：`Application Support/W2W/accounts/<key>/body.sqlite`，`key` = sha256(`服务器|user id`) 的前 16 位十六进制。同一台手机登录不同账号或不同服务器，数据互不相见。
- 文件保护级别 `completeUntilFirstUserAuthentication`：后台同步要读。
- 不进 iCloud / 设备备份：服务器有全量备份；Keychain 是「仅本机」，换机恢复后本来就要重新登录、从云端重新拉。
- GRDB `DatabasePool`（WAL）：读不会挡写。
- 退出登录时删掉整个目录。删之前，离线队列里没发出去的写操作先问用户（N8 的弹窗，规则同 `offline-plan.md` 第 4 节）。

### 4.2 表（迁移 `v1_initial`，定义在 `Storage/Schema.swift`）

时间用 UTC 秒（REAL）；日期是用户本地的 `YYYY-MM-DD`（TEXT）；单位和服务器一致（kg、kcal、ms、bpm）；JSON 存 BLOB。

| 表 | 主键 | 用途 | 谁写 / 谁读 |
|---|---|---|---|
| `meta` | `key` | 零碎信息（全量恢复时间、当前算法版本……） | 同步层 |
| `docs` | `key` | 服务器算好的视图，**原样存**：`today:<日期>`、`today:current`、`trends:<天数>`、`circles:overview`……；带 `version`（N2）和 `fetched_at` | 同步层写；还没有专门表的页面读 |
| `sync_cursors` | `stream` | N2 每个拉取流的游标 | 同步层 |
| `outbox` | `id` | 离线队列（第 5.4 节）；`client_id` 唯一；`group_key` 决定顺序；`state` 取 pending / sending / failed / conflict | `OfflineQueue` |
| `hk_sources` | `id` | HealthKit 来源表（bundle、设备、型号、系统版本） | 同步引擎（N8） |
| `hk_samples` | `uuid` | 数量型和分类型样本（分类的值是 HealthKit 的整数），注册表单位，带 `uploaded` 标记 | 同步引擎写；算法输入 |
| `hk_stat_buckets` | `type, start` | 累计型的 900 秒统计桶（按 UTC 对齐） | 同步引擎 |
| `hk_workouts` | `uuid` | 训练（统计、事件、元数据存 JSON） | 同步引擎 |
| `hk_deleted` | `uuid` | HealthKit 删掉的样本，等着上传 | 同步引擎 |
| `daily_metrics` | `date, metric` | 每天的汇总（长表，指标键和服务器一致），带来源和 flags | 算法 / 同步层 |
| `sleep_sessions` | `id` | 睡眠段，`date` = 醒来那天（结束时间减 4 小时），阶段存 JSON | 算法 / 同步层 |
| `daily_scores` | `date` | 每天的评分：圆环要用的数字是列，整行存在 `payload`；`algo_version`；`origin` 取 server / app；`uploaded` | 算法写（N1 / N3），同步层上传 |
| `records` | `kind, id` | 用户自己的记录（饮食 / 体重 / 喝水 / 日记 / 补剂 / 化验 / 训练）：`date`、`server_id`、`client_id`、`payload`、`version`、`deleted`、`pending`；还没收到服务器回答的行 `id` = `c:<client_id>` | N5 |
| `algo_bundles` | `version` | 已安装的算法包（sha256、api、文件路径、来源 builtin / download、`active`） | N1 |

**不存在本地的**：别人的数据（只存圈子动态的缓存文档）、AI 对话全文、心电图波形、AI 设置里的 key。

### 4.3 迁移规则

- 只在 `Schema.migrator` 末尾**追加** `vN_<名字>`（例如 `v2_records_training_sets`），一个 PR 一个。已经发出去的迁移不能改、不能改名、不能调顺序。`SchemaTests.testMigrationOrder` 把列表钉死，加迁移时同时改这个测试。
- Debug 版开了 `eraseDatabaseOnSchemaChange`：开发时改了迁移，本地库直接清空重建，反正只是缓存。Release 版没有这个开关。
- 本地库只是服务器的缓存，加上还没发出去的队列。要做破坏性改动（比如改主键），就新建表并从服务器重新拉，不在手机上搬数据。

## 5. 同步层

### 5.1 页面怎么用

```swift
// 读：订阅本地库，数据一变就重画
for try await doc in session.db.observeDoc(DocKey.todayCurrent) { … }
// 要新数据：拉取（N0：GET 原接口写进 docs；N2：增量）
await session.sync.refresh(.today(date: nil))      // 或 .changes
// 写：先改本地行，再排队（立刻尝试发送）
session.sync.enqueue(kind: "water", group: "water", method: .post, path: "/v1/users/me/water", body: json)
```

`SyncCoordinator.status`（idle / syncing / offline / failed）给页面显示一行小字（`SyncStatusLine`）。有缓存的时候永远先显示缓存，不出现阻塞式的加载遮罩。

### 5.2 拉取

- **N0（现在）**：`LegacyRemote` 调原有的 GET 接口，把结果原样存进 `docs`；`.changes` 目前只拉今天的文档。各页面在 N2 之前照这个做法：在 `LegacyRemote` 里加自己的 scope，把接口结果存进 `docs`。
- **N2**：`DeltaRemote` 调 `POST /v1/sync/pull`：

  ```json
  // 请求
  {"cursors": {"scores": "…", "daily": "…", "sleep": "…", "records": "…", "docs": "…"}, "limit": 500}
  // 回答
  {"changes": [{"stream": "records", "op": "upsert|delete", "kind": "water", "key": "123", "version": 7,
                "date": "2026-01-02", "body": {…}}],
   "cursors": {"records": "…"}, "more": false, "algo": {"version": "1.2.0", "api": 1}}
  ```

  - 每一页在一个事务里写进对应的表（`records`、`daily_scores`、`daily_metrics`、`sleep_sessions`、`docs`），然后保存游标。`more` 为 true 就接着拉。
  - 游标对 app 来说是不透明的字符串（服务器可以用 `updated_at` + id 实现）。
  - 第一次拉（游标为空）就是**全量恢复**：删了 app 重装、换手机都走这一条，按 stream 分页，先拉最近 14 天再往前补。
  - `HealthKit 原始样本` 也要能拉回来（#340「删掉 app 重装后从云端取回、在本地重算」），作为单独的 stream `raw`，按天分页，只在全量恢复或某天需要重算时拉。
- **什么时候拉**：冷启动、回到前台、自己的写操作提交之后、同步引擎上传完一轮之后（服务器回 `dirty_days`，只拉那几天）、静默推送（`content-available`）、下拉刷新。
- 服务器有没有 N2 接口，由 `/v1/web/me` 的 flag `native_sync` 告诉 app（名字由 N2 定）。没有就继续走 Legacy，所以 N2 上线前后 app 都能用。

### 5.3 推送（上传）

- **记录**：走离线队列。N2 之前用现有接口，并且带 `client_id`：喝水、体重已经支持；其他接口由 N2 加上。N2 之后可以合并成 `POST /v1/sync/push`：

  ```json
  {"mutations": [{"client_id": "…", "kind": "journal", "op": "upsert|delete", "key": "2026-01-02/water",
                  "base_version": 6, "body": {…}}]}
  // 回答：每条一个结果
  {"results": [{"client_id": "…", "status": "applied|duplicate|conflict|rejected", "key": "…", "version": 7, "body": {…}}]}
  ```

- **HealthKit 原始数据**：继续用 `POST /v1/ingest/healthkit`，协议见 `apple-sync-engine-app-contract.md`。改动是先写进本地 `hk_*` 表，再上传，200 之后打 `uploaded` 标记、提交锚点。
- **app 算的分数**：`PUT /v1/users/me/scores/app`（N2），格式 `{"algo_version": "…", "days": [{"date": "…", "row": {…}}]}`。服务器存下来作为这个用户的分数，并对他停掉每小时的服务器算分（#340 第 4 条）。如果服务器用同一个算法版本校验出不一致，会记下来，但不覆盖。

### 5.4 离线队列（`Sync/OfflineQueue.swift`，已实现并有测试）

- 每个 `group_key` 内部先进先出，同时只发一条；不同组互不阻塞。组的划分：`water`、`weight`、`journal:<日期>`、`training:<日期>`、`meal`……
- 结果分几种：
  - 2xx：删掉这一条。
  - 网络错误 / 5xx / 408 / 429：留在 `pending`，退避 2、4、8……秒，最长 5 分钟，加 ±20% 抖动；429 按 `Retry-After` 等。
  - 401：整个队列停下，不丢任何一条，重新登录后接着发。
  - 409：标成 `conflict`。
  - 其他 4xx：标成 `failed`。
- `failed` 和 `conflict` 会挡住同组后面的条目，保证顺序；由用户在「没提交」列表里选重试或丢弃（`needsAttention` / `retry` / `discard`，界面由 N8 做）。
- **冲突规则**：
  - 只追加的记录（喝水、饮食条目、体重、训练逐组）：靠 `client_id` 幂等，没有冲突。
  - 可以编辑的记录（日记的某一项、补剂打卡、个人资料）：以最后一次写为准（服务器时间），删除优先。
  - 整份覆盖的（当天训练、计划）：带 `base_version`，不一致就 409，让用户选。
  - 快捷记餐同一天记了两次：沿用现在的 409 + 「仍要记 / 丢弃」。
  - 计算结果（分数、趋势）：不在队列里。同一算法版本以服务器为准；算法包更新后在手机上重算，再上传。
- 已经提交的写操作，撤销要联网；只有本机还没发出去的可以直接撤销（删掉队列里那一条，再改回本地行）。

### 5.5 只在线的功能（不进队列，不进本地库，最多存一份缓存文档）

Jarvis / AI 对话、圈子的写操作（点赞、评论、邀请）、账号和 AI 设置、化验录入、建食谱 / 快捷记餐模板、看别人的数据。离线时这些入口显示为灰色，点了提示「需要联网」。

## 6. 算法包（JavaScriptCore）

### 6.1 调用约定（api 1，已在 `Algorithms/AlgorithmRuntime.swift` 定下）

**包的形状**

- 一个普通脚本（ES2020）：不用 module，没有 DOM、计时器、网络，结果不能依赖 `Date.now()`。
- 脚本里定义：

  ```js
  globalThis.OBS = {
    apiVersion: 1,
    version: "x.y.z",
    functions: ["…"],
    call(name, inputJson) { … }   // 返回 JSON 字符串
  }
  ```

**`call` 的规则**

- `call` 是同步的纯函数：同样的输入，在每台手机和服务器上都得到同样的输出（golden 测试）。
- 出错返回 `{"error": {"code": "…", "message": "…"}}`，不抛异常（抛了 runtime 也会接住）。
- 输入输出沿用服务器的约定：日期是用户时区的 `YYYY-MM-DD`，时间是 UTC 秒，单位 kg / kcal / ms / bpm，缺的值写 `null`。

**Swift 一侧**

- `try runtime.call("scores.day", input, as: Output.self)`；也可以用 `callJSON`。
- 每个包版本一个 `JSContext`，所有调用在它自己的串行队列上执行。
- 函数清单和输入输出的结构由 N1 定，命名建议 `<模块>.<函数>`（`scores.day`、`trends.series`、`rules.evaluate`）。

### 6.2 下发和启用（N1 实现）

1. **内置包**：`algo-builtin.js` 打进 app，第一次启动和校验失败时用。N0 放的是占位文件，只有 `echo` 和 `about`。
2. **检查新版本**：冷启动时（至少隔 6 小时一次）请求 `GET /v1/algo/manifest`，返回 `{version, api, sha256, signature, min_app}`。如果版本比本机新、`api` 和 app 支持的一致、app 版本 ≥ `min_app`，就下载 `GET /v1/algo/bundle/<version>.js`。
3. **校验**：`BundleVerifier` 先比 sha256，再验 Ed25519 签名（CryptoKit `Curve25519.Signing`；公钥编译进 app，私钥只在维护者手里，不在服务器上）。两项都通过，才写进 `Application Support/W2W/algo/<version>.js` 和表 `algo_bundles`，然后标成 `active`。
4. **重算和回滚**：切换版本后，在后台重算最近 N 天（N 由 N1 定），写进 `daily_scores` 时带上 `algo_version`，再上传。新包加载失败或调用报错，就退回上一个 `active` 版本，并上报。

## 7. 和服务器的接口

`{user}` 一律写 `me`。看别人的数据时（共享或圈子）换成对方的 id。

### 7.1 现有接口，直接用

| 页面 | 接口 |
|---|---|
| 账号 / 启动（N0、N8） | `GET /v1/web/me`（id、名字、flags、用户的今天）、`GET /v1/auth/methods`、`POST /v1/auth/email/start`、`/verify`、`POST /v1/auth/apple`、`GET /v1/account`、`POST /v1/account/logout`、`DELETE /v1/account`、`POST /v1/account/redeem-invite`、`GET /v1/consent`、`POST /v1/users/me/consent`、`GET/PATCH /v1/users/me/profile`、`PUT /v1/users/me/intro` |
| 今天 / 睡眠 / 讲解（N3） | `GET /v1/users/me/today`（N0 已接）、`/sleep/nights`、`/sleep/night`、`/sleep/details`、`/days?month=`、`/health-monitor`、`/strain-coach`、`/sleep-coach`、`/stress`、`/healthspan`、`/hr-zones`、`/vo2max`、`/reminders`（`PATCH /reminders/{id}`）、`/explain/metric/{key}`、`/explain/status/{key}`、`/explain/lab/{key}`、`GET /v1/explain/glossary`、`/goals/sleep` |
| 趋势 / 报告（N4） | `GET /v1/users/me/trends?days=&end=`、`/reports`、`/journal/analysis`、`/cardio`、`/labs/{key}/history`、`/weight/recent` |
| 记录（N5） | 喝水 `GET/POST /water`、`DELETE /water/{id}`；体重 `POST /weight`（`client_id`）、`/weight/recent`；日记 `GET /journal`、`/journal/items`、`/journal/frequent`、`PUT/DELETE /journal/{day}/{key}`；饮食 `GET /meal-templates`、`POST /meal-templates/{id}/log`、`DELETE /meal-logs/{id}`、`/nutrition/combine`、`DELETE /nutrition/entries/{id}`、`/recipes…`、`/diet-profile`、`/goals/nutrition`、`/grocery/…`；训练 `/training/calendar`、`/training/day/{day}`、`/training/week`、`/training/plan…`、`/training/session…`、`/training/muscle-load…`、`/training/exercises…`、`/training/custom-exercises…`、`/training/goals`；补剂 `/supplements…`；化验 `/labs…`；事件 `/events…`；动作限制 `/exercises…` |
| Jarvis（N6） | `POST /v1/assistant/chat`（SSE）、`/chat/confirm`、`GET /chat/{id}/events`、`/conversations…`、`POST /v1/assistant/transcribe`、`/voice-fix`、`GET /v1/users/me/ai-cards`、`/ai-choice`、`/ai-fallback…` |
| 圈子（N7） | `GET /v1/friends`、`/overview`、`/leaderboard`、`/referral`、`/invite`、`POST /v1/friends/join`、`/circles…`、`/nudges`、`/remarks…`、`/v1/friends/feed…`（动态、点赞、评论、未读）、`/v1/avatars…` |
| 我 / 设置 / 同步中心（N8） | `/memory…`、`/ai-providers…`、`/ai-tokens…`、`/ai-connections…`、`/notification-settings`、`/notifications…`、`POST/DELETE /push-devices`、`GET /v1/whats-new`、`/notices…`、`/feedback…`、`/data-status`、`/history-import…`、`/appearance`、`/flags`；同步：`GET/POST /v1/ingest/healthkit`、`GET /v1/ingest/sync-status`、`/v1/ingest/resync`、`PUT /v1/ingest/sync-device`、`/sync/engine/status`、`/sync/overview`、`/sync/history…`、`/health-history`、`POST /v1/web/ingest-token` |

### 7.2 N2 要新加的接口

| 接口 | 作用 |
|---|---|
| `POST /v1/sync/pull` | 增量拉取（第 5.2 节）：stream 有 `scores`、`daily`、`sleep`、`records`、`docs`、`raw`；游标、分页、tombstone；第一次拉即全量恢复 |
| `POST /v1/sync/push` | 批量写记录（第 5.3 节）：按 `client_id` 幂等，带 `base_version` 的冲突返回 `conflict` |
| 各写接口补上 `client_id` | N2 完成 `sync/push` 之前的过渡：日记 `PUT /journal/{day}/{key}`、补剂打卡、快捷记餐 `/meal-templates/{id}/log`、食谱 `/recipes/{id}/log`、训练逐组 / 提交（服务器记下 client_id → 结果，重发时返回同一个结果） |
| `PUT /v1/users/me/scores/app` | 接收 app 算好的分数（带 `algo_version`），并对这个用户停掉每小时的服务器算分；同时增加一个管理命令，用来恢复服务器算分 |
| 服务器端的「文档版本」 | 现在只有动作库的 `table_versions`，是全局的，不分用户。N2 要加按用户的 `doc_versions(user_id, doc_key, version)` 或者 `updated_at` 游标，并把 `touch` 接到导入、算分、记录类接口和 AI 写工具上（`ios-native-plan.md` 第 4.2 节） |
| `dirty_days` | `POST /v1/ingest/healthkit` 的回答里加上受影响的日期，app 只拉那几天 |
| flag `native_sync` | 告诉 app 服务器已经有 N2 的接口 |
| `GET /v1/algo/manifest`、`GET /v1/algo/bundle/{version}.js` | 下发算法包（这两个属于 N1，和 N2 一起部署） |
| 静默推送 | 数据有变化时发 `content-available`，现有推送服务加一个类型 |

## 8. 从 Flutter 版升级：凭据迁移（`Auth/FlutterMigration.swift`，已实现并有测试）

同一个 Bundle ID 和 Team ID，所以是同一个 Keychain 访问组、同一个 `UserDefaults`。迁移只在第一次启动跑一次，标记是 `w2w.native.migrated.v1`。

| Flutter 版存的 | 存在哪里 | 原生版怎么处理 |
|---|---|---|
| 账号 token | Keychain，通用密码项：service `flutter_secure_storage_service`、account `w2w.account.token.v1`，值是 UTF-8（flutter_secure_storage 11 / darwin 0.4.3） | 复制到 service `org.watch2whoop.native` / account `account.token`，访问级别「首次解锁后、仅本机」 |
| 只能上传的 token | 同上，account `w2w.ingest.token.v1` | 复制到 `ingest.token` |
| 服务器地址等 | `UserDefaults` 的 `flutter.w2w.config.v1`（JSON：`todayUrl`、`owHost`、`onboardingDone`…） | `todayUrl` 只取 origin，存进 `w2w.native.account.v1` |
| 界面语言 | `flutter.w2w.lang.v1` | 写进 `w2w.native.lang.v1` |
| 同步锚点、backfill 状态和 token | `w2w.sync.anchor.*`、`w2w.raw.anchor.*`、`w2w.backfill.state.v1`、Keychain `w2w.backfill` | 不用动：拷进来的代码读的就是同一批键 |
| 原生今天页缓存、同步日志 | `flutter.w2w.nativeToday.v1`、`flutter.w2w.syncLog.v1`… | 不迁，第一次拉取后自然就有了 |

- **Flutter 项不删**：TestFlight 还能退回 1.0.8。原生版退出登录时，Flutter 项会一起删掉，避免退回 1.0.8 后又出现一个已经吊销的会话。迁移代码保留两个版本（到 1.1.x），之后删掉。
- **只有 token 没有地址，或者只有地址没有 token**：显示登录页，不删旧数据。
- **测试**：单元测试（`FlutterMigrationTests`）用内存 Keychain 和独立的 UserDefaults 模拟 Flutter 版存的东西。**真机覆盖升级要在 N9 测**：先装 1.0.8 的 TestFlight 包并登录，再装 1.0.9，确认不用重新登录、同步锚点能接上。

## 9. 页面清单（N3–N8）

底栏和今天页的规则一致：`今天 · 趋势 · 饮食 · 记录 · 我`；flag `friends` 打开时，`朋友`（圈子）取代 `饮食`，饮食并进 `记录`（`app.js` 的 `applyTabs()`）。圈子永远是一级入口。每个 tab 在 `MainTabView.screen(_:)` 里替换自己的占位页，这是唯一一个多个 PR 都会改的地方，冲突时各保留各的那一行。

| 块 | 页面 | 主要数据 |
|---|---|---|
| **N3 今天** | 三环（睡眠分 / 恢复 / 昨天负荷）+ 恢复词 + 负荷状态 + 核心指标（HRV、静息心率、睡眠、需要、欠债、步数、能量、锻炼分钟）+ 健康监测 + 负荷目标 + 状态卡 + 提醒 + 训练卡 + 饮食卡；顶部日期 → 月历回看；睡眠详情（阶段图用 Swift Charts）；每个指标的讲解弹层 | `docs today:*`，之后改读 `daily_scores` / `daily_metrics` / `sleep_sessions`；讲解来自 `explain/*`，词条放静态包 |
| **N4 趋势** | 全部指标的序列（7–365 天、和上期比较）、状态卡（HRV 状态、训练状态、韧性……）、周报 / 月报 | `docs trends:*` → 本地算法 `trends.series` |
| **N5 记录** | 日记、喝水、体重（带测量情况标签）、饮食（今天吃了 / 快捷记餐 / 食谱 / 合并）、训练（计划 / 当天逐组 / 肌肉负荷）、补剂、化验 | `records` + 离线队列；饮食 tab 也归 N5 |
| **N6 Jarvis** | 原生按键和菜单（拿 1.0.9 的 `NativeJarvis.swift`、`VoiceInput.swift` 来用）+ 对话（URLSession 字节流读 SSE，Markdown 用 `AttributedString`）+ 语音 / 拍照 / 快记 + 卡片 + 确认 | 只在线；会话列表可以缓存成文档 |
| **N7 圈子** | 动态、点赞评论、排行、邀请、圈子管理、头像 | 只在线；`docs circles:*` 当缓存 |
| **N8 我 / 设置 / 引导 / 同步中心** | 登录（设置二维码 / 链接、邮箱、Apple）、同意书、资料引导、同步中心（权限、补全历史、重新同步、状态面板）、设置、AI 设置、通知、更新记录、数据状态、「没提交」列表、退出登录；把同步引擎的 Dart 部分改写成 Swift，删掉 FlutterShim | `/v1/ingest/*`、`/sync/*`、`hk_*` 表 |

**新页面的做法**

1. 在 `Features/<页面>/` 下写 View 和一个 `@MainActor ObservableObject` 的 ViewModel。
2. 读：订阅本地库（`ValueObservation` / `observeDoc`）。
3. 刷新：调 `session.sync.refresh`。
4. 写：先改本地行，再 `enqueue`。
5. 界面用 `DesignSystem` 里的组件，文字写 `tr("中文", "English")`，图标用 `Icon(.xxx)`。新图标先加进 `lucide_assets.py`，再加进 `Icon.Name` 和 `THIRD_PARTY_NOTICES.md`。
6. 需要新表就在 `Schema` 末尾追加迁移。
7. 测试：解析服务器 JSON 的模型、本地读写、队列行为，至少各有一个单元测试。用虚构的数据。

## 10. 测试和发布

**本地**

- 先 `df -h /`，少于 15G 不要构建。
- 生成工程：`cd apps/ios && xcodegen generate`。
- 测试：`DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project Watch2Whoop.xcodeproj -scheme Watch2Whoop -destination 'platform=iOS Simulator,name=iPhone 17' test`。
- 不用 Xcode 的检查：`python3 apps/ios/scripts/check_sources.py`、`lucide_assets.py --check`、`sync_flutter_sources.sh --check`。

**CI**（`.github/workflows/ci.yml` 的「iOS native」job）

- 只在 `apps/ios/**` 有改动时跑（push 到 main、改了 workflow 时全跑）。
- macOS runner：选最新的 Xcode → 装 XcodeGen 2.46.0 → 源码检查 → `xcodegen generate` → 在模拟器上 `xcodebuild build` 和单元测试（`CODE_SIGNING_ALLOWED=NO`）。
- 不是 `main` 的必需检查（和 `iOS Podfile.lock` 一样），稳定之后再由负责人决定加不加。

**模拟器演示**

- 起一个一次性的 Postgres。
- 用 `quickstart/demo_data.py` 生成合成数据，再用 `body-engine import-apple-zip` 导入。
- 跑 `uvicorn body_engine.api:app`。
- 用 Debug 环境变量登录（第 3 节）。不碰任何真实的库。

N0 的今天 tab（iPhone 17 模拟器，合成数据，浅色 / 深色）：

<img src="images/ios-native/n0-today-light.png" width="240" alt="N0 今天 tab，浅色"> <img src="images/ios-native/n0-today-dark.png" width="240" alt="N0 今天 tab，深色">

**发布**（`apps.md` 第 4 节）

- 版本号 1.0.9，build 号每上传一次加一（`W2W_BUILD_NUMBER`）。
- 签名值写在本地的 `Watch2Whoop.local.xcconfig`，和 Flutter 版一样。
- Xcode → Archive → 上传到 App Store Connect 的**同一条 app 记录**。
- 先给 TestFlight 内部组用一周，再给外部测试用户。
- 发布当天在新提交上打 `app-ios-v1.0.9`，删掉旧的 1.0.9 tag 和 Release（#340）。

**回退**：TestFlight 装回 1.0.8。Flutter 项还在，不用重新登录。

## 11. 还没定 / 风险

1. **OW SDK**：1.0.9 不带 Open Wearables SDK。所以发布前，所有用户都要切到同步引擎的 `app` 模式（#219，`apple-sync-engine-app-contract.md` 第 7 节）；否则 N8 要通过 SPM / 二进制把 OW iOS SDK 加回来。**请负责人定**。
2. **iOS 16 还是 17**（C1）。要看测试用户的系统版本；决定之后只改一行配置。
3. **Swift 6 严格并发**（C9）：N8 改写完同步代码之后再评估。
4. **算法包签名的私钥**放在哪里、谁来签（N1）。建议放在维护者本机或 CI secret，不放在服务器上。
5. **全量恢复的流量**：原始样本按天分页，只在 Wi-Fi 下补 90 天以前的数据。具体限额由 N2 定。

---

## English summary

`apps/ios/` is the new pure-SwiftUI iOS app (#340), replacing the Flutter app. The Flutter app stays at 1.0.8 and gets bug fixes only. The native app keeps the same bundle ID and team, so TestFlight upgrades it in place.

**Decisions**
- iOS 16+, using ObservableObject (not Observation).
- XcodeGen 2.46.0 generates the project; the `.xcodeproj` is not committed.
- SPM dependencies: GRDB 7.11.1 only.
- Bilingual UI: every user-facing string is written `tr("中文", "English")`.
- Colour tokens mirror `app.css`, and CI compares the two.
- Icons are Lucide only, generated by a script.

**Modules**
- App: entry point, account session, tab bar (Friends replaces Food while the `friends` flag is on).
- DesignSystem.
- Networking: Bearer token, 401 means sign in again, retries with backoff for idempotent requests.
- Auth: Keychain, plus migration from the Flutter app.
- Storage: one GRDB database per account, with append-only migrations.
- Sync: coordinator, legacy and delta remotes, and an offline queue that is FIFO per group, idempotent by `client_id`, and keeps failed or conflicting writes for the user to resolve.
- Algorithms: JavaScriptCore runtime with calling convention api 1, `OBS.call(name, json) -> json`. Downloaded bundles are checked by sha256 and an Ed25519 signature before use.
- HealthSync: byte-for-byte copies of the Flutter app's SyncEngine, RawSync and Backfill, compiled against a tiny `Flutter` shim package.
- Features: one folder per page.

**Local database**
- Tables: `meta`, `docs` (server views as they came), `sync_cursors`, `outbox`, raw HealthKit tables (`hk_*`), `daily_metrics`, `sleep_sessions`, `daily_scores` (with `algo_version` and `origin`), `records` (every user-record kind), `algo_bundles`.
- Excluded from backups, readable after first unlock, deleted on sign-out.

**Sync**
- Pages read only the local database, call `sync.refresh` for fresh data, and write locally first, then enqueue.
- N0 pulls through the existing endpoints.
- N2 adds `POST /v1/sync/pull` (streams, cursors, full restore on an empty cursor), `POST /v1/sync/push`, `client_id` on the remaining write endpoints, `PUT /v1/users/me/scores/app` (which also stops server-side hourly scoring for that user), per-user document versions, `dirty_days`, and a `native_sync` flag.
- N1 adds `GET /v1/algo/manifest` and `GET /v1/algo/bundle/{version}.js`.

**Migration from the Flutter app**
- Copies the account and ingest tokens from the `flutter_secure_storage_service` Keychain items.
- Takes the server origin from `flutter.w2w.config.v1` and the language from `flutter.w2w.lang.v1`.
- Sync anchors are shared as they are.
- Flutter items are kept so a rollback to 1.0.8 still works; native sign-out removes them.

**Pages**
- N3: Today, sleep and explain sheets.
- N4: Trends and reports.
- N5: all records and the Food tab.
- N6: Jarvis.
- N7: Circles.
- N8: Me, settings, onboarding, the sync center, and porting the sync engine's Dart side to Swift.

**Testing and CI**
- XCTest unit tests run on the simulator in the CI job "iOS native", which runs only when `apps/ios/**` changes.
- Simulator demos use a throwaway database with synthetic data.

**Open questions**
- The OW SDK is not included, so every user must be in engine `app` mode before 1.0.9.
- iOS 16 or 17.
- Swift 6 strict concurrency.
- Where the bundle signing key lives.
- Full-restore traffic.
