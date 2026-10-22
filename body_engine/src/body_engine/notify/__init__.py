"""Notifications (issue #175): in-app list + APNs push + per-user settings. Features only call notify():

    from body_engine.notify import notify
    notify(conn, user_id, "circle.like", {"zh": "小明给你点了 ❤️", "en": "Ming ❤️ your workout"}, "", url="/app/#tab=friends",
           data={"actor_id": 7})

See core.py for what it does and params.py for categories (declare new ones there).
"""
from .core import notify

__all__ = ["notify"]
