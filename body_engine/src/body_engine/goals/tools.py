"""Assistant tool specs for the bedtime goal (wrapped into Tool and registered by assistant/tools.py)."""
from datetime import date

from ..toolspec import OPT_DAY, USER, nullable, schema
from . import nutrition, store


_schema = schema
_g = nullable("number")
GOAL_SET = schema({"calories": _g, "protein_g": _g, "carbs_g": _g, "fat_g": _g, "sat_fat_max_g": _g,
                   "added_sugar_max_g": _g, "fiber_min_g": nullable("number", "纤维下限")})


TOOL_SPECS: list[dict] = [
    dict(
        name="get_sleep_goal",
        description="入睡时间目标：最终目标、逐步提前的计划、今晚目标入睡 / 起床，最近 7 晚进度（平均入睡 / 起床、达标晚数、"
                    "每周趋势分钟〔负 = 在往前提〕、还差几分钟、预计哪天达到）和每晚明细。时间是本地 HH:MM；"
                    "「一晚」按入睡前的傍晚算。没设目标时 target_bedtime 为空。",
        input_schema=_schema({"user": USER, "night": nullable("string", "入睡前的傍晚 YYYY-MM-DD，null = 今晚")}),
        handler=lambda conn, uid, a: store.view(conn, uid, date.fromisoformat(a["night"]) if a["night"] else None),
        group="body", scope="sleep",
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
        group="body", scope="sleep",
        writes=True,
    ),
    dict(
        name="get_nutrition_goals",
        description="饮食目标：某天生效的目标（训练日 / 休息日）、当前版本两套目标、以后生效的版本和全部历史（谁改的、为什么）。"
                    "添加糖没设上限时默认 25g。",
        input_schema=_schema({"user": USER, "date": OPT_DAY}),
        handler=lambda conn, uid, a: nutrition.view(conn, uid, date.fromisoformat(a["date"]) if a["date"] else None),
        group="nutrition", scope="nutrition",
    ),
    dict(
        name="set_nutrition_goals",
        description="改饮食目标：新加一个版本（旧版本保留）。只填要改的，null = 沿用当前版本；第一版要给全热量和三大营养素。",
        input_schema=_schema({
            "training": GOAL_SET, "rest": GOAL_SET,
            "training_weekdays": {"type": ["array", "null"], "items": {"type": "integer"},
                                  "description": "用训练日目标的星期，1=周一…7=周日"},
            "effective_from": nullable("string", "YYYY-MM-DD，null = 今天"),
            "name": nullable("string", "版本名，例 减脂 B"),
            "reason": {"type": "string", "description": "为什么改"},
        }),
        handler=lambda conn, uid, a: nutrition.set_goals(
            conn, uid, training=_set_or_none(a["training"]), rest=_set_or_none(a["rest"]),
            training_weekdays=a["training_weekdays"],
            effective_from=date.fromisoformat(a["effective_from"]) if a["effective_from"] else None,
            name=a["name"], reason=a["reason"], set_by=a.get("_channel") or "assistant",
            actor_user_id=a.get("_viewer_id")),
        group="nutrition", scope="nutrition",
        writes=True,
    ),
]


def _set_or_none(s: dict | None) -> dict | None:
    return s if s and any(v is not None for v in s.values()) else None
