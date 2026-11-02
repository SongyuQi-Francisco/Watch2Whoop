"""Inviting someone you already know into a circle of yours (issue #359 section 3, flag inbox).

- Only someone who shares at least one circle with you: there is no search, and everything else (no such person, no shared
  circle, a block either way, a hidden profile, yourself) gets the same 404, so this cannot be used to find out who is here.
- Only into a circle you own: the rule for every other kind of invite (store.mint_invite).
- At most params.DIRECT_INVITES_PER_DAY in any 24 hours, one open invite per person and circle.
- You see 「待处理」 or 「已接受」, never that someone said no: a declined invite, or one to someone who muted you, looks
  pending until it expires (params.DIRECT_INVITE_DAYS), and only then can you invite that person to that circle again. An invite
  to someone who muted you is stored already closed, so it never reaches them.
- Nothing is shared by inviting. The person sees a card saying what joining shares and decides (inbox/store.py).
"""
from __future__ import annotations

import psycopg

from . import blocks, moderation, notes, params as P, store
from .store import FriendsError


def _require_known(conn: psycopg.Connection, uid: int, target: int) -> None:
    moderation.require_allowed(conn, uid)
    if target == uid or blocks.pair_blocked(conn, uid, target) or blocks.masked(conn, uid, [target]) \
            or not moderation._shares_circle(conn, uid, target):
        raise FriendsError(404, "这个人不在你的圈子里")


def _status(conn: psycopg.Connection, uid: int, circle: dict, target: int) -> str:
    """open / pending / accepted (came in through my invite) / member / full, as the person inviting may know it."""
    m = conn.execute("SELECT join_via, invited_by FROM circle_members WHERE circle_id = %s AND user_id = %s",
                     (circle["id"], target)).fetchone()
    if m is not None:
        return "accepted" if m["invited_by"] == uid and m["join_via"] == "member_invite" else "member"
    if conn.execute("SELECT 1 FROM circle_invites WHERE circle_id = %s AND invitee_user_id = %s AND expires_at > now() LIMIT 1",
                    (circle["id"], target)).fetchone():
        return "pending"
    return "full" if store.members_and_waiting(conn, circle["id"]) >= store.max_members(circle) else "open"


def _sent_today(conn: psycopg.Connection, uid: int) -> int:
    return conn.execute("""SELECT count(*) AS n FROM circle_invites WHERE created_by = %s AND origin = 'member'
                           AND created_at > now() - interval '24 hours'""", (uid,)).fetchone()["n"]


def options(conn: psycopg.Connection, uid: int, target: int) -> dict:
    """The circles I own and where this person stands in each."""
    _require_known(conn, uid, target)
    store.ensure_personal(conn, uid)
    rows = conn.execute("SELECT * FROM circles WHERE owner_id = %s ORDER BY personal DESC, created_at, id", (uid,)).fetchall()
    return {"circles": [{"id": c["id"], "name": c["name"], "avatar": store.avatar_url(c),
                         "members": conn.execute("SELECT count(*) AS n FROM circle_members WHERE circle_id = %s",
                                                 (c["id"],)).fetchone()["n"],
                         "status": _status(conn, uid, c, target)} for c in rows],
            "per_day": P.DIRECT_INVITES_PER_DAY, "left_today": max(0, P.DIRECT_INVITES_PER_DAY - _sent_today(conn, uid))}


def send(conn: psycopg.Connection, uid: int, target: int, circle_id: int) -> dict:
    """One invite. Sending again changes nothing and answers with where it stands (`already`). {status, already?}"""
    _require_known(conn, uid, target)
    store._require_may_join(conn, uid)
    circle = store._circle_for_owner(conn, circle_id, uid)   # the row lock serialises two sends to the same circle
    status = _status(conn, uid, circle, target)
    if status in ("pending", "accepted", "member"):
        return {"status": status, "already": True}
    if status == "full":
        raise FriendsError(409, f"这个圈子已满（最多 {store.max_members(circle)} 人）")
    if _sent_today(conn, uid) >= P.DIRECT_INVITES_PER_DAY:
        raise FriendsError(429, f"今天发出的邀请到上限了（{P.DIRECT_INVITES_PER_DAY} 个），明天再试")
    closed = conn.execute(
        """SELECT EXISTS (SELECT 1 FROM circle_invite_mutes WHERE user_id = %(t)s AND muted_user_id = %(u)s)
                  OR EXISTS (SELECT 1 FROM circle_bans WHERE user_id = %(t)s)
                  OR EXISTS (SELECT 1 FROM users WHERE id = %(t)s AND status <> 'active') AS closed""",
        {"t": target, "u": uid}).fetchone()["closed"]
    row = conn.execute(
        """INSERT INTO circle_invites (circle_id, created_by, invitee_user_id, expires_at, origin, revoked_at)
           VALUES (%s, %s, %s, now() + make_interval(days => %s), 'member', CASE WHEN %s THEN now() END)
           RETURNING id""", (circle_id, uid, target, P.DIRECT_INVITE_DAYS, closed)).fetchone()
    if not closed:
        notes.invited(conn, target, uid, circle_id, row["id"])
    return {"status": "pending"}


def mute(conn: psycopg.Connection, uid: int, sender: int) -> None:
    """「不再接收此人的邀请」: closes what they sent me that is still open; what they send from now on never reaches me."""
    if sender == uid:
        return
    conn.execute("INSERT INTO circle_invite_mutes (user_id, muted_user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (uid, sender))
    for r in conn.execute("""UPDATE circle_invites SET revoked_at = now() WHERE invitee_user_id = %s AND created_by = %s
                             AND origin = 'member' AND revoked_at IS NULL RETURNING id""", (uid, sender)).fetchall():
        notes.invites_settled(conn, uid, invite_id=r["id"])


def muted(conn: psycopg.Connection, uid: int) -> list[dict]:
    """The people whose invites I turned off. Only their names, as I saw them in the circle."""
    rows = conn.execute(
        """SELECT m.muted_user_id AS id, u.display_name, m.created_at FROM circle_invite_mutes m JOIN users u ON u.id = m.muted_user_id
           WHERE m.user_id = %s ORDER BY m.created_at DESC""", (uid,)).fetchall()
    blocks.mask_rows(conn, uid, rows)
    return rows


def unmute(conn: psycopg.Connection, uid: int, sender: int) -> None:
    if conn.execute("DELETE FROM circle_invite_mutes WHERE user_id = %s AND muted_user_id = %s RETURNING user_id",
                    (uid, sender)).fetchone() is None:
        raise FriendsError(404, "这个人不在你不再接收邀请的名单里")
