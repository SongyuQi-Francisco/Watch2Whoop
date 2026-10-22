"""Heart-rate zone distribution (Z1–Z5 minutes per day / per workout), VO₂max trend and fitness level (same-age same-sex FRIEND 2022 norms)."""
import statistics
from datetime import date, timedelta

import psycopg

from ..i18n import L
from . import healthspan as HS
from .healthspan_store import _table_exists

ZONES = ["Z1 50–60%", "Z2 60–70%", "Z3 70–80%", "Z4 80–90%", "Z5 ≥90%"]   # %HRmax (same set as TRIMP)
LEVELS = ((80, "excellent", "优秀"), (60, "good", "良好"), (40, "average", "中等"), (20, "fair", "一般"), (0, "poor", "较差"))
LEVELS_EN = {"excellent": "Excellent", "good": "Good", "average": "Average", "fair": "Fair", "poor": "Poor"}


def hr_zones(conn: psycopg.Connection, user_id: int, start: date, end: date, with_workouts: bool = True) -> dict:
    """with_workouts=False: the viewer has no training share, the per-workout list is left out (`hidden`)."""
    days = []
    for r in conn.execute("""SELECT date, strain, hr_max, details->'load'->'zone_minutes' AS z, flags->>'no_raw_hr' AS missing
                             FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date""",
                          (user_id, start, end)):
        days.append({"date": r["date"], "zone_minutes": r["z"], "hr_max": r["hr_max"], "strain": r["strain"],
                     "missing_reason": r["missing"] if r["z"] is None else None})
    workouts = None
    if with_workouts and _table_exists(conn, "workouts"):
        workouts = [dict(r) for r in conn.execute(
            """SELECT id, local_date AS date, name_zh, type, start_at, duration_min, avg_hr, max_hr, trimp,
                      hr_zone_min AS zone_minutes, flags FROM workouts
               WHERE user_id = %s AND dup_of IS NULL AND excluded IS NULL AND local_date BETWEEN %s AND %s
               ORDER BY start_at""", (user_id, start, end))]
    total = [round(sum((d["zone_minutes"] or [0] * 5)[i] for d in days), 1) for i in range(5)]
    if not with_workouts:
        return {"zones": ZONES, "start": start, "end": end, "days": days, "total_minutes": total, "hidden": ["training"]}
    return {"zones": ZONES, "start": start, "end": end, "days": days, "total_minutes": total, "workouts": workouts,
            "workouts_note": None if workouts is not None else "训练记录表（workouts）还没建，只有每天的区间分钟"}


def level(pct: float | None) -> tuple[str, str] | tuple[None, None]:
    if pct is None:
        return None, None
    _, key, zh = next(x for x in LEVELS if pct >= x[0])
    return key, zh


def vo2max(conn: psycopg.Connection, user_id: int, end: date, days: int = 365) -> dict:
    u = conn.execute("SELECT birth_date, sex FROM users WHERE id = %s", (user_id,)).fetchone()
    sex = {"m": "male", "male": "male", "男": "male", "f": "female", "female": "female", "女": "female"}.get(
        (u["sex"] or "").strip().lower())
    rows = conn.execute("""SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = 'vo2_max'
                           AND date BETWEEN %s AND %s ORDER BY date""",
                        (user_id, end - timedelta(days=days - 1), end)).fetchall()
    months: dict[str, list[float]] = {}
    for r in rows:
        months.setdefault(r["date"].strftime("%Y-%m"), []).append(r["value"])
    latest = rows[-1] if rows else None
    recent = [r["value"] for r in rows if r["date"] >= end - timedelta(days=89)]
    current = round(statistics.fmean(recent), 1) if recent else (latest["value"] if latest else None)
    age = HS.chrono_age(u["birth_date"], end) if u["birth_date"] else None
    pct = HS.vo2_percentile(current, age, sex) if current is not None and age is not None and sex else None
    key, zh = level(pct)
    trend = None
    if len(rows) >= 3:
        xs = [(r["date"] - end).days / 365.2425 for r in rows]
        ys = [r["value"] for r in rows]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        trend = round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx, 2) if sxx else None
    return {"current": current, "current_basis": "最近 90 天读数的平均" if recent else "最近一次读数",
            "latest": None if latest is None else {"date": latest["date"], "value": latest["value"]},
            "readings": len(rows), "trend_per_year": trend,
            "monthly": [{"month": m, "avg": round(statistics.fmean(v), 1), "n": len(v)} for m, v in sorted(months.items())],
            "norm_median": round(HS.vo2_norm(age, sex), 1) if age is not None and sex else None,
            "percentile": pct, "level": key, "level_zh": zh,   # level_zh = data (AI); level_label = the page's language
            "level_label": L(zh, LEVELS_EN[key]) if key else None,
            "norm_source": "FRIEND 2022 跑步机常模（Kaminsky 2022 Mayo Clin Proc），同龄同性别",
            "missing_reason": None if sex else "没填性别（users.sex），没法对照常模"}
