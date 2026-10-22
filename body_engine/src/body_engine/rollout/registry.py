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
    "memory_recent": "长期记忆「近况」：add_observation 的 until（会过期的笔记）+「我」页近况块（#112）；关着时 until 不生效、按长期事实记",
}
