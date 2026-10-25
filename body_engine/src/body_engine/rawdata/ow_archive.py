"""Apple sync engine, step S6 (docs/apple-sync-engine.md §5.4, risk 1 of §8; issue #219): Open Wearables' history kept
locally, so a user in the app mode never needs OW again and no score loses its history when OW goes away.

What scoring reads from OW at run time, and where it lives after `body-engine ow-archive --user-id N`:
- raw heart rate, HRV (SDNN), respiratory rate, SpO2, wrist temperature, steps: already copied into ow_sample_cache by
  importers/ow_cache.py (migration 770; the nightly full recompute refreshes it from OW). The archive makes sure the copy
  covers [scoring start − HRMAX_WINDOW_DAYS − stress baseline, now] by pulling every 60-day window from OW again (newest
  first, so each window joins the copy's one contiguous range), and records the range in ow_archive. Reusing the copy
  instead of moving the samples into hk_samples(origin 'ow') as §5.4 first planned: the copy already holds exactly what
  OW returns (sources, priorities, XML flags), OW's samples have no end time and XML ones no UUID, and the readers'
  merge by time stamp (scores/inputs._Merger) puts the app's samples first anyway; no data is stored twice.
- sleep stages (scores/inputs.load_ow, the Today page's stage chart): pulled once over the whole range through the OW
  import's own importers/ow.sleep_segments and stored in ow_archive_segments.
- workout windows (insights/stress) and workout heart rate (training/importer.enrich): from the workouts table
  (source 'ow', already imported) and the copy's heart rate.
- daily values, sleep sessions and workouts imported from OW stay where they are (source 'ow'), forever.
Once a user has an ow_archive row and is in the app mode (rawdata/engine.mode), scores/inputs, insights/stress,
training/importer.enrich and web/views read the archive instead of asking OW; in the ow / shadow modes the archive is
ignored. Idempotent: running it again (while OW still runs) refreshes the copy and the stages; it commits per window,
so an interrupted run is simply run again. Nothing is deleted from OW.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ..importers import ow as ow_imp, ow_cache
from ..importers.common import Segment
from . import params as P

UTC = timezone.utc
STRESS_BASELINE_DAYS = 14   # insights/stress.BASELINE_DAYS is read at run time; this is only the archive's margin


def get(conn: psycopg.Connection, user_id: int) -> dict | None:
    return conn.execute("SELECT * FROM ow_archive WHERE user_id = %s", (user_id,)).fetchone()


def reads_archive(conn: psycopg.Connection, user_id: int) -> bool:
    """True when this user's readers take OW's history from the archive (app mode and archived)."""
    from . import samples as S
    return S.reads_app(conn, user_id) and get(conn, user_id) is not None


def _range(conn: psycopg.Connection, user_id: int, tz: ZoneInfo, now: datetime) -> tuple[datetime, datetime] | None:
    from ..insights import stress
    from ..scores import engine as SE, params as SP
    origin = SE.scoring_origin(conn, user_id)
    if origin is None:
        return None
    first = origin - timedelta(days=SP.HRMAX_WINDOW_DAYS + max(stress.BASELINE_DAYS, STRESS_BASELINE_DAYS) + 1)
    return datetime.combine(first, time.min, tz), now


def archive(conn: psycopg.Connection, client, user_id: int, now: datetime | None = None) -> dict:
    """Copy OW's history of this user into the local archive (see the module doc). Commits per window."""
    user = conn.execute("SELECT timezone, ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None or not user["ow_user_id"]:
        raise ValueError(f"用户 {user_id} 没有对应的 OW user，没有可存档的历史")
    tz, ow_user = ZoneInfo(user["timezone"]), user["ow_user_id"]
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    span = _range(conn, user_id, tz, now)
    if span is None:
        raise ValueError(f"用户 {user_id} 还没有手表数据，没有可存档的历史")
    lo, hi = span
    conn.commit()
    windows = 0
    b = hi
    while b > lo:   # newest first: every window touches the copy's range, which only grows backwards
        a = max(lo, b - P.ARCHIVE_WINDOW)
        got = ow_cache.fetch(client, ow_user, ow_cache.CACHE_TYPES, a, b)
        with conn.transaction():
            ow_cache.store(conn, user_id, ow_user, got, a, b, now=now)
        conn.commit()
        windows += 1
        b = a
    sessions = list(client.paginate(f"/users/{ow_user}/events/sleep", include="stages", limit=100,
                                    start_date=(lo.date() - timedelta(days=1)).isoformat(),
                                    end_date=(hi.astimezone(tz).date() + timedelta(days=1)).isoformat()))
    segments = ow_imp.sleep_segments(sessions, tz)
    counts = {r["type"]: r["n"] for r in conn.execute(
        """SELECT type, sum(cardinality(ts))::int AS n FROM ow_sample_cache WHERE user_id = %s AND day >= %s
           GROUP BY type""", (user_id, lo.astimezone(UTC).date()))}
    with conn.transaction():
        conn.execute("DELETE FROM ow_archive_segments WHERE user_id = %s", (user_id,))
        if segments:
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO ow_archive_segments (user_id, start_at, end_at, stage, source)
                                   VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                                [(user_id, s.start, s.end, s.stage, s.device or "") for s in segments])
        conn.execute("""INSERT INTO ow_archive (user_id, ow_user_id, covered_from, covered_to, samples, nights, segments)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (user_id) DO UPDATE SET ow_user_id = EXCLUDED.ow_user_id,
                          covered_from = EXCLUDED.covered_from, covered_to = EXCLUDED.covered_to,
                          samples = EXCLUDED.samples, nights = EXCLUDED.nights, segments = EXCLUDED.segments,
                          archived_at = now()""",
                     (user_id, ow_user, lo, hi, Jsonb(counts), len(sessions), len(segments)))
    conn.commit()
    return {"user_id": user_id, "covered_from": lo, "covered_to": hi, "windows": windows, "samples": counts,
            "nights": len(sessions), "segments": len(segments)}


def check(conn: psycopg.Connection, user_id: int) -> dict:
    """Is the archive complete for scoring? The copy's range per type must cover the archive's range."""
    a = get(conn, user_id)
    if a is None:
        return {"archived": False}
    ranges = {r["type"]: (r["covered_from"], r["covered_to"]) for r in conn.execute(
        "SELECT type, covered_from, covered_to FROM ow_sample_cache_range WHERE user_id = %s", (user_id,))}
    gaps = sorted(t for t in ow_cache.CACHE_TYPES
                  if t not in ranges or ranges[t][0] > a["covered_from"] or ranges[t][1] < a["covered_to"])
    return {"archived": True, "covered_from": a["covered_from"], "covered_to": a["covered_to"], "gaps": gaps,
            "complete": not gaps, "samples": a["samples"], "nights": a["nights"], "segments": a["segments"]}


# ---------------------------------------------------------------- readers (no OW)
def samples(conn: psycopg.Connection, user_id: int, types, lo: datetime, hi: datetime) -> dict[str, list[ow_imp.Sample]]:
    """OW's samples of `types` in [lo, hi) from the copy, by OW series code, in time order (as ow_cache.samples)."""
    return ow_cache._read(conn, user_id, set(types) & ow_cache.CACHE_TYPES, lo, hi)


def sleep_segments(conn: psycopg.Connection, user_id: int, lo: datetime, hi: datetime) -> list[Segment]:
    """The archived stage segments overlapping [lo, hi)."""
    return [Segment(r["start_at"], r["end_at"], r["stage"], r["source"] or None) for r in conn.execute(
        """SELECT start_at, end_at, stage, source FROM ow_archive_segments WHERE user_id = %s AND start_at < %s
           AND end_at > %s ORDER BY start_at""", (user_id, hi, lo))]


def workout_windows(conn: psycopg.Connection, user_id: int, lo: datetime, hi: datetime) -> list[tuple[datetime, datetime]]:
    """(start, end) of the OW workouts imported into the workouts table, as OW's /events/workouts gave them."""
    return [(r["start_at"], r["end_at"]) for r in conn.execute(
        """SELECT start_at, end_at FROM workouts WHERE user_id = %s AND source = 'ow' AND start_at >= %s AND start_at < %s
           ORDER BY start_at""", (user_id, lo, hi))]


def window_samples(conn: psycopg.Connection, user_id: int, start: datetime, end: datetime) -> dict[str, list]:
    """training/importer.window_samples from the copy: heart rate only (OW's energy samples were never copied; an OW
    workout keeps the active energy computed while OW ran)."""
    got = samples(conn, user_id, ["heart_rate"], start, end + timedelta(microseconds=1))
    return {code: ow_imp.drop_xml_duplicates(xs) for code, xs in got.items()}
