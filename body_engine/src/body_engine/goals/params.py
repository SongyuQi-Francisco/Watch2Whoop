"""Bedtime goal parameters and their basis. body-management.md 6.2 is the routine adjustment plan; bump RULES_VERSION in rules/params.py whenever a parameter that affects results changes."""

# ---------- pace of moving earlier ----------
# The body clock can shift only about 15–30 minutes per day (with light exposure + a fixed routine; pushing later is easier than earlier), and a forced overnight change causes insomnia and daytime sleepiness.
# body-management.md 6.2.2 "shift gradually: 30–60 minutes earlier every 2–3 days, never force it overnight". The default is the slower, steadier 20 minutes earlier every 3 days
# (≈ 7 minutes a day, well below the 15–30 minute ceiling, leaving room for a disrupted routine); the user can speed it up on the Today page.
DEFAULT_PACE_MIN = 20
DEFAULT_STEP_DAYS = 3
PACE_RANGE = (5, 60)          # at most 60 minutes per step (the 6.2.2 ceiling)
STEP_DAYS_RANGE = (1, 14)

# ---------- computing progress ----------
WINDOW_NIGHTS = 7             # average bedtime / wake time over the "last 7 nights"
MIN_NIGHTS = 3                # at least 3 of the 7 nights need data to compute an average
TREND_NIGHTS = 14             # weekly trend: linear-regression slope of bedtime over the last 14 nights × 7
MIN_TREND_NIGHTS = 5
ON_TARGET_TOL_MIN = 15        # falling asleep no more than 15 minutes after that night's target counts as on target
DEFAULT_DURATION_MIN = 480    # without a sleep need from scoring, use 8 hours (6.2.8 targets 7–9 hours; scoring baseline 8h)

# ---------- rule reminders (rules/sleep_goal.py) ----------
DEVIATION_MIN = 30            # more than 30 minutes later than that night's target counts as a deviation
DEVIATION_NIGHTS = 3          # 3 consecutive nights of deviation -> remind
# Pre-sleep arrangements (body-management.md 6.2):
SCREEN_OFF_BEFORE_MIN = 60    # 6.2.7 dim the lights and use the phone less in the hour before bed
CAFFEINE_BEFORE_MIN = 8 * 60  # caffeine half-life is about 5–6 hours, so stop 8 hours before the target; 6.2.4 says "none after 12 noon"; take whichever is earlier
CAFFEINE_NOON_CUTOFF_MIN = 12 * 60
DINNER_BEFORE_MIN = 3 * 60    # journal item late_dinner "main meal within 3 hours of bed" counts as too late; 6.2.7 if hungry, have a glass of milk
TRAINING_BEFORE_MIN = 3 * 60  # 6.2.5 finish training at least 3 hours before bed where possible
MORNING_LIGHT = "起床后 30 分钟内晒太阳 10–20 分钟"   # 6.2.3 the most effective signal for shifting the body clock
MORNING_LIGHT_EN = "10–20 min of sunlight within 30 min of waking"   # English of the line above (issue #42)

# ---------- nutrition goals (goals/nutrition.py, issue #31) ----------
# Plausibility ranges for one day's set (anything outside is almost certainly a unit / typing mistake, not a plan).
NUTRITION_RANGES = {"calories": (800, 6000), "protein_g": (0, 400), "carbs_g": (0, 900), "fat_g": (0, 300),
                    "sat_fat_max_g": (0, 100), "added_sugar_max_g": (0, 200), "fiber_min_g": (0, 100)}
# 4 kcal/g protein and carbs, 9 kcal/g fat (Atwater factors). A set whose macros miss its calories by more than this
# share still saves (alcohol, rounding), with a warning.
MACRO_KCAL_TOL = 0.15
GOAL_BACKDATE_DAYS = 366      # corrections may start up to a year back (history stays; reports / adherence follow it)
GOAL_AHEAD_DAYS = 90          # a planned change may start up to 90 days ahead
