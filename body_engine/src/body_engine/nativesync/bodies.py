"""What a pulled change carries: the current content of one (stream, kind, day), read from the source tables at pull time.

Shapes follow the app's local tables (docs/ios-native-app.md section 4.2): times are UTC seconds, dates the user's local
YYYY-MM-DD, units as stored (kg, kcal, ms, bpm). Every builder takes a list of days and returns {day: body} for the days
that have data; a day without data is a deletion (op "delete"). Undated kinds use the day None (key "all").
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg

from ..scores.engine import COLUMNS as SCORE_COLUMNS

DROP = {"user_id", "synced_at"}


def plain(v):
    """JSON-able value: datetimes -> UTC seconds, dates -> ISO, decimals -> float, UUIDs -> str."""
    if isinstance(v, datetime):
        return round(v.timestamp(), 3)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, time):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, list):
        return [plain(x) for x in v]
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    return v


def _row(r: dict, drop: set = DROP) -> dict:
    return {k: plain(v) for k, v in r.items() if k not in drop}


def _by_day(rows, col: str, fn=_row) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r[col], []).append(fn(r))
    return out


# ---------------------------------------------------------------- scores / daily / sleep
def scores(conn, uid: int, days: list) -> dict:
    cols = ", ".join(["date", *SCORE_COLUMNS, "origin", "bundle_version", "computed_at"])
    rows = conn.execute(f"SELECT {cols} FROM daily_scores WHERE user_id = %s AND date = ANY(%s)", (uid, days)).fetchall()
    return {r["date"]: _row(r, DROP | {"date"}) for r in rows}


def daily(conn, uid: int, days: list) -> dict:
    rows = conn.execute("""SELECT date, metric, value, unit, agg, n, min_value, max_value, source FROM daily_metrics
                           WHERE user_id = %s AND date = ANY(%s) ORDER BY date, metric""", (uid, days)).fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(r["date"], {"metrics": {}})["metrics"][r["metric"]] = {
            "value": r["value"], "unit": r["unit"], "agg": r["agg"], "n": r["n"], "min": r["min_value"],
            "max": r["max_value"], "source": r["source"]}
    return out


def sleep(conn, uid: int, days: list) -> dict:
    rows = conn.execute("""SELECT id, wake_date, start_at AS start, end_at AS "end", asleep_min, core_min, deep_min, rem_min,
                                  awake_min, unspecified_min, is_main, source, device
                           FROM sleep_sessions WHERE user_id = %s AND wake_date = ANY(%s) ORDER BY start_at""",
                        (uid, days)).fetchall()
    return {d: {"sessions": items} for d, items in _by_day(rows, "wake_date", lambda r: _row(r, DROP | {"wake_date"})).items()}


# ---------------------------------------------------------------- records: one body per (kind, day) = {"items": [...]}
def _simple(table: str, daycol: str, order: str, drop: set = frozenset()):
    def build(conn, uid: int, days: list) -> dict:
        rows = conn.execute(f"SELECT * FROM {table} WHERE user_id = %s AND {daycol} = ANY(%s) ORDER BY {order}",
                            (uid, days)).fetchall()
        return {d: {"items": items} for d, items in _by_day(rows, daycol, lambda r: _row(r, DROP | set(drop))).items()}
    return build


def _undated(table: str, order: str, where: str = "TRUE"):
    def build(conn, uid: int, days: list) -> dict:
        rows = conn.execute(f"SELECT * FROM {table} WHERE user_id = %s AND {where} ORDER BY {order}", (uid,)).fetchall()
        return {None: {"items": [_row(r) for r in rows]}} if rows else {}
    return build


def _strength(conn, uid: int, days: list) -> dict:
    """Logged workouts with their exercises and sets nested (logged_workouts -> exercises -> sets)."""
    ws = conn.execute("SELECT * FROM logged_workouts WHERE user_id = %s AND date = ANY(%s) ORDER BY created_at, id",
                      (uid, days)).fetchall()
    if not ws:
        return {}
    ids = [w["id"] for w in ws]
    ex = conn.execute("""SELECT * FROM logged_workout_exercises WHERE workout_id = ANY(%s) ORDER BY sort_order, id""",
                      (ids,)).fetchall()
    sets = conn.execute("""SELECT * FROM logged_workout_sets WHERE exercise_entry_id = ANY(%s) ORDER BY set_number, id""",
                        ([e["id"] for e in ex],)).fetchall()
    sets_by = _by_day(sets, "exercise_entry_id", lambda r: _row(r, DROP | {"exercise_entry_id"}))
    ex_by = _by_day(ex, "workout_id", lambda r: {**_row(r, DROP | {"workout_id"}), "sets": sets_by.get(r["id"], [])})
    return {d: {"items": items} for d, items in
            _by_day(ws, "date", lambda r: {**_row(r), "exercises": ex_by.get(r["id"], [])}).items()}


RECORD_KINDS = {
    # kind: (builder, table, day column or None for undated kinds)
    "water": (_simple("water_intake", "local_date", "at, id"), "water_intake", "local_date"),
    "journal": (_simple("journal_entries", "date", "key"), "journal_entries", "date"),
    "food": (_simple("food_entries", "date", "time NULLS LAST, created_at, id"), "food_entries", "date"),
    "weight": (_simple("body_measurements", "date", "date"), "body_measurements", "date"),
    "strength": (_strength, "logged_workouts", "date"),
    "training_session": (_simple("training_session_logs", "date", "date"), "training_session_logs", "date"),
    "meal_log": (_simple("meal_logs", "date", "created_at, id", drop={"entries"}), "meal_logs", "date"),
    "lab": (_simple("lab_results", "collected_on", "test_name, id"), "lab_results", "collected_on"),
    "workout": (_simple("workouts", "local_date", "start_at, id"), "workouts", "local_date"),
    "supplements": (_undated("supplements", "id"), "supplements", None),
    "journal_items": (_undated("journal_items", "key"), "journal_items", None),
    "meal_templates": (_undated("meal_templates", "sort, id"), "meal_templates", None),
}


def record(conn, uid: int, kind: str, days: list) -> dict:
    return RECORD_KINDS[kind][0](conn, uid, days)


# ---------------------------------------------------------------- raw HealthKit data, one body per local day
SAMPLE_COLS = ["uuid", "type", "start", "end", "value", "value_text", "source", "tz", "meta"]
STAT_COLS = ["type", "start", "value"]


def day_bounds(d: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return datetime.combine(d, time(), tz), datetime.combine(d + timedelta(days=1), time(), tz)


def raw(conn, uid: int, days: list, tz: ZoneInfo) -> dict:
    """{day: {"samples": {"cols", "rows"}, "stats": {"cols", "rows"}}}: what the sync engine uploaded for that day
    (hk_samples by local day; the 900 s statistics buckets that start inside the day). Columnar, like the uploads."""
    out: dict = {}
    for d in days:
        lo, hi = day_bounds(d, tz)
        s = conn.execute("""SELECT hk_uuid, type, start_at, end_at, value, value_text, source, tz, meta FROM hk_samples
                            WHERE user_id = %s AND local_date = %s ORDER BY type, start_at""", (uid, d)).fetchall()
        b = conn.execute("""SELECT type, bucket_start, value FROM hk_stats WHERE user_id = %s
                            AND bucket_start >= %s AND bucket_start < %s ORDER BY type, bucket_start""", (uid, lo, hi)).fetchall()
        if s or b:
            out[d] = {"samples": {"cols": SAMPLE_COLS, "rows": [[plain(v) for v in r.values()] for r in s]},
                      "stats": {"cols": STAT_COLS, "rows": [[plain(v) for v in r.values()] for r in b]}}
    return out


def raw_size(body: dict | None) -> int:
    return 0 if not body else len(body["samples"]["rows"]) + len(body["stats"]["rows"])


def restore_days(conn, uid: int, stream: str, kind: str, before: date | None, limit: int, tz: ZoneInfo) -> list[date]:
    """Days with data of one stream (records: one kind), newest first, older than `before`; for the full restore."""
    cond, args = ("", [])
    if before is not None:
        cond, args = (" AND d < %s", [before])
    if stream == "raw":
        q = f"""SELECT d FROM (SELECT DISTINCT local_date AS d FROM hk_samples WHERE user_id = %s
                               UNION SELECT DISTINCT (bucket_start AT TIME ZONE %s)::date FROM hk_stats WHERE user_id = %s) x
                WHERE TRUE{cond} ORDER BY d DESC LIMIT %s"""
        params = [uid, str(tz), uid, *args, limit]
    else:
        table, col = {"scores": ("daily_scores", "date"), "daily": ("daily_metrics", "date"),
                      "sleep": ("sleep_sessions", "wake_date")}.get(stream) or RECORD_KINDS[kind][1:]
        q = f"SELECT d FROM (SELECT DISTINCT {col} AS d FROM {table} WHERE user_id = %s) x WHERE TRUE{cond} ORDER BY d DESC LIMIT %s"
        params = [uid, *args, limit]
    return [r["d"] for r in conn.execute(q, params).fetchall()]
