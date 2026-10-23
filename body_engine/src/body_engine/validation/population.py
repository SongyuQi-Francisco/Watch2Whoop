"""A synthetic population for the calibration question of issue #118 (docs/scoring-validation.md sections 2.6 and 3.7).
Everything here is made up; no real data is used or imitated.

N synthetic persons (synthetic.make_person) whose constants differ: HRV level, how strongly HRV and resting heart rate follow
the hidden readiness state, single-reading noise, readings a night, age, training volume. The question is how the fixed
recovery parameters (weights, logistic slope / offset, zone thresholds) behave across different people: does the share
of green / red days depend on things the per-person baselines should remove (absolute HRV or heart-rate level), or on how
informative a person's inputs are (coupling, noise)? And how much would a strain constant K calibrated on each person vary?
"""
import math
import random
import statistics
from datetime import date

from ..scores import params as P
from . import study, synthetic

TRAITS = ("hrv_ms", "hrv_per_r", "rhr_bpm", "rhr_per_r", "reading_noise", "readings", "age", "plan_scale")
READINGS_CHOICES = ((1, 1, 2), (1, 2, 2, 3), (2, 3, 3, 4))
STRAIN_REF = 12.0      # the strain a median training day is mapped to when K is calibrated (as K = 170 was)


def draw(rng: random.Random) -> tuple[synthetic.Traits, float]:
    """One person's traits and single-reading noise; the ranges are our assumptions, chosen to span typical adults."""
    traits = synthetic.Traits(
        hrv_ms=rng.uniform(25, 90), hrv_per_r=rng.uniform(0.08, 0.30), rhr_bpm=rng.uniform(45, 68),
        rhr_per_r=rng.uniform(1.0, 3.0), readings=rng.choice(READINGS_CHOICES),
        birth=date(rng.randint(1960, 2004), rng.randint(1, 12), 15), plan_scale=rng.uniform(0.5, 1.5))
    return traits, rng.uniform(0.15, 0.45)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for k, i in enumerate(order):
            r[i] = k
        return r
    return study.corr(ranks(xs), ranks(ys))


def person_summary(traits: synthetic.Traits, noise: float, days: int, seed: int) -> dict:
    p = synthetic.make_person(days, seed, noise, traits)
    rows = study.score(p.inputs, p.first, p.last)
    rec = [r["recovery"] for r in rows if r["recovery"] is not None]
    wz = [r["details"]["recovery"]["weighted_z"] for r in rows if r["recovery"] is not None]
    trimp = [r["trimp"] for r in rows if r.get("trimp") is not None and r["trimp"] >= 40]   # training days
    k = statistics.median(trimp) / -math.log(1 - STRAIN_REF / P.STRAIN_MAX) if trimp else None
    strain_train = [r["strain"] for r in rows if r.get("trimp") is not None and r["trimp"] >= 40 and r["strain"] is not None]
    return {"hrv_ms": traits.hrv_ms, "hrv_per_r": traits.hrv_per_r, "rhr_bpm": traits.rhr_bpm, "rhr_per_r": traits.rhr_per_r,
            "reading_noise": noise, "readings": statistics.fmean(traits.readings),
            "age": (synthetic.START - traits.birth).days / 365.25, "plan_scale": traits.plan_scale,
            "recovery_mean": statistics.fmean(rec), "recovery_sd": statistics.stdev(rec), "weighted_z_sd": statistics.stdev(wz),
            "green": sum(study.zone(x) == "green" for x in rec) / len(rec),
            "red": sum(study.zone(x) == "red" for x in rec) / len(rec),
            "vs_truth": study.validity(rows, p.readiness)["recovery_vs_truth"],
            "strain_training_day": statistics.median(strain_train) if strain_train else None, "k_if_calibrated": k}


def run(n: int = 40, days: int = 180, seed: int = 1) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        traits, noise = draw(rng)
        out.append(person_summary(traits, noise, days, seed * 1000 + i))
    return out


OUTCOMES = ("recovery_mean", "recovery_sd", "green", "red", "vs_truth", "weighted_z_sd")


def summary(people: list[dict]) -> dict:
    """Spread of each outcome across persons (min, quartiles, max) and its Spearman correlation with each trait."""
    def q(v):
        v = sorted(x for x in v if x is not None)
        qs = statistics.quantiles(v, n=4)
        return (round(v[0], 3), round(qs[0], 3), round(qs[1], 3), round(qs[2], 3), round(v[-1], 3))
    out = {"n": len(people), "spread": {}, "spearman": {}}
    for o in (*OUTCOMES, "strain_training_day", "k_if_calibrated"):
        out["spread"][o] = q([p[o] for p in people])
    for o in OUTCOMES:
        out["spearman"][o] = {t: _spearman([p[t] for p in people], [p[o] for p in people]) for t in TRAITS}
    return out


def markdown(people: list[dict], days: int, seed: int) -> str:
    s = summary(people)
    out = [f"# Synthetic population ({s['n']} persons × {days} days, seed {seed})", "",
           "## Spread across persons (min / Q1 / median / Q3 / max)", "", "| outcome | spread |", "|---|---|"]
    out += [f"| {k} | {' / '.join(str(x) for x in v)} |" for k, v in s["spread"].items()]
    out += ["", "## Spearman correlation of each outcome with each trait", "",
            "| outcome | " + " | ".join(TRAITS) + " |", "|---|" + "---|" * len(TRAITS)]
    out += [f"| {o} | " + " | ".join(str(s["spearman"][o][t]) for t in TRAITS) + " |" for o in OUTCOMES]
    return "\n".join(out) + "\n"
