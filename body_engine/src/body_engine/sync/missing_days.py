"""Missing days (FB-24): the days of the last MISSING_DAYS_WINDOW the server has nothing of a core kind, newest first, with
the kinds missing each day. The app's sync center turns 「缺 N 天」 into this list, with a re-sync per day (sync/resync.py,
`date`) and one for all of them. GET /v1/users/{user}/sync/missing-days (own data only, sync/api.py).

The kinds, their labels and their tables are the data status's (sync/data_status.py KINDS, only MISSING_DAY_KINDS):
daily_metrics, the resolved daily values of every way in (Open Wearables, the app's engine in its app mode, the Shortcut,
an export), and sleep_sessions by wake date. Not missing: today (the day is not over) and the days before the user's first
data of any of these kinds (the account was not syncing yet). Whether the phone has the data at all (the watch was worn)
only the phone knows: the app reads HealthKit for each missing day and shows a day the phone has nothing of either as
「健康里也没有」, not as a sync problem. Read only; dates and kind codes, no values.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L
from . import data_status as DS, params as P


def kinds() -> list[DS.Kind]:
    by_key = {k.key: k for k in DS.KINDS}
    return [by_key[k] for k in P.MISSING_DAY_KINDS]


def days_with_data(conn: psycopg.Connection, user_id: int, kind_list: list[DS.Kind], start: date,
                   end: date) -> dict[str, set[date]]:
    """kind key -> the local dates in [start, end] the server has data of it."""
    metric_kind = {m: k.key for k in kind_list for m in k.metrics}
    out: dict[str, set[date]] = {k.key: set() for k in kind_list}
    for r in conn.execute("""SELECT DISTINCT metric, date FROM daily_metrics WHERE user_id = %s AND metric = ANY(%s)
                             AND date BETWEEN %s AND %s""", (user_id, list(metric_kind), start, end)):
        out[metric_kind[r["metric"]]].add(r["date"])
    if "sleep" in out:
        out["sleep"] |= {r["wake_date"] for r in conn.execute(
            "SELECT DISTINCT wake_date FROM sleep_sessions WHERE user_id = %s AND wake_date BETWEEN %s AND %s",
            (user_id, start, end))}
    return out


def first_day(conn: psycopg.Connection, user_id: int, kind_list: list[DS.Kind]) -> date | None:
    """The user's first day with data of any of these kinds (whole history)."""
    metrics = [m for k in kind_list for m in k.metrics]
    r = conn.execute("""SELECT (SELECT min(date) FROM daily_metrics WHERE user_id = %(u)s AND metric = ANY(%(m)s)) AS m,
                               (SELECT min(wake_date) FROM sleep_sessions WHERE user_id = %(u)s) AS s""",
                     {"u": user_id, "m": metrics}).fetchone()
    s = r["s"] if any(k.key == "sleep" for k in kind_list) else None
    return min((d for d in (r["m"], s) if d is not None), default=None)


def judge(have: dict[str, set[date]], start: date, end: date, first: date | None) -> list[dict]:
    """Pure: [{date, kinds}] newest first, for each day of [max(start, first), end] that lacks a kind (in `have`'s order)."""
    if first is None:
        return []
    out, d = [], end
    while d >= max(start, first):
        missing = [k for k, days in have.items() if d not in days]
        if missing:
            out.append({"date": d.isoformat(), "kinds": missing})
        d -= timedelta(days=1)
    return out


def collect(conn: psycopg.Connection, user_id: int, today: date | None = None) -> dict:
    """{start, end, window_days, since, kinds: [{key, label}], days: [{date, kinds}], count}."""
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    today = today or datetime.now(tz).date()
    end = today - timedelta(days=1)
    start = today - timedelta(days=P.MISSING_DAYS_WINDOW)
    kind_list = kinds()
    first = first_day(conn, user_id, kind_list)
    days = judge(days_with_data(conn, user_id, kind_list, start, end), start, end, first)
    return {"start": start.isoformat(), "end": end.isoformat(), "window_days": P.MISSING_DAYS_WINDOW,
            "since": first.isoformat() if first else None,
            "kinds": [{"key": k.key, "label": L(k.zh, k.en)} for k in kind_list],
            "days": days, "count": len(days)}
