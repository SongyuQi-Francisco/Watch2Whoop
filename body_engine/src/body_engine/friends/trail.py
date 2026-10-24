"""How each member came into a circle (#269, docs/circles-redesign.md §5; columns of migration 745).

Every join records circle_members.join_via / invited_by / invite_id / circle_code_id (store.py, codes.py, admin.py).
Only the circle's owner sees it, in the member list of circle_view, while the flag is on for them; the other members never
get these fields. The page writes the words; this returns the facts.
"""
from __future__ import annotations

import psycopg

from ..rollout import flags
from . import remarks

FLAG = "circle_join_trail"


def for_owner(conn: psycopg.Connection, viewer_id: int, circle_id: int) -> dict[int, dict]:
    """{member id: {via, by, code, inferred}} for the circle's owner. via: created / invite_code / referral / circle_code /
    null (not recorded); by: {id, display_name, remark} of who invited (null: none, or the account is gone); code: the
    activation code's text for circle_code (a promo code, not a secret). Empty while the flag is off for the viewer."""
    if not flags.enabled(conn, viewer_id, FLAG):
        return {}
    rows = conn.execute(
        """SELECT m.user_id, m.join_via, m.invited_by, m.join_inferred, u.display_name AS by_name, k.code
           FROM circle_members m LEFT JOIN users u ON u.id = m.invited_by LEFT JOIN circle_codes k ON k.id = m.circle_code_id
           WHERE m.circle_id = %s""", (circle_id,)).fetchall()
    nick = remarks.for_viewer(conn, viewer_id, sorted({r["invited_by"] for r in rows if r["invited_by"]}))
    return {r["user_id"]: {
        "via": r["join_via"],
        "by": {"id": r["invited_by"], "display_name": r["by_name"], "remark": nick.get(r["invited_by"]),
               "me": r["invited_by"] == viewer_id} if r["invited_by"] else None,
        "code": r["code"] if r["join_via"] == "circle_code" else None,
        "inferred": r["join_inferred"]} for r in rows}
