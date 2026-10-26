"""Data gaps (FB-18): per user, the health data types that used to arrive regularly through a live way in and stopped.

Why: a way in can stop for one type while everything else keeps flowing (a HealthKit type the sync never reads, a
permission that was never granted, an importer that drops a type), and nobody notices until a chart on the Today page
stays empty. `body-engine data-gaps [--json]` lists them; the Ali doctor shows the core ones as ⚠.

For every user and every data-status kind (sync/data_status.kinds(extended=True): the same types and tables the sync
center shows, including the ones the app reads itself) the days with data are read per source for the last
GAP_GIVE_UP_DAYS + GAP_LOOKBACK_DAYS days (user's local dates; daily_metric_sources for the per-day kinds, the event
tables for sleep / workouts / beat-to-beat series / water). Only **live** days count (thresholds in sync/params.py):
- a history import (GAP_HISTORY_SOURCES: the Apple Health export) is not live;
- for the kinds the app reads itself (channel "app": wrist temperature, stand time, exercise minutes, daylight ...) an
  Open Wearables value is not live either: the OW iOS SDK cannot read those types, so its values come from an export.xml
  uploaded into OW.
A kind is a gap when its newest live day is more than max(GAP_DAYS, the kind's STALE_AFTER_DAYS) days before today (today
does not count), but not more than GAP_GIVE_UP_DAYS, and it had live data on at least GAP_MIN_PRIOR_DAYS of the
GAP_LOOKBACK_DAYS ending on that day (it was regular). Kinds that are never stale (ECG) are skipped.
- `gaps`: users with a gap in a core kind (GAP_CORE_KINDS), only the core kinds; `all` = every regular core kind is in a
  gap (the phone stopped syncing, or the watch was not worn). These are the doctor's ⚠.
- `info`: per user, gaps in the other kinds (`aux`) and the kinds whose data came only from a history import, never from
  a live source (`history_only`, 「只来自历史导入」): information, not a warning.
Read only, user ids and dates only (no values).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from . import data_status as DS, params as P


def is_live(kind: DS.Kind, source: str) -> bool:
    """Whether a source of this kind is a live way in (see the module docstring)."""
    if source in P.GAP_HISTORY_SOURCES:
        return False
    return not (kind.channel == "app" and kind.metrics and source == "ow")


def _sources(conn: psycopg.Connection, user_id: int, tz: str, since: date) -> dict[str, dict[str, set[date]]]:
    """kind key -> source -> local dates with data since `since` (a source seen only before maps to an empty set)."""
    kinds = DS.kinds(extended=True)
    metric_kind = {m: k.key for k in kinds for m in k.metrics}
    out: dict[str, dict[str, set[date]]] = defaultdict(lambda: defaultdict(set))
    for r in conn.execute("""SELECT metric, source, array_agg(DISTINCT date) FILTER (WHERE date >= %s) AS dates
                             FROM daily_metric_sources WHERE user_id = %s AND metric = ANY(%s) GROUP BY metric, source""",
                          (since, user_id, list(metric_kind))):
        out[metric_kind[r["metric"]]][r["source"]] |= set(r["dates"] or ())
    keys = {k.key for k in kinds}
    sql = " UNION ALL ".join(
        f"SELECT '{key}' AS key, {s} AS src, array_agg(DISTINCT {d}) FILTER (WHERE {d} >= %(since)s) AS dates "
        f"FROM {table} WHERE user_id = %(u)s AND {cond} GROUP BY {s}"
        for key, table, d, _t, s, cond in DS.EVENT_SPECS if key in keys)
    for r in conn.execute(sql, {"u": user_id, "tz": tz, "since": since}):
        out[r["key"]][r["src"] or ""] |= set(r["dates"] or ())
    return out


def judge(days: set[date], today: date, stale_after: int) -> dict | None:
    """{last_date, days, prior_days} when these (live) days make a gap, else None (pure; see the module docstring)."""
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


def user_gaps(conn: psycopg.Connection, user_id: int, tz: str, today: date | None = None) -> dict:
    """{user_id, all, core: [...], aux: [...], history_only: [...]}; entries {key, label, last_date[, days, prior_days]}."""
    today = today or datetime.now(ZoneInfo(tz)).date()
    by_kind = _sources(conn, user_id, tz, today - timedelta(days=P.GAP_GIVE_UP_DAYS + P.GAP_LOOKBACK_DAYS))
    out = {"user_id": user_id, "all": False, "core": [], "aux": [], "history_only": []}
    core_flowing = False
    for k in DS.kinds(extended=True):
        stale_after = DS._stale_after(k.key)
        if stale_after is None:
            continue
        srcs = by_kind.get(k.key, {})
        live_srcs = [s for s in srcs if is_live(k, s)]
        if not live_srcs:
            hist = set().union(*srcs.values())
            if hist:      # data in the window, all of it from a history import
                out["history_only"].append({"key": k.key, "label": k.zh, "last_date": max(hist).isoformat()})
            continue
        live = set().union(*(srcs[s] for s in live_srcs))
        core = k.key in P.GAP_CORE_KINDS
        g = judge(live, today, stale_after)
        if g is not None:
            out["core" if core else "aux"].append({"key": k.key, "label": k.zh, **g})
        elif core and sum(1 for d in live if today - timedelta(days=P.GAP_LOOKBACK_DAYS) < d <= today) >= P.GAP_MIN_PRIOR_DAYS:
            core_flowing = True              # a regular core kind that still arrives
    out["all"] = bool(out["core"]) and not core_flowing
    return out


def scan(conn: psycopg.Connection, user_ids: list[int] | None = None, today: date | None = None) -> dict:
    """Every user (or `user_ids`): {checked_users, gap_days, core_kinds, gaps: [{user_id, all, types}], info: [...]}.
    `gaps` holds core kinds only (the doctor's ⚠); `info`: [{user_id, aux, history_only}]."""
    users = conn.execute("SELECT id, coalesce(timezone, 'Australia/Sydney') AS tz FROM users ORDER BY id").fetchall()
    users = [u for u in users if user_ids is None or u["id"] in user_ids]
    gaps, info = [], []
    for u in users:
        g = user_gaps(conn, u["id"], u["tz"], today)
        if g["core"]:
            gaps.append({"user_id": g["user_id"], "all": g["all"], "types": g["core"]})
        if g["aux"] or g["history_only"]:
            info.append({"user_id": g["user_id"], "aux": g["aux"], "history_only": g["history_only"]})
    return {"checked_users": len(users), "gap_days": P.GAP_DAYS, "core_kinds": list(P.GAP_CORE_KINDS),
            "gaps": gaps, "info": info}


def render(res: dict) -> str:
    lines = [f"{res['checked_users']} 个用户，核心类型断流 {len(res['gaps'])} 人"
             + ("" if res["gaps"] else f"（没有连续 {res['gap_days']} 天以上断掉的）")]
    for g in res["gaps"]:
        what = "、".join(f"{t['label']} {t['days']} 天（最后 {t['last_date']}）" for t in g["types"])
        lines.append(f"  ⚠ 用户 {g['user_id']}{'（全部核心类型）' if g['all'] else ''}：{what}")
    for i in res["info"]:
        parts = []
        if i["aux"]:
            parts.append("辅助类型断流 " + "、".join(f"{t['label']} {t['days']} 天" for t in i["aux"]))
        if i["history_only"]:
            parts.append("只来自历史导入 " + "、".join(t["label"] for t in i["history_only"]))
        lines.append(f"  · 用户 {i['user_id']}：" + "；".join(parts))
    return "\n".join(lines)
