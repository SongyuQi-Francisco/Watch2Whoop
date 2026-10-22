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
