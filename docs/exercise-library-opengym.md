# 动作库扩充：openGym 动作文字（#248）/ Exercise library: openGym text

**English summary.** The exercise library now takes the *text* of openGym's catalogue (name, body part, equipment, muscles, category,
description, steps) at a pinned commit, never its media. One idempotent command, `body-engine exercises-import-opengym`, imports it
into `exercise_catalog`; every row keeps `source` and `license`; the emergency flag `exercise_opengym` hides the imported rows.

## 来源与许可证
- 仓库 <https://github.com/DuarteSantos8/openGym>，`catalogue/exercises/*.json`，**钉在 commit `7a3ace53c5e910c2185f22df803726c5838a99ae`**（2026-10-23；代码里是 `training/opengym/__init__.py` 的 `COMMIT`，快照文件头 `meta.commit` 也记着，导入前会核对）。
- **只要文字**：名称、部位、器械、目标 / 次要肌群、类别、描述、步骤。`catalogue/media/` 的图和动画（Gym visual 只授权给 openGym）**一张都没拿，路径和链接也没引用**；测试 `test_snapshot_is_pinned_text_only` 会拦住快照里出现图片 / 视频路径。
- 许可证按条保存：`textSource: exercisedb`（1,324 条）= ExerciseDB v1（AscendAPI，经 hasaneyldrm/exercises-dataset）**MIT**，目录里 `source=exercisedb, license=MIT`；其余 = openGym 自己写的 **AGPL-3.0**（本仓库同为 AGPL-3.0），`source=opengym, license=AGPL-3.0`。署名和 MIT 全文在 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。
- 待决定（#248）：AGPL 部分的文字打进 iOS 包（静态包）时怎么处理 App Store 条款；openGym 的 App Store 额外许可只适用于它自己的 app，我们不依赖它。

## 数据文件
- `body_engine/src/body_engine/training/opengym/snapshot.json.gz`（约 0.6 MB）：5,632 条（openGym 共 6,554 个文件，其中 922 个是只写了 `variantOf` 的变体桩，例如女性版，没有文字，跳过）。由 `tools/build_opengym_snapshot.py <openGym 克隆目录>` 生成，克隆必须在钉住的 commit；字节可复现。
- 迁移 `725_exercise_opengym.sql`：`exercise_catalog` 加 `license`、`description_en`、`instructions_en`、`text_sources`（每条上游记录的 source / license / 原 id / commit / 原部位 / 原目标 / 原类别 / 原器械）；旧行回填 license（free-exercise-db → Unlicense，精选 → AGPL-3.0）。

## 导入规则（`training/opengym/importer.py`）
1. openGym 内部同名同器械同目标的重复（ExerciseDB 里有）合成一行，两个 id 都记在 `text_sources`。
2. 和库里已有动作（精选 / free-exercise-db，不含本次导入的行）合并：规范化名称相同，或去掉 openGym 名字开头的器械词（`cable lat pulldown` → `lat pulldown`）后相同且器械一致；并且器械、主肌群不矛盾。合并后**已有动作的中文名、禁用 / 小心标记（joint_stress）、演示图、来源、中文提示都不动**，只补缺的英文描述 / 步骤 / 次要肌群，英文名不同时加成别名，记 `text_sources`。
3. 其余新增一行 `og_<openGym id>`；中文名先放英文名（界面按语言用 `tr()` / `name_en` 显示，中文翻译另排）；中文描述 / 步骤留空，英文放 `description_en` / `instructions_en`，详情页回退显示。
4. 受限动作的变体（直立划船、颈后推举 / 下拉、双杠臂屈伸）按名称归入 `family_slug`，带上同样的关节压力标记，用户对原动作的禁用 / 小心同样适用。
5. 映射表在 `mapping.py`，**对不上的不猜**：器械保留 openGym 的原英文名（仍可搜索 / 筛选），肌群不记，全部列在导入报告的 `unmapped`（`--report out.json` 导出完整清单）。类别 12 种全部有明确归属（strength / calisthenics / plyometrics / isometric / olympic / pilates / rehab / combat → strength，yoga / mobility → mobility，stretching、cardio 原样），原类别留在 `text_sources`；target 为 `cardiovascular system` 的归 cardio、没有主肌群。
6. 幂等：重跑不写任何行（测试检查 `updated_at` 不变）。`seed-exercises` 会把精选行的别名 / 次要肌群重置成种子值，所以**跑完 `seed-exercises` 之后再跑一次导入**即可。

## 命令
```
cd body_engine
uv run body-engine exercises-import-opengym --dry-run [--report /tmp/og.json]   # 只规划，不写
uv run body-engine exercises-import-opengym                                      # 导入（可重复跑）
uv run body-engine exercises-import-opengym --file other-snapshot.json.gz        # 换一份快照（commit 必须是钉住的）
```

## 导入结果（commit 7a3ace53c5，基准 = 精选 100 + free-exercise-db 777，共 877）
| | 数量 |
|---|---|
| openGym 条目（去掉变体桩后） | 5,632（另跳过 922 个变体桩） |
| 新增行 | 5,391（exercisedb / MIT 1,153；opengym / AGPL-3.0 4,238） |
| 合并进已有动作 | 227（精选 77、free-exercise-db 150） |
| openGym 内部重复合并 | 14 |
| 导入后动作总数 | 6,268 |
| 基准只有 100 个精选时（没装 free-exercise-db） | 新增 5,547、合并 71、总数 5,647 |

对不上的分类（不猜，见 `unmapped`）：器械 17 种（weighted 148、suspension trainer 133、weight plate 45、rope 44、assisted 43、landmine 33、bosu ball 12、sandbag 12 等，涉及约 490 条，保留原英文名）；次要肌群 7 种（hip flexors 782、ankles 216、feet 114、shins 72、hands 37、back 11、ankle stabilizers 4，没有对应的目录肌群，不记）；主肌群 2 种（levator scapulae 65、serratus anterior 15，主肌群留空）。

## 开关与上线
- 开关 `exercise_opengym`（代码默认关）：关着时搜索 / 详情 / 替换 / 周计划模板 / 肌肉负荷图都只看到原来的动作，导入的行和已记训练不受影响；上线后对所有人打开（`body-engine flags set exercise_opengym --state all`），以后只作应急开关。
- 先跑导入、再开开关（顺序反了没有害处，只是搜不到新动作）。

## 动作数变多之后
- 搜索 / 查找仍是内存里的一次扫描（动作表按库缓存），6,268 行下一次搜索约 30 ms；结果条数有上限（接口 ≤ 200，工具 ≤ 150）；工具定义没有改，总长度预算测试照过。
- 替换动作 `recommend_alternatives` 只用精选动作（只有它们有核对过的运动模式和关节压力标记），周计划模板只引用精选 slug，所以都不受影响；肌肉负荷图的「推荐动作」走搜索，精选动作加权排在前面。
