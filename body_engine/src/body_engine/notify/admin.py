"""`body-engine notify status / test / prune` (cli.py): for the deployer, to check the APNs setup after adding the key."""
from __future__ import annotations

import psycopg

from . import core, store


def status(conn: psycopg.Connection) -> dict:
    devices = conn.execute("""SELECT user_id, environment, count(*) AS n, max(last_push_at) AS last_push,
                                     string_agg(DISTINCT last_status, ', ') AS statuses
                              FROM push_devices GROUP BY user_id, environment ORDER BY user_id""").fetchall()
    week = conn.execute("""SELECT kind, push_status, count(*) AS n FROM notifications WHERE created_at > now() - interval '7 days'
                           GROUP BY kind, push_status ORDER BY kind, push_status""").fetchall()
    return {"apns_configured": core.configured(), "devices": devices, "last_7_days": week}


def test(conn: psycopg.Connection, user_id: int) -> dict:
    """Pushes straight to the user's devices (no quiet hours, settings or flag) and also writes it in-app."""
    nid = conn.execute(
        """INSERT INTO notifications (user_id, kind, title, body, title_en, body_en, url, push_status)
           VALUES (%s, 'system.test', '测试通知', '收到这条说明推送已经接通', 'Test notification',
                   'If you see this, push notifications work', '/app/#notifications', 'test') RETURNING id""",
        (user_id,)).fetchone()
    if not core.configured():
        conn.commit()
        return {**nid, "sent": [], "note": "APNs is not configured (BODY_APNS_KEY_FILE / KEY_ID / TEAM_ID / BUNDLE_ID)"}
    jobs = [{"device_id": d["id"], "token": d["token"], "environment": d["environment"],
             "payload": {"aps": {"alert": {"title": "Watch2Whoop", "body": "测试通知 / Test notification"}, "sound": "default"},
                         "kind": "system.test", "nid": nid["id"], "url": "/app/#notifications"},
             "headers": {"apns-push-type": "alert", "apns-priority": "10"}} for d in core._devices(conn, user_id)]
    out = core.send_jobs(conn, jobs)
    conn.commit()
    return {**nid, "sent": out}


def prune(conn: psycopg.Connection, days: int) -> dict:
    n = store.prune(conn, days)
    conn.commit()
    return {"deleted": n}
