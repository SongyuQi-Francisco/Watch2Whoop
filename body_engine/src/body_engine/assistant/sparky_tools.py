"""Assistant tools that read the workout, food and weight logs, all read-only.
They read the viewed user's own data (sparky_client.reader_for_user: Body Engine's own tables with own_food_read, issue #252,
else that user's own SparkyFitness read-only key), and viewing a family member's data still passes shares authorization
first (run_tool)."""
from datetime import date
from zoneinfo import ZoneInfo

from .. import sparky_client
from ..toolspec import DETAIL
from ..weightlog import store as weight_store
from .tools import Tool, _range_schema

MAX_DAYS = 366


def _range(a: dict) -> tuple[date, date]:
    start, end = date.fromisoformat(a["start"]), date.fromisoformat(a["end"])
    if end < start:
        raise ValueError("end 不能早于 start")
    if (end - start).days >= MAX_DAYS:
        raise ValueError(f"一次最多查 {MAX_DAYS} 天")
    return start, end


def _set_text(s: dict) -> str:
    parts = []
    if s.get("reps") is not None:
        parts.append(f"{s['reps']}×{s['weight_kg']:g}kg" if s.get("weight_kg") is not None else f"{s['reps']}次")
    elif s.get("weight_kg") is not None:
        parts.append(f"{s['weight_kg']:g}kg")
    if s.get("duration_s"):
        parts.append(f"{s['duration_s']}s")
    if s.get("distance_km"):
        parts.append(f"{s['distance_km']:g}km")
    if s.get("rir") is not None:
        parts.append(f"RIR{s['rir']:g}")
    if s.get("type") and s["type"] != "Working Set":
        parts.append(f"({s['type']})")
    if s.get("notes"):
        parts.append(f"「{s['notes']}」")
    return " ".join(parts) or "—"


def summarize_sessions(sessions: list[dict]) -> list[dict]:
    """One line of sets per exercise instead of one object per set (same numbers, ~1/4 of the tokens)."""
    out = []
    for s in sessions:
        out.append({"id": s.get("id"), "date": s.get("date"), "name": s.get("name"), "notes": s.get("notes"),
                    "source": s.get("source"), "minutes": s.get("total_duration_minutes") or None,
                    "exercises": [{"name": e.get("name"), "sets": ", ".join(_set_text(x) for x in e.get("sets") or []),
                                   "set_ids": [x.get("id") for x in e.get("sets") or []] or None,
                                   "minutes": e.get("duration_minutes") or None, "avg_hr": e.get("avg_heart_rate"),
                                   "notes": e.get("notes")} for e in s.get("exercises") or []]})
    return out


def _training(conn, uid, a):
    sessions = sparky_client.training_log(sparky_client.reader_for_user(conn, uid), *_range(a))
    return sessions if a.get("detail") else summarize_sessions(sessions)


def _nutrition(conn, uid, a):
    from ..nutrition import daily
    return daily.nutrition(conn, uid, *_range(a), include_entries=a["include_entries"])


def _weight(conn, uid, a):
    start, end = _range(a)
    rows = sparky_client.weight(sparky_client.reader_for_user(conn, uid), start, end)
    ctx = weight_store.in_range(conn, uid, start, end)   # ctx = measuring conditions (空腹 / 运动后…), note, at = measured HH:MM
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])
    entries = [{**r, **weight_store.for_ai(ctx.get(r["date"]), tz)} for r in rows]
    return {"entries": entries, "weekly_avg": sparky_client.weekly_weight_average(rows)}


SPARKY_TOOLS = [
    Tool(
        name="get_training_log",
        description="力量训练记录：每次训练的 id、日期、名称、来源（body_assistant = 管家写的），"
                    "每个动作的组：「次数×重量 RIR」（哑铃是单只）和 set_ids（改 / 删一组用）。动作的禁用状态不在这里。",
        input_schema=_range_schema(detail={**DETAIL, "description": "true=每组一个对象、带动作 ID"}),
        handler=_training,
        group="training", scope="training",
    ),
    Tool(
        name="get_nutrition",
        description="饮食：每天热量、三大营养素、糖 / 添加糖 / 纤维 / 钠合计（known = 有数的条数，null = 未知不是 0）、"
                    "含糖饮料数、当天生效的目标和达标（adherence）；logged=false = 没记录（不等于没吃）。",
        input_schema=_range_schema(include_entries={"type": "boolean", "description": "附每条记录（含 id、source）"}),
        handler=_nutrition,
        group="nutrition", scope="nutrition",
    ),
    Tool(
        name="get_weight",
        description="称重和围度（每天一条：体重 kg、腰围、体脂…，ctx = 测量情况、note = 备注）和每周（周一起）平均体重。调热量看周均。",
        input_schema=_range_schema(),
        handler=_weight,
        group="body", scope="weight",
    ),
]
