# 差距分析：Watch2Whoop 对比 WHOOP 和其他身体管理产品（2026-10-18）

> **状态（2026-10-18）**：用户已确认「P0–P2 能做的都先做」，P0、P1 和 P2 里用现有数据能做的已实现（系统设计第 18 节）。ECG、血压等仍然做不了；Recovery Index 和运动后心率恢复先用现有原始心率，等 iOS app 同步 RR / 心率恢复后更准。

目的：列出 WHOOP 有、我们没有（或没展示）的指标 / 展示 / 算法，我们比 WHOOP 多做的部分，再和 Oura、Garmin、Apple、Fitbit、Polar 比较，给出建议补的项和优先级。**本文是待用户确认的方案，确认前不动手实现。**

已有功能逐项对照见 `docs/whoop-feature-map.md`，算法见 `docs/system-design.md` 第 7 节。下文的「数据」一栏说的是我们库里有没有现成数据：✅ 已有 / 🟡 有但稀疏或口径不同 / ❌ 没有。

---

## 1. 现状一览

**后端已经算出来的（WHOOP 核心基本齐了）**：恢复分、负荷（strain）、睡眠表现 / 需求 / 睡眠债、Strain Coach、Sleep Coach、睡眠阶段、睡眠规律性（SRI）、压力 0–3、健康监测（5 项生命体征）、生病提示、心率区间、VO₂max 和体能等级、身体年龄 / 衰老速度、周报月报、日记影响分析、CTL / ATL / TSB。

**问题在展示**：很多指标只在「今天」页当天出现，或只在接口里，「趋势」tab 看不到它们的长期变化。现在的趋势 tab 只有 12 张卡：

| 已在趋势 tab | 不在趋势 tab，但库里有数据（每天都有值） |
|---|---|
| 恢复、睡眠时长、睡眠分、负荷、静息心率、HRV、体重、睡眠效率、VO₂max、睡眠规律性、训练量 / 周、心率区间 | 呼吸频率、血氧、手腕温度、睡眠需求、睡眠债、睡眠表现 %、恢复性睡眠（深睡 + REM）、压力、负荷目标区间、CTL / ATL / TSB、步数、活动 / 总消耗、恢复颜色分布、恢复贡献项（HRV / 静息心率 / 呼吸 / 睡眠相对基线的 z 分数）、身体年龄（每周）、夜间醒来次数 |

时间范围只有 30 / 90 天，没有 WHOOP 的 1 周 / 6 个月，也没有「这期均值 vs 上期」的对比。

---

## 2. WHOOP 对照

### 2.1 指标和算法

| WHOOP | 我们 | 差距 | 数据 | 建议 |
|---|---|---|---|---|
| Recovery（HRV、静息心率、呼吸、睡眠表现；5.0 起加了血氧和皮温） | ✅ 同样 4 项 z 分数加权；血氧、手腕温度只用来出提示 | 血氧 / 皮温不进分数；HRV 是 SDNN 不是 RMSSD | 🟡 手腕温度只有 22 天 | 不改算法；**趋势里展示贡献项** |
| Strain（心血管 + 肌肉负荷，Strength Trainer 起合进同一个 0–21） | 🟡 心血管 strain 和肌肉负荷分开 | 没合并 | ✅ | P2：合并口径要研究，先不动 |
| Sleep Performance（2024 新口径：睡眠时长 vs 需求 + 规律性 + 效率 + 高睡眠压力） | 🟡 只有 实际 / 需求；OW 睡眠分另算 | 少规律性、效率、睡眠压力三项 | ✅ 三项都有 | P2：可以加一个 WHOOP 式综合分，先确认要不要 |
| Sleep Need / Sleep Debt | ✅ | 只在今天页 | ✅ | **P0 进趋势** |
| Restorative Sleep（深睡 + REM 时长和占比） | ❌ 没单独算 | 少一个指标 | ✅ 阶段分钟有 | **P0 进趋势** |
| Sleep Consistency / Efficiency | ✅ | — | ✅ | 已在趋势 |
| HRV、静息心率、呼吸频率 | ✅ | 呼吸频率不在趋势 | ✅ | **P0 呼吸频率进趋势** |
| 血氧、皮温（Health Monitor） | ✅ 当天状态 | 没有长期曲线 | 🟡 皮温稀疏 | **P0 进趋势**（皮温显示和基线的偏差） |
| Stress Monitor（0–3） | ✅ | 只有当天 | ✅ | **P0 每日压力进趋势** |
| Strain Coach（负荷目标） | ✅ 当天 | 看不到每天达没达标 | ✅ | **P0 负荷卡加目标区间带** |
| Steps（5.0 新加） | ❌ 没展示 | — | ✅ 步数每天都有 | **P0 进趋势** |
| Calories | ✅ 接口有 | 不在趋势 | ✅ | **P0 进趋势** |
| Healthspan / WHOOP Age / Pace of Aging | ✅ 「我」页 | 趋势里没有 | ✅ 每周 | **P0 身体年龄曲线进趋势** |
| VO₂max（WHOOP 自己估算） | ✅ 用 Apple 的读数 | 口径不同但更准 | ✅ | 保持 |
| 心率区间 | ✅ | — | ✅ | 已在趋势 |
| Trends 页（每个指标 1 周 / 1 月 / 6 月，区间均值，和上期比） | 🟡 30 / 90 天，没有对比 | 展示差距 | — | **P0 加 7 / 180 / 365 天和「vs 上期」** |
| Recovery 着色柱 / 绿黄红天数 | ❌ | 展示差距 | ✅ | **P0** |
| Strain & Recovery 叠加图 | ❌ | 展示差距 | ✅ | **P0** |
| Behavior Insights（日记 → 恢复，各 ≥ 5 次是 / 否） | ✅ `analyze_journal`、周报里有 | 趋势里没有 | ✅ | P1：放进「报告」 |
| Monthly Performance Assessment | ✅ 周报 / 月报 | — | ✅ | 已有 |
| AI Coach、My Memory、Proactive Check-ins | ✅ Jarvis + 长期记忆 | 主动提醒是规则引擎，不是 AI | — | 保持 |
| Heart Screener（ECG）、Blood Pressure Insights | ❌ | 手表数据拿不到 | ❌ | 做不了 |
| Hormonal Insights / 生理周期 | ⏳ | 给家人以后做 | 🟡 | 以后 |
| Advanced Labs | ✅ 化验 | 没有联合模型 | ✅ | 保持 |
| 临床医生视频、病历导入（2026 新） | ❌ | 不在范围 | — | 不做 |

### 2.2 我们比 WHOOP 多做的

- **化验**：记录、参考范围、复查间隔；尿酸 / ALT / 甘油三酯这类和饮食规则挂钩。
- **补剂**：在吃 / 暂停 / 按需，和化验关联（比如肌酸 → 肌酐偏高）。
- **饮食**：SparkyFitness 记录 + 食谱 + 快捷记餐模板 + 健康约束徽章（嘌呤、红肉克数、牛肋条、饱和脂肪）+ 按剩余热量 / 蛋白推荐。WHOOP 没有饮食记录。
- **规则提醒**：红肉 ≤ 250 g/周、尿酸、练前进食、恢复红黄绿时的训练建议。
- **力量训练到动作级**：每组次数 × 重量、禁用 / 小心动作（直立划船、颈后推举…）、下次加重建议。WHOOP 的 Strength Trainer 只有肌肉负荷。
- **入睡目标逐步提前**（不只是今晚几点睡）。
- **体重 / 体成分趋势**、身体事件（伤病、体态）。
- **家人共享**（按 scope 授权）、多 AI 提供商、自托管、Apple 健康全量历史导入、Grafana 长期看板。

---

## 3. 和其他产品比

| 产品 | 功能 | 我们 | 数据 | 建议 |
|---|---|---|---|---|
| **Oura** | Readiness 9 项贡献（静息心率、HRV Balance、体温、Recovery Index、睡眠、Sleep Balance、睡眠规律、前一天活动、Activity Balance） | 恢复分 4 项 | — | 贡献项展示（P0）已覆盖大部分；**HRV Balance / Activity Balance 归到下面 Garmin 的 HRV 状态和训练状态** |
| | Sleep Score 7 项（总时长、效率、Restfulness、REM、深睡、潜伏期、Timing） | OW 睡眠分 | ✅ | 恢复性睡眠（P0）补上 REM / 深睡；其他不重复做 |
| | Recovery Index（夜间心率多晚才降到最低） | ❌ | 🟡 要夜间原始心率 | P2 |
| | Resilience（14 天压力和恢复的平衡，5 档） | ❌ | ✅ 压力 + 恢复都有 | **P1** |
| | Daytime Stress / Restored time | ✅ 压力 0–3 | ✅ | 保持 |
| | Cardiovascular Age（脉搏波速度） | ❌ | ❌ 要 PPG 波形 | 做不了（身体年龄代替） |
| | Symptom Radar（生病早期预警） | 🟡 flags | ✅ | P2：多信号合成一个分 |
| | Chronotype | ❌ | ✅ 入睡 / 醒来时间 | P2 |
| **Garmin** | HRV Status（7 天均值 vs 个人基线：平衡 / 不平衡 / 偏低） | ❌ 只有每天的 HRV 和基线带 | ✅ | **P1**，算法简单，WHOOP 用户也看得懂 |
| | Training Status（有效 / 保持 / 巅峰 / 过度 / 去适应 / 恢复…，看 VO₂max 趋势 + 急性负荷） | 🟡 只有 TSB 的一句状态 | ✅ CTL/ATL + VO₂max | **P1** |
| | Acute Load + Load Focus（低有氧 / 高有氧 / 无氧分布） | 🟡 有心率区间 | ✅ | P1，和训练状态一起做 |
| | Training Readiness（睡眠、恢复时间、HRV 状态、急性负荷、近 3 晚睡眠、近 3 天压力） | ≈ 恢复分 | — | 不重复做 |
| | Body Battery（全天 0–100 的能量电量） | ❌ | 🟡 Apple Watch 白天 HRV 太稀疏 | P2，只能粗略近似，不建议 |
| | Recovery Time（还要几小时恢复） | ❌ | 🟡 | P2 |
| | Fitness Age | ✅ 身体年龄 | ✅ | 保持 |
| **Apple** | Vitals（夜间心率 / 呼吸 / 体温 / 血氧 / 睡眠，2 项以上异常提示） | ✅ 健康监测 | ✅ | 保持 |
| | Training Load（每次训练 1–10 主观强度，7 天 vs 28 天） | ❌ | 🟡 库里有 `physical_effort` | P1，可以和训练状态共用 7 / 28 天比较 |
| | Sleep Score（watchOS 26：时长、入睡规律、中断） | 🟡 OW 睡眠分 | ✅ | 不重复做 |
| | 日照时间 | ❌ 没展示 | ✅ `time_in_daylight` | **P1**，作息不稳定，可以放进趋势 |
| | Hypertension Notifications | ❌ | ❌ 手表端算法 | 做不了 |
| | Cardio Recovery（运动后 1 分钟心率下降） | ❌ | ❌ 还没同步这个类型 | P2，要 OW / 快捷指令先加 |
| **Fitbit** | Daily Readiness、Sleep Score | ≈ 恢复分、睡眠分 | — | 不重复做 |
| | Active Zone Minutes（中等区间 ×1、高强度 ×2，每周 150 分钟） | ❌ | ✅ 心率区间分钟 | **P1**，WHO 每周目标好理解 |
| | Stress Management Score | ≈ 压力 | — | 不重复做 |
| | Cardio Load / Target Load | ≈ strain + 负荷目标 | — | 已有 |
| **Polar** | Nightly Recharge（ANS charge −10~+10：心率、HRV、呼吸 vs 28 天基线 + 睡眠） | ≈ 恢复分 | — | 不重复做 |
| | Training Load Pro（心血管负荷、肌肉负荷、主观负荷 sRPE，负荷状态 4 档） | 🟡 有心血管 + 肌肉负荷 | 🟡 | 归入训练状态（P1） |
| | Orthostatic Test、Running Index | ❌ | ❌ | 不做 |

---

## 4. 建议和优先级

### P0：把 WHOOP 已有的指标全部放进「趋势」tab（数据都有，主要是聚合 + 画图）
1. **新卡片**：呼吸频率、血氧、手腕温度偏差、睡眠需求 vs 实际、睡眠债、睡眠表现 %、恢复性睡眠（深睡 + REM，时长和占比）、每日压力、步数、活动 / 总消耗、CTL / ATL / TSB、身体年龄（每周）、夜间醒来次数。
2. **恢复**：柱子按绿 / 黄 / 红着色 + 期间绿黄红天数；点开看贡献项（HRV / 静息心率 / 呼吸 / 睡眠表现相对基线）。
3. **负荷**：每天的 strain 加上 Strain Coach 目标区间带，显示达标天数；再加一张「恢复 vs 负荷」叠加图。
4. **时间范围**：7 / 30 / 90 / 180 / 365 天（WHOOP 的 1 周 / 1 月 / 6 月 + 1 年）；每张卡显示期间均值和「vs 上期」↑↓（今天页的规矩：只放数字和短标签）。
5. **分组**：恢复 / 睡眠 / 负荷 / 压力 / 长期 五组，顺序和 WHOOP 一样；还是以选中的日期为结束点。
6. **后端**：`/trends` 加对应的 series（`daily_scores` 和 `daily_metrics` 里已有），压力和负荷目标复用 insights 模块的算法；加测试。

### P1：新指标，算法小、用得上（建议这次一起做，看你的意见）
7. **HRV 状态**（Garmin）：7 天均值落在 60 天基线带里 = 平衡，低于 = 偏低，带外波动大 = 不平衡。
8. **训练状态**（Garmin / Polar / Apple Training Load）：急慢性负荷比（7 天 / 28 天）+ VO₂max 趋势 → 恢复中 / 保持 / 有效 / 过度 / 去适应；顺带 Load Focus（低有氧 / 高有氧 / 无氧分钟）。
9. **有氧分钟 / 周**（Fitbit AZM / WHO 150 分钟）：中等区间分钟 ×1，高强度 ×2。
10. **Resilience**（Oura）：14 天压力和恢复的平衡，5 档。
11. **日照时间趋势**（Apple）。
12. **Behavior Insights** 放进「报告」。

### P2：以后再说（要新数据、口径要研究或收益不大）
WHOOP 新版 Sleep Performance 综合分、Strain 合并肌肉负荷、Recovery Index、生病预警综合分、Chronotype、Body Battery、Recovery Time、运动后心率恢复。

### 做不了
ECG、血压数值、Cardiovascular Age（要 PPG 波形）、高血压通知（手表端算法）、智能闹钟 / 震动、临床医生视频 / 病历导入。

---

## 5. 要你确认的
1. P0 全部做？（第 4 条的 7 天 / 1 年范围、第 5 条的分组顺序可以调）
2. P1 里哪些这次一起做？（推荐 7 HRV 状态、8 训练状态、9 有氧分钟；10–12 可以下一轮）
3. P2 都先不做，可以吗？

## 来源
- WHOOP：[2026 What's Next](https://www.whoop.com/us/en/thelocker/2026-whats-next/)、[2026 What's New](https://www.whoop.com/ae/en/thelocker/2026-whats-new/)、[临床医生和 AI 新功能](https://www.whoop.com/ae/en/press-center/whoop-expands-health-platform-with-on-demand-clinician-access-and-new-ai-features/)、[Android Authority 报道](https://androidauthority.com/whoop-on-demand-clinician-access-in-app-3665264/)
- Oura：[Readiness Score 帮助中心](https://support.ouraring.com/hc/en-us/articles/360057791533)、[Readiness Score](https://ouraring.com/readiness-score)
- Garmin：[Training Readiness 手册](https://www8.garmin.com/manuals/webhelp/GUID-9183E86B-2399-4CFC-AB50-EAFC6D6ED326/EN-US/GUID-C21BE0C8-A08E-4DA1-B6C6-2E0E2DDDB372.html)、[HRV 和 Training Readiness](https://www.garmin.com/en-MY/blog/new-features-on-garmin-smartwatches-help-to-maximize-your-training/)
- Apple：[About watchOS 26 Updates](https://support.apple.com/en-ie/123002)、[9to5Mac](https://9to5mac.com/2025/11/25/watchos-26-brings-two-powerful-health-features-for-apple-watch/)
- Fitbit、Polar 和 WHOOP 早期功能（Sleep Performance 口径、Strength Trainer、Active Zone Minutes、Nightly Recharge、Training Load Pro）按截至 2026-06 的公开资料整理，没有逐条联网核对。
