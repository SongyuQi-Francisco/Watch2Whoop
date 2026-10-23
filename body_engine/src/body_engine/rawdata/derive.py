"""Apple sync engine, server step S2 (docs/apple-sync-engine.md §3.2.3–3.2.5; flag apple_sync_engine): daily values, sleep
and workouts derived from what the app's sync engine uploaded (hk_samples, hk_stats; rawdata/engine.py), with the Open
Wearables import's definitions, into the existing tables under source 'app'.

Queue: hk_dirty_days (user, local day, HealthKit types), filled by rawdata/engine.write. `body-engine hk-derive-run` (and
sync-refresh-run before a score recompute, sync/refresh.run_scores) takes a user's days in date order, one transaction
per day (row-locked, so an upload marking the day again waits and queues it anew): derive the day, delete its queue row.
Every step recomputes whole days from the stored samples, so a day derived twice gives the same rows (idempotent).

What a dirty day d (the user's local date of a sample's start, like every importer) derives:
- daily values of the registry's quantity types (rawdata/hk_types.ENGINE_TYPES) -> daily_metric_sources(source='app'):
  - `avg` types: mean / min / max / count of all the day's raw samples (every source), as importers/ow.daily_rows;
  - `sum` types: the sum of the day's 15-minute statistics buckets (hk_stats: Apple's own de-duplication of iPhone +
    watch, D1; the OW import approximates the same HealthKit rule from start times only, importers/ow.dedupe_cumulative);
  - a type left without data that day loses its 'app' row.
  The phase-1 direct-upload types (rawdata/hk_types.HK_TYPES) are not touched: rawdata/quantities.rebuild_days remade
  them at upload time, with their own definitions and their own place in resolve_daily (unchanged by the sync mode).
- sleep, in the app mode only: the nights waking on d and d + 1. The stage samples (HKCategoryValueSleepAnalysis), per
  HealthKit source, go through Open Wearables' session rule (ow_sessions: a gap over 2 hours starts a new session, then
  its stage clean-up), then through the OW import's own functions: importers/ow.sleep_segments (a session overlapping a
  higher-priority one is dropped: watch > phone > other) and importers/common.sessionize -> replace_sleep('app').
- workouts: workouts(source='app', external_id = HealthKit UUID) through training/importer.upsert (the user's corrections
  apply; parse_app: OW's type mapping, total = active + basal energy), then refresh_duplicates. App workouts whose sample
  is gone (deleted in Health, a range re-sync) are deleted.

Sync mode (rawdata/engine.mode; D4):
- shadow (and ow: the flag turned off after uploads): stored for the comparison (S4) and invisible. The daily values take
  no part in resolve_daily and daily_metrics is not re-chosen; no sleep_sessions are written (replace_sleep keeps one
  source per day; the comparison reads hk_samples); app workouts are stored with excluded = 'shadow' (not shown, no
  duplicates, in no total). No value a user sees and no score changes.
- app (after the cut-over, S5): the values take part (app above ow, importers/common.SOURCE_PRIORITY), daily_metrics is
  re-chosen, the sleep is written, app workouts rank first among the duplicates, and the score recompute is queued from
  the earliest derived day (sync/refresh.queue_scores; sync-refresh-run does it).
Not derived here: categories other than sleep (mindful sessions: no table yet), and the heart rate / TRIMP of app
workouts (training/importer.enrich still reads OW; step S3 moves it to hk_samples).
"""
from __future__ import annotations

import logging
import time as _time
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..importers import common
from ..importers import ow as ow_imp
from . import engine as EN
from . import hk_types as T
from . import params as P

log = logging.getLogger(__name__)

SOURCE = common.APP_SOURCE
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
WORKOUT = EN.WORKOUT
LOCK_NS = 0x484B                     # advisory lock namespace ("HK") for (namespace, user_id)


def _midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time.min, tz)


def derived_types(hks) -> list[T.EngineType]:
    """The quantity types whose daily values this step derives: the registry's, without the phase-1 direct uploads."""
    return [t for hk in sorted(hks) if (t := T.ENGINE_BY_HK.get(hk)) and t.kind == "quantity" and hk not in T.BY_HK]


# ---------------------------------------------------------------- daily values
def daily_rows(conn: psycopg.Connection, user_id: int, d: date, types: list[T.EngineType],
               tz: ZoneInfo) -> tuple[list[dict], list[str]]:
    """(rows for common.upsert_daily, metrics left without data) of day d."""
    values: dict[str, list[float]] = defaultdict(list)
    avg = [t.hk for t in types if t.agg == "avg"]
    if avg:
        for r in conn.execute("""SELECT type, value FROM hk_samples WHERE user_id = %s AND type = ANY(%s) AND local_date = %s
                                 ORDER BY type, start_at, hk_uuid""", (user_id, avg, d)):
            values[r["type"]].append(r["value"])
    sums = [t.hk for t in types if t.agg == "sum"]
    if sums:
        for r in conn.execute("""SELECT type, value FROM hk_stats WHERE user_id = %s AND type = ANY(%s)
                                 AND bucket_start >= %s AND bucket_start < %s ORDER BY type, bucket_start""",
                              (user_id, sums, _midnight(d, tz), _midnight(d + timedelta(days=1), tz))):
            values[r["type"]].append(r["value"])
    rows, empty = [], []
    for t in types:
        xs = values.get(t.hk)
        if not xs:
            empty.append(t.key)
        elif t.agg == "sum":
            rows.append({"date": d, "metric": t.key, "value": round(sum(xs), 4), "unit": t.store_unit, "agg": "sum",
                         "n": len(xs), "min_value": None, "max_value": None})
        else:
            rows.append({"date": d, "metric": t.key, "value": round(sum(xs) / len(xs), 4), "unit": t.store_unit,
                         "agg": "avg", "n": len(xs), "min_value": min(xs), "max_value": max(xs)})
    return rows, empty


def _write_daily(conn, user_id: int, d: date, rows: list[dict], empty: list[str], mode: str, today: date) -> int:
    visible = mode == "app"
    common.upsert_daily(conn, user_id, SOURCE, rows, today, resolve=False)
    if empty:
        conn.execute("DELETE FROM daily_metric_sources WHERE user_id = %s AND date = %s AND metric = ANY(%s) AND source = %s",
                     (user_id, d, empty, SOURCE))
    if visible and (rows or empty):
        common.resolve_daily(conn, user_id, [d], today, mode)
        if empty:   # nothing left from any source: the chosen row goes too (as rawdata/quantities.rebuild_days)
            conn.execute("""DELETE FROM daily_metrics m WHERE m.user_id = %s AND m.date = %s AND m.metric = ANY(%s)
                            AND NOT EXISTS (SELECT 1 FROM daily_metric_sources s WHERE s.user_id = m.user_id
                                            AND s.date = m.date AND s.metric = m.metric)""", (user_id, d, empty))
    return len(rows)


# ---------------------------------------------------------------- sleep
def clean_stages(stages: list[tuple[str, datetime, datetime]]) -> list[tuple[str, datetime, datetime]]:
    """One session's stages as Open Wearables keeps them (_calculate_final_metrics; P.DETAILED_STAGES)."""
    names = {s for s, _, _ in stages}
    if not names & P.ASLEEP_STAGES:
        keep = [("sleeping" if s == "in_bed" else s, a, b) for s, a, b in stages if s != "unknown"]
    elif names & P.DETAILED_STAGES:
        keep = [x for x in stages if x[0] not in ("in_bed", "sleeping", "unknown")]
    else:
        keep = [x for x in stages if x[0] not in ("in_bed", "unknown")]
    out, last_end = [], None
    for s, a, b in sorted(keep, key=lambda x: x[1]):
        if last_end and a < last_end:
            a = last_end
        if a >= b:
            continue
        out.append((s, a, b))
        last_end = b
    return out


def ow_sessions(rows: list[dict]) -> list[dict]:
    """Stage samples (start_at, end_at, value = HKCategoryValueSleepAnalysis, source_id, source, device_type, device_name,
    any order) -> sleep sessions shaped as Open Wearables' /events/sleep?include=stages returns them, built with OW's
    rule per HealthKit source (P.SESSION_GAP, P.SESSION_START; the same sample twice counts once)."""
    by_src: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_src[(r.get("source_id"), r.get("source") or "")].append(r)
    out = []
    for xs in by_src.values():
        seen, sessions, cur = set(), [], None
        for r in sorted(xs, key=lambda r: r["start_at"]):
            stage = P.SLEEP_STAGES.get(int(r["value"]), "unknown")
            k = (r["start_at"], r["end_at"], stage)
            if k in seen:
                continue
            seen.add(k)
            a, b = r["start_at"], r["end_at"]
            if cur is None:
                if stage not in P.SESSION_START:
                    continue
                cur = {"lo": a, "hi": b, "stages": []}
            else:
                if a <= cur["hi"] and b >= cur["lo"]:
                    gap = timedelta(0)
                elif b <= cur["lo"]:
                    gap = cur["lo"] - b
                else:
                    gap = a - cur["hi"]
                if gap > P.SESSION_GAP:
                    sessions.append(cur)
                    cur = {"lo": a, "hi": b, "stages": []}
            cur["hi"], cur["lo"] = max(cur["hi"], b), min(cur["lo"], a)
            cur["stages"].append((stage, a, b))
        if cur is not None:
            sessions.append(cur)
        first = xs[0]
        src = {"source": first.get("name") or first.get("source") or None, "device_type": first.get("device_type") or None,
               "device_name": first.get("device_name") or first.get("name") or first.get("source") or None}
        for s in sessions:
            stages = clean_stages(s["stages"])
            if stages:
                out.append({"source": src, "start_time": stages[0][1].isoformat(), "sleep_stage_intervals": [
                    {"stage": st, "start_time": a.isoformat(), "end_time": b.isoformat()} for st, a, b in stages]})
    out.sort(key=lambda s: s["start_time"])
    return out


def nights(conn: psycopg.Connection, user_id: int, wake_dates: set[date], tz: ZoneInfo) -> list[common.SleepSession]:
    """The sleep sessions waking on these dates, from the stored stage samples (no writes; the comparison can use it)."""
    if not wake_dates:
        return []
    lo = _midnight(min(wake_dates) - timedelta(days=P.SLEEP_DAYS_BEFORE), tz)
    hi = _midnight(max(wake_dates) + timedelta(days=P.SLEEP_DAYS_AFTER), tz)
    rows = conn.execute("""SELECT h.start_at, h.end_at, h.value, h.source_id, h.source, s.name, s.device_type, s.device_name
                           FROM hk_samples h LEFT JOIN hk_sources s ON s.id = h.source_id
                           WHERE h.user_id = %s AND h.type = %s AND h.start_at >= %s AND h.start_at < %s""",
                        (user_id, SLEEP, lo, hi)).fetchall()
    return [n for n in common.sessionize(ow_imp.sleep_segments(ow_sessions(rows), tz)) if n.wake_date in wake_dates]


def _write_sleep(conn, user_id: int, wake_dates: set[date], tz: ZoneInfo) -> int:
    """App mode: the app's sessions of these dates replace whatever it had there (replace_sleep also clears the lower
    sources of the dates it fills); a date left without app sleep loses its old app sessions."""
    found = nights(conn, user_id, wake_dates, tz)
    common.replace_sleep(conn, user_id, SOURCE, found)
    empty = sorted(wake_dates - {n.wake_date for n in found})
    if empty:
        conn.execute("DELETE FROM sleep_sessions WHERE user_id = %s AND source = %s AND wake_date = ANY(%s)",
                     (user_id, SOURCE, empty))
        conn.execute(
            """UPDATE sleep_sessions s SET is_main = (s.id = m.id)
               FROM (SELECT DISTINCT ON (wake_date) wake_date, id FROM sleep_sessions
                     WHERE user_id = %s AND wake_date = ANY(%s) ORDER BY wake_date, asleep_min DESC, id) m
               WHERE s.user_id = %s AND s.wake_date = m.wake_date""", (user_id, empty, user_id))
    return len(found)


# ---------------------------------------------------------------- workouts
def workout_rows(conn: psycopg.Connection, user_id: int, d: date, tz: ZoneInfo, shadow: bool) -> list[dict]:
    from ..training import importer as TI
    rows = conn.execute("""SELECT h.hk_uuid::text AS uuid, h.start_at, h.end_at, h.value, h.value_text, h.meta, h.tz,
                                  h.source, s.name AS source_name, s.device_type, s.device_name
                           FROM hk_samples h LEFT JOIN hk_sources s ON s.id = h.source_id
                           WHERE h.user_id = %s AND h.type = %s AND h.local_date = %s ORDER BY h.start_at""",
                        (user_id, WORKOUT, d)).fetchall()
    return [TI.parse_app(r, tz, shadow) for r in rows]


def _write_workouts(conn, user_id: int, d: date, tz: ZoneInfo, mode: str) -> dict:
    from ..training import importer as TI
    rows = workout_rows(conn, user_id, d, tz, shadow=mode != "app")
    res = TI.upsert(conn, user_id, rows)
    gone = conn.execute(
        """DELETE FROM workouts w WHERE w.user_id = %s AND w.source = %s AND NOT EXISTS (
               SELECT 1 FROM hk_samples h WHERE h.user_id = w.user_id AND h.type = %s AND h.hk_uuid::text = w.external_id)
           RETURNING id""", (user_id, SOURCE, WORKOUT)).fetchall()
    TI.refresh_duplicates(conn, user_id, mode)
    return {"workouts": len(rows), "workouts_deleted": len(gone), "inserted": res["inserted"], "updated": res["updated"]}


# ---------------------------------------------------------------- one day, one user, everyone
def derive_day(conn: psycopg.Connection, user_id: int, d: date, types: set[str], tz: ZoneInfo, mode: str,
               today: date) -> dict:
    """Derive one dirty day (in the caller's transaction)."""
    out = {"daily": 0, "nights": 0, "workouts": 0, "workouts_deleted": 0}
    qtypes = derived_types(types)
    if qtypes:
        rows, empty = daily_rows(conn, user_id, d, qtypes, tz)
        out["daily"] = _write_daily(conn, user_id, d, rows, empty, mode, today)
    if SLEEP in types and mode == "app":
        out["nights"] = _write_sleep(conn, user_id, {d, d + timedelta(days=1)}, tz)
    if WORKOUT in types:
        w = _write_workouts(conn, user_id, d, tz, mode)
        out["workouts"], out["workouts_deleted"] = w["workouts"], w["workouts_deleted"]
        if mode == "app":   # the day's workouts decide whether its active energy is complete (common.incomplete_days)
            common.resolve_daily(conn, user_id, [d], today, mode)
    return out


def run_user(conn: psycopg.Connection, user_id: int, mode: str | None = None, max_days: int | None = None,
             deadline: float | None = None) -> dict:
    """Derive this user's dirty days, oldest first, one committed transaction per day. Returns counts; busy = another
    process holds the user's derivation lock (the rest stays queued)."""
    queued = [r["day"] for r in conn.execute("SELECT day FROM hk_dirty_days WHERE user_id = %s ORDER BY day", (user_id,))]
    conn.commit()
    if not queued:
        return {"days": 0}
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    mode = mode or EN.mode(conn, user_id)
    today = datetime.now(tz).date()
    out = {"mode": mode, "days": 0, "daily": 0, "nights": 0, "workouts": 0, "workouts_deleted": 0, "left": 0}
    conn.commit()
    first = None
    for i, d in enumerate(queued):
        if (max_days is not None and i >= max_days) or (deadline is not None and _time.monotonic() > deadline):
            out["left"] = len(queued) - i
            break
        try:
            with conn.transaction():
                if not conn.execute("SELECT pg_try_advisory_xact_lock(%s, %s) AS ok", (LOCK_NS, user_id)).fetchone()["ok"]:
                    out.update(busy=True, left=len(queued) - i)
                    break
                row = conn.execute("SELECT types FROM hk_dirty_days WHERE user_id = %s AND day = %s FOR UPDATE",
                                   (user_id, d)).fetchone()
                if row is None:   # derived meanwhile by another run
                    continue
                res = derive_day(conn, user_id, d, set(row["types"]), tz, mode, today)
                conn.execute("DELETE FROM hk_dirty_days WHERE user_id = %s AND day = %s", (user_id, d))
        except Exception as e:  # noqa: BLE001  that day stays queued (rolled back); the later days still go
            log.warning("hk derive of user %s, %s failed: %s", user_id, d, e)
            out.setdefault("errors", []).append({"day": d.isoformat(), "error": str(e)[:300]})
            continue
        out["days"] += 1
        for k in ("daily", "nights", "workouts", "workouts_deleted"):
            out[k] += res[k]
        first = d if first is None else min(first, d)
    if mode == "app" and first is not None:
        from ..sync import refresh
        refresh.queue_scores(conn, user_id, first)
    return out


def run(conn: psycopg.Connection, user_id: int | None = None, max_days: int | None = None,
        budget_s: float = P.TIME_BUDGET_S) -> list[dict]:
    """`body-engine hk-derive-run`: every user with dirty days (or one), within the time budget. Users who have not
    consented (profile/consent.py) are left queued. Nothing queued = nothing done."""
    from ..profile import consent
    deadline = _time.monotonic() + budget_s
    users = [r["user_id"] for r in conn.execute(
        "SELECT DISTINCT user_id FROM hk_dirty_days WHERE %(u)s::int IS NULL OR user_id = %(u)s ORDER BY user_id",
        {"u": user_id})]
    conn.commit()
    out = []
    for uid in users:
        if _time.monotonic() > deadline:
            out.append({"user_id": uid, "skipped": "time_budget"})
            continue
        if consent.gate(conn, uid):
            conn.commit()
            out.append({"user_id": uid, "skipped": "consent"})
            continue
        try:
            out.append({"user_id": uid, **run_user(conn, uid, max_days=max_days, deadline=deadline)})
        except Exception as e:  # noqa: BLE001  one user's failure must not stop the others; the day stays queued
            conn.rollback()
            log.warning("hk-derive-run failed for user %s: %s", uid, e)
            out.append({"user_id": uid, "error": str(e)[:300]})
    return out
