"""Assistant tools that read SparkyFitness (workouts, food, weight), all read-only.
They read with the viewed user's own read-only API key (sparky_client.client_for_user), and viewing a family member's data still passes shares authorization first (run_tool)."""
from datetime import date

from .. import sparky_client
from ..toolspec import DETAIL
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
                                   "minutes": e.get("duration_minutes") or None, "avg_hr": e.get("avg_heart_rate"),
                                   "notes": e.get("notes")} for e in s.get("exercises") or []]})
    return out


def _training(conn, uid, a):
    sessions = sparky_client.training_log(sparky_client.client_for_user(conn, uid), *_range(a))
    return sessions if a.get("detail") else summarize_sessions(sessions)


def _nutrition(conn, uid, a):
    return sparky_client.nutrition(sparky_client.client_for_user(conn, uid), *_range(a), entries=a["include_entries"])


def _weight(conn, uid, a):
    rows = sparky_client.weight(sparky_client.client_for_user(conn, uid), *_range(a))
    return {"entries": rows, "weekly_avg": sparky_client.weekly_weight_average(rows)}


SPARKY_TOOLS = [
    Tool(
        name="get_training_log",
        description="力量训练记录（SparkyFitness）：每次训练的 id、日期、名称、来源（body_assistant = 管家写的），"
                    "每个动作的组：「次数×重量 RIR」（哑铃是单只）。动作的禁用状态不在这里。",
        input_schema=_range_schema(detail={**DETAIL, "description": "true=每组一个对象、带动作 ID"}),
        handler=_training,
        scope="training",
    ),
    Tool(
        name="get_nutrition",
        description="饮食（SparkyFitness）：每天热量、蛋白、碳水、脂肪合计和当天目标（训练日 / 休息日不同）；"
                    "logged=false = 没记录（不等于没吃）。",
        input_schema=_range_schema(include_entries={"type": "boolean", "description": "附每条饮食记录"}),
        handler=_nutrition,
        scope="nutrition",
    ),
    Tool(
        name="get_weight",
        description="称重和围度（SparkyFitness check-in：体重 kg、腰围、体脂…）和每周（周一起）平均体重。调热量看周均。",
        input_schema=_range_schema(),
        handler=_weight,
        scope="weight",
    ),
]
