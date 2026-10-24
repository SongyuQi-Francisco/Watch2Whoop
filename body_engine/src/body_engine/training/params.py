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
SOURCE_RANK = {"ow": 0, "app": 0.5, "shortcut": 1, "apple_export": 2, "hevy": 3}   # apple_export: the export.zip uploaded by the user (history/);
# hevy: a Hevy CSV import (hevy_import.py) has no heart rate, so a watch copy of the same workout is kept and the Hevy sets are shown on it
# app: the app's own sync engine (rawdata/derive.py, docs/apple-sync-engine.md D4). In the ow / shadow sync modes right
# after ow; those app workouts are stored with excluded = 'shadow' anyway, so they hold no duplicates and are never kept.
# In the app mode (after the cut-over, step S5) it ranks first: SOURCE_RANK_APP.
SOURCE_RANK_APP = {**SOURCE_RANK, "app": -1}
DEVICE_RANK = {"watch": 0, "phone": 1}

# ---------- HealthKit workout activity -> type (the app's sync engine, rawdata/derive.py) ----------
# Ported from Open Wearables (MIT, THIRD_PARTY_NOTICES.md), so an app workout gets exactly the `type` the OW import gives
# the same HKWorkout: the iOS SDK names the activity (OpenWearablesHealthSDK 0.15.0, Internal/Types.swift
# `_workoutTypeString`), the backend maps that name to its unified type (vendor/open-wearables, backend/app/constants/
# workout_types/sdk.py `SDK_WORKOUT_TYPE_MAPPINGS`), which is what /events/workouts returns as `type` (TYPE_NAMES keys).
# HKWorkoutActivityType raw value -> (Swift case name, the OW SDK's name). Unknown values are "other" in the SDK.
HK_WORKOUT_ACTIVITIES: dict[int, tuple[str, str]] = {
    1: ("americanFootball", "american_football"), 2: ("archery", "archery"),
    3: ("australianFootball", "australian_football"), 4: ("badminton", "badminton"), 5: ("baseball", "baseball"),
    6: ("basketball", "basketball"), 7: ("bowling", "bowling"), 8: ("boxing", "boxing"), 9: ("climbing", "climbing"),
    10: ("cricket", "cricket"), 11: ("crossTraining", "cross_training"), 12: ("curling", "curling"),
    13: ("cycling", "cycling"), 14: ("dance", "dance"), 15: ("danceInspiredTraining", "dance_inspired_training"),
    16: ("elliptical", "elliptical"), 17: ("equestrianSports", "equestrian"), 18: ("fencing", "fencing"),
    19: ("fishing", "fishing"), 20: ("functionalStrengthTraining", "functional_strength_training"), 21: ("golf", "golf"),
    22: ("gymnastics", "gymnastics"), 23: ("handball", "handball"), 24: ("hiking", "hiking"), 25: ("hockey", "hockey"),
    26: ("hunting", "hunting"), 27: ("lacrosse", "lacrosse"), 28: ("martialArts", "martial_arts"),
    29: ("mindAndBody", "mind_and_body"), 30: ("mixedMetabolicCardioTraining", "mixed_metabolic_cardio_training"),
    31: ("paddleSports", "paddle_sports"), 32: ("play", "play"), 33: ("preparationAndRecovery", "preparation_and_recovery"),
    34: ("racquetball", "racquetball"), 35: ("rowing", "rowing"), 36: ("rugby", "rugby"), 37: ("running", "running"),
    38: ("sailing", "sailing"), 39: ("skatingSports", "skating"), 40: ("snowSports", "snow_sports"), 41: ("soccer", "soccer"),
    42: ("softball", "softball"), 43: ("squash", "squash"), 44: ("stairClimbing", "stair_climbing"),
    45: ("surfingSports", "surfing"), 46: ("swimming", "swimming"), 47: ("tableTennis", "table_tennis"),
    48: ("tennis", "tennis"), 49: ("trackAndField", "track_and_field"), 50: ("traditionalStrengthTraining", "strength_training"),
    51: ("volleyball", "volleyball"), 52: ("walking", "walking"), 53: ("waterFitness", "water_fitness"),
    54: ("waterPolo", "water_polo"), 55: ("waterSports", "water_sports"), 56: ("wrestling", "wrestling"), 57: ("yoga", "yoga"),
    58: ("barre", "barre"), 59: ("coreTraining", "core_training"), 60: ("crossCountrySkiing", "cross_country_skiing"),
    61: ("downhillSkiing", "downhill_skiing"), 62: ("flexibility", "flexibility"),
    63: ("highIntensityIntervalTraining", "hiit"), 64: ("jumpRope", "jump_rope"), 65: ("kickboxing", "kickboxing"),
    66: ("pilates", "pilates"), 67: ("snowboarding", "snowboarding"), 68: ("stairs", "stairs"),
    69: ("stepTraining", "step_training"), 70: ("wheelchairWalkPace", "wheelchair_walk"),
    71: ("wheelchairRunPace", "wheelchair_run"), 72: ("taiChi", "tai_chi"), 73: ("mixedCardio", "mixed_cardio"),
    74: ("handCycling", "hand_cycling"), 75: ("discSports", "disc_sports"), 76: ("fitnessGaming", "fitness_gaming"),
    77: ("cardioDance", "cardio_dance"), 78: ("socialDance", "social_dance"), 79: ("pickleball", "pickleball"),
    80: ("cooldown", "cooldown"), 82: ("swimBikeRun", "swim_bike_run"), 83: ("transition", "transition"),
    84: ("underwaterDiving", "underwater_diving"), 3000: ("other", "other"),
}
# The OW SDK's activity name -> OW unified type (the HealthKit part of SDK_WORKOUT_TYPE_MAPPINGS); a name not listed is
# "other" there (get_unified_workout_type), and "other" itself is "generic"
OW_SDK_WORKOUT_TYPES: dict[str, str] = {
    "walking": "walking", "running": "running", "cycling": "cycling", "wheelchair_walk": "walking",
    "wheelchair_run": "running", "hand_cycling": "cycling", "elliptical": "elliptical", "stair_climbing": "stair_climbing",
    "stairs": "stair_climbing", "jump_rope": "cardio_training", "core_training": "strength_training",
    "functional_strength_training": "strength_training", "strength_training": "strength_training",
    "cross_training": "cardio_training", "mixed_cardio": "cardio_training", "hiit": "cardio_training",
    "step_training": "aerobics", "fitness_gaming": "gaming", "preparation_and_recovery": "stretching",
    "flexibility": "stretching", "cooldown": "stretching", "barre": "aerobics", "cardio_dance": "dance",
    "social_dance": "dance", "yoga": "yoga", "mind_and_body": "stretching", "pilates": "pilates",
    "american_football": "american_football", "australian_football": "football", "baseball": "baseball",
    "basketball": "basketball", "cricket": "cricket", "disc_sports": "disc_sports", "handball": "handball",
    "hockey": "hockey", "lacrosse": "lacrosse", "rugby": "rugby", "soccer": "soccer", "softball": "baseball",
    "volleyball": "volleyball", "badminton": "badminton", "pickleball": "pickleball", "racquetball": "racquetball",
    "squash": "squash", "table_tennis": "table_tennis", "tennis": "tennis", "climbing": "rock_climbing",
    "equestrian": "horseback_riding", "fishing": "fishing", "golf": "golf", "hiking": "hiking", "hunting": "hunting",
    "play": "play", "cross_country_skiing": "cross_country_skiing", "curling": "curling",
    "downhill_skiing": "alpine_skiing", "snow_sports": "snow_sports", "snowboarding": "snowboarding",
    "skating": "ice_skating", "paddle_sports": "paddling", "rowing": "rowing", "sailing": "sailing",
    "surfing": "surfing", "swimming": "swimming", "underwater_diving": "diving", "water_fitness": "swimming",
    "water_polo": "water_polo", "water_sports": "water_sports", "boxing": "boxing", "kickboxing": "boxing",
    "martial_arts": "martial_arts", "tai_chi": "martial_arts", "wrestling": "martial_arts", "archery": "archery",
    "bowling": "bowling", "fencing": "fencing", "gymnastics": "fitness_equipment", "track_and_field": "running",
    "swim_bike_run": "triathlon", "transition": "transition", "dance": "dance", "dance_inspired_training": "dance",
    "mixed_metabolic_cardio_training": "cardio_training", "other": "generic",
}
_HK_ACTIVITY_BY_NAME = {case: sdk for case, sdk in HK_WORKOUT_ACTIVITIES.values()}


def hk_workout_type(activity: int | None, activity_name: str | None = None) -> str:
    """The type the Open Wearables import gives this HKWorkout: by the raw activity value, else by its Swift case name."""
    sdk = (HK_WORKOUT_ACTIVITIES.get(activity) or (None, None))[1] if activity is not None else None
    sdk = sdk or _HK_ACTIVITY_BY_NAME.get(activity_name or "", "other")
    return OW_SDK_WORKOUT_TYPES.get(sdk, "other")

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

HRR_TAIL = timedelta(minutes=3)
HRR_AFTER = (timedelta(seconds=45), timedelta(seconds=100))

# ---------- Training V2: weekly volume guidelines & readiness autoregulation ----------
# Evidence base:
# - Weekly working sets per major muscle group: 10–20 sets/week maximizes hypertrophy, with >=2x/week frequency
#   (Schoenfeld et al., 2016 Sports Med; Schoenfeld et al., 2017 J Sports Sci). body-management.md §4.0 targets:
#   back 12–14, chest 9–10, legs 14–16, shoulders 9–12, arms 4–6, core 6.
# - Readiness autoregulation: WHOOP-style recovery tiers (<34 red, 34–66 yellow, >=67 green) + sleep debt (>=120 min)
#   (Kiviniemi et al., 2007 MSSE; docs/system-design.md §7, §13).
WEEKLY_SET_TARGETS: dict[str, tuple[int, int]] = {
    "back": (10, 24),
    "chest": (8, 16),
    "legs": (10, 20),
    "shoulders": (8, 16),
    "arms": (4, 12),
    "core": (4, 10),
}
RECOVERY_GREEN = 67
RECOVERY_RED = 34
AUTOREG_SLEEP_DEBT_MIN = 120

# What the AI settles with the user before save_plan (issue #65): returned by propose_plan as ask_user_first.
PLAN_QUESTIONS = [
    "从哪天开始（今天或用户选的日子）",
    "本周剩下几天怎么排（只补本周还没练到的，还是本周先休息）",
    "每周哪几天能练（training_days）",
    "有没有固定不能练的日子",
    "要不要加热身附加项（addon_suggestions，按用户自己的动作限制给出）",
]

# Optional mobility / prehab add-ons (issue #66 FB-3): never part of a split template. propose_plan suggests one only when
# the user's own exercise restrictions (exercise_meta banned / caution, mapped to catalog joint_stress) load that area;
# the user decides (propose_plan / save_plan `addons`), and it is added as a short warm-up block to the sessions that train
# the matching muscle groups. Prehab for impingement-type shoulder limits: scapular control + external-rotation work
# (Cools et al., 2014 Br J Sports Med; Kibler et al., 2013 Br J Sports Med); lumbar: core stability + hip mobility
# (McGill, Low Back Disorders 3rd ed.). Rows: (slug, sets, rep_low, rep_high, rest_sec, duration_sec_or_None, note_or_None).
MOBILITY_ADDONS: dict[str, dict] = {
    "shoulder": {
        "name": "肩部灵活 / 激活（热身）", "name_en": "Shoulder mobility / activation (warm-up)",
        "joints": {"shoulder_anterior", "shoulder_ir", "shoulder_internal_rotation", "upper_trap"},
        "groups": {"chest", "back", "shoulders"},
        "exercises": [
            ("foam_roller_t_spine", 1, 5, 5, 30, None, "2–3 个位置"),
            ("serratus_wall_slide", 1, 10, 10, 30, None, None),
            ("band_face_pull_er", 2, 15, 15, 30, None, "轻阻力"),
            ("cross_body_stretch_supine", 1, None, None, 30, 30, "每侧 30 秒"),
        ],
    },
    "lumbar": {
        "name": "下背 / 髋灵活（热身）", "name_en": "Lower back / hip mobility (warm-up)",
        "joints": {"lumbar"},
        "groups": {"legs"},
        "exercises": [
            ("hip_flexor_stretch", 1, None, None, 30, 30, "每侧 30 秒"),
            ("bird_dog", 2, 8, 8, 30, None, "每侧"),
            ("glute_bridge", 2, 12, 12, 30, None, "徒手"),
        ],
    },
}

# Built-in 3 / 4 / 5-day split templates (adapted from standard strength & conditioning splits and body-management.md §4;
# openGym starter split concepts referenced under AGPL-3.0, docs/training-v2-plan.md).
# Each exercise row: (slug, sets, rep_low, rep_high, rest_sec, duration_sec_or_None, note_or_None)
SPLIT_TEMPLATES: dict[str, dict] = {
    "3_day_ppl": {
        "split_type": "3_day_ppl",
        "name": "3 分化 · 推 / 拉 / 腿",
        "days_per_week": 3,
        "summary": "周一推（胸/肩/三头）、周三拉（背/后束/二头）、周五腿（股四头/臀/腘绳/核心），适合每周固定 3 练。",
        "days": [
            (1, "推（胸 / 肩 / 三头）", [
                ("dumbbell_bench_press", 4, 8, 12, 120, None, None),
                ("incline_dumbbell_press", 3, 8, 12, 120, None, None),
                ("landmine_press", 3, 10, 10, 90, None, "每侧"),
                ("dumbbell_lateral_raise", 3, 12, 15, 60, None, None),
                ("cable_triceps_pushdown", 3, 12, 15, 60, None, None),
            ]),
            (3, "拉（背 / 后束 / 二头）", [
                ("lat_pulldown", 4, 8, 12, 120, None, None),
                ("seated_cable_row_close", 4, 8, 12, 120, None, None),
                ("seated_cable_row_wide", 3, 10, 12, 90, None, None),
                ("cable_face_pull", 3, 15, 15, 60, None, "轻重量"),
                ("dumbbell_hammer_curl", 3, 10, 12, 60, None, None),
            ]),
            (5, "腿（股四头 / 臀腿 / 核心）", [
                ("goblet_squat", 4, 8, 12, 120, None, None),
                ("dumbbell_romanian_deadlift", 3, 10, 12, 120, None, None),
                ("leg_press", 3, 10, 12, 120, None, None),
                ("seated_leg_curl", 3, 12, 15, 60, None, None),
                ("standing_calf_raise", 3, 15, 15, 45, None, None),
                ("dead_bug", 3, 10, 10, 45, None, "每侧"),
            ]),
        ],
    },
    "3_day_full": {
        "split_type": "3_day_full",
        "name": "3 分化 · 全身 A / B / C",
        "days_per_week": 3,
        "summary": "每次覆盖推 + 拉 + 下肢，高频率、单次部位疲劳低，适合新手入门或恢复期。",
        "days": [
            (1, "全身 A", [
                ("goblet_squat", 3, 8, 12, 120, None, None),
                ("dumbbell_bench_press", 3, 8, 12, 120, None, None),
                ("lat_pulldown", 3, 8, 12, 120, None, None),
                ("dumbbell_romanian_deadlift", 3, 10, 10, 90, None, None),
                ("dumbbell_lateral_raise", 3, 12, 15, 60, None, None),
                ("dead_bug", 3, 10, 10, 45, None, "每侧"),
            ]),
            (3, "全身 B", [
                ("barbell_hip_thrust", 3, 8, 12, 120, None, None),
                ("incline_dumbbell_press", 3, 8, 12, 120, None, None),
                ("seated_cable_row_close", 3, 8, 12, 120, None, None),
                ("leg_press", 3, 10, 12, 90, None, None),
                ("cable_face_pull", 3, 15, 15, 60, None, "轻重量"),
                ("plank", 3, None, None, 45, 40, "30–45 秒"),
            ]),
            (5, "全身 C", [
                ("bulgarian_split_squat", 3, 8, 10, 90, None, "每侧"),
                ("landmine_press", 3, 10, 10, 90, None, "每侧"),
                ("close_grip_underhand_pulldown", 3, 8, 12, 120, None, None),
                ("seated_leg_curl", 3, 12, 12, 60, None, None),
                ("dumbbell_hammer_curl", 2, 10, 12, 60, None, None),
                ("cable_triceps_pushdown", 2, 12, 12, 60, None, None),
            ]),
        ],
    },
    "4_day_upper_lower": {
        "split_type": "4_day_upper_lower",
        "name": "4 分化 · 上下肢（A / B）",
        "days_per_week": 4,
        "summary": "上肢 A（拉偏重）/ 下肢 A（深蹲主导）/ 上肢 B（推偏重）/ 下肢 B（髋铰链主导），每周 4 天，每个肌群每周练 2 次。",
        "days": [
            (1, "上肢 A（拉为主）", [
                ("lat_pulldown", 4, 8, 12, 120, None, None),
                ("seated_cable_row_close", 4, 8, 12, 120, None, None),
                ("dumbbell_bench_press", 3, 8, 12, 120, None, None),
                ("one_arm_dumbbell_row", 3, 10, 12, 90, None, "每侧"),
                ("dumbbell_lateral_raise", 3, 12, 15, 60, None, None),
                ("cable_face_pull", 3, 15, 15, 60, None, "轻重量"),
                ("dumbbell_hammer_curl", 2, 10, 12, 60, None, None),
            ]),
            (2, "下肢 A（深蹲为主）", [
                ("goblet_squat", 4, 8, 12, 120, None, None),
                ("leg_press", 3, 10, 12, 120, None, None),
                ("dumbbell_romanian_deadlift", 3, 10, 10, 120, None, None),
                ("dumbbell_walking_lunge", 2, 10, 10, 90, None, "每侧"),
                ("seated_leg_curl", 3, 12, 12, 60, None, None),
                ("standing_calf_raise", 3, 15, 15, 45, None, None),
                ("dead_bug", 3, 10, 10, 45, None, "每侧"),
            ]),
            (4, "上肢 B（推为主）", [
                ("incline_dumbbell_press", 4, 8, 12, 120, None, None),
                ("close_grip_underhand_pulldown", 3, 8, 12, 120, None, None),
                ("seated_cable_row_wide", 3, 10, 12, 90, None, None),
                ("landmine_press", 3, 10, 10, 90, None, "每侧"),
                ("cable_fly_mid", 3, 12, 15, 60, None, None),
                ("dumbbell_lateral_raise", 3, 12, 15, 60, None, None),
                ("cable_triceps_pushdown", 2, 12, 12, 60, None, None),
                ("band_external_rotation", 2, 15, 15, 30, None, "每侧"),
            ]),
            (5, "下肢 B（髋铰链为主）", [
                ("barbell_hip_thrust", 4, 8, 12, 120, None, None),
                ("bulgarian_split_squat", 3, 8, 10, 90, None, "每侧"),
                ("leg_press_high_feet", 3, 10, 12, 120, None, None),
                ("seated_leg_extension", 3, 12, 15, 60, None, None),
                ("back_extension_45", 2, 12, 12, 60, None, None),
                ("plank", 3, None, None, 45, 40, "30–45 秒"),
            ]),
        ],
    },
    "5_day_ul_ppl": {
        "split_type": "5_day_ul_ppl",
        "name": "5 分化 · 上下肢 + 推拉腿",
        "days_per_week": 5,
        "summary": "周一上肢、周二下肢、周三休息、周四推、周五拉、周六腿；每个肌群每周 2 次且单次容量更充裕。",
        "days": [
            (1, "上肢（综合）", [
                ("lat_pulldown", 4, 8, 12, 120, None, None),
                ("incline_dumbbell_press", 4, 8, 12, 120, None, None),
                ("seated_cable_row_close", 3, 8, 12, 120, None, None),
                ("landmine_press", 3, 10, 10, 90, None, "每侧"),
                ("dumbbell_lateral_raise", 3, 12, 15, 60, None, None),
                ("dumbbell_hammer_curl", 2, 10, 12, 60, None, None),
                ("cable_triceps_pushdown", 2, 12, 12, 60, None, None),
            ]),
            (2, "下肢 A（深蹲主导）", [
                ("goblet_squat", 4, 8, 12, 120, None, None),
                ("dumbbell_romanian_deadlift", 3, 10, 10, 120, None, None),
                ("leg_press", 3, 10, 12, 120, None, None),
                ("seated_leg_curl", 3, 12, 12, 60, None, None),
                ("standing_calf_raise", 3, 15, 15, 45, None, None),
                ("dead_bug", 3, 10, 10, 45, None, "每侧"),
            ]),
            (4, "推（胸 / 肩 / 三头）", [
                ("dumbbell_bench_press", 4, 8, 12, 120, None, None),
                ("incline_machine_press", 3, 10, 12, 90, None, None),
                ("cable_fly_mid", 3, 12, 15, 60, None, None),
                ("dumbbell_lateral_raise", 4, 12, 15, 60, None, None),
                ("cable_triceps_pushdown", 3, 12, 15, 60, None, None),
                ("band_external_rotation", 2, 15, 15, 30, None, "每侧"),
            ]),
            (5, "拉（背 / 后束 / 二头）", [
                ("close_grip_underhand_pulldown", 4, 8, 12, 120, None, None),
                ("seated_cable_row_wide", 4, 10, 12, 90, None, None),
                ("one_arm_dumbbell_row", 3, 10, 12, 90, None, "每侧"),
                ("cable_face_pull", 3, 15, 15, 60, None, "轻重量"),
                ("cable_bicep_curl", 3, 10, 12, 60, None, None),
            ]),
            (6, "下肢 B（髋铰链主导）", [
                ("barbell_hip_thrust", 4, 8, 12, 120, None, None),
                ("bulgarian_split_squat", 3, 8, 10, 90, None, "每侧"),
                ("leg_press_high_feet", 3, 10, 12, 120, None, None),
                ("seated_leg_extension", 3, 12, 15, 60, None, None),
                ("back_extension_45", 2, 12, 12, 60, None, None),
                ("plank", 3, None, None, 45, 40, "30–45 秒"),
            ]),
        ],
    },
}


# ---------- Recommended working weight (issue #41, training/loads.py, docs/training-v2-plan.md "推荐重量") ----------
# Order of evidence for each planned exercise:
#   1. a progression reminder of the rule engine (rules/progression.py, double progression) -> its next_weight_kg, unchanged;
#   2. the user's own history (SparkyFitness working sets, warm-ups excluded):
#      - latest session in the same rep range -> keep that working weight (double progression only adds weight when every set
#        reached the top of the range, which is what the reminder in 1. says);
#      - otherwise convert through an estimated 1RM: Epley (1985) e1RM = w × (1 + reps_to_failure / 30), with
#        reps_to_failure = reps + RIR (RIR-based autoregulation, Zourdos et al. 2016 JSCR; Helms et al. 2016 Strength Cond J),
#        then w = e1RM / (1 + (target_reps + target_RIR) / 30), rounded down to the exercise's increment;
#   3. no history -> a conservative starting weight from body weight and movement pattern, labelled 「估算」.
EPLEY_DIVISOR = 30
LOAD_RIR_ASSUMED = 2          # RIR not logged -> assume 2 (body-management.md 4.3: each working set leaves 1–2 reps in reserve)
LOAD_TARGET_RIR = 2           # the suggested weight leaves ~2 reps in reserve at the middle of the rep range
LOAD_LOOKBACK_DAYS = 120      # same window as rules/params.PROGRESSION_LOOKBACK_DAYS
LOAD_SAME_RANGE_SLACK = 2     # the latest session's median reps within [low − 2, high + 2] counts as "same rep range"
LOAD_STALE_DAYS = 28          # last logged more than 4 weeks ago -> e1RM × 0.9 (inferred: conservative restart after a break)
LOAD_STALE_FACTOR = 0.9
LOAD_DEFAULT_REPS = (8, 12)   # rep range used for the exercise detail when the exercise is not in the active plan
LOAD_STEP_KG = 2.5            # rounding step when the catalog has no default_increment_kg
LOAD_MIN_KG = {"barbell": 20.0, "ez curl bar": 7.5, "dumbbell": 2.0, "kettlebell": 4.0}   # empty bar / lightest common implement
# Today's readiness (plans.readiness_advice): red says "weights down 10–15%" -> take the smaller cut; yellow says "do not add
# weight today" -> a progression increase is held at the last weight.
LOAD_READINESS_FACTOR = {"red": 0.9}
LOAD_HISTORY_TTL_S = 120      # SparkyFitness history is cached per user for 2 minutes (the Today page asks for it on every load)

# Starting weights without history: fraction of body weight of a *beginner* 1RM for the barbell (or machine stack) version of
# each pattern, from public strength standards for untrained / beginner adult men (Strength Level / ExRx "untrained" tables:
# bench ≈ 0.5, squat ≈ 0.75, deadlift ≈ 1.0, overhead press ≈ 0.35, row ≈ 0.5 × body weight), × LOAD_START_FRACTION for a first
# working set of ~10 reps with 3+ reps in reserve. Deliberately low: the first session calibrates, history takes over afterwards.
LOAD_START_FRACTION = 0.6
STARTER_1RM_BW: dict[str, float] = {
    "horizontal_push": 0.5, "incline_push": 0.4, "vertical_push": 0.35, "vertical_pull": 0.5, "horizontal_pull": 0.5,
    "squat": 0.75, "hip_hinge": 0.75, "lunge_split": 0.35, "knee_extension": 0.4, "knee_flexion": 0.3, "calf": 0.75,
    "lateral_raise": 0.25, "face_pull_er": 0.2, "elbow_flexion": 0.3, "elbow_extension": 0.3, "shrug_upright": 0.6,
}
# Load per implement relative to the barbell version: dumbbells / kettlebells are per single implement (as the progression rule logs them).
STARTER_EQUIPMENT_FACTOR = {"barbell": 1.0, "ez curl bar": 1.0, "smith machine": 0.9, "machine": 1.0, "cable": 0.7,
                            "dumbbell": 0.4, "kettlebell": 0.4}
# Exercises whose load scale differs from their pattern (fraction of body weight for the starting working set, already final).
STARTER_SLUG_BW = {"leg_press": 0.9, "leg_press_high_feet": 0.9, "goblet_squat": 0.15, "barbell_hip_thrust": 0.5,
                   "standing_calf_raise": 0.4}
# Sex unknown -> the female factors (the more conservative). Female / male beginner standard ≈ 0.5 upper body, ≈ 0.7 lower body.
STARTER_SEX_FACTOR = {"male": {"upper": 1.0, "lower": 1.0}, "female": {"upper": 0.5, "lower": 0.7}}
LOWER_BODY_PATTERNS = {"squat", "hip_hinge", "lunge_split", "knee_extension", "knee_flexion", "calf"}

# ---------- free-exercise-db (Unlicense) import: pinned upstream commit, downloaded once into the deployment's own media storage ----------
FREE_DB_REPO = "yuhonas/free-exercise-db"
FREE_DB_COMMIT = "f00c92c7dcf1216a928a52c3706c7ce8e2f71ed5"   # 2026-09-27
FREE_DB_JSON_SHA256 = "5bb747e3fc658f095a60dcbf6d53c96627acdcc6ffb6fffde86f7e26995d40bf"   # dist/exercises.json at that commit (876 exercises)
# Variants of exercises that are restricted for the owner (and caution by default for everyone): an imported variant inherits the
# user's restriction on the curated exercise (catalog.family_slug). Matched against the English name, lower case.
HAZARD_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"\bupright\b.*\brow\b", "upright_row"),
    (r"(press|jerk).*behind.*neck|behind.*neck.*press", "behind_neck_press"),
    (r"(pulldown|pull-up|chin).*behind.*neck|behind.*neck.*(pulldown|pull)", "behind_neck_pulldown"),
    (r"\bdips?\b(?!\s*squat)", "dips"),
)

# ---------- Day changes: re-planning the rest of the week (training/day_overrides.py) ----------
# The same major muscle group on two consecutive days leaves < 48 h between sessions, the usual minimum for a trained
# muscle group to recover (Schoenfeld et al., 2016 Sports Med frequency review; NSCA Essentials 4th ed., ch. 17).
# Costs only rank the candidate weeks; the cheapest one is chosen (ties: fewest moved days).
REPLAN_COST_SAME_GROUP = 10.0      # per major muscle group trained on two consecutive days
REPLAN_COST_SAME_LEGS = 5.0        # extra for legs back to back (largest muscle mass, slowest to recover)
REPLAN_MAX_STREAK = 3              # training days in a row before each extra day costs REPLAN_COST_STREAK
REPLAN_COST_STREAK = 3.0
REPLAN_COST_MOVED = 1.0            # per day whose session differs from the plan's own weekday (keep the week familiar)
REPLAN_COST_DISTANCE = 0.1         # per day a session is moved away from its own weekday (tie-breaker)
REPLAN_COST_SKIPPED = 30.0         # per plan session that no longer fits in this week
REPLAN_COST_RED_NEXT_DAY = 4.0     # recovery red today: training again tomorrow
OVERRIDE_PAST_DAYS = 7             # reschedule_day / swap_days accept dates from today − 7 ...
OVERRIDE_FUTURE_DAYS = 28          # ... to today + 28
# Session names -> major muscle groups when the exercises do not say (SparkyFitness exercises without muscles, free labels)
SESSION_NAME_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("下肢", "腿", "臀", "leg", "lower"), ("legs",)),
    (("上肢", "upper"), ("chest", "back", "shoulders", "arms")),
    (("推", "push", "胸", "chest"), ("chest", "shoulders", "arms")),
    (("拉", "pull", "背", "back"), ("back", "arms")),
    (("肩", "shoulder"), ("shoulders",)),
    (("全身", "full"), ("chest", "back", "legs", "shoulders")),
)

# ---------- "add to plan" from the exercise detail (issue #154, rollout flag exercise_add_to_plan) ----------
ADD_DEFAULT_SETS = 3                 # working sets when the user leaves the recommendation: the library's usual 3 x 8-12
ADD_DEFAULT_REPS = LOAD_DEFAULT_REPS  # same rep range the exercise detail uses for the recommended weight
ADD_DEFAULT_REST_SEC = 90
ADD_DEFAULT_DURATION_SEC = {"cardio": 600, "stretching": 30}   # timed exercises (modality duration): seconds per set
ADD_DAY_OPTIONS = 7                  # days offered for "only this day": today + 6

# ---------- Hevy CSV import (issue #161, rollout flag hevy_import, hevy_import.py, docs/hevy-benchmark.md §6) ----------
# Format: the export's 14 columns (title, start_time, end_time, description, exercise_title, superset_id, exercise_notes,
# set_index, set_type, weight_kg, reps, distance_km, duration_seconds, rpe), checked against real exports in public repos
# (the help centre lists only the export path, not the columns). Imperial accounts export weight_lbs / distance_miles.
HEVY_MAX_UPLOAD_BYTES = 1900 * 1024      # what one request may carry: the public nginx allows 2 MB (the page gzips the CSV first)
HEVY_MAX_CSV_BYTES = 10 * 1024 * 1024    # the CSV after gunzip (~100k sets); also guards against a gzip bomb
HEVY_MAX_ROWS = 200_000
HEVY_SET_TYPES = {"normal": "normal", "warmup": "warmup", "warm_up": "warmup", "dropset": "dropset", "drop_set": "dropset",
                  "failure": "failure"}   # anything else -> normal
HEVY_LIMITS = {"weight_kg": 1000.0, "reps": 1000, "duration_s": 24 * 3600, "distance_km": 500.0, "rpe": 10.0}
HEVY_IMPORTS_KEPT = 20                   # hevy_imports rows kept per user (the result summaries)
# Hevy writes the equipment in brackets ("Bench Press (Dumbbell)"): a catalog match on the bare name must have that equipment
# (else "Incline Bench Press (Dumbbell)" would land on the barbell version and its weights would mislead the load advice).
HEVY_EQUIPMENT = {"barbell": "barbell", "dumbbell": "dumbbell", "cable": "cable", "machine": "machine",
                  "smith machine": "smith machine", "kettlebell": "kettlebell", "band": "bands", "resistance band": "bands",
                  "ez bar": "ez curl bar", "bodyweight": "body only", "weighted": "body only"}
# Names commonly seen in Hevy exports that the catalog calls differently (lower case, as in the file). A best-effort list:
# anything missed stays unmatched and the user maps it once (imported_exercise_maps).
HEVY_ALIASES = {
    "bench press (dumbbell)": "dumbbell_bench_press", "incline bench press (dumbbell)": "incline_dumbbell_press",
    "incline bench press (smith machine)": "smith_incline_press", "chest press (machine)": "machine_chest_press",
    "chest fly (machine)": "pec_deck_fly", "butterfly (pec deck)": "pec_deck_fly", "chest fly (dumbbell)": "dumbbell_fly",
    "shoulder press (machine)": "machine_shoulder_press", "lateral raise (machine)": "machine_lateral_raise",
    "lateral raise (cable)": "cable_lateral_raise", "rear delt reverse fly (machine)": "reverse_pec_deck",
    "lat pulldown (machine)": "lat_pulldown", "seated row (machine)": "machine_row", "dumbbell row": "one_arm_dumbbell_row",
    "t bar row": "t_bar_row", "pull up (assisted)": "assisted_pull_up", "romanian deadlift (dumbbell)": "dumbbell_romanian_deadlift",
    "walking lunge (dumbbell)": "dumbbell_walking_lunge", "lunge (dumbbell)": "reverse_lunge",
    "leg extension (machine)": "seated_leg_extension", "seated leg curl (machine)": "seated_leg_curl",
    "hip abduction (machine)": "hip_abduction_machine", "hip adduction (machine)": "hip_adduction_machine",
    "seated calf raise": "seated_calf_raise", "standing calf raise": "standing_calf_raise",
    "skullcrusher (barbell)": "skull_crusher", "skullcrusher (dumbbell)": "skull_crusher",
    "preacher curl (barbell)": "preacher_curl", "ez bar biceps curl": "ez_bar_curl", "bicep curl (cable)": "cable_bicep_curl",
    "triceps extension (cable)": "overhead_cable_triceps_extension", "hack squat (machine)": "hack_squat",
}

# ---- Plan card estimate (issue #172): "约 N 分钟" on the collapsed 「今日训练计划」 card -------------------------------------
# A planned set takes about EST_SET_WORK_SEC of work (timed exercises: their own duration) plus the prescribed rest;
# the sum is rounded to EST_ROUND_MIN minutes. A rough planning figure, not a measurement.
EST_SET_WORK_SEC = 40
EST_DEFAULT_REST_SEC = 90
EST_ROUND_MIN = 5


# ---------- muscle load map (rollout flag muscle_load, training/muscle_load.py) ----------
MUSCLE_LOAD_VERSION = "v1.2-2026-10-25"   # v1.2: every workout credits muscles, equivalent sets count for the colours; v1.1: sports layer, custom exercises (#189)
PRIMARY_FACTOR, SECONDARY_FACTOR = 1.0, 0.5   # a set counts fully for the primary muscles of the exercise, half for the secondary ones
                                              # (the usual "fractional sets" bookkeeping: Pelland et al. 2024 preprint, Krieger / Israetel practice)
MUSCLE_LOAD_BASELINE_DAYS = 28                # "last trained" looks back this far; the week-over-week comparison needs the previous week
# Recommended EFFECTIVE sets per muscle per week (primary sets + 0.5 x secondary sets, warm-ups excluded): (low, high).
# Below `low` = "low", between = "ok", above `high` = "high". Defaults follow the dose-response meta-analyses:
#   Schoenfeld, Ogborn & Krieger (2017) J Sports Sci 35(11):1073 — >= 10 weekly sets per muscle group gave clearly more hypertrophy than < 10;
#   Schoenfeld et al. (2019) Sports Med / Krieger (2010) — gains keep rising to ~20 sets for most muscles, with diminishing returns.
# Smaller / heavily-assisted muscles use lower numbers, rounded from the volume landmarks (MEV–MAV) in Israetel, Hoffmann & Smith,
# "Scientific Principles of Hypertrophy Training" (Renaissance Periodization, 2019). These are population guides, not a prescription:
# the user can change them (per-user overrides are a later step), and the AI weighs them against recovery, injuries and goals.
WEEKLY_SETS_RANGE_DEFAULT = (10, 20)
WEEKLY_SETS_RANGE = {
    "lower back": (4, 10), "neck": (4, 10), "forearms": (6, 14), "adductors": (4, 12), "abductors": (4, 12),
    "abdominals": (6, 16), "traps": (6, 16), "calves": (8, 16),
}
MUSCLE_SIDE_BY_REGION = {   # body-map region (muscle-body-data.js) -> catalog muscles drawn there; documented here for the tests
    "chest": ["chest"], "upper-back": ["lats", "middle back"], "lower-back": ["lower back"], "trapezius": ["traps"],
    "deltoids": ["shoulders"], "quadriceps": ["quadriceps"], "hamstring": ["hamstrings"], "gluteal": ["glutes", "abductors"],
    "calves": ["calves"], "biceps": ["biceps"], "triceps": ["triceps"], "forearm": ["forearms"],
    "abs": ["abdominals"], "obliques": ["abdominals"], "adductors": ["adductors"], "neck": ["neck"],
}
RECOMMEND_LIMIT = 5                           # exercises suggested for a tapped muscle
MUSCLE_RANGES = ("today", "week", "last7")

# ---- Sports layer of the muscle load map (issue #189, coverage: every workout credits muscles) ----------------------------------
# Watch workouts (workouts table: cycling, swimming, basketball, strength training without logged sets ...) are converted to two numbers per
# muscle: MINUTES (primary x SPORT_PRIMARY_FACTOR, secondary x SPORT_SECONDARY_FACTOR; shown as "sport_min") and EQUIVALENT SETS on the
# same scale as logged sets, so the colours and the weekly range count them (formula: muscle_load.sport_load):
#   equivalent sets of a muscle = minutes x SPORT_SETS_PER_MIN x resistance(type) x INTENSITY_MULT[level] x (1 | 0.5 for primary | secondary)
# The type -> muscles table with its basis is training/sport_muscles.py (SPORT_MUSCLES, FALLBACK: a type that is not listed credits the
# whole body at a low rating, so no workout is ever left without muscles).
from .sport_muscles import SPORT_MUSCLES  # noqa: E402,F401  (re-export: the table used to live here)
SET_CYCLE_MIN = 3.0            # minutes one working set takes in a gym session incl. rest, exercise changes and warm-up
                               # (40 s of work + 90 s rest = EST_SET_WORK_SEC + EST_DEFAULT_REST_SEC, plus changes and warm-up)
SPORT_SETS_PER_MIN = 1 / SET_CYCLE_MIN   # a minute of pure lifting is 1/3 working set; sport_muscles.RESISTANCE scales it per type
# Intensity of the workout (INTENSITY_LEVELS by TRIMP / average heart rate / duration, the same grading as the training calendar) -> multiplier.
# Scaled like session-RPE categories (light / moderate / hard / very hard ~ RPE 3 / 5 / 7 / 9, divided by 5; Foster 2001, J Strength Cond Res
# 15:109): the same minutes of a harder effort load the muscles more.
INTENSITY_MULT = {"rest": 0.4, "light": 0.6, "moderate": 1.0, "hard": 1.4, "max": 1.8}
# A strength workout with no logged sets (the watch only knows time and heart rate; heart rate underestimates lifting, so the intensity
# multiplier is not applied): working sets = minutes / SET_CYCLE_MIN, spread over the muscles of that weekday's planned session (the
# primary muscles of its strength exercises), else over the whole-body mapping. Each set credits on average STRENGTH_PRIMARIES_PER_SET
# primary muscles (compound lifts hit 1-3), so a muscle in a target of n muscles gets sets x STRENGTH_PRIMARIES_PER_SET / n, and the
# secondary muscles of the target half of that (SPORT_SECONDARY_FACTOR).
STRENGTH_PRIMARIES_PER_SET = 1.5
STRENGTH_FALLBACK_RESISTANCE = 1.0
SPORT_PRIMARY_FACTOR, SPORT_SECONDARY_FACTOR = 1.0, 0.5   # minutes count fully for primary muscles, half for secondary ones
