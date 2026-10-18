"""Weekly / monthly reports (matching WHOOP Performance Assessment): summary of recovery, sleep, strain, stress, training, weight and journal findings, compared with the previous period.
Outputs only structured data; the text summary is written by the body butler. Week = 7 days back from end, month = 30 days back from end, previous period = the immediately preceding span of the same length."""
import math
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import sparky_client
from ..records import analysis
from . import sleep_detail

PERIOD_DAYS = {"week": 7, "month": 30}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.fmean(xs), 2) if xs else None


def _clock_mean(times: list[str]) -> str | None:
    """Circular mean of times of day (the mean of 23:30 and 00:30 is 00:00, not 12:00), applicable to both bedtime and wake time."""
    angs = []
    for t in times:
        if not t:
            continue
        dt = datetime.fromisoformat(t)
        angs.append((dt.hour * 60 + dt.minute) / 1440 * 2 * math.pi)
    if not angs:
        return None
    m = math.atan2(sum(math.sin(a) for a in angs), sum(math.cos(a) for a in angs)) % (2 * math.pi)
    mins = round(m / (2 * math.pi) * 1440) % 1440
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) AS t", (name,)).fetchone()["t"] is not None


def _scores_block(conn, user_id: int, start: date, end: date) -> dict:
    rows = conn.execute("""SELECT date, recovery, sleep_score, sleep_actual_min, sleep_performance, sleep_debt_min, strain,
                                  trimp, muscle_load, ctl, atl, tsb, rhr_night, hrv_night_ln, details->'sleep' AS s,
                                  flags ? 'rhr_from_daily' AS rhr_daily
                           FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date""",
                        (user_id, start, end)).fetchall()
    rec = [r["recovery"] for r in rows if r["recovery"] is not None]
    last = rows[-1] if rows else None
    hrv = [math.exp(r["hrv_night_ln"]) for r in rows if r["hrv_night_ln"] is not None]
    return {
        "recovery": {"avg": _mean(rec), "days": len(rec), "green": sum(r >= 67 for r in rec),
                     "yellow": sum(34 <= r < 67 for r in rec), "red": sum(r < 34 for r in rec),
                     "hrv_ms_avg": _mean(hrv),
                     "rhr_avg": _mean(r["rhr_night"] for r in rows if not r["rhr_daily"])},   # only the lowest-during-sleep value
        "sleep": {"nights": sum(r["sleep_actual_min"] is not None for r in rows),
                  "avg_actual_min": _mean(r["sleep_actual_min"] for r in rows),
                  "avg_score": _mean(r["sleep_score"] for r in rows),
                  "avg_performance": _mean(r["sleep_performance"] for r in rows),
                  "avg_debt_min": _mean(r["sleep_debt_min"] for r in rows),
                  "avg_bedtime": _clock_mean([(r["s"] or {}).get("bedtime") for r in rows]),
                  "avg_wake": _clock_mean([(r["s"] or {}).get("wake_time") for r in rows]),
                  "nights_under_6h": sum(1 for r in rows if r["sleep_actual_min"] is not None and r["sleep_actual_min"] < 360)},
        "load": {"days": sum(r["strain"] is not None for r in rows), "avg_strain": _mean(r["strain"] for r in rows),
                 "max_strain": max((r["strain"] for r in rows if r["strain"] is not None), default=None),
                 "days_strain_14_plus": sum(1 for r in rows if (r["strain"] or 0) >= 14),
                 "total_trimp": round(sum(r["trimp"] or 0 for r in rows), 1),
                 "muscle_volume_kg": round(sum(r["muscle_load"] or 0 for r in rows), 1),
                 "ctl_end": last["ctl"] if last else None, "atl_end": last["atl"] if last else None,
                 "tsb_end": last["tsb"] if last else None},
    }


def _stress_block(conn, user_id: int, start: date, end: date) -> dict:
    if not _table_exists(conn, "stress_daily"):
        return {"days": 0, "avg": None}
    rows = conn.execute("SELECT score, high_h FROM stress_daily WHERE user_id = %s AND date BETWEEN %s AND %s",
                        (user_id, start, end)).fetchall()
    return {"days": sum(r["score"] is not None for r in rows), "avg": _mean(r["score"] for r in rows),
            "high_hours": sum(r["high_h"] or 0 for r in rows)}


def _training_block(conn, user_id: int, start: date, end: date, sparky=None) -> dict:
    out = {"sessions": None, "strength_sessions": None, "workouts": None, "workout_minutes": None, "by_type": {},
           "error": None}
    try:
        sparky = sparky or sparky_client.client_for_user(conn, user_id)
        log = sparky_client.training_log(sparky, start, end)
        out["sessions"] = len(log)
        out["strength_sessions"] = sum(1 for s in log if any((e.get("category") or "strength") == "strength"
                                                             for e in s["exercises"]))
        out["session_names"] = [f"{s['date'][:10]} {s['name']}" for s in log]
    except Exception as e:  # SparkyFitness being unreadable affects only this block
        out["error"] = f"SparkyFitness：{type(e).__name__}: {e}"
    if _table_exists(conn, "workouts"):
        rows = conn.execute("""SELECT name_zh, duration_min FROM workouts WHERE user_id = %s AND dup_of IS NULL
                               AND excluded IS NULL AND local_date BETWEEN %s AND %s""", (user_id, start, end)).fetchall()
        out["workouts"] = len(rows)
        out["workout_minutes"] = round(sum(r["duration_min"] for r in rows))
        for r in rows:
            out["by_type"][r["name_zh"]] = out["by_type"].get(r["name_zh"], 0) + 1
    return out


def _weight_block(conn, user_id: int, start: date, end: date, sparky=None) -> dict:
    try:
        sparky = sparky or sparky_client.client_for_user(conn, user_id)
        rows = [r for r in sparky_client.weight(sparky, start - timedelta(days=30), end) if "weight_kg" in r]
    except Exception as e:
        return {"error": f"SparkyFitness：{type(e).__name__}: {e}"}
    inside = [r for r in rows if start.isoformat() <= r["date"] <= end.isoformat()]
    before = [r for r in rows if r["date"] < start.isoformat()]
    first = before[-1] if before else (inside[0] if inside else None)
    last = inside[-1] if inside else None
    return {"weigh_ins": len(inside), "last_kg": last["weight_kg"] if last else None, "last_date": last["date"] if last else None,
            "change_kg": round(last["weight_kg"] - first["weight_kg"], 2) if first and last and first is not last else None,
            "from_date": first["date"] if first and last and first is not last else None}


def _journal_block(conn, user_id: int, end: date) -> list[dict]:
    """Behaviors in the last 90 days of the journal that have a "clear effect on next-day recovery" (>= 5 days each, |t| >= 1.96), sorted by effect size."""
    found = []
    try:
        results = analysis.analyze_all(conn, user_id, end, 90)
    except Exception:
        return []
    for a in results:
        o = a["outcomes"].get("next_recovery") or {}
        if o.get("status") == "ok" and o.get("direction") in ("better", "worse"):
            found.append({"key": a["item"]["key"], "label": a["item"]["label_zh"], "direction": o["direction"],
                          "diff_recovery": o["diff_yes_minus_no"], "cohens_d": o["cohens_d"], "reliable": o["reliable"],
                          "days_yes": a["days_yes"], "days_no": a["days_no"]})
    return sorted(found, key=lambda x: -abs(x["cohens_d"] or 0))[:5]


def _delta(cur, prev):
    if isinstance(cur, dict) and isinstance(prev, dict):
        return {k: _delta(cur[k], prev.get(k)) for k in cur if isinstance(cur[k], (int, float, dict)) and not isinstance(cur[k], bool)}
    if isinstance(cur, (int, float)) and isinstance(prev, (int, float)):
        return round(cur - prev, 2)
    return None


def report(conn: psycopg.Connection, user_id: int, period: str, end: date, sparky=None) -> dict:
    if period not in PERIOD_DAYS:
        raise ValueError("period 要写 week 或 month")
    n = PERIOD_DAYS[period]
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    start, pstart, pend = end - timedelta(days=n - 1), end - timedelta(days=2 * n - 1), end - timedelta(days=n)

    def block(a, b):
        out = _scores_block(conn, user_id, a, b)
        reg = sleep_detail.sri_for(conn, user_id, tz, a, b)
        out["sleep"]["consistency_pct"] = sleep_detail.consistency_pct(reg["sri"])
        out["stress"] = _stress_block(conn, user_id, a, b)
        return out

    cur, prev = block(start, end), block(pstart, pend)
    try:
        sparky = sparky or sparky_client.client_for_user(conn, user_id)
    except Exception:
        sparky = None
    cur["training"], prev["training"] = _training_block(conn, user_id, start, end, sparky), \
        _training_block(conn, user_id, pstart, pend, sparky)
    cur["weight"] = _weight_block(conn, user_id, start, end, sparky)
    return {"period": period, "start": start, "end": end, "previous": {"start": pstart, "end": pend},
            "current": cur, "previous_values": prev,
            "change": {k: _delta(cur[k], prev.get(k)) for k in ("recovery", "sleep", "load", "stress", "training")},
            "journal_findings": _journal_block(conn, user_id, end)}
