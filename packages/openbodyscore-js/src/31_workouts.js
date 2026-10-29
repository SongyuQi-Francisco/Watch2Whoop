/* workouts (2.0.0 package M1c, docs/ios-native-2.0.md section 4): the phone's workouts, the training calendar / day and
the muscle-load map, a port of body_engine training/importer.py (parse_app, duplicates, session_trimp, avg_hr_trimp,
session_hr_recovery, hr_enrich, workout_days), training/overrides.apply, cardio/hrr.hrr60, training/history.py (merge_day,
classify, compute, calendar_view, day_view, summarize, workout_intensity), training/muscle_load.py (aggregate,
aggregate_sports, sport_load, compute_from) and training/sport_muscles.py (normalize, resolve). The Python is the
reference; goldens: test/workouts.test.js against test/golden/workouts.json (body_engine/tests/test_js_golden_workouts.py).

Entry points (each stamped with algo_version = OBS.ALGO_VERSIONS.workouts and bundle_version):
- OBS.workoutDays(src): the twin of importer.workout_days (the app's workouts of some days, enriched; see there for src);
- OBS.trainingCalendar(data, {start, end, label, today}) / OBS.trainingDay(data, {date, today}): history.compute +
  calendar_view / day_view (data = what history.load returns, JSON-like: instants as UTC seconds, dates as YYYY-MM-DD);
- OBS.muscleLoad(data, today): muscle_load.compute_from -> {map, by_muscle} (data.index = {rows, by_name, by_app}).
Texts follow OBS.lang ("zh" / "en"), like the server's request language. Instants inside are integer microseconds (util).
*/
OBS.workouts = (function (U, S) {
  "use strict";
  var US_SEC = U.US_SEC, US_MIN = U.US_MIN;

  // ------------------------------------------------------------------ params (mirrors of the Python, D7)
  // training/params.py, training/importer.py, cardio/hrr.py, rules/params.WARMUP_SET_TYPES; the golden file carries the
  // Python values and test/workouts.test.js checks these are equal.
  var PARAMS = {
    ALGO_VERSION: "workouts-1-2026-10-30",                 // training/importer.WORKOUTS_VERSION
    TRAINING_VERSION: "v1.0-2026-10-17",
    MUSCLE_LOAD_VERSION: "v1.2-2026-10-25",
    MAX_DURATION_S: 12 * 3600,                            // an unfinished record, excluded
    HR_CAP_S: 300,                                        // a heart-rate sample counts until the next one, ≤ 5 min
    MIN_HR_COVERAGE: 0.6,
    DUP_SAME_TIME_S: 120,
    DUP_OVERLAP: 0.8,
    SOURCE_RANK: { ow: 0, app: 0.5, shortcut: 1, apple_export: 2, hevy: 3 },
    SOURCE_RANK_APP: { ow: 0, app: -1, shortcut: 1, apple_export: 2, hevy: 3 },
    HRR_TAIL_S: 180,
    HRR_AFTER_S: [45, 100],
    RULE_HOUR_WINDOW: 2.0,
    INTENSITY_LEVELS: ["rest", "light", "moderate", "hard", "max"],
    INTENSITY_LABELS: { rest: "休息", light: "轻松", moderate: "中等", hard: "高强度", max: "极限" },
    INTENSITY_LABELS_EN: { rest: "Rest", light: "Light", moderate: "Moderate", hard: "Hard", max: "All out" },
    TRIMP_THRESHOLDS: [[200, "max"], [120, "hard"], [50, "moderate"]],
    AVG_HR_TRIMP_FACTOR: 0.94,
    VOLUME_THRESHOLDS: [[12000, "max"], [6000, "hard"], [2500, "moderate"]],
    SETS_THRESHOLDS: [[20, "hard"], [10, "moderate"]],
    DURATION_THRESHOLDS: { sport: [[75, "hard"], [30, "moderate"]], cardio: [[60, "moderate"]],
      strength: [[75, "hard"], [40, "moderate"]], other: [[60, "moderate"]] },
    MIN_SESSION_MIN: 5,
    GENERIC_TYPES: ["generic", "other", "unknown"],
    STRENGTH_TYPES: ["strength_training", "fitness_equipment", "generic", "other"],
    CARDIO_TYPES: ["walking", "running", "treadmill", "stair_climbing", "cycling", "indoor_cycling", "elliptical",
      "rowing_machine", "hiking", "cardio_training"],
    SPORT_TYPES: ["basketball", "swimming", "pool_swimming", "open_water_swimming", "soccer", "tennis", "badminton",
      "table_tennis", "volleyball", "boxing", "martial_arts", "running"],
    WARMUP_SET_TYPES: ["warm-up", "warmup", "warm up", "热身"],
    HRR_PEAK_WINDOW_S: 120,                               // cardio/hrr.py: peak in the last 2 minutes
    HRR_AFTER60_S: 60,
    HRR_TOLERANCE_S: 20,
    HRR_PEAK_MIN_PCT: 0.75,                               // Cole et al. 1999 (cardio/hrr.py)
    PRIMARY_FACTOR: 1.0,
    SECONDARY_FACTOR: 0.5,
    MUSCLE_LOAD_BASELINE_DAYS: 28,
    WEEKLY_SETS_RANGE_DEFAULT: [10, 20],
    WEEKLY_SETS_RANGE: null,                              // filled from TABLES below (training/params.WEEKLY_SETS_RANGE)
    MUSCLE_RANGES: ["today", "week", "last7"],
    SET_CYCLE_MIN: 3.0,
    SPORT_SETS_PER_MIN: 1 / 3.0,
    INTENSITY_MULT: { rest: 0.4, light: 0.6, moderate: 1.0, hard: 1.4, max: 1.8 },
    STRENGTH_PRIMARIES_PER_SET: 1.5,
    STRENGTH_FALLBACK_RESISTANCE: 1.0,
    SPORT_PRIMARY_FACTOR: 1.0,
    SPORT_SECONDARY_FACTOR: 0.5
  };

  // training/params.py (type names, the Shortcut's labels, HealthKit activity -> Open Wearables type), sport_muscles.py,
  // catalog_data.py (muscles), muscle_load.MUSCLE_EN; in the Python's order (dict order decides a few ties).
  var TABLES = {
    TYPE_NAMES: {"strength_training": "力量训练", "walking": "步行 / 爬坡", "running": "跑步", "treadmill": "跑步机",
      "basketball": "篮球", "swimming": "游泳", "pool_swimming": "泳池游泳", "open_water_swimming": "公开水域游泳",
      "cycling": "骑行", "indoor_cycling": "室内单车", "stair_climbing": "爬楼梯", "cardio_training": "有氧训练",
      "elliptical": "椭圆机", "rowing_machine": "划船机", "hiking": "徒步", "yoga": "瑜伽", "pilates": "普拉提",
      "stretching": "拉伸", "fitness_equipment": "器械训练", "soccer": "足球", "tennis": "网球", "badminton": "羽毛球",
      "table_tennis": "乒乓球", "volleyball": "排球", "boxing": "拳击", "martial_arts": "武术", "golf": "高尔夫", "dance": "舞蹈",
      "climbing": "攀岩", "jump_rope": "跳绳", "squash": "壁球", "pickleball": "匹克球", "rugby": "橄榄球",
      "australian_football": "澳式足球", "american_football": "美式足球", "baseball": "棒球", "cricket": "板球", "hockey": "曲棍球",
      "skating": "滑冰", "surfing": "冲浪", "skiing": "滑雪", "generic": "其他运动", "other": "其他运动"},
    TYPE_NAMES_EN: {"strength_training": "Strength training", "walking": "Walking / incline", "running": "Running",
      "treadmill": "Treadmill", "basketball": "Basketball", "swimming": "Swimming", "pool_swimming": "Pool swim",
      "open_water_swimming": "Open water swim", "cycling": "Cycling", "indoor_cycling": "Indoor cycling",
      "stair_climbing": "Stair climbing", "cardio_training": "Cardio", "elliptical": "Elliptical",
      "rowing_machine": "Rowing machine", "hiking": "Hiking", "yoga": "Yoga", "pilates": "Pilates",
      "stretching": "Stretching", "fitness_equipment": "Gym equipment", "soccer": "Soccer", "tennis": "Tennis",
      "badminton": "Badminton", "table_tennis": "Table tennis", "volleyball": "Volleyball", "boxing": "Boxing",
      "martial_arts": "Martial arts", "golf": "Golf", "dance": "Dance", "climbing": "Climbing",
      "jump_rope": "Jump rope", "squash": "Squash", "pickleball": "Pickleball", "rugby": "Rugby",
      "australian_football": "Australian football", "american_football": "American football", "baseball": "Baseball",
      "cricket": "Cricket", "hockey": "Hockey", "skating": "Skating", "surfing": "Surfing", "skiing": "Skiing",
      "generic": "Other workout", "other": "Other workout"},
    SHORTCUT_TYPES: {"traditional strength training": "strength_training",
      "functional strength training": "strength_training", "core training": "strength_training",
      "strength training": "strength_training", "传统力量训练": "strength_training", "功能性力量训练": "strength_training",
      "核心训练": "strength_training", "walking": "walking", "步行": "walking", "running": "running", "跑步": "running",
      "basketball": "basketball", "篮球": "basketball", "swimming": "swimming", "游泳": "swimming", "cycling": "cycling",
      "骑行": "cycling", "骑车": "cycling", "stair climbing": "stair_climbing", "stairs": "stair_climbing",
      "爬楼梯": "stair_climbing", "楼梯": "stair_climbing", "mixed cardio": "cardio_training", "混合有氧": "cardio_training",
      "high intensity interval training": "cardio_training", "高强度间歇训练": "cardio_training",
      "cross training": "cardio_training", "交叉训练": "cardio_training", "elliptical": "elliptical",
      "椭圆机": "elliptical", "rowing": "rowing_machine", "划船": "rowing_machine", "hiking": "hiking", "徒步": "hiking",
      "yoga": "yoga", "瑜伽": "yoga", "flexibility": "stretching", "柔韧度": "stretching", "cooldown": "stretching",
      "整理放松": "stretching", "pilates": "pilates", "普拉提": "pilates", "soccer": "soccer", "足球": "soccer",
      "tennis": "tennis", "网球": "tennis", "badminton": "badminton", "羽毛球": "badminton", "other": "generic",
      "其他": "generic", "mixed metabolic cardio training": "cardio_training", "step training": "cardio_training",
      "hiit": "cardio_training", "preparation and recovery": "stretching", "mind and body": "yoga", "dance": "dance",
      "cardio dance": "dance", "social dance": "dance", "dance inspired training": "dance", "舞蹈": "dance",
      "climbing": "climbing", "攀岩": "climbing", "jump rope": "jump_rope", "跳绳": "jump_rope", "kickboxing": "boxing",
      "拳击": "boxing", "boxing": "boxing", "martial arts": "martial_arts", "武术": "martial_arts", "squash": "squash",
      "pickleball": "pickleball", "table tennis": "table_tennis", "乒乓球": "table_tennis", "volleyball": "volleyball",
      "排球": "volleyball", "rugby": "rugby", "australian football": "australian_football",
      "american football": "american_football", "baseball": "baseball", "softball": "baseball", "cricket": "cricket",
      "hockey": "hockey", "golf": "golf", "高尔夫": "golf", "skating sports": "skating", "skating": "skating",
      "surfing sports": "surfing", "surfing": "surfing", "snow sports": "skiing", "downhill skiing": "skiing",
      "cross country skiing": "skiing", "snowboarding": "skiing", "water fitness": "swimming"},
    HK_WORKOUT_ACTIVITIES: {"1": ["americanFootball", "american_football"], "2": ["archery", "archery"],
      "3": ["australianFootball", "australian_football"], "4": ["badminton", "badminton"], "5": ["baseball",
      "baseball"], "6": ["basketball", "basketball"], "7": ["bowling", "bowling"], "8": ["boxing", "boxing"],
      "9": ["climbing", "climbing"], "10": ["cricket", "cricket"], "11": ["crossTraining", "cross_training"],
      "12": ["curling", "curling"], "13": ["cycling", "cycling"], "14": ["dance", "dance"],
      "15": ["danceInspiredTraining", "dance_inspired_training"], "16": ["elliptical", "elliptical"],
      "17": ["equestrianSports", "equestrian"], "18": ["fencing", "fencing"], "19": ["fishing", "fishing"],
      "20": ["functionalStrengthTraining", "functional_strength_training"], "21": ["golf", "golf"],
      "22": ["gymnastics", "gymnastics"], "23": ["handball", "handball"], "24": ["hiking", "hiking"],
      "25": ["hockey", "hockey"], "26": ["hunting", "hunting"], "27": ["lacrosse", "lacrosse"], "28": ["martialArts",
      "martial_arts"], "29": ["mindAndBody", "mind_and_body"], "30": ["mixedMetabolicCardioTraining",
      "mixed_metabolic_cardio_training"], "31": ["paddleSports", "paddle_sports"], "32": ["play", "play"],
      "33": ["preparationAndRecovery", "preparation_and_recovery"], "34": ["racquetball", "racquetball"],
      "35": ["rowing", "rowing"], "36": ["rugby", "rugby"], "37": ["running", "running"], "38": ["sailing",
      "sailing"], "39": ["skatingSports", "skating"], "40": ["snowSports", "snow_sports"], "41": ["soccer",
      "soccer"], "42": ["softball", "softball"], "43": ["squash", "squash"], "44": ["stairClimbing",
      "stair_climbing"], "45": ["surfingSports", "surfing"], "46": ["swimming", "swimming"], "47": ["tableTennis",
      "table_tennis"], "48": ["tennis", "tennis"], "49": ["trackAndField", "track_and_field"],
      "50": ["traditionalStrengthTraining", "strength_training"], "51": ["volleyball", "volleyball"],
      "52": ["walking", "walking"], "53": ["waterFitness", "water_fitness"], "54": ["waterPolo", "water_polo"],
      "55": ["waterSports", "water_sports"], "56": ["wrestling", "wrestling"], "57": ["yoga", "yoga"],
      "58": ["barre", "barre"], "59": ["coreTraining", "core_training"], "60": ["crossCountrySkiing",
      "cross_country_skiing"], "61": ["downhillSkiing", "downhill_skiing"], "62": ["flexibility", "flexibility"],
      "63": ["highIntensityIntervalTraining", "hiit"], "64": ["jumpRope", "jump_rope"], "65": ["kickboxing",
      "kickboxing"], "66": ["pilates", "pilates"], "67": ["snowboarding", "snowboarding"], "68": ["stairs",
      "stairs"], "69": ["stepTraining", "step_training"], "70": ["wheelchairWalkPace", "wheelchair_walk"],
      "71": ["wheelchairRunPace", "wheelchair_run"], "72": ["taiChi", "tai_chi"], "73": ["mixedCardio",
      "mixed_cardio"], "74": ["handCycling", "hand_cycling"], "75": ["discSports", "disc_sports"],
      "76": ["fitnessGaming", "fitness_gaming"], "77": ["cardioDance", "cardio_dance"], "78": ["socialDance",
      "social_dance"], "79": ["pickleball", "pickleball"], "80": ["cooldown", "cooldown"], "82": ["swimBikeRun",
      "swim_bike_run"], "83": ["transition", "transition"], "84": ["underwaterDiving", "underwater_diving"],
      "3000": ["other", "other"]},
    OW_SDK_WORKOUT_TYPES: {"walking": "walking", "running": "running", "cycling": "cycling",
      "wheelchair_walk": "walking", "wheelchair_run": "running", "hand_cycling": "cycling",
      "elliptical": "elliptical", "stair_climbing": "stair_climbing", "stairs": "stair_climbing",
      "jump_rope": "cardio_training", "core_training": "strength_training",
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
      "equestrian": "horseback_riding", "fishing": "fishing", "golf": "golf", "hiking": "hiking",
      "hunting": "hunting", "play": "play", "cross_country_skiing": "cross_country_skiing", "curling": "curling",
      "downhill_skiing": "alpine_skiing", "snow_sports": "snow_sports", "snowboarding": "snowboarding",
      "skating": "ice_skating", "paddle_sports": "paddling", "rowing": "rowing", "sailing": "sailing",
      "surfing": "surfing", "swimming": "swimming", "underwater_diving": "diving", "water_fitness": "swimming",
      "water_polo": "water_polo", "water_sports": "water_sports", "boxing": "boxing", "kickboxing": "boxing",
      "martial_arts": "martial_arts", "tai_chi": "martial_arts", "wrestling": "martial_arts", "archery": "archery",
      "bowling": "bowling", "fencing": "fencing", "gymnastics": "fitness_equipment", "track_and_field": "running",
      "swim_bike_run": "triathlon", "transition": "transition", "dance": "dance", "dance_inspired_training": "dance",
      "mixed_metabolic_cardio_training": "cardio_training", "other": "generic"},
    SPORT_MUSCLES: {"running": [["quadriceps", "glutes", "calves"], ["hamstrings", "abdominals"], 0.3],
      "treadmill": [["quadriceps", "glutes", "calves"], ["hamstrings", "abdominals"], 0.3],
      "trail_running": [["quadriceps", "glutes", "calves"], ["hamstrings", "abdominals"], 0.3],
      "track_and_field": [["quadriceps", "glutes", "calves", "hamstrings"], ["abdominals"], 0.32],
      "walking": [["glutes", "calves"], ["quadriceps", "hamstrings"], 0.15], "hiking": [["glutes", "quadriceps",
      "calves"], ["hamstrings", "lower back"], 0.25], "stair_climbing": [["quadriceps", "glutes"], ["calves",
      "hamstrings"], 0.35], "stairs": [["quadriceps", "glutes"], ["calves", "hamstrings"], 0.35],
      "step_training": [["quadriceps", "glutes"], ["calves", "hamstrings", "abdominals"], 0.3],
      "snowshoeing": [["quadriceps", "glutes", "calves"], ["hamstrings", "adductors", "abductors"], 0.28],
      "cycling": [["quadriceps", "glutes"], ["calves", "hamstrings", "abdominals"], 0.25],
      "indoor_cycling": [["quadriceps", "glutes"], ["calves", "hamstrings", "abdominals"], 0.3],
      "mountain_biking": [["quadriceps", "glutes"], ["calves", "hamstrings", "abdominals"], 0.3],
      "e_biking": [["quadriceps", "glutes"], ["calves", "hamstrings", "abdominals"], 0.15],
      "hand_cycling": [["shoulders", "triceps", "biceps"], ["lats", "chest", "abdominals"], 0.25],
      "wheelchair_walk": [["lats", "shoulders", "triceps"], ["biceps", "forearms", "chest"], 0.2],
      "wheelchair_run": [["lats", "shoulders", "triceps"], ["biceps", "forearms", "chest"], 0.3],
      "wheelchair": [["lats", "shoulders", "triceps"], ["biceps", "forearms", "chest"], 0.2],
      "elliptical": [["quadriceps", "glutes"], ["hamstrings", "calves", "shoulders"], 0.2],
      "rowing_machine": [["quadriceps", "glutes", "lats", "middle back"], ["hamstrings", "biceps", "lower back",
      "shoulders"], 0.45], "rowing": [["quadriceps", "glutes", "lats", "middle back"], ["hamstrings", "biceps",
      "lower back", "shoulders"], 0.45], "jump_rope": [["calves"], ["quadriceps", "shoulders", "forearms"], 0.3],
      "skateboarding": [["quadriceps", "glutes", "abductors"], ["adductors", "hamstrings", "calves", "abdominals"],
      0.25], "inline_skating": [["quadriceps", "glutes", "abductors"], ["adductors", "hamstrings", "calves",
      "abdominals"], 0.25], "swimming": [["lats", "shoulders", "triceps"], ["chest", "abdominals", "quadriceps",
      "glutes"], 0.3], "pool_swimming": [["lats", "shoulders", "triceps"], ["chest", "abdominals", "quadriceps",
      "glutes"], 0.3], "open_water_swimming": [["lats", "shoulders", "triceps"], ["chest", "abdominals",
      "quadriceps", "glutes"], 0.3], "water_fitness": [["quadriceps", "glutes", "shoulders"], ["abdominals",
      "hamstrings", "chest"], 0.25], "water_polo": [["shoulders", "quadriceps", "lats"], ["triceps", "abdominals",
      "glutes"], 0.3], "paddling": [["lats", "shoulders", "middle back"], ["triceps", "biceps", "abdominals",
      "forearms"], 0.3], "paddle_sports": [["lats", "shoulders", "middle back"], ["triceps", "biceps", "abdominals",
      "forearms"], 0.3], "kayaking": [["lats", "shoulders", "middle back"], ["triceps", "biceps", "abdominals",
      "forearms"], 0.3], "canoeing": [["lats", "shoulders", "middle back"], ["triceps", "biceps", "abdominals",
      "forearms"], 0.3], "water_sports": [["lats", "shoulders", "middle back"], ["triceps", "biceps", "abdominals",
      "forearms"], 0.3], "sailing": [["forearms", "abdominals", "shoulders"], ["lats", "lower back", "quadriceps"],
      0.1], "surfing": [["lats", "shoulders", "triceps"], ["chest", "abdominals", "quadriceps", "lower back"], 0.28],
      "diving": [["quadriceps", "glutes", "calves"], ["abdominals", "shoulders"], 0.1],
      "underwater_diving": [["quadriceps", "glutes", "calves"], ["abdominals", "shoulders"], 0.1],
      "triathlon": [["quadriceps", "glutes", "lats", "shoulders"], ["calves", "hamstrings", "triceps", "abdominals"],
      0.28], "swim_bike_run": [["quadriceps", "glutes", "lats", "shoulders"], ["calves", "hamstrings", "triceps",
      "abdominals"], 0.28], "transition": [["quadriceps", "glutes"], ["calves", "hamstrings"], 0.05],
      "basketball": [["quadriceps", "glutes", "calves"], ["hamstrings", "adductors", "abdominals", "shoulders"],
      0.28], "volleyball": [["quadriceps", "glutes", "calves"], ["shoulders", "abdominals"], 0.28],
      "soccer": [["quadriceps", "hamstrings", "glutes", "calves"], ["adductors", "abductors", "abdominals"], 0.28],
      "football": [["quadriceps", "hamstrings", "glutes", "calves"], ["adductors", "abdominals", "shoulders"], 0.28],
      "australian_football": [["quadriceps", "hamstrings", "glutes", "calves"], ["adductors", "abdominals",
      "shoulders"], 0.28], "american_football": [["quadriceps", "glutes", "hamstrings"], ["calves", "shoulders",
      "chest", "traps", "abdominals"], 0.3], "rugby": [["quadriceps", "glutes", "hamstrings"], ["calves",
      "shoulders", "traps", "abdominals"], 0.3], "handball": [["quadriceps", "glutes", "shoulders"], ["calves",
      "triceps", "abdominals", "hamstrings"], 0.28], "lacrosse": [["quadriceps", "glutes", "calves"], ["hamstrings",
      "shoulders", "forearms", "abdominals"], 0.28], "hockey": [["quadriceps", "glutes", "abductors"], ["adductors",
      "hamstrings", "abdominals", "forearms"], 0.26], "ice_skating": [["quadriceps", "glutes", "abductors"],
      ["adductors", "hamstrings", "calves", "abdominals"], 0.25], "skating": [["quadriceps", "glutes", "abductors"],
      ["adductors", "hamstrings", "calves", "abdominals"], 0.25], "disc_sports": [["quadriceps", "glutes", "calves"],
      ["shoulders", "forearms", "abdominals"], 0.18], "baseball": [["abdominals", "shoulders", "forearms"],
      ["quadriceps", "glutes", "lower back"], 0.12], "softball": [["abdominals", "shoulders", "forearms"],
      ["quadriceps", "glutes", "lower back"], 0.12], "cricket": [["shoulders", "abdominals", "quadriceps"],
      ["forearms", "glutes", "calves"], 0.12], "tennis": [["quadriceps", "glutes", "calves"], ["shoulders",
      "forearms", "abdominals"], 0.25], "badminton": [["quadriceps", "glutes", "calves"], ["shoulders", "forearms",
      "abdominals"], 0.25], "squash": [["quadriceps", "glutes", "calves"], ["shoulders", "forearms", "abdominals"],
      0.25], "racquetball": [["quadriceps", "glutes", "calves"], ["shoulders", "forearms", "abdominals"], 0.25],
      "pickleball": [["quadriceps", "calves"], ["glutes", "shoulders", "forearms"], 0.18],
      "table_tennis": [["calves"], ["quadriceps", "shoulders", "forearms"], 0.12], "golf": [["abdominals",
      "shoulders", "forearms"], ["glutes", "lower back", "quadriceps"], 0.08], "bowling": [["forearms", "abdominals",
      "quadriceps"], ["shoulders", "lower back", "glutes"], 0.06], "archery": [["shoulders", "traps", "middle back"],
      ["forearms", "biceps", "abdominals"], 0.1], "fencing": [["quadriceps", "glutes", "calves"], ["shoulders",
      "forearms", "abdominals"], 0.25], "curling": [["quadriceps", "abdominals", "glutes"], ["shoulders",
      "forearms"], 0.1], "horseback_riding": [["adductors", "abdominals", "glutes"], ["quadriceps", "lower back",
      "forearms"], 0.12], "equestrian": [["adductors", "abdominals", "glutes"], ["quadriceps", "lower back",
      "forearms"], 0.12], "fishing": [["forearms", "abdominals", "quadriceps"], ["shoulders", "lower back",
      "glutes"], 0.06], "hunting": [["quadriceps", "glutes", "calves"], ["lower back", "shoulders", "forearms"],
      0.1], "skiing": [["quadriceps", "glutes", "hamstrings"], ["abdominals", "calves", "adductors", "abductors",
      "lower back"], 0.3], "alpine_skiing": [["quadriceps", "glutes", "hamstrings"], ["abdominals", "calves",
      "adductors", "abductors", "lower back"], 0.3], "snowboarding": [["quadriceps", "glutes", "hamstrings"],
      ["abdominals", "calves", "adductors", "abductors", "lower back"], 0.3], "snow_sports": [["quadriceps",
      "glutes", "hamstrings"], ["abdominals", "calves", "adductors", "abductors", "lower back"], 0.3],
      "downhill_skiing": [["quadriceps", "glutes", "hamstrings"], ["abdominals", "calves", "adductors", "abductors",
      "lower back"], 0.3], "cross_country_skiing": [["quadriceps", "glutes", "lats", "triceps"], ["hamstrings",
      "calves", "shoulders", "abdominals"], 0.35], "boxing": [["shoulders", "triceps"], ["chest", "abdominals",
      "calves", "quadriceps"], 0.3], "kickboxing": [["shoulders", "triceps", "quadriceps"], ["chest", "abdominals",
      "glutes", "calves", "lats"], 0.3], "martial_arts": [["quadriceps", "glutes"], ["shoulders", "abdominals",
      "hamstrings"], 0.3], "wrestling": [["shoulders", "triceps", "quadriceps"], ["chest", "abdominals", "glutes",
      "calves", "lats"], 0.3], "tai_chi": [["quadriceps", "glutes", "calves"], ["abdominals", "shoulders"], 0.12],
      "climbing": [["forearms", "lats", "biceps"], ["middle back", "shoulders", "abdominals"], 0.4],
      "rock_climbing": [["forearms", "lats", "biceps"], ["middle back", "shoulders", "abdominals"], 0.4],
      "gymnastics": [["shoulders", "abdominals", "chest", "lats"], ["triceps", "quadriceps", "forearms"], 0.4],
      "strength_training": [["chest", "lats", "middle back", "shoulders", "quadriceps", "glutes", "hamstrings"],
      ["biceps", "triceps", "abdominals", "lower back", "calves", "forearms", "traps"], 1.0],
      "fitness_equipment": [["chest", "lats", "middle back", "shoulders", "quadriceps", "glutes", "hamstrings"],
      ["biceps", "triceps", "abdominals", "lower back", "calves", "forearms", "traps"], 1.0],
      "functional_strength_training": [["chest", "lats", "middle back", "shoulders", "quadriceps", "glutes",
      "hamstrings"], ["biceps", "triceps", "abdominals", "lower back", "calves", "forearms", "traps"], 1.0],
      "weightlifting": [["chest", "lats", "middle back", "shoulders", "quadriceps", "glutes", "hamstrings"],
      ["biceps", "triceps", "abdominals", "lower back", "calves", "forearms", "traps"], 1.0], "crossfit": [["chest",
      "lats", "middle back", "shoulders", "quadriceps", "glutes", "hamstrings"], ["biceps", "triceps", "abdominals",
      "lower back", "calves", "forearms", "traps"], 1.0], "calisthenics": [["chest", "lats", "middle back",
      "shoulders", "quadriceps", "glutes", "hamstrings"], ["biceps", "triceps", "abdominals", "lower back", "calves",
      "forearms", "traps"], 1.0], "bodyweight": [["chest", "lats", "middle back", "shoulders", "quadriceps",
      "glutes", "hamstrings"], ["biceps", "triceps", "abdominals", "lower back", "calves", "forearms", "traps"],
      1.0], "core_training": [["abdominals"], ["lower back", "glutes", "shoulders"], 0.6],
      "cardio_training": [["quadriceps", "glutes", "abdominals"], ["calves", "hamstrings", "shoulders", "chest",
      "lats"], 0.28], "mixed_cardio": [["quadriceps", "glutes", "abdominals"], ["calves", "hamstrings", "shoulders",
      "chest", "lats"], 0.28], "cross_training": [["quadriceps", "glutes", "abdominals"], ["calves", "hamstrings",
      "shoulders", "chest", "lats"], 0.28], "aerobics": [["quadriceps", "glutes", "abdominals"], ["calves",
      "hamstrings", "shoulders", "chest", "lats"], 0.28], "mixed_metabolic_cardio_training": [["quadriceps",
      "glutes", "abdominals"], ["calves", "hamstrings", "shoulders", "chest", "lats"], 0.28],
      "barre": [["quadriceps", "glutes", "abdominals"], ["calves", "shoulders", "adductors"], 0.25],
      "hiit": [["quadriceps", "glutes", "abdominals"], ["calves", "hamstrings", "shoulders", "chest", "lats"], 0.4],
      "dance": [["quadriceps", "glutes", "calves"], ["hamstrings", "abdominals", "adductors"], 0.22],
      "social_dance": [["quadriceps", "glutes", "calves"], ["hamstrings", "abdominals", "adductors"], 0.22],
      "cardio_dance": [["quadriceps", "glutes", "calves"], ["hamstrings", "abdominals", "adductors"], 0.22],
      "dance_inspired_training": [["quadriceps", "glutes", "calves"], ["hamstrings", "abdominals", "adductors"],
      0.22], "yoga": [["abdominals", "quadriceps", "glutes"], ["shoulders", "lower back", "hamstrings", "triceps"],
      0.15], "mind_and_body": [["abdominals", "quadriceps", "glutes"], ["shoulders", "lower back", "hamstrings",
      "triceps"], 0.15], "pilates": [["abdominals", "glutes"], ["lower back", "adductors", "quadriceps",
      "shoulders"], 0.25], "stretching": [["hamstrings", "quadriceps", "lower back"], ["glutes", "calves",
      "shoulders"], 0.05], "flexibility": [["hamstrings", "quadriceps", "lower back"], ["glutes", "calves",
      "shoulders"], 0.05], "cooldown": [["hamstrings", "quadriceps", "lower back"], ["glutes", "calves",
      "shoulders"], 0.05], "preparation_and_recovery": [["hamstrings", "quadriceps", "lower back"], ["glutes",
      "calves", "shoulders"], 0.05], "play": [["quadriceps", "glutes", "calves"], ["abdominals", "shoulders"], 0.1],
      "gaming": [["forearms"], ["shoulders", "neck"], 0.03], "fitness_gaming": [["quadriceps", "shoulders"],
      ["glutes", "abdominals", "forearms"], 0.1]},
    FALLBACK: [["quadriceps", "glutes", "lats", "chest", "abdominals"], ["shoulders", "middle back", "lower back",
      "traps", "hamstrings", "calves", "biceps", "triceps", "forearms", "adductors", "abductors", "neck"], 0.1],
    FALLBACK_TYPES: ["generic", "other", "unknown"],
    STRENGTH_LIKE: ["bodyweight", "calisthenics", "crossfit", "fitness_equipment", "functional_strength_training",
      "strength_training", "weightlifting"],
    MUSCLE_ZH: {"chest": "胸部", "lats": "背阔肌", "middle back": "中背部", "lower back": "下背部", "traps": "斜方肌",
      "shoulders": "肩部", "quadriceps": "股四头肌", "hamstrings": "腘绳肌", "glutes": "臀部", "calves": "小腿", "biceps": "肱二头肌",
      "triceps": "肱三头肌", "forearms": "前臂", "abdominals": "核心 / 腹肌", "adductors": "大腿内侧", "abductors": "臀中肌 / 外展",
      "neck": "颈部"},
    MUSCLE_GROUP: {"chest": "chest", "lats": "back", "middle back": "back", "lower back": "core",
      "traps": "shoulders", "shoulders": "shoulders", "quadriceps": "legs", "hamstrings": "legs", "glutes": "legs",
      "calves": "legs", "adductors": "legs", "abductors": "legs", "biceps": "arms", "triceps": "arms",
      "forearms": "arms", "abdominals": "core"},
    MUSCLE_EN: {"chest": "Chest", "lats": "Lats", "middle back": "Mid back", "lower back": "Lower back",
      "traps": "Traps", "shoulders": "Shoulders", "quadriceps": "Quads", "hamstrings": "Hamstrings",
      "glutes": "Glutes", "calves": "Calves", "biceps": "Biceps", "triceps": "Triceps", "forearms": "Forearms",
      "abdominals": "Core / abs", "adductors": "Inner thigh", "abductors": "Glute med / abductors", "neck": "Neck"},
    WEEKLY_SETS_RANGE: {"lower back": [4, 10], "neck": [4, 10], "forearms": [6, 14], "adductors": [4, 12],
      "abductors": [4, 12], "abdominals": [6, 16], "traps": [6, 16], "calves": [8, 16]}
  };
  PARAMS.WEEKLY_SETS_RANGE = TABLES.WEEKLY_SETS_RANGE;
  delete TABLES.WEEKLY_SETS_RANGE;

  function set(xs) { return new Set(xs); }
  var GENERIC = set(PARAMS.GENERIC_TYPES), STRENGTH = set(PARAMS.STRENGTH_TYPES), CARDIO = set(PARAMS.CARDIO_TYPES),
    SPORT = set(PARAMS.SPORT_TYPES), WARMUP = set(PARAMS.WARMUP_SET_TYPES), FALLBACK_TYPES = set(TABLES.FALLBACK_TYPES),
    STRENGTH_LIKE = set(TABLES.STRENGTH_LIKE);
  var COUNTED_BY_SETS = set(TABLES.STRENGTH_LIKE.concat(PARAMS.STRENGTH_TYPES));
  var IMPORT_FLAGS = set(["implausible_duration", "energy_from_shortcut"]);
  var ACT_BY_NAME = {};
  Object.keys(TABLES.HK_WORKOUT_ACTIVITIES).forEach(function (k) {
    var v = TABLES.HK_WORKOUT_ACTIVITIES[k];
    ACT_BY_NAME[v[0]] = v[1];
  });
  var LEVEL_RANK = {};
  PARAMS.INTENSITY_LEVELS.forEach(function (l, i) { LEVEL_RANK[l] = i; });
  var TYPE_OF_ZH = {};
  Object.keys(TABLES.TYPE_NAMES).forEach(function (t) { TYPE_OF_ZH[TABLES.TYPE_NAMES[t]] = t; });

  function en() { return OBS.lang === "en"; }
  function L(zh, enText) { return en() ? enText : zh; }
  function has(o, k) { return o !== null && o !== undefined && Object.prototype.hasOwnProperty.call(o, k); }
  function get(o, k, dflt) { return has(o, k) && o[k] !== undefined ? o[k] : (dflt === undefined ? null : dflt); }
  function nn(x) { return x === undefined ? null : x; }
  function us(s) { return Math.round(s * 1e6); }
  function secs(u) { return u / US_SEC; }
  function pct0(x) { return U.round(x * 100) + "%"; }          // Python f"{x:.0%}"
  function f0(x) { return String(U.round(x)); }               // Python f"{x:.0f}"
  function fmtG(x) { return String(Number(x.toPrecision(6))); }   // Python f"{x:g}" (6 significant digits)
  function rN(x) { return x === null || x === undefined ? null : U.round(x); }   // history._r
  function pyIn(s, sub) { return (s || "").indexOf(sub) >= 0; }

  // ------------------------------------------------------------------ types (training/params.py, importer.py)
  function type_name(t) { return has(TABLES.TYPE_NAMES, t) ? TABLES.TYPE_NAMES[t] : t.split("_").join(" "); }

  function hk_workout_type(activity, name) {
    var sdk = activity !== null && activity !== undefined ? (TABLES.HK_WORKOUT_ACTIVITIES[String(activity)] || [null, null])[1] : null;
    sdk = sdk || (has(ACT_BY_NAME, name || "") ? ACT_BY_NAME[name || ""] : "other");
    return has(TABLES.OW_SDK_WORKOUT_TYPES, sdk) ? TABLES.OW_SDK_WORKOUT_TYPES[sdk] : "other";
  }

  function base_row(tz, start, end) {
    var flags = {}, excluded = null;
    if (end - start > PARAMS.MAX_DURATION_S * US_SEC) {
      excluded = "implausible_duration";
      flags.implausible_duration = "时长 " + f0(secs(end - start) / 3600) + " 小时，像是没结束的记录，不计入";
    }
    return { local_date: U.localDate(start, tz), start_at: start, end_at: end,
      duration_min: U.round(secs(end - start) / 60, 2), excluded: excluded, flags: flags };
  }

  function zone_offset(at, tz) { return U.offsetStr(tz.offsetAt(at)); }

  /** importer.parse_app: one HKWorkout of the phone -> a workouts row (start / end as microseconds). */
  function parse_app(r, tz, shadow) {
    var meta = r.meta || {}, stats = meta.stats || {};
    var row = base_row(tz, r.start_at, r.end_at);
    if (r.value && !row.excluded) row.duration_min = U.round(r.value, 2);
    var active = get(stats, "active_kcal"), basal = get(stats, "basal_kcal");
    var total = active === null && basal === null ? null : (active || 0) + (basal || 0);
    var t = hk_workout_type(get(meta, "activity"), get(r, "value_text"));
    var ztz = tz;
    if (r.tz) { try { ztz = U.tzOf(r.tz); ztz.offsetAt(r.start_at); } catch (e) { ztz = tz; } }
    var dist = get(stats, "distance_m");
    row.source = "app";
    row.external_id = String(r.uuid).toLowerCase();       // as the server's hk_samples.hk_uuid::text
    row.origin = r.source_name || r.source || null;
    row.is_xml = false;
    row.device = r.device_name || r.source_name || r.source || null;
    row.type = t;
    row.name_zh = type_name(t);
    row.total_kcal = total === null ? null : U.round(total, 1);
    row.active_kcal = active === null ? null : U.round(active, 1);
    row.distance_km = dist === null ? null : U.round(dist / 1000, 3);
    row.avg_hr = get(stats, "avg_hr");
    row.max_hr = get(stats, "max_hr");
    row.zone_offset = zone_offset(r.start_at, ztz);
    row.device_type = r.device_type || null;
    if (shadow) row.excluded = "shadow";
    return row;
  }

  // ------------------------------------------------------------------ the user's corrections (training/overrides.py)
  function rule_for(rules, start, tz) {
    var p = U.local(start, tz), wd = U.weekday(p.date), h = p.hour + p.minute / 60;
    for (var i = 0; i < rules.length; i++) {
      if (rules[i].weekday === wd && Math.abs(rules[i].start_hour - h) <= PARAMS.RULE_HOUR_WINDOW) return rules[i];
    }
    return null;
  }

  function apply_override(row, ctx) {
    var ov = ctx.overrides.get(row.source + "\u0000" + row.external_id), r;
    if (ov) {
      if (ov.type) {
        row.type = ov.type;
        row.name_zh = ov.name_zh || (has(TABLES.TYPE_NAMES, ov.type) ? TABLES.TYPE_NAMES[ov.type] : ov.type);
      }
      if (ov.name_zh) row.name_zh = ov.name_zh;
      if (ov.hidden) row.excluded = "user_hidden";
    } else if (GENERIC.has(row.type) && (r = rule_for(ctx.rules, row.start_at, ctx.tz))) {
      row.type = r.to_type;
      row.name_zh = r.name_zh || (has(TABLES.TYPE_NAMES, r.to_type) ? TABLES.TYPE_NAMES[r.to_type] : r.to_type);
    }
    return row;
  }

  // ------------------------------------------------------------------ duplicates (importer.duplicates)
  function rank(w, ranks) {
    var dev = (w.device || "").toLowerCase();
    var device = pyIn(dev, "watch") ? 0 : (pyIn(dev, "iphone") || pyIn(dev, "phone")) ? 1 : 2;
    return [GENERIC.has(w.type) ? 1 : 0, has(ranks, w.source) ? ranks[w.source] : 9, w.is_xml ? 1 : 0, device, w.id];
  }

  function is_dup(a, b) {
    var same = PARAMS.DUP_SAME_TIME_S * US_SEC;
    if (Math.abs(a.start_at - b.start_at) <= same && Math.abs(a.end_at - b.end_at) <= same) return true;
    var overlap = secs(Math.min(a.end_at, b.end_at) - Math.max(a.start_at, b.start_at));
    var shorter = Math.min(secs(a.end_at - a.start_at), secs(b.end_at - b.start_at));
    var same_type = a.type === b.type || GENERIC.has(a.type) || GENERIC.has(b.type);
    if (a.source === "hevy" || b.source === "hevy") same_type = same_type || (STRENGTH.has(a.type) && STRENGTH.has(b.type));
    return same_type && shorter > 0 && overlap / shorter >= PARAMS.DUP_OVERLAP;
  }

  /** {id: id it duplicates | null}, as a Map (ids are the "source:external_id" keys). */
  function duplicates(rows, ranks) {
    var same = PARAMS.DUP_SAME_TIME_S * US_SEC, kept = [], target = new Map(), manual = new Map();
    rows.forEach(function (w) { if ((w.flags || {}).manual_dup && w.dup_of) manual.set(w.id, w.dup_of); });
    var sorted = rows.map(function (w) { return { w: w, k: rank(w, ranks) }; })
      .sort(function (a, b) { return U.cmp(a.k, b.k); }).map(function (x) { return x.w; });
    sorted.forEach(function (w) {
      if (manual.has(w.id)) { target.set(w.id, manual.get(w.id)); return; }
      if (w.excluded && w.excluded !== "user_hidden") { target.set(w.id, null); return; }
      var hit = null;
      for (var i = 0; i < kept.length; i++) {
        var k = kept[i];
        if (k.start_at < w.end_at + same && w.start_at < k.end_at + same && is_dup(k, w)) { hit = k; break; }
      }
      target.set(w.id, hit ? hit.id : null);
      if (!hit) kept.push(w);
    });
    return target;
  }

  // ------------------------------------------------------------------ heart rate, TRIMP, recovery (importer.py, hrr.py)
  /** hr = [[t (µs), bpm]] sorted. -> {trimp, zones, covered_min, samples} (Edwards zones of OBS.params). */
  function session_trimp(hr, start, end, hr_max) {
    var xs = hr.filter(function (x) { return start <= x[0] && x[0] <= end; });
    var zones = [0.0, 0.0, 0.0, 0.0, 0.0], covered = 0.0, cap = PARAMS.HR_CAP_S * US_SEC;
    for (var i = 0; i < xs.length; i++) {
      var t = xs[i][0], nxt = i + 1 < xs.length ? xs[i + 1][0] : end;
      var minutes = Math.max(0.0, secs(Math.min(nxt, t + cap, end) - t) / 60);
      covered += minutes;
      var w = S.zone_weight(xs[i][1], hr_max);
      if (w) zones[w - 1] += minutes;
    }
    return { trimp: U.pySum(zones.map(function (m, i) { return m * (i + 1); })), zones: zones, covered_min: covered,
      samples: xs.length };
  }

  function avg_hr_trimp(avg_hr, minutes, hr_max) { return minutes * S.zone_weight(avg_hr, hr_max); }

  function session_hr_recovery(hr, end) {
    var last = [], after = [], lo = end + PARAMS.HRR_AFTER_S[0] * US_SEC, hi = end + PARAMS.HRR_AFTER_S[1] * US_SEC;
    hr.forEach(function (x) {
      if (end - 60 * US_SEC <= x[0] && x[0] <= end) last.push(x[1]);
      if (lo <= x[0] && x[0] <= hi) after.push([Math.abs(secs(x[0] - end) - 60), x[1]]);
    });
    if (!last.length || !after.length) return null;
    var best = after[0];
    for (var i = 1; i < after.length; i++) if (U.cmp(after[i], best) < 0) best = after[i];
    return U.round(last[last.length - 1] - best[1], 1);
  }

  /** importer.hr_enrich -> [upd {trimp, hr_zone_min, hr_samples, hr_max_used}, flags]. */
  function hr_enrich(hr_all, end, start, duration_min, hr_max) {
    var flags = {}, hr = hr_all.filter(function (x) { return x[0] <= end; });
    var hrr = session_hr_recovery(hr_all, end);
    if (hrr !== null) flags.hr_recovery_bpm = hrr;
    var upd = { trimp: null, hr_zone_min: null, hr_samples: hr.length || null, hr_max_used: hr_max };
    if (hr.length && hr_max) {
      var res = session_trimp(hr, start, end, hr_max);
      var cover = res.covered_min / Math.max(duration_min, 1e-6);
      upd.hr_zone_min = res.zones.map(function (z) { return U.round(z, 1); });
      if (cover >= PARAMS.MIN_HR_COVERAGE) upd.trimp = U.round(res.trimp, 1);
      else flags.hr_coverage_low = "心率只覆盖了训练的 " + pct0(cover) + "，不出 TRIMP（强度按平均心率估）";
    } else if (!hr.length) {
      flags.no_raw_hr = "训练时段没有原始心率（手表没戴 / 没同步，或 2025-08 之前没有手表）";
    }
    return [upd, flags];
  }

  /** cardio/hrr.hrr60: hr = [[t (µs), bpm]] sorted. */
  function hrr60(hr, end, hr_max) {
    var times = hr.map(function (x) { return x[0]; });
    var a = U.bisectLeft(times, end - PARAMS.HRR_PEAK_WINDOW_S * US_SEC), b = U.bisectRight(times, end + 5 * US_SEC);
    var out = { peak_hr: null, hr_60s: null, hrr60: null, valid: false, reason: null };
    if (b <= a) { out.reason = "训练最后 2 分钟没有心率"; return out; }
    var peak = -Infinity;
    for (var k = a; k < b; k++) if (hr[k][1] > peak) peak = hr[k][1];
    var target = end + PARAMS.HRR_AFTER60_S * US_SEC, tol = PARAMS.HRR_TOLERANCE_S * US_SEC;
    var i = U.bisectLeft(times, target);
    var before = i > 0 && hr[i - 1][0] > end + 30 * US_SEC ? hr[i - 1] : null;
    var after = i < hr.length ? hr[i] : null;
    var h60 = null;
    if (before && after && after[0] - before[0] <= 2 * tol) {
      var span = secs(after[0] - before[0]);
      var w = span ? secs(target - before[0]) / span : 0.0;
      h60 = before[1] + (after[1] - before[1]) * w;
    } else {
      var near = [before, after].filter(function (x) { return x && Math.abs(x[0] - target) <= tol; });
      if (near.length) h60 = U.minBy(near, function (x) { return Math.abs(x[0] - target); })[1];
    }
    out.peak_hr = U.round(peak, 1);
    if (h60 === null) { out.reason = "结束后 60 秒附近没有心率（手表可能没在高频记录）"; return out; }
    out.hr_60s = U.round(h60, 1);
    out.hrr60 = U.round(peak - h60, 1);
    if (hr_max && peak < PARAMS.HRR_PEAK_MIN_PCT * hr_max) {
      out.reason = "结束前最高心率只有最大心率的 " + pct0(peak / hr_max) + "（要 ≥ " + pct0(PARAMS.HRR_PEAK_MIN_PCT) +
        "），这次的下降不代表心肺恢复";
      return out;
    }
    out.valid = true;
    return out;
  }

  // ------------------------------------------------------------------ the phone's workouts (importer.workout_days)
  function pairs(x) {
    if (x && !Array.isArray(x) && x.cols) {
      var i = x.cols.indexOf("t"), j = x.cols.indexOf("value");
      return x.rows.map(function (r) { return [r[i], r[j]]; });
    }
    return (x || []).map(function (r) { return [r[0], r[1]]; });
  }

  /** importer.heart_rate: [[t (s), bpm]] -> [[µs, bpm]] sorted, the first value of an instant kept. */
  function heart_rate(samples) {
    var seen = new Map();
    pairs(samples).forEach(function (p) { if (!seen.has(p[0])) seen.set(p[0], p[1]); });
    return Array.from(seen.keys()).sort(function (a, b) { return a - b; })
      .map(function (t) { return [us(t), Number(seen.get(t))]; });
  }

  function workout_days(src) {
    var tz = U.tzOf(src.tz), want = new Set(src.days || []);
    var ctx = { overrides: new Map(), rules: src.type_rules || [], tz: tz };
    (src.overrides || []).forEach(function (o) { ctx.overrides.set(o.source + "\u0000" + o.external_id, o); });
    var rows = [];
    (src.hk_workouts || []).forEach(function (r) {
      var w = parse_app(Object.assign({}, r, { start_at: us(r.start), end_at: us(r.end) }), tz, false);
      w = apply_override(w, ctx);
      w.id = "app:" + w.external_id;
      w.dup_of = null;
      rows.push(w);
    });
    (src.others || []).forEach(function (o) {
      rows.push({ id: o.source + ":" + o.external_id, source: o.source, external_id: o.external_id,
        type: o.type || "other", is_xml: !!o.is_xml, device: nn(o.device), start_at: us(o.start), end_at: us(o.end),
        excluded: nn(o.excluded), dup_of: nn(o.dup_of), flags: o.manual_dup ? { manual_dup: true } : {} });
    });
    var target = duplicates(rows, PARAMS.SOURCE_RANK_APP);
    var hr = heart_rate(src.hr), times = hr.map(function (x) { return x[0]; });
    var hrMax = {};
    Object.keys(src.hr_max || {}).forEach(function (k) { if (src.hr_max[k]) hrMax[k] = Number(src.hr_max[k]); });
    var birth = src.birth_date || null, days = {};
    var mine = rows.filter(function (w) { return w.source === "app"; }).sort(function (a, b) {
      return a.start_at - b.start_at || (a.external_id < b.external_id ? -1 : a.external_id > b.external_id ? 1 : 0);
    });
    mine.forEach(function (w) {
      var d = w.local_date;
      if (!want.has(d)) return;
      var dup = target.has(w.id) ? target.get(w.id) : null, flags = {};
      Object.keys(w.flags).forEach(function (k) { if (IMPORT_FLAGS.has(k)) flags[k] = w.flags[k]; });
      var out = { external_id: w.external_id, type: w.type, name_zh: w.name_zh, local_date: d, start: secs(w.start_at),
        end: secs(w.end_at), duration_min: w.duration_min, active_kcal: w.active_kcal, total_kcal: w.total_kcal,
        distance_km: w.distance_km, avg_hr: w.avg_hr, max_hr: w.max_hr, zone_offset: w.zone_offset, device: w.device,
        origin: w.origin, excluded: w.excluded, dup_of: dup, trimp: null, hr_samples: null, hr_max_used: null,
        zone1_min: null, zone2_min: null, zone3_min: null, zone4_min: null, zone5_min: null, hr_recovery_bpm: null,
        intensity: null, hrr60: null, hrr_peak: null, hrr_60s: null, hrr_valid: null, hrr_reason: null };
      if (dup === null && w.excluded === null) {
        var hm = has(hrMax, d) ? hrMax[d] : null;
        if (hm === null && birth) hm = S.hrmax_formula(S.age_on(birth, d));
        var lo = U.bisectLeft(times, w.start_at), hi = U.bisectRight(times, w.end_at + PARAMS.HRR_TAIL_S * US_SEC);
        var res = hr_enrich(hr.slice(lo, hi), w.end_at, w.start_at, w.duration_min, hm), upd = res[0], hf = res[1];
        out.hr_recovery_bpm = has(hf, "hr_recovery_bpm") ? hf.hr_recovery_bpm : null;
        delete hf.hr_recovery_bpm;
        Object.keys(hf).forEach(function (k) { flags[k] = hf[k]; });
        out.trimp = upd.trimp;
        out.hr_samples = upd.hr_samples;
        out.hr_max_used = upd.hr_max_used;
        for (var i = 0; i < 5; i++) out["zone" + (i + 1) + "_min"] = upd.hr_zone_min ? upd.hr_zone_min[i] : null;
        var r60 = hrr60(hr, w.end_at, has(hrMax, d) ? hrMax[d] : null);
        out.hrr60 = r60.hrr60;
        out.hrr_peak = r60.peak_hr;
        out.hrr_60s = r60.hr_60s;
        out.hrr_valid = r60.valid;
        out.hrr_reason = r60.reason;
        out.intensity = workout_intensity(out);
        flags.computed = PARAMS.TRAINING_VERSION;
      }
      out.flags = flags;
      (days[d] = days[d] || []).push(out);
    });
    var outDays = {}, dups = {};
    Array.from(want).sort().forEach(function (d) { outDays[d] = days[d] || []; });
    Array.from(target.keys()).sort().forEach(function (k) { if (target.get(k)) dups[k] = target.get(k); });
    return { algo_version: PARAMS.ALGO_VERSION, days: outDays, dups: dups };
  }

  // ------------------------------------------------------------------ training calendar / day (training/history.py)
  var STATUS_ZH = { banned: "禁用", caution: "注意", active: "正常" };
  var STATUS_EN = { banned: "Banned", caution: "Caution", active: "OK" };
  var BASIS_ZH = { trimp: "按心率（TRIMP）", avg_hr: "按平均心率估", volume: "按力量训练量", duration: "按时长", none: "没有数据" };
  var BASIS_EN = { trimp: "By heart rate (TRIMP)", avg_hr: "Estimated from average HR", volume: "By strength volume",
    duration: "By duration", none: "No data" };
  var SOURCE_EN = { "快捷指令": "Shortcut", "Apple 健康": "Apple Health" };
  var CALENDAR_DAY_KEYS = ["date", "weekday", "today", "future", "sessions_count", "duration_min", "active_kcal",
    "workout_kcal", "total_kcal", "exercise_min", "steps", "strain", "muscle_load", "recovery", "intensity",
    "intensity_label", "types", "labels", "flags"];

  function level_of(value, thresholds) {
    for (var i = 0; i < thresholds.length; i++) if (value >= thresholds[i][0]) return thresholds[i][1];
    return "light";
  }
  function max_level(levels) {
    var xs = levels.filter(function (x) { return x; });
    return xs.length ? U.maxBy(xs, function (l) { return LEVEL_RANK[l]; }) : null;
  }
  function category(t) {
    if (SPORT.has(t)) return "sport";
    if (STRENGTH.has(t)) return "strength";
    if (CARDIO.has(t)) return "cardio";
    return "other";
  }
  function intensity_label(level) { return en() ? PARAMS.INTENSITY_LABELS_EN[level] : PARAMS.INTENSITY_LABELS[level]; }
  function working(sets) { return sets.filter(function (s) { return !WARMUP.has(String(s.type || "").toLowerCase()); }); }
  function is_cardio(e) { return (e.category || "strength") === "cardio"; }

  function volume(exercises) {
    var vol = 0.0, n = 0;
    exercises.forEach(function (e) {
      if (is_cardio(e) || (e.category || "strength") !== "strength") return;
      working(e.sets).forEach(function (s) {
        if (nn(s.reps) === null && nn(s.duration_s) === null) return;
        n += 1;
        if (s.reps && s.weight_kg) vol += s.reps * s.weight_kg;
      });
    });
    return [U.round(vol, 1), n];
  }

  function exercise_view(e, ctx) {
    var status = "active";
    if (ctx && e.exercise_id) status = has(ctx, e.exercise_id) ? ctx[e.exercise_id] : "active";
    var out = { exercise_id: get(e, "exercise_id"), name: get(e, "name"), category: get(e, "category"),
      status: en() ? (STATUS_EN[status] || "OK") : (STATUS_ZH[status] || "正常"), status_key: status,
      notes: get(e, "notes"), duration_min: e.duration_minutes || null,
      sets: e.sets.map(function (s) {
        return { set: get(s, "set"), type: get(s, "type"), reps: get(s, "reps"), weight_kg: get(s, "weight_kg"),
          rir: get(s, "rir"), rpe: get(s, "rpe"), duration_s: get(s, "duration_s"), distance_km: get(s, "distance_km"),
          notes: get(s, "notes") };
      }) };
    if (e.slug) out.slug = e.slug;
    if (e.unmatched) out.unmatched = true;
    return out;
  }

  function source_label(w) {
    if (w.source === "shortcut") return "快捷指令";
    if (w.source === "hevy") return "Hevy";
    if (pyIn((w.device || "").toLowerCase(), "watch")) return "Apple Watch";
    if (w.origin && w.origin !== "apple_health_xml") return w.origin;
    return "Apple 健康";
  }

  function from_workout(w, tz) {
    var flags = {};
    Object.keys(w.flags || {}).forEach(function (k) { if (k !== "computed" && k !== "basal_kcal" && k !== "note") flags[k] = w.flags[k]; });
    return { id: "w" + w.id, workout_id: w.id, source: w.source, source_label: source_label(w), type: w.type,
      type_name: type_name(w.type), name: w.name_zh, start: U.isoformat(w.start_at, tz, "minutes"),
      end: U.isoformat(w.end_at, tz, "minutes"), duration_min: U.round(w.duration_min), active_kcal: rN(w.active_kcal),
      total_kcal: rN(w.total_kcal), distance_km: nn(w.distance_km) === null ? null : U.round(w.distance_km, 2),
      avg_hr: rN(w.avg_hr), max_hr: rN(w.max_hr), hr_zone_min: nn(w.hr_zone_min), trimp: nn(w.trimp),
      hr_max: nn(w.hr_max_used), device: get(w, "device"), flags: flags, exercises: [], volume_kg: null,
      working_sets: null, notes: null, sparky_id: null, _cat: category(w.type) };
  }

  function from_sparky(s, exercises) {
    var minutes = s.total_duration_minutes || U.pySum(exercises.map(function (e) { return e.duration_minutes || 0; })) || null;
    var cardio_only = exercises.length > 0 && exercises.every(is_cardio);
    var t = cardio_only ? "cardio_training" : "strength_training";
    return { id: "s" + s.id, workout_id: null, source: "sparkyfitness", source_label: "SparkyFitness", type: t,
      type_name: type_name(t), name: s.name || type_name(t), start: null, end: null, duration_min: minutes,
      active_kcal: null, total_kcal: null, distance_km: null, avg_hr: null, max_hr: null, hr_zone_min: null, trimp: null,
      hr_max: null, device: null, flags: {}, exercises: exercises, volume_kg: null, working_sets: null,
      notes: get(s, "notes"), sparky_id: get(s, "id"), _cat: cardio_only ? "cardio" : "strength" };
  }

  function add_notes(sess, notes) {
    if (notes && !pyIn(sess.notes || "", notes)) sess.notes = (sess.notes ? sess.notes + "\n" : "") + notes;
  }

  function attach(sess, s, exercises, rename) {
    sess.exercises = sess.exercises.concat(exercises);
    sess.sparky_id = sess.sparky_id || get(s, "id");
    if (!pyIn(sess.source, "sparkyfitness")) sess.source = sess.source + "+sparkyfitness";
    if (!pyIn(sess.source_label, "SparkyFitness")) sess.source_label = sess.source_label + " + SparkyFitness";
    add_notes(sess, s.notes);
    if (rename) sess.name = rename;
  }

  function attach_imported(sessions, imported, targets) {
    var byId = new Map();
    sessions.forEach(function (x) { if (x.workout_id) byId.set(x.workout_id, x); });
    imported.forEach(function (s) {
      var sess = byId.get(has(targets, s.workout) ? targets[s.workout] : null);
      if (!sess) return;
      sess.exercises = sess.exercises.concat(s.exercises);
      if (sess.source !== "hevy" && !pyIn(sess.source, "hevy")) {
        sess.source = sess.source + "+hevy";
        sess.source_label = sess.source_label + " + Hevy";
        if (s.name) sess.name = sess.type_name + " · " + s.name;
      }
      add_notes(sess, s.notes);
    });
  }

  function merge_day(workouts, sparky, tz, imported, targets) {
    var sessions = workouts.map(function (w) { return from_workout(w, tz); });
    if (imported && imported.length) attach_imported(sessions, imported, targets || {});
    var extra = [], usedS = new Set(), usedC = new Set();
    sparky.forEach(function (s) {
      var strength = s.exercises.filter(function (e) { return !is_cardio(e); });
      var cardio = s.exercises.filter(is_cardio), left = [];
      if (strength.length) {
        var cands = sessions.filter(function (x) { return x._cat === "strength" && !usedS.has(x.id); });
        if (cands.length) {
          var target = U.maxBy(cands, function (x) { return x.duration_min || 0; });
          usedS.add(target.id);
          attach(target, s, strength, s.name ? target.type_name + " · " + s.name : null);
        } else left = left.concat(strength);
      }
      cardio.forEach(function (e) {
        var c = sessions.filter(function (x) { return x._cat === "cardio" && !usedC.has(x.id); });
        if (c.length) {
          var want = e.duration_minutes || 0;
          var t = U.minBy(c, function (x) { return want ? Math.abs((x.duration_min || 0) - want) : -(x.duration_min || 0); });
          usedC.add(t.id);
          attach(t, s, [e], get(e, "name"));
        } else left.push(e);
      });
      if (left.length) extra.push(from_sparky(s, left));
    });
    var out = sessions.concat(extra);
    out.forEach(function (sess) {
      if (sess.exercises.some(function (e) { return !is_cardio(e); })) {
        var v = volume(sess.exercises);
        sess.volume_kg = v[0];
        sess.working_sets = v[1];
      }
    });
    return out;
  }

  function classify(sess, hr_max) {
    var basis = null, value = null, level = null;
    hr_max = sess.hr_max || hr_max;
    if (nn(sess.trimp) !== null) {
      level = level_of(sess.trimp, PARAMS.TRIMP_THRESHOLDS); basis = "trimp"; value = sess.trimp;
    } else if (sess.avg_hr && hr_max && sess.duration_min) {
      var est = avg_hr_trimp(sess.avg_hr, sess.duration_min, hr_max) * PARAMS.AVG_HR_TRIMP_FACTOR;
      level = level_of(est, PARAMS.TRIMP_THRESHOLDS); basis = "avg_hr"; value = U.round(est, 1);
    }
    if (sess.working_sets) {
      var vol = max_level([level_of(sess.volume_kg || 0, PARAMS.VOLUME_THRESHOLDS), level_of(sess.working_sets, PARAMS.SETS_THRESHOLDS)]);
      if (level === null || LEVEL_RANK[vol] > LEVEL_RANK[level]) { level = vol; basis = "volume"; value = sess.volume_kg; }
    }
    if (level === null) {
      var minutes = sess.duration_min || 0;
      level = minutes ? level_of(minutes, PARAMS.DURATION_THRESHOLDS[sess._cat]) : "light";
      basis = minutes ? "duration" : "none";
      value = minutes ? minutes : null;
    }
    if ((sess.duration_min || PARAMS.MIN_SESSION_MIN) < PARAMS.MIN_SESSION_MIN) level = "light";
    sess.intensity = level;
    sess.intensity_label = intensity_label(level);
    sess.intensity_basis = basis;
    sess.intensity_value = value;
  }

  /** history.workout_intensity: one workouts row on its own. */
  function workout_intensity(w) {
    var sess = { trimp: nn(w.trimp), avg_hr: rN(w.avg_hr), hr_max: nn(w.hr_max_used), duration_min: U.round(w.duration_min),
      working_sets: null, volume_kg: null, _cat: category(w.type) };
    classify(sess, null);
    return sess.intensity;
  }

  function display_name(name) {
    if (!name || !en()) return name;
    var i = name.indexOf(" · "), head = i >= 0 ? name.slice(0, i) : name, rest = i >= 0 ? name.slice(i) : "";
    var t = has(TYPE_OF_ZH, head) ? TYPE_OF_ZH[head] : null;
    return t ? (has(TABLES.TYPE_NAMES_EN, t) ? TABLES.TYPE_NAMES_EN[t] : head) + rest : name;
  }

  function localize(sess) {
    if (!en()) return;
    sess.name = display_name(sess.name);
    sess.type_name = has(TABLES.TYPE_NAMES_EN, sess.type) ? TABLES.TYPE_NAMES_EN[sess.type] : sess.type_name;
    var i = sess.source_label.indexOf(" + "), head = i >= 0 ? sess.source_label.slice(0, i) : sess.source_label;
    sess.source_label = (has(SOURCE_EN, head) ? SOURCE_EN[head] : head) + (i >= 0 ? sess.source_label.slice(i) : "");
  }

  function dkey(o) {   // {date-ish: v} -> {YYYY-MM-DD: v}
    var out = {};
    Object.keys(o || {}).forEach(function (k) { out[String(k).slice(0, 10)] = o[k]; });
    return out;
  }

  function hr_max_fallback(data, d) {
    var hm = dkey(data.hr_max);
    if (hm[d]) return Number(hm[d]);
    if (data.birth_date) return S.hrmax_formula(S.age_on(String(data.birth_date).slice(0, 10), d));
    return null;
  }

  /** history.compute (build without the database). */
  function compute(data, start, end, today) {
    var tz = U.tzOf(data.timezone), metrics = dkey(data.metrics), scores = dkey(data.scores), targets = data.targets || {};
    var ctx = data.status === null || data.status === undefined ? null : data.status;
    var impBy = {}, wBy = {}, sBy = {};
    (data.imported || []).forEach(function (s) { var d = String(s.date).slice(0, 10); (impBy[d] = impBy[d] || []).push(s); });
    (data.workouts || []).forEach(function (w) {
      var x = Object.assign({}, w, { start_at: us(w.start_at), end_at: us(w.end_at), local_date: String(w.local_date).slice(0, 10) });
      (wBy[x.local_date] = wBy[x.local_date] || []).push(x);
    });
    (data.sparky || []).forEach(function (s) { var d = s.date.slice(0, 10); (sBy[d] = sBy[d] || []).push(s); });
    var days = [];
    U.dateRange(start, end).forEach(function (d) {
      var sessions = merge_day(wBy[d] || [], sBy[d] || [], tz, impBy[d] || null, targets);
      var hr_max = get(scores[d] || {}, "hr_max");
      if (hr_max === null && sessions.some(function (s) { return s.avg_hr && !s.hr_max; })) hr_max = hr_max_fallback(data, d);
      sessions.forEach(function (s) {
        classify(s, hr_max);
        s.exercises = ctx !== null ? s.exercises.map(function (e) { return exercise_view(e, ctx); })
          : s.exercises.map(function (e) { return { name: get(e, "name"), category: get(e, "category") }; });
        s.intensity_basis_label = nn((en() ? BASIS_EN : BASIS_ZH)[s.intensity_basis]);
        delete s._cat;
        localize(s);
      });
      var m = metrics[d] || {}, sc = scores[d] || {};
      var intensity = max_level(sessions.map(function (s) { return s.intensity; })) || "rest";
      var wk = U.pySum(sessions.map(function (s) { return s.active_kcal || 0; }));
      var flags = {}, act = get(m, "active_kcal"), bas = get(m, "basal_kcal");
      if (act !== null && wk > act + 20) {
        flags.daily_incomplete = "全天活动能量 " + f0(act) + " kcal 比训练本身的 " + f0(wk) + " 还少：这天的每日数据只同步了一部分";
      }
      var uniq = function (xs) { return xs.filter(function (x, i) { return xs.indexOf(x) === i; }); };
      days.push({ date: d, weekday: "一二三四五六日"[U.weekday(d)], future: d > today, today: d === today,
        sessions: sessions, sessions_count: sessions.length,
        duration_min: U.pySum(sessions.map(function (s) { return s.duration_min || 0; })),
        workout_kcal: sessions.some(function (s) { return s.active_kcal; }) ? rN(wk) : null,
        active_kcal: rN(act), basal_kcal: rN(bas), total_kcal: act !== null && bas ? rN(act + bas) : null,
        exercise_min: rN(get(m, "exercise_min")), steps: rN(get(m, "steps")),
        strain: get(sc, "strain"), muscle_load: get(sc, "muscle_load"), recovery: get(sc, "recovery"),
        recovery_next_day: get(scores[U.addDays(d, 1)] || {}, "recovery"),
        intensity: intensity, intensity_label: intensity_label(intensity),
        types: uniq(sessions.map(function (s) { return s.type; })),
        labels: uniq(sessions.map(function (s) { return s.name; })), flags: flags });
    });
    var hidden = [];
    if (data.can_scores === false) hidden.push("scores");
    if (data.can_metrics === false) hidden.push("metrics");
    return { days: days, sparky_error: get(data, "sparky_error"), hidden: hidden, timezone: tz.name };
  }

  function summarize(days) {
    var past = days.filter(function (d) { return !d.future; }), streak = 0, best = 0;
    var trained = past.filter(function (d) { return d.sessions_count; });
    past.forEach(function (d) { streak = d.sessions_count ? streak + 1 : 0; best = Math.max(best, streak); });
    var strains = past.filter(function (d) { return d.strain !== null; }).map(function (d) { return d.strain; });
    var byType = new Map();
    past.forEach(function (d) { d.sessions.forEach(function (s) { byType.set(s.type_name, (byType.get(s.type_name) || 0) + 1); }); });
    var byInt = {};
    PARAMS.INTENSITY_LEVELS.forEach(function (l) { byInt[l] = past.filter(function (d) { return d.intensity === l; }).length; });
    var kcal = past.filter(function (d) { return d.active_kcal !== null; }).map(function (d) { return d.active_kcal; });
    var types = {};
    Array.from(byType.entries()).sort(function (a, b) { return b[1] - a[1]; }).forEach(function (kv) { types[kv[0]] = kv[1]; });
    return { days: past.length, training_days: trained.length, rest_days: past.length - trained.length,
      sessions: U.pySum(past.map(function (d) { return d.sessions_count; })),
      total_duration_min: U.pySum(past.map(function (d) { return d.duration_min; })),
      total_active_kcal: kcal.length ? U.round(U.pySum(kcal)) : null,
      avg_active_kcal: kcal.length ? U.round(U.pySum(kcal) / kcal.length) : null,
      total_workout_kcal: U.round(U.pySum(past.map(function (d) { return d.workout_kcal || 0; }))),
      avg_strain: strains.length ? U.round(U.pySum(strains) / strains.length, 1) : null,
      longest_streak: best, by_type: types, by_intensity: byInt };
  }

  function calendar_view(res, start, end, label) {
    var days = res.days.map(function (d) {
      var row = {};
      CALENDAR_DAY_KEYS.forEach(function (k) { row[k] = d[k]; });
      row.sessions = d.sessions.map(function (s) {
        return { id: s.id, name: s.name, type: s.type, start: s.start, duration_min: s.duration_min,
          active_kcal: s.active_kcal, intensity: s.intensity, source: s.source };
      });
      return row;
    });
    return { month: label === undefined ? null : label, start: start, end: end, timezone: res.timezone, days: days,
      summary: summarize(res.days), sparky_error: res.sparky_error, hidden: res.hidden,
      intensity_levels: PARAMS.INTENSITY_LEVELS.map(function (k) { return { key: k, label: intensity_label(k) }; }),
      version: PARAMS.TRAINING_VERSION };
  }

  function day_view(res, d) {
    var x = res.days[0], daily = {};
    ["active_kcal", "basal_kcal", "total_kcal", "workout_kcal", "exercise_min", "steps", "strain", "muscle_load",
      "recovery", "recovery_next_day", "intensity", "intensity_label", "duration_min", "flags"].forEach(function (k) { daily[k] = x[k]; });
    return { date: d, weekday: x.weekday, timezone: res.timezone, sessions: x.sessions, daily: daily,
      sparky_error: res.sparky_error, hidden: res.hidden, version: PARAMS.TRAINING_VERSION };
  }

  // ------------------------------------------------------------------ muscle load (muscle_load.py, sport_muscles.py)
  var NOT_STRENGTH = set(["cardio", "stretching", "mobility"]);
  var SET_WARMUP = set(["warm-up", "warmup", "warm up", "热身"]);
  var CAT_STRIP = /[\s/+·・,，、.。:：\-_'"“”‘’()（）\[\]]/g;
  var PAREN_TAIL = /[（(][^（()）]*[)）]\s*$/;

  function norm(s) {
    s = String(s || "").normalize("NFKC").toLowerCase();
    s = s.split("（禁用）").join("").split("(禁用)").join("");
    return s.replace(CAT_STRIP, "");
  }
  function strip_paren(s) {
    s = String(s || "").split("（禁用）").join("").split("(禁用)").join("").trim();
    return s.replace(PAREN_TAIL, "").trim();
  }
  function Index(data) {
    this.rows = (data && data.rows) || {};
    this.by_name = (data && data.by_name) || {};
    this.by_app = (data && data.by_app) || {};
  }
  Index.prototype.slug_for = function (app_id) {
    if (app_id && has(this.by_app, app_id)) return this.by_app[app_id];
    for (var i = 1; i < arguments.length; i++) {
      var keys = [norm(arguments[i]), norm(strip_paren(arguments[i]))];
      for (var j = 0; j < keys.length; j++) if (keys[j] && has(this.by_name, keys[j])) return this.by_name[keys[j]];
    }
    return null;
  };

  function sm_normalize(t) {
    var key = String(t || "").trim().toLowerCase().split("-").join(" ");
    if (has(TABLES.SHORTCUT_TYPES, key)) return TABLES.SHORTCUT_TYPES[key];
    return key.split(/\s+/).filter(function (x) { return x; }).join("_") || "unknown";
  }
  function sm_resolve(t) {
    var key = sm_normalize(t);
    var row = FALLBACK_TYPES.has(key) ? null : (has(TABLES.SPORT_MUSCLES, key) ? TABLES.SPORT_MUSCLES[key] : null);
    var r = row || TABLES.FALLBACK, prim = r[0];
    return { type: key, primary: prim, secondary: r[1].filter(function (m) { return prim.indexOf(m) < 0; }),
      resistance: r[2], known: row !== null };
  }

  function weekly_range(m) { return has(PARAMS.WEEKLY_SETS_RANGE, m) ? PARAMS.WEEKLY_SETS_RANGE[m] : PARAMS.WEEKLY_SETS_RANGE_DEFAULT; }
  function status_for(m, sets) {
    if (sets <= 0) return "none";
    var r = weekly_range(m);
    return sets < r[0] ? "low" : sets > r[1] ? "high" : "ok";
  }
  function windows(today) {
    var mon = U.addDays(today, -U.weekday(today));
    return [["today", today, today], ["week", mon, today], ["last7", U.addDays(today, -6), today],
      ["prev_week", U.addDays(mon, -7), U.addDays(mon, -1)]];
  }
  function sport_name(t) {
    return L(has(TABLES.TYPE_NAMES, t) ? TABLES.TYPE_NAMES[t] : t, has(TABLES.TYPE_NAMES_EN, t) ? TABLES.TYPE_NAMES_EN[t] : t);
  }
  function r1(x, d) { return U.round(x + 1e-9, d === undefined ? 1 : d); }

  function intensity_level(w) {
    var minutes = w.min, level;
    if (nn(w.trimp) !== null) level = level_of(Number(w.trimp), PARAMS.TRIMP_THRESHOLDS);
    else if (w.avg_hr && w.hr_max) {
      level = level_of(avg_hr_trimp(Number(w.avg_hr), minutes, Number(w.hr_max)) * PARAMS.AVG_HR_TRIMP_FACTOR, PARAMS.TRIMP_THRESHOLDS);
    } else level = level_of(minutes, PARAMS.DURATION_THRESHOLDS[category(sm_normalize(w.type))]);
    return minutes < PARAMS.MIN_SESSION_MIN ? "light" : level;
  }

  /** -> {muscles: [[muscle, sets, primary], ...] (the Python dict's order), level, known, estimated}. */
  function sport_load(w, target) {
    var m = sm_resolve(w.type), loads = [];
    if (STRENGTH_LIKE.has(m.type)) {
      var prim = target && target[0].length ? target[0].slice() : m.primary.slice();
      var sec = target && target[0].length ? target[1].slice() : m.secondary.slice();
      var per = w.min / PARAMS.SET_CYCLE_MIN * PARAMS.STRENGTH_PRIMARIES_PER_SET / prim.length * PARAMS.STRENGTH_FALLBACK_RESISTANCE;
      prim.forEach(function (x) { loads.push([x, per, true]); });
      sec.forEach(function (x) { if (prim.indexOf(x) < 0) loads.push([x, per * PARAMS.SPORT_SECONDARY_FACTOR, false]); });
      return { muscles: dedupe(loads), level: "moderate", known: true, estimated: true };
    }
    var level = intensity_level(w);
    var base = w.min * PARAMS.SPORT_SETS_PER_MIN * m.resistance * PARAMS.INTENSITY_MULT[level];
    m.primary.forEach(function (x) { loads.push([x, base, true]); });
    m.secondary.forEach(function (x) { loads.push([x, base * PARAMS.SPORT_SECONDARY_FACTOR, false]); });
    return { muscles: dedupe(loads), level: level, known: m.known, estimated: !m.known };
  }
  function dedupe(loads) {   // a Python dict union: a later key keeps the first position, takes the later value
    var pos = new Map(), out = [];
    loads.forEach(function (l) { if (pos.has(l[0])) out[pos.get(l[0])] = l; else { pos.set(l[0], out.length); out.push(l); } });
    return out;
  }

  function aggregate_sports(sports, today, logged, targets) {
    var wins = windows(today), ranges = PARAMS.MUSCLE_RANGES, stats = {}, eq = {}, byM = {}, overall = {};
    Object.keys(TABLES.MUSCLE_ZH).forEach(function (m) {
      stats[m] = {}; eq[m] = {}; byM[m] = {};
      ranges.forEach(function (w) { stats[m][w] = 0.0; byM[m][w] = new Map(); });
      wins.forEach(function (w) { eq[m][w[0]] = 0.0; });
    });
    ranges.forEach(function (w) { overall[w] = new Map(); });
    sports.forEach(function (s) {
      if (s.date > today || (logged.has(s.date) && COUNTED_BY_SETS.has(sm_normalize(s.type)))) return;
      var key = sm_normalize(s.type);
      var target = targets && STRENGTH_LIKE.has(key) ? targets(s.date) : null;
      var load = sport_load(s, target);
      wins.forEach(function (win) {
        var wname = win[0];
        if (!(win[1] <= s.date && s.date <= win[2])) return;
        var show = ranges.indexOf(wname) >= 0;
        if (show) {
          var o = overall[wname].get(s.type);
          if (!o) { o = { type: s.type, name: sport_name(s.type), min: 0.0, mapped: true, estimated: false }; overall[wname].set(s.type, o); }
          o.min += s.min;
          o.estimated = o.estimated || load.estimated;
        }
        load.muscles.forEach(function (l) {
          var m = l[0], sets = l[1], primary = l[2];
          if (!has(eq, m)) return;
          eq[m][wname] += sets;
          if (show) {
            var factor = primary ? PARAMS.SPORT_PRIMARY_FACTOR : PARAMS.SPORT_SECONDARY_FACTOR;
            stats[m][wname] += s.min * factor;
            var it = byM[m][wname].get(s.type);
            if (!it) { it = { type: s.type, name: sport_name(s.type), min: 0.0, sets: 0.0, primary: primary }; byM[m][wname].set(s.type, it); }
            it.min += s.min;
            it.sets += sets;
          }
        });
      });
    });
    function done(items) {
      return Array.from(items.values()).sort(function (a, b) { return b.min - a.min; }).map(function (x) {
        var o = Object.assign({}, x, { min: U.round(x.min) });
        if (has(x, "sets")) o.sets = r1(x.sets);
        return o;
      });
    }
    var bm = {}, ov = {};
    Object.keys(byM).forEach(function (m) { bm[m] = {}; ranges.forEach(function (w) { bm[m][w] = done(byM[m][w]); }); });
    ranges.forEach(function (w) { ov[w] = done(overall[w]); });
    return { stats: stats, sets: eq, by_muscle: bm, overall: ov };
  }

  function muscles_of(index, ex) {
    var slug = ex.slug && has(index.rows, ex.slug) ? ex.slug : index.slug_for(get(ex, "exercise_id"), get(ex, "name"));
    var row = index.rows[slug || ""];
    if (row && ((row.primary_muscles || []).length || (row.secondary_muscles || []).length)) {
      var prim = (row.primary_muscles || []).filter(function (m) { return has(TABLES.MUSCLE_ZH, m); });
      var sec = (row.secondary_muscles || []).filter(function (m) { return has(TABLES.MUSCLE_ZH, m) && prim.indexOf(m) < 0; });
      return [prim, sec, slug];
    }
    var p = (ex.primary_muscles || []).map(function (m) { return String(m).toLowerCase(); })
      .filter(function (m) { return has(TABLES.MUSCLE_ZH, m); });
    return [p, [], slug];
  }
  function is_set(s) {
    if (SET_WARMUP.has(String(s.type || "").toLowerCase())) return false;
    return !!s.reps || !!s.duration_s;
  }

  function aggregate(workouts, index, today) {
    var win = windows(today), stats = {}, last = {}, unmatched = new Set(), logged = new Set();
    var since = U.addDays(today, -(PARAMS.MUSCLE_LOAD_BASELINE_DAYS - 1));
    Object.keys(TABLES.MUSCLE_ZH).forEach(function (m) {
      stats[m] = {};
      win.forEach(function (w) { stats[m][w[0]] = { sets: 0.0, volume_kg: 0.0 }; });
    });
    workouts.forEach(function (w) {
      var day = String(w.date).slice(0, 10);
      if (day > today) return;
      (w.exercises || []).forEach(function (ex) {
        if (NOT_STRENGTH.has(ex.category || "strength")) return;
        var sets = (ex.sets || []).filter(is_set);
        if (!sets.length) return;
        var mo = muscles_of(index, ex), prim = mo[0], sec = mo[1], slug = mo[2];
        if (slug && NOT_STRENGTH.has(index.rows[slug].category || "strength")) return;
        if (!prim.length && !sec.length) { unmatched.add(String(ex.name || ex.slug || "?")); return; }
        logged.add(day);
        var weighted = sets.filter(function (s) { return s.reps && s.weight_kg; });
        var kg = U.pySum(weighted.map(function (s) { return Number(s.reps) * Number(s.weight_kg); }));
        var top = null;
        weighted.forEach(function (s) {
          var t = [Number(s.weight_kg), Math.trunc(Number(s.reps))];
          if (top === null || U.cmp(t, top) > 0) top = t;
        });
        var row = index.rows[slug || ""] || {};
        var name = ex.name || row.name_zh || slug, name_en = row.name_en || name;
        [[prim, PARAMS.PRIMARY_FACTOR], [sec, PARAMS.SECONDARY_FACTOR]].forEach(function (pf) {
          pf[0].forEach(function (m) {
            win.forEach(function (w) {
              if (w[1] <= day && day <= w[2]) {
                stats[m][w[0]].sets += sets.length * pf[1];
                stats[m][w[0]].volume_kg += kg * pf[1];
              }
            });
            if (day >= since) {
              var cur = last[m];
              var item = { name: name, name_en: name_en, sets: sets.length, top: top ? fmtG(top[0]) + "×" + top[1] : null,
                primary: pf[1] === PARAMS.PRIMARY_FACTOR };
              if (!cur || day > cur.date) last[m] = { date: day, exercises: [item] };
              else if (day === cur.date && !cur.exercises.some(function (e) { return e.name === name; })) cur.exercises.push(item);
            }
          });
        });
      });
    });
    return { stats: stats, last: last, unmatched: Array.from(unmatched).sort(), logged_days: logged };
  }

  function targets_of(t) {
    if (!t) return null;
    return function (d) {
      var v = t[String(U.weekday(d) + 1)];
      return v ? [v[0].slice(), v[1].slice()] : null;
    };
  }

  /** muscle_load.compute_from -> [map, by_muscle]. */
  function compute_from(data, today) {
    var win = windows(today);
    var sessions = data.sessions || [], imported = data.imported || [];
    var index = data.index instanceof Index ? data.index : new Index(data.index);
    var agg = aggregate(sessions.concat(imported), index, today);
    var sp = aggregate_sports((data.sports || []).map(function (x) { return Object.assign({}, x, { date: String(x.date).slice(0, 10) }); }),
      today, agg.logged_days, targets_of(data.plan_targets));
    var muscles = Object.keys(TABLES.MUSCLE_ZH).map(function (key) {
      var st = agg.stats[key], lst = has(agg.last, key) ? agg.last[key] : null, rng = weekly_range(key), eqm = sp.sets[key];
      var total = function (w) { return st[w].sets + eqm[w]; };
      var week = total("week"), prev = total("prev_week"), ranges = {}, sportMin = {};
      PARAMS.MUSCLE_RANGES.forEach(function (w) {
        ranges[w] = { sets: r1(total(w)), logged_sets: r1(st[w].sets), sport_sets: r1(eqm[w]), volume_kg: U.round(st[w].volume_kg) };
        sportMin[w] = U.round(sp.stats[key][w]);
      });
      return { key: key, name: L(TABLES.MUSCLE_ZH[key], has(TABLES.MUSCLE_EN, key) ? TABLES.MUSCLE_EN[key] : key),
        group: get(TABLES.MUSCLE_GROUP, key),
        ranges: ranges,
        prev_week: { sets: r1(prev), logged_sets: r1(st.prev_week.sets), sport_sets: r1(eqm.prev_week),
          volume_kg: U.round(st.prev_week.volume_kg) },
        delta_sets: r1(week - prev), target: { low: rng[0], high: rng[1] },
        status: { week: status_for(key, week), last7: status_for(key, total("last7")) },
        trained_today: total("today") > 0, sport_min: sportMin,
        last: lst && { date: lst.date, exercises: lst.exercises.map(function (e) {
          var o = {};
          Object.keys(e).forEach(function (k) { if (e[k] !== null && e[k] !== undefined) o[k] = e[k]; });
          return o;
        }).slice(0, 8) } };
    });
    var sex = data.sex === "male" || data.sex === "female" ? data.sex : null;
    return [{ version: PARAMS.MUSCLE_LOAD_VERSION, today: today, week_start: win[1][1], sex: sex,
      source: { sparky: get(data, "sparky_state"), imported_days: imported.length }, unmatched: agg.unmatched,
      sports: sp.overall, muscles: muscles }, sp.by_muscle];
  }

  return { PARAMS: PARAMS, TABLES: TABLES, type_name: type_name, hk_workout_type: hk_workout_type, parse_app: parse_app,
    apply_override: apply_override, duplicates: duplicates, session_trimp: session_trimp, avg_hr_trimp: avg_hr_trimp,
    session_hr_recovery: session_hr_recovery, hr_enrich: hr_enrich, hrr60: hrr60, heart_rate: heart_rate,
    workout_days: workout_days, workout_intensity: workout_intensity, classify: classify, merge_day: merge_day,
    compute: compute, calendar_view: calendar_view, day_view: day_view, summarize: summarize, norm: norm,
    strip_paren: strip_paren, Index: Index, sport_normalize: sm_normalize, sport_resolve: sm_resolve,
    sport_load: sport_load, aggregate_sports: aggregate_sports, aggregate: aggregate, compute_from: compute_from };
})(OBS.util, OBS.strain);

(function (W) {
  "use strict";
  OBS.ALGO_VERSIONS.workouts = W.PARAMS.ALGO_VERSION;
  function stamp(x) { x.algo_version = W.PARAMS.ALGO_VERSION; x.bundle_version = OBS.VERSION; return x; }

  /** The phone's workouts of some days (training/importer.workout_days). */
  OBS.workoutDays = function (src) { return stamp(W.workout_days(src)); };
  /** The training calendar of [start, end] (history.calendar_view(history.compute(...))). */
  OBS.trainingCalendar = function (data, opts) {
    return stamp(W.calendar_view(W.compute(data, opts.start, opts.end, opts.today), opts.start, opts.end, opts.label));
  };
  /** One day's detail (history.day_view(history.compute(...))). */
  OBS.trainingDay = function (data, opts) {
    return stamp(W.day_view(W.compute(data, opts.date, opts.date, opts.today), opts.date));
  };
  /** The muscle-load map {map, by_muscle} (muscle_load.compute_from). */
  OBS.muscleLoad = function (data, today) {
    var r = W.compute_from(data, today);
    return stamp({ map: r[0], by_muscle: r[1] });
  };
})(OBS.workouts);
