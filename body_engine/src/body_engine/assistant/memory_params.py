"""Parameters of the long-term memory (assistant/memory.py, ai/prompt.py), issue #112. Configurable; nothing here is one person's data."""

# A recent note ("this week I'm travelling", "day 3 of a cold") given without an end date expires after this many days.
# Two weeks covers a typical cold, a trip or an exam period; a judgement call, not from a study.
RECENT_DEFAULT_DAYS = 14
# Upper bound for an explicit end date: anything that lasts longer is a lasting fact or a profile card item.
RECENT_MAX_DAYS = 180

# Profile card items the in-page and fallback AI always get, in this order, however long the memory is
# (ai/prompt.py _memory_block): how to talk to the user, then the two items that keep advice safe.
PRIORITY_KEYS = ("communication", "health_constraints", "injuries")
# Observation notes (lasting facts) put into the per-conversation memory, newest first.
PROMPT_FACTS_MAX = 60

# ---------------------------------------------------------------- memory hygiene, phase 1 (issue #113)
# Topics are the profile card's standard keys (memory.STANDARD_KEYS, incl. communication). Profile items with another key
# take the topic of their key prefix (the diet profile's diet_* sections), else one inferred from label + value.
KEY_PREFIX_TOPIC = {"diet_": "diet_preferences"}
# Other keys code writes on its own (training/goals.py physique_goal, ai/params.py EQUIPMENT_CARD_KEY).
KEY_TOPIC = {"physique_goal": "goals", "equipment": "training_preferences"}

# Topic inference for notes the AI gave no topic, and for the backfill of existing entries (data step 665): the first topic
# (in this order) one of whose words is in the text, lower-cased. No match = NULL (phase 2 lets the AI fill it in).
# Specific topics come before the broad ones ("膝盖疼，不做深蹲" is an injury, not a training preference). Plain word lists, a
# judgement call, not from a study; the order and words are what to tune.
TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("communication", ("回答", "回复", "说话", "语气", "表情", "emoji", "啰嗦", "简短", "详细解释", "提醒频率", "别提", "不要提", "称呼",
                       "reply", "replies", "tone", "verbose", "concise")),
    ("injuries", ("伤", "疼", "痛", "扭", "拉伤", "劳损", "手术", "康复", "禁用", "不能做", "injur", "pain", "sprain", "surgery", "rehab")),
    ("health_constraints", ("过敏", "不耐", "痛风", "尿酸", "血压", "血糖", "血脂", "胆固醇", "糖尿病", "哮喘", "医生", "药", "化验", "怀孕",
                            "allerg", "intoleran", "gout", "uric", "blood pressure", "cholesterol", "diabet", "medication", "doctor")),
    ("supplements", ("补剂", "蛋白粉", "肌酸", "鱼油", "维生素", "镁", "褪黑素", "supplement", "creatine", "fish oil", "vitamin", "magnesium")),
    ("follow_ups", ("复查", "复诊", "体检", "待办", "预约", "follow-up", "follow up", "appointment", "check-up", "checkup")),
    ("goals", ("目标", "减脂", "增肌", "减重", "体脂", "备赛", "goal", "lose weight", "fat loss", "gain muscle", "target")),
    ("current_plan", ("计划", "这周练", "本周", "分化", "周期", "plan", "this week", "program", "split")),
    ("schedule", ("起床", "睡觉", "入睡", "作息", "上班", "下班", "通勤", "夜班", "午睡", "每周", "周一", "周二", "周三", "周四", "周五",
                  "周六", "周日", "周末", "早上", "晚上", "wake", "bedtime", "sleep at", "commute", "shift", "weekday", "weekend",
                  "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")),
    ("diet_preferences", ("吃", "喝", "饮食", "早餐", "午餐", "晚餐", "零食", "咖啡", "茶", "酒", "牛奶", "素食", "口味", "辣", "甜",
                          "eat", "drink", "diet", "breakfast", "lunch", "dinner", "snack", "coffee", "tea", "alcohol", "milk", "vegan",
                          "vegetarian")),
    ("training_preferences", ("训练", "健身", "跑步", "篮球", "足球", "游泳", "骑车", "瑜伽", "器械", "哑铃", "杠铃", "健身房", "动作", "力量",
                              "有氧", "train", "workout", "gym", "run", "basketball", "football", "soccer", "swim", "cycl", "yoga",
                              "dumbbell", "barbell", "cardio", "strength")),
)

# add_observation looks for similar notes before writing a new one (same kind, same topic or no topic yet). Similarity is
# difflib's ratio on the text lower-cased without spaces and punctuation: 「周三打篮球」 vs 「每周三晚上篮球」 = 0.67. 0.6 catches
# rewordings of one fact and lets the AI look at near-contradictions (「喝牛奶胀气」 / 「喝牛奶没事」); a judgement call.
SIMILAR_MIN_RATIO = 0.6
SIMILAR_MAX = 3     # at most this many similar notes come back, most similar first

# Days without a confirmation after which an entry counts as stale (amber on the Today page; phase 2 offers them for
# review), by topic. Plans and injuries change within weeks, preferences and basics in months; a judgement call.
STALE_DAYS = {
    "current_plan": 60, "injuries": 60, "follow_ups": 60, "goals": 90, "supplements": 90, "schedule": 120,
    "health_constraints": 180, "diet_preferences": 180, "training_preferences": 180, "what_works": 180,
    "what_doesnt": 180, "communication": 180, "basics": 365,
}
STALE_DEFAULT_DAYS = 120   # entries without a topic

# ---------------------------------------------------------------- memory hygiene, phase 2: periodic review (issue #113)
# The briefing reminds the user's AI to review the memory when the last review is older than this AND at least
# REVIEW_MIN_CANDIDATES entries are waiting (or the memory is nearly full). A judgement call, set by the issue.
REVIEW_INTERVAL_DAYS = 30
REVIEW_MIN_CANDIDATES = 3
# The memory counts as nearly full at this share of PROMPT_FACTS_MAX lasting notes (beyond it the oldest fall out of the prompt).
NEAR_FULL_RATIO = 0.8
# Archived entries are cleaned up (really deleted) this many days after archiving; until then the user can restore them.
ARCHIVE_PURGE_DAYS = 90
# An entry unconfirmed for this many times its STALE_DAYS is offered for archiving ("long_stale"), not just for a look.
LONG_STALE_FACTOR = 2
# Candidates handed to the AI per group (the rest wait for the next review) and notes compared for duplicates (newest first).
REVIEW_MAX_PER_GROUP = 12
REVIEW_COMPARE_MAX = 300
# Contradictions can only be judged by the AI: the server puts entries of the same topic side by side (topics with the most
# entries first), cut to this many topics / entries per topic / characters per entry.
REVIEW_TOPICS_MAX = 4
REVIEW_PER_TOPIC_MAX = 6
REVIEW_TEXT_CHARS = 80
# Items per apply_memory_changes call.
APPLY_MAX_ITEMS = 30
# Topics whose entries the AI changes only on the user's explicit say-so, whatever their origin.
PROTECTED_TOPICS = ("health_constraints", "injuries")
# Origins that count as the user's own words: NULL (unknown) is treated the same, as phase 1 decided.
USER_ORIGINS = ("user_said", None)

# ---------------------------------------------------------------- memory hygiene, phase 3: checking against the data, promotion (issue #113)
# A confirmation counts at most once per this many days (the same fact stated three times in one chat is one confirmation).
COUNT_MIN_GAP_DAYS = 1
# Nightly, a lasting note the data backs is confirmed (confirmed_at, confirm_count) when its last confirmation is at least this old.
DATA_CONFIRM_GAP_DAYS = 30
# A mismatch the user already looked at (entry confirmed since it was written, within this many days) is not offered again.
DATA_SNOOZE_DAYS = 30
DATA_MAX = 6           # mismatches handed to the AI per review (the rest wait for the next one)
DATA_CLAIM_CHARS = 40  # characters of the claim / of the data shown per mismatch

# Promotion: a lasting note is offered for merging into its profile card item when it was confirmed this many times, or when it
# is at least PROMOTE_MIN_AGE_DAYS old and still not stale. Set by the issue (confirmed >= 2 times, or still valid after 60 days).
PROMOTE_MIN_CONFIRMS = 2
PROMOTE_MIN_AGE_DAYS = 60
PROMOTE_MAX = 5            # candidates per review
PROMOTE_CARD_CHARS = 300   # characters of the target card item shown (the AI reads the full text with get_memory when it is cut)

# Training habits against the workout records (watch workouts, own logged strength sessions).
TRAIN_WINDOW_DAYS = 56        # 8 weeks, the window the issue names
TRAIN_MIN_DAYS_EVER = 4       # fewer training days than this in the last TRAIN_LOOKBACK_DAYS = no training data: no verdict
TRAIN_LOOKBACK_DAYS = 120
TRAIN_MIN_MINUTES = 20        # a workout shorter than this (a stroll the watch logged) is not a training day
TRAIN_FREQ_TOLERANCE = 1.0    # days per week the real average may differ from the claimed range before it is a mismatch
# "every Wednesday basketball": no such workout on that weekday in the window = mismatch; at least WEEKDAY_VERIFY_HITS = verified.
WEEKDAY_MISMATCH_MAX_HITS = 0
WEEKDAY_VERIFY_HITS = 3
WEEKDAY_MIN_OCCURRENCES = 6   # the weekdays named must occur at least this often in the window to judge
# Sport id -> (words in the text, workout types it is recorded as; training/params.py TYPE_NAMES). "strength" also counts the
# logged strength sessions. Plain word lists, a judgement call.
SPORT_TYPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "basketball": (("篮球", "basketball"), ("basketball",)),
    "swimming": (("游泳", "swim"), ("swimming", "pool_swimming", "open_water_swimming")),
    "running": (("跑步", "长跑", "慢跑", "running", "jogging"), ("running", "treadmill")),
    "soccer": (("足球", "soccer"), ("soccer", "australian_football")),
    "tennis": (("网球", "tennis"), ("tennis",)),
    "badminton": (("羽毛球", "badminton"), ("badminton",)),
    "cycling": (("骑车", "骑行", "单车", "cycling"), ("cycling", "indoor_cycling")),
    "yoga": (("瑜伽", "yoga"), ("yoga", "pilates")),
    "hiking": (("爬坡", "徒步", "爬山", "hiking"), ("hiking", "walking", "stair_climbing")),
    "strength": (("力量训练", "撸铁", "举铁", "力量", "strength", "lifting"), ("strength_training", "fitness_equipment")),
}
TRAIN_WORDS = ("练", "训练", "健身", "运动", "锻炼", "力量", "gym", "train", "workout", "exercise", "lift")
HABIT_WORDS = ("每", "一般", "通常", "经常", "习惯", "固定", "总是", "常常", "every", "usually", "regularly", "weekly", "routinely")

# Body weight against the weigh-ins (daily_metrics body_mass, body_measurements) and the goal weight (status card goal_weight).
WEIGHT_RECENT_DAYS = 14       # no weigh-in this recent = no verdict on "my weight is ..."
WEIGHT_AVG_DAYS = 7           # readings of the last week are averaged
WEIGHT_TOLERANCE_KG = 1.5     # day-to-day swings of a kilo or two are normal
GOAL_TOLERANCE_KG = 0.5

# Supplements against the supplements table. Name in the text -> words that find it in the table (name / ingredients).
SUPPLEMENT_WORDS: dict[str, tuple[str, ...]] = {
    "肌酸": ("肌酸", "creatine"), "鱼油": ("鱼油", "fish oil", "omega", "epa", "dha"),
    "蛋白粉": ("蛋白粉", "protein powder", "whey"), "维生素D": ("维生素d", "维d", "vitamin d"),
    "维生素C": ("维生素c", "vitamin c"), "维生素B": ("维生素b", "vitamin b"), "镁": ("镁", "magnesium"),
    "锌": ("锌", "zinc"), "褪黑素": ("褪黑素", "melatonin"), "益生菌": ("益生菌", "probiotic"),
    "钙": ("钙", "calcium"), "辅酶Q10": ("辅酶q10", "coq10"), "叶酸": ("叶酸", "folate", "folic"),
}
SUPPLEMENT_CONTEXT = ("补剂", "每天吃", "每日", "随餐", "服用", "在吃", "吃了", "supplement", "taking", "take ")
SUPPLEMENT_ACTIVE = ("active", "as_needed")   # statuses of the supplements table that count as "taking it"

# Injuries against the body events. Body part in the text -> words that find it in an event's body_part / title.
BODY_PARTS: dict[str, tuple[str, ...]] = {
    "肩": ("肩", "shoulder"), "膝": ("膝", "knee"), "腰": ("腰", "lower back", "lumbar"), "背": ("背", "back"),
    "颈": ("颈", "neck"), "肘": ("肘", "elbow"), "腕": ("腕", "wrist"), "踝": ("踝", "ankle"), "髋": ("髋", "hip"),
    "大腿": ("大腿", "thigh", "hamstring", "quad"), "小腿": ("小腿", "calf", "shin"), "脚": ("脚", "foot"),
}
INJURY_WORDS = ("伤", "疼", "痛", "受限", "不适", "弹响", "劳损", "拉伤", "扭", "发炎", "injur", "pain", "sore", "strain", "sprain")
RECOVERED_WORDS = ("好了", "痊愈", "康复了", "恢复了", "已愈", "不痛了", "不疼了", "healed", "recovered", "pain-free")
RULE_WORDS = ("禁用", "不能做", "不要做", "避免", "别做", "禁止", "不做", "avoid", "never do")   # exercise rules, not an injury status
EVENT_CATEGORIES = ("injury", "pain", "symptom", "posture", "other")   # (and events without a category)
INJURY_LOOKBACK_DAYS = 365
INJURY_RESOLVED_GRACE_DAYS = 7   # an event marked better less than this long ago is not yet "the note is out of date"

# Words that make a sentence no claim about the present: negation, wishes, history. Looked for next to the matched word.
NEGATION_BEFORE = ("不", "没", "未", "别", "停", "戒", "无", "no ", "not ", "stop", "quit", "without")
NEGATION_AFTER = ("停", "不吃", "不再", "已停", "没吃", "stopped", "quit")
WISH_WORDS = ("想", "打算", "希望", "考虑", "争取", "准备", "建议", "可以", "want", "plan to", "planning", "hope", "should", "consider")
PAST_WORDS = ("曾", "以前", "之前", "过去", "原来", "once", "used to", "previously", "formerly")
