"""Own table rows -> the record dicts the rest of Body Engine already works with (issue #252 phase 3).

The write code (assistant/sparky_write.py, food_combine.py), the AI write log and the readers in sparky_client
(`_food_entry`, `_session`, `weight`) all take one record shape: the keys the SparkyFitness API returns (entry_date, meal_type,
food_name, sets with weight / duration …). Rebuilding that shape from the own tables lets every one of them work unchanged on
either store, and keeps ids, undo and rollback of older log rows valid. Only the key names are shared with that API; the
tables and this code are Body Engine's own (docs/own-food-weight-storage.md §12).

Food entries store this entry's total nutrition; the record says quantity = serving_size, so per serving × quantity /
serving size gives the stored total back exactly.
"""
from datetime import date
from decimal import Decimal

import psycopg

from . import mapping as M

NUTRIENT_KEYS = {col: k for k, col in M.NUTRIENTS.items()}          # column -> record key
MEASUREMENT_KEYS = {col: k for k, col in M.MEASUREMENTS.items()}


def num(v):
    """Decimal / float from the database -> int when whole, else float (the API gives 10, not 10.0)."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _hhmm(t) -> str | None:
    return t.strftime("%H:%M") if t is not None else None


# ---------------------------------------------------------------- food
def food_record(r: dict) -> dict:
    q = num(r["quantity"])
    rec = {"id": r["id"], "entry_date": r["date"].isoformat(), "entry_time": _hhmm(r["time"]), "meal_type": r["meal"],
           "food_id": r["food_id"], "variant_id": None, "food_name": r["food_name"],
           "quantity": 1 if q is None else q, "serving_size": q or 1, "unit": r["unit"],
           **{k: num(r[col]) for col, k in NUTRIENT_KEYS.items()},
           **{k: num(v) for k, v in (r.get("nutrients") or {}).items()},
           "notes": r["notes"], "source": r["source"], "source_id": r["source_id"]}
    return rec


def food_day(conn: psycopg.Connection, user_id: int, start: date, end: date | None = None) -> list[dict]:
    rows = conn.execute("SELECT * FROM food_entries WHERE user_id = %s AND date BETWEEN %s AND %s "
                        "ORDER BY date, time NULLS LAST, created_at, id", (user_id, start, end or start)).fetchall()
    return [food_record(r) for r in rows]


# ---------------------------------------------------------------- workouts
def _set_record(s: dict) -> dict:
    return {"id": s["id"], "set_number": s["set_number"], "set_type": s["set_type"], "reps": num(s["reps"]),
            "weight": num(s["weight_kg"]), "rir": num(s["rir"]), "rpe": num(s["rpe"]), "duration": num(s["duration_s"]),
            "distance": num(s["distance_km"]), "rest_time": num(s["rest_s"]), "is_pr": s["is_pr"], "notes": s["notes"]}


def _exercise_record(e: dict, sets: list[dict]) -> dict:
    muscles = list(e["primary_muscles"] or []) or None
    return {"id": e["id"], "exercise_id": e["exercise_id"], "sort_order": e["sort_order"],
            "exercise_snapshot": {"name": e["exercise_name"], "category": e["category"], "primary_muscles": muscles},
            "name": e["exercise_name"], "category": e["category"], "notes": e["notes"],
            "superset_group": e["superset_group"], "duration_minutes": num(e["duration_min"]),
            "calories_burned": num(e["calories_burned"]), "avg_heart_rate": num(e["avg_heart_rate"]),
            "sets": [_set_record(s) for s in sets]}


def session_records(conn: psycopg.Connection, user_id: int, start: date, end: date | None = None,
                    ids: list[str] | None = None) -> list[dict]:
    """Logged workouts of [start, end] (or the given ids) as session records: a preset session with its exercises, or a
    single exercise entry (kind single) as the entry itself."""
    if ids is not None:
        ws = conn.execute("SELECT * FROM logged_workouts WHERE user_id = %s AND id = ANY(%s) ORDER BY date, created_at, id",
                          (user_id, ids)).fetchall()
    else:
        ws = conn.execute("SELECT * FROM logged_workouts WHERE user_id = %s AND date BETWEEN %s AND %s "
                          "ORDER BY date, created_at, id", (user_id, start, end or start)).fetchall()
    if not ws:
        return []
    w_ids = [w["id"] for w in ws]
    exs = conn.execute("SELECT * FROM logged_workout_exercises WHERE user_id = %s AND workout_id = ANY(%s) "
                       "ORDER BY sort_order, id", (user_id, w_ids)).fetchall()
    sets: dict[str, list] = {}
    for s in conn.execute("SELECT * FROM logged_workout_sets WHERE user_id = %s AND exercise_entry_id = ANY(%s) "
                          "ORDER BY set_number, id", (user_id, [e["id"] for e in exs])).fetchall():
        sets.setdefault(s["exercise_entry_id"], []).append(s)
    by_w: dict[str, list] = {}
    for e in exs:
        by_w.setdefault(e["workout_id"], []).append(_exercise_record(e, sets.get(e["id"], [])))
    out = []
    for w in ws:
        base = {"id": w["id"], "entry_date": w["date"].isoformat(), "source": w["source"]}
        records = by_w.get(w["id"], [])
        if w["kind"] == "single" and records:
            out.append({**records[0], **base, "type": "individual", "name": w["name"] or records[0]["name"],
                        "duration_minutes": num(w["total_duration_min"]) if w["total_duration_min"] is not None
                        else records[0]["duration_minutes"]})
        else:
            out.append({**base, "type": "preset", "name": w["name"], "notes": w["notes"],
                        "total_duration_minutes": num(w["total_duration_min"]), "exercises": records})
    return out


# ---------------------------------------------------------------- weight / body measurements
def checkin_record(r: dict) -> dict:
    return {"id": r["id"], "entry_date": r["date"].isoformat(),
            **{k: num(r[col]) for col, k in MEASUREMENT_KEYS.items() if r[col] is not None}}


def checkins(conn: psycopg.Connection, user_id: int, start: date, end: date) -> list[dict]:
    rows = conn.execute("SELECT * FROM body_measurements WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date",
                        (user_id, start, end)).fetchall()
    return [checkin_record(r) for r in rows]


# ---------------------------------------------------------------- exercise library
def exercises(conn: psycopg.Connection, user_id: int) -> list[dict]:
    """The user's exercise library in the shape of sparky_client.exercises()."""
    rows = conn.execute("SELECT * FROM user_exercises WHERE user_id = %s ORDER BY name, id", (user_id,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "category": r["category"], "modality": r["modality"], "source_id": None,
             "primary_muscles": list(r["primary_muscles"] or []), "description": r["description"]} for r in rows]
