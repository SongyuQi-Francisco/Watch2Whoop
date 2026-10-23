"""Private nicknames for circle members (#212, rollout flag member_remarks, migration 685).

A remark is what the viewer calls someone: set per person (not per circle), seen by the viewer only (no notification,
never in anyone else's response). It can be set only while the two share a circle; a remark on someone who no longer
shares a circle with the viewer is kept but not given out (for_viewer), and shows again once they share one again.
Pages show the remark with the original name in small type; the AI gets it as `nickname` next to the name.
No other app imports here (only the rollout flags), so friends/ views and web/views.py can use it without a cycle.
"""
from __future__ import annotations

import psycopg

from ..rollout import flags
from . import params as P

FLAG = "member_remarks"


class RemarkError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def clean(text: str | None) -> str | None:
    """Trimmed remark, None when empty (= clear). Line breaks and control characters become spaces / go."""
    text = "".join(ch if ch.isprintable() else " " for ch in (text or "")).strip()
    if not text:
        return None
    if len(text) > P.REMARK_MAX:
        raise RemarkError(400, f"备注最多 {P.REMARK_MAX} 个字")
    return text


def _share_circle(conn, a: int, b: int) -> bool:
    return conn.execute("""SELECT 1 FROM circle_members x JOIN circle_members y ON y.circle_id = x.circle_id
                           WHERE x.user_id = %s AND y.user_id = %s LIMIT 1""", (a, b)).fetchone() is not None


def set_remark(conn: psycopg.Connection, viewer_id: int, target_id: int, text: str | None) -> dict:
    """Set (or clear, with an empty text) what `viewer_id` calls `target_id`. 404 unless they share a circle now, the same
    answer as for someone who does not exist."""
    if target_id == viewer_id:
        raise RemarkError(400, "不能给自己设备注")
    remark = clean(text)
    if not _share_circle(conn, viewer_id, target_id):
        raise RemarkError(404, "这个人不在你的圈子里")
    if remark is None:
        conn.execute("DELETE FROM circle_member_remarks WHERE viewer_id = %s AND target_id = %s", (viewer_id, target_id))
    else:
        conn.execute("""INSERT INTO circle_member_remarks (viewer_id, target_id, remark) VALUES (%s, %s, %s)
                        ON CONFLICT (viewer_id, target_id) DO UPDATE SET remark = EXCLUDED.remark, updated_at = now()""",
                     (viewer_id, target_id, remark))
    return {"target_id": target_id, "remark": remark}


def for_viewer(conn: psycopg.Connection, viewer_id: int, user_ids) -> dict[int, str]:
    """{user id: remark} of the people in `user_ids` the viewer gave a remark and shares a circle with right now. Empty
    while the flag is off for the viewer (the pages then show the plain names)."""
    ids = sorted({int(i) for i in user_ids if i is not None and int(i) != viewer_id})
    if not ids or not flags.enabled(conn, viewer_id, FLAG):
        return {}
    rows = conn.execute(
        """SELECT r.target_id, r.remark FROM circle_member_remarks r
           WHERE r.viewer_id = %(v)s AND r.target_id = ANY(%(ids)s)
             AND EXISTS (SELECT 1 FROM circle_members a JOIN circle_members b ON b.circle_id = a.circle_id
                         WHERE a.user_id = %(v)s AND b.user_id = r.target_id)""", {"v": viewer_id, "ids": ids}).fetchall()
    return {r["target_id"]: r["remark"] for r in rows}
