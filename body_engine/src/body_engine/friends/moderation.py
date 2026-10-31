"""Circle moderation (App Store guideline 1.2, migration 1100): block, report, the admin review queue, bans.

People (every function acts for `uid`, the caller):
- block / unblock / blocked_list: per person, across all circles; only someone who shares a circle with the caller (or is
  already blocked) — the same 404 as for someone who does not exist. What a block hides: blocks.py.
- report(kind, id, reason, note): a post, a comment or a person's profile (name + picture), reason one of
  params.REPORT_REASONS. Hidden for the reporter at once; reporting a profile also blocks that person for the reporter.
  When params.REPORT_HIDE_AFTER distinct people reported it (counting only reports after an admin last chose keep), the item
  is hidden for everyone but its author until reviewed. Reporting the same item twice changes nothing.
Admins (BODY_ADMIN_USER_IDS for the HTTP endpoints; the command line `body-engine circles-reports`):
- queue(): open and auto-hidden items, oldest first, with the reported text / name, reasons and counts.
- resolve(item, action): keep (shown again, reports so far no longer count), remove (a comment is deleted, a post hidden
  for everyone but its author, a profile's name hidden and its picture deleted), ban-user (remove + the author is taken out
  of every circle and may not create / join / comment / ❤️ / cheer). Every decision and every automatic hide is written to
  circle_moderation_log.
- overdue(): items waiting longer than params.REVIEW_WITHIN_HOURS — for a doctor-style health check (private ops call it).
"""
from __future__ import annotations

import psycopg

from ..i18n import L
from ..images import store as images
from . import blocks, params as P, store
from .store import FriendsError

ACTIONS = ("keep", "remove", "ban-user")


# ---------------------------------------------------------------- bans
def require_allowed(conn: psycopg.Connection, uid: int) -> None:
    """Raises 403 for someone an admin banned from circles."""
    if blocks.banned(conn, uid):
        raise FriendsError(403, L("你已不能使用圈子", "You can no longer use circles"))


# ---------------------------------------------------------------- blocks
def _shares_circle(conn, a: int, b: int) -> bool:
    return conn.execute("""SELECT 1 FROM circle_members x JOIN circle_members y ON y.circle_id = x.circle_id
                           WHERE x.user_id = %s AND y.user_id = %s LIMIT 1""", (a, b)).fetchone() is not None


def block(conn: psycopg.Connection, uid: int, target_id: int, source: str = "block") -> dict:
    if target_id == uid:
        raise FriendsError(400, L("不能屏蔽自己", "You can't block yourself"))
    if target_id not in blocks.blocked_by(conn, uid) and not _shares_circle(conn, uid, target_id):
        raise FriendsError(404, L("这个人不在你的圈子里", "This person is not in your circles"))
    conn.execute("""INSERT INTO circle_blocks (user_id, blocked_user_id, source) VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, blocked_user_id) DO NOTHING""", (uid, target_id, source))
    store.sync_pair(conn, uid, target_id)   # circle sharing between the two pauses
    return {"user_id": target_id, "blocked": True}


def unblock(conn: psycopg.Connection, uid: int, target_id: int) -> dict:
    if conn.execute("DELETE FROM circle_blocks WHERE user_id = %s AND blocked_user_id = %s RETURNING 1",
                    (uid, target_id)).fetchone() is None:
        raise FriendsError(404, L("没有屏蔽这个人", "This person is not blocked"))
    store.sync_pair(conn, uid, target_id)   # shares again what both still pick in common circles (unless the other blocked)
    return {"user_id": target_id, "blocked": False}


def blocked_list(conn: psycopg.Connection, uid: int) -> list[dict]:
    """The 「已屏蔽」 list: who, since when. Names of hidden / removed profiles stay hidden; no pictures."""
    rows = conn.execute(
        """SELECT b.blocked_user_id AS id, u.display_name, b.created_at, b.source FROM circle_blocks b
           JOIN users u ON u.id = b.blocked_user_id WHERE b.user_id = %s ORDER BY b.created_at DESC""", (uid,)).fetchall()
    blocks.mask_rows(conn, uid, rows)
    return rows


# ---------------------------------------------------------------- reports
def _target(conn, uid: int, kind: str, target_id: int) -> int:
    """The author of an item the caller may see right now (else 404, never telling whether it exists)."""
    from . import feed
    if kind == "comment":
        c = conn.execute("SELECT id, post_id, user_id FROM circle_feed_comments WHERE id = %s AND deleted_at IS NULL",
                         (target_id,)).fetchone()
        if c is None or not feed.comment_visible(conn, uid, c["id"]):
            raise FriendsError(404, L("没有这条评论", "No such comment"))
        author = c["user_id"]
    elif kind == "post":
        author = feed._post_for(conn, uid, target_id)["user_id"]   # 404 also once the caller reported it
    else:
        if not _shares_circle(conn, uid, target_id):
            raise FriendsError(404, L("这个人不在你的圈子里", "This person is not in your circles"))
        author = target_id
    if author == uid:
        raise FriendsError(400, L("不能举报自己的内容", "You can't report your own content"))
    return author


def report(conn: psycopg.Connection, uid: int, kind: str, target_id: int, reason: str | None, note: str | None = None) -> dict:
    """Report an item; returns {reported, hidden_for_everyone}."""
    if kind not in P.REPORT_KINDS:
        raise FriendsError(400, L("只能举报动态、评论或成员资料", "Only posts, comments and profiles can be reported"))
    reason = reason if reason in P.REPORT_REASONS else "other"
    note = " ".join((note or "").split())[:P.REPORT_NOTE_MAX] or None
    author = _target(conn, uid, kind, target_id)
    item = conn.execute(
        """INSERT INTO circle_moderation (kind, target_id, user_id) VALUES (%s, %s, %s)
           ON CONFLICT (kind, target_id) DO UPDATE SET last_reported_at = now() RETURNING *""",
        (kind, target_id, author)).fetchone()
    new = conn.execute("""INSERT INTO circle_reports (item_id, user_id, reason, note) VALUES (%s, %s, %s, %s)
                          ON CONFLICT (item_id, user_id) DO NOTHING RETURNING id""", (item["id"], uid, reason, note)).fetchone()
    if kind == "profile":
        block(conn, uid, target_id, source="report")
    hidden = item["status"] in blocks.HIDDEN
    if new is not None and item["status"] in ("open", "kept"):
        n = conn.execute("""SELECT count(DISTINCT user_id) AS n FROM circle_reports WHERE item_id = %s
                            AND created_at > coalesce(%s, '-infinity'::timestamptz)""", (item["id"], item["kept_at"])).fetchone()["n"]
        if n >= P.REPORT_HIDE_AFTER:
            conn.execute("UPDATE circle_moderation SET status = 'hidden', hidden_at = now() WHERE id = %s", (item["id"],))
            _log(conn, item, "auto_hide", "auto", None, f"{n} reports")
            hidden = True
        elif item["status"] == "kept":   # reported again after a keep: back in the queue (still shown)
            conn.execute("UPDATE circle_moderation SET status = 'open', resolved_at = NULL, action = NULL WHERE id = %s",
                         (item["id"],))
    return {"reported": True, "hidden_for_everyone": hidden}


def _log(conn, item: dict, action: str, actor: str, admin_id: int | None, note: str | None = None) -> None:
    conn.execute("""INSERT INTO circle_moderation_log (item_id, user_id, admin_id, actor, action, note)
                    VALUES (%s, %s, %s, %s, %s, %s)""", (item["id"], item["user_id"], admin_id, actor, action, (note or None) and note[:500]))


# ---------------------------------------------------------------- admin review
def _content(conn, item: dict) -> dict:
    """What was reported, for the reviewer: the comment text, the profile's name / picture, or the post's day."""
    if item["kind"] == "comment":
        c = conn.execute("SELECT body, post_id, deleted_at FROM circle_feed_comments WHERE id = %s", (item["target_id"],)).fetchone()
        return {"text": c and c["body"], "post_id": c and c["post_id"], "deleted": bool(c and c["deleted_at"])} if c else {"gone": True}
    if item["kind"] == "post":
        p = conn.execute("SELECT date FROM circle_feed_posts WHERE id = %s", (item["target_id"],)).fetchone()
        return {"date": p["date"]} if p else {"gone": True}
    u = conn.execute("SELECT display_name, avatar_image_id IS NOT NULL AS has_avatar FROM users WHERE id = %s",
                     (item["target_id"],)).fetchone()
    return {"name": u["display_name"], "has_avatar": u["has_avatar"]} if u else {"gone": True}


def queue(conn: psycopg.Connection, status: str = "pending", limit: int = 100) -> list[dict]:
    """Reported items, oldest first. status: pending (open + hidden, the default), or one of open / hidden / kept / removed,
    or all."""
    states = {"pending": ["open", "hidden"], "all": ["open", "hidden", "kept", "removed"]}.get(status, [status])
    rows = conn.execute(
        """SELECT m.*, u.display_name AS author_name,
                  (SELECT count(*) FROM circle_reports r WHERE r.item_id = m.id) AS reports,
                  (SELECT json_object_agg(reason, n) FROM (SELECT reason, count(*) AS n FROM circle_reports r
                     WHERE r.item_id = m.id GROUP BY reason) x) AS reasons,
                  (SELECT json_agg(note ORDER BY id) FROM circle_reports r WHERE r.item_id = m.id AND note IS NOT NULL) AS notes,
                  round(extract(epoch FROM now() - m.first_reported_at) / 3600.0, 1) AS age_hours
           FROM circle_moderation m LEFT JOIN users u ON u.id = m.user_id
           WHERE m.status = ANY(%s) ORDER BY m.first_reported_at, m.id LIMIT %s""", (states, limit)).fetchall()
    for r in rows:
        r["content"] = _content(conn, r)
        r["author_banned"] = blocks.banned(conn, r["user_id"])
    return rows


def resolve(conn: psycopg.Connection, item_id: int, action: str, admin_id: int | None = None, note: str | None = None,
            actor: str = "cli") -> dict:
    """keep / remove / ban-user one item (ValueError for an unknown item or action). Audited in circle_moderation_log."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    item = conn.execute("SELECT * FROM circle_moderation WHERE id = %s FOR UPDATE", (item_id,)).fetchone()
    if item is None:
        raise ValueError(f"no reported item {item_id}")
    if action == "keep":
        conn.execute("""UPDATE circle_moderation SET status = 'kept', kept_at = now(), hidden_at = NULL, resolved_at = now(),
                        resolved_by = %s, action = 'keep' WHERE id = %s""", (admin_id, item_id))
    else:
        conn.execute("""UPDATE circle_moderation SET status = 'removed', resolved_at = now(), resolved_by = %s, action = %s
                        WHERE id = %s""", (admin_id, action, item_id))
        _remove(conn, item)
        if action == "ban-user":
            ban(conn, item["user_id"], admin_id, item_id, note)
    _log(conn, item, action, actor, admin_id, note)
    return conn.execute("SELECT * FROM circle_moderation WHERE id = %s", (item_id,)).fetchone()


def _remove(conn, item: dict) -> None:
    if item["kind"] == "comment":
        conn.execute("UPDATE circle_feed_comments SET deleted_at = coalesce(deleted_at, now()) WHERE id = %s", (item["target_id"],))
    elif item["kind"] == "profile":   # the picture goes; the name stays hidden behind params.HIDDEN_NAME (status removed)
        u = conn.execute("SELECT avatar_image_id FROM users WHERE id = %s FOR UPDATE", (item["target_id"],)).fetchone()
        if u and u["avatar_image_id"]:
            conn.execute("UPDATE users SET avatar_image_id = NULL WHERE id = %s", (item["target_id"],))
            images.delete(conn, u["avatar_image_id"])
    # a post stays for its author only (status removed hides it from everyone else)


def ban(conn: psycopg.Connection, uid: int, admin_id: int | None = None, item_id: int | None = None, note: str | None = None) -> None:
    """Bar `uid` from circles: out of every circle now (their circles pass on), no new ones."""
    conn.execute("""INSERT INTO circle_bans (user_id, banned_by, item_id, note) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id) DO NOTHING""", (uid, admin_id, item_id, (note or None) and note[:500]))
    store.leave_all(conn, uid)
    conn.execute("""UPDATE circle_invites SET revoked_at = now() WHERE revoked_at IS NULL
                    AND (invitee_user_id = %s OR created_by = %s)""", (uid, uid))


def unban(conn: psycopg.Connection, uid: int, admin_id: int | None = None, actor: str = "cli") -> bool:
    row = conn.execute("DELETE FROM circle_bans WHERE user_id = %s RETURNING item_id", (uid,)).fetchone()
    if row is None:
        return False
    conn.execute("""INSERT INTO circle_moderation_log (item_id, user_id, admin_id, actor, action) VALUES (%s, %s, %s, %s, 'unban')""",
                 (row["item_id"], uid, admin_id, actor))
    return True


def log(conn: psycopg.Connection, item_id: int | None = None, limit: int = 50) -> list[dict]:
    return conn.execute("""SELECT * FROM circle_moderation_log WHERE (%s::bigint IS NULL OR item_id = %s::bigint)
                           ORDER BY id DESC LIMIT %s""", (item_id, item_id, limit)).fetchall()


def overdue(conn: psycopg.Connection, hours: float = P.REVIEW_WITHIN_HOURS) -> dict:
    """For a health check: {pending, overdue, oldest_hours}: items waiting for review, those older than `hours`."""
    r = conn.execute(
        """SELECT count(*) AS pending, count(*) FILTER (WHERE first_reported_at < now() - make_interval(secs => %s)) AS overdue,
                  round(extract(epoch FROM now() - min(first_reported_at)) / 3600.0, 1) AS oldest_hours
           FROM circle_moderation WHERE status IN ('open', 'hidden')""", (hours * 3600,)).fetchone()
    return {"pending": r["pending"], "overdue": r["overdue"], "oldest_hours": float(r["oldest_hours"]) if r["oldest_hours"] is not None else None,
            "within_hours": hours}
