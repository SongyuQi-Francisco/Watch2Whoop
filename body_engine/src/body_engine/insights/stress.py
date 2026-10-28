"""Stress monitor (matching WHOOP Stress Monitor; the formulas are in openbodyscore.stress): while awake and not moving, how far heart rate / HRV deviates from a personal calm baseline, scored 0–3.

- Only heart-rate readings while awake (not in any sleep segment), not during a workout (OW workout records; in the sync mode `app` the app's own samples and workouts first, rawdata/samples.py), and with no walking from 5 minutes before to 2 minutes after (steps < 20).
  WHOOP excludes activity using acceleration; we have no acceleration, so we substitute steps and workout records.
- Calm baseline: median of such readings over the previous 14 days = usual awake resting heart rate; dispersion = 1.4826 × MAD (robust standard deviation, floor 3 bpm).
  HRV baseline: mean / standard deviation of night ln HRV (daily_scores.hrv_night_ln) over the previous 60 days; the occasional daytime HRV readings (Apple, every 2–4 hours)
  count as high stress when below it.
- Per hour: at least 3 readings are needed for a score. Deviation z = (median heart rate of the hour − baseline) / dispersion; with HRV readings z = 0.7 × heart-rate z + 0.3 × HRV z.
  Score = clip(0.5 + z, 0, 3): the usual state ≈ 0.5 (low), half a dispersion above enters "medium" (>= 1), 1.5 above enters "high" (>= 2).
- Per day: mean score of the scored hours, hours in low / medium / high, and the highest hour.
No raw heart rate (the Shortcut only has hour-summed heart rate) -> no score for that day, with the reason stated.
"""
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

# the pure scoring part moved to the openbodyscore package (issue #115); re-exported so existing imports keep working
from openbodyscore.stress import (BANDS, BASELINE_DAYS, HR_WEIGHT, HRV_WEIGHT, MAX_SCORE, MIN_BASELINE_SAMPLES,  # noqa: F401
                                  MIN_HOUR_SAMPLES, OFFSET, SPREAD_FLOOR, STEP_ACTIVE, STEP_WINDOW, STRESS_VERSION, band,
                                  calm_samples, override, robust_baseline, score_day)

from ..importers import ow as ow_imp, ow_cache as ow_cache_mod
from ..rawdata import ow_archive as rx_archive, samples as rx_samples

STRESS_TYPES = ["heart_rate", "steps", "heart_rate_variability_sdnn"]   # OW series codes (the app's samples are read by them too)
DEFAULT_DAYS = 90                                             # compute-scores recomputes the last 90 days by default


# ---------------------------------------------------------------- read data, compute day by day, write to the database
def _ow_samples(client, ow_user: str, types: list[str], lo: datetime, hi: datetime, conn: psycopg.Connection | None = None,
                user_id: int | None = None, ow_cache: str | None = None) -> dict[str, list]:
    """ow_cache "use" / "refresh": read through the local copy (importers/ow_cache.py); None: all from OW."""
    out: dict[str, dict] = defaultdict(dict)
    if ow_cache is not None and conn is not None and user_id is not None:
        return _dedupe(ow_cache_mod.samples(conn, client, user_id, ow_user, types, lo, hi, ow_cache))
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


def _dedupe(got: dict[str, list]) -> dict[str, list]:
    """OW Samples by code -> (ts, value) lists as the paging below builds them (one per time stamp, steps per source)."""
    out: dict[str, dict] = defaultdict(dict)
    for code, xs in got.items():
        for s in xs:
            k = (s.ts, s.source) if code == "steps" else s.ts
            if k not in out[code] or not s.xml:
                out[code][k] = (s.ts, s.value)
    return {t: sorted(v.values()) for t, v in out.items()}


def _workouts(client, ow_user: str, start: date, end: date, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    out = []
    for w in client.paginate(f"/users/{ow_user}/events/workouts", start_date=start.isoformat(),
                             end_date=(end + timedelta(days=1)).isoformat(), limit=100):
        out.append((datetime.fromisoformat(w["start_time"]), datetime.fromisoformat(w["end_time"])))
    return sorted(out)


def _with_app(conn: psycopg.Connection, user_id: int, tz: ZoneInfo, lo: datetime, hi: datetime, ow_samples: dict,
              ow_workouts: list) -> tuple[dict, list]:
    """The sync mode `app` (rawdata/samples.py): the app's own samples and workouts; OW's samples of a type only for the
    days the app has none of that type (step counts are kept per source, so mixing both on one day would add up), and
    the workouts of both (a duplicate window excludes nothing more)."""
    app = rx_samples.samples(conn, user_id, STRESS_TYPES, lo, hi)
    out = {}
    for code in STRESS_TYPES:
        mine = app.get(code, [])
        k = (lambda s: (s.ts, s.source)) if code == "steps" else (lambda s: s.ts)   # as _ow_samples
        have = {s.ts.astimezone(tz).date() for s in mine}
        xs = {k(s): (s.ts, s.value) for s in mine}
        out[code] = sorted([*xs.values(), *(x for x in ow_samples.get(code, []) if x[0].astimezone(tz).date() not in have)])
    return out, sorted({*ow_workouts, *rx_samples.workout_windows(conn, user_id, lo, hi)})


def compute(conn: psycopg.Connection, client, user_id: int, start: date, end: date, ow_cache: str | None = None) -> list[dict]:
    """ow_cache: how the OW samples are read (importers/ow_cache.py), None = all from OW."""
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
    if rx_archive.reads_archive(conn, user_id):   # app mode, OW's history archived (step S6): OW is not asked
        samples = _dedupe(rx_archive.samples(conn, user_id, STRESS_TYPES, lo, hi))
        workouts = rx_archive.workout_windows(conn, user_id, lo, hi)
    elif client is not None and user["ow_user_id"]:
        samples = _ow_samples(client, user["ow_user_id"], STRESS_TYPES, lo, hi, conn, user_id, ow_cache)
        workouts = _workouts(client, user["ow_user_id"], start - timedelta(days=BASELINE_DAYS), end, tz)
    if rx_samples.reads_app(conn, user_id):
        samples, workouts = _with_app(conn, user_id, tz, lo, hi, samples, workouts)
    hr, steps, hrv = samples.get("heart_rate", []), samples.get("steps", []), samples.get("heart_rate_variability_sdnn", [])
    return score_range(tz, start, end, sleep, workouts, hr, steps, hrv, {r["date"]: r["hrv_night_ln"] for r in hrv_rows},
                       shortcut)


def score_range(tz: ZoneInfo, start: date, end: date, sleep: list, workouts: list, hr: list, steps: list, hrv: list,
                hrv_night: dict[date, float], shortcut: set[date]) -> list[dict]:
    """The day-by-day part of compute(), without the database: pure, ported to packages/openbodyscore-js (stressDays).
    sleep / workouts: [(start, end)]; hr / steps / hrv: [(time, value)] sorted; hrv_night: date -> night ln HRV
    (daily_scores.hrv_night_ln); shortcut: dates that only have Shortcut data (flag text only)."""
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
        hist = [v for k, v in hrv_night.items() if d - timedelta(days=60) <= k < d]
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
