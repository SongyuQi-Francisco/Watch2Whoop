"""Rule engine parameters. Every threshold cites its source (a section of body-management.md, or a system design section);
those marked "inferred" are values where body-management.md was unclear, taken from the most conservative reading (collected in the system design "rule engine" section).
Bump RULES_VERSION whenever a parameter that affects results changes."""

RULES_VERSION = "v1.4-2026-10-22"   # v1.4: afternoon water reminder (rules/hydration.py, issue #125); v1.1: added bedtime goal (sleep_goal, parameters in goals/params.py); v1.2: sugar caps, goals from nutrition_goal_versions; v1.3: red meat / food caps from the user's own diet constraints (#76)

# ---------- Double progression (body-management.md 4.5; rep ranges and increments come from SparkyFitness workout templates, migrated from 4.4) ----------
# 4.5: when all sets of an exercise reach the top of the rep range -> add one increment next time (upper body +1-2.5kg, lower body +2.5-5kg) and go back to the bottom of the range.
# Each exercise in a template has rep_goal (top of range), per-set reps (bottom of range) and increment_value (increment); when the template has no increment, use by body part:
INCREMENT_FALLBACK_KG = {"upper": 2.5, "lower": 2.5}   # take the conservative value within the 4.5 range (same as the migration script)
# Inferred: 4.3 "leave 1-2 reps in reserve each set". If RIR is logged and the lowest RIR < 1 (taken to failure), do not add weight even at the top of the range; first reach 1 rep in reserve at this weight.
MIN_RIR_FOR_INCREASE = 1
# Inferred: body-management.md has no weight-reduction rule. "Consecutive failure" = at the same weight, 2 workouts in a row have a working set below the bottom of the range -> reduce one increment (same increment as for adding).
FAIL_STREAK = 2
# Inferred: fewer logged working sets than the template (e.g. planned 4, did 2) does not count as "all sets at the top".
REQUIRE_PLANNED_SETS = True
PROGRESSION_LOOKBACK_DAYS = 120          # how far back to look for the latest log of each exercise
WARMUP_SET_TYPES = {"warm-up", "warmup", "warm up", "热身"}

# ---------- Adjust calories by weekly average weight (body-management.md 5.2 V2.1, details in 5.2.0) ----------
# 5.2: still based on weekly average weight: if the two-week average loss is under 0.4kg/week, cut another 150 kcal; if loss exceeds 1kg or fatigue / early waking appears, add 200 kcal.
# 5.2.0: keep as is when losing 0.5-0.9kg/week; when cutting calories, cut oil and snacks first, not training-day carbs.
SLOW_LOSS_KG_PER_WEEK = 0.4              # two-week average loss per week below this -> cut
SLOW_LOSS_KCAL = -150
FAST_LOSS_KG_PER_WEEK = 1.0              # weekly loss above this -> add
FAST_LOSS_KCAL = 200
# Inferred: "loss over 1kg" does not say one week or two; use one week (catches too-fast loss earlier, more conservative).
# Inferred: a 7-day window needs at least 2 weigh-ins to count as a weekly average (1 is too noisy). Between 0.4-0.5 and 0.9-1.0 counts as "keep".
MIN_WEIGHINS_PER_WEEK = 2
TARGET_LOSS_RANGE = (0.5, 0.9)           # the 5.2.0 keep range, used only in message text

# ---------- Pre-workout eating (body-management.md 4.2.1) ----------
PRE_MEAL_CARBS_G = (50, 70)              # a proper meal 1.5-2 hours before training: carbs 50-70g, protein 20-30g
PRE_MEAL_PROTEIN_G = (20, 30)
# Inferred: only a day's food log is known, not the training time; warn if food was logged that day but total carbs < 50g (not even one proper meal).
DAY_CARBS_WARN_G = 50
WATER_TARGET_L = 3.0                     # about 3-3.5L per day while taking creatine
SYMPTOM_LOOKBACK_DAYS = 14               # hypoglycemia-like feeling on leg day / not enough eaten before training in the last 14 days -> reminder is raised to warning
LEG_MUSCLES = {"quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors"}

# ---------- Red meat recognition (food knowledge, for the user's own diet constraints) ----------
# Limits are per user (nutrition/constraints.py, issue #76): nobody gets a red meat cap or a per-food limit by default.
# "Per week" is a rolling 7 days (including today); beef / lamb / pork, including processed meat such as bacon, ham and
# sausage, all count as red meat (WCRF/AICR 2018 definition of red and processed meat).
# Grams: use the journal red_meat_g if logged that day; otherwise estimate from food log names (each entry at the typical
# portion below, raw weight; a typical main-meal serving of meat is 120–150 g raw).
RED_MEAT_KEYWORDS: list[tuple[str, int]] = [   # (keyword, estimated grams per serving), earlier matches win
    ("牛肋条", 135), ("short rib", 135),
    ("培根", 30), ("bacon", 30), ("火腿", 30), ("香肠", 50), ("sausage", 50),
    ("牛", 150), ("beef", 150), ("steak", 150), ("羊", 150), ("lamb", 150), ("猪", 150), ("pork", 150),
    ("排骨", 150), ("rump", 150), ("topside", 150),
]
# strip these from the name before matching (milk and avocado are not meat)
RED_MEAT_EXCLUDE = ("牛奶", "牛乳", "牛油果", "牛油", "酸牛奶", "羊奶", "羊乳", "蜗牛", "牛蛙", "milk", "avocado")

# ---------- Banned / caution exercises (body-management.md 4.3, 2.3; exercise_meta) ----------
BANNED_LOG_LOOKBACK_DAYS = 14            # a banned exercise appears in the last 14 days of training logs -> remind
CAUTION_LOG_LOOKBACK_DAYS = 14
# 4.3 "never do heavy cable face pulls"; 4.4 plans 18kg. Remind above that (exercise_ref -> max kg).
CAUTION_MAX_WEIGHT_KG = {"cable_face_pull": 18.0}
# when the exercise is not in exercise_meta, decide by the marker at the start of the SparkyFitness exercise description (written by the migration script)
SPARKY_STATUS_MARKERS = {"【禁用】": "banned", "【注意】": "caution"}

# ---------- Combined with scores (system design section 7; body-management.md 4.3, 6.2) ----------
RECOVERY_RED = 34                        # recovery score < 34 = red (WHOOP's banding, the same band section 7 used when recomputing)
# Inferred: "heavy day" = the plan has a strength template (excluding shoulder routine / stretching) or basketball.
HEAVY_CARDIO_KEYWORDS = ("篮球", "basketball")
# Inferred: body-management.md sets no sleep debt threshold; use 2h (= DEBT_CAP_MIN, the cap on sleep debt counted into sleep need in scoring).
SLEEP_DEBT_HIGH_MIN = 120

# ---------- Sugar (issue #31; nutrition/extras.py, rules/nutrition.sugar_caps, status card, reports) ----------
# WHO 2015 "Guideline: Sugars intake for adults and children": free sugars < 10% of energy (strong recommendation;
# 10% of 2000 kcal = 50 g) and a further reduction to < 5% (conditional; about 25 g / 6 teaspoons a day).
# AHA 2009 (Johnson et al., Circulation 120:1011) also caps added sugar at 25 g/day for women, 36 g/day for men.
# Default cap = the WHO 5% figure; a goal version's added_sugar_max_g overrides it. Above the cap -> info, above
# ADDED_SUGAR_WARN_G (the WHO 10% figure) -> warning.
ADDED_SUGAR_CAP_G = 25
ADDED_SUGAR_WARN_G = 50
# Sugar-sweetened drinks (soft drinks, juice, milk tea, energy drinks): fructose raises serum urate and triglycerides
# (Choi & Curhan 2008, BMJ 336:309 — gout risk rises with >= 2 servings/day of sugar-sweetened soft drinks; ACR 2020
# gout guideline: limit high-fructose corn syrup; EASL 2016 NAFLD guideline: avoid fructose-containing drinks).
# Inferred: the guidelines give no weekly count; warn when more than 2 in a rolling 7 days (including today).
SUGARY_DRINKS_PER_WEEK = 2
# A drink is "sugary" when log_food marked sugary_drink=true, or its name matches a keyword below and contains none of
# the exclusions; a keyword match with known total sugar below SUGARY_DRINK_MIN_SUGAR_G is not counted (diet versions).
SUGARY_DRINK_KEYWORDS = ("可乐", "雪碧", "芬达", "汽水", "碳酸饮料", "奶茶", "果汁", "橙汁", "苹果汁", "柠檬茶", "冰红茶",
                         "绿茶饮料", "能量饮料", "红牛", "运动饮料", "佳得乐", "宝矿力", "含糖饮料", "甜饮料", "糖水",
                         "coke", "cola", "soda", "soft drink", "juice", "bubble tea", "milk tea", "lemonade",
                         "iced tea", "energy drink", "gatorade", "powerade", "red bull", "sprite", "fanta")
SUGARY_DRINK_EXCLUDE = ("无糖", "零度", "零糖", "0糖", "低糖", "zero", "diet", "sugar free", "sugar-free", "no sugar")
SUGARY_DRINK_MIN_SUGAR_G = 5

# ---------- Calorie / protein adherence (status of a logged day against the goal in effect that day) ----------
# Inferred (no source in body-management.md): calories within +-10% of the day's goal and protein >= 90% of it count as
# "on target" — the usual tolerance of diet-tracking apps; days without any food entry are not judged.
CALORIE_ADHERENCE_TOL = 0.10
PROTEIN_ADHERENCE_MIN = 0.90
