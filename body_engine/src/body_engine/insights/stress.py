"""Stress monitor (matching WHOOP Stress Monitor): while awake and not moving, how far heart rate / HRV deviates from a personal calm baseline, scored 0–3.

- Only heart-rate readings while awake (not in any sleep segment), not during a workout (OW workout records), and with no walking from 5 minutes before to 2 minutes after (steps < 20).
  WHOOP excludes activity using acceleration; we have no acceleration, so we substitute steps and workout records.
- Calm baseline: median of such readings over the previous 14 days = usual awake resting heart rate; dispersion = 1.4826 × MAD (robust standard deviation, floor 3 bpm).
  HRV baseline: mean / standard deviation of night ln HRV (daily_scores.hrv_night_ln) over the previous 60 days; the occasional daytime HRV readings (Apple, every 2–4 hours)
  count as high stress when below it.
- Per hour: at least 3 readings are needed for a score. Deviation z = (median heart rate of the hour − baseline) / dispersion; with HRV readings z = 0.7 × heart-rate z + 0.3 × HRV z.
  Score = clip(0.5 + z, 0, 3): the usual state ≈ 0.5 (low), half a dispersion above enters "medium" (>= 1), 1.5 above enters "high" (>= 2).
- Per day: mean score of the scored hours, hours in low / medium / high, and the highest hour.
No raw heart rate (the Shortcut only has hour-summed heart rate) -> no score for that day, with the reason stated.
"""
import bisect
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ..importers import ow as ow_imp

STRESS_VERSION = "v1.0-2026-10-17"
BASELINE_DAYS = 14
MIN_BASELINE_SAMPLES = 50
SPREAD_FLOOR = 3.0
STEP_WINDOW = (timedelta(minutes=5), timedelta(minutes=2))   # from 5 minutes before to 2 minutes after the reading
STEP_ACTIVE = 20                                              # >= 20 steps in that span counts as moving
MIN_HOUR_SAMPLES = 3
HR_WEIGHT, HRV_WEIGHT = 0.7, 0.3
OFFSET, MAX_SCORE = 0.5, 3.0
BANDS = ((2.0, "high"), (1.0, "medium"), (0.0, "low"))
DEFAULT_DAYS = 90                                             # compute-scores recomputes the last 90 days by default


def band(score: float | None) -> str | None:
    if score is None:
        return None
    return next(name for lo, name in BANDS if score >= lo)


def robust_baseline(values: list[float]) -> tuple[float, float] | None:
    if len(values) < MIN_BASELINE_SAMPLES:
        return None
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    return med, max(1.4826 * mad, SPREAD_FLOOR)


def calm_samples(hr: list[tuple[datetime, float]], sleep: list[tuple[datetime, datetime]],
                 workouts: list[tuple[datetime, datetime]], steps: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """Heart-rate readings while awake, not in a workout, not walking. Each list sorted by time."""
    busy = sorted(sleep + workouts)
    out, j = [], 0
    step_times = [t for t, _ in steps]
    for t, v in hr:
        while j < len(busy) and busy[j][1] < t:
            j += 1
        if any(a <= t <= b for a, b in busy[j:j + 3]):
            continue
        lo = bisect.bisect_left(step_times, t - STEP_WINDOW[0])
        hi = bisect.bisect_right(step_times, t + STEP_WINDOW[1])
        if sum(v2 for _, v2 in steps[lo:hi]) >= STEP_ACTIVE:
            continue
        out.append((t, v))
    return out


def score_day(calm: list[tuple[datetime, float]], hrv: list[tuple[datetime, float]], d: date, tz: ZoneInfo,
              hr_base: tuple[float, float], hrv_base: tuple[float, float] | None) -> dict:
    """calm: heart-rate readings while awake and still that day; hrv: HRV readings (ms) during the day (awake)."""
    by_hour: dict[int, list[float]] = defaultdict(list)
    for t, v in calm:
        lt = t.astimezone(tz)
        if lt.date() == d:
            by_hour[lt.hour].append(v)
    hrv_hour: dict[int, list[float]] = defaultdict(list)
    for t, v in hrv:
        lt = t.astimezone(tz)
        if lt.date() == d and v > 0:
            hrv_hour[lt.hour].append(math.log(v))
    hours = []
    for h in range(24):
        xs = by_hour.get(h, [])
        if len(xs) < MIN_HOUR_SAMPLES:
            continue
        z = (statistics.median(xs) - hr_base[0]) / hr_base[1]
        zh = None
        if hrv_base and hrv_hour.get(h):
            zh = (hrv_base[0] - statistics.fmean(hrv_hour[h])) / max(hrv_base[1], 0.05)
            z = HR_WEIGHT * z + HRV_WEIGHT * zh
        s = max(0.0, min(MAX_SCORE, OFFSET + z))
        hours.append({"hour": h, "score": round(s, 2), "level": band(s), "hr": round(statistics.median(xs), 1),
                      "samples": len(xs), "hrv_z": None if zh is None else round(zh, 2)})
    if not hours:
        return {"score": None, "level": None, "hours": [], "low_h": 0, "medium_h": 0, "high_h": 0, "peak_hour": None}
    mean = statistics.fmean(x["score"] for x in hours)
    peak = max(hours, key=lambda x: x["score"])
    count = lambda lv: sum(1 for x in hours if x["level"] == lv)
    return {"score": round(mean, 2), "level": band(mean), "hours": hours, "low_h": count("low"),
            "medium_h": count("medium"), "high_h": count("high"), "peak_hour": peak["hour"]}


# ---------------------------------------------------------------- read data, compute day by day, write to the database
def _ow_samples(client, ow_user: str, types: list[str], lo: datetime, hi: datetime) -> dict[str, list]:
    out: dict[str, dict] = defaultdict(dict)
    cur = lo
    while cur < hi:
        nxt = min(cur + timedelta(days=30), hi)
        for raw in client.paginate(f"/users/{ow_user}/timeseries", start_time=cur.isoformat(), end_time=nxt.isoformat(),
                                   types=types, resolution="raw", limit=1000):
            if raw.get("is_daily_total") or raw["type"] not in types:
                continue
            _, _, s = ow_imp.parse_sample(raw)
            k = (s.ts, s.source) if raw["type"] == "steps" else s.ts   # each device records steps separately, split by source
            if k not in out[raw["type"]] or not s.xml:
                out[raw["type"]][k] = (s.ts, s.value)
        cur = nxt
    return {t: sorted(v.values()) for t, v in out.items()}


def _workouts(client, ow_user: str, start: date, end: date, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    out = []
    for w in client.paginate(f"/users/{ow_user}/events/workouts", start_date=start.isoformat(),
                             end_date=(end + timedelta(days=1)).isoformat(), limit=100):
        out.append((datetime.fromisoformat(w["start_time"]), datetime.fromisoformat(w["end_time"])))
    return sorted(out)


def compute(conn: psycopg.Connection, client, user_id: int, start: date, end: date) -> list[dict]:
    user = conn.execute("SELECT timezone, ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()
    tz = ZoneInfo(user["timezone"])
    lo = datetime.combine(start - timedelta(days=BASELINE_DAYS), time.min, tz)
    hi = datetime.combine(end + timedelta(days=1), time.min, tz)
    sleep = [(r["start_at"], r["end_at"]) for r in conn.execute(
        "SELECT start_at, end_at FROM sleep_sessions WHERE user_id = %s AND end_at >= %s AND start_at < %s ORDER BY start_at",
        (user_id, lo, hi))]
    hrv_rows = conn.execute("""SELECT date, hrv_night_ln FROM daily_scores WHERE user_id = %s AND hrv_night_ln IS NOT NULL
                               AND date BETWEEN %s AND %s""", (user_id, start - timedelta(days=60), end)).fetchall()
    shortcut = {r["date"] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_metrics WHERE user_id = %s AND source = 'shortcut' AND date BETWEEN %s AND %s",
        (user_id, start, end))}
    samples, workouts = {}, []
    if client is not None and user["ow_user_id"]:
        samples = _ow_samples(client, user["ow_user_id"], ["heart_rate", "steps", "heart_rate_variability_sdnn"], lo, hi)
        workouts = _workouts(client, user["ow_user_id"], start - timedelta(days=BASELINE_DAYS), end, tz)
    hr, steps, hrv = samples.get("heart_rate", []), samples.get("steps", []), samples.get("heart_rate_variability_sdnn", [])
    calm = calm_samples(hr, sleep, workouts, steps)
    awake_hrv = calm_samples(hrv, sleep, workouts, steps)
    calm_by_day: dict[date, list] = defaultdict(list)
    for t, v in calm:
        calm_by_day[t.astimezone(tz).date()].append((t, v))
    hrv_by_day: dict[date, list] = defaultdict(list)
    for t, v in awake_hrv:
        hrv_by_day[t.astimezone(tz).date()].append((t, v))
    hr_days = {t.astimezone(tz).date() for t, _ in hr}
    rows = []
    d = start
    while d <= end:
        flags = {}
        base_vals = [v for k in range(1, BASELINE_DAYS + 1) for _, v in calm_by_day.get(d - timedelta(days=k), [])]
        hr_base = robust_baseline(base_vals)
        hist = [r["hrv_night_ln"] for r in hrv_rows if d - timedelta(days=60) <= r["date"] < d]
        hrv_base = (statistics.fmean(hist), statistics.stdev(hist)) if len(hist) >= 14 else None
        if d not in hr_days:
            flags["no_raw_hr"] = ("这天只有快捷指令数据：心率是按小时求和的，算不了压力" if d in shortcut
                                  else "没有原始心率（手表没戴或没同步）")
            res = score_day([], [], d, tz, (0, 1), None)
        elif hr_base is None:
            flags["baseline_insufficient"] = f"前 {BASELINE_DAYS} 天醒着不动的心率读数只有 {len(base_vals)} 条（要 ≥ {MIN_BASELINE_SAMPLES}）"
            res = score_day([], [], d, tz, (0, 1), None)
        else:
            res = score_day(calm_by_day.get(d, []), hrv_by_day.get(d, []), d, tz, hr_base, hrv_base)
            if res["score"] is None:
                flags["no_calm_hours"] = "醒着不动、每小时至少 3 条心率的时段一个都没有"
            if not steps:
                flags["no_motion_data"] = "没有步数数据，走路时的心率没法排除，压力可能偏高"
        res["baseline"] = None if hr_base is None else {"calm_hr": round(hr_base[0], 1), "spread": round(hr_base[1], 2),
                                                         "hrv_ln": None if hrv_base is None else round(hrv_base[0], 3)}
        rows.append({"date": d, **res, "flags": flags})
        d += timedelta(days=1)
    return rows


def write(conn: psycopg.Connection, user_id: int, rows: list[dict]) -> int:
    if not rows:
        return 0
    with conn.transaction():
        conn.execute("DELETE FROM stress_daily WHERE user_id = %s AND date BETWEEN %s AND %s",
                     (user_id, rows[0]["date"], rows[-1]["date"]))
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO stress_daily (user_id, date, score, level, low_h, medium_h, high_h, peak_hour, hours,
                                             baseline, flags, algo_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(user_id, r["date"], r["score"], r["level"], r["low_h"], r["medium_h"], r["high_h"], r["peak_hour"],
                  Jsonb(r["hours"]), Jsonb(r["baseline"]), Jsonb(r["flags"]), STRESS_VERSION) for r in rows])
    return len(rows)


def read(conn: psycopg.Connection, user_id: int, start: date, end: date, hours: bool = True) -> list[dict]:
    cols = "date, score, level, low_h, medium_h, high_h, peak_hour, baseline, flags, algo_version"
    return conn.execute(f"SELECT {cols}{', hours' if hours else ''} FROM stress_daily WHERE user_id = %s "
                        "AND date BETWEEN %s AND %s ORDER BY date", (user_id, start, end)).fetchall()
