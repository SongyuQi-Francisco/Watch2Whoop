# 身体管理系统 · 系统设计 v0.8（2026-10-17）

> 基于开源项目二次开发，不从零写。先在本地 Mac 上部署当服务器；按多用户、可迁移来设计，将来可以给家人用、可以搬上云。
> v0.1（从零开发）、v0.2（单人局域网版）已作废。v0.4：确认第 11 节剩余决定，加入 AI 助手模块（第 12 节），记录第 0–2 阶段进度。v0.5：OW 在本机部署好，export.xml 已导入 OW，Body Engine 改为以 OW 为准（第 9、10 节）。v0.6：规则提醒引擎（第 13 节）、肌肉负荷（第 7 节，algo v1.1）、SparkyFitness 动作 ID 对上 exercise_meta、日记分析加次日恢复分。v0.7：今天页（PWA，第 14 节）、Grafana 上线（第 9 节）。v0.8：Watch2Whoop iOS app（第 17 节）：手机上一个 app 同时做后台同步和今天页，取代 OW 示例 app + 快捷指令。选型依据是 2026-10-16 的调研，摘要见附录。

## 0. 产品原则（2026-10-17 用户定）
- **单一入口**：每台设备只有一个入口——手机是 **Watch2Whoop iOS app**（内嵌今天页 + 后台同步健康数据，第 17 节；TestFlight 发布前用 PWA 加到主屏幕），电脑是今天页（浏览器）。记录和查看任何东西都不跳转到别的 app 或网页。SparkyFitness、Open Wearables、Grafana 等开源项目只作为后台，不让用户直接用；需要的功能由 Body Engine 接口 + 今天页界面提供。
- 例外：订阅类 AI（Claude、ChatGPT/Codex…）只能在官方客户端里用，对话在用户自己的客户端里（连 Body Engine 的远程 MCP）；用自己 API key 的用户在今天页内直接对话（`docs/ai-assistant-plan.md` 第 9 节）。
- 简约：少字、数字说话、细节点开再看；详细文字是给 AI 管家看的底层数据，不在前台展示。

## 1. 需求
1. 可标准化的数据进数据库，`body-management.md` 只保留叙述性内容。
2. 有前台（手机 + 电脑），日常记录和查看不必通过对话。
3. Apple Watch 当数据采集器，做出类似 WHOOP 的功能：恢复分、负荷分、睡眠需求 / 睡眠债、行为日记。用户是 **Series 12**（watchOS 27 / iOS 27）。
4. 规则自动化：双进阶加重提示、按周均体重调热量、练前进食提醒、频率上限。
5. **多用户**：将来给家人用，每人一个账号、数据各自独立，可授权互看；家人可能戴其他品牌的手表。
6. **部署**：现在只在本地 Mac 上跑（云端太贵，暂不考虑），设计上保持随时能搬上云。
7. 所有组件都有 API，方便 Claude 读写。
8. **AI 助手**：以后把 Claude 接进来当助手，能查数据、记日记、给建议（第 12 节）。

## 2. 已定的选型
| 模块 | 选用 | 理由 |
|---|---|---|
| 可穿戴数据采集（所有家人） | **Open Wearables**（MIT，真开源） | 一套部署管一家人；iOS SDK 用 HealthKit 后台增量同步（anchor + 重试）；支持心率原始样本、静息心率、SDNN、**RMSSD（iOS 27+）**、睡眠阶段、训练记录和 effort score；API 能取原始分辨率数据，有 webhook；还能接 Garmin、Oura、Whoop、Withings、Polar、Google Health / Health Connect 等，家人戴别的表也能接 |
| WHOOP 式评分、化验、补剂、日记、事件、规则 | **自研 Body Engine**（Python FastAPI + Postgres） | 没有现成项目覆盖；公式借用 vitals（MIT）和 Open Wearables 的 Sleep Score 规格 |
| 你自己的趋势看板 | **Grafana OSS** | 只给你用。它做不到真正的"每人只看自己的数据"，给家人看的图表放进 Body Engine 的网页 |
| 训练和饮食记录（所有家人） | **SparkyFitness**（2026-10-16 用户选定，求快） | 一个 app 覆盖饮食日记、训练（组数、次数、重量、RIR、进阶模式、计划）、体重；家人分享（7 种权限，beta）；原生 OIDC；iOS app；中文。非商业许可：能 fork、修改、自用和免费给家人用，不能商用。**少改核心**，Body Engine 通过 API / 只读数据库角色读数据 |
| 远程访问 | **Tailscale**（个人免费版最多 6 个用户） | Mac 不对公网开端口；端到端加密；能给 Mac 签发 HTTPS 证书（有些 iOS app 只连 HTTPS） |
| AI 助手 | **Body Engine 的 assistant 模块** + Claude（Anthropic API / MCP） | 工具定义只写一份，应用内对话走 Claude API，你自己的 Claude Code / 桌面版走 MCP（第 12 节） |
| 统一登录（家人加入时再上） | **Pocket ID**（BSD-2，passkey 登录，已通过 OIDC 认证） | 轻量；各 app 都能接 OIDC（wger 要通过 oauth2-proxy 用 header 方式） |

**为什么不用 SparkyFitness 采集 Apple Watch 数据**：它的 iOS app 能后台同步 HealthKit，但心率和 HRV 只按天上传最小值、最大值、平均值，没有 RMSSD，算不了恢复分和负荷分。

## 3. 训练和饮食记录 app：已选方案 A（2026-10-16）
用户选择「先快」→ **SparkyFitness**。以下对比留作记录；以后如果要商用或开放给外人，再考虑换成方案 B。
| | 方案 A：SparkyFitness 一个 app | 方案 B：wger + NutriTrace / CookTrace |
|---|---|---|
| 覆盖 | 饮食日记、训练（组数、次数、重量、RIR、进阶模式、计划）、体重、家人分享（beta）、OIDC、iOS app、中文 | wger 训练最强（进阶规则、iOS app、中文）；NutriTrace（饮食日记）+ CookTrace（菜谱、库存、购物清单） |
| 许可证 | **作者自定义的非商业许可，不是 OSI 开源**。能看源码、fork、修改、自用和免费给家人用；改过的版本必须沿用同一许可；不能商用 | wger、NutriTrace、CookTrace 都是 AGPL-3.0，真开源，可以商用（修改后对外提供服务要公开源码） |
| 家人体验 | 家人只装一个 app | 家人要用 2–3 个 app / 网页 |
| 风险 | 单人维护、更新快、常有不兼容改动；家人分享还是 beta | NutriTrace / CookTrace 是 2026 年的新项目，只有 PWA；家人分享和统一登录要自己补 |
| 二开方式 | 尽量不改核心，Body Engine 通过 API / 只读数据库角色读数据 | 同左 |

两种方案下，Tandoor（菜谱、购物清单、库存）都可以作为第二阶段的可选项。

## 4. 架构
```
 家人的 iPhone / 手表                         你的 MacBook Pro（Docker，Tailscale）
 ┌─────────────────────────┐            ┌────────────────────────────────────────────┐
 │ Open Wearables app       │──HTTPS───▶│ Open Wearables（采集层，所有人的原始数据）    │
 │（HealthKit 后台同步）     │            │   FastAPI / Postgres / Redis / Celery       │
 │ SparkyFitness app        │──HTTPS───▶│ SparkyFitness（饮食、训练、体重）                        │
 │ Safari：今天页           │──HTTPS───▶│ Body Engine（评分、规则、日记、化验、今天页） │
 └─────────────────────────┘            │   ↑ 通过 API / webhook 读 OW 和记录 app      │
                                         │ Postgres（body 库）◀── Grafana（只给你）      │
 其他品牌手表 ──云端接口──▶ Open Wearables │ Pocket ID（统一登录，家人加入时）             │
                                         └────────────────────────────────────────────┘
```

## 5. 每类数据只认一个来源
| 数据 | 唯一来源 | 说明 |
|---|---|---|
| 手表原始数据（心率、HRV、睡眠、训练、步数、能量、体重等） | Open Wearables | Body Engine 只存派生结果，需要时按天拉原始数据计算，不复制全量原始数据 |
| 饮食、训练记录（动作、组、重量） | 记录 app（SparkyFitness） | Body Engine 经 API 现读；训练日历按天和手表的训练合并（第 16 节） |
| 手表 / 快捷指令的训练（时间、时长、能量、心率） | Open Wearables（OW app 暂停时快捷指令补） | 复制进 `workouts`（要按训练时段算心率区间 / TRIMP），第 16 节 |
| 评分、日记、补剂、化验、症状事件、动作补充信息、分享授权 | Body Engine | — |

## 6. Body Engine 表设计（多用户）
所有业务表都带 `user_id`；接口按登录用户鉴权；查看他人数据要先查 `shares`。
**已实现的版本以 `body_engine/src/body_engine/migrations/` 为准**，比下面的初稿多了：`api_tokens`（每人独立 token，只存 sha256）；`daily_metrics` 加 `min_value`、`max_value`、`source`、`updated_at`；`sleep_sessions` 加 `unspecified_min`、`device` 和唯一键 `(user_id, source, start_at)`；`assistant_audit`（第 12 节）。
`030_health_records.sql`（健康档案，2026-10-16）：补剂加 `advice`（当前建议）、status 扩成 active / as_needed / paused / stopped / not_needed；化验加 `test_key`（跨报告统一键，看趋势）、`category`，flag 限定 H / L / abnormal / normal；事件加 `time_precision`（minute / day / approx / month）、`resolved_on`；日记项加 `category`、`description`、`sort`；动作加 `name_en`、`aliases`、`reason`、`app_exercise_id`（记录 app 的动作 ID，`exercise_ref` 先用英文 slug）；补剂、化验、事件都加 `import_key`（迁移用，(user_id, import_key) 唯一）和 `created_at` / `updated_at`。
`050_grafana_ro.sql`（2026-10-17）：只读角色 `grafana_ro`（NOLOGIN + 只授权看板要用的表，密码由 `deploy/grafana/bootstrap.py` 设），见第 9 节。
`050_web_sessions.sql`（今天页，2026-10-17）：`web_sessions`（user_id、token_id → api_tokens、session_sha256、user_agent、expires_at；token 吊销 / 删除时会话失效），见第 14 节。
`070_sleep_goals.sql`（入睡时间目标，2026-10-17）：`sleep_goals`（每人一行：target_bedtime、target_wake、target_duration_min、pace_min_per_step、step_days、start_date、start_bedtime_off、status active / paused），见第 13.1 节。
`080_workouts.sql`（训练历史，2026-10-17）：`workouts`（每次训练一行：source ow / shortcut、external_id、type、name_zh、local_date、start_at / end_at、duration_min、active_kcal / total_kcal、distance_km、avg_hr / max_hr、trimp、hr_zone_min、dup_of（副本指向保留的那条）、excluded、flags；唯一键 (user_id, source, external_id)），见第 16 节。
`040_reminders.sql`（规则提醒，2026-10-16）：新表 `reminders`（user_id、date、rule_key、subject、level info / warning / alert、title、message、evidence JSONB、status new / seen / dismissed、rules_version，(user_id, date, rule_key, subject) 唯一）和 `rule_runs`（每次评估每条规则的结果），见第 13 节。

```sql
CREATE TABLE users (
  id SERIAL PRIMARY KEY, display_name TEXT NOT NULL,
  oidc_sub TEXT UNIQUE,                 -- Pocket ID 的用户标识（家人加入后启用）
  ow_user_id TEXT UNIQUE,               -- 对应 Open Wearables 里的 user
  role TEXT NOT NULL DEFAULT 'member',  -- owner / member
  birth_date DATE, sex TEXT, height_cm REAL, timezone TEXT DEFAULT 'Australia/Sydney',
  consent_at TIMESTAMPTZ                -- 家人的书面同意时间
);
CREATE TABLE shares (                  -- A 授权 B 查看哪些内容
  owner_id INT REFERENCES users(id), viewer_id INT REFERENCES users(id),
  scopes TEXT[] NOT NULL,               -- 例：{scores, sleep, training}
  expires_at TIMESTAMPTZ, PRIMARY KEY (owner_id, viewer_id)
);

CREATE TABLE daily_metrics (           -- 从 OW 汇总的每日指标，可重算
  user_id INT REFERENCES users(id), date DATE, metric TEXT,
  value DOUBLE PRECISION, unit TEXT, agg TEXT, n INT,
  PRIMARY KEY (user_id, date, metric)
);
CREATE TABLE sleep_sessions (
  id BIGSERIAL PRIMARY KEY, user_id INT REFERENCES users(id), wake_date DATE NOT NULL,
  start_at TIMESTAMPTZ, end_at TIMESTAMPTZ,
  asleep_min INT, core_min INT, deep_min INT, rem_min INT, awake_min INT,
  is_main BOOLEAN NOT NULL DEFAULT TRUE, source TEXT
);
CREATE TABLE daily_scores (
  user_id INT REFERENCES users(id), date DATE,
  sleep_score INT, sleep_need_min INT, sleep_debt_min INT, sleep_performance DOUBLE PRECISION,
  hrv_kind TEXT,                        -- rmssd / sdnn（同一个人不混用）
  hrv_night_ln DOUBLE PRECISION, rhr_night DOUBLE PRECISION, recovery INT,
  trimp DOUBLE PRECISION, strain DOUBLE PRECISION, muscle_load DOUBLE PRECISION,
  ctl DOUBLE PRECISION, atl DOUBLE PRECISION, tsb DOUBLE PRECISION,
  flags JSONB, algo_version TEXT NOT NULL,
  PRIMARY KEY (user_id, date)
);

CREATE TABLE exercise_meta (           -- 补记录 app 没有的动作字段
  user_id INT REFERENCES users(id), exercise_ref TEXT,   -- 记录 app 里的动作 ID
  name_zh TEXT, status TEXT NOT NULL DEFAULT 'active',   -- active / caution / banned
  per_side BOOLEAN NOT NULL DEFAULT FALSE, cues TEXT,
  PRIMARY KEY (user_id, exercise_ref)
);

CREATE TABLE journal_items (
  user_id INT REFERENCES users(id), key TEXT, label_zh TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'bool', unit TEXT, active BOOLEAN NOT NULL DEFAULT TRUE,
  PRIMARY KEY (user_id, key)
);
CREATE TABLE journal_entries (         -- 没记录 = 缺失，不等于"否"
  user_id INT, date DATE, key TEXT, value DOUBLE PRECISION NOT NULL, source TEXT, note TEXT,
  PRIMARY KEY (user_id, date, key),
  FOREIGN KEY (user_id, key) REFERENCES journal_items(user_id, key)
);

CREATE TABLE supplements (
  id SERIAL PRIMARY KEY, user_id INT REFERENCES users(id),
  name TEXT NOT NULL, dose TEXT, timing TEXT, ingredients TEXT,
  status TEXT NOT NULL,                 -- active / paused / not_needed
  started_on DATE, stopped_on DATE, rationale TEXT, journal_key TEXT
);
CREATE TABLE lab_results (
  id SERIAL PRIMARY KEY, user_id INT REFERENCES users(id), collected_on DATE NOT NULL,
  test_name TEXT NOT NULL, value DOUBLE PRECISION, value_text TEXT, unit TEXT,
  ref_low DOUBLE PRECISION, ref_high DOUBLE PRECISION, flag TEXT, lab TEXT, note TEXT
);
CREATE TABLE events (
  id SERIAL PRIMARY KEY, user_id INT REFERENCES users(id), occurred_at TIMESTAMPTZ NOT NULL,
  category TEXT, body_part TEXT, severity INT, title TEXT NOT NULL, note TEXT
);
CREATE TABLE sync_state (
  user_id INT, source TEXT, last_synced_at TIMESTAMPTZ, cursor TEXT,
  PRIMARY KEY (user_id, source)
);
```

## 7. WHOOP 式评分（第一版算法）
实现顺序：睡眠 → 恢复 → 负荷 → 日记。每个分数记录 `algo_version`；**每个人、每种指标各自建基线**。
**睡眠、恢复、负荷已实现（2026-10-16，`algo_version = v1.0-2026-10-16`；v1.1 加肌肉负荷，其余不变）**：代码 `body_engine/src/body_engine/scores/`（参数全在 `params.py`），表 `daily_scores`（迁移 `010_scores.sql` 加了 `sleep_actual_min`、`resp_rate_night`、`hr_max`、`details`、`computed_at`），命令 `body-engine compute-scores`，接口 `GET /v1/users/{user}/scores`（shares scope `scores`），助手工具 `get_scores`。实现细节和与设计的差异见本节末尾「实现说明」。

**睡眠**
- 只用手表来源的睡眠数据，算总睡眠、各阶段时长、夜间清醒、入睡时间。
- 睡眠分：照 Open Wearables 的 Sleep Score 规格（时长 40%、阶段 20%、规律性 20%、中断 20%），0–100。
- 睡眠需求 = 基线 8h（之后用个人数据校准）+ 最近 14 晚加权睡眠债 + 前一天负荷加成；睡眠表现 = min(实际 / 需求, 1)。

**恢复（0–100）**
- HRV：**Series 12 优先用 RMSSD**（iOS 27 新类型，Bevel 等已在读取，Apple 没有正式说明哪些表会写入，要实测确认）；没有就用 SDNN。同一个人不混用两种。
  - **2026-10-18 更新**（`docs/raw-data-plan.md`）：RMSSD 不必等 OW / iOS 新类型——Apple 每次 HRV 读数都存了约 1 分钟的逐拍时间，`rawdata/hrv.py` 用它算 RMSSD（表 `heartbeat_series`，迁移 150；历史从 export.xml 回算，`import-raw` / 历史导入）。`params.HRV_KIND` 可选 `sdnn` / `rmssd`，切换前先跑 `hrv-compare` 给用户看（本机 2025-08-28 到 2026-10-13：相邻两晚相关 SDNN 0.04 → RMSSD 0.28，恢复分和静息心率的相关 −0.40 → −0.53）。每晚读数次数不会变多（Apple 决定，实测 1–4 次）。
  - 训练后心率恢复 HRR60（`cardio/hrr.py`，表 `workout_hrr`）：训练最后 2 分钟最高心率 − 结束后 60 秒心率，峰值 ≥ 75% HRmax 才算有效；`compute-scores` 顺带写。
- 当晚 HRV = 睡眠窗口内读数取 ln 后的中位数；当晚静息心率 = 睡眠期最低 5 分钟均值。
- 各项和 60 天基线比算 z 分数：HRV 0.6、静息心率 0.2（反向）、睡眠表现 0.15、呼吸频率 0.05（反向），再用 logistic 映射到 0–100。
- 满 14 晚才出分；手腕温度和血氧只做异常标记。生病提示：静息心率 > 基线 + 5 且 HRV < 0.85 × 基线。
- Apple 自带的 Readiness 和 Sleep Score **读不到**（HealthKit 没有对应类型，Apple 论坛已确认），只能在手机上肉眼对照。

**负荷（0–21）**
- 用原始心率样本。Series 12 官方说全天每 5 秒测一次，但写进 HealthKit 的粒度要实测（有报告约 30 秒）；一个样本里可能装着一串数值，要用 series 查询展开。
- %HRR = (HR − 静息) / (HRmax − 静息)；HRmax 取「观测值 99.5 百分位」和「208 − 0.7 × 年龄」中较大的一个。
- Edwards TRIMP（50/60/70/80/90% HRmax 五区，分钟 × 权重 1–5）；strain = 21 × (1 − e^(−TRIMP/K))，K 校准到常规训练落在 10–14。
- iOS 27 的 `HKWorkoutZoneGroup`（心率区间时长）可以直接用。
- 力量训练心率会低估负荷：另算肌肉负荷 = workoutEffortScore（或主观 RPE）× 分钟数。
- 长期：CTL（42 天 EWMA）、ATL（7 天 EWMA）、TSB = CTL − ATL。

**日记**
- 每天勾选前一天的行为：补剂、咖啡、酒、桑拿、晚饭太晚、练腿等。
- 结局：次日恢复分（或 ln HRV 的 z 分数）。先比较两组均值；再做 OLS（控制前一天负荷、睡眠时长、星期几），用 Newey-West（HAC）处理自相关；多个行为一起检验时做 BH 校正。
- 90 天内「是」「否」各 ≥ 5 次才显示，15–20 次以上才可信；天天吃的补剂（如肌酸）无法分析。
- **第一版已实现（2026-10-16，`records/analysis.py`）**：两组均值比较，结局 = 次日静息心率、次日 ln HRV（SDNN 日均值）；给出差值、Welch t、Cohen's d、正态近似 95% 区间；各 < 5 天不出结果，各 ≥ 15 天标 reliable。没记录的日子不算「否」。次日恢复分已加进 `OUTCOMES`（2026-10-16，排第一）；OLS + Newey-West + BH 是第二版。
- 默认日记项（`records/defaults.py`，每人一套，`journal-init` 建）：饮食（咖啡、12 点后咖啡因、喝酒、晚饭太晚、红肉 g、喝水 L、练前没吃够）、训练（上肢、练腿、爬坡、篮球、游泳、睡前 3 小时内练完、肩部 routine）、生活（桑拿、早上晒太阳、午睡、睡前看手机、压力大）、症状（低血糖感、极度口渴、生病、肩疼）；每种补剂一项（`supp_*`，加补剂时自动建）。

### 实现说明（v1.0-2026-10-16）
**运行**：`body-engine compute-scores --user-id N [--start --end]`。每次都从评分起点（第一天有 `resting_heart_rate` = 开始戴手表，Alex 是 2025-08-28）整段重算，只删除并重写 `--start`～`--end` 的行，所以重复跑结果一样（幂等）、部分重写和整段重写一致。整段约 20 秒（大头是从 OW 拉 17 万条心率）。每天都写一行，算不出的分数留空，原因写在 `flags`（`{代码: 中文说明}`），各分项、z 分数、基线、心率区间分钟数在 `details`。
**数据来源**：睡眠段读 `sleep_sessions`（导入器已切好段）；睡眠阶段明细、原始心率 / HRV / 呼吸 / 血氧 / 手腕温度从 OW 拉（`/timeseries?resolution=raw`、`/events/sleep?include=stages`），OW 没有的天用快捷指令存档补（HRV、呼吸、血氧、睡眠阶段；快捷指令的心率是按小时求和，不用）。同一条样本（类型 + 开始时间）只留一条，OW iPhone app 同步的优先于 export.xml。

| 部分 | 实现 / 和设计的差异 | 参数 |
|---|---|---|
| 手表来源 | OW 的 XML 导入不存睡眠来源，用「有阶段数据（core/deep/rem）或设备名含 Watch」判断；只有「睡着」没有阶段的段（iPhone / 第三方 app）不计。每个醒来日期最长的手表段是主睡眠 | — |
| 睡眠分 | 逐行移植 OW 0.9.0 `algorithms/sleep.py` + `config_algorithms.py`。时长按净睡眠；中断用阶段明细去掉入睡前 / 醒后赖床的清醒；**相邻的清醒片段合并成一次醒来**（OW 不合并）；规律性用前 15 天内最近 14 个主睡眠的入睡时间中位数（OW 的窗口），前 15 天没有主睡眠时按规格给 0（`consistency_no_history`）。拿不到阶段明细时用整段清醒分钟、醒来次数按 0 | 7–9h 满分；深睡 / REM 目标各 90 分钟；晚睡 15 分钟起扣、再 105 分钟扣光，早睡最多扣 20；清醒 20 分钟内不扣、再 70 分钟扣光；> 5 分钟算一次醒来，0–1 次满分、4 次以上 0 |
| 实际睡眠 | 主睡眠 + 上一个主睡眠结束后、这次开始前（最多 24h）的小睡 | — |
| 睡眠债 | 最近 14 晚「8h − 实际睡眠」的**加权平均**（权重 0.75^(k−1)，按有数据的晚上归一化，睡多可以抵消，下限 0）；没数据的晚上不计。`sleep_debt_min` 是进入当晚睡眠前的值 | — |
| 睡眠需求 | 8h + 睡眠债（最多加 2h）+ 前一天 strain 超过 7 的部分每 1 分加 3 分钟（strain 21 → +42 分钟）；前一天没负荷就不加（`prev_strain_missing`）。基线 8h 还没用个人数据校准 | — |
| HRV | `hrv_kind = sdnn`（RMSSD 等 OW 0.10）。睡眠窗口内读数 ln 后取中位数；Apple 夜里只测 1–3 次（124 晚只有 1 条），**单条读数的晚上很吵**（见下面「已知问题」） | — |
| 当晚静息心率 | 睡眠期最低 5 分钟均值：手表睡觉时约 4–5 分钟才一条心率，每条代表到下一条（最多 10 分钟），按时间加权；窗口里被覆盖不到一半不算，都不够时取最低单条。没有睡眠期原始心率时退回当天 Apple 的 `resting_heart_rate`，**和它自己的 60 天基线比**（Apple 全天值比睡眠最低值高约 6 bpm，混着比会误报生病），标 `rhr_from_daily` | — |
| 恢复分 | 前 60 天（不含当天）的均值 / 标准差算 z，标准差有下限、z 截断在 ±3；缺的项把权重按比例分给其他项（HRV 必须有）；recovery = 100 / (1 + e^−(1.6 × 加权 z + 0.3))：和基线持平 57，+0.5 → 75，−0.5 → 38。前 60 天 HRV 不满 14 晚不出分（`baseline_insufficient`） | 权重 HRV 0.6、静息心率 0.2、睡眠表现 0.15、呼吸 0.05；标准差下限 ln HRV 0.05、心率 1、表现 0.05、呼吸 0.5 |
| 异常标记 | 生病（`illness_warning`）照设计；手腕温度比 60 天中位数偏 ≥ 0.6°C（至少 5 晚基线）；当晚血氧中位数 < 94% 或 < 90% 的读数 ≥ 2 条（`spo2_low`）。只标记，不进分数 | — |
| HRmax | 观测 99.5 百分位用前 365 天（含当天）的全部心率读数；读数大多是静息，百分位只有 160 左右，实际一直是公式值（25 岁 190.5） | — |
| TRIMP | 用 %HRmax 五区（设计里的 %HRR 没用上）。每条心率代表到下一条、最多 5 分钟（平时 2–5 分钟一条，训练中 5 秒一条）；按本地日期算整天，不只算训练时段 | 区间 50/60/70/80/90% HRmax，权重 1–5 |
| strain | **K = 170**：Alex 有手表训练记录的力量 / 篮球 / 游泳日（20 天）整天 TRIMP 中位数约 145 → strain 12；四分位约 87–220 → 8.3–15。休息日 TRIMP 中位数约 20 → 2.3（WHOOP 把低心率也算进去，休息日会高一些） | — |
| 覆盖不足 | 一天里有心率的小时数 < 12：录到训练（TRIMP ≥ 40）的照常出分、标 `hr_coverage_low`（可能偏低）；否则 strain 留空（`no_raw_hr`，部分值在 `details.load.trimp_recorded`）。今天（`day_in_progress`）照常出分 | — |
| CTL / ATL / TSB | 用 TRIMP；α = 1 − e^(−1/τ)；初值 = 最初 14 个有数据日的 TRIMP 均值，前 42 天标 `ctl_warmup`；没 TRIMP 的天沿用前一天（`load_missing`） | τ = 42 / 7 天 |
| 肌肉负荷（v1.1） | **从 SparkyFitness 训练记录算**（`scores/muscle.py`；OW 0.9.0 没有 workoutEffortScore）：`muscle_load` = 当天力量动作工作组 Σ 次数 × 重量（kg，训练容量）。重量按记录的数（哑铃单只、单侧动作按每侧），是同一个人前后可比的相对量；热身组不算，自重组只计组数。有 RPE + 时长时另算 session RPE（平均 RPE × 分钟）放 `details.muscle.srpe`，单位不同不相加（现在的记录都没有 RPE）。`details.muscle` 还有工作组数、各肌群组数。当天没有训练记录 → 留空（没记录 ≠ 没练）；连了 SparkyFitness 但读不到 → 标 `muscle_load_unavailable`，其他分数照算；没连的用户不算。`compute-scores` 每次一起重算 | — |
| 手腕温度 / 血氧 | 只做标记；手腕温度一年只有 21 晚数据 | — |

**回算结果（2025-08-28 → 2026-10-16，415 天）**
| 分数 | 有值天数 | P5 | P25 | 中位数 | P75 | P90 |
|---|---|---|---|---|---|---|
| 睡眠分 | 336 | 45 | 69 | 82 | 91 | 95 |
| 睡眠表现 | 336 | 0.41 | 0.59 | 0.75 | 0.88 | 1.00 |
| 实际睡眠（分钟） | 336 | 227 | 330 | 410 | 475 | 542 |
| 睡眠债（分钟） | 336 | 0 | 36 | 67 | 112 | 147 |
| 睡眠需求（分钟） | 336 | 480 | 519 | 550 | 594 | 600 |
| 恢复分 | 317 | 17 | 36 | 58 | 75 | 86 |
| 当晚 HRV（SDNN, ms） | 331 | 25 | 34 | 43 | 57 | 75 |
| 当晚静息心率 | 336 | 44 | 47 | 49 | 52 | 53 |
| strain | 366 | 0 | 0.6 | 2.3 | 6.1 | 11.2 |
| TSB | 415 | −49 | −11 | 3 | 13 | 21 |

- 恢复分：绿（≥ 67）121 天、黄 124 天、红（< 34）72 天。
- 缺失：没有手表睡眠 79 天（其中 22 天只有非手表的睡眠）→ 睡眠分、恢复分都空；HRV 基线不满 14 晚 14 天（最开始两周）、睡眠窗口没有 HRV 5 天；strain 空 49 天（14 天完全没心率，35 天心率不足 12 小时又没录到训练），另有 12 天覆盖不足但录到训练照常出分。2026-10-14 之后：10-14 只有凌晨的 export.xml 心率 → 空；10-15、10-16 有 OW iPhone app 同步的部分心率（10-15 只录到晚上训练那 2 小时，strain 4.4 偏低）。
- 生病提示 4 天：2025-12-30、2026-01-06、05-04、07-24。血氧偏低 9 晚（集中在 2026-07-20～26）。手腕温度没有触发。

**抽查（对照 `body-management.md`）**
- 9/7～9/30「训练一开始就全身疲惫、早醒、减重太快」：这段睡眠分均值 62（8 月 74、10 月 75），主睡眠 4.9h（另有白天小睡，实际 6.2h），strain 均值 7.5（8 月 3.4、10 月 3.3），TSB 均值 −26，9-16 低到 −70（9-13～9-16 连续四天 strain 15–16）。恢复分在连续高强度后从 9-13 的 95 掉到 9-14 的 17。和记录的疲劳期对得上。
- 训练日：9-13 肩 16.2、9-15 背 15.2、9-16 腿（爬坡 40 分钟）15.5、9-18 胸 12.2、10-10 背 10.4；9-21 / 9-24 / 9-30 的短训练（25–30 分钟、平均心率约 100）只有 4.4 / 6.2 / 8.4——心率低估力量训练，要靠以后的肌肉负荷。10-12 肩：手表没数据，空。
- 10-13 只睡 4.6h（早上 9 点才睡）：睡眠分 45、表现 0.56、恢复 42（黄）。10-14「凌晨 4 点还没睡」（6:30 才睡）：规律性 0，睡眠分 73。
- 10-15 练腿时低血糖：当天早上恢复 80（绿）、睡 7.9h，说明低血糖和恢复无关，和 4.2.1 的判断（空腹训练、没吃主食）一致。

**已知问题 / 以后**
- SDNN 每晚只有 1–3 条读数，偶尔一条很高（如 9-30 的 183 ms）会把恢复分推到 90 以上，即使睡眠很差（9-24：睡眠分 37、恢复 93）。等 OW 0.10 的 RMSSD（夜间读数更多）切过去：改 `HRV_KIND`、升 `ALGO_VERSION`、整段重算，基线自然重建，不混用。
- 规律性分经常是 0（入睡时间常在凌晨 1 点到早上 9 点之间跳），这是 OW 规格本身对作息不规律很严格，符合实际。
- 睡眠需求基线 8h、K = 170、恢复 logistic 的 1.6 / 0.3 都是第一版取值，攒够 1–2 个月 RMSSD 和主观感受后再校准。
- 2026-10-14 之后要有 strain，需要原始心率：OW iPhone app 稳定同步，或者按 `docs/shortcut-raw-heart-rate.md` 让快捷指令上传原始心率（未实施）。
- 增量：现在每次整段重算（约 20 秒）；数据多几年后再改成从上次结果续算。

### 身体年龄和其他分析（`insights/`，2026-10-17）
对标 WHOOP 的其余分析功能，逐项对照见 `docs/whoop-feature-map.md`。代码 `body_engine/src/body_engine/insights/`，表 `healthspan_weekly`、`stress_daily`（`100_insights.sql`；`101_healthspan_pace.sql` 加 `pace_window_weeks`），`compute-scores` 跑完评分后顺带重算身体年龄（每周快照，整段几秒）和最近 90 天的压力；其余功能从 `daily_scores` 现算。接口都是只读，shares scope `scores`（睡眠细项用 `sleep`），管家工具同名 `get_*`（`assistant/tools.py` 注册，`get_*` 在管家的 allow 列表里）。

**身体年龄（`healthspan*.py`，`hs-v1.0-2026-10-17`）**
1. 窗口最近 180 天，最近 30 天的天数权重 × 2；每项至少 30 天数据（作息规律性 = 30 对相邻两晚），不足的项不计、写进 `insufficient`。VO₂max 和体重是零星测量，改成「180 天里 ≥ 2 次 VO₂max、最近一次 ≤ 120 天」「最近 90 天有一次体重 / 腰围」。
2. 每项按文献曲线换算成全因死亡风险比，再**除以锚点处的风险比**（文献多以最差一档为参照，直接用会让几乎所有人都「更年轻」；锚点 = 典型成人 = 0 年）。曲线在 knots 之间按 ln(HR) 线性插值，两端外持平。
3. Δ年 = ln(HR) / ln2 × 8（Gompertz，Kirkwood 2015 Philos Trans R Soc B 引 Jones 1956：40 岁后死亡率约每 8 年翻倍），单项 ±4 年；合计 × 0.6、±10 年。身体年龄 = 实际年龄（带小数）+ 合计。
4. 衰老速度（`hs-v1.1`，2026-10-17 中控定）：每周日一个快照（这一周的快照日是今天）；每周的 Δ 先做 4 周滑动平均（这周和前 3 周），再用最近 26 个周点对时间做最小二乘，pace = 1 + dΔ/dt（年 / 年，等于身体年龄的斜率），限制在 −1 ~ 3；`pace_raw` 是没截断的值，`window_weeks` 是用了几个周点（前期不满 26 周时用现有全部，≥ 8 周才出）。v1.0 是 8 周、不平滑，用户的值一直顶在 −1（没截断 −8.9）。

| 项（类别） | 我们的值 | 曲线（出处，数值都核对过原文） | 锚点 |
|---|---|---|---|
| 平均睡眠时长（睡眠） | 主睡眠 + 小睡（`sleep_actual_min`），先换成问卷口径：自报 ≈ 3.365 + 0.567 × 实测（Lauderdale 2008 Epidemiology：实测 5h → 自报 +1.2h、7h → +0.4h） | U 形，最低点 7h；低于每小时 RR 1.06，高于 1.13（Yin 2017 JAHA 剂量-反应荟萃；Cappuccio 2010 Sleep、Shen 2016 Sci Rep 形状一致） | 自报 7h |
| 作息规律性（睡眠） | SRI（Phillips 2017）：醒来日期的中午到中午为一天，按分钟比相隔 24h 的睡 / 醒状态，只配对前后两天都有手表主睡眠的；近 30 天的配对 × 2 | UK Biobank 五分位中位数 65.1 / 75.6 / 81.0 / 85.2 / 89.8 → HR 1 / 0.80 / 0.75 / 0.72 / 0.70（Windred 2024 Sleep，参照 Q1） | 81（队列中位数） |
| 日均步数（活动） | `daily_metrics.step_count`（OW 按来源去重） | < 60 岁：4849 / 7245 / 8911 步 → 1 / 0.59 / 0.51，之后平台；≥ 60 岁：2841 / 5217 / 7116 / 10501 → 1 / 0.62 / 0.52 / 0.43（Paluch 2022 Lancet Public Health） | 年龄组队列中位数（< 60 ≈ 8078，≥ 60 ≈ 6167） |
| 每周中高强度（活动） | 全天心率 Z2–Z3（60–80% HRmax）分钟 + 2 × Z4–Z5（≥ 80%），× 7 | 中等强度分钟 / 周（参照 0–19）：47 → 0.91、112 → 0.84、187 → 0.80、262 → 0.79、337 → 0.74、412 → 0.74、525 → 0.69、700 → 0.68（Lee 2022 Circulation 的细分档中点）；高强度按 2 倍等效 | 150 分钟（指南最低量） |
| 每周力量训练（活动） | 手表的力量训练记录（workouts 表）和 SparkyFitness 训练记录，同一天取大的；SparkyFitness 没记时长时每个工作组按 3 分钟；从开始戴表那天起没记录的天按 0 | J 形：0 → 1、40 分钟 → 0.83（最低）、140 → 0.92（论文没给点估计，读图，推断），更多按 140（Momma 2022 BJSM） | 0（多数成人不练） |
| VO₂max（体能） | Apple 读数，同龄同性别常模：FRIEND 2022 跑步机第 50 百分位（Kaminsky 2022 Mayo Clin Proc，按年龄段中点插值） | 每高 1 MET（3.5）RR 0.87（Kodama 2009 JAMA），最多算 ±3 MET | 常模中位数 |
| 静息心率（体能） | `daily_metrics.resting_heart_rate`（Apple 全天口径，和文献的静坐测量接近） | 每高 10 bpm HR 1.16（Jensen 2013 Heart，已校正 VO₂max，所以选它而不是 Zhang 2016 CMAJ 的 1.09）；45 bpm 以下持平（Zhang 2016：从 45 起线性） | 65（Quer 2020 PLoS One：9 万可穿戴用户的平均） |
| 身体成分（体能） | 有腰围用腰围身高比，没有用 BMI（SparkyFitness 的称重优先，身高 users.height_cm） | BMI：17 → 1.51、19.25 → 1.13、21.25 → 1.00、23.75 → 1.00、26.25 → 1.07、28.75 → 1.20、32.5 → 1.45、37.5 → 1.94、45 → 2.76（GBMC 2016 Lancet 从不吸烟者）；腰围身高比：每 0.1 HR 1.24，J 形最低 0.50（Jayedi 2020 BMJ） | BMI 25；腰围身高比 0.50 |

每项还给一句 ≤ 20 字的建议（`healthspan_params.TIPS`，按方向选；睡眠时长分「多睡」和「睡太久」）。用户 1 的 `users.sex` 原来是空的，按 body-management.md 5.2「男、25 岁」填成 `male`（只填空值）。

**其他功能**
- **压力**（`stress.py`）：只看醒着（不在任何睡眠段）、不在训练（OW 训练记录）、前 5 分钟到后 2 分钟走路 < 20 步的心率；平静基线 = 前 14 天这些读数的中位数，离散度 = 1.4826 × MAD（≥ 3 bpm，读数 ≥ 50 条才出）；每小时 ≥ 3 条才出分：z = (中位数 − 基线) / 离散度，有白天 HRV 读数时 z = 0.7 × 心率 z + 0.3 × HRV z（HRV 基线 = 前 60 天夜间 ln HRV）；分数 = clip(0.5 + z, 0, 3)，< 1 低、1–2 中、≥ 2 高；每天 = 有分小时的平均。没有加速度，步数和训练记录是代替。
- **今日负荷目标**（`coach.strain_coach`）：恢复绿（≥ 67）14–18、黄 10–14、红（< 34）0–10；昨天 TSB < −30 整体下调 2；有生病提示 0–8；没恢复分按黄。进度 = 当前 strain / 区间下限。
- **睡眠教练**（`coach.sleep_coach`）：今晚需求 = 评分同一套公式（8h + 截至昨晚的 14 晚加权睡眠债 ≤ 2h + 今天 strain 加成）；预留入睡潜伏期（最近有 ≥ 3 晚潜伏期数据取中位数，否则 15 分钟）。**和入睡目标（`goals/`，规则会话）的关系**：有生效的入睡目标时**推荐按目标睡**（逐步提前的计划，生物钟一天只能挪 15–30 分钟，不要求一晚睡够）；目标里定了起床时间 → 算出「按目标睡比需求少几分钟」并建议晚点起或短午睡；没定起床时间 → 起床时间 = 目标入睡 + 潜伏期 + 需求。没有目标 → 起床时间取最近 7 晚中位数，推荐 = 起床 − 需求 − 潜伏期。goals 模块不在时（还没合并）自动当作没有目标。
- **健康监测**（`coach.health_monitor`）：静息心率（睡眠期最低）、HRV、呼吸、血氧（夜间中位数）、手腕温度；正常范围 = 前 60 天均值 ± 2 SD（HRV 在 ln 尺度；最小半带宽：心率 3、呼吸 1、血氧 2、温度 0.5），≥ 14 晚才出；最近一晚的静息心率是退回的 Apple 全天值时，整项改用全天值和它自己的基线（不和睡眠期最低值混比）。血氧、手腕温度的当晚值由评分引擎写进 `daily_scores.details.recovery`。
- **睡眠细项**：规律性 % = SRI（7 / 30 天）截到 0–100；效率 = 睡着 /（潜伏期开始 → 醒来）；潜伏期 = 第一段睡着前紧挨着的手表「清醒」片段（Apple 不记「在床」，现有数据里没有这种片段，都是空）；醒来次数、夜间清醒同睡眠分。效率和潜伏期由评分引擎写进 `details.sleep`。
- **心率区间**：每天 = `details.load.zone_minutes`；每次训练 = `workouts.hr_zone_min`（训练历史会话的表，存在时才有）。
- **VO₂max**：最近 90 天平均（没有就最近一次）、每月平均、每年趋势（线性回归）、FRIEND 2022 十分位插值的百分位和等级（≥ 80 优秀、60 良好、40 中等、20 一般、更低较差）。
- **周报 / 月报**（`reports.py`）：周 = end 往前 7 天，月 = 30 天，和紧挨着的上期比；恢复（均值、绿黄红天数、HRV、静息心率只用睡眠期最低值）、睡眠（时长、分数、表现、睡眠债、规律性、入睡 / 起床的圆周平均、< 6h 的晚数）、负荷（strain、TRIMP、肌肉容量、期末 CTL / ATL / TSB）、压力、训练（SparkyFitness 次数和名称、workouts 次数和分钟、类型）、体重（期内最后一次和期初比）、近 90 天日记里对次日恢复有显著影响的行为（|t| ≥ 1.96，按效应排，最多 5 条）。

**接口字段**（前端按这个做；日期都是用户本地日期）
- `GET /healthspan?weeks=26` → `{body_age, chrono_age, delta_years, pace_of_aging, pace_raw, window_weeks, as_of, algo_version, history: [{week, as_of, body_age, delta_years, pace, pace_raw, window_weeks}], contributors: [{key, label, category, value, unit, hr, years, direction, tip, data_days, …}], insufficient: [{key, label, category, reason}]}`。`key` ∈ sleep_duration / sleep_regularity / steps / mvpa / strength / vo2max / rhr / body_comp；`category` ∈ sleep / activity / fitness；`direction` ∈ older / younger / neutral；contributors 按 |years| 从大到小。附加字段：sleep_duration 有 `self_report_equiv_h`；vo2max 有 `norm_median, readings, percentile`；body_comp 有 `measure`（bmi / waist_to_height）、`measured_on`、`weight_kg`、`note`。还没算时 body_age 等为空、有 `missing_reason`。
- `GET /stress?start=&end=&hours=true` → `{start, end, scale, days: [{date, score, level, low_h, medium_h, high_h, peak_hour, baseline: {calm_hr, spread, hrv_ln}, flags, algo_version, hours: [{hour, score, level, hr, samples, hrv_z}]}]}`；默认今天。flags：no_raw_hr / baseline_insufficient / no_calm_hours / no_motion_data。
- `GET /strain-coach?date=` → `{date, recovery, zone, target_low, target_high, reasons[], current_strain, progress, remaining_to_low, status (below / in_range / above), current_missing_reason}`。
- `GET /sleep-coach?date=` → `{date, need_min, need_parts: {baseline, from_debt, from_strain}, sleep_debt_min, strain_today, latency_buffer_min, wake_time, wake_source (goal / goal_plus_need / recent_median), bedtime_for_need, goal_bedtime, recommended_bedtime, source (goal / need), shortfall_min, notes[]}`；时间都是 HH:MM。
- `GET /health-monitor?date=` → `{date, metrics: [{key, label, unit, value, date, low, high, baseline, baseline_nights, status (low / in_range / high), missing_reason}], out_of_range[], all_in_range}`；key ∈ rhr / hrv / resp_rate / spo2 / wrist_temp。
- `GET /sleep/details?start=&end=`（默认 14 晚）→ `{start, end, consistency_pct_7d, sri_7d, sri_7d_pairs, consistency_pct_30d, sri_30d, sri_30d_pairs, nights: [{date, sleep_score, bedtime, wake_time, asleep_min, nap_min, efficiency, latency_min, waso_min, awakenings, deep_min, rem_min}], notes}`。
- `GET /hr-zones?start=&end=`（默认 7 天）→ `{zones[5], start, end, days: [{date, zone_minutes[5], hr_max, strain, missing_reason}], total_minutes[5], workouts: [{id, date, name_zh, type, start_at, duration_min, avg_hr, max_hr, trimp, zone_minutes, flags}] | null, workouts_note}`。
- `GET /vo2max?end=&days=365` → `{current, current_basis, latest: {date, value}, readings, trend_per_year, monthly: [{month, avg, n}], norm_median, percentile, level (excellent / good / average / fair / poor), level_zh, norm_source, missing_reason}`。
- `GET /reports?period=week|month&end=` → `{period, start, end, previous: {start, end}, current: {recovery, sleep, load, stress, training, weight}, previous_values: {…同结构，无 weight}, change: {recovery, sleep, load, stress, training}（数值字段的差）, journal_findings: [{key, label, direction, diff_recovery, cohens_d, reliable, days_yes, days_no}]}`；period 不对返回 422。

**2026-10-17 真实数据（用户 1）**
- **身体年龄 28.8**（实际 25.2，+3.6）。贡献（年，合计前）：步数 5754 步 +4.0（封顶；< 60 岁组队列中位数约 8078）、作息规律性 SRI 67.8 +2.7、VO₂max 43.4（同龄中位数 46.4，第 40 百分位）+1.4、中高强度 67 分钟 / 周 +0.9、BMI 26.3 +0.4、睡眠 6.5h（≈ 自报 7.1h）+0.1；静息心率 56.7 −1.4、力量训练 37 分钟 / 周 −2.0。合计 6.0 × 0.6 = 3.6。8 项都够数据。
- 每周历史：2025-10 约 25.5，2026-01 升到 29，2026-04～07 约 30.3（最高 30.5），8 月开始练、作息改善后降到 28.8。**衰老速度 −1.0（没截断 −1.79，26 周窗口）**：最近 26 周的平滑 Δ 从 4 月的 5.5 降到现在的 3.6，后两个月降得快，还是略低于下限；按周看 8-30 是 2.64、9-13 是 1.06、9-27 是 −0.31、10-11 起到 −1。
- 压力：最近 90 天 85 天有分（5 天没有原始心率）；10-15 均值 1.82（中，22 点那小时高）、10-16 0.47（低），平静基线约 66 bpm、离散度约 15。
- 今日负荷目标（10-17）：恢复 87 → 绿 14–18；今天只有快捷指令，当前 strain 空。
- 睡眠教练（10-17）：需求 509 分钟（睡眠债 29），最近起床中位数 14:01 → 要睡够 05:16 前睡；入睡目标合并后（今晚 06:45）会推荐 06:45、建议 15:29 起。
- 健康监测：全部在范围内（静息心率今天是 Apple 全天值 58.2，范围 46–65）；手腕温度最近一晚没有读数。
- 睡眠规律性 7 天 77%、30 天 66%；VO₂max 43.1（第 39 百分位，一般），每年 −1.4。
- 周报（10-11～10-17 对 10-04～10-10）：恢复均值 64 对 74.5，睡眠 427 对 489 分钟，平均入睡 07:04 对 01:55，压力 0.65 对 0.98，力量训练 2 次对 1 次。日记发现：没有（日记记录还不够）。

**已知问题 / 以后**
- **衰老速度**：改成 26 周 + 4 周滑动平均后，没截断的值从 −8.9 降到 −1.8，但身体年龄这几个月变化大，仍会顶到边界。最早几个月（2025-11～2026-08）是 3.0（没截断 3.4–13.4），部分原因是数据刚开始积累：各项陆续够 30 天才计入，Δ 随之上升，这不全是习惯变差。参数 `PACE_WEEKS`、`PACE_SMOOTH_WEEKS`、`PACE_MIN_WEEKS`。
- 步数项对 < 60 岁很陡（Paluch 的 Q1 → Q2 HR 0.59），5754 步就顶到 +4 年；快捷指令天的步数没去重（偏高），影响很小。
- 中高强度用的是全天心率区间，不只训练，和问卷的「休闲运动」口径不完全一样；VO₂max 用 Apple 估计值（偏低几个单位，没校正）；静息心率口径是 Apple 全天值。
- 力量训练分钟是估的（SparkyFitness 没记时长）；workouts 表合并后会更准。
- 压力没有加速度，不动但在做事（开车、打字）和真的放松分不开；要原始心率，快捷指令改上传原始心率后 2026-10-14 之后的天才有。
- 健康监测的手腕温度一年只有 21 晚，基本出不了范围。
- 身体年龄只是趋势参考：各项风险比来自不同人群的观察性研究，合计用 0.6 收缩是经验值，不是医学诊断。

## 8. 数据量
- 每人每天心率样本：按 5 秒算约 17,280 条，按 30 秒算约 2,880 条；HRV 最多 288 条。
- 四口之家一年约 2,500 万行，普通 Postgres 约 2GB/年；用 TimescaleDB 压缩后约 40–200MB/年。家用纯 Postgres 加 BRIN 索引也够。
- 同步必须增量、分批、压缩、幂等；去重键 (user, metric, ts, source)。快捷指令不适合这个量级，只做过渡和对账。

## 9. 部署（本地 Mac）
- Docker Desktop for Mac + docker compose；所有镜像**固定版本号**，密码放 secrets 文件，`.env` 不进 git。
- 除反向代理外，容器端口只绑定 `127.0.0.1`。
- **远程访问**：Mac 和每个家人的手机装 Tailscale；用 Tailscale 给 Mac 签的 HTTPS 证书（`*.ts.net`）。不对公网开端口。
- **Mac 不能睡**：用户自己在系统设置里打开"使用电源适配器时防止自动睡眠"；插电使用；合盖会睡。（2026-10-16 确认插电时 sleep = 0 已经打开；doctor 每小时检查）
- **无人值守运行（2026-10-16，`deploy/ops/`，详见那里的 README）**：
  - 定时任务用 macOS LaunchAgent（不用 crontab）：每小时 :10 `import-ow`（关联了 OW 的用户）+ `compute-scores`（只写最近 3 天）+ `evaluate-rules`（命令存在才跑）；每天 03:30 完整 `compute-scores` + 备份；每小时 :40 健康检查；登录时拉起全部服务。用户从 `users` 表循环，不写死 user_id；任务之间文件锁互斥。
  - 运维脚本 `ops.py` 只用标准库、用系统自带 python3 跑，数据库通过 `docker exec` 访问：Body Engine 环境坏了，健康检查和备份不受影响。
  - 健康检查 `deploy/ops/doctor.sh`：容器状态和重启策略、9 个本机端口、Body Engine / OW / SparkyFitness 接口、Grafana 和它的 3 个数据源、快捷指令接收器（局域网 IP）、Tailscale serve 8443 / 8444 / 8445 / 443（今天页；真实走 HTTPS 访问）、快捷指令上传和导入、OW 同步、评分计算、每小时任务、备份、恢复演练、磁盘、Docker 内存、LaunchAgent、插电睡眠设置、时区。✗ 发 macOS 通知（同样的问题最多每 6 小时一次，恢复时通知）。
  - 日志 `health_data/ops/<任务>-YYYY-MM-DD.log`，保留 30 天。
  - 开机自愈：所有容器 `unless-stopped`（OW 用 override 覆盖，不改 vendor）；登录时 `start-all` 打开 Docker Desktop、`up -d --no-build --pull never --wait` 四个 compose 项目（含 Grafana）、确认快捷指令接收器加载。Mac 重启后需要登录一次（LaunchAgent 在登录后才跑）。
  - 看门狗（2026-10-17 加，起因是当天 18:56 Docker Desktop 整个崩溃退出）：每 3 分钟查 Docker 守护进程和各 compose 项目，Docker 卡在退出就结束旧进程、不在就 `open -a Docker`，有服务没在跑就 `compose up`；自动恢复后发通知，连续 3 次失败发通知；每次宕机的时间段和原因（从 macOS 系统日志、崩溃报告、版本号、开机时间推断）记进 `health_data/ops/state.json`，doctor 显示最近一次。维护时 `ops.sh watchdog --pause 分钟`。
- **备份（已实施）**：每晚在同一个快照里数行数 + `pg_dump -Fc -Z0` 三个库（Body Engine、OW、SparkyFitness）+ `pg_dumpall --globals-only`，+ Grafana 里网页上新建的看板（API 导出 JSON；Grafana 数据卷不备份，数据源和看板是 git 里的 provisioning 文件），连同 `secrets/` 和 `health_data/archive/` 用 restic 加密存到 `~/Backups/body-management-restic`（密码 `secrets/restic_password`，要另存到密码管理器）；保留 7 天日备 + 4 周周备 + 6 月月备；每晚 `restic check`，周日抽查 10% 数据。压缩去重后约 45MB，每天增量几百 KB。
  - 恢复演练 `ops.sh restore-drill`：恢复到临时容器（同镜像、无网络、不映射端口），逐表核对行数和 secrets 校验和，不碰线上库。2026-10-16 演练三个库全部一致（OW 155 万行）。建议每季度一次（doctor 超过 90 天会提示）。
  - **还没有异地备份**：仓库和系统盘在同一块盘上。加外接硬盘或 B2 / R2（`restic copy`）的步骤见 `deploy/ops/README.md`，由用户决定。
- **Open Wearables**：关闭匿名 telemetry（`TELEMETRY_ENABLED=false`）；它是 pre-1.0，升级前看 release notes。
  - 部署方式见 `deploy/open-wearables/README.md`：上游 compose + 本仓库的 `compose.override.yaml`（不改 vendor 源码），用官方发布的 0.9.0 镜像，所有镜像钉版本 + digest。
  - 端口（只绑 127.0.0.1）：API 8200、开发者门户 8201、Flower 8202、S3 8203、Postgres 5434；Redis、Svix 不对外。Body Engine 是 5433 / 8100。
  - 大文件导入走 S3 分片上传，S3 用 **SeaweedFS**（MinIO 已不在 Docker Hub 发布镜像）。
  - 内存：OW 全套空闲约 1.5GB，导入 export.xml 时峰值约 3GB，Docker Desktop 给 8GB 够用。
- **SparkyFitness**（2026-10-16 部署）：官方 v1.8.0 镜像（钉版本 + digest），不 fork、不改源码，部署方式见 `deploy/sparkyfitness/README.md`。
  - 端口：网页 / API 127.0.0.1:8300；tailnet 内 HTTPS `tailscale serve --https=8444`（8443 是 OW）；数据库和后端不对外。注册默认关闭。
  - Body Engine 读它的方式：**API + 每人一把只读 API key**（1.8.0 起服务端强制只读），不用只读数据库角色。理由：它的表靠 Postgres 行级安全区分用户，绕过 RLS 的只读角色等于能看所有家人的数据；表结构变化快；数据库端口不用对外。实现在 `body_engine/src/body_engine/sparky_client.py`，`users.sparky_user_id / sparky_key_name`（迁移 `020_sparkyfitness.sql`）。
  - 内存：空闲约 320MB（上限 server 768MB / db 512MB / 前端 128MB）。没有匿名统计；网页只会去 GitHub 查新版本做提示。
- **Grafana**（2026-10-17 部署，只给你本人，`deploy/grafana/`，详见那里的 README）：OSS 13.2.4（钉版本 + digest；`docker pull` 卡住，用 `deploy/ops/fetch_image.py` 直接下载再 `docker load`）。
  - 端口 127.0.0.1:8400；tailnet 内 HTTPS `tailscale serve --https=8445`。关闭匿名访问、注册、建组织、公开看板、外部快照、遥测（使用统计、查新版本 / 插件更新、新闻）和默认预装插件；只装 Infinity 4.1.1。账号 `admin`，密码 `secrets/grafana_admin_password`。
  - 数据源全只读：Body Engine 库、OW 库各一个 `grafana_ro` 角色（`default_transaction_read_only`、30 秒超时、最多 10 连接、只授权看板要用的表；Body Engine 的不含 `api_tokens`、`assistant_audit`、`shares`、补剂 / 化验 / 事件，OW 的不含 `user`、`api_key` 等）。Body Engine 的角色用迁移 `050_grafana_ro.sql` 建（迁移进 git、不放密码：只建 NOLOGIN 角色和授权），OW 的表归它自己的 alembic 管，所以用部署脚本的 `ow_grafana_ro.sql` 建；两边的 LOGIN 密码都由 `bootstrap.py` 设，它同时验证读得到、敏感表读不到、写和建表都被拒。Grafana 连 `host.docker.internal:5433 / 5434`，不加入其他 compose 项目的网络。
  - 体重读 SparkyFitness API（Infinity 插件 + 你本人的只读 key），不建 SparkyFitness 数据库角色（理由同上，行级安全）。没用 Body Engine 的 `body_mass`：它来自 HealthKit，最近一条是 2025-08-28。
  - 看板 5 个（中文、时区 Australia/Sydney、默认 90 天）：总览、生理指标（60 天基线带）、体重和活动、日记和提醒、数据质量。由 `deploy/grafana/build_dashboards.py` 生成 JSON，provisioning 加载，网页上不能保存修改。
  - 内存上限 768MB（打开几个看板后约 380MB）。

## 10. 实施阶段
0. **环境**：装 Docker Desktop、Tailscale（Mac + iPhone）；`git init`（`.gitignore` 已建好）。
1. **采集**：本地部署 Open Wearables；去它的 Discord 申请 iOS TestFlight 邀请；iPhone 用邀请码连到 Mac；用 OW 导入 export.xml 回填历史；和现有快捷指令并行 1–2 周对账，**实测 Series 12 的心率写入间隔和 RMSSD 样本数**。
2. **Body Engine 底座**：多用户表；从 OW 拉数据做每日汇总和睡眠；Grafana 基础面板（只给你）。
3. **记录 app**：部署 SparkyFitness（固定版本号，不开自动更新）；把 `body-management.md` 里的训练计划、训练日志、营养目标、菜谱、体重迁移进去（补剂、化验进 Body Engine）。
4. **评分和今天页**：睡眠 → 恢复 → 负荷 → 日记；规则提醒（2026-10-16 完成，第 13 节）；今天页（PWA，2026-10-17 完成，第 14 节）。
5. **家人上线**：Pocket ID 统一登录；给家人建账号、发 Tailscale 邀请和 OW 邀请码；拿到书面同意；支持数据导出和删除。
6. **（可选）上云**：方案见附录 B。

### 进度（2026-10-16）
- 第 0 阶段：Docker Desktop、Tailscale（Mac）已装好；`git init` 完成。**还要你做**：iPhone 装 Tailscale。（插电防睡眠已打开。）
- 第 1 阶段：OW 的 fork 以 git submodule 引入（`vendor/open-wearables`，钉在 0.9.0）；**已在本机部署**（`deploy/open-wearables/`），给你建了 OW user（Body Engine 的 `users.ow_user_id`），export.xml 已导入 OW（155 万条时间序列、102 次训练、796 段睡眠，条数核对见 `deploy/open-wearables/README.md`）。**还要你做**：按那份 README 的「让 iPhone 上的 OW app 连到这台 Mac」配好 Tailscale serve 和 app。之后和快捷指令并行 1–2 周对账。
- 第 2 阶段：Body Engine 的 OW 导入器（`body-engine import-ow`）已完成，历史每日指标和睡眠已改为以 OW 为准（source=`ow`）。
- 第 2 阶段（续）：**健康档案**（2026-10-16）：补剂、化验、日记、身体事件、动作补充信息的 API 和助手工具已完成；`body-engine import-records` 把 body-management.md 的记录迁进来（补剂 8、化验 8、事件 5、动作 32〔禁用 4、要小心 8、正常 20〕、日记项 27、日记回填 16），幂等、不覆盖之后的修改。2026-08 体检的日期按「8 月 30 日起复查」推断为 08-30，原始报告找到后要核对。迁移完成前，新的补剂 / 化验 / 事件仍同时写进 body-management.md。
- 第 2 阶段（续）：Body Engine 底座已跑起来（`compose.yaml`：`body-db` Postgres 17.11 + `body-engine`，端口 127.0.0.1:5433 / 8100）；全量导出（到 2026-10-13）和快捷指令（10-14 起）的每日指标、睡眠已导入。
- 第 2 阶段（续，2026-10-17）：**Grafana 已上线**（第 9 节，`deploy/grafana/`）：5 个看板都有真实数据，tailnet 内 https://your-server.your-tailnet.ts.net:8445。doctor 和每晚备份已纳入。**还要你做**：用 `admin` + `secrets/grafana_admin_password` 登录看一遍，建议登录后改密码并存进密码管理器（改了以后把新密码写回那个文件，doctor 和备份要用）。
- 第 3 阶段（2026-10-16）：SparkyFitness v1.8.0 已在本机部署，tailnet 内 https://your-server.your-tailnet.ts.net:8444；给你建了账号（owner@example.com，管理员）。`body-management.md` 的数据已迁移并核对（`deploy/sparkyfitness/migrate_body_md.py`）：动作库 46 个（含 4 个禁用动作、10 个"注意"）、训练模板 5 个（上肢 A / 下肢 A / 上肢 B / 下肢 B / 肩部 routine）、每周训练计划 V3.0、训练日志 10 次 / 37 个动作 / 145 组、营养目标 V2.1（训练日 / 休息日按星期自动切换）、菜谱 25 个、饮食记录 1 条、体重 4 条。Body Engine 的 AI 助手可以读训练、饮食、体重（`get_training_log` / `get_nutrition` / `get_weight`）。**还要你做**：iPhone 装 SparkyFitness app 并登录（`deploy/sparkyfitness/README.md`）；确认 10-15 的"上斜卧推 23kg"是哪种器械。之后训练和饮食记在 SparkyFitness，确认没问题再停止往 `body-management.md` 里写。

### 历史数据导入规则
- 来源优先级：`ow` > `apple_export` > `shortcut`，高的覆盖低的。OW 部署后从 OW 重算，覆盖前两者。
- 全量导出：读 `health_sync/parse_export.py` 生成的 CSV；导出当天数据不完整，丢掉。
- 睡眠：只用手表的阶段数据（Asleep*/Awake，不用 InBed）；**只按"睡着"片段之间的间隔 > 60 分钟切段**（手表偶尔记一段几小时的"清醒"，会把主睡眠和小睡连起来）；归到"醒来 − 4h"那天；每天睡得最久的一段是主睡眠。
- **OW（`importers/ow.py`）**：按 30 天一批拉 OW 的原始样本，按用户时区切天；睡眠拉 OW 的阶段数据，用上面同一套规则重新切段。export.xml 导出当天不完整：只有 XML 数据的最后一天不写。增量同步从上次游标（`sync_state.cursor` = 最后一条样本时间）往前 2 天整天重算；导入新的 export.xml 后要跑一次 `import-ow --full`（或 `--start`）。
  - **同一批数据可能有两份**（2026-10-17 修）：iPhone app 连上后会回填历史，和 export.xml 导入的是同一批样本，OW 当成两个来源。实时来源优先：同类型同时间戳的 XML 样本丢掉，累计型去重时同设备类型的实时来源排在 XML 前，睡眠会话重叠时只留高优先级的。之前 10-12 / 10-13 的步数和 10-11 / 10-13 的睡眠因此翻倍。
  - **睡眠阶段先换成用户时区**（2026-10-17 修）：OW 返回的偏移不可靠（app 同步的是 UTC，XML 导入的一律写成 +11:00），之前有 8 段睡眠的醒来日期错了一天；换时区后固定成当时的 UTC 偏移，跨夏令时切换的片段时长才对。
- **累计型指标去重（2026-10-16 修好）**：全量导出原来按"最大的单一来源"取值，iPhone 和手表分别记了不同时段时偏低。OW 导入器按 HealthKit 的规则去重：手表 > 手机 > 其他，重叠时段只算高优先级来源，低优先级样本按没被覆盖的时间比例计入。OW 不存样本结束时间，按"同来源下一条样本开始、最多 10 分钟"推断；用 export.xml 的真实起止时间校准：近一年 396 个双设备日，中位误差 4.8%，P90 10.5%，平均偏低约 5%。
  - 例：2026-10-13 步数，真实起止时间 + 手表优先算出 **2032**；OW 导入器 **1922**；原来的最大单一来源 1752。
  - **更正**：原来写的"快捷指令拿到的 HealthKit 去重值 2304"不对。快捷指令按小时分组后是把所有来源直接相加（例：17:00–17:42 这组 640 = 手表 564 + iPhone 76，两台设备同时在走），**没有去重，累计型指标偏高**（手表和手机同时戴着走路的日子尤其明显）。OW 的 iPhone app 接上后快捷指令只做对账。
- **OW 0.9.0 的 XML 导入的已知问题**：不换算单位（标 meters 实际是 km、标 kcal 实际是 export.xml 里的 kJ 等，导入器对 XML 来源按导出单位处理，能量单位用 `--xml-energy-unit`，默认 kJ）；不存时区；来源只到设备类型；睡眠不存来源；站立小时、正念等类型不支持。

## 11. 需要用户确认的决定
- [x] Apple Watch：Series 12。
- [x] 最终给家人用、可上云；**目前只在本地 Mac 部署**。
- [x] 训练和饮食记录：**方案 A，SparkyFitness**（先快）。
- [x] **2026-10-17 改为自建 Watch2Whoop iOS app**（第 17 节）：同一个 OW Flutter SDK（iOS SDK 0.15），加上内嵌今天页，由朋友的 Apple Developer 账号签名、TestFlight 分发。下面这条是之前的决定，留作记录。
- [x] Open Wearables 的 iOS app：**用 OW 官方的 TestFlight 公开链接** <https://testflight.apple.com/join/h2Pv8Dbe>（2026-10-16 在 OW Discord 的 #invitation-links 频道找到，不用单独申请；家人也用这个链接）。连上 OW 之前快捷指令继续跑。
  - **2026-10-16 实测问题**：公开 TestFlight 版打包的是 iOS SDK 0.14.0，有同步竞态问题（每次同步只传一批约 1000 条就被打断，90 天约 19.7 万条传不完）；0.15.0 已修复。另外通过 iPhone 镜像操作时手机处于锁屏状态，HealthKit 读不到数据，"记断点"失败会让同步暂停。
  - 解决办法：请有 Apple Developer 账号的朋友按 `docs/ow-ios-app-testflight-guide.md` 用 SDK 0.15.0 编译分发。
  - RMSSD：iOS SDK 还没发布（在 master 分支），OW 后端的支持 2026-10-15 合并进 main（#1750），也还没发版；要等 OW 0.10.0。在那之前恢复分只能用 SDNN。不稳定再考虑自建 app（US$99/年）或 Health Auto Export。
  - 网页 / PWA **不能**代替这个 app：Safari 读不到 HealthKit，采集只能靠原生 app（或快捷指令）。网页可以代替的是"看数据、记日记"那部分（今天页）。
  - OW 的 app 是开源示例（`the-momentum/open_wearables_health_sdk` 的 `example/`，Flutter，MIT）。朋友如果有 Apple Developer 账号，可以自己编译、换 Bundle ID 后用他的 TestFlight 发给你；如果朋友手上的是 OW 的公开邀请链接（testflight.apple.com/join/…），直接转给你就行。
- [x] 历史数据：**两条都走**。先把 parse_export.py 的 CSV 直接导进 Body Engine；OW 部署好后把 export.xml 导进 OW，以 OW 为准重算。
- [x] Body Engine 数据库：**独立 Postgres 容器**，不和 OW 共用。
- [x] Open Wearables：**fork**（2026-10-16 已建：github.com/the-momentum/open-wearables，公开）。submodule 指向 fork，钉在 0.9.0；官方仓库设为 `upstream`，升级时 `git fetch upstream` 后合并新 tag。
- [x] AI 助手：**先接 MCP，用你的 Claude 订阅**（2026-10-16 已完成）。应用内对话（要 API key、按量付费）等家人要用时再做，届时再定模型和每月预算。

- [x] openGym（github.com/DuarteSantos8/openGym，AGPL-3.0）：2026-10-17 评估，**暂不接入**。现场记录体验（引导训练、组间计时、PR、5600+ 动作演示、肌肉图）比 SparkyFitness 好，但：训练记录和双进阶规则已有；不记饮食（会变成两个 app）；数据存 JSON 文件、没有长期 API token，难接 Body Engine；项目 2026-07 才建，作者 2026-08 删过库后恢复，2026-10 加了允许闭源再授权的 CLA。用户先用 SparkyFitness 实练 1–2 周；体验不好再部署 openGym 只做训练前台（先 fork 并钉住 AGPL 版本）。
- [ ] **训练模块 v2（以后的版本，暂不做，2026-10-19 记入路线图）**：动作库（要点、主要 / 次要肌群、器械、图片）、分化选择（3 / 4 / 5 分化）和每周计划、动作推荐（遵守 `exercise_meta` 的禁用 / 小心动作），主要靠跟 AI 对话来建计划。基础库用 free-exercise-db（Unlicense），wger 做第二来源和翻译；openGym 的演示图 / 动画不能复用，只有 AGPL 的动作文字和计划模板可以改写并注明出处。方案见 [training-v2-plan.md](training-v2-plan.md)；README 路线图同步。

## 12. AI 助手（预留，2026-10-16 加入）
目标：把 Claude 接进来当助手——查数据、解释评分、记日记 / 事件、按规则给建议，以后家人也能用。

**两条接入路径，共用一套工具**
| 路径 | 谁用 | 怎么接 | 费用 |
|---|---|---|---|
| MCP（`channel=mcp`） | 你自己 | 本机 stdio MCP server（`body-engine-mcp`），Claude Code（本项目的 `.mcp.json`）/ Claude 桌面版直接连 | 走你的 Claude 订阅，不按 token 计费 |
| 应用内对话（`channel=api`） | 你和家人 | 今天页里的对话框 → Body Engine 用 Anthropic Python SDK 调 Messages API + tool use，模型默认 `claude-opus-5-5` | 按 token 计费；API key 放 `secrets/anthropic_api_key`（你自己生成并写入） |

另外：Open Wearables 自带一个 MCP server（`vendor/open-wearables/mcp`），手表原始数据可以直接让 Claude 查。

**订阅和 API 的边界**
- Claude 订阅（Pro / Max）只能在 Anthropic 自己的客户端里用（claude.ai、Claude 桌面版、手机版、Claude Code）。自己的网页 / app 里的对话框不能借用订阅额度，必须用 API key 按量付费。所以家人用的应用内对话走 API。
- 手机版 Claude / claude.ai 网页要连 MCP，只能用"远程 MCP"（自定义连接器），连接是从 Anthropic 的服务器发起的，服务器要在公网上能访问到。现在 Mac 只在 Tailscale 内网里，**手机版 Claude 暂时连不上**；以后要用，可以用 Tailscale Funnel 把一个带 OAuth 鉴权的 MCP 地址开到公网，但这等于对公网开端口，要单独评估。

**规则**
- 助手永远代表某个已登录用户，只能看到这个用户能看到的数据（含 `shares` 授权）。工具和 HTTP API 调同一套查询函数（`queries.py`），鉴权一致。
- 读操作直接执行；写操作（记日记、加事件、改补剂）的工具标 `writes=True`，执行前要用户确认。
- 每次工具调用写一条 `assistant_audit`（谁、哪个通道、哪个工具、参数、成功与否）。
- 发给 Claude API 的只有回答问题需要的数据，不整库上传；给家人开放前要在同意书里写明数据会发给 Anthropic。

**已实现**：`body_engine/src/body_engine/assistant/`——工具注册表（`get_profile`、`list_metrics`、`get_daily_metrics`、`get_sleep`、`list_events`、`add_event`；评分 `get_scores`；读 SparkyFitness 的 `get_training_log`、`get_nutrition`、`get_weight`（scope：training / nutrition / weight）；健康档案的在 `records/tools.py`：`list_supplements`、`add_supplement`*、`update_supplement`*、`get_lab_results`（含参考范围比较和历次趋势）、`list_journal_items`、`log_journal`*、`get_journal`、`analyze_journal`、`update_event`*、`get_exercise_rules`、`set_exercise_rule`*；* = 写工具。格式就是 Claude API 的 tool 定义）、`run_tool`（鉴权 + 审计；写操作只能写给自己）、`GET /v1/assistant/tools`；MCP server `body_engine/src/body_engine/mcp_server.py`（身份 = `secrets/body_engine_token_owner`，写工具标 `read_only_hint=false`，客户端执行前会请你批准）。`POST /v1/assistant/chat` 返回 501（未启用）。
规则提醒的工具 `get_reminders`、`dismiss_reminder`*（2026-10-16，scope `reminders`，第 13 节）；入睡时间目标 `get_sleep_goal`、`set_sleep_goal`*（2026-10-17，scope `sleep`，第 13.1 节）。
**身体管家（P1，2026-10-17）**：AI 全部走订阅，不做应用内对话（见 `docs/ai-assistant-plan.md` 第 3、6 节）。常驻 Claude Code 会话（`assistant/` + `deploy/assistant/`，Remote Control），新工具：写 SparkyFitness 的 `log_food`*、`log_workout`*（禁用动作拦截）、`log_weight`*、`undo_sparky_entry`*（Body Engine 第一次用 SparkyFitness 读写 key，只在这几个写工具里）、化验 `add_lab_results`*、截图指标 `log_metric_snapshot`* / `get_metric_snapshots`（表 `metric_snapshots`，不覆盖 daily_metrics）、长期记忆 `get_memory`、`update_profile_card`*、`add_observation`*、`delete_observation`*（表 `assistant_profile` / `assistant_observations`，迁移 060，API `/v1/users/{user}/memory`，shares scope `memory`）、`search_exercises`。
**多用户多提供商（2026-10-17，`docs/ai-assistant-plan.md` 第 9 节）**：应用内对话已实现，但不限 Anthropic——每个用户用自己的 AI 来源（表 `ai_providers`，迁移 110，代码 `body_engine/src/body_engine/ai/`）。订阅类在用户自己的官方客户端里连远程 MCP `/mcp`（AI 专用 token）；API 类（Anthropic / OpenAI / Gemini / OpenAI 兼容）走 `POST /v1/assistant/chat`（SSE、写操作确认、月预算、用量）。key 加密存、只回末 4 位；AI 配置不在 shares 里。上面表格里「API key 放 secrets/anthropic_api_key」的单 key 方案作废。

## 13. 规则提醒引擎（2026-10-16，需求 4）
代码 `body_engine/src/body_engine/rules/`（阈值和出处全在 `params.py`，`RULES_VERSION = v1.0-2026-10-16`），表 `reminders` / `rule_runs`（`040_reminders.sql`），命令 `body-engine evaluate-rules --user-id N [--date]`，接口 `GET /v1/users/{user}/reminders`、`GET .../reminders/runs`、`PATCH .../reminders/{id}`（shares scope `reminders`），助手工具 `get_reminders`、`dismiss_reminder`。**只给建议，不改 SparkyFitness 里的目标、计划或记录**（Body Engine 只有只读 key）。

**数据**：SparkyFitness（被评估用户自己的只读 key）的训练记录、动作库、训练模板、每周训练计划、饮食记录、当天饮食目标（`/goals/by-date`）、体重；Body Engine 的 `exercise_meta`、`daily_scores`、`journal_entries`、`daily_metrics.body_mass`（体重补充，SparkyFitness 同一天有手动称重时以手动为准）。每条规则是纯函数 `(Ctx) -> RuleResult`，结果是 triggered / not_triggered / insufficient_data（数据不足，写明原因）/ error（读数据出错）。

**写库（幂等）**：同一 (用户, 日期, 规则, subject) 只有一行，内容变了才更新，`status` 不动；这次没再触发的旧提醒删掉（派生数据），但规则是 error 时保留它的旧提醒。加重建议、禁用动作、超重、调热量这几类是 sticky：同一个 subject 被「不再提醒」过，之后的日子直接是 dismissed。每天的评估都会重新列出当天仍成立的提醒，所以 API / 助手默认只看当天。

| 规则 key | 依据 | 触发 | 级别 |
|---|---|---|---|
| `progression` | 4.5 双进阶；次数区间 / 档位来自当前计划的训练模板（4.4 迁移进去的 rep_goal、每组 reps、increment_value） | 某动作最近一次训练的工作组全部 ≥ 区间上限、组数不少于计划组数 → 下次 + 一档（上肢 2.5、腿举 / 臀推 5）；记了 RIR 且最低 < 1 → 先保持；同一重量连续 2 次有组低于下限 → 减一档。模板里不是 rep_goal 的动作（面拉、弹力带、平板支撑）不提示 | info / 减重 warning |
| `calorie_adjust` | 5.2（V2.1）、5.2.0 | 滚动 7 天均值（每个窗口 ≥ 2 次称重）：一周降 > 1.0kg → +200 kcal；两周平均每周降 < 0.4kg（含增重）→ −150 kcal（先减油和零食，不先砍训练日碳水）；附 SparkyFitness 当前训练日 / 休息日目标和建议值 | +200 warning / −150 info |
| `pre_workout_fuel` | 4.2.1 | 当天计划或记录里有负重力量训练或篮球 / 游泳 → 练前正餐（碳水 50–70g、蛋白 20–30g）、喝水；有腿部动作（主要肌群股四头 / 腘绳 / 臀 / 小腿）→ 加「不空腹练腿、备快糖、低血糖处理」，最近 14 天日记有低血糖感 / 练前没吃够时升 warning；当天记了饮食但全天碳水 < 50g → 另一条 warning；昨天喝水 < 3L 加一句 | info / warning |
| `frequency_caps` | 5.3.1、5.3.2、CLAUDE.md 健康约束 | 滚动 7 天：红肉 > 250g warning、≥ 200g info；牛肋条 ≥ 2 次 warning、1 次 info（写下一次最早哪天）；红肉 > 2 顿 warning。克数优先用日记 `red_meat_g`，否则按饮食记录名称估（牛肋条 135g、培根 30g、其他红肉 150g / 份；牛奶、牛油果等排除） | info / warning |
| `exercise_restrictions` | 4.3、exercise_meta | 当前计划的模板里有 banned 动作 → alert；最近 14 天训练记录里做了 banned 动作 → warning；绳索面拉 > 18kg → warning；当天计划里的 caution 动作列出要点 → info。状态以 exercise_meta 为准，没对上的看 SparkyFitness 描述的【禁用】/【注意】 | alert / warning / info |
| `readiness` | 第 7 节、4.3、6.2 | 当天恢复分 < 34（红）且计划有力量模板或篮球 → 降强度 warning（没计划时 info）；`illness_warning` → warning；睡眠债 ≥ 2h info、≥ 4h warning | info / warning |

**body-management.md 没写清楚、按最保守解释取的值**（`params.py` 里标「推断」）：
1. 「每周」按滚动 7 天（含当天）算，不按自然周。
2. 红肉包括牛、羊、猪和培根 / 火腿 / 香肠等加工肉（5.3.1 写的是「红肉（牛肉）」，日记项说明写「牛羊猪瘦肉都算」，取范围大的）。
3. 「降超过 1kg」按一周的降幅判断（两周平均会更晚发现）；周均值要求每个 7 天窗口至少 2 次称重；0.4–0.5、0.9–1.0kg/周之间按「保持」。
4. 减重规则（4.5 没写）：同一重量连续 2 次训练有组低于下限 → 减一档（同加重档位）。
5. 记了 RIR 而且最低 RIR < 1（力竭）时到上限也先不加（4.3「每组留 1–2 次余力」）；记录组数少于计划组数不算「所有组到上限」。
6. 「大重量日」= 计划里有含负重动作的力量模板，或篮球；肩部 routine / 拉伸不算。
7. 睡眠债阈值 2h（body-management.md 没定，取评分里睡眠债计入需求的上限）；恢复「红」用 < 34（第 7 节的分档）。
8. 练前进食：只知道当天饮食、不知道训练时间，所以只在「当天记了饮食但全天碳水 < 50g」时警告。
9. 绳索面拉的「大重量」= 超过计划的 18kg。

**对接**
- **动作 ID**：`body-engine link-exercises --user-id N [--dry-run]`（`records/exercise_link.py`）按 source_id（迁移脚本的 `bm:<key>`）→ 名称 / 别名 → 去掉末尾括号注释后的名称三档对，一对一，同档有歧义不写，已填的不覆盖。用户 1：32 个 exercise_meta 全部对上（source_id 18、名称 / 别名 13、去括号 1），5 个已知禁用 / 注意动作 ID 核对一致；SparkyFitness 里 14 个动作没有 exercise_meta（肩部 routine 5 个、爬坡、篮球、9 月历史动作 4 个、腿举（高脚位）、上斜卧推（器械未注明）——它去括号后能对上 `incline_dumbbell_press` 的别名「上斜卧推」，但那个已经被「上斜哑铃卧推（30°）」精确对上，不抢）。规则对没对上的动作看 SparkyFitness 描述标记。
- **日记分析**：结局加「次日恢复分」（`next_recovery`，排第一），D 的行为对 D+1 的 `daily_scores.recovery`。
- **肌肉负荷**：见第 7 节实现说明（algo v1.1）；10 个训练日有值，例：9-21 背 strain 4.4、容量 7020kg，9-15 背 strain 15.2、容量 7020kg。

**2026-10-16 真实数据的结果**（`evaluate-rules --user-id 1`，8 条）
- `progression`（检查 7 个有记录的计划动作）：高位下拉、坐姿划船（窄握）、反手窄距下拉 39 → 41.5kg；保加利亚分腿蹲 5 → 7.5kg（10-15 做了 4 × 12，超过区间 8–10；提醒里引用了当天备注「回到计划的 3 × 8–10，先用 5kg」，两者不一致以备注 / 自己感觉为准）。上斜哑铃卧推、杠铃臀推等 15 个计划动作还没练过；哑铃侧平举、龙门架夹胸、坐姿腿屈伸还没到上限。最近 28 天做过但不在 V3.0 计划里的 7 个动作（9 月部位分化的哑铃推肩、前平举等）不提示；10-15 的「上斜卧推（器械未注明）」也不在计划里，没有次数区间，不提示（日志里写的「做满 12 次 → 加 2–2.5kg」要等确认器械后并进计划动作）。
- `pre_workout_fuel`：周五下肢 B → warning（10-15 有低血糖感）。
- `exercise_restrictions`：10-12 做了直立划船（禁用）warning；10-12 绳索面拉 32kg > 18kg warning；今天计划里的保加利亚分腿蹲（注意）info。
- **没触发**：`frequency_caps`（近 7 天只有 10-15 一天有饮食记录，估红肉 30g〔培根〕、1 顿）；`readiness`（恢复 60、睡眠债 12 分钟）。
- **数据不足**：`calorie_adjust`（SparkyFitness 只有 9-07、9-23、9-25、10-14 四次称重，Body Engine 的 body_mass 最近一条是 2025-08-28；每个 7 天窗口要 ≥ 2 次称重）。
- 回溯 10-15：多一条「今天记录的碳水只有 45g」warning，和当天练腿低血糖对得上。

**已知问题 / 以后**
- 要每天自动跑：`compute-scores` 之后跑 `evaluate-rules`（调度交给运维；没跑的那天 API 当天列表为空）。
- 体重数据太少，调热量规则要每周至少称 2 次（最好每天早上）才会工作。
- 饮食记录只有一条，红肉克数大多只能按菜名估；准确的话在日记里记 `red_meat_g`。
- SparkyFitness 的 `/reports/nutrition-trends-with-goals` 返回的目标是默认 2000 kcal，不跟每周目标计划（训练日 2400 / 休息日 2200）走；规则改用 `/goals/by-date`。`get_nutrition` 工具里的 goal 还是那个默认值，以后要改。
- 训练记录没有 RIR / RPE / 时长，「力竭先保持」和 session RPE 暂时用不上。

### 13.1 入睡时间目标（2026-10-17）
用户最近基本早上 6–7 点才睡（10-10～10-16 七晚平均 07:04，起床 13:00–15:44），想逐步提到 0 点。代码 `body_engine/src/body_engine/goals/`（参数和依据在 `goals/params.py`），表 `sleep_goals`（`070_sleep_goals.sql`，每人一个当前目标），接口 `GET / PUT /v1/users/{user}/goals/sleep`（shares scope `sleep`），助手工具 `get_sleep_goal`、`set_sleep_goal`*，命令 `body-engine set-sleep-goal --user-id N --bedtime 00:00 [--wake --pace --step-days]`，规则 `sleep_goal`（`rules/sleep_goal.py`，`RULES_VERSION` 升到 v1.1-2026-10-17）。

**睡眠日**：一晚归到入睡前的那个傍晚（night = 入睡时刻 − 12h 的日期；10-17 07:39 入睡算 10-16 那晚），时间用「相对这晚之后那个午夜的分钟数」：00:00 = 0、23:00 = −60、06:30 = +390，所以 06:30 比 00:00 晚 6.5 小时；起床同样相对那个午夜（15:02 = +902）。「今晚」= 现在 − 12h 的日期（凌晨 3 点还没睡，今晚仍是昨天傍晚那晚）。

**计划**：设目标时取最近 7 晚（≥ 3 晚有数据）平均入睡、取整到 5 分钟为起点；第 k 档 = max(起点 − (k+1) × 每档分钟, 目标)，从 start_date + k × step_days 那晚起，到目标为止。默认每 3 天提前 20 分钟（`goals/params.py`：生物钟每天大约只能前移 15–30 分钟；body-management.md 6.2.2「每 2–3 天提前 30–60 分钟，不要一夜硬调」，默认取更慢更稳的，用户可以在今天页改快，每档上限 60 分钟）。改目标 = 从当时的 7 晚平均重新定起点。
**今晚的目标** = 计划这一档和「最近 7 晚平均 − 一档」里更晚的那个：落后计划时不要求一夜追回来。起床目标 = `target_wake`，没设就 = 今晚目标 + 睡眠时长（目标里的，否则评分的睡眠需求，否则 8h）。
**进度**（看今晚之前的 7 晚）：平均入睡 / 起床、达标晚数（入睡 ≤ 那晚目标 + 15 分钟；那晚在计划开始前不算）、每周趋势（最近 14 晚 ≥ 5 晚的线性回归斜率 × 7，负数 = 在往前提）、离最终目标差多少分钟、预计哪天达到（按趋势；趋势不在变早就为空；已达到 = 今晚）、比计划落后多少分钟。

**提醒**（`sleep_goal`，今天页风格：标题一句、每行一件事）
| 提醒 | 触发 | 级别 |
|---|---|---|
| 今晚 HH:MM 前睡 | 有生效的目标就每天出：目标前 60 分钟停屏 / 调暗（6.2.7）；咖啡在「目标前 8 小时」和「中午 12 点」（6.2.4）里取更早的，今天起床已经过了这个点就写「今天别再喝咖啡」；晚饭、训练目标前 3 小时结束（6.2.5、日记项 late_dinner）；起床时间 + 起床后 30 分钟内晒太阳 10–20 分钟（6.2.3） | info |
| 连续 N 晚比目标晚 | 最近 3 晚（要连着有数据）都比当晚目标晚 30 分钟以上 | warning |
| 到 HH:MM 这一档了 / 达成目标 | 最近 7 晚平均 ≤ 计划这一档 + 15 分钟（第一档不算）/ ≤ 最终目标 + 15 分钟；sticky，同一档只提一次 | info |
推断（最保守）：咖啡取两条规则里更早的截止时间；6.2.1「先固定起床时间」没有单独做成规则，`target_wake` 可选，设了就按它提醒。

**API**（今天页按这个对接）
`GET /v1/users/{user}/goals/sleep?night=YYYY-MM-DD`（默认今晚）：
```json
{"night": "2026-10-17", "target_bedtime": "00:00", "target_wake": null, "target_duration_min": 506,
 "pace_min_per_step": 20, "step_days": 3, "start_date": "2026-10-17", "start_bedtime": "07:05", "status": "active",
 "tonight_target_bedtime": "06:45", "tonight_target_wake": "15:11",
 "plan": [{"from_date": "2026-10-17", "bedtime": "06:45"}, {"from_date": "2026-10-20", "bedtime": "06:25"}, "…",
          {"from_date": "2026-12-19", "bedtime": "00:00"}],
 "plan_end_date": "2026-12-19",
 "progress": {"nights_7d": 6, "avg_bedtime_7d": "07:04", "avg_wake_7d": "14:16", "on_target_nights_7d": 0,
              "trend_min_per_week": 236.1, "gap_min": 424, "eta_date": null, "behind_plan_min": 20},
 "recent_nights": [{"night": "2026-10-16", "bedtime": "07:39", "wake": "15:02", "target": null, "on_target": null}]}
```
没设目标时 `target_bedtime` 等目标字段为 null、`plan` 为 []，`progress` 里平均 / 趋势照给，`on_target_nights_7d`、`gap_min`、`eta_date`、`behind_plan_min` 为 null。时间都是本地 HH:MM，日期 YYYY-MM-DD。
`PUT /v1/users/{user}/goals/sleep` body `{"target_bedtime": "00:00", "target_wake": null, "pace_min_per_step": null, "step_days": null, "target_duration_min": null, "status": null}`：`target_bedtime` 必填（HH:MM，24 小时制），其余可省（沿用旧目标，没有就默认）；`status` = active / paused。只能改自己的、token 要有 write；cookie 登录要带 `X-Body-Web: 1`。返回同 GET。格式错 422。

**用户 1 的默认目标（2026-10-17 设）**：00:00，每 3 天提前 20 分钟；起点 07:05（10-10～10-16 六晚平均 07:04），今晚 06:45，22 档，2026-12-19 那晚到 00:00。最近 14 晚的趋势是每周晚 236 分钟（10-03 前后还在 0–3 点），所以现在 eta 为空。

## 14. 今天页（PWA，2026-10-17，需求 2）
手机优先的网页前台，日常查看和记录不用再通过对话。代码 `body_engine/src/body_engine/web/`，由 Body Engine 的 FastAPI 直接提供（`/app/`），**没有前端构建链**：原生 JS + CSS，图表手写 SVG，不依赖 CDN（离线可用）。用法和 iPhone 步骤见 `body_engine/README.md`「今天页」。

**页面 v2（2026-10-17，用户要求「简约的高级感」后重做；参考 WHOOP / Oura / Apple Fitness 的克制风格）**：中文、iPhone 宽度、跟随系统深色模式（纯黑）、可「添加到主屏幕」。每个模块最多一行短字，解释和明细都在点开的底部弹层里；分数缺失显示「—」+ ⓘ。
| 页 | 首屏 | 点开（弹层） | 数据 |
|---|---|---|---|
| 今天 | 恢复分大圆环 + 状态词（绿「适合高强度」/ 黄「按计划训练」/ 红「以恢复为主」）；3 个小指标：睡眠时长、睡眠分、昨天负荷；训练负荷标签（新鲜 / 平衡 / 积累疲劳 / 疲劳）；**睡眠**卡片（入睡 → 醒来、睡着时长、阶段图 hypnogram、深睡 / 核心 / REM / 清醒）；**入睡目标**（今晚目标 · 最终、最近 7 晚入睡点 + 阶梯目标线、达标晚数）；「训练与饮食」（训练卡片；饮食进度 + 快捷记录餐食模板）；提醒最多 3 条 + 「全部」 | 恢复、睡眠明细（在床、各阶段、阶段来源）、负荷、CTL / ATL / TSB、训练明细、饮食明细、入睡目标设置（期望入睡 / 醒来、节奏）、餐食模板明细（改克数再记、「克数待确认 · 问管家」）、提醒全文和操作 | `GET /v1/users/{u}/today`（`sleep` 块：主睡眠、`stages`、`recent`）、`goals/sleep`（规则会话）、`meal-templates`（身体管家会话） |
| 趋势 | 指标：恢复、睡眠时长、睡眠分、负荷、静息心率、HRV、体重，30 / 90 天折线 + 7 天均值（按住看某天）；睡眠：平均睡眠 / 入睡 / 醒来、14 晚阶段柱、作息时间轴（晚 8 点 → 次日下午 4 点）、每晚列表 | 某一晚的明细 | `trends`、`sleep/nights` |
| 饮食（v2.4 新 tab；v2.32 按 issue #43 调整） | 「饮食」：当天吃了什么 vs 目标、快捷记录、每餐推荐（早 / 午 / 晚 / 加餐各 2 道：口味〔近 60 天常记〕、收藏、家里存货〔采购的存货〕、剩余热量 / 蛋白、嘌呤、红肉额度、饱和脂肪 / 添加糖上限；短标签）；页面底部「食谱」进入食谱子页（搜索、按早 / 午 / 晚 / 加餐分组的食谱卡片）；「采购」 | 食谱详情（营养、食材克数随份数缩放、做法）、选餐次 / 份数「记 n 份」+ 撤销、收藏、现在能做 | `/recipes`、`/recipes/{id}`、`/recipes/{id}/log`（记到页面正在看的那天）、`/meal-logs/{id}`、`/recipes/meal-plan`（身体管家会话，scope nutrition；不存在时「暂无食谱」） |
| 训练历史（从「训练与饮食」进入的全屏面板） | 月历：每天按 intensity 画圆点（rest 不画）+ 时长，今天高亮，左右滑动换月；本月训练天数 / 总时长 / 活动能量 / 最长连续 | 某天：每次训练（类型、时段、时长、活动能量、心率、心率区间、强度、力量动作和组）+ 当天活动能量、锻炼分钟、步数、strain、肌肉负荷、次日恢复 | `training/calendar`、`training/day/{date}`（训练历史会话，scope training；不存在时「暂无」） |
| 记录 | 今天 / 昨天；「快捷记录」餐食模板（只记今天，长按改克数）；「常用」6–8 个标签（最近 30 天记得最多的，不足按默认顺序补齐），「全部」展开按类别的完整清单；记身体事件、用 AI 记录 | 数量项填数、身体事件表单 | `journal/frequent` + 现有 journal / events 接口 |
| 我 | 头像和权限；**身体年龄**（v2.5）；**身体状态**（按 group 分组的 2 列小卡片：label、大号 value + 单位、状态色、趋势箭头〔按 good_direction 着色〕、迷你趋势线）、**观察笔记**（最近 3 条 + 全部）；补剂、身体事件；切换查看家人、数据更新时间、退出登录、版本 | 状态项的一行 note 和「管家 · 日期」、笔记全文和删除（DELETE）、补剂清单、事件列表 | `GET /v1/users/{u}/memory` 的 `status`（身体管家会话；没有时「暂无」）；档案卡的长文字不在前台展示、不可编辑 |
| 问管家（每页底部一枚） | 「✦ 问管家」 | 打开 `BODY_ASSISTANT_URL`（身体管家 Claude Code 会话的 Remote Control 地址）；没配置时提示「管家未连接」 | `/v1/web/me` 的 `assistant_url`（只接受 https） |

视觉规则：系统字体（SF Pro）、等宽数字、靠字重分层；中性灰阶 + 每种状态一个低饱和色（恢复绿 / 黄 / 红，睡眠用一个色相的深浅），不多色混用；卡片无描边、靠浅底色区分，大留白；动效只有圆环加载和轻微淡入（「减少动态效果」时关闭）。

第一版（v1，5 个 tab、卡片里写解释）的截图和 v2 的对比见交接笔记。
**v2.10（2026-10-18，用户在 Watch2Whoop app 里看过后）**：①**少字**：所有页面、卡片和弹层按「我」页状态卡的标准，只用图表、数字和短标签；解释性句子、教练建议句子、空状态说明都删掉（接口的文字字段不变，给管家读）；说明段落换成档位条 / 范围条。②**回看任意一天**：顶部日期点开月历（新接口 `GET /v1/users/{u}/days?month=`：每天有没有评分 / 睡眠 / 训练 / 指标 + 恢复分颜色 + 最早一天 `first`，按 shares 分类授权），‹ › 和左右滑动切天，「回到今天」；SPA 内切换（`history.replaceState` 写 `?date=`），回看时隐藏快捷记餐和改目标；今天页加「活动」（步数 / 活动能量 / 锻炼分钟，`training/day` 的 daily），Apple Health 历史里只有指标的老日子也有东西看。
**v2.5（2026-10-17，WHOOP 功能覆盖）**：新东西都进现有页面、不加入口，接口不存在时整张卡片隐藏。今天页加健康监测（5 项生命体征圆点，异常标红）、负荷目标（Strain Coach 区间 + 进度环）、压力（0–3 + 全天曲线），睡眠教练并进入睡目标卡片；「我」页顶部加身体年龄（healthspan：数字、± 岁、衰老速度，点开历史和按睡眠 / 活动 / 体能分组的贡献因素 + 建议）；趋势加睡眠规律性、睡眠效率、VO₂max + 体能等级、心率区间分布（这 4 个由 `/trends` 自己聚合），和「报告」分段（周报 / 月报卡片流）。healthspan / strain-coach / stress / health-monitor / sleep-coach / reports 由评分会话提供，前端读的字段见 README「今天页 → WHOOP 功能覆盖」。
**v2.4（2026-10-17，单一入口，第 0 节）**：去掉所有跳 SparkyFitness 的链接（接口也不再返回 `sparky_url`）；底部 tab 改成 今天 / 趋势 / 饮食 / 记录 / 我，新增饮食 tab（食谱 + 以后的采购）；电脑宽屏用同一个今天页（左侧导航、两 / 三栏、弹层居中），趋势在宽屏下把 Grafana 最有用的图做进来（静息心率 / HRV 个人基线带、每周训练量），Grafana 只当备用。
**v2.2（2026-10-17，用户在 iPhone 上用过后）**：睡眠卡片加阶段图（OW 阶段明细优先、快捷指令存档兜底，按用户时区）；加入睡目标卡片；快捷记餐（用户早午饭固定，不想再打开 SparkyFitness，SparkyFitness 入口降为「我」页的备用链接）；「我」页档案卡改成只读的状态卡。
**2026-10-17 变更**：AI 全部走 Claude 订阅（`docs/ai-assistant-plan.md` 第 3 节）。订阅只能在官方客户端用，所以今天页不做自己的对话框、不做拍照 / 附件上传，也不调 `/v1/assistant/chat`；底部改成「问管家」链接，对话和照片都在 Claude app 里进行。

**取舍和默认值**
- 分数为空时显示原因：`views.FIELD_FLAGS` 定义每个分数由哪些 flag 解释（恢复：no_sleep → hrv_missing → baseline_insufficient；strain：no_raw_hr；肌肉负荷：muscle_load_unavailable，没有 flag 时 =「这天没有力量训练记录」；这天没有评分行 =「还没计算」）。今天还没睡眠（例如凌晨）时附最近一次的恢复分和睡眠。
- CTL/ATL/TSB 用**昨天**的值（= 今天开始时的状态）：TSB ≥ 10 新鲜、−10～10 平衡、−30～−10 积累疲劳、< −30 疲劳偏高；最近 7 天有 load_missing 的天数会写出来（CTL/ATL 沿用前一天，新鲜度可能被高估）。分档是第一版取值。
- 聚合接口按块鉴权：每块对应一个 shares scope（scores / reminders / training / nutrition / sleep / metrics / weight；训练块里的禁用状态还要 exercises、加重建议还要 reminders），没授权的块返回 `{"error", "code": "forbidden"}`，其他块照常；和被看的人完全没有 shares 时整个请求 403。SparkyFitness 读不到只影响那一块（`code: sparky`）。
- 写操作沿用现有接口和规则（只能写给自己、token 要有 write）。日记的「否」不提供按钮（没勾 = 没记录），已有的 0 值显示成虚线框「否」，点一下改成「是」。
- 家人视角：顶部下拉列出自己和 shares 里授权给自己的人（没有授权就不显示），看家人时所有写按钮隐藏。

**登录和安全**
- **一次性登录码（2026-10-17，给 Watch2Whoop app 的设置二维码）**：二维码里不放长期 token。管理员 `body-engine login-code --user-id N --label 设备`（或 invite.py）发 12 位码（只存 sha256，默认 72 小时、只能用一次）；app 打开 `/app/#login=码`，今天页用 `POST /v1/web/login-code` 换成和 token 登录一样的 cookie，兑换时给这台设备新建专属 API token（`app:<label>`，明文不出服务器），吊销它 = 这台设备退出（表 `web_login_codes`，迁移 120）。
- 每人独立的 API token 不变。网页第一次输入 token → `POST /v1/web/login` 校验后建会话，cookie 里只放随机会话 ID（库里存 sha256），token 不留在浏览器；`HttpOnly; Secure; SameSite=Strict; Path=/`，有效期 90 天、每次使用顺延（`/v1/web/me` 重发 cookie）；会话继承 token 的 read / write，token 吊销立即失效；退出 = 删会话 + 清离线缓存。
- `api.current_user`：有 `Authorization` 头照旧走 Bearer；没有才看 cookie。cookie 鉴权的写请求必须带 `X-Body-Web: 1`（防 CSRF，配合 SameSite=Strict；服务不开 CORS）。
- `/app/` 的 CSP：`script-src 'self'`、`frame-ancestors 'none'`（style 允许内联，用于进度条宽度）；`/v1/` 回 `Cache-Control: private, no-store`。
- 离线：service worker 对页面和 GET 接口「先网络后缓存」，缓存加保存时间，离线时顶部横幅显示「离线：显示的是 X 时保存的数据」；401 时清数据缓存。数据缓存在手机本地浏览器里（Cache API），退出登录会删掉。
- iOS 上「添加到主屏幕」的 app 和 Safari 的 cookie 分开存，要各登录一次。

**部署**：端口仍只绑 127.0.0.1；tailnet 内 HTTPS `tailscale serve --bg --https=443 http://127.0.0.1:8100`（不用 Funnel），地址 https://your-server.your-tailnet.ts.net 。容器要读 SparkyFitness：`compose.yaml` 给 body-engine 加了 `BODY_SPARKY_URL=http://host.docker.internal:8300`、`BODY_SECRETS_DIR=/run/secrets` 和 docker secret `sparkyfitness_api_key_owner_ro`（只挂只读 key，不挂整个 secrets/；家人加入时每人加一把）。doctor 检查 443 → 8100 并真实请求 `/app/`。

**验证（2026-10-17）**：`tests/test_web.py` 21 个（v2.5 加了睡眠规律性 / 效率 / VO₂max / 心率区间的聚合和授权）；最初 17 个（v2 加了常用日记项的排序 / 补齐 / 授权、状态短标签、「问管家」链接只接受 https、页面里没有对话框和文件上传）（cookie 属性、未登录 401、伪造 / 退出 / 吊销后 401、没有 CSRF 头 403、只读 token 不能写、写给别人 403、聚合内容和原因、假 SparkyFitness 的训练 / 饮食、家人按块授权、睡眠时区、趋势和体重均值、静态文件和安全头）；内置浏览器手机尺寸（375 × 812，深色 / 浅色）在本机开发端口走完登录、5 个页面、日记勾选 + 撤销、记事件；tailnet HTTPS 用 curl 走完登录 → today → 写 / 撤销 → 退出。真实数据：today 约 0.2 秒（含 4 次 SparkyFitness 请求）。

**已知问题 / 以后**
- **睡眠段日期（OW 导入器，不在本模块）**：`sleep_sessions` 里 8 段 OW 睡眠的 `wake_date` 比「醒来 − 4h」早一天（2023-08-07 起，最近是 10-12～10-14：例 10-13 09:08–14:04 的那段记在 10-12，且 asleep_min 552 比段长还长），导致 10-14 和快捷指令补的 10-15 看起来是同一晚、对应日期的评分错一天。今天页如实显示库里的数据，要在 `importers/ow.py` 修。
- 记录页的「否」、编辑 / 删除事件、改补剂还没有界面（接口都有）；以后主要靠 AI 助手录入（对话、截图、文字，自动识别归档）。
- `/v1/assistant/chat` 已实现（多提供商，`docs/ai-assistant-plan.md` 第 9 节）：默认 AI 是 API 类的用户点「问管家」打开页内对话面板（`static/ai.js`、`ai.css`），订阅类和用户 1 本人保持跳转；「我」页加「AI 设置」。
- **已按真实接口对接（v2.6，2026-10-17，合并 c2f1dec 之后）**：入睡目标（13.1：`recent_nights`、`tonight_target_wake`、`behind_plan_min`、`nights_7d`）、状态卡、快捷记餐（409 → 可「仍要记」）、食谱（每份营养、`constraint_flags`、409）、训练日历（16：`today` / `future`、`workout_kcal` / `total_kcal`、`sparky_error`、只有 SparkyFitness 的训练）。在开发端口连共享库实际点过；写入只测了一次快捷记餐 → 重复 409 → 撤销（SparkyFitness 里 5 条已删干净）。截图在交接笔记的 v8。
- **WHOOP 卡片已接真实接口（v2.7，2026-10-17）**：healthspan、strain-coach、stress（hours=true）、health-monitor、sleep-coach（并进入睡目标卡片）、sleep/details（趋势的睡眠规律性改用 SRI）、vo2max（体能等级）、reports（卡片流）；字段读法见 `body_engine/README.md`「WHOOP 功能覆盖」。缺数据时按 `missing_reason` / `current_missing_reason` / stress 的 flags 显示原因。
- 应用内 AI 对话框（第 12 节）还没做；家人统一登录（Pocket ID）以后接，会话表不用改。
- 吊销 token 现在要用 SQL（README 里有命令），以后加 `token-list` / `token-revoke` 命令。

## 15. 食谱管理（2026-10-17）
用户要在今天页里管理自己的食谱库：现在能做什么菜、每道菜用哪些食材、早午晚各推荐什么；下一步是采购管理（Costco / Woolworths，每周估算采购清单）。按第 0 节「单一入口」，界面只在今天页。
- **不引入新服务**：评估过 Mealie / Tandoor / Grocy / KitchenOwl，菜谱存储沿用 SparkyFitness 的 meals（迁移时导入 21 道菜 + 4 个组合），Body Engine 补元数据（表 `recipe_meta`，迁移 090）。采购阶段再评估要不要接 Grocy / KitchenOwl。
- **营养以 Body Engine 为准**：SparkyFitness 的 meal 只有热量和蛋白（碳水、脂肪多为空），所以食材存在 `recipe_meta.ingredients`（整锅的量，每样带热量、三大营养素、饱和脂肪、嘌呤估算、红肉克数、采购单位、门店），每份营养和健康约束标记由食材算。SparkyFitness 里的 meal 只是镜像（新建 / 改食材时同步）。
- **初版**：`body-engine recipes-init --user-id 1` 按 body-management.md（5.2.2、5.3.1、5.3.1b、5.3.2）给 25 个菜填 slots、标签、做法（只摘原文）、食材（原文有克数的用原文，没写的按常见份量估，并让每份热量和蛋白落在原文估算附近，见 `assistant/recipe_seed.py`）；「已会做」的（蘑菇烧鸡、盐葱酱牛肋条、姜葱鸡油饭、组合）can_make=true，蘑菇烧鸡和姜葱鸡油饭收藏。
- **健康约束**（`assistant/recipe_params.py`）：嘌呤（尿酸偏高）、饱和脂肪（脂肪肝、ALT、甘油三酯）、红肉 ≤ 250g/周、牛肋条 ≤ 1 次/周。推荐时红肉超额度和近 7 天吃过牛肋条的直接排除，嘌呤和饱和脂肪扣分。
- **接口和工具**：见 `body_engine/README.md`（`/v1/users/{user}/recipes…`，管家工具 `list_recipes`、`get_recipe`、`suggest_meals`、`create_recipe`、`update_recipe`、`log_recipe`）。记录和快捷记餐共用 `meal_logs` 和撤销接口。
- **采购（2026-10-20）**：商品库、周采购清单、家里存货，见 `docs/grocery-plan.md`（迁移 240，`grocery/`，今天页饮食 →「采购」）。

## 16. 训练历史（2026-10-17，训练日历）
用户要求：在「训练与饮食」里看到历史训练，用日历标出哪天练了、强度、时长；Apple Watch 每天的卡路里消耗和训练消耗也要进系统，好安排训练（也给身体管家用）。代码 `body_engine/src/body_engine/training/`，迁移 `080_workouts.sql`，参数 `training/params.py`（`TRAINING_VERSION`）。

**数据从哪来**
| 内容 | 来源 | 说明 |
|---|---|---|
| 每次训练的时间、时长、能量、心率、距离 | Open Wearables `/events/workouts`（export.xml 导入 + 手表实时同步 + 第三方 app 如 Keep）；快捷指令上传的 `Workout|开始|结束|类型|时长秒|活动能量kcal|平均心率|最高心率|来源` 行（OW app 暂停时补；格式以 `docs/shortcut-sync-spec.md` 为准，解析在 `training/shortcut_workouts.py`） | 存进 `workouts`（每个来源原样一份，唯一键 `(user_id, source, external_id)`），**这是唯一复制进 Body Engine 的训练数据**：训练心率区间 / TRIMP 要按时段拉原始心率算，现读太慢 |
| 力量训练的动作、组、次数、重量 | SparkyFitness（第 5 节，唯一来源） | 不复制，接口按天现读、合并 |
| 每天的活动能量、基础能量、锻炼分钟、步数 | `daily_metrics`（OW > 快捷指令） | 没训练的日子也有 |
| strain、肌肉负荷、恢复分 | `daily_scores`（第 7 节） | — |

**导入（`training/importer.py`）**：`import-ow` 每次顺带拉同一个日期范围的训练（每小时任务自动跑，`--full` 从头）；`import-workouts` 只拉训练（回填 / 重算用）；`import-shortcut`（接收器收到上传后自动跑）导入快捷指令的训练行（`training/shortcut_workouts.py`；时长是秒、能量是活动能量 kcal，也认带单位 / 千位分隔符的写法，空字段可以）。都是幂等的。
- **单位**：OW 的 XML 导入把 export.xml 的 kJ 原样存成 kcal（和每日指标同一个问题），除以 4.184；实时同步的是 kcal。用样本核对：OW 的 `calories_kcal` 是**总能量**（活动 + 基础）——2026-09-04 力量训练 302.9 = 活动样本 213.3 + 基础 90.5。
- **活动能量**：训练时段内活动 + 基础能量样本之和 ≥ 总能量 85% 时，按样本比例分出活动能量；样本没同步全时用 总能量 − 基础代谢 × 时长（基础代谢 = 本人核对得上的训练里的中位数，约 1.94 kcal/分钟；没有就用每日基础能量 / 1440），标 `active_kcal_estimated`。
- **心率 / TRIMP**：按训练时段拉 OW 原始心率（XML 副本去掉），和评分模块同一套 Edwards 五区（50/60/70/80/90% HRmax，权重 1–5）；HRmax 用当天 `daily_scores.hr_max`（没有就 208 − 0.7 × 年龄）；一条心率最多代表到下一条或 5 分钟、不超过训练结束。被心率覆盖不到 60% 的不出 TRIMP（标 `hr_coverage_low`），强度改用平均心率估。存 `trimp`、`hr_zone_min`（五区分钟）、`hr_samples`、`hr_max_used`。
- **去重**：同一次训练的副本（OW 里 XML 导入的 + 实时同步的；OW + 快捷指令）：开始、结束都在 2 分钟内，或同类型、时间重叠 ≥ 较短那次的 80%。优先级 OW > 快捷指令、实时同步 > XML、手表 > 手机 > 其他；低的 `dup_of` 指向保留的那条。每次导入整段重算。
- **不计入**：时长 > 12 小时的（2024-07 有一条 74 小时、一条 10 天的，没结束的记录），`excluded = implausible_duration`。
- **时间**：一律用用户时区；`local_date` = 开始那天（跨午夜的训练算开始那天）。设备记录的时区偏移另存 `zone_offset`（2025-08、2026-07～09 有 +08:00 的，人在国内时；日期仍按 Australia/Sydney，和其他所有数据一致）。
- **删除**：OW 里删掉的训练，下次拉到那个范围时这边也删（只删 source=ow、在拉取范围内的）。快捷指令的存档是累积的，不删。
- **类型中文名**：力量训练、篮球、游泳、跑步、骑行、爬楼梯、有氧训练……；**walking 一律写「步行 / 爬坡」**：跑步机爬坡在 HealthKit 里记成步行、没有坡度，单看手表分不出（不猜）；同一天 SparkyFitness 记了「爬坡（跑步机）」的，合并后用那个名字。

**合并（`training/history.py`，SparkyFitness 只有日期没有时间，所以按天配对）**
- 这天 SparkyFitness 的一次训练：力量动作并到当天**最长的一次手表力量类训练**（strength_training / generic …）上，名称「力量训练 · 背」；有氧动作（爬坡）并到**时长最接近的一次手表有氧训练**上，名称改用 SparkyFitness 的。手表给时长、心率、能量，SparkyFitness 给动作、组、备注。
- 配不上的（手表没戴 / 没同步，例 10-10、10-12）单独成一次 `source=sparkyfitness`；没有 SparkyFitness 记录的手表训练单独显示。
- 动作状态：exercise_meta 的 banned / caution（要 shares 的 exercises），否则看 SparkyFitness 动作描述的【禁用】【注意】标记 → 「禁用 / 注意 / 正常」。

**强度分级（每次训练，阈值在 `params.py`）**：rest / light / moderate / hard / max（休息 / 轻松 / 中等 / 高强度 / 极限）。
| 依据（按顺序） | 规则 |
|---|---|
| 1. TRIMP（训练时段的原始心率） | < 50 light、50–120 moderate、120–200 hard、≥ 200 max。依据：本人 36 次心率完整的手表训练四分位 26 / 59 / 110；40 分钟爬坡（心率 140）约 120、1 小时爬坡约 180、1 小时篮球 160–180、2 小时篮球 210。换成单次 strain（K = 170）：50 → 5.4、120 → 10.6、200 → 14.6（WHOOP「strenuous」下沿） |
| 2. 平均心率（快捷指令、心率没同步全） | 估 TRIMP = 分钟 × 平均心率所在区的权重 × 0.94（0.94 = 那 36 次里 真实 / 估计 的中位数，四分位 0.79–1.06），同一组阈值 |
| 3. 力量训练量（有 SparkyFitness 记录） | 心率会低估力量训练（第 7 节），取心率那档和训练量那档里**高的**：Σ 次数 × 重量 ≥ 2,500 moderate、≥ 6,000 hard、≥ 12,000 max；或工作组 ≥ 10 moderate、≥ 20 hard |
| 4. 只有时长 | 球类 / 游泳 / 跑步 ≥ 30 分钟 moderate、≥ 75 hard；力量 ≥ 40 / ≥ 75；有氧和其他 ≥ 60 moderate；其余 light |
少于 5 分钟的记录一律 light。**每天的强度 = 当天最高的一次**；没训练 = rest。

**接口（scope training；评分字段另要 scores、每日活动另要 metrics，没授权的为 null 并列在 `hidden`）**
- `GET /v1/users/{user}/training/calendar?month=YYYY-MM`（默认本月；或 `start` / `end`，最多 92 天）→ `{month, start, end, timezone, days: [...], summary, sparky_error, hidden, intensity_levels, version}`
  - `days[]`：`date, weekday, today, future, sessions_count, duration_min, active_kcal（全天活动能量）, workout_kcal（训练的活动能量之和）, total_kcal（全天活动 + 基础）, exercise_min, steps, strain, muscle_load, recovery（当天早上）, intensity, intensity_label, types[], labels[], flags{daily_incomplete?}, sessions[{id, name, type, start, duration_min, active_kcal, intensity, source}]`；没训练的日子也在（intensity = rest），今天之后的 `future = true`。
  - `summary`：`days`（到今天为止）`, training_days, rest_days, sessions, total_duration_min, total_active_kcal, avg_active_kcal, total_workout_kcal, avg_strain, longest_streak, by_type{中文名: 次数}, by_intensity{等级: 天数}`。
- `GET /v1/users/{user}/training/day/{date}` → `{date, weekday, timezone, sessions: [...], daily, sparky_error, hidden, version}`
  - `sessions[]`：`id（w<workout id> / s<SparkyFitness id>）, workout_id, sparky_id, source（ow / shortcut / sparkyfitness / ow+sparkyfitness）, source_label, type, type_name, name, start, end（用户时区 ISO，只有 SparkyFitness 的为 null）, duration_min, active_kcal, total_kcal, distance_km, avg_hr, max_hr, hr_zone_min[5], trimp, hr_max, device, flags, intensity, intensity_label, intensity_basis（trimp / avg_hr / volume / duration / none）, intensity_basis_label, intensity_value, exercises[{exercise_id, name, category, status（禁用 / 注意 / 正常）, status_key, notes, duration_min, sets[{set, type, reps, weight_kg, rir, rpe, duration_s, distance_km, notes}]}], volume_kg, working_sets, notes`
  - `daily`：`active_kcal, basal_kcal, total_kcal, workout_kcal, exercise_min, steps, strain, muscle_load, recovery, recovery_next_day（第二天早上的恢复分）, intensity, intensity_label, duration_min, flags`
- 身体管家工具 `get_training_calendar`（`month` 或 `start` / `end`，默认最近 28 天）、`get_training_day`，只读、scope training；`assistant/CLAUDE.md` 要求安排训练前先看最近 2–4 周的日历和恢复。`run_tool` 把查看者 ID 传给处理函数（`_viewer_id`，不进审计），分块授权和接口一致。

**回填结果（2026-10-17，用户 1）**：OW 152 次（XML 102、手表实时 41、Keep 9）→ 副本 49、不计入 2、保留 101（2016–2025-06 的 60 次是 iPhone / Keep 记的步行、跑步、骑行，没有心率）。2025-08-28 开始戴手表后 41 次：步行 / 爬坡 18（平均 39 分钟、活动 265 kcal）、力量训练 12（51 分钟、277 kcal）、篮球 6（55 分钟、484 kcal、TRIMP 均值 140）、游泳 2、有氧 / 骑行 / 跑步各 1。有 TRIMP 的 37 次；活动能量是估算的 14 次。
| 月份 | 训练天 | 次数 | 时长（分钟） | 训练活动能量 | 日均活动能量 | 类型 |
|---|---|---|---|---|---|---|
| 2025-09 | 6 | 6 | 205 | 1,172 | 597 | 步行 / 爬坡 5、跑步 1 |
| 2025-10 ～ 2026-06 | 每月 0–1 | — | — | — | 312–634 | 零星步行、骑行 |
| 2026-07 | 2 | 4 | 254 | 1,900 | 292 | 篮球 3、有氧 1 |
| 2026-08 | 7 | 7 | 297 | 2,159 | 307 | 篮球 2、游泳 2、力量 2、步行 1 |
| 2026-09 | 12 | 17 | 839 | 5,709 | 586 | 力量 9、步行 / 爬坡 7、篮球 1；最长连续 5 天；中等 6 天、高强度 5 天 |
| 2026-10（到 17 日） | 4 | 4 | 95 | 499 | 331 | 力量 3（10-10、10-12 只有 SparkyFitness）、步行 1 |

**抽查（对照 `body-management.md` 第 7 节训练日志，10 次全部对上）**：9-13 肩（手表 78 分钟 + 动作 6 个，直立划船标「禁用」、面拉 / 推肩标「注意」，训练量 2,976 + 24 组 → 高强度）+ 训练后 41 分钟步行 / 爬坡（心率 143）；9-15 背（5 个动作 7,020 → 高强度；手表心率只有 TRIMP 33，正是「心率低估力量训练」）+ 爬坡 43 分钟；9-16 腿：手表的 44 分钟步行 = SparkyFitness 的「爬坡 40 分钟」→ 合并成「爬坡（跑步机）」，后面 22 分钟力量 = 弓步蹲 + 腿屈伸；9-18 胸 + 爬坡 30 分钟；9-21 / 9-24 / 9-30 的短训练（25–30 分钟）；10-10 背、10-12 肩：手表没数据，只有 SparkyFitness（和第 7 节「10-12 肩：手表没数据」一致）；10-15 胸 + 腿：手表 61 分钟、平均心率 118，心率只同步了 24% → 按平均心率估为中等。

**已知问题 / 以后**
- ~~10-14～10-16 的每日活动能量 / 步数偏低~~（2026-10-18 已修）：累计型指标不再「ow 一律优先」，每个来源的日值存 `daily_metric_sources`，`importers/common.py resolve_daily` 在完整、去重的来源里取最大；能量只同步了一部分的来源（基础代谢不到平常一半，或活动能量比当天训练还少 = 日历的 `daily_incomplete`）和快捷指令按小时合计的值（iPhone + 手表直接相加，`flags.hourly_sum`）只在没有干净来源时才用。10-14 / 10-15 的能量改用快捷指令（基础 2,247 / 2,089、活动 584 / 844 kcal），步数仍是 OW 的 8,260 / 6,818。规则见 `docs/shortcut-sync-spec.md` 第 5 节。
- OW iPhone app 暂停后，新的训练只能靠快捷指令（由「快捷指令补全数据和定时同步」会话改，规范 `docs/shortcut-sync-spec.md`）；快捷指令拿不到训练时段的原始心率，强度只能按平均心率估（OW 有那段心率时 `import-ow` 会补算 TRIMP）。
- SparkyFitness 没有训练时间，同一天两次力量训练时只能按「最长的那次」配对；以后 SparkyFitness 记了开始时间再改成按时间重叠。
- 阈值是第一版，攒够主观感受（RPE）后再校准；改了升 `TRAINING_VERSION`（`import-workouts` 会重算全部 TRIMP / 能量）。
- 2016–2025-06 iPhone / Keep 记的历史训练没有心率，强度只能按时长。
- 今天页的训练日历界面由今天页会话按上面的字段做。

## 17. Watch2Whoop iOS app（2026-10-17，采集层 + 手机的单一入口）
用户原话：「先用朋友的 Apple Developer 账号发 TestFlight，开始做 Watch2Whoop iOS app。」快捷指令限制太大（锁屏读不到、只能汇总、量大吃力），OW 官方 TestFlight 版（iOS SDK 0.14）首次同步会被打断（0.15 已修）。代码 `apps/watch2whoop/`（Flutter），给朋友的编译上传步骤（中英双语）在 `apps/watch2whoop/README.md`。

**为什么 Flutter**：OW 官方 Flutter SDK（`open_wearables_health_sdk`，MIT）同时支持 iOS HealthKit 和 Android Health Connect / 三星健康，以后做其他手表 / 安卓的 Watch2Whoop 改动最小；OW 的示例 app 本身就是 Flutter。今天页不重写，直接内嵌（WebView），只有同步页、引导是原生 Flutter 界面。

| 部分 | 做法 |
|---|---|
| 首次引导（面向家人，2026-10-17 改） | **扫「设置二维码」一步完成**：二维码 = `watch2whoop://setup?today=…&ow=…&code=…&login=…`（`deploy/open-wearables/invite.py` 生成：OW 一次性邀请码 + 今天页一次性登录码 + 两个服务器地址；没有长期 token）。iPhone 相机扫码打开 app（URL scheme，冷启动 / 运行中都能收，运行中会先确认）或 app 内扫码 / 粘贴 → 自动填服务器、兑换 OW 邀请码 → HealthKit 授权（用户自己点「全部打开」）→ 打开今天页时用登录码自动登录（`/app/#login=码`）。「手动填写」收起来；默认服务器地址编译时注入（`--dart-define-from-file=config/defaults.json`，仓库里为空）。只有服务器在内网时才需要 VPN（一句可选提示），不再依赖 Tailscale |
| 主界面 | 全屏 WebView 今天页（安全区和下拉刷新由今天页自己处理）。同源链接在 app 内；其他链接（「问管家」→ Claude app、SparkyFitness）交给系统。WebView 注入 JS 通道 `window.Watch2Whoop`，今天页据此在「我」页和登录页显示「手机同步」（`watch2whoop://sync`，app 拦下来打开原生同步页），浏览器里不显示（今天页 v2.2） |
| 后台同步 | OW SDK：HealthKit 后台推送（observer + background delivery）+ BGTask（refresh / processing）+ anchor 增量；首次同步最近 **90 天**（`W2W_SYNC_DAYS_BACK`，更早的已从 export.xml 导入）。申请 SDK 支持的全部类型，去掉生殖健康和饮食（饮食以 SparkyFitness 为准）。AppDelegate 启动时原生先拉起 SDK（BGTask 必须在启动完成前注册；HealthKit 后台唤醒时 Flutter 不一定跑，原生先恢复观察者） |
| 同步状态页 | 原生：后台同步开 / 关、上次上传、上次完整同步、首次同步进度（屏幕常亮）、各类型累计条数、上次失败原因、立即同步、失败重试、连接失效时重新连接、服务器设置、最近日志。SDK 只给「本轮已发多少」，按类型的条数靠解析 SDK 日志（`Sending … items (类型: 条数)` + 随后的 `upload=… status=2xx`）；日志由原生先缓冲（后台同步时 Dart 可能没跑），打开 app 时取回统计 |
| 隐私 / 开源 | 只连用户填的服务器；没有 Sentry 等任何第三方上报；仓库里 Bundle ID / Team ID / 显示名是占位值（`ios/Flutter/Watch2Whoop.xcconfig`），朋友在不进 git 的 `Watch2Whoop.local.xcconfig` 填自己的；默认服务器地址也只能用不进 git 的 `config/defaults.json` 编译进去 |
| 依赖（全部钉死） | Flutter 3.47.7；open_wearables_health_sdk 0.0.25（→ CocoaPods `OpenWearablesHealthSDK` **0.15.0**，见 `ios/Podfile.lock`）；webview_flutter 4.14.1 / wkwebview 3.27.0；url_launcher 6.3.3；shared_preferences 2.5.6；http 1.6.0；wakelock_plus 1.8.1；package_info_plus 10.2.2。插件全走 CocoaPods（OW SDK 0.0.25 还没有 Swift Package） |

**系统限制（app 里一句话提示）**：锁屏时 iOS 不让读健康数据（同步暂停，解锁后继续）；从后台划掉 app 后 iOS 不再唤醒它（打开一次补上）；后台时机由 iOS 决定。SDK 0.15 不支持**手腕温度**和 **HRV RMSSD**（iOS 27，要等 SDK 发版 + OW ≥ 0.10.0）。WKWebView 没有 service worker，app 里的今天页没有离线缓存。

**和快捷指令的过渡（并行对账）**
1. TestFlight 装好、首次 90 天同步完成后，快捷指令**继续跑**（不关自动化）。Body Engine 每小时 `import-ow` 照常拉 OW，来源优先级 `ow` > `apple_export` > `shortcut` 不变；app 的实时来源和 export.xml 重叠的那段，OW 导入器已按「实时来源优先」去重（第 10 节「同一批数据可能有两份」）。
2. 并行 1–2 周，每天对比快捷指令存档和 OW（Grafana「数据质量」看板 / 身体管家）：步数、活动能量（快捷指令按小时分组是多来源直接相加，本来就偏高）、静息心率、HRV、睡眠时长和入睡 / 醒来、训练次数和时长。手表数据每天都有、差异能解释，就算通过。
3. 通过后关掉快捷指令里除手腕温度以外的项（手腕温度在 SDK 支持前继续由快捷指令传）；SDK 支持后整条自动化停用，`health_sync` 接收器保留做应急。
4. 真机上要实测：Series 12 的心率写入间隔、锁屏 / 划掉后台后的恢复、后台同步的实际延迟。

**验证（2026-10-17，模拟器 iPhone 17 Pro / iOS 26.2）**：引导全流程（粘贴设置信息、错误邀请码报错、测试邀请码连接 OW、HealthKit 授权）；调试版往模拟器健康 app 写测试数据，HealthKit 推送触发同步；Docker 中断时上传超时，页面显示失败原因，「失败重试」后上传成功，OW 里测试用户收到心率 144、活动能量 13、步数 12、血氧 / HRV / 呼吸 / 静息心率各 3、体重 1（99.9 kg）、睡眠 1 段、训练 1 次，和 app 的按类型条数一致。内嵌今天页（v2.8）用只读测试 token 登录后，今天 / 趋势 / 饮食 / 记录 / 我五个页面正常，安全区和底部 tab 位置正确；「我」→「手机同步」打开原生同步页，「问管家」弹层、「退出登录」正常。**发现并修了**：WKWebView 在 http://localhost 下不保存 Secure cookie（登录 200 后 `/v1/web/me` 401）——加了只给本机开发的开关 `BODY_WEB_INSECURE_LOCAL_COOKIE=1`（且 Host 必须是 localhost / 127.0.0.1 才去掉 Secure），生产 tailnet HTTPS 不变。单元测试：app 25 个，今天页 `test_web.py` 22 个。测试 OW 用户、测试 token 都已删除 / 吊销，模拟器里的调试版 app 已卸载。真机（后台推送、锁屏、TestFlight 安装、tailnet HTTPS 下的登录）要用户拿到 TestFlight 后验证。

**验证（2026-10-17 晚，设置二维码）**：独立开发库（不碰正式库）+ 本机 OW：invite.py 生成设置链接和二维码；全新安装后 `simctl openurl` 模拟相机扫码冷启动 → 自动填服务器、连上 OW → 健康权限 → 打开今天页自动登录（`POST /v1/web/login-code` 200）；运行中再打开同一链接先确认，码已用过时提示邀请码失效、今天页保持登录、原有同步不受影响；家人没配 AI 时「问管家」提示并能打开「AI 设置」；app 内扫码页在模拟器里提示「相机不可用」（真机扫码待 TestFlight 验证）。**2026-10-18 公网验证（Ali）**：在 Ali 上用 `invite.py --body-cmd deploy/ali/engine.sh` 给占位账号（Body Engine user + OW user）生成设置链接 / 二维码（zbarimg 解码与链接一致）；另一台模拟器（iPhone 17）装带公网默认值的构建（手动填写预填 `body.example.com` / `sync.example.com`），设置链接冷启动 → 经 https://sync.example.com 兑换邀请码 → 健康权限 → 经 https://body.example.com 用登录码自动登录（设备 token `app:<label>` 已建）→ 写模拟器测试数据，Ali 的 OW 收到心率 144 等（与 app 一致）→ `import-ow` / `compute-scores` 后今天页显示同步来的数据。验证后占位账号（OW user、Body Engine user 及其全部数据）已删除。invite.py 改成兼容 Python 3.6（Ali 的系统 Python）。

**问管家（2026-10-17）**：按这个用户的 AI 配置走——API 类提供商 → 今天页内对话（AI 接入层）；有身体管家链接（`BODY_ASSISTANT_URL`，只有部署者本人）→ 打开 Claude app；订阅类但没有链接（家人）→ 说明在自己的 Claude / ChatGPT app 里对话；什么都没配 → 提示并可一键打开「我 → AI 设置」。

**以后**：Android（`flutter create --platforms android .`，SDK 已支持 Health Connect / 三星健康）；SDK 发版后接 RMSSD、手腕温度；OW SDK 有 Swift Package 版后改用 SPM；家人用外部测试组 + 每人一个 OW 邀请码。

## 18. 趋势 tab：对标 WHOOP 和其他产品（2026-10-18）

差距分析和优先级见 `docs/wearable-gap-analysis.md`（用户 2026-10-18 确认「P0–P2 能做的都先做」）。

- **后端** `body_engine/src/body_engine/trends/`：`calc.py` 纯函数（HRV 状态、训练状态 + 负荷重点、有氧分钟、韧性、WHOOP 式睡眠表现综合分、含肌肉负荷的 strain、生病风险、生物钟类型、身体电量估算、恢复时间估算、最低心率出现时间、运动后心率恢复），`params.py` 阈值和出处，`series.py` 聚合成 `/trends` 的 series 和 `status`。只读 `daily_scores` / `daily_metrics` / `stress_daily` / `healthspan_weekly` / `sleep_sessions` / `workouts`，不建新表，不改恢复分和 strain。
- **新数据**：评分时在 `details.recovery.rhr_low_offset_min` 记下夜间最低 5 分钟心率出现在入睡后第几分钟（最低心率出现时间用，重算评分后历史才有）；训练计算时多读结束后 3 分钟的心率，写 `workouts.flags.hr_recovery_bpm`（新训练才有；要回填历史需提高 `TRAINING_VERSION`，会对每次训练重新拉 OW 心率）。
- **预留接口**：Apple 健康的 `heart_rate_recovery_one_minute` 同步进 `daily_metrics` 后自动优先使用；心跳间期（RR）同步后可以把最低心率出现时间和 HRV 换成 RR 算法（`calc.recovery_index` 的输入不变）。
- **前端**：趋势 tab 按 WHOOP 分「恢复 / 睡眠 / 负荷 / 压力 / 长期」五组，范围 1 周 / 1 月 / 3 月 / 6 月 / 1 年，每张卡大数字是期间均值（体重、VO₂max、身体年龄是最新值），右上角「vs 上期」；顶部状态卡；「报告」里加了行为影响（Behavior Insights）。讲解词条在 `explain/glossary_trends.py`，个人部分由 `explain/personal.calc_trend` 从同一份 `/trends` 数据取。
- 身体电量和恢复时间是粗略估算（界面标「估算」）：Apple Watch 白天没有连续 HRV。

## 附录 A：调研摘要（2026-10-16）
**采集**
- [Open Wearables](https://github.com/the-momentum/open-wearables)：MIT，约 2.7k★，0.9.0（2026-09-17），1.0 目标 2026-10 月底；iOS SDK 0.15.0；app 在 TestFlight（Discord 申请）。
- SparkyFitness 的 HealthKit 同步：心率 / SDNN 只传每日 min / max / avg，没有 RMSSD → 不适合评分。
- Health Auto Export：后台同步要 Premium（US$6.99/年），手机要解锁，单次后台约 30 秒；每个家人都要自己配置付费。
- 自建 iOS app：HealthKit 后台推送部分类型最快每小时；Apple Developer US$99/年；TestFlight 每个 build 90 天过期。
- 快捷指令：只能解锁时运行，没有后台保证，量大时吃力。
- Apple：RMSSD 类型 iOS 27 引入；Series 12 全天每 5 秒测心率、HRV 最多每 5 分钟一次；Readiness / Sleep Score 不对第三方开放。

**记录 app**
- [wger](https://github.com/wger-project/wger)：AGPL；2.7（2026-09-03）；多用户、可自助注册；分享只能通过"健身房教练"角色；2.6 起支持 OIDC（django-allauth），也可用 header 方式；iOS app。
- [SparkyFitness](https://github.com/CodeWithCJ/SparkyFitness)：非商业许可；v1.8.0；家人分享（7 种权限，可设到期，数据库行级安全，beta）；原生 OIDC；iOS app；训练有组数、次数、重量、RIR、进阶模式、计划。
- [Tandoor](https://github.com/TandoorRecipes/recipes)：AGPL + Commons Clause；"空间"适合一家人共享菜谱、膳食计划、购物清单；OIDC。
- [NutriTrace](https://github.com/TraceApps/nutritrace) + [CookTrace](https://github.com/TraceApps/cooktrace)：AGPL；2026 年新项目；只有 PWA。
- Grafana：Viewer 能绕过仪表盘过滤直接查数据库，要做到每人只看自己需要"每人一个 org + 行级安全的数据库角色"；所以只给你用。

**评分算法参考**：[vitals](https://github.com/DocStream-Oficial/vitals)（MIT，公式和日记统计）；Open Wearables Sleep Score 规格（MIT）；noop（无许可证，只参考公式）；apple-health-parser（MIT）；NeuroKit2（MIT）。

**登录**：Pocket ID（BSD-2，passkey）首选；家人用不惯 passkey 可换 Authelia + LLDAP；Authentik 功能全但要 ≥2GB 内存。

## 附录 B：将来上云的方案（暂不执行）
- 主机：BinaryLane 悉尼 4 vCPU / 8GB / 100GB，约 A$39/月（澳洲公司、x86）；或 Vultr 悉尼 US$40/月。Oracle 免费层额度已减半且有回收风险，不放家人健康数据。
- 组件：Caddy（自动 HTTPS）、Pocket ID + oauth2-proxy、CrowdSec、Tailscale 管 SSH；尽量合并成一个 Postgres 实例。
- 备份：restic 每晚推到 Cloudflare R2 或 Backblaze B2（<50GB 每月不到 US$1），每周再复制一份回家里。
- 总成本约 A$42–45/月。
- 隐私：个人给家人用，一般属于 Privacy Act 的个人 / 家庭豁免；但 2025-06 起的"严重侵犯隐私"法定侵权仍适用，要拿到家人同意、支持导出和删除。将来开放给外人，健康信息就受 Privacy Act 约束。
- 迁移：pg_dump / restore，切换域名，app 改服务器地址。
