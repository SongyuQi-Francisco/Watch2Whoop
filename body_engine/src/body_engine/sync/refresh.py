"""On-demand refresh of one user's data (app opened / pull to refresh), between the hourly jobs.

- request(): own user, rate limited (one accepted request per MIN_INTERVAL per user; later ones just return the status),
  idempotent (one row per user in sync_refresh, concurrent requests collapse). Starts a background thread in the API
  process that runs the same import as `body-engine import-ow` (importers/ow.run: daily metrics, sleep, workouts from the
  cursor; a few seconds) under a per-user advisory lock, then queues the score recompute.
- The recompute (`compute-scores` for the last SCORE_DAYS days + insights, then the rules) takes about 1–2 minutes of CPU
  and loads a year of data, too heavy for the API container: `body-engine sync-refresh-run` does it for every queued user
  (Ali: the per-minute imports timer starts it in a one-off container only when something is queued). With
  BODY_SYNC_SCORES=inline the API thread recomputes itself (single-machine setups with room to spare).
- status(): what the Today page shows (「同步中… / 已更新 07:42」): the row plus the time of the last OW import from any path
  (sync_state.last_synced_at, so the hourly job counts too).
- queue_scores(): data that changed older days (the app's direct uploads, rawdata/quantities.py) asks for a recompute from
  that day: scores_from keeps the earliest asked day, scores_queued_at the time of the last ask (a recompute that started
  before a newer ask leaves the row queued, so the newer data is scored too). No OW import, no rate limit.
- run_queued() also runs the server part of a forced re-sync (sync/resync.py): the re-sync marks the row scores_queued
  while it has work, so the per-minute job that already watches this table picks it up (no new trigger needed).
Users who have not consented (profile/consent.py) and users without an OW link are not processed.
"""
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

log = logging.getLogger(__name__)

MIN_INTERVAL = timedelta(minutes=2)    # one accepted request per user per 2 minutes
SCORE_DAYS = 3                         # recompute the last 3 days, like the hourly job
STALE = timedelta(minutes=15)          # a refresh stuck this long (process restarted) may start again
LOCK_NS = 0x5359                       # advisory lock namespace ("SY") for (namespace, user_id)
SCORES_INLINE = os.environ.get("BODY_SYNC_SCORES", "queue") == "inline"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def status(conn: psycopg.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM sync_refresh WHERE user_id = %s", (user_id,)).fetchone() or {}
    last = conn.execute("SELECT max(last_synced_at) AS t FROM sync_state WHERE user_id = %s AND source = 'ow'",
                        (user_id,)).fetchone()
    linked = conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()
    st = row.get("status") or "idle"
    # Busy only while recent: a queued recompute nobody picked up (no worker yet) must not show 「同步中…」 forever
    busy = st in ("importing", "scores_queued", "scoring") and row["updated_at"] > _now() - STALE
    return {"status": st, "busy": busy,
            "requested_at": row.get("requested_at"), "imported_at": row.get("imported_at"),
            "scored_at": row.get("scored_at"), "last_import_at": last["t"] if last else None,
            "error": row.get("error"), "linked": bool(linked and linked["ow_user_id"])}


def _set(conn, user_id: int, **fields) -> None:
    sets = ", ".join(f"{k} = %s" for k in fields)
    conn.execute(f"UPDATE sync_refresh SET {sets}, updated_at = now() WHERE user_id = %s", (*fields.values(), user_id))


def request(conn: psycopg.Connection, user_id: int, start_thread=None) -> dict:
    """Accept a refresh (or say why not). Returns status() plus accepted / throttled / retry_after / reason."""
    from ..profile import consent
    if consent.gate(conn, user_id):
        return {**status(conn, user_id), "accepted": False, "reason": "consent"}
    if not status(conn, user_id)["linked"]:
        return {**status(conn, user_id), "accepted": False, "reason": "not_linked"}
    now = _now()
    conn.execute("INSERT INTO sync_refresh (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    # Row lock: two requests at the same moment cannot both pass the checks below
    row = conn.execute("SELECT * FROM sync_refresh WHERE user_id = %s FOR UPDATE", (user_id,)).fetchone()
    busy = row["status"] in ("importing", "scoring") and row["updated_at"] > now - STALE
    if row["requested_at"] and row["requested_at"] > now - MIN_INTERVAL:
        wait = int((row["requested_at"] + MIN_INTERVAL - now).total_seconds()) + 1
        conn.commit()
        return {**status(conn, user_id), "accepted": False, "throttled": True, "retry_after": wait}
    if busy:
        conn.commit()
        return {**status(conn, user_id), "accepted": False, "reason": "busy"}
    _set(conn, user_id, status="importing", requested_at=now, error=None)
    conn.commit()
    (start_thread or _start_thread)(user_id)
    return {**status(conn, user_id), "accepted": True}


def _start_thread(user_id: int) -> None:
    threading.Thread(target=_run_import, args=(user_id,), name=f"sync-refresh-{user_id}", daemon=True).start()


def _connect():
    from .. import api
    return api.pool.connection()


def _run_import(user_id: int, client=None, connect=None) -> None:
    """Background: the import-ow step for this user, then queue (or run) the score recompute."""
    with (connect or _connect)() as conn:
        try:
            got = conn.execute("SELECT pg_try_advisory_lock(%s, %s) AS ok", (LOCK_NS, user_id)).fetchone()["ok"]
            if not got:   # another refresh of this user is importing (other API worker)
                _set(conn, user_id, status="scores_queued")
                conn.commit()
                return
            try:
                from ..importers import ow
                from ..ow_client import OWClient
                ow.run(conn, client or OWClient(), user_id)
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_NS, user_id))
            _set(conn, user_id, status="scores_queued", imported_at=_now(), error=None)
            conn.commit()
            if SCORES_INLINE:
                run_scores(conn, user_id, client)
        except Exception as e:  # noqa: BLE001  shown on the page as "not updated"; the hourly job still runs
            conn.rollback()
            log.warning("sync refresh import failed for user %s: %s", user_id, e)
            _set(conn, user_id, status="error", error=str(e)[:300])
            conn.commit()


def queue_scores(conn: psycopg.Connection, user_id: int, from_date) -> None:
    """Ask for a score recompute from `from_date` (at least the usual SCORE_DAYS window). Commits."""
    conn.execute("INSERT INTO sync_refresh (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    conn.execute("""UPDATE sync_refresh SET scores_from = LEAST(coalesce(scores_from, %(d)s), %(d)s), scores_queued_at = now(),
                           status = CASE WHEN status IN ('importing', 'scores_queued', 'scoring') THEN status
                                         ELSE 'scores_queued' END,
                           updated_at = now()
                    WHERE user_id = %(u)s""", {"u": user_id, "d": from_date})
    conn.commit()


def run_scores(conn: psycopg.Connection, user_id: int, client=None, scorer=None) -> dict:
    """Recompute the last SCORE_DAYS days, or from scores_from when an upload asked for older days (same as the hourly
    job: compute-scores + insights, then the rules)."""
    row = conn.execute("SELECT scores_from, scores_queued_at FROM sync_refresh WHERE user_id = %s", (user_id,)).fetchone() or {}
    t0 = conn.execute("SELECT now() AS t").fetchone()["t"]
    _set(conn, user_id, status="scoring")
    conn.commit()
    try:
        res = (scorer or _score)(conn, user_id, client, row.get("scores_from"))
        # asked again while this ran: stay queued (the next run covers the newer data); else done
        conn.execute("""UPDATE sync_refresh SET scored_at = now(), error = NULL, updated_at = now(),
                               status = CASE WHEN scores_queued_at > %(t)s THEN 'scores_queued' ELSE 'idle' END,
                               scores_from = CASE WHEN scores_queued_at > %(t)s THEN scores_from ELSE NULL END
                        WHERE user_id = %(u)s""", {"u": user_id, "t": t0})
        conn.commit()
        return res
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        _set(conn, user_id, status="error", error=str(e)[:300])
        conn.commit()
        return {"error": str(e)}


def _score(conn, user_id: int, client=None, scores_from=None) -> dict:
    from ..importers import shortcut
    from ..insights import jobs as insights_jobs
    from ..ow_client import OWClient
    from ..rules import engine as rules_engine
    from ..scores import engine as scores_engine
    client = client or OWClient()
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    start = datetime.now(tz).date() - timedelta(days=SCORE_DAYS)
    if scores_from is not None:
        start = min(start, scores_from)
    res = scores_engine.run(conn, client, user_id, start, None, shortcut.user_archive_dir(user_id))
    res["insights"] = insights_jobs.run(conn, client, user_id)
    rules_engine.run(conn, user_id, None)
    return res


def run_queued(conn: psycopg.Connection, user_id: int | None = None, scorer=None, resync_kw: dict | None = None) -> list[dict]:
    """`body-engine sync-refresh-run`: recompute scores for every user whose refresh is queued (or one user), and run one
    budgeted step of their forced re-sync if they have one (sync/resync.py). Idempotent: nothing queued = nothing done."""
    from ..profile import consent
    from . import resync
    rows = conn.execute("""SELECT user_id FROM sync_refresh WHERE status = 'scores_queued'
                           OR (status = 'scoring' AND updated_at < now() - %s) ORDER BY requested_at""",
                        (STALE,)).fetchall()
    out = []
    for r in rows:
        uid = r["user_id"]
        if user_id is not None and uid != user_id:
            continue
        if consent.gate(conn, uid):
            _set(conn, uid, status="idle")
            conn.commit()
            continue
        if resync.pending(conn, uid):
            # its last step recomputes the scores from the start of its range, which covers the usual window too
            out.append({"user_id": uid, "resync": resync.run_step(conn, uid, **(resync_kw or {}))})
            continue
        out.append({"user_id": uid, **run_scores(conn, uid, scorer=scorer)})
    return out
