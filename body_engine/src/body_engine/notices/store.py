"""Admin notices (migration 775): one message to one user, shown once on their Today page.

Admins are the user ids in BODY_ADMIN_USER_IDS (comma separated; none by default, so the HTTP endpoints answer 403 until
the deployer sets it; the command line needs no admin id). The same variable the private ops plugins use.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import psycopg

ADMIN_ENV = "BODY_ADMIN_USER_IDS"
MAX_TITLE = 80
MAX_BODY = 1000
MAX_LINK = 500
MAX_LABEL = 20
MAX_PENDING = 5          # the page shows at most this many waiting notices, oldest first
# https://… anywhere, a path of the Today page, or a link the app handles (watch2whoop://sync, …); nothing else
# (no javascript:, data:, http://)
LINK_RE = re.compile(r"^(https://[^\s<>\"']+|/app/[^\s<>\"']*|watch2whoop://[a-z-]+(\?[^\s<>\"']*)?)$")


def admin_ids() -> set[int]:
    return {int(x) for x in os.environ.get(ADMIN_ENV, "").replace(" ", "").split(",") if x.isdigit()}


def is_admin(user_id: int) -> bool:
    return user_id in admin_ids()


def _clean(text: str | None, limit: int, what: str, required: bool = False) -> str | None:
    t = (text or "").strip()
    if required and not t:
        raise ValueError(f"{what} is empty")
    if len(t) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    return t or None


def check_link(link: str | None) -> str | None:
    link = (link or "").strip() or None
    if link is None:
        return None
    if len(link) > MAX_LINK or not LINK_RE.match(link):
        raise ValueError("link must be https://…, /app/… or watch2whoop://…")
    return link


def send(conn: psycopg.Connection, user_id: int, title: str, body: str = "", link: str | None = None,
         link_label: str | None = None, sent_by: int | None = None, expires_days: int | None = None) -> dict:
    """Writes one notice (the caller commits). Raises ValueError for a bad text / link or an unknown user."""
    title = _clean(title, MAX_TITLE, "title", required=True)
    body = _clean(body, MAX_BODY, "body") or ""
    link = check_link(link)
    link_label = _clean(link_label, MAX_LABEL, "link label")
    if link_label and not link:
        raise ValueError("link label without a link")
    if expires_days is not None and not 1 <= expires_days <= 365:
        raise ValueError("expires_days must be 1–365")
    if conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone() is None:
        raise ValueError(f"no user {user_id}")
    expires = datetime.now(timezone.utc) + timedelta(days=expires_days) if expires_days else None
    return conn.execute(
        """INSERT INTO admin_notices (user_id, title, body, link, link_label, sent_by, expires_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, user_id, created_at, expires_at""",
        (user_id, title, body, link, link_label, sent_by, expires)).fetchone()


def pending(conn: psycopg.Connection, user_id: int) -> list[dict]:
    """The user's notices not seen yet (not withdrawn, not expired), oldest first."""
    return conn.execute(
        """SELECT id, title, body, link, link_label, created_at FROM admin_notices
           WHERE user_id = %s AND seen_at IS NULL AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())
           ORDER BY created_at, id LIMIT %s""", (user_id, MAX_PENDING)).fetchall()


def mark_seen(conn: psycopg.Connection, user_id: int, notice_id: int) -> bool:
    """True when it was the user's own unseen notice (the caller commits)."""
    return conn.execute("UPDATE admin_notices SET seen_at = now() WHERE id = %s AND user_id = %s AND seen_at IS NULL RETURNING id",
                        (notice_id, user_id)).fetchone() is not None


def revoke(conn: psycopg.Connection, notice_id: int) -> bool:
    """Withdraws a notice that was not seen yet (the caller commits)."""
    return conn.execute("""UPDATE admin_notices SET revoked_at = now() WHERE id = %s AND seen_at IS NULL AND revoked_at IS NULL
                           RETURNING id""", (notice_id,)).fetchone() is not None


def list_all(conn: psycopg.Connection, user_id: int | None = None, limit: int = 50) -> list[dict]:
    """For admins: the latest notices (of one user), with when they were seen."""
    return conn.execute(
        """SELECT id, user_id, title, body, link, link_label, sent_by, created_at, expires_at, seen_at, revoked_at
           FROM admin_notices WHERE (%(u)s::int IS NULL OR user_id = %(u)s) ORDER BY id DESC LIMIT %(n)s""",
        {"u": user_id, "n": limit}).fetchall()
