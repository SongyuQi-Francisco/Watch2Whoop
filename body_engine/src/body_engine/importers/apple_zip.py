"""Import directly the export.zip from iPhone Health -> "Export All Health Data" (or the extracted export.xml).

Gives the same results as the two-step route of health_sync/parse_export.py + apple_export.py (same definitions for daily metrics); differences:
- Streaming: the XML in the zip is parsed while being decompressed (iterparse, each top-level element is cleared once handled), nothing is extracted to disk, memory depends only on the number of days.
  A 700MB export.xml (30MB zip) runs in a 512MB one-off container.
- Written in one pass: daily_metrics, sleep_sessions, workouts (source = apple_export; sleep and non-cumulative metrics rank below OW,
  cumulative ones are chosen among sources by importers/common.py resolve_daily), and the raw samples scoring needs go into export_samples (heart rate, HRV, respiration, SpO2, wrist temperature, sleep stages).
- The beat-to-beat series of each HRV reading -> heartbeat_series, electrocardiograms/*.csv -> ecg_recordings (rawdata/, migration 150).
- Data for the export day is incomplete (nothing after the export time), so that day's daily metrics are dropped.
- Idempotent: importing the same (or a newer) export repeatedly produces no duplicates.
On a Chinese-language system the exported files are called 「导出.zip」 ("export.zip") / apple_health_export/导出.xml ("export.xml"); found as "the largest non-cda .xml under apple_health_export/".
"""
import collections
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterator
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import psycopg

from .. import queries
from ..rawdata import export as rx
from ..rawdata import store as rx_store
from ..training import importer as wk
from ..training import shortcut_workouts as sw
from .apple_export import SOURCE, STAGE_MAP
from .common import Segment, normalize, replace_sleep, sessionize, snake, upsert_daily

HK = "HKQuantityTypeIdentifier"
# cumulative types: take the most complete single source per day (avoids counting iPhone + watch twice), same as parse_export.py
SUM_TYPES = {HK + t for t in ("StepCount", "ActiveEnergyBurned", "BasalEnergyBurned", "AppleExerciseTime",
                              "DistanceWalkingRunning", "FlightsClimbed", "AppleStandTime", "DistanceSwimming",
                              "SwimmingStrokeCount", "DietaryWater", "DietaryEnergyConsumed", "DietaryProtein",
                              "TimeInDaylight", "PhysicalEffort", "DistanceCycling")}
RAW_KINDS = {HK + "HeartRate": "hr", HK + "RespiratoryRate": "resp", HK + "OxygenSaturation": "spo2",
             HK + "AppleSleepingWristTemperature": "temp",
             HK + "HeartRateVariabilitySDNN": "hrv"}   # export_samples hrv is always SDNN (RMSSD: heartbeat_series)
SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
BATCH = 5000
TS_FORMAT = "%Y-%m-%d %H:%M:%S %z"


class ExportError(ValueError):
    """The file is not an Apple Health export (a Chinese message shown to the user)."""


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, TS_FORMAT)
    except ValueError:
        return None


def find_xml_member(zf: zipfile.ZipFile) -> zipfile.ZipInfo:
    cands = [i for i in zf.infolist() if i.filename.lower().endswith(".xml") and "cda" not in i.filename.lower()
             and not i.is_dir() and i.filename.count("/") <= 1]
    if not cands:
        raise ExportError("压缩包里没有 export.xml：请上传「健康」app 导出的 export.zip（导出.zip）原文件")
    return max(cands, key=lambda i: i.file_size)


class _Counting(io.RawIOBase):
    """Wrapper that counts bytes read (for progress)."""

    def __init__(self, f):
        self.f, self.n = f, 0

    def readable(self):
        return True

    def readinto(self, b):
        data = self.f.read(len(b))
        n = len(data)
        b[:n] = data
        self.n += n
        return n


@dataclass
class Parsed:
    export_at: datetime | None = None
    daily: dict = field(default_factory=lambda: collections.defaultdict(lambda: [0.0, 0, float("inf"), float("-inf")]))
    srcsum: dict = field(default_factory=lambda: collections.defaultdict(float))
    units: dict = field(default_factory=dict)
    segments: list = field(default_factory=list)
    workouts: list = field(default_factory=list)
    samples: int = 0
    records: int = 0
    heartbeats: int = 0


def iter_elements(stream) -> Iterator[ET.Element]:
    """Top-level elements of export.xml (Record, Workout, ExportDate, Me …), cleared once handled, so memory does not grow with the file."""
    depth, root = 0, None
    try:
        for ev, el in ET.iterparse(stream, events=("start", "end")):
            if ev == "start":
                depth += 1
                if root is None:
                    root = el
                    if el.tag != "HealthData":
                        raise ExportError("这不是 Apple 健康的导出文件（根元素不是 HealthData）")
                continue
            depth -= 1
            if depth == 1:
                yield el
                root.clear()
    except ET.ParseError as e:
        raise ExportError(f"export.xml 解析失败（文件不完整或损坏：{e}）；请重新导出再上传") from e
    if root is None:
        raise ExportError("export.xml 是空的")


def parse_workout(el: ET.Element, tz: ZoneInfo) -> dict | None:
    start, end = _ts(el.get("startDate")), _ts(el.get("endDate"))
    if start is None or end is None or end < start:
        return None
    t = sw.shortcut_type(el.get("workoutActivityType") or "")
    stats = {s.get("type"): s for s in el.findall("WorkoutStatistics")}
    row = wk._base_row(tz, start, end)
    dur = el.get("duration")
    if dur and not row["excluded"]:
        try:
            m = float(dur) * {"min": 1, "s": 1 / 60, "hr": 60}.get(el.get("durationUnit") or "min", 1)
            if 0 < m <= row["duration_min"] + 1:
                row["duration_min"] = round(m, 2)
        except ValueError:
            pass

    def stat(type_, attr="sum"):
        s = stats.get(HK + type_)
        return (float(s.get(attr)), s.get("unit")) if s is not None and s.get(attr) else (None, None)

    kcal, unit = stat("ActiveEnergyBurned")
    if kcal is not None and unit == "kJ":
        kcal /= wk.KJ_PER_KCAL
    dist, dunit = stat("DistanceWalkingRunning")
    if dist is None:
        dist, dunit = stat("DistanceSwimming")
    if dist is None:
        dist, dunit = stat("DistanceCycling")
    if dist is not None:
        dist = dist * {"km": 1, "m": 0.001, "mi": 1.609344, "yd": 0.0009144}.get(dunit or "km", 1)
    avg_hr, _ = stat("HeartRate", "average")
    max_hr, _ = stat("HeartRate", "maximum")
    source = el.get("sourceName")
    row.update(source=SOURCE, external_id=f"{start.astimezone(ZoneInfo('UTC')).isoformat()}|{t}", origin="export.xml",
               is_xml=True, device=source, type=t, name_zh=wk.type_name(t),
               active_kcal=None if kcal is None else round(kcal, 1), total_kcal=None,
               distance_km=None if dist is None else round(dist, 3),
               avg_hr=avg_hr if avg_hr and 25 <= avg_hr <= 230 else None,
               max_hr=max_hr if max_hr and 25 <= max_hr <= 230 else None, zone_offset=None)
    return row


def parse(stream, tz: ZoneInfo, on_samples: Callable[[list[tuple]], None],
          on_progress: Callable[[], None] | None = None,
          on_heartbeats: Callable[[list[dict]], None] | None = None) -> Parsed:
    """Streaming parse; raw samples are handed to on_samples every BATCH items (kind, start, end, value, stage, source),
    the beat-to-beat series of HRV readings to on_heartbeats (rawdata/export.py items)."""
    p = Parsed()
    batch: list[tuple] = []
    beats: list[dict] = []

    def flush():
        if batch:
            on_samples(list(batch))
            p.samples += len(batch)
            batch.clear()
        if beats and on_heartbeats:
            on_heartbeats(list(beats))
            p.heartbeats += len(beats)
            beats.clear()

    for el in iter_elements(stream):
        tag = el.tag
        if tag == "Record":
            p.records += 1
            t = el.get("type")
            if on_heartbeats and t == rx.HRV_TYPE and (hb := rx.heartbeat_from_record(el)):
                beats.append(hb)
            if t == SLEEP_TYPE:
                stage = STAGE_MAP.get((el.get("value") or "").replace("HKCategoryValueSleepAnalysis", ""))
                start, end = _ts(el.get("startDate")), _ts(el.get("endDate"))
                if stage and start and end and end >= start:
                    src = el.get("sourceName") or ""
                    p.segments.append(Segment(start, end, stage, src))
                    batch.append(("sleep", start, end, None, stage, src))
            else:
                try:
                    v = float(el.get("value"))
                except (TypeError, ValueError):
                    continue
                day = (el.get("startDate") or "")[:10]
                if len(day) != 10:
                    continue
                d = p.daily[(t, day)]
                d[0] += v
                d[1] += 1
                d[2] = min(d[2], v)
                d[3] = max(d[3], v)
                if t in SUM_TYPES:
                    p.srcsum[(t, day, el.get("sourceName"))] += v
                p.units[t] = el.get("unit")
                kind = RAW_KINDS.get(t)
                if kind:
                    start, end = _ts(el.get("startDate")), _ts(el.get("endDate"))
                    if start and end and end >= start:
                        if kind == "spo2" and v <= 1.0:
                            v *= 100
                        batch.append((kind, start, end, v, None, el.get("sourceName") or ""))
            if len(batch) >= BATCH or len(beats) >= BATCH:
                flush()
                if on_progress:
                    on_progress()
        elif tag == "Workout":
            row = parse_workout(el, tz)
            if row:
                p.workouts.append(row)
        elif tag == "ExportDate":
            p.export_at = _ts(el.get("value"))
    flush()
    return p


def daily_rows(p: Parsed, export_date: date | None) -> list[dict]:
    """Same definitions as parse_export.py -> apple_export.read_daily."""
    best: dict = {}
    for (t, day, _src), v in p.srcsum.items():
        best[(t, day)] = max(best.get((t, day), 0), v)
    rows = []
    for (t, day), (s, n, mn, mx) in p.daily.items():
        try:
            d = date.fromisoformat(day)
        except ValueError:
            continue
        if export_date and d >= export_date:
            continue
        metric = snake(t)
        unit = p.units.get(t)
        if t in SUM_TYPES:
            value, unit2 = normalize(metric, best.get((t, day), s), unit)
            rows.append({"date": d, "metric": metric, "value": round(value, 4), "unit": unit2, "agg": "sum", "n": n,
                         "min_value": None, "max_value": None})
        else:
            value, unit2 = normalize(metric, s / n, unit)
            lo, _ = normalize(metric, mn, unit)
            hi, _ = normalize(metric, mx, unit)
            rows.append({"date": d, "metric": metric, "value": round(value, 4), "unit": unit2, "agg": "avg", "n": n,
                         "min_value": round(lo, 4), "max_value": round(hi, 4)})
    return rows


def save_samples(conn: psycopg.Connection, user_id: int, import_id: int | None, rows: list[tuple]) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO export_samples (user_id, kind, start_at, end_at, value, stage, source, import_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (user_id, kind, start_at, source) DO UPDATE SET
                 end_at = EXCLUDED.end_at, value = EXCLUDED.value, stage = EXCLUDED.stage, import_id = EXCLUDED.import_id""",
            [(user_id, k, s, e, v, st, src, import_id) for k, s, e, v, st, src in rows])


def open_export(path: Path):
    """-> (readable XML byte stream, size of the XML after decompression, close function). Either zip or xml works."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"PK"):
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile as e:
            raise ExportError("压缩包损坏（可能没传完整）；请重新导出再上传") from e
        info = find_xml_member(zf)
        raw = zf.open(info)
        return raw, info.file_size, lambda: (raw.close(), zf.close())
    if head.lstrip().startswith(b"<?xml") or head.lstrip().startswith(b"<"):
        f = open(path, "rb")
        return f, path.stat().st_size, f.close
    raise ExportError("文件格式不对：请上传「健康」app 导出的 export.zip（导出.zip）")


def run(conn: psycopg.Connection, user_id: int, path: Path, import_id: int | None = None,
        progress: Callable[[float], None] | None = None) -> dict:
    """Import one export.zip / export.xml; returns a summary. progress(0–1) is called now and then during the parsing phase."""
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    raw, xml_size, close = open_export(path)
    counting = _Counting(raw)
    try:
        p = parse(io.BufferedReader(counting, 1 << 20), tz, lambda rows: save_samples(conn, user_id, import_id, rows),
                  (lambda: progress(min(counting.n / max(xml_size, 1), 1.0))) if progress else None,
                  lambda items: rx_store.save_heartbeats(conn, user_id, items, import_id))
    finally:
        close()
    ecg = rx.import_ecgs(conn, user_id, path, import_id)
    if p.records == 0:
        raise ExportError("导出文件里没有健康数据")
    export_date = p.export_at.astimezone(tz).date() if p.export_at else None
    daily = daily_rows(p, export_date)
    sessions = sessionize(p.segments)
    with conn.transaction():
        n_daily = upsert_daily(conn, user_id, SOURCE, daily)
        sleep = replace_sleep(conn, user_id, SOURCE, sessions)
        res = wk.upsert(conn, user_id, p.workouts)
        dups = wk.refresh_duplicates(conn, user_id)
        conn.execute("""INSERT INTO sync_state (user_id, source, last_synced_at, cursor) VALUES (%s, %s, now(), %s)
                        ON CONFLICT (user_id, source) DO UPDATE SET last_synced_at = now(), cursor = EXCLUDED.cursor""",
                     (user_id, SOURCE, (export_date or queries.local_today(conn, user_id)).isoformat()))
    days = sorted({r["date"] for r in daily})
    return {"records": p.records, "daily_rows": n_daily, "days": len(days),
            "first_day": days[0].isoformat() if days else None, "last_day": days[-1].isoformat() if days else None,
            "nights": len(sessions), "sleep": sleep, "workouts": len(p.workouts), "workouts_inserted": res["inserted"],
            "workout_duplicates": dups["duplicates"], "samples": p.samples, "heartbeat_series": p.heartbeats,
            "ecg": ecg["ecg"],
            "export_date": export_date.isoformat() if export_date else None}


def first_bytes_ok(head: bytes) -> bool:
    return head.startswith(b"PK") or bool(re.match(rb"\s*<", head))
