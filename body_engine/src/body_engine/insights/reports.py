"""Weekly / monthly reports (matching WHOOP Performance Assessment): summary of recovery, sleep, strain, stress, training, weight,
nutrition (calorie / protein adherence against the goal in effect each day, added sugar, sugary drinks) and journal findings, compared with the previous period.
Outputs only structured data; the text summary is written by the body butler. Week = 7 days back from end, month = 30 days back from end, previous period = the immediately preceding span of the same length.

Viewing someone else's report (issue #111): every block is authorized by its own shares scope, like the Today page's
per-block sections: recovery / sleep / load / stress need scores, training training, weight weight, nutrition nutrition,
journal_findings journal. A block the viewer may not see is not computed, left out of current / previous_values /
change and its scope is listed in `hidden`. Sleep is split like the Today page: duration / score / debt come with the
scores row, bedtime, wake time and regularity (from the sleep sessions) need `sleep`. With only `nutrition_summary`
the nutrition block keeps just the goal adherence counts.

2.0.0 package M5 (docs/ios-native-2.0.md section 4): `report` = `compute(load(...))`. `load` reads the inputs of both periods
once, as plain rows; `compute` is pure and is the reference of the phone's `OBS.report` (packages/openbodyscore-js/
src/35_reports.js). A block whose input is absent from `data` (the key is missing, not None) is left out: the phone then
takes that block from the server's document (nutrition always: goals and food extras are not on the phone yet).
Thresholds with their sources: reports_params.py."""
import math
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import auth, sparky_client
from ..records import analysis
from . import reports_params as RP, sleep_detail

REPORTS_VERSION = RP.REPORTS_VERSION
PERIOD_DAYS = RP.PERIOD_DAYS
SLEEP_TIMING = ("avg_bedtime", "avg_wake", "consistency_pct")   # from the sleep sessions: scope sleep
NUTRITION_SUMMARY = ("days_logged", "calories_on_target_days", "calories_judged_days", "protein_on_target_days",
                     "protein_judged_days")                     # all that scope nutrition_summary shows
REPORT_SCOPES = ("scores", "sleep", "training", "weight", "nutrition", "nutrition_summary", "journal")


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


def _sparky_error(e: Exception) -> str:
    return f"SparkyFitness：{type(e).__name__}: {e}"


def window(period: str, end: date) -> tuple[date, date, date]:
    """(start, previous start, previous end) of the period ending on `end`."""
    n = PERIOD_DAYS[period]
    return end - timedelta(days=n - 1), end - timedelta(days=2 * n - 1), end - timedelta(days=n)


# ---------------------------------------------------------------- pure blocks (the phone runs the same, 35_reports.js)
def scores_block(rows: list[dict]) -> dict:
    """rows: the period's daily_scores rows by date (load's columns; `s` = details.sleep, `rhr_daily` = flags has
    rhr_from_daily)."""
    rec = [r["recovery"] for r in rows if r["recovery"] is not None]
    last = rows[-1] if rows else None
    hrv = [math.exp(r["hrv_night_ln"]) for r in rows if r["hrv_night_ln"] is not None]
    return {
        "recovery": {"avg": _mean(rec), "days": len(rec), "green": sum(r >= RP.RECOVERY_GREEN for r in rec),
                     "yellow": sum(RP.RECOVERY_RED <= r < RP.RECOVERY_GREEN for r in rec),
                     "red": sum(r < RP.RECOVERY_RED for r in rec),
                     "hrv_ms_avg": _mean(hrv),
                     "rhr_avg": _mean(r["rhr_night"] for r in rows if not r["rhr_daily"])},   # only the lowest-during-sleep value
        "sleep": {"nights": sum(r["sleep_actual_min"] is not None for r in rows),
                  "avg_actual_min": _mean(r["sleep_actual_min"] for r in rows),
                  "avg_score": _mean(r["sleep_score"] for r in rows),
                  "avg_performance": _mean(r["sleep_performance"] for r in rows),
                  "avg_debt_min": _mean(r["sleep_debt_min"] for r in rows),
                  "avg_bedtime": _clock_mean([(r["s"] or {}).get("bedtime") for r in rows]),
                  "avg_wake": _clock_mean([(r["s"] or {}).get("wake_time") for r in rows]),
                  "nights_under_6h": sum(1 for r in rows if r["sleep_actual_min"] is not None
                                         and r["sleep_actual_min"] < RP.SHORT_SLEEP_MIN)},
        "load": {"days": sum(r["strain"] is not None for r in rows), "avg_strain": _mean(r["strain"] for r in rows),
                 "max_strain": max((r["strain"] for r in rows if r["strain"] is not None), default=None),
                 "days_strain_14_plus": sum(1 for r in rows if (r["strain"] or 0) >= RP.HIGH_STRAIN),
                 "total_trimp": round(sum(r["trimp"] or 0 for r in rows), 1),
                 "muscle_volume_kg": round(sum(r["muscle_load"] or 0 for r in rows), 1),
                 "ctl_end": last["ctl"] if last else None, "atl_end": last["atl"] if last else None,
                 "tsb_end": last["tsb"] if last else None},
    }


def stress_block(rows: list[dict] | None) -> dict:
    """rows: the period's stress_daily rows (None = there is no stress_daily table)."""
    if rows is None:
        return {"days": 0, "avg": None}
    return {"days": sum(r["score"] is not None for r in rows), "avg": _mean(r["score"] for r in rows),
            "high_hours": sum(r["high_h"] or 0 for r in rows)}


def consistency(sessions: list[dict], tz: ZoneInfo, a: date, b: date) -> int | None:
    """Sleep regularity % of [a, b]: sleep_detail.sri_for on the sessions load read (wake dates a − 1 … b, by start)."""
    rows = [r for r in sessions if a - timedelta(days=1) <= r["wake_date"] <= b]
    return sleep_detail.consistency_pct(sleep_detail.sri_from(*sleep_detail.watch_sessions(rows), tz, a, b)["sri"])


def training_block(t: dict, a: date, b: date) -> dict:
    """t: {"log": the training log's sessions (sparky_client.training_log shape) or None, "error": text or None,
    "workouts": rows {local_date, name_zh, duration_min} of the kept workouts, or None (no table)}."""
    out = {"sessions": None, "strength_sessions": None, "workouts": None, "workout_minutes": None, "by_type": {},
           "error": None}
    if t["error"] is not None:   # SparkyFitness being unreadable affects only this block
        out["error"] = t["error"]
    else:
        log = [s for s in t["log"] if a.isoformat() <= s["date"][:10] <= b.isoformat()]
        out["sessions"] = len(log)
        out["strength_sessions"] = sum(1 for s in log if any((e.get("category") or "strength") == "strength"
                                                             for e in s["exercises"]))
        out["session_names"] = [f"{s['date'][:10]} {s['name']}" for s in log]
    if t["workouts"] is not None:
        rows = [r for r in t["workouts"] if a <= r["local_date"] <= b]
        out["workouts"] = len(rows)
        out["workout_minutes"] = round(sum(r["duration_min"] for r in rows))
        for r in rows:
            out["by_type"][r["name_zh"]] = out["by_type"].get(r["name_zh"], 0) + 1
    return out


def weight_block(w: dict, start: date, end: date) -> dict:
    """w: {"rows": weigh-ins of [start − 30 days, end] by date (sparky_client.weight)} or {"error": text}."""
    if "error" in w:
        return {"error": w["error"]}
    rows = [r for r in w["rows"] if "weight_kg" in r]
    inside = [r for r in rows if start.isoformat() <= r["date"] <= end.isoformat()]
    before = [r for r in rows if r["date"] < start.isoformat()]
    first = before[-1] if before else (inside[0] if inside else None)
    last = inside[-1] if inside else None
    return {"weigh_ins": len(inside), "last_kg": last["weight_kg"] if last else None, "last_date": last["date"] if last else None,
            "change_kg": round(last["weight_kg"] - first["weight_kg"], 2) if first and last and first is not last else None,
            "from_date": first["date"] if first and last and first is not last else None}


def nutrition_block(n: dict, a: date, b: date) -> dict:
    """Food logs against the goal in effect each day (goals/nutrition.py) and added sugar (unknown is not 0).
    n: {"days": nutrition/daily.nutrition days} or {"error": text}."""
    if "error" in n:   # SparkyFitness being unreadable affects only this block
        return {"error": n["error"]}
    days = [d for d in n["days"] if a.isoformat() <= d["date"] <= b.isoformat()]
    logged = [d for d in days if d["logged"]]
    judged_cal = [d for d in logged if d["adherence"]["calories"] is not None]
    judged_pro = [d for d in logged if d["adherence"]["protein"] is not None]
    sugar = [d for d in logged if d["added_sugar_g"] is not None]
    return {"days_logged": len(logged), "avg_calories": _mean(d["calories"] for d in logged),
            "avg_protein_g": _mean(d["protein_g"] for d in logged),
            "calories_on_target_days": sum(1 for d in judged_cal if d["adherence"]["calories"]),
            "calories_judged_days": len(judged_cal),
            "protein_on_target_days": sum(1 for d in judged_pro if d["adherence"]["protein"]),
            "protein_judged_days": len(judged_pro),
            "added_sugar_avg_g": _mean(d["added_sugar_g"] for d in sugar), "added_sugar_known_days": len(sugar),
            "added_sugar_over_cap_days": sum(1 for d in sugar if d["goal"] and d["added_sugar_g"] > d["goal"]["added_sugar_max_g"]),
            "sugar_avg_g": _mean(d["sugar_g"] for d in logged),
            "sugary_drinks": sum(d["sugary_drinks"] for d in logged),
            "goal_versions": sorted({d["goal"]["name"] for d in days if d["goal"] and d["goal"]["name"]})}


def journal_block(results: list[dict] | None) -> list[dict]:
    """Behaviors in the last 90 days of the journal that have a "clear effect on next-day recovery" (>= 5 days each,
    |t| >= 1.96), sorted by effect size. results: analysis.analyze_all (first version); None = it failed."""
    found = []
    for a in results or []:
        o = a["outcomes"].get("next_recovery") or {}
        if o.get("status") == "ok" and o.get("direction") in ("better", "worse"):
            found.append({"key": a["item"]["key"], "label": a["item"]["label_zh"], "direction": o["direction"],
                          "diff_recovery": o["diff_yes_minus_no"], "cohens_d": o["cohens_d"], "reliable": o["reliable"],
                          "days_yes": a["days_yes"], "days_no": a["days_no"]})
    return sorted(found, key=lambda x: -abs(x["cohens_d"] or 0))[:RP.JOURNAL_TOP]


def _delta(cur, prev):
    if isinstance(cur, dict) and isinstance(prev, dict):
        return {k: _delta(cur[k], prev.get(k)) for k in cur if isinstance(cur[k], (int, float, dict)) and not isinstance(cur[k], bool)}
    if isinstance(cur, (int, float)) and isinstance(prev, (int, float)):
        return round(cur - prev, 2)
    return None


def compute(data: dict, period: str, end: date) -> dict:
    """The report from `load`'s data (pure). data: {"tz", "can": the visible scopes, "nutrition_mode": full | summary |
    None, "scores", "stress", "sleep_sessions", "training", "weights", "nutrition", "journal_results" | "journal"}; see
    `load`. "journal" (the phone) = the input of analysis.analyze_data instead of the server's ready results."""
    if period not in PERIOD_DAYS:
        raise ValueError("period 要写 week 或 month")
    start, pstart, pend = window(period, end)
    tz = data["tz"] if isinstance(data["tz"], ZoneInfo) else ZoneInfo(data["tz"])
    can = set(data["can"])
    nutrition = data.get("nutrition_mode")

    def block(a, b):
        out = {}
        if "scores" in can:
            out = scores_block([r for r in data["scores"] if a <= r["date"] <= b])
            if "stress" in data:
                out["stress"] = stress_block(None if data["stress"] is None
                                             else [r for r in data["stress"] if a <= r["date"] <= b])
            if "sleep" in can:
                out["sleep"]["consistency_pct"] = consistency(data["sleep_sessions"], tz, a, b)
            else:
                for k in SLEEP_TIMING:
                    out["sleep"].pop(k, None)
        return out

    cur, prev = block(start, end), block(pstart, pend)
    if "training" in can and "training" in data:
        cur["training"], prev["training"] = training_block(data["training"], start, end), \
            training_block(data["training"], pstart, pend)
    if "weight" in can and "weights" in data:
        cur["weight"] = weight_block(data["weights"], start, end)
    if nutrition and "nutrition" in data:
        cur["nutrition"], prev["nutrition"] = nutrition_block(data["nutrition"], start, end), \
            nutrition_block(data["nutrition"], pstart, pend)
        if nutrition == "summary":
            cur["nutrition"], prev["nutrition"] = ({k: v for k, v in x.items() if k in NUTRITION_SUMMARY or k == "error"}
                                                   for x in (cur["nutrition"], prev["nutrition"]))
    out = {"period": period, "start": start, "end": end, "previous": {"start": pstart, "end": pend},
           "current": cur, "previous_values": prev,
           "change": {k: _delta(cur[k], prev.get(k)) for k in ("recovery", "sleep", "load", "stress", "training",
                                                               "nutrition") if k in cur}}
    if "journal" in can:
        if "journal_results" in data:
            out["journal_findings"] = journal_block(data["journal_results"])
        elif "journal" in data:
            out["journal_findings"] = journal_block(analysis.analyze_data(data["journal"], end, RP.JOURNAL_DAYS))
    hidden = [s for s in ("scores", "sleep", "training", "weight", "nutrition", "journal") if s not in can]
    if hidden:
        out["hidden"] = hidden   # not shared with this viewer: left out, not an error
    return out


# ---------------------------------------------------------------- reading the server's tables
def load(conn: psycopg.Connection, user_id: int, period: str, end: date, viewer_id: int | None = None,
         sparky=None) -> dict:
    """Every input of the report for both periods, only for the scopes the viewer may see (module docstring)."""
    start, pstart, _ = window(period, end)
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    can = auth.visible_scopes(conn, viewer_id, user_id, REPORT_SCOPES)
    nutrition = "full" if "nutrition" in can else "summary" if "nutrition_summary" in can else None
    data: dict = {"tz": tz, "can": sorted(can), "nutrition_mode": nutrition}
    if "scores" in can:
        data["scores"] = conn.execute(
            """SELECT date, recovery, sleep_score, sleep_actual_min, sleep_performance, sleep_debt_min, strain,
                      trimp, muscle_load, ctl, atl, tsb, rhr_night, hrv_night_ln, details->'sleep' AS s,
                      flags ? 'rhr_from_daily' AS rhr_daily
               FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date""",
            (user_id, pstart, end)).fetchall()
        data["stress"] = conn.execute(
            "SELECT date, score, high_h FROM stress_daily WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date",
            (user_id, pstart, end)).fetchall() if _table_exists(conn, "stress_daily") else None
        if "sleep" in can:   # sleep_detail.load_intervals of both periods (wake dates from the day before each)
            data["sleep_sessions"] = conn.execute(
                """SELECT wake_date, start_at, end_at, asleep_min, awake_min, deep_min, rem_min, core_min, is_main, device
                   FROM sleep_sessions WHERE user_id = %s AND wake_date BETWEEN %s AND %s ORDER BY start_at""",
                (user_id, pstart - timedelta(days=1), end)).fetchall()
    if {"training", "weight"} & can or nutrition:
        try:
            sparky = sparky or sparky_client.reader_for_user(conn, user_id)
        except Exception:
            sparky = None
    if "training" in can:
        t = {"log": None, "error": None, "workouts": None}
        try:
            t["log"] = sparky_client.training_log(sparky or sparky_client.reader_for_user(conn, user_id), pstart, end)
        except Exception as e:  # SparkyFitness being unreadable affects only this block
            t["error"] = _sparky_error(e)
        if _table_exists(conn, "workouts"):
            t["workouts"] = conn.execute(
                """SELECT local_date, name_zh, duration_min FROM workouts WHERE user_id = %s AND dup_of IS NULL
                   AND excluded IS NULL AND local_date BETWEEN %s AND %s ORDER BY local_date, start_at, id""",
                (user_id, pstart, end)).fetchall()
        data["training"] = t
    if "weight" in can:
        try:
            data["weights"] = {"rows": sparky_client.weight(sparky or sparky_client.reader_for_user(conn, user_id),
                                                            start - timedelta(days=30), end)}
        except Exception as e:
            data["weights"] = {"error": _sparky_error(e)}
    if nutrition:
        from ..nutrition import daily
        try:
            data["nutrition"] = {"days": daily.nutrition(conn, user_id, pstart, end, include_entries=False,
                                                         client=sparky)["days"]}
        except Exception as e:  # SparkyFitness being unreadable affects only this block
            data["nutrition"] = {"error": _sparky_error(e)}
    if "journal" in can:
        try:
            data["journal_results"] = analysis.analyze_all(conn, user_id, end, RP.JOURNAL_DAYS)
        except Exception:
            data["journal_results"] = None
    return data


def _nutrition_block(conn, user_id: int, start: date, end: date, sparky=None) -> dict:
    """One period's nutrition block straight from the tables (tests)."""
    from ..nutrition import daily
    try:
        n = {"days": daily.nutrition(conn, user_id, start, end, include_entries=False, client=sparky)["days"]}
    except Exception as e:
        n = {"error": _sparky_error(e)}
    return nutrition_block(n, start, end)


def report(conn: psycopg.Connection, user_id: int, period: str, end: date, sparky=None,
           viewer_id: int | None = None) -> dict:
    """`viewer_id` = who reads it (None = the owner): blocks outside the viewer's shares are left out (module docstring)."""
    if period not in PERIOD_DAYS:
        raise ValueError("period 要写 week 或 month")
    return compute(load(conn, user_id, period, end, viewer_id, sparky), period, end)
