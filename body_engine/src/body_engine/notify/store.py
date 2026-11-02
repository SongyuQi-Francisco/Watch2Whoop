"""In-app notification list and push device registration (per user; callers check authorization)."""
from __future__ import annotations

import re

import psycopg

from . import params as P

TOKEN_RE = re.compile(r"^[0-9a-fA-F]{32,200}$")


def _row(r: dict, en: bool) -> dict:
    return {"id": r["id"], "kind": r["kind"], "url": r["url"],
            "title": (r["title_en"] if en else None) or r["title"], "body": (r["body_en"] if en else None) or r["body"],
            "created_at": r["created_at"].isoformat(), "read": r["read_at"] is not None, "data": r["data"] or {}}


def unread(conn: psycopg.Connection, user_id: int) -> int:
    return conn.execute("SELECT count(*) AS n FROM notifications WHERE user_id = %s AND read_at IS NULL",
                        (user_id,)).fetchone()["n"]


def list_for(conn: psycopg.Connection, user_id: int, en: bool, before: int | None = None, limit: int = P.LIST_LIMIT) -> dict:
    rows = conn.execute(
        """SELECT id, kind, title, body, title_en, body_en, url, data, created_at, read_at FROM notifications
           WHERE user_id = %s AND (%s::bigint IS NULL OR id < %s) ORDER BY id DESC LIMIT %s""",
        (user_id, before, before, limit + 1)).fetchall()
    return {"items": [_row(r, en) for r in rows[:limit]], "more": len(rows) > limit, "unread": unread(conn, user_id)}


def mark_read(conn: psycopg.Connection, user_id: int, ids: list[int] | None = None, kind: str | None = None) -> int:
    """ids = those; kind = every unread one of that kind or category (circle: circle.like, circle_feed_like …); neither = all."""
    sql = "UPDATE notifications SET read_at = now() WHERE user_id = %s AND read_at IS NULL"
    args: list = [user_id]
    if ids is not None:
        sql += " AND id = ANY(%s)"
        args.append(ids)
    if kind:
        sql += " AND (kind = %s OR kind LIKE %s OR kind LIKE %s)"
        esc = kind.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        args += [kind, esc + ".%", esc + "\\_%"]
    return conn.execute(sql, args).rowcount


def prune(conn: psycopg.Connection, days: int = P.KEEP_DAYS) -> int:
    return conn.execute("DELETE FROM notifications WHERE created_at < now() - make_interval(days => %s)", (days,)).rowcount


def register_device(conn: psycopg.Connection, user_id: int, token: str, environment: str, api_token_id: int | None,
                    bundle_id: str | None = None, app_version: str | None = None, lang: str | None = None,
                    silent: bool = False) -> dict:
    """A token is one app install: re-registering moves it to whoever registers it now (signed in on that phone).
    silent: the install also takes background pushes when the user's data changed (the native app, notify/silent.py)."""
    if not TOKEN_RE.match(token or ""):
        raise ValueError("token")
    if environment not in ("sandbox", "production"):
        raise ValueError("environment")
    row = conn.execute(
        """INSERT INTO push_devices (user_id, token, environment, bundle_id, app_version, lang, api_token_id, silent)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (token) DO UPDATE SET user_id = EXCLUDED.user_id, environment = EXCLUDED.environment,
             bundle_id = EXCLUDED.bundle_id, app_version = EXCLUDED.app_version, lang = EXCLUDED.lang,
             api_token_id = EXCLUDED.api_token_id, silent = EXCLUDED.silent, last_seen_at = now()
           RETURNING id, environment, created_at""",
        (user_id, token.lower(), environment, bundle_id, app_version, lang, api_token_id, silent)).fetchone()
    return {"id": row["id"], "environment": row["environment"]}


def remove_devices(conn: psycopg.Connection, user_id: int, token: str | None = None, api_token_id: int | None = None) -> int:
    """One token of this user, or every device registered with this API token (sign-out)."""
    if token:
        return conn.execute("DELETE FROM push_devices WHERE user_id = %s AND token = %s", (user_id, token.lower())).rowcount
    if api_token_id is not None:
        return conn.execute("DELETE FROM push_devices WHERE user_id = %s AND api_token_id = %s",
                            (user_id, api_token_id)).rowcount
    return 0


def devices(conn: psycopg.Connection, user_id: int) -> list[dict]:
    rows = conn.execute("""SELECT id, token, environment, app_version, created_at, last_push_at, last_status FROM push_devices
                           WHERE user_id = %s ORDER BY id""", (user_id,)).fetchall()
    return [{"id": r["id"], "token_tail": r["token"][-6:], "environment": r["environment"], "app_version": r["app_version"],
             "created_at": r["created_at"].isoformat(), "last_push_at": r["last_push_at"] and r["last_push_at"].isoformat(),
             "last_status": r["last_status"]} for r in rows]
