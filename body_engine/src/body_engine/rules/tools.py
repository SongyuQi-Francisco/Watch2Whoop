"""AI assistant tool specs for reminders (wrapped into registered Tools by assistant/tools.py)."""
from datetime import date

from ..toolspec import DETAIL, OPT_END, USER, nullable, schema
from . import store


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


_schema = schema
REMINDER_KEYS = ("id", "date", "rule_key", "level", "title", "message", "status")


def _get(conn, uid, a):
    end = _d(a["end"]) or date.today()
    start = _d(a["start"]) or end
    statuses = None if a["include_dismissed"] else ["new", "seen"]
    if a.get("detail"):
        return {"reminders": store.list_reminders(conn, uid, start, end, statuses),
                "rule_runs": store.rule_runs(conn, uid, end, end)}
    # Default: no evidence (the data behind each reminder) and one line per rule run
    runs = [f"{r.get('rule_key')}: {r.get('status')}" + (f"（{r['note']}）" if r.get("note") else "")
            for run in store.rule_runs(conn, uid, end, end) for r in run["results"] or []]
    return {"reminders": [{k: r[k] for k in REMINDER_KEYS}
                          for r in store.list_reminders(conn, uid, start, end, statuses, evidence=False)],
            "rule_runs": runs}


def _dismiss(conn, uid, a):
    row = store.set_status(conn, uid, a["reminder_id"], "dismissed")
    if row is None:
        raise ValueError("没有这条提醒（或不是你的）")
    return row


TOOL_SPECS: list[dict] = [
    dict(
        name="get_reminders",
        description="规则提醒（每天生成，只是建议）：加重 / 减重、按周均体重调热量、练前进食、用户自己设的红肉 / 食物次数上限、"
                    "禁用 / 注意动作、恢复分低 / 睡眠债高。级别 alert > warning > info，状态 new / seen / dismissed；"
                    "rule_runs = 结束那天每条规则的结果（没触发 / 数据不足的原因）。",
        input_schema=_schema({
            "user": USER,
            "start": nullable("string", "YYYY-MM-DD，null = 只看 end 那天（够用）"),
            "end": OPT_END,
            "include_dismissed": {"type": "boolean"},
            "detail": {**DETAIL, "description": "true=带依据数据 evidence（大）"},
        }),
        handler=_get,
        scope="reminders",
    ),
    dict(
        name="dismiss_reminder",
        description="把一条提醒标成「不再提醒」（id 从 get_reminders），同一件事以后不再出现。",
        input_schema=_schema({"reminder_id": {"type": "integer"}}),
        handler=_dismiss,
        scope="reminders",
        writes=True,
    ),
]
