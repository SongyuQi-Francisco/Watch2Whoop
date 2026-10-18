"""HRV from Apple's beat-to-beat series (HKHeartbeatSeriesSample / <HeartRateVariabilityMetadataList> in export.xml).

Each background HRV reading of the watch stores about one minute of beat times (median 52 beats on the owner's data). From them:
RR_i = t_i − t_{i−1}; an interval is invalid when the beat was preceded by a gap (precededByGap), lies outside 300–2000 ms,
or deviates more than MAX_DEVIATION from the series median (ectopic beats / motion artifacts, Task Force ESC/NASPE 1996).
RMSSD = sqrt(mean((RR_i − RR_{i−1})²)) over pairs of adjacent valid intervals; SDNN = population SD of the valid intervals.
Check against Apple: SDNN recomputed this way correlates r = 0.90 with Apple's own SDNN value on 1,928 readings (docs/raw-data-plan.md §1).
"""
import math
import statistics
from dataclasses import dataclass

MIN_RR_MS, MAX_RR_MS = 300.0, 2000.0
MAX_DEVIATION = 0.25        # relative to the median RR of the series
MIN_VALID_DIFFS = 20        # fewer successive differences than this -> no RMSSD (the reading is unusable)
ALGO = "rr-v1"              # bump when the filter changes (stored with each row)


@dataclass(frozen=True)
class HrvResult:
    rmssd: float | None
    sdnn: float | None
    mean_rr: float | None
    n_beats: int
    n_valid: int            # valid RR intervals
    n_diffs: int            # successive differences used for RMSSD

    @property
    def artifact_ratio(self) -> float:
        n = max(self.n_beats - 1, 1)
        return round(1 - self.n_valid / n, 3)


def rr_intervals(beats_s: list[float], gaps: list[bool] | None = None) -> list[float | None]:
    """Beat times (seconds since the series start) -> RR in ms; None where beat i was preceded by a gap or times go backwards."""
    out: list[float | None] = []
    for i in range(1, len(beats_s)):
        if gaps is not None and i < len(gaps) and gaps[i]:
            out.append(None)
            continue
        d = (beats_s[i] - beats_s[i - 1]) * 1000
        out.append(d if d > 0 else None)
    return out


def clean(rr: list[float | None]) -> list[float | None]:
    ok = [x for x in rr if x is not None and MIN_RR_MS <= x <= MAX_RR_MS]
    if not ok:
        return [None] * len(rr)
    med = statistics.median(ok)
    return [x if x is not None and MIN_RR_MS <= x <= MAX_RR_MS and abs(x - med) <= MAX_DEVIATION * med else None
            for x in rr]


def analyze(beats_s: list[float], gaps: list[bool] | None = None) -> HrvResult:
    rr = clean(rr_intervals(beats_s, gaps))
    valid = [x for x in rr if x is not None]
    diffs = [b - a for a, b in zip(rr, rr[1:]) if a is not None and b is not None]
    rmssd = math.sqrt(sum(d * d for d in diffs) / len(diffs)) if len(diffs) >= MIN_VALID_DIFFS else None
    sdnn = statistics.pstdev(valid) if len(valid) > MIN_VALID_DIFFS else None
    return HrvResult(rmssd=None if rmssd is None else round(rmssd, 2), sdnn=None if sdnn is None else round(sdnn, 2),
                     mean_rr=round(statistics.fmean(valid), 1) if valid else None,
                     n_beats=len(beats_s), n_valid=len(valid), n_diffs=len(diffs))


def parse_clock(s: str) -> float | None:
    """'HH:MM:SS.ss' (export.xml InstantaneousBeatsPerMinute time) -> seconds since midnight."""
    try:
        h, m, sec = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    except (ValueError, AttributeError):
        return None


def beats_from_clock(times: list[str]) -> list[float]:
    """Clock times of consecutive beats -> seconds since the first beat (unwraps midnight).
    export.xml has only 10 ms resolution and no gap flags; the median filter in clean() takes care of the missing flags."""
    out: list[float] = []
    base = prev = None
    day = 0.0
    for s in times:
        t = parse_clock(s)
        if t is None:
            continue
        if prev is not None and t + day < prev - 43200:
            day += 86400
        t += day
        if base is None:
            base = t
        out.append(round(t - base, 3))
        prev = t
    return out
