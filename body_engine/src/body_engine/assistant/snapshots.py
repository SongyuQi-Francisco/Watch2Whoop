"""Metrics read from screenshots (table metric_snapshots): things third parties cannot read, like Apple's Readiness / sleep score and InBody segment data.
Comparison only, a separate table, never written to daily_metrics, so data from sensor sources (ow / apple_export / shortcut) is not overwritten."""
import re
from datetime import date

import psycopg

METRIC_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Units and sane ranges of common metrics (other metric names can be written too, just without range checks)
KNOWN: dict[str, tuple[str | None, float, float, str]] = {
    "apple_readiness": (None, 0, 100, "Apple 健康 / 手表的 Readiness（准备度）分"),
    "apple_sleep_score": (None, 0, 100, "Apple 的睡眠分（醒来那天）"),
    "apple_vitals_outliers": (None, 0, 10, "Apple Vitals 里超出平常范围的指标个数"),
    "inbody_score": (None, 0, 120, "InBody 分数"),
    "skeletal_muscle_kg": ("kg", 10, 80, "骨骼肌量"),
    "body_fat_mass_kg": ("kg", 1, 150, "体脂肪量"),
    "body_fat_pct": ("%", 3, 60, "体脂率"),
    "visceral_fat_level": (None, 1, 30, "内脏脂肪等级"),
    "waist_hip_ratio": (None, 0.5, 1.5, "腰臀比"),
    "body_water_l": ("L", 15, 80, "身体水分"),
    "bmr_kcal": ("kcal", 800, 4000, "基础代谢"),
}


def log_snapshot(conn: psycopg.Connection, user_id: int, day: date, source_app: str, metrics: list[dict],
                 note: str | None) -> dict:
    if not metrics:
        raise ValueError("metrics 不能为空")
    if not METRIC_RE.match(source_app or ""):
        raise ValueError("source_app 用小写英文，例 apple_health、inbody、whoop")
    out = {"created": [], "updated": [], "unchanged": []}
    for m in metrics:
        name, value, unit = m["metric"], m["value"], m.get("unit")
        if not METRIC_RE.match(name or ""):
            raise ValueError(f"指标名 {name!r} 用小写英文和下划线，例 apple_readiness、apple_sleep_score")
        if name in KNOWN:
            std, lo, hi, _ = KNOWN[name]
            if not lo <= value <= hi:
                raise ValueError(f"{name} = {value} 不在合理范围 {lo}–{hi}，核对截图")
            unit = unit or std
        cur = conn.execute("SELECT value, unit FROM metric_snapshots WHERE user_id = %s AND date = %s AND metric = %s "
                           "AND source_app = %s", (user_id, day, name, source_app)).fetchone()
        brief = {"date": day.isoformat(), "metric": name, "value": value, "unit": unit}
        if cur and cur["value"] == value and cur["unit"] == unit:
            out["unchanged"].append(brief)
            continue
        conn.execute(
            """INSERT INTO metric_snapshots (user_id, date, metric, value, unit, source, source_app, note)
               VALUES (%s, %s, %s, %s, %s, 'screenshot', %s, %s)
               ON CONFLICT (user_id, date, metric, source_app) DO UPDATE SET value = EXCLUDED.value,
                 unit = EXCLUDED.unit, note = EXCLUDED.note, updated_at = now()""",
            (user_id, day, name, value, unit, source_app, note))
        (out["updated"] if cur else out["created"]).append({**brief, **({"previous": cur["value"]} if cur else {})})
    sensor = conn.execute("""SELECT metric, value, unit, source FROM daily_metrics
                             WHERE user_id = %s AND date = %s AND metric = ANY(%s)""",
                          (user_id, day, [m["metric"] for m in metrics])).fetchall()
    if sensor:
        out["sensor_data_same_day"] = sensor  # Sensor data of the same name (untouched), for comparison
    return out


def list_snapshots(conn: psycopg.Connection, user_id: int, start: date, end: date,
                   metrics: list[str] | None = None) -> list[dict]:
    q = """SELECT date, metric, value, unit, source, source_app, note FROM metric_snapshots
           WHERE user_id = %s AND date BETWEEN %s AND %s"""
    params: list = [user_id, start, end]
    if metrics:
        q += " AND metric = ANY(%s)"
        params.append(metrics)
    return conn.execute(q + " ORDER BY date, metric", params).fetchall()
