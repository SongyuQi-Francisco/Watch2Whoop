"""Admin side of circles (#199, CLI `body-engine circle-admin`): make a circle for a group (a club, a society) with an owner,
a member cap and default scopes, and set or clear its avatar. Activation codes for it are in codes.py.

The owner joins the circle as its first member and shares its default scopes with everyone who joins later: pick an owner
who agreed to that (usually the deployer's own account, or the group's organiser). The owner must have given the circle
consent, as for any circle (store._require_consent).
"""
from __future__ import annotations

import psycopg

from ..images import store as images
from . import params as P, store

MAX_MEMBERS_LIMIT = 1000


def create_circle(conn: psycopg.Connection, name: str, owner_user_id: int, avatar: bytes | None = None,
                  max_members: int | None = None, default_scopes=None) -> dict:
    """{id, name, owner_id, max_members, default_scopes, avatar_image_id}."""
    name = store._clean_name(name)
    owner = conn.execute("SELECT id, status FROM users WHERE id = %s", (owner_user_id,)).fetchone()
    if owner is None or owner["status"] != "active":
        raise ValueError(f"用户 {owner_user_id} 不存在或不是 active")
    store._require_consent(conn, owner_user_id)
    if max_members is not None and not 2 <= max_members <= MAX_MEMBERS_LIMIT:
        raise ValueError(f"人数上限 2–{MAX_MEMBERS_LIMIT}")
    scopes = store.clean_scopes(default_scopes if default_scopes is not None else P.DEFAULT_SCOPES)
    if store._count_circles(conn, owner_user_id) >= P.MAX_CIRCLES_PER_USER:
        raise ValueError(f"圈主已经在 {P.MAX_CIRCLES_PER_USER} 个圈子里了")
    image_id = images.save(conn, avatar, "circle_avatar", owner_user_id) if avatar is not None else None
    row = conn.execute(
        """INSERT INTO circles (name, owner_id, max_members, default_scopes, avatar_image_id) VALUES (%s, %s, %s, %s, %s)
           RETURNING id, name, owner_id, max_members, default_scopes, avatar_image_id""",
        (name, owner_user_id, max_members, scopes, image_id)).fetchone()
    conn.execute("INSERT INTO circle_members (circle_id, user_id, scopes, join_via) VALUES (%s, %s, %s, 'created')",
                 (row["id"], owner_user_id, scopes))
    store.sync_user(conn, owner_user_id)
    return row


def set_avatar(conn: psycopg.Connection, circle_id: int, data: bytes | None, by_user_id: int | None = None) -> int | None:
    """Replace the circle's avatar (data=None clears it); the old image is deleted. Returns the new image id."""
    c = conn.execute("SELECT id, owner_id, avatar_image_id FROM circles WHERE id = %s FOR UPDATE", (circle_id,)).fetchone()
    if c is None:
        raise ValueError(f"没有圈子 {circle_id}")
    image_id = images.save(conn, data, "circle_avatar", by_user_id or c["owner_id"]) if data is not None else None
    conn.execute("UPDATE circles SET avatar_image_id = %s WHERE id = %s", (image_id, circle_id))
    if c["avatar_image_id"]:
        images.delete(conn, c["avatar_image_id"])
    return image_id


def list_circles(conn: psycopg.Connection) -> list[dict]:
    """Every circle that is not someone's personal one, with owner, member count, cap and whether it has an avatar."""
    return conn.execute(
        """SELECT c.id, c.name, c.owner_id, u.display_name AS owner_name, c.max_members, c.default_scopes,
                  c.avatar_image_id IS NOT NULL AS has_avatar, c.created_at,
                  (SELECT count(*) FROM circle_members m WHERE m.circle_id = c.id) AS members
           FROM circles c LEFT JOIN users u ON u.id = c.owner_id WHERE NOT c.personal ORDER BY c.id""").fetchall()


def find_circle(conn: psycopg.Connection, ref: str) -> int:
    """A circle by id, or by its exact name when that is unique."""
    if str(ref).isdigit():
        row = conn.execute("SELECT id FROM circles WHERE id = %s", (int(ref),)).fetchone()
        if row is None:
            raise ValueError(f"没有圈子 {ref}")
        return row["id"]
    rows = conn.execute("SELECT id FROM circles WHERE name = %s", (" ".join(ref.split()),)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"按名字找到 {len(rows)} 个圈子「{ref}」：用 id（circle-admin list）")
    return rows[0]["id"]
