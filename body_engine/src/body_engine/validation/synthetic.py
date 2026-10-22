"""A synthetic person for the validation study. Everything here is made up; no real data is used or imitated.

A latent daily readiness state r (mean 0, roughly unit variance) drives the nightly physiology, so the study can check
how well the scores track a known truth when the inputs are sampled the way an Apple Watch samples them:
1–3 background HRV readings a night, a heart-rate reading every 4–5 minutes asleep and every 5 minutes awake,
denser readings during workouts. The generative model below is our own assumption. Agreement with it shows that the
scoring pipeline is internally consistent and how much sampling noise costs; it is not evidence of real-world validity.

Model (wake date d):
- workouts follow a weekly plan, each skipped with probability 0.15;
- r_d = PHI × r_{d−1} − LOAD_EFFECT × (yesterday's workout minutes × intensity, standardised) − short-sleep effect + noise,
  plus one made-up 3-day illness (r − 2.5, resting heart rate + 6 bpm);
- night: true ln HRV = ln(HRV_MS) + HRV_PER_R × r; each reading adds N(0, reading_noise) (the Apple single-reading noise);
  true resting heart rate = RHR_BPM − RHR_PER_R × r; respiratory rate 14.5 − 0.3 r.
"""
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..scores.inputs import Inputs, Night

TZ = ZoneInfo("Europe/London")
BIRTH = date(1991, 3, 1)
START = date(2026, 1, 5)
# weekday -> (minutes, average heart rate); 0 = Monday
PLAN = {0: (60, 120), 1: (40, 152), 3: (60, 122), 5: (75, 145), 6: (90, 112)}
SKIP_P = 0.15
PHI, LOAD_EFFECT, SHORT_SLEEP_EFFECT, NOISE = 0.6, 0.35, 0.4, 0.7
HRV_MS, HRV_PER_R = 45.0, 0.18
RHR_BPM, RHR_PER_R = 50.0, 2.0
ILLNESS_DAY = 100


@dataclass
class Person:
    inputs: Inputs
    first: date
    last: date
    readiness: dict[date, float] = field(default_factory=dict)
    illness: set[date] = field(default_factory=set)


def _stages(rng: random.Random, start: datetime, minutes: float) -> list[tuple[datetime, datetime, str]]:
    """90-minute cycles: core, deep (long early in the night), REM; a short awakening between cycles now and then."""
    out, t, end, cycle = [], start, start + timedelta(minutes=minutes), 0
    while t < end:
        deep = 25 if cycle < 3 else 5
        for stage, m in (("core", 65 - deep), ("deep", deep), ("rem", 20 + 5 * min(cycle, 3))):
            b = min(t + timedelta(minutes=m), end)
            if b > t:
                out.append((t, b, stage))
            t = b
        if t < end and rng.random() < 0.35:
            b = min(t + timedelta(minutes=rng.uniform(2, 12)), end)
            out.append((t, b, "awake"))
            t = b
        cycle += 1
    return out


def make_person(days: int = 180, seed: int = 7, reading_noise: float = 0.30) -> Person:
    """days: scored days (the person starts wearing the watch on START); reading_noise: SD of a single HRV reading on the ln scale."""
    rng = random.Random(seed)
    inp = Inputs(TZ, BIRTH)
    p = Person(inp, START, START + timedelta(days=days - 1))
    load_prev, r = 0.0, 0.0
    load_mean, load_sd = 45.0, 45.0     # rough scale of minutes × intensity below, for standardising
    for i in range(days):
        d = START + timedelta(days=i)
        sick = ILLNESS_DAY <= i < ILLNESS_DAY + 3
        hours = min(9.5, max(4.5, rng.gauss(7.2, 0.8)))
        r = PHI * r - LOAD_EFFECT * (load_prev - load_mean) / load_sd \
            - SHORT_SLEEP_EFFECT * max(0.0, 7.0 - hours) + rng.gauss(0, NOISE) - (2.5 if sick else 0.0)
        p.readiness[d] = r
        if sick:
            p.illness.add(d)

        # ---- the night that ends on d
        bed = datetime.combine(d - timedelta(days=1), time(23, 30), TZ) + timedelta(minutes=rng.gauss(0, 45))
        segs = _stages(rng, bed, hours * 60)
        wake = segs[-1][1]
        mins = {k: sum((b - a).total_seconds() / 60 for a, b, s in segs if s == k) for k in ("core", "deep", "rem", "awake")}
        inp.nights.append(Night(d, bed, wake, mins["core"] + mins["deep"] + mins["rem"], mins["deep"], mins["rem"],
                                mins["awake"], "synthetic"))
        inp.segments += segs
        rhr = RHR_BPM - RHR_PER_R * r + rng.gauss(0, 1.0) + (6.0 if sick else 0.0)
        t = bed
        while t < wake:
            frac = (t - bed) / (wake - bed)
            inp.hr.append((t, rhr + 4 * abs(frac - 0.6) + abs(rng.gauss(0, 2.5))))
            t += timedelta(minutes=rng.choice((4, 5)))
        ln_true = math.log(HRV_MS) + HRV_PER_R * r
        for _ in range(rng.choice((1, 2, 2, 3))):
            at = bed + (wake - bed) * rng.uniform(0.1, 0.9)
            inp.hrv.append((at, math.exp(ln_true + rng.gauss(0, reading_noise))))
        for k in (1, 3):
            inp.resp.append((bed + timedelta(hours=k), 14.5 - 0.3 * r + rng.gauss(0, 0.4)))
        inp.daily_rhr[d] = rhr + 6

        # ---- the day d: awake readings every 5 minutes, a workout at 18:00 per the plan
        load_prev = 0.0
        plan = PLAN.get(d.weekday())
        w0 = w1 = None
        if plan and rng.random() > SKIP_P:
            minutes, avg = plan
            w0 = datetime.combine(d, time(18), TZ)
            w1 = w0 + timedelta(minutes=minutes)
            t = w0
            while t < w1:
                inp.hr.append((t, avg + rng.gauss(0, 8)))
                t += timedelta(seconds=30)
            load_prev = minutes * max(0.0, (avg - 90) / 30)
        t, bedtime = wake + timedelta(minutes=5), datetime.combine(d, time(23, 0), TZ)
        while t < bedtime:
            if not (w0 and w0 <= t < w1 + timedelta(minutes=5)):
                inp.hr.append((t, 72 + rng.gauss(0, 6)))
            t += timedelta(minutes=5)
    inp.hr.sort()
    inp.hrv.sort()
    inp.resp.sort()
    inp.segments.sort()
    return p
