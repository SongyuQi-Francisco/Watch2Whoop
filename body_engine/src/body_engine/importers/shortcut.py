"""Import data uploaded by the iPhone Shortcut: health_data/archive/health_*.txt (format in docs/shortcut-sync-spec.md).

Used in the transition period (until the Open Wearables iOS app is back). Non-cumulative metrics and sleep have the lowest priority (never overwrite
apple_export / ow); cumulative ones compete on completeness and value (importers/common.py resolve_daily).
Both archive formats are accepted:
- rows-v0 (before 2026-10-17): one row per sample, Type|Start|End|Value|Unit|Source.
- cols-v1: first line #format=cols-v1, then six sections @@type / @@start / @@end / @@value / @@unit / @@source, one value per line in each section.
  The Shortcut skips empty values (sleep has no unit; sleep and hourly-grouped samples have no source), so the unit / source sections are shorter than the others,
  and are padded by "whole type missing" (_align); if they cannot be matched, that section is dropped and noted in shortcut_uploads.notes.
Samples are stored by (type, start, source) into shortcut_samples (a new upload overwrites the old); daily metrics, sleep and inferred workouts are all recomputed from the table:
- Hourly-grouped heart rate is the sum of the readings in that hour, not an average, and is unused; raw heart rate (ungrouped, with a source) goes into daily heart_rate and scoring.
- Cumulative metrics (steps, active / resting energy, exercise / stand minutes), cut into days in the user's time zone (users.timezone):
  - raw samples (Shortcut 1.1+, 2026-10-18): per day the largest single-source (device) total, the same rule as export.xml (apple_zip /
    parse_export). iPhone and Watch both count the same walk, so adding them doubles steps; the largest single device never double counts,
    and only misses what the other device alone recorded (e.g. steps while the watch was charging, a few %).
  - hourly-grouped samples (Shortcut 1.0 and older): no source, the iPhone and the Watch are already added together (steps ~2x), so the day
    is flagged hourly_sum and the value is used only when no clean source has that day (importers/common.py resolve_daily).
    Per type, hour samples from the first raw sample's hour on are ignored (the new Shortcut took over); the earlier ones still count.
  - kJ converted to kcal.
- Sleep is cut at gaps > 60 minutes and assigned to the day of (wake − 4h).
- The Shortcut cannot read workouts: workout windows are inferred from ungrouped exercise minutes + raw heart rate, producing Workout rows (spec §4), handed to the training module.
Incremental: already imported upload files (shortcut_uploads) are skipped, --all re-imports everything; importing the same data several times gives the same result.
"""
import gzip
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from itertools import combinations
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from ..training import shortcut_workouts  # Workout rows -> workouts table (format in docs/shortcut-sync-spec.md §4)
from . import common
from .common import Segment, normalize, replace_sleep, sessionize, upsert_daily

SOURCE = "shortcut"
OWNER_USER_ID = 1   # the receiver (health_sync/health_server.py) files user 1's uploads in archive/, everyone else's in archive/u<id>/


def user_archive_dir(user_id: int) -> Path:
    """This user's Shortcut upload archive. Never default another user to archive/: that is user 1's data, and scoring
    falls back to reading the archive for users without imported samples (it mixed user 1's samples into theirs)."""
    from ..config import settings
    base = settings.health_data_dir / "archive"
    return base if int(user_id) == OWNER_USER_ID else base / f"u{int(user_id)}"
COLS = ("type", "start", "end", "value", "unit", "source")
SUM = {"Steps": "step_count", "Active Calories": "active_energy_burned",
       "Resting Calories": "basal_energy_burned", "Exercise Time": "apple_exercise_time",
       "Stand Minutes": "apple_stand_time"}
AVG = {"Heart Rate Variability": "heart_rate_variability_sdnn", "Resting Heart Rate": "resting_heart_rate",
       "Oxygen Saturation": "oxygen_saturation", "Respiratory Rate": "respiratory_rate", "VO2 Max": "vo2_max",
       "Cardio Fitness": "vo2_max", "Walking Heart Rate Average": "walking_heart_rate_average",
       "Weight": "body_mass", "Body Mass": "body_mass", "Body Fat Percentage": "body_fat_percentage",
       "Lean Body Mass": "lean_body_mass", "Wrist Temperature": "apple_sleeping_wrist_temperature",
       "Sleep Wrist Temperature": "apple_sleeping_wrist_temperature", "Heart Rate": "heart_rate"}
STAGE_MAP = {"Core": "core", "Deep": "deep", "REM": "rem", "Awake": "awake", "Asleep": "unspecified",
             "In Bed": None}
# Types with no unit / source in the Shortcut (spec §3): preferred when padding the unit and source sections of cols-v1.
# NO_SOURCE is the Shortcut 1.0 layout (cumulative types grouped by hour); from 1.1 only Sleep has no source, which
# _source_candidates tries next ({"Sleep"} is its first combination).
NO_UNIT = {"Sleep"}
NO_SOURCE = {"Sleep", "Steps", "Active Calories", "Resting Calories", "Stand Minutes"}
RAW_HR_SHARE = 0.95            # heart rate in the old format: counts as raw samples only if >= 95% in a file have start = end and all lie within 25–230
# Inferred workouts (spec §4)
WORKOUT_GAP = timedelta(minutes=5)
WORKOUT_MIN_EXERCISE = 10      # minutes
WORKOUT_MIN_HR_SAMPLES = 5
WORKOUT_MIN_AVG_HR = 95
WORKOUT_TYPE = "Other"
WORKOUT_SOURCE = "快捷指令推断（锻炼分钟 + 心率）"
KJ_PER_KCAL = 4.184


@dataclass
class Sample:
    type: str
    start: datetime
    end: datetime
    value: float | None
    value_text: str | None
    unit: str | None
    source: str
    agg: str = "raw"


@dataclass
class Upload:
    file: str
    format: str
    bytes: int
    samples: list[Sample] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)    # rows as-is, one per sample (Workout rows are left for the training module)
    notes: dict = field(default_factory=dict)


def upload_name(path: Path) -> str:
    return path.name.removesuffix(".gz")


def archive_files(archive_dir: Path) -> list[Path]:
    files = list(archive_dir.glob("health_*.txt")) + list(archive_dir.glob("health_*.txt.gz"))
    return sorted(files, key=upload_name)


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return data.decode("utf-8", errors="ignore")


def _blocks(types: list[str]) -> list[tuple[str, int, int]]:
    """A run of consecutive same-type samples -> (type, start, length). The Shortcut joins each type together in the order of "Find Health Samples"."""
    out = []
    for i, t in enumerate(types):
        if out and out[-1][0] == t and out[-1][1] + out[-1][2] == i:
            out[-1] = (t, out[-1][1], out[-1][2] + 1)
        else:
            out.append((t, i, 1))
    return out


def _align(types: list[str], col: list[str], candidates: list[set[str]], same_within_type: bool) -> list[str] | None:
    """A section where the Shortcut skipped empty values -> same length as types (missing ones padded with ''). What is missing is always a "whole type":
    first try candidates in order (type combinations that may be wholly missing), use the first that fits and ignore later ones that also fit;
    if none works, search for "which wholly missing types add up to exactly this shortfall", and use it only if the solution is unique. Returns None if nothing matches."""
    missing = len(types) - len(col)
    if missing < 0:
        return None
    blocks = _blocks(types)
    chosen = None
    if missing == 0:
        chosen = set()
    for cand in candidates if chosen is None else []:
        idx = {i for i, b in enumerate(blocks) if b[0] in cand}
        if sum(blocks[i][2] for i in idx) == missing:
            chosen = idx
            break
    if chosen is None:
        ways, how = {0: 1}, {0: frozenset()}     # ways[s] = number of subsets summing to s (counted up to 2)
        for i, (_t, _s, ln) in enumerate(blocks):
            for s in sorted(ways, reverse=True):
                if s + ln <= missing:
                    ways[s + ln] = min(2, ways.get(s + ln, 0) + ways[s])
                    how.setdefault(s + ln, how[s] | {i})
        if ways.get(missing) != 1:
            return None
        chosen = set(how[missing])
    out, k = [], 0
    for i, (_t, _s, ln) in enumerate(blocks):
        if i in chosen:
            out += [""] * ln
        else:
            part = col[k:k + ln]
            if len(part) < ln or (same_within_type and len(set(part)) > 1):
                return None
            out += part
            k += ln
    return out if k == len(col) else None


def _source_candidates(types: list[str]) -> list[set[str]]:
    """Combinations in which the source section may be wholly missing: NO_SOURCE from the spec first, then try "sleep + any number of cumulative types (grouped by hour)", fewer first."""
    present = sorted(set(types) & set(SUM))
    combos = [{"Sleep", *c} for r in range(len(present) + 1) for c in combinations(present, r)]
    return [NO_SOURCE] + combos


def _parse_cols(text: str, up: Upload) -> list[list[str]]:
    cols, cur = {}, None
    for line in text.split("\n")[1:]:
        line = line.rstrip("\r")
        if line.startswith("@@"):
            cur = line[2:].strip()
            cols[cur] = []
        elif cur is not None:
            cols[cur].append(line)
    for c in COLS:
        cols.setdefault(c, [])
        # a blank line at the end of a section (the newline at the end of the Text action) is not a value: the Shortcut skips empty values and never outputs blank lines
        while cols[c] and cols[c][-1].strip() == "":
            cols[c].pop()
    types = [t.strip() for t in cols["type"]]
    n = len(types)
    for c in ("start", "end", "value"):
        if len(cols[c]) != n:
            up.notes["cols_mismatch"] = f"{c} 段 {len(cols[c])} 行，类型段 {n} 行，整个文件不用"
            return []
    unit = _align(types, cols["unit"], [NO_UNIT], True)
    if unit is None:
        up.notes["unit_misaligned"] = f"单位段 {len(cols['unit'])} 行对不上 {n} 行，单位全部留空"
        unit = [""] * n
    source = _align(types, cols["source"], _source_candidates(types), False)
    if source is None:
        up.notes["source_misaligned"] = f"来源段 {len(cols['source'])} 行对不上 {n} 行，来源全部留空"
        source = [""] * n
    return [[types[i], cols["start"][i].strip(), cols["end"][i].strip(), cols["value"][i].strip(),
             unit[i].strip(), source[i].strip()] for i in range(n)]


def _to_sample(p: list[str]) -> Sample | None:
    t, start, end, v, unit, src = (p + [""] * 6)[:6]
    try:
        s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError:
        return None
    if s.tzinfo is None or e.tzinfo is None or e < s:
        return None
    try:
        x, text = float(v), None
    except ValueError:
        x, text = None, v or None
    return Sample(t, s, e, x, text, unit or None, src)


def parse_file(path: Path) -> Upload:
    text = _read_text(path)
    first = text.split("\n", 1)[0].strip()
    up = Upload(upload_name(path), "cols-v1" if first.startswith("#format=cols-v1") else "rows-v0", len(text.encode()))
    if up.format == "cols-v1":
        rows = _parse_cols(text, up)
    else:
        rows = [p for p in (line.rstrip("\r").split("|") for line in text.split("\n")) if len(p) >= 6 and p[1]]
    up.rows = rows
    for p in rows:
        if p[0] == "Workout":
            continue
        smp = _to_sample(p)
        if smp is not None:
            up.samples.append(smp)
    _mark_agg(up)
    return up


def _mark_agg(up: Upload) -> None:
    """Which samples were summed by hour by the Shortcut (agg = hour): cumulative types without a source. Heart rate: with a source = raw readings;
    when the source section is lost (or an old file) follow the plan document's rule: only if >= 95% have start = end and all lie within 25–230 does it count as raw."""
    hr = [s for s in up.samples if s.type == "Heart Rate"]
    hr_raw_by_shape = bool(hr) and (sum(s.start == s.end for s in hr) >= RAW_HR_SHARE * len(hr)
                                    and all(s.value is not None and 25 <= s.value <= 230 for s in hr))
    # A cumulative type without a source is hour-grouped, unless it has two samples in one clock hour: then they are raw samples whose
    # source section was lost (source_misaligned). Marking those "hour" would make _store delete all but one of them per hour.
    per_hour = Counter((s.type, s.start.replace(minute=0, second=0, microsecond=0)) for s in up.samples
                       if s.type in SUM and not s.source)
    raw_sum = {t for (t, _h), n in per_hour.items() if n > 1}
    if raw_sum:
        up.notes["sum_source_missing"] = f"{'、'.join(sorted(raw_sum))} 是原始样本但没有来源：各设备只能直接相加（偏高）"
    for s in up.samples:
        if s.type == "Heart Rate":
            s.agg = "raw" if s.source or hr_raw_by_shape else "hour"
        elif s.type in SUM and not s.source and s.type not in raw_sum:
            s.agg = "hour"
    if hr and all(s.agg == "hour" for s in hr):
        up.notes["hr_hourly_sum"] = "心率是按小时求和的，不用"


def read_archive(archive_dir: Path) -> list[list[str]]:
    """Merge all uploads into one row per sample ([type, start, end, value, unit, source], cols-v1 is expanded into this too), keeping only the last of each.
    Used by the training module to read Workout rows; the import itself goes through shortcut_samples."""
    rows = {}
    for f in archive_files(archive_dir) if archive_dir.is_dir() else []:
        for p in parse_file(f).rows:
            rows[tuple(p[:4])] = p
    return list(rows.values())


# ---------- write to the database ----------

def _store(conn: psycopg.Connection, user_id: int, up: Upload) -> None:
    rows = {}
    for s in up.samples:
        rows[(s.type, s.start, s.source)] = s       # duplicates within one file: keep the later one
    hour = [s for s in rows.values() if s.agg == "hour"]
    with conn.cursor() as cur:
        if hour:
            # hour-summed samples: readings that arrive earlier in the hour may be uploaded later, so "start" can change; old samples of the same hour are deleted
            cur.execute(
                """DELETE FROM shortcut_samples s USING unnest(%s::text[], %s::text[], %s::timestamptz[]) AS n(type, source, start_at)
                   WHERE s.user_id = %s AND s.agg = 'hour' AND s.type = n.type AND s.source = n.source
                     AND date_trunc('hour', s.start_at) = date_trunc('hour', n.start_at)
                     AND s.start_at <> n.start_at AND s.upload <= %s""",
                ([s.type for s in hour], [s.source for s in hour], [s.start for s in hour], user_id, up.file))
        cur.executemany(
            """INSERT INTO shortcut_samples (user_id, type, start_at, end_at, value, value_text, unit, source, agg, upload)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (user_id, type, start_at, source) DO UPDATE SET
                 end_at = EXCLUDED.end_at, value = EXCLUDED.value, value_text = EXCLUDED.value_text,
                 unit = EXCLUDED.unit, agg = EXCLUDED.agg, upload = EXCLUDED.upload
               WHERE shortcut_samples.upload <= EXCLUDED.upload""",
            [(user_id, s.type, s.start, s.end, s.value, s.value_text, s.unit, s.source, s.agg, up.file)
             for s in rows.values()])
    conn.execute(
        """INSERT INTO shortcut_uploads (user_id, file, format, bytes, rows, counts, notes) VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (user_id, file) DO UPDATE SET format = EXCLUDED.format, bytes = EXCLUDED.bytes, rows = EXCLUDED.rows,
             counts = EXCLUDED.counts, notes = EXCLUDED.notes, imported_at = now()""",
        (user_id, up.file, up.format, up.bytes, len(up.samples),
         json.dumps(Counter(s.type for s in up.samples), ensure_ascii=False), json.dumps(up.notes, ensure_ascii=False)))


def _load(conn: psycopg.Connection, user_id: int, lo: datetime, hi: datetime, types) -> list[dict]:
    return conn.execute(
        """SELECT type, start_at, end_at, value, value_text, unit, source, agg FROM shortcut_samples
           WHERE user_id = %s AND start_at >= %s AND start_at < %s AND type = ANY(%s) ORDER BY start_at""",
        (user_id, lo, hi, list(types))).fetchall()


def raw_since(conn: psycopg.Connection, user_id: int) -> dict[str, datetime]:
    """Per cumulative type, the start of the hour of its first raw sample: hour-grouped samples from then on are ignored (build_daily)."""
    return {r["type"]: r["t"].replace(minute=0, second=0, microsecond=0) for r in conn.execute(
        """SELECT type, min(start_at) AS t FROM shortcut_samples WHERE user_id = %s AND agg = 'raw' AND source <> ''
           AND type = ANY(%s) GROUP BY type""", (user_id, list(SUM)))}


def _use_sum_sample(r: dict, cut: dict[str, datetime]) -> bool:
    """Hour-grouped samples of a type count only before that type's first raw sample (the newer Shortcut supersedes them)."""
    return r["agg"] == "raw" or r["type"] not in cut or r["start_at"] < cut[r["type"]]


def _summed(r: dict) -> bool:
    """Samples in which the devices are already added together: hour-grouped, or raw ones whose source was lost."""
    return r["agg"] == "hour" or not r["source"]


def build_daily(samples: list[dict], tz: ZoneInfo, cut: dict[str, datetime] | None = None) -> list[dict]:
    """Daily rows (source shortcut) from shortcut_samples, days cut in the user's time zone tz.
    Cumulative: largest single-device total of the raw samples + the hour-grouped / sourceless part (flag hourly_sum when that part exists).
    cut: raw_since(); defaults to the first raw sample of each type in samples."""
    if cut is None:
        cut = {}
        for r in samples:
            if r["type"] in SUM and r["agg"] == "raw" and r["source"]:
                h = r["start_at"].replace(minute=0, second=0, microsecond=0)
                cut[r["type"]] = min(cut.get(r["type"], h), h)
    sums = defaultdict(float)     # (date, metric, source) -> total of the raw samples of one device
    summed = defaultdict(float)   # (date, metric) -> total of the samples where devices are already added together
    counts = defaultdict(int)
    avgs = defaultdict(list)      # (date, metric) -> value
    units = {}
    for r in samples:
        t = r["type"]
        metric = SUM.get(t) or AVG.get(t)
        if metric is None or r["value"] is None or (t == "Heart Rate" and r["agg"] != "raw"):
            continue
        if t in SUM and not _use_sum_sample(r, cut):
            continue
        x, unit = normalize(metric, r["value"], r["unit"])
        if metric == "body_fat_percentage" and x <= 1.0:
            x, unit = x * 100, "%"
        d = r["start_at"].astimezone(tz).date()
        units[metric] = unit
        if t in SUM:
            if _summed(r):
                summed[(d, metric)] += x
            else:
                sums[(d, metric, r["source"])] += x
            counts[(d, metric)] += 1
        else:
            avgs[(d, metric)].append(x)
    best = {k: 0.0 for k in summed}
    for (d, metric, _src), total in sums.items():
        best[(d, metric)] = max(best.get((d, metric), 0.0), total)
    out = []
    for (d, m), v in best.items():
        row = {"date": d, "metric": m, "value": round(v + summed.get((d, m), 0.0), 4), "unit": units[m], "agg": "sum",
               "n": counts[(d, m)], "min_value": None, "max_value": None}
        if summed.get((d, m)):
            unit = "" if units[m] in (None, "count") else f" {units[m]}"
            row["flags"] = {"hourly_sum": f"其中 {summed[(d, m)]:.0f}{unit} 是快捷指令按小时合计的"
                                          "（iPhone 和手表直接相加，偏高）"}
        out.append(row)
    out += [{"date": d, "metric": m, "value": round(sum(xs) / len(xs), 4), "unit": units[m], "agg": "avg",
             "n": len(xs), "min_value": min(xs), "max_value": max(xs)} for (d, m), xs in avgs.items()]
    return out


def build_segments(samples) -> list[Segment]:
    """Sleep stage segments. samples can be rows of shortcut_samples or the one-row-per-sample output of read_archive."""
    out = []
    for r in samples:
        if isinstance(r, dict):
            t, s, e, v, src = r["type"], r["start_at"], r["end_at"], r["value_text"], r["source"]
        else:
            t, s, e, v, src = r[0], r[1], r[2], r[3], r[5]
            if t == "Sleep":
                try:
                    s, e = datetime.fromisoformat(s), datetime.fromisoformat(e)
                except ValueError:
                    continue
        if t == "Sleep" and STAGE_MAP.get(v):
            out.append(Segment(s, e, STAGE_MAP[v], src or None))
    return out


def infer_workouts(samples: list[dict], cut: dict[str, datetime] | None = None) -> list[list[str]]:
    """Ungrouped exercise minutes joined into stretches (gaps <= 5 minutes); a stretch with >= 10 cumulative minutes, >= 5 raw heart-rate readings and average >= 95 counts as a workout.
    Active energy is split by the proportion overlapping the workout window (the only way to spread hourly-grouped samples);
    with raw samples, the largest single device in the window (as in build_daily). Returns Workout rows per spec §4."""
    cut = cut or {}
    ex = [r for r in samples if r["type"] == "Exercise Time" and r["agg"] == "raw" and r["value"]]
    hr = [r for r in samples if r["type"] == "Heart Rate" and r["agg"] == "raw" and r["value"]]
    energy = [r for r in samples if r["type"] == "Active Calories" and r["value"] and _use_sum_sample(r, cut)]
    sessions = []
    for r in ex:
        if sessions and r["start_at"] - sessions[-1]["end"] <= WORKOUT_GAP:
            sessions[-1]["end"] = max(sessions[-1]["end"], r["end_at"])
            sessions[-1]["min"] += r["value"]
        else:
            sessions.append({"start": r["start_at"], "end": r["end_at"], "min": r["value"]})
    out = []
    for s in sessions:
        if s["min"] < WORKOUT_MIN_EXERCISE:
            continue
        xs = [r["value"] for r in hr if s["start"] <= r["start_at"] <= s["end"]]
        if len(xs) < WORKOUT_MIN_HR_SAMPLES or sum(xs) / len(xs) < WORKOUT_MIN_AVG_HR:
            continue
        by_src = defaultdict(float)     # '' = devices already added together (hour-grouped)
        for r in energy:
            span = (r["end_at"] - r["start_at"]).total_seconds()
            overlap = (min(r["end_at"], s["end"]) - max(r["start_at"], s["start"])).total_seconds()
            if overlap > 0 or (span == 0 and s["start"] <= r["start_at"] <= s["end"]):
                v = r["value"] / KJ_PER_KCAL if r["unit"] == "kJ" else r["value"]
                by_src["" if _summed(r) else r["source"]] += v * (overlap / span if span > 0 else 1)
        kcal = by_src.pop("", 0.0) + max(by_src.values(), default=0.0)
        out.append(["Workout", s["start"].isoformat(), s["end"].isoformat(), WORKOUT_TYPE,
                    str(round((s["end"] - s["start"]).total_seconds())), f"{kcal:.1f}" if energy else "",
                    f"{sum(xs) / len(xs):.1f}", f"{max(xs):g}", WORKOUT_SOURCE])
    return out


def _tz(conn: psycopg.Connection, user_id: int) -> ZoneInfo:
    user = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None:
        raise ValueError(f"没有用户 {user_id}")
    return ZoneInfo(user["timezone"])


def _rebuild(conn: psycopg.Connection, user_id: int, tz: ZoneInfo, first, last) -> dict:
    """Recompute daily metrics, sleep and inferred workouts of the local days [first, last] from shortcut_samples."""
    day0 = datetime.combine(first, time.min, tz)
    day_end = datetime.combine(last + timedelta(days=1), time.min, tz)
    cut = raw_since(conn, user_id)
    daily = build_daily(_load(conn, user_id, day0, day_end, set(SUM) | set(AVG)), tz, cut)
    n_daily = upsert_daily(conn, user_id, SOURCE, daily)
    # sleep: read 3 extra days back, rewrite only wake dates >= first day − 1 (the night truncated at the window edge is not written)
    segs = build_segments(_load(conn, user_id, day0 - timedelta(days=3), day_end + timedelta(days=1), ["Sleep"]))
    sessions = [s for s in sessionize(segs) if s.wake_date >= first - timedelta(days=1)]
    sleep = replace_sleep(conn, user_id, SOURCE, sessions)
    workouts = infer_workouts(_load(conn, user_id, day0 - timedelta(hours=6), day_end,
                                    ["Exercise Time", "Heart Rate", "Active Calories"]), cut)
    return {"daily_rows": n_daily, "sleep": sleep, "workouts": workouts}


def rebuild(conn: psycopg.Connection, user_id: int, first, last=None) -> dict:
    """`import-shortcut --rebuild-since`: recompute the Shortcut's days from the table (no archive parsing) and re-choose every daily
    metric of [first, last] (also the days without Shortcut data), e.g. after the rules in importers/common.py change. Idempotent."""
    tz = _tz(conn, user_id)
    last = last or datetime.now(tz).date()
    with conn.transaction():
        res = _rebuild(conn, user_id, tz, first, last)
        n = common.resolve_daily(conn, user_id, [first + timedelta(days=i) for i in range((last - first).days + 1)])
    return {"range": [first, last], "daily_rows": res["daily_rows"], "resolved": n, "sleep": res["sleep"],
            "workouts_inferred": len(res["workouts"]), "workouts": shortcut_workouts.run(conn, user_id, res["workouts"])}


def run(conn: psycopg.Connection, user_id: int, archive_dir: Path, all_files: bool = False) -> dict:
    tz = _tz(conn, user_id)
    done = {r["file"] for r in conn.execute("SELECT file FROM shortcut_uploads WHERE user_id = %s", (user_id,))}
    files = [f for f in (archive_files(archive_dir) if archive_dir.is_dir() else [])
             if all_files or upload_name(f) not in done]
    rows = []                                   # rows as-is, one per sample (the training module reads Workout rows)
    lo = hi = None
    notes = {}
    with conn.transaction():
        for f in files:
            up = parse_file(f)
            _store(conn, user_id, up)
            rows += up.rows
            if up.notes:
                notes[up.file] = up.notes
            if up.samples:
                a, b = min(s.start for s in up.samples), max(s.end for s in up.samples)
                lo, hi = min(lo or a, a), max(hi or b, b)
    if lo is None:
        return {"files": len(files), "samples": 0, "daily_rows": 0, "sleep": {"inserted": 0, "skipped": 0},
                "workouts": {"workouts": 0, "inserted": 0, "updated": 0}}
    first, last = lo.astimezone(tz).date(), hi.astimezone(tz).date()
    with conn.transaction():
        res = _rebuild(conn, user_id, tz, first, last)
        n_daily, sleep, workouts = res["daily_rows"], res["sleep"], res["workouts"]
        rows += workouts
        conn.execute("""INSERT INTO sync_state (user_id, source, last_synced_at) VALUES (%s, %s, now())
                        ON CONFLICT (user_id, source) DO UPDATE SET last_synced_at = now()""", (user_id, SOURCE))
    return {"files": len(files), "samples": sum(1 for p in rows if p and p[0] != "Workout"), "range": [first, last],
            "daily_rows": n_daily, "sleep": sleep, "workouts_inferred": len(workouts),
            "workouts": shortcut_workouts.run(conn, user_id, rows), "notes": notes}
