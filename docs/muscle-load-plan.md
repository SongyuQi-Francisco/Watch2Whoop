# 肌肉负荷图（开关 `muscle_load`）

第一期：2D 男 / 女人体图（正面 + 背面）。#189 补了三件事：运动一层（每次运动都练到肌群）、性别按个人资料、每人自己的自定义动作（下面各节）。第二期：3D（只用免费开源模型，本文末尾是调研结论，第二期不在这次实现）。

## 数据怎么算（`training/muscle_load.py`，参数和出处在 `training/params.py`）
- 来源：SparkyFitness 里的力量训练（训练页逐组提交的、AI 记的都在里面）；Hevy 导入（#167 合并后，`training_session_logs.status='imported'` 的组，没合并前没有这类行、不影响）。没有组、没有次数 / 时长的不计入；热身组不计；有氧 / 拉伸不计。
- 每个动作的肌群来自动作库：**主要肌群每组按 100%、次要肌群按 50%**（动作库没有这个动作时，退回 SparkyFitness 自带的主要肌群）。
- 两个数：**有效组数**（上面加权后的组数）和**训练量 kg**（次数 × 重量，同样加权；自重 / 计时的组加组数不加 kg）。
- 时间范围：今天、本周（周一起，用户时区）、近 7 天；另外算上周（上一个完整周）做对比，和最近 28 天内最后一次练的日子和动作。
- 颜色：本周（近 7 天视图用近 7 天）有效组数对每周推荐区间：没练（0）/ 偏少（< 下限）/ 合适 / 偏多（> 上限）。默认 10–20 组（Schoenfeld 2017 剂量反应元分析：每周 ≥ 10 组增肌明显更好，约 20 组以内持续受益），小肌群 / 辅助较多的肌群用更低的区间（出处在 params.py）。今天练过的加描边；「今天」视图里没练的肌群显示灰色。
- 区间是人群参考值，不是处方；以后可以做成按人调整，AI 会结合恢复、伤病和目标判断。

## 运动一层：每次运动都练到肌群（#189，覆盖修复）
- 来源：`workouts` 表（手表 / 快捷指令 / Hevy 的训练），`dup_of` 不为空、`excluded` 有值（含用户隐藏的）不算。
- **规则：只要记录了运动，就一定有肌群受到训练，强弱不论。** 类型 → 主要 / 次要肌群 + 阻力系数在 `training/sport_muscles.py`（`SPORT_MUSCLES`，115 个类型表项，覆盖 118 个类型名：HealthKit 全部活动类型经 Open Wearables 映射后的类型、快捷指令的中英文名称、我们自己的类型名，另外多收了常见的）；依据写在文件头：能引用的来自文献（跑步 Novacheck 1998、上坡走 Franz & Kram 2012、骑车 Hug & Dorel 2009、划船 Turpin 2011、游泳 Martens 2015、篮球 Stojanović 2018、网球 Kovacs 2007），其余按动作模式评定并注明。
- **兜底**：没收录的类型和来源本身不知道是什么的（`other` / `generic` / `unknown`）按「全身低强度」（`FALLBACK`：大肌群主要、其余次要，阻力系数 0.1），绝不会一块肌群都没有。
- 每次运动给每块肌群两个数：**分钟**（主要 ×1、次要 ×0.5，`sport_min`）和**等效组数**，后者和逐组记录放在同一尺度上，**计入 `sets` 和颜色**（`sets` = `logged_sets` + `sport_sets`，三个字段都给）：
  - 等效组数 = 分钟 ÷ 3（一组连休息约 3 分钟）× 类型的阻力系数 × 强度倍率 × （主要 1 / 次要 0.5）
  - 强度档和训练日历同一套：有 TRIMP 按 TRIMP，没有按平均心率估，再没有按时长；倍率 休息 0.4 / 轻松 0.6 / 中等 1.0 / 高强度 1.4 / 极限 1.8（session-RPE 档位 ÷ 5，Foster 2001）。
  - 例：60 分钟中等强度骑行 = 60 ÷ 3 × 0.25 × 1.0 = 5 组（股四头肌、臀），小腿 / 腘绳肌 2.5 组。
- **手表记录的力量训练、没有逐组明细**：工作组数 = 分钟 ÷ 3，不乘强度（心率会低估力量训练）；分给用户训练计划里**当天（周几）**的力量动作的主要肌群，次要肌群按一半；计划里没有这天就分给全身（`strength_training` 的全身映射）。平均每组算 1.5 块主要肌群（复合动作），所以目标有 n 块主要肌群时每块得 组数 × 1.5 ÷ n。**当天已经有逐组记录的，手表的力量训练不再重复计算。**
- 今天 / 本周 / 近 7 天三个范围都算，上周也算（本周对比）：每块肌群 `sport_min`，整体 `sports[范围]`（`estimated=true` 是估的），单块详情 `sports[范围]`（「篮球 74 分钟 · 主要」）。
- 图上：被运动练到的肌群照常着色（颜色按合计的有效组数），同时盖一层斜纹；图例「运动」。
- **覆盖检查**：`body-engine muscle-coverage-check [--days 28] [--json]` 扫所有用户近 N 天的运动，类型没映射（走了兜底）或映射为空就报错、列出类型和用户，退出码 1。每晚任务和 doctor（「肌肉图覆盖」）都跑；测试 `tests/test_muscle_coverage.py` 遍历映射表，保证每种类型都非空。出现 ✗ 就到 `sport_muscles.py` 给这个类型补一行。
- 没有逐组记录的动作（动作库没有的，`unmatched_exercises`）还是只列出来、不计入，用 Jarvis 的「补进动作库」。
- 查询时实时计算，改映射不用回算历史。

## 性别按个人资料（#189）
- 去掉完整页、今天页小卡、Jarvis 卡片上的「男 | 女」切换，一律按个人资料（`users.sex`，`profile/store.py`）。
- 资料没填：显示男性图，并有小提示「在个人资料里设置性别」，点开就是个人资料（Jarvis 卡片多一个「设置性别」按钮，`show_card` 的 open page `profile`）。

## 每人自己的自定义动作（#189）
- 表 `custom_exercises`（迁移 `650_custom_exercises.sql`，带 `user_id`）：中英文名、别名、主要 / 次要肌群（只能用动作库的肌群 key）、器械、来源（ai / manual）、时间。代码 `training/custom.py`。
- 匹配：`catalog.rows_for(conn, user_id)` = 公共动作库 + 这个用户自己的（slug `my_<id>`，source `user_custom`）；`loads.Index`（肌肉图、推荐重量）、动作库搜索、动作详情都用它。别人看不到，接口只能管自己的。
- 公共动作库已有的名字、自己已有的名字都拒绝（不会遮住公共动作）。禁用动作照常拦截：名字是受限动作的变体（`HAZARD_FAMILIES`，如 upright row）时带上这个家族，用户禁用了原动作，这个也算禁用。
- Jarvis：`add_custom_exercise`（写工具，直接执行；托底 AI 也有）、`delete_custom_exercise`（删除类，`ALWAYS_CONFIRM` 和 `assistant/.claude/settings.json` 的 ask 里都有）；`get_muscle_load` 的 `unmatched_exercises` 就是待补的。角色设定里写了：按动作判断肌群，拿不准先问用户。
- 页面：肌肉图「N 个动作没对上动作库」旁边「让 Jarvis 补进动作库」，打开 Jarvis 并预填一条列出这些动作名的消息；补完后页面自动刷新、这些组被计入。动作库页「我的动作」可以加、改、删（`web/static/custom-exercises.js`）。
- 接口（都在 `muscle_load` 开关后面）：`GET / POST /v1/users/{user}/training/custom-exercises`、`PATCH / DELETE …/custom-exercises/{id}`，只能是自己。
- 管理员：`body-engine exercises custom-list` 列出所有用户的自定义动作名和多少人加过（不显示是谁），以后把常见的并进公共动作库。

## 接口（都在 `muscle_load` 开关后面，关着返回 404）
- `GET /v1/users/{user}/training/muscle-load`：全部肌群、三个范围一次给齐。需要 `training`（朋友有训练共享可以看）；自己的另带 `goals`。
- `GET /v1/users/{user}/training/muscle-load/{muscle}`：单块详情 + 推荐动作（来自动作库，不含禁用动作，「注意」动作标出来）。
- `GET / PUT /v1/users/{user}/training/goals`：重点肌群（最多 5 个）和体态 / 形体目标（一句话，≤ 200 字）。只能是自己；体态目标同时写进长期记忆档案卡 `physique_goal`。迁移 `640_training_goals.sql`。

## 页面
- 记录 → 训练顶部：完整的图（今天 / 本周 / 近 7 天，按个人资料性别，没有手动切换），正面和背面并排，点肌肉或下面的列表看详情；「让 Jarvis 分析我的肌群训练」「训练设置」按钮。
- 今天页：一张小卡，点开进入记录 → 训练。
- 我 → 训练设置：重点肌群、体态目标。
- Jarvis：`show_card` 新类型 `muscle`（复用 #151 的互动卡片）。

## AI
- 只读工具 `get_muscle_load`（组 `training`，scope `training`）：每块肌群的组数、kg、上周、推荐区间、状态、最近日期；自己的另带重点肌群和体态目标。
- 分析 → 建议 → **用户确认后**用现有的 `propose_plan` / `save_plan` / `swap_exercise` 改计划（描述里不重复，按钮的提示词写明了「先给我看改动，我确认后再改」）。

## 第二期：3D 开源模型调研（只用免费开源，不实现）
| 候选 | 许可 | 说明 |
|---|---|---|
| Z-Anatomy（基于 BodyParts3D，含男 / 女全身肌肉，Blender / 可导出 glTF） | CC BY-SA 4.0；BodyParts3D 是 CC BY-SA 2.1 Japan | 最接近需求。允许商用，但 **ShareAlike**：改过的模型文件要同样许可发布，必须署名。仓库里个别部件是 CC BY-NC / NC-SA（内耳、肾等），**必须剔除**，肌肉部分用 BodyParts3D 衍生的部分。 |
| Open 3D Model（荷兰大学解剖系） | CC BY-SA，但页面同时写「非商业、大学」 | 许可冲突，不用。 |
| MakeHuman 等人体生成器 | 导出资产 CC0 | 有男 / 女体型，但没有按肌肉分块，做不了点肌肉。 |
| Sketchfab / CGTrader 等 | 多数付费或各自条款 | 不符合「只用免费开源」。 |

结论：第二期用 **Z-Anatomy / BodyParts3D 的肌肉网格**（只取肌肉层，Blender 里减面、按本图的 16 个肌群归并、导出 glTF + Draco，男、女各一份），用 three.js 在 WebView 里渲染。要确认的三件事：① 模型文件的 ShareAlike 只约束模型文件（单独放目录、单独署名、不和代码混在一起），代码许可不受影响——发布前再核对一次；② 女性模型是否有现成的肌肉分块（BodyParts3D 本体是男性，Z-Anatomy 有女性版本，需要核对质量）；③ iPhone WKWebView 的帧率和内存（目标每个模型 < 3 MB、< 30 万三角面）。
