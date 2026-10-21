"""Training history parameters: type display names (Chinese), dedup, intensity thresholds (docs/system-design.md §15). Bump TRAINING_VERSION whenever a parameter that affects results changes."""
from datetime import timedelta

TRAINING_VERSION = "v1.0-2026-10-17"

# ---------- type -> display name (Chinese) ----------
# OW canonical types (vendor/open-wearables/backend/app/schemas/enums/workout_types.py). Types not listed fall back to OW's English name.
# walking: treadmill incline walking is usually recorded as "walking" in HealthKit, with no incline information. body-management.md 4.1 prefers incline climbing for cardio,
# but the watch alone cannot tell climbing from a stroll, so the name is "步行 / 爬坡" (walking / incline) and we do not guess; if SparkyFitness logged a climb that day, its name is used when merging.
TYPE_NAMES = {
    "strength_training": "力量训练", "walking": "步行 / 爬坡", "running": "跑步", "treadmill": "跑步机",
    "basketball": "篮球", "swimming": "游泳", "pool_swimming": "泳池游泳", "open_water_swimming": "公开水域游泳",
    "cycling": "骑行", "indoor_cycling": "室内单车", "stair_climbing": "爬楼梯", "cardio_training": "有氧训练",
    "elliptical": "椭圆机", "rowing_machine": "划船机", "hiking": "徒步", "yoga": "瑜伽", "pilates": "普拉提",
    "stretching": "拉伸", "fitness_equipment": "器械训练", "soccer": "足球", "tennis": "网球", "badminton": "羽毛球",
    "table_tennis": "乒乓球", "volleyball": "排球", "boxing": "拳击", "martial_arts": "武术", "golf": "高尔夫",
    "dance": "舞蹈", "climbing": "攀岩", "jump_rope": "跳绳", "squash": "壁球", "pickleball": "匹克球",
    "rugby": "橄榄球", "australian_football": "澳式足球", "american_football": "美式足球", "baseball": "棒球",
    "cricket": "板球", "hockey": "曲棍球", "skating": "滑冰", "surfing": "冲浪", "skiing": "滑雪",
    "generic": "其他运动", "other": "其他运动",
}
# English display names (Today page in English, issue #42); name_zh stays the stored name
TYPE_NAMES_EN = {
    "strength_training": "Strength training", "walking": "Walking / incline", "running": "Running", "treadmill": "Treadmill",
    "basketball": "Basketball", "swimming": "Swimming", "pool_swimming": "Pool swim", "open_water_swimming": "Open water swim",
    "cycling": "Cycling", "indoor_cycling": "Indoor cycling", "stair_climbing": "Stair climbing", "cardio_training": "Cardio",
    "elliptical": "Elliptical", "rowing_machine": "Rowing machine", "hiking": "Hiking", "yoga": "Yoga", "pilates": "Pilates",
    "stretching": "Stretching", "fitness_equipment": "Gym equipment", "soccer": "Soccer", "tennis": "Tennis", "badminton": "Badminton",
    "table_tennis": "Table tennis", "volleyball": "Volleyball", "boxing": "Boxing", "martial_arts": "Martial arts", "golf": "Golf",
    "dance": "Dance", "climbing": "Climbing", "jump_rope": "Jump rope", "squash": "Squash", "pickleball": "Pickleball",
    "rugby": "Rugby", "australian_football": "Australian football", "american_football": "American football", "baseball": "Baseball",
    "cricket": "Cricket", "hockey": "Hockey", "skating": "Skating", "surfing": "Surfing", "skiing": "Skiing",
    "generic": "Other workout", "other": "Other workout",
}
GENERIC_TYPES = {"generic", "other", "unknown"}   # the source did not know the activity: overrides / type rules may set it
RULE_HOUR_WINDOW = 2.0   # a type rule (workout_type_rules) matches generic workouts on its weekday starting within ±2 h

# Categories: used when merging SparkyFitness records and as the duration fallback when there is no heart rate
STRENGTH_TYPES = {"strength_training", "fitness_equipment", "generic", "other"}   # SparkyFitness strength exercises are merged into these workouts
CARDIO_TYPES = {"walking", "running", "treadmill", "stair_climbing", "cycling", "indoor_cycling", "elliptical",
                "rowing_machine", "hiking", "cardio_training"}                      # SparkyFitness cardio exercises are merged into these
SPORT_TYPES = {"basketball", "swimming", "pool_swimming", "open_water_swimming", "soccer", "tennis", "badminton",
               "table_tennis", "volleyball", "boxing", "martial_arts", "running"}   # without heart rate, duration is graded as "higher intensity"

# Types given by the Shortcut's "Find Workouts" action (English / Chinese system language) -> OW canonical type
SHORTCUT_TYPES = {
    "traditional strength training": "strength_training", "functional strength training": "strength_training",
    "core training": "strength_training", "strength training": "strength_training",
    "传统力量训练": "strength_training", "功能性力量训练": "strength_training", "核心训练": "strength_training",
    "walking": "walking", "步行": "walking", "running": "running", "跑步": "running",
    "basketball": "basketball", "篮球": "basketball", "swimming": "swimming", "游泳": "swimming",
    "cycling": "cycling", "骑行": "cycling", "骑车": "cycling", "stair climbing": "stair_climbing", "stairs": "stair_climbing",
    "爬楼梯": "stair_climbing", "楼梯": "stair_climbing", "mixed cardio": "cardio_training", "混合有氧": "cardio_training",
    "high intensity interval training": "cardio_training", "高强度间歇训练": "cardio_training",
    "cross training": "cardio_training", "交叉训练": "cardio_training", "elliptical": "elliptical", "椭圆机": "elliptical",
    "rowing": "rowing_machine", "划船": "rowing_machine", "hiking": "hiking", "徒步": "hiking", "yoga": "yoga", "瑜伽": "yoga",
    "flexibility": "stretching", "柔韧度": "stretching", "cooldown": "stretching", "整理放松": "stretching",
    "pilates": "pilates", "普拉提": "pilates", "soccer": "soccer", "足球": "soccer", "tennis": "tennis", "网球": "tennis",
    "badminton": "badminton", "羽毛球": "badminton", "other": "generic", "其他": "generic",
    # more HKWorkoutActivityType names (export.xml / OW labels: HKWorkoutActivityTypeX -> "x" with spaces)
    "mixed metabolic cardio training": "cardio_training", "step training": "cardio_training", "hiit": "cardio_training",
    "preparation and recovery": "stretching", "mind and body": "yoga",
    "dance": "dance", "cardio dance": "dance", "social dance": "dance", "dance inspired training": "dance", "舞蹈": "dance",
    "climbing": "climbing", "攀岩": "climbing", "jump rope": "jump_rope", "跳绳": "jump_rope",
    "kickboxing": "boxing", "拳击": "boxing", "boxing": "boxing", "martial arts": "martial_arts", "武术": "martial_arts",
    "squash": "squash", "pickleball": "pickleball", "table tennis": "table_tennis", "乒乓球": "table_tennis",
    "volleyball": "volleyball", "排球": "volleyball", "rugby": "rugby", "australian football": "australian_football",
    "american football": "american_football", "baseball": "baseball", "softball": "baseball", "cricket": "cricket",
    "hockey": "hockey", "golf": "golf", "高尔夫": "golf", "skating sports": "skating", "skating": "skating",
    "surfing sports": "surfing", "surfing": "surfing", "snow sports": "skiing", "downhill skiing": "skiing",
    "cross country skiing": "skiing", "snowboarding": "skiing", "water fitness": "swimming",
}

# ---------- import ----------
XML_ENERGY_UNIT = "kJ"                 # OW's XML import copies export.xml values (kJ on an Australian phone) but labels them kcal; handled the same as importers/ow.py
MAX_DURATION = timedelta(hours=12)     # a "workout" over 12 hours is an unfinished record (July 2024 had 74-hour and 10-day ones) and is excluded
HR_CAP = timedelta(minutes=5)          # a heart-rate sample counts until the next one or 5 minutes at most (same as scores/params.HR_SAMPLE_CAP)
MIN_HR_COVERAGE = 0.6                  # share of the workout covered by heart rate < 60% -> no TRIMP (only partly recorded), intensity is estimated from average heart rate
ENERGY_MATCH_TOL = 0.15                # active + basal energy samples in the window sum to >= 85% of the workout's total energy -> split out active energy by sample ratio; otherwise (not fully synced) -> total energy - estimated basal metabolism
DEFAULT_BASAL_KCAL_PER_MIN = 1.45      # fallback when estimating active energy (total - basal) without personal data (≈ 2090 kcal/day)

# ---------- dedup ----------
# Copies of the same workout: start and end both within 2 minutes, or time overlap >= 80% (of the shorter workout's duration) with the same type.
# Priority: OW > Shortcut; live sync > export.xml import; watch > phone > other (consistent with the rules in importers/ow.py).
DUP_SAME_TIME = timedelta(minutes=2)
DUP_OVERLAP = 0.8
SOURCE_RANK = {"ow": 0, "shortcut": 1, "apple_export": 2}   # apple_export: the export.zip uploaded by the user (history/)
DEVICE_RANK = {"watch": 0, "phone": 1}

# ---------- intensity grading (per workout) ----------
INTENSITY_LEVELS = ("rest", "light", "moderate", "hard", "max")
INTENSITY_LABELS = {"rest": "休息", "light": "轻松", "moderate": "中等", "hard": "高强度", "max": "极限"}
INTENSITY_LABELS_EN = {"rest": "Rest", "light": "Light", "moderate": "Moderate", "hard": "Hard", "max": "All out"}
# 1) With raw heart rate: the workout's Edwards TRIMP (five zones at 50/60/70/80/90% HRmax, minutes × 1–5).
#    Basis: after backfill, for the owner's 36 watch workouts with complete heart rate, TRIMP quartiles are 26 / 59 / 110;
#    a 40-minute incline climb (average heart rate 140) is about 120, a 1-hour climb about 180, 1 hour of basketball 160–180, 2 hours of basketball 210, strength training at heart rate around 100 is 20–90.
#    Converted to single-workout strain (21 × (1 − e^(−TRIMP/170)), K of the scoring module): 50 -> 5.4, 120 -> 10.6, 200 -> 14.6 (the lower edge of WHOOP's "strenuous").
TRIMP_THRESHOLDS = ((200, "max"), (120, "hard"), (50, "moderate"))   # (lower bound, level); below the lowest tier = light
# 2) No complete raw heart rate but an average heart rate (Shortcut, heart rate only partly synced): estimated TRIMP = minutes × weight of the zone of the average heart rate × 0.94, using the same thresholds.
#    0.94 = median of (true TRIMP / this estimate) over the 36 workouts above (quartiles 0.79–1.06).
AVG_HR_TRIMP_FACTOR = 0.94
# 3) Strength training (with a SparkyFitness record): heart rate underestimates strength training (§7), so take the higher of the "heart-rate level" and the "volume level".
#    Volume = Σ reps × weight (kg, a relative measure, same as scores/muscle.py) over working sets; the owner's 10 sessions in Sep–Oct 2026:
#    2 exercises about 1,300–1,800, 3 exercises about 2,200–4,500, a 5-exercise back day about 7,000. Working sets 8–24.
VOLUME_THRESHOLDS = ((12000, "max"), (6000, "hard"), (2500, "moderate"))   # below 2500 = light
SETS_THRESHOLDS = ((20, "hard"), (10, "moderate"))                          # working set count (bodyweight exercises have no volume, so look at set count)
# 4) Nothing at all: by duration and category (minute lower bound -> level). Ball sports / swimming / running count as higher intensity, walking as lower.
DURATION_THRESHOLDS = {
    "sport": ((75, "hard"), (30, "moderate")),
    "cardio": ((60, "moderate"),),
    "strength": ((75, "hard"), (40, "moderate")),
    "other": ((60, "moderate"),),
}
MIN_SESSION_MIN = 5                    # records under 5 minutes (workouts started by accident) are still shown, but with intensity light

# Heart-rate recovery: also read heart rate up to 3 minutes after the workout ends; drop = last reading of the final minute − reading closest to
# 1 minute after the end (within 45–100 s). Stored in workouts.flags.hr_recovery_bpm (Trends tab). Apple's own
# heart_rate_recovery_one_minute (HealthKit), when synced into daily_metrics, takes priority.
HRR_TAIL = timedelta(minutes=3)
HRR_AFTER = (timedelta(seconds=45), timedelta(seconds=100))
