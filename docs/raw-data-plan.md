# 手表原始数据方案：用自己的 app 尽量拿全 Apple Watch 数据（2026-10-18，待用户确认）

用户原话：「我们如果有 Apple developer 账号也不能拿到手表的原始数据么？尝试尽量拿到手表全部的原始数据然后配上算法把其他这些效果都实现上。」

本文是**调研和方案，确认前不大规模实现**。「做不了」的清单来自 `docs/wearable-gap-analysis.md` 第 4 节；iOS app 的现状见 `docs/system-design.md` 第 17 节。

---

## 0. 结论先说

1. **有 Developer 账号也拿不到「最原始」的数据**：光电传感器的 PPG 波形、原始 ECG 流只在 SensorKit 里，而 SensorKit 的 entitlement 只发给经 Apple 批准的研究项目（Apple 文档原话 "only grants the entitlement for approved research studies"），个人 app 申请不到。手表端算法（高血压通知、睡眠呼吸紊乱、心肺适能、睡眠分）也不开放，只能读它们写进「健康」的**结果**，睡眠分连结果都不写。
2. **但「健康」里还有一大块我们没同步的「次原始」数据，有账号就能拿**，价值最大的四样：
   - **心跳间期（HKHeartbeatSeriesSample）**：Apple 每次测 HRV 都存了约 1 分钟的逐拍时间。**实测**：本机 export.xml 里 2025-08-28 以来 1,927 次 HRV 读数全部带逐拍数据（中位数 52 拍），用它重算的 SDNN 和 Apple 的 SDNN 相关 0.90，说明可以自己算 **RMSSD**（WHOOP 口径）。不用等 OW 发版，**历史也能回算**。
   - **Apple 自己的 RMSSD（`heartRateVariabilityRMSSD`，iOS 27 新类型，已在 Apple 文档核实）**：OW SDK 0.15 还不支持，自己的插件可以马上读。Series 12 多久写一次 Apple 没说，要真机实测；和上一条自算的对照着用。
   - **心电图（HKElectrocardiogram）**：用户自己在手表上测的 30 秒单导联 ECG，512 Hz 电压（本机导出的 csv 是 512 Hz）+ Apple 的分类（窦性心律 / 房颤 / 心率过低或过高无法判断 / 读数差）。能做「看得见的 ECG 波形 + 历史」，并从波形里算心率、RR 间期。本机导出里有 1 条（2025-08-28，窦性心律）。
   - **OW SDK 和快捷指令都没同步的类型**：运动后 1 分钟心率恢复、睡眠呼吸紊乱、**高血压通知事件**（iOS 26.2 新类型）、日照时间、房颤负荷、体力消耗（physicalEffort）、手腕温度（app 用户目前没有）、步行稳定性、音频暴露、心率过高 / 过低 / 心律不齐通知等。
3. **自己写 watchOS 配套 app 能补的**：**智能闹钟**（smart alarm 会话，起床前 30 分钟窗口内看到浅睡迹象就震动）、后台加速度记录（CMSensorRecorder，50 Hz、每段 ≤ 12 小时，用来数夜里翻身 / 醒来、精确判断白天静止）、训练时 800 Hz 加速度（只在真实训练里，可做力量训练计次）。**拿不到更多夜间 HRV**：第三方没有实时 RR，训练会话里也没有逐拍，夜间 HRV 测几次由 Apple 决定（实测每晚 1–4 次）；用训练会话整夜保活会被审核拒（Guideline 2.5.4），耗电也大。
4. **「做不了」清单逐项的新结论**：

| 原来「做不了」 | 新结论 | 怎么做 |
|---|---|---|
| ECG | ✅ **能做**（看波形 + 分类 + 历史 + 从波形算心率 / HRV） | 原生插件读 HKElectrocardiogram；不做自己的诊断 |
| 血压数值 | ❌ 仍然做不了（没有任何公开 API 给数值） | 手动录入 / 蓝牙血压计写进「健康」的 bloodPressure 可以读；高血压通知事件见第 2 节 |
| 心血管年龄 | 🟡 **只能「估算」** | 无 PPG 脉搏波速度；用 VO₂max、静息心率、HRV、心率恢复、年龄做近似，界面标「估算」 |
| 高血压通知 | 🟡 **能读事件，不能自己算** | `hypertensionEvent`（iOS 26.2）有就展示事件历史；澳洲 2026-01 起可用，但 Apple 注明 Series 12 在部分地区的额外批准还没下来，要看你的表上能不能打开这个功能 |
| 智能闹钟 | ✅ **能做**，但要 watchOS 配套 app | smart alarm 会话（最长 30 分钟，睡前在手表上点一下预约）+ 手表上的运动 / 心率判断浅睡；只能震动 |

5. **推荐路径**：不改 OW fork，**给 Flutter app 加一个原生 Swift 插件「RawSync」直接读 HealthKit，分批上传到 Body Engine 新接口** `POST /v1/ingest/healthkit`（按用户 token、幂等、后台）；OW SDK 继续管它已经管的类型。watchOS 配套 app 放到 P1/P2。**P0 的第一半不需要 app，也不需要等 TestFlight**：心跳间期从已有的 export.xml / 历史导入就能先回算 RMSSD；HRR60、Recovery Index 用已经在同步的原始心率就能算。

---

## 1. 现状：三条数据通道各拿到什么

| 通道 | 拿到的 | 拿不到的 |
|---|---|---|
| 快捷指令（测试用户现在靠它） | 14 类：心率、HRV（SDNN 数值）、静息心率、睡眠阶段、步数、活动 / 静息能量、锻炼分钟、步行心率、体重、手腕温度、血氧、呼吸、VO₂max | 没有体能训练类型、没有心跳序列、没有 ECG；只有数值和起止时间 |
| OW SDK 0.0.25（iOS SDK 0.15，Watch2Whoop app） | 读了 `open_wearables_health_sdk` 源码：步数 / 距离 / 爬楼 / 步行指标、能量、心率原始样本、静息心率、**SDNN**、VO₂max、血氧、呼吸、体重 / 体成分、体温、血糖、血压、饮食、睡眠、正念、生理期、跑步 / 骑行功率等、**训练**（统计值 + 分圈 / 分段事件，`route` 和 `samples` 固定传空）、workoutEffortScore / estimatedWorkoutEffortScore | **心跳序列、ECG、RMSSD（iOS 27）、手腕温度**、心率恢复、日照、体力消耗、步行稳定性、音频暴露、房颤负荷、呼吸紊乱、各种心律 / 心率事件、训练路线和 `workoutActivities` |
| export.xml（OW 导入 + 今天页「导入健康历史」） | 「健康」里的全部记录，包括每条 HRV 读数的逐拍数据（`<HeartRateVariabilityMetadataList>`）、`electrocardiograms/*.csv`、`workout-routes/*.gpx` | 只能手动导出，不是实时；我们的导入器现在**丢掉了逐拍数据和 ECG** |

**本机 export.xml 实测（2026-10-14 导出，Series 10 时代的数据为主）**：
- HRV 读数 2,002 条，其中 1,928 条带 ≥ 20 拍的逐拍数据（中位数 52 拍）；用逐拍时间重算 SDNN 和 Apple 的 SDNN 相关 **r = 0.90**（说明解析和去伪迹是对的），同一批读数的 RMSSD 中位数 32 ms（Apple SDNN 中位数 40 ms）。
- 夜间（23:00–09:00）HRV 读数：每晚 1 次 116 晚、2 次 227 晚、3 次 94 晚、4 次 3 晚——**换成 RMSSD 不会多出读数**。
- 夜间（02:00–07:00）心率样本中位间隔 **261 秒**（四分位 152–355 秒）；白天中位 8 秒（多为训练）。
- 训练结束后 Apple 仍按约 5 秒记录心率：2026-06 以来 30 次训练有 28 次能算「结束后 60 秒心率下降」。
- 有但我们没用的类型（条数）：physicalEffort 112,205、耳机音量 22,253、环境音量 15,078、日照时间 1,795、环境降噪 1,099、步行稳定性 202、音频暴露事件 25、手腕温度 21；ECG 1 条（512 Hz、Lead I、窦性心律）；训练路线 10 条（5.8 MB GPX）。
- **没有**：heartRateRecoveryOneMinute、房颤负荷、睡眠呼吸紊乱、effort score、心律 / 高低心率事件（功能没开或那时的手表 / 系统不写）。换 Series 12 / iOS 27 之后要用 app 真机再统计一次。

---

## 2. HealthKit 里还能读、现在没同步的类型

版本是 Apple 文档的起始版本（2026-10-18 核对 developer.apple.com 的文档数据）；用户的 iPhone / 手表是 iOS 27 / watchOS 27，下表全部可用。「只读」= 只有 Apple 能写，第三方只能读。

| 类型 | iOS / watchOS | 内容 | 谁写 / 多久一次 | 解锁 |
|---|---|---|---|---|
| **`HKHeartbeatSeriesSample`**（`HKSeriesType.heartbeat()`，`HKHeartbeatSeriesQuery`） | 13 / 6 | 每拍：距序列开始的秒数（双精度）+ `precededByGap` | 手表每次后台 HRV 测量、正念 / 呼吸练习时写（Apple 没写文档，开发者论坛经验 + 本机实测一致）；**训练中不写**；夜里 1–4 次 | 自算 **RMSSD**、pNN50、去伪迹后的「干净」HRV；历史可从 export.xml 回算 |
| **`heartRateVariabilityRMSSD`** | **27 / 27** | ms | 新类型，Apple 只给了声明；哪些表写、多久写一次**要真机实测**（第三方博客说 Series 12 写） | 直接用 Apple 的 RMSSD；和自算的对照，取更密的那个 |
| **`HKElectrocardiogram`**（`HKElectrocardiogramQuery` 取电压） | 14 / 7，**只读** | 采样率、电压序列（µV，Lead I 类）、平均心率、分类（窦性心律 / 房颤 / 心率过低或过高无法判断 / 读数差 / 其他）、症状 | 用户自己在手表「心电图」app 测；澳洲 TGA 2021 年批准，可用 | ECG 波形查看、历史、从波形算心率 / RR |
| `heartRateRecoveryOneMinute` | 16 / 9 | 次/分，峰值到停止后 1 分钟的下降 | 手表在部分训练后写 | 心肺恢复趋势（和自算的对照） |
| `workoutEffortScore` / `estimatedWorkoutEffortScore` | 18 / 11 | Apple 费力分 1–10 | OW SDK 已同步数值；`HKWorkoutEffortRelationshipQuery` 能拿到它属于哪次训练 | Apple Training Load（差距分析 P1） |
| `appleSleepingBreathingDisturbances`、`sleepApneaEvent` | 18 / 11 | 每晚呼吸紊乱次数 / 「升高」事件 | 要开睡眠呼吸暂停提醒（澳洲 2025-07 起可用） | 健康监测新增一项、和饮酒 / 体重 / 睡姿的关系 |
| **`hypertensionEvent`** | **26.2 / 26.2** | 高血压通知事件（只有事件，没有血压数值） | 澳洲 2026-01 起可用，**但 Apple 功能地区表注明 Series 12 / Ultra 4 在部分地区的额外批准还没下来**，用户的表可能收不到 | 能收到就展示事件历史；这是「高血压通知」能做到的全部 |
| `timeInDaylight` | 17 / 10 | 分钟，累计 | 手表环境光传感器 | 日照趋势、和入睡时间 / 作息的关系 |
| `atrialFibrillationBurden` | 16 / 9，只读 | % | 确诊房颤并开启「房颤史」才有，每周一次 | 给有需要的家人 |
| `appleSleepingWristTemperature` | 16 / 9，只读 | ℃，每晚一个 | 已由快捷指令同步；**OW SDK 0.15 不支持** | app 用户不再依赖快捷指令 |
| `physicalEffort` | 17 / 10 | MET，约每分钟 | 手表自动 | 更细的白天负荷、静止判定（压力分） |
| `appleWalkingSteadiness`（+ 事件） | 15 / 8，只读 | 0–1 | iPhone 约每周一次 | 长期趋势（家人老人更有用） |
| `environmentalAudioExposure`、`headphoneAudioExposure`、`environmentalSoundReduction`、`audioExposureEvent` | 13–16 | dBA | 手表 / AirPods | 听力健康（低优先级） |
| `irregularHeartRhythmEvent`、`highHeartRateEvent`、`lowHeartRateEvent`、`lowCardioFitnessEvent` | 12.2–14.3，只读 | 事件（高低心率带阈值，低心肺适能带 VO₂max） | 手表通知时写 | 健康监测里显示「手表提醒过」 |
| `appleStandTime`、`appleMoveTime`、`appleStandHour` | 13–14.5 | 分钟 / 小时 | 手表 | 活动圆环口径（低优先级） |
| 训练 `workoutActivities`（多段）、`allStatistics`、关联样本 | 16 / 9 | 每一段的类型、起止、统计 | OW 只传了 events | 间歇训练 / 铁三分段；分段心率 |
| `HKWorkoutRoute`（`HKWorkoutRouteQuery` → CLLocation） | 11 / 4 | GPS 轨迹 | 户外训练 | 路线地图、爬坡分析——**涉及位置，默认不读**（第 7 节） |
| Health Records（临床记录） | 只读、要单独 entitlement | — | **只支持美国、英国、加拿大，澳洲不支持** | 不做 |
| 睡眠分 / Readiness / Vitals 结论 | — | **HealthKit 没有这些类型** | — | 只能自己算（已经在算） |

**系统限制（影响所有类型）**：
- 锁屏时「健康」数据库加密，后台读不到（快捷指令读到空就是这个原因），解锁后补。
- 后台推送：iOS 上很多类型最多每小时一次；心跳序列 / ECG 能不能后台推送文档没写，**要实测**，不行就靠 BGTask 和打开 app 时补。处理完必须回调，连续 3 次不回调系统会停推。
- **iOS 27 新的授权方式**：用户可以只授权「最近一段时间」的数据，`getEarliestAuthorizedSampleDate(for:)` 能查到每类最早能读的日期，但分不清「全部授权」和「拒绝」。首次回溯和基线计算要用它判断，app 引导里提示「选全部历史」。

---

## 3. 第三方拿不到的（有 Developer 账号也不行）

| 数据 | 为什么拿不到 | 我们的替代 |
|---|---|---|
| **原始 PPG 波形**（光电容积脉搏波） | 只有 SensorKit 有（`SRSensor` 的 PPG / ECG 原始流），而 SensorKit 只给 **Apple 批准的研究项目**（要提交研究方案、IRB 伦理审批，Apple 审核后发 `com.apple.developer.sensorkit.reader.allow` entitlement），个人 / 家庭 app 申请不到；HealthKit 和 CoreMotion 都没有 PPG | 用 Apple 的心率、心跳序列、血氧结果 |
| **血压数值** | Apple Watch 不测血压数值；高血压通知是手表端用 PPG 做的 30 天模式识别，只输出「可能高血压」事件 | `hypertensionEvent`（能收到的话）+ 血压计录入的 `bloodPressure` |
| **Apple 的算法本身**（睡眠分期、HRV 测量时机、高血压、呼吸紊乱、心肺适能、睡眠分） | 在手表 / iPhone 上运行，不开放；我们只能读它们写进「健康」的结果；睡眠分、Vitals 结论连结果都不写 | 自己的恢复 / 睡眠 / 健康监测算法（已有） |
| **按需的连续 RR 间期** | 没有公开 API 让第三方实时拿逐拍；训练会话里也只有心率（约每秒到每 5 秒），没有心跳序列 | 心跳序列（Apple 定时测）+ 自己算 |
| **夜间更多 HRV 读数** | 测量时机由 Apple 决定；第三方不能触发 | 只能多做正念 / 呼吸（白天有用，夜里不现实）；靠 RMSSD 和更稳的统计（中位数）减噪 |
| **Oura 式心血管年龄**（脉搏波速度） | 要 PPG 波形 | 心肺年龄估算（第 5 节），标「估算」 |
| 临床记录 | 澳洲不支持 | 化验继续手动 / 截图录入（已有） |

---

## 4. watchOS 配套 app 能额外拿到什么

前提：手表上的 HealthKit 只是 iPhone 的一个子集，旧数据会定期从手表清掉（保留多久 Apple 没写）。**历史和日常同步一律在 iPhone 上读**；watch app 只做 iPhone 读不到的：手表传感器的实时 / 后台记录、在手表上震动。

| 能力 | 给什么 | 版本 / 配置 | 耗电 | 审核风险 | 能做出的效果 | 结论 |
|---|---|---|---|---|---|---|
| **HKWorkoutSession + HKLiveWorkoutBuilder** | 训练期间的高频心率（官方只说「高频」，论坛观察约 5 秒一次）、实时能量、2026 年新的训练心率区间 API（`HKWorkoutZoneGroup`）；**没有逐拍 RR** | watchOS 5+；`WKBackgroundModes` 加 `workout-processing`；界面必须明确显示「训练中」并能保存 / 丢弃 | 高（和系统「体能训练」app 一样） | 真训练：无；**拿来整夜保活：高**（2.5.4，DTS 2026-03 明确说会被拒） | 系统「体能训练」app 本来就产生同样的高频心率，OW SDK 已同步；自己开训练的增量只在于能和 CMBatchedSensorManager 配合（下一行） | 只在用户在 watch app 里主动开始的训练里用；**不做整夜训练会话** |
| **CMBatchedSensorManager** | 800 Hz 加速度、200 Hz 设备姿态（每秒一批） | watchOS 10+；**只在活跃训练会话里** | 高（训练期间） | 同上（真训练里无） | 力量训练自动计次 / 组间休息、挥臂次数（篮球、游泳）；**和你的力量训练记录（SparkyFitness 每组次数 × 重量）可以互相校对** | P2，收益看你是否愿意在手表上开「力量训练」 |
| **CMSensorRecorder** | 50 Hz 三轴加速度，单次最长 12 小时，app 被挂起 / 杀掉也继续录，系统保留 3 天；没有陀螺仪 | watchOS 2+；`NSMotionUsageDescription` | 官方没数字；由系统录、app 不常驻，一般认为低。**要真机测**一晚掉电 | 低（正当用途：睡眠 / 活动分析） | 每 30 秒活动量 → 夜间翻身 / 醒来、入睡起床时刻校正、白天精确「静止」（压力分）。50 Hz × 8 小时 ≈ 144 万行 × 3 轴，**只在手表上提特征**，原始数据不出手表 | P2，夜间版先做 |
| **WKExtendedRuntimeSession（smart alarm）** | 在预约时间开始的后台会话，**最长 30 分钟**；会话里必须调 `notifyUser(hapticType:)` 震动；能读心率（论坛报告）；会话里能不能实时读 CoreMotion **没有资料，要真机测**（不行就读 CMSensorRecorder 已录的数据，可能有几分钟延迟） | watchOS 6+；`WKBackgroundModes` 加 `alarm`；**只能在 app 前台时用 `start(at:)` 预约**，最远 36 小时、同时只能一个 | 低（30 分钟） | 低（按用途使用） | **智能闹钟**：最晚起床时间 T 前 30 分钟开会话，检测到翻身 / 心率上升（浅睡或已醒的标志）就震动，否则 T 准时震。只能震动；**每晚睡前要在手表上点一下「设闹钟」**（或白天预约次日，36 小时内有效） | **P1** |
| WKExtendedRuntimeSession（self care / mindfulness / physical therapy） | 前台 10 分钟 / 1 小时，后台 1 小时 | 同上，每个 app 只能选**一种**类型 | 中 | 按用途选，不能借用 | 已经选了 smart alarm 就不能再用其他类型 | 不用 |
| 手表直接联网上传 | watchOS 6+ 独立网络，background URLSession；后台刷新每小时不到 4 次（没有表盘复杂功能时更少） | — | — | — | 不需要：手表的夜间特征经 WatchConnectivity `transferFile` 交给 iPhone，由 RawSync 一起上传；手表上不存服务器 token | 不做 |
| SensorKit（PPG / ECG 原始流、心率高频） | 只在 iPhone 上、只给批准的研究项目 | — | — | 拿不到 entitlement | — | 不做 |

**已知问题（和你的设备直接相关）**：开发者论坛 FB24834050 报告 watchOS 27.0 / 27.0.1 上第三方训练只录到约 1 个心率样本，提到 Series 12 也有，Apple 还没回复。做训练相关功能前先在你的表上实测，可能要等 watchOS 27.x 修复。

---

## 5. 新数据 → 新功能和算法

全部放在 Body Engine 新模块 `body_engine/src/body_engine/rawdata/`（导入、存储）和 `body_engine/src/body_engine/cardio/`（算法），不改今天页现有文件；界面只在确认后加一张卡 / 一个弹层（和 whoop 会话错开）。

| 功能 | 用到的数据 | 算法要点 | 效果 / 和现在比 | 需要 watch app？ |
|---|---|---|---|---|
| **RMSSD 恢复分** | ① Apple 的 `heartRateVariabilityRMSSD`（iOS 27）；② 心跳间期序列（每次 HRV 读数约 1 分钟，历史也有） | 两者都存；评分用哪一个看实测：哪个每晚读数多、和次日主观感受 / 睡眠分更一致就用哪个，定了就不混用。② 的算法：逐拍时间 → RR；去掉 `precededByGap` 的间隔和偏离中位数 > 20–30% 的异常拍（异位搏动 / 运动伪迹）；每次读数 RMSSD = √mean(ΔRR²)，≥ 30 个有效间隔才用；当晚 = 睡眠窗口内 ln RMSSD 中位数。`hrv_kind = rmssd`，升 `ALGO_VERSION`，整段重算，基线自然重建（不和 SDNN 混用，系统设计第 7 节已定） | WHOOP 用 RMSSD；SDNN 对呼吸和慢波更敏感。**每晚读数次数不变（实测 1–4 次）**，单条读数吵的问题只能缓解（RMSSD 对短窗更稳），不能根治 | 否 |
| **ECG 波形** | HKElectrocardiogram + 电压（512 Hz × 30 秒 ≈ 15,360 点） | 存压缩后的 int16 数组；Pan-Tompkins 找 R 峰 → 平均心率、RR、RMSSD（30 秒窗口，静坐时测，比夜间读数干净）；**分类直接用 Apple 的**，不自己判读 | 今天页「记录」里看历史 ECG 列表 + 波形（25 mm/s 网格，可左右滑）、Apple 分类、症状、导出 PDF 给 GP | 否 |
| **Recovery Index**（Oura） | 夜间原始心率（已有，实测中位间隔 4.4 分钟） | 睡眠段内 5 分钟滚动均值的最低点出现在睡眠的第几个百分位 / 入睡后多少分钟；前半夜到底 = 好 | 现有数据就能做（分辨率约 5 分钟）；watch app 的高频心率能更准，但不值得为它整夜开训练会话 | 否 |
| **运动后心率恢复（HRR60）** | ① Apple 的 `heartRateRecoveryOneMinute`（有就用）；② 训练结束前后的原始心率（实测训练后 Apple 仍按约 5 秒记录，近 30 次训练 28 次能算） | ② = 结束前 2 分钟峰值 − 结束后 60 秒心率；只在峰值 ≥ 最大心率 75% 时算（否则是「慢慢走完」，没有意义）；长期看 30 天均值趋势 | Apple 的值只在部分训练后写；自己算覆盖所有训练（篮球、力量、爬坡） | 否；有 watch app 后训练结束时可以提醒「站着别动 1 分钟」做标准化测量 |
| **更准的睡眠** | 加速度（CMSensorRecorder 50 Hz，手表上提取每 30 秒的活动量）、夜间心率、Apple 睡眠阶段 | 以 Apple 睡眠阶段为主；加速度只用来：① 夜里醒来 / 翻身次数（Restfulness，Oura 口径）；② 校正入睡 / 起床时刻（Apple 有时晚记几十分钟）；③ 没戴表时识别空白 | 睡眠时长、效率、醒来次数更可信；不自己重做分期（Apple 的分期用了 PPG + 加速度，已经比纯加速度好） | **是**（P2） |
| **更准的压力** | 白天活动量（加速度 / 步数）、心率、白天 HRV 读数（Apple 的「正念」读数也带心跳序列） | 现在用「前 5 分钟步数 < 20」判断静止；有了每分钟活动量可以精确排除运动、抬手等，压力分的误报下降；白天 HRV 用 RMSSD | 压力分更干净；第二步可以做 Body Battery 近似（仍是近似） | 步数版不需要；精确版要（P2） |
| **智能闹钟** | 手表上的加速度 + 心率（闹钟会话期间；CMSensorRecorder 整夜在录） | 设定最晚起床时间 T（窗口固定 ≤ 30 分钟，系统上限）；会话在 T − 30 分钟开始，每分钟算活动量和心率变化，检测到「翻身 / 心率抬升」（浅睡或已醒的标志）立刻震动叫醒，否则 T 准时震；前半段的数据用 CMSensorRecorder 已录好的那部分判断「最近是否在深睡」 | 和 WHOOP / Garmin 的智能闹钟体验接近；只能震动；**睡前要在手表上点一下预约**（系统规定只能在 app 前台时预约，最远 36 小时）；可以加「每天同一时间」的快捷入口 | **是**（P1） |
| **心血管年龄（估算）** | VO₂max、静息心率、RMSSD、HRR60、年龄、性别、（有的话）血压 | 不能测脉搏波速度，只能用公开常模做「心肺年龄」：VO₂max 对照 FRIEND / HUNT 常模反推年龄为主（权重最大），静息心率、HRV、HRR 各自按年龄常模微调；界面写「估算」，并和现有「身体年龄」合并或并列（待定） | 和 Oura 的 Cardiovascular Age 原理不同（Oura 用 PPG 脉搏波速度）；只能当趋势看 | 否 |
| **血压** | 只有用户自己录入 / 血压计写进「健康」的数值；高血压通知事件（如果 HealthKit 给，见第 2 节） | 不做任何从手表推算血压的功能（不可靠，也有医疗器械风险） | 有血压计的家人可以看趋势、和化验一起看 | 否 |
| 睡眠呼吸紊乱、日照、房颤负荷、体力消耗、步行稳定性、音频暴露 | Apple 写进「健康」的结果 | 直接展示趋势；呼吸紊乱「升高」进健康监测；日照进作息分析（作息不稳定、常 2 点睡）；房颤负荷只有确诊房颤并开启「房颤史」才有 | 补齐差距分析里 P1 的「日照时间」等 | 否 |

### 算法的出处和验证
- RMSSD、异位搏动过滤：Task Force of ESC/NASPE 1996 HRV 标准；Apple 的 SDNN 用同一份逐拍数据（我们重算 SDNN 和 Apple 相关 0.90，用来验证去伪迹的写法）。
- Recovery Index：Oura 公开说明（夜间心率最低点出现得越早越好）。
- HRR60：Cole 等 1999（NEJM，运动后 1 分钟心率下降 ≤ 12 bpm 为异常）；我们只看个人趋势，不做诊断。
- 心肺年龄：Nes 等（HUNT「Fitness Age」，VO₂max → 年龄）、FRIEND 常模；身体年龄模块（`insights/healthspan.py`）已有部分常模，复用。
- 智能闹钟：基于活动量的睡眠 / 觉醒判别（Cole-Kripke / 改进的 actigraphy 计数）+ 心率上升。

---

## 6. 实现路径

### 6.1 iOS：原生插件 vs 扩展 OW fork

| | **A. 自己的原生 Swift 插件 RawSync（推荐）** | B. 扩展 OW fork（iOS SDK + OW 后端 + Body Engine 导入） |
|---|---|---|
| 要改的仓库 | `apps/watch2whoop/ios/Runner/RawSync/*.swift` + Body Engine 一个接口 | OW iOS SDK（另一个仓库，CocoaPods 发布）、OW 后端（Python，表结构 / 序列类型）、OW Flutter SDK、Body Engine 的 OW 导入器 |
| 心跳序列 / ECG / 路线这种「序列」数据 | 自己定格式，直接进 Body Engine 的表 | OW 的数据模型是「时间点 + 数值」的 timeseries，没有放逐拍序列和 15k 点波形的地方，要改 OW 的表和接口 |
| 升级 | 和 OW 解耦，OW / SDK 升级不受影响 | 每次 OW / SDK 升级都要 rebase fork（项目约定是 fork 只在确实必要时才改） |
| 回馈开源 | 可以单独开源（Watch2Whoop 的一部分） | 能回馈上游，但上游未必收 |
| 数据位置 | 新数据只在 Body Engine（OW 不重复存） | OW 多存一份（Ali 磁盘紧张） |
| 风险 | 两个 HealthKit 读取方并存（各自 anchor，互不影响；HealthKit 允许一个 app 内多处查询） | 改动面大、要维护三个 fork |

**推荐 A**：OW SDK 继续同步它已有的类型（心率、睡眠、训练…，评分管道不变）；RawSync 只读 OW 没有的类型，避免重复。等以后 OW SDK 支持了某个类型（比如手腕温度），在 RawSync 里关掉它即可。

### 6.2 RawSync 插件（Swift，`apps/watch2whoop/ios/Runner/RawSync/`）
- **类型清单**写在一个表里（类型、查询方式、首次回溯天数、批大小）：心跳序列（HKHeartbeatSeriesQuery 取逐拍）、ECG（HKElectrocardiogramQuery 取电压）、Apple RMSSD、手腕温度、高血压 / 呼吸紊乱 / 心律事件和第 2 节其余的 quantity / category 类型、训练的 activities 和 effort 关联（`HKWorkoutEffortRelationshipQuery`）。路线默认不读（第 7 节）。OW SDK 以后支持了某一类，就从清单里删掉。
- **iOS 27 的授权窗口**：每类先查 `getEarliestAuthorizedSampleDate(for:)`，回溯起点取它和 90 天里较晚的那个，并把「最早能读的日期」报给服务端；窗口之外按「未知」处理（基线不把它当成没有数据）。
- **增量**：每类一个 `HKAnchoredObjectQuery` anchor（存 Keychain 之外的 app 容器，删除 app 时一起删）；删除的样本（`deletedObjects`）也上报，服务端软删。
- **后台**：每类 `enableBackgroundDelivery`（频率按 Apple 允许的上限，大多是 hourly）+ 复用 app 已有的 BGTask processing 窗口；上传用 background `URLSession`（锁屏后也能把已经读出来的批次传完）。锁屏时读不到「健康」是系统限制，解锁后补。
- **批次**：每批 ≤ 1 MB（gzip 后），带 `batch_id`（UUID）和每条样本的 HealthKit `uuid`；服务端按 (user_id, hk_uuid) 幂等，同一批重传不重复。
- **鉴权**：新 scope `ingest` 的设备 token（只能调 `/v1/ingest/*`，不能读任何数据）。获取方式：今天页在 app 内登录后，经已有的 JS 通道 `window.Watch2Whoop` 调 `POST /v1/web/ingest-token`（cookie 会话 + `X-Body-Web: 1`）换一个设备 token，交给原生存进 Keychain；退出登录 / 换账号时吊销。不需要家人多扫一个码。
- **同意**：member 没同意前服务端照常收但标 `pending`、不处理（和 OW 同步的现有规则一致，同意书第 2 条）。
- **同步状态页**：现有原生「手机同步」页加一节「原始数据」：各类型条数、上次上传、失败原因（Flutter 侧 `pages/` 一个小组件，走 `watch2whoop/native` 通道）。
- **测试**：XCTest 测解析和分批；模拟器里调试版已有 `DebugHealthSeeder`，加心跳序列 / ECG 的模拟数据（模拟器能写 HKHeartbeatSeriesBuilder，ECG 第三方不能写——ECG 只能真机测）。

### 6.3 Body Engine（新模块 `rawdata/`、`cardio/`，迁移 150）

```sql
-- 150_raw_healthkit.sql（草案）
CREATE TABLE hk_ingest_batches (          -- 每批一行：审计 + 幂等
  user_id INT REFERENCES users(id) ON DELETE CASCADE, batch_id UUID, received_at TIMESTAMPTZ DEFAULT now(),
  device TEXT, app_version TEXT, counts JSONB, status TEXT,   -- pending（未同意）/ done / failed
  PRIMARY KEY (user_id, batch_id));
CREATE TABLE hk_samples (                 -- 标量类型：quantity / category / 事件
  user_id INT REFERENCES users(id) ON DELETE CASCADE, hk_uuid UUID, type TEXT,
  start_at TIMESTAMPTZ, end_at TIMESTAMPTZ, value DOUBLE PRECISION, value_text TEXT, unit TEXT,
  source TEXT, device TEXT, metadata JSONB, deleted_at TIMESTAMPTZ,
  PRIMARY KEY (user_id, hk_uuid));        -- 加 (user_id, type, start_at) 索引
CREATE TABLE heartbeat_series (           -- 每次 HRV 读数一行
  user_id INT REFERENCES users(id) ON DELETE CASCADE, hk_uuid UUID, start_at TIMESTAMPTZ, end_at TIMESTAMPTZ,
  beats_ms INT[],                         -- 距序列开始的毫秒
  gaps BOOLEAN[],                         -- precededByGap
  source TEXT, rmssd DOUBLE PRECISION, sdnn DOUBLE PRECISION, n_valid INT,   -- 入库时算好
  origin TEXT,                            -- app / apple_export
  PRIMARY KEY (user_id, hk_uuid));
CREATE TABLE ecg_recordings (
  user_id INT REFERENCES users(id) ON DELETE CASCADE, hk_uuid UUID, start_at TIMESTAMPTZ,
  classification TEXT, symptoms_status TEXT, avg_hr DOUBLE PRECISION, sample_hz DOUBLE PRECISION,
  voltage_uv BYTEA,                       -- int16 little-endian（0.1 µV 精度不需要，1 µV 足够）再 zstd / gzip 压缩
  derived JSONB,                          -- R 峰、RR、RMSSD、质量
  PRIMARY KEY (user_id, hk_uuid));
CREATE TABLE workout_details (            -- 训练的 activities / events / effort，挂到 workouts
  user_id INT, hk_uuid UUID, workout_id BIGINT REFERENCES workouts(id) ON DELETE SET NULL,
  activities JSONB, events JSONB, effort DOUBLE PRECISION, effort_estimated DOUBLE PRECISION,
  hrr60 DOUBLE PRECISION, hrr60_source TEXT, PRIMARY KEY (user_id, hk_uuid));
GRANT SELECT ON hk_samples, heartbeat_series, workout_details TO grafana_ro;   -- ECG 不给 Grafana
```

- **接口**：`POST /v1/ingest/healthkit`（scope `ingest`，gzip JSON，≤ 1.5 MB，过公网现有 `/v1/` location 的 2 MB 上限；限流复用 `w2w_api`）；`GET /v1/users/{user}/ecg`、`/ecg/{id}`（波形，scope 新增 `ecg`，默认不随 shares 共享）、`/cardio?date=`（HRR、Recovery Index、心肺年龄）。
- **导入**：接口只做校验 + 落库（快）；派生计算（RMSSD、ECG R 峰、HRR）在入库时同步做（都是毫秒级）；评分用的日汇总由每小时的 `compute-scores` 读新表。
- **历史回算（P0，不需要 app）**：`importers/apple_zip.py` / `apple_export.py` 解析 HRV 记录时顺带读 `<HeartRateVariabilityMetadataList>`，写 `heartbeat_series`（origin = apple_export；export.xml 的拍时间只有 10 ms 精度、bpm 取整，RMSSD 有约 ±1–2 ms 的量化误差，app 直读的是双精度秒，更准；同一读数两边都有时 app 优先）；`electrocardiograms/*.csv` 写 `ecg_recordings`。新用户的「导入健康历史」顺带受益。
- **评分切 RMSSD**：`scores/inputs.py` 的 HRV 来源加 `heartbeat_series`；`params.HRV_KIND = 'rmssd'`、升 `ALGO_VERSION`、`compute-scores --all` 重算；切之前先出一份对比报告（同一段时间 SDNN 版 vs RMSSD 版恢复分的分布、和睡眠分 / 主观感受日记的相关），用户确认再切。
- **测试**：`tests/test_rawdata.py`（幂等、去伪迹、跨用户隔离、未同意不处理）、`tests/test_cardio.py`（合成 RR 序列、合成 ECG 找 R 峰、HRR 边界）。

### 6.4 watchOS 配套 app（P1：智能闹钟；P2：加速度记录）
- **放哪里**：`apps/watch2whoop/ios/` 里加一个 watchOS target「Watch2Whoop Watch」（Xcode → File → New → Target → watchOS → App，选「Watch App for Existing iOS App」，单 target 的 SwiftUI watch app）。Flutter 只管 iOS Runner；watch target 是纯 Swift，`flutter build ipa` / Xcode Archive 会把内嵌的 watch app 一起打包（Runner 的 Embed Watch Content 构建阶段）。默认是「依赖型」（iPhone 上装了 app 手表上才能用），不勾「Supports Running Without iOS App Installation」。
- **Flutter 工程的已知坑**（社区经验，加 target 时一次处理好、提交进仓库，朋友不用自己改）：Runner 的 Build Phases 里「Embed Watch Content」要挪到「Thin Binary」前面，否则报依赖循环；向导建的 watch target 的 Supported Platforms 可能是空的，要填 watchOS；归档校验可能报缺 `WKApplication` 键，要在 watch 的 Info.plist 里加；改完 target 重跑 `pod install`（Podfile 里 watch target 不需要任何 pod）。
- **代码**：SwiftUI 界面（闹钟设置、开关「夜间记录」）、`SmartAlarm.swift`（WKExtendedRuntimeSession）、`MotionRecorder.swift`（CMSensorRecorder 申请记录、早上取数据提特征）、和手机通信用 WatchConnectivity（`transferFile` 把夜间特征文件交给 iPhone，由 RawSync 一起上传；手表不直接连服务器，不在手表上存 token）。
- **朋友打包时多出来的步骤**（README 中英文都要加）：
  1. 什么都不用多填：watch 的 Bundle ID 在仓库的 xcconfig 里写成 `$(W2W_BUNDLE_ID).watchkitapp`，自动跟着 iOS 的走（惯例是 iOS Bundle ID 作前缀；Apple 现行文档没有硬性规定这个后缀）。
  2. Xcode → **watch target** → Signing & Capabilities：勾自动签名，Team 应该已经是你的（同样来自 xcconfig）；确认能看到 **HealthKit** 和 **Background Modes → Alarm**（智能闹钟需要），来自仓库，不用自己加。第一次会提示注册 watch 的 App ID（带 HealthKit），点 Register / Try Again。
  3. App Store Connect 不用新建 app（watch app 跟 iOS app 走同一个记录）；TestFlight 构建里会多一个 watch app，测试员在手表的 App Store「iPhone 上的 App」里安装（或 Watch app → 自动安装）。
  4. Xcode 需要装 watchOS 平台（Xcode → Settings → Components 里下载）；构建时间多约 1–2 分钟。
  5. 上传前在 Organizer 里点「Validate App」，watch 部分报错时把错误截图发回来（多半是上面的 Info.plist / 平台设置）。
- **审核风险**：TestFlight 外部测试要过 Beta App Review。智能闹钟用的是专门的 smart alarm 会话类型，正当；**不要**为了常驻用训练会话（HKWorkoutSession）跑整夜——审核和耗电都有问题（第 4 节）。

---

## 7. 隐私、同意书、存储和保留

### 7.1 同意书要改（`docs/family-consent.md` 和 `profile/consent.md` 一起改，升版本号，已同意的人重新确认）
- 第 1 条「收集哪些数据」加：**心电图（ECG）波形和 Apple 的分类结果**、**心跳间期（逐拍时间）**、Apple 写入「健康」的心律 / 高血压通知、房颤负荷、睡眠呼吸紊乱等结果、（装了手表 app 才有）手表加速度的夜间统计特征（不上传原始加速度）。
- 「不收集」继续写**位置**：训练路线（GPS）默认**不读**；以后要做路线时单独开关、单独同意。
- 第 4 条「谁能看到」加：ECG 默认**不进共享**，要单独授权；AI 助手默认拿不到 ECG 波形（只有分类和心率）。
- 第 6 条风险说明加：ECG 和心律相关内容**只是展示 Apple 的结果，不是诊断**；Apple 的 ECG 不能发现心脏病发作。

### 7.2 体积估算（Ali 剩约 6 GB）

| 数据 | 每人每天 | 每人每年 | 5 人 3 年 | 保留策略 |
|---|---|---|---|---|
| 心跳序列（约 5 次 × 52 拍，int 数组） | ~3 KB | ~1 MB | ~15 MB | 永久 |
| ECG（30 秒 × 512 Hz，int16 + 压缩 ≈ 20–25 KB/条；用户自己测，估每月 ≤ 10 条） | — | ≤ 3 MB | ≤ 45 MB | 永久（用户可删单条） |
| 新标量类型（physicalEffort 等，physicalEffort 约 270 条/天最多） | ~50 KB | ~18 MB | ~270 MB | 原始样本 400 天后汇总成每日值再删（和 shortcut_samples 一起定） |
| 夜间加速度特征（每 30 秒 6 个数，≈ 1,000 行/晚） | ~20 KB | ~7 MB | ~110 MB | 原始特征 180 天，之后只留每晚汇总 |
| ~~原始加速度~~（50 Hz × 3 轴 × 8 小时 ≈ 50 MB/晚） | — | — | — | **不上传**，只在手表上提特征 |
| ~~训练路线~~（实测 10 条 GPX 5.8 MB） | — | — | — | 默认不读（位置） |

合计 5 人 3 年 < 0.5 GB，加索引和 WAL 也在 1 GB 内；Ali 的 doctor 已有磁盘告警，新表加进 `ops.py` 的备份（pg_dump 本来就包含整库）。

---

## 8. 优先级、工作量、风险

| 级别 | 内容 | 工作量 | 风险 / 依赖 |
|---|---|---|---|
| **P0** | ① export.xml / 历史导入回算心跳序列 → RMSSD（`heartbeat_series`、导入器扩展）；② SDNN vs RMSSD 对比报告，用户确认后切评分；③ 导入已有 ECG csv；④ 用现有原始心率做 **HRR60** 和 **Recovery Index**（`cardio/`，只出接口 + 管家工具，界面另说） | 2–3 天 | 不需要 app、不需要 TestFlight；风险低（切评分前有对比报告） |
| **P0** | ⑤ RawSync 插件 + `/v1/ingest/healthkit` + 迁移 150 + 同意书改版：心跳序列、ECG、**Apple RMSSD**、**手腕温度**（app 用户不再靠快捷指令）、高血压 / 呼吸紊乱 / 心律事件、第 2 节其余标量类型、训练 effort / activities | 4–6 天（插件 2–3、服务端 1–2、真机联调 1） | **依赖 TestFlight**（朋友打包、真机验证后台推送和锁屏）；ECG、Apple RMSSD 的写入频率只能真机测；新类型会在 app 更新后弹一次「健康」授权，要用户自己点「全部打开」；同意书改版要家人重新确认 |
| **P1** | ⑥ 今天页：ECG 列表 + 波形弹层、「心肺」卡（HRR60、Recovery Index、心肺年龄估算）；⑦ 心肺年龄估算算法；⑧ 睡眠呼吸紊乱 / 日照 / 房颤负荷 / 体力消耗进趋势和健康监测 | 3–4 天 | 要和 whoop 会话的趋势页改动错开（建议它合并后再做） |
| **P1** | ⑨ watchOS app 第一版：**智能闹钟**（+ 手表上设置界面、手机上的闹钟设置同步） | 4–5 天 | 打包步骤变多；需要用户真机长期试（叫醒时机是否合适）；审核风险低 |
| **P2** | ⑩ 手表夜间加速度（CMSensorRecorder）→ Restfulness、入睡 / 起床校正；⑪ 精确静止判定的压力分、Body Battery 近似；⑫ 训练时高频心率 / 800 Hz 加速度（只对自己在 watch app 里开始的训练，例如力量训练计次） | 5–8 天 | 耗电要实测；从手表取 12 小时数据和提特征的耗时；⑫ 受 watchOS 27 训练心率 bug 影响（第 4 节）；收益中等 |
| 不做 | PPG 波形、血压推算、自己做 ECG / 心律诊断、整夜训练会话、上传原始加速度 | — | 拿不到 / 医疗风险 / 审核风险 / 耗电 |

---

## 9. 用户的决定（2026-10-18）和进度
用户原话「五个问题全按照推荐来」：① 路径 A（RawSync 原生插件，不改 OW fork）；② P0 ①–④ 先做，切 RMSSD 前先看对比报告；③ 同意书改版（加 ECG、心跳间期，路线不收集），已同意的家人重新确认；④ watchOS 智能闹钟放 P1；⑤ 心血管年龄只做估算，和身体年龄**并列**。

| 项 | 状态 |
|---|---|
| P0 ① 心跳序列 → RMSSD（`rawdata/`、迁移 150、`import-raw`、`raw-slim`，历史导入自动带上） | ✅ 2026-10-18 |
| P0 ② SDNN vs RMSSD 对比（`hrv-compare`） | ✅ 命令完成；本机 export.xml（到 2026-10-14）的报告已交中控，Ali 上全量数据的报告待部署后跑；**评分仍是 SDNN，等用户确认再切** |
| P0 ③ ECG 导入、接口（scope `ecg`）、管家摘要工具 | ✅ |
| P0 ④ HRR60（`cardio/hrr.py`，`workout_hrr`）、Recovery Index 展示（`/cardio`，算法沿用评分引擎的 `rhr_low_offset_min`） | ✅ 本机实测 40 次训练 9 次够强度可算，均值 23 bpm |
| P0 ⑤ RawSync + ingest 接口 + 同意书改版 | 进行中 |
| P1 今天页卡片、心肺年龄估算（和身体年龄并列）、watchOS 智能闹钟 | 未开始 |

## 9a. 原来要你确认的（已确认，保留原文）
1. 推荐路径 A（自己的原生插件 RawSync，不改 OW fork），可以吗？
2. P0 ①–④（只用已有数据：RMSSD 回算 + 对比报告、ECG 导入、HRR60、Recovery Index）先做，可以吗？切评分到 RMSSD 要等你看过对比报告。
3. P0 ⑤ 的同意书改版（加 ECG、心跳间期；路线继续不收集）可以吗？改完家人要重新确认一次。
4. watchOS 配套 app（智能闹钟）放 P1，可以吗？朋友打包时要多做第 6.4 节的几步。
5. 心血管年龄只做「估算」并标明，和现有「身体年龄」并列还是合并？

---

## 来源（2026-10-18 核对）
Apple 文档前缀 `https://developer.apple.com/documentation/`（页面是 JS 渲染的，核对用的是同路径的 JSON：`https://developer.apple.com/tutorials/data/documentation/<路径>.json`，带版本信息）。
- HealthKit 类型：`healthkit/hkheartbeatseriessample`、`healthkit/hkheartbeatseriesquery`、`healthkit/hkelectrocardiogram`、`healthkit/hkelectrocardiogram/classification-swift.enum`、`healthkit/hkquantitytypeidentifier/heartratevariabilityrmssd`、`healthkit/hkcategorytypeidentifier/hypertensionevent`、`healthkit/hkcategorytypeidentifier/sleepapneaevent`、`healthkit/hkquantitytypeidentifier/applesleepingbreathingdisturbances`、`healthkit/hkworkoutactivity`、`healthkit/reading-route-data`、`healthkit/hkhealthstore/getearliestauthorizedsampledate(for:completion:)`、`healthkit/hkhealthstore/enablebackgrounddelivery(for:frequency:withcompletion:)`、`healthkit/protecting-user-privacy`、`healthkit/accessing-health-records`、`updates/healthkit`
- watchOS / CoreMotion：`healthkit/running-workout-sessions`、`coremotion/cmsensorrecorder`、`updates/coremotion`、`watchkit/using-extended-runtime-sessions`、`watchkit/wkextendedruntimesession/start(at:)`、`bundleresources/information-property-list/wkbackgroundmodes`、`watchos-apps/setting-up-a-watchos-project`、`watchos-apps/keeping-your-watchos-app-s-content-up-to-date`
- SensorKit：`sensorkit`、`sensorkit/srsensor/photoplethysmogram`、`sensorkit/configuring-your-project-for-sensor-reading`
- 开发者论坛：心跳序列写入时机 https://developer.apple.com/forums/thread/746218 ；训练会话保活被拒、健康 entitlement https://developer.apple.com/forums/thread/816544 ；watchOS 27 训练心率 bug https://developer.apple.com/forums/thread/849181 ；smart alarm 会话 https://developer.apple.com/forums/thread/772957 ；睡眠分没有 API https://developer.apple.com/forums/thread/799857
- 功能地区：https://www.apple.com/watchos/feature-availability/ （ECG、睡眠呼吸暂停、高血压通知的澳洲可用性和 Series 12 脚注）、https://www.apple.com/ios/feature-availability/ （健康记录只有美英加）、https://support.apple.com/en-au/117296 （高血压通知）、https://www1.racgp.org.au/newsgp/professional/apple-watch-hypertension-alert-to-prompt-more-gp-c 、https://eftm.com/2025/07/sleep-apnea-notifications-come-to-aussie-apple-watch-owners-from-today-264528
- 本机实测：`health_data/full_export/apple_health_export/`（2026-10-14 导出）的 export.xml、`electrocardiograms/`、`workout-routes/`；脚本只在会话临时目录跑过，没有进仓库（P0 实现时写成正式的导入器和测试）。
- 标 **unverified / 要实测** 的：心跳序列的写入时机、Apple RMSSD 在 Series 12 上的频率、心跳序列 / ECG 能否后台推送、`hypertensionEvent` 是否只读和取值、smart alarm 会话里能否实时读 CoreMotion、CMSensorRecorder 的耗电、训练会话心率间隔。
