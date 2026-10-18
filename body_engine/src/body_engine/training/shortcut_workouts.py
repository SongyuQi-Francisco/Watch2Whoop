"""Workout rows uploaded by the Shortcut -> workouts (source=shortcut), deduplicated against the same workout from OW (OW first, importer.refresh_duplicates).

The format follows docs/shortcut-sync-spec.md (maintained by the Shortcut session); one row:
    Workout|start|end|type|duration_s|active_kcal|avg_hr|max_hr|source
- start / end: ISO 8601 with time zone (without a time zone, the user's time zone is assumed); rows where end is before start are dropped.
- type: HealthKit workout type name, English / Chinese both accepted (Traditional Strength Training / 传统力量训练 -> strength_training,
  mapping table params.SHORTCUT_TYPES, unrecognized ones kept as-is).
- duration_s, active_kcal and heart rate may be empty; thousands separators and unit suffixes in numbers ("3725 s", "512 kcal", "2,140 kJ", "1:02:05") are also accepted.
  If duration is empty, end − start is used. Average / max heart rate outside 25–230 is not used.
- Reads health_data/archive/health_*.txt (importers/shortcut.read_archive has merged multiple uploads: the same row counts once);
  external_id = start time (UTC)|type. The archive is cumulative and never deleted.
- The Shortcut cannot upload raw heart rate for the workout window: when OW has that heart rate, import-ow's enrich_pending backfills TRIMP, otherwise intensity is estimated from average heart rate.
"""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import psycopg

from . import params as P
from .importer import KJ_PER_KCAL, _base_row, enrich_pending, refresh_duplicates, type_name, upsert

_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _number(s: str | None) -> float | None:
    """The first number in the text. Thousands separators (2,092 / 1 234) are removed; a remaining comma is taken as a decimal point (some locales write 12,5)."""
    if not s:
        return None
    s = re.sub(r"(?<=\d)[,   ](?=\d{3}(?!\d))", "", s)
    m = _NUM.search(s)
    return float(m.group().replace(",", ".")) if m else None


def duration_min(s: str | None) -> float | None:
    """Duration -> minutes. The spec says seconds; also accepts "1:02:03" and "45:10" (h:m:s / m:s) and forms with min / 分钟 / h / 小时."""
    if not s or not s.strip():
        return None
    s = s.strip()
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", s):
        parts = [int(x) for x in s.split(":")]
        return (parts[-1] + parts[-2] * 60 + (parts[-3] * 3600 if len(parts) == 3 else 0)) / 60
    x = _number(s)
    if x is None:
        return None
    low = s.lower()
    if re.search(r"min|分钟", low):
        return x
    if re.search(r"\bh(r|rs|ours?)?\b|小时", low):
        return x * 60
    return x / 60          # spec: seconds


def energy_kcal(s: str | None) -> float | None:
    x = _number(s)
    if x is None:
        return None
    low = s.lower()
    return x / KJ_PER_KCAL if ("kj" in low or "千焦" in low) else x


def _hr(s: str | None) -> float | None:
    x = _number(s)
    return x if x is not None and 25 <= x <= 230 else None


def shortcut_type(s: str) -> str:
    key = re.sub(r"^hkworkoutactivitytype", "", (s or "").strip().lower())
    if key in P.SHORTCUT_TYPES:
        return P.SHORTCUT_TYPES[key]
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", re.sub(r"^HKWorkoutActivityType", "", (s or "").strip())).lower()
    return P.SHORTCUT_TYPES.get(spaced, key.replace(" ", "_") or "other")


def parse(p: list[str], tz: ZoneInfo) -> dict | None:
    """A row in the archive (already split on |) -> a row of workouts; not a Workout row or start / end unparsable -> None."""
    if not p or p[0].strip().lower() != "workout" or len(p) < 4:
        return None
    try:
        start, end = datetime.fromisoformat(p[1].strip()), datetime.fromisoformat(p[2].strip())
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=tz)
    if end < start:
        return None
    f = lambda i: p[i].strip() if len(p) > i and p[i].strip() else None
    t = shortcut_type(p[3])
    row = _base_row(tz, start, end)
    dur = duration_min(f(4))
    if dur and not row["excluded"] and 0 < dur <= row["duration_min"] + 1:
        row["duration_min"] = round(dur, 2)
    source = f(8)
    row.update(source="shortcut", external_id=f"{start.astimezone(timezone.utc).isoformat()}|{t}", origin=source,
               is_xml=False, device=source, type=t, name_zh=type_name(t), active_kcal=None, total_kcal=None,
               distance_km=None, avg_hr=_hr(f(6)), max_hr=_hr(f(7)), zone_offset=None)
    kcal = energy_kcal(f(5))
    if kcal is not None and 0 <= kcal < 5000:
        row["active_kcal"] = round(kcal, 1)
        row["flags"]["energy_from_shortcut"] = "活动能量来自快捷指令"
    return row


def run(conn: psycopg.Connection, user_id: int, archive_rows: list[list[str]], client=None) -> dict:
    """Workout rows in the output of importers/shortcut.read_archive -> workouts. Idempotent."""
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    rows = {}
    for p in archive_rows:
        r = parse(p, tz)
        if r is not None:
            rows[r["external_id"]] = r
    if not rows:
        return {"workouts": 0}
    with conn.transaction():
        res = upsert(conn, user_id, list(rows.values()))
        dups = refresh_duplicates(conn, user_id)
    n = 0
    if client is not None:
        with conn.transaction():
            n = enrich_pending(conn, client, user_id)
    return {"workouts": len(rows), "inserted": res["inserted"], "updated": res["updated"],
            "duplicates": dups["duplicates"], "enriched": n}
