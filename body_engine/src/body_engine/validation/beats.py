"""Synthetic beat-to-beat series for checking the HRV artifact filter (rawdata/hrv.py, `rr-v1`; issue #118,
docs/scoring-validation.md sections 2.5 and 3.6). Everything here is made up; no real data is used or imitated.

A series is about one minute of beats, like one Apple Watch background HRV reading:
RR_i = mean_rr + RSA sinusoid (breathing period 3.5–5 s) + a slower 0.1 Hz wave + white noise. The clean series has a known
RMSSD and SDNN (the textbook formulas over every interval, no filter). Artifacts are then injected beat by beat:
- ectopic: a premature beat (RR × 0.60–0.85, uniform: from clearly to mildly premature) followed by a compensatory pause
  (the next beat stays where it was, so the next RR is longer by the same amount);
- missed: a beat not detected (two intervals merged);
- extra: a false beat detected halfway through an interval (the interval split in two).
`analyze()` (the production filter) runs on the corrupted series; the error is against the clean truth.
Optionally the beat times are rounded to 10 ms, the resolution of export.xml.
"""
import math
import random
import statistics

from ..rawdata import hrv as H

ARTIFACTS = ("ectopic", "missed", "extra")
SECONDS = 60.0


def clean_series(rng: random.Random, mean_rr: float, rsa_ms: float, lf_ms: float, noise_ms: float) -> list[float]:
    """Beat times in seconds (first beat at 0) of an artifact-free series lasting about SECONDS."""
    period = rng.uniform(3.5, 5.0)
    phase, lf_phase = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)
    t, beats = 0.0, [0.0]
    while t < SECONDS:
        rr = mean_rr + rsa_ms * math.sin(2 * math.pi * t / period + phase) \
            + lf_ms * math.sin(2 * math.pi * t / 10.0 + lf_phase) + rng.gauss(0, noise_ms)
        t += max(rr, 250.0) / 1000
        beats.append(t)
    return beats


def truth(beats: list[float]) -> tuple[float, float]:
    """(RMSSD, SDNN) in ms over every interval of a clean series, no filter."""
    rr = [(b - a) * 1000 for a, b in zip(beats, beats[1:])]
    diffs = [b - a for a, b in zip(rr, rr[1:])]
    return math.sqrt(sum(d * d for d in diffs) / len(diffs)), statistics.pstdev(rr)


def corrupt(rng: random.Random, beats: list[float], rate: float, kinds: tuple[str, ...] = ARTIFACTS) -> list[float]:
    """The same series with artifacts: each beat (not the first two or the last) is hit with probability `rate`."""
    out = list(beats)
    i = 2
    while i < len(out) - 1:
        if rng.random() < rate:
            kind = rng.choice(kinds)
            a, b, c = out[i - 1], out[i], out[i + 1]
            if kind == "ectopic":           # beat i comes early; beat i+1 stays where it was (compensatory pause)
                out[i] = a + rng.uniform(0.60, 0.85) * (b - a)
            elif kind == "missed":
                del out[i]
                continue
            else:                           # extra beat halfway between i and i+1
                out.insert(i + 1, b + (c - b) / 2)
                i += 1
            i += 2                          # never two artifacts on neighbouring beats
            continue
        i += 1
    return out


def quantise(beats: list[float], step_s: float = 0.01) -> list[float]:
    return [round(b / step_s) * step_s for b in beats]


# (label, mean RR ms, RSA amplitude, 0.1 Hz amplitude, white noise): from low to high HRV
LEVELS = [("low", 750.0, 8.0, 6.0, 5.0), ("normal", 900.0, 30.0, 20.0, 12.0),
          ("high", 1050.0, 70.0, 35.0, 20.0), ("very high", 1150.0, 130.0, 50.0, 25.0)]
RATES = (0.0, 0.02, 0.05)
FILTERS = (("rr-v1 (25%)", {}), ("20%", {"MAX_DEVIATION": 0.20}), ("30%", {"MAX_DEVIATION": 0.30}),
           ("range only", {"MAX_DEVIATION": 10.0}))


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def run(n: int = 300, seed: int = 11, levels=LEVELS, rates=RATES, filters=FILTERS, quantised: bool = False) -> list[dict]:
    """One row per (HRV level, artifact rate, filter): true RMSSD range, median / 95th percentile of the absolute relative
    error of RMSSD and SDNN, share of series without an RMSSD, and the share of intervals the filter rejected."""
    out = []
    for label, mean_rr, rsa, lf, noise in levels:
        for rate in rates:
            rng = random.Random(f"{seed}-{label}-{rate}")
            series = []
            for _ in range(n):
                clean = clean_series(rng, mean_rr, rsa, lf, noise)
                bad = corrupt(rng, clean, rate)
                series.append((truth(clean), quantise(bad) if quantised else bad))
            for name, changes in filters:
                p = H.override(**changes) if changes else H
                e_rmssd, e_sdnn, missing, rejected = [], [], 0, []
                for (t_rmssd, t_sdnn), beats in series:
                    res = H.analyze(beats, params=p)
                    rejected.append(1 - res.n_valid / max(res.n_beats - 1, 1))
                    if res.rmssd is None:
                        missing += 1
                    else:
                        e_rmssd.append(abs(res.rmssd - t_rmssd) / t_rmssd)
                    if res.sdnn is not None:
                        e_sdnn.append(abs(res.sdnn - t_sdnn) / t_sdnn)
                true_rmssd = [t for (t, _), _ in series]
                out.append({"level": label, "true_rmssd": (round(_pct(true_rmssd, 0.05)), round(_pct(true_rmssd, 0.95))),
                            "rate": rate, "filter": name,
                            "rmssd_err_median": round(statistics.median(e_rmssd), 3) if e_rmssd else None,
                            "rmssd_err_p95": round(_pct(e_rmssd, 0.95), 3) if e_rmssd else None,
                            "sdnn_err_median": round(statistics.median(e_sdnn), 3) if e_sdnn else None,
                            "no_rmssd": round(missing / n, 3), "rejected": round(statistics.fmean(rejected), 3)})
    return out


def markdown(rows: list[dict], quantised: bool = False) -> str:
    out = [f"# HRV artifact filter on synthetic beat series ({'times rounded to 10 ms' if quantised else 'exact times'})", "",
           "Absolute relative error against the clean series; `rejected` = share of intervals the filter dropped.", "",
           "| HRV level (true RMSSD 5–95%, ms) | artifact rate | filter | RMSSD error median | RMSSD error p95 | SDNN error median | no RMSSD | rejected |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lo, hi = r["true_rmssd"]
        out.append(f"| {r['level']} ({lo}–{hi}) | {r['rate']:.0%} | {r['filter']} | {r['rmssd_err_median']} | {r['rmssd_err_p95']} | "
                   f"{r['sdnn_err_median']} | {r['no_rmssd']:.0%} | {r['rejected']:.1%} |")
    return "\n".join(out) + "\n"
