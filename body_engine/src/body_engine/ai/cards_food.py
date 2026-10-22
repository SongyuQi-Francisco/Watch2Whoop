"""Diet cards of the in-page chat (rollout flag jarvis_cards_v2 on top of jarvis_rich; ai/cards.py dispatches here).

Each builder reads the user's own data through the same functions the food page uses, so a card shows what the page
shows: goals (that day's intake against its targets), suggest (suggest_meals / the per-meal plan), recipe (one recipe),
recipes (the recipe library, filtered), templates (quick meal templates), shopping (this week's shopping list).
Buttons are open / send actions only (see ai/cards.py); the texts a row button sends are built by the page (ai.js)
from the row's own fields, never from the model.
"""
from datetime import date

import psycopg

MAX_ITEMS, MAX_RECIPES, MAX_INGREDIENTS, MAX_BADGES, MAX_SHOP = 3, 8, 12, 4, 30
GOAL_TOTALS = ("calories", "protein_g", "carbs_g", "fat_g", "saturated_fat_g", "added_sugar_g", "fiber_g")
GOAL_FIELDS = ("calories", "protein_g", "carbs_g", "fat_g", "sat_fat_max_g", "added_sugar_max_g", "fiber_min_g")
SLOTS_ALL = ("breakfast", "lunch", "dinner", "snack", "all")


def _image(url) -> str | None:
    """Only the Today page's own images (a same-origin path): no third-party request from a chat card."""
    u = str(url or "")
    return u if u.startswith("/") and not u.startswith("//") else None


def _badges(xs) -> list[dict]:
    return [{"label": b.get("label"), "tone": b.get("tone")} for b in (xs or []) if isinstance(b, dict)][:MAX_BADGES]


def _brief(r: dict) -> dict:
    """A recipe row: per serving, or per meal (with the rice it needs) for a suggestion."""
    return {"id": r["id"], "name": r["name"], "kcal": r.get("meal_kcal", r.get("kcal")),
            "protein_g": r.get("meal_protein_g", r.get("protein_g")), "with_staple": bool(r.get("with_staple")),
            "min": (r.get("prep_min") or 0) + (r.get("cook_min") or 0) or None, "favorite": bool(r.get("favorite")),
            "badges": _badges(r.get("labels") or r.get("badges")), "image": _image(r.get("image"))}


def goals(conn: psycopg.Connection, uid: int, day: date) -> dict:
    from ..nutrition import daily
    d = daily.nutrition(conn, uid, day, day, include_entries=False)["days"][0]
    g = d.get("goal") or {}
    return {"type": "goals", "date": day.isoformat(), "logged": d["logged"], "name": g.get("name"),
            "day_type": g.get("day_type"), "totals": {k: d.get(k) for k in GOAL_TOTALS},
            "goal": {k: g.get(k) for k in GOAL_FIELDS} if g.get("calories") or g.get("protein_g") else None,
            "actions": [{"kind": "open", "page": "food"}, {"kind": "send", "key": "next_meal"}]}


def suggest(conn: psycopg.Connection, uid: int, day: date, slot: str | None) -> dict:
    from ..assistant import recipes
    if slot is not None and slot not in SLOTS_ALL:
        raise ValueError(f"slot 只能是 {' / '.join(SLOTS_ALL)}")
    if slot == "all":
        plan = recipes.meal_plan(conn, uid, day)
        slots = [{"slot": s["slot"], "logged": s["logged"], "target": s["target"],
                  "items": [_brief(x) for x in s["suggestions"][:MAX_ITEMS]]} for s in plan["slots"]]
    else:
        plan = recipes.suggest(conn, uid, day, slot)
        slots = [{"slot": plan["slot"], "logged": None, "target": plan["meal_target"],
                  "items": [_brief(x) for x in plan["suggestions"][:MAX_ITEMS]]}]
    rem = plan.get("remaining") or {}
    return {"type": "suggest", "date": plan["date"], "all": slot == "all",
            "remaining": {k: rem.get(k) for k in ("kcal", "protein_g")}, "slots": slots,
            "actions": [{"kind": "send", "key": "more_meals"}, {"kind": "open", "page": "food"}]}


def recipe(conn: psycopg.Connection, uid: int, recipe_id) -> dict:
    from ..assistant import recipes
    try:
        r = recipes.get_recipe(conn, uid, int(recipe_id))
    except (LookupError, TypeError, ValueError):
        raise ValueError("没有这个菜谱（id 用 list_recipes / suggest_meals 里的）") from None
    servings = float(r.get("servings") or 1) or 1
    ings = [{"food": i.get("food"), "grams": None if i.get("grams") is None else round(float(i["grams"]) / servings)}
            for i in r.get("ingredients") or []][:MAX_INGREDIENTS]
    tags = [t for t in r.get("manual_tags") or [] if t][:3]
    return {"type": "recipe", **_brief(r), "carbs_g": r.get("carbs_g"), "fat_g": r.get("fat_g"),
            "per_serving": None if r.get("per_serving") in ("1 份", "1 serving") else r.get("per_serving"), "can_make": r.get("can_make"), "tags": tags,
            "ingredients": ings, "more_ingredients": max(0, len(r.get("ingredients") or []) - MAX_INGREDIENTS),
            "steps": len(r.get("steps") or []), "last_cooked_on": r.get("last_cooked_on"),
            "actions": [{"kind": "send", "key": "log_recipe"}, {"kind": "send", "key": "recipe_steps"},
                        {"kind": "open", "page": "recipe"}]}


def recipe_list(conn: psycopg.Connection, uid: int, slot: str | None) -> dict:
    from ..assistant import recipes
    if slot is not None and slot not in recipes.SLOTS:
        slot = None
    rows = recipes.list_recipes(conn, uid, slot)
    return {"type": "recipes", "slot": slot, "total": len(rows), "items": [_brief(r) for r in rows[:MAX_RECIPES]],
            "actions": [{"kind": "open", "page": "food.recipes"}, {"kind": "send", "key": "next_meal"}]}


def templates(conn: psycopg.Connection, uid: int) -> dict:
    from ..assistant import meals
    rows = sorted(meals.list_templates(conn, uid), key=lambda t: -(t.get("uses_30d") or 0))
    items = [{"id": t["id"], "name": t["name"], "meal": t["meal"], "kcal": (t.get("totals") or {}).get("kcal"),
              "protein_g": (t.get("totals") or {}).get("protein_g"), "needs_confirmation": bool(t["needs_confirmation"]),
              "logged": bool(t.get("today_log")), "uses_30d": t.get("uses_30d") or 0} for t in rows[:MAX_RECIPES]]
    return {"type": "templates", "total": len(rows), "items": items, "actions": [{"kind": "open", "page": "food"}]}


def shopping(conn: psycopg.Connection, uid: int, day: date | None) -> dict:
    from ..grocery import store
    lst = store.get_list(conn, uid, day)
    items = [{"id": i["id"], "name": i["name"], "qty": i.get("qty_label"), "category": i.get("category"),
              "category_label": i.get("category_label"), "bought": i["bought"], "staple": i.get("staple")}
             for i in lst["items"]]
    return {"type": "shopping", "week_start": lst["week_start"], "week_end": lst["week_end"], "progress": lst["progress"],
            "est_total": lst.get("est_total") or {}, "unpriced": lst.get("unpriced") or 0,
            "items": items[:MAX_SHOP], "more": max(0, len(items) - MAX_SHOP),
            "warnings": [w.get("label") for w in lst.get("warnings") or [] if w.get("label")][:3],
            "actions": [{"kind": "open", "page": "food.shopping"}, {"kind": "send", "key": "shopping_plan"}]}


def summary(card: dict) -> str | None:
    """The model's one line for a diet card (AI-facing, Chinese); None for other types."""
    t = card["type"]
    if t == "goals":
        tot, g = card["totals"], card["goal"] or {}
        if not g:
            return f"已显示 {card['date']} 饮食目标卡：这天没有设饮食目标（可以 set_nutrition_goals）。"
        return (f"已显示 {card['date']} 饮食目标卡（{card['name'] or ''}）：热量 {tot['calories'] or 0:g}/{g['calories'] or 0:g} kcal，"
                f"蛋白 {tot['protein_g'] or 0:g}/{g['protein_g'] or 0:g} g。不要用文字复述卡片里的数字。")
    if t == "suggest":
        names = "；".join(f"{s['slot']}：" + "、".join(f"{x['name']}(id {x['id']})" for x in s["items"]) for s in card["slots"] if s["items"])
        return (f"已显示推荐餐卡：{names or '没有合适的菜'}。用户点「记下这餐」会发一条消息让你按食谱记；"
                "点「换一个」会要别的推荐。不要用文字复述卡片里的数字。")
    if t == "recipe":
        return (f"已显示食谱卡「{card['name']}」(id {card['id']})：每份 {card['kcal'] or 0:g} kcal、蛋白 {card['protein_g'] or 0:g} g，"
                f"{len(card['ingredients'])} 样食材、{card['steps']} 步。不要用文字复述卡片内容。")
    if t == "recipes":
        names = "、".join(f"{x['name']}(id {x['id']})" for x in card["items"])
        return f"已显示食谱列表卡（共 {card['total']} 道）：{names or '没有食谱'}。不要用文字复述卡片内容。"
    if t == "templates":
        names = "、".join(f"{x['name']}(id {x['id']}{'，今天已记' if x['logged'] else ''})" for x in card["items"])
        return f"已显示快捷记餐模板卡：{names or '还没有模板'}。用户点「记一次」会发一条消息让你按模板记。"
    if t == "shopping":
        p = card["progress"]
        return (f"已显示 {card['week_start']} 这周的采购清单卡：{p['bought']}/{p['total']} 已买。"
                "不要用文字复述清单。")
    return None
