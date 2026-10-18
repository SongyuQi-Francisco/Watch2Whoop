"""AI assistant tool specs for training history (wrapped into Tool and registered by assistant/tools.py), all read-only."""
from datetime import date, datetime, timedelta

from ..toolspec import DATE, DETAIL, USER, nullable, schema
from . import history


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


_schema = schema


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


TOOL_SPECS: list[dict] = [
    dict(
        name="get_training_calendar",
        description="训练日历（手表训练 + SparkyFitness 力量记录按天合并）：每天强度（rest / light / moderate / hard / max）、"
                    "训练和时长、strain、恢复分、活动能量、步数；summary：训练 / 休息天数、总时长、平均 strain、最长连练、"
                    "各类型次数、各强度天数。安排训练前看最近 2–4 周（都不填 = 最近 28 天）。",
        input_schema=_schema({
            "user": USER,
            "month": nullable("string", "YYYY-MM 整月；和 start / end 二选一"),
            "start": nullable("string", "YYYY-MM-DD，null = end 前 27 天"),
            "end": nullable("string", "YYYY-MM-DD，null = 今天；最多 92 天"),
            "detail": {**DETAIL, "description": "true=每次训练的 id、开始时间、来源、能量"},
        }),
        handler=_calendar,
        scope="training",
    ),
    dict(
        name="get_training_day",
        description="某天的训练详情：每次训练的来源、类型、开始 / 结束、时长、能量、平均 / 最高心率、五区分钟、TRIMP、强度和依据；"
                    "力量训练的动作（禁用 / 注意 / 正常）和每组次数、重量、RIR、训练量；daily：全天能量、步数、strain、次日恢复分。",
        input_schema=_schema({"user": USER, "date": DATE}),
        handler=_day,
        scope="training",
    ),
]
