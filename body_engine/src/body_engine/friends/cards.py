"""Member cards on the Friends tab (docs/friends-social-plan.md §3.3) and one-tap cheers (nudges).

A card shows one person: today (status label from the recovery zone, recovery / sleep / strain, trained today, last
synced day when today has no data) and the last 7 days (recovery zone per day, strain per day). Every block is checked
against the viewer's grant for that person (auth.can_view): scores → numbers, dots and bars; training → the trained badge.
A block the person did not share is null and listed in `hidden`. Dates are each person's own local dates.
"""
from __future__ import annotations

from datetime import timedelta

import psycopg

from .. import auth
from ..web import views
from . import params as P, store

NUDGE_KINDS = ("clap", "sleep", "train")   # 👏 / 💤 / 💪


def co_members(conn: psycopg.Connection, uid: int) -> list[dict]:
    """Everyone who shares at least one circle with `uid`, with the names of those circles."""
    return conn.execute(
        """SELECT u.id, u.display_name, array_agg(DISTINCT c.name ORDER BY c.name) AS circles, min(o.joined_at) AS since
           FROM circle_members m JOIN circle_members o ON o.circle_id = m.circle_id AND o.user_id <> m.user_id
           JOIN circles c ON c.id = m.circle_id JOIN users u ON u.id = o.user_id
           WHERE m.user_id = %s GROUP BY u.id, u.display_name ORDER BY min(o.joined_at), u.id""", (uid,)).fetchall()


def share_with(conn: psycopg.Connection, viewer_id: int, owner_id: int) -> set[str]:
    """Scopes `owner_id` grants `viewer_id` right now (circles + hand-made, nutrition implies nutrition_summary)."""
    row = conn.execute(f"""SELECT {auth.EFFECTIVE_SCOPES.format(s="s")} AS scopes FROM shares s
                           WHERE s.owner_id = %s AND s.viewer_id = %s""", (owner_id, viewer_id)).fetchone()
    scopes = set(row["scopes"]) if row else set()
    for narrow, wide in auth.IMPLIED_BY.items():
        if scopes & set(wide):
            scopes.add(narrow)
    return scopes - set(P.NEVER_SHARED)


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
        week.append({"date": d, "recovery": r.get("recovery"), "zone": views.recovery_zone(r.get("recovery")),
                     "strain": r.get("strain")})
    return {"recovery": t.get("recovery"), "zone": zone, "label": views._recovery_word(zone),
            "sleep_score": t.get("sleep_score"), "sleep_min": t.get("sleep_actual_min"), "strain": t.get("strain"),
            "last_date": last, "synced_today": last == today, "week": week}


def _trained_today(conn, uid: int, today) -> bool:
    return conn.execute("SELECT 1 FROM workouts WHERE user_id = %s AND local_date = %s AND dup_of IS NULL LIMIT 1",
                        (uid, today)).fetchone() is not None


def card(conn: psycopg.Connection, viewer_id: int, owner_id: int, name: str) -> dict:
    today = views.local_today(conn, owner_id)
    scopes = None if viewer_id == owner_id else share_with(conn, viewer_id, owner_id)
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
            """SELECT n.kind, u.display_name FROM circle_nudges n JOIN users u ON u.id = n.from_user_id
               WHERE n.to_user_id = %s AND n.created_at > now() - interval '24 hours' ORDER BY n.id""", (uid,)):
        got[r["kind"]].append(r["display_name"])
    return sent, got


def overview(conn: psycopg.Connection, uid: int) -> dict:
    """Friends tab: my own card first (with cheers received in the last 24 hours), then every co-member."""
    me = conn.execute("SELECT display_name FROM users WHERE id = %s", (uid,)).fetchone()
    sent, got = _nudges(conn, uid)
    mine = {**card(conn, uid, uid, me["display_name"]), "received": got}
    members = []
    for m in co_members(conn, uid):
        c = card(conn, uid, m["id"], m["display_name"])
        members.append({**c, "circles": m["circles"], "sent": sent.get(m["id"], [])})
    return {"me": mine, "members": members, "nudge_kinds": list(NUDGE_KINDS),
            "has_circles": conn.execute("SELECT 1 FROM circle_members WHERE user_id = %s", (uid,)).fetchone() is not None}


def nudge(conn: psycopg.Connection, uid: int, to_id: int, kind: str) -> dict:
    """One cheer: only to someone sharing a circle with me, one of each kind per person per day (my local date)."""
    if kind not in NUDGE_KINDS:
        raise store.FriendsError(400, f"不认识的鼓励：{kind}")
    if to_id == uid or not conn.execute(
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
