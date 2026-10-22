"""Strain: HRmax, Edwards TRIMP, strain (0–21), CTL / ATL / TSB. Pure functions;
every function that reads a parameter takes `params=` (default: params.py; params.override(**changes) builds a modified copy)."""
import math
from collections import Counter
from datetime import date, datetime, timedelta

from . import params as P


def hrmax_formula(age: float) -> float:
    """Tanaka: 208 − 0.7 × age."""
    return 208 - 0.7 * age


def age_on(birth: date, d: date) -> int:
    return d.year - birth.year - ((d.month, d.day) < (birth.month, birth.day))


class HrHistogram:
    """Heart-rate histogram (integer bpm) in a sliding window, used to find the 99.5th percentile of observed heart rate."""

    def __init__(self):
        self.counts: Counter = Counter()
        self.n = 0

    def add(self, hist: Counter, sign: int = 1) -> None:
        for bpm, c in hist.items():
            self.counts[bpm] += sign * c
        self.n += sign * sum(hist.values())

    def percentile(self, q: float) -> float | None:
        if self.n <= 0:
            return None
        rank = q / 100 * (self.n - 1)
        seen = 0
        for bpm in sorted(self.counts):
            seen += self.counts[bpm]
            if seen > rank:
                return float(bpm)
        return float(max(self.counts))


def hrmax(age: float, observed_p995: float | None) -> float:
    """HRmax is the larger of "99.5th percentile of observed values" and "208 − 0.7 × age"."""
    return max(hrmax_formula(age), observed_p995 or 0.0)


def sample_minutes(times: list[datetime], cap: timedelta | None = None, *, params=P) -> list[float]:
    """Minutes each heart-rate sample represents: until the next one, at most cap; the last one counts as half of cap."""
    cap = params.HR_SAMPLE_CAP if cap is None else cap
    out = []
    for i, t in enumerate(times):
        dt = (times[i + 1] - t) if i + 1 < len(times) else cap / 2
        out.append(min(dt, cap).total_seconds() / 60)
    return out


def zone_weight(hr: float, hr_max: float, *, params=P) -> int:
    pct = hr / hr_max
    for lo, w in params.EDWARDS_ZONES:
        if pct >= lo:
            return w
    return 0


def edwards_trimp(samples: list[tuple[datetime, float]], minutes: list[float], hr_max: float,
                  *, params=P) -> tuple[float, list[float]]:
    """Edwards TRIMP = Σ minutes per zone × weight (50/60/70/80/90% HRmax -> 1–5). Returns (TRIMP, minutes in zones 1–5)."""
    zones = [0.0] * 5
    for (_, hr), m in zip(samples, minutes):
        w = zone_weight(hr, hr_max, params=params)
        if w:
            zones[w - 1] += m
    return sum(m * (i + 1) for i, m in enumerate(zones)), zones


def strain(trimp: float, k: float | None = None, *, params=P) -> float:
    k = params.STRAIN_K if k is None else k
    return params.STRAIN_MAX * (1 - math.exp(-trimp / k))


def training_load(trimps: list[float | None], *, params=P) -> list[dict]:
    """CTL (42-day EWMA), ATL (7-day EWMA), TSB = CTL − ATL, computed day by day.
    Days without data (None) keep the previous day's value without updating and are flagged missing; the initial value = mean of the first 14 days with data."""
    known = [t for t in trimps if t is not None][:params.LOAD_SEED_DAYS]
    if not known:
        return [{"ctl": None, "atl": None, "tsb": None, "missing": t is None} for t in trimps]
    ctl = atl = sum(known) / len(known)
    a_ctl, a_atl = 1 - math.exp(-1 / params.CTL_DAYS), 1 - math.exp(-1 / params.ATL_DAYS)
    out = []
    for t in trimps:
        if t is not None:
            ctl += a_ctl * (t - ctl)
            atl += a_atl * (t - atl)
        out.append({"ctl": ctl, "atl": atl, "tsb": ctl - atl, "missing": t is None})
    return out
