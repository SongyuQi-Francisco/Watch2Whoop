"""Sugar, fiber and sodium per food entry (issue #31).

- SparkyFitness food entries have sugars / dietary_fiber / sodium; log_food writes them there. Added sugar and the
  sugary-drink flag have no SparkyFitness field, so they go to food_entry_extras (keyed by the entry id) — log_food stores
  all of its optional values there too, so they survive even if SparkyFitness drops one.
- Old entries: parse_notes finds sugar grams written in entry notes ("添加糖 12g", "含糖 30g"); backfill_from_notes is
  idempotent and dry-run by default (source notes_parse never overwrites what log_food wrote).
- Unknown is never 0: a value no source has stays None, and day totals carry how many entries were known.
"""
import re
from datetime import date

import psycopg

from ..rules import params as P

OPTIONAL = ("sugar_g", "added_sugar_g", "fiber_g", "sodium_mg")
MAX_SODIUM_MG = 20000


def check_optional(label: str, it: dict, carbs_g: float | None = None, clamp_notes: list | None = None) -> dict:
    """Validate the optional fields of one food / ingredient (None = unknown). Returns {field: value}.
    Errors name the field and by how much it is over. clamp_notes (a list) = the AI write tools: added sugar above total
    sugar is cut to the total sugar and a note is appended to the list, instead of failing the whole write."""
    out = {}
    for f in OPTIONAL:
        v = it.get(f)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
            raise ValueError(f"{label}：{f} 要是 ≥ 0 的数（不知道就 null）")
        out[f] = None if v is None else float(v)
    a, s = out["added_sugar_g"], out["sugar_g"]
    if a is not None and s is not None and a > s + 0.5:
        if clamp_notes is None:
            raise ValueError(f"{label}：添加糖不能多于总糖（added_sugar_g {a:g} g，sugar_g {s:g} g，超出 {a - s:g} g）")
        clamp_notes.append(f"{label}：添加糖 {a:g} g 比总糖 {s:g} g 多 {a - s:g} g，已按总糖截为 {s:g} g（总糖不准就改总糖）")
        out["added_sugar_g"] = s
    for f in ("sugar_g", "added_sugar_g"):
        if out[f] is not None and carbs_g is not None and out[f] > carbs_g + 0.5:
            raise ValueError(f"{label}：{f} 不能多于碳水（{f} {out[f]:g} g，碳水 {carbs_g:g} g，超出 {out[f] - carbs_g:g} g）")
    if out["sodium_mg"] is not None and out["sodium_mg"] > MAX_SODIUM_MG:
        raise ValueError(f"{label}：钠 {out['sodium_mg']:g} mg 太多，是不是把 g 当成 mg 了")
    return out


def known_sum(items: list[dict], field: str, scale: float = 1) -> float | None:
    vals = [i[field] for i in items if i.get(field) is not None]
    return round(sum(vals) * scale, 1) if vals else None


def save(conn: psycopg.Connection, user_id: int, entry_id: str, day: date, values: dict,
         sugary_drink: bool | None, source: str = "log_food") -> bool:
    """Upsert one entry's extras; nothing is written when every value is unknown. notes_parse never overwrites log_food."""
    vals = {f: values.get(f) for f in OPTIONAL}
    if all(v is None for v in vals.values()) and sugary_drink is None:
        return False
    row = conn.execute(
        """INSERT INTO food_entry_extras (user_id, entry_id, date, sugar_g, added_sugar_g, fiber_g, sodium_mg,
                                          sugary_drink, source)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (user_id, entry_id) DO UPDATE SET date = EXCLUDED.date, sugar_g = EXCLUDED.sugar_g,
             added_sugar_g = EXCLUDED.added_sugar_g, fiber_g = EXCLUDED.fiber_g, sodium_mg = EXCLUDED.sodium_mg,
             sugary_drink = EXCLUDED.sugary_drink, source = EXCLUDED.source, updated_at = now()
           WHERE food_entry_extras.source = 'notes_parse' OR EXCLUDED.source = 'log_food'
           RETURNING entry_id""",
        (user_id, str(entry_id), day, *vals.values(), sugary_drink, source)).fetchone()
    return row is not None


def set_values(conn: psycopg.Connection, user_id: int, entry_id: str, day: date, values: dict) -> None:
    """Write the given (not None) optional values of one entry, keeping what the row already has for the others; inserts the
    row when the entry had none. update_food_entry uses it for corrected / scaled values."""
    vals = {f: values.get(f) for f in OPTIONAL}
    if all(v is None for v in vals.values()):
        return
    conn.execute(
        """INSERT INTO food_entry_extras (user_id, entry_id, date, sugar_g, added_sugar_g, fiber_g, sodium_mg, source)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'log_food')
           ON CONFLICT (user_id, entry_id) DO UPDATE SET date = EXCLUDED.date,
             sugar_g = COALESCE(EXCLUDED.sugar_g, food_entry_extras.sugar_g),
             added_sugar_g = COALESCE(EXCLUDED.added_sugar_g, food_entry_extras.added_sugar_g),
             fiber_g = COALESCE(EXCLUDED.fiber_g, food_entry_extras.fiber_g),
             sodium_mg = COALESCE(EXCLUDED.sodium_mg, food_entry_extras.sodium_mg),
             source = 'log_food', updated_at = now()""",
        (user_id, str(entry_id), day, *vals.values()))


def carry_over(conn: psycopg.Connection, user_id: int, old_id: str, new_id: str, new_date: date,
               factor: float = 1) -> None:
    """An entry edited by update_food_entry (assistant/sparky_write.py): its extras follow the new id / date and are
    scaled by the portion factor. No-op when the entry had none."""
    conn.execute(
        """UPDATE food_entry_extras SET entry_id = %s, date = %s, sugar_g = round((sugar_g * %s)::numeric, 1),
             added_sugar_g = round((added_sugar_g * %s)::numeric, 1), fiber_g = round((fiber_g * %s)::numeric, 1),
             sodium_mg = round((sodium_mg * %s)::numeric, 1), updated_at = now()
           WHERE user_id = %s AND entry_id = %s""",
        (str(new_id), new_date, factor, factor, factor, factor, user_id, str(old_id)))


def load(conn: psycopg.Connection, user_id: int, start: date, end: date) -> dict[str, dict]:
    return {r["entry_id"]: r for r in conn.execute(
        """SELECT entry_id, sugar_g, added_sugar_g, fiber_g, sodium_mg, sugary_drink, source FROM food_entry_extras
           WHERE user_id = %s AND date BETWEEN %s AND %s""", (user_id, start, end))}


# ---------------------------------------------------------------- sugary drinks
def is_sugary_drink(entry: dict) -> bool:
    """entry: merged entry (sugary_drink_flag from log_food, food name, sugar_g)."""
    if entry.get("sugary_drink_flag") is not None:
        return bool(entry["sugary_drink_flag"])
    name = (entry.get("food") or "").lower()
    if not any(k in name for k in P.SUGARY_DRINK_KEYWORDS) or any(x in name for x in P.SUGARY_DRINK_EXCLUDE):
        return False
    return entry.get("sugar_g") is None or entry["sugar_g"] >= P.SUGARY_DRINK_MIN_SUGAR_G


def merge(entries: list[dict], extras: dict[str, dict]) -> list[dict]:
    """Entries from sparky_client.food_entries + their extras: SparkyFitness values win, extras fill the gaps;
    added_sugar_g only comes from extras. Adds sugary_drink (bool)."""
    out = []
    for e in entries:
        x = extras.get(str(e.get("id"))) or {}
        m = dict(e)
        for f in OPTIONAL:
            if m.get(f) is None and x.get(f) is not None:
                m[f] = round(float(x[f]), 1)
            m.setdefault(f, None)
        m["sugary_drink_flag"] = x.get("sugary_drink")
        m["sugary_drink"] = is_sugary_drink(m)
        del m["sugary_drink_flag"]
        out.append(m)
    return out


def day_totals(entries: list[dict]) -> dict:
    """Known sums of the optional fields (None when no entry knows it) and how many entries knew each."""
    return {**{f: known_sum(entries, f) for f in OPTIONAL},
            "known": {"of": len(entries), **{f: sum(1 for e in entries if e.get(f) is not None) for f in OPTIONAL}},
            "sugary_drinks": sum(1 for e in entries if e.get("sugary_drink"))}


# ---------------------------------------------------------------- old notes
_NUM = r"(\d+(?:\.\d+)?)\s*(?:g|克)"
NOTE_PATTERNS = {   # earlier patterns win; 添加糖 / 加糖 / added sugar before the plain sugar ones
    "added_sugar_g": [re.compile(r"(?:添加糖|加糖|added\s*sugars?)\s*[:：约≈]?\s*" + _NUM, re.I)],
    "sugar_g": [re.compile(r"(?:总糖|含糖|糖分|糖|sugars?)\s*[:：约≈]?\s*" + _NUM, re.I)],
    "fiber_g": [re.compile(r"(?:膳食纤维|纤维|fiber|fibre)\s*[:：约≈]?\s*" + _NUM, re.I)],
}


def parse_notes(text: str | None) -> dict:
    """Sugar / fiber grams written in a note. 「添加糖 12g」 is not also read as total sugar."""
    s = text or ""
    out = {}
    for f, pats in NOTE_PATTERNS.items():
        for p in pats:
            if f == "sugar_g":
                s2 = NOTE_PATTERNS["added_sugar_g"][0].sub(" ", s)
                m = p.search(s2)
            else:
                m = p.search(s)
            if m:
                out[f] = float(m.group(1))
                break
    return out


def backfill_from_notes(conn: psycopg.Connection, user_id: int, start: date, end: date, apply: bool = False,
                        client=None) -> dict:
    """Parse sugar / fiber grams out of the notes of entries in [start, end] into food_entry_extras (source
    notes_parse). Only fills what neither SparkyFitness nor log_food knows. Idempotent; apply=False = dry run."""
    from .. import sparky_client
    client = client or sparky_client.client_for_user(conn, user_id)
    rows = sparky_client.food_entries(client, start, end)
    have = load(conn, user_id, start, end)
    found = []
    for e in rows:
        parsed = parse_notes(e.get("notes"))
        x = have.get(str(e.get("id"))) or {}
        if x.get("source") == "log_food":
            continue
        new = {f: v for f, v in parsed.items() if e.get(f) is None}
        if not new:
            continue
        if any(x.get(f) != v for f, v in new.items()):
            found.append({"id": e.get("id"), "date": e["date"], "food": e.get("food"), "notes": e.get("notes"), **new})
            if apply:
                save(conn, user_id, e["id"], date.fromisoformat(e["date"]), {**{f: x.get(f) for f in OPTIONAL}, **new},
                     x.get("sugary_drink"), source="notes_parse")
    return {"apply": apply, "entries_scanned": len(rows), "updates": found}
