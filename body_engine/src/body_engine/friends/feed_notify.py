"""Tells a post's author about a ❤️ or a comment through the general push module notify/ (notify(conn, user_id, kind, title,
body, url=None, data=None)). That module is built apart and may not be there yet: then this does nothing and the page's red dot
(feed.unread) is the only signal. Never raises: a failed push must not undo the like or comment."""
from __future__ import annotations

import importlib
import importlib.util
import logging

import psycopg

from ..i18n import use_lang

log = logging.getLogger(__name__)
MODULE = "body_engine.notify"

LIKE, COMMENT = "circle_feed_like", "circle_feed_comment"
_TEXT = {   # (zh, en) title and body per kind; {name} is the actor, {text} the comment (cut)
    LIKE: (("{name} 给你的训练点了 ❤️", "{name} liked your workout ❤️"), ("今天的训练动态", "Today's training post")),
    COMMENT: (("{name} 评论了你的训练", "{name} commented on your workout"), ("{text}", "{text}")),
}


def available() -> bool:
    try:
        return importlib.util.find_spec(MODULE) is not None
    except (ImportError, ValueError):
        return False


def push(conn: psycopg.Connection, to_user: int, kind: str, name: str, text: str = "", post_id: int | None = None) -> bool:
    """Returns True when a notification was handed to notify/."""
    if not available():
        return False
    try:
        fn = getattr(importlib.import_module(MODULE), "notify", None)
        if fn is None:
            return False
        lang = (conn.execute("SELECT language FROM users WHERE id = %s", (to_user,)).fetchone() or {}).get("language") or "zh"
        (tz, te), (bz, be) = _TEXT[kind]
        with use_lang(lang):
            en = lang.startswith("en")
            title = (te if en else tz).format(name=name)
            body = (be if en else bz).format(text=text[:80])
        fn(conn, to_user, kind, title, body, url=f"/app/?tab=friends&feed={post_id}" if post_id else "/app/?tab=friends",
           data={"post_id": post_id})
        return True
    except Exception:   # the push module is optional and best effort
        log.exception("circle feed notification failed")
        return False
