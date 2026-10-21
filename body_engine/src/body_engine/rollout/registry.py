"""Declared feature flags: key -> one-line description (shown by `body-engine flags list`).

A key must be declared here before code checks it (flags.enabled raises on an unknown key, so a typo fails in tests) or
`flags set` accepts it. Declare it in the same PR as the feature; its database row is created as `off` the first time the
flags are listed or set, so a deploy never turns anything on by itself. Remove the key (and the checks) once the feature
is on for everyone and the old path is gone.
"""
FLAGS: dict[str, str] = {
    # "new_food_page": "饮食页新版（2026-11）",
    "water": "饮水记录：今天页喝水卡片、log_water、下午提醒、Apple 健康饮水（#125）",
}
