"""Assistant tool registry. The format is the Claude API tool definition (name / description / input_schema);
the MCP server is generated from here too, so only one copy is maintained."""
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from .. import auth, queries
from ..i18n import use_lang
from ..toolspec import DETAIL, END, START, USER, fill_defaults, nullable, schema
from . import briefing, writelog
from ..scores import store as scores_store
from ..records.tools import TOOL_SPECS as RECORD_TOOL_SPECS
from ..rules.tools import TOOL_SPECS as RULE_TOOL_SPECS
from ..goals.tools import TOOL_SPECS as GOAL_TOOL_SPECS
from ..training.tools import TOOL_SPECS as TRAINING_TOOL_SPECS


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[psycopg.Connection, int, dict], Any]  # (conn, owner_id, args) -> result
    scope: str            # shares authorization needed when viewing someone else's data
    writes: bool = False  # write operations need user confirmation before running
    group: str = ""       # domain group (ai/params.GROUPS): which tools are sent on each call, the rest via find_tools
    strict: bool = True   # Claude strict tools; only the meta tools with a free-form `args` object are not strict

    def api_definition(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema, "strict": self.strict}


def _with_now(profile: dict | None) -> dict | None:
    if profile:
        now = datetime.now(ZoneInfo(profile["timezone"]))
        profile["now_local"] = now.isoformat(timespec="minutes")
        profile["weekday"] = "一二三四五六日"[now.weekday()]
    return profile


def _d(s: str) -> date:
    return date.fromisoformat(s)


_schema = schema


def _range_schema(**extra: dict) -> dict:
    return _schema({"user": USER, "start": START, "end": END, **extra})


SCORE_DETAIL_ONLY = ("hrv_kind", "hrv_night_ln", "hr_max", "algo_version")


def _scores(conn, uid, a):
    rows = scores_store.scores(conn, uid, _d(a["start"]), _d(a["end"]), a["details"])
    if a["details"]:
        return rows
    return [{k: v for k, v in r.items() if k not in SCORE_DETAIL_ONLY} for r in rows]


def _daily_metrics(conn, uid, a):
    rows = queries.daily_metrics(conn, uid, _d(a["start"]), _d(a["end"]), a["metrics"] or None)
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


def _briefing(conn, uid, a):
    return briefing.get(conn, a.get("_viewer_id", uid), uid)


TOOLS: list[Tool] = [
    Tool(
        name="get_briefing",
        description="对话开始先调它（约 1.5k token，一次代替好几个工具）：现在时间、资料、最新恢复 / 睡眠 / HRV / 静息心率、"
                    "最近 7 天评分和入睡时间、7 天 vs 60 天均值、28 天训练概况和最近 7 天训练、今天的提醒、状态卡、"
                    "档案卡要点、最近的观察笔记、禁用 / 注意动作。需要细节再调对应工具。",
        input_schema=_schema({"user": USER}),
        handler=_briefing,
        group="body", scope="profile",
    ),
    Tool(
        name="get_profile",
        description="基本资料：年龄、身高、性别、时区和当地时间 now_local。",
        input_schema=_schema({"user": USER}),
        handler=lambda conn, uid, a: _with_now(queries.profile(conn, uid)),
        group="body", scope="profile",
    ),
    Tool(
        name="list_metrics",
        description="有哪些每日指标：名称、单位、sum / avg、覆盖日期。指标名不确定时先查。",
        input_schema=_schema({"user": USER}),
        handler=lambda conn, uid, a: queries.metric_names(conn, uid),
        group="body", scope="metrics",
    ),
    Tool(
        name="get_daily_metrics",
        description="每日健康指标（步数、能量、静息心率、HRV、血氧、体重…），按指标返回 {unit, source, values: {日期: 值}}。"
                    "metrics 尽量指定（空 = 全部，量大）。",
        input_schema=_range_schema(metrics={"type": "array", "items": {"type": "string"}, "description": "如 step_count"},
                                   detail={**DETAIL, "description": "true=逐行带 n / min / max"}),
        handler=_daily_metrics,
        group="body", scope="metrics",
    ),
    Tool(
        name="get_sleep",
        description="睡眠记录：入睡、醒来、各阶段分钟，按醒来日期。is_main=false 是小睡。",
        input_schema=_range_schema(main_only={"type": "boolean", "description": "只要主睡眠"}),
        handler=lambda conn, uid, a: queries.sleep_sessions(conn, uid, _d(a["start"]), _d(a["end"]), a["main_only"]),
        group="body", scope="sleep",
    ),
    Tool(
        name="get_scores",
        description="每日评分（日期 = 醒来那天）：睡眠分、睡眠表现 / 需求 / 睡眠债（分钟）、恢复分 0–100、当晚 HRV（ms，SDNN）、"
                    "静息心率、负荷 strain 0–21、TRIMP、CTL / ATL / TSB。分数为空看 flags。",
        input_schema=_range_schema(details={"type": "boolean", "description": "附分项、z 分数、基线（大）"}),
        handler=_scores,
        group="body", scope="scores",
    ),
    Tool(
        name="list_events",
        description="身体事件（伤病、疼痛、低血糖、生病、看医生），按本地日期。",
        input_schema=_range_schema(),
        handler=lambda conn, uid, a: queries.events(conn, uid, _d(a["start"]), _d(a["end"])),
        group="records", scope="events",
    ),
    Tool(
        name="add_event",
        description="记一条身体事件（伤病、疼痛、低血糖、生病、看医生）。",
        input_schema=_schema({
            "occurred_at": {"type": "string", "description": "ISO 8601，不带时区按用户时区"},
            "title": {"type": "string", "description": "一句话标题"},
            "category": nullable("string", "injury / pain / illness / symptom / medical / other"),
            "body_part": nullable("string"),
            "severity": nullable("integer", "1–5"),
            "note": nullable("string"),
        }),
        handler=lambda conn, uid, a: queries.add_event(conn, uid, a["occurred_at"], a["title"], a["category"],
                                                      a["body_part"], a["severity"], a["note"]),
        group="records", scope="events",
        writes=True,
    ),
]
from .sparky_tools import SPARKY_TOOLS  # noqa: E402 (it needs Tool above)
TOOLS += SPARKY_TOOLS
TOOLS += [Tool(**spec) for spec in RECORD_TOOL_SPECS]  # health profile: supplements, lab results, journal, events, exercise rules
TOOLS += [Tool(**spec) for spec in RULE_TOOL_SPECS]  # rule reminders
TOOLS += [Tool(**spec) for spec in GOAL_TOOL_SPECS]  # bedtime goal
TOOLS += [Tool(**spec) for spec in TRAINING_TOOL_SPECS]  # training calendar (watch + SparkyFitness merged)
from ..insights.tools import TOOL_SPECS as INSIGHT_TOOL_SPECS  # noqa: E402
TOOLS += [Tool(**spec) for spec in INSIGHT_TOOL_SPECS]  # WHOOP-style analysis: body age, stress, coach, reports
from ..cardio.tools import TOOL_SPECS as CARDIO_TOOL_SPECS  # noqa: E402
TOOLS += [Tool(**spec) for spec in CARDIO_TOOL_SPECS]  # heart rate recovery, recovery index, ECG summaries
from .butler_tools import TOOL_SPECS as BUTLER_TOOL_SPECS  # noqa: E402
TOOLS += [Tool(**spec) for spec in BUTLER_TOOL_SPECS]  # body butler: write SparkyFitness, screenshot metrics, long-term memory
from ..grocery.tools import TOOL_SPECS as GROCERY_TOOL_SPECS  # noqa: E402
TOOLS += [Tool(**spec) for spec in GROCERY_TOOL_SPECS]  # grocery: product library, weekly shopping list, pantry
from .diet_profile_tools import TOOL_SPECS as DIET_PROFILE_TOOL_SPECS  # noqa: E402
TOOLS += [Tool(**spec) for spec in DIET_PROFILE_TOOL_SPECS]  # 「我的饮食档案」: targets rationale, constraints, day plan, versions
from .diet_profile_tools import CONSTRAINT_TOOL_SPECS  # noqa: E402
TOOLS += [Tool(**spec) for spec in CONSTRAINT_TOOL_SPECS]  # per-user diet constraints (nutrition/constraints.py, issue #76)
from ..friends.tools import TOOL_SPECS as FRIENDS_TOOL_SPECS  # noqa: E402
TOOLS += [Tool(**spec) for spec in FRIENDS_TOOL_SPECS]  # Friends page: circle members' shared status and leaderboard ranks
from ..water.tools import TOOL_SPECS as WATER_TOOL_SPECS  # noqa: E402
TOOLS += [Tool(**spec) for spec in WATER_TOOL_SPECS]  # daily water intake (issue #125)
from ..feedback.tools import TOOL_SPECS as FEEDBACK_TOOL_SPECS  # noqa: E402
TOOLS += [Tool(**spec) for spec in FEEDBACK_TOOL_SPECS]  # root rules: bugs and feature requests to the developers
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def run_tool(conn: psycopg.Connection, viewer_id: int, channel: str, name: str, args: dict, *,
             provider_id: int | None = None, model: str | None = None) -> Any:
    """Run one tool: authorize -> call -> write audit. Confirmation of writes is the caller's job (chat loop / MCP client).
    The tool runs in a savepoint: on error only what it half-wrote is undone and the audit stays (the caller must also commit after an error, see mcp_server).
    In-Today-page chat (ai/chat.py) additionally records provider_id / model; for the MCP channel these two columns are empty."""
    with use_lang("zh"):   # AI-facing texts stay as they are, also when the in-page chat request carries X-Body-Lang: en
        return _run_tool(conn, viewer_id, channel, name, args, provider_id=provider_id, model=model)


def _run_tool(conn: psycopg.Connection, viewer_id: int, channel: str, name: str, args: dict, *,
              provider_id: int | None = None, model: str | None = None) -> Any:
    tool = TOOLS_BY_NAME[name]
    full = fill_defaults(tool.input_schema, args)  # MCP clients may leave out nullable / defaulted fields (toolspec.relaxed)
    owner_id = viewer_id if full.get("user", "me") == "me" else int(full["user"])
    try:
        if tool.writes and owner_id != viewer_id:
            raise PermissionError("写操作只能写给自己")
        if not auth.can_view(conn, viewer_id, owner_id, tool.scope):
            raise PermissionError(f"没有查看用户 {owner_id} 的 {tool.scope} 的授权")
        with conn.transaction():
            args_in = {**full, "_viewer_id": viewer_id, "_channel": channel}
            if tool.writes:  # every AI write is logged with before / after values (writelog.py, rollback_write)
                with writelog.capture(viewer_id, channel, name) as cap:
                    result = tool.handler(conn, owner_id, args_in)
                    if not cap["ids"]:
                        writelog.record_generic(conn, full, result)
            else:
                result = tool.handler(conn, owner_id, args_in)  # training calendar blocks are further authorized per viewer
    except Exception as e:
        _audit(conn, viewer_id, channel, name, args, False, str(e), provider_id, model)
        raise
    _audit(conn, viewer_id, channel, name, args, True, None, provider_id, model)
    if tool.writes:  # whatever it wrote may be in the opening briefing (SparkyFitness writes are not in its fingerprint)
        briefing.invalidate(viewer_id)
    return result


def _audit(conn, user_id, channel, tool, args, ok, error, provider_id=None, model=None):
    if provider_id is None and model is None:  # MCP channel: do not touch the columns added by migration 110 (databases before the migration still work)
        conn.execute("INSERT INTO assistant_audit (user_id, channel, tool, args, ok, error) VALUES (%s,%s,%s,%s,%s,%s)",
                     (user_id, channel, tool, Jsonb(args), ok, error))
        return
    conn.execute("""INSERT INTO assistant_audit (user_id, channel, tool, args, ok, error, provider_id, model)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (user_id, channel, tool, Jsonb(args), ok, error, provider_id, model))
