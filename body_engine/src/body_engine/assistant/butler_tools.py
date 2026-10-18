"""Tools added by the body butler (P1): write SparkyFitness (food, workouts, weight, undo), screenshot metrics, long-term memory.
Only specs (dicts) are given here; assistant/tools.py wraps them into registered Tools. Format follows the Claude API strict tools:
every property is in `required`, optional ones use null. Write tools (writes=True) have no user parameter and can only write for the caller.
Lab result writing (add_lab_results) lives in records/tools.py."""
from datetime import date

from ..toolspec import DETAIL, END, START, USER, nullable, schema
from . import meals, memory, recipes, snapshots, sparky_write, status


_schema = schema
_n = nullable

DATE = {"type": "string", "description": "行为发生那天 YYYY-MM-DD（凌晨睡前的事算前一天）"}
OPT_LOG_DATE = _n("string", "YYYY-MM-DD，null = 今天（凌晨睡前吃的要明确给前一天）")
HHMM = _n("string", "HH:MM，null = 现在")
G = {"type": "number"}  # grams / kcal fields: the property name says the unit

FOOD_ITEM = _schema({
    "date": DATE,
    "meal": {"type": "string", "enum": list(sparky_write.MEALS)},
    "time": _n("string", "HH:MM"),
    "food": {"type": "string", "description": "具体到能估营养，例：盐葱酱牛肋条（牛肋条 150g）"},
    "amount": {"type": "string", "description": "份量，例 200 g、1 碗（约 250g 熟饭）"},
    "calories": {"type": "number", "description": "这一份的总 kcal"},
    "protein_g": G, "carbs_g": G, "fat_g": G,
    "saturated_fat_g": _n("number"),
    "alcohol_g": _n("number", "酒类填"),
    "notes": _n("string", "估算依据、拿不准的地方"),
})

SET = _schema({
    "reps": _n("integer"),
    "weight_kg": _n("number", "哑铃单只、绳索每侧、杠铃总重"),
    "rir": _n("number", "离力竭还剩几次"),
    "duration_s": _n("integer", "平板 / 有氧"),
    "set_type": _n("string", "Working Set（默认）/ Warm-up"),
    "notes": _n("string"),
})

EXERCISE = _schema({
    "exercise": {"type": "string", "description": "动作名，会对上动作库"},
    "exercise_id": _n("string", "search_exercises 查到的 ID，比名字准"),
    "sets": {"type": "array", "items": SET},
    "notes": _n("string"),
})


STATUS_ITEM = _schema({
    "key": {"type": "string", "description": "小写英文下划线，例 phase、goal_weight"},
    "label": {"type": "string", "description": "≤ 6 字"},
    "value": {"type": "string", "description": "≤ 12 字，不含单位"},
    "unit": _n("string"),
    "tone": {"type": "string", "enum": list(status.TONES)},
    "trend": {"type": ["string", "null"], "enum": ["up", "down", "flat", None]},
    "good_direction": {"type": ["string", "null"], "enum": ["up", "down", None]},
    "group": {"type": "string", "enum": status.GROUPS},
    "spark": {"type": ["array", "null"], "items": {"type": ["number", "null"]}, "description": "最近数值 旧→新 ≤ 30 个"},
    "note": _n("string", "≤ 20 字"),
})

MEAL_ITEM = _schema({
    "food": {"type": "string"},
    "grams": {"type": "number", "description": "饮料 ml≈g"},
    "kcal": G, "protein_g": G, "carbs_g": G, "fat_g": G, "sat_fat_g": G,
    "purine_level": {"type": ["string", "null"], "enum": ["low", "medium", "high", None]},
    "red_meat_g": {"type": "number", "description": "红肉 / 加工肉生重，没有 0"},
    "note": _n("string"),
})


INGREDIENT = _schema({
    "food": {"type": "string"},
    "grams": {"type": "number", "description": "整锅用量"},
    "kcal": G, "protein_g": G, "carbs_g": G, "fat_g": G, "sat_fat_g": G,
    "purine_mg": {"type": "number", "description": "估"},
    "red_meat_g": {"type": "number", "description": "红肉 / 加工肉生重，没有 0"},
    "purchase_unit": _n("string", "例 整盒（约 1kg）"),
    "store": {"type": ["string", "null"], "enum": [*recipes.STORES, None]},
    "note": _n("string"),
})

STATUS_BRIEF = ("key", "label", "value", "unit", "tone", "note", "source")


def _recipe_list(conn, uid, a):
    """One flat row per recipe (constraint levels instead of the nested flags; no image). Full recipe: get_recipe."""
    out = []
    for r in recipes.list_recipes(conn, uid, a["slot"], a["tag"], a["q"]):
        f = r.get("constraint_flags") or {}
        out.append({k: r.get(k) for k in ("id", "name", "slots", "tags", "kcal", "protein_g", "carbs_g", "fat_g",
                                          "per_serving", "favorite", "can_make", "last_cooked_on")}
                   | {"min": (r.get("prep_min") or 0) + (r.get("cook_min") or 0) or None,
                      "purine": f.get("purine_level"), "sat_fat": f.get("sat_fat_level"),
                      "red_meat_g": f.get("red_meat_g") or None, "short_rib": f.get("short_rib") or None})
    return out


def _memory_with_status(conn, uid, a=None):
    st, m = status.list_status(conn, uid), memory.get_memory(conn, uid)
    if (a or {}).get("detail"):
        return {"status": st, **m}
    # Default: what the AI needs to read and maintain the memory, without sparklines / timestamps / evidence
    return {"status": [{k: s.get(k) for k in STATUS_BRIEF} for s in st],
            "profile_card": [{"key": c["key"], "label": c["label"], "value": c["value"],
                              "updated": f"{c['updated_at']:%Y-%m-%d}"} for c in m["profile_card"]],
            "observations": [{"id": o["id"], "date": f"{o['created_at']:%Y-%m-%d}", "text": o["text"]}
                             for o in m["observations"]]}


TOOL_SPECS: list[dict] = [
    # ---------- SparkyFitness writes ----------
    dict(
        name="search_exercises",
        description="在动作库（+ SparkyFitness）按名称/别名/肌群/器械/模式搜动作：返回 slug、exercise_id、肌群、器械、状态（active/caution/banned）与要点。",
        input_schema=_schema({
            "user": USER,
            "query": _n("string"),
            "muscle": _n("string", "chest/back/shoulders/legs/arms/core 或中文"),
            "equipment": _n("string", "dumbbell/barbell/cable/machine/body only 或中文"),
            "pattern": _n("string"),
        }),
        handler=lambda conn, uid, a: sparky_write.search_exercises(
            conn, uid, a.get("query") or "", muscle=a.get("muscle"),
            equipment=a.get("equipment"), pattern=a.get("pattern")),
        scope="training",
    ),
    dict(
        name="log_food",
        description="饮食记进 SparkyFitness（每条一种食物 / 一道菜）。热量和三大营养素要自洽（4/4/9，酒精 7），对不上会被拒；"
                    "同天同餐同名当重复跳过（allow_duplicates=true 才写）。返回条目 id（撤销用 undo_sparky_entry）。",
        input_schema=_schema({
            "items": {"type": "array", "items": FOOD_ITEM},
            "allow_duplicates": {"type": "boolean", "description": "用户确认是另一份才 true"},
        }),
        handler=lambda conn, uid, a: sparky_write.log_food(conn, uid, a["items"], a["allow_duplicates"]),
        scope="nutrition", writes=True,
    ),
    dict(
        name="log_workout",
        description="一次训练记进 SparkyFitness（每个动作每组次数、重量、RIR）。有 banned 动作整次拒绝（告诉用户原因和替代）；"
                    "对不上的动作报错并给候选；同天同动作当重复跳过（allow_duplicate=true 才写）。",
        input_schema=_schema({
            "date": DATE,
            "name": {"type": "string", "description": "例 上肢 A"},
            "notes": _n("string", "感受、低血糖、心率等"),
            "exercises": {"type": "array", "items": EXERCISE, "description": "按做的顺序"},
            "allow_duplicate": {"type": "boolean"},
            "create_missing_exercises": {"type": "boolean", "description": "用户确认是新动作才 true"},
        }),
        handler=lambda conn, uid, a: sparky_write.log_workout(conn, uid, a),
        scope="training", writes=True,
    ),
    dict(
        name="log_weight",
        description="称重 / 体成分记进 SparkyFitness check-in（每天一条）。同天已有不同数值不覆盖（overwrite=true 才改）。"
                    "InBody 的其他项（内脏脂肪、骨骼肌…）用 log_metric_snapshot。",
        input_schema=_schema({
            "date": DATE,
            "weight_kg": _n("number", "早上空腹"),
            "body_fat_pct": _n("number"),
            "waist_cm": _n("number", "肚脐一圈"),
            "muscle_mass_kg": _n("number"),
            "body_water_pct": _n("number"),
            "bone_mass_kg": _n("number"),
            "bmr_kcal": _n("number"),
            "overwrite": {"type": "boolean"},
        }),
        handler=lambda conn, uid, a: sparky_write.log_weight(conn, uid, a),
        scope="weight", writes=True,
    ),
    dict(
        name="undo_sparky_entry",
        description="撤销管家写进 SparkyFitness 的一条饮食或训练（log_food / log_workout 返回的 id）。用户在 app 里记的不删。",
        input_schema=_schema({
            "kind": {"type": "string", "enum": ["food", "workout"]},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "id": {"type": "string"},
        }),
        handler=lambda conn, uid, a: sparky_write.undo_entry(conn, uid, a["kind"], a["date"], a["id"]),
        scope="training", writes=True,
    ),
    # ---------- screenshot metrics ----------
    dict(
        name="log_metric_snapshot",
        description="记截图里读出的、第三方读不到的指标（apple_readiness、apple_sleep_score、InBody skeletal_muscle_kg、"
                    "visceral_fat_level…）。单独存，不覆盖传感器数据；同天同指标再记会更新。",
        input_schema=_schema({
            "date": {"type": "string", "description": "YYYY-MM-DD（睡眠分 = 醒来那天）"},
            "source_app": {"type": "string", "description": "apple_health、apple_watch、inbody…"},
            "metrics": {"type": "array", "items": _schema({
                "metric": {"type": "string", "description": "小写英文下划线"},
                "value": {"type": "number"},
                "unit": _n("string"),
            })},
            "note": _n("string"),
        }),
        handler=lambda conn, uid, a: snapshots.log_snapshot(conn, uid, date.fromisoformat(a["date"]), a["source_app"],
                                                            a["metrics"], a["note"]),
        scope="metrics", writes=True,
    ),
    dict(
        name="get_metric_snapshots",
        description="截图记下的指标（Apple Readiness / 睡眠分、InBody…），和传感器数据对照用。",
        input_schema=_schema({
            "user": USER, "start": START, "end": END,
            "metrics": {"type": "array", "items": {"type": "string"}, "description": "空 = 全部"},
        }),
        handler=lambda conn, uid, a: snapshots.list_snapshots(conn, uid, date.fromisoformat(a["start"]),
                                                              date.fromisoformat(a["end"]), a["metrics"] or None),
        scope="metrics",
    ),
    # ---------- long-term memory ----------
    dict(
        name="get_memory",
        description="完整长期记忆：状态卡 status（source=auto 的后端算、不能改）、身体档案卡（目标、健康约束、伤病、作息、偏好、"
                    "有效 / 无效做法、计划、补剂、复查待办）、观察笔记。get_briefing 里已有要点，要全文时再调。",
        input_schema=_schema({"user": USER, "detail": {**DETAIL, "description": "true=带趋势数组、时间戳、来源和依据"}}),
        handler=lambda conn, uid, a: _memory_with_status(conn, uid, a),
        scope="memory",
    ),
    dict(
        name="update_profile_card",
        description="新建或整段替换档案卡的一项（value 是完整新内容，不是追加）。标准键：" +
                    "、".join(memory.STANDARD_KEYS) + "；别的键要给 label。写简洁的事实，带日期和依据。",
        input_schema=_schema({
            "key": {"type": "string"},
            "value": {"type": "string", "description": "≤ 4000 字"},
            "label": _n("string", "中文名；标准键 null"),
        }),
        handler=lambda conn, uid, a: memory.update_profile(conn, uid, a["key"], a["value"], a["label"]),
        scope="memory", writes=True,
    ),
    dict(
        name="add_observation",
        description="记一条观察笔记：对话里学到的、以后有用的新事实（偏好、习惯、身体反应）。一句话一个事实，不重复记。",
        input_schema=_schema({
            "text": {"type": "string", "description": "≤ 1000 字"},
            "evidence": _n("string", "用户原话 / 哪些数据"),
        }),
        handler=lambda conn, uid, a: memory.add_observation(conn, uid, a["text"], a["evidence"]),
        scope="memory", writes=True,
    ),
    dict(
        name="set_status_items",
        description="写状态卡（今天页「我」页的短卡片）：当前阶段、伤病状态、目标体重 goal_weight、计划执行等需要判断的项。"
                    "整项替换，remove_keys 删过时的。细节写档案卡。后端自动算的项（" + "、".join(status.AUTO_KEYS) + "）不能写。",
        input_schema=_schema({
            "items": {"type": "array", "items": STATUS_ITEM},
            "remove_keys": {"type": "array", "items": {"type": "string"}},
        }),
        handler=lambda conn, uid, a: status.set_items(conn, uid, a["items"], a["remove_keys"]),
        scope="memory", writes=True,
    ),
    # ---------- quick meal logging (meal templates) ----------
    dict(
        name="get_meal_templates",
        description="快捷记餐模板（例：固定早餐）：每样食物克数和营养、合计、克数是否待确认、今天记过没有。",
        input_schema=_schema({"user": USER}),
        handler=lambda conn, uid, a: meals.list_templates(conn, uid),
        scope="nutrition",
    ),
    dict(
        name="create_meal_template",
        description="新建快捷记餐模板（「我的固定早餐是…」）。克数不确定先问，实在没有按常见份量估并 needs_confirmation=true；"
                    "培根、火腿等加工肉填 red_meat_g。",
        input_schema=_schema({
            "name": {"type": "string"},
            "meal": {"type": "string", "enum": list(sparky_write.MEALS)},
            "items": {"type": "array", "items": MEAL_ITEM},
            "needs_confirmation": {"type": "boolean"},
            "note": _n("string"),
        }),
        handler=lambda conn, uid, a: meals.create_template(conn, uid, a["name"], a["meal"], a["items"],
                                                           a["needs_confirmation"], a["note"]),
        scope="nutrition", writes=True,
    ),
    dict(
        name="update_meal_template",
        description="修改快捷记餐模板（例：用户报了实际克数 → 更新 items、needs_confirmation=false）。items 整份替换；"
                    "null = 不改；active=false = 删掉。",
        input_schema=_schema({
            "template_id": {"type": "integer"},
            "name": _n("string"),
            "meal": {"type": ["string", "null"], "enum": [*sparky_write.MEALS, None]},
            "items": {"type": ["array", "null"], "items": MEAL_ITEM},
            "needs_confirmation": _n("boolean"),
            "note": _n("string"),
            "active": _n("boolean"),
        }),
        handler=lambda conn, uid, a: meals.update_template(conn, uid, a["template_id"], {
            k: a[k] for k in ("name", "meal", "items", "needs_confirmation", "note", "active")}),
        scope="nutrition", writes=True,
    ),
    dict(
        name="log_meal_template",
        description="按模板记一餐进 SparkyFitness。overrides 改这次的克数（营养按比例，0 = 没吃）。当天这一餐记过会拒绝"
                    "（allow_duplicates=true 才再记）。有红肉时返回 warnings，要转告用户。返回 log_id（撤销用 undo_meal_log）。",
        input_schema=_schema({
            "template_id": {"type": "integer"},
            "date": OPT_LOG_DATE,
            "time": HHMM,
            "overrides": {"type": "array", "items": _schema({"food": {"type": "string"}, "grams": {"type": "number"}}),
                          "description": "不改就空数组"},
            "allow_duplicates": {"type": "boolean"},
        }),
        handler=lambda conn, uid, a: meals.log_template(
            conn, uid, a["template_id"], date.fromisoformat(a["date"]) if a["date"] else None, a["time"],
            a["overrides"] or None, a["allow_duplicates"], channel="butler"),
        scope="nutrition", writes=True,
    ),
    dict(
        name="undo_meal_log",
        description="撤销一次快捷记餐（log_meal_template / log_recipe / 今天页按钮返回的 log_id）。",
        input_schema=_schema({"log_id": {"type": "integer"}}),
        handler=lambda conn, uid, a: meals.undo_log(conn, uid, a["log_id"]),
        scope="nutrition", writes=True,
    ),
    # ---------- recipes ----------
    dict(
        name="list_recipes",
        description="食谱库（收藏在前）：每份热量 / 三大营养素、适合哪餐、标签、总分钟、收藏、会不会做、嘌呤 / 饱和脂肪等级、"
                    "红肉克数、最近吃的日期。推荐吃什么用 suggest_meals；做法和食材用 get_recipe。",
        input_schema=_schema({
            "user": USER,
            "slot": {"type": ["string", "null"], "enum": [*recipes.SLOTS, None]},
            "tag": _n("string"),
            "q": _n("string", "搜菜名、菜系、标签、食材"),
        }),
        handler=_recipe_list,
        scope="nutrition",
    ),
    dict(
        name="get_recipe",
        description="一道菜的全部信息：食材（整锅，每样营养、嘌呤、红肉、采购单位）、做法、几份。",
        input_schema=_schema({"user": USER, "recipe_id": {"type": "integer"}}),
        handler=lambda conn, uid, a: recipes.get_recipe(conn, uid, a["recipe_id"]),
        scope="nutrition",
    ),
    dict(
        name="suggest_meals",
        description="推荐这一餐吃什么（食谱库 3 道 + 理由）：按当天剩余热量 / 蛋白、红肉周额度、牛肋条频率、嘌呤、饱和脂肪、"
                    "最近吃过没有、收藏。不含主食的菜按配 90g 米算。excluded = 被额度排除的菜。",
        input_schema=_schema({
            "user": USER,
            "date": _n("string", "YYYY-MM-DD，null = 今天"),
            "slot": {"type": ["string", "null"], "enum": [*recipes.SLOTS, None], "description": "null = 按现在时间"},
        }),
        handler=lambda conn, uid, a: recipes.suggest(conn, uid, date.fromisoformat(a["date"]) if a["date"] else None,
                                                     a["slot"]),
        scope="nutrition",
    ),
    dict(
        name="create_recipe",
        description="新建菜谱（对话里的做法、菜的照片、网页菜谱），同时在 SparkyFitness 建镜像。食材按整锅（servings 份）写，"
                    "每样带克数和营养；嘌呤按常见含量估（每 100g：鸡约 130、鱼 110–120、牛猪 110–120、豆腐 60、蛋奶 0、米 25、"
                    "燕麦 90、蘑菇 50、青菜 30）；做法几条短句。",
        input_schema=_schema({
            "name": {"type": "string", "description": "≤ 40 字"},
            "cuisine": _n("string"),
            "slots": {"type": "array", "items": {"type": "string", "enum": list(recipes.SLOTS)}},
            "tags": {"type": "array", "items": {"type": "string"}},
            "prep_min": _n("integer"),
            "cook_min": _n("integer"),
            "servings": {"type": "number"},
            "serving_label": _n("string", "1 份指什么"),
            "includes_staple": {"type": "boolean", "description": "每份已含饭 / 面 / 燕麦"},
            "ingredients": {"type": "array", "items": INGREDIENT},
            "steps": {"type": "array", "items": {"type": "string"}},
            "note": _n("string"),
            "can_make": {"type": "boolean"},
        }),
        handler=lambda conn, uid, a: recipes.create_recipe(conn, uid, a),
        scope="nutrition", writes=True,
    ),
    dict(
        name="update_recipe",
        description="修改菜谱：收藏、会不会做、适合哪餐、标签、时间、份数、食材 / 做法（整份替换）、备注；null = 不改；"
                    "active=false = 删掉。改了食材 / 名字会同步 SparkyFitness。",
        input_schema=_schema({
            "recipe_id": {"type": "integer"},
            "name": _n("string"),
            "slots": {"type": ["array", "null"], "items": {"type": "string", "enum": list(recipes.SLOTS)}},
            "tags": {"type": ["array", "null"], "items": {"type": "string"}},
            "prep_min": _n("integer"),
            "cook_min": _n("integer"),
            "servings": _n("number"),
            "serving_label": _n("string"),
            "includes_staple": _n("boolean"),
            "ingredients": {"type": ["array", "null"], "items": INGREDIENT},
            "steps": {"type": ["array", "null"], "items": {"type": "string"}},
            "note": _n("string"),
            "favorite": _n("boolean"),
            "can_make": _n("boolean"),
            "active": _n("boolean"),
        }),
        handler=lambda conn, uid, a: recipes.update_recipe(conn, uid, a["recipe_id"],
                                                           {k: v for k, v in a.items() if k != "recipe_id"}),
        scope="nutrition", writes=True,
    ),
    dict(
        name="log_recipe",
        description="记吃了食谱里的一道菜（一条 SparkyFitness 饮食记录）。servings 吃了几份；不含主食的菜，米饭另用 log_food。"
                    "同天同餐同菜会拒绝（allow_duplicates 才再记）。返回 log_id（撤销用 undo_meal_log）。",
        input_schema=_schema({
            "recipe_id": {"type": "integer"},
            "date": OPT_LOG_DATE,
            "meal": {"type": ["string", "null"], "enum": [*recipes.SLOTS, None], "description": "null = 按现在时间"},
            "servings": {"type": "number"},
            "time": HHMM,
            "allow_duplicates": {"type": "boolean"},
        }),
        handler=lambda conn, uid, a: recipes.log_recipe(conn, uid, a["recipe_id"],
                                                        date.fromisoformat(a["date"]) if a["date"] else None, a["meal"],
                                                        a["servings"], a["time"], a["allow_duplicates"], channel="butler"),
        scope="nutrition", writes=True,
    ),
    dict(
        name="delete_observation",
        description="删除一条观察笔记（id 从 get_memory / get_briefing），例如用户说这条不对。删了就没有了。",
        input_schema=_schema({"observation_id": {"type": "integer"}}),
        handler=lambda conn, uid, a: _deleted(memory.delete_observation(conn, uid, a["observation_id"])),
        scope="memory", writes=True,
    ),
]


def _deleted(row):
    if row is None:
        raise ValueError("没有这条观察笔记（或不是你的）")
    return {"deleted": row}
