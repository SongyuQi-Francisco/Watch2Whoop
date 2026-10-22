"""AI assistant tool specs for training history and Training V2 (wrapped into Tool and registered by assistant/tools.py):
read the calendar / a day, correct or hide a watch workout (overrides.py), exercise catalog, weekly splits, plan management, exercise swap, day changes (day_overrides.py)."""
from datetime import date, datetime, timedelta

from ..toolspec import DATE, DETAIL, USER, nullable, schema
from . import catalog, day_overrides, history, loads, overrides, params as P, plans


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
    ex = catalog.get_exercise(conn, uid, a["exercise"])
    ex.pop("image_urls", None)   # pictures are for the Today page, not the model
    out = {**ex, **loads.for_exercise(conn, a.get("_viewer_id", uid), uid, ex, plans._user_today(conn, uid))}
    for alt in out.get("alternatives") or []:
        alt.pop("thumb", None)
    return out


def _propose_plan(conn, uid, a):
    return plans.propose_plan(
        conn,
        user_id=uid,
        split_type=a["split_type"],
        custom_days=a.get("custom_days"),
        name=a.get("name"),
        notes=a.get("notes"),
        auto_swap_banned=bool(a.get("auto_swap_banned")),
        training_days=a.get("training_days"),
        start_date=a.get("start_date"),
        addons=a.get("addons"),
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
        training_days=a.get("training_days"),
        addons=a.get("addons"),
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


PLAN_START = {
    "training_days": {"type": ["array", "null"], "items": {"type": "integer"}, "description": "能练的星期几 1–7"},
    "start_date": _n("string", "YYYY-MM-DD，默认今天"),
    "addons": {"type": ["array", "null"], "items": {"type": "string", "enum": list(P.MOBILITY_ADDONS)}},
}

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
        group="training", scope="training",
    ),
    dict(
        name="get_training_day",
        description="某天训练详情：来源、起止、时长、能量、心率五区、TRIMP、强度，力量动作与每组次数/重量/RIR，全天指标。",
        input_schema=_schema({"user": USER, "date": DATE}),
        handler=_day,
        group="training", scope="training",
    ),
    dict(
        name="get_exercise",
        description="单个动作详情：中英文名、主次肌群、器械、动作模式、要点、关节压力、用户限制、同模式安全替代、推荐重量 load 与最近 3 次记录。",
        input_schema=_schema({"user": USER, "exercise": {"type": "string"}}),
        handler=_get_exercise,
        group="training", scope="training",
    ),
    dict(
        name="propose_plan",
        description="周计划草案（不写库）。先问用户 ask_user_first 里的事（开始日、本周剩余怎么排、哪几天能练、不能练的日子、热身附加项），出草案给用户看（含推荐重量）。first_week=本周已练后只补缺的安排。",
        input_schema=_schema({
            "user": USER,
            "split_type": {"type": "string", "enum": SPLIT_ENUM},
            "name": _n("string"),
            "notes": _n("string"),
            "auto_swap_banned": {"type": "boolean"},
            "custom_days": {"type": ["array", "null"], "items": PLAN_DAY},
            **PLAN_START,
        }),
        handler=_propose_plan,
        group="training", scope="training",
    ),
    dict(
        name="save_plan",
        description="用户确认 propose_plan 草案后才调：保存并激活周计划（参数同草案），本周按 first_week 补缺；SparkyFitness 同步失败计划照样保存，按 tell_user 告诉用户。",
        input_schema=_schema({
            "split_type": {"type": "string", "enum": SPLIT_ENUM},
            "name": _n("string"),
            "start_date": _n("string", "YYYY-MM-DD"),
            "notes": _n("string"),
            "auto_swap_banned": {"type": "boolean"},
            "sync_sparky": {"type": "boolean"},
            "custom_days": {"type": ["array", "null"], "items": PLAN_DAY},
            "training_days": PLAN_START["training_days"],
            "addons": PLAN_START["addons"],
        }),
        handler=_save_plan,
        group="training", scope="training",
        writes=True,
    ),
    dict(
        name="get_week_plan",
        description="当前生效的周训练计划（周一至周日）：每天动作、组数处方与推荐重量 load（按进阶/同上次/换算/估算）、肌群周组数、近期加减重建议、今日恢复分自调节建议。",
        input_schema=_schema({"user": USER, "date": _n("string", "YYYY-MM-DD"), "detail": DETAIL}),
        handler=_get_week_plan,
        group="training", scope="training",
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
        group="training", scope="training",
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
        group="training", scope="training", writes=True,
    ),
    dict(
        name="delete_workout",
        description="删掉（隐藏）重复 / 错误的手表训练，不计入日历、周报、负荷，同步也不回来。",
        input_schema=_schema({"workout_id": {"type": "integer"}}),
        handler=lambda conn, uid, a: overrides.hide_workout(conn, uid, a["workout_id"]),
        group="training", scope="training", writes=True,
    ),
]


# ---------------------------------------------------------------- day changes (day_overrides.py)
def _reschedule_day(conn, uid, a):
    return day_overrides.reschedule_day(conn, uid, date.fromisoformat(a["date"]), a["session"], reason=a.get("reason"),
                                    replan=a.get("replan") is not False)


def _swap_days(conn, uid, a):
    return day_overrides.swap_days(conn, uid, date.fromisoformat(a["date_a"]), date.fromisoformat(a["date_b"]))


def _replan_week(conn, uid, a):
    return day_overrides.replan_week(conn, uid, _d(a.get("date")), apply=a.get("apply") is not False)


def _undo_day_changes(conn, uid, a):
    return day_overrides.undo_day_changes(conn, uid, _d(a.get("start")), _d(a.get("end")))


TOOL_SPECS += [
    dict(
        name="reschedule_day",
        description="改某天练什么：session=计划里的训练名 / 周几 / 休息 / 计划外活动；默认重排本周剩余几天。",
        input_schema=_schema({
            "date": DATE,
            "session": {"type": "string"},
            "reason": _n("string"),
            "replan": {"type": "boolean"},
        }),
        handler=_reschedule_day,
        group="training", scope="training",
        writes=True,
    ),
    dict(
        name="swap_days",
        description="互换两天的训练（如 周二 ↔ 周四）。",
        input_schema=_schema({"date_a": DATE, "date_b": DATE}),
        handler=_swap_days,
        group="training", scope="training",
        writes=True,
    ),
    dict(
        name="replan_week",
        description="重排本周 date 之后的训练（不连练同一肌群、看恢复分）；apply=false 只预览。",
        input_schema=_schema({"date": _n("string", "YYYY-MM-DD，默认今天"), "apply": {"type": "boolean"}}),
        handler=_replan_week,
        group="training", scope="training",
        writes=True,
    ),
    dict(
        name="undo_day_changes",
        description="撤销按日期的训练调整（默认今天到周日）。",
        input_schema=_schema({"start": _n("string", "YYYY-MM-DD"), "end": _n("string", "YYYY-MM-DD")}),
        handler=_undo_day_changes,
        group="training", scope="training",
        writes=True,
    ),
]
