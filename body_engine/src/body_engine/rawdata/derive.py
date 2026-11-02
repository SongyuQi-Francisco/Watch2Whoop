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
2.0.0 (docs/ios-native-2.0.md, package M1): the phone derives the same daily values and sleep itself from what it reads
from HealthKit, with packages/openbodyscore-js src/30_derive.js, the twin of `derive_days` below (pure: JSON-like rows in,
rows out; goldens tests/test_js_golden_derive.py). DERIVE_VERSION is the algorithm's version for both (algo/versions.py
key `derive`); the server's own path (derive_day) runs the same pure functions on its tables.
Not derived here: categories other than sleep (mindful sessions: no table yet). The heart rate / TRIMP of app workouts
is training/importer.enrich's, which reads hk_samples in the app mode (rawdata/samples.py, step S3).
"""
from __future__ import annotations

import logging
import time as _time
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from ..importers import common
from ..importers import ow as ow_imp
from . import engine as EN
from . import hk_types as T
from . import params as P
from . import samples as S

log = logging.getLogger(__name__)

# Version of the derivation (daily values, source choice, sleep) shared with the JS bundle (OBS.ALGO_VERSIONS.derive).
# Bump it with any change of daily_values / phase1_rows / common.resolve / the sleep rules, in the same PR as the JS port
# and the goldens (packages/openbodyscore-js README "Changing an algorithm").
DERIVE_VERSION = "derive-1-2026-10-30"

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
    return daily_values(d, types, values)


def daily_values(d: date, types: list[T.EngineType], values: dict[str, list[float]]) -> tuple[list[dict], list[str]]:
    """Pure part of daily_rows: values = {HealthKit type: the day's raw values (avg types, in start / uuid order) or
    statistics buckets (sum types, in time order)} -> (rows, metrics left without data)."""
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


def phase1_rows(d: date, samples: list[tuple[str, str, float]]) -> list[dict]:
    """The phase-1 direct-upload types' daily rows (rawdata/quantities._day_rows, whose SQL groups by source) from the
    day's samples (HealthKit type, source name, stored value), in start / uuid order: per type the sums of each source,
    then `sum` = the largest single-source total, `avg` = mean / min / max of all (quantities.WRITERS)."""
    from . import quantities as Q
    acc: dict[str, dict] = {}
    per: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for hk, src, v in samples:
        k = (hk, src or "")
        if k not in per:
            per[k] = {"s": 0.0, "n": 0, "mn": v, "mx": v}
            order.append(k)
        p = per[k]
        p["s"] += v
        p["n"] += 1
        p["mn"], p["mx"] = min(p["mn"], v), max(p["mx"], v)
    for hk, src in order:
        p = per[(hk, src)]
        a = acc.setdefault(hk, {"s": 0.0, "n": 0, "mn": float("inf"), "mx": float("-inf"), "best": 0.0})
        a["s"] += p["s"]
        a["n"] += p["n"]
        a["mn"] = min(a["mn"], p["mn"])
        a["mx"] = max(a["mx"], p["mx"])
        a["best"] = max(a["best"], p["s"])
    return [Q.WRITERS[T.BY_HK[hk].agg](T.BY_HK[hk], d, a) for hk, a in acc.items()]


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
    out.sort(key=lambda s: datetime.fromisoformat(s["start_time"]))   # by instant (the strings' offsets may differ)
    return out


def night_window(wake_dates, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[lo, hi) of the stage samples (by start) that hold every night waking on these dates."""
    return (_midnight(min(wake_dates) - timedelta(days=P.SLEEP_DAYS_BEFORE), tz),
            _midnight(max(wake_dates) + timedelta(days=P.SLEEP_DAYS_AFTER), tz))


def nights_of(rows: list[dict], wake_dates, tz: ZoneInfo) -> list[common.SleepSession]:
    """Pure part of nights: the stage samples of night_window (ow_sessions' rows) -> the sessions waking on these dates."""
    return [n for n in common.sessionize(ow_imp.sleep_segments(ow_sessions(rows), tz)) if n.wake_date in wake_dates]


def nights(conn: psycopg.Connection, user_id: int, wake_dates: set[date], tz: ZoneInfo) -> list[common.SleepSession]:
    """The sleep sessions waking on these dates, from the stored stage samples (no writes; the comparison can use it)."""
    if not wake_dates:
        return []
    return nights_of(S.stage_rows(conn, user_id, *night_window(wake_dates, tz)), wake_dates, tz)


def session_rows(sessions: list[common.SleepSession]) -> list[dict]:
    """Sessions of one wake date as sleep_sessions stores them (common.replace_sleep), UTC seconds, with their stage
    segments; is_main = the longest asleep, the first of equals (replace_sleep's ORDER BY asleep_min DESC, id)."""
    main = max(range(len(sessions)), key=lambda i: sessions[i].asleep_min) if sessions else -1
    return [{"start": s.start.timestamp(), "end": s.end.timestamp(), "asleep_min": s.asleep_min,
             "core_min": round(s.minutes["core"]), "deep_min": round(s.minutes["deep"]), "rem_min": round(s.minutes["rem"]),
             "awake_min": round(s.minutes["awake"]), "unspecified_min": round(s.minutes["unspecified"]), "is_main": i == main,
             "device": s.device, "segments": [[g.start.timestamp(), g.end.timestamp(), g.stage] for g in s.segments]}
            for i, s in enumerate(sessions)]


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


# ---------------------------------------------------------------- 2.0.0 M1: the phone's derivation (src/30_derive.js)
def _instant(x) -> datetime:
    return x if isinstance(x, datetime) else datetime.fromtimestamp(x, timezone.utc)


def ingest_rows(samples: list[dict], buckets: list[dict], tz: ZoneInfo,
                hk_units: bool = True) -> tuple[list[dict], list[dict]]:
    """What rawdata/engine keeps of the phone's HealthKit rows: samples {uuid, type, start, end (UTC seconds), value,
    source, source_id, device_type, device_name} and 15-minute buckets {type, start, value}. hk_units: the values are in
    the registry's HealthKit unit and get its factor (engine._rows / _stats); then samples outside the plausible range,
    ending before they start or longer than engine.MAX_SAMPLE_SPAN are dropped, and zero buckets left out. Samples get
    `day` (local date of the start); samples are sorted by (start, uuid), buckets by (type, start)."""
    from .engine import MAX_SAMPLE_SPAN
    out_s, out_b = [], []
    for r in samples:
        t = T.ENGINE_BY_HK.get(r["type"])
        if t is None or t.kind not in ("quantity", "category"):
            continue
        v = float(r["value"]) * (t.factor if hk_units else 1.0)
        a, b = _instant(r["start"]), _instant(r["end"])
        if not (t.lo <= v <= t.hi) or b < a or b - a > MAX_SAMPLE_SPAN:
            continue
        out_s.append({**r, "start_at": a, "end_at": b, "value": v, "day": a.astimezone(tz).date(),
                      "source": r.get("source") or "", "name": r.get("source") or None})
    for r in buckets:
        t = T.ENGINE_BY_HK.get(r["type"])
        if t is None or not t.stats:
            continue
        v = float(r["value"]) * (t.factor if hk_units else 1.0)
        if v == 0 or not t.lo <= v <= t.hi:
            continue
        out_b.append({"type": t.hk, "start_at": _instant(r["start"]), "value": v})
    out_s.sort(key=lambda r: (r["start_at"], r.get("uuid") or ""))
    out_b.sort(key=lambda r: (r["type"], r["start_at"]))
    return out_s, out_b


def derive_days(src: dict) -> dict:
    """The phone's derivation of some days from its own HealthKit rows (the twin of src/30_derive.js `deriveDays`):
    src = {tz, today, days: [YYYY-MM-DD], wake_dates: [YYYY-MM-DD], samples: [...], buckets: [...] (ingest_rows),
           units: "hk" (default) | "stored", other: [daily_metric_sources rows of the other sources: date, metric, source,
           value, unit, agg, n, min_value, max_value, flags], app_history: [the phone's own 'app' rows of earlier days,
           for the basal reference], workout_kcal: {date: active kcal of that day's workouts}}
    -> {algo_version, daily: {day: {sources: the 'app' rows, chosen: the day's value per metric (common.resolve)}},
        sleep: {wake date: session_rows}}.
    As the server in the sync mode `app`: the app's values take part in the choice (common.SOURCE_PRIORITY)."""
    tz = ZoneInfo(src["tz"])
    today = date.fromisoformat(src["today"])
    samples, buckets = ingest_rows(src.get("samples") or [], src.get("buckets") or [], tz,
                                   (src.get("units") or "hk") == "hk")
    days = sorted({date.fromisoformat(d) for d in src.get("days") or []})
    qtypes = [t for t in T.ENGINE_TYPES if t.kind == "quantity" and t.hk not in T.BY_HK]
    app: dict[date, list[dict]] = {}
    for d in days:
        values: dict[str, list[float]] = defaultdict(list)
        mine = [r for r in samples if r["day"] == d]
        for r in sorted(mine, key=lambda r: (r["type"], r["start_at"], r.get("uuid") or "")):
            if T.ENGINE_BY_HK[r["type"]].agg == "avg":   # raw samples count for averages; sums come from the buckets
                values[r["type"]].append(r["value"])
        lo, hi = _midnight(d, tz), _midnight(d + timedelta(days=1), tz)
        for b in buckets:
            if lo <= b["start_at"] < hi:
                values[b["type"]].append(b["value"])
        rows, _ = daily_values(d, qtypes, values)
        rows += phase1_rows(d, [(r["type"], r["source"], r["value"]) for r in mine if r["type"] in T.BY_HK])
        app[d] = sorted(({k: v for k, v in r.items() if k != "date"} | {"source": SOURCE, "flags": {}} for r in rows),
                        key=lambda r: r["metric"])

    def cand(r: dict, d: date | None = None) -> dict:
        day = d or (date.fromisoformat(r["date"]) if isinstance(r["date"], str) else r["date"])
        return {**r, "date": day, "flags": dict(r.get("flags") or {})}

    other = [cand(r) for r in src.get("other") or [] if r["source"] != SOURCE]
    history = [c for c in (cand(r) for r in src.get("app_history") or [] if r["source"] == SOURCE) if c["date"] not in app]
    new = [cand(r, d) for d in days for r in app[d]]
    per_day: dict[date, float] = {}
    for r in other + history + new:
        if r["metric"] == "basal_energy_burned" and r["value"] > 0 and "hourly_sum" not in r["flags"]:
            per_day[r["date"]] = max(per_day.get(r["date"], r["value"]), r["value"])
    wk = {date.fromisoformat(k): v for k, v in (src.get("workout_kcal") or {}).items() if v is not None}
    daily = {}
    if days:
        want = set(days)
        chosen: dict[date, list[dict]] = defaultdict(list)
        for win, flags in common.resolve([r for r in other if r["date"] in want] + new,
                                         common.basal_reference(per_day, days[0], days[-1]), wk, today):
            chosen[win["date"]].append({k: win[k] for k in ("metric", "source", "value", "unit", "agg", "n", "min_value",
                                                            "max_value")} | {"flags": flags})
        for d in days:
            daily[d.isoformat()] = {"sources": app[d], "chosen": sorted(chosen.get(d, []), key=lambda r: r["metric"])}
    sleep = {}
    stage = [r for r in samples if r["type"] == SLEEP]
    for w in sorted({date.fromisoformat(d) for d in src.get("wake_dates") or []}):
        lo, hi = night_window({w}, tz)
        sleep[w.isoformat()] = session_rows(nights_of([r for r in stage if lo <= r["start_at"] < hi], {w}, tz))
    return {"algo_version": DERIVE_VERSION, "daily": daily, "sleep": sleep}


# ---------------------------------------------------------------- one day, one user, everyone
def derive_day(conn: psycopg.Connection, user_id: int, d: date, types: set[str], tz: ZoneInfo, mode: str,
               today: date, phone: bool = False) -> dict:
    """Derive one dirty day (in the caller's transaction). phone: the package `derive` is primary for the user and the
    phone derives the day's values and sleep itself (2.0.0 S9, nativesync/primary.py): only the workouts are imported
    (the phone's `workouts` upload enriches the server's rows), nothing of daily_metric_sources / daily_metrics /
    sleep_sessions is touched."""
    out = {"daily": 0, "nights": 0, "workouts": 0, "workouts_deleted": 0}
    qtypes = derived_types(types)
    if qtypes and not phone:
        rows, empty = daily_rows(conn, user_id, d, qtypes, tz)
        out["daily"] = _write_daily(conn, user_id, d, rows, empty, mode, today)
    if SLEEP in types and mode == "app" and not phone:
        out["nights"] = _write_sleep(conn, user_id, {d, d + timedelta(days=1)}, tz)
    if WORKOUT in types:
        w = _write_workouts(conn, user_id, d, tz, mode)
        out["workouts"], out["workouts_deleted"] = w["workouts"], w["workouts_deleted"]
        if mode == "app" and not phone:   # the day's workouts decide whether its active energy is complete (common.incomplete_days)
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
    from ..nativesync import primary
    phone_primary = primary.server_primary(conn, user_id, "derive")   # 2.0.0 S9: the phone derives this user's days
    if phone_primary:
        out.update(phone_primary=True, phone_days=0, waiting=0, fallback_days=0)
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
                row = conn.execute("SELECT types, queued_at FROM hk_dirty_days WHERE user_id = %s AND day = %s FOR UPDATE",
                                   (user_id, d)).fetchone()
                if row is None:   # derived meanwhile by another run
                    continue
                plan = (primary.derive_plan(conn, user_id, d, set(row["types"]), row["queued_at"], today)
                        if phone_primary else "server")
                if phone_primary:
                    out[{"phone": "phone_days", "wait": "waiting", "server": "fallback_days"}[plan]] += 1
                    if plan == "server":   # the phone did not upload this day in time: the server derives it again
                        primary.fallback(conn, user_id, "derive", "hk-derive-run", d)
                res = derive_day(conn, user_id, d, set(row["types"]), tz, mode, today, phone=plan != "server")
                if plan != "wait":   # waiting days stay queued for the next run (the phone may still upload them)
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
