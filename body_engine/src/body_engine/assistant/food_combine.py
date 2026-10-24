"""Food entries already logged on one day -> a quick-meal template, or merged into one entry (issue #93, migration 480).

- template: each selected entry becomes one item of a new meal template (grams from the entry's portion; when a portion is
  not in grams the item is kept at its logged nutrition and the template is marked "grams to confirm"). The entries stay.
- merge: one new SparkyFitness entry with the summed nutrition (kcal, macros, saturated fat; sugar / added sugar / fiber /
  sodium summed over the entries that know them, unknown stays unknown) replaces the selected entries. The originals are
  kept whole in food_merges, so split() puts them back (Today page 「拆开」, or rollback_write for the AI: kind merge).
  Red meat labelled in the names ("红肉 Ng") is carried over as one total label, so the weekly red-meat count stays right.
- both: the template first, then the merge.
Everything goes through the caller's own SparkyFitness read-write key, so only the caller's own entries can be read or changed;
ids from another day or another user are "not found". AI calls are logged in ai_write_log (writelog.py).
"""
import re
from collections import Counter
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from .. import sparky_client
from ..i18n import L
from ..nutrition import extras
from ..ownstore import mirror
from ..rules.nutrition import RED_MEAT_MARK
from . import meals, sparky_write, writelog

ACTIONS = ("template", "merge", "both")
MAX_ENTRIES = 20
_GRAMS_UNIT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(g|克|ml|毫升)\s*$", re.I)
_GRAMS_NAME = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*(?:g|克)")
_TAIL = re.compile(r"\s*[（(][^（）()]*(?:\d\s*(?:g|克|ml|份|个))[^（）()]*[）)]\s*$", re.I)
SUM_FIELDS = ("calories", "protein_g", "carbs_g", "fat_g", "saturated_fat_g")


def _not_found() -> LookupError:
    return LookupError(L("这天没有这些饮食记录（或不是你的）", "No such food entries on that day (or they aren't yours)"))


def grams_of(e: dict) -> float | None:
    """Portion in grams: unit "150 g" (x quantity), unit g / ml with the amount in quantity, or "（150g" in the name."""
    unit, qty = (e.get("unit") or "").strip(), e.get("quantity") or 0
    if m := _GRAMS_UNIT.match(unit):
        return round(float(m.group(1)) * (qty or 1), 1)
    if unit.lower() in ("g", "克", "ml", "毫升") and qty:
        return round(float(qty), 1)
    if m := _GRAMS_NAME.search(e.get("food") or ""):
        return float(m.group(1))
    return None


def clean_name(name: str | None) -> str:
    """"鸡胸肉（150g，红肉 0g）" -> "鸡胸肉"."""
    n = (name or "").strip()
    return (_TAIL.sub("", n) or n)[:60]


def _load(conn, user_id: int, day: date, entry_ids: list[str], client) -> tuple[list[dict], list[dict]]:
    """(raw SparkyFitness rows, entries with nutrition totals and extras) for the selected ids, in the order of the day."""
    ids = [str(i) for i in dict.fromkeys(entry_ids or [])]
    if not ids:
        raise ValueError(L("至少选一条", "Select at least one entry"))
    if len(ids) > MAX_ENTRIES:
        raise ValueError(L(f"一次最多 {MAX_ENTRIES} 条", f"At most {MAX_ENTRIES} entries at a time"))
    raw = [r for r in (client.get(f"/food-entries/by-date/{day.isoformat()}") or []) if str(r.get("id")) in ids]
    if len(raw) != len(ids):
        raise _not_found()
    ex = extras.load(conn, user_id, day, day)
    entries = extras.merge([sparky_client._food_entry(r) for r in raw], ex)
    order = {i: n for n, i in enumerate(ids)}
    pairs = sorted(zip(raw, entries), key=lambda p: ((p[1].get("time") or ""), order[str(p[0]["id"])]))
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _meal_of(entries: list[dict], meal: str | None) -> str:
    if meal:
        if meal not in sparky_write.MEALS:
            raise ValueError(L(f"meal 只能是 {', '.join(sparky_write.MEALS)}", f"meal must be one of {', '.join(sparky_write.MEALS)}"))
        return meal
    counts = Counter(e.get("meal_type") for e in entries if e.get("meal_type") in sparky_write.MEALS)
    return counts.most_common(1)[0][0] if counts else "snacks"


def totals(entries: list[dict]) -> dict:
    return {**{f: round(sum(e.get(f) or 0 for e in entries), 1) for f in SUM_FIELDS},
            **{f: extras.known_sum(entries, f) for f in extras.OPTIONAL},
            "red_meat_g": sum(float(m.group(1)) for e in entries if (m := RED_MEAT_MARK.search(e.get("food") or "")))}


# ---------------------------------------------------------------- template
def _template_items(entries: list[dict]) -> tuple[list[dict], bool]:
    items, unsure, seen = [], False, Counter()
    for e in entries:
        g = grams_of(e)
        if g is None or g <= 0 or g > 2000:
            g, unsure = 100.0, True   # portion not in grams: keep the logged nutrition, grams to confirm
        name = clean_name(e.get("food")) or L("食物", "Food")
        seen[name] += 1
        if seen[name] > 1:
            name = f"{name} {seen[name]}"
        red = RED_MEAT_MARK.search(e.get("food") or "")
        carbs = e.get("carbs_g") or 0
        sugar = None if e.get("sugar_g") is None else min(e["sugar_g"], carbs)          # app entries can disagree slightly
        added = None if e.get("added_sugar_g") is None else min(e["added_sugar_g"], sugar if sugar is not None else carbs)
        items.append({"food": name, "grams": g, "kcal": e.get("calories") or 0, "protein_g": e.get("protein_g") or 0,
                      "carbs_g": e.get("carbs_g") or 0, "fat_g": e.get("fat_g") or 0,
                      "sat_fat_g": min(e.get("saturated_fat_g") or 0, e.get("fat_g") or 0),
                      "red_meat_g": min(float(red.group(1)), g) if red else 0, "purine_level": None,
                      "sugar_g": sugar, "added_sugar_g": added, "fiber_g": e.get("fiber_g"), "sodium_mg": e.get("sodium_mg"),
                      "note": None})
    return items, unsure


def _template_name(entries: list[dict], name: str | None) -> str:
    n = (name or "").strip() or " + ".join(clean_name(e.get("food")) for e in entries)
    return n if len(n) <= 30 else n[:29] + "…"


# ---------------------------------------------------------------- merge
def _merged_food(entries: list[dict], day: date, meal: str, name: str | None) -> dict:
    t = totals(entries)
    names = [clean_name(e.get("food")) for e in entries]
    label = (name or "").strip() or " + ".join(names)
    label = label if len(label) <= 50 else label[:49] + "…"
    if t["red_meat_g"]:
        label += f"（红肉 {t['red_meat_g']:g}g）"   # data, not UI text: the rules count red meat by this label
    grams = [grams_of(e) for e in entries]
    amount = f"{sum(grams):g} g" if all(g is not None for g in grams) else L(f"{len(entries)} 样合计", f"{len(entries)} items")
    times = sorted(e["time"] for e in entries if e.get("time"))
    return {"date": day.isoformat(), "meal": meal, "time": times[0] if times else None, "food": label, "amount": amount,
            "calories": t["calories"], "protein_g": t["protein_g"], "carbs_g": t["carbs_g"], "fat_g": t["fat_g"],
            "saturated_fat_g": t["saturated_fat_g"], "alcohol_g": None,
            **{f: t[f] for f in extras.OPTIONAL},
            "notes": L("合并自：", "Merged from: ") + "、".join(e.get("food") or "" for e in entries)}


def combine(conn: psycopg.Connection, user_id: int, day: date, entry_ids: list[str], action: str,
            name: str | None = None, meal: str | None = None, channel: str = "web", client=None) -> dict:
    from ..rollout import flags
    flags.guard(conn, user_id, "meal_merge")   # rollout flag (docs/rollout.md): off -> "not available to you yet"
    if action not in ACTIONS:
        raise ValueError(L("action 只能是 template / merge / both", "action must be template, merge or both"))
    client = client or sparky_client.write_client_for_user(conn, user_id)
    raw, entries = _load(conn, user_id, day, entry_ids, client)
    if action in ("merge", "both") and len(entries) < 2:
        raise ValueError(L("合并至少要两条", "Merging needs at least two entries"))
    meal = _meal_of(entries, meal)
    out: dict = {"date": day.isoformat(), "meal": meal, "totals": totals(entries)}
    if action in ("template", "both"):
        items, unsure = _template_items(entries)
        tname = _template_name(entries, name)
        tpl = meals.create_template(conn, user_id, tname, meal, items, unsure,
                                    L("克数不是按克记的，按记下的营养存的；告诉管家实际克数后更新", "Portions weren't in grams; "
                                      "nutrition kept as logged — tell the AI the real grams to update") if unsure else None,
                                    strict_energy=False)
        writelog.record(conn, "template", "create", tpl["id"], None, {"id": tpl["id"], "name": tpl["name"]})
        out["template"] = {"id": tpl["id"], "name": tpl["name"], "items": len(tpl["items"]), "needs_confirmation": unsure}
    if action in ("merge", "both"):
        food = _merged_food(entries, day, meal, name)
        meal_ids = {m["name"]: m["id"] for m in client.get("/meal-types")}
        new = sparky_write._create_food(client, meal_ids, day, food)
        extras.save(conn, user_id, new["id"], day, {f: food.get(f) for f in extras.OPTIONAL}, None)
        for r in raw:
            client.request("DELETE", sparky_write._entry_path("food", r))
        from ..grocery import pantry_auto   # grocery imports assistant
        for r in raw:   # what the originals took from the pantry follows the merged entry (issue #104)
            pantry_auto.merge_into(conn, user_id, r["id"], new["id"])
        row = conn.execute(
            """INSERT INTO food_merges (user_id, date, entry_id, name, originals, totals, channel)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (user_id, day, str(new["id"]), food["food"], Jsonb(writelog._plain(raw)), Jsonb(out["totals"]), channel)).fetchone()
        writelog.record(conn, "merge", "merge", row["id"], {"entry_ids": [str(r["id"]) for r in raw]},
                        {"entry_id": str(new["id"]), "name": food["food"]})
        mirror.after_write(conn, user_id, client, food=[day])   # dual write (issue #252); off: nothing
        out["merge"] = {"id": row["id"], "entry_id": str(new["id"]), "name": food["food"], "replaced": len(raw),
                        "undo": L("拆开：今天页这条的「拆开」，或 rollback_write", "Split: 「拆开」 on the Today page, or rollback_write")}
    return out


def split(conn: psycopg.Connection, user_id: int, merge_id: int, client=None, log: bool = True) -> dict:
    """Undo a merge: remove the merged entry and put the original entries back (their sugar / fiber extras follow them)."""
    row = conn.execute("SELECT * FROM food_merges WHERE id = %s AND user_id = %s FOR UPDATE", (merge_id, user_id)).fetchone()
    if row is None:
        raise LookupError(L("没有这次合并（或不是你的）", "No such merge (or it isn't yours)"))
    if row["undone_at"] is not None:
        raise LookupError(L("这次合并已经拆开过了", "This merge was already split"))
    client = client or sparky_client.write_client_for_user(conn, user_id)
    cur = next((r for r in client.get(f"/food-entries/by-date/{row['date'].isoformat()}") or []
                if str(r.get("id")) == row["entry_id"]), None)
    if cur is not None:
        client.request("DELETE", sparky_write._entry_path("food", cur))
    restored = []
    for o in row["originals"]:
        new = sparky_write._recreate_food(client, o)
        extras.carry_over(conn, user_id, str(o["id"]), str(new["id"]), row["date"])
        restored.append(str(new["id"]))
    from ..grocery import pantry_auto   # grocery imports assistant
    pantry_auto.split_back(conn, user_id, row["entry_id"], {str(o["id"]): rid for o, rid in zip(row["originals"], restored)})
    conn.execute("UPDATE food_merges SET undone_at = now(), restored_ids = %s WHERE id = %s", (restored, merge_id))
    if log:
        writelog.record(conn, "merge", "split", merge_id, {"entry_id": row["entry_id"]}, {"entry_ids": restored})
    mirror.after_write(conn, user_id, client, food=[row["date"]])
    return {"split": merge_id, "date": row["date"].isoformat(), "restored": len(restored), "merged_entry_gone": cur is None}
