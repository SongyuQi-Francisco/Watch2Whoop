"""GET /v1/users/{user}/cardio: heart rate recovery after workouts + recovery index per night (shares scope 'scores';
the per-workout heart rate recovery is workout data and needs 'training' too, else it is left out and listed in `hidden`).

`load` reads the rows, `compute` (pure, no database) builds the document: the reference of the bundle's
OBS.cardioView (packages/openbodyscore-js src/32_stress.js, docs/ios-native-2.0.md M2)."""
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import auth
from . import hrr as H


def _mean(xs):
    return round(statistics.mean(xs), 1) if xs else None


def window(start: date | None, end: date | None, tz: ZoneInfo) -> tuple[date, date]:
    """Default range: the 90 days up to today."""
    end = end or datetime.now(tz).date()
    return start or end - timedelta(days=89), end


def load(conn: psycopg.Connection, user_id: int, start: date, end: date) -> dict:
    """{tz, hrr: workout_hrr rows joined with their workout (the 30 days before the last 30 too), scores: [{date, details}]}."""
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    lo = min(start, end - timedelta(days=2 * H.TREND_DAYS - 1))
    rows = conn.execute("""SELECT h.workout_id, h.local_date, h.end_at, h.peak_hr, h.hr_60s, h.hrr60, h.apple_hrr60, h.valid,
                                  h.reason, w.type, w.name_zh, w.duration_min
                           FROM workout_hrr h JOIN workouts w ON w.id = h.workout_id
                           WHERE h.user_id = %s AND h.local_date BETWEEN %s AND %s ORDER BY h.end_at""",
                        (user_id, lo, end)).fetchall()
    scores = conn.execute("SELECT date, details FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date",
                          (user_id, start, end)).fetchall()
    return {"tz": tz, "hrr": [dict(r) for r in rows], "scores": [dict(r) for r in scores]}


def compute(data: dict, start: date, end: date) -> dict:
    """The /cardio document (before the share check) from load's data: rows sorted by end_at, scores by date."""
    tz, rows = data["tz"], data["hrr"]
    last = [r["hrr60"] for r in rows if r["valid"] and r["local_date"] > end - timedelta(days=H.TREND_DAYS)]
    prev = [r["hrr60"] for r in rows if r["valid"]
            and end - timedelta(days=2 * H.TREND_DAYS) < r["local_date"] <= end - timedelta(days=H.TREND_DAYS)]
    workouts = [{"workout_id": r["workout_id"], "date": r["local_date"], "type": r["type"], "name_zh": r["name_zh"],
                 "duration_min": r["duration_min"], "end_at": r["end_at"].astimezone(tz).isoformat(timespec="minutes"),
                 "peak_hr": r["peak_hr"], "hr_60s": r["hr_60s"], "hrr60": r["hrr60"], "apple_hrr60": r["apple_hrr60"],
                 "valid": r["valid"], "reason": r["reason"]} for r in rows if r["local_date"] >= start]
    nights = []
    for r in data["scores"]:
        if not start <= r["date"] <= end:
            continue
        rec, sl = (r["details"] or {}).get("recovery") or {}, (r["details"] or {}).get("sleep") or {}
        off = rec.get("rhr_low_offset_min")
        if off is None:
            continue
        pct = None
        if sl.get("bedtime") and sl.get("wake_time"):
            span = (datetime.fromisoformat(sl["wake_time"]) - datetime.fromisoformat(sl["bedtime"])).total_seconds() / 60
            pct = round(min(max(off / span, 0), 1) * 100) if span > 0 else None
        nights.append({"date": r["date"], "offset_min": off, "pct": pct})
    pcts = [n["pct"] for n in nights if n["pct"] is not None]
    return {"start": start, "end": end, "algo": H.ALGO,
            "hrr": {"workouts": workouts, "last30_mean": _mean(last), "last30_n": len(last), "prev30_mean": _mean(prev),
                    "prev30_n": len(prev), "peak_min_pct": H.PEAK_MIN_PCT},
            "recovery_index": {"nights": nights, "median_pct": round(statistics.median(pcts)) if pcts else None}}


def cardio(conn: psycopg.Connection, user_id: int, start: date | None = None, end: date | None = None,
           viewer_id: int | None = None) -> dict:
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    start, end = window(start, end, tz)
    out = compute(load(conn, user_id, start, end), start, end)
    if "training" not in auth.visible_scopes(conn, viewer_id, user_id, ("training",)):
        del out["hrr"]
        out["hidden"] = ["training"]
    return out
