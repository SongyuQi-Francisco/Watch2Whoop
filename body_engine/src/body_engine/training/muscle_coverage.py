"""Coverage check of the muscle load map: every workout a user recorded must credit at least one muscle.

`body-engine muscle-coverage-check [--days N] [--json]` scans the workouts of the last N days of all users (one grouped query) and lists
the types that are a problem:
- "empty": the type's mapping resolves to no muscle, or its load comes out as zero (cannot happen with the current table; this is the
  guard against a bad edit);
- "unmapped": the type is not in training/sport_muscles.SPORT_MUSCLES. It still credits the whole body at a low rating (so the map is
  never blank), but the type needs a real mapping, so it is reported as a bug to fix, like the owner asked.
"generic" / "other" / "unknown" (the source itself did not know the activity) use the fallback by design and are not reported.
Exit code 1 when anything is reported (hourly / nightly job and the doctor read it). Idempotent, read-only.
"""
from __future__ import annotations

import psycopg

from . import muscle_load, sport_muscles

DEFAULT_DAYS = 28


def check_type(t: str) -> str | None:
    """None when the type is covered, else the reason code ("empty" / "unmapped")."""
    m = sport_muscles.resolve(t)
    if not (m["primary"] or m["secondary"]):
        return "empty"
    load = muscle_load.sport_load({"type": t, "min": 30.0})   # 30 minutes, no heart rate: any positive duration must give a load
    if not load["muscles"] or max(v[0] for v in load["muscles"].values()) <= 0:
        return "empty"
    if not m["known"] and m["type"] not in sport_muscles.FALLBACK_TYPES:
        return "unmapped"
    return None


def scan(conn: psycopg.Connection, days: int = DEFAULT_DAYS) -> dict:
    rows = conn.execute("""SELECT type, count(*) AS n, count(DISTINCT user_id) AS users, round(sum(duration_min)::numeric) AS minutes,
                                  array_agg(DISTINCT user_id) AS user_ids
                           FROM workouts
                           WHERE local_date >= current_date - %s AND dup_of IS NULL AND excluded IS NULL AND duration_min > 0
                           GROUP BY type ORDER BY n DESC""", (days,)).fetchall()
    bad = []
    total = 0
    for r in rows:
        total += r["n"]
        reason = check_type(r["type"])
        if reason:
            bad.append({"type": r["type"], "reason": reason, "workouts": r["n"], "users": r["users"], "minutes": int(r["minutes"] or 0),
                        "user_ids": sorted(r["user_ids"])[:20]})
    return {"ok": not bad, "days": days, "workouts": total, "types": len(rows), "uncovered": bad}


def render(res: dict) -> str:
    if res["ok"]:
        return f"✓ 近 {res['days']} 天 {res['workouts']} 次运动（{res['types']} 种类型）都映射到了肌群"
    lines = [f"✗ 近 {res['days']} 天有 {len(res['uncovered'])} 种运动类型没有完整映射到肌群（共 {res['workouts']} 次运动）："]
    why = {"empty": "映射是空的", "unmapped": "没收录，暂时按全身低强度兜底"}
    for b in res["uncovered"]:
        lines.append(f"  - {b['type']}：{why[b['reason']]}（{b['workouts']} 次、{b['minutes']} 分钟、{b['users']} 人，user {b['user_ids']}）"
                     f"→ 到 training/sport_muscles.py 补这个类型")
    return "\n".join(lines)
