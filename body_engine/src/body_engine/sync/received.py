"""When did data last reach the server for a user, whichever way it came: the same reckoning as the Phone sync health check
(sync/broken.py) plus the app's own upload and the Shortcut.

Newest of: ow_watch.ow_synced_at (Open Wearables' own stamp after each saved SDK batch, polled every 30 s), the newest sample
the OW import pulled (sync_state(ow).cursor), the last upload of the app's own sync engine (rawdata_uploads.received_at) and
the last Shortcut upload (shortcut_uploads.imported_at). Never later than now. Read only. Used by the Friends cards and the
AI's friends overview (friends/cards.py), so a person who synced 2 minutes ago is never shown as "not synced" only because
today's scores are not computed yet.
"""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg

_SQL = r"""SELECT greatest(
    (SELECT ow_synced_at FROM ow_watch WHERE user_id = %(u)s),
    (SELECT CASE WHEN cursor ~ '^\d{4}-\d{2}-\d{2}' THEN cursor::timestamptz END
       FROM sync_state WHERE user_id = %(u)s AND source = 'ow'),
    (SELECT max(received_at) FROM rawdata_uploads WHERE user_id = %(u)s),
    (SELECT max(imported_at) FROM shortcut_uploads WHERE user_id = %(u)s)) AS t"""


def last_received_at(conn: psycopg.Connection, user_id: int, now: datetime | None = None) -> datetime | None:
    """Time of the newest data that reached the server for `user_id` (None = never)."""
    t = conn.execute(_SQL, {"u": user_id}).fetchone()["t"]
    if t is None:
        return None
    now = now or datetime.now(timezone.utc)
    return min(t, now)
