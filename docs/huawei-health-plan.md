# 华为手表接入方案：华为运动健康服务（Health Service Kit）调研与设计

> 对应 issue #253 的第 1 期（调研 + 设计）。只描述方案，不含代码改动；资质申请（第 2 期）由项目负责人本人办理，本文第 3 节是办理步骤。
> 资料只用华为官方开发者文档、华为官方支持页面、App Store 页面和开源项目文档，每处引用都附链接（文档页面的「最后更新」日期在 2026-01 至 2026-09 之间，华为改动文档很频繁，动手前请重读对应页面）。
> 标「待查」的地方，华为文档没写或我们没法在不开通权限的情况下确认，第 1 期不能拿它们当结论，第 3 期动手前要先确认（第 6 节「阶段 0」把它们列成了验证项）。
> 文中没有任何真人的数据。
> 华为把「Health Kit」改名为「Health Service Kit」（运动健康服务），本文两个名字混用，指同一个服务。

## 0. 摘要

- **给华为手机家人的客户端（第 7 节，优先级最高）**：手表数据走服务器云侧，**客户端不需要读任何健康权限**，所以第一版壳可以不碰手表数据，不被资质卡住。分两种手机：**HarmonyOS 4.x 及以前**能装 APK（华为开发者社区文章；官方未直接表述，待确认）→ 给 Flutter 工程加 `android/` 目标出 APK 侧载，**不需要任何华为账号**，约 1–1.5 周开发；**HarmonyOS NEXT（5.0 起）**不能装 APK → 单独做 ArkTS 壳（Flutter for OpenHarmony 是社区分支，没有成熟度声明且版本落后，不建议），需要华为开发者账号（个人实名 1–2 个工作日），约 2 周开发；分发先用调试包 + `hdc`（≤ 100 台设备，个人账号可用）当面装，远程用邀请测试（≤ 10000 人，**官方 FAQ：不需要备案和资质**，首个版本要审核）。正式上架要 APP 备案和隐私材料，周期按月，不是最快路径。预计家人可用：A 线约 1.5 周，N 线当面约 3 周、远程约 4–5 周（估算）。
- **推荐路线：云侧 REST API（服务器直连）**，不做端侧 SDK。我们只有 iOS app 和服务器；云侧接口不要求手机系统（华为运动健康 App 的 iOS 版本身就有「HUAWEI Health Kit」授权入口），也不要求我们的 app 上架华为应用市场。端侧 SDK 只支持 Android 和 HarmonyOS，我们没有这两个客户端。
- **关键门槛不是技术，是资质**：评分需要的心率、HRV、睡眠、血氧、体温、最大摄氧量，华为都归在「高阶」数据；个人开发者拿不到高阶数据，企业（含事业单位 / 社会团体）要求**实缴注册资本 ≥ 500 万元、成立满 1 年**（中国站文档）。实缴只有 50 万元的主体只能申请「基础」数据：训练记录（含训练中心率）、步数、热量、距离，**没有睡眠、静息心率、HRV，也就算不出恢复分和睡眠分**（第 2.3 节）。所以负责人先确认主体的实缴注册资本，再决定要不要开始申请。
- **能拿到的**：夜间 HRV 是 **RMSSD**（整数毫秒，范围 (0, 200]，仅「部分手表」，不是逐拍时间）；睡眠有清醒 / 浅睡 / REM / 深睡四个阶段和华为自己的睡眠分；训练有 100 多种类型和训练中心率明细。**拿不到的**：呼吸频率只有按夜的统计值（均值 / 最大 / 最小，没有逐条样本）；HRV 没有逐拍间期，我们现有的「由逐拍时间算 RMSSD」那条路对华为用不了；采样间隔华为文档没写（待查）；绝大多数数据的**时效是「小时」级**，不是实时（第 2 节）。
- **和现有架构的关系**：华为作为一个独立的「外部厂商连接」，走自己的原始样本表和派生步骤，写进现有的 `daily_metric_sources` / `sleep_sessions` / `workouts`（来源 `huawei`），评分代码基本不改，只新增一个 HRV 口径 `rmssd_huawei`（第 4、5 节）。它不依赖 Open Wearables（OW 没有华为 provider），和 #219（自建 Apple 同步引擎）的原始样本表 + 脏天队列思路一致、可以共用派生队列；只部署在内地区域（#249）。
- **工作量**：代码约 28–34 人日（分 6 个 PR），另有一个跨设备验证实验（第 5 节，接 #118）。资质审批如果一次通过，从申请到拿到正式权限约 **6 周**（3 + 10 + 15 个工作日，加上 24 小时生效），被驳回一次每次再加约 2–3 周（第 3、6 节）。
- 华为手机上的客户端（给家人用）见第 7 节；需要负责人决定的点和推荐见第 8 节。

---

## 1. 接入方式

### 1.1 华为提供的三条路

华为官方说明的三种开放方式（[应用侧开放服务](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/application_description-0000001418052318)）：

| 路线 | 能做什么 | 平台 | 对我们 |
|---|---|---|---|
| 端侧 Android Java SDK | 在 Android app 里读写用户健康数据，扩展能力还能拿实时心率 / 实时训练 | Android（要集成 HMS Core） | 我们没有 Android app，不选 |
| 端侧 HarmonyOS JS SDK | 同上，在 HarmonyOS app 里 | HarmonyOS | 我们没有 HarmonyOS app，不选（`apps/harmony` 只是占位） |
| **云侧 REST API** | 服务器用 OAuth 2.0 访问用户在华为云上的健康数据，读写 + 订阅推送，Web 和移动 app 都能用 | 与手机系统无关 | **选这条** |

依据：官方「使用场景」一节明确说，要同时支持 Android 和 iOS、小程序或 Web 的，推荐用 REST API，体验一致、开发效率高（同一页）。云侧开放文档的数据类型表也写明「REST API 支持跨平台开发」（[开放数据总览](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/data_description-0000001467889369)）。

两个事实让云侧在我们这里可行：

1. **数据从华为运动健康 App 到华为云，与手机系统无关**。官方授权说明里给了 iPhone 用户的开关位置：「打开华为运动健康 App → 我的 → 第三方服务 → HUAWEI Health Kit → 关联」，Android 用户则是「我的 → 隐私管理 → HUAWEI Health Kit」（[在华为运动健康中开放授权](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/server-app-auth-example-0000001071757151)）。也就是说「华为手表 + iPhone」的用户，数据照样进华为云，云侧接口照样能读。
2. **注册产品时有「服务端应用（Server app）」类型**：申请 HUAWEI ID 服务时，产品类型可选「移动应用」（要填 APK 包名和签名指纹）或「服务端应用」（服务端对接，Web 应用需要回调地址和服务器出口 IP）（[申请 HUAWEI ID 服务](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/apply-id-0000001050069756)）。我们的授权由服务器完成，应注册为服务端应用。**待查**：企业开发者用「服务端应用」注册、而 app 只上架 App Store（不上架华为应用市场）时，运动健康服务的审核是否放行；个人开发者的明确要求是「应用要上架华为应用市场且是移动应用」（[资质要求](https://developer.huawei.com/consumer/cn/doc/HMSCore-Guides/application-qualification-description-0000001425636570)），企业没有这句，但没有明说可以。阶段 0 要先向华为提工单确认（工单入口在 [申请页](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/apply-kitservice-0000001050071707) 末尾）。

### 1.2 不选的路

- **端侧 SDK**：只在以后决定做 Android / HarmonyOS 客户端时才有意义；它的好处是实时心率和实时训练（官方：扩展能力「实时数据」，见[开放数据总览](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/data_description-0000001467889369)），但评分用不到实时数据。
- **第三方聚合服务**（例如 [Terra 的华为接入](https://tryterra.co/integrations/huawei)、[Thryve](https://www.thryve.health/huawei-health-kit-api-integration)）：它们也是在华为云侧接口上封装，等于把用户健康数据再转交一个商业公司，和项目「数据留在自己的服务器」的原则冲突，不采用。
- **Open Wearables**：OW 没有华为 provider（OW 仓库的 provider 目录里有 Apple、Garmin、Fitbit、Polar、Suunto、Oura、Whoop、Withings 等，没有华为，见[OW 仓库](https://github.com/the-momentum/open-wearables/tree/main/backend/app/services/providers)；OW 的 Apple 健康文档只是把「Huawei Watch → Huawei Health」列为可以经 Apple 健康汇入的手表，[文档](https://github.com/the-momentum/open-wearables/blob/main/docs/providers/apple-health.mdx)）。所以不走 OW，这也符合 #219 把 OW 下线的方向。

### 1.3 华为手表配 iPhone：经华为运动健康 iOS 版往 Apple 健康同步什么

我们已有的 Apple 同步（OW 或 #219 的新引擎）能读到「写进 Apple 健康」的数据。华为运动健康 iOS 版往 Apple 健康写什么，**华为没有官方的完整清单**，能查到的只有这些：

| 类型 | 证据 | 状态 |
|---|---|---|
| 体重（写入） | App Store 说明：「在华为运动健康记录的体重数据会同步到 HealthKit」 | 官方声明 |
| 运动数据（双向） | App Store 说明：运动数据自动同步到 HealthKit，也可读取 HealthKit 的运动数据 | 官方声明 |
| 步数（写入） | 华为官方支持页以步数为例，说明在「健康」App 里允许「华为运动健康」写入数据 | 官方页面当例子提到，未列完整 |
| 心率、HRV、静息心率、睡眠分期、血氧、呼吸、体温、VO₂max | **没有任何官方声明** | **待查**；有用户评论说睡眠细节只留在华为 App 里，只有就寝时间和总时长到「健康」，不能当依据 |

来源：[华为运动健康 App Store 页面](https://apps.apple.com/cn/app/%E5%8D%8E%E4%B8%BA%E8%BF%90%E5%8A%A8%E5%81%A5%E5%BA%B7/id1325481372)、[华为官方支持页（同步步数到第三方）](https://consumer.huawei.com/cn/support/content/zh-cn01057420/)。后者还写明：同步到「健康」不是实时的（建议下拉同步后等 10–30 分钟），第三方里的步数可能与华为 App 不一致。

**结论**：经 Apple 健康这条路，最多拿到训练记录、步数、体重，缺心率明细、HRV、静息心率、睡眠分期、血氧、体温，也就是恢复分和睡眠分的全部输入。它可以做兜底，不能当主路线。阶段 0 在一台华为手表 + iPhone 上看「健康」里「华为运动健康」到底写了哪些类型（`HKSource` 的 bundle id 和样本类型列表，`apple-sync-engine.md` 第 3.1.7 节的来源信息已经会记录）。

**两条路同时开的重复问题**：同一个用户既连了华为云，iPhone 上的华为运动健康又往 Apple 健康写训练和步数，我们的 Apple 同步会把同一次训练读两遍。处理见第 4.3 节。

---

## 2. 数据：每类能不能拿到、多频繁、对应哪个评分输入

### 2.1 先讲两个通用前提

**数据开放等级。** 云侧数据分「基础」和「高阶」两档，总览表里用锁图标区分：我们核对了表里的图标，**活动类（步数、热量、距离、海拔、中高强度运动、活跃小时、日活动汇总）、身高、运动记录（摘要、训练中明细、位置）、历史数据**是基础（开锁），**体重、心率（含静息心率、HRV）、压力、睡眠、血糖、血压、血氧、体温、心脏健康（ECG）、肺功能（含 VO₂max、睡眠呼吸）、生殖健康**是高阶（闭锁）。「高阶数据暂不向个人开发者开放」（[开放数据总览](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/data_description-0000001467889369)，[资质要求](https://developer.huawei.com/consumer/cn/doc/HMSCore-Guides/application-qualification-description-0000001425636570)）。注意总览表里的锁是图片，文字抓取读不出来，我们是逐个比对图片得出的，申请前请在页面上再看一遍。

**数据时效。** 文档里每类数据都有「查询时效」：多数是「按小时」，睡眠记录和训练记录是「按分钟」，没有一项是秒级（秒级只有端侧的实时心率）。官方还写明：手表没连着手机蓝牙、手机没网、华为运动健康没设成受保护后台应用时，时效没有保证；用户可以打开华为运动健康手动同步。官方建议后台每天凌晨查一次或每 4 小时查一次、用户打开 app 时主动查一次、给用户一个「手动同步」按钮三者结合，需要及时性的用订阅通知（[数据查询时效](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/dataquery-timeliness-desc-0000001058864328)）。对我们的影响：今天页的恢复分要等睡眠记录进云（按分钟，早晨醒来后通常能很快拿到，但华为文档不保证），白天的心率、压力会晚到几小时，这和 Apple 路线的「几分钟」有差距。

**采样间隔。** 开发者文档没有写任何一类数据的采样间隔，只写了查询时效。第 3 期动手前，用测试权限在真实手表上统计每晚每类样本的条数和间隔（阶段 0 / 1 的验证项，工具见第 6 节）。下表的「频率」一栏因此多是「待查」。

### 2.2 逐类对照

「口径」一栏来自各数据类型页（链接在表后）。「评分输入」对应 `scores/inputs.py` 的字段和 [评分方法论](scoring-methodology.md)。

| 数据 | 华为类型 / 口径 | 等级 | 时效 | 采样频率 | 我们的评分输入 | 缺了会怎样 |
|---|---|---|---|---|---|---|
| 心率（瞬时） | `com.huawei.instantaneous.heart_rate`，bpm，手表 / 手环 | 高阶 | 小时 | 待查 | `hr`：夜间最低 5 分钟均值 → 恢复分的静息心率；全天心率 → 压力、strain | 没有 → 恢复分缺静息心率项（权重 0.20，其余项重新归一，仍可算）；压力算不出 |
| 静息心率 | `resting_heart_rate`，按天，bpm | 高阶 | 小时 | 每天 1 条 | `daily_rhr`：没有睡眠期心率时的兜底，和自己的基线比 | 有夜间心率则不影响 |
| **HRV** | `com.huawei.heart_rate_variability`，字段 **`heartRateVariabilityRMSSD`**，整数毫秒，(0, 200]，**仅「部分手表」**；睡眠记录里关联了睡眠期的 HRV 样本 | 高阶 | 小时 | 待查（每晚几条？） | `hrv`：**恢复分里权重最大的一项（0.60），而且必须有**；见第 5 节口径 | **没有 HRV = 没有恢复分**（现有规则：「HRV 必须有」） |
| 睡眠分期 | `com.huawei.continuous.sleep.fragment`：1 浅睡、2 REM、3 深睡、4 清醒、5 小睡，片段不重叠；手动记录的睡眠拿不到 | 高阶 | 分钟（睡眠记录） | 片段级 | `nights` / `segments` → `sleep_sessions`（light→core） | 没有 → 睡眠分、睡眠需求、规律性全缺，恢复分缺睡眠表现项 |
| 睡眠记录（汇总） | `com.huawei.health.record.sleep`：入睡 / 醒来时间、总时长、浅 / 深 / REM 时长、夜间清醒时长和次数、上床 / 离床时间、华为睡眠分、类型（TruSleep / 普通 / 小睡）；只有时长超过 3 小时的才算一条完整睡眠记录；可关联睡眠期心率、血氧、HRV 样本和呼吸频率统计 | 高阶 | 分钟 | 每天 | 睡眠分的四项（时长、阶段、规律性、中断）都能算；华为自己的睡眠分只存档不进我们的分数 | — |
| 血氧 | `com.huawei.instantaneous.spo2`，%，手表 / 手环（部分）；有按天统计 | 高阶 | 明细小时、「最新」分钟 | 待查 | `spo2`：只做低血氧标记（< 94%），不进分数 | 缺标记，分数不受影响 |
| **呼吸频率** | **没有逐条明细（官方：「明细数据暂未开放」）**；只有统计 `…breathe_rate.statistics`（均值 / 最大 / 最小 + 基线），**只能经睡眠记录取**，次 / 分，仅「部分手表」，归在「肺功能」权限里 | 高阶 | 小时 | 每晚 1 条统计 | `resp`：恢复分的呼吸项（权重 0.05）。现在取窗口内读数的中位数，华为只给均值 → 用均值代替，标 `resp_stat=mean` | 缺 → 呼吸项为空，其余项重新归一，仍可算 |
| 体温 | 体表温度 `…skin.temperature`（部分手表）、体温 `…body.temperature`（体温计等生态设备）；34–42 °C | 高阶 | 小时 | 待查 | `temp`：和自己 60 天中位数比，偏离 ≥ 0.6 °C 做标记，不进分数 | 缺标记 |
| VO₂max | `com.huawei.vo2max`，整数 ml/kg/min，**仅「部分专业手表 / 手环」**；「最新值」接口只给最近 12 个月 | 高阶 | 分钟 | 随训练更新 | 身体年龄的心肺项（`daily_metrics.vo2max`） | 缺 → 身体年龄少一个域，其余照算 |
| 训练记录（摘要） | `activityrecord`：100 多种类型（跑步、骑行、游泳、篮球、力量训练、HIIT 等）；摘要含运动时长、距离、热量、训练心率统计 | **基础** | 分钟 | 每次训练 | `workouts`（类型、时长、热量） | — |
| 训练中心率明细 | `com.huawei.instantaneous.exercise_heart_rate`（需 `activity` 权限）；还有训练后恢复心率 `com.huawei.recovery_heart_rate` | **基础** | 分钟 | 待查 | `hr` 里训练时段的心率 → TRIMP、五区分钟、训练后心率恢复（`workout_hrr`） | 缺 → 这次训练的 strain 退回按时长估算（现有降级逻辑） |
| 步数 / 距离 / 热量 / 中高强度 / 活跃小时 | `…steps.delta`、`…distance.delta`、`…calories.burnt`、静息热量 `resting_calories` 等；日汇总 | **基础** | 小时 | 按小时 | `step_count`、`active_energy`、`basal_energy` | 缺 → 身体年龄的步数域、训练日历的热量 |
| 体重 / 体脂 | 华为和兼容体脂秤；**高阶** | 高阶 | 分钟 | — | 我们的体重来自 SparkyFitness，不申请 | — |
| 压力（华为算的） | `com.huawei.instantaneous.stress` | 高阶 | 小时 | — | 我们有自己的压力算法（v1.0），不用华为的值，不申请 | — |
| ECG | 「ECG 测量明细」，部分手表；高阶 | 高阶 | 分钟 | — | 我们的 ECG 走单独的 `ecg` 权限和同意，本期不接 | — |

各类型页：[HRV](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/health-heart-rate-variability-0000002258349489)、[心率 / 静息心率](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/heart-rate-0000001131423780)、[睡眠分期](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/sleep-0000001131264006)、[睡眠记录](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/sleep-record-0000001177830493)、[血氧](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/blood-oxygen-0000001131264010)、[呼吸频率](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/health-breathe-rate-0000002223400302)、[体温](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/body-temperature-0000001177423533)、[VO₂max](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/vo2max-0000001279093573)、[训练记录概述](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/introduction-fitness-record-data-0000001131831088) 和[跑步](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/running-0000001131918792)（其他运动类型页结构相同）。

### 2.3 缺什么会让哪个分算不出来（按资质档位）

| 拿到的权限 | 能出的分数 | 算不出的 |
|---|---|---|
| **全部（高阶 + 基础）** | 恢复、睡眠、睡眠需求 / 债、strain、压力、身体年龄（缺 VO₂max 的手表型号少一个域）、训练日历 | 呼吸项用均值近似、没有逐拍 HRV 去伪迹 |
| **只有基础** | strain（来自训练中心率）、训练日历、训练后心率恢复、身体年龄的步数 / 力量域 | **恢复、睡眠、睡眠需求、压力、生病提示、静息心率**——这些是产品的核心，只有基础档几乎没有价值 |

### 2.4 华为的睡眠分期算法会随版本变

华为官方说明：TruSleep 4.0 及以前主要靠体动、心率和呼吸判断分期（用心率里提取的间期和呼吸做心肺耦合分析），5.0 起加入体位和神经活动等特征；新算法下深睡时长或占比可能变化，「只要比例在参考范围内就正常」；目前支持 5.0 的只有少数新机型（[TruSleep 5.0 介绍](https://consumer.huawei.com/cn/support/content/zh-cn16093496/)、[睡眠监测原理](https://consumer.huawei.com/en/support/content/en-us15963854/)）。对我们的含义：同一个人换表或手表固件升级，深睡 / REM 分钟数会跳变，而我们的睡眠分用了绝对阈值（深睡、REM 各 90 分钟满分）；要在数据里记录设备型号（`sleep_sessions.device` 已有）并在验证里单独看（第 5 节）。

---

## 3. 资质：负责人需要办的事

> 这些由负责人本人办理。下面是按官方文档整理的步骤，括号里是官方给的审核时长。所有页面都以中国站为准（手表用户和开发者账号都在内地），英文站的规则对「中国大陆企业」另有说明，两站的实缴资本数字不一样。

### 3.1 开发者账号：个人还是企业

| | 个人开发者 | 企业 / 事业单位 / 社会团体 |
|---|---|---|
| 能申请的数据 | **只有基础数据**，高阶数据暂不对个人开放 | 基础和高阶 |
| 前提 | **应用必须上架华为应用市场**，而且是移动应用，否则不开放数据；不得有个人信用不良记录 | 企业：基础数据要实缴注册资本 ≥ 50 万元，**高阶数据 ≥ 500 万元**；主体存续且成立 ≥ 1 年。事业单位 / 政府机构：免资金和年限要求。社会团体：以实缴活动资金计，同样 50 万 / 500 万 |
| 数据订阅推送（订阅通知） | 不支持，必须企业账号 | 支持 |
| 其他 | 不开放儿童数据（所有主体都一样） | 企业需要提供年报、审计报告、章程、验资报告等能写明实缴资本的官方文件；资本数据以国家企业信用信息公示系统最新年报为准 |

来源：[中国站·应用开发者申请资质说明](https://developer.huawei.com/consumer/cn/doc/HMSCore-Guides/application-qualification-description-0000001425636570)（2026-01 更新）、[英文站同页](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/application-qualification-description-0000001425636570)、[订阅说明](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/subscription-0000001078496860)、[接入流程](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/access-process-0000001624467736)。

**对我们的结论**：我们的 iOS app 不会上架华为应用市场，个人开发者这条路基本走不通（它只能拿基础数据，而且要求上架）。要拿到睡眠 / HRV / 心率，必须是满足 500 万元实缴资本的企业或事业单位 / 社会团体名义的开发者账号。**先由负责人确认可用主体的实缴注册资本，这一条决定了整件事是否可行**（第 8 节 D1）。官方说资质有疑问可以发邮件或提工单（页面上给了华为运动健康服务的联系邮箱，邮件里要写：开发者站点、用户所在国家 / 地区、企业简介和项目场景、项目负责人联系方式）。

### 3.2 申请步骤（云侧）

1. **注册华为开发者账号并实名认证**（个人或企业，企业只能注册一个账号）。注意「团队账号下的成员账号目前不能独立使用运动健康服务」（[申请页](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/apply-kitservice-0000001050071707)）。
2. **在控制台创建项目和产品，申请「HUAWEI ID 服务」**（约 3 个工作日）。产品类型选「服务端应用」（见第 1.1 节待查）；填回调 URL（只能一个、不带问号和 GET 参数、不能以斜杠结尾、完整路径匹配）和服务器出口 IP；记下 App ID 和密钥。回调 URL 和出口 IP 属于内地区域服务器，具体域名放私有运维文档，不放公开仓库。（[申请 HUAWEI ID 服务](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/apply-id-0000001050069756)）
3. **申请「运动健康服务」测试权限**：控制台 → Health Service Kit → 申请，同意协议，选产品，选数据权限，填写申请材料。
   - 材料：官方提供「申请材料清单（企业开发者）」Excel 和一份 PDF（下载链接在[资质页](https://developer.huawei.com/consumer/cn/doc/HMSCore-Guides/application-qualification-description-0000001425636570)）；每个申请的读 / 写权限都要在表 1、表 2 里写明用途；企业介绍页必填；**隐私政策**里的开发者信息和应用名要与开发者联盟登记一致、写清从华为运动健康取了哪些数据以及用途；涉及数据出境要按《个人信息保护法》第 39 条写清并让用户主动勾选（默认不勾）；华为运动健康的标识要按《标识使用规范》，**入口和提示语里不能出现「华为」「华为运动健康」「华为手表」等文字，只能用官方标识**（这条会影响我们的界面文案，见第 4.5 节）。
   - 审核：人工审核**约 10 个工作日**，结果邮件通知；被驳回按原因改了重提，**每次再约 10 个工作日**。常见驳回原因：缺权限说明、标识用法不对、隐私政策不达标、必填项缺失、资质不符（[驳回常见问题](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/apply-kitservice-0000001050071707)）。
   - 遵守**最小权限**：只申请业务用到的权限，上线后华为会抽查权限使用（抽查已上线应用、监控接口调用、专人检查）。
   - 通过后拿到的是**测试权限，只开放给前 100 个用户**；权限因缓存要等 24 小时才能测。
4. **开发和测试**（第 3 期）。
5. **申请验证（正式权限）**：控制台 → Health Service Kit → 申请验证，勾选要验证的权限（至少一项；用不到的要取消勾选），提交材料（官方要求视频证明和上线检查清单《应用上线 CheckList》），专家组评审**约 15 个工作日**，通过后短信和邮件通知，**24 小时后生效**，用户数限制解除；驳回重提每次再约 15 个工作日。**测试权限拿到后建议一年内完成验证，否则权限可能被收回**。（[申请验证](https://developer.huawei.com/consumer/cn/doc/HMSCore-Guides/verification-0000001211786611)、[接入流程](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/access-process-0000001624467736)）
6. **权限变化**：业务用的权限变了，在控制台「管理」里改后重新提交。

### 3.3 要申请哪些权限（最小集）

按第 2 节，我们只申请评分用得到的（权限名以各数据类型页为准，下面是已核实的）：

| 用途 | 权限（读） |
|---|---|
| 心率、静息心率、HRV | `https://www.huawei.com/healthkit/heartrate.read`（控制台：健康数据 → 心率） |
| 睡眠分期、睡眠记录 | `…/healthkit/sleep.read` |
| 血氧 | `…/healthkit/oxygensaturation.read` |
| 体温（体表温度） | `…/healthkit/bodytemperature.read` |
| 呼吸频率统计、VO₂max（同属「肺功能」） | `…/healthkit/pulmonary.read` |
| 训练摘要 | `…/healthkit/activityrecord.read` |
| 训练中心率明细 | `…/healthkit/activity.read` |
| 步数 / 距离 / 热量等日活动 | 各数据类型页（距离、热量的权限名见[授权示例](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/auth-example-0000001054581058)里的 `distance.read`、`calories.read`；步数权限名待在控制台确认） |
| 读授权前的历史数据 | `…/healthkit/historydata.open.year`（周 / 月 / 年三档；**不申请则只能读授权之后产生的数据**，最多回溯授权前一年；[读取历史数据](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/historydata-open-0000001209921350)） |

**不申请**：写权限（我们只读）、ECG、血压、血糖、生殖健康、体重、位置 / 路线、压力。路线数据（训练轨迹）延续 #219 的 D2：不读。

### 3.4 费用

**官方文档里没有看到运动健康服务的收费条款**（申请、测试、验证、调用接口的页面都没写费用），但我们不能据此断定免费：开发者账号的企业认证、可能的接口调用限额，向华为确认。第 8 节 D1 的工单里一起问。

---

## 4. 架构

### 4.1 数据怎么进来

```
华为手表 ──蓝牙──▶ 华为运动健康 App（用户手机：iOS / Android / HarmonyOS）
                        │ 上传（有延迟：小时级，用户可手动同步）
                        ▼
                 华为运动健康云（中国站）
                        ▲  OAuth 2.0 授权码（用户点「同意」）
                        │  REST 拉取 + 订阅推送（仅企业账号）
        内地区域服务器（Body Engine）── 华为连接器
                        │ 原始样本 → 派生 → daily_metrics / sleep_sessions / workouts
                        ▼
                 评分（恢复 / 睡眠 / strain …）→ 今天页（iOS app）
```

REST 基址是 `https://health-api.cloud.huawei.com/healthkit/v2/…`，授权地址是 `https://oauth-login.cloud.huawei.com/oauth2/v3/authorize`（官方文档的示例，见[授权](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/auth-example-0000001054581058)、[撤销授权](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/canceling-authorization-0000001209549384)）。

**拉取策略**（对照官方建议，见 2.1）：
- 每小时一次增量拉取（沿用每小时的导入任务）+ 今天页下拉 / 「立即同步」手动拉取（和 #219 的 `POST /sync/refresh` 同一个入口，华为用户走各自的连接器）；
- 企业账号注册订阅后，收到变更通知（只带起止时间、数据类型、记录日，不带数据）就按范围拉取，这样早晨睡眠记录一进云就能触发（订阅事件：睡眠 / 心率 / HRV / 血氧 / 体温 / VO₂max 的 `SAMPLESET_EVENT$UPDATE$…`、睡眠记录 `HEALTH_RECORD_EVENT$UPDATE$…`、训练 `ACTIVITY_RECORD_EVENT$UPDATE`，以及用户撤销 `USER_EVENT$REVOKE_ALL`、令牌失效 `USER_EVENT$REFRESH_TOKEN_INVALID`；[订阅](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/subscription-0000001078496860)）。**订阅的回调地址必须能返回 204**（控制台有连通性测试，[FAQ](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/faq-0000001476980529)），是内地区域服务器上一个公网接口，备案和域名事项放 #249 的私有部分；
- 首次授权后补历史：有 `historydata.open.year` 权限时回溯 90 天（评分基线要 60 天，至少 14 晚）；没有则只从授权时刻起，约两周内恢复分在「校准中」。

### 4.2 怎么存

沿用 #219 的做法（原始样本和日值分开、整天重算、脏天队列），但表不放进 Apple 的 `hk_samples`（#219 的 D3：按 HealthKit UUID 去重），新建厂商无关的表，以后小米 / 其他厂商也能用：

- `ext_connections`：`user_id`、`provider='huawei'`、华为 OpenID、已授权的权限列表（授权后调「查询授权信息」接口取回，不信任我们请求的列表）、状态（`linked` / `needs_reauth` / `revoked`）、授权时刻（决定能读到的最早时间）、最后成功同步时间；**刷新令牌（有效期约 6 个月，每次换新令牌要存新的）用现有 AI 密钥的加密方式**（AES-256-GCM，附加数据绑定 user_id，`secrets/ai_master_key`，见 `ai/`），接口永不回传。
- `ext_samples`：`user_id`、`provider`、`type`（华为类型名）、`start_at` / `end_at`、`value`（JSONB）、`device`（手表型号，来自数据采集器信息）、`external_id`（记录 ID，去重和删除用）。幂等：同 (user, provider, type, start, external_id) 只留一条。
- 派生步骤（`ext/derive.py`，和 #219 的 `hk-derive-run` 共用脏天队列和算分队列）：写 `daily_metric_sources(source='huawei')`、`sleep_sessions(source='huawei', device=…)`、`workouts(source='huawei', external_id=华为记录 ID)`，再走 `resolve_daily()`。累计型（步数 / 热量 / 距离）用华为的按天统计接口（`sampleSet:polymerize`），不是自己对小时样本求和，避免和 Apple 的 `hourly_sum` 问题重演。
- **来源标记**：`SOURCE_PRIORITY` 加一个 `huawei`；今天页和接口里每个分数带「数据来源」徽标（短标签，用官方标识，见 4.5）；`daily_scores.flags` 加 `source`、`hrv_kind=rmssd_huawei`、`resp_stat=mean` 等中间量，方便对照和排错。

### 4.3 同一天有 Apple 和华为两份数据：去重和优先级

典型情况有三种，规则按「**不混用、可解释**」定：

1. **只用华为手表（最常见）**：只有 `huawei` 来源，没有冲突。
2. **用户换表（从 Apple Watch 换成华为，或反过来）**：每个用户有一个「主手表」设置（默认 = 最近 7 天里有手表睡眠阶段的来源）。同一夜的睡眠、HRV、静息心率**只取主手表一个来源，不拼接**（`replace_sleep` 本来就是一天只留一个来源）；切换时间点之后基线重建，第 5 节。
3. **iPhone 上的华为运动健康往 Apple 健康写训练、步数、体重**：Apple 同步读到的这些样本，来源是华为运动健康（HealthKit 的 `source.bundleIdentifier`，#219 第 3.1.7 节会记录），和云侧拉的是同一份数据。规则：**该用户已连接华为时，Apple 同步忽略来源为华为运动健康的样本**（对 `hk_samples` 在派生时按来源过滤，原始样本照存，不丢）；没有连接华为时照常使用，作为第 1.3 节的兜底。训练重复再兜一层：现有 `workouts.dup_of` / `refresh_duplicates()` 按开始时间重叠去重，`SOURCE_RANK` 加 `huawei`（和 `app`、`ow` 同层级，主手表那个排前面）。
4. **累计型（步数、热量）**：华为按天统计「手机 + 手表 + 手环」合计；Apple 路线也是多设备去重后的值。沿用现有规则：每个来源先判断是否完整，在干净来源里取最大。**不要把两个来源相加。**

### 4.4 授权、撤销授权、删除

**授权**（只在内地区域的账号上显示入口，第 4.5 节）：

1. 今天页「我 → 数据来源 → 连接华为运动健康」→ app 用系统浏览器会话打开服务器的 `/v1/ext/huawei/authorize`（服务器生成和用户绑定的随机 `state`，防 CSRF）→ 跳到华为授权页（`response_type=code`，`access_type=offline` 才会返回刷新令牌，`display=touch`，权限列表里必须带 `openid`）；
2. 用户登录华为账号、勾选权限（用户可以只勾一部分，我们按「授权信息查询」接口回传的实际权限记录，缺哪类就显示哪类缺失）；
3. 华为带 `code` 回调服务器 → 服务器用 App 密钥换访问令牌 + 刷新令牌 → 加密入库 → 再跳回 app（沿用现有 `watch2whoop://` 自定义协议，状态参数里带成功 / 失败）；
4. **第二道开关**：用户还要在华为运动健康 App 里打开「HUAWEI Health Kit」授权（iPhone：我的 → 第三方服务 → HUAWEI Health Kit → 关联；也可以用华为提供的 H5 页面完成，需要再登录一次；[官方说明](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/server-app-auth-example-0000001071757151)）。服务器调「查询隐私授权状态」接口，没打开就在 app 里提示去开，并给出 H5 入口；
5. 开始补历史 + 增量。

**撤销**（两个方向都要处理）：
- 用户在我们这边点「断开华为」：服务器调 `DELETE /healthkit/v2/consents/{appId}`（取消我们 app 的全部授权），清除本地令牌，状态 `revoked`；原始 `ext_samples` 按用户选择处理：「断开并删除华为数据」（默认，删原始样本、同来源的日值和睡眠，受影响的天重算评分）或「断开但保留已有的分数」。我们不往华为写数据，所以 `deleteData` 参数无关；
- 用户在华为那边撤销（华为账号 → 隐私中心 → 管控帐号访问，或华为运动健康 App → 隐私管理 → 数据共享与授权）：企业账号靠订阅事件 `REVOKE_ALL` 即时知道；否则下次拉取遇到令牌失效（刷新令牌失效的几种情形：改密码、账号冻结 / 注销、设备被删除等，返回 1203 / 11205 / 31204）时标 `needs_reauth`，今天页提示重新连接，不静默失败；
- 用户在华为那边删除账号（`USER_EVENT$DELETE_USER`）：同「断开并删除」。

来源：[撤销授权](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/canceling-authorization-0000001209549384)、[个人数据处理说明](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/personal-data-0000001488852434)（华为是数据处理者、我们是数据控制者；用户撤销授权后华为会删除我们写入的数据；华为账号注销后其数据删除）、[FAQ](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/faq-0000001476980529)。

### 4.5 在内地区域怎么部署

- **站点边界**：华为云按用户账号注册地分站点，跨站请求会被拒绝（403，错误码 121001，响应头 `Location` 给出该用户应访问的域名；[FAQ](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/faq-0000001476980529)）。我们的开发者账号在中国站，只能服务中国站的华为账号用户，**这正好对上 #249 的划分：只有大陆用户放在内地服务器**。因此华为连接器只部署在内地区域，海外区域的 app 不显示入口；香港 / 澳门 / 台湾和海外用户的华为账号在其他站点，需要另一个站点的开发者账号和资质（英文站的资本门槛不同），**本期不做**。
- **一个回调 URL**：HUAWEI ID 服务只能登记一个回调地址，所以注册产品、回调域名、出口 IP 都跟内地区域一一对应，不跨区。
- **数据出境**：内地区域服务器直接拉华为中国站，数据不出境；隐私政策和同意书里要写明数据来源是华为、保存在内地。健康数据是敏感个人信息，通常需要单独同意（《个人信息保护法》第 28、29 条；**需法务确认，本文不是法律意见**）。同意书文字怎么改在 #249 的内地版同意书里统一处理；这里只提供「华为连接要告知的内容清单」：数据类型、来源、用途、保存地点、撤销方式。
- **界面文案与标识**：华为要求运动健康标识按规范使用，**入口和提示语里不能出现「华为」「华为运动健康」「华为手表」等文字**，只能出现官方标识（[驳回常见问题](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/apply-kitservice-0000001050071707)）。所以今天页入口用官方图标 + 通用文字「连接运动健康数据」，不写品牌名；`tr()` 的中英文都按此处理（项目规则：新功能中英双语）。图标要用华为官方提供的，不能自己画（项目规则：图标来自开源库或官方资源，来源记入 `THIRD_PARTY_NOTICES.md`）。
- **合规边界**：未成年人数据华为不开放（官方：运动健康服务不开放儿童数据）；未成年人不在本期范围。

---

## 5. 评分适配

### 5.1 为什么大多数分数不需要单独校准

恢复分是每项对**用户自己前 60 天基线**的 z 分数（HRV 取 ln）。基线是每个人、每种口径各自一套，所以：**手表之间的绝对值差异（加性偏差、乘性偏差）会被基线吸收**，Apple 的 SDNN 和华为的 RMSSD 数值本来就不同，对同一个只用一块表的人没有影响。真正会影响分数质量的是：**读数的噪声、每晚读数条数、缺失夜数**（现有验证文档 3.3 节：单条读数噪声是恢复分最大的误差来源），这些华为文档里没写，要靠数据看。

**必须单独处理的有四处：**

| 项 | 问题 | 处理 |
|---|---|---|
| HRV 口径 | 华为给的是设备算好的 RMSSD（整数毫秒，上限 200），没有逐拍时间 | 新增口径 `hrv_kind = rmssd_huawei`：取夜间睡眠窗口内读数的 ln 中位数，**不走** `rawdata/hrv.py` 的逐拍去伪迹（拿不到逐拍）；同一个人整段历史只用一种口径（现有规则）；`RMSSD_*` 门槛（最近 28 晚至少 21 晚有读数）对 `rmssd_huawei` 单独取值，**待数据定**，第一版沿用现有数值并标「设计取值、未验证」 |
| 200 ms 上限 | 范围写的是 (0, 200]，高 HRV 的年轻人可能被截断在上限 | 阶段 1 统计截断比例；如果 > 一定比例，对这个人降权或标记 |
| 睡眠分绝对阈值 | 深睡 / REM 各 90 分钟满分，是按 Apple 阶段用的；华为的 TruSleep 分期和 Apple 分期的分钟数不同，且随版本变（第 2.4 节） | **v1 不改阈值**，数据里带 `device`、flags 写 `stage_source=huawei`，第 5.3 节的跨设备实验先看差多少，再决定要不要给来源单独的目标值；决定前今天页对华为来源的睡眠分显示数据来源徽标，方法论文档写明「阈值来自 OW 规格，未对华为分期验证」 |
| 呼吸频率 | 只有按夜统计（均值），不是窗口中位数 | 用均值，标 `resp_stat=mean`；权重 0.05，影响很小 |

不需要改的：strain（TRIMP 用心率，和 PPG 品牌无关，华为训练心率明细直接进 `hr`）、静息心率窗口算法、生病提示（用各自基线）、压力（用心率和 HRV 的 z 分数，口径变了基线跟着变）、身体年龄（输入是日值，VO₂max 缺就少一个域）。

### 5.2 换表时的基线

用户从 Apple Watch 换到华为（或反过来）时，主手表切换点之后的 60 天基线要重建：`rmssd_huawei` 和 `sdnn` 不能混在一个基线里。做法：切换日写入 `users` 的设置（主手表 + 生效日期），整段重算（现有机制就是整段重算）时，基线窗口只取同口径的夜；前 14 夜 HRV 不满不出恢复分，今天页显示「校准中」（现有行为）。

### 5.3 和 #118 验证工作的衔接

[评分验证](scoring-validation.md) 第 5 节有真实数据预注册方案 R1–R8。建议新增一条 **R9：跨设备一致性（Apple Watch × 华为手表）**，在写代码前把方法定好：

- **被试**：同时戴两块表睡觉的志愿者（越多越好，至少 3 人，每人 ≥ 14 晚）；
- **比较量**（每晚）：夜间 ln HRV（SDNN vs 华为 RMSSD，只看相关和相对变化，不看绝对值）、夜间静息心率、总睡眠、深睡 / REM / 清醒分钟、入睡和醒来时间、睡眠分、恢复分；
- **方法**：Bland–Altman（偏差和一致性界限）、Spearman 相关、逐夜的恢复分颜色是否一致（绿 / 黄 / 红）；两个来源各自只用自己的基线算分；
- **预先写下的判定**：恢复分颜色一致率、ln HRV 相关系数低于什么值就判定「HRV 项对华为来源不可靠」（阈值在跑之前由负责人定）；深睡 / REM 的平均偏差超过什么值才考虑给华为来源单独的睡眠阶段目标；
- **数据量**：每晚读数条数、缺失夜比例、截断比例（第 5.1 节）单独列出；
- **限制**：这里没有多导睡眠图和心电图，比的是「两个消费级设备互相一致」，不是「谁更准」（和现有验证文档一样的局限）。

这条实验需要志愿者的知情同意，用现有的测试用户同意流程；数据只在内地区域里，不出境（阶段 3 才有数据）。

---

## 6. 分期计划、工作量、风险

### 6.1 分期

| 阶段 | 内容 | 谁 | 估算 |
|---|---|---|---|
| **0 资质与验证项**（对应 issue 第 2 期） | 确认主体实缴资本（D1）；提工单问「服务端应用 + 不上架华为应用市场」是否放行、费用、手表型号清单（HRV 和呼吸「部分手表」是哪些）；注册账号 → HUAWEI ID 服务（3 工作日）→ 测试权限（10 工作日，驳回重提再加）；用华为手表 + iPhone 看「健康」里华为运动健康写了哪些类型 | 负责人 | 约 3–5 周（不含驳回） |
| **1 连接器**（可在阶段 0 等审核时开始，用按官方文档字段做的假接口测试；真实联调要等测试权限） | S1 授权与令牌（4–5 人日）：授权 / 回调 / 令牌加密 / 撤销 / 令牌失效处理；S2 拉取与落表（6–8 人日）：REST 客户端（重试、限流、跨站 403 重定向）、`ext_connections` / `ext_samples` 迁移、历史回溯、每小时拉取 + 手动同步；`huawei-sample-stats` 命令（每晚每类样本数和间隔，回答采样频率） | 开发会话 | 10–13 人日 |
| **2 派生与评分适配** | S3 派生（5–6 人日）：写日值 / 睡眠 / 训练、训练类型映射表（`HUAWEI_WORKOUT_TYPES`，照官方常量表）、来源优先级和 Apple 重复过滤、主手表设置；S4 评分适配（4–5 人日）：`rmssd_huawei` 口径、呼吸均值、flags、合成数据测试、方法论文档更新 | 开发会话 | 9–11 人日 |
| **3 界面与订阅** | S5 订阅推送（3 人日，企业账号，回调接口 + 订阅注册 + 事件处理）；S6 app / 今天页（3–4 人日）：「连接运动健康数据」入口（官方标识、中英文）、状态页、断开 / 删除、缺哪类数据的说明 | 开发会话 | 6–7 人日 |
| **4 验证** | R9 跨设备实验（工具 ~3 人日 + 2 周采集）；正式权限验证（15 工作日，要录屏视频和上线清单）；全员开放前先给内测组（沿用 `rollout/registry.py`，开关 `huawei_sync`，默认关） | 负责人 + 开发会话 | 3 人日 + 日历时间 |

合计代码约 **28–34 人日**（6 个 PR）。时间线上，资质审批是关键路径：**3 + 10 + 15 个工作日 + 24 小时 ≈ 6 周**（一次通过），开发可以在测试权限到手后与验证申请并行。拿到测试权限后要在**一年内**提交验证。

### 6.2 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 主体实缴资本不足 500 万 | 拿不到睡眠 / HRV / 心率，整个功能没有价值 | 阶段 0 第一件事就确认；不够就不申请，不做基础档半成品（D1） |
| 审核被驳回（标识、隐私政策、资质等） | 每次多 10–15 个工作日 | 按官方「常见驳回」逐条自查；隐私政策、界面、材料三者保持一致 |
| 「部分手表」才有 HRV / 呼吸 / 体温 / VO₂max | 用户的型号不在列表里时，恢复分算不出 | 阶段 0 问华为要型号清单；今天页对缺失项明确显示原因（沿用 flags），不静默 |
| 数据时效是小时级，没连蓝牙 / 手机没后台权限时更晚 | 今天页恢复分晚出 | 手动同步按钮 + 订阅推送；文案提示「打开运动健康 App 同步」 |
| HRV 读数稀疏 / 被截断在 200 | 恢复分噪声大 | `huawei-sample-stats` 先量；R9 实验；必要时对该来源降权 |
| 睡眠分期算法随华为版本变 | 同一人深睡突变，睡眠分跳变 | 记录设备型号和固件时间点，睡眠分对华为来源显示来源徽标；R9 后再定目标值 |
| API / 类型随华为更新（HRV、呼吸频率、匹克球等都是新近加的） | 字段变化导致解析失败 | 按类型解析、未知类型存原样跳过；定期看华为的[变更记录](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/version-change-history-0000001233272401)；解析失败进数据质量告警 |
| 刷新令牌 6 个月过期 / 用户改密码 | 悄悄断流 | 每次刷新存新令牌；`needs_reauth` 状态 + 提示；订阅事件 `REFRESH_TOKEN_INVALID` |
| 华为抽查权限用途、测试权限一年内要验证 | 权限被收回 | 最小权限；开发完成后尽快提交验证 |
| 华为手表用户多数是否用 iPhone | 只做 iPhone 路线可能覆盖面很小（**推断**：华为手表通常配华为 / Android 手机，我们没有查到官方的配对比例统计） | D3：iPhone 和华为手机客户端并行（第 7 节），不读手表数据的壳先发 |

---

## 7. 华为端 app：发布手续和最快路径

> 背景：负责人要尽早给用华为手机的家人发一个可用版本。本节回答三件事：华为手机上能装什么 app、要办什么手续、最快怎么让家人用上。
> 核心结论：**手表数据走云侧 REST（第 1、4 节），由服务器和华为云对接，客户端不需要读任何健康权限**，所以第一个版本可以完全不碰手表数据（今天页、AI 记录、手动记录），之后服务器侧资质通过了，不用改客户端就能加上手表数据。客户端只是「内嵌今天页的壳」，华为端的发布手续里**不涉及健康数据权限**。

### 7.1 先分系统：家人的手机是哪一种

| | HarmonyOS 4.x 及更早（含 4.3）/ 海外 EMUI | HarmonyOS NEXT（5.0 起，含 5.x、6.x、26.0） |
|---|---|---|
| 能不能装 APK | 能（有 Android 兼容层，Android 应用可以直接运行） | **不能**，只支持鸿蒙原生应用（HAP） |
| 我们要做什么 | 给现有 Flutter 工程加 `android/` 目标（`docs/apps.md` 本来就计划做），出 APK 侧载 | 单独做一个 ArkTS 原生壳（`apps/harmony/`，`docs/apps.md` 已建议），内嵌今天页 |
| 要华为开发者账号吗 | **不要**（侧载不经过华为） | 要（签名证书、Profile 都由华为开发者账号签发） |

依据：
- 华为开发者社区的一篇梳理文章写明：普通 HarmonyOS（到 4.3，最后一个安卓适配版本）借助兼容层可直接运行 Android 应用，HarmonyOS NEXT「不再兼容任何安卓应用，仅支持 HAP 格式的鸿蒙原生应用」（[HarmonyOS 历史版本及与 NEXT 的区别](https://developer.huawei.com/consumer/cn/blog/topic/03199548836169090)）。**注意这是社区文章，不是华为的正式政策文件**；我们没有查到华为官方文档对「NEXT 不能装 APK」的直接表述（**待确认**，家人手机上装一下就知道）。
- 华为官方的设备清单页（2026-10 更新）列出了 HarmonyOS 5.x / 6.x / 26.0 支持的手机：Mate 60 系列及以后、Pura 70 系列及以后、nova 14 以后、畅享 90 等（[各版本支持设备型号清单](https://developer.huawei.com/consumer/cn/doc/doccenter-release-notes/support-device)）。也就是说近两三年的华为手机大概率已经是 NEXT；老机型是不是 4.x、能不能升级，要看具体机型，**先查家人的手机**。
- 怎么查：手机「设置 → 关于手机」里看 HarmonyOS 版本号（4.x 还是 5.x / 6.x），记下手机型号和手表型号（手表型号决定能不能有 HRV，第 2 节）。

### 7.2 NEXT 这一侧：Flutter 能不能用

- 有社区 / 开源鸿蒙 SIG 维护的「Flutter for OpenHarmony」分支（[flutter_flutter](https://gitcode.com/CPF-Flutter/flutter_flutter/tree/br_3.27.4-ohos-1.0.4)：基于 Flutter 3.27.4，说明里提到 3.35.7 起可以从同一份源码构建；用 `flutter build hap` 出包）和插件适配仓库（[flutter_packages](https://gitcode.com/openharmony-tpc/flutter_packages)：已适配 webview_flutter、url_launcher、shared_preferences、path_provider、camera、image_picker 等，推荐版本 3.7 / 3.22 / 3.27 / 3.35 / 3.41）。仓库说明里**没有成熟度或生产可用的声明**，已知问题里有模拟器不支持 Vulkan 要关 Impeller、个别 ROM 上调试包安装失败等。
- 我们的 app 锁在 Flutter 3.47.7，比这些分支都新；依赖里的 `sign_in_with_apple`（苹果专有）、`open_wearables_health_sdk`（OW 没有鸿蒙版）、`flutter_secure_storage`、`mobile_scanner`、`wakelock_plus` 等，没有在适配清单里（**待确认**，要逐个查）。
- 所以**不建议用 Flutter 做鸿蒙版**，和 `docs/apps.md` 的判断一致：做一个**原生 ArkTS 壳**，用 ArkWeb 的 `Web` 组件内嵌今天页（自己实现登录、扫码、深链、文件 / 麦克风权限）。壳不依赖 OW，也不读健康数据。

### 7.3 开发者账号和手续（只有 NEXT 这一侧要）

| 事项 | 个人开发者 | 企业开发者 | 来源 |
|---|---|---|---|
| 实名认证 | 提交身份证等，**审核一般 1–2 个工作日**（周末节假日除外） | 营业执照等，人工审核或对公银行打款认证，审核同样 1–2 个工作日；打款认证的到账时间 **待确认** | [注册认证 FAQ](https://developer.huawei.com/consumer/cn/doc/doccenter-getting-started/hdc-verification-faq-0000002752607795) |
| 账号数量 | 每个个人 / 企业只能有一个实名账号；**个人账号不能直接升级成企业账号**，要用企业信息重新注册 | 同左 | 同上 |
| 调试证书 | 实名后有效期 **1 年**（未实名只有 14 天）；每个账号最多 3 张 | 同左 | [申请调试证书](https://developer.huawei.com/consumer/cn/doc/doccenter-getting-started/agc-help-debug-cert-0000002283256797) |
| 注册调试设备 | 用设备 UDID（`hdc shell bm get --udid`，要打开 USB 调试并连电脑），**一个账号最多 100 台**；调试 Profile 也最多选 100 台设备 | 同左 | [注册设备](https://developer.huawei.com/consumer/cn/doc/doccenter-getting-started/agc-help-add-device-0000002283189937)、[调试 Profile](https://developer.huawei.com/consumer/cn/doc/doccenter-getting-started/agc-help-debug-profile-0000002248181278) |
| 费用 | 官方文档里没有看到注册 / 测试分发的收费条款（**待确认**，社区帖子说个人上架无需费用，不算官方依据） | 同左 | — |

**三种不上架应用市场的分发方式**（[对比表](https://developer.huawei.com/consumer/cn/doc/doccenter-submission/agc-help-internal-test-overview-0000002253054942)）：

| 方式 | 谁能用 | 人数上限 | 要审核吗 | 要备案 / 资质吗 | 适合 |
|---|---|---|---|---|---|
| **调试包 + hdc 安装**（调试证书 + 调试 Profile） | 个人、企业都可以（文档没有限制） | 100 台设备 | 不经过应用市场，没有审核 | 不要 | 开发期、当面给最多几位家人装；要打开 USB 调试连电脑 |
| **指定设备发布**（自己的服务器放包，Deeplink 下载，或 hdc 安装） | **仅企业开发者**（[流程页](https://developer.huawei.com/consumer/cn/doc/doccenter-submission/agc-help-internal-test-guide-0000002295325149)明说） | 100 台设备 | 不提交应用市场 | 不要 | 有企业账号时，团队内部 |
| **邀请测试**（AppGallery 邀请测试 / AppTest 邀请测试，用邀请链接或邀请码） | 文档没有限定企业（个人账号能不能用 **待确认**，提工单问） | 外部测试用户最多 10000 人 | **要**：首个版本全面审核，之后的构建版本通常分钟级（部分开发者开放）；审核时长官方没有给确数，应用审核指南说常规应用通常 24 小时（**待确认**邀请测试同样） | **不要**（官方 FAQ：发布邀请测试不需要先提供资质、备案） | **远程给家人**：发个链接，不用 USB、不用 UDID |

两点限制：邀请测试停止后，已安装的测试版 90 天后无法启动；邀请测试和 AppTest 需要在 HarmonyOS 开发者中心里先为应用开通（AppTest 要工单申请，来源：[AppTest 邀请测试流程](https://developer.huawei.com/consumer/cn/doc/doccenter-submission/agc-help-apptest-invite-test-guide-0000002258174270)、[邀请测试 FAQ](https://developer.huawei.com/consumer/cn/doc/doccenter-submission/agc-help-invite-test-faq-0000002363660365)）。

### 7.4 正式上架要什么（不建议第一版做）

- **APP 备案**（工信部移动互联网应用程序备案）：未备案不能提供互联网信息服务，上架审核会核验；
- **隐私政策**：开发者名称、应用名要和开发者中心一致，链接长期可访问；要填隐私标签；个人开发者还要提交《个人开发者承诺函》；
- 软件著作权：**非必选**，华为建议申请，名称要和应用名一致；
- **要小心的品类**：应用里有**生成式人工智能服务**（我们的应用内 AI 对话属于这一类）要提供算法备案和安全评估报告；医疗服务、健康体检等需要医疗机构执业许可证（我们不是医疗服务，但上架分类时要避免被归入）。**待确认**：我们的应用内 AI 是否落入「深度合成或生成式人工智能服务」的资质要求，要问华为客服。
- 个人开发者上架的限制：需要具备资质的法律实体才能上架的品类（金融、医疗等）个人不能提交。

来源：[应用资质审核要求](https://developer.huawei.com/consumer/cn/doc/doccenter-submission/80301)、[AppGallery 审核指南](https://developer.huawei.com/consumer/cn/doc/doccenter-submission/50104)。正式上架牵涉备案（和 #249 的内地区域域名备案是同一批材料，细节放私有运维文档），周期按月算，**不是最快路径**。

### 7.5 健康数据权限（Android 侧 HMS 和 NEXT 侧）

- **申请是同一套**：「运动健康服务」是一个控制台卡片，数据权限（心率、睡眠、HRV 等）按「产品 / 应用」申请，端侧 Android（HMS Core）、端侧 HarmonyOS、云侧 REST 用的是同一份资质要求和同一套审核（[接入流程](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/access-process-0000001624467736)、[端侧概述](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/dev-overview-0000001475952057)：端侧支持 Android 和 HarmonyOS）。NEXT 端侧（ArkTS，`@kit.HealthServiceKit`）有没有额外步骤（例如签名指纹、ACL 权限）**待确认**，因为我们不走端侧，所以不影响本方案。
- **个人开发者**：只能申请「基础」数据（训练记录和训练中心率、步数、热量），而且**要求应用上架华为应用市场**；心率、睡眠、HRV 等「高阶」数据不对个人开放（第 3.1 节）。所以个人账号**读不到**家人的睡眠和 HRV。
- **审核时间**（企业主体）：测试权限约 10 个工作日，正式验证约 15 个工作日（第 3.2 节）。**短期内（家人要用的两三周内）拿不到**。
- **第一个版本可以不读手表数据吗**：可以，而且是推荐做法：华为端壳只做今天页 + AI 记录 + 手动记录；手表数据在资质通过后，经服务器的云侧连接器（第 4 节）加进来，授权用系统浏览器完成（华为账号 OAuth），**客户端不需要新版本**，只是今天页「我 → 数据来源」里多一个入口。
- 如果想用个人账号提前拿到一点数据：个人开发者 + 应用上架华为应用市场（要备案）→ 基础数据（训练、步数），只能算 strain，没有恢复分和睡眠分，且要走正式上架，周期长，**不推荐**。

### 7.6 最快让家人用上：推荐路径和时间

> 时间是估算（工作日 / 人日），不是承诺；标「负责人」的是必须本人去办的。

**第 0 步（负责人，今天，约 10 分钟）**：问清每位家人的手机型号、HarmonyOS 版本号（4.x 还是 5.x / 6.x）、手表型号。分成两组走下面两条线，两条线可以并行。

**可选过渡（零开发，可能当天可用，需要负责人同意）**：今天页本身是网页，登录有「邮箱验证码 / 激活码」流程（`activate.html`、`install.html`），家人用华为浏览器打开今天页地址、添加到桌面就能用。**限制**：项目已决定「app 是唯一客户端」，网页模式不再维护；网页里没有按住说话（需要 app 注入的麦克风接口）、没有离线壳。能不能用、哪些功能缺，**待确认**（需要在一台华为手机上试）。仅作为 app 出来之前的过渡，D10。

**A 线：HarmonyOS 4.x 及以前的家人 → Android APK 侧载（最快）**

| 步骤 | 谁 | 预计 |
|---|---|---|
| A1 给 Flutter 工程加 `android/` 目标：去掉 iOS 专有部分（Apple 登录、OW 同步、HealthKit）用 `Platform` 开关隔开；WebView、扫码（`mobile_scanner` 在没有 Google 服务的机型上的表现 **待确认**，必要时换内置识别库）、账号令牌存储、深链 `watch2whoop://setup` 的 Android 意图过滤；中英文文案 | 开发会话 | **5–8 人日**（约 1–1.5 周） |
| A2 用自己的 keystore 签名、出 release APK，写一页安装说明 | 开发会话 | 0.5 人日 |
| A3 把 APK 发给家人，在手机上允许「外部来源应用安装」（具体设置路径和华为「纯净模式」是否拦截 **待确认**，用一台家人的手机试） | 负责人 + 家人 | 当天 |

**N 线：HarmonyOS NEXT 的家人 → ArkTS 壳**

| 步骤 | 谁 | 预计 |
|---|---|---|
| N1 注册华为开发者账号并实名认证：**个人账号 1–2 个工作日**；这个账号只用于客户端分发，不影响第 3 节的企业资质（个人和企业各自一个账号，互不冲突）。先问清是否要等 D1 的结论再决定用哪个主体（见 D11） | **负责人** | 1–2 个工作日（今天开始） |
| N2 开发者中心创建 HarmonyOS 应用（应用名、包名；与 iOS 的包名独立）、申请调试证书（实名后 1 年有效） | 负责人 + 开发会话 | 0.5 天 |
| N3 开发 ArkTS 壳：Stage 模型、`Web` 组件内嵌今天页（Cookie 持久化、外链用系统浏览器）、登录 / 激活码、扫设置二维码、`watch2whoop://setup` 深链、文件选择 / 麦克风权限、中英文。需要 DevEco Studio、`hdc`、一台 NEXT 真机（家人的手机可当测试机，要打开开发者模式和 USB 调试）；今天页里调用 app 注入接口（`window.Watch2Whoop`、`Watch2WhoopFeatures`）的地方要用 ArkWeb 的 JS 代理对应实现。这个仓库的开发会话**没有写过 ArkTS、也没验证过 DevEco 环境**，所以第一版留出真机迭代时间 | 开发会话 | **8–12 人日**（约 2 周） |
| N4a **当面装**：调试证书 + 调试 Profile + 用 `hdc` 安装到最多 100 台已登记 UDID 的手机（UDID 用 `hdc shell bm get --udid` 取） | 负责人 + 家人（当面） | N3 完成后当天 |
| N4b **远程装**：为应用开通邀请测试 / AppTest（工单），准备发布证书、发布 Profile、应用资料和隐私政策地址，创建测试群组或公开链接，首个版本过审；家人用链接 / 邀请码安装，不用 USB（**个人账号能否使用 + 审核时长待确认**，先提工单问） | 负责人 + 开发会话 | 约 1–2 周（含审核，估算） |

**合计**：A 线约 **1.5 周**；N 线当面安装约 **3 周**，远程邀请测试约 **4–5 周**。两条线同时做，开发会话是瓶颈（A1 + N3 合计约 13–20 人日，顺序做大约 3.5–4 周；先 A 后 N，A 线家人 1.5 周可用）。**手表数据**是另一条线：资质审批约 6 周（第 3 节），不挡客户端发布。

**排期建议**：先做 A 线 + 过渡（如果批准）；N 线的账号注册今天就开始（1–2 个工作日），让开发会话在 A1 之后接 N3；家人在身边的先用 hdc 装 N4a，等 N4b 的工单有答复再决定要不要走邀请测试。

---

## 8. 要负责人决定的点

| 编号 | 问题 | 推荐 |
|---|---|---|
| **D1** | 用哪个主体申请、要不要申请：高阶数据要求实缴注册资本 ≥ 500 万元的企业 / 事业单位 / 社会团体，个人不行 | **先确认可用主体的实缴注册资本。≥ 500 万再开始；不够就不申请，也不做只有基础数据的版本**（没有恢复 / 睡眠）。同时提工单问：服务端应用 + 不上架华为应用市场是否放行、是否收费、手表型号清单。备选（待查）：用户从运动健康 App 自行导出文件再导入（像 Apple 健康导出那样），要先确认华为是否提供这种导出和格式 |
| **D2** | 接入路线 | **云侧 REST（服务器直连）**；端侧 SDK 不做 |
| **D3** | 目标人群：只做「华为手表 + iPhone」，还是也覆盖 Android / HarmonyOS 手机用户（需要新客户端，和「iPhone app 是唯一客户端」的决定冲突）。负责人已提出要尽早给用华为手机的家人用 | **都做，而且华为手机客户端先发一个不读手表数据的版本**：4.x 手机用 Android APK 侧载、NEXT 手机用 ArkTS 壳（第 7 节）；手表数据经服务器云侧连接器后加，客户端不用改 |
| **D4** | 区域：华为连接器只放内地区域，只服务中国站华为账号；港澳台和海外用户的华为账号不支持 | **同意**（对上 #249）；海外站点另开 issue，需要另一个开发者账号和资质 |
| **D5** | HRV 口径：新增 `rmssd_huawei`，不和 Apple 的 SDNN 混用，换表重建基线 | **同意**（第 5 节） |
| **D6** | 多来源规则：每个用户一个「主手表」，同一夜 / 同一指标只取一个来源，不拼接；用户已连华为时 Apple 同步忽略来源为华为运动健康的样本 | **同意**（第 4.3 节） |
| **D7** | 同意书和隐私：华为是新的数据来源和处理方，内地版需要告知并单独同意（需法务确认），连接前再弹一次数据类型清单 | **在 #249 的内地版同意书里一起改**，连接前的弹窗本期实现；法务意见由负责人去问 |
| **D8** | 申请的权限范围 | **最小集**（第 3.3 节：心率 / 睡眠 / 血氧 / 体温 / 肺功能 / 训练 / 步数等 / 历史一年）；不申请写权限、ECG、血压、血糖、生殖健康、体重、位置 |
| **D9** | 跨设备验证实验（R9）要不要做、找谁做志愿者 | **做**，拿到测试权限后找 ≥ 3 位同时戴两块表的志愿者、每人 ≥ 14 晚；判定阈值由负责人在跑之前定 |
| **D10** | 家人用哪条路、要不要先用网页过渡 | **先问清每位家人的手机型号和 HarmonyOS 版本号**，4.x → A 线（APK），5.x / 6.x → N 线（ArkTS 壳）；网页过渡只在 app 出来前用，并先在一台华为手机上确认功能缺口（会和「app 是唯一客户端」的决定冲突，需要你点头） |
| **D11** | 华为开发者账号：个人还是企业 | **今天先注册个人账号**用于客户端分发（1–2 个工作日，个人账号不能升级成企业账号，但个人和企业各一个账号互不冲突）；第 3 节的运动健康资质用企业主体另注册，两者无关联；是否再等 D1 的结论取决于你是否想让所有东西落在企业账号下（指定设备发布只有企业账号能用） |
| **D12** | 远程分发：邀请测试要不要走，生成式 AI 的资质怎么办 | **走邀请测试**（官方 FAQ：不需要备案和资质）；正式上架暂不做，等备案（#249 同一批材料）和「应用内 AI 是否要算法备案」问清再说 |

---

## 9. 引用清单

华为官方开发者文档（英文站，中国站同路径把 `/en/` 换成 `/cn/`）：
- [Health Service Kit 接入流程](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/access-process-0000001624467736)、[开放数据总览](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/data_description-0000001467889369)、[应用侧开放服务（三种路线）](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/application_description-0000001418052318)、[设备侧概述](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/dev-overview-0000001475952057)
- 资质：[中国站](https://developer.huawei.com/consumer/cn/doc/HMSCore-Guides/application-qualification-description-0000001425636570)、[英文站](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/application-qualification-description-0000001425636570)；申请：[申请 HUAWEI ID 服务](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/apply-id-0000001050069756)、[申请 Health Service Kit](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/apply-kitservice-0000001050071707)、[申请验证（中国站）](https://developer.huawei.com/consumer/cn/doc/HMSCore-Guides/verification-0000001211786611)
- 云侧开发：[服务介绍](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/overview-restful-api-0000001050071695)、[授权](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/auth-example-0000001054581058)、[撤销授权](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/canceling-authorization-0000001209549384)、[在华为运动健康中开放授权](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/server-app-auth-example-0000001071757151)、[读取历史数据](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/historydata-open-0000001209921350)、[订阅](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/subscription-0000001078496860)、[数据查询时效](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/dataquery-timeliness-desc-0000001058864328)、[个人数据处理说明](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/personal-data-0000001488852434)、[FAQ](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/faq-0000001476980529)
- 数据类型：[概述](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/overview-0000001177423513)、[健康采样数据](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/health-sampling-data-0000001131423778)、[心率](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/heart-rate-0000001131423780)、[HRV](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/health-heart-rate-variability-0000002258349489)、[睡眠状态](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/sleep-0000001131264006)、[睡眠记录](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/sleep-record-0000001177830493)、[血氧](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/blood-oxygen-0000001131264010)、[呼吸频率](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/health-breathe-rate-0000002223400302)、[体温](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/body-temperature-0000001177423533)、[VO₂max](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/vo2max-0000001279093573)、[训练记录](https://developer.huawei.com/consumer/en/doc/HMSCore-Guides/introduction-fitness-record-data-0000001131831088)
- 睡眠算法：[TruSleep 5.0](https://consumer.huawei.com/cn/support/content/zh-cn16093496/)、[睡眠监测原理](https://consumer.huawei.com/en/support/content/en-us15963854/)
- iOS：[华为运动健康 App Store 页面](https://apps.apple.com/cn/app/%E5%8D%8E%E4%B8%BA%E8%BF%90%E5%8A%A8%E5%81%A5%E5%BA%B7/id1325481372)、[华为官方支持页（同步步数到第三方）](https://consumer.huawei.com/cn/support/content/zh-cn01057420/)
- 开源：[Open Wearables providers](https://github.com/the-momentum/open-wearables/tree/main/backend/app/services/providers)、[Apple 健康文档](https://github.com/the-momentum/open-wearables/blob/main/docs/providers/apple-health.mdx)
- 华为端 app（第 7 节）：[注册认证 FAQ](https://developer.huawei.com/consumer/cn/doc/doccenter-getting-started/hdc-verification-faq-0000002752607795)、[申请调试证书](https://developer.huawei.com/consumer/cn/doc/doccenter-getting-started/agc-help-debug-cert-0000002283256797)、[注册设备](https://developer.huawei.com/consumer/cn/doc/doccenter-getting-started/agc-help-add-device-0000002283189937)、[调试 Profile](https://developer.huawei.com/consumer/cn/doc/doccenter-getting-started/agc-help-debug-profile-0000002248181278)、[指定设备发布](https://developer.huawei.com/consumer/cn/doc/doccenter-submission/agc-help-internal-test-overview-0000002253054942)、[AppTest 邀请测试](https://developer.huawei.com/consumer/cn/doc/doccenter-submission/agc-help-apptest-invite-test-guide-0000002258174270)、[邀请测试 FAQ](https://developer.huawei.com/consumer/cn/doc/doccenter-submission/agc-help-invite-test-faq-0000002363660365)、[应用资质审核要求](https://developer.huawei.com/consumer/cn/doc/doccenter-submission/80301)、[各版本支持设备型号](https://developer.huawei.com/consumer/cn/doc/doccenter-release-notes/support-device)、[HarmonyOS 与 NEXT 的区别（社区文章）](https://developer.huawei.com/consumer/cn/blog/topic/03199548836169090)、[Flutter for OpenHarmony](https://gitcode.com/CPF-Flutter/flutter_flutter/tree/br_3.27.4-ohos-1.0.4)、[插件适配](https://gitcode.com/openharmony-tpc/flutter_packages)；项目内 [docs/apps.md](apps.md)
- 项目内：[#219 自建同步引擎](apple-sync-engine.md)、[评分方法论](scoring-methodology.md)、[评分验证](scoring-validation.md)、[原始数据方案](raw-data-plan.md)；#249 多区域方案见 PR #250（`docs/multi-region.md`，合并后可链接）

---

## English summary

**Goal.** Add Huawei watches as a data source (issue #253, phase 1: research and design only; no code).

**Recommended route.** Huawei Health Service Kit **cloud-side REST API** (server-to-server, OAuth 2.0 authorization code), not the device-side SDKs: those exist only for Android and HarmonyOS, and we have an iOS app plus a server. The Huawei Health iOS app has its own "HUAWEI Health Kit" link switch, so "Huawei watch + iPhone" works through the cloud. Going through Apple Health instead is only a fallback: the only officially declared types are weight and workouts (steps are mentioned as an example); heart rate, HRV, sleep stages, SpO₂ and temperature are undocumented.

**The real gate is qualification.** Huawei labels heart rate (incl. resting HR and HRV), sleep, SpO₂, temperature, VO₂max and respiratory rate "advanced" data. Individual developers cannot get advanced data (and must list the app on Huawei's store). On the China site, an enterprise needs paid-up capital ≥ CNY 5 million (≥ CNY 0.5 million for basic data) and ≥ 1 year of existence; public institutions are exempt from the capital rule. With basic data only (workouts with in-workout heart rate, steps, energy) there is no recovery score, no sleep score, no resting heart rate. Process: HUAWEI ID service (~3 working days) → test scopes (manual review ~10 working days, 100-user cap, 24 h to take effect) → verification (video + checklist, ~15 working days) → formal scopes; test scopes should be verified within a year. The docs state no fees (confirm with Huawei). The owner decides on the applicant entity first (D1).

**Data.** HRV is a device-computed **RMSSD** (integer ms, range (0, 200], "selected watches" only, no beat-to-beat intervals), so our RR-interval path cannot be used; sleep has light / REM / deep / awake stages plus a sleep record with Huawei's own score; respiratory rate exists only as per-night statistics via the sleep record; most data has hour-level query timeliness; sampling intervals are not documented (to be measured with test scopes). Mapping: new HRV kind `rmssd_huawei` (per-user baseline, never mixed with Apple SDNN), respiratory mean instead of median, sleep-stage targets unchanged in v1 with a source badge until a cross-device study (proposed R9 under #118: ≥ 3 volunteers wearing both watches ≥ 14 nights, Bland–Altman) says otherwise.

**Architecture.** A provider-neutral `ext_connections` / `ext_samples` raw layer (refresh tokens encrypted like AI keys), a derive step that shares #219's dirty-day queue and writes `source='huawei'` rows into `daily_metric_sources`, `sleep_sessions`, `workouts`; one "primary watch" per user (no cross-source merging within a night); when Huawei is linked, Apple-sync samples whose source is Huawei Health are ignored to avoid duplicates. Authorization and revocation both ways (in-app disconnect calls the consents API; Huawei-side revocation via subscription events or token failure → `needs_reauth`). Deployed only in the mainland region (#249): Huawei cloud sites are per account region and cross-site calls are rejected; UI must use Huawei's official logo, not brand text.

**Plan.** Phase 0 qualification (owner, ~3–5 weeks + rejections); phases 1–3 about 28–34 person-days in 6 PRs; phase 4 validation. Twelve owner decisions with recommendations are in section 8.

**Huawei-phone client for family (section 7, top priority).** Watch data comes through the server-side cloud API, so the client needs no health permission and the first version can ship without watch data. HarmonyOS 4.x and earlier run Android APKs (per a Huawei developer-community article; no official statement found): add an `android/` target to the Flutter app and sideload the APK, no Huawei account needed, ~1–1.5 weeks. HarmonyOS NEXT (5.0+) cannot run APKs: build a thin native ArkTS shell with an ArkWeb view (the Flutter-for-OpenHarmony fork is community-maintained, lags our Flutter version and makes no maturity claims), ~2 weeks, needs a Huawei developer account (individual real-name verification takes 1–2 working days). Distribution: debug-signed build + `hdc` on up to 100 registered devices (individual accounts allowed), or invitation testing (up to 10,000 users; Huawei's FAQ says no filing or qualification is needed; first version is reviewed); designated-device release is enterprise-only; full store listing needs ICP-style app filing, a privacy policy and possibly generative-AI filings, and takes months. Estimates: APK ~1.5 weeks, NEXT ~3 weeks in person or 4–5 weeks remote.
