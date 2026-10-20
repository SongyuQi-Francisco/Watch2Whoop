"""AI assistant tool specs for training history and Training V2 (wrapped into Tool and registered by assistant/tools.py):
read the calendar / a day, correct or hide a watch workout (overrides.py), exercise catalog, weekly splits, plan management, exercise swap."""
from datetime import date, datetime, timedelta

from ..toolspec import DATE, DETAIL, USER, nullable, schema
from . import catalog, history, overrides, params as P, plans


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


_schema = schema
_n = nullable

PLAN_EX = _schema({
    "exercise": {"type": "string", "description": "slug 或动作名"},
    "sets": {"type": "integer"},
    "rep_low": _n("integer"),
    "rep_high": _n("integer"),
    "rest_sec": _n("integer"),
    "duration_sec": _n("integer"),
    "weight_kg": _n("number"),
    "note": _n("string"),
})

PLAN_DAY = _schema({
    "day_of_week": {"type": "integer", "description": "1=周一…7=周日"},
    "name": {"type": "string"},
    "exercises": {"type": "array", "items": PLAN_EX},
})


def slim_calendar(cal: dict) -> dict:
    """One short row per day (sessions as text), the summary, no per-session ids / labels / legend."""
    days = [{"date": f"{d['date']:%m-%d}", "wd": d["weekday"], "intensity": d["intensity"],
             "sessions": "、".join(f"{s['name']} {s['duration_min'] or 0:.0f}min" for s in d["sessions"]) or None,
             "strain": d["strain"], "recovery": d["recovery"], "active_kcal": d["active_kcal"], "steps": d["steps"]}
            for d in cal["days"] if not d["future"]]
    s = cal["summary"]
    summary = {**{k: s[k] for k in ("training_days", "rest_days", "sessions", "total_duration_min", "avg_active_kcal",
                                     "avg_strain", "longest_streak", "by_type")},
               "by_intensity": {k: v for k, v in s["by_intensity"].items() if v}}
    return {"start": cal["start"], "end": cal["end"], "summary": summary, "days": days,
            "sparky_error": cal["sparky_error"], "hidden": cal["hidden"]}


def _calendar(conn, uid, a):
    viewer = a.get("_viewer_id", uid)
    if a["month"]:
        cal = history.calendar(conn, viewer, uid, a["month"])
    else:  # no month: the last 28 days up to end (or today), as the description says
        end = _d(a["end"]) or datetime.now(history._tz(conn, uid)).date()
        cal = history.calendar(conn, viewer, uid, start=_d(a["start"]) or end - timedelta(days=27), end=end)
    return cal if a.get("detail") else slim_calendar(cal)


def _day(conn, uid, a):
    return history.day(conn, a.get("_viewer_id", uid), uid, date.fromisoformat(a["date"]))


def _get_exercise(conn, uid, a):
    return catalog.get_exercise(conn, uid, a["exercise"])


def _propose_plan(conn, uid, a):
    return plans.propose_plan(
        conn,
        user_id=uid,
        split_type=a["split_type"],
        custom_days=a.get("custom_days"),
        name=a.get("name"),
        notes=a.get("notes"),
        auto_swap_banned=bool(a.get("auto_swap_banned")),
    )


def _save_plan(conn, uid, a):
    return plans.save_plan(
        conn,
        user_id=uid,
        split_type=a["split_type"],
        days=a.get("custom_days"),
        name=a.get("name"),
        start_date=a.get("start_date"),
        notes=a.get("notes"),
        auto_swap_banned=bool(a.get("auto_swap_banned")),
        sync_sparky=bool(a.get("sync_sparky", True)),
    )


def _get_week_plan(conn, uid, a):
    viewer = a.get("_viewer_id", uid)
    return plans.get_week_plan(
        conn,
        viewer_id=viewer,
        owner_id=uid,
        day=_d(a.get("date")),
        detail=bool(a.get("detail")),
    )


def _swap_exercise(conn, uid, a):
    return plans.swap_exercise(
        conn,
        user_id=uid,
        from_exercise=a["from_exercise"],
        to_exercise=a.get("to_exercise"),
        day_of_week=a.get("day_of_week"),
        session_name=a.get("session_name"),
        sync_sparky=bool(a.get("sync_sparky", True)),
    )


SPLIT_ENUM = [*P.SPLIT_TEMPLATES.keys(), "custom"]

TOOL_SPECS: list[dict] = [
    dict(
        name="get_training_calendar",
        description="训练日历（手表 + SparkyFitness 合并）：每天强度、时长、strain、恢复分、活动能量、步数与汇总（默认最近 28 天）。",
        input_schema=_schema({
            "user": USER,
            "month": nullable("string", "YYYY-MM"),
            "start": nullable("string", "YYYY-MM-DD"),
            "end": nullable("string", "YYYY-MM-DD"),
            "detail": DETAIL,
        }),
        handler=_calendar,
        scope="training",
    ),
    dict(
        name="get_training_day",
        description="某天训练详情：来源、起止、时长、能量、心率五区、TRIMP、强度，力量动作与每组次数/重量/RIR，全天指标。",
        input_schema=_schema({"user": USER, "date": DATE}),
        handler=_day,
        scope="training",
    ),
    dict(
        name="get_exercise",
        description="单个动作详情：中英文名、主次肌群、器械、动作模式、要点、关节压力、用户限制（active/caution/banned）及同模式安全替代。",
        input_schema=_schema({"user": USER, "exercise": {"type": "string"}}),
        handler=_get_exercise,
        scope="training",
    ),
    dict(
        name="propose_plan",
        description="生成 3/4/5 分化或自定义周计划草案（不写库）：每天动作处方、各肌群周组数、恢复分自调节、禁用冲突与替代。排计划先调它预览。",
        input_schema=_schema({
            "user": USER,
            "split_type": {"type": "string", "enum": SPLIT_ENUM},
            "name": _n("string"),
            "notes": _n("string"),
            "auto_swap_banned": {"type": "boolean"},
            "custom_days": {"type": ["array", "null"], "items": PLAN_DAY},
        }),
        handler=_propose_plan,
        scope="training",
    ),
    dict(
        name="save_plan",
        description="保存并激活周训练计划（写 training_plans 并同步 SparkyFitness）；含禁用动作且未开 auto_swap_banned 时拒绝。",
        input_schema=_schema({
            "split_type": {"type": "string", "enum": SPLIT_ENUM},
            "name": _n("string"),
            "start_date": _n("string", "YYYY-MM-DD"),
            "notes": _n("string"),
            "auto_swap_banned": {"type": "boolean"},
            "sync_sparky": {"type": "boolean"},
            "custom_days": {"type": ["array", "null"], "items": PLAN_DAY},
        }),
        handler=_save_plan,
        scope="training",
        writes=True,
    ),
    dict(
        name="get_week_plan",
        description="当前生效的周训练计划（周一至周日）：每天动作与组数处方、肌群周组数、近期加减重建议、今日恢复分自调节建议。",
        input_schema=_schema({"user": USER, "date": _n("string", "YYYY-MM-DD"), "detail": DETAIL}),
        handler=_get_week_plan,
        scope="training",
    ),
    dict(
        name="swap_exercise",
        description="替换周计划中的动作：to_exercise=null 仅返回安全替代列表；指定非禁用动作则替换并同步 SparkyFitness。",
        input_schema=_schema({
            "from_exercise": {"type": "string"},
            "to_exercise": _n("string"),
            "day_of_week": _n("integer", "1–7"),
            "session_name": _n("string"),
            "sync_sparky": {"type": "boolean"},
        }),
        handler=_swap_exercise,
        scope="training",
        writes=True,
    ),
    dict(
        name="update_workout",
        description="改手表训练的类型 / 名称（get_training_day 的 workout_id），同步不会覆盖。"
                    "apply_to_similar：同星期相近时间的「其他运动」也用这个类型。",
        input_schema=_schema({
            "workout_id": {"type": "integer"},
            "type": nullable("string", "basketball、swimming、strength_training…"),
            "name": nullable("string"),
            "apply_to_similar": nullable("boolean"),
        }),
        handler=lambda conn, uid, a: overrides.update_workout(conn, uid, a["workout_id"], a["type"], a["name"],
                                                              bool(a["apply_to_similar"])),
        scope="training", writes=True,
    ),
    dict(
        name="delete_workout",
        description="删掉（隐藏）重复 / 错误的手表训练，不计入日历、周报、负荷，同步也不回来。",
        input_schema=_schema({"workout_id": {"type": "integer"}}),
        handler=lambda conn, uid, a: overrides.hide_workout(conn, uid, a["workout_id"]),
        scope="training", writes=True,
    ),
]
