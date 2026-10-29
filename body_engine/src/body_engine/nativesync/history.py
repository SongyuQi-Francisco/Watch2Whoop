"""The phone's full history (#340 local history, docs/ios-native-app.md section 9.1 "本机完整历史"): stream `raw_history`
of POST /v1/sync/pull.

After a reinstall the phone restores the last APP_RAW_DAYS of raw samples first (stream `raw`, a few seconds), then
downloads everything older in the background, newest first, so it can recompute every day itself when the algorithm
changes. What it needs for an old day is not only what the app's sync engine uploaded (hk_samples): most people's first
years came through Open Wearables (live or archived), the Shortcut or an export.zip. So a day's body is the scoring inputs
as the server's own scoring merges them (scores/inputs.load: every source, one sample per kind and start time, a live
sample before an export.xml copy):

  {"t0": <local midnight, UTC seconds>,
   "inputs": {"hr": [[dt, value], ...], "hrv": [...], "resp": [...], "spo2": [...], "temp": [...]},
   "segments": [[start, end, stage], ...],
   "beats": {"cols": [...HISTORY_BEAT_COLS], "rows": [...]},
   "complete": true}

- inputs: the samples starting that local day; dt = seconds after t0 (ms precision, like every time the pull sends);
  hrv is Apple's SDNN (the RMSSD users' values are the beat-to-beat readings below); SpO2 in percent.
- segments: the sleep-stage segments the scores use (inputs.night_segments) of the nights waking this day: segments are
  grouped into nights by the importers' gap rule and a night goes to the day of its end - 4 h, like its sleep_sessions row.
- beats: the beat-to-beat readings starting that day, with the beats themselves (ms since the first beat, gap flags) so the
  phone keeps them too, and the server's RMSSD / SDNN.
- complete: false when a source the server scores from could not be read (Open Wearables not reachable for a user whose
  OW history is not archived): the phone must not recompute such days and upload them as its own.

Pages are bounded by days and rows (params.HISTORY_PAGE_*): one page costs about what compute-scores of those days costs.
api.py throttles them per user and per process (throttle.py).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..importers.common import WAKE_DAY_SHIFT
from . import params as P
from .bodies import day_bounds, plain

KINDS = ("hr", "hrv", "resp", "spo2", "temp")
SESSION_GAP = timedelta(minutes=60)   # importers: a gap of more than an hour starts a new sleep session
HISTORY_BEAT_COLS = ["uuid", "start", "end", "rmssd", "sdnn", "origin", "beats_ms", "gaps"]


def restore_days(conn: psycopg.Connection, uid: int, before: date | None, until: date | None, limit: int) -> list[date]:
    """Days that may have scoring inputs, newest first: older than `before` (the cursor's position) and `until` (the
    first day the phone already has, history_before). A day with data from any source has daily metrics or a night."""
    cond, args = "", []
    for b in (before, until):
        if b is not None:
            cond += " AND d < %s"
            args.append(b)
    q = f"""SELECT d FROM (SELECT DISTINCT date AS d FROM daily_metrics WHERE user_id = %s
                           UNION SELECT DISTINCT wake_date FROM sleep_sessions WHERE user_id = %s) x
            WHERE TRUE{cond} ORDER BY d DESC LIMIT %s"""
    return [r["d"] for r in conn.execute(q, [uid, uid, *args, limit]).fetchall()]


def total_days(conn: psycopg.Connection, uid: int, until: date | None) -> dict:
    """{"days": how many days a full download covers, "oldest": the first of them}: the phone's progress bar."""
    cond, args = ("AND d < %s", [until]) if until else ("", [])
    r = conn.execute(f"""SELECT count(*) AS n, min(d) AS oldest FROM (
                            SELECT DISTINCT date AS d FROM daily_metrics WHERE user_id = %s
                            UNION SELECT DISTINCT wake_date FROM sleep_sessions WHERE user_id = %s) x WHERE TRUE {cond}""",
                     [uid, uid, *args]).fetchone()
    return {"days": int(r["n"]), "oldest": r["oldest"].isoformat() if r["oldest"] else None}


def _client(conn: psycopg.Connection, uid: int):
    """(OW client or None, complete): the server scores with OW when the user has an OW account whose history is not
    archived yet (rawdata/ow_archive); without a client those samples are missing."""
    from ..ow_client import OWClient, OWError
    from ..rawdata import ow_archive
    ow_user = conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (uid,)).fetchone()["ow_user_id"]
    if not ow_user or ow_archive.get(conn, uid) is not None:
        return None, True
    try:
        return OWClient(), True
    except OWError:
        return None, False


def bodies(conn: psycopg.Connection, uid: int, days: list[date], tz: ZoneInfo) -> dict:
    """{day: body} for these days (one load over their whole range); days without any input are left out."""
    if not days:
        return {}
    from ..importers import shortcut
    from ..scores import inputs as SI
    lo_day, hi_day = min(days), max(days)
    want = set(days)
    client, complete = _client(conn, uid)
    inp = SI.load(conn, client, uid, lo_day, hi_day, shortcut.user_archive_dir(uid), hrv_kind="sdnn", ow_cache="use")
    t0 = {d: day_bounds(d, tz)[0].timestamp() for d in days}
    inputs: dict = defaultdict(lambda: defaultdict(list))
    for kind in KINDS:
        for ts, v in getattr(inp, kind):
            d = ts.astimezone(tz).date()
            if d in want and v is not None:
                inputs[d][kind].append([round(ts.timestamp() - t0[d], 3), v])
    segs: dict = defaultdict(list)
    for night in nights(SI.night_segments(conn, uid, tz, lo_day, hi_day)):
        d = (max(b for _, b, _ in night) - WAKE_DAY_SHIFT).astimezone(tz).date()
        if d in want:
            segs[d] += [[plain(a), plain(b), stage] for a, b, stage in night]
    beats: dict = defaultdict(list)
    lo, hi = day_bounds(lo_day, tz)[0], day_bounds(hi_day, tz)[1]
    for r in conn.execute("""SELECT hk_uuid, start_at, end_at, rmssd, sdnn, origin, beats_ms, gaps FROM heartbeat_series
                             WHERE user_id = %s AND start_at >= %s AND start_at < %s ORDER BY start_at""", (uid, lo, hi)):
        d = r["start_at"].astimezone(tz).date()
        if d in want:
            beats[d].append([plain(v) for v in r.values()])
    out = {}
    for d in days:
        if not inputs.get(d) and not segs.get(d) and not beats.get(d):
            continue
        out[d] = {"t0": t0[d], "inputs": {k: v for k, v in inputs[d].items() if v}, "segments": segs.get(d, []),
                  "beats": {"cols": HISTORY_BEAT_COLS, "rows": beats.get(d, [])}, "complete": complete}
    return out


def nights(segments: list[tuple]) -> list[list[tuple]]:
    """Sorted stage segments -> nights: a gap of more than SESSION_GAP starts a new one (the importers' session rule), so a
    night's segments all go to its wake day (end of the night - 4 h), the day its sleep_sessions row has."""
    out: list[list[tuple]] = []
    for s in sorted(segments):
        if out and s[0] - max(e for _, e, _ in out[-1]) <= SESSION_GAP:
            out[-1].append(s)
        else:
            out.append([s])
    return out


def size(body: dict | None) -> int:
    """Rows of one body (the page bound)."""
    if not body:
        return 0
    return (sum(len(v) for v in (body.get("inputs") or {}).values()) + len(body.get("segments") or [])
            + len((body.get("beats") or {}).get("rows") or []))


def page(conn: psycopg.Connection, uid: int, tz: ZoneInfo, days: list[date]) -> tuple[list[date], dict]:
    """The days of one answer (newest first, at least one) within HISTORY_PAGE_ROWS, and their bodies."""
    got = bodies(conn, uid, days, tz)
    sent, rows = [], 0
    for d in days:
        n = size(got.get(d))
        if sent and rows + n > P.HISTORY_PAGE_ROWS:
            break
        sent.append(d)
        rows += n
    return sent, got


def changed_days(conn: psycopg.Connection, uid: int, xid: int, seq: int, until: date | None) -> list[dict]:
    """After the download: the days older than `until` whose scoring inputs (params.SCORE_INPUT_STREAMS) changed after
    (xid, seq): an export.zip, a re-sync, a late import. One row {day, xid, seq} per day (its newest change), sorted by
    (xid, seq) like every incremental page; only transactions older than the oldest still running (pull.py)."""
    cond = "AND day < %(until)s" if until else ""
    rows = conn.execute(
        f"""SELECT DISTINCT ON (day) day, xid::text::bigint AS xid, seq FROM sync_changes
            WHERE user_id = %(u)s AND stream = ANY(%(st)s) AND day IS NOT NULL {cond}
              AND (xid, seq) > (%(x)s::text::xid8, %(s)s) AND xid < pg_snapshot_xmin(pg_current_snapshot())
            ORDER BY day, xid DESC, seq DESC""",
        {"u": uid, "st": list(P.SCORE_INPUT_STREAMS), "x": xid, "s": seq, "until": until}).fetchall()
    rows.sort(key=lambda r: (r["xid"], r["seq"]))
    return rows
