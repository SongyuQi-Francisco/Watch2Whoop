# 体态照片给 AI 看（#174、#177）设计方案

> 状态：**方案，等负责人决定**（第 9 节）。这次只有文档，不写功能代码、不调用任何付费 API、不用任何真实用户的照片。
> 同意书的任何改动在本文里都只是**草案**（第 5 节），必须由负责人批准后才进 `profile/consent.md` / `docs/family-consent.md`。

## 0. 一句话结论
- **第一期（开关 `posture_photos`）**：用户在 Jarvis 里按引导拍正面 / 侧面 / 背面 3 张，照片**只随这次提问**发给他自己选的 AI；服务器在回答结束后**删掉原图**，只留 AI 的文字结论。AI 结合 `get_muscle_load` 给建议、把结论写进长期记忆，**改训练计划要用户确认**（沿用 `propose_plan` → 确认 → `save_plan`）。
- **第二期（开关 `posture_records`）**：可选的「体态记录」，**单独同意、默认关**，照片加密存服务器、不进任何共享、可对比、可删、到期自动删。这一期要改同意书（草案在 5.2）。
- **先做第零期评估**：用合成人体图（已知角度）测托底 DeepSeek 和 Claude / GPT / Gemini 看体态的能力，结果决定每家能不能用、怎么措辞。评估预算见 3.6，等负责人批准再花钱。
- 现状里有一个和 issue 描述不一致的地方：**今天页内对话（自带 API key 和托底）发的照片现在是存库的**（第 1 节第 2 条），不是「默认不存」。第一期顺带修正，范围由负责人定（决定 D2）。

## 1. 现状（本方案依据的代码事实，2026-10-24 的 main）
1. **照片管道**（设计文档 `ai-assistant-plan.md` 第 9.11 节、#150）：页面 `web/static/ai-media.js` 用 canvas 重新编码成长边 ≤ 1600 px 的 JPEG（重新编码会丢掉 EXIF / GPS），一次最多 8 张（`ai/chat.py MAX_IMAGES`），每张 ≤ 5 MB；提供商不支持图片时 `no_vision` 拒收（`ai/catalog.py` 的 `vision`，托底 `fallback_params.VISION = True`，每张最多计 1024 token）。
2. **今天页内对话的照片现在会存库**：`chat._run` 把用户消息（含 `{"type":"image","data":<base64>}`）原样写进 `ai_messages.content`，直到用户删掉这个对话才消失；`ai/context.py` 在保留的轮次里会把图片重发给模型，折叠进摘要的轮次不重发；`GET` 对话只返回图片张数。托底同意要点里写了「对话记录存在本服务器，你可以随时删除」，主同意书第 1 节写的是「不主动收集：照片」。
3. **订阅类（MCP：Claude / ChatGPT / Antigravity 等）**：照片在用户自己的官方 app 里直接发给那家 AI，**根本不经过我们的服务器**；AI 只通过 MCP 工具读写数据。身体管家（部署者本人）同理，照片进的是管家的 Claude Code 会话（会话记录在服务器上管家用户的目录里，只涉及部署者自己）。
4. **肌肉负荷图第一期**（`docs/muscle-load-plan.md`，开关 `muscle_load`）：`get_muscle_load`（组 `training`、scope `training`，托底也有）返回每块肌群的有效组数、训练量、上周、推荐区间和状态、运动分钟，自己的另带**重点肌群**和**体态 / 形体目标**；后两者在「我 → 训练设置」编辑（表 `training_goals`，迁移 640），体态目标同时写进长期记忆档案卡 `physique_goal`（topic `goals`，source `user`）。肌群 key 是动作库的 17 个粗粒度肌群（`training/catalog_data.MUSCLE_ZH`，例如 `shoulders` 不分前 / 中 / 后束）。
5. **长期记忆**（`assistant/memory.py`）：档案卡（键值）+ 观察笔记（带 topic、来源、日期，用户可删；`memory_hygiene` 开着时会先找相似笔记）。
6. **图片工具**：`images/`（#199 / #200 头像用）已有按文件头校验、按 EXIF 方向摆正、缩放、去掉全部元数据、存表 `images`。
7. **app**：`NSCameraUsageDescription` 已经写了「把照片 / 视频发给你的 AI 时拍照」，第一期不用改。

## 2. 能做什么、不做什么（产品口径）
- **做**：AI 看 3 张站姿照片，给出**观察**（「看起来肩膀偏前」「右肩似乎略高」「侧面看骨盆前倾不明显」）、**和训练的关系**（结合肌肉负荷：推 / 拉组数比、哪些肌群偏少）和**训练建议**（多练什么、注意什么、怎么复拍对比）。
- **不做**：诊断（脊柱侧弯、关节问题等只提示「建议找医生 / 物理治疗师看」）、给分数式的「体态评分」、按照片评价胖瘦或外貌、按照片自动把动作设成「禁用」。
- **为什么要保守**：单张照片看体态本身就有限——拍摄角度、相机高度、镜头畸变、衣服、当时的站姿都会改变结果；临床上即使用照片测量，也要在体表贴标记点、固定拍摄条件才比较可靠（例如 SAPO 这类照片测量软件），不贴标记点的目测一致性更低。体态偏差和疼痛的关系在文献里也不强。所以界面和提示词都写「观察」「可能」，结论要带「照片质量」说明，建议偏向「多练弱的、少练过多的」这种低风险方向。具体文献在实现时由开发会话查证后写进 `params.py`，本文不先引用。

## 3. 第零期：模型评估方案（只写方案，不调用）
### 3.1 评估对象
| 通道 | 模型（`ai/catalog.py` 当前默认 + 一个便宜档） | 说明 |
|---|---|---|
| 托底 | DeepSeek `deepseek-flash` | 大多数测试用户用它；按 ¥3 / 月额度，图片每张最多 1024 token |
| Anthropic API | `claude-opus-5-5`、`claude-sonnet-5-5` | 自带 key 的用户 |
| OpenAI API | `gpt-6.1-sol`、`gpt-5.4-mini` | 同上 |
| Gemini API | `gemini-4-argon`、`gemini-3.8-flash` | 同上 |
| 订阅（MCP） | 不单独测 | 同一家模型，系统设定不同；评估结论按模型家族套用，手册写同样的拍照指南 |

### 3.2 测试图：合成为主，公开授权图只做定性
- **合成人体（定量，有标准答案）**：用免费开源的人体生成器（MakeHuman 导出资产为 CC0）在 Blender 里摆姿势、渲染，每张图的偏差是**已知参数**：
  | 项目 | 参数 | 等级 |
  |---|---|---|
  | 圆肩 / 肩胛前伸 | 肩峰相对耳垂 / 躯干中线前移距离 | 无 / 轻 / 中 / 明显 |
  | 头前伸 | 颅椎角（侧面耳屏–C7 与水平线夹角） | 同上 |
  | 高低肩 | 左右肩峰高度差（cm）和方向 | 0 / 1 / 2 / 3 cm，左高或右高 |
  | 骨盆前倾 | 髂前上棘–髂后上棘连线角度 | 同上 |
  | 干扰项（少量） | 膝超伸、轻度躯干侧移 | 测「乱报」 |
- **变量**：男 / 女 × 4 种体型；穿紧身运动衣 / 宽松 T 恤；光线；相机高度（腰 / 胸 / 头顶）、距离、广角畸变；人没正对镜头（转 5° / 10°）；背景杂乱；手机竖拍分辨率。
- **对照组**：**完全中性**的站姿要占约 1/4，用来测 AI 是不是「总能找出问题」（假阳性）。
- **规模**：约 200 套（每套正 / 侧 / 背 3 张）。渲染脚本和清单（每张图的参数、许可）进仓库 `tools/posture_eval/`，图片本身体积大，放发布附件或按脚本重新生成，不进 git 历史。
- **公开授权图（定性）**：只用 Wikimedia Commons 上 CC0 / CC BY 的体态示意图、医学插图，清单里逐张记许可和出处；没有可靠标准答案，只看措辞和是否乱下诊断。**不用任何真实用户照片，也不用来源不明的网络照片。**
- **关键点基线（不调 AI）**：同一批图用开源姿态估计（MediaPipe Pose Landmarker / MoveNet，Apache-2.0）算高低肩、头前伸的角度，作为「不靠大模型」的对照。如果关键点在高低肩这类几何量上明显更准，第三期就把关键点测出来的数字和照片一起给 AI（第 7 节）。

### 3.3 提示词和输出格式
- 用**生产环境同一套**系统设定（托底用 `system_prompt_lite.md`，其他用 `system_prompt.md`）+ 第一期要加的体态说明（第 6.2 节），这样测出来的就是用户会看到的。
- 评估时额外要求结构化输出（JSON）：每项 `{item, finding: none|mild|moderate|marked|unsure, side, confidence, evidence, photo_issues[]}`，方便自动打分；第一期上线后不要求用户看到 JSON。
- 给一个固定的合成「用户情境」（肌肉负荷：推多拉少；一条禁用动作），测建议是否用到肌肉负荷、是否避开禁用动作。

### 3.4 指标
| 指标 | 怎么算 |
|---|---|
| 检出率 | 中 / 明显等级里，说出「有」且方向对的比例（每项分开） |
| 假阳性率 | 中性图里报出「有」的比例（`unsure` 不算错） |
| 等级一致性 | 和标准答案的加权 kappa |
| 方向正确率 | 高低肩左右、哪侧更前 |
| 稳定性 | 同一套图跑 2 次结论一致的比例 |
| 照片质量意识 | 斜拍 / 太近 / 宽松衣服时有没有在 `photo_issues` 里说出来 |
| 措辞 | 人工按评分表抽查 30 条：有没有诊断式说法、评价外貌、建议禁用动作、忽略禁用动作 |
| 用不用肌肉负荷 | 建议里有没有引用情境里的推 / 拉组数 |
| 费用和延迟 | 每次实际 token 和秒数 |

### 3.5 建议的通过门槛（数值由负责人定，D9）
- 中性图假阳性 ≤ 20%；中 / 明显等级检出 ≥ 75%；高低肩 ≥ 2 cm 时方向正确 ≥ 90%；措辞违规 0 条。
- **没过门槛的模型**：第一期仍可以发照片（用户自己的 AI，用户的选择），但回答前加固定提示「这个模型看体态的准确度有限，结论仅供参考」，并在 `ai/catalog.py` 里给这个模型记 `posture: "limited"`；托底没过门槛时，托底的体态入口只给拍照指南和「用文字描述你的体态」，不发照片（D4）。

### 3.6 预算（等批准，不先花）
- 用量：每个模型 200 套 × 2 次 = 400 次调用；每次 3 张图 + 约 2.5k 文字 token 输入、约 0.8k 输出。图片 token 各家算法不同，按每张 1–1.6k 估，每次输入约 6–8k token。每个模型约 3.2M 输入 + 0.32M 输出 token。
- **托底 DeepSeek**：按 `fallback_params.py` 的高峰价（输入未命中缓存 ¥2 / 百万、输出 ¥8 / 百万）≈ 3.2 × 2 + 0.32 × 8 ≈ **¥9**。
- **Claude / GPT / Gemini**：按评估当天各家官方价目表算（价格随时会变，本文不写死）。做法：先每个模型跑 **10 套试跑**（约 5%），用 `ai_usage` 记下的真实花费外推全量，**超过单模型上限就只测便宜档**。
- 建议上限：DeepSeek ¥20；三家合计 **US$100**（每家 US$35 以内）。用负责人自己的 key、本地测试账号，图片全是合成的（D1）。

## 4. 数据流
### 4.1 第一期：默认不存（开关 `posture_photos`）
```
页面拍 / 选 3 张 → canvas 重新编码（去 EXIF、长边 1600）→ 可选：手动涂掉脸
  → POST /v1/assistant/chat（images[] + purpose="posture"）
  → 服务器：校验 → 发给用户选的 AI（托底 / 自带 key）→ 流式回答
  → 回答结束（含工具循环、写入确认）后：ai_messages 里这几张图的 base64 换成占位
     {"type":"image_redacted","purpose":"posture","n":3}
  → 之后的轮次：context.py 把占位写成「（这里有 3 张体态照片，按设置没有保存）」，AI 靠自己上一轮的文字结论继续
```
- **不进日志**：实现时逐个核对适配器和 `chat.py` 的日志 / 报错路径，并加测试确认请求日志、错误日志、审计里都没有图片数据。
- **不进备份**：回答结束后才删，在这几分钟里正好碰上每晚备份的概率很小；要做到严格，可以让图片从一开始就不写进 `ai_messages`（只在内存里传给模型），代价是工具循环中途出错重试时要用户重发。推荐「回答结束即删」（简单，和重试兼容）。
- **订阅类（MCP）用户**：照片永远不经过我们的服务器。今天页的体态入口对他们是「复制提示词 + 拍照指南」，在自己的 AI app 里发照片；AI 照样用 `get_muscle_load`、写记忆。
- **托底**：照片发给 DeepSeek，托底同意里已经写了。体态照片比饭菜照片敏感，第一次用体态入口前弹一次**提示**（不是新的同意，第 5.1 节）。
- **AI 公司那边**：照片按各家 API 条款处理，一些公司会为滥用监控保留一段时间；实现时核对各家当前条款，把要点写进提示和手册，不写死天数。
- **人脸**：体态分析不需要脸。拍照引导里写「可以不露脸，侧面露出耳朵就够」；页面提供手动「涂掉」（在缩略图上点一下就在那里画实心圆，canvas 里完成，原图不出手机）。不做自动人脸检测（WKWebView 没有现成接口，引入模型的代价不值得）。

### 4.2 第二期：可选的「体态记录」（开关 `posture_records`）
- **默认关、单独同意**：「我 → 训练设置 → 体态记录」里打开时弹单独的同意（表 `posture_consents`，带版本，和托底同意一样的做法），**不并进一键同意（`consent_one_tap`）**。不同意就和第一期一样不存。
- **存什么**：表 `posture_photos`（`id, user_id, taken_on, view front|side|back, image BYTEA, nonce, created_at, expires_at`），每张 ≤ 1200 px、去元数据（复用 `images/` 的校验和缩放）；**AES-256-GCM 加密**，附加数据绑定 `user_id` + 照片 id，主密钥 `secrets/media_master_key`（和 `ai_master_key` 分开，可以单独轮换）。表 `posture_assessments`（`user_id, taken_on, provider_kind, model, findings JSONB, summary TEXT`）存 AI 的结构化结论，**不存照片也能有**（第一期就可以写，见 6.1）。
- **谁能看**：只有本人。**不进任何共享、圈子、管理员看板**（和心电图同级，新 scope `posture`，`shares` 永远不含它），不 GRANT 给 `grafana_ro`。管理员技术上有数据库和主密钥，同意书要如实写（D12）。
- **给 AI 的**：工具 `get_posture_history` 只返回日期和文字结论，**不返回照片**。只有用户在页面上点「和 X 月的对比」时，页面把那两次的照片解密后作为这次提问的附件发给他选的 AI（同样回答后删副本）。
- **删除和期限**：单张 / 单次 / 全部删除；默认 **12 个月**到期自动删（每晚任务，D6）；注销账号级联删除；备份里的密文按同意书第 3 节的 6 个月轮换消失。关掉体态记录 = 删除全部已存照片（先确认）。
- **对比页**：同一视角两次照片左右并排 / 滑块对比，下面是两次的 AI 结论；不做自动打分曲线。
- **容量**：每次 3 张 × 约 200 KB，每月一次，100 人一年约 0.7 GB，数据库和备份都承受得了。

## 5. 同意书的影响（以下全是草案，等负责人批准）
### 5.1 第一期：建议不改同意书正文
- 主同意书第 2 节「AI 助手」已经写了「回答需要的那部分数据发给我选的 AI 公司」；托底条款已经写了「我发的照片、截图……也会发过去」。第一期只是一种新的提问方式，**发送范围没有扩大**；而「回答后删原图」让存储比现在更少。
- 所以第一期**不升版本**（不打扰所有人重新确认），改为体态入口第一次使用时的**提示**（页面文案，不是同意书）：
  > 体态照片只随这次提问发给你选的 AI（{AI 名称}），回答完服务器上就删掉原图，只留 AI 的文字结论。可以不露脸；穿贴身的运动服，看得更准。AI 的结论只是观察，不是诊断。
- 如果负责人决定把「回答后删原图」扩大到所有聊天照片（D2），建议等第二期改版时在第 2 节「AI 助手」末尾一起补一句（草案）：
  > 我在 Jarvis 里发的照片、截图和视频画面只随那次提问发给我选的 AI，回答完成后服务器上不保留原图，只保留文字对话。

### 5.2 第二期：需要改版（草案）
- **第 1 节「收集哪些数据」**，把「不主动收集：位置、通讯录、照片、其他 app 的数据」改成：
  > - **体态照片（可选，默认不开）**：只有我在「训练设置 → 体态记录」里单独同意并打开后，才会保存我自己拍的体态照片（正面 / 侧面 / 背面），用来对比变化；照片加密保存，默认 12 个月后自动删除，我可以随时删除或关掉（关掉即删除全部已存照片）。
  > - 不主动收集：位置、通讯录、相册里的其他照片、其他 app 的数据。
- **第 2 节「谁能看到」**，在「心电图」那条后面加：
  > - **体态照片**：只有我自己能看，**不进任何共享和圈子**；AI 只读文字结论，只有我点「对比」时才把那几张照片随这次提问发给我选的 AI。管理员作为服务器维护者，技术上能接触到加密后的数据和密钥。
- 版本号按当天日期升级（例如 `2026-11-xx.1`），中文、英文（`consent.en.md` / `family-consent.en.md`）两份同时改，`docs/family-consent.md` 和 `profile/consent.md` 保持一字不差（测试会查）。
- **单独的体态记录同意要点**（打开开关时显示，草案）：
  > 1. 打开后，你拍的体态照片会加密存在服务器上，用来对比变化。
  > 2. 只有你能看；不进共享和圈子。
  > 3. AI 平时只读文字结论；你点「对比」时，那几张照片会随这次提问发给你选的 AI。
  > 4. 默认 12 个月后自动删除；随时可以删除，关掉即删除全部。
  > 5. 这是训练参考，不是医疗检查。

## 6. 接肌肉负荷图和训练计划（#177）
### 6.1 流程
```
用户发 3 张体态照片（或在自己的 AI app 里发）
  → AI：get_briefing → get_muscle_load(range=week) → get_exercise_rules
  → 回答：每项观察（带把握程度和照片质量）+ 和肌肉负荷的关系 + 建议
  → 写长期记忆：add_observation(topic=posture, 「YYYY-MM-DD 体态照片（AI 观察，正/侧/背）：…」)
     （记录类写工具照旧直接执行；用户可在「我」页删）
  → [第二期 / 或第一期就写] posture_assessments 存结构化结论
  → 附肌肉卡：show_card muscle + highlight=[建议多练的肌群]
  → 用户点「按这个调计划」→ AI propose_plan（只给改动）→ 用户确认 → save_plan / swap_exercise
```
- **计划改动必须确认**：和肌肉负荷第一期一样，`propose_plan` 给出改动，用户在卡片上确认后才 `save_plan`；提示词和按钮文字都写「先给我看改动，我确认后再改」。
- **重点肌群**：AI 只**建议**（卡片上「设为重点肌群」按钮，用户点了才 `PUT /training/goals`），不新增 AI 写 `focus_muscles` 的工具。
- **体态目标（`physique_goal`）**：是用户自己写的（source `user`），AI 不改写；可以在回答里建议一句新目标，用户点「用这句」才更新。
- **动作限制**：AI 不能因为照片把动作设成「禁用」；最多建议「注意」，用户确认后才 `set_exercise_rule`（照片看到的只是体态，不是伤病）。已有的禁用 / 注意动作照常拦截推荐。
- **复拍**：建议 4–8 周复拍一次，同样的位置、距离、衣服；第二期才有对比页，第一期 AI 只能对比自己记下的文字结论。

### 6.2 要改的地方（第一期）
| 位置 | 改动 |
|---|---|
| `memory_params.py` | 新 topic `posture`（关键词「体态 / 圆肩 / 骨盆前倾 / 高低肩 / 头前伸 / posture」），`STALE_DAYS["posture"] = 90` |
| `training/params.py` | `POSTURE_HINTS`：每种体态模式 → 建议多练的肌群 key（如圆肩 → `middle back`、`traps`、`shoulders`；骨盆前倾 → `glutes`、`abdominals`、`hamstrings`）、建议留意的比例（如推 / 拉组数比 > 1.5），每条带出处和「证据强度：弱」说明 |
| `get_muscle_load` | 自己的结果多一个 `posture`：最近一次体态结论（日期 + 一句话，来自记忆 / `posture_assessments`）和按 `POSTURE_HINTS` 算出的提示（推 / 拉组数比等）；description 只加几个字（工具定义有总长度预算） |
| 肌肉卡（`show_card` type `muscle`） | 新参数 `highlight`（肌群 key 列表）+ 短标签，图上描边显示 |
| `ai/system_prompt.md`、`assistant/CLAUDE.md`（两份同步）、`system_prompt_lite.md`、MCP prompt `butler` | 体态照片一节：只说观察、带把握程度和照片质量、不诊断不评价外貌、先看肌肉负荷和禁用动作、写记忆、改计划先确认、建议复拍 |
| `ai/chat.py`、`ai/context.py` | `purpose` 参数；回答结束后把这条消息的图片换成占位；占位的上下文文字 |
| `web/static/ai-media.js` + Jarvis 入口 | 「体态照片」模式：三步拍照引导（正 / 侧 / 背的站位示意，用开源图标，不自绘）、手动涂抹、第一次提示；`tr()` 双语 |
| 「我 → 训练设置」、肌肉负荷页 | 「拍体态照片让 Jarvis 看」按钮；订阅类用户 = 复制提示词 + 拍照指南 |
| 使用手册（中英） | 「和 Jarvis 对话」里加体态照片一小节 |
| 测试 | 图片回答后被替换、占位进上下文、日志无 base64、`no_vision` 时的提示、`purpose` 只接受已知值、托底没过门槛时不收体态照片、i18n |

## 7. 分期和开关
| 期 | 开关 | 内容 | 依赖 |
|---|---|---|---|
| 第零期 评估 | 无（离线脚本） | `tools/posture_eval/`：合成图渲染、跑各家模型、打分、报告 `docs/posture-eval-results.md`（只有合成数据） | D1 预算批准 |
| 第一期 对话里看体态 | `posture_photos` | 第 4.1、6 节：拍照引导、回答后删原图、记忆、肌肉卡高亮、计划确认、手册 | 第零期结果（决定措辞、托底开不开） |
| 第二期 体态记录 | `posture_records`（需要 `posture_photos`） | 第 4.2 节：单独同意、加密存储、对比、到期删除、`get_posture_history`；同意书改版 | 负责人批准同意书草案 |
| 第三期（可选）关键点测量 | `posture_keypoints` | 页面里用 MediaPipe Pose（WASM，Apache-2.0，模型约 3–9 MB）在手机上算高低肩、头前伸角度，数字和照片一起给 AI；照片不多发任何地方 | 第零期证明关键点比大模型准 |

开关都按 `docs/rollout.md` 的流程：部署默认关 → 内测组 → 线上验收 → 全员。`posture_records` 在 `rollout/registry.py` 的说明里写明需要同意书新版本。

## 8. 风险
| 风险 | 应对 |
|---|---|
| AI 乱报体态问题，用户焦虑 | 中性图假阳性门槛；提示词要求说把握程度；界面只放短标签，长解释给 AI |
| 照片质量差导致结论错 | 拍照引导 + AI 必须说照片问题；建议同条件复拍 |
| 托底把身体照片发到境外公司 | 首次提示写明是 DeepSeek；没过评估门槛就不对托底开放照片 |
| 体态照片泄露 | 第一期回答后即删；第二期加密、不进共享、到期删除 |
| 未成年人的身体照片 | 体态入口只对资料里满 18 岁的人显示（D11） |
| 建议变成「医疗建议」 | 不诊断、不按照片禁用动作；疼痛 / 明显不对称建议就医 |

## 9. 需要负责人决定
| # | 决定 | 推荐 |
|---|---|---|
| D1 | 第零期评估花不花钱、预算、用谁的 key | **批准**：DeepSeek ≤ ¥20、三家合计 ≤ US$100，先 10 套试跑再全量；用负责人自己的 key + 本地测试账号，只用合成图 |
| D2 | 「回答后删原图」只管体态照片，还是所有聊天照片 | **所有聊天照片**（让「不主动收集照片」和事实一致）；体态照片是第一期必做，其他照片可以同一个 PR 顺带 |
| D3 | 第一期改不改同意书 | **不改正文**，用首次提示；等第二期一起改版，只让大家重新确认一次 |
| D4 | 托底 DeepSeek 能不能收体态照片 | **看第零期结果**：过门槛才开；没过就只给拍照指南和文字描述 |
| D5 | 做不做第二期长期存储 | **做**，但单独同意、默认关、加密、不进共享 |
| D6 | 体态照片默认保存多久 | **12 个月**自动删除（用户可以提前删） |
| D7 | 人脸怎么处理 | **拍照引导 + 手动涂抹**，不做自动人脸检测 |
| D8 | AI 能不能根据照片改动作限制 | **只能建议「注意」、用户确认后写**；不能设「禁用」 |
| D9 | 评估通过门槛 | 假阳性 ≤ 20%、检出 ≥ 75%、高低肩方向 ≥ 90%、措辞违规 0 |
| D10 | 订阅类（MCP）用户 | **只给提示词和拍照指南**，照片不经过服务器 |
| D11 | 未成年人 | **只对满 18 岁的用户**开体态入口 |
| D12 | 同意书里写不写「管理员技术上能接触加密数据和密钥」 | **写**（如实，和现有「管理员能接触数据库」一致） |
| D13 | 同意书草案（5.1 补句、5.2 两处改动、体态记录同意要点） | 等负责人逐字批准，批准前不进 `consent.md` |

---

## English summary
Design for #174 / #177 (posture photos analysed by the user's own AI, linked to the muscle load map). Documentation only: no feature code, no paid API calls, no real user photos.

- **Phase 0 – evaluation (no flag)**: synthetic human renders (MakeHuman CC0 assets posed in Blender with known shoulder protraction, forward head, shoulder height difference and pelvic tilt, plus neutral controls and capture variations), qualitative checks on CC0 / CC BY illustrations only, and an open-source keypoint baseline (MediaPipe Pose / MoveNet). Models: fallback DeepSeek `deepseek-flash`, Claude, GPT and Gemini (default + a cheaper tier). Metrics: detection, false positives on neutral poses, weighted kappa, side accuracy, repeat stability, photo-quality awareness, wording (no diagnosis, no appearance judgements), use of muscle load, cost and latency. Budget awaiting approval: DeepSeek ≈ ¥9 (cap ¥20), the three others ≤ US$100 total after a 10-set pilot.
- **Phase 1 – `posture_photos`**: guided front / side / back capture in Jarvis, EXIF dropped by canvas re-encoding, optional manual face masking; photos go only to the user's chosen AI for that question and are **replaced by a placeholder in `ai_messages` once the answer completes** (today, in-app chat images are stored until the conversation is deleted — the plan proposes fixing this for all chat images). MCP subscription users send photos in their own AI app; nothing reaches our server. The AI reads `get_muscle_load` and exercise rules, writes an observation (new memory topic `posture`), shows the muscle card with highlighted muscles, and changes the plan only after the user confirms (`propose_plan` → confirm → `save_plan`). No consent text change; a first-use notice instead.
- **Phase 2 – `posture_records`**: optional, separate opt-in consent, AES-256-GCM encrypted storage with its own key, never shared or in circles (like ECG), text-only `get_posture_history` for the AI, side-by-side comparison, 12-month auto-delete. Requires a consent revision; the draft wording (§5.2) awaits the owner's approval.
- **Phase 3 – `posture_keypoints` (optional)**: on-device pose keypoints give measured angles alongside the photos, if Phase 0 shows they beat the models on geometry.
- Owner decisions D1–D13 (§9) each carry a recommendation; every consent wording change is a draft only.
