"""Quick meal logging: a frequently eaten meal is saved as a template (table meal_templates); one tap on the Today page or one sentence to the butler logs it into SparkyFitness.

- Each food in a template: food, grams, kcal, protein_g, carbs_g, fat_g, sat_fat_g, purine_level (low / medium / high),
  red_meat_g (raw red meat grams; processed meats like bacon / ham count too), optional sugar_g / added_sugar_g / fiber_g /
  sodium_mg (null = unknown, issue #31), note. If the grams are estimated, needs_confirmation=true.
- Logging = one SparkyFitness food entry per food (via sparky_write.log_food: duplicate detection, source=body_assistant),
  the name carries grams and "红肉 Ng", and the rules engine and status cards count red meat by the labelled grams. Each logging leaves a row in meal_logs (entry ids),
  and undo deletes the SparkyFitness entries by it (only ones the butler wrote).
The caller is responsible for authorization (writes only for the caller).
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ..i18n import L
from ..nutrition import constraints as diet_constraints, extras
from . import sparky_write

PURINE = ("low", "medium", "high")
PURINE_ZH = {"low": "低嘌呤", "medium": "中嘌呤", "high": "高嘌呤"}
NUM_FIELDS = ("grams", "kcal", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "red_meat_g")
SCALED = ("kcal", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "red_meat_g")
TOTAL_FIELDS = ("kcal", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "red_meat_g")


class DuplicateMealError(ValueError):
    pass


def check_items(items: list[dict]) -> list[dict]:
    if not items:
        raise ValueError("模板至少要有一样食物")
    out, names = [], set()
    for it in items:
        food = (it.get("food") or "").strip()
        if not food:
            raise ValueError("每样食物都要有名字（food）")
        if food in names:
            raise ValueError(f"「{food}」重复了，同一个模板里名字不能重复")
        names.add(food)
        clean = {"food": food}
        for f in NUM_FIELDS:
            v = it.get(f)
            if v is None and f in ("sat_fat_g", "red_meat_g"):
                v = 0
            if not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"{food}：{f} 要是 ≥ 0 的数")
            clean[f] = float(v)
        if clean["grams"] <= 0 or clean["grams"] > 2000:
            raise ValueError(f"{food}：克数 {clean['grams']:g} 不合理")
        if clean["red_meat_g"] > clean["grams"]:
            raise ValueError(f"{food}：红肉克数不能多于总克数")
        if clean["sat_fat_g"] > clean["fat_g"] + 0.5:
            raise ValueError(f"{food}：饱和脂肪不能多于总脂肪")
        est = 4 * clean["protein_g"] + 4 * clean["carbs_g"] + 9 * clean["fat_g"]
        if abs(est - clean["kcal"]) > max(15, 0.25 * clean["kcal"]):
            raise ValueError(f"{food}：热量 {clean['kcal']:g} kcal 和三大营养素算出来的 {est:.0f} kcal 差太多")
        clean.update(extras.check_optional(food, it, clean["carbs_g"]))   # null = unknown, kept as null
        purine = it.get("purine_level")
        if purine not in (*PURINE, None):
            raise ValueError(f"{food}：purine_level 只能是 low / medium / high")
        clean["purine_level"] = purine
        clean["note"] = (it.get("note") or "").strip() or None
        out.append(clean)
    return out


def totals(items: list[dict]) -> dict:
    """Sums; sugar / added sugar / fiber / sodium only over items that know them (null when none do)."""
    return {**{f: round(sum(i.get(f) or 0 for i in items), 1) for f in TOTAL_FIELDS},
            **{f: extras.known_sum(items, f) for f in extras.OPTIONAL}}


def _view(row: dict, today_log: dict | None = None) -> dict:
    return {"id": row["id"], "name": row["name"], "meal": row["meal"], "items": row["items"],
            "totals": totals(row["items"]), "needs_confirmation": row["needs_confirmation"], "note": row["note"],
            "updated_at": row["updated_at"], "today_log": today_log}


def user_now(conn, user_id: int) -> datetime:
    tz = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"]
    return datetime.now(ZoneInfo(tz))


def list_templates(conn: psycopg.Connection, user_id: int) -> list[dict]:
    rows = conn.execute("""SELECT * FROM meal_templates WHERE user_id = %s AND active ORDER BY sort, id""",
                        (user_id,)).fetchall()
    today = user_now(conn, user_id).date()
    logs = {}
    for r in conn.execute("""SELECT id, template_id, created_at FROM meal_logs
                             WHERE user_id = %s AND date = %s AND undone_at IS NULL ORDER BY id""", (user_id, today)):
        logs[r["template_id"]] = {"log_id": r["id"], "logged_at": r["created_at"]}
    # How often each template was logged in the last 30 days (not undone): the Today page shows the most used first
    uses = {r["template_id"]: r["n"] for r in conn.execute(
        """SELECT template_id, count(*) AS n FROM meal_logs
           WHERE user_id = %s AND date > %s::date - 30 AND undone_at IS NULL AND template_id IS NOT NULL
           GROUP BY template_id""", (user_id, today))}
    return [{**_view(r, logs.get(r["id"])), "uses_30d": uses.get(r["id"], 0)} for r in rows]


def _get(conn, user_id: int, template_id: int) -> dict:
    row = conn.execute("SELECT * FROM meal_templates WHERE id = %s AND user_id = %s AND active",
                       (template_id, user_id)).fetchone()
    if row is None:
        raise LookupError("没有这个模板（或不是你的）")
    return row


def create_template(conn, user_id: int, name: str, meal: str, items: list[dict], needs_confirmation: bool,
                    note: str | None = None) -> dict:
    if meal not in sparky_write.MEALS:
        raise ValueError(f"meal 只能是 {', '.join(sparky_write.MEALS)}")
    name = (name or "").strip()
    if not name or len(name) > 30:
        raise ValueError("模板名 1–30 字")
    if conn.execute("SELECT 1 FROM meal_templates WHERE user_id = %s AND name = %s AND active", (user_id, name)).fetchone():
        raise ValueError(f"已经有叫「{name}」的模板，用 update_meal_template 改")
    conn.execute("DELETE FROM meal_templates WHERE user_id = %s AND name = %s AND NOT active", (user_id, name))
    row = conn.execute(
        """INSERT INTO meal_templates (user_id, name, meal, items, needs_confirmation, note)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
        (user_id, name, meal, Jsonb(check_items(items)), needs_confirmation, note)).fetchone()
    return _view(row)


def update_template(conn, user_id: int, template_id: int, changes: dict) -> dict:
    """changes: name / meal / items (replaced whole) / needs_confirmation / note / active; None = unchanged."""
    cur = _get(conn, user_id, template_id)
    f = {k: v for k, v in changes.items() if v is not None}
    if "items" in f:
        f["items"] = Jsonb(check_items(f["items"]))
    if "meal" in f and f["meal"] not in sparky_write.MEALS:
        raise ValueError(f"meal 只能是 {', '.join(sparky_write.MEALS)}")
    if not f:
        raise ValueError("没有要改的内容")
    sets = ", ".join(f"{k} = %s" for k in f)
    row = conn.execute(f"UPDATE meal_templates SET {sets}, updated_at = now() WHERE id = %s AND user_id = %s RETURNING *",
                       (*f.values(), cur["id"], user_id)).fetchone()
    return _view(row)


def scale_items(items: list[dict], overrides: list[dict] | None) -> list[dict]:
    """Scale nutrition proportionally by the overridden grams. overrides: [{food, grams}], food must match the name in the template."""
    if not overrides:
        return [dict(i) for i in items]
    by_name = {o["food"]: o["grams"] for o in overrides}
    unknown = set(by_name) - {i["food"] for i in items}
    if unknown:
        raise ValueError(f"模板里没有：{'、'.join(sorted(unknown))}")
    out = []
    for i in items:
        g = by_name.get(i["food"])
        if g is None:
            out.append(dict(i))
            continue
        if not isinstance(g, (int, float)) or g < 0 or g > 2000:
            raise ValueError(f"{i['food']}：克数 {g} 不合理")
        if g == 0:
            continue  # Not eaten this time
        k = g / i["grams"]
        out.append({**i, "grams": float(g), **{f: round(i[f] * k, 1) for f in SCALED},
                    **{f: round(i[f] * k, 1) for f in extras.OPTIONAL if i.get(f) is not None}})
    if not out:
        raise ValueError("全部克数都是 0，没东西可记")
    return out


def entry_name(i: dict) -> str:
    extra = f"，红肉 {i['red_meat_g']:g}g" if i.get("red_meat_g") else ""
    return f"{i['food']}（{i['grams']:g}g{extra}）"


def red_meat_warning(items: list[dict], cap: float | None) -> str | None:
    """Only for a user with a red meat constraint (cap = their g per week, nutrition/constraints.py, issue #76)."""
    g = sum(i.get("red_meat_g") or 0 for i in items)
    if not g or cap is None:
        return None
    names = "、".join(i["food"] for i in items if i.get("red_meat_g"))
    return L(f"{names}是红肉 / 加工肉：这份 {g:g}g，算进你设的每周 ≤ {cap:g}g；天天吃一周约 {g * 7:.0f}g"
             + ("，会超上限" if g * 7 > cap else ""),
             f"{names}: red / processed meat, {g:g} g, counts toward your {cap:g} g a week; every day that is about "
             f"{g * 7:.0f} g a week" + (", over your limit" if g * 7 > cap else ""))


def log_template(conn: psycopg.Connection, user_id: int, template_id: int, day: date | None = None,
                 time: str | None = None, overrides: list[dict] | None = None, allow_duplicates: bool = False,
                 channel: str = "api", client=None) -> dict:
    tpl = _get(conn, user_id, template_id)
    items = scale_items(tpl["items"], overrides)
    now = user_now(conn, user_id)
    day = day or now.date()
    time = time or now.strftime("%H:%M")
    food = [{"date": day.isoformat(), "meal": tpl["meal"], "time": time, "food": entry_name(i),
             "amount": f"{i['grams']:g} g", "calories": i["kcal"], "protein_g": i["protein_g"], "carbs_g": i["carbs_g"],
             "fat_g": i["fat_g"], "saturated_fat_g": i["sat_fat_g"], "alcohol_g": None,
             **{f: i.get(f) for f in extras.OPTIONAL}, "sugary_drink": None,
             "notes": "；".join(x for x in (f"快捷记餐「{tpl['name']}」", PURINE_ZH.get(i.get("purine_level")),
                                           i.get("note"), "克数待确认" if tpl["needs_confirmation"] else None) if x)}
            for i in items]
    res = sparky_write.log_food(conn, user_id, food, allow_duplicates, client=client, deduct=False)
    if not res["created"]:
        raise DuplicateMealError(f"{day} 的{sparky_write.MEALS[tpl['meal']]}已经记过「{tpl['name']}」，没有重复记"
                                 "（确实又吃了一份就用 allow_duplicates）")
    entries = [{"id": c["id"], "food": c["food"]} for c in res["created"]]
    logged_names = {c["food"] for c in res["created"]}
    logged_items = [i for i in items if entry_name(i) in logged_names]
    from ..grocery import pantry_auto   # grocery imports assistant
    pantry = []
    for c in res["created"]:   # each item is one entry: its raw food name and grams against the pantry (issue #104)
        i = next((x for x in items if entry_name(x).strip() == c["food"]), None)
        if i is not None and i["grams"]:
            pantry += pantry_auto.deduct(conn, user_id, c["id"], day, [(i["food"], float(i["grams"]), "g")])
    row = conn.execute(
        """INSERT INTO meal_logs (user_id, template_id, date, meal, items, entries, channel)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at""",
        (user_id, tpl["id"], day, tpl["meal"], Jsonb(logged_items), Jsonb(entries), channel)).fetchone()
    cap = diet_constraints.for_user(conn, user_id, day).red_meat_g
    warnings = [w for w in (red_meat_warning(logged_items, cap),
                            "模板克数还没确认，是估算值" if tpl["needs_confirmation"] else None) if w]
    return {"log_id": row["id"], "template_id": tpl["id"], "name": tpl["name"], "date": day.isoformat(), "time": time,
            "meal": tpl["meal"], "entries": entries, "totals": totals(logged_items),
            "skipped_duplicates": [s["food"] for s in res["skipped_duplicates"]], "warnings": warnings + res["warnings"],
            **({"pantry_used": pantry} if pantry else {})}


def undo_log(conn: psycopg.Connection, user_id: int, log_id: int, client=None) -> dict:
    row = conn.execute("SELECT * FROM meal_logs WHERE id = %s AND user_id = %s", (log_id, user_id)).fetchone()
    if row is None:
        raise LookupError("没有这次记录（或不是你的）")  # Shared by quick meal logging and recipe logging
    if row["undone_at"] is not None:
        raise LookupError("这次记录已经撤销过了")
    deleted, missing = [], []
    for e in row["entries"]:
        try:
            sparky_write.undo_entry(conn, user_id, "food", row["date"].isoformat(), e["id"], client=client)
            deleted.append(e)
        except ValueError:  # Already deleted in the app
            missing.append(e)
    conn.execute("UPDATE meal_logs SET undone_at = now() WHERE id = %s", (log_id,))
    if row.get("recipe_id"):
        from .recipes import refresh_last_cooked
        refresh_last_cooked(conn, row["recipe_id"])
    return {"log_id": log_id, "date": row["date"].isoformat(), "deleted": deleted, "already_gone": missing}


# ---------------------------------------------------------------- initial version
FIXED_BREAKFAST = {  # User description (2026-10-17): two slices of bread, avocado, two slices of bacon, a cup of coffee, two fried eggs; grams estimated from common portions, to be confirmed
    "name": "固定早餐", "meal": "breakfast", "needs_confirmation": True,
    "note": "克数是按常见份量估的，告诉管家实际克数后更新",
    "items": [
        {"food": "面包 2 片", "grams": 70, "kcal": 175, "protein_g": 6.5, "carbs_g": 30, "fat_g": 2.2, "sat_fat_g": 0.5,
         "purine_level": "low", "red_meat_g": 0, "note": "按普通吐司每片 35g 估"},
        {"food": "牛油果", "grams": 45, "kcal": 72, "protein_g": 0.9, "carbs_g": 4, "fat_g": 6.6, "sat_fat_g": 1.0,
         "purine_level": "low", "red_meat_g": 0, "note": "约 1/4 个 Hass（body-management.md 5.2.2：40–50g）"},
        {"food": "培根 2 片", "grams": 40, "kcal": 120, "protein_g": 10, "carbs_g": 0.5, "fat_g": 9, "sat_fat_g": 3.2,
         "purine_level": "medium", "red_meat_g": 40, "note": "加工红肉；short cut bacon 更瘦"},
        {"food": "咖啡 1 杯", "grams": 250, "kcal": 5, "protein_g": 0.3, "carbs_g": 0.5, "fat_g": 0.1, "sat_fat_g": 0,
         "purine_level": "low", "red_meat_g": 0, "note": "按黑咖啡估；加奶请告诉管家"},
        {"food": "煎蛋 2 个", "grams": 100, "kcal": 175, "protein_g": 12.5, "carbs_g": 0.6, "fat_g": 13.5,
         "sat_fat_g": 3.5, "purine_level": "low", "red_meat_g": 0, "note": "2 个大号鸡蛋 + 约 3g 油"},
    ],
}


def seed_templates(conn, user_id: int) -> list[str]:
    if conn.execute("SELECT 1 FROM meal_templates WHERE user_id = %s AND name = %s",
                    (user_id, FIXED_BREAKFAST["name"])).fetchone():
        return []
    create_template(conn, user_id, FIXED_BREAKFAST["name"], FIXED_BREAKFAST["meal"], FIXED_BREAKFAST["items"],
                    True, FIXED_BREAKFAST["note"])
    return [FIXED_BREAKFAST["name"]]

