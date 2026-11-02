"""Measuring conditions a weigh-in can carry. The Today page (web/static/weight.js) draws one chip per key with its own tr() label;
tests/test_weightlog.py checks the keys and the Chinese texts there agree with this table. The AI reads the Chinese label."""

# key -> label the AI reads. Order = chip order on the page.
CONTEXT_TAGS = {
    "fasting": "早上空腹",
    "after_toilet": "如厕后",
    "after_meal": "饭后",
    "after_workout": "运动后",
    "before_bed": "睡前",
    "clothed": "穿衣",
    "unclothed": "未穿衣",
}
EXCLUSIVE = (("clothed", "unclothed"),)   # at most one of each group
MAX_NOTE = 120                            # characters
RECENT_DEFAULT = 10                       # records on the weigh-in sheet
RECENT_MAX = 30
RECENT_WINDOW_DAYS = 180                  # how far back the recent list looks
