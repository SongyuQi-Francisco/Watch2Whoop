"""Set / clear / look up avatars. All functions act for one caller; the API (api.py) checks the flag and the token."""
from __future__ import annotations

import psycopg

from ..friends import admin, store as friends
from ..images import store as images
from .urls import shares_circle, url, urls_for  # noqa: F401  (re-exported)


class AvatarError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def get_user_image(conn: psycopg.Connection, viewer_id: int, user_id: int) -> dict | None:
    """The image row if the person has an avatar and the viewer may see it, else None (the API answers 404 for both)."""
    if not shares_circle(conn, viewer_id, user_id):
        return None
    row = conn.execute("SELECT avatar_image_id FROM users WHERE id = %s", (user_id,)).fetchone()
    return images.get(conn, row["avatar_image_id"]) if row and row["avatar_image_id"] else None


def set_user(conn: psycopg.Connection, user_id: int, data: bytes | None) -> int | None:
    """Replace the person's avatar (data=None removes it); the old image row is deleted. Returns the new image id."""
    old = conn.execute("SELECT avatar_image_id FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
    new = images.save(conn, data, "user_avatar", user_id) if data is not None else None
    conn.execute("UPDATE users SET avatar_image_id = %s WHERE id = %s", (new, user_id))
    if old and old["avatar_image_id"]:
        images.delete(conn, old["avatar_image_id"])
    return new


def set_circle(conn: psycopg.Connection, user_id: int, circle_id: int, data: bytes | None) -> int | None:
    """The circle's owner replaces or removes its logo (the admin CLI does the same without an owner check, friends/admin.py).
    A personal circle (everyone's own, shown as the person's own list) has no logo."""
    try:
        circle = friends._circle_for_owner(conn, circle_id, user_id)
    except friends.FriendsError as e:
        raise AvatarError(e.status, str(e)) from e
    if circle["personal"]:
        raise AvatarError(400, "个人圈子没有 logo")
    return admin.set_avatar(conn, circle_id, data, by_user_id=user_id)
