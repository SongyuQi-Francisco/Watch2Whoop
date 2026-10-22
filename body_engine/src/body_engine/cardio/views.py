"""GET /v1/users/{user}/cardio: heart rate recovery after workouts + recovery index per night (shares scope 'scores';
the per-workout heart rate recovery is workout data and needs 'training' too, else it is left out and listed in `hidden`)."""
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import auth
from . import hrr as H


def _mean(xs):
    return round(statistics.mean(xs), 1) if xs else None


def cardio(conn: psycopg.Connection, user_id: int, start: date | None = None, end: date | None = None,
           viewer_id: int | None = None) -> dict:
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    end = end or datetime.now(tz).date()
    start = start or end - timedelta(days=89)
    lo = min(start, end - timedelta(days=2 * H.TREND_DAYS - 1))
    rows = conn.execute("""SELECT h.*, w.type, w.name_zh, w.duration_min FROM workout_hrr h JOIN workouts w ON w.id = h.workout_id
                           WHERE h.user_id = %s AND h.local_date BETWEEN %s AND %s ORDER BY h.end_at""",
                        (user_id, lo, end)).fetchall()
    last = [r["hrr60"] for r in rows if r["valid"] and r["local_date"] > end - timedelta(days=H.TREND_DAYS)]
    prev = [r["hrr60"] for r in rows if r["valid"]
            and end - timedelta(days=2 * H.TREND_DAYS) < r["local_date"] <= end - timedelta(days=H.TREND_DAYS)]
    workouts = [{"workout_id": r["workout_id"], "date": r["local_date"], "type": r["type"], "name_zh": r["name_zh"],
                 "duration_min": r["duration_min"], "end_at": r["end_at"].astimezone(tz).isoformat(timespec="minutes"),
                 "peak_hr": r["peak_hr"], "hr_60s": r["hr_60s"], "hrr60": r["hrr60"], "apple_hrr60": r["apple_hrr60"],
                 "valid": r["valid"], "reason": r["reason"]} for r in rows if r["local_date"] >= start]
    nights = []
    for r in conn.execute("""SELECT date, details FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s
                             ORDER BY date""", (user_id, start, end)):
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
    out = {"start": start, "end": end, "algo": H.ALGO,
           "hrr": {"workouts": workouts, "last30_mean": _mean(last), "last30_n": len(last), "prev30_mean": _mean(prev),
                   "prev30_n": len(prev), "peak_min_pct": H.PEAK_MIN_PCT},
           "recovery_index": {"nights": nights, "median_pct": round(statistics.median(pcts)) if pcts else None}}
    if "training" not in auth.visible_scopes(conn, viewer_id, user_id, ("training",)):
        del out["hrr"]
        out["hidden"] = ["training"]
    return out
