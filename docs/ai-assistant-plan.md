# AI 助手规划（v0.3，2026-10-17：P1 身体管家已实现，见第 6 节；多用户多提供商见第 9 节）

> 定位：**你的私人身体管理专家**。它知道你的身体数据、目标、限制和习惯，给出建议和安排，你有问题随时问它；记录也主要交给它——你说一句话、拍一张照片，它识别后归档进数据库。

## 1. 现在已经有的

| 能力 | 状态 |
|---|---|
| 入口 | Mac 上的 Claude Code / Claude 桌面版，通过 MCP 连接 Body Engine，**用你的 Claude 订阅，不额外计费**。手机上还用不了 |
| 能读的数据（16 个工具） | 基本资料、每日指标（步数、静息心率、HRV、血氧、呼吸、体重…）、睡眠、评分（睡眠 / 恢复 / 负荷 / CTL·ATL·TSB）、训练记录、饮食和每日目标、体重、补剂、化验（含参考范围和趋势）、日记、日记分析、身体事件、动作限制、规则提醒 |
| 能写的数据（7 个工具） | 记身体事件 / 改事件、记日记、加补剂 / 改补剂、改动作限制、关闭提醒；**每次写都要你批准** |
| 安全 | 助手只能看你有权看的数据（家人要授权）；写只能写给自己；每次调用都留审计记录 |
| 还没有 | 手机上的对话、拍照 / 截图识别、长期记忆、主动推送、写训练和饮食记录、生成计划 |

## 2. 要做成什么样

### 2.1 一句话 / 一张图就能记录（多模态输入 → 自动归档）
| 你给它 | 它识别成 | 存到 |
|---|---|---|
| 文字或语音：「昨晚两瓶啤酒，12 点才吃晚饭」 | 日记：喝酒、晚饭太晚（日期 = 昨天） | Body Engine 日记 |
| 饭菜照片、外卖截图、营养成分表 | 食物、份量、热量和三大营养素估算，嘌呤 / 饱和脂肪提示 | SparkyFitness 饮食记录 |
| 训练笔记、器械屏幕、别的 app 截图 | 动作、组数、次数、重量、RIR；对上动作库，禁用动作会拦下 | SparkyFitness 训练记录 |
| 化验单照片 / PDF | 项目、数值、单位、参考范围、异常标记 | Body Engine 化验 |
| 体脂秤 / InBody 截图 | 体重、体脂、肌肉量 | SparkyFitness 体重 / 围度 |
| Apple 健康 / 手表截图（Readiness、睡眠分这类第三方读不到的） | 对应的分数，作为对照 | Body Engine 每日指标（来源标为截图） |
| 补剂瓶子照片 | 名称、成分、剂量 | Body Engine 补剂 |
| 「肩又响了，有点疼」 | 身体事件（部位、严重程度） | Body Engine 事件 |

**归档流程**：识别类型 → 抽取成结构化数据 → 检查单位、日期、合理范围、是否重复 → 弹出「确认卡片」（写什么、写到哪、拿不准的字段标黄）→ 你点确认或改一下 → 写入并留审计、原图存档（随每晚备份加密）→ 随时可撤销。

### 2.2 最懂你的身体（长期记忆 + 个人档案）
- **身体档案卡**：助手自动维护、你可以查看和修改——基本情况、目标和进度、健康约束（例如化验异常带来的饮食限制）、伤病和禁用动作、作息规律、饮食偏好、什么对你有效（来自日记分析，例：喝酒后第二天 HRV 降多少）、当前训练和饮食计划。每次对话都会带上。
- **观察笔记**：对话里学到的新事实（「周三晚上固定打篮球」「乳糖不耐」），带来源和日期存进数据库；你可以删除。
- **回答有依据**：每条建议说清楚用了哪些数据（例：「最近 7 天恢复分均值 48，比你 60 天基线低 1.2 个标准差」），不编数字。

### 2.3 主动的专家
| 时机 | 内容 |
|---|---|
| 每天醒来后 | 晨间简报：恢复、睡眠、今天的训练要不要调整、练前吃什么、今天的提醒（3 行以内） |
| 训练前 | 按恢复分和计划给出今天的重量 / 组数调整，禁用动作的替代 |
| 每晚 | 今天饮食还差多少、睡前提醒（作息、补剂） |
| 每周日 | 周复盘：体重周均值、训练量、睡眠债、日记发现、下周调整 |
| 每月 / 体检前后 | 月报、复查提醒（按化验异常项）、化验解读 |
| 异常时 | 生病信号（静息心率升高 + HRV 降低）、过度疲劳（TSB 过低）、空腹训练的低血糖风险 |

推送方式：今天页（PWA，加到主屏幕后 iOS 支持网页推送）。

### 2.4 规划和安排
- **训练**：按目标、恢复、日程生成或调整每周计划和每次训练（遵守禁用动作、伤病限制、双进阶规则），减载周安排。
- **饮食**：按用户的健康约束和训练日 / 休息日目标出食谱、外食点单建议、购物清单；红肉等每周额度跟踪。
- **睡眠和作息**：结合你作息不规律的现实，给可执行的调整，而不是「11 点前睡」。
- **补剂和复查**：定期审查补剂（例：复查前停 护肝片）、肌酸对肌酐的影响提醒、复查时间。
- 所有安排先给方案，你确认后才写进 SparkyFitness / Body Engine。

### 2.5 边界
- 不是医生：不调整药物、不做诊断；出现危险信号（胸痛、晕厥、持续异常心率等）直接建议就医。
- 只用你的数据和公认的运动营养知识，拿不准就说不确定。

## 3. 入口和费用（2026-10-17 用户决定：全部走 Claude 订阅，不用 API）
**架构：一个后端（Mac），两个前台（电脑、手机）。**
- **后端**：Mac 上的 Body Engine + Open Wearables + SparkyFitness，加上 body-engine MCP server（所有工具）。
- **AI 助手 =「身体管家」Claude Code 会话**：常驻在 Mac 上（`assistant/` 目录，有自己的 CLAUDE.md 角色设定和 `.mcp.json`），用 **Remote Control** 对外。它用你的订阅登录，本机的 MCP 工具都能用；你在手机 Claude app 或电脑上都接进同一个会话，手机可以直接发照片 / 截图 / 文件。写数据库前的权限确认会推到手机上，相当于「确认卡片」。
- **前台**：今天页（PWA）看数据，底部「问管家」按钮跳到这个会话；对话、拍照记录在 Claude app（手机）/ Claude 桌面版或 Claude Code（电脑）里进行。
- **为什么不在今天页里自己做对话框**：Anthropic 规定订阅登录只能用于官方客户端（Claude app、claude.ai、Claude Code）；自己的后端程序化调用 Claude（包括 Agent SDK、`claude -p`）要用 API key 按量付费。见 code.claude.com/docs/en/legal-and-compliance 与 /remote-control。
- **限制**：Mac 要开着、`claude` 进程要一直跑（由 LaunchAgent 守护）；占用订阅额度；**家人不能共用你的订阅**（条款禁止共享账号），家人要用 AI 需要各自的 Claude 账号，或以后改走 API。
- **模型**：Claude Opus 5.5（在 Claude Code 里设置）。
- 手机版 Claude 的「自定义连接器」需要公网可达的 MCP，不用（不把健康数据接口暴露到公网）。

**隐私**：对话和照片由 Anthropic 处理（按你订阅账号的数据政策）。

## 4. 路线图
| 阶段 | 内容 | 依赖 |
|---|---|---|
| P0（已完成） | MCP + 23 个工具、审计、权限 | — |
| P1（已实现，待用户登录验收，见第 6 节） | 「身体管家」常驻会话（Remote Control）+ 角色设定 + 多模态记录归档（文字、照片、截图、PDF）+ 写 SparkyFitness（饮食、训练、体重）+ 长期记忆（身体档案卡、观察笔记）+ 今天页「问管家」入口 | 订阅；SparkyFitness 读写 key |
| P2 | 晨间简报、周复盘（Claude 桌面版本地定时任务或规则引擎 + 网页推送）、更多工具 | P1 |
| P3 | 规划：训练计划 / 饮食计划生成与调整、复查和补剂管理 | P2 |
| P4（接入层已实现，第 9 节） | 家人 / 其他用户：每人用自己的 AI 来源——订阅在自己的客户端里连远程 MCP，或填自己的 API key 在今天页里对话；各自的 token、key、记忆和预算 | 统一登录（Pocket ID）、每人的 SparkyFitness key |
| P8（已实现，第 9.14 节） | Google AI：订阅类 `gemini_plan_mcp`（Gemini CLI / Antigravity 连远程 MCP）、Gemini API 免费额度（单价 $0）（服务端 Gemini CLI 和管家目录的 Gemini CLI 配置已于 2026-10 下线） | Google 账号 / Gemini CLI |
| 以后 | RMSSD 上线后重算恢复、语音对话、手机版 Claude 接入（需要公网远程 MCP，要单独评估安全） | OW 0.10 等 |

## 5. 已决定（2026-10-17）
1. AI 全部走 Claude 订阅，不开 API；架构见第 3 节。
2. 模型：Claude Opus 5.5。
3. 允许助手写 SparkyFitness（饮食、训练、体重），每次写都要确认。
4. 允许长期记忆（身体档案卡 + 观察笔记），在今天页可查看、可删除。

## 6. 实现情况（P1「身体管家」，2026-10-17）
**会话**：`assistant/`（`CLAUDE.md` 角色设定、`.mcp.json` 连 body-engine、`.claude/settings.json` 最小权限 + 模型 `claude-opus-5-5`）；`deploy/assistant/butler.py` 在里面跑 `claude remote-control --name 身体管家 --spawn same-dir --permission-mode default --capacity 4 --no-chrome`，LaunchAgent `org.watch2whoop.body.butler`（登录启动、`KeepAlive` 崩溃重启、启动前清掉 `ANTHROPIC_API_KEY` 等变量）。会话地址写 `health_data/assistant/state.json`，doctor 检查「身体管家」一行。用法、权限设计和**第一次启用要你做的事（`claude auth login`、信任目录）**见 `deploy/assistant/README.md`。

**和第 2 节的对应**
| 目标 | 实现 |
|---|---|
| 2.1 文字 / 语音 → 日记、事件 | 已有的 `log_journal`、`add_event`；CLAUDE.md 写了日期规则（行为发生那天、凌晨算睡前那天、睡眠归醒来那天、化验按采样日期），每条消息由 hook 附上当前本地时间 |
| 2.1 饭菜照片 / 外卖截图 / 营养成分表 → SparkyFitness 饮食 | `log_food`：热量和三大营养素（+ 酒精）自洽检查、同餐同名去重、按内容建 / 复用自定义食物、`source=body_assistant` 幂等 |
| 2.1 训练笔记 / 截图 → SparkyFitness 训练，禁用动作拦下 | `search_exercises` + `log_workout`：对上动作库和 exercise_meta，banned 整次拒绝、同日同动作去重 |
| 2.1 化验单 → 化验 | `add_lab_results`：统一 test_key（能接上 8 月体检的趋势）、单位换算、离谱值拒绝、同日不覆盖 |
| 2.1 体脂秤 / InBody | `log_weight`（SparkyFitness check-in）+ `log_metric_snapshot`（其他项） |
| 2.1 Apple Readiness / 睡眠分截图 | `log_metric_snapshot`：单独的表 `metric_snapshots`，来源 screenshot，不覆盖传感器数据 |
| 2.1 补剂瓶子 | 已有的 `add_supplement` / `update_supplement` |
| 2.1 确认卡片 | 写工具在设置里是 `ask`：Remote Control 把权限确认推到手机 / 网页，点允许才写；拒绝就不写。拿不准的字段由管家在写之前的摘要里标出来（Claude Code 的确认框不能自定义黄色高亮） |
| 2.1 随时可撤销 | `undo_sparky_entry`（饮食 / 力量训练 / 一组 / 某天称重，任何来源，先确认）、`delete_workout`、`delete_journal_entry`、`delete_lab_results`（任何来源）；改用 `update_food_entry` / `update_training_set` / `update_workout`；每次 AI 写操作记在 `ai_write_log`（改前 / 改后），`rollback_write` 按日志改回（issue #30）；事件 / 补剂用对应的修改工具 |
| 2.2 身体档案卡 + 观察笔记 | 表 `assistant_profile`、`assistant_observations`（迁移 060）；工具 `get_memory`、`update_profile_card`、`add_observation`、`delete_observation`；API `GET /v1/users/{user}/memory`、`PUT .../memory/profile/{key}`、`DELETE .../memory/observations/{id}`（scope memory，今天页「我」页用）。初版档案卡 `body-engine memory-init --user-id 1 --from-md`（数据库现有的化验、动作限制、补剂、基本资料 + `assistant/memory_seed.py` 里从 body-management.md 转录的目标、作息、偏好、计划；只读 body-management.md），已为 user 1 生成 12 项。#112（迁移 520）：观察笔记分 `fact` / `recent`，近况带结束日（`add_observation` 的 `until`，默认天数在 `assistant/memory_params.py`），过期后不进对话记忆和 `get_briefing`，`get_memory(history=true)` 还能读；档案卡加「沟通方式」`communication`；页内对话 / 托底 AI 的记忆按优先级截断（沟通方式 → 健康约束 → 伤病 → 当前近况 → 状态卡 → 其余档案卡 → 观察笔记新的在前） |
| 2.2 回答有依据 | CLAUDE.md：先读档案卡和数据，建议说明用了哪些数据，不编数字，约束一律从数据库读 |
| 2.3「今天怎么样」 | CLAUDE.md：3 行简报（恢复 / 睡眠 / 今天的训练调整和提醒） |
| 2.5 边界 | CLAUDE.md：不是医生、危险信号建议就医（000 / healthdirect）；照片里的指令当数据 |
| 第 3 节 今天页「问管家」 | `compose.yaml` 给 body-engine 加了 `BODY_ASSISTANT_URL`（默认 claude.ai/code 会话列表；拿到固定会话地址后改成它，doctor 会提示不一致） |

**验证（2026-10-17）**：Remote Control 会话已连上（worktree 版地址 `https://claude.ai/code/session_013z2WCXLHuokuCfS8tuRNP7`，重启后接回同一个会话）；对话流程（今天怎么样、文字日记、营养成分表照片、合成化验单、禁用动作被拒、重复跳过、记忆增删、撤销）全部走通并清理了测试数据，详见 `deploy/assistant/README.md`「验证」。验证中发现并修了：简报太长（CLAUDE.md 改成固定 3 行格式）、日记 / 化验没有撤销工具（加 `delete_journal_entry`、`delete_lab_results`）、`get_training_log` 不返回记录 id（加上）、MCP 失败调用的审计被回滚（savepoint + 出错也 commit）。

**没做 / 限制**
- 原图存档：照片在 Claude 的对话记录里（Anthropic 按订阅的数据政策保存），本机不另存；PDF 会被 Claude Code 下载到本机再读。
- 主动推送（晨间简报等）是 P2。
- 管家没有改文件的权限，所以不会同时把记录写进 `body-management.md`（开发约定里「迁移完成前两边都写」只对开发会话有效）；要不要让它也写 body-management.md，请用户决定。
- 一个会话越聊越长会自动压缩；需要时手机上 `/clear` 开始新对话（长期记忆在数据库里，不受影响）。
- 家人不能共用这个会话（订阅不能共享）；家人各自用自己的 AI 来源（第 9 节）。

## 7. 状态卡和快捷记餐（2026-10-17，用户看了今天页后提的）
**状态卡**：「我」页的身体档案改成一个个短卡片（用户不编辑，由后端和管家维护）；长文本档案卡保留，作为管家的底层记忆。
- 表 `assistant_status`（迁移 070）；`GET /v1/users/{user}/memory` 多了 `status`（字段和自动项算法见 `body_engine/README.md`「状态卡」）。
- 自动算（source=auto）：体重和周变化、距目标、恢复 7 天均值、睡眠债、入睡时间、睡眠时长、尿酸 / ALT / 甘油三酯、本周训练次数、红肉 7 天。每小时任务 `refresh-status` + 读取时超过 30 分钟刷新。
- 管家判断（`set_status_items`，不弹确认）：目标体重 `goal_weight`、当前阶段 `phase`、伤病部位（例 `shoulder`），以后按需要加（计划执行等）。管家 CLAUDE.md：对话里发现状态变化就更新，状态项永远很短。
- 初版（user 1，`memory-init --from-md`）：14 项 = 11 个自动 + 3 个判断项。

**快捷记餐**：用户的早餐基本固定，今天页点一下就记，不用登录 SparkyFitness。
- 表 `meal_templates`、`meal_logs`；API `GET /v1/users/{user}/meal-templates`、`POST .../meal-templates/{id}/log`、`DELETE .../meal-logs/{log_id}`；管家工具 `get_meal_templates`、`create_meal_template`、`update_meal_template`、`log_meal_template`、`undo_meal_log`（写的都弹确认）。
- 初版「固定早餐」：面包 2 片 70g、牛油果 45g、培根 2 片 40g（红肉 40g）、黑咖啡 250ml、煎蛋 2 个 100g，合计 547 kcal / 蛋白 30 / 碳水 36 / 脂肪 31，克数是按常见份量估的（needs_confirmation=true）。
- 红肉：模板条目名字带「红肉 Ng」，规则引擎和状态卡按标注统计；有红肉时返回提醒（天天吃一周约 280g，会超 250g）。管家第一次记录时提醒一次（记一条观察笔记，之后不重复），不阻止记录。

**验证（2026-10-17 18:00）**：真实数据生成 14 个状态项；HTTP 接口记「固定早餐」→ 5 条 SparkyFitness 记录、红肉 7 天随之更新、同一天再记 409、撤销后全部删掉；管家对话里「早餐照常吃了」→ 记录 + 第一次红肉提醒；「培根其实是 short cut 50g…肩好多了」→ 改模板、撤销重记到 09:00、状态卡肩改「明显好转」。测试数据（记录、观察、模板和肩的改动、测试食物）都恢复了。

## 8. 食谱管理（2026-10-17）
- 管家推荐「吃什么」时优先用食谱库：`suggest_meals` 按当天剩余目标和健康约束打分给 3 道，管家挑 1–2 道说理由；用户在对话里说的新菜（文字 / 照片 / 网页）用 `create_recipe` 存进食谱库（同时在 SparkyFitness 建镜像），吃了用 `log_recipe` 记录。写的都弹确认。设计见系统设计第 15 节。
- **验证（2026-10-17 18:20）**：真实数据 25 道菜的每份热量和蛋白和 body-management.md 的估算基本一致（例：姜葱鸡油饭 751 / 原文 700 kcal，蘑菇烧鸡 340 / 380）；接口记录清蒸鲈鱼 → SparkyFitness 1 条、last_cooked_on 更新，撤销后都恢复。管家对话：「今天晚饭吃什么」→ suggest_meals → 推荐蘑菇烧鸡配饭（说明饱和脂肪低、不含红肉、嘌呤中等）；「我学了一道新菜：鸡胸滑蛋…」→ create_recipe（每份 466 kcal / 蛋白 53g）+ 记住「不吃虾」；「晚饭吃了这个，米饭 90g」→ log_recipe + log_food。测试数据都已删除。

## 9. 多用户多提供商（2026-10-17，为家人和其他用户准备）
> 用户原话的意思：以后多用户部署，每个人用的 AI 来源不一样（Claude 订阅、Codex / ChatGPT 订阅、各家 API），而且互不相通——我的 Claude 只能我用；有的国家用不了 Claude，那些用户要能换 Codex 或别家的 API 接入。
> 原则：**管家的工具、角色设定和记忆与提供商无关**；换的只是「谁来思考」。

### 9.1 三种接入方式
| 方式 | 适合谁 | 怎么接 | 谁付钱 | 后端碰不碰用户的 AI 账号 |
|---|---|---|---|---|
| ① 订阅 + 自己的官方客户端（`claude_plan_mcp`、`codex_plan_mcp`、`generic_mcp_client`） | 有 Claude Pro/Max、ChatGPT（Codex）等订阅的人 | 用户在**自己登录的**官方客户端（Claude Code / Claude 桌面版、Codex CLI、Gemini CLI…）里连 Body Engine 的**远程 MCP**（`/mcp`，Streamable HTTP），用自己的 AI token | 用户自己的订阅 | 不碰：后端只提供工具，不调用、不存任何订阅凭据 |
| ② API key + 今天页内对话（`anthropic_api`、`openai_api`、`gemini_api`、`openai_compatible`） | 没有订阅、所在地区用不了某家、或想在今天页里直接对话的人 | 在「我 → AI 设置」填自己的 key；「问管家」打开页内对话面板（流式、确认卡片、拍照） | 用户自己的 API 账户，按 token 计费，受月预算限制 | 用这个用户自己的 key 代表他调用（只在他自己的对话请求里解密） |
| ③ 部署者本人的身体管家（第 3、6 节） | 部署者（user 1） | Claude Code Remote Control 常驻会话 + 本机 stdio MCP | 部署者的 Claude 订阅 | 不变 |

一个人可以配多个，标一个默认；「问管家」按默认的决定：API 类 → 页内对话；订阅类 / 没设置 → 原来的跳转（只有部署者本人能拿到身体管家的链接）。

### 9.2 条款约束（保守做法）
- **Anthropic**（已核实，第 3 节）：Claude 订阅只能用在 Anthropic 的官方客户端里；自己的后端程序化调用 Claude 必须用 API key。→ 订阅只走 ①；后端调用 Claude 只用用户自己填的 API key（②）。
- **其他家的订阅**（ChatGPT / Codex、Google 的个人账号等）：按各自条款，本项目**一律当成「只能在官方客户端里用」**处理——后端不保存、不转发、不代登录任何订阅账号，用户在自己的 Codex CLI / Gemini CLI 里连 MCP。
- **API key**：属于用户自己的 API 账户，由后端代表这个用户调用，用量算在他的账户上；不同用户之间不共用 key（共用账号本身也违反各家条款）。
- 账号永远一人一个：不允许把一个人的订阅或 key 给别的用户用（技术上也做不到，见 9.3）。

### 9.3 隔离模型
| 东西 | 归属和隔离 |
|---|---|
| 提供商配置（表 `ai_providers`） | 按 user_id；接口路径只能是 `me` / 自己的 ID（**家人授权 shares 不包括 AI 配置**，即使授权了全部数据也看不到）；改 / 删别人的 → 404 / 403 |
| API key | AES-256-GCM 加密，主密钥 `secrets/ai_master_key`（权限 600，不进 git、不进环境变量），**附加数据绑定 user_id**（A 的密文拷到 B 名下也解不开）；任何接口只返回末 4 位；只在这个用户自己的对话请求里解密，解密前再核对行的 user_id |
| AI 客户端 token（远程 MCP） | `api_tokens` 里 scope 带 `mcp` 的 token，每个客户端一把、可单独吊销；**只能用在 `/mcp`**，普通 `/v1` 接口和网页登录都不认（泄露了也只能做 MCP 工具能做的事）；只读 token 看不到写工具 |
| 对话、待确认的写操作、用量（`ai_conversations` / `ai_messages` / `ai_tool_calls` / `ai_usage`） | 全部按 user_id；别人确认不了你的写操作、读不到你的对话 |
| 长期记忆 | 系统提示只带这个用户自己的档案卡 / 观察笔记 / 状态卡 |
| 工具权限 | 和 MCP / HTTP API 完全一样（同一个 `run_tool`）：读别人的数据要 shares，写只能写给自己 |
| 审计 | `assistant_audit`：channel = `mcp:<客户端>`（远程 MCP，取 token 名，不信客户端自报）/ `api:<kind>`（页内对话，另记 provider_id、model）/ `butler` / `mcp` |
| Grafana | 新表都不授权给 `grafana_ro` |

### 9.4 费用和预算
- 订阅类：不产生本项目的费用（用的是用户自己的订阅额度）。
- API 类：每个提供商一个**月预算**（`monthly_budget_usd`，默认 **$5**，按用户时区的自然月）。每次调模型前检查本月估算花费，到了就停（开始前 402，对话中途发 `error: budget`），提示去「AI 设置」调高。每次调用记 `ai_usage`（输入 / 输出 / 缓存 token、估算费用）。
- 估算用 `ai/catalog.py` 的价格表（2026-10-17 从各家价格页抄的；已公布涨价的按涨价后算；不在表里的模型按最贵一档 $10 / $50 每百万 token 估，宁可高估）；可以在提供商上填自定义单价（例如本机 Ollama 填 0）。实际账单以提供商为准。
- 单次大概多少：工具定义约 4.7 万字（62 个工具）是每次调用的固定开销。Claude 走自动提示缓存，第二次起这部分按 0.1 倍计；OpenAI / Gemini 按全价估。以 Opus 5.5 为例，一轮带两三次工具调用的问答大约 $0.02–0.08。
- 一条消息最多连续调 8 次模型。对话没有长度上限（#53，`ai/context.py`）：发给模型的历史超过预算（自带 key 12 万字符或 160 条消息，托底 2.4 万字符或 40 条，工具调用和结果都算）时，把早期轮次压缩成摘要存进 `ai_conversations.summary`（`summary_upto` = 摘要覆盖到的最后一条消息），之后每次调用只发「系统提示 + 摘要 + 最近几轮原文」，摘要复用、再超预算时把旧摘要和新轮次合成一份新的。只在用户消息处切（工具调用和结果不会被拆开，当前这一轮永远完整保留）。压缩是一次不带工具的调用（同一个提供商和模型），记进 `ai_usage`（`purpose = 'compact'`），算进同一份月预算 / 托底额度；压缩失败就只发旧摘要 + 最近几轮，下次再试。用户看到的对话始终完整。

### 9.5 地区可用性
- Claude、OpenAI、Gemini 的 API 和订阅都有各自「支持的国家和地区」名单（例如中国大陆都不在 Anthropic / OpenAI 的名单里），以各家官方页面为准。
- 用不了的用户：选 `openai_compatible` 接当地可用的服务（例：DeepSeek `https://api.deepseek.com/v1`、通义千问 DashScope 兼容模式 `https://dashscope.aliyuncs.com/compatible-mode/v1`），或在这台 Mac 上跑 **Ollama**（`http://host.docker.internal:11434/v1`，数据不出本机，费用 0）。工具、记忆、确认流程完全一样。
- 兼容接口的模型能力参差：有的不支持函数调用或图片（报错会原样显示）；建议用支持 function calling 的模型。
- 隐私：对话和用到的数据会发给用户选的提供商（按他自己账户的数据政策处理）；给家人开放前在同意书里写明。

### 9.6 实现（`body_engine/src/body_engine/ai/`，迁移 `110_ai_providers.sql`）
| 部分 | 内容 |
|---|---|
| 提供商配置 | `providers.py`、`catalog.py`、`crypto.py`；接口 `GET/POST /v1/users/{user}/ai-providers`、`PATCH/DELETE .../{id}`（字段见 `body_engine/README.md`）。openai_compatible 的 base_url：https 任意主机、http 只允许本机、不允许内网 IP 和带账号密码的地址 |
| 远程 MCP | `mcp_http.py`：`/mcp`，MCP Python SDK 的 StreamableHTTPSessionManager（无状态 + JSON 响应），每个请求先认 AI token；工具 / 注解 / 审计和 stdio 版共用 `mcp_server.build_server`；多了一个 prompt `butler`（角色设定）。只在 tailnet 暴露（沿用 443 → 8100 的 tailscale serve，不用 Funnel）。AI token：`tokens.py`、接口 `/v1/users/{user}/ai-tokens`、命令 `ai-token-create / list / revoke` |
| 页内对话 | `chat.py`（循环、确认、预算、用量）、`prompt.py` + `system_prompt.md`（从 `assistant/CLAUDE.md` 抽出的提供商无关角色设定；Claude Code 管家继续用原文件，改设定时两份一起改）、`adapters/`（Anthropic Messages API、OpenAI Responses API、OpenAI 兼容 Chat Completions、Gemini、测试用假适配器）、接口 `POST /v1/assistant/chat`、`/chat/confirm`（SSE）、`/conversations` |
| 今天页 | 新文件 `web/static/ai.js`、`ai.css`（AI 设置、MCP 连接说明和 token、对话面板：流式、确认卡片、拍照 / 选图自动缩到 1568px）；`app.js` 只加 3 个挂钩 |

各家怎么连远程 MCP（token 都放钥匙串、用环境变量 `BODY_ENGINE_MCP_TOKEN`，不写进配置文件）：
```bash
security add-generic-password -a "$USER" -s body-engine-mcp -w      # 粘贴今天页生成的 AI token
echo 'export BODY_ENGINE_MCP_TOKEN=$(security find-generic-password -s body-engine-mcp -w)' >> ~/.zshrc
claude mcp add-json --scope user body-engine '{"type": "http", "url": "https://your-server.your-tailnet.ts.net/mcp", "headers": {"Authorization": "Bearer ${BODY_ENGINE_MCP_TOKEN}"}}'
codex mcp add body-engine --url https://your-server.your-tailnet.ts.net/mcp --bearer-token-env-var BODY_ENGINE_MCP_TOKEN
```
Gemini CLI：`~/.gemini/settings.json` 的 `mcpServers` 加 `"body-engine": {"httpUrl": "https://…ts.net/mcp", "headers": {"Authorization": "Bearer $BODY_ENGINE_MCP_TOKEN"}}`。

### 9.7 验证（2026-10-17）
- `tests/test_ai.py` 16 个（独立库 `body_test_ai`，假适配器，不调付费 API）：key 不外泄、A/B 配置互不可见（即使 B 授权 A 看全部数据）、密文绑定用户、对话只用自己的 key 和自己的记忆、读工具循环 + 审计（provider / model）、写操作要确认（别人确认不了、不能确认两次）、拒绝和「不理它直接发新消息」、只读登录、图片校验、预算拒绝（中途和开始前）、订阅类返回跳转、远程 MCP（token 只能用在 /mcp、只读看不到写工具、吊销立即失效、只能看自己的数据、审计 channel）。全量 `uv run pytest` 252 个通过。
- 本机 `claude` CLI（2.1.302）实测连远程 MCP（临时起在 127.0.0.1:8190、指向独立测试库）：`--mcp-config` 和 `claude mcp add-json --scope user`（临时配置目录）两种写法都 ✔ Connected，配置里存的是 `${BODY_ENGINE_MCP_TOKEN}` 字面量；`get_profile` / `get_memory` 返回测试用户自己的数据；错 token → 401。
- Codex CLI：`codex mcp add … --bearer-token-env-var` 生成的配置正确（临时 CODEX_HOME）；真正连接要在登录后的 Codex 会话里，没有替用户登录，没测。Gemini CLI：见交接笔记。
- 真实 API（Anthropic / OpenAI / Gemini / 兼容接口）的端到端对话**没测**：要等用户或家人提供 key。适配器按官方 SDK（anthropic 1.15、openai 3.33、google-genai 2.31）的签名写，假适配器只验证了循环本身。

### 9.8 默认值（按最保守的做法先定，可改）
1. 四种 API 都开放（Anthropic、OpenAI、Gemini、OpenAI 兼容）；订阅类三种。
2. 默认模型：Claude `claude-opus-5-5`、OpenAI `gpt-6.1-sol`、Gemini `gemini-4-argon`；兼容接口必须自己填。
3. 每个提供商月预算默认 $5。
4. 对话里的图片存在数据库的对话记录里（历史只追加，不能事后删图片；删对话会一起删）；不进备份以外的地方。
5. 页内对话的写操作要确认，和身体管家的权限设置一致：长期记忆的增改（`update_profile_card`、`add_observation`、`set_status_items`）不确认，其余写工具（含删观察笔记）都要确认（`chat.NO_CONFIRM`）。
6. 身体管家的链接只给部署者本人（`BODY_ASSISTANT_USER_ID`，默认 1）；以前所有人都拿得到。

### 9.9 没做 / 以后
- 用户 1 本人：不配置 API，照旧用身体管家（Claude app）。今天页「问管家」行为不变。
- 容器里写 SparkyFitness（`log_food` 等）需要每个用户自己的 SparkyFitness 读写 key（现在容器只挂了 user 1 的只读 key）：家人用页内对话记饮食 / 训练前要先给他建 SparkyFitness 账号和 key。
- 页内对话没有「历史对话列表」界面（接口有）；没有语音；没有主动推送。
- 订阅类用户在手机上用：Claude app / ChatGPT app 的自定义连接器要公网可达的 MCP，现在只在 tailnet 里，所以订阅类用户目前只能在电脑上的客户端里用（同第 3 节的限制）。
- 主密钥轮换（v2 前缀）和「导出 / 删除我的 AI 数据」以后做。

### 9.10 托底 AI（2026-10-18，`ai/fallback.py`、迁移 `160_ai_fallback.sql`）
> 用户原话的意思：先给所有人都接上 DeepSeek Flash，用我的 API；每人每月只能用 ¥3；这是托底——注册了想用 AI 但没配自己的，就直接用我这个。

| 项 | 做法 |
|---|---|
| 谁用 | `ai_providers` 里**没有默认提供商**、也没有身体管家链接的人。配了自己的默认 AI 就用自己的（订阅类、停用的也不回落到托底）。部署者本人照旧用身体管家；测试用 `body-engine ai-fallback try --user-id 1 --message …`（只读登录，不写数据） |
| 模型 | `deepseek-flash`（= DeepSeek-V4.1-Flash-1017），`https://api.deepseek.com`，OpenAI 兼容；**关闭思考模式**（`extra_body={"thinking": {"type": "disabled"}}`：便宜、没有 reasoning token）；单次 `max_tokens` 4096（一周训练计划的参数约 2000 token，2048 太紧，#184）；图片先关（`VISION=False`，实测后再开） |
| 价格 | DeepSeek 中文价格页的人民币价（2026-10-18 核对）：缓存命中 0.04、未命中 2、输出 8 元 / 百万 token（高峰价；空闲时段半价）。**一律按高峰价算**（保守：实际花费不会比记的多，也不用管中国法定节假日），所以不用汇率。改价在 `ai/fallback_params.py` |
| 额度 | 每人每个自然月（用户时区）¥3（`MONTHLY_QUOTA_CNY`）；管理员可单独改（表 `ai_fallback_quota`，`ai-fallback quota --user-id N --cny 5`，`--cny 0` = 不给用，`--default` 恢复）。用量记 `ai_usage`：`kind=system_fallback`、`provider_id` 空、`cost_usd=0`（不算进用户自己 key 的预算）、`cost_cny` 实际花费、`cache_read_tokens` = 缓存命中 |
| 每次调模型前 | 本月已用完 → 402「本月额度已用完，MM-DD 重置」；**最坏情况估算**（整段 prompt 按未命中价 + 最大输出）超过剩余 → 停；每人每分钟 20 次、每天 150 次模型调用（429）；一条消息最多 12 次模型调用、最多 ¥0.3（`MAX_TURN_CNY`），**最后一次不能再调工具**：带上同样的工具但关掉调用（`tool_choice: none`）、在历史末尾加一句收尾提示，让模型按已查到的直接回答，页面上提示「查了 N 轮，没答全的回「继续」」（#184：以前 6 次用完直接报错，采购规划、排周计划这种多步问题答不完）；对话历史超过 2.4 万字符或 40 条消息时自动压缩成摘要（压缩本身也按上面的规则预检、计费） |
| 省 token | 精简角色设定 `ai/system_prompt_lite.md`（约 1100 字，完整版约 6600 字）、长期记忆快照最多 4000 字、29 个常用工具（约 1.5 万字；完整 64 个约 4.7 万字）。DeepSeek 自动做前缀缓存，历史只追加，第二轮起大部分输入按 0.04 元计 |
| 只读自己的数据 | 托底的工具定义里去掉了 `user` 参数，执行时强制 `user=me`：即使家人授权了，也不经过 shares 读别人的数据。没有心电图、逐拍心跳（`get_ecg_list`、`get_cardio` 不在里面）。写操作照样要确认 |
| 同意 | 同意书 `docs/family-consent.md` 第 4 节加了「托底 AI（可选）」条款（版本 2026-10-18.2）；**第一次用托底前单独弹层**说明发什么、发给谁，勾选后才能用，记在 `ai_fallback_consents`（版本 `fallback.CONSENT_VERSION`）；「AI 设置」里可以撤回。没同意主同意书（`needs_consent`）的也不能用。部署者本人不用单独同意 |
| 总额保护 | 表 `ai_fallback_state`：总开关（`ai-fallback enable / disable`）和最近一次余额（DeepSeek `GET /user/balance`，免费；对话时最多 10 分钟查一次，doctor 每小时查）。余额 < ¥2 → 所有人暂停（409「管理员的余额不足」）；余额查询失败不暂停（DeepSeek 自己会拒，适配器把 401 / 402 转成「托底 AI 暂停了」） |
| 管理员 | `body-engine ai-fallback status [--json]`：余额、本月合计、每人的花费 / 额度 / 调用次数 / token / 同意 / 自己的 AI；Ali 上 `docker exec $(docker ps -qf name=body-body-engine) body-engine ai-fallback status` |
| doctor | Ali `doctor.py` 的「托底 AI」：✓ 余额和本月花费；⚠ 没开通、被关掉、余额 < ¥10 或少于本月大家最多还会花的钱（提醒充值 platform.deepseek.com）；✗ 暂停（余额 < ¥2 / key 失效）或容器里读不到 key |
| 界面 | 「我 → AI 设置」多一行「托底 AI · DeepSeek Flash · 本月 ¥1.20 / ¥3」（点开：剩余、重置日、同意 / 撤回；部署者看到账户余额）；对话面板顶部「托底 · 剩 ¥1.80 / ¥3 · 11-01 重置」 |
| 顺带修的 | 用户自己配的 `openai_compatible` 接 DeepSeek（默认开思考模式）时，带工具的后续请求必须回传 `reasoning_content`，否则 400：兼容适配器现在把它存在 native 里、同一个 kind:model 原样回传；DeepSeek 的缓存命中 token 也拆出来了 |
| key | Ali `/opt/body-management/secrets/deepseek_api_key`（600 root，`compose.body.yaml` 挂成 docker secret，`BODY_DEEPSEEK_API_KEY_FILE`）；Mac 主仓库 `secrets/` 一份（Mac 每晚也会拉 Ali 的 secrets 备份）。没有这个文件就没有托底（今天页照旧「未设置」） |
| 验证 | `tests/test_ai_fallback.py` 15 个（假适配器，DeepSeek 余额接口打桩）；2026-10-18 在 Mac 上用真 key 跑了一次两轮工具调用（不含健康数据）：思考模式确实关了、缓存命中能拆出、工具循环正常、余额接口返回 ¥12.01，两轮共约 ¥0.0005 |

成本估算：一次模型调用的输入约 0.8–1.2 万 token（设定 + 工具 + 记忆 + 历史）；缓存未命中约 ¥0.02，命中后约 ¥0.001，再加输出（500 token ≈ ¥0.004）。一个问题通常 1–3 次调用，¥3 大约够 150–400 个问题。**余额**：7 个人 × ¥3 = ¥21 / 月，2026-10-18 余额只有 ¥12.01，要先充值。

**2026-10-21（issue #58）补齐今天页的读工具**：托底 AI 被问「衰老速度什么意思」时答「你的数据里没有」，因为白名单里没有身体年龄、压力、健康监测等本系统自己算的指标的读工具。`TOOL_NAMES` 从 44 个加到 63 个：`get_healthspan`、`get_stress`、`get_health_monitor`、`get_hr_zones`、`get_vo2max`、`get_report`、`get_sleep_details`、`get_lab_history`、`get_metric_snapshots`、`analyze_journal`、`get_diet_profile`、`get_nutrition_goals`、`get_meal_templates`、`list_recipes`、`get_recipe`、`suggest_meals`、`get_shopping_list`、`get_weekly_specials`、`find_products`（仍然不加 `get_cardio` / `get_ecg_list`）。`system_prompt_lite.md` 加了一句「今天页上的指标都由本系统计算，先调工具，不要说没有」。`tests/test_fallback_coverage.py` 把今天页脚本调用的每个接口、每个讲解 key 对到托底的读工具，新加卡片忘了加白名单时 CI 会失败。固定开销：工具定义 26.6k → 34.2k 字符（按 DeepSeek 的估算规则约 +2.6k token），设定 +0.1k token；每次模型调用缓存未命中多 ¥0.005，命中多 ¥0.0001。趋势页的几张状态卡（HRV 状态、训练状态、身体电量、恢复时间、韧性、生病风险、生物钟等）目前**没有任何 AI 工具**能读（只在 `/trends` 里算），测试里列为 `NO_TOOL_YET`，留作后续。

#### 9.10.1 额度卡（2026-10-23，issue #182，`ai/quota_cards.py`、迁移 `645_ai_quota_cards.sql`，开关 `ai_cards`）
> 负责人提出：托底额度用完要等下个月 1 号，需要「额度卡」让管理员提前给人恢复或增加额度：定期发、作为奖励送、单独补偿。私有部署以后的付费扩充也调同一个发放函数（公开仓库不做支付）。
>
> **2026-10-23 负责人决定：这次只把功能做好，先不上线，内测也不开。** 合并部署后开关 `ai_cards` 保持关闭，负责人决定上线时再开。关着时：今天页没有任何卡片入口（AI 设置、额度行、Jarvis 详情、对话里的按钮、通知设置里的「Jarvis 额度卡」类型都不出现，`/ai-cards` 接口 404）；`ai-cards periodic` 什么也不做；`ai-cards grant --user N` 照常能发卡（方便以后测试），但不发通知，`--all` 只发给开关开着的人（关着时 0 人）。

| 项 | 做法 |
|---|---|
| 卡的种类 | `reset` 重置卡：本月已用额度清零（用卡之前的花费不再算进这个人的额度）；`extend` 扩充卡：本月额度 + X 元（每张最多 ¥50，`quota_cards_params.MAX_EXTEND_CNY`）。卡在**用掉的那个月**生效，没用的卡留到过期 |
| 额度怎么算 | `fallback.allowance()`：本月可用 = 月额度 + 本月用过的扩充卡合计 − 本月最后一次用重置卡之后的花费（没用过重置卡就从月初算）。`summary` / `precheck` / 联网搜索（`ai/web`）都用它；`month_spent_cny` 仍是管理员这个月为这个人实际付的钱（`ai-fallback status` 的「本月」列） |
| 来源和状态 | 来源 `admin` / `periodic` / `reward` / `other`；状态 未用 → 已用 / 过期 / 撤销（不会回到未用）。默认 30 天有效（`--expires 30d / 2w / 2026-11-30 / never`）；过期由每小时任务和每次读的时候标记 |
| 表 | `ai_quota_cards`（卡：发放人 `granted_by`、原因、`grant_key` 幂等键、有效期、用掉 / 撤销的时间和人）；`ai_quota_ledger`（只追加的流水：发放 / 使用 / 过期 / 撤销，谁做的，用卡时记下用前已用和用前额度——重置卡清掉的金额就是「用前已用」）；`ai_quota_card_rules`（定期发放规则）。三张表都给 `grafana_ro` 读（管理员看板） |
| 发放函数（给别的模块和插件用） | `quota_cards.grant(conn, user_id, kind, source=, reason=, actor=, amount_cny=, count=, expires_at=, key=)`：参数校验（种类、来源、金额、张数 1–10、原因必填 ≤ 200 字、用户存在）、带 `key` 时幂等（同一个人同一个 key 只发一次；多张时每张 `key#i`）、写流水、发站内通知；在调用方的事务里，调用方提交。`quota_cards.reward(conn, user_id, reason, module=, key=)` = `source=reward`、key 为 `reward:<module>:<key>`、`actor=module:<module>`，例如「反馈被采纳」`reward(conn, uid, "反馈被采纳", module="feedback", key=f"adopted:{id}")`。私有插件（包括以后的付费扩充）调同一个函数，用自己的 `actor`（如 `plugin:<名字>`）和 key（如订单号）保证不重复发 |
| 管理员 CLI | `body-engine ai-cards grant --user N \| --all --kind reset\|extend [--amount X] --count 1 --expires 30d --reason … [--key K] [--no-notify] [--by 名字]`（`--all` = 开关对他开着、托底额度 > 0 的人）；`list [--user N] [--status unused]`、`ledger [--user N]`、`revoke --card ID … \| --user N --reason …`（只收回没用的卡）；`ai-fallback status` 多一列「额度卡」（R = 重置卡、E = 扩充卡张数） |
| 定期发放 | 规则在表里，可以随时改：`ai-cards rule-set <名字> --period monthly --day 1 --kind reset --count 1 --expires-days 30 --active-days 30 --reason "每月一张"`（例：每月 1 号给近 30 天用过 Jarvis 的人各 1 张）；`--period weekly --day 1` = 每周一；`rule-on / rule-off`、`rules`。每小时 `ai-cards periodic`（`deploy/ops/ops.py` 的 hourly；私有部署的定时任务要加同一行）：先标过期，再按**每个人的本地日期**看规则在当前周期是否到期，用 key `rule:<名字>:<周期>`（`2026-10` / `2026-W43`）发，所以同一周期不会重复发，漏跑的会在同一周期内补发；`--dry-run` 只列出会发给谁。只发给开关对他开着、托底额度 > 0 的人 |
| 管理员看板 | Grafana「身体管理 · Jarvis 额度卡」（`deploy/grafana/build_dashboards.py` → `06-ai-cards.json`）：没用的卡总数、时段内发出 / 用掉、每人没用的卡（重置 / 扩充 / 扩充合计 / 最早到期 / 累计用过）、额度流水、定期规则 |
| 用户端（开关 `ai_cards`） | 「我 → AI 设置」里 Jarvis 那行和对话面板顶部的额度行后面显示「额度卡 ×N」；点额度行或 Jarvis 详情里的「额度卡」打开卡片列表（重置卡 / 扩充卡 +N%、来源、原因、到期日，下面是用过 / 过期 / 收回的记录）；额度用完时提示里多一句「可以用一张额度卡」，对话里出现「用一张额度卡」按钮。**界面上只有「Jarvis」和百分比**：扩充卡按月额度换算成 +N%，不显示金额和提供商 |
| 用卡 | `POST /v1/users/me/ai-cards/{id}/use`：先弹确认（「本月 Jarvis 额度回到 100%（现在剩 N%）」）；只能用自己的卡（别人的卡 404，家人授权不包括 AI）；**连点只算一次**：行加锁后只有「未用」才会变「已用」，同一张卡再点返回 `already: true`、不改任何东西、不写第二条流水；按钮在请求期间也禁用。返回新的额度，页面立即刷新百分比。本月还没用就不让用重置卡（免得白费）；额度为 0（管理员不给用）的人不能用卡 |
| 通知 | 通知类型带 `"flag": "ai_cards"`：开关对他关着时通知设置里不出现这一类。收到卡（新发出的才发，幂等重放不发）时 `notify.notify(conn, uid, "ai_card", …, url="/app/?tab=me&ai_cards=1")`，通知类型「Jarvis 额度卡」登记在 `notify/params.py`，用户可以在通知设置里关推送；开关没对他开时不发通知（卡照样发，开了以后能看到） |
| 安全和成本 | **卡绕不过全体暂停**：DeepSeek 余额低于停用线（或管理员关掉）时 `precheck` 先拦，用卡也被拒绝（「Jarvis 现在暂停了，卡先留着」）。`ai-fallback status --json` 的 `month_remaining_cny`（doctor 的「本月最多还会花」）= 每个可用的人本月剩下的额度 + 没用的卡最多还能加的钱：所有扩充卡合计 + 每张重置卡 ×（月额度 + 本月已扩充 + 没用的扩充卡合计）——先用完扩充卡再用重置卡时的最坏情况；另外给出 `month_cards_max_cny`。私有部署的 doctor 直接读这个字段，不用改 |
| 验证 | `tests/test_ai_quota_cards.py`：校验、幂等、通知、奖励、重置 / 扩充的额度计算、连点、拒绝的几种情况、暂停时绕不过、过期和收回、doctor 金额、定期规则（干跑、同一周期只发一次、下个周期再发、关规则、关开关）、CLI、接口（开关关着 404、别人的卡、英文提示） |

### 9.11 多模态：照片、视频、语音（2026-10-18）
> 用户原话的意思：托底 AI 不能发语音、照片、视频是有问题的；所有 API 都走 DeepSeek，模型不支持的能力自己搭配。产品本来就是「没有输入框，所有记录靠跟 AI 对话」。

| 输入 | 怎么做 | 发给谁 | 费用 |
|---|---|---|---|
| 📷 照片 | 页面里压到长边 ≤ 1600 px 的 JPEG（`ai-media.js`），一次最多 8 张（`chat.MAX_IMAGES`），发送前有缩略图、点一下删除 | 用户的 AI；托底 = DeepSeek `deepseek-flash`（支持图片，只能放在 user 消息里，每张最多计 1024 token，2026-10-18 实测 900×600 约 350 token） | 计入 ¥3（`fallback_params.IMAGE_TOKENS` 用在调用前的最坏估算；不再按 base64 字数估） |
| 🎬 视频 | DeepSeek 不收视频：页面里用 video + canvas 每约 2.5 秒抽一帧、最多 8 帧（和照片合计 ≤ 8）、长边 1024；声音轨用 Web Audio 解码成 16 kHz WAV 走语音识别，文字放进输入框（「［视频语音］…」）；消息里带「［视频 N 帧，约 X 秒］」告诉模型是同一段视频。文件 > 80 MB 不解声音（手机浏览器内存） | 帧同照片；声音只到本服务器 | 帧计入 ¥3；语音识别不花钱 |
| 🎤 按住说话 | 页面用 getUserMedia + Web Audio 采原始 PCM（不用 MediaRecorder，避开各浏览器的编码差异），转 16 kHz 单声道 WAV（60 秒 1.9 MB），`POST /v1/assistant/transcribe` → 本机 `asr` 容器识别 → 文字放进输入框（「［语音］…」），**用户看过、改过再发**；上滑取消，最长 60 秒 | **不出服务器**（DeepSeek 没有语音识别接口，按「只用 DeepSeek」的约束不接别家付费语音） | 不花钱；每人每天 50 条、每分钟 6 条（表 `asr_usage`，只存时长，不存录音和文字） |

**语音识别选型**（`deploy/asr/`）：sherpa-onnx 1.13.8 + **SenseVoice-Small int8**（FunAudioLLM，中 / 英 / 日 / 韩 / 粤，自带标点和数字规范化），模型 239 MB（Hugging Face 固定版本 + sha256，`fetch_model.sh` 下到 `$BODY_ROOT/models/asr/`，不进镜像、不进 git），镜像 242 MB（python:3.12.15-slim）。对比过 Paraformer-zh-small（82 MB、内存约 270 MB、更快，但没有标点、只认中文、错字更多）和 faster-whisper（中文差、要 ffmpeg，更大）。
- **Ali 实测（2026-10-18，限 1 核 / 768 MB）**：5 秒语音约 0.5 秒（约 10 倍实时）；60 秒语音 8.3 秒；首次加载模型 1.6 秒；加载后内存约 370 MB、识别 60 秒语音时峰值约 530 MB；闲置 10 分钟卸载模型后约 40 MB。
- 准确率（macOS 合成的普通话，5 句）：数字、单位都对（「88.6公斤」「12点半」「100公斤」），同音字有错（「五组→无足」「补剂→补寄」「一粒→一里」）；真人录音一般更好。所以识别结果**先放输入框让用户改**，提示词也告诉模型「［语音］可能有同音错字」。
- 容器：`compose.body.yaml` 的 `asr`，`cpus: 1.0`、`mem_limit: 640m`，同一时间只识别一条、最多 3 条排队（再多 503「正忙」），录音只在内存里、不落盘、日志只记时长；`127.0.0.1:8770` 只给 doctor，Body Engine 走 `http://asr:8000`（`BODY_ASR_URL`，没设就是关闭，页面退回键盘听写）。doctor 的容器内存预算从 3584M 调到 4224M。
- **iOS**：Safari / 主屏幕 PWA 支持 getUserMedia（iOS 14.5+），代码路径是标准的，但这次没在真机 / 模拟器上实测（这台 Mac 的模拟器工具要求 `xcode-select` 指向完整 Xcode）。**Watch2Whoop app 的 WKWebView** 里录音要 app 加 `NSMicrophoneUsageDescription` 并处理 WebView 的麦克风权限请求：在 app 声明 `window.Watch2WhoopFeatures = {mic: true}` 之前，页面在 app 里不调用麦克风，按 🎤 会提示「用键盘上的 🎤 听写」（手册里写了）。**2026-10-23（#171，app 1.0.7）已打通**：app 回答 WebKit 的媒体采集请求（iOS 15+），只给今天页自己的来源授予麦克风（不给相机、不给离线副本），页面加载后注入 `window.Watch2WhoopFeatures.mic`；页面的判断集中在 `ai-media.js` 的 `voicePath()`，iOS 里拒绝了麦克风时提示并给「打开设置」（`mic-settings` → app 打开本 app 的设置页）。app 里选照片 / 视频走系统相册不需要权限，但「拍照」会用相机，`NSCameraUsageDescription` 现在写的是「只扫二维码、不拍照」，要一起改。
- 隐私：同意书第 4 节托底条款加了「照片、截图、视频画面会发给 DeepSeek」，新增「语音输入」条款（服务器上转文字、不发第三方、转完即删）；版本 2026-10-18.3。托底的单独同意版本升到 2026-10-18.2，之前同意过的人第一次再用会重新看到确认弹层。
- 2026-10-22：同意书 2026-10-22.1 和托底确认要点写明「呼吸频率 / 血氧 / 手腕温度等每日指标」和「采购清单、家里存货、已连接的超市、商品价格和购买记录」（#81 核对时发现这两类之前没写明，但已经在发），托底的单独同意版本升到 2026-10-22；和心电图 / 心跳间期那次改版（#79）一起，用户只需要重新确认一次。
- 公网 nginx（`deploy/ali/public/watch2whoop.https.conf`）：`Permissions-Policy` 原来是 `microphone=()`（禁止录音）→ `microphone=(self)`；`/v1/` 的请求体上限 2 MB 发几张照片就会超 → `/v1/assistant/chat` 单独 16 MB、SSE 不缓冲、读超时 300 秒，`/v1/assistant/transcribe` 3 MB。今天页的 CSP 加了 `img-src blob:`、`media-src 'self' blob:`（读选中的照片 / 视频）。
- 托底多了 3 个写工具：`add_lab_results`（化验单照片）、`log_metric_snapshot`（手表 / InBody 截图）、`add_supplement`（补剂瓶子），照旧每次写之前确认。
- 验证：`tests/test_ai_fallback.py` 19 个（图片 / 8 帧上限、估算按每张 1024 token、旧同意要重新确认、语音：关闭时 503、格式 / 时长校验、每天 / 每分钟限次、识别服务挂了也计数、要登录、413）。真实调用（本地测试账号，无真实数据）：一张 InBody 图正确读出了体重和体脂率（¥0.0008）；浏览器里用 ffmpeg 生成的 13 秒视频走完整流程：抽 5 帧 + 声音识别进输入框 → 发送 → DeepSeek 从画面读出 InBody 数值、从文字读出午餐（¥0.03）。

### 9.12 自由选择 Jarvis 用哪个 AI、停用后回落（2026-10-18，`ai/choice.py`、迁移 `190_ai_choice.sql`）
> 用户原话的意思：配了自己的 AI 以后想回去用内置的托底，没法选；把自己的 AI 停用了，Jarvis 还是往那边跳，不会自动回到托底。

**原因**（两处）：① 后端只看 `ai_providers.is_default`，不管它是不是停用了，停用的订阅还是「mcp」模式；吊销 AI token 也不影响。② 前端在「AI 设置」改完后 `app.js` 的 `onAiChange` 只重画页面，没更新自己保存的 `S.ai` / `S.assistantUrl`，Jarvis 按钮按旧状态跳，直到整页刷新。另外弹层「先关再开」时关闭动画的定时器会把新弹层藏掉（「去 AI 设置」「换 AI」会碰到），一起修了。

**现在**：
- 每人一个「Jarvis 用」的选择（表 `ai_choice`：provider / fallback / butler；没有行 = 自动）。「我 → AI 设置」最上面是单选列表：托底 AI（固定一项，每个人都有，部署者也有）、自己的每个 AI、身体管家（只有部署者）；对话面板顶部的「托底 ▾」是同一个列表的快速切换。`GET/PUT /v1/users/{user}/ai-choice`，PUT 的回答里带新的 `ai` 状态，页面立即切换、不刷新；切到页内 AI（API / 托底）会开新对话，切到订阅 / 管家会关掉面板，下次点 Jarvis 就去那边。
- **选中的不能用时自动往下落**：选了托底 → 托底；选了管家 → 管家；否则自己的默认 AI（**必须是启用的**；订阅类如果建过 AI token 但全部吊销了，也算不能用）；部署者 → 管家；托底；都没有 → 「未设置」。所以停用 / 删除自己的 AI、吊销全部 AI token 后，Jarvis 立刻回到托底。「用这个」/ 添加时勾「Jarvis 用它」是明确的选择，会覆盖之前选的托底。
- 托底的同意照旧：第一次切到托底时弹同意层，同意过的不再弹（部署者不用单独同意）。
- **部署者用托底也按每月 ¥3 计**（和别人一样，钱也是他自己的；要多用 `body-engine ai-fallback quota --user-id 1 --cny 10`）。部署者没选过时仍然默认走身体管家（行为不变），可以选托底，再选回管家。
- `/v1/web/me` 的 `assistant_url` 只在当前用管家时才有（选了别的就不会再被跳到管家）；`ai.choice` 是当前选中项（`fallback` / `butler` / `p:<id>`）。订阅类的「在官方 app 里问」弹层加了「换 AI」按钮。
- 测试（`tests/test_ai_fallback.py` 新增 3 个）：停用 → 托底、启用 → 回来、吊销全部 token → 托底、删除 → 托底；有自己的 API 时选托底、再选回自己的、勾「Jarvis 用它」覆盖托底；不能选别人的 / 停用的 / 管家（非部署者）；部署者在管家和托底之间切、托底没开通时往下落。浏览器里实测：Codex 用户「换 AI」→ 选托底 → 同意 → 不刷新直接进页内对话；面板里切回 Codex 面板自动关闭；停用 Codex 后 `/v1/web/me` 立即是托底。

### 9.13 外置 AI 的 token 瘦身（2026-10-18，`assistant/briefing.py`、`assistant/compact.py`、`toolspec.py`）
> 用户原话的意思：每次点开绑定自己订阅的外置 AI 助手，是不是都要把完整上下文全部重新读一遍？这对 token 很浪费，要优化。

**实际情况**：MCP 客户端（Claude Code / Claude app / Codex…）每次调模型都会带上「系统设定 + 全部工具定义 + 对话历史（含工具返回）」；有提示缓存时重复部分按缓存价算，但仍占上下文、新对话第一轮是全价。所以能省的是三块：①工具定义（固定开销，每次都带）；②开局要读的东西；③每次工具返回的大小。

**量化方法**：Quick Start 合成数据（60 天，`memory-init --from-md`、`recipes-init`、`import-records` 补上真实规模的档案卡 / 食谱 / 化验 / 动作限制），通过远程 MCP `/mcp` 实际调用，按模型实际收到的文本用 tiktoken `cl100k_base` 计数（Claude 的分词器不同，中文偏差约 ±20%，只看相对变化）。

| 项 | 之前 | 之后 |
|---|---|---|
| 工具定义（name + description + input_schema，MCP 宽松版） | 64 个，21.0k token（47.6k 字） | 65 个，14.4k（35.0k 字），−31% |
| 同上，今天页内对话的严格版 / 托底 AI 子集 | 21.0k / 9.4k | 15.6k / 7.1k |
| server instructions | 363 | 249 |
| 开局（以前：`get_memory` + `get_profile`；现在：`get_briefing`） | 4.4k | 1.6k |
| 「我今天恢复怎么样，该练什么」的工具返回（以前：上面两个 + `get_scores` 7 天、`get_reminders`、`get_exercise_rules`、`get_training_calendar`、`get_strain_coach`） | 19.1k | 3.2k（`get_briefing` + `get_exercise_rules` + `get_strain_coach`）；照旧全调也只有 6.3k |
| 这个问答 3 次模型调用的输入合计（外置客户端 / 管家含 CLAUDE.md） | 87.7k / 103.4k | 48.8k（−44%）/ 64.1k（−38%） |

单个工具的默认返回（之前 → 之后）：`analyze_journal` 13.3k → 0.9k、`get_cardio` 7.0k → 0.2k、`get_daily_metrics`（7 天全部）5.7k → 1.0k、`get_training_calendar` 5.4k → 0.9k、`list_recipes` 5.6k → 2.3k、`get_memory` 4.3k → 2.5k、`get_exercise_rules` 4.0k → 1.5k、`get_reminders` 3.6k → 0.8k、`get_training_log`（14 天）3.5k → 0.6k、`get_scores`（7 天）1.6k → 0.5k。

**做了什么**：
- **`get_briefing`**（只读，scope profile，其他各块按各自 scope 授权，没授权的列在 `hidden`）：一次返回现在时间、资料、最新恢复 / 睡眠 / HRV / 静息心率、最近 7 天评分表（含入睡–起床）、7 天 vs 60 天均值、28 天训练概况 + 最近 7 天训练、今天的提醒（标题）、状态卡（去掉和评分重复的 4 项）、档案卡要点（健康约束、伤病、当前计划、目标各取开头，其余列键名）、最近 5 条观察笔记、禁用 / 注意动作名单。约 1.5k token。**缓存**：只缓存本人的，进程内按用户；本地日期 + 数据指纹（users、daily_scores、sleep_sessions、workouts、reminders、档案卡、观察笔记、状态卡、exercise_meta 的行数和最新时间）不变且不到 10 分钟就直接用；本人的任何写工具立即清掉；看家人的不缓存。指纹管不到的 SparkyFitness（力量记录、体重）靠 10 分钟过期。
- **工具返回压缩**（`compact.py`，MCP 和页内对话都用；`run_tool` 本身和 HTTP API 不变）：去掉 null / 空字段（提示词里写明「省略 = 没数据」）、3 行以上的对象列表变成 `{"cols", "rows"}` 表格、小数取 2 位、时间到分钟、JSON 不带空格。
- **默认摘要、`detail=true` 才给明细**：`get_memory`（去掉趋势数组、时间戳、依据）、`get_reminders`（去掉 evidence，规则运行结果一条一行）、`get_exercise_rules`（正常动作只给名字）、`get_training_calendar`（每天一行；**不填参数 = 最近 28 天**，以前实际返回的是本月含未来日子）、`analyze_journal`（只给有结果的项）、`get_cardio`（汇总 + 最近 10 次有效 HRR）、`get_training_log`（每个动作一行「8×60kg RIR2」）、`get_daily_metrics`（按指标转置）；`get_scores` 不带 details 时去掉 `hrv_kind / hrv_night_ln / hr_max / algo_version`；`list_recipes` 每道菜一行扁平字段。
- **工具定义瘦身**：description 只留功能和关键规则（「写之前告诉用户并确认」之类的重复话移到 server instructions 一处），参数说明删掉字段名已表达的；公共片段在 `toolspec.py`。MCP 的 `tools/list` 用宽松版（`toolspec.relaxed`：可空字段、`user`、`detail` 不再必填，去掉 `additionalProperties`），模型不用再给每个没用的字段写 `null`（输出 token 也省）；`run_tool` 先用 `fill_defaults` 补齐，handler 看到的仍是严格形状。页内对话（Claude API strict tools）继续用严格版。没有合并 / 删除任何工具，旧的调用方式照样能用。
- **提示词**：`ai/system_prompt.md`、`assistant/CLAUDE.md`（两份同步）、`system_prompt_lite.md`、MCP instructions：开局先 `get_briefing`，不再先调 `get_memory` + `get_profile`；按问题深入、日期范围按需、大结果默认摘要；去掉重复的工具清单、压缩状态卡和长期记忆两节。托底 AI 的工具子集加了 `get_briefing`。
- **没改**：授权 / 隔离逻辑、`chat.py` 的 ALWAYS_CONFIRM、管家 `settings.json` 的 allow / ask（新工具只读，`mcp__body-engine__*` 自动覆盖）。

**以后可以考虑**：管家 `settings.json` 里 `ENABLE_TOOL_SEARCH=false`（工具定义全部预加载）；打开工具搜索后固定开销还能再降一大截，但每个工具第一次用要多一次搜索往返，要实测再定。今天页内对话的系统提示仍附完整长期记忆（≤ 12000 字），和 `get_briefing` 的记忆要点有少量重复。

**验证**：`tests/test_briefing.py`（压缩、宽松 schema 和补齐、工具定义预算、开局摘要只含自己的数据、家人按 shares 只看授权的块、缓存命中 / 写工具清缓存 / 外部改数据指纹变化 / 过期 / 别人写不影响、默认摘要 vs detail）、`tests/test_ai.py` 新增远程 MCP 的宽松 schema + `get_briefing` 用例。

#### 9.14 Google AI：订阅、Gemini API 免费额度（P8，迁移 `225_gemini_plan.sql`；服务端 Gemini CLI 和管家 Gemini 版本已下线）

| 部分 | 做法 |
|---|---|
| 订阅 `gemini_plan_mcp` | 和 Claude / ChatGPT 订阅一样：用户在自己登录的 Gemini CLI / Antigravity 里连远程 MCP `/mcp`，后端不碰 Google 账号。连接说明：`gemini mcp add --scope user --transport http -H 'Authorization: Bearer ${BODY_ENGINE_MCP_TOKEN}' body-engine <地址>/mcp`（单引号：配置里存 `${…}`，运行时才展开），或 `~/.gemini/settings.json` 的 `mcpServers` 写 `{"type": "http", "url": …, "headers": …}`（Gemini CLI 0.39 的 `mcp add` 就是这样写的）。「连接客户端」按提供商种类预选客户端。`generic_mcp_client` 改名「其他 MCP 客户端」。 |
| Gemini API 免费额度 | AI 设置里勾「免费额度（按 $0 计）」= 单价 0：用量照记，`check_budget` 不再因为预算 0 拦截。**免费额度不是无限的**：Google 按模型限速 / 限每日次数（数字常变，以 AI Studio 的 rate limits 页为准），超了返回 429（适配器报「被限流」）；**免费层的内容 Google 可能用来改进产品**（Gemini API 条款的 Unpaid Services），手册里写明了。取消勾选只清掉这个勾设的 $0 单价。 |
| Gemini 用量 | `usage_metadata.cached_content_token_count` 拆成 `cache_read_tokens`（`prompt_token_count` 本身含缓存部分，所以从 input 里减掉），缓存按输入价的 0.25 计（Gemini 2.5 的缓存价；新模型更便宜，偏高估）。 |
| 服务端 Gemini CLI `gemini_cli` | **已下线（2026-10，迁移 `460_retire_gemini_cli.sql`）**。原因：Google 从 2026-06-18 起不再让 AI Pro / Ultra 订阅登录 Gemini CLI，而这个功能故意只走订阅登录，所以早就不能用了。适配器、目录项、`BODY_GEMINI_CLI_ENABLED` / `BODY_GEMINI_HOME` 都已删除；旧的 `gemini_cli` 行被停用、取消默认，「AI 设置」里显示「已下线」、只能删除，Jarvis 按 `choice.effective` 的规则回落（部署者到管家链接，其他人到托底 AI）。kind 仍留在 CHECK 约束里，旧行才合法；目录里没有这一项，所以建不了新的。 |
| 管家 Gemini 版本 | **已下线（2026-10）**，原因同上：`assistant/.gemini/`、`assistant/GEMINI.md`、`butler.py gemini-check` 已删除。管家只保留 Claude 版本；Google 订阅用户用 Antigravity 连 `/mcp`（`gemini_plan_mcp`）。 |

**验证**：`tests/test_ai.py`：订阅重定向、免费额度不被预算拦、Gemini 缓存用量拆分；`gemini_cli` 下线后：目录里没有、建不了、旧行显示「已下线」、不能修改 / 启用 / 被 Jarvis 选用、可以删除。


#### 9.15 Google AI Pro / Ultra 订阅怎么接（2026-10-21 按 Google 官方文档核实）
- **订阅**：Google AI Pro / AI Ultra（原 Google One AI Premium / Gemini Advanced），Google One 普通存储套餐不算。
- **Gemini CLI**：2026-06-18 起不再给 AI Pro / Ultra 和免费个人账号提供服务，只剩企业版 Gemini Code Assist 和付费 Gemini API key（[Google Developers Blog](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)）。所以「连接客户端」里的 Gemini CLI 只对付费 API key 有用；服务端 `gemini_cli`（故意只走订阅登录）和管家的 Gemini 版本因此已下线（9.14）。
- **Antigravity**（桌面版 / IDE / CLI `agy`）是订阅用户现在的客户端。有免费个人档（按周算额度），Pro / Ultra 额度更高。远程 MCP 写在 `~/.gemini/config/mcp_config.json`（或项目里的 `.agents/mcp_config.json`），格式是 `serverUrl` + `headers`（不认 `url` / `httpUrl`），也支持 OAuth / DCR（[Antigravity MCP 文档](https://antigravity.google/docs/mcp/)）。配置里的 `${VAR}` 据第三方说不会展开，所以我们给的命令让 shell 把 token 填进去。还没关的 bug：[antigravity-cli#71](https://github.com/google-antigravity/antigravity-cli/issues/71)（工具能列出来但调不动，维护者建议升级到 v1.2.9 并用 `~/.gemini/config`）。**`/mcp` + Bearer 现在就能接。**
- **Gemini 网页 / 手机 app 的自定义应用**（Connected Apps → Custom apps，只能在 gemini.google.com 添加）：18 岁以上、**人在美国**、只支持英文、个人账号、要开 Keep Activity（[Gemini Apps Help](https://support.google.com/gemini/answer/17209137)）。鉴权：服务器支持 OAuth 动态客户端注册就不用填；不支持就在 Advanced features 里填凭据，没有静态 Bearer 头的选项。`/mcp` 现在接不上。
- **地区**：澳洲能用 Antigravity 和 AI Pro，Gemini app 的自定义应用不行；中国大陆 Gemini 只有 Workspace（[Gemini 网页版可用地区](https://support.google.com/gemini/answer/13575153)），AI Pro / Antigravity 不开放，网络也连不上，中国用户还是用托底 AI 或 DeepSeek 等 API。
- **没做（要做的话另开 PR）**：
  1. ~~`/mcp` 加 MCP 标准的 OAuth 2.1~~（已做，见 9.16）：protected resource / authorization server metadata、DCR `/register`、`/authorize`（复用今天页登录 + 同意页）、`/token`（PKCE、refresh token，发的仍是 mcp scope 的 AI token，可吊销）、401 + `WWW-Authenticate`；现有 Bearer token 照常可用。约 2–3 天。Gemini app 只对美国用户有意义，但 Claude 网页 / 手机连接器、ChatGPT 连接器也走 OAuth。
  2. ~~服务端 `gemini_cli` 和管家 Gemini 版本：改用 Antigravity CLI 或下线~~：已下线（迁移 460）。

#### 9.16 /mcp 的 OAuth 2.1（2026-10-21，`ai/oauth.py`、`ai/oauth_http.py`、`ai/oauth_api.py`、迁移 `440_mcp_oauth.sql`）
- **为什么**：Claude 网页 / 手机 / 桌面的自定义连接器、ChatGPT 连接器、Gemini app 只走 OAuth（Claude 的静态请求头是 beta、只给组织 Owner；ChatGPT 明确不支持静态 token）。按 MCP 授权规范做，三家都能接。
- **用的库**：官方 `mcp` Python SDK 的 `mcp.server.auth`（MIT，已经是依赖）：`/oauth/authorize`、`/oauth/token`、`/oauth/register`、`/oauth/revoke` 和 PKCE 校验来自 SDK；存储和策略是 `BodyOAuthProvider`。
- **发现**：`/mcp` 没带有效 token 时回 401 + `WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource/mcp"`；RFC 9728 元数据（resource = `<base>/mcp`，授权服务器 = `<base>/oauth`）和 RFC 8414 元数据（`/.well-known/oauth-authorization-server/oauth`，根路径和 `/oauth/.well-known/…` 也给）。
- **开关**：只有设了 `BODY_PUBLIC_URL` 才开 OAuth（issuer / resource 只从它来，不用 Host 头推导）；没设时元数据和 `/oauth/*` 都是 404，`/mcp` 的 401 不带 `resource_metadata`，静态 token 照常。
- **注册**：DCR，**全部是公开客户端**（`token_endpoint_auth_method` 强制 `none`，没有 client secret）。回调地址白名单：带主机名的 https、本机回环 http（任意端口匹配）、带点号的反向域名私有协议（RFC 8252 §7.1，例 `com.example.app:/cb`）。同一 IP 每小时 20 个；全服务器每天最多 500 个**还没被同意过的**客户端；没被同意过的客户端 1 小时后由 `oauth-cleanup` 删掉，同意了但从没换过 token 的连接也是 1 小时后删掉。参数在 `ai/oauth_params.py`。
- **授权**：`/oauth/authorize` 按 IP 限流（10 分钟 30 次）；请求有误时**不跳回客户端**（防开放重定向，RFC 9700 §4.11.2），只回我们自己的 400 错误页，唯一允许的跳转是到同意页；只认 S256；`resource` 不是本服务器的 `/mcp` 就拒绝（RFC 8707）；`/oauth/authorize` 把请求存 10 分钟，浏览器去 `/app/oauth.html?req=…`。同意页先加载、再从本站读登录状态（今天页 cookie 是 SameSite=Strict，从 AI 应用跳过来的那次请求不带 cookie），显示应用名、跳转地址（https 只显示域名，其他协议连协议一起显示）、账号和权限，可以选只读；跳转域名不是已知 AI 厂商（`KNOWN_REDIRECT_HOSTS`）时有醒目提醒，本机地址另有提醒；只读登录不能同意，用 cookie 同意必须带 `X-Body-Web: 1`。
- **令牌**：授权码一次性、5 分钟；access token = `api_tokens` 里 scope 含 mcp 的一行（和静态 AI token 同一种，`/mcp`、审计、只读处理都不变），1 小时过期，绑在连接（grant）上；refresh token 90 天、每次用都轮换，旧的被再次使用（或授权码被重复使用）就吊销整个连接（没有宽限期，符合规范；上线后看 `oauth_grants.revoke_reason` 的统计，客户端网络重试误伤多了再考虑短宽限期）；`revoke_tokens(everything=True)` 也会吊销用户全部连接；`/oauth/revoke` 也是吊销整个连接。所有令牌、授权码、请求号只存 sha256。
- **用户看到的**：「我 → AI 设置 → 连接客户端（MCP）」列出已连接的应用（应用名、跳转域名、最近使用），可以断开（`GET/DELETE /v1/users/me/ai-connections`）；静态 AI token 照常可用，列表和上限只算静态的。每人最多 20 个连接。
- **运维**：`body-engine oauth-cleanup`（每小时，幂等）、`oauth-connections --user-id N`、`oauth-revoke --user-id N --connection-id M`。公网 nginx 要放行 `/oauth/`（authorize、token、register、revoke）、`/.well-known/oauth-protected-resource`*、`/.well-known/oauth-authorization-server`*、`/app/oauth.html`、`/app/oauth.js`，见 PR 说明。

### 9.17 工具分组、按需加载（2026-10-21，#99；`ai/params.py`、`assistant/toolsets.py`、迁移 `490_ai_conversation_page.sql`）
> 106 个工具的定义一共 58,539 字符，每次调模型（MCP `tools/list`、页内对话）都整份发过去，预算测试的上限已经到 59k，再加工具就要再提。

**做法**：每个工具有一个领域组（`*/tools.py` 里每个工具的 `group=`）；每次只发「常驻核心组 + 当前页面对应的组」，其余的通过三个元工具拿到。不依赖 `tools/list_changed`（很多 MCP 客户端不支持）。
- **组**（`ai/params.GROUPS`）：`body`（评分、睡眠、指标、分析、提醒、体重）、`training`、`nutrition`、`recipes`、`grocery`、`records`（补剂、化验、日记、身体事件）、`memory`（档案卡、观察笔记、状态卡、写入记录和回滚）、`feedback`。
- **常驻核心组**（`ai/params.CORE_TOOLS`，32 个，17.1k 字符，上限 30k）：`get_briefing`、各场景的 context 工具（`get_shopping_context` / `record_store_prices`）、常用读（评分、睡眠、每日指标、训练日历 / 记录、周计划、饮食、体重、日记、提醒、禁用动作）和常用记录（`log_food`、`log_workout`、`log_weight`、`log_journal`、`add_event`、`log_meal_template`、`update_food_entry`、`add_observation`、`update_profile_card`、`report_feedback`）及其撤销（`undo_sparky_entry`、`get_write_log` / `rollback_write`）。
- **三个元工具**（`assistant/toolsets.py`）：
  - `find_tools(group | query)`：返回这些工具的完整参数说明（宽松 schema）和该用哪个元工具调用；都不填 = 列出所有组和工具名；query 按空格分词匹配名字和说明，最多 12 个；
  - `run_read_tool(name, args)`：read_only_hint = true；
  - `run_write_tool(name, args)`：read_only_hint = false，MCP 客户端写之前照样弹审批。
- **护栏不变**：元工具先被解析成里面那个真实的工具（`toolsets.resolve`），再按直接调用的路径走 `run_tool`：
  - 审计（`assistant_audit.tool`）和页内对话的 `ai_tool_calls.tool` 记的是真实的工具名；`find_tools` 不读用户数据，不写审计；
  - `run_read_tool` 调写工具、`run_write_tool` 调读工具都会被拒绝；只读 token / 只读登录不能用 `run_write_tool`；
  - `ALWAYS_CONFIRM` 按真实的工具名判断：页内对话里 `run_write_tool(delete_observation…)` 照样先弹确认；
  - MCP：删除 / 撤销类（`ALWAYS_CONFIRM`）一直在 `tools/list` 里，`run_write_tool` 不接它们（返回「要直接调用」），这样客户端按工具名的审批规则（管家 `settings.json` 的 `ask`）照样生效；
  - 托底 AI：`find_tools` 只列它白名单里的工具（用它自己的 schema，没有 `user`），`run_read_tool` / `run_write_tool` 也只能调白名单里的（心电图照样调不到）。
- **MCP**：`tools/list` = 核心组 + `ALWAYS_CONFIRM` 的工具 + 元工具；只读 token 看不到写工具和 `run_write_tool`。没列出来的工具按名字直接调用也照样能用（旧客户端、管家的写法不变）。`INSTRUCTIONS`、`system_prompt.md`（MCP prompt「butler」用同一份）、`assistant/CLAUDE.md`、`system_prompt_lite.md` 各加了一句怎么用。
- **页内对话**：`POST /v1/assistant/chat` 加可以为空的 `page`（今天页的 tab id `today` / `trends` / `food` / `record` / `me`，子页面写成 `<tab>.<sub>`，例 `food.shopping`、`record.training`；没有对应的子页面就退回 tab；不认识的 = 只发核心组）。发「核心组 + 这个页面的组 + 元工具」，顺序跟 `TOOLS` 一致、元工具在最后。对话记下去过的页面（`ai_conversations.pages`，最近的在最后，只存认识的页面 id）：第一次带一个新页面发消息时，把那个页面的组追加进这个对话的工具集，仍按 `TOOLS` 的顺序排，原来发过的工具相对顺序不变；**有上限**：加上后超过 `PAGE_BUDGET_CHARS`（36k；托底 AI 用 `FALLBACK_PAGE_BUDGET_CHARS` 25k）就从最早的页面开始去掉它们的组，核心组和当前页面的组一直保留，去掉的照样能用 `find_tools` 拿到（`toolsets.visit_page`）。工具在提示缓存前缀的最前面，所以只有加上或去掉组的那一轮缓存失效一次，之后又稳定；回到还在的页面、不带 `page`、不认识的页面 id 都不变（2026-10-21 负责人决定，审阅后加了上限）。实际效果：大页面（`today`、`food.*`、`record`）各自已经接近上限，换过去基本是替换；小页面之间（例 `record.journal` → `me`、`today` → `trends`）会累积。没有 `page` 的旧对话 / 旧客户端只发核心组。托底 AI 在它的白名单范围内同样处理，额度预估按实际发的工具算。

| | 改前 | 改后 |
|---|---|---|
| MCP `tools/list`（可写 token） | 106 个，58,539 字符 | 44 个，20,088 字符（−66%） |
| MCP `tools/list`（只读 token） | 46 个只读工具 | 21 个，8,222 字符 |
| 页内对话，没有 page / 「今天」/「采购」 | 106 个，58,539 字符 | 35 个 18.1k / 65 个 31.4k / 58 个 32.2k |
| 托底 AI（没有 page） | 67 个，29.6k | 28 个，13.3k |

**预算测试**（`tests/test_tool_groups.py`，上限都在 `ai/params.py`）：核心组 ≤ 30k；每个组各有上限（`GROUP_BUDGET_CHARS`），以后加工具只占它自己那个组；元工具 ≤ 2.5k；MCP `tools/list` ≤ 35k；每个页面的组合 ≤ 36k。另有：每个读工具在合成数据（一年的评分、120 天睡眠和指标、160 次训练、日记、化验、身体事件、观察笔记）上的摘要档不超过 `READ_RESULT_MAX_CHARS`（8k，`propose_plan` 14k、`search_exercises` 12k）；工具顺序稳定（同一个页面每次一样、跟 `TOOLS` 顺序、元工具在最后）；元工具的护栏（只读、`ALWAYS_CONFIRM`、托底白名单、读写不混用、审计记真实工具名，`tests/test_ai.py`、`tests/test_ai_fallback.py` 端到端）。

**以后可以考虑**：按实际用量调核心组（`assistant_audit` 里统计哪些工具常被 `run_*_tool` 调，就挪进核心组或对应页面的组）；`find_tools` 的关键词匹配目前是简单的子串匹配，中文同义词（「称重」/「体重」）要靠按组浏览。

### 9.18 场景脚手架：context 工具 + 批量写回 + 操作说明（2026-10-21，#100；`assistant/scenes.py`、`assistant/scene_tools.py`、`ai/playbooks/`）
**统一约定**（照 `get_shopping_context` / `record_store_prices` 的样子，以后加场景都按这个来）：
- **context 工具**：只读，一次返回完成这件事需要的全部信息，另带
  - `rules`：护栏，AI 必须照做（写在返回里，不靠 AI 记得提示词）；
  - `needs`：还缺什么（数据没有、服务没连上、要先问用户的事），AI 先处理这些再动手。
  - 按 section 鉴权（和 `get_briefing` 一样）：看别人的数据时只给对方 shares 允许的部分，其余列在 `hidden`；某一块出错（例如 SparkyFitness 没连上）只影响这一块，错误写在原处并进 `needs`。
  - 用 `compact` 输出，摘要档受 9.17 的返回字数上限测试约束。
- **批量写回**：一次写多条，幂等（同一条再写不会重复），返回里每一条单独成功 / 失败和原因，失败的改了单独重写，不用整批重来。
- **操作说明（playbook）**：`ai/playbooks/<name>.md`，给 AI 看的中文。每份写清楚：调哪些工具、按什么顺序、什么时候先问用户、护栏（只处理用户自己的事；不绕过任何网站的限制；联网只查清单上的东西，或者这个场景不联网）。两种拿法是同一份文本：MCP prompt `<name>`（和 `butler` 并列），以及工具 `get_playbook(name)`（很多客户端不显示 prompt；页内对话、托底 AI 也用它）。

**这次加的**：
- `get_training_context(user, date)`：本周计划（每天练什么、组数和推荐重量、各肌群周组数、进阶提醒、能练的星期几、计划用到的器械）、`readiness`（恢复分档位、减量系数、CTL / ATL / TSB、今天的负荷目标）、禁用 / 注意动作、最近 14 天的训练和负荷、档案卡里的器械（key `equipment`）；`needs` 例：还没有周计划、不知道哪几天能练 / 有哪些器械、最近没有恢复分、训练记录没连上。
- `get_week_review_context(user, end)`：最近 7 天（到 `end`，默认今天；和今天页的周报同一套数字，`insights/reports.py`）的恢复、睡眠、负荷、压力、训练、饮食达标率、体重，每块带 `vs_previous`（和上一周的差值）；这一周的日记（每项记了几天、几天是「是」、备注）和 90 天里影响次日恢复的习惯；身体事件。`needs` 例：恢复分或饮食记录少于 4 天、SparkyFitness 没连上、没称重。
- 两个都只读，放进常驻核心组（核心组加上它们和 `get_playbook` 后 35 个、18.1k 字符，上限 30k；MCP `tools/list` 21.1k）；鉴权在工具上只要 `profile`（和 `get_briefing` 一样），其余按 section 查。
- playbook 第一批 4 份：`plan_shopping`（规划采购）、`plan_training_week`（排一周训练）、`weekly_review`（复盘一周）、`log_meal_from_photo`（照片记餐）。
- 托底 AI 的白名单加上 `get_playbook`、`get_training_context`、`get_week_review_context`（都不碰心电图）。
- 每份 playbook 的说明下面统一加一行「提到的工具不在你的工具列表里时，用 find_tools 找，再用 run_read_tool / run_write_tool 调。」（`playbooks.FIND_LINE`，在 `text()` 里加，MCP prompt 和 `get_playbook` 仍是同一份）：通过 MCP 接上时，playbook 里提到的很多工具不在 `tools/list` 里。
- **托底 AI 能做哪些 playbook**（`playbooks.FALLBACK`，负责人决定：白名单不为它扩大）：只有里面提到的工具全在托底白名单里的才标 yes（`playbooks.OPTIONAL_TOOLS` 里的可选工具不算，playbook 写明了没有它时怎么做），现在是 `plan_training_week` 和 `weekly_review`（没有 `set_nutrition_goals` 时只给建议数值，请用户用自己的 AI 改目标）；`plan_shopping`、`log_meal_from_photo` 差好几个采购 / 记餐工具，标 no。`system_prompt_lite.md` 只列 yes 的；托底调 `get_playbook` 拿 no 的，返回一句「这件事需要用户自己的 AI」。
- `system_prompt.md`、`assistant/CLAUDE.md`、`system_prompt_lite.md`、MCP `INSTRUCTIONS` 各加一行，说明有哪些 playbook。

**测试**：`tests/test_scenes.py`（两个 context 工具有数据 / 没数据、另一个结束日期、没有 share 读不到、只共享 profile + scores 时其余部分在 `hidden` 里）、`tests/test_playbooks.py`（文件和名单一致、提到的工具都存在、每份有步骤 / 先问用户 / 护栏三节、`get_playbook` 和 prompt 同一份文本、托底白名单不含心电图）、`tests/test_ai.py::test_mcp_playbook_prompts`（`prompts/list`、`prompts/get`、`get_playbook` 走真实的 `/mcp`）。

### 9.19 回答里的互动卡片（2026-10-22，#53；`ai/cards.py`、迁移 `595_ai_tool_call_cards.sql`，灰度开关 `jarvis_rich`）
- **模型不写 HTML，也不给数据**：卡片是服务端从用户自己的数据生成的 JSON 块（`{"v": 1, "type", ..., "actions"}`），页面按 type 渲染（`ai.js` 的 `showCard`），所有文字都经过转义。
- **两种来源**：
  - 模型调 `show_card(type, date, metric, days, buttons)`，只说要显示什么。
  - 写工具成功后自动附卡（`cards.AUTO`）：`log_food` / `update_food_entry` / `log_meal_template` / `log_recipe` → 当天饮食卡；`log_workout` → 当天训练卡；`log_weight` → 体重 30 天小图。同一轮里同一天的卡只出一张，等这一轮的写入都跑完再生成，所以合计是最新的。
- **卡片类型**：
  - `training`：当天计划、准备度和动作卡头部，复用训练页（FB-5，`training-day.js` 的 `summaryHtml`）的同一套样式和数据（`training/session_log.view`）。
  - `meal`：热量 / 蛋白和目标进度、碳水脂肪、明细。
  - `metric`：恢复 / 负荷 / 睡眠 / HRV / 静息心率 / 体重的小图，复用今天页的 `tChart`（`window.BodyCharts`）。
  - `sleep`（2026-10-22，睡眠详情页 PR）：某晚（`date` = 醒来那天，null = 最近一晚）的入睡 → 醒来、睡着时长、阶段时间轴（`BodyCharts.hypno`）、睡眠分、需求和规则要点的短标签；数据来自睡眠详情页同一个 `sleep_night/view.build`，按钮「睡眠详情」打开那晚的详情页（`sleep_detail` 关着时打开趋势 → 睡眠）。今天页详情页底部「让 Jarvis 详细分析这晚的睡眠」只预填问题，用户自己发出去才调用 AI。
  - `actions`：2–4 个快捷按钮。
  - 确认卡（删除 / 撤销）照旧。
- **点击怎么回传**：
  - `open`：打开今天页自己的页面（`record.training` / `food` / `trends` / `today`），记录走那边现有的接口。
  - `send`：作为一条用户消息发回 AI。固定按钮的文字由页面翻译（`key`），`actions` 卡的按钮是模型写的 label / text。
  - 卡片里不会出现模型给的 URL。
- **各家 AI**：卡片就是一个普通的工具（四家 adapter 都支持工具调用），托底 AI 也能用。自动卡片不依赖模型，弱模型也有卡片。开关打开时，系统提示末尾会加一段说明（`cards.PROMPT`），告诉模型什么时候用卡片、不要复述卡片里的数字。
- **远程 MCP 不受影响**：`show_card` 不在 `assistant/tools.TOOLS` 里，只在页内对话、开关打开时追加到工具列表；MCP 的 tools/list 和工具预算不变。Claude / ChatGPT 连接器照常拿文字和工具返回。
- **图片**：照片 / 截图理解照旧（识别后 `log_food` 等），写入成功自动附卡，用户直接在卡上看到结果、点「打开饮食」核对。
- **存储**：卡片存在 `ai_tool_calls.card`，`GET /v1/assistant/conversations/{id}` 的每条消息带 `cards`，断线重连（resync）时重画。卡片是显示时的快照，点进去的页面是实时的。
- **模型拿到什么**：工具结果只给一句摘要（「已显示 10-22 饮食卡：1850 kcal / 目标 2000…」），不重复卡片数据，省 token。
- **第二版：所有模块（2026-10-23，T0；`ai/cards_food.py`，灰度开关 `jarvis_cards_v2`，要 `jarvis_rich` 也开着）**。开关关着时工具、提示、每张卡都和上面一样；开着时 `show_card` 换成 `cards.TOOL_V2`（更多 type，加 `slot` / `id` 参数；各 type 的用法写在提示 `cards.PROMPT_V2` 里，工具描述只有一句，工具预算不变）。
  - 饮食（第一个 PR）：`goals`（某天热量环 + 蛋白环，碳水 / 脂肪 / 饱和脂肪上限 / 添加糖上限 / 纤维进度条，数据来自 `nutrition/daily`）、`suggest`（`suggest_meals` 的推荐，`slot=all` 是全天每餐计划；每道菜是饮食页自己的 `recipeCard`，下面「记下这餐」）、`recipe`（一道菜：每份宏量、每份食材克数、步数、约束标签；只显示同源图片 `/…`，第三方图片地址不进卡片；「打开食谱」打开饮食页上这道菜的弹层，在那里改份数 / 餐次再记）、`recipes`（食谱列表，按 slot）、`templates`（快捷记餐模板，「记一次」）、`shopping`（本周采购清单：未买在前、已买划掉、进度、估价、警告）。
  - `meal` 卡在 v2 里带 `entry_actions`：点一条出现「×0.5 / ×1.5 / ×2 / 删除」；写工具后的自动卡多一个「撤销」。
  - 行内按钮也是 `send`：页面用这一行自己的字段（服务器给的名字和 id）拼一句话发回 AI，例如「按快捷模板「固定早餐」记一次（模板 id 3）」「删掉 2026-10-22 记的「鸡胸肉饭」（id e1）」；删除 / 撤销照旧走确认卡（`ALWAYS_CONFIRM`）。模型给不了 URL、HTML 或数字。
  - 自动卡：写工具多了 `cards.AUTO_V2`（`set_nutrition_goals` → goals，`create/update_meal_template` → templates，`update_recipe` → recipe，`add_shopping_items` / `generate_shopping_list` / `mark_bought` → shopping，`undo_sparky_entry`（饮食）/ `undo_meal_log` / `combine_food_entries` → meal）；**读工具**的 `cards.READ_AUTO`（`get_nutrition`（单日）、`get_nutrition_goals`、`suggest_meals`、`get_recipe`、`list_recipes`、`get_meal_templates`、`get_shopping_list`）**只给托底 AI**：弱模型很少自己调 show_card，查了什么就自动出那张卡；强模型常为了分析查好几天，不自动出卡，靠提示自己决定。一轮对话里同一张卡（type + date + id + slot …）只出一次，模型自己发过的也不再自动出。
  - 图标：只用 Lucide（`utensils` 是没有图片的食谱占位、`check` 是已买），内联路径，记在 `THIRD_PARTY_NOTICES.md`。

### 9.20 页内对话联网：服务器的 web_search / web_fetch（2026-10-22，#150；`ai/web/`、迁移 `600_ai_web.sql`，灰度开关 `ai_web`）
**问题**：采购（查价 → `save_product` / `record_store_prices` → 清单）靠 AI 自己联网。用 Claude / ChatGPT 连接器（MCP）和 Anthropic / OpenAI / Gemini 自己 key 的人有联网能力；托底 AI（管理员的 DeepSeek）和 `openai_compatible` 没有，于是永远用不了采购清单。

**谁用什么**
| 今天页的 AI | 联网方式 | 费用 |
|---|---|---|
| Anthropic API key | 官方 `web_search` + `web_fetch`（4.6 以后的模型 `_20260209` 带动态过滤；其余 `web_search_20250305` + `web_fetch_20250910`；`max_content_tokens` 20k） | 用户自己的 key（搜索 $10 / 1000 次 + token） |
| OpenAI API key | Responses API `{"type": "web_search"}` | 用户自己的 key |
| Gemini API key | `google_search` + `url_context`（Gemini 3：每月前 5000 次免费，之后 $14 / 1000 次） | 用户自己的 key |
| 以上三家 | 都要用户在「AI 设置」打开「允许联网」（`ai_providers.web_tools`，默认关，#83）；不受 `ai_web` 影响 | |
| `openai_compatible` | 各家联网方式不统一（Qwen `enable_search`、Kimi `$web_search`、OpenRouter `:online`…），不做专门适配 → 服务器工具 | 管理员（计入托底额度） |
| 托底 AI（DeepSeek） | 服务器工具；同时加上存商品和出清单要用的采购工具（`web.FALLBACK_GROCERY`），`plan_shopping` playbook 对它开放 | 管理员（计入托底额度） |
| MCP 连接器（Claude / ChatGPT / Codex…） | 客户端自己的联网，服务器工具不出现在 `tools/list` | 用户自己的订阅 |

**服务器工具**（只在 `ai_web` 对这个用户打开时出现，跟在元工具后面发送，`group=grocery`）
- `web_search(query)`：调一个可换的搜索 API（`ai/web/search.py`：Brave 默认 / Tavily / Serper，`BODY_WEB_SEARCH_PROVIDER`；key `secrets/web_search_api_key` 或 `BODY_WEB_SEARCH_API_KEY_FILE`）。**没配 key 时不出现，不报错**。按用户时区本地化（`Australia/*` → AU）。返回前 5 条的标题、链接、≤ 300 字摘要。
- `web_fetch(url)`：服务器读一个公开网页（`ai/web/fetch.py`），返回标题、schema.org Product（名称、品牌、规格、条码、价格、币种、库存）、价格 meta 和 ≤ 4000 字正文摘录。

**合规**（`ai/web/fetch.py`，参数 `ai/web/params.py`）
- 只在用户这次的请求需要时由模型调用；没有定时任务、不翻页、不跟链接、不批量。
- 先读 robots.txt（RFC 9309）按 `Watch2WhoopBot` 判断：200 按规则；404 类 = 没规则；401 / 403 / 429 / 5xx / 连不上 = 不读。User-Agent 如实写明是机器人和项目地址，不冒充浏览器。
- 401 / 403 / 407 / 429 / 451 / 503、反爬 / 验证码页（Cloudflare、Imperva、PerimeterX、DataDome… 的特征）、登录墙（密码框 + 很少的正文）→ 停下，返回 `blocked=true` 和 `tell_user`「这家超市的网页读不到，可以把商品页截图或小票发给我」；同一网站 1 小时内不再请求，不重试、不换地址。**绝不绕过反爬或验证码。**
- Coles / Woolworths（`NO_FETCH_HOSTS`，已知有反爬）直接按 blocked 处理，连 robots.txt 也不请求；它们的价格靠搜索结果摘要、用户截图或小票。
- 只读 http / https 标准端口；主机名解析出的地址必须全是公网地址，连接直接连到检查过的地址（防 SSRF / DNS 重绑定），每次跳转（≤ 3 次）重新检查。
- 只回给模型摘录；库里只存模型用 `save_product` / `record_store_prices` 写的商品名、规格、价格、门店、链接、日期，不存整页。（对话记录里会留着模型读到的那段摘录，和其他工具结果一样，删对话就删掉。）

**费用和限制**
- 每次搜索记一行 `ai_usage`（`purpose = web_search`，`cost_usd = 0`，`cost_cny` = 单价 × 7.3），读网页也记一行（`web_fetch`，0 元），都不是模型调用（托底的每分钟 / 每天调用上限不算它们）。
- `fallback.month_spent_cny` = 托底模型调用 + 服务器搜索：每人每月仍是同一个托底额度（默认 ¥3），搜索前先查剩余够不够。`openai_compatible` 用户的搜索也算进这个额度，不动他自己的美元预算。
- 每人每天（本地日）搜索 20 次、读网页 40 次；全部用户每月搜索合计 3000 次。

**测试**：`tests/test_ai_web.py`（robots 各种回答、反爬 / 验证码 / 登录墙 / 403 拒绝且不重试、Coles / WWS 和内网地址不发请求、跳转重新检查、三家搜索 API 的请求格式、开关关着没有工具、开关打开后托底和兼容接口有、Anthropic key 没有、计费进 `ai_usage` 和托底额度、额度不够和每天次数用完时拒绝）；`tests/test_grocery_prices.py`（各家官方联网的请求参数）；`tests/test_playbooks.py`（`FALLBACK_WEB` 正好够用）。
