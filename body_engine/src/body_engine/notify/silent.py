"""Silent pushes (content-available) when a user's data changed (#340 N2): the native app wakes up and pulls.

The change log (sync_changes, nativesync/) is the trigger, so every writer counts (imports, scores, records, the AI). A
loop in the API process (watcher(), started by api.lifespan) looks every SILENT_TICK_S seconds for users with changes
newer than the last one they were told about, and sends one background push to each of their devices registered with
silent = true (only the native app asks for it), at most one per user per SILENT_MIN_INTERVAL_S.

Nothing happens without APNs configured: no query, no state. Several API workers: one tick at a time holds an advisory
lock; the others skip. The push carries no health data, only the user's new data version.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import psycopg

from ..nativesync import params as P
from . import core

log = logging.getLogger(__name__)
LOCK = (0x5359, 0x4E43)   # advisory lock ("SY", "NC"): one tick at a time across API workers


def payload(version: int) -> dict:
    return {"aps": {"content-available": 1}, "w2w": {"type": "data_changed", "data_version": version}}


HEADERS = {"apns-push-type": "background", "apns-priority": "5"}


def due(conn: psycopg.Connection) -> list[dict]:
    """Users with silent-push devices and changes since their last silent push (recent changes only)."""
    return conn.execute(
        """SELECT c.user_id, max(c.seq) AS seq FROM sync_changes c
           LEFT JOIN sync_push_state s ON s.user_id = c.user_id
           WHERE c.changed_at > now() - make_interval(mins => %(look)s) AND c.seq > coalesce(s.seq, 0)
             AND (s.pushed_at IS NULL OR s.pushed_at < now() - make_interval(secs => %(gap)s))
             AND EXISTS (SELECT 1 FROM push_devices d WHERE d.user_id = c.user_id AND d.silent)
           GROUP BY c.user_id ORDER BY c.user_id""",
        {"look": P.SILENT_LOOKBACK_MINUTES, "gap": P.SILENT_MIN_INTERVAL_S}).fetchall()


def tick(conn: psycopg.Connection) -> list[dict]:
    """One round. Returns [{user_id, seq, devices}] of the pushes sent (empty without APNs or without anything due)."""
    if not core.configured():
        return []
    if not conn.execute("SELECT pg_try_advisory_xact_lock(%s, %s) AS ok", LOCK).fetchone()["ok"]:
        conn.rollback()
        return []
    sent = []
    for r in due(conn):
        devices = conn.execute("SELECT id, token, environment FROM push_devices WHERE user_id = %s AND silent ORDER BY id",
                               (r["user_id"],)).fetchall()
        jobs = [{"device_id": d["id"], "token": d["token"], "environment": d["environment"],
                 "payload": payload(int(r["seq"])), "headers": HEADERS} for d in devices]
        core.send_jobs(conn, jobs)
        conn.execute("""INSERT INTO sync_push_state (user_id, seq, pushed_at) VALUES (%s, %s, now())
                        ON CONFLICT (user_id) DO UPDATE SET seq = EXCLUDED.seq, pushed_at = now()""", (r["user_id"], r["seq"]))
        sent.append({"user_id": r["user_id"], "seq": int(r["seq"]), "devices": len(jobs)})
    conn.commit()
    return sent


def _tick_once() -> None:
    from .. import api
    if api.pool is None or not core.configured():
        return
    try:
        with api.pool.connection() as conn:
            tick(conn)
    except Exception:  # noqa: BLE001  a failed round is retried at the next tick
        log.exception("silent push tick failed")


@asynccontextmanager
async def watcher():
    """Runs the loop for the lifetime of the API process; does nothing at all while APNs is not configured."""
    task = asyncio.create_task(_loop()) if core.configured() else None
    try:
        yield
    finally:
        if task:
            task.cancel()


async def _loop() -> None:
    while True:
        await asyncio.sleep(P.SILENT_TICK_S)
        await asyncio.to_thread(_tick_once)
