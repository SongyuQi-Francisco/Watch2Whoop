"""Stress monitor: while awake and not moving, how far heart rate / HRV deviates from a personal calm baseline, scored 0–3.

- Only heart-rate readings while awake (not in any sleep segment), not during a workout (OW workout records), and with no walking from 5 minutes before to 2 minutes after (steps < 20).
  Accelerometer data is not available from Apple Health exports, so steps and workout records stand in for it.
- Calm baseline: median of such readings over the previous 14 days = usual awake resting heart rate; dispersion = 1.4826 × MAD (robust standard deviation, floor 3 bpm).
  HRV baseline: mean / standard deviation of nightly ln HRV over the previous 60 days; the occasional daytime HRV readings (Apple, every 2–4 hours)
  count as high stress when below it.
- Per hour: at least 3 readings are needed for a score. Deviation z = (median heart rate of the hour − baseline) / dispersion; with HRV readings z = 0.7 × heart-rate z + 0.3 × HRV z.
  Score = clip(0.5 + z, 0, 3): the usual state ≈ 0.5 (low), half a dispersion above enters "medium" (>= 1), 1.5 above enters "high" (>= 2).
- Per day: mean score of the scored hours, hours in low / medium / high, and the highest hour.
No raw heart rate -> no score for that day (the caller states the reason).

Parameters: 1.4826 × MAD is the usual factor that makes the median absolute deviation comparable to a standard deviation for
normally distributed data. The windows, weights, offset and bands below are v1 values, not yet validated (METHODOLOGY.md, issue #118).
Every function takes `params=` (default: this module); `override(**changes)` builds a modified copy.
"""
import bisect
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import _override

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
_SELF = sys.modules[__name__]   # default for the `params=` argument; override(**changes) gives a modified copy


def band(score: float | None, *, params=_SELF) -> str | None:
    if score is None:
        return None
    return next(name for lo, name in params.BANDS if score >= lo)


def robust_baseline(values: list[float], *, params=_SELF) -> tuple[float, float] | None:
    if len(values) < params.MIN_BASELINE_SAMPLES:
        return None
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values)
    return med, max(1.4826 * mad, params.SPREAD_FLOOR)


def calm_samples(hr: list[tuple[datetime, float]], sleep: list[tuple[datetime, datetime]],
                 workouts: list[tuple[datetime, datetime]], steps: list[tuple[datetime, float]],
                 *, params=_SELF) -> list[tuple[datetime, float]]:
    """Heart-rate readings while awake, not in a workout, not walking. Each list sorted by time."""
    busy = sorted(sleep + workouts)
    out, j = [], 0
    step_times = [t for t, _ in steps]
    for t, v in hr:
        while j < len(busy) and busy[j][1] < t:
            j += 1
        if any(a <= t <= b for a, b in busy[j:j + 3]):
            continue
        lo = bisect.bisect_left(step_times, t - params.STEP_WINDOW[0])
        hi = bisect.bisect_right(step_times, t + params.STEP_WINDOW[1])
        if sum(v2 for _, v2 in steps[lo:hi]) >= params.STEP_ACTIVE:
            continue
        out.append((t, v))
    return out


def score_day(calm: list[tuple[datetime, float]], hrv: list[tuple[datetime, float]], d: date, tz: ZoneInfo,
              hr_base: tuple[float, float], hrv_base: tuple[float, float] | None, *, params=_SELF) -> dict:
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
        if len(xs) < params.MIN_HOUR_SAMPLES:
            continue
        z = (statistics.median(xs) - hr_base[0]) / hr_base[1]
        zh = None
        if hrv_base and hrv_hour.get(h):
            zh = (hrv_base[0] - statistics.fmean(hrv_hour[h])) / max(hrv_base[1], 0.05)
            z = params.HR_WEIGHT * z + params.HRV_WEIGHT * zh
        s = max(0.0, min(params.MAX_SCORE, params.OFFSET + z))
        hours.append({"hour": h, "score": round(s, 2), "level": band(s, params=params), "hr": round(statistics.median(xs), 1),
                      "samples": len(xs), "hrv_z": None if zh is None else round(zh, 2)})
    if not hours:
        return {"score": None, "level": None, "hours": [], "low_h": 0, "medium_h": 0, "high_h": 0, "peak_hour": None}
    mean = statistics.fmean(x["score"] for x in hours)
    peak = max(hours, key=lambda x: x["score"])
    count = lambda lv: sum(1 for x in hours if x["level"] == lv)
    return {"score": round(mean, 2), "level": band(mean, params=params), "hours": hours, "low_h": count("low"),
            "medium_h": count("medium"), "high_h": count("high"), "peak_hour": peak["hour"]}


def override(**changes):
    """These parameters with some replaced, for the `params=` argument of this module's functions, e.g.
    `robust_baseline(values, params=stress.override(SPREAD_FLOOR=2.0))`. The module itself stays unchanged."""
    return _override.override(_SELF, changes)
