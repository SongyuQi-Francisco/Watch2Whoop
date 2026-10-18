"""Pull data from Open Wearables (OW): daily metrics and sleep, source=ow (highest priority for sleep and non-cumulative metrics;
cumulative ones compete with apple_export / shortcut on completeness and value, importers/common.py resolve_daily).

- Daily metrics: raw samples pulled in time-window batches (/timeseries, resolution=raw), cut into days by the user's time zone.
  - Cumulative types (steps, energy, distance ...): the iPhone and the watch often record the same walk at once, so adding them double counts,
    and taking the "largest single source" would miss stretches recorded by only one device. Here we dedupe by HealthKit's rule:
    source priority watch > phone > other, overlapping time counts only the higher-priority one, and lower-priority samples are counted in proportion to the uncovered time.
    OW stores only a sample's start time, so the end time is inferred as "the start of the next sample from the same source, at most 10 minutes"
    (calibrated with real start/end times from export.xml: 396 two-device days in the past year, median error 4.8%, P90 10.5%, about 5% low on average).
  - Others (heart rate, HRV, SpO2 ...): mean, min and max of all samples that day.
- The same data can exist twice in OW: the export.xml import and the iPhone app backfill (OW treats them as two sources).
  Live sources win: an XML sample of the same type and timestamp is dropped; in cumulative dedup, live sources of the same device type rank ahead of XML;
  when sleep sessions overlap, only the higher-priority one is kept.
- Sleep: pull OW's sleep stages (/events/sleep?include=stages), first convert to the user's time zone (OW's returned offsets are unreliable:
  the app syncs UTC, and XML imports are all written with the +11:00 in effect at import time), then re-segment with the same rule as other sources (common.sessionize).
- export.xml is a snapshot of the moment of export, so the export day is incomplete: the last day with only XML data is not written (same as apple_export).
- Incremental: sync_state(source='ow').cursor records the time of the last sample; the next run recomputes whole days from LOOKBACK days before it (to catch late data).
- Workout records (/events/workouts → workouts table) are pulled over the same range: training/importer.py.
"""
import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from ..ow_client import OWClient
from .common import Segment, replace_sleep, sessionize, upsert_daily

SOURCE = "ow"
WINDOW = timedelta(days=30)          # pull 30 days of samples per batch, to bound memory
LOOKBACK = timedelta(days=2)         # days recomputed backwards on incremental sync
CUMULATIVE_CAP = timedelta(minutes=10)
DEVICE_PRIORITY = {"watch": 0, "band": 0, "ring": 0, "phone": 1}   # other (no device, third-party app) = 2
STAGE_MAP = {"light": "core", "deep": "deep", "rem": "rem", "awake": "awake", "sleeping": "unspecified"}
XML_SOURCE = "apple_health_xml"


@dataclass(frozen=True)
class Spec:
    metric: str          # Body Engine metric name (consistent with apple_export)
    unit: str
    agg: str             # sum / avg
    factor: float = 1.0  # conversion factor from the OW-labelled unit -> unit (OW's XML import does not convert units, see _value)


# OW series type -> Body Engine metric. Types not listed use OW's name and unit, aggregated by avg.
SPECS = {
    "steps": Spec("step_count", "count", "sum"),
    "active_energy": Spec("active_energy_burned", "kcal", "sum"),
    "basal_energy": Spec("basal_energy_burned", "kcal", "sum"),
    "distance_walking_running": Spec("distance_walking_running", "km", "sum", 0.001),
    "distance_cycling": Spec("distance_cycling", "km", "sum", 0.001),
    "distance_swimming": Spec("distance_swimming", "m", "sum"),
    "exercise_time": Spec("apple_exercise_time", "min", "sum"),
    "stand_time": Spec("apple_stand_time", "min", "sum"),
    "flights_climbed": Spec("flights_climbed", "count", "sum"),
    "time_in_daylight": Spec("time_in_daylight", "min", "sum"),
    "swimming_stroke_count": Spec("swimming_stroke_count", "count", "sum"),
    "heart_rate": Spec("heart_rate", "count/min", "avg"),
    "resting_heart_rate": Spec("resting_heart_rate", "count/min", "avg"),
    "walking_heart_rate_average": Spec("walking_heart_rate_average", "count/min", "avg"),
    "heart_rate_variability_sdnn": Spec("heart_rate_variability_sdnn", "ms", "avg"),
    "heart_rate_variability_rmssd": Spec("heart_rate_variability_rmssd", "ms", "avg"),
    "oxygen_saturation": Spec("oxygen_saturation", "%", "avg"),
    "respiratory_rate": Spec("respiratory_rate", "count/min", "avg"),
    "vo2_max": Spec("vo2_max", "mL/min·kg", "avg"),
    "weight": Spec("body_mass", "kg", "avg"),
    "height": Spec("height", "cm", "avg"),
    "body_mass_index": Spec("body_mass_index", "count", "avg"),
    # OW maps Apple's sleeping wrist temperature to body_temperature
    "body_temperature": Spec("apple_sleeping_wrist_temperature", "degC", "avg"),
    "physical_effort": Spec("physical_effort", "kcal/hr·kg", "avg"),
    "walking_speed": Spec("walking_speed", "km/hr", "avg", 3.6),
    "walking_step_length": Spec("walking_step_length", "cm", "avg"),
    "walking_steadiness": Spec("apple_walking_steadiness", "%", "avg"),
    "walking_double_support_percentage": Spec("walking_double_support_percentage", "%", "avg"),
    "walking_asymmetry_percentage": Spec("walking_asymmetry_percentage", "%", "avg"),
    "stair_ascent_speed": Spec("stair_ascent_speed", "m/s", "avg"),
    "stair_descent_speed": Spec("stair_descent_speed", "m/s", "avg"),
    "six_minute_walk_test_distance": Spec("six_minute_walk_test_distance", "m", "avg"),
    "running_speed": Spec("running_speed", "km/hr", "avg", 3.6),
    "running_stride_length": Spec("running_stride_length", "m", "avg", 0.01),
    "running_power": Spec("running_power", "W", "avg"),
    "running_vertical_oscillation": Spec("running_vertical_oscillation", "cm", "avg"),
    "running_ground_contact_time": Spec("running_ground_contact_time", "ms", "avg"),
    "headphone_audio_exposure": Spec("headphone_audio_exposure", "dBASPL", "avg"),
    "environmental_audio_exposure": Spec("environmental_audio_exposure", "dBASPL", "avg"),
    "environmental_sound_reduction": Spec("environmental_sound_reduction", "dBASPL", "avg"),
    "water_temperature": Spec("water_temperature", "degC", "avg"),
}
# Ratio types: apple_export stores SpO2 as 0–100 and the rest as 0–1; kept consistent here
PERCENT_100 = {"oxygen_saturation"}
# export.xml's energy unit follows the phone's region setting (kJ by default in Australia); OW's XML import copies the number but labels it kcal
ENERGY = {"active_energy", "basal_energy"}
KJ_PER_KCAL = 4.184
FRACTION = {"walking_steadiness", "walking_double_support_percentage", "walking_asymmetry_percentage"}


@dataclass
class Sample:
    ts: datetime         # time-zone aware
    value: float
    source: str          # source key used for dedup: device type + model
    priority: int        # smaller = higher priority: device type priority × 2, +1 more for XML copies
    xml: bool = False    # from the export.xml import (not the iPhone app's live sync)


def spec_for(code: str, unit: str | None) -> Spec:
    return SPECS.get(code) or Spec(code, unit or "", "avg")


def _value(code: str, spec: Spec, value: float, source: str | None, xml_energy_unit: str = "kJ") -> float:
    """OW's XML import stores the numbers in export.xml as-is without conversion (labelled meters but actually km),
    so XML-sourced values are already in the units apple_export uses (except energy, converted to kcal per xml_energy_unit);
    other sources (SDK) are converted from OW's labelled units."""
    if source != XML_SOURCE:
        value *= spec.factor
    elif code in ENERGY and xml_energy_unit == "kJ":
        value /= KJ_PER_KCAL
    if code in PERCENT_100 and value <= 1.0:
        value *= 100
    elif code in FRACTION and value > 1.0:
        value /= 100
    return value


def parse_sample(raw: dict, xml_energy_unit: str = "kJ") -> tuple[str, str | None, Sample]:
    src = raw.get("source") or {}
    device_type = src.get("device_type") or "unknown"
    xml = src.get("source") == XML_SOURCE
    spec = spec_for(raw["type"], raw.get("unit"))
    value = _value(raw["type"], spec, float(raw["value"]), src.get("source"), xml_energy_unit)
    sample = Sample(datetime.fromisoformat(raw["timestamp"]), value,
                    f"{device_type}:{src.get('device') or ''}:{src.get('source') or ''}",
                    DEVICE_PRIORITY.get(device_type, 2) * 2 + xml, xml)
    return raw["type"], raw.get("unit"), sample


def drop_xml_duplicates(samples: list[Sample]) -> list[Sample]:
    """Samples backfilled by the iPhone app and those imported from export.xml are the same data: where a live sample exists at the same timestamp, the XML one is dropped."""
    live = {s.ts for s in samples if not s.xml}
    return [s for s in samples if not (s.xml and s.ts in live)]


def dedupe_cumulative(samples: list[Sample], tz: ZoneInfo, cap: timedelta = CUMULATIVE_CAP) -> dict[date, float]:
    """Cumulative metrics are summed per day; in overlapping time from multiple sources only the higher-priority source counts (HealthKit's rule)."""
    by_source = defaultdict(list)
    for s in samples:
        by_source[s.source].append(s)
    # infer each sample's end time: the start of the next one from the same source, at most cap
    intervals = []   # (priority, start, end, value)
    for xs in by_source.values():
        xs.sort(key=lambda s: s.ts)
        for i, s in enumerate(xs):
            end = s.ts + cap
            if i + 1 < len(xs):
                end = min(end, xs[i + 1].ts)
            intervals.append((s.priority, s.ts.timestamp(), end.timestamp(), s))
    totals = defaultdict(float)
    starts, ends = [], []      # stretches already taken by higher priority (merged, sorted by start)
    for prio in sorted({iv[0] for iv in intervals}):
        level = [iv for iv in intervals if iv[0] == prio]
        for _, a, b, s in level:
            totals[s.ts.astimezone(tz).date()] += s.value * _uncovered(starts, ends, a, b)
        merged = sorted([(a, b) for a, b in zip(starts, ends)] + [(a, b) for _, a, b, _ in level])
        starts, ends = [], []
        for a, b in merged:
            if starts and a <= ends[-1]:
                ends[-1] = max(ends[-1], b)
            else:
                starts.append(a)
                ends.append(b)
    return dict(totals)


def _uncovered(starts: list[float], ends: list[float], a: float, b: float) -> float:
    """Fraction of [a, b] not covered by occupied stretches; for a zero-length sample, whether that point in time is covered."""
    i = max(bisect.bisect_right(starts, a) - 1, 0)
    if b <= a:
        return 0.0 if i < len(starts) and starts[i] <= a <= ends[i] else 1.0
    covered = 0.0
    while i < len(starts) and starts[i] < b:
        covered += max(0.0, min(ends[i], b) - max(starts[i], a))
        i += 1
    return max(0.0, 1 - covered / (b - a))


def daily_rows(samples_by_type: dict[str, tuple[str | None, list[Sample]]], tz: ZoneInfo) -> list[dict]:
    rows = []
    for code, (unit, samples) in samples_by_type.items():
        spec = spec_for(code, unit)
        by_day = defaultdict(list)
        for s in samples:
            by_day[s.ts.astimezone(tz).date()].append(s.value)
        sums = dedupe_cumulative(samples, tz) if spec.agg == "sum" else {}
        for d, values in by_day.items():
            if spec.agg == "sum":
                value, mn, mx = sums.get(d, 0.0), None, None
            else:
                value, mn, mx = sum(values) / len(values), min(values), max(values)
            rows.append({"date": d, "metric": spec.metric, "value": round(value, 4), "unit": spec.unit,
                         "agg": spec.agg, "n": len(values), "min_value": mn, "max_value": mx})
    return rows


def _session_priority(sess: dict) -> int:
    """XML-imported sleep has no source (provider=unknown), ranked behind app-synced sleep."""
    src = sess.get("source") or {}
    return DEVICE_PRIORITY.get(src.get("device_type") or "", 2) * 2 + (0 if src.get("source") else 1)


def _local(ts: str, tz: ZoneInfo) -> datetime:
    """Convert to the user's time zone, then pin to the UTC offset of that moment: subtracting two times of the same ZoneInfo is done in wall-clock time,
    so a segment crossing a DST change is one hour longer / shorter; subtracting fixed offsets gives the real duration."""
    local = datetime.fromisoformat(ts).astimezone(tz)
    return local.astimezone(timezone(local.utcoffset()))


def sleep_segments(sessions: list[dict], tz: ZoneInfo) -> list[Segment]:
    """OW sleep session -> stage segments (user's time zone). A session overlapping a higher-priority one is dropped entirely (the XML copy of the same night)."""
    def stages(sess):
        out = []
        for st in sess.get("sleep_stage_intervals") or []:
            stage = STAGE_MAP.get(st["stage"])   # in_bed / unknown do not count as sleep
            if stage:
                out.append((stage, _local(st["start_time"], tz), _local(st["end_time"], tz)))
        return out

    kept, segs = [], []
    for sess in sorted(sessions, key=_session_priority):
        parts = stages(sess)
        if not parts:
            continue
        lo, hi = min(p[1] for p in parts), max(p[2] for p in parts)
        if any(lo < k_hi and k_lo < hi for k_lo, k_hi in kept):
            continue
        kept.append((lo, hi))
        device = (sess.get("source") or {}).get("device_name")
        segs += [Segment(a, b, stage, device) for stage, a, b in parts]
    return segs


def link_user(conn: psycopg.Connection, client: OWClient, user_id: int, ow_user_id: str | None = None) -> str:
    """users.ow_user_id <- the OW user UUID. If already linked, return directly; if ow_user_id is not given, create a new one in OW."""
    row = conn.execute("SELECT display_name, ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None:
        raise ValueError(f"没有用户 {user_id}")
    if row["ow_user_id"] and (ow_user_id is None or ow_user_id == row["ow_user_id"]):
        return row["ow_user_id"]
    if ow_user_id is None:
        ow_user_id = client.create_user(row["display_name"])["id"]
    else:
        client.get_user(ow_user_id)  # confirm it exists
    conn.execute("UPDATE users SET ow_user_id = %s WHERE id = %s", (ow_user_id, user_id))
    return ow_user_id


def _first_sample_time(client: OWClient, ow_user_id: str) -> datetime | None:
    page = client.get(f"/users/{ow_user_id}/timeseries", start_time="2000-01-01T00:00:00Z",
                      end_time=datetime.now().astimezone().isoformat(), resolution="raw", limit=1)
    return datetime.fromisoformat(page["data"][0]["timestamp"]) if page["data"] else None


def run(conn: psycopg.Connection, client: OWClient, user_id: int, start: date | None = None,
        end: date | None = None, full: bool = False, xml_energy_unit: str = "kJ") -> dict:
    """Pull the data of the days [start, end] (user's local dates), recompute whole days, then write to the database.
    Without start: full=True starts from the earliest data in OW, otherwise from LOOKBACK days before the last sync cursor."""
    user = conn.execute("SELECT ow_user_id, timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None or not user["ow_user_id"]:
        raise ValueError(f"用户 {user_id} 还没对应 OW 的 user：先运行 body-engine ow-link --user-id {user_id}")
    ow_user, tz = user["ow_user_id"], ZoneInfo(user["timezone"])
    state = conn.execute("SELECT cursor FROM sync_state WHERE user_id = %s AND source = %s",
                         (user_id, SOURCE)).fetchone()
    if start is None:
        if full or not (state and state["cursor"]):
            first = _first_sample_time(client, ow_user)
            if first is None:
                return {"daily_rows": 0, "sleep": {"inserted": 0, "skipped": 0}, "samples": 0}
            start = first.astimezone(tz).date()
        else:
            start = (datetime.fromisoformat(state["cursor"]).astimezone(tz) - LOOKBACK).date()
    end = end or datetime.now(tz).date()

    n_samples = n_rows = 0
    last_ts = datetime.fromisoformat(state["cursor"]) if state and state["cursor"] else None
    held: tuple[date, list[dict]] | None = None   # the held-back "last day with only XML data"
    lo = datetime.combine(start, time.min, tz)
    stop = datetime.combine(end + timedelta(days=1), time.min, tz)
    while lo < stop:
        hi = min(lo + WINDOW, stop)
        by_type: dict[str, tuple[str | None, list[Sample]]] = {}
        live_days, xml_days = set(), set()
        for raw in client.paginate(f"/users/{ow_user}/timeseries", start_time=lo.isoformat(),
                                   end_time=hi.isoformat(), resolution="raw", limit=1000):
            if raw.get("is_daily_total"):
                continue   # whole-day summary given by the device; counting it with the details would double count
            code, unit, s = parse_sample(raw, xml_energy_unit)
            by_type.setdefault(code, (unit, []))[1].append(s)
            (xml_days if s.xml else live_days).add(s.ts.astimezone(tz).date())
            last_ts = max(last_ts, s.ts) if last_ts else s.ts
            n_samples += 1
        by_type = {code: (unit, drop_xml_duplicates(samples)) for code, (unit, samples) in by_type.items()}
        rows = [r for r in daily_rows(by_type, tz) if start <= r["date"] <= end]
        if xml_days:
            # this batch has later XML data, so the day held back earlier is not the export day; write it as usual
            if held:
                rows += held[1]
            last_xml = max(xml_days)
            held = None
            if last_xml not in live_days:
                held = (last_xml, [r for r in rows if r["date"] == last_xml])
                rows = [r for r in rows if r["date"] != last_xml]
        with conn.transaction():
            n_rows += upsert_daily(conn, user_id, SOURCE, rows)
        lo = hi

    # sleep: pull one extra day so that when OW splits a night into two segments we do not get only the second half; keep only those waking after start
    sessions = list(client.paginate(f"/users/{ow_user}/events/sleep", include="stages", limit=100,
                                    start_date=(start - timedelta(days=1)).isoformat(),
                                    end_date=(end + timedelta(days=1)).isoformat()))
    nights = [s for s in sessionize(sleep_segments(sessions, tz)) if start <= s.wake_date <= end]
    with conn.transaction():
        sleep = replace_sleep(conn, user_id, SOURCE, nights)
        conn.execute("""INSERT INTO sync_state (user_id, source, last_synced_at, cursor) VALUES (%s, %s, now(), %s)
                        ON CONFLICT (user_id, source) DO UPDATE SET last_synced_at = now(), cursor = EXCLUDED.cursor""",
                     (user_id, SOURCE, last_ts.isoformat() if last_ts else None))
    from ..training import importer as workouts   # workout records: same date range (from the beginning when full)
    return {"start": start, "end": end, "samples": n_samples, "daily_rows": n_rows, "sleep": sleep,
            "skipped_export_day": held[0] if held else None,
            "workouts": workouts.import_ow(conn, client, user_id, start, end, xml_energy_unit)}
