# 饮食、体重、训练记录改存 Body Engine 自己的表（#252）

> 状态：设计（第 1 期）。第 2 期（新表 + 双写 + 迁移命令）同时开 PR；第 3、4 期等负责人确认后再做。
> 英文摘要在文末。

## 0. 为什么、做什么

饮食、食物库、体重、训练记录现在存在 SparkyFitness 里：Body Engine 用每人一把只读 key 读，身体管家和今天页用读写 key 写。SparkyFitness 的许可证不允许商业使用，这限制了谁能部署本项目；它也让部署多了三个容器（server、frontend、db），内地区域（#249）还要再部署一份。

目标：

- 这四类记录改存在 Body Engine 自己的表里，每张表带 `user_id`、按用户鉴权，日期是用户本地日期，重量 kg、能量 kcal。
- 工具和接口对外行为不变：工具名、参数、返回字段、报错文字、id 的格式都不变，用户和 AI 感觉不到换了存储。
- 一个幂等的迁移命令把现有用户的数据逐表搬过来并核对；正式环境执行前由负责人确认。
- 迁完后 SparkyFitness 变成可选，最后从部署里拿掉。Grafana 改读新表。

不做的：

- 不改 SparkyFitness 本身（继续用官方镜像，不 fork），也不复制、改写它的代码和表结构：新存储完全由我们自己实现，见第 12 节「许可证：独立实现」。
- 训练计划（`training_plans`）、食谱（`recipe_meta`）、快捷记餐（`meal_templates`）、营养目标（`nutrition_goal_versions`）、动作字典（`exercise_catalog`、`custom_exercises`、`exercise_meta`）本来就在 Body Engine，只是有的会**镜像**到 SparkyFitness（计划 → workout preset / plan template，食谱 → meal）。第 4 期停掉这些镜像，表本身不动。
- SparkyFitness 里的饮水、自定义测量项、运动 app 同步等功能本项目没用到，不迁。饮水已经是自己的 `water_logs`（#125）。

## 1. 现在都在哪里用 SparkyFitness

| 位置 | 用法 |
|---|---|
| `sparky_client.py` | HTTP 客户端、每人的只读 / 读写 key、`link_state`；读函数 `training_log`、`food_entries`、`nutrition`、`weight`、`exercises`、`workout_presets`、`workout_plans`、`goal_for_date` |
| `assistant/sparky_write.py` | 所有写：`log_food`、`log_workout`、`log_weight`、`update_food_entry`、`update_set`、`undo_entry`、`delete_food_entry`、`rollback`，还有动作匹配 / 禁用检查（`exercise_index`、`resolve_exercise`、`search_exercises`） |
| `assistant/sparky_tools.py` | 读工具 `get_training_log`、`get_nutrition`、`get_weight` |
| `assistant/butler_tools.py` | 写工具定义：`log_food`、`log_workout`、`log_weight`、`undo_sparky_entry`、`update_food_entry`、`update_training_set`、`search_exercises`、`rollback_write`、`combine_food_entries`、`log_meal_template`、`undo_meal_log`、`log_recipe` |
| `assistant/meals.py`、`recipes.py`、`food_combine.py` | 快捷记餐、记食谱（走 `log_food`），合并 / 拆开当天饮食（直接建 / 删条目），食谱镜像成 SparkyFitness meal |
| `training/session_log.py` | 训练页逐组记录提交（走 `log_workout`，重交时删掉上一次） |
| `training/hevy_import.py` | Hevy 导入**不写** SparkyFitness：写 `workouts` + `training_session_logs`（source=hevy），读的时候和 SparkyFitness 的训练合并 |
| `training/plans.py` | 计划镜像到 SparkyFitness（建动作、workout preset、plan template） |
| `offline/api.py` | 离线补交体重（走 `log_weight`） |
| `assistant/memory_api.py` | 今天页删一条饮食、合并 / 拆开、记快捷餐 |
| 读的地方（约 20 个模块） | `nutrition/daily.py`、`nutrition/extras.py`、`assistant/status.py`、`assistant/briefing.py`、`goals/nutrition.py`、`insights/reports.py`、`insights/healthspan_store.py`、`scores/engine.py`、`rules/context.py`、`training/history.py`、`loads.py`、`muscle_load.py`、`plan_start.py`、`day_overrides.py`、`catalog.py`、`records/exercise_link.py`、`trends/series.py`、`web/views.py` |
| 以 SparkyFitness 条目 id 为键的自有表 | `food_entry_extras.entry_id`、`pantry_deductions.entry_id`、`food_merges.entry_id / originals`、`training_session_logs.sparky_entry_id`、`exercise_meta.app_exercise_id`、`ai_write_log.target / before / after`、`meal_logs.entries` |
| 账号 | `users.sparky_user_id / sparky_key_name`，`account/provision.py`（自助注册时建 SparkyFitness 账号和两把 key），`account/delete.py`（注销时删 SparkyFitness 账号） |
| 部署 | `deploy/sparkyfitness/`、根目录 `compose.yaml` 的 key 挂载、`quickstart/`、`deploy/ops/ops.py`（备份、doctor、端口）、`deploy/ali/`（私有）、Grafana Infinity 数据源（体重看板） |

读的函数虽然多，形状只有四种：`training_log` 的会话列表、`food_entries` 的条目列表、`weight` 的称重列表、`exercises` 的动作库。第 3 期只要让这四个函数（和 `nutrition`）从新表返回**一模一样的形状**，调用方基本不用改。

## 2. 表结构（迁移 735）

共同约定：

- **主键用文本 id，沿用 SparkyFitness 的 id。** 迁移和双写进来的行，`id` 就是 SparkyFitness 里的 id（uuid 文本）；以后在新表里新建的行用 `gen_random_uuid()::text`。这样上面那些以条目 id 为键的表（存货扣减、附加营养、合并记录、AI 写入日志、训练页提交记录、`exercise_meta.app_exercise_id`）一个都不用改写，AI 拿到的 id 在切换前后也一样，切换当天用旧 id 撤销照样能用。
- `origin`：`sparky`（从 SparkyFitness 来的：迁移或双写）/ `own`（直接写进新表的，第 3 期起）。双写和迁移只会改、删 `origin = 'sparky'` 的行。
- `synced_at`：最后一次从 SparkyFitness 同步的时间（`origin = 'own'` 时为空）。
- 写入时的用户校验：`INSERT … ON CONFLICT (id) DO UPDATE … WHERE t.user_id = EXCLUDED.user_id`，冲突行属于别的用户时报错，不会覆盖。
- 食物库和动作库例外：主键是 `(user_id, id)`。SparkyFitness 的公共食物 / 动作会被多个用户引用，每人各存一行，互不影响。
- 所有表 `GRANT SELECT … TO grafana_ro`（角色存在时）。

### 2.1 `foods` 食物库

每人自己的食物（AI 记饮食时建的自定义食物、从别处收录的），一行 = 一种食物的一份。

| 列 | 说明 |
|---|---|
| `id TEXT`，主键 `(user_id, id)` | SparkyFitness food id / 新 uuid |
| `user_id` | |
| `name`, `brand` | |
| `serving_size NUMERIC`, `serving_unit TEXT` | 一份是多少（AI 建的食物：1 份 = 那次的份量文字） |
| `calories`, `protein_g`, `carbs_g`, `fat_g`, `saturated_fat_g`, `sugar_g`, `fiber_g`, `sodium_mg`, `alcohol_g` | **每份**的营养，kcal / g / mg，空 = 不知道 |
| `nutrients JSONB` | SparkyFitness 有、这里没单独列的营养素（胆固醇、钾、维生素……），原样按每份保存，不丢数据 |
| `provider_type`, `provider_external_id` | AI 建的是 `custom` + `ba:<内容哈希>`（`log_food` 按它复用同一食物）。索引 `(user_id, provider_external_id)`：SparkyFitness 里可能有重复，第 3 期核对完再改成唯一 |
| `is_custom`, `notes`, `origin`, `synced_at`, `created_at`, `updated_at` | |

### 2.2 `food_entries` 饮食记录

一行 = 某天某餐吃的一样东西，**营养存这一条的合计**（SparkyFitness 存的是每份营养 × 份数，读的时候才换算；这里迁移时就换算好，读的时候不用再算）。

| 列 | 说明 |
|---|---|
| `id TEXT PK` | SparkyFitness entry id / 新 uuid |
| `user_id`, `date DATE`, `time TIME` | 用户本地日期 / 时间 |
| `meal TEXT` | breakfast / lunch / dinner / snacks（和 `sparky_write.MEALS` 一致） |
| `food_id TEXT`（外键 `(user_id, food_id) → foods`，删食物时置空）, `food_name` | 名字单独存一份：食物改名或删掉，历史记录不变 |
| `quantity NUMERIC`, `unit TEXT`, `serving_size NUMERIC` | 份数、单位、一份的大小（改份量时按比例换算） |
| `calories`, `protein_g`, `carbs_g`, `fat_g`, `saturated_fat_g`, `sugar_g`, `fiber_g`, `sodium_mg`, `alcohol_g` | 这一条的合计 |
| `added_sugar_g`, `sugary_drink` | 原来只在 `food_entry_extras` 里（SparkyFitness 没这两个字段）；双写和迁移时从 `food_entry_extras` 带过来 |
| `nutrients JSONB` | 其他营养素的合计 |
| `notes`, `source`（body_assistant / manual / …）, `source_id`（幂等键，索引 `(user_id, source_id)`，第 3 期改成唯一） | |
| `origin`, `synced_at`, `created_at`, `updated_at` | 索引 `(user_id, date)` |

`food_entry_extras` 第 3 期之后只读不写（新条目的加糖等直接写在 `food_entries`），第 4 期删表前确认已全部带进来。

### 2.3 `body_measurements` 称重 / 体成分

一人一天一行（和 SparkyFitness 的 check-in 一样，`log_weight` 的「同一天不覆盖」规则也按天）。

| 列 | 说明 |
|---|---|
| `user_id`, `date` | 主键 `(user_id, date)` |
| `id TEXT`，`(user_id, id)` 唯一 | SparkyFitness check-in id / 新 uuid（撤销日志里用日期，不依赖它） |
| `weight_kg`, `body_fat_pct`, `waist_cm`, `neck_cm`, `hips_cm`, `height_cm`, `muscle_mass_kg`, `bone_mass_kg`, `body_water_pct`, `bmr_kcal` | 空 = 这天没记这项 |
| `origin`, `synced_at`, `created_at`, `updated_at` | |

`log_metric_snapshot` 存的 app 截图指标（`metric_snapshots`）是另一回事，不合并。SparkyFitness check-in 里的 `steps` 不迁（步数以手表为准）。

### 2.4 `logged_workouts` / `logged_workout_exercises` / `logged_workout_sets` 训练记录

用 `logged_` 前缀和已有的手表训练表 `workouts`（OW / 快捷指令 / Hevy 的整段训练）区分。三层结构和 SparkyFitness、`training_log()` 的返回一致。

`logged_workouts`（一次训练）：`id TEXT PK`、`user_id`、`date`、`name`、`notes`、`source`（body_assistant / page / sparky app …）、`kind`（`preset` = 多个动作的一次训练，`single` = 单独记的一个动作，如一次有氧）、`total_duration_min`、`origin`、`synced_at`、时间戳；索引 `(user_id, date)`。

`logged_workout_exercises`（一次训练里的一个动作）：`id TEXT PK`（SparkyFitness exercise entry id；`single` 训练里和训练 id 相同，两张表各自唯一不冲突）、`workout_id → logged_workouts ON DELETE CASCADE`、`user_id`、`sort_order`、`exercise_id`（→ `user_exercises.id`，也就是 `exercise_meta.app_exercise_id` 用的那个 id）、快照 `exercise_name`、`category`、`primary_muscles TEXT[]`（动作库以后改名不影响历史）、`notes`、`superset_group`、`duration_min`、`calories_burned`、`avg_heart_rate`。

`logged_workout_sets`（一组）：`id TEXT PK`、`exercise_entry_id → logged_workout_exercises ON DELETE CASCADE`、`user_id`、`set_number`、`set_type`、`reps`、`weight_kg`、`rir`、`rpe`、`duration_s`、`distance_km`、`rest_s`、`is_pr`、`notes`。

### 2.5 `user_exercises` 记录用的动作库

SparkyFitness 里每人「我的动作」（迁移来的 + 记训练时新建的）。保留 id，因为 `exercise_meta.app_exercise_id`、训练记录、计划镜像都引用它。

列：`id TEXT`（主键 `(user_id, id)`）、`user_id`、`name`、`category`、`modality`、`primary_muscles TEXT[]`、`description`、`catalog_slug`（对上 `exercise_catalog` 的动作，`link-exercises` 会填）、`origin`、`synced_at`、时间戳。

和现有动作表的关系：`exercise_catalog` 是共享字典，`custom_exercises`（#189）是用户用 AI / 页面加的自己的动作，`exercise_meta` 是每人的禁用 / 小心标记。第 3 期起动作匹配（`exercise_index`）读 `user_exercises` 代替 SparkyFitness 动作库；第 4 期之后新建的「记录用动作」直接写 `custom_exercises`，`user_exercises` 只留历史（以后可以合并成一张，不在本次范围）。

### 2.6 `own_store_migrations` 迁移记录

每次**真正写入**（不含 `--dry-run`）的 `sparky-migrate` 记一行：`user_id`、`since`、`until`、`ok`（核对是否全部一致）、`report JSONB`（逐表数字）、`actor`、`ran_at`。第 3 期的读开关只对**最近一次迁移 ok** 的用户生效，避免读到半套数据。

### 2.7 和现有表的关系

| 现有表 | 关系 |
|---|---|
| `meal_templates` / `meal_logs` | 模板本来就在自己的表；记一次 = 若干 `food_entries`，`meal_logs.entries` 里存的条目 id 不变 |
| `recipe_meta` | 食谱本来就在自己的表；记食谱 = 一条 `food_entries`。`sparky_meal_id` 是镜像，第 4 期停止镜像后留空 |
| `training_session_logs` | 训练页草稿 / 提交记录不变；提交后的那次训练进 `logged_workouts`，`sparky_entry_id` 存的仍是那次训练的 id（第 4 期可改名 `workout_entry_id`，不急） |
| `workouts`（手表训练）、Hevy 导入 | 不动。读的时候继续和 `logged_workouts` 合并，规则同现在 |
| `food_entry_extras`、`pantry_deductions`、`food_merges` | 键是条目 id，id 沿用，不用改写 |
| `ai_write_log` | `kind` 仍是 food / session / sets / weight；第 3 期起新写的日志在 before / after 里带 `"store": "own"`，回滚按它分派（见第 4 节） |
| `nutrition_goal_versions` | 不动；`goal_for_date` 的 SparkyFitness 兜底（没有自己的目标版本时）在第 4 期去掉，迁移时给这些用户导入一个目标版本 |

## 3. 每个工具 / 接口改读写哪里

对外行为不变是硬要求：工具名、参数、返回字段、报错文字（含英文 `errors_en.py`）、id 格式不变。#201 的工具快照（`tests/fixtures/datalayer/*.json`）第 2、3 期都不应该变；工具名 `undo_sparky_entry` 也不改（改名会让用户已经配好的 MCP 权限失效），只把描述里的 SparkyFitness 换成「底层数据库」（`ai_parity` 已经这么叫）。

| 工具 / 接口 | 第 2 期（双写，开关开） | 第 3 期（读开关开） | 第 4 期（只写新表） |
|---|---|---|---|
| `log_food`、`log_meal_template`、`log_recipe`、`POST …/meal-templates/{id}/log`、`POST /recipes/{id}/log` | 照常写 SparkyFitness，然后把当天饮食同步进新表 | 查重读新表 | 只写 `foods` + `food_entries` |
| `update_food_entry`、`combine_food_entries`、`POST /nutrition/combine`、`DELETE /nutrition/merges/{id}` | 写完同步涉及的日期 | 读新表定位条目 | 只改新表 |
| `undo_sparky_entry`、`DELETE /nutrition/entries/{id}`、`undo_meal_log`、`DELETE /meal-logs/{id}` | 删完同步那天 | 读新表定位 | 只删新表 |
| `log_workout`、`POST /training/session/{day}/submit` | 写完同步那天的训练 | 查重、动作库读新表 | 只写 `logged_*` |
| `update_training_set` | 写完同步那天 | 读新表 | 只改新表 |
| `log_weight`、`POST /offline/weight` | 写完同步那天 | 冲突检查读新表 | 只写 `body_measurements` |
| `rollback_write` | 回滚完同步涉及的日期 | 按 `store` 分派 | 只回滚新表（旧的 SparkyFitness 日志见第 4 节） |
| `get_training_log`、`get_nutrition`、`get_weight`、`search_exercises` | 不变（读 SparkyFitness） | 读新表，同形状 | 同左 |
| 间接读的：`get_briefing`、`get_training_calendar` / `get_training_day`、`get_report`、`get_healthspan`、`get_strain_coach`、`get_muscle_load`、状态卡、规则提醒、评分（肌肉负荷）、趋势体重、今天页饮食 / 训练 / 记录页、`/training/calendar`、`/training/day/{day}`、`/today`、`/trends`、`/nutrition/*`、`/training/muscle-load*` | 不变 | 换成新表的同形状读函数 | 同左 |
| `GET /training/sparky`、`POST /training/sparky/connect`（训练记录连接状态） | 不变 | 读开关开着的用户直接返回 connected | 删掉入口，接口返回 connected 保持旧 app 兼容 |

数一下：直接写 SparkyFitness 的 AI 工具 11 个（`log_food`、`log_workout`、`log_weight`、`undo_sparky_entry`、`update_food_entry`、`update_training_set`、`rollback_write`、`combine_food_entries`、`log_meal_template`、`undo_meal_log`、`log_recipe`），直接读的 4 个（`get_training_log`、`get_nutrition`、`get_weight`、`search_exercises` 的动作库部分），间接读的约 10 个；HTTP 接口写 8 个（快捷餐记录 / 撤销、记食谱、合并 / 拆开、删一条饮食、训练页提交、离线体重），读约 12 个。

第 3 期的实现方式：新模块 `ownstore/read.py` 提供 `training_log(conn, user_id, start, end)`、`food_entries(…)`、`nutrition(…)`、`weight(…)`、`exercises(…)`，返回和 `sparky_client` 同名函数**逐字段相同**的结构；`ownstore/source.py` 的 `logs_for(conn, user_id)` 按开关返回「新表读者」或「SparkyFitness 读者」，调用方从 `sparky_client.training_log(sparky_client.client_for_user(conn, uid), …)` 改成 `logs_for(conn, uid).training_log(…)`。写这边把 `sparky_write` 拆成「检查」（日期、营养自洽、禁用动作、查重，和存储无关）+「存储后端」两层，SparkyFitness 后端是现在的代码，新表后端是新写的；同一组单测对两个后端各跑一遍。

## 4. 撤销（undo）

现在的撤销有三层，换存储后都保留：

1. **AI 工具里的删除**（`undo_sparky_entry`、`undo_meal_log`）：按 id 或「日期 + 餐 + 名字（+ 时间）」精确定位一条，删之前问用户（`assistant/.claude/settings.json` 的 `ask` 和 `ai/chat.py` 的 `ALWAYS_CONFIRM`）。工具名不变，这两处不用改；新表后端只是换了定位和删除的地方。
2. **AI 写入日志 + `rollback_write`**（`ai_write_log`）：每次 AI 写都记 before / after，回滚把 before 放回去。第 3 期起新表后端记的 before / after 是新表的行（带 `"store": "own"`），`writelog._sparky` 分派到 `ownstore.write.rollback`；没有 `store` 的旧日志仍按 SparkyFitness 回滚——只要第 4 期之前 SparkyFitness 还在。第 4 期之后，旧日志的回滚改成作用在新表上：因为 id 沿用、迁移把字段一一对应，SparkyFitness 形状的 before 可以换算成新表的行（`ownstore/convert.py`，和迁移用同一套映射）。
3. **今天页的删除 / 合并拆开 / 撤销快捷餐**：同 1，存货扣减按条目 id 加回（`pantry_auto.restore`），id 不变所以不用改。

双写期间（第 2 期）撤销完全走 SparkyFitness，然后把涉及的日期重新同步进新表，新表跟着变。

## 5. 迁移命令 `body-engine sparky-migrate`

```bash
body-engine sparky-migrate --user-id N --dry-run              # 预演：照常读、照常写，最后整个事务回滚，打印核对表
body-engine sparky-migrate --user-id N --confirm              # 真正写入（负责人看过预演结果再跑）
body-engine sparky-migrate --user-id N --since 2025-01-01 --until 2026-10-24 --dry-run
```

- 读：用这个用户自己的**只读** key（`client_for_user`），不需要读写 key，也不需要管理员 key；只能读到他自己的数据。
- 范围：默认从 2000-01-01 到用户今天（本地日期）；饮食、体重按日期范围读，训练翻历史分页直到早于 `--since`，食物库和动作库读全部「我的」。
- 写：同一套映射（`ownstore/mapping.py`）把 SparkyFitness 的行转成新表的行，`ON CONFLICT (id) DO UPDATE` 按 id 覆盖；范围内新表有、SparkyFitness 已经没有的 `origin = 'sparky'` 行删掉（用户在 SparkyFitness app 里删过的）。所以命令可以随便重复跑：第二次跑全部「不变」。
- 附加营养：`food_entries.added_sugar_g / sugary_drink` 从 `food_entry_extras` 带过来；糖 / 纤维 / 钠 SparkyFitness 有值时用 SparkyFitness 的，没有时用 `food_entry_extras` 的（和 `extras.merge` 的规则一样）。
- 核对：写完从新表读回来，和 SparkyFitness 逐条比对关键字段（饮食：日期、餐、名字、份数、热量和三大营养素保留 1 位小数；训练：日期、名字、每个动作的名字和每组的次数 / 重量 / RIR / 时长；体重：每个数值；食物库：名字、每份营养；动作库：名字、类别），打印逐表的表：

```
表                      SparkyFitness  新表(前)  新增  更新  不变  删除  新表(后)  核对
foods                   …
food_entries            …
body_measurements       …
logged_workouts         …
logged_workout_exercises…
logged_workout_sets     …
user_exercises          …
```

  最后一列 ✓ = 逐条一致，✗ 时列出前几条不一致的 id 和字段。任何一张表 ✗，退出码非 0。
- `--confirm` 才真正提交，否则拒绝并提示先 `--dry-run`；`--dry-run` 和 `--confirm` 不能同时给。真正写入的结果记进 `own_store_migrations`。
- 没连 SparkyFitness 的用户（`link_state` = not_linked）：打印「没有要迁的数据」，退出码 0。
- 同意：迁移只是把用户已经存在本系统里的数据换个表放，不做任何分析，所以不受「没同意前不处理」限制（否则下线 SparkyFitness 时没同意的人的数据会丢）。
- 正式环境的流程（第 3 期）：中控对每个用户先 `--dry-run`，把核对表（只有数字，没有健康数值）给负责人看，负责人确认后再 `--confirm`。命令本身不打印任何健康数值，不一致时只列 id 和字段名。

## 6. 开关

都是 `rollout/registry.py` 里的功能开关，按用户生效，代码默认关（`docs/rollout.md`）：

| 开关 | 期 | 开着时 | 关着时 |
|---|---|---|---|
| `own_food_store` | 2 | 每次写 SparkyFitness 之后，把涉及的日期从 SparkyFitness 读回来同步进新表（同步失败只记日志，不影响这次写） | 和现在完全一样：不多发任何请求、不碰新表 |
| `own_food_read` | 3 | 读路径用新表（还要求这个用户最近一次 `sparky-migrate` ok），写仍然双写 | 读 SparkyFitness |
| `own_food_only` | 4 | 只写新表，不再需要 SparkyFitness key | 双写 |

开的顺序：`own_food_store`（全体）→ 对每个用户跑迁移 → `own_food_read`（beta → 全体）→ 观察一到两周 → `own_food_only`（beta → 全体）→ 下线。

双写为什么是「写完回读那一天」而不是「把同一份数据各写一遍」：SparkyFitness 写入后才有 id、换算后的营养、动作快照，回读一次拿到的就是它实际存的东西，新表和它逐字段一致；更新、删除、回滚、合并都是「同步受影响的日期」，不用每种操作各写一套镜像逻辑；漏同步（比如用户直接在 SparkyFitness app 里改）由迁移命令重跑补齐。代价是开关开着时每次写多一两个 GET，记录类写入频率很低，可以接受。

## 7. 回退

- 第 2 期：关 `own_food_store`。新表数据留着不影响任何东西；要清空就 `DELETE FROM … WHERE user_id = N AND origin = 'sparky'` 后重跑迁移。
- 第 3 期：关 `own_food_read`，立即回到读 SparkyFitness。因为一直在双写，SparkyFitness 数据是全的。
- 第 4 期开 `own_food_only` 之后：SparkyFitness 不再收到新写入。回退 = 关 `own_food_only` + `own_food_read`，再用反向同步命令（第 4 期再写，`sparky-push-back`：把 `origin = 'own'` 的行写回 SparkyFitness）补上这段时间的新数据。所以 `own_food_only` 全体打开后，SparkyFitness 的容器和数据卷至少保留 4 周（只读，不再对外），确认不用回退后才真正删。
- 每一步之前都有 restic 备份（`ops.sh backup` 的 pg_dump 包含 Body Engine 和 SparkyFitness 两个库）。

## 8. Grafana

- 第 3 期：体重看板（`deploy/grafana/build_dashboards.py` 里用 Infinity 数据源读 SparkyFitness API 的面板）改成 SQL 读 `body_measurements`；可以顺便加饮食（`food_entries` 每日合计）和训练量（`logged_workout_sets`）面板。新表在迁移 735 里已经给 `grafana_ro` 授了 SELECT。
- 第 4 期：删掉 Infinity 数据源（`provisioning/datasources/datasources.yaml`、`quickstart/grafana/datasources/datasources.yaml`）和 Grafana 用的 SparkyFitness 只读 key；`ops.py` 的 `GRAFANA_DATASOURCES` 去掉 sparkyfitness。

## 9. 下线 SparkyFitness 的步骤（第 4 期）

1. 所有用户 `own_food_only` 打开满 4 周，期间没有回退。
2. 停止镜像：`training/plans.py` 不再建 preset / plan template / 动作，`recipes.py` 不再建 meal；`training_plans.sparky_*`、`recipe_meta.sparky_meal_id` 留着不用（以后的迁移再删列）。
3. 删代码：`sparky_client.py` 只留 `SparkyError` 和迁移命令要用的读函数（迁移命令留一个版本，方便晚到的部署者迁数据），`sparky_write.py` 的 SparkyFitness 后端、`link_state` 相关的「连接训练记录」入口、`account/provision.py` 的 sparky 步骤、`account/delete.py` 的删 SparkyFitness 账号、`goals/nutrition.py` 的 SparkyFitness 兜底。
4. 部署：根目录 `compose.yaml` 去掉 `BODY_SPARKY_URL` 和 key 挂载；`quickstart/` 去掉 SparkyFitness 三个容器和 `demo_sparky.py`（演示数据改写新表）；`deploy/ops/ops.py` 去掉 sparkyfitness 的备份、doctor、端口、tailscale serve 8444；私有部署仓库里对应的部分由中控改。
5. 停容器：`deploy/sparkyfitness/sf.sh down`（不删数据卷），最后一次 `pg_dump` 进备份；再过 4 周删数据卷和 `secrets/sparkyfitness*`。
6. 文档：README、`docs/README.md`、`docs/system-design.md`、`docs/LICENSES.md`、`THIRD_PARTY_NOTICES.md`、`CLAUDE.md`、`deploy/*/README.md`、使用手册（中英两份）、`body_engine/README.md` 里关于 SparkyFitness 的部分同步更新；`deploy/sparkyfitness/` 整个目录删掉。
7. `users.sparky_user_id / sparky_key_name`、`user_provisioning.sparky_*` 列留一个版本再删。

## 10. 工作量

| 期 | 内容 | 估计 |
|---|---|---|
| 1 | 本设计文档 | 0.5 天 |
| 2 | 迁移 735（7 张表 + 迁移记录表）、`ownstore/mapping.py`（映射）、`ownstore/mirror.py`（双写同步）、在 `sparky_write` / `food_combine` / `session_log` 的写入点挂同步、`sparky-migrate` 命令（预演、核对、记录）、单测（假客户端） | 1.5–2 天 |
| 3 | 新表读者（4 个形状）+ 约 20 个调用方改成 `logs_for`、写入拆「检查 + 后端」、新表后端、`store` 分派的回滚、`own_food_read` 开关、Grafana 体重面板、对每个用户正式迁移和核对 | 3–4 天 + 迁移当天 0.5 天 |
| 4 | `own_food_only`、反向同步命令、停镜像、删代码、部署和文档清理、两段观察期 | 2–3 天（不含观察期） |

## 11. 风险

- **SparkyFitness 接口字段和我们以为的不一样**（它更新快）：迁移只依赖现在 `sparky_client` / `sparky_write` 已经在用的接口和字段，映射遇到不认识的营养素放进 `nutrients`，不丢；预演的核对表会暴露对不上的字段。
- **双写期间用户直接在 SparkyFitness app 里改**：新表会落后，读开关打开前重跑迁移补齐；读开关打开后 SparkyFitness 网页对用户已经没有入口（产品原则：只有今天页一个入口）。
- **id 冲突**：SparkyFitness id 是 uuid，新行也是 uuid；写入时校验 `user_id`，冲突就报错，不会写到别人名下。
- **大用户的迁移时间**：饮食按范围一次读、训练分页读，几年数据也就几千条，几十秒内完成；整个迁移一个事务，失败全部回滚。

## 12. 许可证：独立实现

SparkyFitness 的许可证不允许商业使用。它的第 2 条还规定：修改版，以及包含它实质部分的新作品，都只能用同样的非商业条款发布。所以 fork 它、改写它、或把它的一部分搬进本项目，都不合规。本项目的规则：

- **新存储完全是我们自己写的。** 表结构、代码、字段命名都按 Body Engine 自己的设计来。不复制、不照搬 SparkyFitness 的源码、SQL schema、迁移文件、种子数据或界面文字，写的时候也不拿它们做参照。
- **字段按我们自己的约定命名。** 例如带单位后缀的 `protein_g`、`sodium_mg`、`weight_kg`、`body_fat_pct`，和 Body Engine 现有的表、工具一致。表的划分也按我们自己的需要来：营养存每条记录的合计；体重一人一天一行；训练分三层，和手表训练表 `workouts` 分开命名；加糖等字段并进饮食记录。
- **和 SparkyFitness 唯一的接触面是它对外的 HTTP API**，并且只有两处：
  - 迁移命令 `sparky-migrate` 用每个用户**自己的只读 key**，读出这个用户自己的数据；
  - 第 2、3 期的双写在写完之后读回同一个用户自己的数据。
  
  映射代码（`ownstore/mapping.py`）里出现的 SparkyFitness 字段名，只是读 API 返回值时用的键，就像读任何外部服务的接口一样；不出现在我们的表结构里。
- 读出来的是**我们用户自己的数据**。数据属于用户，不属于软件许可证的保护范围。
- 第 4 期下线后，代码里只保留迁移命令和它用到的 API 读函数（方便晚来的部署者迁数据），其余 SparkyFitness 相关代码全部删除。部署里不再包含 SparkyFitness 的任何镜像。
- 评审要求：涉及新存储的 PR，评审时确认没有从 SparkyFitness 仓库复制的内容（代码、SQL、注释、测试数据）。

---

## English summary

Food, food library, weight and workout logs live in SparkyFitness today, whose licence forbids commercial use; this plan moves them into Body Engine's own tables without changing any tool or endpoint behaviour.

- **Tables (migration 735)**: `foods`, `food_entries` (per-entry totals; absorbs `food_entry_extras`' added sugar / sugary drink), `body_measurements` (one row per user and day), `logged_workouts` / `logged_workout_exercises` / `logged_workout_sets` (named apart from the watch `workouts` table), `user_exercises` (the per-user exercise library the logs reference), and `own_store_migrations` (one row per real migration run with its per-table check). Every table has `user_id`; local dates; kg and kcal; `grafana_ro` gets SELECT. **Primary keys are text and keep the SparkyFitness ids** (new rows get a uuid), so every table already keyed by entry id (pantry deductions, extras, merges, the AI write log, training-page submits, `exercise_meta.app_exercise_id`) stays valid and undo works across the switch.
- **Phases**: (1) this design; (2) the tables, dual write behind flag `own_food_store` (after each SparkyFitness write the affected dates are read back and synced into the new tables; off = exactly as before) and `body-engine sparky-migrate --user-id N [--dry-run | --confirm]` (idempotent upsert by id, deletes rows SparkyFitness no longer has, per-table check against SparkyFitness, the owner confirms before production runs); (3) read path switch behind `own_food_read` (same-shape readers for `training_log` / `food_entries` / `nutrition` / `weight` / `exercises`, only for users whose last migration was ok), write split into checks + storage backend, rollback dispatched by a `store` marker, Grafana weight panel on SQL; (4) `own_food_only`, stop mirroring plans / recipes, remove SparkyFitness from code, compose, quickstart, ops and docs after a 4-week fallback window.
- **Rollback**: turn the flags off (dual write keeps SparkyFitness complete until phase 4); after phase 4 a push-back command restores SparkyFitness from rows written meanwhile.
- **Effort**: about 0.5 + 2 + 4 + 3 days.
- **Licence: independent implementation** (§12): clause 2 of SparkyFitness's licence keeps modified versions and works containing substantial parts of it under the same non-commercial terms, so nothing is forked, adapted or copied (source, SQL schema, migrations, seed data). Tables, code and field names are our own design. The only contact is its public HTTP API, used by the migration and the dual write to read each user's own data with that user's own read-only key.
