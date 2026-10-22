"""Circle training feed (flag circle_feed, migration 635): a Hevy / Instagram-style post per person per local day.

- Auto posting: a person's training of a day (sets ticked done in training_session_logs, watch / shortcut workouts) appears in
  the feed of every circle where they ticked the `training` scope — no prompt. The consent text already covers it ("circle
  members see what I tick"), so nothing is posted to a circle without that scope. One post per person per day; the row in
  circle_feed_posts only anchors ❤️ and comments, the content is computed live at read time, so new sets update the post.
- A post cannot be deleted. Who may see one is decided at every read: the viewer shares a circle with the author in which the
  author ticked `training`, and auth.can_view agrees. Leaving a circle therefore hides everything at once. ECGs, labs and
  weight never enter a post (only exercise names, sets, reps, weight, duration).
- ❤️ (apart from the one-tap cheers 👏 💤 💪), text comments (the writer deletes their own, anyone can report), a count of ❤️ /
  comments on my posts since I last opened the feed (the red dot), and a notification to the author through notify/
  (kinds circle.like / circle.comment, linking to the post; never for one's own post).
"""
from __future__ import annotations

from datetime import date, timedelta

import psycopg

from .. import auth
from ..i18n import L
from ..training import catalog
from ..web import views
from ..notify import notify
from . import params as P
from .store import FriendsError

LIKE, COMMENT = "circle.like", "circle.comment"   # notification kinds (category circle in notify/params.py)


def post_url(post_id: int) -> str:
    """Where a notification opens: the Friends tab's feed, scrolled to this post (circle-feed.js listens for body:link)."""
    return f"/app/?tab=friends&feed={post_id}"


# a ❤️ / comment counts while its writer is the post's author or still shares a circle with the author
_WRITER_OK = """({w} = p.user_id OR EXISTS (SELECT 1 FROM circle_members x JOIN circle_members y ON y.circle_id = x.circle_id
                                              WHERE x.user_id = {w} AND y.user_id = p.user_id))"""


# ---------------------------------------------------------------- who sees what
def may_see(conn: psycopg.Connection, viewer_id: int, author_id: int) -> bool:
    """Same circle, the author ticked `training` there (the viewer's own posts too), and the share grant agrees."""
    ok = conn.execute(
        """SELECT 1 FROM circle_members a JOIN circle_members b ON b.circle_id = a.circle_id
           WHERE a.user_id = %s AND b.user_id = %s AND %s = ANY(b.scopes) LIMIT 1""",
        (viewer_id, author_id, P.FEED_SCOPE)).fetchone() is not None
    return ok and (viewer_id == author_id or auth.can_view(conn, viewer_id, author_id, P.FEED_SCOPE))


def visible_authors(conn: psycopg.Connection, viewer_id: int) -> list[int]:
    rows = conn.execute(
        """SELECT DISTINCT b.user_id FROM circle_members a JOIN circle_members b ON b.circle_id = a.circle_id
           WHERE a.user_id = %s AND %s = ANY(b.scopes)""", (viewer_id, P.FEED_SCOPE)).fetchall()
    return sorted(r["user_id"] for r in rows if r["user_id"] == viewer_id
                  or auth.can_view(conn, viewer_id, r["user_id"], P.FEED_SCOPE))


def _circles_between(conn, viewer_id: int, author_id: int) -> list[str]:
    return [r["name"] for r in conn.execute(
        """SELECT DISTINCT c.name FROM circle_members a JOIN circle_members b ON b.circle_id = a.circle_id
           JOIN circles c ON c.id = a.circle_id WHERE a.user_id = %s AND b.user_id = %s AND %s = ANY(b.scopes)
           ORDER BY c.name""", (viewer_id, author_id, P.FEED_SCOPE))]


def _post_for(conn, viewer_id: int, post_id: int) -> dict:
    """A post the viewer may see; otherwise 404 (not telling whether it exists)."""
    p = conn.execute("SELECT * FROM circle_feed_posts WHERE id = %s", (post_id,)).fetchone()
    if p is None or not may_see(conn, viewer_id, p["user_id"]):
        raise FriendsError(404, L("没有这条动态", "No such post"))
    return p


# ---------------------------------------------------------------- the content of one day
def _done_sets(ex: dict) -> list[dict]:
    return [s for s in ex.get("sets") or [] if s.get("done") and any(s.get(k) is not None for k in ("reps", "weight_kg", "duration_s"))]


def _prev_best(conn, user_id: int, day: date) -> dict[str, float]:
    """Heaviest ticked weight per exercise on earlier days, for the 🏆 personal record."""
    best: dict[str, float] = {}
    for r in conn.execute("SELECT exercises FROM training_session_logs WHERE user_id = %s AND date < %s", (user_id, day)):
        for ex in r["exercises"] or []:
            for s in _done_sets(ex):
                if s.get("weight_kg"):
                    best[ex["slug"]] = max(best.get(ex["slug"], 0.0), float(s["weight_kg"]))
    return best


def content(conn: psycopg.Connection, user_id: int, day: date, names: dict | None = None) -> dict | None:
    """What the post of `user_id` on `day` shows right now, None when that day has no training. Total load (kg) = the sum of
    reps × weight over the sets ticked done."""
    row = conn.execute("SELECT exercises FROM training_session_logs WHERE user_id = %s AND date = %s", (user_id, day)).fetchone()
    works = conn.execute(
        """SELECT name_zh, type, duration_min FROM workouts WHERE user_id = %s AND local_date = %s AND dup_of IS NULL
           AND excluded IS NULL ORDER BY start_at""", (user_id, day)).fetchall()
    exercises = []
    if row:
        names = names if names is not None else {r["slug"]: r for r in catalog.catalog_rows(conn)}
        prev = _prev_best(conn, user_id, day)
        for ex in row["exercises"] or []:
            sets = _done_sets(ex)
            if not sets:
                continue
            cat = names.get(ex["slug"]) or {}
            top = max((float(s["weight_kg"]) for s in sets if s.get("weight_kg")), default=0.0)
            exercises.append({
                "slug": ex["slug"],
                "name": L(ex.get("name") or cat.get("name_zh") or ex["slug"], cat.get("name_en") or ex.get("name") or ex["slug"]),
                "sets": [{k: s[k] for k in ("reps", "weight_kg", "duration_s") if s.get(k) is not None} for s in sets],
                "volume_kg": round(sum((s.get("reps") or 0) * float(s.get("weight_kg") or 0) for s in sets), 1),
                "pr": bool(top and ex["slug"] in prev and top > prev[ex["slug"]]),   # a first lift is no record
            })
    if not exercises and not works:
        return None
    lead = next((w for w in works if "strength" in (w["type"] or "")), works[0] if works else None)
    return {
        "title": lead["name_zh"] if lead else L("力量训练", "Strength training"),
        "duration_min": round(sum(w["duration_min"] for w in works)) if works else None,
        "exercise_count": len(exercises), "set_count": sum(len(e["sets"]) for e in exercises),
        "volume_kg": round(sum(e["volume_kg"] for e in exercises), 1),
        "pr_count": sum(1 for e in exercises if e["pr"]),
        "exercises": exercises,
    }


def ensure_recent(conn: psycopg.Connection, user_id: int, today: date | None = None) -> int:
    """Make the posts of the last FEED_LOOKBACK_DAYS days that have training and no post yet; returns how many."""
    today = today or views.local_today(conn, user_id)
    since = today - timedelta(days=P.FEED_LOOKBACK_DAYS)
    days = {r["date"] for r in conn.execute("SELECT date, exercises FROM training_session_logs WHERE user_id = %s AND date >= %s", (user_id, since))
            if any(_done_sets(e) for e in r["exercises"] or [])}
    days |= {r["local_date"] for r in conn.execute(
        "SELECT DISTINCT local_date FROM workouts WHERE user_id = %s AND local_date >= %s AND dup_of IS NULL AND excluded IS NULL",
        (user_id, since))}
    return sum(conn.execute("INSERT INTO circle_feed_posts (user_id, date) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING id",
                            (user_id, d)).fetchone() is not None for d in days)


# ---------------------------------------------------------------- the feed
def _counts(conn, viewer_id: int, ids: list[int]) -> tuple[dict, dict]:
    likes = {r["post_id"]: r for r in conn.execute(
        f"""SELECT l.post_id, count(*) AS n, bool_or(l.user_id = %s) AS mine FROM circle_feed_likes l
            JOIN circle_feed_posts p ON p.id = l.post_id
            WHERE l.post_id = ANY(%s) AND l.active AND {_WRITER_OK.format(w='l.user_id')} GROUP BY l.post_id""", (viewer_id, ids))}
    comments = {r["post_id"]: r["n"] for r in conn.execute(
        f"""SELECT c.post_id, count(*) AS n FROM circle_feed_comments c JOIN circle_feed_posts p ON p.id = c.post_id
            WHERE c.post_id = ANY(%s) AND c.deleted_at IS NULL AND {_WRITER_OK.format(w='c.user_id')} GROUP BY c.post_id""", (ids,))}
    return likes, comments


def feed(conn: psycopg.Connection, viewer_id: int, before: str | None = None, limit: int = P.FEED_PAGE) -> dict:
    """Newest first: posts of everyone whose circles show me their training (and mine). `before` = the `next` of the
    previous page ("date:id"). A post whose day has no training any more is skipped."""
    authors = visible_authors(conn, viewer_id)
    limit = max(1, min(int(limit), 30))
    if not authors:
        return {"posts": [], "next": None, "has_circles": _has_circles(conn, viewer_id)}
    if before is None:
        for a in authors:
            ensure_recent(conn, a)
    cur = _cursor(before)
    names = {r["slug"]: r for r in catalog.catalog_rows(conn)}
    users = {r["id"]: r["display_name"] for r in conn.execute("SELECT id, display_name FROM users WHERE id = ANY(%s)", (authors,))}
    out: list[dict] = []
    exhausted = False
    while len(out) < limit and not exhausted:
        rows = conn.execute(
            """SELECT * FROM circle_feed_posts WHERE user_id = ANY(%s) AND (%s::date IS NULL OR (date, id) < (%s::date, %s::bigint))
               ORDER BY date DESC, id DESC LIMIT %s""", (authors, *(cur[0] if cur else None,) * 2, cur[1] if cur else None, limit * 2)).fetchall()
        exhausted = len(rows) < limit * 2
        likes, comments = _counts(conn, viewer_id, [r["id"] for r in rows])
        for r in rows:
            cur = (r["date"], r["id"])
            body = content(conn, r["user_id"], r["date"], names)
            if body is None:
                continue
            lk = likes.get(r["id"]) or {}
            out.append({"id": r["id"], "date": r["date"], "user": {"id": r["user_id"], "name": users.get(r["user_id"], ""), "self": r["user_id"] == viewer_id},
                        "circles": _circles_between(conn, viewer_id, r["user_id"]), **body,
                        "likes": {"count": lk.get("n", 0), "mine": bool(lk.get("mine"))}, "comment_count": comments.get(r["id"], 0)})
            if len(out) == limit:
                break
    more = len(out) == limit and cur is not None and conn.execute(
        "SELECT 1 FROM circle_feed_posts WHERE user_id = ANY(%s) AND (date, id) < (%s, %s) LIMIT 1", (authors, *cur)).fetchone()
    return {"posts": out, "next": f"{cur[0].isoformat()}:{cur[1]}" if more else None, "has_circles": True,
            "preview_exercises": P.FEED_PREVIEW_EXERCISES, "comment_max": P.COMMENT_MAX}


def _cursor(before: str | None):
    if not before:
        return None
    try:
        d, i = before.split(":")
        return date.fromisoformat(d), int(i)
    except ValueError:
        raise FriendsError(400, L("翻页参数不对", "Bad paging cursor"))


def _has_circles(conn, uid: int) -> bool:
    return conn.execute("SELECT 1 FROM circle_members WHERE user_id = %s LIMIT 1", (uid,)).fetchone() is not None


# ---------------------------------------------------------------- ❤️
def like(conn: psycopg.Connection, uid: int, post_id: int) -> dict:
    p = _post_for(conn, uid, post_id)
    new = conn.execute(
        """INSERT INTO circle_feed_likes (post_id, user_id) VALUES (%s, %s) ON CONFLICT (post_id, user_id)
           DO UPDATE SET active = TRUE WHERE NOT circle_feed_likes.active RETURNING (xmax = 0) AS inserted""", (post_id, uid)).fetchone()
    if new is not None and new["inserted"] and p["user_id"] != uid:   # only the first time: liking again does not notify again
        name = _name(conn, uid)
        notify(conn, p["user_id"], LIKE, {"zh": f"{name} 给你的训练点了 ❤️", "en": f"{name} ❤️ your workout"}, "",
               url=post_url(post_id), data={"actor_id": uid, "post_id": post_id})
    return _likes_of(conn, uid, p)


def unlike(conn: psycopg.Connection, uid: int, post_id: int) -> dict:
    p = _post_for(conn, uid, post_id)
    conn.execute("UPDATE circle_feed_likes SET active = FALSE WHERE post_id = %s AND user_id = %s", (post_id, uid))
    return _likes_of(conn, uid, p)


def _likes_of(conn, uid: int, p: dict) -> dict:
    likes, _ = _counts(conn, uid, [p["id"]])
    lk = likes.get(p["id"]) or {}
    return {"count": lk.get("n", 0), "mine": bool(lk.get("mine"))}


def _name(conn, uid: int) -> str:
    return conn.execute("SELECT display_name FROM users WHERE id = %s", (uid,)).fetchone()["display_name"]


# ---------------------------------------------------------------- comments
def comments(conn: psycopg.Connection, uid: int, post_id: int) -> list[dict]:
    _post_for(conn, uid, post_id)
    rows = conn.execute(
        f"""SELECT c.id, c.user_id, c.body, c.created_at, u.display_name FROM circle_feed_comments c
            JOIN circle_feed_posts p ON p.id = c.post_id JOIN users u ON u.id = c.user_id
            WHERE c.post_id = %s AND c.deleted_at IS NULL AND {_WRITER_OK.format(w='c.user_id')}
            ORDER BY c.id DESC LIMIT %s""", (post_id, P.COMMENTS_SHOWN)).fetchall()
    return [{"id": r["id"], "user": {"id": r["user_id"], "name": r["display_name"], "self": r["user_id"] == uid},
             "body": r["body"], "created_at": r["created_at"]} for r in reversed(rows)]


def clean_comment(text: str) -> str:
    text = " ".join((text or "").replace("\r", "\n").split("\n")).strip()   # one line of text, no formatting
    text = "".join(ch for ch in text if ch.isprintable())
    if not text:
        raise FriendsError(400, L("评论不能是空的", "A comment can't be empty"))
    if len(text) > P.COMMENT_MAX:
        raise FriendsError(400, L(f"评论最多 {P.COMMENT_MAX} 个字", f"A comment is at most {P.COMMENT_MAX} characters"))
    return text


def add_comment(conn: psycopg.Connection, uid: int, post_id: int, text: str) -> dict:
    p = _post_for(conn, uid, post_id)
    text = clean_comment(text)
    recent = conn.execute("SELECT count(*) AS n FROM circle_feed_comments WHERE user_id = %s AND created_at > now() - interval '1 hour'",
                          (uid,)).fetchone()["n"]
    if recent >= P.COMMENTS_PER_HOUR:
        raise FriendsError(429, L("评论太频繁了，稍后再试", "Too many comments, try again later"))
    c = conn.execute("INSERT INTO circle_feed_comments (post_id, user_id, body) VALUES (%s, %s, %s) RETURNING id, created_at",
                     (post_id, uid, text)).fetchone()
    name = _name(conn, uid)
    if p["user_id"] != uid:
        notify(conn, p["user_id"], COMMENT, {"zh": f"{name} 评论了你的训练", "en": f"{name} commented on your workout"},
               text[:120], url=post_url(post_id), data={"actor_id": uid, "post_id": post_id})
    return {"id": c["id"], "user": {"id": uid, "name": name, "self": True}, "body": text, "created_at": c["created_at"]}


def delete_comment(conn: psycopg.Connection, uid: int, comment_id: int) -> None:
    """Only the writer deletes a comment (the post's author can't; they can report it)."""
    row = conn.execute("UPDATE circle_feed_comments SET deleted_at = now() WHERE id = %s AND user_id = %s AND deleted_at IS NULL RETURNING id",
                       (comment_id, uid)).fetchone()
    if row is None:
        raise FriendsError(404, L("没有这条评论，或不是你写的", "No such comment, or it isn't yours"))


def report_comment(conn: psycopg.Connection, uid: int, comment_id: int, reason: str | None = None) -> dict:
    c = conn.execute("SELECT * FROM circle_feed_comments WHERE id = %s AND deleted_at IS NULL", (comment_id,)).fetchone()
    if c is None:
        raise FriendsError(404, L("没有这条评论", "No such comment"))
    _post_for(conn, uid, c["post_id"])
    if c["user_id"] == uid:
        raise FriendsError(400, L("不能举报自己的评论（可以直接删除）", "You can't report your own comment (delete it instead)"))
    reason = (reason or "").strip()[:P.REPORT_REASON_MAX] or None
    conn.execute("INSERT INTO circle_feed_reports (comment_id, reporter_id, reason) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                 (comment_id, uid, reason))
    return {"reported": True}


# ---------------------------------------------------------------- red dot
def unread(conn: psycopg.Connection, uid: int) -> int:
    """❤️ and comments by others on my posts since I last opened the feed. The first call starts the clock (no old backlog)."""
    row = conn.execute("INSERT INTO circle_feed_seen (user_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING seen_at", (uid,)).fetchone()
    if row is not None:
        return 0
    seen = conn.execute("SELECT seen_at FROM circle_feed_seen WHERE user_id = %s", (uid,)).fetchone()["seen_at"]
    n = conn.execute(
        f"""SELECT (SELECT count(*) FROM circle_feed_likes l JOIN circle_feed_posts p ON p.id = l.post_id
                    WHERE p.user_id = %(u)s AND l.user_id <> %(u)s AND l.active AND l.created_at > %(t)s AND {_WRITER_OK.format(w='l.user_id')})
                 + (SELECT count(*) FROM circle_feed_comments c JOIN circle_feed_posts p ON p.id = c.post_id
                    WHERE p.user_id = %(u)s AND c.user_id <> %(u)s AND c.deleted_at IS NULL AND c.created_at > %(t)s
                      AND {_WRITER_OK.format(w='c.user_id')}) AS n""", {"u": uid, "t": seen}).fetchone()["n"]
    return int(n)


def mark_seen(conn: psycopg.Connection, uid: int) -> None:
    conn.execute("""INSERT INTO circle_feed_seen (user_id) VALUES (%s)
                    ON CONFLICT (user_id) DO UPDATE SET seen_at = now()""", (uid,))
