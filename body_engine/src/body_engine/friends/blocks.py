"""What blocks, reports and moderation hide, as SQL fragments and small lookups the read paths use (feed.py, cards.py,
store.py, avatars/urls.py). Writing blocks and reports, and the admin review, are in moderation.py. Imports nothing from the
other friends modules, so every one of them can use it without a cycle.

- A block (circle_blocks) hides both ways: the two people do not see each other's posts, comments, ❤️, member cards and
  leaderboard rows; there are no notifications or cheers between them, and circle sharing between them pauses
  (store._sync_one). They still see each other in a circle's member list, so they can leave or the owner can remove.
- A reported item (circle_moderation) is hidden from everyone who reported it; while its status is hidden (enough distinct
  reporters, params.REPORT_HIDE_AFTER) or removed (an admin), it is hidden from everyone except its author.
- A hidden / removed profile shows params.HIDDEN_NAME instead of the name and no picture (except to the person themselves).
"""
from __future__ import annotations

import psycopg

from ..i18n import L
from . import params as P

HIDDEN = ("hidden", "removed")


def not_blocked(v: str, w: str) -> str:
    """SQL: neither `v` nor `w` (column names or placeholders) blocked the other."""
    return (f"NOT EXISTS (SELECT 1 FROM circle_blocks bk WHERE (bk.user_id = {v} AND bk.blocked_user_id = {w})"
            f" OR (bk.user_id = {w} AND bk.blocked_user_id = {v}))")


def item_shown(kind: str, target: str, author: str, v: str) -> str:
    """SQL: the item `kind` / `target` written by `author` may be shown to `v` (its author always sees it)."""
    assert kind in P.REPORT_KINDS
    return (f"({author} = {v} OR NOT EXISTS (SELECT 1 FROM circle_moderation mo WHERE mo.kind = '{kind}' AND mo.target_id = {target}"
            f" AND (mo.status IN ('hidden', 'removed') OR EXISTS (SELECT 1 FROM circle_reports rp WHERE rp.item_id = mo.id"
            f" AND rp.user_id = {v}))))")


def pair_blocked(conn: psycopg.Connection, a: int, b: int) -> bool:
    return conn.execute(f"SELECT 1 WHERE NOT ({not_blocked('%s', '%s')})", (a, b, b, a)).fetchone() is not None


def hidden_people(conn: psycopg.Connection, uid: int) -> set[int]:
    """Everyone `uid` blocked or who blocked `uid`."""
    return {r["p"] for r in conn.execute(
        """SELECT blocked_user_id AS p FROM circle_blocks WHERE user_id = %s
           UNION SELECT user_id FROM circle_blocks WHERE blocked_user_id = %s""", (uid, uid))}


def blocked_by(conn: psycopg.Connection, uid: int) -> set[int]:
    """The people `uid` blocked (the 「已屏蔽」 list; a member row says 「已屏蔽」)."""
    return {r["blocked_user_id"] for r in conn.execute("SELECT blocked_user_id FROM circle_blocks WHERE user_id = %s", (uid,))}


def banned(conn: psycopg.Connection, uid: int) -> bool:
    return conn.execute("SELECT 1 FROM circle_bans WHERE user_id = %s", (uid,)).fetchone() is not None


def masked(conn: psycopg.Connection, viewer_id: int, user_ids) -> set[int]:
    """People in `user_ids` whose profile the viewer must not see (hidden / removed by moderation); never the viewer."""
    ids = sorted({int(i) for i in user_ids if i is not None and int(i) != viewer_id})
    if not ids:
        return set()
    return {r["target_id"] for r in conn.execute(
        "SELECT target_id FROM circle_moderation WHERE kind = 'profile' AND target_id = ANY(%s) AND status = ANY(%s)",
        (ids, list(HIDDEN)))}


def hidden_name() -> str:
    return L(*P.HIDDEN_NAME)


def mask_rows(conn: psycopg.Connection, viewer_id: int, rows, id_key: str = "id", name_key: str = "display_name"):
    """Replaces the name of every masked person in `rows` (dicts, changed in place); returns the masked ids."""
    rows = list(rows)
    hide = masked(conn, viewer_id, [r[id_key] for r in rows])
    for r in rows:
        if r[id_key] in hide:
            r[name_key] = hidden_name()
    return hide
