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
    "generic": "其他运动", "other": "其他运动",
}

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
        "summary": "上肢 A（拉偏重）/ 下肢 A（深蹲主导）/ 上肢 B（推偏重）/ 下肢 B（髋铰链主导），每个肌群每周练 2 次。",
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
            (3, "肩部康复 / 灵活性 routine", [
                ("cross_body_stretch_supine", 3, None, None, 30, 30, "每侧 30 秒"),
                ("doorway_pec_stretch", 2, None, None, 30, 30, "每侧 30 秒"),
                ("foam_roller_t_spine", 3, 5, 5, 30, None, "2–3 个位置"),
                ("serratus_wall_slide", 2, 10, 10, 30, None, None),
                ("band_face_pull_er", 3, 15, 15, 30, None, "轻阻力"),
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

