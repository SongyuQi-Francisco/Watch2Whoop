"""Write mapped SparkyFitness rows into the own tables (migration 730): upsert by id, delete what SparkyFitness no longer has.

Every apply_* call covers one user and one date range and makes the tables equal to the given SparkyFitness rows there:
rows are upserted by id (unchanged rows are not touched), rows with origin 'sparky' in the range that are not in the input
are deleted (deleted in SparkyFitness meanwhile). Rows with origin 'own' are never touched. Ids of entries / workouts /
sets are unique across users; an id that already belongs to another user raises instead of being overwritten.
Returns {table: {"inserted": n, "updated": n}}; callers count before / after themselves.
"""
from datetime import date

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from . import mapping as M

# table -> (key columns, data columns, has origin / synced_at)
TABLES = {
    "foods": (("user_id", "id"), M.FOOD_COLS, True),
    "food_entries": (("id",), M.ENTRY_COLS, True),
    "body_measurements": (("user_id", "date"), M.MEASUREMENT_COLS, True),
    "user_exercises": (("user_id", "id"), M.EXERCISE_COLS, True),
    "logged_workouts": (("id",), M.WORKOUT_COLS, True),
    "logged_workout_exercises": (("id",), M.WORKOUT_EXERCISE_COLS, False),
    "logged_workout_sets": (("id",), M.SET_COLS, False),
}
JSON_COLS = {"nutrients"}


def _value(col: str, v):
    return Jsonb(v) if col in JSON_COLS and v is not None else v


def _check_owner(conn: psycopg.Connection, table: str, user_id: int, ids: list[str]) -> None:
    if not ids:
        return
    other = conn.execute(sql.SQL("SELECT id FROM {} WHERE id = ANY(%s) AND user_id <> %s LIMIT 3").format(
        sql.Identifier(table)), (ids, user_id)).fetchall()
    if other:
        raise ValueError(f"{table}: ids already belong to another user: {[r['id'] for r in other]}")


def upsert(conn: psycopg.Connection, table: str, user_id: int, rows: list[dict], *, if_missing: bool = False) -> dict:
    """Upsert rows (mapping dicts without user_id) as origin 'sparky'. if_missing: only insert rows that do not exist
    (placeholders such as a food known only from an entry must not overwrite the full library row)."""
    keys, cols, synced = TABLES[table]
    if not rows:
        return {"inserted": 0, "updated": 0}
    if keys == ("id",):
        _check_owner(conn, table, user_id, [r["id"] for r in rows])
    if table == "body_measurements":   # the check-in id is unique too: drop a stale row holding it on another date
        conn.execute("DELETE FROM body_measurements WHERE user_id = %s AND origin = 'sparky' AND id = ANY(%s) "
                     "AND NOT (date = ANY(%s))", (user_id, [r["id"] for r in rows], [r["date"] for r in rows]))
    data = [c for c in cols if c not in keys]
    ins_cols = ["user_id", *[k for k in keys if k != "user_id"], *data]
    t = sql.Identifier(table)
    values = sql.SQL(", ").join(sql.Placeholder() * len(ins_cols))
    if synced:   # origin / synced_at: set by the sync, not by the mapping
        ins_cols += ["origin", "synced_at"]
        values = sql.SQL("{}, 'sparky', now()").format(values)
    if if_missing:
        action = sql.SQL("DO NOTHING")
    else:
        sets = [sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in data]
        if synced:
            sets += [sql.SQL("synced_at = now()"), sql.SQL("updated_at = now()")]
        old = sql.SQL(", ").join(sql.SQL("{}.{}").format(t, sql.Identifier(c)) for c in data)
        new = sql.SQL(", ").join(sql.SQL("EXCLUDED.{}").format(sql.Identifier(c)) for c in data)
        own_rows = sql.SQL(" AND {}.origin = 'sparky'").format(t) if synced else sql.SQL("")
        action = sql.SQL("DO UPDATE SET {sets} WHERE {t}.user_id = EXCLUDED.user_id{own} "
                         "AND ROW({old}) IS DISTINCT FROM ROW({new})").format(
            sets=sql.SQL(", ").join(sets), t=t, own=own_rows, old=old, new=new)
    q = sql.SQL("INSERT INTO {t} ({cols}) VALUES ({values}) ON CONFLICT ({target}) {action} "
                "RETURNING (xmax = 0) AS inserted").format(
        t=t, cols=sql.SQL(", ").join(map(sql.Identifier, ins_cols)), values=values,
        target=sql.SQL(", ").join(map(sql.Identifier, keys)), action=action)
    out = {"inserted": 0, "updated": 0}
    for r in rows:
        hit = conn.execute(q, [user_id, *[r[k] for k in keys if k != "user_id"],
                               *[_value(c, r.get(c)) for c in data]]).fetchone()
        if hit is not None:   # None = unchanged (or an 'own' row, which a sync never overwrites)
            out["inserted" if hit["inserted"] else "updated"] += 1
    return out


# ---------------------------------------------------------------- per kind
def apply_food(conn: psycopg.Connection, user_id: int, start: date, end: date, raw_entries: list[dict]) -> dict:
    """Food entries of [start, end] as SparkyFitness has them now; foods they hang on are added when missing."""
    ids = [str(r["id"]) for r in raw_entries]
    extras = {r["entry_id"]: r for r in conn.execute(
        "SELECT * FROM food_entry_extras WHERE user_id = %s AND entry_id = ANY(%s)", (user_id, ids)).fetchall()}
    day = start if start == end else None
    rows = [M.food_entry(r, extras.get(str(r["id"])), day) for r in raw_entries]
    placeholders = {f["id"]: f for r in raw_entries if (f := M.food_from_entry(r))}
    out = {"foods": upsert(conn, "foods", user_id, list(placeholders.values()), if_missing=True)}
    conn.execute("DELETE FROM food_entries WHERE user_id = %s AND origin = 'sparky' AND date BETWEEN %s AND %s "
                 "AND NOT (id = ANY(%s))", (user_id, start, end, ids))
    out["food_entries"] = upsert(conn, "food_entries", user_id, rows)
    return out


def apply_foods(conn: psycopg.Connection, user_id: int, raw_foods: list[dict]) -> dict:
    """The user's whole food library (upsert only: an entry may still hang on a food the listing no longer shows)."""
    return {"foods": upsert(conn, "foods", user_id, [M.food(f) for f in raw_foods])}


def apply_measurements(conn: psycopg.Connection, user_id: int, start: date, end: date, raw_rows: list[dict]) -> dict:
    day = start if start == end else None
    rows = [m for r in raw_rows if (m := M.measurement(r, day)) and start <= m["date"] <= end]
    conn.execute("DELETE FROM body_measurements WHERE user_id = %s AND origin = 'sparky' AND date BETWEEN %s AND %s "
                 "AND NOT (date = ANY(%s))", (user_id, start, end, [r["date"] for r in rows]))
    return {"body_measurements": upsert(conn, "body_measurements", user_id, rows)}


def apply_exercises(conn: psycopg.Connection, user_id: int, library: list[dict]) -> dict:
    """The user's exercise library (sparky_client.exercises; upsert only, history keeps referring to removed ones)."""
    return {"user_exercises": upsert(conn, "user_exercises", user_id, [M.exercise(e) for e in library])}


def apply_workouts(conn: psycopg.Connection, user_id: int, start: date, end: date, raw_sessions: list[dict]) -> dict:
    day = start if start == end else None
    ws, exs, sets, lib = [], [], [], {}
    for s in raw_sessions:
        w, e, st = M.workout(s, day)
        if not start <= w["date"] <= end:
            continue
        ws.append(w)
        exs += e
        sets += st
        for x in M._exercise_entries(s):
            if (row := M.exercise_from_snapshot(x)) and row["id"] not in lib:
                lib[row["id"]] = row
    out = {"user_exercises": upsert(conn, "user_exercises", user_id, list(lib.values()), if_missing=True)}
    conn.execute("DELETE FROM logged_workouts WHERE user_id = %s AND origin = 'sparky' AND date BETWEEN %s AND %s "
                 "AND NOT (id = ANY(%s))", (user_id, start, end, [w["id"] for w in ws]))
    out["logged_workouts"] = upsert(conn, "logged_workouts", user_id, ws)
    w_ids = [w["id"] for w in ws]
    conn.execute("DELETE FROM logged_workout_exercises WHERE user_id = %s AND workout_id = ANY(%s) AND NOT (id = ANY(%s))",
                 (user_id, w_ids, [e["id"] for e in exs]))
    out["logged_workout_exercises"] = upsert(conn, "logged_workout_exercises", user_id, exs)
    conn.execute("DELETE FROM logged_workout_sets WHERE user_id = %s AND exercise_entry_id = ANY(%s) AND NOT (id = ANY(%s))",
                 (user_id, [e["id"] for e in exs], [s["id"] for s in sets]))
    out["logged_workout_sets"] = upsert(conn, "logged_workout_sets", user_id, sets)
    return out
