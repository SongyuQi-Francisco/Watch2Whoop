"""Rule engine parameters. Every threshold cites its source (a section of body-management.md, or a system design section);
those marked "inferred" are values where body-management.md was unclear, taken from the most conservative reading (collected in the system design "rule engine" section).
Bump RULES_VERSION whenever a parameter that affects results changes."""

RULES_VERSION = "v1.1-2026-10-17"   # v1.1: added bedtime goal (sleep_goal, parameters in goals/params.py)

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

# ---------- Frequency caps (body-management.md 5.3.1, 5.3.2; health constraints in CLAUDE.md) ----------
# 5.3.1: red meat (beef) total <= 250g per week; beef short rib at most 1 meal per week. 5.3.2: beef / pork (lean cuts) combined <= 2 meals per week.
RED_MEAT_G_PER_WEEK = 250
RED_MEAT_WARN_FRACTION = 0.8             # at 80% (200g) show "almost there"
SHORT_RIB_PER_WEEK = 1
RED_MEAT_MEALS_PER_WEEK = 2
# Inferred: "per week" is a rolling 7 days (including today), more conservative than calendar weeks; beef / lamb / pork (including processed meat such as bacon, ham, sausage) all count as red meat
# (the journal item red_meat_g's note also says "lean beef, lamb and pork all count").
# Grams: use the journal red_meat_g if logged that day; otherwise estimate from food log names (each entry at the typical portion below, raw weight).
RED_MEAT_KEYWORDS: list[tuple[str, int]] = [   # (keyword, estimated grams per serving), earlier matches win
    ("牛肋条", 135), ("short rib", 135),          # 5.3.1: beef short rib 120-150g
    ("培根", 30), ("bacon", 30), ("火腿", 30), ("香肠", 50), ("sausage", 50),
    ("牛", 150), ("beef", 150), ("steak", 150), ("羊", 150), ("lamb", 150), ("猪", 150), ("pork", 150),
    ("排骨", 150), ("rump", 150), ("topside", 150),
]
SHORT_RIB_KEYWORDS = ("牛肋条", "short rib", "beef rib")
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
