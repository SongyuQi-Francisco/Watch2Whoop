"""The AI-first page (#279, flag ui_ai_first, docs/ui-ai-first-redesign.md v0.4, sections 4–5): which Today cards go on the
page, in what order and why, and the findings shown in the first screen. Every threshold says where it comes from; they only
choose what to show first, they never change a score."""

FEATURE = "ui_ai_first"

# The cards of Today that can be on "my Today", by their data-card key (web/static/app.js), in the old page's order (the default)
MODULES = ("health_monitor", "strain_coach", "sleep", "sleep_goal", "training", "muscle_load", "journal")
MAX_MODULES = 6   # design v0.4, D5: at most six; the rest stay in 全部功能

# Focus the user picks in the first screen (D4) -> the cards that come first
FOCUS = {
    "fat": ("training",),                         # 减脂: food and training on one card
    "muscle": ("training", "muscle_load"),        # 增肌
    "sleep": ("sleep_goal", "sleep"),             # 睡得更好
    "recover": ("health_monitor", "strain_coach"),  # 训练和恢复
    "markers": ("health_monitor",),               # 健康指标
}

# "Used": a tool any AI called for the user in the last USED_DAYS days (assistant_audit, every channel) -> its card
USED_DAYS = 30
TOOLS = {
    "log_food": "training", "log_meal_template": "training", "log_recipe": "training", "get_nutrition": "training",
    "log_workout": "training", "get_training_log": "training", "get_training_day": "training", "get_training_calendar": "training",
    "get_week_plan": "training", "swap_exercise": "training",
    "set_sleep_goal": "sleep_goal", "get_sleep_goal": "sleep_goal", "get_sleep_coach": "sleep_goal",
    "get_sleep": "sleep", "get_sleep_details": "sleep",
    "get_strain_coach": "strain_coach", "get_health_monitor": "health_monitor",
    "log_journal": "journal", "get_journal": "journal", "analyze_journal": "journal",
}

# ---- findings (section 4.3): rules over daily_scores / sleep_sessions, at most OVERVIEW_FINDINGS in the first screen
OVERVIEW_FINDINGS = 3
RECENT_DAYS = 7          # "this week"
BASE_DAYS = 60           # personal baseline window, as in scores / trends (trends/params.py BASELINE_DAYS)
MIN_BASE_DAYS = 14       # a baseline needs 14 days (trends/params.py MIN_BASELINE_DAYS; recovery needs 14 nights of HRV)
MIN_RECENT_DAYS = 3      # at least 3 of the last 7 (trends/params.py HRV_STATUS_MIN_DAYS)
HRV_CHANGE = 0.10        # 7-day mean HRV 10 % off the 60-day mean: about one day-to-day SD of ln HRV, the width of the
                         # "balanced" band in trends (HRV_BAND_SD = 1 SD; Plews et al. 2013 use the 7-day ln rMSSD mean the same way)
RECOVERY_DAYS = 30       # recovery this month vs the month before
RECOVERY_CHANGE = 5      # points (recovery 0–100); smaller is within week-to-week noise of a 30-day mean
SLEEP_SHORT_MIN = 30     # 7-night mean sleep at least 30 min under the night's need (scores: sleep need = 8 h + debt + strain)
STRAIN_HIGH = 14.0       # strain ≥ 14 counts as a hard day (the 14–18 "high" band of the 0–21 scale, app.js strainSheet)
STRAIN_STREAK = 3        # three hard days in a row before saying so
BEDTIME_NIGHTS = 14      # bedtime spread over the last 14 main sleeps ...
BEDTIME_SPREAD_MIN = 60  # ... SD ≥ 60 min (sleep regularity research treats > 1 h of night-to-night shift as irregular;
                         # Phillips et al. 2017 SRI; trends uses the same nights for its regularity card)
BEDTIME_MIN_NIGHTS = 10  # trends/params.py CHRONO_MIN_NIGHTS
