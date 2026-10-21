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
    "intro_v2": "新手引导新视觉（#95）；关着时用 #63 的旧版",
}
