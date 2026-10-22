"""Which HRV kind scores a user: Apple's SDNN or RMSSD from beat-to-beat series (issue #117). Default: SDNN for everyone.

Rules (thresholds in scores/params.py = openbodyscore params, RMSSD_*):
1. A user is scored with rmssd only if switched on for it (score_settings.rmssd_enabled, set with `body-engine hrv-kind`;
   no row = off) AND either already scored with rmssd or has enough recent beat data: >= RMSSD_MIN_NIGHTS of the last
   RMSSD_COVERAGE_DAYS nights with an RMSSD reading, the newest at most RMSSD_MAX_GAP_DAYS old. Otherwise sdnn.
   Once a user is on rmssd, a gap in uploads does not switch them back: those nights simply have no HRV.
2. Never mixed within one person: a run that writes only part of the history (compute-scores --start, the hourly job)
   keeps the kind of the rows it leaves in place. The kind therefore changes only at a full recompute (the nightly job,
   or compute-scores without --start), which rewrites the whole history with the new kind.
3. Nights without an HRV reading of the chosen kind get no HRV (flag hrv_missing) and so no recovery score; the other
   kind never fills in, and the baseline only uses nights of the chosen kind.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from . import params as P

KINDS = ("sdnn", "rmssd")


def rmssd_enabled(conn: psycopg.Connection, user_id: int) -> bool:
    row = conn.execute("SELECT rmssd_enabled FROM score_settings WHERE user_id = %s", (user_id,)).fetchone()
    return bool(row and row["rmssd_enabled"])


def set_rmssd_enabled(conn: psycopg.Connection, user_id: int, enabled: bool, by: str | None = None) -> None:
    conn.execute("""INSERT INTO score_settings (user_id, rmssd_enabled, updated_by) VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET rmssd_enabled = EXCLUDED.rmssd_enabled, updated_at = now(),
                                                        updated_by = EXCLUDED.updated_by""", (user_id, enabled, by))


def coverage(conn: psycopg.Connection, user_id: int, tz: ZoneInfo, end: date, params=P) -> dict:
    """RMSSD coverage of the nights ending on the RMSSD_COVERAGE_DAYS mornings up to end (local dates)."""
    first = end - timedelta(days=params.RMSSD_COVERAGE_DAYS - 1)
    lo = datetime.combine(first - timedelta(days=1), time(params.RMSSD_NIGHT_HOURS[0]), tz)
    hi = datetime.combine(end, time(params.RMSSD_NIGHT_HOURS[1]), tz)
    nights = set()
    for r in conn.execute("""SELECT start_at FROM heartbeat_series WHERE user_id = %s AND rmssd IS NOT NULL
                             AND start_at >= %s AND start_at < %s""", (user_id, lo, hi)):
        t = r["start_at"].astimezone(tz)
        if t.hour >= params.RMSSD_NIGHT_HOURS[0]:
            nights.add(t.date() + timedelta(days=1))
        elif t.hour < params.RMSSD_NIGHT_HOURS[1]:
            nights.add(t.date())
    last = conn.execute("SELECT max(start_at) AS t FROM heartbeat_series WHERE user_id = %s AND rmssd IS NOT NULL",
                        (user_id,)).fetchone()["t"]
    last_day = last.astimezone(tz).date() if last else None
    nights = {d for d in nights if first <= d <= end}
    return {"nights": len(nights), "window_days": params.RMSSD_COVERAGE_DAYS, "last_reading": last_day,
            "enough": len(nights) >= params.RMSSD_MIN_NIGHTS and last_day is not None
                      and (end - last_day).days <= params.RMSSD_MAX_GAP_DAYS}


def stored_kinds(conn: psycopg.Connection, user_id: int, outside: tuple[date, date] | None = None) -> set[str]:
    """hrv_kind of this user's stored daily_scores rows (only those outside [start, end] when given)."""
    if outside is None:
        rows = conn.execute("SELECT DISTINCT hrv_kind FROM daily_scores WHERE user_id = %s AND hrv_kind IS NOT NULL",
                            (user_id,))
    else:
        rows = conn.execute("""SELECT DISTINCT hrv_kind FROM daily_scores WHERE user_id = %s AND hrv_kind IS NOT NULL
                               AND (date < %s OR date > %s)""", (user_id, *outside))
    return {r["hrv_kind"] for r in rows}


def choose(conn: psycopg.Connection, user_id: int, tz: ZoneInfo, start: date, end: date, params=P) -> tuple[str, str]:
    """(kind, reason) for a run that writes [start, end]. reason: default | rmssd_enabled | rmssd_insufficient_beats |
    kept_until_full_recompute."""
    if not rmssd_enabled(conn, user_id):
        kind, reason = params.HRV_KIND, "default"
    elif "rmssd" in stored_kinds(conn, user_id) or coverage(conn, user_id, tz, end, params)["enough"]:
        kind, reason = "rmssd", "rmssd_enabled"
    else:
        kind, reason = params.HRV_KIND, "rmssd_insufficient_beats"
    kept = stored_kinds(conn, user_id, (start, end))
    if len(kept) == 1 and kind not in kept:   # a partial write never mixes kinds; the full recompute switches
        return kept.pop(), "kept_until_full_recompute"
    return kind, reason
