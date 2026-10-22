"""Which avatar URLs a viewer may be given (no other imports from the app, so friends/ pages can use it without a cycle)."""
from __future__ import annotations

import psycopg


def url(user_id: int, image_id: int | None) -> str | None:
    """Where the page loads a personal avatar; the image id in the query makes the URL change when the picture does."""
    return f"/v1/avatars/users/{user_id}?v={image_id}" if image_id else None


def shares_circle(conn: psycopg.Connection, viewer_id: int, user_id: int) -> bool:
    return viewer_id == user_id or conn.execute(
        """SELECT 1 FROM circle_members a JOIN circle_members b ON b.circle_id = a.circle_id
           WHERE a.user_id = %s AND b.user_id = %s LIMIT 1""", (viewer_id, user_id)).fetchone() is not None


def urls_for(conn: psycopg.Connection, viewer_id: int, user_ids) -> dict[int, str]:
    """{user id: avatar URL} for the people in `user_ids` who have one and that `viewer_id` may see (themselves, circle
    co-members). Everyone else is missing from the result, so the page shows their initial."""
    ids = sorted({int(i) for i in user_ids if i is not None})
    if not ids:
        return {}
    rows = conn.execute(
        """SELECT u.id, u.avatar_image_id FROM users u WHERE u.id = ANY(%(ids)s) AND u.avatar_image_id IS NOT NULL
             AND (u.id = %(v)s OR EXISTS (SELECT 1 FROM circle_members a JOIN circle_members b ON b.circle_id = a.circle_id
                                          WHERE a.user_id = %(v)s AND b.user_id = u.id))""", {"ids": ids, "v": viewer_id}).fetchall()
    return {r["id"]: url(r["id"], r["avatar_image_id"]) for r in rows}
