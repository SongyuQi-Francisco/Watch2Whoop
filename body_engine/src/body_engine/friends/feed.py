"""Circle training feed (flag circle_feed, migration 635): a Hevy / Instagram-style post per person per local day.

- Auto posting: a person's training of a day (sets ticked done in training_session_logs — the page's own row and, with the
  Hevy import, the imported row of that day — plus the SparkyFitness workouts of that day, e.g. the ones the AI logged, all
  merged into the one post; watch / shortcut workouts with their summary numbers) appears in
  the feed of every circle where they ticked the `training` scope — no prompt. The consent text already covers it ("circle
  members see what I tick"), so nothing is posted to a circle without that scope. One post per person per day; the row in
  circle_feed_posts only anchors ❤️ and comments, the content is computed live at read time, so new sets update the post.
- A post cannot be deleted. Who may see one is decided at every read: the viewer shares a circle with the author in which the
  author ticked `training`, and auth.can_view agrees. Leaving a circle therefore hides everything at once. ECGs, labs and
  weight never enter a post (only exercise names, sets, reps, weight, duration, and per watch workout its summary: energy,
  average / maximum heart rate, distance, minutes per heart-rate zone and the TRIMP load — never a heart-rate series).
- Exercises: the page's row first, then the Hevy import, then SparkyFitness (the session the page submitted is skipped: the
  page's row is the same sets, or newer). A name is matched to the catalog and the owner's custom exercises (#192) through
  training/loads.Index; an unmatched name is shown as logged (the page draws a dumbbell) and counts in sets and volume (#194).
- ❤️ (apart from the one-tap cheers 👏 💤 💪), text comments (the writer deletes their own, anyone can report), a count of ❤️ /
  comments on my posts since I last opened the feed (the red dot), and a notification to the author through notify/
  (kinds circle.like / circle.comment, linking to the post; never for one's own post).
"""
from __future__ import annotations

from datetime import date, timedelta

import psycopg

from .. import auth
from ..i18n import L
from ..training import catalog, custom, loads, params as TP
from ..training.history import display_name
from ..web import views
from ..notify import notify
from ..rules import params as RP
from . import params as P, store
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


def _circles_between(conn, viewer_id: int, author_id: int) -> list[dict]:
    """The circles a post reaches the viewer through: [{id, name, avatar}] (avatar URL or null: the page shows the initial)."""
    return [{"id": r["id"], "name": r["name"], "avatar": store.avatar_url(r)} for r in conn.execute(
        """SELECT DISTINCT c.id, c.name, c.avatar_image_id FROM circle_members a JOIN circle_members b ON b.circle_id = a.circle_id
           JOIN circles c ON c.id = a.circle_id WHERE a.user_id = %s AND b.user_id = %s AND %s = ANY(b.scopes)
           ORDER BY c.name, c.id""", (viewer_id, author_id, P.FEED_SCOPE))]


def _circle_fields(refs: list[dict]) -> dict:
    # circles: names (as before); circle_refs: the same circles with id and avatar
    return {"circles": [c["name"] for c in refs], "circle_refs": refs}


def _post_for(conn, viewer_id: int, post_id: int) -> dict:
    """A post the viewer may see; otherwise 404 (not telling whether it exists)."""
    p = conn.execute("SELECT * FROM circle_feed_posts WHERE id = %s", (post_id,)).fetchone()
    if p is None or not may_see(conn, viewer_id, p["user_id"]):
        raise FriendsError(404, L("没有这条动态", "No such post"))
    return p


# ---------------------------------------------------------------- the content of one day
def _done_sets(ex: dict, imported: bool = False) -> list[dict]:
    """Sets that count: on the page's own row the ones ticked done; on an imported row (Hevy, status 'imported', no done
    ticks) or a SparkyFitness workout every set except warm-ups, the same rule as the muscle load and the training history volume."""
    if imported:
        sets = [s for s in ex.get("sets") or [] if str(s.get("type") or "").lower() not in RP.WARMUP_SET_TYPES]
    else:
        sets = [s for s in ex.get("sets") or [] if s.get("done")]
    return [s for s in sets if any(s.get(k) is not None for k in ("reps", "weight_kg", "duration_s"))]


class Author:
    """What one author's posts need, read once per feed request: the catalog index with their custom exercises and their
    SparkyFitness workouts (training/loads.load_history: the LOAD_LOOKBACK_DAYS window, cached for LOAD_HISTORY_TTL_S)."""

    def __init__(self, conn: psycopg.Connection, user_id: int, today: date | None = None):
        self.conn, self.uid = conn, user_id
        self.today = today or views.local_today(conn, user_id)
        self._index: loads.Index | None = None
        self._sparky: dict[date, list[dict]] = {}

    @property
    def index(self) -> loads.Index:
        if self._index is None:
            self._index = loads.Index(self.conn, self.uid)
        return self._index

    def sparky(self, day: date) -> list[dict]:
        """SparkyFitness workouts of the window holding `day` (Hevy rows are read from training_session_logs, not from here)."""
        end = self.today if self.today - timedelta(days=TP.LOAD_LOOKBACK_DAYS) <= day <= self.today else day
        if end not in self._sparky:
            hist, _err, _linked = loads.load_history(self.conn, self.uid, end)   # unlinked / unreadable: the local rows only
            self._sparky[end] = [x for x in hist if not str(x.get("id") or "").startswith("hevy:")]
        return self._sparky[end]

    def sparky_on(self, day: date, skip: set[str]) -> list[dict]:
        return [x for x in self.sparky(day) if x["date"][:10] == day.isoformat() and str(x.get("id")) not in skip]

    def sparky_before(self, day: date) -> list[dict]:
        return [x for x in self.sparky(day) if x["date"][:10] < day.isoformat()]

    def resolve(self, ex: dict) -> str | None:
        """The catalog / custom slug of a logged exercise: its own slug, else its SparkyFitness id or name (#194)."""
        return ex.get("slug") or self.index.slug_for(ex.get("exercise_id"), ex.get("name"))


def _key(slug: str | None, ex: dict) -> str:
    """An exercise's identity for records: its catalog slug, else (an unmatched name) the normalized name."""
    return slug or f"name:{catalog.norm(ex.get('name'))}"


def _rows(conn, user_id: int, cond: str, args: tuple) -> list[dict]:
    """training_session_logs rows (page first, then imported) with an `imported` flag."""
    return [{**r, "imported": r["status"] == "imported"} for r in conn.execute(
        f"""SELECT date, exercises, status, source, sparky_entry_id FROM training_session_logs WHERE user_id = %s AND {cond}
            ORDER BY date, source DESC""", (user_id, *args))]


def _logged(rows: list[dict], sparky: list[dict]) -> list[tuple[dict, list[dict]]]:
    """(exercise, counted sets) of the local rows and the SparkyFitness workouts, in that order; exercises without sets dropped."""
    out = [(ex, _done_sets(ex, r["imported"])) for r in rows for ex in r["exercises"] or []]
    out += [(ex, _done_sets(ex, True)) for s in sparky for ex in s.get("exercises") or []]
    return [(ex, sets) for ex, sets in out if sets]


def _prev_best(conn, a: Author, day: date) -> dict[str, float]:
    """Heaviest counted weight per exercise on earlier days (page, imported and SparkyFitness alike), for the 🏆 personal record."""
    best: dict[str, float] = {}
    for ex, sets in _logged(_rows(conn, a.uid, "date < %s", (day,)), a.sparky_before(day)):
        k = _key(a.resolve(ex), ex)
        for s in sets:
            if s.get("weight_kg"):
                best[k] = max(best.get(k, 0.0), float(s["weight_kg"]))
    return best


def _kind(t: str | None) -> str:
    t = t or ""
    return "strength" if t in TP.STRENGTH_TYPES or "strength" in t else "sport" if t in TP.SPORT_TYPES \
        else "cardio" if t in TP.CARDIO_TYPES else "other"


def _r(x):
    return None if x is None else round(x)


def _activity(w: dict) -> dict:
    """One watch / Shortcut workout as the post shows it: its summary numbers only (no heart-rate series)."""
    zones = [round(float(z or 0), 1) for z in (w["hr_zone_min"] or [])]
    return {"type": w["type"], "kind": _kind(w["type"]), "name": display_name(w["name_zh"]),
            "duration_min": _r(w["duration_min"]), "active_kcal": _r(w["active_kcal"]),
            "avg_hr": _r(w["avg_hr"]), "max_hr": _r(w["max_hr"]),
            "distance_km": round(w["distance_km"], 2) if w["distance_km"] else None,
            "hr_zones": zones if sum(zones) > 0 else None, "load": _r(w["trimp"])}


def _exercise(a: Author, ex: dict, sets: list[dict], prev: dict[str, float]) -> dict:
    slug = a.resolve(ex)
    cat = a.index.rows.get(slug or "") or {}
    if custom.id_of(slug) is not None:          # the owner's own exercise of the same name (#192): shown as they defined it
        zh, en = cat.get("name_zh"), cat.get("name_en") or cat.get("name_zh")
    else:                                       # as logged (an unmatched name included, #194), else the catalog's name
        zh = ex.get("name") or cat.get("name_zh") or slug
        en = cat.get("name_en") or zh
    top = max((float(s["weight_kg"]) for s in sets if s.get("weight_kg")), default=0.0)
    k = _key(slug, ex)
    return {
        "slug": slug,
        "name": L(zh, en),
        "thumb": next(iter(catalog.image_urls(cat.get("images") or [])[:1]), None),   # the demo picture, else the page draws the muscle
        "muscles": list(cat.get("primary_muscles") or []),                            # neither: a dumbbell (cardio, an unmatched name)
        "sets": [{k2: s[k2] for k2 in ("reps", "weight_kg", "duration_s") if s.get(k2) is not None} for s in sets],
        "volume_kg": round(sum((s.get("reps") or 0) * float(s.get("weight_kg") or 0) for s in sets), 1),
        "pr": bool(top and k in prev and top > prev[k]),   # a first lift is no record
    }


def content(conn: psycopg.Connection, user_id: int, day: date, author: Author | None = None) -> dict | None:
    """What the post of `user_id` on `day` shows right now, None when that day has no training. Total load (kg) = the sum of
    reps × weight over the counted sets; the watch workouts give the time, energy, heart rate, zones and load."""
    a = author or Author(conn, user_id)
    rows = _rows(conn, user_id, "date = %s", (day,))   # the page's row and an imported row of the same day -> one post
    submitted = {str(r["sparky_entry_id"]) for r in rows if r.get("sparky_entry_id")}
    works = conn.execute(
        """SELECT name_zh, type, duration_min, start_at, active_kcal, avg_hr, max_hr, distance_km, hr_zone_min, trimp
           FROM workouts WHERE user_id = %s AND local_date = %s AND dup_of IS NULL AND excluded IS NULL ORDER BY start_at""",
        (user_id, day)).fetchall()
    logged = _logged(rows, a.sparky_on(day, submitted))
    if not logged and not works:
        return None
    prev = _prev_best(conn, a, day) if logged else {}
    exercises = [_exercise(a, ex, sets, prev) for ex, sets in logged]
    acts = sorted((_activity(w) for w in works), key=lambda x: x["kind"] != "strength")   # the strength workout leads
    names = list(dict.fromkeys(x["name"] for x in acts))
    if exercises and not any(x["kind"] == "strength" for x in acts):
        names.insert(0, L("力量训练", "Strength training"))
    hr = [(x["avg_hr"], x["duration_min"] or 0) for x in acts if x["avg_hr"]]
    return {
        "title": " + ".join(names[:3]) if names else L("力量训练", "Strength training"),
        "duration_min": round(sum(w["duration_min"] for w in works)) if works else None,
        "started_at": works[0]["start_at"] if works else None,   # for the "3 h ago" in the post header
        "exercise_count": len(exercises), "set_count": sum(len(e["sets"]) for e in exercises),
        "volume_kg": round(sum(e["volume_kg"] for e in exercises), 1),
        "pr_count": sum(1 for e in exercises if e["pr"]),
        "exercises": exercises,
        # the watch's summary of the day's workouts (#194): totals for the stats row, one entry per workout for its detail line
        "active_kcal": round(sum(x["active_kcal"] for x in acts if x["active_kcal"])) if any(x["active_kcal"] for x in acts) else None,
        "avg_hr": round(sum(h * m for h, m in hr) / sum(m for _, m in hr)) if hr and sum(m for _, m in hr) else (hr[0][0] if hr else None),
        "max_hr": max((x["max_hr"] for x in acts if x["max_hr"]), default=None),
        "activities": acts,
        "no_exercises": not exercises and any(x["kind"] == "strength" for x in acts),   # a watch strength workout, no sets logged
    }


def ensure_recent(conn: psycopg.Connection, user_id: int, today: date | None = None, author: Author | None = None) -> int:
    """Make the posts of the last FEED_LOOKBACK_DAYS days that have training and no post yet; returns how many."""
    a = author or Author(conn, user_id, today)
    today = today or a.today
    since = today - timedelta(days=P.FEED_LOOKBACK_DAYS)
    days = {r["date"] for r in _rows(conn, user_id, "date >= %s", (since,))
            if any(_done_sets(e, r["imported"]) for e in r["exercises"] or [])}
    days |= {date.fromisoformat(x["date"][:10]) for x in a.sparky(today) if x["date"][:10] >= since.isoformat()
             and any(_done_sets(e, True) for e in x.get("exercises") or [])}
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


def _previews(conn, ids: list[int]) -> dict[int, list[dict]]:
    """The newest P.COMMENT_PREVIEW comments of each post (oldest first), shown under the post like Instagram does."""
    out: dict[int, list[dict]] = {}
    for r in conn.execute(
            f"""SELECT * FROM (SELECT c.post_id, c.id, c.user_id, c.body, u.display_name,
                       row_number() OVER (PARTITION BY c.post_id ORDER BY c.id DESC) AS rn
                FROM circle_feed_comments c JOIN circle_feed_posts p ON p.id = c.post_id JOIN users u ON u.id = c.user_id
                WHERE c.post_id = ANY(%s) AND c.deleted_at IS NULL AND {_WRITER_OK.format(w='c.user_id')}) t
                WHERE rn <= %s ORDER BY post_id, id""", (ids, P.COMMENT_PREVIEW)):
        out.setdefault(r["post_id"], []).append({"id": r["id"], "user": {"id": r["user_id"], "name": r["display_name"]}, "body": r["body"]})
    return out


def feed(conn: psycopg.Connection, viewer_id: int, before: str | None = None, limit: int = P.FEED_PAGE) -> dict:
    """Newest first: posts of everyone whose circles show me their training (and mine). `before` = the `next` of the
    previous page ("date:id"). A post whose day has no training any more is skipped."""
    authors = visible_authors(conn, viewer_id)
    limit = max(1, min(int(limit), 30))
    if not authors:
        return {"posts": [], "next": None, "has_circles": _has_circles(conn, viewer_id)}
    who = {a: Author(conn, a) for a in authors}   # one catalog index / SparkyFitness read per author for the whole page
    if before is None:
        for a in authors:
            ensure_recent(conn, a, author=who[a])
    cur = _cursor(before)
    users = {r["id"]: r["display_name"] for r in conn.execute("SELECT id, display_name FROM users WHERE id = ANY(%s)", (authors,))}
    out: list[dict] = []
    exhausted = False
    while len(out) < limit and not exhausted:
        rows = conn.execute(
            """SELECT * FROM circle_feed_posts WHERE user_id = ANY(%s) AND (%s::date IS NULL OR (date, id) < (%s::date, %s::bigint))
               ORDER BY date DESC, id DESC LIMIT %s""", (authors, *(cur[0] if cur else None,) * 2, cur[1] if cur else None, limit * 2)).fetchall()
        exhausted = len(rows) < limit * 2
        likes, comments = _counts(conn, viewer_id, [r["id"] for r in rows])
        previews = _previews(conn, [r["id"] for r in rows])
        for r in rows:
            cur = (r["date"], r["id"])
            body = content(conn, r["user_id"], r["date"], who[r["user_id"]])
            if body is None:
                continue
            lk = likes.get(r["id"]) or {}
            out.append({"id": r["id"], "date": r["date"], "user": {"id": r["user_id"], "name": users.get(r["user_id"], ""), "self": r["user_id"] == viewer_id},
                        **_circle_fields(_circles_between(conn, viewer_id, r["user_id"])), **body,
                        "likes": {"count": lk.get("n", 0), "mine": bool(lk.get("mine"))}, "comment_count": comments.get(r["id"], 0),
                        "preview_comments": previews.get(r["id"], [])})
            if len(out) == limit:
                break
    more = len(out) == limit and cur is not None and conn.execute(
        "SELECT 1 FROM circle_feed_posts WHERE user_id = ANY(%s) AND (date, id) < (%s, %s) LIMIT 1", (authors, *cur)).fetchone()
    return {"posts": out, "next": f"{cur[0].isoformat()}:{cur[1]}" if more else None, "has_circles": True,
            "preview_exercises": P.FEED_PREVIEW_EXERCISES, "comment_preview": P.COMMENT_PREVIEW, "comment_max": P.COMMENT_MAX}


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
