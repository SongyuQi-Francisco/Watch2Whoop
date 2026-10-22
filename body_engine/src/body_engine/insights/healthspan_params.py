"""Body-age parameters moved to the openbodyscore package (packages/openbodyscore/src/openbodyscore/healthspan_params.py, issue #115);
re-exported here so existing imports keep working. Only the improvement tips (product texts shown to users) stay here."""
from openbodyscore.healthspan_params import *  # noqa: F401,F403
from openbodyscore.healthspan_params import override  # noqa: F401

# ---------------------------------------------------------------- strength-training record coverage (issue #210)
# Data-quality heuristics, not part of the score: they only label the strength item 「记录可能不全」 (coverage
# "possibly_incomplete") and change its tip; the minutes, the dose-response curve and the conversion stay the same.
# Design choices, first version, not validated. Evidence that the user trained but the records miss it:
# - the journal (journal_entries) has a strength item (STRENGTH_JOURNAL_KEYS, value > 0) on at least STRENGTH_JOURNAL_MISSED_MIN
#   days of the last STRENGTH_COVERAGE_WEEKS weeks that have no strength record;
# - or the active training plan (training_plans, started before that window) plans days_per_week sessions, and fewer than
#   STRENGTH_PLAN_MIN_SHARE of them (days_per_week x weeks) have a strength record.
STRENGTH_COVERAGE_WEEKS = 4
STRENGTH_JOURNAL_KEYS = ("training_upper", "training_legs")   # the default journal items (records/defaults.py)
STRENGTH_JOURNAL_MISSED_MIN = 2
STRENGTH_PLAN_MIN_SHARE = 0.5

# ---------------------------------------------------------------- improvement tips (<= 20 characters)
TIPS = {
    "sleep_short": "每晚多睡 30–60 分钟",
    "sleep_long": "睡太久，固定起床时间",
    "sleep_ok": "睡眠时长合适，保持",
    "sleep_regularity": ("作息规律，保持", "固定起床时间，误差 ≤ 1 小时"),
    "steps": ("步数够了，保持", "每天多走 2000 步"),
    "mvpa": ("运动量达标，保持", "每周加 2 次 30 分钟快走或爬坡"),
    "strength": ("力量训练规律，保持", "每周力量训练 2 次以上"),
    "strength_incomplete": "记录可能不全，先补记力量训练",   # coverage "possibly_incomplete" (issue #210)
    "vo2max": ("心肺能力好，保持", "加 Z2 有氧和每周 1 次间歇"),
    "rhr": ("静息心率低，保持", "多做有氧、少熬夜"),
    "body_comp": ("体重合适，保持", "按计划减脂"),
    "body_comp_goal": "按计划减到目标体重 {kg} kg",   # when the user's own goal weight (profile) is below their weight
    "activity": ("活动量充足，保持", "每天多走 2000 步或多练一次"),
}
