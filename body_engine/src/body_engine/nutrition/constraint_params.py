"""Per-user diet constraints (issue #76): the vocabulary, value ranges and the generic, literature-backed reference values.

Nothing here applies to anybody by itself. A constraint only acts for a user who has it (table diet_constraints, set by the
user's AI or carried over from the user's own profile card, nutrition/constraints.py). The reference values below are
what the AI may suggest, with their sources; the user decides.
"""

# kind -> label (zh, en), unit (zh, en), value range (None = on/off), whether a target (a food) is needed, reference
KINDS: dict[str, dict] = {
    "red_meat_g_per_week": {
        "short": ("红肉每周 ≤ {v} g", "Red meat ≤ {v} g a week"),
        "label": ("红肉每周上限", "Red meat per week"), "unit": ("g / 周", "g / week"), "range": (0, 3000),
        # WCRF / AICR 2018 Third Expert Report, recommendation "limit red and processed meat": no more than about three
        # portions a week, about 350–500 g cooked weight (~525–750 g raw), little if any processed meat.
        "reference": "WCRF/AICR 2018：每周不超过约 350–500 g（熟重）",
    },
    "red_meat_meals_per_week": {
        "short": ("红肉每周 ≤ {v} 顿", "Red meat ≤ {v} meals a week"),
        "label": ("红肉每周顿数上限", "Red meat meals per week"), "unit": ("顿 / 周", "meals / week"), "range": (0, 21),
        "reference": "WCRF/AICR 2018：每周约 3 份以内",
    },
    "food_times_per_week": {
        "short": ("{t}每周 ≤ {v} 次", "{t} ≤ {v} times a week"),
        "label": ("某种食物每周上限", "A food per week"), "unit": ("次 / 周", "times / week"), "range": (0, 21),
        "target": True, "reference": "按用户自己的情况（医生 / 营养师建议）",
    },
    "low_purine": {
        # ACR 2020 gout guideline (FitzGerald JD et al., Arthritis Care Res 2020;72:744-60): limit purine-rich foods,
        # alcohol and high-fructose corn syrup for people with gout / hyperuricaemia. Per-serving purine bands:
        # assistant/recipe_params.py (food purine tables).
        "label": ("低嘌呤", "Low purine"), "unit": None, "range": None,
        "reference": "ACR 2020 痛风指南：高尿酸 / 痛风时限制高嘌呤食物",
    },
    "low_saturated_fat": {
        # WHO 2023 guideline on saturated fatty acid intake: < 10 % of energy; AHA 2021 dietary guidance: lower for
        # people with raised LDL / triglycerides. Per-serving bands: assistant/recipe_params.py.
        "label": ("低饱和脂肪", "Low saturated fat"), "unit": None, "range": None,
        "reference": "WHO 2023：饱和脂肪 < 总能量 10%",
    },
    "avoid_alcohol": {
        # EASL-EASD-EASO 2016 NAFLD guideline and ACR 2020 gout guideline: avoid / limit alcohol.
        "label": ("不喝酒", "No alcohol"), "unit": None, "range": None,
        "reference": "EASL 2016 脂肪肝指南、ACR 2020 痛风指南：避免 / 限制酒精",
    },
}

# One food entry with at least this much saturated fat gets a warning when logged, for a user with low_saturated_fat:
# WHO 2023 < 10 % of energy ≈ 22 g a day at 2000 kcal, so 10 g in one item is about half the day's room.
SAT_FAT_ENTRY_WARN_G = 10

# At this share of a weekly cap the reminder says "almost there" (info) before the warning above the cap.
WARN_FRACTION = 0.8

# Generic names that count as the same food (food knowledge, not anybody's limit): a food_times_per_week target matches
# these too, besides its own name and the row's `match` list.
FOOD_ALIASES: dict[str, tuple[str, ...]] = {
    "牛肋条": ("short rib", "beef rib", "牛小排"),
    "培根": ("bacon",),
    "火腿": ("ham",),
    "香肠": ("sausage",),
}

# Carrying the existing data over (db.DATA_STEPS, once): constraints written in a user's own profile card item
# health_constraints (the AI's profile card) are turned into rows. Generic text patterns; values come from the text.
CARRY_OVER_ITEM = "health_constraints"
CARRY_OVER_BASIS = ("从档案卡「健康约束」迁移", "Carried over from the profile card (health constraints)")
