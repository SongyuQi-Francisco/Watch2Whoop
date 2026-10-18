# WHOOP 功能对照（2026-10-17）

WHOOP 的「身体数据和分析」功能逐项 → 我们的实现、接口、状态。接口都在 `GET /v1/users/{user}/…`（Bearer token 或今天页 cookie；看别人要 shares 授权，括号里是 scope），身体管家用同名的 `get_*` 工具。算法和参数见 `docs/system-design.md` 第 7 节。

状态：✅ 已做（后端 + API + 管家工具）；🟡 做了但数据或口径有限；⏳ 以后做；❌ 做不了。前端（今天页）由今天页会话按这里的字段做。

| WHOOP 功能 | 我们的实现 | 接口 / 工具 | 状态 |
|---|---|---|---|
| **Recovery**（恢复分 0–100） | 当晚 ln HRV（SDNN）、睡眠期最低 5 分钟心率、睡眠表现、呼吸频率相对 60 天基线的 z 分数加权 + logistic；满 14 晚才出分 | `scores`（scores）/ `get_scores` | ✅（HRV 是 SDNN，RMSSD 等 OW 0.10） |
| **Strain**（负荷 0–21） | 全天 Edwards TRIMP → 21 × (1 − e^(−TRIMP/170)) | `scores` / `get_scores` | 🟡 要原始心率；2026-10-14 之后只有快捷指令的天没有（快捷指令改上传原始心率由另一个会话在做） |
| 力量训练负荷（Strength Trainer / Muscular Load） | 肌肉负荷 = SparkyFitness 工作组的 Σ 次数 × 重量 | `scores.muscle_load` | 🟡 只有训练容量，没有 WHOOP 的肌肉分区模型 |
| **Strain Coach**（今日负荷目标） | 恢复绿 14–18、黄 10–14、红 0–10；昨天 TSB < −30 下调 2；生病提示 0–8；当前 strain、进度、状态 | `strain-coach?date=`（scores）/ `get_strain_coach` | ✅ |
| **Sleep Performance / Sleep Need / Sleep Debt** | 睡眠表现 = 实际 / 需求；需求 = 8h + 14 晚加权睡眠债（≤ 2h）+ 前一天负荷加成 | `scores` / `get_scores` | ✅ |
| **Sleep Coach**（几点睡） | 今晚需求（含睡眠债和今天负荷）+ 起床时间 → 几点睡；有入睡目标时按目标（逐步提前），并给出和睡够的差距 | `sleep-coach?date=`（scores）/ `get_sleep_coach` | ✅（入睡目标模块合并后自动接上） |
| 睡眠阶段（清醒 / 浅睡 / 深睡 / REM） | 手表的阶段分钟和时间轴 | `sleep`、今天页 `sleep/nights` | ✅ |
| **Sleep Consistency**（睡眠规律性 %） | 睡眠规律性指数 SRI（相隔 24h 睡 / 醒状态相同的比例），7 / 30 天，截到 0–100 | `sleep/details`（sleep）/ `get_sleep_details` | ✅ |
| Sleep Efficiency（睡眠效率） | 睡着 /（潜伏期开始 → 醒来） | `sleep/details` | 🟡 没有「在床」时间（Apple Watch 只记睡着 / 清醒），只反映夜间清醒 |
| 入睡潜伏期 | 第一段睡着前紧挨着的手表「清醒」片段 | `sleep/details` | 🟡 现有数据里一直没有这种片段 → 都是空 |
| 夜间醒来次数 / 清醒时长 | 去掉入睡前和醒后赖床的清醒，> 5 分钟算一次 | `sleep/details`、`scores.details.sleep` | ✅ |
| **Stress Monitor**（压力 0–3） | 醒着、不在训练、没在走路时的心率（和白天 HRV）相对 14 天平静基线的偏离，每小时和每天 | `stress?start=&end=&hours=`（scores）/ `get_stress` | 🟡 没有加速度，用步数和训练记录排除活动；要原始心率 |
| **Health Monitor**（生命体征在不在正常范围） | 静息心率、HRV、呼吸频率、血氧、手腕温度：60 天基线 ± 2 SD（有最小带宽），最近一晚的状态 | `health-monitor?date=`（scores）/ `get_health_monitor` | ✅（手腕温度数据很少） |
| 生病 / 异常提示 | 静息心率 > 基线 + 5 且 HRV < 0.85 × 基线；血氧偏低、手腕温度偏离 | `scores.flags` | ✅ |
| 心率区间（每天 / 每次训练 Z1–Z5） | 50/60/70/80/90% HRmax 五区分钟数；每次训练的来自 workouts 表 | `hr-zones?start=&end=`（scores）/ `get_hr_zones` | ✅（每次训练的部分要 workouts 表，训练历史会话在做） |
| **VO₂max / 体能等级** | Apple 的 VO₂max 读数：每月平均、每年趋势、同龄同性别百分位和等级（FRIEND 2022） | `vo2max?end=&days=`（scores）/ `get_vo2max` | ✅ |
| **WHOOP Age / Healthspan**（身体年龄、衰老速度） | 8 项指标 → 文献的全因死亡风险比 → Gompertz 换成年数（单项 ±4、合计 × 0.6、±10）；每周一次；衰老速度 = 1 + Δ（4 周滑动平均）在最近 26 周的斜率，截在 −1 ~ 3 | `healthspan?weeks=`（scores）/ `get_healthspan` | ✅（身体年龄变化大时仍会顶到边界，`pace_raw` 给没截断的值） |
| **Performance Assessment**（周报 / 月报） | 恢复、睡眠、负荷、压力、训练、体重的汇总、上期和变化、日记发现；文字由管家写 | `reports?period=week\|month&end=`（scores）/ `get_report` | ✅ |
| **Journal**（行为日记和影响分析） | 日记项、次日恢复分 / 静息心率 / HRV 的两组比较 | records 模块的 journal 接口 / `analyze_journal` | ✅（第一版；OLS + Newey-West 以后） |
| Training Load（CTL / ATL / TSB） | 42 / 7 天 EWMA | `scores` | ✅ |
| 训练记录（Activity） | 手表训练（OW）+ SparkyFitness 力量训练 | 训练历史会话的 workouts / training 接口 | 🟡 训练历史会话在做 |
| 卡路里 / 能量消耗 | 活动 + 静息能量（OW 按来源去重） | `daily-metrics` | ✅ |
| **Trends**（每个指标 1 周 / 1 月 / 6 月，和上期比） | 趋势 tab 五组 + 7–365 天 + 「vs 上期」，WHOOP 所有指标都有卡片 | `trends?days=&end=` | ✅（2026-10-18） |
| Restorative Sleep、Steps、恢复颜色分布、Strain vs 目标 | 趋势 tab 卡片 | `trends` | ✅ |
| Behavior Insights | 「报告」里的行为影响（日记各 ≥ 5 天是 / 否，次日恢复差） | `journal/analysis` | ✅ |
| Sleep Performance（新口径） | 可选的综合分：睡够 60% + 规律 20% + 效率 20%（没有睡眠压力） | `trends` `sleep_perf_whoop` | 🟡 对照用，不进恢复分 |
| Strain 含肌肉负荷 | 可选口径：TRIMP + 肌肉负荷（中位训练量 ≈ 60 TRIMP） | `trends` `strain_combined` | 🟡 不替换 strain |
| 其他产品的指标（HRV 状态、训练状态、有氧分钟、韧性、生病风险、生物钟、身体电量、恢复时间、最低心率出现时间、运动后心率恢复） | 见 `docs/wearable-gap-analysis.md`、系统设计第 18 节 | `trends.status` | ✅ / 🟡（电量、恢复时间是估算） |
| 社区 / 团队 / 排行榜 | — | — | ❌ 不做（个人和家人用） |
| **心电图（ECG）** | — | — | ❌ 做不了：HealthKit 的 ECG 波形 OW 0.9.0 不同步；医疗功能不自己解读 |
| **血压洞察** | — | — | ❌ 做不了：Apple Watch 只有高血压通知（是 / 否），没有数值；有袖带血压计时可以手动记成化验 / 指标 |
| **智能闹钟（Sleep Coach 唤醒）** | — | — | ❌ 做不了：要手表端 app 控制震动 |
| **震动提醒 / 触觉提醒** | — | — | ❌ 做不了：同上，用 iPhone 的通知代替（规则提醒） |
| **生理周期（Menstrual Cycle Insights）** | — | — | ⏳ 给家人以后再做（HealthKit 有周期数据，OW 要先支持） |
| 怀孕模式 | — | — | ⏳ 同上 |
| 血检整合（Advanced Labs） | 化验记录、参考范围、标记 | records 模块的 lab 接口 / `get_lab_results` | ✅（不做 WHOOP 那种生物年龄联合模型） |

## 字段（前端按这个做）
见 `docs/system-design.md` 第 7 节「身体年龄和其他分析 · 接口字段」。
