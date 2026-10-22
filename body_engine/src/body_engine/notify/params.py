"""Notification categories, merging and quiet-hours defaults (notify/, issue #175).

A kind is "<category>.<event>" or "<category>_<event>" (or just "<category>"). The category is what a user switches
on / off in the settings; declare a new one here in the same PR as the feature that sends it (notify() raises on an
unknown category, so a typo fails in tests). MERGED gives the text of a merged push per kind; kinds without one use DEFAULT_MERGED.
"""
from datetime import time

FEATURE = "notifications"   # rollout flag: the Today page list / settings, and whether pushes go out at all

# category -> label (zh, en), default on
CATEGORIES: dict[str, dict] = {
    "circle": {"zh": "圈子互动", "en": "Circle activity", "default": True},   # likes / comments on the training feed
    "system": {"zh": "系统消息", "en": "System", "default": True},
    "ai_card": {"zh": "Jarvis 额度卡", "en": "Jarvis allowance cards", "default": True},   # ai/quota_cards.py (#182)
}

# Same kind to the same person within MERGE_MINUTES while unread: one notification on the phone, updated in place
# (apns-collapse-id), the text becomes a count; only the first one makes a sound. {n} = people (distinct
# data["actor_id"]) or, without actors, notifications.
MERGE_MINUTES = 30
MAX_PUSHES_PER_WINDOW = 10      # after this many updates in one window the rest stay in-app only
MERGED: dict[str, tuple[str, str]] = {
    "circle.like": ("{n} 个人给你点了 ❤️", "{n} people ❤️ your workout"),          # friends/feed.py (#176)
    "circle.comment": ("{n} 个人评论了你的训练", "{n} people commented on your workout"),
}
DEFAULT_MERGED = ("{n} 条新的{label}", "{n} new · {label}")
MERGED_BODY = ("点开查看", "Tap to see them")

# Quiet hours in the user's time zone (no push, the in-app list still gets it). start > end wraps midnight.
QUIET_ENABLED = True
QUIET_START = time(23, 0)
QUIET_END = time(7, 0)

LIST_LIMIT = 50                 # notifications per page of the list
KEEP_DAYS = 90                  # older notifications are deleted by prune()
MAX_TITLE = 120
MAX_BODY = 400
MAX_URL = 300
MAX_DATA_BYTES = 2000
