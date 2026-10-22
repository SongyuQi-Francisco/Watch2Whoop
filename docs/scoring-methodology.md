# 评分方法论（中文版）

> 对应 issue #118。英文版是评分库的 `METHODOLOGY.md`（#115 / #123 合并以后放在 `packages/openbodyscore/METHODOLOGY.md`，
> 合并之前暂放在 [scoring-methodology.en.md](scoring-methodology.en.md)）。验证实验的方案和结果见 [scoring-validation.md](scoring-validation.md)。
> 本文只描述代码现在的做法（`ALGO_VERSION = v1.1-2026-10-16`，身体年龄 `hs-v1.1-2026-10-17`，压力 `v1.0-2026-10-17`，
> HRV 去伪迹 `rr-v1`）。**这些分数是 WHOOP 式的健康参考，不是医疗诊断，与 WHOOP 公司无关。**

## 0. 怎么读这份文档

### 0.1 参数的四种来源
每个参数都标成下面四类中的一类。**有多少依据写多少**：没验证过的写「第一版，未验证」。

| 类别 | 含义 |
|---|---|
| **文献** | 数值直接来自发表的研究或标准（文末参考文献，DOI 已在 Crossref 核实，2026-10-22）。文献只支持「这个数是怎么来的」，**不等于这个分数整体被验证过** |
| **移植的规格** | 照搬另一个开源实现的规格（目前只有 Open Wearables 0.9.0 的睡眠分，MIT 许可） |
| **设计选择** | 我们定的第一版取值：方向有依据或常识，具体数值没有用数据拟合过 |
| **个人校准** | 用一个人（项目负责人）的数据定的数值。不符合「不写死某一个人的数据」的规定，要换掉（#118 校准一节） |

### 0.2 通用约定
- **日期**都是用户本地日期（`users.timezone`）。一段睡眠归到「醒来时间 − 4 小时」那天（醒来日期）。
- **个人基线**：除非另说，都是**当天之前**（不含当天）60 天内的值，每个人、每种指标各自一套。
- **缺数据**：算不出的分数留空，不补 0、不插值；原因写在 `daily_scores.flags`（代码 → 中文说明），中间量写在 `details`。
  「没记录 ≠ 没做」。
- **整段重算**：每次从评分起点（第一天有静息心率 = 开始戴表）算到今天，结果只取决于数据本身；改了任何影响结果的参数都要升
  `ALGO_VERSION` 并整段重算，同一个人的历史不混用两个版本。
- 所有阈值都在 `*/params.py`（睡眠 / 恢复 / 负荷在 `scores/params.py`，身体年龄在 `insights/healthspan_params.py`，
  压力在 `insights/stress.py` 顶部，HRV 去伪迹在 `rawdata/hrv.py` 顶部），可以按调用覆盖。

### 0.3 输入从哪来
Apple Watch 写进 HealthKit 的原始样本（经 iOS app / Open Wearables、快捷指令或「健康」全量导出进来）：睡眠阶段段、心率、HRV（Apple 给的是
SDNN，另有每次读数约 1 分钟的逐拍时间）、呼吸频率、血氧、手腕温度、Apple 的全天静息心率、训练记录、步数；体重、腰围、VO₂max；
SparkyFitness 的力量训练记录。**Apple 自己的 Readiness / 睡眠分 HealthKit 读不到**，不能直接对照。

---

## 1. 睡眠分（0–100）
代码 `scores/sleep.py`。**逐行移植 Open Wearables 0.9.0 Sleep Score**（`backend/app/algorithms/sleep.py`、`config_algorithms.py`）。

**输入**：当晚主睡眠（每个醒来日期最长的手表睡眠段；只有「睡着」没有阶段数据的 iPhone / 第三方来源不计）、阶段明细、前 15 天内
最近 14 个主睡眠的入睡时间。

**计算**
1. 时长分 *D*（净睡眠小时数 *h*）：7 ≤ *h* ≤ 9 → 100；*h* < 7 → `100 × (1 + e^(k₁(7−m₁))) / (1 + e^(k₁(h−m₁)))`，k₁ = −1.5，m₁ = 5；
   *h* > 9 → 同式 k₂ = 0.8、m₂ = 11、锚点 9，最低 50。
2. 阶段分 *St* = 0.5 × min(深睡 / 90, 1) × 100 + 0.5 × min(REM / 90, 1) × 100。
3. 规律性分 *C*：今晚入睡时间和 14 晚入睡中位数（中午起算，跨午夜连续）之差 Δ 分钟；晚睡 Δ > 15 起扣，每 105 分钟扣 100；
   早睡最多扣 20。前 15 天没有主睡眠时按规格给 0。
4. 中断分 *I*：夜间清醒（去掉第一段睡着之前、最后一段睡着之后的清醒）80 分，20 分钟内不扣，之后每 70 分钟扣 80；
   醒来次数（> 5 分钟）20 分：0–1 次满分，2 次 100%、3 次 75%、4 次 50%… 按 `(1, 1, 0.75, 0.5, 0)` 取。
   **和 OW 不同的一处**：相邻的清醒片段合并成一次醒来（OW 不合并）。
5. 睡眠分 = 0.40 *D* + 0.20 *St* + 0.20 *C* + 0.20 *I*（取整）。

| 参数 | 值 | 类别 |
|---|---|---|
| 四项权重 | 0.40 / 0.20 / 0.20 / 0.20 | 移植的规格 |
| 满分时长 | 7–9 h | 移植的规格（和 NSF 2015、AASM/SRS 2015 对成年人「7–9 h / 至少 7 h」的建议一致） |
| 时长 sigmoid | k 1.5 / 0.8，中点 5 h / 11 h，睡太久最低 50 | 移植的规格 |
| 深睡 / REM 目标 | 各 90 分钟，各占 50% | 移植的规格 |
| 规律性 | 宽限 15 分钟、扣光窗口 105 分钟、早睡最多扣 20、14 晚 | 移植的规格 |
| 中断 | 宽限 20 分钟、扣光窗口 70 分钟、> 5 分钟算一次 | 移植的规格 |
| 合并相邻清醒片段 | — | 设计选择 |

**已知局限**
- 权重和阈值是 OW 定的，我们没有独立验证；OW 的规格本身也没有公开的验证数据。
- 阶段分完全依赖手表的睡眠分期。PPG 睡眠分期和多导睡眠图（PSG）比，分清醒 / 睡着的准确率较高，分四类（清醒、浅睡、深睡、REM）的一致性只有中等（Fonseca 2017：四分类 κ ≈ 0.42）；
  深睡 90 分钟这个目标对年长的人偏高。
- 规律性对作息不规律的人很严格（经常 0 分），这是规格本身的设计。
- 没有「在床」数据，入睡潜伏期通常空着，不进分数。

## 2. 睡眠需求、睡眠债、睡眠表现
代码 `scores/sleep.py`。

**计算**
- 实际睡眠 = 主睡眠 + 上一个主睡眠结束后（最多往前 24 小时）、这次开始前的小睡。
- 睡眠债 = 前 14 晚「基线需求 − 实际睡眠」的加权平均，权重 0.75^(k−1)（k = 1 是昨晚），只用有数据的晚上并按它们归一化；
  多睡可以抵消，结果 ≥ 0。
- 睡眠需求 = 基线 8 h + min(睡眠债, 2 h) + 前一天 strain 超过 7 的部分 × 3 分钟（strain 21 → +42 分钟）。前一天没有 strain 就不加。
- 睡眠表现 = min(实际睡眠 / 睡眠需求, 1)，进恢复分。

| 参数 | 值 | 类别 |
|---|---|---|
| 基线需求 | 480 分钟 | 设计选择（在 NSF / AASM 建议的 7–9 h 范围内），第一版，未用个人数据校准 |
| 窗口 / 衰减 | 14 晚、0.75 | 设计选择，第一版，未验证 |
| 睡眠债上限 | 120 分钟 | 设计选择，第一版，未验证 |
| 负荷加成 | strain > 7 的部分每点 3 分钟 | 设计选择，第一版，未验证 |

**已知局限**：所有人都用同一个 8 h 基线；睡眠需求个体差异很大。WHOOP 说它的睡眠需求会按个人数据调整，我们还没做。

## 3. 由逐拍时间算 HRV
代码 `rawdata/hrv.py`（`rr-v1`）。Apple Watch 每次后台测 HRV 时存了约 1 分钟的逐拍时间（`HKHeartbeatSeriesSample`），夜里 1–4 次。

**计算**
1. RRᵢ = tᵢ − tᵢ₋₁（毫秒）；前面有间断（`precededByGap`）或时间倒退的记为无效。
2. 去伪迹：RR 不在 300–2000 ms，或偏离这条序列 RR 中位数超过 25% → 无效（异位搏动 / 运动伪迹）。
3. RMSSD = √mean((RRᵢ − RRᵢ₋₁)²)，只用相邻两个都有效的间隔对，至少 20 对；SDNN = 有效间隔的总体标准差。

| 参数 | 值 | 类别 |
|---|---|---|
| RMSSD / SDNN 定义 | — | 文献（Task Force ESC/NASPE 1996） |
| 有效 RR 范围 | 300–2000 ms | 设计选择（对应 30–200 bpm） |
| 偏离中位数 | 25% | 设计选择（常见做法是 20–30%，没有拟合） |
| 最少差值数 | 20 | 设计选择 |

**已有的检查**：用同一批逐拍时间重算的 SDNN 和 Apple 自己给的 SDNN 相关约 0.9（`docs/raw-data-plan.md` 第 1 节），说明解析和去伪迹
没有系统性错误。**没有和心电图（ECG）真值比过**。export.xml 里的拍时间只有 10 ms 精度，RMSSD 有约 ±1–2 ms 的量化误差；app 直读是双精度。

**已知局限**
- 读数次数由 Apple 决定，第三方不能触发；换算法不会多出读数。
- 1 分钟的超短窗口：和 4–5 分钟的标准记录相比，RMSSD 用 10 秒的记录就已有效，SDNN 建议至少 30 秒或多段 10 秒（Munoz 2015；综述见 Shaffer 2017）。
  超短窗口对 RMSSD 更友好，这是 #117 想换 RMSSD 的理由之一。

## 4. 恢复分（0–100）
代码 `scores/recovery.py`、`scores/engine.py`。

**输入**（都取当晚主睡眠窗口）
- 当晚 HRV：窗口内每次读数取 ln，再取中位数。现在用 Apple 的 SDNN（`HRV_KIND = "sdnn"`）；改用逐拍算的 RMSSD 是 #117。
- 当晚静息心率：睡眠期**最低 5 分钟均值**。手表睡觉时约 4–5 分钟一条心率，每条代表到下一条为止（最多 10 分钟），按时间加权；
  窗口里覆盖不到一半的不算；都不够时取最低单条。没有睡眠期心率时退回 Apple 的全天静息心率，**和它自己的基线比**（全天值比睡眠最低值高几 bpm，不能混比）。
- 当晚呼吸频率：窗口内读数中位数。睡眠表现：第 2 节。

**计算**
1. 每项和前 60 天基线比：z = (x − 均值) / max(标准差, 下限)，截断在 ±3；静息心率、呼吸频率取反（越低越好）。
2. 加权 z̄ = Σ wₖ zₖ / Σ wₖ（只对有值的项；**HRV 必须有**）。
3. 恢复分 = 100 / (1 + e^−(1.6 z̄ + 0.3))，取整。和基线持平 = 57，z̄ = +0.5 → 75，−0.5 → 38。
4. 颜色：≥ 67 绿，34–66 黄，< 34 红。
5. 前 60 天 HRV 不满 14 晚不出分。
6. 只做标记、不进分数：**生病提示**（静息心率 > 基线 + 5 bpm 且 HRV < 基线 × 0.85）、手腕温度偏离 60 天中位数 ≥ 0.6 °C（至少 5 晚基线）、
   当晚血氧中位数 < 94% 或 < 90% 的读数 ≥ 2 条。

| 参数 | 值 | 类别 |
|---|---|---|
| 方法：ln HRV（及静息心率）对个人滚动基线的 z 分数 | — | 文献（HRV 指导训练的常见做法：Kiviniemi 2007、Plews 2012 / 2013、Buchheit 2014）。**这些研究用的是早晨或夜间的 ln RMSSD**，现在的 SDNN 输入和它们不完全一致 |
| 输入选择：HRV、静息心率、睡眠、呼吸频率 | — | 设计选择，和 WHOOP 公开说明的输入一致（WHOOP 开发者文档的恢复对象有 `hrv_rmssd_milli`、`resting_heart_rate`；WHOOP 说明恢复由 HRV、静息心率、睡眠、呼吸频率组成） |
| 权重 | HRV 0.60、静息心率 0.20、睡眠表现 0.15、呼吸 0.05 | 设计选择，第一版，**没有拟合过** |
| logistic | 斜率 1.6、偏移 0.3 | 设计选择，第一版，没有拟合过 |
| 颜色阈值 | 67 / 34 | 设计选择（和 WHOOP 界面的三色分档一致；WHOOP 没有公开正式文档） |
| 基线 | 60 天、至少 14 晚 | 设计选择 |
| 标准差下限 | ln HRV 0.05、心率 1、表现 0.05、呼吸 0.5 | 设计选择 |
| z 截断 | ±3 | 设计选择 |
| 静息心率窗口 | 最低 5 分钟、每条最多代表 10 分钟 | 设计选择 |
| 生病提示 | +5 bpm 且 HRV < 85% | 设计选择。方向有文献：静息心率升高先于流感样疾病 / 新冠症状出现（Radin 2020、Mishra 2020），呼吸频率升高也有（Miller 2020）；**阈值本身没有验证** |
| 手腕温度 / 血氧标记 | 0.6 °C；94% / 90% × 2 | 设计选择，未验证 |

**已知局限**
- **夜里只有 1–4 次 HRV 读数**（WHOOP 是整夜连续测）。单条读数很吵：一条偏高的 SDNN 能把睡得很差的一晚推到 90 分以上。
  合成数据上，单条读数噪声从 0.15 增到 0.45（ln 尺度），恢复分和真实状态的相关从 0.82 降到 0.70（见验证文档第 3.3 节）。
- 权重、logistic 参数都没有拟合；不同人的分数分布可能差别很大（例如一个人长期偏黄）。
- 基线跟着人走：长期变好或变差会被基线吸收，恢复分只反映「相对最近 60 天」。
- 生病提示在合成数据上会有假阳性（验证文档第 3.2 节），只能当提示。

## 5. 负荷 strain（0–21）和训练负荷
代码 `scores/strain.py`。

**计算**
1. HRmax = max(208 − 0.7 × 年龄, 前 365 天全部心率读数的 99.5 百分位)。没有生日时只用观测值。
2. 每条心率代表到下一条为止，最多 5 分钟（平时 2–5 分钟一条，训练中约 5 秒一条）；最后一条算半个上限。
3. Edwards TRIMP = Σ 各区分钟 × 权重：≥ 50 / 60 / 70 / 80 / 90% HRmax → 权重 1–5。按本地日期算**整天**，不只算训练时段。
4. strain = 21 × (1 − e^(−TRIMP / K))，K = 170。
5. 覆盖不足：一天有心率的小时数 < 12 → 录到训练（TRIMP ≥ 40）照常出分并标「可能偏低」，否则留空、不进 CTL / ATL。
6. CTL / ATL：TRIMP 的指数加权平均，时间常数 42 / 7 天（α = 1 − e^(−1/τ)），TSB = CTL − ATL；初值 = 最初 14 个有数据日的 TRIMP 均值；
   没有 TRIMP 的天沿用前一天。

| 参数 | 值 | 类别 |
|---|---|---|
| Edwards 五区和权重 | 50–90% HRmax，1–5 | 文献（Edwards 1993《The Heart Rate Monitor Book》，书，无 DOI） |
| HRmax 公式 | 208 − 0.7 × 年龄 | 文献（Tanaka 2001） |
| HRmax 取观测 99.5 百分位和公式的大者 | 365 天 | 设计选择 |
| CTL / ATL / TSB | 42 / 7 天 | 文献（Banister 的体能-疲劳模型，Morton 1990；42 / 7 是训练软件的常用取值，属于惯例） |
| 0–21 的饱和曲线形状 | 1 − e^(−x/K) | 设计选择（WHOOP 公开说 strain 是 0–21 的非线性刻度，没有公开公式） |
| **K** | **170** | **个人校准**：负责人一个人 20 个训练日的 TRIMP 中位数映射到 12。要换掉 |
| 心率样本代表时间上限 | 5 分钟 | 设计选择 |
| 覆盖规则 | 12 小时、TRIMP ≥ 40 | 设计选择 |
| CTL / ATL 初值 | 前 14 个有数据日 | 设计选择 |

**已知局限**
- 心率低估力量训练的负荷（所以另算肌肉负荷，第 6 节）。
- 整天的心率都算进去，久坐但心率偏高（压力、咖啡、天热）也会累积少量 TRIMP。
- 读数大多在静息，观测 99.5 百分位通常低于公式，实际几乎总是公式值；Tanaka 公式本身的个体误差约 ±10 bpm，会整体移动五个区。
- K 来自一个人；不同人、不同运动的分布不一样。验证文档里的敏感性分析：K ±20% → strain 平均变化约 1 分。

## 6. 肌肉负荷
代码 `scores/muscle.py`。

- `muscle_load` = 当天力量动作的工作组 Σ 次数 × 重量（kg）。重量按记录的数（哑铃单只、单侧按每侧），热身组不算，自重组只计组数。
  是「同一个人前后可比」的相对量，**不是真实总重量**。
- 有 RPE 和时长时另算 session RPE = 平均 RPE × 分钟（Foster 2001），单位不同，不和容量相加。
- 没有训练记录 → 留空。

| 参数 | 值 | 类别 |
|---|---|---|
| 训练容量 = 次数 × 重量 | — | 设计选择（常用的容量指标） |
| session RPE | 平均 RPE × 分钟 | 文献（Foster 2001） |

**已知局限**：不同动作的容量不可比（深蹲和侧平举）；不进 strain，也不进恢复分。

## 7. 压力（0–3）
代码 `insights/stress.py`。

**计算**
1. 只用醒着（不在任何睡眠段）、不在训练、读数前 5 分钟到后 2 分钟走路 < 20 步的心率（代替加速度）。
2. 平静基线 = 前 14 天这些读数的中位数；离散度 = 1.4826 × MAD（≥ 3 bpm）；读数 < 50 条不出。
3. 每小时 ≥ 3 条才出分：z = (该小时心率中位数 − 基线) / 离散度；该小时有白天 HRV 读数时 z = 0.7 × 心率 z + 0.3 × HRV z
   （HRV 基线 = 前 60 天夜间 ln HRV 的均值 / 标准差，低于基线算压力高）。
4. 分数 = clip(0.5 + z, 0, 3)，< 1 低、1–2 中、≥ 2 高；每天 = 有分小时的平均。

| 参数 | 值 | 类别 |
|---|---|---|
| 1.4826 × MAD | — | 文献（正态分布下 MAD 的一致性常数，Rousseeuw & Croux 1993） |
| 其余（14 天、50 条、3 bpm、20 步、5 / 2 分钟、3 条 / 小时、0.7 / 0.3、偏移 0.5、分档 1 / 2） | — | 设计选择，第一版，未验证 |

**已知局限**：没有加速度，「不动但在做事」（开车、打字）和真的放松分不开；咖啡、天热、脱水都会被当成压力；没有和主观压力或皮质醇比过。

## 8. 身体年龄和衰老速度
代码 `insights/healthspan.py`，参数和完整出处在 `insights/healthspan_params.py`（逐条核对过原文表格，2026-10-17）。
**线上从 2026-10-23 起用 v1.2**（活动合成一个域，见 8.1）；本节写的是 v1.1，v1.2 只改了活动部分，其余都照本节。

**计算**
1. 每项取最近 180 天的均值，最近 30 天的天数权重 × 2；每项至少 30 天数据（规律性 = 30 对相邻两晚；VO₂max 180 天内 ≥ 2 次且最近一次 ≤ 120 天；体重 / 腰围最近 90 天有一次）。
2. 每项用文献的剂量-反应曲线换算成全因死亡风险比 HR，曲线节点之间按 ln(HR) 线性插值，两端外持平；再**除以锚点（「典型成人」）处的 HR**。
3. Δ年 = ln(HR) / ln 2 × 8（Gompertz：成人死亡率约每 8 年翻倍），单项截断 ±4 年；合计 × 0.6、截断 ±10 年。身体年龄 = 实际年龄 + 合计。
4. 衰老速度：每周一个快照，Δ 先做 4 周滑动平均，再对最近 26 个周点做最小二乘，pace = 1 + dΔ/dt，截断在 −1 ~ 3；≥ 8 周才出。

| 项 | 曲线（文献） | 锚点（设计选择） |
|---|---|---|
| 睡眠时长 | U 形，最低 7 h，短每小时 RR 1.06、长 1.13（Yin 2017）；手表实测先换成问卷口径：自报 ≈ 3.365 + 0.567 × 实测（Lauderdale 2008） | 自报 7 h |
| 作息规律性 SRI（Phillips 2017） | 五分位 HR 1 / 0.80 / 0.75 / 0.72 / 0.70（Windred 2024） | 81（队列中位数） |
| 日均步数 | 分 < 60 / ≥ 60 岁的四分位 HR（Paluch 2022） | 各年龄组队列中位数 |
| 每周中高强度分钟 | 中等强度细分档 HR，高强度按 2 倍（Lee 2022） | 150 分钟（指南最低量） |
| 每周力量训练 | J 形，40 分钟最低 0.83（Momma 2022）；140 分钟处 0.92 是读图推断 | 0 |
| VO₂max | 每高 1 MET RR 0.87（Kodama 2009），最多 ±3 MET | 同龄同性别 FRIEND 中位数（Kaminsky 2022） |
| 静息心率 | 每高 10 bpm HR 1.16（Jensen 2013），45 bpm 以下持平（Zhang 2016） | 65 bpm（Quer 2020） |
| BMI / 腰围身高比 | GBMC 2016（Di Angelantonio）/ Jayedi 2020 | 25 / 0.50 |
| 年数换算 | 死亡率翻倍时间 8 年（Kirkwood 2015） | — |

| 合成方法参数 | 值 | 类别 |
|---|---|---|
| 锚点（每项） | 见上表 | 设计选择 |
| 窗口、近 30 天 × 2、≥ 30 天 | — | 设计选择 |
| 单项 ±4 年、合计 × 0.6、±10 年 | — | 设计选择，第一版，未验证 |
| 衰老速度 26 周、4 周平滑、−1 ~ 3 | — | 设计选择 |

**已知局限**
- 每条曲线来自队列研究的**关联**，不是因果；各研究的人群（多为欧美中老年人）、协变量调整、暴露测量方式（多为问卷）都和手表数据不同。
- 各项之间相关（步数、运动、VO₂max、静息心率），× 0.6 去重是粗略的，没有用多变量模型；合计的意义大于单项。
- 敏感性是解析的：没被截断时，Δ年和「翻倍时间」「× 0.6」都成正比，任一个 ±20% → 身体年龄偏离实际年龄的部分也 ±20%。
- 没有任何外部验证（比如和 WHOOP Age、表观遗传年龄比）。


### 8.1 v1.2：活动合成一个域（`hs-v1.2-2026-10-23`，2026-10-23 起线上使用；v1.1 保留用于对比和回滚）
参数和出处在 `healthspan_params_v12.py`（没写的全部沿用 v1.1）；`healthspan_store.snapshot(data, as_of, params)` 按参数集算，
`healthspan_store.VERSIONS` 列出两个版本，`healthspan_store.CURRENT` 是写进 `healthspan_weekly` 的版本（v1.2）。
负责人看过对比后决定切换（2026-10-23），两个可选项都用默认值：力量训练不折算进量、高强度不单独加分；合计 × 0.6 不变。
回滚：`CURRENT` 改回 `VERSIONS["v1.1"]`，部署后重算所有用户。对比工具：`python -m body_engine.validation healthspan --user-id N`（只读数据库）。

**为什么改**
1. v1.1 把步数、中高强度运动（MVPA）、力量训练当成三项，各自 ±4 年再 × 0.6。Lee 2022 的 MVPA 本身就包括走路和举重，
   步数又主要是走路，同一份活动最多被算三次。
2. < 60 岁的步数曲线（Paluch 2022 四分位）在 4849 → 7245 步之间 HR 从 1.00 掉到 0.59，这一段置信区间最宽（0.39–0.88），
   也最容易混进反向因果（走得少的人里有病人）。样本更大的 Ding 2025 meta 分析在 5000–10000 步之间几乎是平的。
3. 力量、游泳、骑车、划船几乎不产生手腕步数，练得多、走得少的人在步数这一项上吃亏。

**计算（只改活动部分）**
1. 量 = 手腕日均步数 × 手腕系数（1.0）+ 不产生步数的训练折算的步数当量（日均）。折算：分钟 × 同 MET 的走 / 跑每分钟步数
   （3 MET ≈ 100 步/分、6 MET ≈ 130 步/分，中间线性，6 MET 以上不再加；< 3 MET 的瑜伽 / 普拉提 / 拉伸记 0）。
   只折算游泳、骑车、划船机（各自取 Compendium 一般强度的 MET）；走路、跑步、篮球、网球、徒步、爬楼、椭圆机本身就有步数，不重复加。
2. HR_量 = 步数曲线（< 60 岁 Ding 2025；≥ 60 岁仍用 Paluch 2022），相对各年龄组的参照点。
3. HR_活动 = HR_量 × HR_力量（Momma 2022 的 J 形曲线，和 v1.1 一样）。力量训练**不再**折算成步数当量，见下。
4. 活动域 Δ年 = ln(HR_活动)/ln 2 × 8，**整个域**截断 ±4 年；其余各项不变，合计仍 × 0.6、截断 ±10 年。
5. 心率区间 MVPA 只作参考（`parts` 里 `counted: false`），不再计入：它里面的走路已经在步数里、训练已经在折算里。参考值的口径也改了：
   力量训练和游泳 / 骑车 / 划船按 MET 计（中等 × 1、≥ 6 MET × 2，和问卷一致），其余仍按心率区间。

**MVPA 为什么偏低（v1.1 口径的问题）**：v1.1 按全天心率 ≥ 60% HRmax 计中高强度。力量训练大部分时间（组间休息）在 50–60% HRmax，
一节力量课常常只有一小部分时间计入；而且同样的 %HRmax，力量训练的摄氧量比跑步低（Collins 1991），所以「按心率」对力量训练
既不是绝对强度（问卷口径）也不是摄氧量。Apple 的锻炼分钟也不能直接用：它的阈值约 30% VO₂ 储备，低于中等强度的 40%（Abt 2018），会偏高。
另外 180 天窗口本来就会把几个月前不怎么练的时期算进去，这一点是有意的（看长期习惯）。

**参数**

| 参数 | 值 | 类别 |
|---|---|---|
| < 60 岁步数曲线 | 相对 2000 步：3000 0.77、4000 0.64、5000 0.57、6000 0.54、7000 0.53、8000–10000 0.52、11000 0.49、12000 0.45，两端外持平（Ding 2025 表 2） | 文献 |
| < 60 岁参照点 | 7803 步（Paluch 2022 < 60 岁队列中位数，原文） | 文献（作为参照点是设计选择） |
| ≥ 60 岁步数曲线和参照点 | 不变（Paluch 2022，6167 步） | 文献 / 设计选择 |
| 手腕系数 | 1.0：Apple Watch 和腰 / 髋部研究设备在群体水平上没有一致偏差（Breteler 2019 少约 4–5%，Spartano 2024 中位数几乎相同）；原始加速度计换到手腕会多约 2500 步（Tudor-Locke 2015），但那不是 Apple 的算法 | 文献 |
| 步频 ↔ MET | 100 步/分 ≈ 3 MET、130 步/分 ≈ 6 MET（CADENCE-Adults 2019 / 2020）；中间线性、6 MET 以上持平 | 文献 / 设计选择 |
| 训练类型的 MET | 游泳 5.8 [18240]、户外骑车 7.0 [01014]、室内单车 6.8 [01200]、划船机 5.0 [02071]、力量 3.5 [02054]（2024 Compendium） | 文献 |
| 只折算哪些类型 | 游泳、骑车、划船机 | 设计选择 |
| 力量训练只走自己的曲线（`STRENGTH_IN_VOLUME = False`） | Momma 2022 的力量效应是在考虑了有氧活动之后的（纳入研究都考虑了有氧或其他活动；GRADE 很低），再折算成步数就重复了；Lee 2022 的口径（举重算中等强度）作为对比项保留 | 设计选择 |
| 量 × 力量相乘 | 步数研究没调整力量训练，相乘是近似 | 设计选择 |
| 活动域上限 | ±4 年（等于 v1.1 单项上限，活动合计不再能到 ±12 年） | 设计选择，第一版，未验证 |
| 个人校准 | 无 | — |

**合成例子**（虚构的人，只看活动部分，Δ年是 × 0.6 之前）

| 例子 | v1.1（步数 / MVPA / 力量 = 合计） | v1.2 活动域 |
|---|---|---|
| 25 岁，6000 步，MVPA 80 分钟/周，力量 40 分钟/周 | +4.0 / +0.7 / −2.1 = +2.6 | −1.8（量 +0.4，力量 −2.1） |
| 25 岁，6000 步，每周游泳 3 次 × 45 分钟 | +4.0 / +0.1 / 0 = +4.1 | 0.0（游泳折算约 2500 步/天） |
| 25 岁，3000 步，不运动 | +4.0 / +2.0 / 0 = +6.0 | +4.0（截断前 +4.5） |
| 35 岁，10500 步，MVPA 350，力量 120 | −0.8 / −1.2 / −1.2 = −3.2 | −1.6（量 −0.4，力量 −1.2） |
| 70 岁，5000 步，MVPA 100 | +1.5 / +0.5 / 0 = +2.0 | +1.5 |

**v1.2 的已知局限**
- 很活跃的人会少一些加分：v1.1 的 MVPA 曲线一直奖励到每周 600 分钟，v1.2 不再单独奖励强度（Lee 2019、Saint-Maurice 2020 发现
  调整步数后步频 / 强度基本没有额外关联；del Pozo Cruz 2022 发现还有一些）。没找到把「MVPA 分钟数」和步数放进同一模型的研究。
- 没记录成训练的游泳 / 骑车不会被折算（心率参考里看得到，但不计入）。
- Ding 2025 没做排除早期死亡的敏感性分析；它的曲线比 Paluch 平，方向和 Hansen 2020、Lee 2019 排除早期死亡后的结果一致，但这是间接证据。
- 活动域 ±4 年、量 × 力量相乘、只折算三种训练都是设计选择，没有用数据验证。

## 9. 由上面几个分数派生的功能（简述）
- **今日负荷目标**（`insights/coach.py`）：恢复绿 14–18、黄 10–14、红 0–10；昨天 TSB < −30 整体下调 2；有生病提示 0–8。区间是按 K = 170 的 strain 定的，设计选择。
- **睡眠教练**：今晚需求 = 第 2 节同一套公式 + 入睡潜伏期缓冲（最近中位数或 15 分钟）。
- **健康监测**：静息心率、HRV、呼吸、血氧、手腕温度各自的「60 天均值 ± 2 SD」范围（HRV 在 ln 尺度），≥ 14 晚才出；设计选择。
- **训练后心率恢复 HRR60**（`cardio/hrr.py`）：训练最后 2 分钟最高心率 − 结束后 60 秒心率，峰值 ≥ 75% HRmax 才算有效；设计选择。

## 10. 和 Apple、WHOOP 公开口径的对照

| 方面 | Apple | WHOOP（公开资料） | 我们 |
|---|---|---|---|
| HRV 指标 | HealthKit 的 `heartRateVariabilitySDNN`（SDNN，ms），后台 1 分钟读数，夜里 1–4 次 | RMSSD（开发者 API 字段 `hrv_rmssd_milli`），用睡眠期间连续的光电心率算（具体窗口和加权没有正式文档） | 现在 Apple 的 SDNN；可选由逐拍时间算 RMSSD（#117） |
| 恢复 / 准备度 | 读不到（不在 HealthKit） | 0–100%，输入 HRV、静息心率、睡眠、呼吸频率；颜色三档 | 第 4 节，输入相同，权重是我们定的 |
| 负荷 | 无对应（只有活动圆环、训练负荷估算） | strain 0–21，非线性，基于心血管负荷，公式未公开 | Edwards TRIMP → 0–21 饱和曲线，K 待重新校准 |
| 睡眠分 | 读不到 | 睡眠表现 = 实际 / 需求，需求随负荷和睡眠债调整 | OW 睡眠分 + WHOOP 式睡眠需求 / 表现 |
| 身体年龄 | 无 | WHOOP Age（方法有公开说明，权重未公开） | 文献曲线 + Gompertz 换算（第 8 节） |

**没有做过和 WHOOP 真机的同步对比**；表里的 WHOOP 一栏只来自它公开的文档和说明，具体权重 / 公式 WHOOP 没有公开。

## 11. 已知局限汇总
1. 只用过一个人一年多的数据做回算和抽查；**没有多用户验证、没有和 WHOOP / Oura / 心电图对比**（方案见验证文档）。
2. 恢复分的权重、logistic、睡眠需求基线都没有拟合；strain 的 K 是一个人的个人校准。
3. HRV 读数稀疏（Apple 决定），单条读数噪声是恢复分最大的误差来源。
4. 训练、压力没有加速度，靠步数和训练记录代替。
5. 身体年龄的曲线是队列关联，不是个人的因果效应。
6. 所有分数都只作为健康参考，不用于诊断或治疗决定。

## 12. 参考文献
DOI 在 2026-10-22 用 Crossref 核对过（标题、作者、期刊、年份）。身体年龄的文献另见 `insights/healthspan_params.py`（逐条对照过原文表格）。

- Task Force of the European Society of Cardiology and the North American Society of Pacing and Electrophysiology. Heart rate variability: standards of measurement, physiological interpretation and clinical use. *Circulation* 1996;93:1043–1065. doi:10.1161/01.CIR.93.5.1043
- Shaffer F, Ginsberg JP. An overview of heart rate variability metrics and norms. *Front Public Health* 2017;5:258. doi:10.3389/fpubh.2017.00258
- Munoz ML et al. Validity of (ultra-)short recordings for heart rate variability measurements. *PLoS One* 2015;10:e0138921. doi:10.1371/journal.pone.0138921
- Hernando D et al. Validation of the Apple Watch for heart rate variability measurements during relax and mental stress in healthy subjects. *Sensors* 2018;18:2619. doi:10.3390/s18082619
- Kiviniemi AM et al. Endurance training guided individually by daily heart rate variability measurements. *Eur J Appl Physiol* 2007;101:743–751. doi:10.1007/s00421-007-0552-2
- Plews DJ et al. Heart rate variability in elite triathletes, is variation in variability the key to effective training? A case comparison. *Eur J Appl Physiol* 2012;112:3729–3741. doi:10.1007/s00421-012-2354-4
- Plews DJ et al. Training adaptation and heart rate variability in elite endurance athletes: opening the door to effective monitoring. *Sports Med* 2013;43:773–781. doi:10.1007/s40279-013-0071-8
- Buchheit M. Monitoring training status with HR measures: do all roads lead to Rome? *Front Physiol* 2014;5:73. doi:10.3389/fphys.2014.00073
- Radin JM et al. Harnessing wearable device data to improve state-level real-time surveillance of influenza-like illness in the USA. *Lancet Digit Health* 2020;2:e85–e93. doi:10.1016/S2589-7500(19)30222-5
- Mishra T et al. Pre-symptomatic detection of COVID-19 from smartwatch data. *Nat Biomed Eng* 2020;4:1208–1220. doi:10.1038/s41551-020-00640-6
- Miller DJ et al. Analyzing changes in respiratory rate to predict the risk of COVID-19 infection. *PLoS One* 2020;15:e0243693. doi:10.1371/journal.pone.0243693
- Tanaka H, Monahan KD, Seals DR. Age-predicted maximal heart rate revisited. *J Am Coll Cardiol* 2001;37:153–156. doi:10.1016/S0735-1097(00)01054-8
- Edwards S. *The Heart Rate Monitor Book.* Sacramento: Fleet Feet Press; 1993.（书，无 DOI）
- Morton RH, Fitz-Clarke JR, Banister EW. Modeling human performance in running. *J Appl Physiol* 1990;69:1171–1177. doi:10.1152/jappl.1990.69.3.1171
- Foster C et al. A new approach to monitoring exercise training. *J Strength Cond Res* 2001;15:109–115. doi:10.1519/00124278-200102000-00019
- Hirshkowitz M et al. National Sleep Foundation's sleep time duration recommendations: methodology and results summary. *Sleep Health* 2015;1:40–43. doi:10.1016/j.sleh.2014.12.010
- Watson NF et al. Recommended amount of sleep for a healthy adult: a joint consensus statement of the AASM and SRS. *J Clin Sleep Med* 2015;11:591–592. doi:10.5664/jcsm.4758
- Fonseca P et al. Validation of photoplethysmography-based sleep staging compared with polysomnography in healthy middle-aged adults. *Sleep* 2017;40:zsx097. doi:10.1093/sleep/zsx097
- Rousseeuw PJ, Croux C. Alternatives to the median absolute deviation. *J Am Stat Assoc* 1993;88:1273–1283. doi:10.1080/01621459.1993.10476408
- 身体年龄：Yin 2017 (doi:10.1161/JAHA.117.005947)、Lauderdale 2008 (doi:10.1097/EDE.0b013e318187a7b0)、Phillips 2017 (doi:10.1038/s41598-017-03171-4)、
  Windred 2024 (doi:10.1093/sleep/zsad253)、Paluch 2022 (doi:10.1016/S2468-2667(21)00302-9)、Lee 2022 (doi:10.1161/CIRCULATIONAHA.121.058162)、
  Momma 2022 (doi:10.1136/bjsports-2021-105061)、Kodama 2009 (doi:10.1001/jama.2009.681)、Kaminsky 2022 (doi:10.1016/j.mayocp.2021.08.020)、
  Jensen 2013 (doi:10.1136/heartjnl-2012-303375)、Zhang 2016 (doi:10.1503/cmaj.150535)、Quer 2020 (doi:10.1371/journal.pone.0227709)、
  Global BMI Mortality Collaboration 2016 (doi:10.1016/S0140-6736(16)30175-1)、Jayedi 2020 (doi:10.1136/bmj.m3324)、Kirkwood 2015 (doi:10.1098/rstb.2014.0379)
- 身体年龄 v1.2：Ding 2025 (doi:10.1016/S2468-2667(25)00164-1)、Hansen 2020 (doi:10.1111/sms.13726)、Lee IM 2019 (doi:10.1001/jamainternmed.2019.0899)、
  Saint-Maurice 2020 (doi:10.1001/jama.2020.1382)、del Pozo Cruz 2022 (doi:10.1001/jamainternmed.2022.4000)、Tudor-Locke 2015 (doi:10.1249/MSS.0000000000000476)、
  Breteler 2019 (doi:10.2196/11489)、Spartano 2024 (doi:10.2196/54631)、Tudor-Locke 2011 (doi:10.1186/1479-5868-8-79)、CADENCE-Adults 2019 (doi:10.1186/s12966-019-0769-6) / 2020 (doi:10.1186/s12966-020-01045-z)、
  Herrmann 2024 Compendium (doi:10.1016/j.jshs.2023.10.010)、Collins 1991 (doi:10.1249/00005768-199105000-00018)、Abt 2018 (doi:10.2196/cardio.8574)
- 移植的规格：Open Wearables 0.9.0，`backend/app/algorithms/sleep.py`、`config_algorithms.py`（MIT，仓库里的 `vendor/open-wearables`）
- 公开资料：WHOOP Developer Platform「Recovery」（developer.whoop.com/docs/developing/user-data/recovery，字段 `hrv_rmssd_milli`、`resting_heart_rate`）；
  Apple HealthKit `HKQuantityTypeIdentifier.heartRateVariabilitySDNN`、`HKHeartbeatSeriesSample`（developer.apple.com）
