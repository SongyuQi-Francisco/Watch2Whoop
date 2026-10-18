"""Import the full Apple Health export: reads processed/*.csv produced by health_sync/parse_export.py.

- daily_metrics.csv -> daily_metrics (source=apple_export). Data for the export day is incomplete and dropped.
- sleep_segments.csv -> sleep_sessions. Only the watch's stage data (Asleep*/Awake) is used; InBed does not count as sleep.
"""
import csv
from datetime import date, datetime
from pathlib import Path

import psycopg

from .common import Segment, normalize, replace_sleep, sessionize, snake, upsert_daily

SOURCE = "apple_export"
STAGE_MAP = {"AsleepCore": "core", "AsleepDeep": "deep", "AsleepREM": "rem",
             "AsleepUnspecified": "unspecified", "Asleep": "unspecified", "Awake": "awake"}


def _num(s: str) -> float | None:
    return float(s) if s not in ("", None) else None


def read_daily(path: Path, export_date: date) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            d = date.fromisoformat(r["date"])
            if d >= export_date:
                continue
            metric = snake(r["type"])
            value, unit = normalize(metric, float(r["value"]), r["unit"] or None)
            mn, mx = _num(r["min"]), _num(r["max"])
            if mn is not None:
                mn, _ = normalize(metric, mn, r["unit"] or None)
                mx, _ = normalize(metric, mx, r["unit"] or None)
            rows.append({"date": d, "metric": metric, "value": round(value, 4), "unit": unit,
                         "agg": r["agg"], "n": int(r["n"]), "min_value": mn, "max_value": mx})
    return rows


def read_segments(path: Path) -> list[Segment]:
    segs = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            stage = STAGE_MAP.get(r["stage"])
            if stage is None:
                continue
            segs.append(Segment(datetime.strptime(r["start"], "%Y-%m-%d %H:%M:%S %z"),
                                datetime.strptime(r["end"], "%Y-%m-%d %H:%M:%S %z"), stage, r["source"]))
    return segs


def run(conn: psycopg.Connection, user_id: int, processed_dir: Path, export_date: date) -> dict:
    daily = read_daily(processed_dir / "daily_metrics.csv", export_date)
    sessions = sessionize(read_segments(processed_dir / "sleep_segments.csv"))
    with conn.transaction():
        n_daily = upsert_daily(conn, user_id, SOURCE, daily)
        sleep = replace_sleep(conn, user_id, SOURCE, sessions)
        conn.execute("""INSERT INTO sync_state (user_id, source, last_synced_at, cursor) VALUES (%s, %s, now(), %s)
                        ON CONFLICT (user_id, source) DO UPDATE SET last_synced_at = now(), cursor = EXCLUDED.cursor""",
                     (user_id, SOURCE, export_date.isoformat()))
    return {"daily_rows": n_daily, "sleep": sleep}
