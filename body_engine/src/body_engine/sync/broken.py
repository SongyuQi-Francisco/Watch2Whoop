"""「手机同步已断开」: a user whose phone stopped reaching Open Wearables while they still use the app.

Seen in production: the OW SDK credentials of a phone became invalid, every upload got 401, the data stopped for days and
the user did not notice. The Today page asks GET /v1/users/me/sync/connection (sync/api.py) and, when broken, shows a
banner and once a day a sheet whose button reconnects the phone (web/static/sync-repair.js: a fresh OW invitation code
from POST /v1/account/ow-invitation, handed to the app). `body-engine sync-broken` lists these users for the admin.

Broken = all of (thresholds in sync/params.py):
- linked to OW (users.ow_user_id) and OW data reached us before: a user who never synced is onboarding, not broken;
- the last OW data is older than SYNC_BROKEN_AFTER but not older than SYNC_BROKEN_GIVE_UP;
- the server reached OW for this user within SYNC_BROKEN_SERVER_OK (sync_state(ow).last_synced_at, written by every
  successful import): when OW or the hourly import is down nobody's data moves, and it is not the phone;
- the user used the app within SYNC_BROKEN_APP_SEEN (a Today page session, or an upload of the app's own sync); the
  Today page asking counts as a visit.
Last OW data = the later of ow_watch.ow_synced_at (OW's own stamp after each saved SDK batch, polled every 30 s,
sync/ow_watch.py) and sync_state(ow).cursor (the newest sample the import pulled). Read only.
"""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg

from . import params as P

REASONS = ("broken", "ok", "not_linked", "never", "given_up", "server_stale", "not_seen")


def _ts(cursor: str | None) -> datetime | None:
    if not cursor:
        return None
    try:
        t = datetime.fromisoformat(cursor)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


_SQL = """
SELECT u.id, u.ow_user_id, w.ow_synced_at, s.last_synced_at AS imported_at, s.cursor,
       (SELECT max(last_seen_at) FROM web_sessions ws WHERE ws.user_id = u.id) AS web_seen,
       (SELECT max(received_at) FROM rawdata_uploads ru WHERE ru.user_id = u.id) AS app_upload
FROM users u
LEFT JOIN ow_watch w ON w.user_id = u.id
LEFT JOIN sync_state s ON s.user_id = u.id AND s.source = 'ow'
"""


def _judge(r: dict, now: datetime, visiting: bool) -> dict:
    last = max((t for t in (r["ow_synced_at"], _ts(r["cursor"])) if t is not None), default=None)
    seen = now if visiting else max((t for t in (r["web_seen"], r["app_upload"]) if t is not None), default=None)
    out = {"user_id": r["id"], "broken": False, "last_data_at": last,
           "hours": round((now - last).total_seconds() / 3600, 1) if last else None,
           "after_hours": int(P.SYNC_BROKEN_AFTER.total_seconds() // 3600), "app_seen_at": seen}
    if not r["ow_user_id"]:
        reason = "not_linked"
    elif last is None:
        reason = "never"
    elif now - last < P.SYNC_BROKEN_AFTER:
        reason = "ok"
    elif now - last > P.SYNC_BROKEN_GIVE_UP:
        reason = "given_up"
    elif r["imported_at"] is None or now - r["imported_at"] > P.SYNC_BROKEN_SERVER_OK:
        reason = "server_stale"
    elif seen is None or now - seen > P.SYNC_BROKEN_APP_SEEN:
        reason = "not_seen"
    else:
        reason = "broken"
    out["broken"] = reason == "broken"
    out["reason"] = reason
    return out


def check(conn: psycopg.Connection, user_id: int, now: datetime | None = None, visiting: bool = True) -> dict:
    """{broken, reason, last_data_at, hours, after_hours, app_seen_at} for one user. visiting: the user is on the page now."""
    r = conn.execute(_SQL + " WHERE u.id = %s", (user_id,)).fetchone()
    if r is None:
        raise ValueError(f"no user {user_id}")
    return _judge(r, now or datetime.now(timezone.utc), visiting)


def broken_users(conn: psycopg.Connection, now: datetime | None = None) -> list[dict]:
    """Every user whose phone sync looks broken (for `body-engine sync-broken`), oldest data first."""
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(_SQL + " WHERE u.ow_user_id IS NOT NULL ORDER BY u.id").fetchall()
    out = [x for x in (_judge(r, now, False) for r in rows) if x["broken"]]
    return sorted(out, key=lambda x: x["last_data_at"])
