# 动作库质量：中英双语、演示图、质量分级（#248 后续）

> 2026-10-25 第一轮：术语表、50 条翻译样本、演示图覆盖审计和匹配、质量分级规则、缺口方案。数字都来自脚本，在一次性的
> `body_test_*` 库里按部署的方式重建整个动作库（迁移 → 精选 100 + free-exercise-db 777 → openGym 导入 5,391，共 6,268 条），
> 不读正式库。脚本：`tools/translate_exercises.py`、`tools/match_exercise_media.py`、`tools/exercise_quality_report.py`。

## 0. 结论（给负责人）

| 项目 | 现在 | 可以做到（只用合法免费来源） | 缺口 |
|---|---|---|---|
| 中英双语名称 / 要点 / 步骤 | 100 条有中文（精选） | 全部 6,268 条：剩下 6,168 条机翻，DeepSeek 约 **¥10–12**（50 条样本实际 ¥0.079） | 精选 100 条只有一句要点，95 条缺分步步骤（见 1.5） |
| 器械 / 肌群标签 | 17 种器械、2 种主肌群没有中文（约 490 + 80 条显示英文） | 术语表已给出中文，下一步加进标签表 | — |
| 演示图 | 871 条有图，**全部来自 free-exercise-db，版权不明**（见 2.1） | wger + Everkinetic（CC-BY-SA，可进公开仓库）**237 条**；再加 RepDB 免费层（只能私有部署）共 **477 条** | 去重后 5,667 个不同动作里还缺约 **5,280 个** |
| 质量达标（规则见第 3 节） | 107 条 | 翻译全量后：只算合法图 **196 条**；如果暂时保留 free-exercise-db 的图 **915 条** | 不达标的留在库里，按名字能找到，但不主动推荐 |

**最要紧的一件事**：free-exercise-db 的图不在它的 Unlicense 范围内——维护者说不知道图从哪来、"usage would be at your own risk"；
它的上游 exercises.json 写明这些图是网上抓的，作者不拥有版权，建议不要用于商业项目；有人反查到来源是 bodybuilding.com。
我们现在每个部署都下载并展示这 871 条的图。需要负责人决定：马上停用，还是在合法图补上之前暂时保留（第 4 节）。
**已决定（2026-10-25）**：内测阶段照旧展示、算达标，逐张标注来源和风险；商用前用开关 `exercise_media_free_db_off` 一键切成「暂无演示」（第 5 节）。

## 1. 中英双语

### 1.1 现状
- 精选 100 条（`catalog_data.py`）：人工中文名 + 一句中文要点；英文在 `catalog_en.py`。
- free-exercise-db 777 条、openGym 5,391 条（ExerciseDB 来源 1,153 + openGym 自写 4,238）：只有英文，`name_zh` 就是英文名。
- 标签：肌群、器械、动作模式的中文在 `catalog_data.py`，页面用 `tr()` 换英文。openGym 带来的 17 种器械（weighted、suspension trainer、
  weight plate、rope、assisted、landmine、bosu ball、sandbag 等，约 490 条）和 medicine ball / exercise ball 没有中文标签，中文页面显示英文；
  2 种主肌群（levator scapulae 65 条、serratus anterior 15 条）对不上，这些动作没有主肌群。

### 1.2 术语表
`body_engine/src/body_engine/training/translations/glossary.json`：器械、肌群、体位 / 握法 / 修饰词、动作名、后缀（V. 2、male、pov、
form check）五组约 460 条。每个英文词只有一个首选中文（`|` 后是可接受的写法），例如 bench press = 卧推、Romanian deadlift = 罗马尼亚硬拉、
landmine = 地雷管、stability ball = 瑞士球、suspension trainer = 悬挂训练带。改译名只改这里。

### 1.3 批处理（`tools/translate_exercises.py`）
- 离线跑，结果提交进仓库：`training/translations/exercises_zh.jsonl`（每行一个动作：中文名、描述、步骤、`zh_source=machine`、
  模型名、`src_sha` = 原文英文的哈希、`glossary_misses`）。运行时不调 AI。
- 幂等、可续跑：英文没变的行跳过；上游英文改了，旧译文不再套用（动作退回英文），重跑只翻这些。
- 每批 12 条，把这批出现的术语表词条一起给模型；名称里缺了术语表译法的再单独重试一次，仍缺的记进 `glossary_misses` 等人看；
  步骤条数必须和原文一样，否则重试。
- 服务：任何 OpenAI 兼容接口，默认 DeepSeek `deepseek-flash`（关思考模式），key 从本机 `secrets/deepseek_api_key` 读，不进仓库。
  价格按 `ai/fallback_params.py`（输入 ¥2 / 百万 token、输出 ¥8 / 百万 token，按高峰价）；`--budget-cny` 到了就停。
- 估算：6,168 条、约 316 万字符英文 → 约 156 万输入 + 109 万输出 token，**估 ¥11.8**；50 条样本实际 11.6k 输入 + 7.0k 输出 token = **¥0.079**，
  按比例全量约 **¥9.7**。预算建议给 ¥20（含重试）。对比：Claude Sonnet 同样的量约 20 美元。

### 1.4 50 条样本
分层抽样（按来源 × 类别），结果在 `exercises_zh.jsonl`。人工看过：名称基本地道（「杠铃泽奇深蹲保持」「哑铃站姿单臂蜘蛛弯举」
「交替叛逆者划船」），步骤忠实。发现并已修正的术语表问题：crossover 原来译成「交叉夹胸」，导致「交叉夹胸后撤弓步」，改为「交叉」；
Stairmaster = 爬楼机（模型给了登山机）。50 条里术语表遗漏 0 条。

### 1.5 怎么接进库里（本 PR 的代码）
- 迁移 `755_exercise_zh_source.sql`：`exercise_catalog.zh_source`（human / machine / 空）。已有中文的行（精选 100 条）标 human，
  导入永远不覆盖；禁用 / 小心标记在 `exercise_meta` 和 `joint_stress`，完全不动。
- 两个导入器（`seed-exercises --free-db`、`exercises-import-opengym`）读数据文件套用译文，所以部署后重跑这两个命令就生效，再跑一次结果不变。
- 开关 `exercise_zh_machine`（代码默认关）：关着时页面照旧显示英文（`catalog.rows_for` 给英文视图），中文也搜不到；数据照样在表里。
- 精选 100 条只有一句人工要点，95 条没有分步步骤（其中 53 条有合并进来的英文步骤）。第二轮：英文步骤机翻成中文、另外 42 条
  补写步骤，都标 machine，人工中文名和要点不动。

## 2. 演示图

### 2.1 现状审计（6,268 条）
| 来源 | 有图的动作 | 许可证 | 结论 |
|---|---|---|---|
| free-exercise-db 的图（精选 97 条 + free-exercise-db 774 条借用同一批图） | 871 | 仓库写 Unlicense，但图不在内：维护者 [issue #2](https://github.com/yuhonas/free-exercise-db/issues/2)「不知道图从哪来」；上游 [exercises.json CONTRIBUTING](https://github.com/wrkout/exercises.json/blob/master/CONTRIBUTING.md#exercise-images)「网上抓的，不拥有版权，建议不要商用」；[wrkout/exercises.json#305](https://github.com/wrkout/exercises.json/issues/305) 反查到 bodybuilding.com | **版权不明，不能算合法图** |
| openGym / ExerciseDB 的图和 GIF | 0（从来没拿） | Gym visual 所有，只授权给 openGym；hasaneyldrm/exercises-dataset 的 MIT 只覆盖代码和文字 | 不能用 |
| 其余 5,397 条 | 0 | — | — |

### 2.2 合法来源逐个核对（2026-10-25 读的原文）
| 来源 | 数量 | 许可证要点 | 能否进公开仓库 |
|---|---|---|---|
| **wger**（`/api/v2/exerciseinfo/`，932 个动作） | 279 个动作有图（383 张），46 个有视频（78 段，1080p HEVC .MOV） | 每张图自己的许可证：CC-BY-SA 3.0（88 张）/ 4.0（295 张）；字段 `license_author`、`author_history`、`license_object_url` 等。46 张标 `is_ai_generated`（不用），59 张没写作者（CC-BY-SA 要署名，先不用）；可用 278 张 | 可以：图作为单独文件、带各自的许可证和署名（ShareAlike 只约束图本身，不影响 AGPL 代码） |
| **Everkinetic**（github.com/everkinetic/data，钉 `446bb9a3`） | 293 个动作，每个两张姿势图（PNG + SVG 线稿） | 仓库 `LICENSE.md`：CC-BY-SA 4.0，「based on everkinetic.com created by Greg Priday」 | 可以，同上 |
| **RepDB 免费层**（sergei-argutin/exercise-dataset，`LICENSE-DATA.md` v1.0） | 650 个动作，512px WebP 扁平风格，AI 生成（RepDB 委托） | 应用内个人 / 商用免费；**必须**在关于 / 鸣谢页放「Exercise data by RepDB (repdb.co)」；**不得**作为数据集 / 数据仓库 / API 再发布；图**不得**作为生成式模型的输入、参考或条件（只是在有 AI 功能的 app 里展示不违反，但我们永远不能把这些图发给 AI 看） | **不行**，只能私有部署下载（ops overlay），公开仓库里连匹配表都不放 |
| Wikimedia Commons / Openverse | 零散照片（Commons「Weight training exercises」只有 20 个文件） | 每个文件不同 | 不成体系，不用 |
| MuscleWiki | — | API 条款禁止存储、转载 | 不能用 |

### 2.3 匹配（`tools/match_exercise_media.py` → `training/media_matches.json`）
名称标准化（大小写、连字符、复数、同义词：db → dumbbell、lever → machine、flyes → fly、one arm → single arm 等），没写器械的一方把器械词补进名称，然后
`分数 = 名称相似度（同序 1.0 / 同词集 0.97 / 否则词的 Jaccard）× 器械矛盾 0.5 × 主肌群矛盾 0.7`。
**高置信 ≥ 0.95 才会自动挂图**；0.80–0.95 为中置信，列出来给人看（多半是变体：暂停卧推、牧师凳锤式弯举、单臂 vs 双臂），低于 0.80 不列。
抽查高置信 25 条全部正确（例：Cable Triceps Pushdown ← Triceps Pushdown、Lever Shoulder Press ← Shoulder Press, on Machine）。

| 来源 | 高置信 | 中置信 |
|---|---|---|
| wger | 91 条（用到 76 个 wger 动作） | 35 |
| Everkinetic | 188 条（用到 156 个） | 100 |
| 合计（去掉重复挂的） | **237 条** | 再加 115 条 |
| RepDB 免费层（只做了统计，没提交匹配表） | 307 条（用到 249 个） | 再加 90 条 |
| 三者合计 | **477 条**（按去重后的不同动作算 389 个） | |

现在 871 条有图的动作里，能用合法图替换的：CC 来源 148 条，加 RepDB 227 条；精选 100 条里能替换 51 条（CC 37 条）。

### 2.4 覆盖小结
- 全库 6,268 条，按名称 + 器械去重后 5,667 个不同动作（601 条是重复，406 条带 V. 2 / male / pov / form check 这类变体标记）。
- 只用合法免费来源：237 条（只用可进公开仓库的）/ 477 条（加 RepDB 免费层，私有部署）。
- 缺口：去重后约 **5,280 个**不同动作没有合法图。

## 3. 质量分级（`training/quality.py`）

| 项 | 分 | 说明 |
|---|---|---|
| 中英名称 | 20 | 中文名有汉字（人工或机翻）且有英文名。**必须** |
| 文字 | 20 / 10 | 中英文各有 ≥ 2 条步骤 20 分；只有一句描述 / 要点（两种语言都有）10 分。至少要有一句：**必须** |
| 演示图 / 视频 | 25 | 页面真能显示的图：wger / Everkinetic，以及内测阶段照旧展示的 free-exercise-db 图（第 5 节）。**必须** |
| 主肌群 | 15 | 有；有氧类不要求。**必须** |
| 次肌群 | 5 | 有 |
| 器械 | 5 | 每种器械都有中文标签 |
| 不是变体 | 10 | 名称不带 V. 2 / (male) / pov / form check |

- **达标** = 总分 ≥ 70 且所有「必须」都满足，另外**不是重复**：名称（去掉变体标记）+ 器械相同的一组只留一条——有人工中文的、有合法图的、
  来源优先（精选 > free-exercise-db > ExerciseDB > openGym）、不带变体标记的那条；抽查 30 组都对。
- 达标的才出现在搜索结果、推荐、替换动作里；不达标的留在库里，**按名字精确搜**能找到，已经记过的训练照常显示。
- 分数、缺什么（`missing`）留在接口里给管家读；页面上不放解释句子。
- 实现放开关后面（下一轮，代码默认关）；分数线、各项分值、「必须」列表都是 `quality.py` 顶部的常量。

按现在的数据算（`tools/exercise_quality_report.py`）：

| 场景 | 达标 | 主要缺 |
|---|---|---|
| 现在（只有精选有中文），free-exercise-db 的图算 | 107 | 中文 6,121、图 5,308 |
| 翻译全量后，free-exercise-db 的图算 | 915 | 图 5,308、重复 601 |
| 翻译全量后，只算合法图（wger + Everkinetic 高置信） | 196 | 图 6,031 |
| 翻译全量后，合法图再加 RepDB 免费层 | 390 | 图 |

## 4. 缺口方案（给负责人选）

| 方案 | 能补多少 | 大概费用 | 许可证要点 | 能否进开源仓库 |
|---|---|---|---|---|
| A. wger + Everkinetic | 237 条（已算进上面） | 免费 | CC-BY-SA，逐张署名、改过要注明、衍生图也得 CC-BY-SA | 可以 |
| B. RepDB 免费层 | 再多 ~240 条（合计 477） | 免费 | 必须署名「Exercise data by RepDB (repdb.co)」；不能再发布成数据集；不能喂给生成式 AI | 不能，只放私有部署（ops overlay 下载） |
| C. RepDB 付费 Starter / Standard | **不增加覆盖**（同样 650 个动作），换成 1024px、Standard 有 524 个动画 | 一次性 $299 / $499（女性版 +$99） | 不要求署名；不能把原始图 / 数据集再发布；不能喂给生成式模型 | 不能，只放私有部署 |
| D. ExerciseDB（AscendAPI） | 1,300–1,500 个动作的 GIF（v2 宣称 5,000 GIF） | 网上说的「全量 $300 一次性」Gumroad 页面现在 404，无法核实；官方只写「custom pricing」 | 现行 API 条款**禁止存到服务器或设备**、缓存不超过 1 小时、不得再分发；而且这批 GIF 本来就是 Gym visual 的图 | 不能；要买也得写明允许私有部署存储，否则不可用 |
| E. Gym visual 授权 | 最多：openGym 的约 5,400 个动作和 ExerciseDB 的图都出自它 | 没有公开价格，要发邮件问（#47 里有邮件草稿，没发） | 要谈：私有部署存储、app 内展示、多用户、不交给 AI | 不能，只放私有部署 |
| F. 自己拍 | 按需：先拍最常用的 200–300 个 | 现金很少（三脚架 + 补光约 ¥300）；人工约 10 分钟 / 个，300 个 ≈ 50 小时；找人拍另算（粗估一天 100 个左右，几千元） | 自己的作品，可以选 CC-BY-SA 或保留版权；出镜的人要签肖像授权 | 可以 |

**建议**：
1. free-exercise-db 的图：建议停用（或者负责人决定在替换完之前暂时保留）。先上 A + B 替换掉能替换的 227 条。
2. 翻译全量（约 ¥10，预算 ¥20）+ 质量分级上线，这样搜索 / 推荐只出现「中英齐全 + 有合法图」的动作，其余留在库里不推荐。
3. 缺口：先发邮件问 Gym visual 的私有部署价格（覆盖最广）；同时对计划模板和用户实际记录最多的动作自己拍（方案 F，可以进开源仓库）。
   不建议买 RepDB 付费版（不增加覆盖）；ExerciseDB 在拿到允许存储的书面许可之前不考虑。

## 5. 负责人的决定（2026-10-25）和第二轮
- **翻译用 Claude API**：`claude-sonnet-5-5`，官方 Anthropic Python SDK，大批量走 Message Batches（半价）；key 由负责人放在本机
  `secrets/anthropic_api_key`，脚本只读不打印。请求：结构化 JSON 输出（`output_config.format`），思考关闭（`thinking: between_tools`，
  Sonnet 5.5 的最低档），effort low，不带 temperature。整个库用同一个模型：`--same-model` 会把 DeepSeek 做的 50 条样本也用 Claude 重翻。
  - 估算（按真实提示词逐组算，Claude 分词按偏保守的系数）：6,168 条、510 个请求、约 131 万输入 + 121 万输出 token →
    **Message Batches 约 7.4 美元**（同步调用约 14.8 美元）；加上少量同步重试 / 术语修正，预算给 20 美元（`--budget 20`，到了就停）。
  - 时间：Message Batches 多数 1 小时内完成，最长 24 小时；结果回来后的校验和修正几分钟。
  - 50 条样本的请求已经生成好（`--prepare-only`），key 放好后：
    `cd body_engine && uv run python ../tools/translate_exercises.py --slugs <50 条> --redo`（样本，约 0.13 美元同步）→ 看效果 →
    `uv run python ../tools/translate_exercises.py --same-model --batch-api --budget 20`（全量）。
- **演示图不买付费版**，只用免费又能合法使用的来源；openGym / Gym visual 的图仍然一张不用。没有合法图的动作显示「暂无演示」，按质量规则不主动推荐。
  free-exercise-db 现有的 871 张图：**照旧展示，也算达标**（中控 2026-10-25 回复：内测阶段效果优先，以后商用再补授权；发布方以 Unlicense 公开，
  不像 Gym visual 有明确禁用条款）。每张图标来源 free-exercise-db、许可证「Unlicense（上游声明，图片原始出处不明）」，风险写在
  `THIRD_PARTY_NOTICES.md`；开关 `exercise_media_free_db_off` 一打开，这批图一键全部变成「暂无演示」（商用前用）。

第二轮实现（开关都默认关）：
- `exercise_media_licensed`（只看「所有人」这一档，因为是版权问题）：迁移 760 `exercise_catalog.media`；`body-engine exercises-media-fetch`
  按 `media_matches.json` 的高置信匹配下载 wger（跳过 AI 生成、没写作者的图，只从 wger.de 下载）和 Everkinetic（钉住的 commit）的图到
  `<media>/exercises/licensed/`，每张图的作者 / 许可证 / 来源写进 `media`；图不改尺寸、不裁剪。打开后详情页先显示这些图、再显示
  free-exercise-db 的图，下面一行写每张图的来源和许可证，没有图的显示「暂无演示」；训练卡片、朋友动态的缩略图同样处理。
- `exercise_media_free_db_off`（只看「所有人」这一档，内测阶段保持关闭）：打开后 free-exercise-db 的图全部不再显示（文件还在，关掉即恢复），
  没有别的图的动作显示「暂无演示」，质量分级里也不再算有图。
- `exercise_quality_gate`（按人）：搜索、浏览、替换动作只给达标的动作；精确搜名字仍能找到不达标的；用户自己设置过的动作照常显示。
  有图 = 页面真能显示的图（已下载到本部署）。free-exercise-db 的图照旧算（决定 B），所以替换动作的候选基本不变（精选 100 条里 97 条有图）；
  一旦打开 `exercise_media_free_db_off`，候选只剩有 wger / Everkinetic 图的精选动作（约 37 个）。
- RepDB 免费层（再多约 240 条）只能放私有部署：匹配表和下载走 ops overlay，还没做。

部署（合并后）：`body-engine migrate`（755、760）→ `seed-exercises --free-db` → `exercises-import-opengym` → `exercises-media-fetch`
（出网 wger.de、raw.githubusercontent.com，约 600 张图、几十 MB）→ 中控按顺序打开 `exercise_zh_machine`、`exercise_media_licensed`、`exercise_quality_gate`
（`exercise_media_free_db_off` 保持关闭，商用前再开）。

## 6. 还没做
- 翻译全量（等 key）；器械 / 肌群标签补中文；精选动作的分步步骤；wger 的 46 段视频（1080p HEVC，约 3 GB，先不下）；RepDB 走 ops overlay；
  中置信的 115 条匹配人工过一遍。
