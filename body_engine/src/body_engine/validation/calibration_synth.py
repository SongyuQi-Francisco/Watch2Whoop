"""Synthetic persons for the calibration rules (docs/scoring-calibration.md section 8, issue #118). Everything is made up.

A person has a latent daily state (AR(1)) that drives nightly HRV, resting heart rate and respiratory rate. Each night has one to
four background readings. A reading's beat series is built from the state plus its own noise, so its RMSSD comes through the
production filter; its SDNN is a separate noisy value. Settings give the checks a known answer:
- `rmssd_noise` < `sdnn_noise`: RMSSD readings are truly less noisy, the rules should find it (power);
- equal noise: no difference, the rules should keep the current value (false positives);
- `ectopic_rate`: mildly premature beats are injected, which a tighter artifact threshold should handle better;
- `rhr_drift`: resting heart rate steps up halfway, which makes the colours lopsided for a while (the scale check).
"""
import math
import random
from datetime import date, datetime, timedelta, timezone

from ..rawdata import hrv as H
from . import beats as B
from . import calibration as K
from .calibration import Day, Person, Reading

START = date(2030, 1, 1)
UTC = timezone.utc


def _beats_for(rng: random.Random, target_rmssd: float, mean_rr: float) -> list[float]:
    """A clean one-minute series whose RMSSD is near the target (the breathing and noise amplitudes scale with it)."""
    f = target_rmssd / 33.0
    return B.clean_series(rng, mean_rr, 30.0 * f, 20.0 * f, 12.0 * f)


def _mild_ectopic(rng: random.Random, beats: list[float], rate: float) -> list[float]:
    """Premature beats 21–24% early with a compensatory pause: they pass a 25% median filter but not a 20% one
    (scoring-validation 3.6)."""
    out = list(beats)
    i = 2
    while i < len(out) - 1:
        if rng.random() < rate:
            out[i] = out[i - 1] + rng.uniform(0.76, 0.79) * (out[i] - out[i - 1])
            i += 2
        else:
            i += 1
    return out


def make_person(days: int = 300, seed: int = 1, *, rmssd_noise: float = 0.15, sdnn_noise: float = 0.30, ectopic_rate: float = 0.0,
                rhr_drift: float = 0.0, state_to_hrv: float = 0.12, app_from: float = 0.7, rmssd_level: float = 40.0) -> Person:
    """`app_from`: the share of the days after which readings count as the newest source (exact beat times).
    `rmssd_level`: the person's typical RMSSD in ms."""
    rng = random.Random(f"calibration-{seed}")
    p = Person(stored_kind="rmssd")
    state = 0.0
    for i in range(days):
        d = START + timedelta(days=i)
        state = 0.6 * state + rng.gauss(0, 0.8)
        start = datetime(d.year, d.month, d.day, 0, 30, tzinfo=UTC) - timedelta(minutes=rng.randint(0, 90))
        end = start + timedelta(hours=rng.uniform(6.0, 8.5))
        drift = rhr_drift if i >= days / 2 else 0.0
        trimp = rng.uniform(100, 220) if d.weekday() in (0, 2, 4, 5) else rng.uniform(5, 35)
        actual = (end - start).total_seconds() / 60 - rng.uniform(10, 40)
        day = Day(d, start, end, rhr=52 - 1.5 * state + drift + rng.gauss(0, 0.8), resp=14 - 0.1 * state + rng.gauss(0, 0.3),
                  perf=min(1.0, 0.85 + 0.05 * state + rng.gauss(0, 0.08)), actual_min=actual, trimp=trimp,
                  strain=round(21 * (1 - math.exp(-trimp / 170)), 2), debt_min=max(0.0, 480 - actual))
        p.days[d] = day
        p.train_days[d] = (trimp, day.strain)
        p.daily_rhr[d] = day.rhr + 5 + rng.gauss(0, 1.5)
        if d.weekday() in (0, 2, 4):
            p.hrr[d] = 30 + 2 * state + rng.gauss(0, 5)
        origin = "app" if i >= app_from * days else "apple_export"
        for k in range(rng.randint(1, 4)):
            t = start + timedelta(minutes=30 + 70 * k + rng.randint(0, 20))
            target = math.exp(math.log(rmssd_level) + state_to_hrv * state + rng.gauss(0, rmssd_noise))
            series = _beats_for(rng, target, 950 + 12 * rng.gauss(0, 1))
            if ectopic_rate:
                series = _mild_ectopic(rng, series, ectopic_rate)
            if origin == "apple_export":
                series = B.quantise(series)
            sd = math.exp(math.log(55) + state_to_hrv * state + rng.gauss(0, sdnn_noise))
            stored = H.analyze(series).rmssd
            p.readings.append(Reading(t, origin, [round(b * 1000) for b in series], None, sd, stored))
            p.sdnn.append((t, sd))
    p.readings.sort(key=lambda r: r.start)
    p.sdnn.sort()
    # what a scoring run would have stored for this person (rule A then has something to reproduce)
    series = K.stored_rmssd_series(p)
    for row in K.replay(p, series):
        day = p.days[row["date"]]
        day.stored_recovery, day.stored_hrv_ln = row["recovery"], row["hrv_night_ln"]
    return p


def operating_characteristics(seeds: int = 40, days: int = 300, n_boot: int = 300) -> list[dict]:
    """How often the rules say `eligible` when the answer is known: the false-positive rate and the power of D2 and D3."""
    out = []
    for label, kw in (("D2, no difference (noise 0.25 / 0.25)", {"rmssd_noise": 0.25, "sdnn_noise": 0.25}),
                      ("D2, RMSSD less noisy (0.15 / 0.45)", {"rmssd_noise": 0.15, "sdnn_noise": 0.45})):
        verdicts = []
        for seed in range(1, seeds + 1):
            p = make_person(days, seed, **kw)
            era = K.app_era(p)
            verdicts.append(K.compare(p, K.sdnn_series(p), K.rmssd_series(p, "RMSSD"), n_boot=n_boot, seed=seed, era=era)["verdict"])
        out.append({"setting": label, "eligible": sum(v == "eligible" for v in verdicts) / seeds, "seeds": seeds})
    for label, kw in (("D3, no artifacts", {}), ("D3, 0.5% mildly premature beats", {"ectopic_rate": 0.005}),
                      ("D3, 3% mildly premature beats", {"ectopic_rate": 0.03})):
        results = []
        for seed in range(1, seeds + 1):
            p = make_person(days, seed, **kw)
            era = K.app_era(p)
            cur, cand = K.rmssd_series(p, "25%"), K.rmssd_series(p, "20%", max_deviation=0.20)
            n = sum(1 for r in p.readings if r.beats_ms and len(r.beats_ms) >= 2)
            results.append(K.compare(p, cur, cand, n_boot=n_boot, seed=seed, era=era, extra=K.filter_extras(cur, cand, n)))
        out.append({"setting": label, "seeds": seeds, "eligible": sum(r["verdict"] == "eligible" for r in results) / seeds,
                    "improvement": sum(r["gates"]["E_improvement"]["ok"] for r in results) / seeds,
                    "no_overfilter": sum(r["gates"]["no_overfilter"]["ok"] for r in results) / seeds})
    return out


def operating_markdown(rows: list[dict]) -> str:
    def share(row: dict, key: str) -> str:
        return f"{row[key]:.0%}" if key in row else "—"
    out = ["# Calibration rules on synthetic persons (known answer)", "",
           "| setting | seeds | `eligible` | rule E passes | over-filtering check passes |", "|---|---|---|---|---|"]
    out += [f"| {r['setting']} | {r['seeds']} | {r['eligible']:.0%} | {share(r, 'improvement')} | {share(r, 'no_overfilter')} |" for r in rows]
    return "\n".join(out) + "\n"
