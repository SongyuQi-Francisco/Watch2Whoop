"""AI tool for the Friends page: a read-only overview of circle members (docs/friends-social-plan.md §3.6).
Only the caller's circles and only what each member shares with the caller; not in the fallback AI's tool set."""
from .. import auth
from ..rollout import flags
from ..toolspec import schema
from . import leaderboard


def _overview(conn, uid: int, a: dict) -> dict:
    flags.guard(conn, a["_viewer_id"], auth.CIRCLES_FLAG)   # behind the `friends` flag like the Friends page
    return leaderboard.ai_overview(conn, a["_viewer_id"])

TOOL_SPECS: list[dict] = [
    dict(name="get_friends_overview",
         description="圈子成员（家人 / 朋友）今天状态、近 7 天恢复和 5 个排行榜名次；只含对方共享给我的部分。",
         input_schema=schema({}),
         handler=_overview, group="body", scope="scores"),
]
