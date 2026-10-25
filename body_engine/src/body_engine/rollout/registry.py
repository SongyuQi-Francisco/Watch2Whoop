"""Declared feature flags: key -> one-line description (shown by `body-engine flags list`).

A key must be declared here before code checks it (flags.enabled raises on an unknown key, so a typo fails in tests) or
`flags set` accepts it. Declare it in the same PR as the feature; its database row is created as `off` the first time the
flags are listed or set, so a deploy never turns anything on by itself. Remove the key (and the checks) once the feature
is on for everyone and the old path is gone.
"""
FLAGS: dict[str, str] = {
    # "new_food_page": "饮食页新版（2026-11）",
    "friends": "朋友页：圈子和共享、朋友 tab、排行榜（docs/friends-social-plan.md）；同意书至少 2026-10-22.2",
    "meal_merge": "饮食当天明细多选：存成快捷模板 / 合并成一条（#93、#97）",
    "water": "饮水记录：今天页喝水卡片、log_water、下午提醒、Apple 健康饮水（#125）",
    "me_settings": "「我」页精简 + 「设置」子页面（#140）；关着时保持旧版",
    "intro_v2": "新手引导新视觉（#95）；关着时用 #63 的旧版",
    "pantry_view": "采购：今天页「家里存货」、一键「用完了」、清单上「家里有 x」（#102）；关着时不显示",
    "grocery_staples": "采购：常买清单（按购买历史推荐、周清单自动加上到期的常买、set_staple）（#103）；关着时没有常买",
    "pantry_auto": "采购：记餐自动扣存货（每人还有自己的开关，默认关）+ 保质期提醒（#104）；关着时不扣、不提醒",
    "jarvis_quick": "Jarvis 对话面板按当前页面显示 2–3 个快捷问题（#101）；关着时不显示",
    "training_day_v2": "训练页新版：今天的计划动作卡（指导、推荐组数 / 重量、禁用提示）+ 逐组记录并提交（FB-5）；关着时是旧的列表",
    "memory_recent": "长期记忆「近况」：add_observation 的 until（会过期的笔记）+「我」页近况块（#112）；关着时 until 不生效、按长期事实记",
    "jarvis_ui_v2": "Jarvis 对话面板新版式：和今天页同一套标题栏、卡片、输入框（#53）；关着时保持现在的样子",
    "ai_web": "今天页 AI 联网查价：托底 AI 和兼容接口用服务器的 web_search / web_fetch（遵守 robots.txt、碰到拦截就停），托底 AI 加上存商品和出清单的工具（#150）；关着时不能联网",
    "mcp_web": "外接 AI 联网：MCP 客户端（Claude / ChatGPT / Codex…）和身体管家也能用服务器的 web_search / web_fetch（find_tools 采购组里，和 ai_web 同一套规则："
               "每人每天次数、搜索费用记进 ai_usage 并算进本人的 Jarvis 月额度、所有人合计月上限、robots.txt、拦截即停、不抓 Coles / Woolworths、不读内网），"
               "没配搜索 key 时只有 web_fetch；关着时外接 AI 只能用它自己的联网",
    "sleep_detail": "睡眠详情页：点今天页的睡眠卡 / 睡眠时长打开这晚的时长、阶段时间轴、睡眠分构成、和自己平均比、夜间心率 / HRV / 呼吸 / 血氧 / 手腕温度、规则要点和「让 Jarvis 分析」；关着时是原来的弹层",
    "exercise_add_to_plan": "动作库 / 动作详情的「加入训练计划」：加到某个训练组（每次都有）或只加到某一天，禁用动作拦住（#154）；关着时详情页没有这个按钮（返回按钮不受开关控制）",
    "hevy_import": "「我 → 设置 → 导入 → 从 Hevy 导入」：上传 Hevy 导出的训练 CSV，写进训练记录（workouts + training_session_logs），动作名对到动作库，重算肌肉负荷（#161）；关着时没有入口、接口 404",
    "records_v2": "记录页去掉「日记」分页（只留 训练 | 饮食）：今天页新增「日记」卡（点开编辑当天的行为 / 补剂打卡 / 身体事件）、趋势页新增「行为影响」卡；关着时保持 日记 | 训练 | 饮食",
    "offline_mode": "离线使用：离线时显示本机缓存的今天页 / 趋势 / 记录 / 我，喝水、体重、训练逐组先存本机、联网后按顺序提交，AI 对话等置灰（#77）；关着时和以前一样",
    "data_status": "「我 → 数据状态」：每类健康数据服务器最近收到的时间、近 7 天的天数 / 条数，没收到的类型给原因和去 iOS 设置打开的步骤（#163）；关着时没有这个入口（接口和 app 1.0.7 的手机同步页不受开关控制）",
    "jarvis_rich": "Jarvis 回答里的互动卡片：训练卡、饮食卡、指标小图、快捷按钮（show_card + 记录后自动附卡，#53）；关着时纯文字",
    "jarvis_cards_v2": "Jarvis 卡片第二版（需要 jarvis_rich 也开着）：饮食目标进度（热量环 / 蛋白环）、推荐餐、食谱、食谱列表、快捷记餐模板、采购清单卡，饮食卡上点一条改份量 / 删除、刚记完能撤销；恢复 / 负荷 / 压力 / 身体年龄各项贡献 / 任意指标趋势（和上一期比）/ 体重 / 化验单项历史 / 补剂打卡 / 饮水 / 日记分析 / 周报月报 / 心率区间 / VO₂max / 健康监测 / 数据状态卡，睡眠卡补上效率、入睡耗时、夜醒和睡眠分构成；托底 AI 查了这些数据自动附卡；关着时和 jarvis_rich 一样",
    "product_card": "Jarvis 商品卡（FB-12，需要 jarvis_rich 和 jarvis_cards_v2 也开着）：保存商品、问某个商品的价格 / 营养时 Jarvis 发一张卡——每 100g（和每个 / 每包）的热量和蛋白碳水脂肪分布、各超市用户自己记录的最近价格和多久以前记的、价格走势、家里库存、「记一次」和「拍价签 / 小票」按钮；只用用户自己记的小票 / 价签截图 / 商品库 / 条码查到的营养，不访问超市网站，没有的块不显示；关着时 show_card 没有 product 类型、也不自动出商品卡",
    "friend_card_v2": "朋友页成员卡片新版：今天的状态为主、带星期的近 7 天恢复条（点一天看详情）、未共享 / 未同步状态、自己的卡片标「朋友看到的你」；关着时保持旧版",
    "exercise_opengym": "动作库扩充（#248）：把 openGym 的动作文字（约 5,400 个，英文名 / 器械 / 肌群 / 描述 / 步骤，MIT 或 AGPL-3.0，只要文字不要图）加进动作库搜索、动作详情和替换；关着时这些动作搜不到、详情打不开（库里的数据和已记的训练不受影响），只剩原来的约 880 个。应急开关：上线后对所有人打开",
    "exercise_zh_machine": "动作库机翻中文（#248）：只有英文的动作（free-exercise-db 和 openGym 导入的约 6,100 个）显示离线批量机器翻译的中文名、要点和步骤（数据在 training/translations/，按术语表统一译名，运行时不调 AI），中文搜索也能搜到；人工写的中文名和禁用 / 小心标记不受影响；关着时这些动作照旧显示英文。上线后对所有人打开",
    "exercise_curated_steps": "精选动作的分步步骤（#248）：原来只有一句要点的约 95 个精选动作，详情里显示中英双语的分步步骤（AI 按人工要点写、人工抽查过，数据在 training/translations/curated_steps.json；人工的中文名、要点和禁用 / 小心标记不动）；关着时照旧只有一句要点",
    "exercise_media_licensed": "动作演示图加上核对过许可证的图并逐张署名（#248）：wger、Everkinetic 的图（CC-BY-SA，body-engine exercises-media-fetch 下载）排在前面，free-exercise-db 的图照旧显示（标「Unlicense（上游声明，图片原始出处不明）」）；详情里图下写来源，没有图的显示「暂无演示」。只看「所有人」这一档（版权问题，不按人分批）；关着时和以前一样只显示 free-exercise-db 的图、不写来源",
    "exercise_media_free_db_off": "一键停用 free-exercise-db 的演示图（#248，商用前用）：这批图上游说是网上抓的、原始出处不明；打开后它们全部不再显示，只剩 wger / Everkinetic 的图，没有图的动作显示「暂无演示」、按质量规则不推荐（文件还在，关掉即恢复）。只看「所有人」这一档。内测阶段保持关闭（负责人 2026-10-25 决定）",
    "exercise_quality_gate": "动作库质量分级（#248，docs/exercise-library-quality.md）：搜索、浏览和替换动作只给达标的动作（中英名称、文字、合法演示图、主肌群齐全、不是重复）；不达标的留在库里，按名字精确搜仍能找到，已记的训练照常显示，自己设置过的动作也照常显示；关着时和以前一样",
    "static_bundle": "静态数据包（#248）：GET /v1/static-bundle/manifest、/v1/static-bundle?since=N（动作库 + 标签 + 讲解词条 + 训练分化模板的带版本号数据包和增量），app 把它打进安装包、先读本地再按版本号更新；关着时接口 404、app 只用包里的版本（打包命令 body-engine static-bundle-pack 不受开关控制）。上线后对所有人打开",
    "muscle_load": "肌肉负荷图：记录 → 训练顶部 + 今天页小卡的 2D 人体图（按个人资料性别；今天 / 本周 / 近 7 天的有效组数和训练量、按推荐区间着色、运动按时长 × 强度折成等效组数一起着色（每种运动都映射到肌群）、点肌肉看详情和推荐动作）、「让 Jarvis 分析我的肌群训练」「让 Jarvis 补进动作库」、动作库「我的动作」（每人自己的自定义动作）、「我 → 训练设置」（重点肌群、体态目标）、Jarvis 的 get_muscle_load / add_custom_exercise / delete_custom_exercise 和肌肉图卡片；关着时没有这些入口",
    "circle_feed": "朋友页「动态」：圈子成员当天的训练帖（给圈子勾选了「训练」共享的人自动发，一天一条、内容实时更新、不能删除，退出圈子后对方看不到）+ ❤️ 点赞 + 文字评论（评论者可删、可举报）+ 红点；另需 friends 开关；关着时朋友页只有 成员 | 排行榜",
    "ai_cards": "Jarvis 额度卡：「我 → AI 设置」和 Jarvis 额度旁显示卡数、用卡（重置卡清零本月已用额度、扩充卡加额度）、额度用完时提示用卡、收到卡的站内通知；定期发放规则只发给开着的人（#182）；关着时看不到卡、不能用卡（管理员 / 奖励照样能发，开了以后能看到）",
    "notifications": "通知：今天页右上角红点、通知列表、通知设置（按类型开关、免打扰），以及 APNs 推送是否发出（#175）；关着时只写站内通知、不推送、页面上没有入口（设备登记接口不受开关控制）",
    "memory_hygiene": "长期记忆整理一期（#113）：add_observation 先找同主题的相似笔记（有就不新建、返回 similar）、update_profile_card 返回旧值提示合并、「我 → 长期记忆」按主题分组 +「x 天前确认」+「还准确」/ 删除 / 已归档恢复；二期：get_memory_review / apply_memory_changes + playbook memory_review（重复 / 过时 / 同主题的条目，用户同意后合并、归档、恢复）、每晚 memory-review-run、get_briefing 里每月一次的整理提醒、「长期记忆」里的「整理」按钮；关着时和以前一样（新字段照样写，撤回照样能用）",
    "journal_regression": "趋势页「行为影响」卡用日记分析第二版：OLS 控制前一天负荷、睡眠时长、星期几，Newey-West 标准误，多个行为一起做 BH 校正（#118）；另需 records_v2 开关；关着时是两组均值比较（第一版）",
    "ai_parity": "Jarvis 权限一致 + 不说底层软件名（T0）：托底 AI 能用其他通道对本人数据的全部工具（同意书排除的心电图 / 逐拍心跳 / 家人数据除外，"
                 "每次只带 core + 当前页第一组，其余 find_tools）；页内对话和 MCP 里给 AI 看的文字、模型输出、存库的对话、报错不出现 SparkyFitness，"
                 "改叫「底层数据库」/ our database；数据状态页同样；关着时和以前一样",
    "circles_by_circle": "朋友页按圈子分开（#269 P1，docs/circles-redesign.md 第 2 节）：顶部切换条「全部 + 每个圈子」，选了圈子后成员、动态、排行榜只含这个圈子的人、"
                         "只按他们在这个圈子里勾的范围显示，帖子下只显示这个圈子的人的 ❤️ 和评论；「全部」没有排行榜；get_friends_overview 多 circle 参数；"
                         "关着时和以前一样（所有圈子混在一起）",
    "circle_prefs": "多圈子管理（#269 P2，迁移 765，docs/circles-redesign.md 第 3 节）：每个人给自己的圈子置顶（最多 3 个，切换条最前面）、静音（不发 ❤️ / 评论通知、"
                    "不算红点、「全部」动态里不出现）、归档（从切换条和「全部」里隐藏、也静音，「已归档」里能恢复；照常共享，页面明确提示）；"
                    "切换条和圈子列表按最近动静排序；圈子 ≥ 6 个时圈子列表能搜索（圈名、成员名 / 备注）；邀请收件箱显示人数和默认共享、「全部忽略」；"
                    "切换条需要 circles_by_circle 也开着；关着时和以前一样（接口 404）",
    "circle_join_trail": "圈主看到的圈子成员列表里每人多一行「由谁邀请 · 通过什么（邀请链接 / 推荐码 / 圈子激活码 / 建圈） · 哪天入圈」（#269，迁移 745；"
                         "入圈记录一直在写，开关只管显示，成员之间永远看不到）；关着时成员列表和以前一样",
    "member_remarks": "给圈子成员设备注（只有自己看得到，按人设）：成员卡片「⋯」→「设置备注」，成员卡片、排行榜、训练动态的帖子和评论、圈子成员列表、只读查看对方时顶部都显示备注 + 小字原名，get_friends_overview 返回 nickname（#212）；关着时显示原名、没有入口、接口 404",
    "trends_v2": "趋势页指标：每个类别的标题行可折叠 / 展开（收起后仍显示项数和状态，状态记在本机），类别里的指标卡手机两列、电脑按宽度多列（#195）；关着时保持每个指标一整行",
    "consent_one_tap": "一键同意：第一次打开只有「开始使用」+ 一行小字（《数据与隐私说明》《Jarvis AI 说明》可点开看全文），点一下同时记下"
                       "数据同意书和托底 AI（Jarvis）的同意（method one_tap，版本都是当前的），之后第一次用 Jarvis 不再弹确认；同意书升版本时"
                       "的重新确认和 Jarvis 说明的确认也是一键。两份正文和版本号不变。要对还没同意的新用户生效，用 --state all；关着时和以前一样",
    "avatars": "头像：「我 → 个人资料」上传 / 更换 / 删除个人头像（拍照或相册、裁成方形），圈主在圈子设置里上传 / 更换 / 删除圈子 logo（#200）；关着时没有上传入口、上传接口 404（已经存在的头像和管理员设的圈子头像照常显示）",
    "sync_center": "app 的「同步中心」：手机同步 + 数据更新 + 数据状态合成一页（总体状态、权限、按类型比「手机最新 / 服务器最新」、"
                   "立即重新同步 7 / 30 / 90 天 / 全部 / 自定义天数）；今天页里这三项在 app 内合成一个入口；重新同步接口 /v1/ingest/resync；"
                   "关着时和以前一样（接口 404）",
    "app_types_pause": "暂停 app 直传的补充健康类型（站立、锻炼分钟、日照、体能消耗、睡眠手腕温度、音量、步行心率、上下楼速度、步行稳定性，"
                       "rawdata/hk_types.py）：开着时服务器不收、app 不读（锚点不动，关掉后补传）。这是应急开关：默认关 = 正常收",
    "apple_sync_engine": "自建 Apple 健康同步引擎（#219，docs/apple-sync-engine.md）：服务器接收 app 同步引擎上传的原始样本、"
                         "训练、睡眠阶段、15 分钟统计桶、按范围重新同步和同步记录（rawdata/engine.py），GET /v1/ingest/healthkit "
                         "的 engine 块告诉 app 收哪些字段和类型、同步模式 shadow（只存着做对比，不影响分数）；关着时 = ow 模式，"
                         "这些字段一律 409 不收，旧版 app 不受影响",
    "own_food_store": "饮食 / 食物库 / 体重 / 训练记录双写（#252 第 2 期，docs/own-food-weight-storage.md）：每次写 SparkyFitness 后，"
                      "把涉及的日期读回来同步进 Body Engine 自己的表（food_entries、foods、body_measurements、logged_workouts…），"
                      "同步失败只记日志、不影响这次写；还没有任何地方读这些表。关着时和以前完全一样（不多发请求、不碰新表）",
    "jarvis_scope_guard": "Jarvis 话题守门（#170，只管托底 AI，用户自己的 AI 不受影响）：明显跑题且费钱的请求（写代码、作业、长文写作 / 长篇翻译、"
                          "和健康无关的通用话题）由服务端规则直接用固定的礼貌说明回复，不调模型、不扣额度、不调工具，托底 AI 的系统提示里也加上话题范围；"
                          "健康、训练、饮食、睡眠、化验、app 用法、反馈和简短寒暄照常；关着时和以前完全一样",
    "jarvis_orbit": "Jarvis 入口改版（#53）：底部居中的圆形图标键（Lucide orbit，无文字）；托底 AI（Jarvis）的人点一下沿圆环展开「对话 / 语音 / 拍照 / 快记」，"
                    "按住直接说话（往左上滑取消、往右上滑锁定），回答在半屏里（文字 + 卡片，「继续对话」进完整对话页），识别后按个人词表纠错"
                    "（POST /v1/assistant/voice-fix、transcribe?fix=1）；app 有本机识别（Watch2WhoopFeatures.voice，1.0.8+）时边说边出字。"
                    "选了自己 AI 的人只换按钮外观，点一下和以前一样；关着时是原来的「Jarvis」胶囊键",
    "jarvis_dial": "Jarvis 键新效果（PR #294，需要 jarvis_orbit 也开着）：四个子键像仪表盘一样从 Jarvis 键左右两侧沿向上拱的弧线展开并由小变大"
                   "（展开约 200ms、收起 150ms，不回弹，只动 transform / opacity），按下 80ms 内有反馈；黑白单色线条（浅色模式黑、深色模式白，"
                   "没有彩色、渐变、光晕，录音时键反色），Lucide 图标统一描边；减少动态效果时直接显示。先只给负责人自己开；关着时是现在的圆环展开样式，完全不变",
    "jarvis_voice_direct": "Jarvis 按住说话松开直接发送（需要 jarvis_orbit 也开着）：开之前先改托底 AI 同意书第 4 条和数据同意书「语音输入」"
                           "（现在写的是「确认文字后才发送」）；关着时松开后先显示识别出的文字，可以改，点「发送」才发",
    "boot_api": "启动提速（#247 第 3.2 节）：今天页启动时一次 POST /v1/web/boot 拿到上次启动用到的全部只读接口（/v1/web/me、今天页各块、"
                "health-history 等，服务器内部逐个执行原接口、各自鉴权、各自出错），页面不再串行等 3 层；关着时接口 404、页面逐个请求",
    "cached_first_paint": "先显示上次的数据（#247 第 3.5 节，需要 offline_mode 也开着）：打开今天页时先用本机副本（12 小时内、今天的）画出来，"
                          "顶部标「更新中」，新数据回来后替换；退出登录 / 登录过期时副本清空；关着时和以前一样等数据回来再画",
    "tab_prefetch": "切 tab 提速（#247 第 3.3 节）：趋势 / 饮食 / 我 的两层请求并成一层；今天页画完后浏览器空闲时预取其他 tab 的数据"
                    "（最多 30 个请求，蜂窝网络 / 省流量模式 / 慢网不预取）；关着时和以前一样切过去才请求",
    "open_signup": "开放注册（全局开关，只认 --state all）：活跃账号数低于 account/params.py 的 OPEN_SIGNUP_MAX_ACTIVE（默认 200）时，"
                   "新注册的账号直接开通（和兑换激活码后一样开户），已经在等激活码的账号下次访问时自动开通；到上限后新账号自动回到"
                   "要激活码；同一 IP 每天最多 OPEN_SIGNUP_PER_IP_DAY 个；激活码 / 推荐码 / 圈子码照样有效；同意书照旧。"
                   "管理命令 body-engine open-signup status / list。关着时和以前一样，注册后要激活码",
    "optimistic_writes":"写操作先显示（#247 第 3.4 节）：快捷记餐、喝水、体重点下去立刻显示已记，后台提交，失败撤回并提示；"
                         "记餐 / 撤销的返回带上今天的饮食块，不再整份重拉 /today；关着时和以前一样等服务器回来",
}
