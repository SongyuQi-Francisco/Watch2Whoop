"""Kind `healthspan` of package M4 (coach; docs/ios-native-2.0.md sections 3.2 and 4): the body-age weeks the phone computed.

One upload day = one week, keyed by its Monday (`week_start`); its rows = [one row] as insights/healthspan_store.weekly
writes it: {as_of, chrono_age, body_age, delta_years, pace_of_aging, pace_raw, pace_window_weeks, contributors: [{key, …}],
insufficient: [{key, …}]}. The phone builds it with the bundle's healthspanLocal (src/34_coach.js) =
healthspan_store.weekly_local: the same loader (from_parts) on its local tables (daily_scores, daily_metrics, sleep_sessions,
workouts, the SparkyFitness strength log and weigh-ins of ext_records, the profile) and the same formulas.

The spot check recomputes the week with healthspan_store.compute up to the row's `as_of` from the server's tables and the
SparkyFitness log; section 3.4: body age 0.1 year, pace 0.01, contributor keys exact (the contributors' own numbers and
texts are not compared: recompute takes the phone's lists when their keys agree, as stress takes the hours). The phone has
no Hevy import and no training plan (the plan only labels the strength item), so their weeks may differ: the check compares
the newest uploaded week only.
Stored (primary only): the week's healthspan_weekly row upserted, origin 'app' + bundle_version (migration 1030).
Pull: record kind `healthspan` of the `records` stream (whole-week replace), so a reinstalled phone gets the rows back.
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta

import psycopg

from ...i18n import L
from ...insights import healthspan_store as HSS
from .. import bodies

KIND = "healthspan"
PACKAGE = "coach"
ALGO_KEY = "coach"
# section 3.4: body age 0.1 year (shown with one decimal; both sides run the same algorithm, goldens within 1e-9); delta
# years the same; pace of aging 0.01 (shown with two decimals); the chronological age is exact arithmetic (0.01)
TOLERANCE = {"body_age": 0.1, "delta_years": 0.1, "pace_of_aging": 0.01, "pace_raw": 0.01, "chrono_age": 0.01}
MAX_ITEMS = 20               # contributors / insufficient per week (the algorithm has at most 9 keys)
MAX_JSON = 20_000            # characters of one week's lists (texts included)

RECORD_KIND = "healthspan"
RECORD_TABLE = "healthspan_weekly"
RECORD_DAYCOL = "week_start"
record_body = bodies._simple("healthspan_weekly", "week_start", "week_start")


def _num(v, field: str, nullable: bool = True, lo: float | None = None, hi: float | None = None):
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
            or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(L(f"{field} 应该是范围内的数字", f"{field} must be a number in range"))
    return v


def _items(xs, field: str) -> list[dict]:
    if not isinstance(xs, list) or len(xs) > MAX_ITEMS or any(not isinstance(x, dict) for x in xs) \
            or any(x.get("key") not in HSS.LABELS for x in xs):
        raise ValueError(L(f"{field} 格式不对", f"Bad {field}"))
    return xs


def validate(day: date, rows: list[dict]) -> list[dict]:
    if day.weekday() != 0:
        raise ValueError(L("body age 按周上传：日期是那一周的星期一", "Body age is uploaded by week: the date is its Monday"))
    if len(rows) > 1:
        raise ValueError(L("一周只有一行身体年龄", "One body-age row per week"))
    out = []
    for r in rows:
        try:
            as_of = date.fromisoformat(str(r.get("as_of")))
        except ValueError:
            raise ValueError(L("as_of 应该是日期", "as_of must be a date")) from None
        if not day <= as_of <= day + timedelta(days=6):
            raise ValueError(L("as_of 不在这一周", "as_of is not in this week"))
        weeks = r.get("pace_window_weeks")
        if weeks is not None and (isinstance(weeks, bool) or not isinstance(weeks, int) or not 0 <= weeks <= 1000):
            raise ValueError(L("pace_window_weeks 应该是非负整数", "pace_window_weeks must be a non-negative integer"))
        row = {"as_of": as_of, "chrono_age": _num(r.get("chrono_age"), "chrono_age", False, 0, 130),
               "body_age": _num(r.get("body_age"), "body_age", True, 0, 200),
               "delta_years": _num(r.get("delta_years"), "delta_years", True, -100, 100),
               "pace_of_aging": _num(r.get("pace_of_aging"), "pace_of_aging", True, -10, 10),
               "pace_raw": _num(r.get("pace_raw"), "pace_raw"), "pace_window_weeks": weeks,
               "contributors": _items(r.get("contributors") or [], "contributors"),
               "insufficient": _items(r.get("insufficient") or [], "insufficient")}
        if len(json.dumps([row["contributors"], row["insufficient"]], ensure_ascii=False, default=str)) > MAX_JSON:
            raise ValueError(L("这一周的数据太大", "This week's row is too large"))
        out.append(row)
    return out


def _keys(items: list[dict]) -> list[str]:
    return sorted(x["key"] for x in items or [])


def recompute(conn: psycopg.Connection, uid: int, day: date, uploaded: dict[date, list[dict]]) -> list[dict] | None:
    app = (uploaded.get(day) or [None])[0]
    as_of = app["as_of"] if app is not None else day + timedelta(days=6)
    rows = [r for r in HSS.compute(conn, uid, today=as_of) if r["week_start"] == day]
    if not rows:
        return None   # no scoring origin / birth date on the server (yet), or the week is before it: nothing to compare
    r = rows[0]
    server = validate(day, [{k: (v.isoformat() if k == "as_of" else v) for k, v in r.items()
                             if k not in ("week_start", "notes")}])[0]
    server["contributors"], server["insufficient"] = _dumped(server["contributors"]), _dumped(server["insufficient"])
    if app is not None:
        for f in ("contributors", "insufficient"):
            if _keys(app[f]) == _keys(server[f]):
                server[f] = app[f]              # contributor keys exact; their numbers and texts are not compared
    return [server]


def _dumped(items: list[dict]) -> list[dict]:
    """As the phone sends them (dates as strings)."""
    return json.loads(json.dumps(items, default=str, ensure_ascii=False))


def write(conn: psycopg.Connection, uid: int, days: dict[date, list[dict]], bundle_version: str | None) -> int:
    rows = [{**r, "week_start": d} for d, rs in days.items() for r in rs]
    for d, rs in days.items():
        if not rs:   # an empty week: the phone has no body age for it
            conn.execute("DELETE FROM healthspan_weekly WHERE user_id = %s AND week_start = %s", (uid, d))
    if rows:
        HSS.write(conn, uid, rows, only=True, origin="app", bundle_version=bundle_version)
    return len(rows)
