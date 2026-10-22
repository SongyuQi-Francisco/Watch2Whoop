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
    "sleep_detail": "睡眠详情页：点今天页的睡眠卡 / 睡眠时长打开这晚的时长、阶段时间轴、睡眠分构成、和自己平均比、夜间心率 / HRV / 呼吸 / 血氧 / 手腕温度、规则要点和「让 Jarvis 分析」；关着时是原来的弹层",
    "exercise_add_to_plan": "动作库 / 动作详情的「加入训练计划」：加到某个训练组（每次都有）或只加到某一天，禁用动作拦住（#154）；关着时详情页没有这个按钮（返回按钮不受开关控制）",
    "hevy_import": "「我 → 设置 → 导入 → 从 Hevy 导入」：上传 Hevy 导出的训练 CSV，写进训练记录（workouts + training_session_logs），动作名对到动作库，重算肌肉负荷（#161）；关着时没有入口、接口 404",
    "records_v2": "记录页去掉「日记」分页（只留 训练 | 饮食）：今天页新增「日记」卡（点开编辑当天的行为 / 补剂打卡 / 身体事件）、趋势页新增「行为影响」卡；关着时保持 日记 | 训练 | 饮食",
    "offline_mode": "离线使用：离线时显示本机缓存的今天页 / 趋势 / 记录 / 我，喝水、体重、训练逐组先存本机、联网后按顺序提交，AI 对话等置灰（#77）；关着时和以前一样",
    "data_status": "「我 → 数据状态」：每类健康数据服务器最近收到的时间、近 7 天的天数 / 条数，没收到的类型给原因和去 iOS 设置打开的步骤（#163）；关着时没有这个入口（接口和 app 1.0.7 的手机同步页不受开关控制）",
    "jarvis_rich": "Jarvis 回答里的互动卡片：训练卡、饮食卡、指标小图、快捷按钮（show_card + 记录后自动附卡，#53）；关着时纯文字",
    "friend_card_v2": "朋友页成员卡片新版：今天的状态为主、带星期的近 7 天恢复条（点一天看详情）、未共享 / 未同步状态、自己的卡片标「朋友看到的你」；关着时保持旧版",
    "muscle_load": "肌肉负荷图：记录 → 训练顶部 + 今天页小卡的 2D 人体图（按个人资料性别；今天 / 本周 / 近 7 天的有效组数和训练量、按推荐区间着色、运动按时长单独一层斜纹、点肌肉看详情和推荐动作）、「让 Jarvis 分析我的肌群训练」「让 Jarvis 补进动作库」、动作库「我的动作」（每人自己的自定义动作）、「我 → 训练设置」（重点肌群、体态目标）、Jarvis 的 get_muscle_load / add_custom_exercise / delete_custom_exercise 和肌肉图卡片；关着时没有这些入口",
    "circle_feed": "朋友页「动态」：圈子成员当天的训练帖（给圈子勾选了「训练」共享的人自动发，一天一条、内容实时更新、不能删除，退出圈子后对方看不到）+ ❤️ 点赞 + 文字评论（评论者可删、可举报）+ 红点；另需 friends 开关；关着时朋友页只有 成员 | 排行榜",
    "ai_cards": "Jarvis 额度卡：「我 → AI 设置」和 Jarvis 额度旁显示卡数、用卡（重置卡清零本月已用额度、扩充卡加额度）、额度用完时提示用卡、收到卡的站内通知；定期发放规则只发给开着的人（#182）；关着时看不到卡、不能用卡（管理员 / 奖励照样能发，开了以后能看到）",
    "notifications": "通知：今天页右上角红点、通知列表、通知设置（按类型开关、免打扰），以及 APNs 推送是否发出（#175）；关着时只写站内通知、不推送、页面上没有入口（设备登记接口不受开关控制）",
    "ai_parity": "Jarvis 权限一致 + 不说底层软件名（T0）：托底 AI 能用其他通道对本人数据的全部工具（同意书排除的心电图 / 逐拍心跳 / 家人数据除外，"
                 "每次只带 core + 当前页第一组，其余 find_tools）；页内对话和 MCP 里给 AI 看的文字、模型输出、存库的对话、报错不出现 SparkyFitness，"
                 "改叫「底层数据库」/ our database；数据状态页同样；关着时和以前一样",
    "trends_v2": "趋势页指标：每个类别的标题行可折叠 / 展开（收起后仍显示项数和状态，状态记在本机），类别里的指标卡手机两列、电脑按宽度多列（#195）；关着时保持每个指标一整行",
}
