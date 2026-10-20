"""Recipe management (table recipe_meta, docs/system-design.md section 15).

- Recipe storage keeps using SparkyFitness meals (a mirror: name, steps, per-serving calories and protein); Body Engine stores the metadata: which meal it suits (slots),
  tags, times, servings, steps, ingredients (amounts for the whole pot, each with nutrition, purine, red meat grams, purchase unit, store;
  optional sugar_g / added_sugar_g / fiber_g / sodium_mg, null = unknown), favourite, can-cook, last eaten date.
- Per-serving nutrition and health-constraint flags (constraint_flags: purine, saturated fat, red meat) are computed from the ingredients; thresholds are in recipe_params.py.
- Logging: one SparkyFitness food entry (the name carries servings and "红肉 Ng", the rules engine counts red meat by that label), one row left in meal_logs,
  and undo is shared with meal templates (DELETE /meal-logs/{log_id}).
- Suggestions: score by the calories / protein left for the day (SparkyFitness day target - logged), how many meals remain after this one, the weekly red meat quota, beef short rib frequency,
  purine (weighted more when uric acid is high), saturated fat, whether eaten recently, favourites, can-cook; return 3 dishes, each with a one-line reason.
The caller is responsible for authorization (writes only for the caller).
"""
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Jsonb

from .. import sparky_client
from ..grocery.sync import offers as store_offers, params as sync_params
from ..nutrition import extras
from ..records import store as records_store
from ..rules import params as rule_params
from ..rules.nutrition import is_short_rib
from . import meals, recipe_params as RP, recipe_seed, sparky_write

SLOTS = ("breakfast", "lunch", "dinner", "snack")
SLOT_ZH = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}
STORES = ("costco", "woolworths", "other")
NUTRIENTS = ("kcal", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "purine_mg", "red_meat_g")


def sparky_meal(slot: str) -> str:
    return "snacks" if slot == "snack" else slot


# ---------------------------------------------------------------- validation and computation
def check_ingredients(items: list[dict]) -> list[dict]:
    if not items:
        raise ValueError("菜谱至少要有一样食材")
    out = []
    for it in items:
        food = (it.get("food") or "").strip()
        if not food:
            raise ValueError("每样食材都要有名字（food）")
        clean = {"food": food}
        for f in ("grams", *NUTRIENTS):
            v = it.get(f)
            if v is None and f in ("sat_fat_g", "purine_mg", "red_meat_g"):
                v = 0
            if not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"{food}：{f} 要是 ≥ 0 的数")
            clean[f] = float(v)
        if not 0 < clean["grams"] <= 5000:
            raise ValueError(f"{food}：克数 {clean['grams']:g} 不合理")
        if clean["red_meat_g"] > clean["grams"]:
            raise ValueError(f"{food}：红肉克数不能多于总克数")
        if clean["sat_fat_g"] > clean["fat_g"] + 0.5:
            raise ValueError(f"{food}：饱和脂肪不能多于总脂肪")
        est = 4 * clean["protein_g"] + 4 * clean["carbs_g"] + 9 * clean["fat_g"]
        if abs(est - clean["kcal"]) > max(15, 0.3 * clean["kcal"]):
            raise ValueError(f"{food}：热量 {clean['kcal']:g} kcal 和三大营养素算出来的 {est:.0f} kcal 差太多")
        clean.update(extras.check_optional(food, it, clean["carbs_g"]))   # null = unknown, kept as null
        store = it.get("store")
        if store not in (*STORES, None):
            raise ValueError(f"{food}：store 只能是 {' / '.join(STORES)} 或 null")
        clean.update(purchase_unit=(it.get("purchase_unit") or "").strip() or None, store=store,
                     note=(it.get("note") or "").strip() or None)
        out.append(clean)
    return out


def per_serving(ingredients: list[dict], servings: float) -> dict:
    """Per-serving nutrition; sugar / added sugar / fiber / sodium only over ingredients that know them (null when none do)."""
    return {**{f: round(sum(i.get(f) or 0 for i in ingredients) / servings, 1) for f in NUTRIENTS},
            **{f: extras.known_sum(ingredients, f, 1 / servings) for f in extras.OPTIONAL}}


def constraint_flags(ingredients: list[dict], servings: float) -> dict:
    ps = per_serving(ingredients, servings)
    purine, sat = ps["purine_mg"], ps["sat_fat_g"]
    return {"purine_mg": round(purine),
            "purine_level": "low" if purine < RP.PURINE_LOW_MG else "medium" if purine <= RP.PURINE_HIGH_MG else "high",
            "sat_fat_g": sat, "sat_fat_level": "low" if sat <= RP.SAT_FAT_LOW_G else "medium" if sat <= RP.SAT_FAT_HIGH_G else "high",
            "red_meat_g": ps["red_meat_g"], "short_rib": any(is_short_rib(i["food"]) for i in ingredients)}


def auto_tags(row: dict, ps: dict, flags: dict) -> list[str]:
    tags = []
    if ps["protein_g"] >= RP.HIGH_PROTEIN_G:
        tags.append("高蛋白")
    if flags["purine_level"] == "low" and not flags["red_meat_g"]:  # Red meat is already capped, so it is not tagged "low purine" to avoid misleading
        tags.append("低嘌呤")
    if flags["sat_fat_level"] == "low":
        tags.append("低饱和脂肪")
    if (row["prep_min"] or 0) + (row["cook_min"] or 0) <= RP.QUICK_MIN and row["prep_min"] is not None:
        tags.append("快手")
    if flags["red_meat_g"]:
        tags.append("红肉")
    if row["includes_staple"]:
        tags.append("含主食")
    return tags


def _servings(row) -> float:
    return float(row["servings"])


def view(row: dict, detail: bool = False) -> dict:
    ps = per_serving(row["ingredients"], _servings(row))
    flags = constraint_flags(row["ingredients"], _servings(row))
    out = {"id": row["id"], "name": row["name"], "slots": row["slots"],
           "tags": list(dict.fromkeys([*row["tags"], *auto_tags(row, ps, flags)])),
           "kcal": ps["kcal"], "protein_g": ps["protein_g"], "carbs_g": ps["carbs_g"], "fat_g": ps["fat_g"],
           "per_serving": row["serving_label"] or "1 份", "prep_min": row["prep_min"], "cook_min": row["cook_min"],
           "favorite": row["favorite"], "can_make": row["can_make"], "constraint_flags": flags,
           "last_cooked_on": row["last_cooked_on"], "image": row["image"]}
    if detail:
        out.update(cuisine=row["cuisine"], servings=_servings(row), includes_staple=row["includes_staple"],
                   ingredients=row["ingredients"], steps=row["steps"], note=row["note"], manual_tags=row["tags"],
                   sat_fat_g=ps["sat_fat_g"], **{f: ps[f] for f in extras.OPTIONAL},
                   sparky_meal_id=row["sparky_meal_id"], source=row["source"],
                   updated_at=row["updated_at"])
    return out


# ---------------------------------------------------------------- read
def _rows(conn, user_id: int) -> list[dict]:
    return conn.execute("SELECT * FROM recipe_meta WHERE user_id = %s AND active ORDER BY favorite DESC, name",
                        (user_id,)).fetchall()


def get_row(conn, user_id: int, recipe_id: int) -> dict:
    row = conn.execute("SELECT * FROM recipe_meta WHERE id = %s AND user_id = %s AND active",
                       (recipe_id, user_id)).fetchone()
    if row is None:
        raise LookupError("没有这个菜谱（或不是你的）")
    return row


def list_recipes(conn: psycopg.Connection, user_id: int, slot: str | None = None, tag: str | None = None,
                 q: str | None = None) -> list[dict]:
    if slot is not None and slot not in SLOTS:
        raise ValueError(f"slot 只能是 {' / '.join(SLOTS)}")
    out = []
    for r in _rows(conn, user_id):
        v = view(r)
        if slot and slot not in v["slots"]:
            continue
        if tag and tag not in v["tags"]:
            continue
        if q:
            hay = " ".join([r["name"], r["cuisine"] or "", *v["tags"], *(i["food"] for i in r["ingredients"])]).lower()
            if q.strip().lower() not in hay:
                continue
        out.append(v)
    return out


def get_recipe(conn, user_id: int, recipe_id: int) -> dict:
    return view(get_row(conn, user_id, recipe_id), detail=True)


# ---------------------------------------------------------------- write
def _check_meta(f: dict) -> dict:
    if "slots" in f:
        bad = [s for s in f["slots"] if s not in SLOTS]
        if bad or not f["slots"]:
            raise ValueError(f"slots 至少一个，只能是 {' / '.join(SLOTS)}")
        f["slots"] = list(dict.fromkeys(f["slots"]))
    if "tags" in f:
        f["tags"] = list(dict.fromkeys(t.strip() for t in f["tags"] if t and t.strip()))
        if any(len(t) > 10 for t in f["tags"]):
            raise ValueError("标签每个不超过 10 个字")
    if "name" in f and not 1 <= len(f["name"].strip()) <= 40:
        raise ValueError("菜名 1–40 字")
    if "servings" in f and not 0 < f["servings"] <= 50:
        raise ValueError("servings 要在 0–50 之间")
    for k in ("prep_min", "cook_min"):
        if k in f and f[k] is not None and not 0 <= f[k] <= 600:
            raise ValueError(f"{k} 要在 0–600 分钟之间")
    if "ingredients" in f:
        f["ingredients"] = check_ingredients(f["ingredients"])
    if "steps" in f:
        f["steps"] = [s.strip() for s in f["steps"] if s and s.strip()]
    return f


def _sync_sparky(conn, user_id: int, row: dict, client=None) -> str | None:
    """Create / update this dish's mirror in SparkyFitness (one custom food + one meal), return the meal id. Failures do not affect Body Engine."""
    client = client or sparky_client.write_client_for_user(conn, user_id)
    ps = per_serving(row["ingredients"], _servings(row))
    unit = row["serving_label"] or "1 份"
    nutr = {"calories": ps["kcal"], "protein": ps["protein_g"], "carbs": ps["carbs_g"], "fat": ps["fat_g"],
            "saturated_fat": ps["sat_fat_g"]}
    nutr.update({k: ps[f] for k, f in (("sugars", "sugar_g"), ("dietary_fiber", "fiber_g"), ("sodium", "sodium_mg"))
                 if ps[f] is not None})
    food = client.request("POST", "/foods", {"name": row["name"], "is_custom": True, "provider_type": "custom",
                                             "provider_external_id": f"recipe:{row['id']}", "serving_size": 1,
                                             "serving_unit": unit, **nutr, "notes": "身体管家的食谱镜像"})
    variant = (food.get("default_variant") or {}).get("id")
    payload = {"name": row["name"], "description": " ".join(row["steps"])[:1000], "notes": row["note"],
               "serving_size": 1, "serving_unit": "份", "total_servings": _servings(row),
               "foods": [{"food_id": food["id"], "variant_id": variant, "item_type": "food", "quantity": _servings(row),
                          "unit": unit, "serving_size": 1, "serving_unit": unit, **nutr}]}
    if row["sparky_meal_id"]:
        client.request("PUT", f"/meals/{row['sparky_meal_id']}", payload)
        return row["sparky_meal_id"]
    return client.request("POST", "/meals", payload)["id"]


def _store_flags(conn, recipe_id: int) -> dict:
    row = conn.execute("SELECT * FROM recipe_meta WHERE id = %s", (recipe_id,)).fetchone()
    conn.execute("UPDATE recipe_meta SET constraint_flags = %s WHERE id = %s",
                 (Jsonb(constraint_flags(row["ingredients"], _servings(row))), recipe_id))
    return conn.execute("SELECT * FROM recipe_meta WHERE id = %s", (recipe_id,)).fetchone()


CREATE_FIELDS = ("name", "cuisine", "slots", "tags", "prep_min", "cook_min", "servings", "serving_label",
                 "includes_staple", "ingredients", "steps", "note", "can_make", "favorite")


def create_recipe(conn, user_id: int, data: dict, source: str = "assistant", sync: bool = True, client=None) -> dict:
    f = _check_meta({k: data[k] for k in CREATE_FIELDS if data.get(k) is not None})
    for k in ("name", "slots", "ingredients"):
        if k not in f:
            raise ValueError(f"新菜谱要有 {k}")
    if conn.execute("SELECT 1 FROM recipe_meta WHERE user_id = %s AND name = %s AND active", (user_id, f["name"])).fetchone():
        raise ValueError(f"已经有「{f['name']}」了，用 update_recipe 改")
    cols = {**f, "ingredients": Jsonb(f["ingredients"]), "source": source}
    row = conn.execute(
        f"INSERT INTO recipe_meta (user_id, {', '.join(cols)}) VALUES (%s, {', '.join(['%s'] * len(cols))}) RETURNING *",
        (user_id, *cols.values())).fetchone()
    row = _store_flags(conn, row["id"])
    warning = None
    if sync:
        try:
            meal_id = _sync_sparky(conn, user_id, row, client)
            row = conn.execute("UPDATE recipe_meta SET sparky_meal_id = %s WHERE id = %s RETURNING *",
                               (meal_id, row["id"])).fetchone()
        except sparky_client.SparkyError as e:
            warning = f"SparkyFitness 镜像没建成（不影响使用）：{e}"
    out = view(row, detail=True)
    if warning:
        out["warning"] = warning
    return out


PATCH_FIELDS = ("favorite", "can_make", "slots", "tags")


def update_recipe(conn, user_id: int, recipe_id: int, changes: dict, sync: bool = True, client=None) -> dict:
    """Edit a recipe. In changes, None = unchanged; ingredients / steps are replaced whole; active=False deletes."""
    row = get_row(conn, user_id, recipe_id)
    f = _check_meta({k: v for k, v in changes.items() if v is not None and k in (*CREATE_FIELDS, "active")})
    if not f:
        raise ValueError("没有要改的内容")
    if "ingredients" in f:
        f["ingredients"] = Jsonb(f["ingredients"])
    sets = ", ".join(f"{k} = %s" for k in f)
    conn.execute(f"UPDATE recipe_meta SET {sets}, updated_at = now() WHERE id = %s", (*f.values(), row["id"]))
    row = _store_flags(conn, row["id"])
    out = view(row, detail=True)
    if sync and {"name", "ingredients", "servings", "serving_label", "steps", "note"} & set(f) and f.get("active", True):
        try:
            meal_id = _sync_sparky(conn, user_id, row, client)
            if meal_id != row["sparky_meal_id"]:
                conn.execute("UPDATE recipe_meta SET sparky_meal_id = %s WHERE id = %s", (meal_id, row["id"]))
        except sparky_client.SparkyError as e:
            out["warning"] = f"SparkyFitness 镜像没更新（不影响使用）：{e}"
    return out


# ---------------------------------------------------------------- logging
def default_slot(conn, user_id: int) -> str:
    t = meals.user_now(conn, user_id)
    m = t.hour * 60 + t.minute
    return "breakfast" if m < 630 else "lunch" if m < 900 else "dinner" if m < 1290 else "snack"


def entry_name(name: str, servings: float, red_meat_g: float) -> str:
    extra = f"，红肉 {red_meat_g:g}g" if red_meat_g else ""
    return f"{name}（{servings:g} 份{extra}）"


def log_recipe(conn: psycopg.Connection, user_id: int, recipe_id: int, day: date | None = None,
               meal: str | None = None, servings: float = 1, time: str | None = None, allow_duplicates: bool = False,
               channel: str = "api", client=None) -> dict:
    row = get_row(conn, user_id, recipe_id)
    if not 0 < servings <= 10:
        raise ValueError("servings 要在 0–10 之间")
    if meal is None:
        guess = default_slot(conn, user_id)
        slot = guess if guess in row["slots"] or not row["slots"] else row["slots"][0]
    else:
        slot = meal
    if slot not in SLOTS:
        raise ValueError(f"meal 只能是 {' / '.join(SLOTS)}")
    now = meals.user_now(conn, user_id)
    day = day or now.date()
    time = time or now.strftime("%H:%M")
    ps = per_serving(row["ingredients"], _servings(row))
    k = servings
    red = round(ps["red_meat_g"] * k, 1)
    name = entry_name(row["name"], servings, red)
    item = {"date": day.isoformat(), "meal": sparky_meal(slot), "time": time, "food": name,
            "amount": f"{servings:g} × {row['serving_label'] or '1 份'}", "calories": round(ps["kcal"] * k, 1),
            "protein_g": round(ps["protein_g"] * k, 1), "carbs_g": round(ps["carbs_g"] * k, 1),
            "fat_g": round(ps["fat_g"] * k, 1), "saturated_fat_g": round(ps["sat_fat_g"] * k, 1), "alcohol_g": None,
            **{f: None if ps[f] is None else round(ps[f] * k, 1) for f in extras.OPTIONAL}, "sugary_drink": None,
            "notes": f"食谱「{row['name']}」"}
    res = sparky_write.log_food(conn, user_id, [item], allow_duplicates, client=client)
    if not res["created"]:
        raise meals.DuplicateMealError(f"{day} 的{SLOT_ZH[slot]}已经记过「{row['name']}」，没有重复记"
                                       "（确实又吃了一份就用 allow_duplicates）")
    entries = [{"id": c["id"], "food": c["food"]} for c in res["created"]]
    logged = [{"food": name, "servings": servings, **{f: round(ps[f] * k, 1) for f in NUTRIENTS}}]
    log = conn.execute(
        """INSERT INTO meal_logs (user_id, template_id, recipe_id, date, meal, items, entries, channel)
           VALUES (%s, NULL, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (user_id, row["id"], day, sparky_meal(slot), Jsonb(logged), Jsonb(entries), channel)).fetchone()
    refresh_last_cooked(conn, row["id"])
    warnings = list(res["warnings"])
    if red:
        week = red_meat_week(conn, user_id, day)
        if week is not None and week > rule_params.RED_MEAT_G_PER_WEEK:
            warnings.append(f"近 7 天红肉已约 {week:.0f}g，超过每周 {rule_params.RED_MEAT_G_PER_WEEK}g 的上限")
    return {"log_id": log["id"], "recipe_id": row["id"], "name": row["name"], "date": day.isoformat(), "time": time,
            "meal": sparky_meal(slot), "servings": servings, "entries": entries,
            "totals": {f: round(ps[f] * k, 1) for f in ("kcal", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "red_meat_g")},
            "warnings": warnings}


def refresh_last_cooked(conn, recipe_id: int) -> None:
    conn.execute("""UPDATE recipe_meta SET last_cooked_on = (SELECT max(date) FROM meal_logs
                    WHERE recipe_id = %s AND undone_at IS NULL) WHERE id = %s""", (recipe_id, recipe_id))


# ---------------------------------------------------------------- suggestions
def red_meat_week(conn, user_id: int, today: date) -> float | None:
    from .status import red_meat_grams
    try:
        return red_meat_grams(conn, user_id, today)
    except sparky_client.SparkyError:
        return None


def _short_rib_recent(food: list[dict]) -> bool:
    return any(is_short_rib(f.get("food") or "") for f in food)


def _half_label(h: dict) -> str:
    save = f" 省 ${h['save']:g}" if h.get("save") else ""
    return f"{h['food']}（{h['label']}{save}）"


def suggest(conn: psycopg.Connection, user_id: int, day: date | None = None, slot: str | None = None,
            client=None) -> dict:
    day = day or meals.user_now(conn, user_id).date()
    slot = slot or default_slot(conn, user_id)
    if slot not in SLOTS:
        raise ValueError(f"slot 只能是 {' / '.join(SLOTS)}")
    client = client or sparky_client.client_for_user(conn, user_id)
    from ..goals import nutrition as nutrition_goals
    goal = nutrition_goals.resolve(conn, user_id, day, client)
    week_food = sparky_client.food_entries(client, day - timedelta(days=6), day)
    eaten = [f for f in week_food if f["date"] == day.isoformat()]
    sums = {k: sum(f.get(k) or 0 for f in eaten) for k in ("calories", "protein_g", "carbs_g", "fat_g")}
    remaining = {"kcal": round((goal["calories"] or 0) - sums["calories"]),
                 "protein_g": round((goal["protein_g"] or 0) - sums["protein_g"]),
                 "carbs_g": round((goal["carbs_g"] or 0) - sums["carbs_g"]),
                 "fat_g": round((goal["fat_g"] or 0) - sums["fat_g"])}
    logged_meals = {f.get("meal_type") for f in eaten}
    assumed = []
    if slot in RP.SLOT_ORDER:
        i = RP.SLOT_ORDER.index(slot)
        assumed = [s for s in RP.SLOT_ORDER[:i] if s not in logged_meals]  # Earlier main meals with no record: assume they were eaten in the usual proportions
        later = [s for s in RP.SLOT_ORDER[i + 1:] if s not in logged_meals]
        pool = {k: remaining[k] - sum(goal_val * RP.SLOT_SHARE[s] for s in assumed)
                for k, goal_val in (("kcal", goal["calories"] or 0), ("protein_g", goal["protein_g"] or 0))}
        share = RP.SLOT_SHARE[slot] / (RP.SLOT_SHARE[slot] + sum(RP.SLOT_SHARE[s] for s in later))
        target = {k: max(v * share, 0) for k, v in pool.items()}
    else:  # Snack: at most 10% of the day's total from what is left
        target = {k: max(min(remaining[k], g * RP.SLOT_SHARE["snack"]), 0)
                  for k, g in (("kcal", goal["calories"] or 0), ("protein_g", goal["protein_g"] or 0))}
    week_red = red_meat_week(conn, user_id, day)
    red_left = None if week_red is None else rule_params.RED_MEAT_G_PER_WEEK - week_red
    rib_recent = _short_rib_recent(week_food)
    uric_high = any(r["test_key"] == "uric_acid" and r["status"] == "high"
                    for r in records_store.lab_report(conn, user_id, ["uric_acid"]))
    purine_w = 1.5 if uric_high else 1.0
    picks, excluded = [], []
    rows = _rows(conn, user_id)
    # this week's half price in the user's connected supermarkets (grocery-sync; {} when off / nothing connected)
    half = store_offers.half_price_hits(conn, user_id, [i["food"] for r in rows if slot in r["slots"] for i in r["ingredients"]])
    for row in rows:
        if slot not in row["slots"]:
            continue
        v = view(row)
        flags = v["constraint_flags"]
        if flags["short_rib"] and rib_recent:
            excluded.append({"id": row["id"], "name": row["name"], "reason": "近 7 天已经吃过牛肋条（每周最多 1 次）"})
            continue
        if flags["red_meat_g"] and red_left is not None and flags["red_meat_g"] > red_left:
            excluded.append({"id": row["id"], "name": row["name"],
                             "reason": f"本周红肉只剩 {max(red_left, 0):.0f}g，这道要 {flags['red_meat_g']:g}g"})
            continue
        staple = slot in ("lunch", "dinner") and not row["includes_staple"]
        kcal = v["kcal"] + (RP.STAPLE["kcal"] if staple else 0)
        prot = v["protein_g"] + (RP.STAPLE["protein_g"] if staple else 0)
        score = 0.0
        if target["kcal"] > 0:
            score += RP.W_KCAL * abs(kcal - target["kcal"]) / target["kcal"]
        else:
            score += RP.W_KCAL * kcal / 500  # Today's calories are already enough: the lighter the better
        if target["protein_g"] > 0:
            score += RP.W_PROTEIN * max(target["protein_g"] - prot, 0) / target["protein_g"]
        score += purine_w * RP.W_PURINE[flags["purine_level"]] + RP.W_SAT_FAT[flags["sat_fat_level"]]
        recent = row["last_cooked_on"] and (day - row["last_cooked_on"]).days <= RP.RECENT_DAYS
        if recent:
            score += RP.W_RECENT
        score -= (RP.BONUS_FAVORITE if row["favorite"] else 0) + (RP.BONUS_CAN_MAKE if row["can_make"] else 0)
        hp = [{"food": i["food"], **{k: half[i["food"]][k] for k in ("label", "name", "price", "save")}}
              for i in row["ingredients"] if i["food"] in half]
        score -= min(sync_params.BONUS_HALF_PRICE * len(hp), sync_params.BONUS_HALF_PRICE_MAX)
        picks.append((score, row, v, staple, kcal, prot, recent, hp))
    picks.sort(key=lambda x: x[0])
    out = []
    for score, row, v, staple, kcal, prot, recent, hp in picks[:RP.SUGGEST_COUNT]:
        bits = [f"这一餐还能吃约 {target['kcal']:.0f} kcal、蛋白 {target['protein_g']:.0f}g",
                f"这道{'配饭 90g ' if staple else ''}约 {kcal:.0f} kcal、蛋白 {prot:.0f}g"]
        f = v["constraint_flags"]
        if f["purine_level"] == "low" and not f["red_meat_g"]:
            bits.append("低嘌呤")
        elif f["purine_level"] == "high":
            bits.append("嘌呤偏高，汤少喝")
        if f["red_meat_g"] and red_left is not None:
            bits.append(f"吃完本周红肉还剩 {red_left - f['red_meat_g']:.0f}g")
        if row["favorite"]:
            bits.append("你收藏的")
        if not row["can_make"]:
            bits.append("还没做过，可以学")
        if hp:
            bits.append("本周半价：" + "、".join(_half_label(h) for h in hp))
        out.append({**v, "with_staple": staple, "meal_kcal": round(kcal), "meal_protein_g": round(prot),
                    "score": round(score, 3), "reason": "；".join(bits) + "。", **({"half_price": hp} if hp else {})})
    return {"date": day.isoformat(), "slot": slot, "goal": {"name": goal["name"], "kcal": goal["calories"],
                                                            "protein_g": goal["protein_g"]},
            "eaten": {"kcal": round(sums["calories"]), "protein_g": round(sums["protein_g"])},
            "remaining": remaining, "meal_target": {k: round(v) for k, v in target.items()},
            "assumed_eaten": [sparky_meal(x) for x in assumed],
            "red_meat_week_g": None if week_red is None else round(week_red),
            "suggestions": out, "excluded": excluded}


# ---------------------------------------------------------------- initial version
def seed(conn: psycopg.Connection, user_id: int, client=None) -> dict:
    """Create metadata from recipe_seed.RECIPES for meals already in SparkyFitness (matched by name; existing ones untouched)."""
    client = client or sparky_client.client_for_user(conn, user_id)
    by_name = {m["name"]: m["id"] for m in client.get("/meals", filter="mine")}
    created, kept, missing = [], [], []
    for r in recipe_seed.RECIPES:
        if conn.execute("SELECT 1 FROM recipe_meta WHERE user_id = %s AND import_key = %s",
                        (user_id, f"md:{r['key']}")).fetchone():
            kept.append(r["name"])
            continue
        meal_id = by_name.get(r["name"])
        if meal_id is None:
            missing.append(r["name"])
        ings = [recipe_seed.ingredient(*x) for x in r["ingredients"]]
        row = conn.execute(
            """INSERT INTO recipe_meta (user_id, sparky_meal_id, name, cuisine, slots, tags, prep_min, cook_min, servings,
                                        serving_label, includes_staple, ingredients, steps, note, favorite, can_make,
                                        source, import_key)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'seed', %s) RETURNING id""",
            (user_id, meal_id, r["name"], r["cuisine"], r["slots"], r["tags"], r.get("prep_min"), r.get("cook_min"),
             r.get("servings", 1), r.get("serving_label"), r.get("includes_staple", False), Jsonb(ings), r["steps"],
             r.get("note"), r.get("favorite", False), r.get("can_make", False), f"md:{r['key']}")).fetchone()
        _store_flags(conn, row["id"])
        created.append(r["name"])
    return {"created": created, "kept": kept, "not_in_sparkyfitness": missing}
