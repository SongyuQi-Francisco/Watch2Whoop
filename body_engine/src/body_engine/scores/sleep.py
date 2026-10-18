"""Sleep: sleep score (Open Wearables 0.9.0's Sleep Score, ported line by line), sleep need, sleep debt, sleep performance. Pure functions."""
import math
import statistics
from datetime import datetime

from . import params as P

_MAX_EXP = 709.0


def _sigmoid(x: float, k: float, base: float, midpoint: float, anchor: float) -> float:
    """Scaled sigmoid that equals base exactly at anchor (OW scoring_primitives.score_sigmoid)."""
    num = 1 + math.exp(min(_MAX_EXP, k * (anchor - midpoint)))
    den = 1 + math.exp(min(_MAX_EXP, k * (x - midpoint)))
    return base * num / den


def duration_score(hours: float) -> int:
    """Full marks for 7–9h; too little sleep loses points on a steep sigmoid, too much loses slowly and bottoms out at 50."""
    if P.OPTIMAL_MIN_HOURS <= hours <= P.OPTIMAL_MAX_HOURS:
        return 100
    if hours < P.OPTIMAL_MIN_HOURS:
        raw = _sigmoid(hours, -P.UNDERSLEEP_K, 100, P.UNDERSLEEP_MIDPOINT, P.OPTIMAL_MIN_HOURS)
        return max(0, min(100, int(raw)))
    raw = min(100, int(_sigmoid(hours, P.OVERSLEEP_K, 100, P.OVERSLEEP_MIDPOINT, P.OPTIMAL_MAX_HOURS)))
    return max(50, raw)


def _stage_score(minutes: float, target: float) -> int:
    if minutes >= target:
        return 100
    if minutes <= 0:
        return 0
    return int(minutes / target * 100)


def stages_score(deep_min: float, rem_min: float) -> int:
    total = _stage_score(deep_min, P.DEEP_TARGET_MIN) * P.DEEP_WEIGHT + _stage_score(rem_min, P.REM_TARGET_MIN) * P.REM_WEIGHT
    return max(0, min(100, int(total)))


def hours_past_noon(dt: datetime) -> float:
    """Local time -> hours after 12 noon (continuous across midnight: 2 a.m. = 14)."""
    h = dt.hour + dt.minute / 60 + dt.second / 3600
    return (h + 24 if h < 12 else h) - 12


def consistency_score(history: list[datetime], tonight: datetime) -> int:
    """Compared with the median bedtime of the last 14 nights: deduction starts when more than 15 minutes late, all points lost 105 minutes later; going to bed early deducts at most 20.
    OW gives 0 points when history is empty. All times must be the user's local time."""
    if not history:
        return 0
    median = statistics.median(hours_past_noon(t) for t in history)
    diff = (hours_past_noon(tonight) - median) * 60
    penalty = 0.0
    if diff > P.CONSISTENCY_GRACE_MIN:
        penalty = (diff - P.CONSISTENCY_GRACE_MIN) / P.MAX_LATE_PENALTY_WINDOW_MIN * 100
    elif diff < -P.CONSISTENCY_GRACE_MIN:
        penalty = min(P.MAX_EARLY_PENALTY_POINTS,
                      (abs(diff) - P.CONSISTENCY_GRACE_MIN) / P.MAX_EARLY_PENALTY_WINDOW_MIN * 100)
    return max(0, int(100 - penalty))


def interruptions_score(awake_min: float, awakenings: list[float]) -> int:
    """Night awake time (WASO) 80 points: no deduction within 20 minutes, all lost 70 minutes later; wake-ups (> 5 minutes) 20 points: full marks for 0–1, 0 for 4 or more."""
    dur = P.INTERRUPTION_DURATION_POINTS
    if awake_min > P.INTERRUPTIONS_GRACE_MIN:
        excess = awake_min - P.INTERRUPTIONS_GRACE_MIN
        dur = max(0.0, dur - excess / P.MAX_INTERRUPTION_PENALTY_WINDOW_MIN * P.INTERRUPTION_DURATION_POINTS)
    n = sum(1 for d in awakenings if d > P.SIGNIFICANT_WAKE_MIN)
    freq = P.INTERRUPTION_FREQUENCY_POINTS * P.FREQ_SCORE_FRACTIONS[min(n, len(P.FREQ_SCORE_FRACTIONS) - 1)]
    return max(0, min(100, int(dur + freq)))


def waso(segments: list[tuple[datetime, datetime, str]]) -> tuple[float, list[float]]:
    """Real night awake time: excludes awake time before the first asleep segment (sleep latency) and after the last asleep segment (lingering in bed).
    Adjacent awake segments are merged into one wake-up. Returns (total awake minutes, minutes of each wake-up)."""
    segs = sorted(segments, key=lambda s: s[0])
    sleep_idx = [i for i, s in enumerate(segs) if s[2] != "awake"]
    if not sleep_idx:
        return 0.0, []
    awakenings: list[float] = []
    prev_end = None
    for start, end, stage in segs[sleep_idx[0]:sleep_idx[-1] + 1]:
        if stage != "awake":
            prev_end = None
            continue
        minutes = (end - start).total_seconds() / 60
        if prev_end is not None and start <= prev_end:
            awakenings[-1] += minutes
        else:
            awakenings.append(minutes)
        prev_end = end
    return sum(awakenings), awakenings


def sleep_score(total_sleep_min: float, deep_min: float, rem_min: float, bedtime: datetime,
                history: list[datetime], awake_min: float, awakenings: list[float]) -> dict:
    """Weighted over four items: duration 40%, stages 20%, regularity 20%, interruptions 20%. total_sleep_min is net sleep (excluding awake time)."""
    if not 0 < total_sleep_min <= 24 * 60:
        raise ValueError(f"睡眠时长不合理：{total_sleep_min} 分钟")
    parts = {
        "duration": duration_score(total_sleep_min / 60),
        "stages": stages_score(deep_min, rem_min),
        "consistency": consistency_score(history, bedtime),
        "interruptions": interruptions_score(awake_min, awakenings),
    }
    frac = (parts["duration"] / 100 * P.DURATION_IMPACT + parts["stages"] / 100 * P.STAGES_IMPACT
            + parts["consistency"] / 100 * P.CONSISTENCY_IMPACT + parts["interruptions"] / 100 * P.INTERRUPTIONS_IMPACT)
    return {"score": max(0, min(100, int(100 * frac))), **parts}


def sleep_debt(actual_by_night: list[float | None]) -> float:
    """actual_by_night[0] is the actual sleep minutes of the previous night (k=1), going back at most 14 nights; None = no data (not counted).
    Sleep debt = weighted average over the last 14 nights of the shortfall (baseline need - actual), weights 0.75^(k−1); oversleeping can offset it, and the result is never below 0."""
    num = den = 0.0
    for k, actual in enumerate(actual_by_night[:P.DEBT_NIGHTS]):
        if actual is not None:
            w = P.DEBT_DECAY ** k
            num += w * (P.SLEEP_NEED_BASELINE_MIN - actual)
            den += w
    return max(0.0, num / den) if den else 0.0


def sleep_need(debt_min: float, prev_strain: float | None) -> dict:
    """Sleep need = baseline 8h + sleep debt (at most 2h) + previous day's strain add-on (3 minutes per point of strain above 7)."""
    from_debt = min(P.DEBT_CAP_MIN, debt_min)
    from_strain = max(0.0, (prev_strain or 0.0) - P.STRAIN_NEED_FROM) * P.STRAIN_NEED_MIN_PER_POINT
    return {"need": P.SLEEP_NEED_BASELINE_MIN + from_debt + from_strain, "baseline": P.SLEEP_NEED_BASELINE_MIN,
            "from_debt": from_debt, "from_strain": from_strain}


def performance(actual_min: float, need_min: float) -> float:
    return min(actual_min / need_min, 1.0)
