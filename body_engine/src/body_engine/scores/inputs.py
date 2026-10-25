"""Inputs for scoring: sleep segments (Body Engine database), sleep-stage detail and raw samples (Open Wearables + Shortcut archive).

- Sleep segments follow sleep_sessions (the importers already split them by source priority); only watch sources are used:
  a segment counts if it has stage data (core / deep / rem) or the device name contains Watch (OW's XML import does not store the sleep source, so this is the only way to tell;
  segments with only "asleep" and no stages come from the iPhone / third-party apps and are ignored).
- Raw samples: heart rate, HRV, respiratory rate, SpO2, wrist temperature. In the sync mode `app` (rawdata/engine.mode,
  step S5) the app's own sync engine first (hk_samples, rawdata/samples.py: the same series, and its sleep stages; OW's
  nights only where the app has none), then OW. Otherwise OW first; the Shortcut fills days OW lacks: read from shortcut_samples
  (written by import-shortcut; hour-summed heart rate agg = hour is unused, only ungrouped raw readings); if this user has no rows in the table yet, fall back to reading the archive.
  Only one sample per (type + start time) is kept, non-XML preferred.
- The export.zip uploaded by the user (history/): raw samples and sleep stages in export_samples, treated as an XML source.
- The types the app reads from HealthKit itself (hk_samples, rawdata/hk_types.py; sleeping wrist temperature): a live source.
- HRV: hrv_kind = 'sdnn' (Apple's SDNN from OW / Shortcut / export_samples) or 'rmssd' (computed from Apple's beat-to-beat
  series in heartbeat_series, rawdata/hrv.py; the app copy wins over the export.xml copy). The two are never mixed.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from ..importers import ow as ow_imp, ow_cache as ow_cache_mod
from ..importers.common import WAKE_DAY_SHIFT as WAKE_SHIFT, Segment
from ..importers import shortcut as sc_imp
from ..rawdata import samples as rx_samples, store as rx_store
from . import params as P

Samples = list[tuple[datetime, float]]

# OW series type -> field in the inputs (HRV only for sdnn: RMSSD comes from heartbeat_series, see rmssd_hrv)
OW_BASE = {"heart_rate": "hr", "respiratory_rate": "resp", "oxygen_saturation": "spo2", "body_temperature": "temp"}
# Shortcut type -> field (heart rate uses only ungrouped raw readings, see load_shortcut_db)
SHORTCUT_BASE = {"Respiratory Rate": "resp", "Oxygen Saturation": "spo2", "Wrist Temperature": "temp",
                 "Sleep Wrist Temperature": "temp"}


def ow_types(hrv_kind: str) -> dict:
    return {**OW_BASE, "heart_rate_variability_sdnn": "hrv"} if hrv_kind == "sdnn" else dict(OW_BASE)


def shortcut_types(hrv_kind: str) -> dict:
    return {**SHORTCUT_BASE, "Heart Rate Variability": "hrv"} if hrv_kind == "sdnn" else dict(SHORTCUT_BASE)


OW_TYPES = ow_types(P.HRV_KIND)
SHORTCUT_TYPES = shortcut_types(P.HRV_KIND)
WINDOW = timedelta(days=60)   # each OW pull covers 60 days


@dataclass
class Night:
    wake_date: date
    start: datetime
    end: datetime
    asleep_min: float
    deep_min: float
    rem_min: float
    awake_min: float
    source: str


@dataclass
class Inputs:
    tz: ZoneInfo
    birth_date: date | None
    hrv_kind: str = P.HRV_KIND
    nights: list[Night] = field(default_factory=list)             # watch sleep segments (main sleep + naps)
    non_watch_dates: set[date] = field(default_factory=set)       # wake dates that have only non-watch sleep
    segments: list[tuple[datetime, datetime, str]] = field(default_factory=list)   # sleep-stage detail
    hr: Samples = field(default_factory=list)
    hrv: Samples = field(default_factory=list)
    resp: Samples = field(default_factory=list)
    spo2: Samples = field(default_factory=list)
    temp: Samples = field(default_factory=list)
    daily_rhr: dict[date, float] = field(default_factory=dict)    # daily_metrics.resting_heart_rate (fallback)
    shortcut_dates: set[date] = field(default_factory=set)        # days with Shortcut data (used to explain why data is missing)
    muscle: dict[date, dict] = field(default_factory=dict)        # muscle load computed from SparkyFitness workout records (muscle.py)
    muscle_error: str | None = None                               # connected to SparkyFitness but the workout records cannot be read


def is_watch_sleep(row: dict) -> bool:
    if row.get("device") and "watch" in row["device"].lower():
        return True
    return (row["core_min"] or 0) + (row["deep_min"] or 0) + (row["rem_min"] or 0) > 0


def load_db(conn: psycopg.Connection, user_id: int, start: date, end: date) -> Inputs:
    user = conn.execute("SELECT timezone, birth_date FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None:
        raise ValueError(f"没有用户 {user_id}")
    inp = Inputs(ZoneInfo(user["timezone"]), user["birth_date"])
    for r in conn.execute("""SELECT wake_date, start_at, end_at, asleep_min, core_min, deep_min, rem_min, awake_min,
                                    source, device FROM sleep_sessions
                             WHERE user_id = %s AND wake_date BETWEEN %s AND %s ORDER BY start_at""",
                          (user_id, start, end)):
        if is_watch_sleep(r):
            inp.nights.append(Night(r["wake_date"], r["start_at"], r["end_at"], r["asleep_min"], r["deep_min"] or 0,
                                    r["rem_min"] or 0, r["awake_min"] or 0, r["source"]))
        else:
            inp.non_watch_dates.add(r["wake_date"])
    inp.non_watch_dates -= {n.wake_date for n in inp.nights}
    for r in conn.execute("""SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = 'resting_heart_rate'
                             AND date BETWEEN %s AND %s""", (user_id, start, end)):
        inp.daily_rhr[r["date"]] = r["value"]
    inp.shortcut_dates = {r["date"] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_metrics WHERE user_id = %s AND source = 'shortcut' AND date BETWEEN %s AND %s",
        (user_id, start, end))}
    return inp


class _Merger:
    """Deduplicate by (field, start time); non-XML (OW app live sync, Shortcut) takes priority over export.xml."""

    def __init__(self):
        self.seen: dict[tuple[str, datetime], tuple[bool, float]] = {}

    def add(self, key: str, ts: datetime, value: float, xml: bool) -> None:
        k = (key, ts)
        if k not in self.seen or (self.seen[k][0] and not xml):
            self.seen[k] = (xml, value)

    def fill(self, inp: Inputs) -> None:
        by = defaultdict(list)
        for (key, ts), (_, v) in self.seen.items():
            by[key].append((ts, v))
        for key, xs in by.items():
            xs.sort()
            setattr(inp, key, xs)


def load_ow(client, ow_user_id: str, tz: ZoneInfo, start: date, end: date, merger: _Merger,
            types: dict | None = None, conn: psycopg.Connection | None = None, user_id: int | None = None,
            ow_cache: str | None = None) -> list:
    """Pull OW raw samples into the merger; returns the sleep-stage segments. ow_cache "use" / "refresh" reads the samples
    through the local copy (importers/ow_cache.py, needs conn and user_id); None reads them all from OW."""
    types = OW_TYPES if types is None else types
    lo = datetime.combine(start, time.min, tz)
    stop = datetime.combine(end + timedelta(days=1), time.min, tz)
    if ow_cache is not None and conn is not None and user_id is not None:
        got = ow_cache_mod.samples(conn, client, user_id, ow_user_id, types, lo, stop, ow_cache)
        for code, xs in got.items():
            key = types[code]
            for s in xs:
                merger.add(key, s.ts, s.value, s.xml)
        lo = stop   # the samples are in; only the sleep stages below
    while lo < stop:
        hi = min(lo + WINDOW, stop)
        for raw in client.paginate(f"/users/{ow_user_id}/timeseries", start_time=lo.isoformat(),
                                   end_time=hi.isoformat(), types=list(types), resolution="raw", limit=1000):
            if raw.get("is_daily_total") or raw["type"] not in types:
                continue
            _, _, s = ow_imp.parse_sample(raw)
            merger.add(types[raw["type"]], s.ts, s.value, s.xml)
        lo = hi
    sessions = list(client.paginate(f"/users/{ow_user_id}/events/sleep", include="stages", limit=100,
                                    start_date=(start - timedelta(days=1)).isoformat(),
                                    end_date=(end + timedelta(days=1)).isoformat()))
    return ow_imp.sleep_segments(sessions, tz)


def load_hk(conn: psycopg.Connection, user_id: int, tz: ZoneInfo, start: date, end: date, merger: _Merger,
            types: dict) -> list:
    """The app mode (rawdata/engine.mode): the sync engine's raw samples of the same OW series types into the merger, put
    in before OW's so a sample both carry is the engine's (the merger keeps the first live sample of a time stamp);
    returns the sleep-stage segments (OW's session rule on the uploaded stage samples, rawdata/samples.py)."""
    lo = datetime.combine(start, time.min, tz)
    hi = datetime.combine(end + timedelta(days=1), time.min, tz)
    for code, xs in rx_samples.samples(conn, user_id, list(types), lo, hi).items():
        for s in xs:
            merger.add(types[code], s.ts, s.value, xml=False)
    return rx_samples.sleep_segments(conn, user_id, tz, lo, hi)


def load_shortcut_db(conn: psycopg.Connection, user_id: int, tz: ZoneInfo, start: date, end: date,
                     merger: _Merger, types: dict | None = None) -> list | None:
    """Put the raw samples in shortcut_samples into the merger; returns the sleep-stage segments. Returns None if this user has never imported (no rows in the table)."""
    if conn.execute("SELECT 1 FROM shortcut_samples WHERE user_id = %s LIMIT 1", (user_id,)).fetchone() is None:
        return None
    lo = datetime.combine(start - timedelta(days=1), time.min, tz)
    hi = datetime.combine(end + timedelta(days=2), time.min, tz)
    rows = conn.execute(
        """SELECT type, start_at, end_at, value, value_text, source, agg FROM shortcut_samples
           WHERE user_id = %s AND start_at >= %s AND start_at < %s AND type = ANY(%s) ORDER BY start_at""",
        (user_id, lo, hi, [*(SHORTCUT_TYPES if types is None else types), "Heart Rate", "Sleep"])).fetchall()
    types = SHORTCUT_TYPES if types is None else types
    for r in rows:
        key = "hr" if r["type"] == "Heart Rate" and r["agg"] == "raw" else types.get(r["type"])
        if key is None or r["value"] is None:
            continue
        x = r["value"] * 100 if key == "spo2" and r["value"] <= 1.0 else r["value"]
        merger.add(key, r["start_at"], x, xml=False)
    return sc_imp.build_segments(rows)


def load_shortcut(archive_dir: Path, merger: _Merger, types: dict | None = None) -> list:
    """Fallback: for users who have not imported yet, read the archive directly (old-format heart rate is summed by hour and unused)."""
    types = SHORTCUT_TYPES if types is None else types
    if not archive_dir.is_dir():
        return []
    rows = sc_imp.read_archive(archive_dir)
    for t, ts, _end, v, unit, *_ in rows:
        key = types.get(t)
        if key is None:
            continue
        try:
            x = float(v)
        except ValueError:
            continue
        if key == "spo2" and x <= 1.0:
            x *= 100
        merger.add(key, datetime.fromisoformat(ts), x, xml=False)
    return sc_imp.build_segments(rows)


def load_export_db(conn: psycopg.Connection, user_id: int, tz: ZoneInfo, start: date, end: date,
                   merger: _Merger, hrv_kind: str = P.HRV_KIND) -> list:
    """Put the raw samples in export_samples (from the export.zip the user uploaded, history/) into the merger as an XML source (yielding to live-synced ones);
    returns the sleep-stage segments."""
    lo = datetime.combine(start - timedelta(days=1), time.min, tz)
    hi = datetime.combine(end + timedelta(days=2), time.min, tz)
    segs = []
    for r in conn.execute("""SELECT kind, start_at, end_at, value, stage, source FROM export_samples
                             WHERE user_id = %s AND start_at >= %s AND start_at < %s ORDER BY start_at""",
                          (user_id, lo, hi)):
        if r["kind"] == "sleep":
            segs.append(Segment(r["start_at"], r["end_at"], r["stage"], r["source"]))
        elif r["value"] is not None and (r["kind"] != "hrv" or hrv_kind == "sdnn"):   # export_samples hrv is SDNN
            merger.add(r["kind"], r["start_at"], r["value"], xml=True)
    return segs


def load_app_db(conn: psycopg.Connection, user_id: int, tz: ZoneInfo, start: date, end: date, merger: _Merger) -> None:
    """Raw samples the app read from HealthKit itself (hk_samples, rawdata/hk_types.py: the types with a `score` field,
    e.g. sleeping wrist temperature -> temp), as a live source (they win over an export.xml copy of the same sample)."""
    from ..rawdata import hk_types
    if not hk_types.SCORE_TYPES:
        return
    lo = datetime.combine(start - timedelta(days=1), time.min, tz)
    hi = datetime.combine(end + timedelta(days=2), time.min, tz)
    for r in conn.execute("""SELECT type, start_at, value FROM hk_samples
                             WHERE user_id = %s AND type = ANY(%s) AND start_at >= %s AND start_at < %s""",
                          (user_id, list(hk_types.SCORE_TYPES), lo, hi)):
        merger.add(hk_types.SCORE_TYPES[r["type"]], r["start_at"], r["value"], xml=False)


def rmssd_hrv(conn: psycopg.Connection, user_id: int, tz: ZoneInfo, start: date, end: date) -> Samples:
    """Per-reading RMSSD from heartbeat_series (the app copy already replaced the export copy of the same reading)."""
    lo = datetime.combine(start - timedelta(days=1), time.min, tz)
    hi = datetime.combine(end + timedelta(days=2), time.min, tz)
    return [(t, v) for t, v, _ in rx_store.rmssd_samples(conn, user_id, lo, hi)]


def load(conn: psycopg.Connection, client, user_id: int, start: date, end: date, archive_dir: Path | None,
         hrv_kind: str = P.HRV_KIND, ow_cache: str | None = None) -> Inputs:
    inp = load_db(conn, user_id, start, end)
    inp.hrv_kind = hrv_kind
    ow_user = conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()["ow_user_id"]
    merger = _Merger()
    segs = []
    app = rx_samples.reads_app(conn, user_id)
    if app:   # the app's own sync engine first (rawdata/samples.py); OW then only fills what it lacks
        segs += load_hk(conn, user_id, inp.tz, start, end, merger, ow_types(hrv_kind))
    if client is not None and ow_user:
        ow_segs = load_ow(client, ow_user, inp.tz, start, end, merger, ow_types(hrv_kind), conn=conn, user_id=user_id,
                          ow_cache=ow_cache)
        if app:
            have = rx_samples.wake_dates(segs, inp.tz)
            ow_segs = [s for s in ow_segs if (s.end - WAKE_SHIFT).astimezone(inp.tz).date() not in have]
        segs += ow_segs
    db_segs = load_shortcut_db(conn, user_id, inp.tz, start, end, merger, shortcut_types(hrv_kind))
    if db_segs is not None:
        segs += db_segs
    elif archive_dir is not None:
        segs += load_shortcut(archive_dir, merger, shortcut_types(hrv_kind))
    load_app_db(conn, user_id, inp.tz, start, end, merger)
    # export.zip sleep stages only fill nights other sources lack (two sets of segments for one night do not line up in time, and mixing them would double-count awake time)
    taken = {(s.end - WAKE_SHIFT).astimezone(inp.tz).date() for s in segs}
    segs += [s for s in load_export_db(conn, user_id, inp.tz, start, end, merger, hrv_kind)
             if (s.end - WAKE_SHIFT).astimezone(inp.tz).date() not in taken]
    merger.fill(inp)
    if hrv_kind == "rmssd":
        inp.hrv = rmssd_hrv(conn, user_id, inp.tz, start, end)
    # stage segments: keep only one per (start time + stage) (OW and the Shortcut overlap)
    uniq = {(s.start, s.stage): s for s in segs}
    inp.segments = sorted((s.start, s.end, s.stage) for s in uniq.values())
    return inp


def segments_in(segments: list[tuple[datetime, datetime, str]], start: datetime, end: datetime) -> list:
    return [s for s in segments if s[0] >= start and s[1] <= end]

