# 训练模块 v2（动作库、3/4/5 分化、周计划与智能换动作）

> 状态：已实现（迁移 `220_training_v2.sql`、`330_exercise_media.sql`，代码在 `body_engine/src/body_engine/training/`，测试在 `body_engine/tests/test_training_v2.py`、`test_training_loads.py`）。

## 目标与核心能力
- **单一入口**：今天页「记录」tab → 训练（以及今天页「今日训练与饮食」卡片），不另装 app，即可查看**本周计划 / 分化模板切换 / 肌群周容量 / 恢复分自调节 / 动作库检索与安全替代**。
- **三层可维护双语动作库（`exercise_catalog` + `exercise_meta`）**：
  1. **L0 开源公共领域底座**：兼容 [yuhonas/free-exercise-db](https://github.com/yuhonas/free-exercise-db)（Unlicense，800+ 动作英文名、肌群、器械、力学分类与分步图片路径）。钉在固定 commit（`training/params.py` 的 `FREE_DB_COMMIT`，JSON 校验 sha256），`body-engine fetch-exercise-db --images` 一次性下载 JSON 和约 1,750 张演示图到部署自己的存储（`BODY_MEDIA_DIR`，默认 `<repo>/media`，今天页从 `/app/media/exercises/…` 读，**运行时不连 GitHub**），`body-engine seed-exercises --free-db` 导入。和精选层按图片目录 / 英文名 / 别名 / slug 合并（精选的中文层优先，上游补图片、次肌群和英文别名），其余约 870 个作为英文条目加入。
  2. **L1 双语与生物力学精选层（`training/catalog_data.py`）**：内置 100 个高频标准动作的中英文名、国内健身房/小红书/抖音常见俗称别名（`aliases`）、18 类生物力学动作模式（`movement_pattern`，如 `horizontal_push`、`vertical_pull`、`hip_hinge`、`face_pull_er` 等）、关节压力标签（`joint_stress`：肩前侧受压、要求肩内旋、易激活上斜方、下背剪切力、膝/肘/腕负荷）、单双侧标记（`per_side`）、默认进阶步长（`default_increment_kg`）与中文动作要点（`cues_zh` / `instructions_zh`）。
  3. **L2 用户个人规则层（`exercise_meta`）**：按 `user_id` 隔离，存储每个人的 `banned`（禁用）/ `caution`（注意）/ `active`（正常）、个人伤病原因（`reason`）、个人专属提示（`cues`）及 `SparkyFitness` 映射 ID（`app_exercise_id`）。无个人记录时，高风险动作（直立划船、颈后推举、颈后下拉、双杠臂屈伸）默认降级为 `caution` 提示。
- **3 / 4 / 5 分化模板与周计划管理（`training_plans`）**：
  - 内置 4 套科学分化模板（定义在 `training/params.py` 的 `SPLIT_TEMPLATES`，全部避开肩内旋高危禁用动作）：
    - `3_day_ppl`：三分化 · 推 / 拉 / 腿（周 3 练）
    - `3_day_full`：全身三练 · A / B / C（周 3 练，高频适合新手或减脂期）
    - `4_day_upper_lower`：上下肢四分化 · Upper A / Lower A / Upper B / Lower B（周 4 练，推荐主力模板）
    - `5_day_ul_ppl`：五分化 · 上下肢 + 推拉腿（周 5 练，进阶高容量）
  - 支持 `custom` 自定义任意天数的周计划（由 AI 从抖音 / 小红书笔记或用户口述提取生成）。
  - **周有效组数核算（`volume_summary`）**：按胸、背、腿/臀、肩、手臂、核心统计每周直接工作组数（+ 次要肌群 0.5 折算有效组数）与训练频率，对比增肌推荐靶区（10–20 组/周，Schoenfeld et al., 2016）标记 `low / optimal / high`。
  - **今日恢复分自调节（`readiness_advice`）**：结合 `daily_scores` 的恢复分（绿 ≥ 67 / 黄 34–66 / 红 < 34）与睡眠债（≥ 120 分钟），自动给出今日正式组系数与 RIR 余力建议。
- **动作安全门控与同模式替代推荐（`recommend_alternatives` / `swap_exercise`）**：
  - 排计划（`propose_plan` / `save_plan`）与换动作（`swap_exercise`）均强制经过 `exercise_meta` 安全门控：含用户 `banned` 动作时拒绝直接写入（或开启 `auto_swap_banned=true` 自动替换为同动作模式、同主肌群、避开受限关节的安全动作）。
- **SparkyFitness 双向最佳努力同步**：保存或换动作时，自动确保动作存在于 SparkyFitness、创建或更新每天的 `/workout-presets` 与 `/workout-plan-templates`；未配置 SparkyFitness 时完全独立工作。

## 推荐重量（issue #41，`training/loads.py`，参数和出处在 `training/params.py`）
每个计划动作按下面的顺序给本次的工作组重量（只给建议，不写库；计划里写了 `weight_kg` 的以计划为准）：
1. **规则引擎的进阶提醒**（`rules/progression.py` 双进阶：全部组到区间上限 → 加一档；连续两次没到下限 → 减一档）→ 原样采用，标「按进阶」。
2. **自己的历史**（SparkyFitness 最近 120 天的正式组，不含热身组；按 SparkyFitness 动作 id 或名称 / 别名对到动作库）：
   - 上次的次数在本次区间内（±2）→ 保持上次重量（「同上次」，双进阶只在全部到上限时加重，那时第 1 条会给出加重提醒）；
   - 区间不同（例如上次 5 次、这次 10–12 次）→ 用 Epley 估算 1RM 换算：e1RM = 重量 × (1 + (次数 + RIR) / 30)，RIR 没记按 2；本次重量 = e1RM / (1 + (区间中值 + 2) / 30)，按动作步长向下取整（「换算」）；
   - 超过 4 周没练 → e1RM × 0.9，且同区间时不超过上次重量。
3. **没有历史**：按体重 × 动作模式的入门 1RM 比例 × 0.6 × 器械系数（哑铃 / 壶铃按单只）给保守的起始重量，标「估算」；性别未填按更保守的女性系数；没有体重就不给数字。只对有 `weight` 授权的人显示（估算能反推体重）；SparkyFitness 读不到时也不估算（不能忽略真实历史）。
- 自重动作显示「自重」，计时 / 拉伸 / 有氧不显示重量；今天恢复红区 → 重量 × 0.9，黄区 → 不执行加重。
- 显示位置：`get_week_plan` / `propose_plan`（每个动作的 `load` 和简短的 `load_text`）、今天页训练卡、「记录 → 训练」某天的计划、动作详情（推荐重量 + 最近 3 次记录）。

## 动作详情弹层
演示图轮播（free-exercise-db 的起始 / 结束两张，自动交替，滑动后停止）、主 / 次肌群、肌群示意图（正面 + 背面，主肌群深色、次肌群浅色；人体多边形数据来自 MIT 许可的 react-body-highlighter，见 [LICENSES.md](LICENSES.md)）、推荐重量、要点、最近记录、同模式安全替代。只放标签和数字。
- 导入的上游动作若是受限动作的变体（直立划船、颈后推举 / 下拉、双杠臂屈伸的各种器械版本，`params.HAZARD_FAMILIES`），会记下 `family_slug`：用户对原动作的禁用 / 注意同样作用到变体；同模式替代只从精选层里选（上游条目没有经过关节压力标注）。

## 数据来源与许可证
| 来源 | 许可 | 用法 |
|---|---|---|
| [free-exercise-db](https://github.com/yuhonas/free-exercise-db) | Unlicense | **基础动作库**，公共领域无传染性：提供英文标准名、主要 / 次要肌群、器械、力学分类与图片路径；通过 `convert_free_exercise_db_entry` 转换入库 |
| [wger](https://github.com/wger-project/wger) 动作数据库 | 代码 AGPL；动作数据 CC-BY-SA | 术语与多语言分类参考 |
| [openGym](https://github.com/DuarteSantos8/openGym) | AGPL-3.0 + CLA | **不使用**其专有演示图/动画素材；仅参考公开入门分化（PPL、上下肢、全身）结构思想。评估记录见 [system-design.md](system-design.md) 第 11 节 |
| [react-body-highlighter](https://github.com/giavinh79/react-body-highlighter) | MIT | 动作详情的肌群示意图（正反两面人体多边形坐标，`web/static/muscle-map.js`） |

> **openGym 的演示图 / 动画**来自 Gym visual（gymvisual.com），是授权给 openGym 使用的商业素材，**不在 openGym 的开源许可范围内**，本项目不能直接拿来用。如果以后想用，需要维护者先向 gymvisual.com 申请授权（说明自托管、非商业、家人使用的场景和规模），拿到书面授权之前一律不使用；现在的演示图全部来自 free-exercise-db（Unlicense）。

## AI 交互工作流与 MCP 工具（延续「没有表单」）
1. 用户把抖音 / 小红书链接、截图、照片或文字发给自己的 AI（或说「帮我排个四分化，周二周四没空」「今天哑铃被占了把卧推换掉」）。
2. AI 调用 `search_exercises` / `get_exercise` 将口语动作映射到标准 `slug`，检查用户的禁用/注意规则与关节压力标签。
3. AI 调用 `propose_plan` 生成周计划草案，向用户展示每天动作处方、各肌群周有效组数、禁用动作替换说明与今日恢复分建议。
4. 用户确认后，AI 调用写工具 `save_plan`（或平时微调调用 `swap_exercise`）落库并同步 SparkyFitness。

| MCP 工具 | 读写 | 说明 |
|---|---|---|
| `search_exercises` | 读（`training`） | 按中英文名、别名、肌群（`muscle`）、器械（`equipment`）、动作模式（`pattern`）检索动作库 |
| `get_exercise` | 读（`training`） | 单个动作完整卡片：中英文名、肌群、器械、分步说明、要点、关节压力、用户限制状态与前 5 个同模式安全替代动作 |
| `propose_plan` | 读（`training`） | 预览 3/4/5 分化或自定义周计划草案：检查禁用冲突、计算各肌群周有效组数、附带今日恢复分自调节建议（不写库） |
| `save_plan` | 写（需确认） | 校验无禁用动作后保存激活周训练计划（写 `training_plans` 并尽力同步 SparkyFitness） |
| `get_week_plan` | 读（`training`） | 读取当前生效周计划（周一至周日）、肌群周组数、近期渐进超负荷加减重建议与今日恢复分自调节建议 |
| `swap_exercise` | 写（需确认） | 替换周计划中的动作：`to_exercise=null` 仅预览安全替代动作；指定非禁用动作则落库替换并同步 SparkyFitness |

## HTTP 接口与日常维护命令
- `GET /v1/users/{user}/training/exercises?q=&muscle=&equipment=&pattern=&category=`：动作库筛选与搜索
- `GET /v1/users/{user}/training/exercises/{slug}`：动作详情、关节压力、个人限制与同模式安全替代动作
- `GET /v1/users/{user}/training/splits`：内置 3 / 4 / 5 分化模板列表
- `GET /v1/users/{user}/training/plan`（及 `/training/week`）：当前周计划、肌群周组数、加减重建议与恢复分自调节
- `POST /v1/users/{user}/training/plan/propose`：预览分化或自定义周计划草案
- `PUT /v1/users/{user}/training/plan`：保存并激活周计划（禁用动作返回 `409 Conflict`）
- `POST /v1/users/{user}/training/plan/swap`：替换计划内动作（或预览替代动作）
- 命令行维护：`uv run body-engine fetch-exercise-db [--images]`（下载钉版本的 free-exercise-db 和演示图到 `BODY_MEDIA_DIR`，幂等、可续传）；`uv run body-engine seed-exercises [--free-db | --free-db-json /path/to/exercises.json]`（幂等更新动作库，`CURATED_EXERCISES` 精选中文层始终优先于上游英文条目）。容器里 `BODY_MEDIA_DIR=/data/media`，对应仓库根目录的 `media/`（`compose.yaml` 挂载，不进 git）。
