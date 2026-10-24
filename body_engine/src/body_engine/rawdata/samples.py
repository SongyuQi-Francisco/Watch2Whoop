"""Apple sync engine, server step S3 (docs/apple-sync-engine.md §5.4, §6; flag apple_sync_engine): the read layer over what
the app's own sync engine uploaded (hk_samples, hk_stats, hk_sources; rawdata/engine.py), shaped like the Open Wearables
reads it replaces, so the callers keep their own logic:
- samples(): raw quantity samples as importers/ow.Sample keyed by OW's series code (heart_rate, steps, active_energy ...;
  importers/ow.SPECS gives the code of each Body Engine metric), values already in the stored unit the OW import
  converts to (SpO2 0–100, km/h ...). Cumulative types the app sends only as 15-minute statistics buckets (active /
  basal energy, D1) come as one sample per bucket, prorated to the asked window (a bucket half inside counts half);
- sleep_segments(): the stage samples through Open Wearables' session rule (rawdata/derive.ow_sessions) and the OW
  import's own importers/ow.sleep_segments, as a Today-page night or the scores read them;
- workout_windows(): the start / end of every uploaded workout (also the shadow-stored ones), like OW's /events/workouts.

Who reads it (reads_app = the user's sync mode is `app`, rawdata/engine.mode; step S5 turns that mode on):
- scores/inputs.load (compute-scores, hrv-compare), insights/stress.compute, training/importer.enrich (workout heart
  rate, TRIMP, energy split), web/views.night_stages (the Today page's stage chart).
- In the app mode the engine's data comes first and Open Wearables only fills what it lacks (history before the app
  synced, until step S6 moves OW's raw history into hk_samples): the scores' merger keeps the first sample of a time
  stamp, the stage chart / scores take OW's nights only for wake dates the app has none of, stress takes OW's samples
  only for days the app has none of that type (step counts per source would add up otherwise).
- In the ow and shadow modes (every user until S5: flag off = ow, flag on = shadow) nothing here is called, so every
  value a user sees is computed exactly as before.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..importers import ow as ow_imp
from ..importers.common import Segment
from . import engine as EN
from . import hk_types as T

SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
WORKOUT = EN.WORKOUT
BUCKET = timedelta(seconds=EN.BUCKET_S)
SLEEP_PAD = timedelta(days=1)       # stage samples read around a window: a night's session must be whole (OW reads ±1 day)

_OW_BY_METRIC = {spec.metric: code for code, spec in ow_imp.SPECS.items()}
# OW series code -> the engine's quantity type (only the types both carry)
HK_BY_OW: dict[str, T.EngineType] = {_OW_BY_METRIC[t.key]: t for t in T.ENGINE_TYPES
                                     if t.kind == "quantity" and t.key in _OW_BY_METRIC}


def reads_app(conn: psycopg.Connection, user_id: int) -> bool:
    """True when this user's scores, stress, workout heart rate and stage chart read the engine's samples first."""
    return EN.mode(conn, user_id) == "app"


def _priority(device_type: str | None) -> int:
    """importers/ow.parse_sample's priority for a live (non-XML) sample: watch > phone > other."""
    return ow_imp.DEVICE_PRIORITY.get(device_type or "", 2) * 2


def _source_key(device_type: str | None, model: str | None, name: str | None) -> str:
    return f"{device_type or 'unknown'}:{model or ''}:{name or ''}"


def samples(conn: psycopg.Connection, user_id: int, codes, lo: datetime, hi: datetime) -> dict[str, list[ow_imp.Sample]]:
    """Samples of these OW series codes starting in [lo, hi) (statistics buckets: overlapping it, prorated), by code,
    sorted by time. Codes the engine does not read are left out."""
    raw, stats = {}, {}
    for code in codes:
        if (t := HK_BY_OW.get(code)) is None:
            continue
        (raw if t.raw else stats)[t.hk] = code
    out: dict[str, list[ow_imp.Sample]] = defaultdict(list)
    if raw:
        for r in conn.execute(
                """SELECT h.type, h.start_at, h.value, h.source, s.name, s.device_type, s.device_model
                   FROM hk_samples h LEFT JOIN hk_sources s ON s.id = h.source_id
                   WHERE h.user_id = %s AND h.type = ANY(%s) AND h.start_at >= %s AND h.start_at < %s
                   ORDER BY h.start_at, h.hk_uuid""", (user_id, list(raw), lo, hi)):
            out[raw[r["type"]]].append(ow_imp.Sample(
                r["start_at"], r["value"], _source_key(r["device_type"], r["device_model"], r["name"] or r["source"]),
                _priority(r["device_type"])))
    if stats:
        span = BUCKET.total_seconds()
        for r in conn.execute(
                """SELECT type, bucket_start, value FROM hk_stats WHERE user_id = %s AND type = ANY(%s)
                   AND bucket_start > %s AND bucket_start < %s ORDER BY bucket_start""",
                (user_id, list(stats), lo - BUCKET, hi)):
            a, b = max(r["bucket_start"], lo), min(r["bucket_start"] + BUCKET, hi)
            if b > a:   # Apple's buckets are already de-duplicated across devices: one source, top priority
                out[stats[r["type"]]].append(ow_imp.Sample(a, r["value"] * (b - a).total_seconds() / span, "stats", 0))
    return dict(out)


def stage_rows(conn: psycopg.Connection, user_id: int, lo: datetime, hi: datetime) -> list[dict]:
    """The sleep-stage samples starting in [lo, hi), with their source (rawdata/derive.ow_sessions' input)."""
    return conn.execute("""SELECT h.start_at, h.end_at, h.value, h.source_id, h.source, s.name, s.device_type, s.device_name
                           FROM hk_samples h LEFT JOIN hk_sources s ON s.id = h.source_id
                           WHERE h.user_id = %s AND h.type = %s AND h.start_at >= %s AND h.start_at < %s""",
                        (user_id, SLEEP, lo, hi)).fetchall()


def sleep_segments(conn: psycopg.Connection, user_id: int, tz: ZoneInfo, lo: datetime, hi: datetime) -> list[Segment]:
    """Stage segments of the sessions around [lo, hi) (whole sessions: samples from a day before to a day after), the
    way the OW import cuts them (a session overlapping a higher-priority one is dropped: watch > phone > other)."""
    from .derive import ow_sessions
    return ow_imp.sleep_segments(ow_sessions(stage_rows(conn, user_id, lo - SLEEP_PAD, hi + SLEEP_PAD)), tz)


def workout_windows(conn: psycopg.Connection, user_id: int, lo: datetime, hi: datetime) -> list[tuple[datetime, datetime]]:
    """(start, end) of the uploaded workouts starting in [lo, hi), sorted."""
    return [(r["start_at"], r["end_at"]) for r in conn.execute(
        """SELECT start_at, end_at FROM hk_samples WHERE user_id = %s AND type = %s AND start_at >= %s AND start_at < %s
           ORDER BY start_at""", (user_id, WORKOUT, lo, hi))]


def wake_dates(segments: list[Segment], tz: ZoneInfo) -> set[date]:
    """The wake dates (common.WAKE_DAY_SHIFT rule, as scores/inputs) the segments cover."""
    from ..importers.common import WAKE_DAY_SHIFT
    return {(s.end - WAKE_DAY_SHIFT).astimezone(tz).date() for s in segments}
