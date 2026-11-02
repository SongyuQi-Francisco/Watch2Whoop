"""What circles tell people through notify/ for the inbox (issue #359): someone joined your circle, your invite was accepted,
someone invited you. Written only while the flag `inbox` is on for the person told: with it off nothing changes for them.

Never between two people when either blocked the other, never about someone whose profile moderation hid, and nothing about a
circle the person muted or archived (the same rule as likes and comments, prefs.quiet). The inbox checks blocks and
moderation again on every read, so someone blocked after this was written still disappears from it.

A "circle.invite" notification only carries the push: the inbox lists the invite's card instead (inbox/store.py) and keeps the
card's read state here. invites_settled() closes it when the invite is answered, so the push badge does not stay up.
"""
from __future__ import annotations

import psycopg

from .. import auth
from ..notify import notify
from ..rollout import flags
from . import blocks, params as P

JOINED, ACCEPTED, INVITED = "circle.joined", "circle.invite_accepted", "circle.invite"


def circle_url(circle_id: int) -> str:
    """Where a joined / accepted notification leads: the Friends tab narrowed to this circle."""
    return f"/app/?tab=friends&circle_id={circle_id}"


def invite_url(invite_id: int) -> str:
    """Where an invite's push leads: its card in 「我 → 消息」."""
    return f"/app/?tab=me&inbox=invite:{invite_id}"


def _told(conn: psycopg.Connection, to: int | None, actor: int, circle_id: int | None = None) -> bool:
    if to is None or to == actor:
        return False
    if not flags.enabled(conn, to, P.INBOX_FLAG) or not flags.targeted(conn, to, auth.CIRCLES_FLAG):
        return False
    if blocks.pair_blocked(conn, to, actor) or blocks.masked(conn, to, [actor]):
        return False
    if circle_id is None:
        return True
    from . import prefs   # (prefs imports store, which imports this module)
    return circle_id not in prefs.hidden(conn, to, feed=True)


def _names(conn: psycopg.Connection, uid: int, circle_id: int) -> tuple[str, str, int | None]:
    who = conn.execute("SELECT display_name FROM users WHERE id = %s", (uid,)).fetchone()["display_name"] or "?"
    c = conn.execute("SELECT name, owner_id FROM circles WHERE id = %s", (circle_id,)).fetchone()
    return who, c["name"], c["owner_id"]


def member_joined(conn: psycopg.Connection, uid: int, circle_id: int, invite_maker: int | None = None) -> None:
    """`uid` just came into the circle. `invite_maker` (an invite addressed to them): that person hears their invite was
    accepted. The owner hears someone joined, unless the owner is that person: one message, not two."""
    who, name, owner = _names(conn, uid, circle_id)
    data = {"actor_id": uid, "circle_id": circle_id}
    if _told(conn, invite_maker, uid, circle_id):
        notify(conn, invite_maker, ACCEPTED, {"zh": f"{who} 接受了你的邀请，加入了「{name}」",
                                              "en": f"{who} accepted your invite to “{name}”"}, "",
               url=circle_url(circle_id), data=data)
    if owner != invite_maker and _told(conn, owner, uid, circle_id):
        notify(conn, owner, JOINED, {"zh": f"{who} 加入了你的圈子「{name}」", "en": f"{who} joined your circle “{name}”"}, "",
               url=circle_url(circle_id), data=data)


def invited(conn: psycopg.Connection, to: int, maker: int, circle_id: int, invite_id: int) -> None:
    """`maker` invited `to` (friends/direct.py, friends/admin.py)."""
    if not _told(conn, to, maker):
        return
    who, name, _ = _names(conn, maker, circle_id)
    notify(conn, to, INVITED, {"zh": f"{who} 邀请你加入「{name}」", "en": f"{who} invited you to join “{name}”"}, "",
           url=invite_url(invite_id), data={"actor_id": maker, "circle_id": circle_id, "invite_id": invite_id})


def invites_settled(conn: psycopg.Connection, uid: int, invite_id: int | None = None, circle_id: int | None = None) -> None:
    """The invite (or every invite to the circle, or all of them) was answered: its push no longer counts as unread."""
    conn.execute(
        """UPDATE notifications SET read_at = now() WHERE user_id = %(u)s AND kind = %(k)s AND read_at IS NULL
           AND (%(i)s::text IS NULL OR data->>'invite_id' = %(i)s::text)
           AND (%(c)s::text IS NULL OR data->>'circle_id' = %(c)s::text)""",
        {"u": uid, "k": INVITED, "i": invite_id, "c": circle_id})
