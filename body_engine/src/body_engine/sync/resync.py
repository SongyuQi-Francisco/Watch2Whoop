"""Forced re-sync from the app's sync center (flag sync_center): "send everything of the last N days again".

One job per user (table sync_resync, migration 675):
1. request(): the app asks for a range (days 1–3650, or None = all history). Refused while another job of this user is
   running ('busy'), within MIN_INTERVAL of the last accepted one ('throttled', ALL_MIN_INTERVAL for all history), or
   before consent ('consent'). Accepted -> 'phone': the phone re-reads Apple Health from the start of the range (Open
   Wearables SDK re-export + the app's own types, apps/watch2whoop/lib/src/resync.dart) and reports its progress.
2. progress(): phone_percent / counts; phone_done -> 'queued', and the row in sync_refresh is marked scores_queued so the
   per-minute job that runs `sync-refresh-run` picks the user up (sync/refresh.run_queued -> run_step here).
3. run_step(): 'queued' -> 'importing': importers/ow.run for [range start (or the first day in OW), today] in WINDOW_DAYS
   windows, resumable (import_cursor) and within TIME_BUDGET per run; -> 'scoring': compute-scores + insights + rules from
   the range start (all history: everything) -> 'done'. Users without an OW link skip the import. Unfinished after the
   budget: sync_refresh stays scores_queued and the next run continues. Any error: 'error' (shown; a new request may start).
   In the sync mode app (rawdata/engine.mode, #219 S5b) nothing is imported from OW: the phone re-sends the range
   through the engine (range / range_done, rawdata/engine.py), and 'queued' stays queued while that range is still open
   or the engine's days are not derived yet (_engine_waiting, at most RESYNC_ENGINE_WAIT), then -> 'scoring'.
cancel(): stops a job that has not reached the server part. A phone job without a report for PHONE_STALE is no longer
running (the app was closed for good): a new request may start, the server part never runs for it.
Own data only; the app calls it with its account token or its upload-only token (sync/api.py, /v1/ingest/resync).
Thresholds in sync/params.py.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from . import params as P

log = logging.getLogger(__name__)

PHONE = "phone"
SERVER = ("queued", "importing", "scoring")
ACTIVE = (PHONE, *SERVER)
LOCK_NS = 0x5253                # advisory lock namespace ("RS")
CLOCK_SLACK = timedelta(minutes=5)   # requested_at is the API's clock, sync_ranges.created_at the database's


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(conn, user_id: int) -> dict:
    return conn.execute("SELECT * FROM sync_resync WHERE user_id = %s", (user_id,)).fetchone() or {}


def _tz(conn, user_id: int) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])


def _set(conn, user_id: int, **fields) -> None:
    cols = ", ".join(f"{k} = %s" for k in fields)
    conn.execute(f"UPDATE sync_resync SET {cols}, updated_at = now() WHERE user_id = %s", (*fields.values(), user_id))


def _running(row: dict, now: datetime) -> bool:
    st = row.get("status")
    if st == PHONE:
        return row["updated_at"] > now - P.RESYNC_PHONE_STALE
    return st in SERVER


def percent(row: dict) -> int | None:
    """Overall progress: phone 0–60 %, server import 60–95 %, scoring 95–99 %, done 100."""
    st = row.get("status")
    if st == PHONE:
        return min(60, round((row.get("phone_percent") or 0) * 0.6))
    if st == "queued":
        return 60
    if st == "importing":
        frm, until, cur = row.get("import_from"), row.get("import_until"), row.get("import_cursor")
        if frm and until and cur:
            total = max((until - frm).days + 1, 1)
            return 60 + min(35, round(35 * min(max((cur - frm).days, 0), total) / total))
        return 60
    if st == "scoring":
        return 97
    if st == "done":
        return 100
    return None


def _iso(v) -> str | None:
    return v.isoformat() if v is not None else None


def status(conn: psycopg.Connection, user_id: int) -> dict:
    """The job as the app shows it, plus the on-demand refresh status (「已更新 07:42」, sync/refresh.status)."""
    from . import refresh
    row = _row(conn, user_id)
    now = _now()
    st = row.get("status") or "idle"
    running = _running(row, now) if row else False
    return {"status": st, "active": running, "stale": st == PHONE and not running,
            "phase": (PHONE if st == PHONE else "server") if running else None,
            "days": row.get("days"), "range_from": _iso(row.get("range_from")),
            "percent": percent(row) if running or st == "done" else None,
            "phone_percent": row.get("phone_percent") or 0, "phone_counts": row.get("phone_counts") or {},
            "requested_at": _iso(row.get("requested_at")), "phone_done_at": _iso(row.get("phone_done_at")),
            "finished_at": _iso(row.get("finished_at")), "error": row.get("error"),
            "refresh": {k: _iso(v) if isinstance(v, datetime) else v for k, v in refresh.status(conn, user_id).items()}}


def range_start(today: date, days: int | None) -> date | None:
    """Same as the app's syncDaysBack: from the start of the day `days` days ago (None = all history)."""
    return None if days is None else today - timedelta(days=days)


def request(conn: psycopg.Connection, user_id: int, days: int | None) -> dict:
    """Start a re-sync of the last `days` days (None = all). Returns status() plus accepted / reason / retry_after."""
    from ..profile import consent
    if days is not None and not 1 <= days <= P.RESYNC_MAX_DAYS:
        raise ValueError(f"days must be 1–{P.RESYNC_MAX_DAYS} or null")
    if consent.gate(conn, user_id):
        return {**status(conn, user_id), "accepted": False, "reason": "consent"}
    conn.execute("INSERT INTO sync_resync (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    row = conn.execute("SELECT * FROM sync_resync WHERE user_id = %s FOR UPDATE", (user_id,)).fetchone()
    now = _now()
    if _running(row, now):
        conn.commit()
        return {**status(conn, user_id), "accepted": False, "reason": "busy"}
    gap = P.RESYNC_ALL_MIN_INTERVAL if days is None else P.RESYNC_MIN_INTERVAL
    if row["requested_at"] and row["requested_at"] > now - gap:
        wait = int((row["requested_at"] + gap - now).total_seconds()) + 1
        conn.commit()
        return {**status(conn, user_id), "accepted": False, "reason": "throttled", "retry_after": wait}
    today = datetime.now(_tz(conn, user_id)).date()
    _set(conn, user_id, status=PHONE, days=days, range_from=range_start(today, days), requested_at=now, phone_percent=0,
         phone_counts=Jsonb({}), phone_done_at=None, import_from=None, import_until=None, import_cursor=None,
         finished_at=None, error=None)
    conn.commit()
    return {**status(conn, user_id), "accepted": True}


def progress(conn: psycopg.Connection, user_id: int, phone_percent: int | None = None, counts: dict | None = None,
             phone_done: bool = False) -> dict:
    """The phone's progress; phone_done hands over to the server part. Ignored unless the job is in the phone phase."""
    row = conn.execute("SELECT * FROM sync_resync WHERE user_id = %s FOR UPDATE", (user_id,)).fetchone()
    if row and row["status"] == PHONE:
        sets: dict = {}
        if phone_percent is not None:
            sets["phone_percent"] = max(row["phone_percent"], max(0, min(100, int(phone_percent))))
        if counts:
            sets["phone_counts"] = Jsonb({**(row["phone_counts"] or {}), **{k: int(v) for k, v in counts.items()}})
        if phone_done:
            sets.update(status="queued", phone_percent=100, phone_done_at=_now())
        if sets:
            _set(conn, user_id, **sets)
        if phone_done:
            _wake(conn, user_id)
    conn.commit()
    return status(conn, user_id)


def cancel(conn: psycopg.Connection, user_id: int) -> dict:
    """Stop a job before the server part started (phone / queued). The server part, once running, finishes."""
    conn.execute("UPDATE sync_resync SET status = 'cancelled', updated_at = now() WHERE user_id = %s AND status IN ('phone', 'queued')",
                 (user_id,))
    conn.commit()
    return status(conn, user_id)


def _wake(conn, user_id: int) -> None:
    """Mark the user's sync_refresh row queued: the per-minute job runs sync-refresh-run, which runs run_step."""
    conn.execute("INSERT INTO sync_refresh (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    conn.execute("""UPDATE sync_refresh SET status = 'scores_queued', updated_at = now()
                    WHERE user_id = %s AND status <> 'importing'""", (user_id,))


def wake_if_waiting(conn: psycopg.Connection, user_id: int) -> None:
    """Self-heal (called when the app polls): a server part waiting while the refresh row is not queued (e.g. an on-open
    refresh ended with an error and reset it) gets queued again. Commits."""
    if _row(conn, user_id).get("status") in SERVER:
        r = conn.execute("SELECT status FROM sync_refresh WHERE user_id = %s", (user_id,)).fetchone()
        if r is None or r["status"] in ("idle", "error"):
            _wake(conn, user_id)
            conn.commit()


def pending(conn: psycopg.Connection, user_id: int) -> bool:
    return _row(conn, user_id).get("status") in SERVER


def _score(conn, user_id: int, client, start: date | None) -> dict:
    from ..importers import shortcut
    from ..insights import jobs as insights_jobs
    from ..rules import engine as rules_engine
    from ..scores import engine as scores_engine
    # the re-sync re-imported its range from OW, which also refreshed that part of the local sample copy
    res = scores_engine.run(conn, client, user_id, start, None, shortcut.user_archive_dir(user_id), ow_cache="use")
    res["insights"] = insights_jobs.run(conn, client, user_id, ow_cache="use")
    rules_engine.run(conn, user_id, None)
    return res


def _ow_or_none():
    from ..ow_client import OWClient, OWError
    try:
        return OWClient()
    except OWError:   # no OW configured (after it was retired)
        return None


def _engine_waiting(conn, user_id: int, row: dict) -> str | None:
    """The sync mode app: what the server part still waits for before it recomputes, or None.
    'range' = a range re-sync the app opened for this job (sync_ranges since the request) has no range_done yet;
    'derive' = the engine's uploaded days are not all derived yet (derived here first, within the refresh budget).
    Either for at most RESYNC_ENGINE_WAIT after the phone said it was done: an app killed mid-range or a day that keeps
    failing to derive must not hold the job forever (the scores are then computed from what is there)."""
    from . import refresh
    since = row.get("requested_at") or _now()
    patient = _now() - (row.get("phone_done_at") or since) < P.RESYNC_ENGINE_WAIT
    open_ = conn.execute("""SELECT count(*) AS n FROM sync_ranges WHERE user_id = %s AND done_at IS NULL
                            AND created_at >= %s""", (user_id, since - CLOCK_SLACK)).fetchone()["n"]
    if open_ and patient:
        return "range"
    refresh.derive_engine_days(conn, user_id)
    left = conn.execute("SELECT count(*) AS n FROM hk_dirty_days WHERE user_id = %s", (user_id,)).fetchone()["n"]
    return "derive" if left and patient else None


def _first_day(conn, user_id: int, client) -> date | None:
    from ..importers import ow
    row = conn.execute("SELECT ow_user_id, timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    first = ow._first_sample_time(client, row["ow_user_id"])
    return first.astimezone(ZoneInfo(row["timezone"])).date() if first else None


def run_step(conn: psycopg.Connection, user_id: int, client=None, importer=None, scorer=None, first_day=None,
             budget: float = P.RESYNC_TIME_BUDGET, clock=_time.monotonic) -> dict:
    """One budgeted step of the server part. Keeps sync_refresh queued while unfinished, idle when done. Commits."""
    if importer is None:
        from ..importers import ow
        importer = lambda c, cl, uid, s, e: ow.run(c, cl, uid, s, e)   # noqa: E731
    got = conn.execute("SELECT pg_try_advisory_lock(%s, %s) AS ok", (LOCK_NS, user_id)).fetchone()["ok"]
    if not got:
        return {"status": "locked"}
    t0 = clock()
    windows = 0
    try:
        from ..rawdata import engine as rx_engine
        app = rx_engine.mode(conn, user_id) == "app"
        if client is None and app:   # nothing is imported; the app mode's readers fill from OW only while it still runs
            client = _ow_or_none()
        elif client is None:   # inside the try: an OW client that cannot start (no API key) fails this job, shown in the
            from ..ow_client import OWClient   # app, instead of raising out of sync-refresh-run with the job stuck queued
            client = OWClient()
        row = _row(conn, user_id)
        if app and row.get("status") == "importing":   # switched to the app mode while importing: OW is not read any more
            _set(conn, user_id, status="scoring")
            conn.commit()
            row = _row(conn, user_id)
        if row.get("status") == "queued":
            if app:
                waiting = _engine_waiting(conn, user_id, row)
                if waiting:   # the next per-minute run looks again
                    _wake(conn, user_id)
                    conn.commit()
                    return {"status": "queued", "waiting": waiting, "windows": 0, "percent": percent(row)}
            linked = conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()["ow_user_id"]
            first = (first_day or _first_day)(conn, user_id, client) if linked and not app else None
            if first is None:   # the app mode, nothing in OW or no link: only the scores
                _set(conn, user_id, status="scoring")
            else:
                frm = max(first, row["range_from"]) if row["range_from"] else first
                _set(conn, user_id, status="importing", import_from=frm, import_cursor=frm,
                     import_until=datetime.now(_tz(conn, user_id)).date())
            conn.commit()
            row = _row(conn, user_id)
        while row["status"] == "importing" and clock() - t0 < budget:
            cur, until = row["import_cursor"], row["import_until"]
            if cur <= until:
                end = min(cur + timedelta(days=P.RESYNC_WINDOW_DAYS - 1), until)
                importer(conn, client, user_id, cur, end)
                _set(conn, user_id, import_cursor=end + timedelta(days=1))
                windows += 1
            row = _row(conn, user_id)
            if row["import_cursor"] > row["import_until"]:
                _set(conn, user_id, status="scoring")
            conn.commit()
            row = _row(conn, user_id)
        if row["status"] == "scoring" and clock() - t0 < budget:
            (scorer or _score)(conn, user_id, client, row["range_from"])
            _set(conn, user_id, status="done", finished_at=_now(), error=None)
            conn.execute("""UPDATE sync_refresh SET status = 'idle', scored_at = now(), error = NULL, updated_at = now(),
                                   scores_from = NULL WHERE user_id = %s AND status IN ('scores_queued', 'scoring')""",
                         (user_id,))
            conn.commit()
            row = _row(conn, user_id)
        if row["status"] in SERVER:   # budget used up: the next run continues
            _wake(conn, user_id)
            conn.commit()
        return {"status": row["status"], "windows": windows, "percent": percent(row)}
    except Exception as e:  # noqa: BLE001  shown in the app; a new request may start
        conn.rollback()
        log.warning("re-sync failed for user %s: %s", user_id, e)
        _set(conn, user_id, status="error", error=str(e)[:300])
        conn.execute("UPDATE sync_refresh SET status = 'idle', updated_at = now() WHERE user_id = %s AND status = 'scores_queued'",
                     (user_id,))
        conn.commit()
        return {"status": "error", "error": str(e)}
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_NS, user_id))
        conn.commit()


def run_queued(conn: psycopg.Connection, user_id: int | None = None, **kw) -> list[dict]:
    """One step for every user with a server part waiting (for `body-engine sync-refresh-run --resync-only` and tests)."""
    from ..profile import consent
    out = []
    for r in conn.execute("SELECT user_id FROM sync_resync WHERE status IN ('queued', 'importing', 'scoring') "
                          "ORDER BY requested_at").fetchall():
        uid = r["user_id"]
        if (user_id is not None and uid != user_id) or consent.gate(conn, uid):
            continue
        out.append({"user_id": uid, **run_step(conn, uid, **kw)})
    return out
