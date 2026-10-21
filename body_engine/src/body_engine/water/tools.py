"""AI tool for water (registered by assistant/tools.py). One short write tool to stay inside the tool-definition budget
(tests/test_briefing.py); the AI reads today's total from get_briefing (section water). Undo: rollback_write (kind water)."""
from ..assistant import writelog
from ..toolspec import nullable, schema
from . import enabled, store


def _log(conn, uid, a):
    if not enabled(conn, uid):
        raise ValueError("喝水记录还没对这个账号开放")
    at = store.parse_at(a["at"], store.tz_of(conn, uid))
    row = store.add(conn, uid, a["ml"], "ai", at)
    writelog.record(conn, "water", "create", row["id"], None, row)
    day = store.day_view(conn, uid, row["date"])
    return {"logged": row["ml"], "at": row["at"], "today_ml": day["total_ml"], "goal_ml": day["goal"]["ml"]}


TOOL_SPECS: list[dict] = [
    dict(
        name="log_water",
        description="记喝水（ml；一杯≈250）。at=HH:MM，null=现在。",
        input_schema=schema({"ml": {"type": "number"}, "at": nullable("string")}),
        handler=_log, scope="nutrition", writes=True,
    ),
]
