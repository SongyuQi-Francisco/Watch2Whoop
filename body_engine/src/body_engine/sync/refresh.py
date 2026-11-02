"""On-demand refresh of one user's data (app opened / pull to refresh), between the hourly jobs.

- request(): own user, rate limited (one accepted request per MIN_INTERVAL per user; later ones just return the status),
  idempotent (one row per user in sync_refresh, concurrent requests collapse). Starts a background thread in the API
  process that runs the same import as `body-engine import-ow` (importers/ow.run: daily metrics, sleep, workouts from the
  cursor; a few seconds) under a per-user advisory lock, then queues the score recompute.
  With BODY_SYNC_IMPORT=queue (servers that run the per-minute `sync-refresh-run`, e.g. a one-off job container) the API
  process does not import at all: the row is queued with import_queued_at, as the poller does (sync/ow_watch.py), and
  the job imports and recomputes. In the sync mode `app` (rawdata/engine.mode, the app's own sync engine feeds the
  scores) nothing is imported from OW either: the request only queues the recompute (run_scores derives first). An import holds a window of raw samples in memory (a new user's first 30-day window is
  tens of MB), which together with other work pushed the API container over its memory limit.
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
- run_scores() first derives the app sync engine's queued days of the user (rawdata/derive.py, hk-derive-run's work for
  one user), so the recompute sees them when the engine's values count (sync mode app).
- New OW uploads (sync/ow_watch.py, the API's poller) queue the row with import_queued_at: run_queued() then imports from
  OW first (the same import-ow under the same per-user lock), so the data and the scores are updated by one run. After a
  successful recompute the hourly job's fingerprints are stored (batch.remember), so it does not redo the work.
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
IMPORT_QUEUED = os.environ.get("BODY_SYNC_IMPORT", "inline") == "queue"   # see request()


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
    from ..rawdata import engine as rx_engine
    if consent.gate(conn, user_id):
        return {**status(conn, user_id), "accepted": False, "reason": "consent"}
    app = rx_engine.mode(conn, user_id) == "app"   # the app's own sync engine feeds the scores: nothing to import from OW
    if not app and not status(conn, user_id)["linked"]:
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
    if app or (IMPORT_QUEUED and start_thread is None):   # the per-minute sync-refresh-run imports and recomputes
        conn.execute("""UPDATE sync_refresh SET status = 'scores_queued', requested_at = %s,
                               import_queued_at = CASE WHEN %s THEN import_queued_at ELSE now() END,
                               scores_queued_at = now(), error = NULL, updated_at = now() WHERE user_id = %s""",
                     (now, app, user_id))
        conn.commit()
        return {**status(conn, user_id), "accepted": True}
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
                from ..rawdata import retire
                if not retire.ow_import_stopped():   # 2.0.0 R1: after the cutoff only the scores are refreshed
                    ow.run(conn, client or OWClient(), user_id)
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_NS, user_id))
            _set(conn, user_id, status="scores_queued", imported_at=_now(), error=None)
            # an import the poller queued before this one was requested is covered by it
            conn.execute("""UPDATE sync_refresh SET import_queued_at = NULL WHERE user_id = %s
                            AND import_queued_at <= requested_at""", (user_id,))
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


def derive_engine_days(conn: psycopg.Connection, user_id: int) -> dict:
    """The app sync engine's queued days of this user (rawdata/derive.py), so a recompute sees them; in the app sync mode
    that also moves scores_from back to the earliest derived day. A failure is logged, never stops the recompute."""
    try:
        import time as _time
        from ..rawdata import derive, params as rparams
        return derive.run_user(conn, user_id, deadline=_time.monotonic() + rparams.REFRESH_TIME_BUDGET_S)
    except Exception as e:  # noqa: BLE001  the days stay queued for the next hk-derive-run
        conn.rollback()
        log.warning("hk derive failed for user %s: %s", user_id, e)
        return {"error": str(e)[:300]}


def run_scores(conn: psycopg.Connection, user_id: int, client=None, scorer=None) -> dict:
    """Recompute the last SCORE_DAYS days, or from scores_from when an upload asked for older days (same as the hourly
    job: compute-scores + insights, then the rules). The sync engine's queued days are derived first."""
    derive_engine_days(conn, user_id)
    row = conn.execute("SELECT scores_from, scores_queued_at FROM sync_refresh WHERE user_id = %s", (user_id,)).fetchone() or {}
    t0 = conn.execute("SELECT now() AS t").fetchone()["t"]
    _set(conn, user_id, status="scoring")
    conn.commit()
    fps = _fingerprints(conn, user_id)
    try:
        res = (scorer or _score)(conn, user_id, client, row.get("scores_from"))
        _remember(conn, user_id, fps)
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


def _fingerprints(conn, user_id: int) -> dict | None:
    """The hourly job's input fingerprints before a recompute (batch.py), stored after it succeeded."""
    try:
        from .. import batch
        tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
        today = datetime.now(tz).date()
        res = {"compute-scores": batch.fingerprint(conn, user_id, today),
               "evaluate-rules": batch.fingerprint(conn, user_id, today, extras=True)}
        conn.commit()
        return res
    except Exception as e:  # noqa: BLE001  only an optimisation: the hourly job then simply recomputes
        conn.rollback()
        log.warning("fingerprint failed for user %s: %s", user_id, e)
        return None


def _remember(conn, user_id: int, fps: dict | None) -> None:
    if not fps:
        return
    from .. import batch
    try:
        for step, fp in fps.items():
            batch.remember(conn, user_id, step, fp)
    except Exception as e:  # noqa: BLE001  the scores are in; the hourly job then simply recomputes
        conn.rollback()
        log.warning("remembering the fingerprints failed for user %s: %s", user_id, e)


def import_queued(conn: psycopg.Connection, user_id: int, client=None) -> dict:
    """The import the poller (sync/ow_watch.py) queued: import-ow for this user under the per-user lock, then the row is
    scores_queued again with the imported days included. Returns the import result, {"busy": True} when an API refresh is
    importing this user right now (it queues the scores itself), or {"error": ...} (the hourly job retries)."""
    t0 = conn.execute("SELECT now() AS t").fetchone()["t"]
    conn.commit()
    if not conn.execute("SELECT pg_try_advisory_lock(%s, %s) AS ok", (LOCK_NS, user_id)).fetchone()["ok"]:
        conn.commit()
        return {"busy": True}
    try:
        from ..rawdata import retire
        if retire.ow_import_stopped():   # 2.0.0 R1: OW retired, nothing to import; the queued scores still run
            conn.execute("""UPDATE sync_refresh SET status = 'scores_queued', import_queued_at = NULL, updated_at = now()
                            WHERE user_id = %s""", (user_id,))
            conn.commit()
            return {"skipped": "legacy_cutoff"}
        _set(conn, user_id, status="importing")
        conn.commit()
        from ..importers import ow
        if client is None:
            from ..ow_client import OWClient
            client = OWClient()
        res = ow.run(conn, client, user_id)
        conn.execute("""UPDATE sync_refresh SET status = 'scores_queued', imported_at = now(), error = NULL, updated_at = now(),
                               scores_from = CASE WHEN %(s)s::date IS NULL THEN scores_from
                                                  ELSE LEAST(coalesce(scores_from, %(s)s::date), %(s)s::date) END,
                               import_queued_at = CASE WHEN import_queued_at <= %(t)s THEN NULL ELSE import_queued_at END
                        WHERE user_id = %(u)s""", {"u": user_id, "s": res.get("start"), "t": t0})
        conn.commit()
        return res
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        log.warning("queued import failed for user %s: %s", user_id, e)
        conn.execute("""UPDATE sync_refresh SET status = 'error', error = %s, import_queued_at = NULL, updated_at = now()
                        WHERE user_id = %s""", (str(e)[:300], user_id))
        conn.commit()
        return {"error": f"{type(e).__name__}: {e}"}
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_NS, user_id))
        conn.commit()


def _score(conn, user_id: int, client=None, scores_from=None) -> dict:
    from ..importers import shortcut
    from ..insights import jobs as insights_jobs
    from ..ow_client import OWClient
    from ..rules import engine as rules_engine
    from ..scores import engine as scores_engine
    from ..ow_client import OWError
    try:
        client = client or OWClient()
    except OWError:   # no OW configured (after it was retired): the app mode's readers do not need it
        client = None
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    start = datetime.now(tz).date() - timedelta(days=SCORE_DAYS)
    if scores_from is not None:
        start = min(start, scores_from)
    from ..nativesync import appscores, primary
    plan = appscores.plan(conn, user_id)
    # 2.0.0 S9: rules the user's phone evaluates (package `rules` primary) are not evaluated here
    rules_here = not primary.server_primary(conn, user_id, "rules")
    if plan["mode"] == "app":   # the user's app computes the scores (#340 N2): only the days it left stale
        res = (scores_engine.run(conn, client, user_id, min(plan["days"]), None, shortcut.user_archive_dir(user_id),
                                 ow_cache="use", only=set(plan["days"])) if plan["days"] else {"rows": 0})
        res["app_scoring"] = True
        if rules_here:
            rules_engine.run(conn, user_id, None)
        return res
    res = scores_engine.run(conn, client, user_id, start, None, shortcut.user_archive_dir(user_id), ow_cache="use")
    res["insights"] = insights_jobs.run(conn, client, user_id, ow_cache="use")
    if rules_here:
        rules_engine.run(conn, user_id, None)
    return res


def run_queued(conn: psycopg.Connection, user_id: int | None = None, scorer=None, resync_kw: dict | None = None,
               importer=None) -> list[dict]:
    """`body-engine sync-refresh-run`: recompute scores for every user whose refresh is queued (or one user), and run one
    budgeted step of their forced re-sync if they have one (sync/resync.py). Idempotent: nothing queued = nothing done."""
    from ..profile import consent
    from . import resync
    rows = conn.execute("""SELECT user_id, import_queued_at FROM sync_refresh WHERE status = 'scores_queued'
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
        imported = None
        if r["import_queued_at"] is not None:   # new OW uploads (sync/ow_watch.py): import them first
            imported = (importer or import_queued)(conn, uid)
            if "error" in imported or imported.get("busy"):
                out.append({"user_id": uid, "import": imported})
                continue
        res = run_scores(conn, uid, scorer=scorer)
        out.append({"user_id": uid, **({"import": _brief(imported)} if imported else {}), **res})
    return out


def _brief(res: dict) -> dict:
    return {k: res.get(k) for k in ("start", "end", "samples", "daily_rows")}
