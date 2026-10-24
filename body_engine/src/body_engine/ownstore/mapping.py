"""SparkyFitness rows (as its API returns them) -> rows of Body Engine's own tables (migration 730). Pure functions, no I/O.

The dual write (mirror.py) and `body-engine sparky-migrate` (migrate.py) both use these, so the two paths always store the
same thing. Only fields sparky_client / assistant/sparky_write already rely on are read; nutrients without a column here
go into `nutrients` (unknown keys are kept, not dropped). Food entry nutrition is stored as the entry's total, computed
exactly like sparky_client._food_entry (per serving × quantity / serving size, unrounded).
The SparkyFitness field names below are only the keys of its API responses being read; the tables use our own names
(independent implementation, see the package docstring).
"""
from datetime import date

# SparkyFitness nutrient field -> column (per serving in foods, per entry in food_entries)
NUTRIENTS = {"calories": "calories", "protein": "protein_g", "carbs": "carbs_g", "fat": "fat_g",
             "saturated_fat": "saturated_fat_g", "sugars": "sugar_g", "dietary_fiber": "fiber_g", "sodium": "sodium_mg",
             "alcohol_g": "alcohol_g"}
# Other numeric nutrients of a SparkyFitness food variant / entry, kept in `nutrients` (scaled like the others)
OTHER_NUTRIENTS = ("cholesterol", "potassium", "trans_fat", "polyunsaturated_fat", "monounsaturated_fat",
                   "vitamin_a", "vitamin_c", "calcium", "iron")
EXTRA_FIELDS = ("sugar_g", "added_sugar_g", "fiber_g", "sodium_mg")   # food_entry_extras columns (nutrition/extras.py)

FOOD_COLS = ("name", "brand", "serving_size", "serving_unit", *NUTRIENTS.values(), "nutrients", "provider_type",
             "provider_external_id", "is_custom", "notes")
ENTRY_COLS = ("date", "time", "meal", "food_id", "food_name", "quantity", "unit", "serving_size", *NUTRIENTS.values(),
              "added_sugar_g", "sugary_drink", "nutrients", "notes", "source", "source_id")
# SparkyFitness check-in field -> column (assistant/sparky_write.CHECKIN_COLUMNS minus steps: steps come from the watch)
MEASUREMENTS = {"weight": "weight_kg", "body_fat_percentage": "body_fat_pct", "waist": "waist_cm", "neck": "neck_cm",
                "hips": "hips_cm", "height": "height_cm", "muscle_mass_kg": "muscle_mass_kg", "bone_mass_kg": "bone_mass_kg",
                "body_water_percentage": "body_water_pct", "bmr": "bmr_kcal"}
MEASUREMENT_COLS = ("id", *MEASUREMENTS.values())
EXERCISE_COLS = ("name", "category", "modality", "primary_muscles", "description")
WORKOUT_COLS = ("date", "name", "notes", "source", "kind", "total_duration_min")
WORKOUT_EXERCISE_COLS = ("workout_id", "sort_order", "exercise_id", "exercise_name", "category", "primary_muscles", "notes",
                         "superset_group", "duration_min", "calories_burned", "avg_heart_rate")
SET_COLS = ("exercise_entry_id", "set_number", "set_type", "reps", "weight_kg", "rir", "rpe", "duration_s", "distance_km",
            "rest_s", "is_pr", "notes")


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _day(v, default: date | None = None) -> date:
    if v:
        return date.fromisoformat(str(v)[:10])
    if default is None:
        raise ValueError("SparkyFitness row without a date")
    return default


def json_list(v) -> list[str]:
    from ..sparky_client import _json_list
    return [str(x) for x in _json_list(v)]


# ---------------------------------------------------------------- food
def food_entry(raw: dict, extras: dict | None = None, day: date | None = None) -> dict:
    """One SparkyFitness food entry -> a food_entries row. extras: its food_entry_extras row, if any (added sugar and the
    sugary-drink flag; sugar / fiber / sodium only where SparkyFitness has no value, like extras.merge)."""
    qty, serving = raw.get("quantity") or 0, raw.get("serving_size") or 1
    scale = qty / serving if serving else 0
    total = lambda k: None if _num(raw.get(k)) is None else _num(raw[k]) * scale
    row = {"id": str(raw["id"]), "date": _day(raw.get("entry_date"), day),
           "time": (raw.get("entry_time") or "")[:5] or None, "meal": raw.get("meal_type") or "snacks",
           "food_id": None if raw.get("food_id") is None else str(raw["food_id"]),
           "food_name": raw.get("food_name") or "", "quantity": _num(raw.get("quantity")), "unit": raw.get("unit"),
           "serving_size": _num(raw.get("serving_size")),
           **{col: total(k) for k, col in NUTRIENTS.items()},
           "added_sugar_g": None, "sugary_drink": None,
           "nutrients": {k: total(k) for k in OTHER_NUTRIENTS if total(k) is not None},
           "notes": raw.get("notes"), "source": raw.get("source"),
           "source_id": None if raw.get("source_id") is None else str(raw["source_id"])}
    ex = extras or {}
    for f in EXTRA_FIELDS:
        if row.get(f) is None and _num(ex.get(f)) is not None:
            row[f] = _num(ex[f])
    if ex.get("sugary_drink") is not None:
        row["sugary_drink"] = bool(ex["sugary_drink"])
    return row


def food_from_entry(raw: dict) -> dict | None:
    """A minimal food library row for a food an entry hangs on but the library listing did not return (a public food,
    or a dual write before the migration); the migration overwrites it with the full food when it sees one."""
    if raw.get("food_id") is None:
        return None
    per = lambda k: _num(raw.get(k))
    return {"id": str(raw["food_id"]), "name": raw.get("food_name") or "", "brand": raw.get("brand_name"),
            "serving_size": _num(raw.get("serving_size")), "serving_unit": raw.get("serving_unit") or raw.get("unit"),
            **{col: per(k) for k, col in NUTRIENTS.items()},
            "nutrients": {k: per(k) for k in OTHER_NUTRIENTS if per(k) is not None},
            "provider_type": None, "provider_external_id": None, "is_custom": True, "notes": None}


def food(raw: dict) -> dict:
    """One food of the SparkyFitness food library (/foods/foods-paginated) -> a foods row (its default serving)."""
    v = raw.get("default_variant") or {}
    per = lambda k: _num(v.get(k) if v.get(k) is not None else raw.get(k))
    return {"id": str(raw["id"]), "name": raw.get("name") or "", "brand": raw.get("brand"),
            "serving_size": _num(v.get("serving_size") if v else raw.get("serving_size")),
            "serving_unit": v.get("serving_unit") if v else raw.get("serving_unit"),
            **{col: per(k) for k, col in NUTRIENTS.items()},
            "nutrients": {k: per(k) for k in OTHER_NUTRIENTS if per(k) is not None},
            "provider_type": raw.get("provider_type"), "provider_external_id": raw.get("provider_external_id"),
            "is_custom": bool(raw.get("is_custom", True)), "notes": raw.get("notes")}


# ---------------------------------------------------------------- weight / body measurements
def measurement(raw: dict, day: date | None = None) -> dict | None:
    """One SparkyFitness check-in -> a body_measurements row; None when it holds none of our values (e.g. steps only)."""
    vals = {col: _num(raw.get(k)) for k, col in MEASUREMENTS.items()}
    if all(v is None for v in vals.values()):
        return None
    d = _day(raw.get("entry_date"), day)
    return {"date": d, "id": str(raw["id"]) if raw.get("id") is not None else f"sparky-checkin-{d.isoformat()}", **vals}


# ---------------------------------------------------------------- exercise library
def exercise(e: dict) -> dict:
    """One exercise of sparky_client.exercises() -> a user_exercises row."""
    return {"id": str(e["id"]), "name": e.get("name") or "", "category": e.get("category"), "modality": e.get("modality"),
            "primary_muscles": json_list(e.get("primary_muscles")), "description": e.get("description")}


def exercise_from_snapshot(e: dict) -> dict | None:
    """A library row from the exercise snapshot of a logged exercise (public exercises are not in "my exercises")."""
    if e.get("exercise_id") is None:
        return None
    snap = e.get("exercise_snapshot") or {}
    return {"id": str(e["exercise_id"]), "name": snap.get("name") or e.get("name") or "",
            "category": snap.get("category") or e.get("category"), "modality": snap.get("modality"),
            "primary_muscles": json_list(snap.get("primary_muscles")), "description": None}


# ---------------------------------------------------------------- workouts
def _exercise_entries(s: dict) -> list[dict]:
    """Exercise entries of one SparkyFitness session: a preset session's exercises, or the standalone entry itself
    (assistant/sparky_write._session_entries)."""
    return s.get("exercises") or [] if s.get("type", "preset") == "preset" else [s]


def workout(raw: dict, day: date | None = None) -> tuple[dict, list[dict], list[dict]]:
    """One SparkyFitness session -> (logged_workouts row, logged_workout_exercises rows, logged_workout_sets rows)."""
    preset = raw.get("type", "preset") == "preset"
    entries = _exercise_entries(raw)
    first = (entries[0].get("exercise_snapshot") or {}).get("name") or entries[0].get("name") if entries else None
    w = {"id": str(raw["id"]), "date": _day(raw.get("entry_date"), day),
         "name": raw.get("name") or (None if preset else first), "notes": raw.get("notes") if preset else None,
         "source": raw.get("source"), "kind": "preset" if preset else "single",
         "total_duration_min": _num(raw.get("total_duration_minutes") if preset else raw.get("duration_minutes"))}
    exercises, sets = [], []
    for i, e in enumerate(entries):
        snap = e.get("exercise_snapshot") or {}
        eid = str(e["id"]) if e.get("id") is not None else f"{w['id']}:{i}"
        exercises.append({
            "id": eid, "workout_id": w["id"], "sort_order": e.get("sort_order") if e.get("sort_order") is not None else i,
            "exercise_id": None if e.get("exercise_id") is None else str(e["exercise_id"]),
            "exercise_name": snap.get("name") or e.get("name"), "category": snap.get("category") or e.get("category"),
            "primary_muscles": json_list(snap.get("primary_muscles")), "notes": e.get("notes"),
            "superset_group": None if e.get("superset_group") is None else str(e["superset_group"]),
            "duration_min": _num(e.get("duration_minutes")), "calories_burned": _num(e.get("calories_burned")),
            "avg_heart_rate": _num(e.get("avg_heart_rate"))})
        for n, s in enumerate(sorted(e.get("sets") or [], key=lambda s: s.get("set_number") or 0)):
            sets.append({
                "id": str(s["id"]) if s.get("id") is not None else f"{eid}:{n + 1}", "exercise_entry_id": eid,
                "set_number": s.get("set_number") or n + 1, "set_type": s.get("set_type"), "reps": _num(s.get("reps")),
                "weight_kg": _num(s.get("weight")), "rir": _num(s.get("rir")), "rpe": _num(s.get("rpe")),
                "duration_s": _num(s.get("duration")), "distance_km": _num(s.get("distance")),
                "rest_s": _num(s.get("rest_time")), "is_pr": s.get("is_pr"), "notes": s.get("notes")})
    return w, exercises, sets
