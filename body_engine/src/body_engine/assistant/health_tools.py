"""The first health tools: profile, daily metrics, sleep, scores, the opening briefing and body events (moved from
assistant/tools.py, issue #116). Only tool specs (dicts) are given; dl_app.py registers them. These use the framework's
(ctx, args) handler signature (datalayer/execute.ToolContext)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .. import auth, queries
from ..scores import store as scores_store
from ..toolspec import DETAIL, USER, nullable, range_schema, schema
from . import briefing


def _with_now(profile: dict | None) -> dict | None:
    if profile:
        now = datetime.now(ZoneInfo(profile["timezone"]))
        profile["now_local"] = now.isoformat(timespec="minutes")
        profile["weekday"] = "一二三四五六日"[now.weekday()]
    return profile


def _d(s: str) -> date:
    return date.fromisoformat(s)


SCORE_DETAIL_ONLY = ("hrv_kind", "hrv_night_ln", "hr_max", "algo_version")


def _scores(ctx, a):
    rows = scores_store.scores(ctx.conn, ctx.owner_id, _d(a["start"]), _d(a["end"]), a["details"],
                               not a["details"] or auth.can_view(ctx.conn, ctx.viewer_id, ctx.owner_id, "sleep"))
    if a["details"]:
        return rows
    return [{k: v for k, v in r.items() if k not in SCORE_DETAIL_ONLY} for r in rows]


def _daily_metrics(ctx, a):
    rows = queries.daily_metrics(ctx.conn, ctx.owner_id, _d(a["start"]), _d(a["end"]), a["metrics"] or None)
    if a.get("detail"):
        return rows
    out: dict = {}
    for r in rows:  # pivot: one entry per metric instead of repeating unit / source / agg on every row
        m = out.setdefault(r["metric"], {"unit": r["unit"], "source": r["source"], "values": {}})
        m["values"][r["date"].isoformat()] = r["value"]
        if r["source"] != m["source"]:
            m.setdefault("other_source", {})[r["date"].isoformat()] = r["source"]
        if r.get("flags"):
            m.setdefault("flags", {})[r["date"].isoformat()] = r["flags"]
    return out


TOOL_SPECS: list[dict] = [
    dict(
        name="get_briefing",
        description="对话开始先调它（约 1.5k token，一次代替好几个工具）：现在时间、资料、最新恢复 / 睡眠 / HRV / 静息心率、"
                    "最近 7 天评分和入睡时间、7 天 vs 60 天均值、28 天训练概况和最近 7 天训练、今天的提醒、状态卡、"
                    "档案卡要点、最近的观察笔记、禁用 / 注意动作。需要细节再调对应工具。",
        input_schema=schema({"user": USER}),
        handler=lambda ctx, a: briefing.get(ctx.conn, ctx.viewer_id, ctx.owner_id),
        group="body", scope="profile",
    ),
    dict(
        name="get_profile",
        description="基本资料：年龄、身高、性别、时区和当地时间 now_local。",
        input_schema=schema({"user": USER}),
        handler=lambda ctx, a: _with_now(queries.profile(ctx.conn, ctx.owner_id)),
        group="body", scope="profile",
    ),
    dict(
        name="list_metrics",
        description="有哪些每日指标：名称、单位、sum / avg、覆盖日期。指标名不确定时先查。",
        input_schema=schema({"user": USER}),
        handler=lambda ctx, a: queries.metric_names(ctx.conn, ctx.owner_id),
        group="body", scope="metrics",
    ),
    dict(
        name="get_daily_metrics",
        description="每日健康指标（步数、能量、静息心率、HRV、血氧、体重…），按指标返回 {unit, source, values: {日期: 值}}。"
                    "metrics 尽量指定（空 = 全部，量大）。",
        input_schema=range_schema(metrics={"type": "array", "items": {"type": "string"}, "description": "如 step_count"},
                                  detail={**DETAIL, "description": "true=逐行带 n / min / max"}),
        handler=_daily_metrics,
        group="body", scope="metrics",
    ),
    dict(
        name="get_sleep",
        description="睡眠记录：入睡、醒来、各阶段分钟，按醒来日期。is_main=false 是小睡。",
        input_schema=range_schema(main_only={"type": "boolean", "description": "只要主睡眠"}),
        handler=lambda ctx, a: queries.sleep_sessions(ctx.conn, ctx.owner_id, _d(a["start"]), _d(a["end"]), a["main_only"]),
        group="body", scope="sleep",
    ),
    dict(
        name="get_scores",
        description="每日评分（日期 = 醒来那天）：睡眠分、睡眠表现 / 需求 / 睡眠债（分钟）、恢复分 0–100、当晚 HRV（ms，SDNN）、"
                    "静息心率、负荷 strain 0–21、TRIMP、CTL / ATL / TSB。分数为空看 flags。",
        input_schema=range_schema(details={"type": "boolean", "description": "附分项、z 分数、基线（大）"}),
        handler=_scores,
        group="body", scope="scores",
    ),
    dict(
        name="list_events",
        description="身体事件（伤病、疼痛、低血糖、生病、看医生），按本地日期。",
        input_schema=range_schema(),
        handler=lambda ctx, a: queries.events(ctx.conn, ctx.owner_id, _d(a["start"]), _d(a["end"])),
        group="records", scope="events",
    ),
    dict(
        name="add_event",
        description="记一条身体事件（伤病、疼痛、低血糖、生病、看医生）。",
        input_schema=schema({
            "occurred_at": {"type": "string", "description": "ISO 8601，不带时区按用户时区"},
            "title": {"type": "string", "description": "一句话标题"},
            "category": nullable("string", "injury / pain / illness / symptom / medical / other"),
            "body_part": nullable("string"),
            "severity": nullable("integer", "1–5"),
            "note": nullable("string"),
        }),
        handler=lambda ctx, a: queries.add_event(ctx.conn, ctx.owner_id, a["occurred_at"], a["title"], a["category"],
                                                 a["body_part"], a["severity"], a["note"]),
        group="records", scope="events",
        writes=True,
    ),
]
