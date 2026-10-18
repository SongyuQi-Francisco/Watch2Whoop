"""Assistant tool specs for the bedtime goal (wrapped into Tool and registered by assistant/tools.py)."""
from datetime import date

from ..toolspec import USER, nullable, schema
from . import store


_schema = schema


TOOL_SPECS: list[dict] = [
    dict(
        name="get_sleep_goal",
        description="入睡时间目标：最终目标、逐步提前的计划、今晚目标入睡 / 起床，最近 7 晚进度（平均入睡 / 起床、达标晚数、"
                    "每周趋势分钟〔负 = 在往前提〕、还差几分钟、预计哪天达到）和每晚明细。时间是本地 HH:MM；"
                    "「一晚」按入睡前的傍晚算。没设目标时 target_bedtime 为空。",
        input_schema=_schema({"user": USER, "night": nullable("string", "入睡前的傍晚 YYYY-MM-DD，null = 今晚")}),
        handler=lambda conn, uid, a: store.view(conn, uid, date.fromisoformat(a["night"]) if a["night"] else None),
        scope="sleep",
    ),
    dict(
        name="set_sleep_goal",
        description="设 / 改入睡时间目标：从最近 7 晚平均入睡时间开始逐步提前（默认每 3 天提前 20 分钟，生物钟一天只能挪 15–30 分钟）。"
                    "null = 沿用旧值或默认。",
        input_schema=_schema({
            "target_bedtime": {"type": "string", "description": "HH:MM 24 小时制"},
            "target_wake": nullable("string", "HH:MM；null = 今晚目标 + 睡眠时长"),
            "pace_min_per_step": nullable("integer", "每档提前几分钟 5–60"),
            "step_days": nullable("integer", "每档几天 1–14"),
        }),
        handler=lambda conn, uid, a: store.set_goal(conn, uid, a["target_bedtime"], a["target_wake"],
                                                    a["pace_min_per_step"], a["step_days"]),
        scope="sleep",
        writes=True,
    ),
]
