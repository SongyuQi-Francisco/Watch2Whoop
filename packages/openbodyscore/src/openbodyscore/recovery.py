"""Recovery: that night's HRV, that night's resting heart rate, 60-day personal baseline, z-score weighting + logistic, illness / temperature / SpO2 flags. Pure functions;
every function that reads a parameter takes `params=` (default: params.py; params.override(**changes) builds a modified copy)."""
import math
import statistics
from datetime import datetime, timedelta

from . import params as P

Samples = list[tuple[datetime, float]]   # (time, value), sorted by time


def in_window(samples: Samples, start: datetime, end: datetime) -> list[float]:
    return [v for t, v in samples if start <= t <= end]


def night_hrv_ln(samples: Samples, start: datetime, end: datetime) -> tuple[float | None, int]:
    """Median of the ln of the HRV readings within the sleep window. Returns (value, number of readings)."""
    xs = [math.log(v) for v in in_window(samples, start, end) if v > 0]
    return (statistics.median(xs) if xs else None), len(xs)


def lowest_window_mean(samples: Samples, start: datetime, end: datetime,
                       window: timedelta | None = None, hold: timedelta | None = None, *, params=P) -> float | None:
    """Lowest 5-minute mean during sleep (time-weighted). The watch measures heart rate only about every 4–5 minutes during sleep,
    so each reading counts until the next one (at most hold); if readings cover less than half of the window, it does not count."""
    window = params.RHR_WINDOW if window is None else window
    hold = params.HR_HOLD_CAP if hold is None else hold
    return lowest_window(samples, start, end, window, hold, params=params)[0]


def lowest_window(samples: Samples, start: datetime, end: datetime,
                  window: timedelta | None = None, hold: timedelta | None = None, *, params=P) -> tuple[float | None, datetime | None]:
    """lowest_window_mean plus when that window started (for the Trends recovery index)."""
    window = params.RHR_WINDOW if window is None else window
    hold = params.HR_HOLD_CAP if hold is None else hold
    xs = [(t, v) for t, v in samples if start <= t <= end]
    if not xs:
        return None, None
    spans = []   # (start, end, value)
    for i, (t, v) in enumerate(xs):
        stop = min(t + hold, end, xs[i + 1][0] if i + 1 < len(xs) else end)
        spans.append((t, max(stop, t), v))
    best, at = None, None
    for w0, _, _ in spans:
        w1 = w0 + window
        covered = weighted = 0.0
        for a, b, v in spans:
            if a >= w1:
                break
            overlap = (min(b, w1) - max(a, w0)).total_seconds()
            if overlap > 0:
                covered += overlap
                weighted += overlap * v
        if covered >= window.total_seconds() / 2:
            mean = weighted / covered
            if best is None or mean < best:
                best, at = mean, w0
    if best is None:   # readings too sparse, fall back to the lowest single reading
        at, best = min(xs, key=lambda x: x[1])
    return best, at


def baseline(values: list[float], *, params=P) -> tuple[float, float] | None:
    """Personal baseline: mean and standard deviation (returns None if fewer than MIN_BASELINE_NIGHTS samples)."""
    if len(values) < params.MIN_BASELINE_NIGHTS:
        return None
    return statistics.fmean(values), statistics.stdev(values)


def zscore(x: float, mean: float, sd: float, floor: float, *, params=P) -> float:
    z = (x - mean) / max(sd, floor)
    return max(-params.Z_CLIP, min(params.Z_CLIP, z))


def recovery_score(z: dict[str, float], *, params=P) -> tuple[int, float]:
    """z: each item's z-score against its baseline (sign already flipped to "higher is better"). A missing item's weight is redistributed proportionally to the others; HRV is required.
    Returns (recovery score 0–100, weighted z)."""
    if "hrv" not in z:
        raise ValueError("没有 HRV 不能算恢复分")
    total_w = sum(params.RECOVERY_WEIGHTS[k] for k in z)
    s = sum(params.RECOVERY_WEIGHTS[k] * v for k, v in z.items()) / total_w
    score = 100 / (1 + math.exp(-(params.RECOVERY_SLOPE * s + params.RECOVERY_OFFSET)))
    return int(round(score)), s


def illness_warning(rhr: float | None, rhr_base: float | None, hrv_ln: float | None, hrv_base_ln: float | None, *, params=P) -> bool:
    """Resting heart rate > baseline + 5 and HRV < 0.85 × baseline (the HRV baseline is a mean of ln, converted back to ms for comparison)."""
    if None in (rhr, rhr_base, hrv_ln, hrv_base_ln):
        return False
    return rhr > rhr_base + params.ILLNESS_RHR_DELTA and math.exp(hrv_ln) < params.ILLNESS_HRV_RATIO * math.exp(hrv_base_ln)


def wrist_temp_flag(temp: float | None, history: list[float], *, params=P) -> float | None:
    """Deviation of wrist temperature from the 60-day baseline median; returns the deviation if >= 0.6°C, otherwise None."""
    if temp is None or len(history) < params.MIN_TEMP_BASELINE:
        return None
    delta = temp - statistics.median(history)
    return delta if abs(delta) >= params.WRIST_TEMP_DELTA else None


def spo2_flag(readings: list[float], *, params=P) -> dict | None:
    """That night's SpO2 (0–100): median < 94% or >= 2 readings < 90% -> return a summary, otherwise None."""
    if not readings:
        return None
    med = statistics.median(readings)
    low = sum(1 for r in readings if r < params.SPO2_LOW_READING)
    if med < params.SPO2_LOW_MEDIAN or low >= params.SPO2_LOW_COUNT:
        return {"median": round(med, 1), "min": min(readings), "below_90": low}
    return None
