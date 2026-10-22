"""Training goals (rollout flag muscle_load): focus muscles and a one-sentence physique goal, edited in 我 → 训练设置.
The focus muscles live in training_goals; the physique sentence is also the AI's long-term memory (profile card item physique_goal),
so every conversation knows it. Self only (the api layer); the AI reads both through get_muscle_load / get_memory."""
from __future__ import annotations

import psycopg

from ..assistant import briefing, memory
from ..i18n import L
from . import catalog_data as CD

MAX_FOCUS = 5
MAX_GOAL_CHARS = 200
MEMORY_KEY, MEMORY_LABEL = "physique_goal", "体态 / 形体目标"


def get(conn: psycopg.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT focus_muscles, physique_goal, updated_at FROM training_goals WHERE user_id = %s", (user_id,)).fetchone()
    return {"focus_muscles": list((row or {}).get("focus_muscles") or []), "physique_goal": (row or {}).get("physique_goal"),
            "updated_at": (row or {}).get("updated_at"),
            "muscles": [{"key": k, "name": L(zh, k)} for k, zh in CD.MUSCLE_ZH.items()], "max_focus": MAX_FOCUS}


def save(conn: psycopg.Connection, user_id: int, focus_muscles: list[str] | None, physique_goal: str | None) -> dict:
    focus = list(dict.fromkeys(focus_muscles or []))
    bad = [m for m in focus if m not in CD.MUSCLE_ZH]
    if bad:
        raise ValueError(L(f"没有这些肌群：{'、'.join(bad)}", f"Unknown muscles: {', '.join(bad)}"))
    if len(focus) > MAX_FOCUS:
        raise ValueError(L(f"重点肌群最多选 {MAX_FOCUS} 个", f"Pick at most {MAX_FOCUS} focus muscles"))
    goal = (physique_goal or "").strip()
    if len(goal) > MAX_GOAL_CHARS:
        raise ValueError(L(f"体态目标最多 {MAX_GOAL_CHARS} 字", f"The physique goal is at most {MAX_GOAL_CHARS} characters"))
    conn.execute(
        """INSERT INTO training_goals (user_id, focus_muscles, physique_goal) VALUES (%s, %s, %s)
           ON CONFLICT (user_id) DO UPDATE SET focus_muscles = EXCLUDED.focus_muscles, physique_goal = EXCLUDED.physique_goal, updated_at = now()""",
        (user_id, focus, goal or None))
    if goal:
        memory.update_profile(conn, user_id, MEMORY_KEY, goal, label=MEMORY_LABEL, source="user")
    else:
        memory.delete_profile(conn, user_id, MEMORY_KEY)
    briefing.invalidate(user_id)
    return get(conn, user_id)
