# AI 优先 UI 调研：同类产品怎么做 AI 交互、渐进解锁和数据展示（2026-10-25）

> 给 [`ui-ai-first-redesign.md`](ui-ai-first-redesign.md) 做依据。Issue：#279。
> 只用公开资料：官网、官方帮助中心 / 博客 / 开发者文档、App Store 页面，官方没写的用媒体评测和社区帖补充，脚注里都标了来源类型。
> 有几个官方页面抓取失败（WHOOP 的 thelocker 博客、部分 Apple / Oura 支持页），内容来自搜索摘要，脚注里标了「据搜索摘要」。
> 「未查到公开资料」= 没找到可信来源，不等于确认没有。产品以后可能会改，以 2026-10-25 能查到的版本为准。

## 0. 我们现在的样子（对照用）

现有今天页（`web/static/app.js` 等，全部在 WebView 里）：

| 位置 | 内容 | 数量 |
|---|---|---|
| 今天 | 恢复圈 + 睡眠 / 睡眠分 / 昨天负荷三块 + 负荷状态、活动（步数 / 活动 / 锻炼）、健康监测、负荷目标、压力、提醒、睡眠卡（阶段图）、入睡目标、训练与饮食（含快捷记餐）、肌肉负荷、日记 | 约 13 张卡 |
| 趋势 | 指标 / 睡眠 / 报告三段；指标按 恢复（8）、睡眠（10）、负荷（12）、压力（2）、长期（3）、行为影响（1）分组，外加「当前状态」 | 约 36 张图 + 状态块 |
| 饮食 | 饮食 / 采购两段；饮食档案、当天摄入、喝水、快捷记录、每餐推荐、食谱 | 6–8 块 |
| 记录 | 训练（本周、建议、训练历史月历）、日记、饮食 | 2–3 段 |
| 我 | 身体年龄、状态卡、近况、长期记忆、健康档案（补剂 / 事件 / 化验 / 训练记录）、AI 设置、圈子 / 邀请、饮食档案、训练设置、通知、数据状态、导入、手机同步、手册、新手引导、设置… | 15+ 行 |
| 朋友（开关 `friends`） | 成员 / 动态 / 排行榜 + 圈子管理 | 1 个 tab |
| Jarvis | 底部中间的圆键（`jarvis_orbit`）：点开 对话 / 语音 / 拍照 / 快记 四个键，按住直接说话，回答在半屏里，带约 25 种卡片（`show_card`） | 1 个入口 |
| 引导 | 激活码 / 邮箱登录 → 同意书 → 3 屏介绍 + 小贴士（`intro.js`）→ 使用手册 | — |

另外 `rollout/registry.py` 里有 50 多个功能开关，大部分已经对所有人打开。问题不是缺功能，而是**所有功能同时摆出来**：新用户第一天看到的和用了半年的人一样多，而且大部分卡片在没有数据时只显示「—」或「基线不足」。

---

## 1. AI 驱动的健康 / 健身产品

### 1.1 WHOOP
- **首页**：最上面是睡眠、恢复、负荷三个分，可点进详情；设置里能隐藏指标；所有页面都有「+」（开始运动 / 记日记）[^whoop-1]。评测补充：首页还有心率、HRV、呼吸频率、Strain vs Recovery 周图和全天建议，底栏重排后 Coach 更好点到，首页能连到 20 多个页面，上下滑看更多或更少细节[^whoop-2]（媒体）。
- **AI 在哪里**：WHOOP Coach 2023-09 上线（基于 GPT-4），App 内提问、保留历史[^whoop-3]（媒体）。2026-05 起有 My Memory（可编辑的目标、日程、药物等背景）、Proactive Check-Ins（主动提醒：重训前先睡够、旅行调整、负荷预警）、语音 / 文字记日记[^whoop-4]（第三方博客）；DC Rainmaker 确认这三项已可用[^whoop-5]（媒体）。2026-07 起 AI 能把趋势问题直接画成图[^whoop-6]（官方更新日志，据搜索摘要）。
- **新用户**：社区说法约 4 天出恢复分、约 30 天基线稳定[^whoop-7]（社区）；官方社区回复说约 3 周完成校准，Healthspan 要约 90 天、首月至少 21 次睡眠[^whoop-8]（社区里的官方回复）；主动提醒的前两周也是校准期[^whoop-4]。
- **信息密度**：首页分数 → 点进趋势 → 日记 / 行为；可隐藏指标。
- **图表**：恢复 0–100% 三色，绿 67–100、黄 34–66、红 0–33[^whoop-9]（官方开发者文档，据搜索摘要）。

### 1.2 Oura
- **首页**：新版从 5 个 tab 合成 3 个：Today、Vitals、My Health[^oura-3]（媒体）。Today 顶部是睡眠、准备度、活动三个分数入口和心率、白天压力的快捷入口，下面是随一天变化的洞察和当天时间线；官方说目标是做成新闻 app 的「Top Stories」，不用翻很多数据才知道要点[^oura-2]（官方博客；页面标 2024-10-03，媒体报道在 2025-10，具体改版时间未核实）。
- **分层**：Vitals 把核心指标放在个人基线范围里，点开看贡献因子；My Health 放心血管年龄、压力韧性等慢变量和周 / 季 / 年报告[^oura-2]。
- **AI 在哪里**：Oura Advisor 从 Today 右下角的「+」进入，也能从准备度洞察里点进去[^oura-1][^oura-6]；能读数据画图、做计划、记住用户说过的事（Memories），语气可选、check-in 频率可调[^oura-1]。没有单独的 AI tab。
- **新用户**：官方说最多需要两周学会你的各项平均值，之前用人群平均值给洞察[^oura-4]。
- **图表**：分数 0–100，85+ Optimal、70–84 Good、60–69 Fair、<60 Pay attention[^oura-5]。

### 1.3 Bevel
- **首页**：评测称是「好扫读的仪表盘」，5 个核心分（Recovery、Sleep、Strain、Stress、Energy Bank）[^bevel-2]（媒体）；新版加了 Timeline，把运动、睡眠、饮食按时间排在一起[^bevel-1]。具体排布未查到公开资料。
- **AI**：Bevel Intelligence（Pro 订阅，有每周额度），聊天有 Fast / Thinking / Adaptive 三档，能建 check-in、生成训练计划并发到手表、读日历[^bevel-1]；AI 记饭、AI 生成训练在免费版[^bevel-2]。入口位置未查到公开资料。
- **新用户**：评测说个性化基线要两到六周[^bevel-3]（第三方）；用户反馈没戴表睡就没有恢复分[^bevel-4]（社区）。

### 1.4 Athlytic
- **首页**：4 项：Recovery（0–100%）、Exertion（0–10，白天累积）、Target Exertion Zone（按恢复给的当日目标）、Sleep（质量、睡眠债、规律性）[^ath-1]。
- **AI**：官网强调「Built before AI」；App Store 版本加了 Apple Intelligence 的「Ask Athlytic」，用自然语言问数据，开发者说数据不出设备[^ath-1][^ath-2]。偏只读问答。
- **新用户**：官方说**没有校准期**：连接 Apple 健康时拉历史数据，对比 60 天滚动基线，不足 60 天的边用边优化[^ath-3]。这和我们一样（app 首次同步 90 天）。

### 1.5 Gentler Streak
- **首页**：一条 Activity Path：绿色带的下沿是维持体能的最低量、上沿是过量线，白线是今天的实际活动；可切每日 / 10 天 / 30 天[^gs-1]。3 个 tab：Streak、Insights、Profile[^gs-2]。
- **AI**：没有聊天助手，只有端侧的文字建议和 Siri 快捷指令[^gs-2]。
- **借鉴点**：用「范围带」代替分数，一眼就知道今天该多动还是少动；还能手动把状态设成 生病 / 受伤 / 休息，建议跟着变[^gs-1]。

### 1.6 Welltory
- **首页**：Today 屏是每日能量「电池」预测 + 神经系统快照，下面是洞察（点开能看数据来源）和短期自我实验[^well-1][^well-2]。
- **AI**：App 内购有「AI health coach」，功能细节未查到公开资料[^well-2]。
- **借鉴点**：洞察可溯源（点开看是哪些数据推出来的）。

### 1.7 Apple 健康 / 健身
- **首页**：健康 app 的 Summary = Pinned（用户自己选的类别）+ Highlights（随时间变化的提示，可「显示全部」）[^apple-1]。
- **渐进解锁**：Vitals 要连续戴表睡 **7 晚**才建立个人范围，之后才在至少 2 项夜间指标超出范围时通知[^apple-2]（官方，据搜索摘要）。这是「数据够了才出现」的典型。
- **AI**：watchOS 26 的 Workout Buddy 是运动中的语音播报，端侧处理[^apple-3]（媒体）。传闻中的 AI 健康教练没有在 iOS 27 发布[^apple-4]（媒体，传闻）。
- **借鉴点**：「固定」（Pin）由用户决定首页放什么，其他都在「浏览」里。

### 1.8 Garmin Connect
- **首页**：2024 年改成分区，顶部 In Focus 放 Training Readiness、Training Status、Body Battery 等少数核心指标，滚到底「Edit Home」自定义[^gar-2]（媒体，据搜索摘要）；网页版有约 120 多个报表元素可自选[^gar-3]。
- **AI**：Connect+（付费）的 Active Intelligence 是首页顶部的文字小卡，可点赞 / 点踩，没有聊天；评测觉得早期提示偏泛[^gar-3][^gar-4]（媒体）。
- **教训**：可定制是 Garmin 的主要手段，但默认就很多，靠用户自己删。

### 1.9 Fitbit + Gemini 教练
- **首页**：Today 顶部 4 个可自定义的焦点指标，下面是建议卡片流；底栏 4 个 tab：Today / Fitness / Sleep / Health[^fit-1][^fit-2]（媒体）。
- **AI**：浮动按钮「Ask Coach」打开聊天，每个 tab 也有自己的 Coach 提示；能建训练计划、按反馈和伤病调整、解释睡眠、记住用户说过的事；主动给每日睡眠洞察[^fit-1][^fit-2]。没有单独的 Coach tab。
- **新用户**：第一次用是 5–10 分钟的文字或语音对话，聊目标、偏好、器材，可以跳过[^fit-1]。
- **上线方式**：2025-10 先给美国 Premium 用户公测，可在菜单里切回旧版[^fit-3]（媒体）。

---

## 2. AI 原生 app 的首页

### 2.1 ChatGPT
- **主动推送 Pulse**（2025-09）：夜里根据聊天、记忆、反馈生成，早上给 5–10 张卡片，点开看完整内容并追问；看完显示「今天就这些」，**刻意做成有终点的**，不是无限信息流；邮箱 / 日历连接器默认关闭，要用户自己开[^chatgpt-1][^chatgpt-2]。Pulse 是和聊天并列的一个入口，不替代聊天首页[^chatgpt-1]。
- **对话里的富组件**（Apps SDK 设计规范，官方）：三种显示方式：inline（默认）、全屏（多步任务，输入框还在）、画中画；inline 卡片**只做一件事、最多 2 个动作（1 主 1 次）**，不要 tab、深层导航和嵌套滚动；轮播 3–8 项，每项最多一个按钮；组件先于模型文字显示；只有图形能明显帮上忙时才加组件[^chatgpt-4]。

### 2.2 Claude
- **首页**：聊天优先，没有信息流；主动能力（定时任务、持续运行的 agent）放在单独的 Cowork 里[^claude-1]。
- **富组件**：2026-03 起回复里能直接画图表、示意图，手机上能在对话里显示交互式小 app[^claude-1]。
- **记忆**：分条目、能在设置里查看和删除；健康这类敏感话题默认不进记忆，要用户自己打开[^claude-1]。2026-01 起能读 iOS / Android 的健康数据（Pro / Max，先美国）[^claude-1]。
- **连接器**：在每个对话的「+」里按需打开[^claude-3]；语音模式调用已连接的工具前先问用户[^claude-4]。

### 2.3 Perplexity
- **首页**：搜索框为主，可选的小组件（金融、体育）由设置里的关注列表驱动，可以关掉；Discover 信息流从侧边栏进入[^pplx-1][^pplx-2]。
- **借鉴点**：首页只有一个主动作（问），其他都是可选的。

---

## 3. 渐进式披露 / 功能解锁

### 3.1 原则（NN/g 等）
- 先只给最重要的少数选项，专业选项在需要时再给；新手少出错，老手少扫无关项。**层级最好不超过两层**，多了容易迷路[^nng-1]。
- 手机教程：NN/g 测试里看过卡片式教程的人反而觉得任务更难，成功率和耗时也没有优势；结论是只讲陌生的东西，短、不打扰[^nng-2]（具体分数是二手转述）。
- 「功能解锁」的风险：锁住的东西不告诉人怎么解锁、看不到前面还有什么、太早给高风险写操作。**撤销 / 导出 / 删除这类控制要一直能看到**；专业工具不适合隐藏[^unlock-1]（模式库，非研究）。

### 3.2 Duolingo
- 先上手做一道题再注册，注册提示放在第一节课之后（二手资料称日活因此 +20%）[^duo-1]。
- 单一路径，每个圆圈一级；故事、练习嵌在路径里，任务放在单独的 Quests tab[^duo-2]（官方）。
- 联赛（排行榜）每周开始，可以在设置里关掉公开资料来退出[^duo-3]（官方）。「完成约 10 节课才开联赛」只见于第三方[^duo-4]，官方没写。
- **借鉴**：一次只出现一个新东西，出现时机和「刚完成的动作」挂钩。

### 3.3 游戏式引导
- 一次教一个机制，先给安全的练习空间，再加难度；教程短、少文字、靠反馈[^game-1]。
- 阶梯：引入 → 练习 → 和已学的组合，每段结尾一个小挑战[^game-2]。反例：前 5 分钟同时教移动、射击、技能、制作，测试者全部迷失[^game-2]。

### 3.4 Notion
- 注册时问用途和角色，按回答准备起始工作区，不一次展示所有功能[^notion-1]（第三方分析）。
- 「/」打开完整的块菜单，**顶部放你最常用、最近用的**[^notion-2]（官方）。
- 有用户反馈空白页和满模板都吓人，缺中间的渐进路径[^notion-3]（用户评论）。

### 3.5 Linear
- 工作区还空着时就教 Cmd+K，把命令菜单当成核心交互；「熟悉 Linear」清单里每完成一项，就在上下文里露出一块界面[^linear-1]（第三方分析）。
- 命令菜单里直接显示快捷键，兼顾参考表和提示[^linear-2]。

### 3.6 Arc
- Arc Max（一组 AI 功能）默认关闭，要在设置里主动打开[^arc-1]；上手演示约 90 秒、只教 2 个功能，并保留一键回到普通搜索[^arc-2]（单人分析）。

### 3.7 触发条件汇总

| 触发 | 例子 | 我们能用的信号 |
|---|---|---|
| 完成一个动作 | Duolingo 第一节课后提示注册；Linear 清单每完成一项露出一块界面 | AI 调过某个工具（`assistant_audit` 记了所有渠道的工具调用） |
| 数据够了 | Apple Vitals 7 晚；Oura 两周学基线；WHOOP 约 4 天出分、约 3 周校准 | 评分模块的基线（恢复要 14 晚 HRV、负荷状态要 21 天等） |
| 用户主动 | Apple 健康的 Pinned；Arc Max 手动开；Garmin Edit Home | 「固定到今天」「显示全部模块」 |
| 时间 | 文章里提到，但没找到具体产品案例 | 使用天数（第一周结束才有周报） |
| 事件 | Apple Vitals 超出范围才通知；Gentler Streak 手动设生病 | 规则提醒、生病风险、健康监测异常 |

---

## 4. 数据展示

- **首页核心数字**：WHOOP 3 个（睡眠、恢复、负荷）[^whoop-1]，Oura 3 个分数入口[^oura-2]，Athlytic 4 项[^ath-1]，Fitbit 4 个可自定义焦点指标[^fit-1]。没找到把个数写成规范的权威文章[^nng-3]，但做得好的都在 3–4 个。
- **三层**：Oura 的 Today（要点）→ Vitals（核心指标对基线）→ My Health（长期）[^oura-2]；WHOOP 首页分数 → 点进构成和趋势[^whoop-1]；案例研究也多用「首屏分数 + 卡片，趋势单独看，细节渐进展开」[^dash-1]。原始数据一层只有少数产品给（Welltory 的洞察能溯源[^well-2]、Garmin 网页报表[^gar-3]）。
- **和基线比**：Oura Vitals、Apple Vitals、Athlytic 都把今天的值画在**个人范围**里，而不是人群标准[^oura-2][^apple-2][^ath-3]。
- **颜色**：NN/g 说颜色不该表示数值大小，只用来加强已经由形状 / 位置表达的分组，也不能只靠颜色（色盲）[^nng-3]。Apple 图表指南：用有区分度的颜色，不要同色不同深浅表示不同意思，标签简短[^apple-hig]。WHOOP 的红黄绿是行业惯例，但旁边总有数字。
- **图表类型**：长度和位置最适合快速读数；环形、仪表盘这类角度编码不适合精确比较，适合「一个值在 0–100 里的位置」[^nng-3]。
- **数字和文字**：NN/g 复杂应用指南建议去掉非必要元素，大号数字不配装饰图标更好扫读[^nng-4]；具体比例没有公开标准。
- **范围带**：Gentler Streak 用一条「该在这个范围里」的带子代替分数[^gs-1]，WHOOP / Athlytic 的负荷目标也是区间[^ath-1]。

---

## 5. 对照表

| 产品 | 首页核心数 | AI 入口 | AI 能写吗 | 新用户 | 密度控制 | 借鉴 | 不借鉴 |
|---|---|---|---|---|---|---|---|
| WHOOP | 3 分 + 3 生理值 | Coach（底栏），「+」 | 记日记 | 约 4 天出分、约 3 周校准，主动提醒前两周校准 | 隐藏指标 | 三个分放最上；AI 记忆可编辑；主动 check-in | 首页连 20 多页，太深 |
| Oura | 3 分数入口 + 洞察 + 时间线 | Today 右下「+」 | 未见公开资料 | 两周学基线，之前用人群平均 | 5 tab → 3 tab | **3 个 tab**；Today 像「头条」；Vitals 对个人范围 | — |
| Bevel | 5 个分 | 未查到 | 记饭、训练计划、check-in | 两到六周基线（第三方） | Timeline 合并记录 | 时间线把运动 / 睡眠 / 饮食放一起 | 5 个分偏多；AI 按额度收费 |
| Athlytic | 4 项 | Ask Athlytic（系统 AI） | 否 | 无校准期，拉历史 | 小组件做一瞥层 | 用历史数据省掉校准期 | — |
| Gentler Streak | 1 条活动带 | 无聊天 | — | 未查到 | 3 tab，数据少 | 范围带；手动设生病 / 受伤 | 没有 AI |
| Welltory | 能量电池 + 快照 | 付费 AI 教练 | 未查到 | 未查到 | 洞察 → 报告 | 洞察可溯源 | 指标命名太抽象 |
| Apple 健康 | 用户固定的类别 | 无（Workout Buddy 只播报） | — | Vitals 7 晚 | Pinned + 浏览 | **固定**；数据够了才出现 | 默认几乎为空，全靠用户自己配 |
| Garmin | In Focus 少数几项 | 首页文字小卡（付费） | 否 | 未查到 | Edit Home、网页报表 | 洞察可点赞 / 点踩 | 默认很多、靠用户删；AI 提示泛 |
| Fitbit + Gemini | 4 焦点指标 + 建议卡 | 浮动按钮 + 每个 tab 的提示 | 计划、调整 | 5–10 分钟开场对话，可跳过 | 4 tab | **开场对话代替表单**；能切回旧版 | 每个 tab 都有 Coach 提示，入口太散 |
| ChatGPT | 输入框 + Pulse | 本身就是 | 是 | 未查到 | Pulse 有终点 | **inline 卡片一事一卡、≤2 个动作**；推送有终点 | 信息流化 |
| Claude | 输入框 | 本身就是 | 是（连接器） | 未查到 | 连接器按对话开 | 记忆可查看可删；敏感记忆默认关 | — |
| Perplexity | 搜索框 | 本身就是 | — | 未查到 | 小组件可关 | 首页一个主动作 | Discover 信息流 |
| Duolingo | — | — | — | 先做一题再注册 | 单一路径、一次一个新东西 | **新东西跟着刚完成的动作出现** | 条件不公开 |
| Notion | — | — | — | 问用途定制起点 | 「/」菜单顶部是常用 | 按常用排序 | 空白页 |
| Linear | — | — | — | 先教 Cmd+K | 清单完成一项露一块 | 用一个核心交互贯穿 | — |
| Arc | — | Max 手动打开 | — | 90 秒只教 2 个功能 | opt-in | 能一键回到普通模式 | — |

## 6. 最重要的三条结论

1. **做得好的首页只有 3–4 个核心数，tab 在收缩**：Oura 从 5 个 tab 合到 3 个、把 Today 做成「头条」；WHOOP / Oura 首页都是三个分。我们的今天页有约 13 张卡、6 个 tab，是同类里最多的。
2. **AI 不单独占 tab，而是一个随处可达的按钮，并且越来越主动**：Oura 的「+」、Fitbit 的浮动按钮、WHOOP 的 Coach 都是这样；同时都在加记忆、主动 check-in、在对话里直接画图。ChatGPT 的规范给了对话里卡片的边界：一卡一事、最多两个动作、可展开；主动推送要有终点。
3. **渐进解锁的触发要看得见、和用户刚做的事挂钩**：Apple Vitals 7 晚、Oura 两周、WHOOP 约 3 周是「数据够了才出现」，Duolingo / Linear 是「完成一个动作就露出一块」。这次调研的产品里没看到「AI 用过某个功能，这个模块才出现」的做法（最接近的是 WHOOP 的 AI 会建议你追踪哪些行为[^whoop-4]），这是我们可以做的差异点；但要像 Apple 的 Pinned 一样让用户能自己固定和全部打开，安全类控制一直可见。

---

## English summary

Desk research for the AI-first redesign (#279), public sources only. **Takeaways:** (1) good health home screens show 3–4 core numbers and tab bars are shrinking (Oura went from 5 tabs to 3 and treats Today as "top stories"); ours has about 13 cards on Today and up to 6 tabs. (2) The AI is a button reachable everywhere (Oura's "+", Fitbit's floating "Ask Coach", WHOOP Coach), increasingly proactive (memory, check-ins, charts in chat); ChatGPT's Apps SDK guidelines bound in-chat cards to one purpose and at most two actions, and Pulse is deliberately finite. (3) Progressive unlocking works when the trigger is visible and tied to what the user just did: data-gated reveals (Apple Vitals after 7 nights, Oura's two-week baseline, WHOOP's ~3-week calibration) and action-gated reveals (Duolingo, Linear). None of the products reviewed reveals a module because the AI used it — that is our angle — but users must still be able to pin modules and turn everything on, and safety controls must always stay visible.

---

[^whoop-1]: WHOOP，The all-new WHOOP home screen，https://www.whoop.com/us/en/thelocker/the-all-new-whoop-home-screen/ （官方；抓取 403，据搜索摘要，2023）
[^whoop-2]: the5krunner，https://the5krunner.com/2023/03/28/new-whoop-home-screen-looks-pretty-but-is-it-as-intuitive/ （媒体，2023-03，2026-01 更新）
[^whoop-3]: BikeRadar，https://www.bikeradar.com/news/whoop-coach-chatgpt-4/ （媒体，2023-09）
[^whoop-4]: Strength Insight，https://strengthinsight.app/blog/using-whoop-ai-coach/ （第三方博客，2026）
[^whoop-5]: DC Rainmaker，https://www.dcrainmaker.com/2026/05/whoops-defensive-hastily-features.html （媒体，2026-05-10）
[^whoop-6]: WHOOP 2026 更新日志，https://www.whoop.com/us/en/thelocker/2026-whats-new/ （官方；抓取 403，据搜索摘要）
[^whoop-7]: WHOOP 社区，https://community.whoop.com/t/how-long-does-it-take-for-whoop-to-learn-your-baseline-metrics/359 （社区）
[^whoop-8]: WHOOP 社区，https://www.community.whoop.com/t/how-long-does-it-take-for-healthspan-to-calibrate-unlock/471 （社区，含官方回复；据搜索摘要）
[^whoop-9]: WHOOP 开发者文档，https://developer.whoop.com/docs/whoop-101 （官方；据搜索摘要）
[^oura-1]: Oura 博客，Oura Advisor，https://ouraring.com/blog/oura-advisor （官方，2025-03-31，2026-04 更新）
[^oura-2]: Oura 博客，New Oura app experience，https://ouraring.com/blog/new-oura-app-experience/ （官方，页面标 2024-10-03）
[^oura-3]: 9to5Google，https://9to5google.com/2025/10/20/oura-app-redesign/ （媒体，2025-10-20；据搜索摘要）
[^oura-4]: Oura 支持，https://support.ouraring.com/hc/en-us/articles/360057791533 （官方；据搜索摘要）
[^oura-5]: Oura 支持，Readiness Score，https://support.ouraring.com/hc/en-us/articles/360025589793-Readiness-Score （官方）
[^oura-6]: Oura 支持，https://support.ouraring.com/hc/en-us/articles/39512345699219/ （官方，2026-06；据搜索摘要）
[^bevel-1]: Bevel App Store 页面，https://apps.apple.com/app/6456176249 （App Store）
[^bevel-2]: kiledjian.com，https://kiledjian.com/2026/07/07/bevel-turns-apple-watch-data.html （个人博客，2026-07）
[^bevel-3]: Neura Health，https://neura.health/insight/bevel-health-app-in-depth-review （第三方评测，日期未知）
[^bevel-4]: Bevel 反馈板，https://feedback.bevel.health/feature-requests/p/source-of-hrv-reading-used-in-recovery （用户发言；据搜索摘要）
[^ath-1]: Athlytic 官网，https://www.athlyticapp.com （官方）
[^ath-2]: Athlytic App Store 页面，https://apps.apple.com/no/app/id1543571755 （App Store）
[^ath-3]: Athlytic 帮助，Getting started，https://athlyticapp.helpscoutdocs.com/article/13-getting-started （官方）
[^gs-1]: Gentler Streak 文档，What is the Activity Path，https://docs.gentler.app/understanding-your-activity-path/what-is-the-activity-path （官方）
[^gs-2]: Gentler Streak App Store 页面，https://apps.apple.com/us/app/gentler-streak-workout-tracker/id1576857102 （App Store）
[^well-1]: Welltory 官网，https://welltory.com （官方）
[^well-2]: Welltory App Store 页面，https://apps.apple.com/us/app/welltory-ai-health-app/id1074367771 （App Store）
[^apple-1]: Apple 支持，健康 app 摘要，https://support.apple.com/en-us/104997 （官方）
[^apple-2]: Apple 支持，Vitals，https://support.apple.com/120142 （官方；据搜索摘要）
[^apple-3]: Tom's Guide，Workout Buddy，https://www.tomsguide.com/wellness/fitness-trackers/apple-watch-just-got-an-ai-fitness-coach-how-to-enable-workout-buddy-in-watchos-26 （媒体，2025）
[^apple-4]: 9to5Mac，https://9to5mac.com/2026/08/29/apple-health-revamp-ai-coach-new-apple-watch-next-month/ （媒体，传闻，2026-08-29）
[^apple-hig]: Apple Human Interface Guidelines，Charting data，https://developer.apple.com/design/human-interface-guidelines/charting-data （官方）
[^gar-2]: Wareable，https://www.wareable.com/garmin/garmin-connect-app-update-2024-hands-on （媒体，2024；据搜索摘要）
[^gar-3]: DC Rainmaker，Garmin Connect+，https://dcrainmaker.com/2025/03/garmin-connect-plus-subscription-walkthrough.html （媒体，2025-03）
[^gar-4]: TechRadar，https://www.techradar.com/health-fitness/garmin-adds-premium-garmin-connect-tier-with-ai-features-but-promises-your-free-experience-is-not-going-away （媒体，2025-03）
[^fit-1]: 9to5Google，Fitbit Coach preview，https://9to5google.com/2025/10/27/fitbit-coach-preview/ （媒体，2025-10-27）
[^fit-2]: TechCrunch，https://techcrunch.com/2025/10/27/fitbits-revamped-app-with-gemini-powered-health-coach-rolls-out-to-premium-users/ （媒体，2025-10-27）
[^fit-3]: TechRepublic，https://www.techrepublic.com/article/news-fitbit-ai-coach-expands-ios-five-markets/ （媒体，2026-02；据搜索摘要）
[^chatgpt-1]: TechCrunch，ChatGPT Pulse，https://techcrunch.com/2025/09/25/openai-launches-chatgpt-pulse-to-proactively-write-you-morning-briefs/ （媒体，2025-09-25）
[^chatgpt-2]: eWeek，https://www.eweek.com/news/openai-chatgpt-pulse/ （媒体，二手综述）
[^chatgpt-4]: OpenAI Apps SDK UI guidelines，https://developers.openai.com/apps-sdk/concepts/ui-guidelines （官方）
[^claude-1]: Anthropic 发布说明，https://support.claude.com/en/articles/12138966-release-notes （官方）
[^claude-3]: Claude 帮助，Connectors，https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities （官方）
[^claude-4]: Claude 帮助，Voice mode，https://support.claude.com/en/articles/11101966-use-voice-mode （官方）
[^pplx-1]: Perplexity 帮助中心，Account & Settings，https://www.perplexity.ai/help-center/en/articles/10352990-account-settings （官方）
[^pplx-2]: Perplexity 博客，Getting started，https://www.perplexity.ai/hub/blog/getting-started-with-perplexity （官方）
[^nng-1]: NN/g，Progressive Disclosure，https://www.nngroup.com/articles/progressive-disclosure/
[^nng-2]: NN/g，Mobile-App Onboarding / tutorials，https://www.nngroup.com/articles/mobile-tutorials/ （分数为二手转述）
[^nng-3]: NN/g，Dashboards: preattentive processing，https://www.nngroup.com/articles/dashboards-preattentive/
[^nng-4]: NN/g，Complex application design，https://www.nngroup.com/articles/complex-application-design/ （据搜索摘要）
[^unlock-1]: AI UX Playground，Progressive feature unlock，https://aiuxplayground.com/pattern/progressive-feature-unlock （模式库，非研究）
[^duo-1]: Appcues，https://www.appcues.com/blog/the-10-best-user-onboarding-experiences （二手）
[^duo-2]: Duolingo 博客，New home screen design，https://blog.duolingo.com/new-duolingo-home-screen-design/ （官方，2022）
[^duo-3]: Duolingo 博客，Leagues，https://blog.duolingo.com/duolingo-leagues-leaderboards/ （官方）
[^duo-4]: We Got This Covered，https://wegotthiscovered.com/fyi/all-duolingo-leagues-explained/ （第三方）
[^game-1]: Game Developer，Introducing mechanics，https://www.gamedeveloper.com/design/game-design-introducing-mechanics （2018）
[^game-2]: Wayline，https://www.wayline.io/blog/tutorials-as-onboarding-level-design （设计博客）
[^notion-1]: Candu，https://candu.ai/blog/how-notion-crafts-a-personalized-onboarding-experience-6-lessons-to-guide-new-users （厂商博客）
[^notion-2]: Notion 帮助中心，Writing and editing basics，https://www.notion.com/help/writing-and-editing-basics （官方）
[^notion-3]: Notion 模板页用户评论，https://www.notion.com/vi/templates/product-design-rfc （用户评论）
[^linear-1]: Supademo，https://supademo.com/content/plg-onboarding-gallery/linear （二手）
[^linear-2]: Knock，https://knock.app/blog/how-to-design-great-keyboard-shortcuts （二手）
[^arc-1]: TidBITS，https://tidbits.com/2023/10/06/arc-web-browser-introduces-focused-ai-features/ （媒体，2023）
[^arc-2]: Onboard Me，https://onboardme.substack.com/p/how-arc-browser-introduces-ai-max-feature （单人分析）
[^dash-1]: Uxcel 案例，https://app.uxcel.com/showcase/healthflow-designing-a-simple-and-insightful-wellness-dashboard-755 （案例）
