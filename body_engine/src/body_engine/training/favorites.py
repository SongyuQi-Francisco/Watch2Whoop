"""Starred exercises of the exercise library (UI 2.0.1), per user and private: only the person reads or writes them
(a friend with the training share sees the library without stars). Table exercise_favorites (migration 1180)."""
from __future__ import annotations

import psycopg

from ..i18n import L
from . import catalog, params as P


def slugs(conn: psycopg.Connection, user_id: int) -> set[str]:
    return {r["slug"] for r in conn.execute("SELECT slug FROM exercise_favorites WHERE user_id = %s", (user_id,))}


def _canonical(conn: psycopg.Connection, user_id: int, slug: str) -> str:
    """The slug as the library lists it (a name or alias finds it too); LookupError when the library has no such exercise."""
    try:
        return catalog.get_exercise(conn, user_id, slug)["slug"]
    except ValueError as e:
        raise LookupError(str(e)) from None


def _out(conn: psycopg.Connection, user_id: int, slug: str, on: bool) -> dict:
    n = conn.execute("SELECT count(*) AS n FROM exercise_favorites WHERE user_id = %s", (user_id,)).fetchone()["n"]
    return {"slug": slug, "favorite": on, "favorites_count": n}


def add(conn: psycopg.Connection, user_id: int, slug: str) -> dict:
    """Star an exercise (idempotent)."""
    slug = _canonical(conn, user_id, slug)
    have = slugs(conn, user_id)
    if slug not in have and len(have) >= P.FAVORITES_MAX:
        raise ValueError(L(f"最多收藏 {P.FAVORITES_MAX} 个动作", f"At most {P.FAVORITES_MAX} starred exercises"))
    conn.execute("INSERT INTO exercise_favorites (user_id, slug) VALUES (%s, %s) ON CONFLICT DO NOTHING", (user_id, slug))
    return _out(conn, user_id, slug, True)


def remove(conn: psycopg.Connection, user_id: int, slug: str) -> dict:
    """Unstar (idempotent; a slug the library no longer has can still be removed)."""
    try:
        canon = _canonical(conn, user_id, slug)
    except LookupError:
        canon = slug
    conn.execute("DELETE FROM exercise_favorites WHERE user_id = %s AND slug = ANY(%s)", (user_id, sorted({slug, canon})))
    return _out(conn, user_id, canon, False)
