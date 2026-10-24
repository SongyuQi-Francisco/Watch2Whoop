"""A member's own handling of their circles (#269 P2, flag circle_prefs, migration 765, docs/circles-redesign.md §3).

pin    first in the switcher (at most params.MAX_PINNED)
mute   no ❤️ / comment notifications or red dot through this circle; its posts left out of the 「全部」 feed
archive  hidden from the switcher and from 「全部」 (members and feed), and muted; still listed under 「已归档」

Nothing here changes sharing: an archived or muted circle shares exactly as before, and the others in it see no
difference. Leaving the circle is what stops sharing. Only the member reads or writes their own prefs.
"""
from __future__ import annotations

import psycopg

from ..rollout import flags
from . import params as P
from .store import FriendsError

FLAG = "circle_prefs"


def on(conn: psycopg.Connection, uid: int) -> bool:
    return flags.enabled(conn, uid, FLAG)


def of(conn: psycopg.Connection, uid: int) -> dict[int, dict]:
    """{circle id: {pinned, muted, archived}} for the circles `uid` is in (only rows that exist)."""
    return {r["circle_id"]: {"pinned": r["pinned_at"] is not None, "muted": r["muted_at"] is not None,
                             "archived": r["archived_at"] is not None, "pinned_at": r["pinned_at"]}
            for r in conn.execute("""SELECT p.* FROM circle_member_prefs p JOIN circle_members m
                                     ON m.circle_id = p.circle_id AND m.user_id = p.user_id WHERE p.user_id = %s""", (uid,))}


def hidden(conn: psycopg.Connection, uid: int, feed: bool = False) -> list[int]:
    """Circles left out of the 「全部」 view: archived ones (feed=True: muted ones too). Empty while the flag is off."""
    if not on(conn, uid):
        return []
    return sorted(cid for cid, p in of(conn, uid).items() if p["archived"] or (feed and p["muted"]))


def recent(conn: psycopg.Connection, circle_ids: list[int]) -> dict[int, object]:
    """When something last happened in each circle: the newest member joining, or the newest training post of a member
    who shows their training there. Drives the 「最近」 order."""
    if not circle_ids:
        return {}
    return {r["id"]: r["at"] for r in conn.execute(
        """SELECT c.id, greatest(
                    (SELECT max(m.joined_at) FROM circle_members m WHERE m.circle_id = c.id),
                    (SELECT max(f.created_at) FROM circle_feed_posts f JOIN circle_members m
                       ON m.user_id = f.user_id AND m.circle_id = c.id AND %s = ANY(m.scopes))) AS at
           FROM circles c WHERE c.id = ANY(%s)""", (P.FEED_SCOPE, circle_ids))}


def set_prefs(conn: psycopg.Connection, uid: int, circle_id: int, pinned: bool | None = None, muted: bool | None = None,
              archived: bool | None = None) -> dict:
    """Change one's own pin / mute / archive for a circle (None = leave as is). Archiving unpins. 404 when not a member,
    409 past params.MAX_PINNED pinned circles."""
    if not conn.execute("SELECT 1 FROM circle_members WHERE circle_id = %s AND user_id = %s", (circle_id, uid)).fetchone():
        raise FriendsError(404, "没有这个圈子（或你不在里面）")
    conn.execute("INSERT INTO circle_member_prefs (user_id, circle_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (uid, circle_id))
    cur = conn.execute("SELECT * FROM circle_member_prefs WHERE user_id = %s AND circle_id = %s FOR UPDATE",
                       (uid, circle_id)).fetchone()
    if archived:
        pinned = False
    if pinned and cur["pinned_at"] is None:
        if cur["archived_at"] is not None and archived is not False:
            raise FriendsError(409, "已归档的圈子不能置顶，先取消归档")
        n = conn.execute("""SELECT count(*) AS n FROM circle_member_prefs p JOIN circle_members m
                            ON m.circle_id = p.circle_id AND m.user_id = p.user_id
                            WHERE p.user_id = %s AND p.pinned_at IS NOT NULL""", (uid,)).fetchone()["n"]
        if n >= P.MAX_PINNED:
            raise FriendsError(409, f"最多置顶 {P.MAX_PINNED} 个圈子")

    def col(flag, now):
        return "now()" if flag and now is None else ("NULL" if flag is False else None)
    sets = [f"{name} = {expr}" for name, expr in (("pinned_at", col(pinned, cur["pinned_at"])),
                                                   ("muted_at", col(muted, cur["muted_at"])),
                                                   ("archived_at", col(archived, cur["archived_at"]))) if expr]
    if sets:
        conn.execute(f"UPDATE circle_member_prefs SET {', '.join(sets)} WHERE user_id = %s AND circle_id = %s", (uid, circle_id))
    conn.execute("""DELETE FROM circle_member_prefs WHERE user_id = %s AND circle_id = %s
                    AND pinned_at IS NULL AND muted_at IS NULL AND archived_at IS NULL""", (uid, circle_id))
    p = of(conn, uid).get(circle_id) or {"pinned": False, "muted": False, "archived": False}
    return {"pinned": p["pinned"], "muted": p["muted"], "archived": p["archived"]}


def quiet(conn: psycopg.Connection, author_id: int, writer_id: int) -> bool:
    """Whether a ❤️ / comment by `writer_id` on `author_id`'s post should stay silent for the author: every circle the
    post reaches the writer through is muted or archived by the author. False while the flag is off for the author."""
    hide = hidden(conn, author_id, feed=True)
    if not hide or writer_id == author_id:
        return False
    return conn.execute(
        """SELECT 1 FROM circle_members a JOIN circle_members w ON w.circle_id = a.circle_id AND w.user_id = %s
           WHERE a.user_id = %s AND %s = ANY(a.scopes) AND NOT (a.circle_id = ANY(%s)) LIMIT 1""",
        (writer_id, author_id, P.FEED_SCOPE, hide)).fetchone() is None
