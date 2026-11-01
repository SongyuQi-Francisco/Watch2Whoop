"""Kind `stress` of package M2 (stress; docs/ios-native-2.0.md sections 3.2 and 4): the stress_daily rows the phone computed.

Rows of one day = [one row] as insights/stress.score_range writes it: {score, level, low_h, medium_h, high_h, peak_hour,
hours: [{hour, score, level, hr, samples, hrv_z}], baseline: {calm_hr, spread, hrv_ln} | null, flags: {key: text}}.
The phone builds it with the bundle's stressLocal (src/32_stress.js) = insights/stress.days_local: its own HealthKit samples
first, the history area on days without any, the same merge as the server's sync mode `app` (insights/stress.merge_app).

The spot check recomputes the day with insights/stress.compute from the server's tables (the app's uploaded samples and
workouts, Open Wearables' archive, sleep_sessions, daily_scores.hrv_night_ln); a day the server has no raw heart rate for
is not compared. Section 3.4: score 0.01, level exact, hours: the level of each hour exact (the hour's own numbers are not
compared: recompute takes the phone's hour rows when the (hour, level) pairs agree), the baseline's numbers within their
display rounding, counts and peak hour exact, flags exact (texts are part of the output).
Stored (primary only): the day's stress_daily row replaced, origin 'app' + bundle_version (migration 1020).
Pull: record kind `stress` of the `records` stream (whole-day replace), so a reinstalled phone gets the rows back.
"""
from __future__ import annotations

import math
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from ...i18n import L
from ...insights import stress as S
from .. import bodies

KIND = "stress"
PACKAGE = "stress"
ALGO_KEY = "stress_cards"
# section 3.4: the day's score 0.01 (shown with one decimal; both sides run the same algorithm, goldens within 1e-9)
TOLERANCE = {"score": 0.01}
# the baseline's numbers, compared inside recompute (see _baseline_close): half their stored rounding (1 / 2 / 3 decimals)
BASELINE_TOLERANCE = {"calm_hr": 0.05, "spread": 0.005, "hrv_ln": 0.0005}
LEVELS = (None, "low", "medium", "high")
HOUR_KEYS = ("hour", "score", "level", "hr", "samples", "hrv_z")

RECORD_KIND = "stress"
RECORD_TABLE = "stress_daily"
RECORD_DAYCOL = "date"
record_body = bodies._simple("stress_daily", "date", "date")


def _num(v, field: str, nullable: bool = True, lo: float | None = None, hi: float | None = None):
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
            or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(L(f"{field} 应该是范围内的数字", f"{field} must be a number in range"))
    return v


def _int(v, field: str, nullable: bool = False, hi: int | None = None) -> int | None:
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or v < 0 or (hi is not None and v > hi):
        raise ValueError(L(f"{field} 应该是非负整数", f"{field} must be a non-negative integer"))
    return int(v)


def validate(day: date, rows: list[dict]) -> list[dict]:
    if len(rows) > 1:
        raise ValueError(L("一天只有一行压力", "One stress row per day"))
    out = []
    for r in rows:
        if r.get("level") not in LEVELS:
            raise ValueError(L("压力等级不对", "Bad stress level"))
        hours = r.get("hours") or []
        if not isinstance(hours, list) or len(hours) > 24 or any(not isinstance(h, dict) for h in hours):
            raise ValueError(L("hours 格式不对", "Bad hours"))
        hs = []
        for h in hours:
            if h.get("level") not in LEVELS[1:]:
                raise ValueError(L("压力等级不对", "Bad stress level"))
            hs.append({"hour": _int(h.get("hour"), "hour", hi=23), "score": _num(h.get("score"), "score", False, 0, 3),
                       "level": h["level"], "hr": _num(h.get("hr"), "hr", True, 0, 300),
                       "samples": _int(h.get("samples"), "samples"), "hrv_z": _num(h.get("hrv_z"), "hrv_z")})
        base = r.get("baseline")
        if base is not None and (not isinstance(base, dict) or set(base) - set(BASELINE_TOLERANCE)):
            raise ValueError(L("baseline 格式不对", "Bad baseline"))
        flags = r.get("flags") or {}
        if not isinstance(flags, dict) or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 300
                                              for k, v in flags.items()):
            raise ValueError(L("flags 格式不对", "Bad flags"))
        out.append({"score": _num(r.get("score"), "score", True, 0, 3), "level": r.get("level"),
                    "low_h": _int(r.get("low_h"), "low_h", hi=24), "medium_h": _int(r.get("medium_h"), "medium_h", hi=24),
                    "high_h": _int(r.get("high_h"), "high_h", hi=24),
                    "peak_hour": _int(r.get("peak_hour"), "peak_hour", nullable=True, hi=23), "hours": hs,
                    "baseline": None if base is None else {k: _num(base.get(k), k) for k in BASELINE_TOLERANCE},
                    "flags": dict(flags)})
    return out


def _levels(hours: list[dict]) -> list[tuple]:
    return [(h["hour"], h["level"]) for h in hours or []]


def _baseline_close(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None:
        return a is b
    return all((a.get(k) is None) == (b.get(k) is None) and (a.get(k) is None or abs(a[k] - b[k]) <= t + 1e-12)
               for k, t in BASELINE_TOLERANCE.items())


def recompute(conn: psycopg.Connection, uid: int, day: date, uploaded: dict[date, list[dict]]) -> list[dict] | None:
    rows = S.compute(conn, None, uid, day, day)
    if not rows or "no_raw_hr" in rows[0]["flags"]:
        return None   # the server has no raw heart rate of that day (yet): nothing to compare with
    server = validate(day, [{k: v for k, v in rows[0].items() if k != "date"}])[0]
    app = (uploaded.get(day) or [None])[0]
    if app is not None:
        if _levels(app["hours"]) == _levels(server["hours"]):
            server["hours"] = app["hours"]          # hours: only their levels are compared
        if _baseline_close(app["baseline"], server["baseline"]):
            server["baseline"] = app["baseline"]
    return [server]


def write(conn: psycopg.Connection, uid: int, days: dict[date, list[dict]], bundle_version: str | None) -> int:
    n = 0
    for d, rows in days.items():
        conn.execute("DELETE FROM stress_daily WHERE user_id = %s AND date = %s", (uid, d))
        for r in rows:
            conn.execute(
                """INSERT INTO stress_daily (user_id, date, score, level, low_h, medium_h, high_h, peak_hour, hours, baseline,
                                             flags, algo_version, origin, bundle_version)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'app', %s)""",
                (uid, d, r["score"], r["level"], r["low_h"], r["medium_h"], r["high_h"], r["peak_hour"], Jsonb(r["hours"]),
                 Jsonb(r["baseline"]) if r["baseline"] is not None else None, Jsonb(r["flags"]), S.STRESS_VERSION,
                 bundle_version))
            n += 1
    return n
