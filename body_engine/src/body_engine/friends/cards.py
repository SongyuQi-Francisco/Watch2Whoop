"""Member cards on the Friends tab (docs/friends-social-plan.md §3.3) and one-tap cheers (nudges).

A card shows one person: today (status label from the recovery zone, recovery / sleep / strain, trained today, and when
data last reached the server: `state` scored / pending / stale / never, `last_sync_at`, never just the last scored day) and the last 7 days (per day: weekday, today mark, recovery zone, sleep, strain). Every block is checked
against the viewer's grant for that person (auth.can_view): scores → numbers, dots and bars; training → the trained badge.
A block the person did not share is null and listed in `hidden`. Dates are each person's own local dates.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import psycopg

from .. import auth
from ..rollout import flags
from ..web import views
from ..avatars import urls as avatar_store
from ..queries import _now, user_tz
from ..sync import received
from . import blocks, params as P, prefs, remarks, store

NUDGE_KINDS = ("clap", "sleep", "train")   # 👏 / 💤 / 💪


# ---------------------------------------------------------------- one circle at a time (#269 P1, flag circles_by_circle)
def by_circle_on(conn: psycopg.Connection, uid: int) -> bool:
    return flags.enabled(conn, uid, P.BY_CIRCLE_FLAG)


def expand(scopes) -> set[str]:
    """A member's picks in one circle as grants: nutrition implies nutrition_summary (like share_with); never ecg."""
    out = set(scopes or ())
    for narrow, wide in auth.IMPLIED_BY.items():
        if out & set(wide):
            out.add(narrow)
    return out - set(P.NEVER_SHARED)


def one_circle(conn: psycopg.Connection, viewer_id: int, circle_id) -> dict | None:
    """The circle a Friends view is narrowed to: {id, name, picks: {member id: scopes picked there}}. None = all circles
    (no circle asked for, or the flag is off for the viewer: then the old merged views). 404 when not a member."""
    if circle_id in (None, "") or not by_circle_on(conn, viewer_id):
        return None
    try:
        cid = int(circle_id)
    except (TypeError, ValueError):
        raise store.FriendsError(400, "圈子 id 不对") from None
    c = conn.execute("""SELECT c.id, c.name FROM circles c JOIN circle_members m ON m.circle_id = c.id AND m.user_id = %s
                        WHERE c.id = %s""", (viewer_id, cid)).fetchone()
    if c is None:
        raise store.FriendsError(404, "没有这个圈子（或你不在里面）")
    picks = {r["user_id"]: expand(r["scopes"]) for r in conn.execute(
        "SELECT user_id, scopes FROM circle_members WHERE circle_id = %s", (cid,))}
    return {"id": c["id"], "name": c["name"], "picks": picks}


def circle_list(conn: psycopg.Connection, uid: int) -> list[dict]:
    """The switcher on the Friends tab: my circles (oldest first), with member count and avatar. With the circle_prefs
    flag: my pin / mute / archive and the last activity, pinned first (in pinning order), then the most recent first."""
    out = [{"id": r["id"], "name": r["name"], "personal": r["personal"], "members": r["n"], "avatar": store.avatar_url(r)}
           for r in conn.execute(
               """SELECT c.id, c.name, c.personal, c.avatar_image_id, c.created_at,
                         (SELECT count(*) FROM circle_members x WHERE x.circle_id = c.id) AS n
                  FROM circles c JOIN circle_members m ON m.circle_id = c.id AND m.user_id = %s
                  ORDER BY c.created_at, c.id""", (uid,))]
    if not prefs.on(conn, uid):
        return out
    mine, last = prefs.of(conn, uid), prefs.recent(conn, [c["id"] for c in out])
    for c in out:
        p = mine.get(c["id"]) or {}
        c.update(pinned=bool(p.get("pinned")), muted=bool(p.get("muted")), archived=bool(p.get("archived")),
                 recent_at=last.get(c["id"]))
    pinned = sorted((c for c in out if c["pinned"]), key=lambda c: mine[c["id"]]["pinned_at"])
    rest = sorted((c for c in out if not c["pinned"]), key=lambda c: c["recent_at"].timestamp() if c["recent_at"] else 0,
                  reverse=True)
    return pinned + rest


def co_members(conn: psycopg.Connection, uid: int, circle_id: int | None = None, skip: list[int] = ()) -> list[dict]:
    """Everyone who shares at least one circle with `uid` (only circle `circle_id` when given; never through the circles
    in `skip`), with the names of those circles. Never someone either of the two blocked (member cards and leaderboards
    leave them out; friends/blocks.py); a profile hidden by moderation comes with params.HIDDEN_NAME."""
    rows = conn.execute(
        f"""SELECT u.id, u.display_name, array_agg(DISTINCT c.name ORDER BY c.name) AS circles, min(o.joined_at) AS since
           FROM circle_members m JOIN circle_members o ON o.circle_id = m.circle_id AND o.user_id <> m.user_id
           JOIN circles c ON c.id = m.circle_id JOIN users u ON u.id = o.user_id
           WHERE m.user_id = %(u)s AND (%(c)s::int IS NULL OR m.circle_id = %(c)s::int) AND NOT (m.circle_id = ANY(%(skip)s))
             AND {blocks.not_blocked('%(u)s', 'o.user_id')}
           GROUP BY u.id, u.display_name ORDER BY min(o.joined_at), u.id""",
        {"u": uid, "c": circle_id, "skip": list(skip)}).fetchall()
    blocks.mask_rows(conn, uid, rows)
    return rows


def share_with(conn: psycopg.Connection, viewer_id: int, owner_id: int) -> set[str]:
    """Scopes `owner_id` grants `viewer_id` right now (circles + hand-made, nutrition implies nutrition_summary)."""
    scopes = auth.granted_scopes(conn, viewer_id, owner_id)
    for narrow, wide in auth.IMPLIED_BY.items():
        if scopes & set(wide):
            scopes.add(narrow)
    return scopes - set(P.NEVER_SHARED)


def sync_state(scored_today: bool, got: datetime | None, last_scored, now: datetime) -> str:
    """scored: today's scores are in. pending: none yet, but data arrived within SYNC_STALE_AFTER (they are being computed).
    stale: no data for longer than that (or no record of any, only older scores). never: nothing at all."""
    if scored_today:
        return "scored"
    if got is not None:
        return "pending" if now - got <= P.SYNC_STALE_AFTER else "stale"
    return "stale" if last_scored is not None else "never"


def sync_info(conn, uid: int, today, last_scored, now: datetime | None = None) -> dict:
    """What a card says about syncing: {state, last_sync_at, last_sync_date, last_sync_min}. The time is the newest data that
    reached the server (sync/received.py), shown coarsely: to the minute, and under JUST_NOW_MIN minutes it is "now"."""
    now = now or _now()
    got = received.last_received_at(conn, uid, now)
    out = {"state": sync_state(last_scored == today, got, last_scored, now),
           "last_sync_at": None, "last_sync_date": None, "last_sync_min": None}
    if got is not None:
        mins = int((now - got).total_seconds() // 60)
        if mins < P.JUST_NOW_MIN:
            got, mins = now, 0
        out.update(last_sync_at=got.replace(second=0, microsecond=0), last_sync_min=mins,
                   last_sync_date=got.astimezone(user_tz(conn, uid)).date())
    return out


def _scores_block(conn, uid: int, today) -> dict:
    rows = {r["date"]: r for r in conn.execute(
        """SELECT date, recovery, sleep_score, sleep_actual_min, strain FROM daily_scores
           WHERE user_id = %s AND date BETWEEN %s AND %s""", (uid, today - timedelta(days=6), today))}
    t = rows.get(today) or {}
    zone = views.recovery_zone(t.get("recovery"))
    last = conn.execute("""SELECT max(date) AS d FROM daily_scores WHERE user_id = %s AND date <= %s
                           AND (recovery IS NOT NULL OR strain IS NOT NULL)""", (uid, today)).fetchone()["d"]
    week = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        r = rows.get(d) or {}
        week.append({"date": d, "weekday": d.weekday(), "today": d == today, "recovery": r.get("recovery"),
                     "zone": views.recovery_zone(r.get("recovery")), "sleep_score": r.get("sleep_score"),
                     "sleep_min": r.get("sleep_actual_min"), "strain": r.get("strain")})
    return {"recovery": t.get("recovery"), "zone": zone, "label": views._recovery_word(zone),
            "sleep_score": t.get("sleep_score"), "sleep_min": t.get("sleep_actual_min"), "strain": t.get("strain"),
            "last_date": last, "synced_today": last == today, **sync_info(conn, uid, today, last), "week": week}


def _trained_today(conn, uid: int, today) -> bool:
    return conn.execute("""SELECT 1 FROM workouts WHERE user_id = %s AND local_date = %s AND dup_of IS NULL
                           AND excluded IS NULL LIMIT 1""",
                        (uid, today)).fetchone() is not None


def seen_by_friends(conn: psycopg.Connection, uid: int, one: dict | None = None) -> dict:
    """What my friends see of me, for my own card ("what friends see", flag friend_card_v2): a block is shown when at
    least one circle member may see it (the union of the scopes I grant), `varies` when not everyone sees the same.
    With no circle members yet nobody sees anything, so `count` is 0 and the page shows the full card.
    `one` (one_circle): only that circle's members and what I picked there."""
    limit = one["picks"].get(uid, set()) if one else None
    sets = [frozenset(share_with(conn, m["id"], uid) & limit if one else share_with(conn, m["id"], uid))
            for m in co_members(conn, uid, one and one["id"])]
    union = set().union(*sets) if sets else set()
    return {"count": len(sets), "scopes": sorted(union), "varies": len(set(sets)) > 1,
            "hidden": [s for s in ("scores", "training") if s not in union] if sets else []}


def card(conn: psycopg.Connection, viewer_id: int, owner_id: int, name: str, limit: set[str] | None = None) -> dict:
    """`limit`: in one circle's view, only what the owner picked in that circle (on top of what they grant the viewer)."""
    today = views.local_today(conn, owner_id)
    scopes = None if viewer_id == owner_id else share_with(conn, viewer_id, owner_id)
    if scopes is not None and limit is not None:
        scopes &= limit
    can = (lambda s: True) if scopes is None else (lambda s: s in scopes)
    out = {"id": owner_id, "display_name": name, "self": viewer_id == owner_id, "date": today,
           "shared": sorted(scopes) if scopes is not None else None, "hidden": []}
    out["scores"] = _scores_block(conn, owner_id, today) if can("scores") else None
    out["trained"] = _trained_today(conn, owner_id, today) if can("training") else None
    for key, scope in (("scores", "scores"), ("trained", "training")):
        if out[key] is None:
            out["hidden"].append(scope)
    return out


def _nudges(conn, uid: int) -> tuple[dict, dict]:
    """(what I sent today per person, what I received today) with each side's own local date."""
    my_day = views.local_today(conn, uid)
    sent: dict[int, list[str]] = {}
    for r in conn.execute("SELECT to_user_id, kind FROM circle_nudges WHERE from_user_id = %s AND date = %s ORDER BY id",
                          (uid, my_day)):
        sent.setdefault(r["to_user_id"], []).append(r["kind"])
    got = {k: [] for k in NUDGE_KINDS}
    for r in conn.execute(
            f"""SELECT n.kind, n.from_user_id, u.display_name FROM circle_nudges n JOIN users u ON u.id = n.from_user_id
               WHERE n.to_user_id = %(u)s AND n.created_at > now() - interval '24 hours'
                 AND {blocks.not_blocked('%(u)s', 'n.from_user_id')} ORDER BY n.id""", {"u": uid}):
        got[r["kind"]].append(r)
    nick = remarks.for_viewer(conn, uid, [r["from_user_id"] for rs in got.values() for r in rs])   # my own remarks (#212)
    got = {k: [nick.get(r["from_user_id"], r["display_name"]) for r in rs] for k, rs in got.items()}
    return sent, got


def overview(conn: psycopg.Connection, uid: int, circle_id=None) -> dict:
    """Friends tab: my own card first (with cheers received in the last 24 hours), then every co-member.
    With the circles_by_circle flag: `circles` (the switcher) and, for `circle_id`, only that circle's members, each card
    showing only what they picked there (`circle` = its id; null = all circles, merged)."""
    one = one_circle(conn, uid, circle_id)
    me = conn.execute("SELECT display_name FROM users WHERE id = %s", (uid,)).fetchone()
    sent, got = _nudges(conn, uid)
    # 「全部」 leaves out the circles I archived (#269 P2); they still share, so 「what friends see」 below still counts them
    co = co_members(conn, uid, one and one["id"], () if one else prefs.hidden(conn, uid))
    avatars = avatar_store.urls_for(conn, uid, [uid, *(m["id"] for m in co)])
    nick = remarks.for_viewer(conn, uid, [m["id"] for m in co])   # what I call them (#212), only for me
    mine = {**card(conn, uid, uid, me["display_name"]), "received": got, "seen": seen_by_friends(conn, uid, one),
            "avatar": avatars.get(uid)}
    members = []
    for m in co:
        c = card(conn, uid, m["id"], m["display_name"], one["picks"].get(m["id"], set()) if one else None)
        members.append({**c, "circles": m["circles"], "sent": sent.get(m["id"], []), "avatar": avatars.get(m["id"]),
                        "remark": nick.get(m["id"])})
    out = {"me": mine, "members": members, "nudge_kinds": list(NUDGE_KINDS), "remark_max": P.REMARK_MAX,
           "has_circles": conn.execute("SELECT 1 FROM circle_members WHERE user_id = %s", (uid,)).fetchone() is not None}
    if by_circle_on(conn, uid):
        out["circles"] = circle_list(conn, uid)
        out["circle"] = one and one["id"]
    return out


def nudge(conn: psycopg.Connection, uid: int, to_id: int, kind: str) -> dict:
    """One cheer: only to someone sharing a circle with me, one of each kind per person per day (my local date)."""
    if kind not in NUDGE_KINDS:
        raise store.FriendsError(400, f"不认识的鼓励：{kind}")
    from . import moderation
    moderation.require_allowed(conn, uid)
    if to_id == uid or blocks.pair_blocked(conn, uid, to_id) or not conn.execute(
            """SELECT 1 FROM circle_members m JOIN circle_members o ON o.circle_id = m.circle_id
               WHERE m.user_id = %s AND o.user_id = %s""", (uid, to_id)).fetchone():
        raise store.FriendsError(404, "这个人不在你的圈子里")
    day = views.local_today(conn, uid)
    row = conn.execute("""INSERT INTO circle_nudges (from_user_id, to_user_id, kind, date) VALUES (%s, %s, %s, %s)
                          ON CONFLICT DO NOTHING RETURNING id""", (uid, to_id, kind, day)).fetchone()
    if row is None:
        raise store.FriendsError(409, "今天已经点过了")
    return {"sent": [r["kind"] for r in conn.execute(
        "SELECT kind FROM circle_nudges WHERE from_user_id = %s AND to_user_id = %s AND date = %s ORDER BY id",
        (uid, to_id, day))]}
