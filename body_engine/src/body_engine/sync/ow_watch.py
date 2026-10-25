"""Scores within minutes of new watch data from Open Wearables (docs/hourly-batch.md "New data within minutes").

The iOS SDK uploads to Open Wearables, not to us; before this, Body Engine pulled only in the hourly job (or when the app
asked, sync/refresh.py), so a morning's sleep reached the recovery score up to an hour late. Now the API process asks OW
every OW_WATCH_POLL_S which users uploaded lately: ONE request (GET /users sorted by last_synced_at, which OW stamps
after every saved SDK batch) and a small query here, nothing else while nobody uploads. A user counts as having new data
when OW's time is later than both the last import we queued (ow_watch.queued_through) and the start of the last OW import
or probe from any path (sync_state.last_synced_at: the hourly job, the app's refresh). Once their uploads went quiet for
OW_WATCH_QUIET (and at most once per OW_WATCH_MIN_GAP), sync_refresh is set to scores_queued with import_queued_at: the
per-minute job that already runs `sync-refresh-run` (Ali: the imports timer, a one-off container only when something is
queued) imports from OW and recomputes the scores (sync/refresh.run_queued). The Today page shows 「同步中…」 meanwhile and
reloads when the scores are in (web/static/sync.js). Users without consent, without an OW link, or with a refresh already
running are left alone; the hourly job stays as the safety net.

Off with BODY_OW_WATCH=off, or when there is no OW API key. `body-engine ow-watch` runs one round by hand.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import psycopg

from . import params as P

log = logging.getLogger(__name__)
LOCK_KEY = 0x4F57_5741   # advisory lock ("OWWA"): one poller across API processes


def enabled() -> bool:
    if os.environ.get("BODY_OW_WATCH", "on").strip().lower() in ("off", "0", "false", "no"):
        return False
    from ..config import settings
    return bool(settings.ow_api_key)


def recent_uploads(client, since: datetime) -> dict[str, datetime]:
    """{OW user id: last upload} of the users whose last upload is not older than `since` (newest first, a few pages)."""
    out: dict[str, datetime] = {}
    for page in range(1, P.OW_WATCH_MAX_PAGES + 1):
        res = client.get("/users", sort_by="last_synced_at", sort_order="desc", limit=100, page=page)
        items = res.get("items") or []
        for it in items:
            t = it.get("last_synced_at")
            if not t:
                return out   # nulls come last
            t = datetime.fromisoformat(t)
            if t < since:
                return out
            out[str(it["id"])] = t
        if len(items) < 100:
            break
    return out


def poll(conn: psycopg.Connection, client=None, now: datetime | None = None) -> dict:
    """One round. Returns {"uploads": n, "queued": [user ids], "waiting": [user ids]}. Commits."""
    from ..profile import consent
    from .refresh import STALE
    if client is None:
        from ..ow_client import OWClient
        client = OWClient()
    now = now or datetime.now(timezone.utc)
    seen = recent_uploads(client, now - P.OW_WATCH_LOOKBACK)
    out = {"uploads": len(seen), "queued": [], "waiting": []}
    if not seen:
        return out
    rows = conn.execute("""
        SELECT u.id, u.ow_user_id, w.ow_synced_at, w.queued_through, w.queued_at, s.last_synced_at AS imported_at,
               r.status, r.updated_at AS refresh_at
        FROM users u
        LEFT JOIN ow_watch w ON w.user_id = u.id
        LEFT JOIN sync_state s ON s.user_id = u.id AND s.source = 'ow'
        LEFT JOIN sync_refresh r ON r.user_id = u.id
        WHERE u.ow_user_id = ANY(%s) ORDER BY u.id""", (list(seen),)).fetchall()
    for r in rows:
        uid, t = r["id"], seen[r["ow_user_id"]]
        if r["ow_synced_at"] != t:
            conn.execute("""INSERT INTO ow_watch (user_id, ow_synced_at) VALUES (%s, %s)
                            ON CONFLICT (user_id) DO UPDATE SET ow_synced_at = EXCLUDED.ow_synced_at, updated_at = now()""",
                         (uid, t))
        handled = max((x for x in (r["queued_through"], r["imported_at"]) if x is not None), default=None)
        if handled is not None and t <= handled:
            continue   # nothing after the last import
        busy = r["status"] in ("importing", "scoring") and r["refresh_at"] > now - STALE
        if now - t < P.OW_WATCH_QUIET or (r["queued_at"] and now - r["queued_at"] < P.OW_WATCH_MIN_GAP) or busy:
            out["waiting"].append(uid)   # still uploading / just queued / being processed: a later round
            continue
        if consent.gate(conn, uid):
            continue
        if _queue(conn, uid, t, STALE):
            out["queued"].append(uid)
        else:
            out["waiting"].append(uid)
    conn.commit()
    return out


def _queue(conn: psycopg.Connection, user_id: int, through: datetime, stale) -> bool:
    """Ask sync-refresh-run for an import + recompute, unless a refresh of the user started meanwhile."""
    conn.execute("INSERT INTO sync_refresh (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    row = conn.execute("""UPDATE sync_refresh SET status = 'scores_queued', import_queued_at = now(), scores_queued_at = now(),
                                 error = NULL, updated_at = now()
                          WHERE user_id = %s AND NOT (status IN ('importing', 'scoring') AND updated_at > now() - %s)
                          RETURNING user_id""", (user_id, stale)).fetchone()
    if row is None:
        return False
    conn.execute("""INSERT INTO ow_watch (user_id, ow_synced_at, queued_through, queued_at) VALUES (%s, %s, %s, now())
                    ON CONFLICT (user_id) DO UPDATE SET queued_through = EXCLUDED.queued_through, queued_at = now(),
                      updated_at = now()""", (user_id, through, through))
    return True


def run_once(conn: psycopg.Connection, client=None) -> dict | None:
    """poll() under the advisory lock; None when another process is polling."""
    if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_KEY,)).fetchone()["ok"]:
        conn.commit()
        return None
    try:
        return poll(conn, client)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        conn.commit()


def _tick() -> None:
    from .. import api
    with api.pool.connection() as conn:
        res = run_once(conn)
    if res and res["queued"]:
        log.info("ow-watch: queued users %s", res["queued"])


async def _loop(stop: asyncio.Event) -> None:
    import anyio
    while not stop.is_set():
        try:
            await anyio.to_thread.run_sync(_tick)
        except Exception as e:  # noqa: BLE001  OW down / restarting: try again next round
            log.warning("ow-watch round failed: %s: %s", type(e).__name__, e)
        try:
            await asyncio.wait_for(stop.wait(), P.OW_WATCH_POLL_S)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def watcher():
    """Runs the poller while the API is up (api.lifespan); does nothing when disabled."""
    if not enabled():
        yield
        return
    stop = asyncio.Event()
    task = asyncio.create_task(_loop(stop))
    try:
        yield
    finally:
        stop.set()
        await task
