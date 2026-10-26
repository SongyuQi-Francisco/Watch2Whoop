"""Data gaps (FB-18): per user, the health data types that used to arrive regularly and stopped.

Why: a way in can stop for one type while everything else keeps flowing (a HealthKit type the sync never reads, a
permission that was never granted, an importer that drops a type), and nobody notices until a chart on the Today page
stays empty. `body-engine data-gaps [--json]` lists every such type; the Ali doctor shows them as ⚠.

For every user and every data-status kind (sync/data_status.kinds(extended=True): the same types and tables the sync
center shows, including the ones the app reads itself) the days with data are read for the last
GAP_GIVE_UP_DAYS + GAP_LOOKBACK_DAYS days (user's local dates). A kind is a gap when (thresholds in sync/params.py):
- its newest day is more than max(GAP_DAYS, the kind's STALE_AFTER_DAYS) days before today (today does not count: the
  day is not over), but not more than GAP_GIVE_UP_DAYS;
- it had data on at least GAP_MIN_PRIOR_DAYS of the GAP_LOOKBACK_DAYS ending on that newest day (it was regular).
Kinds that are never stale (ECG) are skipped. `all` = every regular kind of the user is in a gap (the phone stopped
syncing, or the watch was not worn), as opposed to single types. Read only, user ids and dates only (no values).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from . import data_status as DS, params as P


def _dates(conn: psycopg.Connection, user_id: int, tz: str, since: date) -> dict[str, set[date]]:
    """kind key -> local dates with data since `since`."""
    kinds = DS.kinds(extended=True)
    metric_kind = {m: k.key for k in kinds for m in k.metrics}
    out: dict[str, set[date]] = {}
    for r in conn.execute("""SELECT DISTINCT metric, date FROM daily_metrics
                             WHERE user_id = %s AND metric = ANY(%s) AND date >= %s""",
                          (user_id, list(metric_kind), since)):
        out.setdefault(metric_kind[r["metric"]], set()).add(r["date"])
    keys = {k.key for k in kinds}
    specs = [s for s in DS.EVENT_SPECS if s[0] in keys]
    sql = " UNION ALL ".join(f"SELECT DISTINCT '{key}' AS key, {d} AS d FROM {table} "
                             f"WHERE user_id = %(u)s AND {cond} AND {d} >= %(since)s"
                             for key, table, d, _t, _s, cond in specs)
    for r in conn.execute(sql, {"u": user_id, "tz": tz, "since": since}):
        out.setdefault(r["key"], set()).add(r["d"])
    return out


def judge(days: set[date], today: date, stale_after: int) -> dict | None:
    """{last_date, days, prior_days} when these days make a gap, else None (pure; see the module docstring)."""
    if not days:
        return None
    last = max(days)
    gap = (today - last).days
    if gap <= max(P.GAP_DAYS, stale_after) or gap > P.GAP_GIVE_UP_DAYS:
        return None
    prior = sum(1 for d in days if last - timedelta(days=P.GAP_LOOKBACK_DAYS) < d <= last)
    if prior < P.GAP_MIN_PRIOR_DAYS:
        return None
    return {"last_date": last.isoformat(), "days": gap, "prior_days": prior}


def user_gaps(conn: psycopg.Connection, user_id: int, tz: str, today: date | None = None) -> dict | None:
    """{user_id, all, types: [{key, label, last_date, days, prior_days}]} for one user, or None without gaps."""
    today = today or datetime.now(ZoneInfo(tz)).date()
    by_kind = _dates(conn, user_id, tz, today - timedelta(days=P.GAP_GIVE_UP_DAYS + P.GAP_LOOKBACK_DAYS))
    flowing, types = False, []
    for k in DS.kinds(extended=True):
        stale_after = DS._stale_after(k.key)
        if stale_after is None:
            continue
        days = by_kind.get(k.key, set())
        g = judge(days, today, stale_after)
        if g is not None:
            types.append({"key": k.key, "label": k.zh, **g})
        elif sum(1 for d in days if today - timedelta(days=P.GAP_LOOKBACK_DAYS) < d <= today) >= P.GAP_MIN_PRIOR_DAYS:
            flowing = True                   # a regular kind that still arrives
    if not types:
        return None
    return {"user_id": user_id, "all": not flowing, "types": types}


def scan(conn: psycopg.Connection, user_ids: list[int] | None = None, today: date | None = None) -> dict:
    """Every user (or `user_ids`): {checked_users, gap_days, gaps: [user_gaps(...)]}."""
    users = conn.execute("SELECT id, coalesce(timezone, 'Australia/Sydney') AS tz FROM users ORDER BY id").fetchall()
    users = [u for u in users if user_ids is None or u["id"] in user_ids]
    gaps = [g for u in users if (g := user_gaps(conn, u["id"], u["tz"], today)) is not None]
    return {"checked_users": len(users), "gap_days": P.GAP_DAYS, "gaps": gaps}


def render(res: dict) -> str:
    if not res["gaps"]:
        return f"{res['checked_users']} 个用户：没有连续 {res['gap_days']} 天以上断掉的数据类型"
    lines = [f"{res['checked_users']} 个用户，{len(res['gaps'])} 个有断掉的数据类型："]
    for g in res["gaps"]:
        what = "、".join(f"{t['label']} {t['days']} 天（最后 {t['last_date']}）" for t in g["types"])
        lines.append(f"  用户 {g['user_id']}{'（全部类型）' if g['all'] else ''}：{what}")
    return "\n".join(lines)
