"""AI tool for the AI-first page (#279, registered by assistant/tools.py): any AI can tidy the user's Today ("把睡眠放到首页",
"首页太乱了") and set what they care about. One short write tool in its own small group home (sent on the Me page, find_tools elsewhere),
inside the tool-definition budget (tests/test_briefing.py). FlagOff while ui_ai_first is off for the user."""
from datetime import datetime

from ..rollout import flags
from ..toolspec import nullable, schema
from ..water.store import tz_of
from . import home as H
from . import params as P


def _set(conn, uid, a):
    flags.guard(conn, uid, P.FEATURE)
    if a["focus"] is not None:
        H.set_focus(conn, uid, a["focus"])
    if a["module"]:
        H.set_module(conn, uid, a["module"], a["state"] or "pinned")
    h = H.home(conn, uid, datetime.now(tz_of(conn, uid)).date())
    return {"focus": h["focus"], "today": [m["key"] for m in h["modules"][:P.MAX_MODULES]], "hidden": h["hidden"]}


TOOL_SPECS: list[dict] = [
    dict(
        name="set_home",
        description="今天页「我的首页」：module 放上(pinned) / 移走(hidden) / 交回规则(auto)；focus 设关注点。null = 不改。",
        input_schema=schema({
            "module": nullable("string", None, enum=[*P.MODULES, None]),
            "state": nullable("string", None, enum=["pinned", "hidden", "auto", None]),
            "focus": {"type": ["array", "null"], "items": {"type": "string", "enum": list(P.FOCUS)}},
        }),
        handler=_set, group="home", scope="memory", writes=True,
    ),
]
