"""Golden cases: fixed synthetic data -> every public scoring function -> one JSON-serializable dict.

The same cases run against two module sets: the `openbodyscore` package (tests/test_golden.py) and the old
`body_engine.*` import paths (body_engine/tests/test_openbodyscore_golden.py). The golden file was written by running
this module against the scoring code *before* it moved into the package (`python golden_cases.py --write`), so a
match proves the extraction changed no number.

The synthetic person mirrors quickstart/demo_data.py (made up, 60 days, 4 training days a week); standard library only.
"""
import json
import math
import random
import sys
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

GOLDEN = Path(__file__).parent / "golden" / "scores.json"
TZ = timezone(timedelta(hours=10))
FIRST = date(2026, 8, 1)
DAYS = 60
BIRTH = date(1990, 1, 1)
# Weekday -> (workout minutes, average HR); 0 = Monday
PLAN = {0: (60, 118), 1: (40, 152), 3: (60, 120), 5: (75, 145), 6: (90, 112)}


def _at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), TZ)


def _stages(rng: random.Random, start: datetime, minutes: int) -> list[tuple[datetime, datetime, str]]:
    out, t, end, cycle = [], start, start + timedelta(minutes=minutes), 0
    if rng.random() < 0.6:   # sleep latency: awake before the first asleep segment
        out.append((t, t + timedelta(minutes=rng.randint(3, 15)), "awake"))
        t = out[-1][1]
    while t < end:
        frac = (t - start).total_seconds() / (minutes * 60)
        parts = [("core", max(10, int(rng.gauss(25, 4)))), ("deep", max(5, int(rng.gauss(28 * (1 - frac), 5)))),
                 ("core", max(10, int(rng.gauss(25, 4)))), ("rem", max(5, int(rng.gauss(10 + 25 * frac, 5))))]
        if cycle and rng.random() < 0.5:
            parts.insert(0, ("awake", rng.randint(2, 12)))
            if rng.random() < 0.3:   # adjacent awake segments merge into one wake-up
                parts.insert(1, ("awake", rng.randint(1, 4)))
        for stage, m in parts:
            stop = min(t + timedelta(minutes=m), end)
            if stop > t:
                out.append((t, stop, stage))
            t = stop
        cycle += 1
    return out


def _beats(rng: random.Random, mean_rr_ms: float, n: int) -> tuple[list[float], list[bool]]:
    """Beat times (s) of one ~1-minute HRV reading with an ectopic beat and an occasional gap."""
    t, beats, gaps = 0.0, [0.0], [False]
    for i in range(1, n):
        rr = rng.gauss(mean_rr_ms, 45)
        if i == n // 3:
            rr *= 0.55   # ectopic beat
        t += rr / 1000
        beats.append(round(t, 3))
        gaps.append(i == n // 2 and rng.random() < 0.5)
    return beats, gaps


def generate(seed: int = 7) -> dict:
    """60 days of nights and days for one made-up person."""
    rng = random.Random(seed)
    fatigue, nights, days = 0.0, [], []
    for i in range(DAYS):
        d = FIRST + timedelta(days=i)
        wd, trend = d.weekday(), i / DAYS
        late = 1 if wd in (5, 6) else 0
        bed = _at(d - timedelta(days=1), 23, 15) + timedelta(minutes=rng.gauss(25 + 40 * late, 35))
        minutes = int(max(240, min(600, rng.gauss(445 - 25 * fatigue + 20 * late, 45))))
        segs = _stages(rng, bed, minutes)
        wake = segs[-1][1]
        rhr = 58 - 3 * trend + 3.5 * fatigue + rng.gauss(0, 1.2)
        hrv = 52 + 8 * trend - 9 * fatigue + rng.gauss(0, 5)
        hr, t = [], bed
        while t < wake:
            hr.append((t, float(round(rhr + 2 + rng.gauss(0, 2.5)))))
            t += timedelta(minutes=rng.choice((3, 4, 5, 5, 6, 9)))
        hrv_r = [(bed + timedelta(minutes=60 + k * 90), round(max(15.0, hrv + rng.gauss(0, 6)), 1)) for k in range(4)]
        resp = [round(14.5 + 0.6 * fatigue + rng.gauss(0, 0.4), 1) for _ in range(6)]
        spo2 = [round(min(100.0, rng.gauss(96.5, 1.2 + 2 * (i % 17 == 0))), 1) for _ in range(6)]
        temp = round(35.6 + 0.2 * fatigue + rng.gauss(0, 0.15) + (0.9 if i % 23 == 22 else 0), 2)
        beats = [_beats(rng, 60000 / (rhr + 4), rng.randint(40, 70)) for _ in range(2)]
        nights.append({"wake_date": d, "segments": segs, "hr": hr, "hrv": hrv_r, "resp": resp, "spo2": spo2,
                       "temp": temp, "beats": beats})

        plan = PLAN.get(wd) if rng.random() > 0.1 else None
        w0 = w1 = None
        if plan:
            w0 = _at(d, 18 if wd < 5 else 10) + timedelta(minutes=rng.randint(-30, 45))
            w1 = w0 + timedelta(minutes=int(plan[0] * rng.uniform(0.85, 1.15)))
        dhr, steps, dhrv = [], [], []
        t = max(wake, _at(d, 7))
        worn = not (i % 19 == 18)   # a day the watch was mostly off
        stop = _at(d, 23) if worn else _at(d, 12)
        while t < stop:
            if w0 and w0 <= t < w1:
                prog = (t - w0) / (w1 - w0)
                v, step = plan[1] + 12 * math.sin(math.pi * prog) + rng.gauss(0, 7), 1
            else:
                v, step = rhr + 22 + rng.gauss(0, 9) + (15 if t.hour == 15 and i % 5 == 0 else 0), rng.choice((2, 4, 6))
            dhr.append((t, float(round(max(rhr, v)))))
            t += timedelta(minutes=step)
        for h in range(7, 23):
            for q in range(0, 60, 15):
                n = max(0, int(rng.gauss(140, 120)))
                if w0 and w0.hour == h and wd in (1, 5, 6):
                    n += 1000
                steps.append((_at(d, h, q), float(n)))
        for h in (9, 13, 17, 21):
            dhrv.append((_at(d, h, 20), round(max(12.0, hrv - 6 + rng.gauss(0, 8)), 1)))
        sessions = []
        if plan and wd in (0, 3):
            sessions.append({"date": d.isoformat(), "name": "Strength", "total_duration_minutes": None if i % 2 else 60,
                             "exercises": [
                                 {"category": "strength", "primary_muscles": ["chest", "triceps"], "duration_minutes": 20,
                                  "sets": [{"type": "warm-up", "reps": 10, "weight_kg": 20},
                                           *({"reps": 8, "weight_kg": round(60 + 2 * trend * 10, 1), "rpe": 8} for _ in range(3))]},
                                 {"category": "strength", "primary_muscles": ["back"], "duration_minutes": 15,
                                  "sets": [{"reps": 10, "weight_kg": None, "rpe": 7}, {"reps": None, "duration_s": 45}]},
                                 {"category": "cardio", "duration_minutes": 10, "sets": [{"duration_s": 600, "rpe": 6}]}]})
        days.append({"date": d, "hr": dhr, "steps": steps, "hrv": dhrv, "sessions": sessions,
                     "workout": (w0, w1) if w0 else None})
        fatigue = max(0.0, min(2.0, fatigue * 0.55 + (plan[0] * (plan[1] - 60) / 4000 if plan else 0)
                               + rng.gauss(0, 0.15)))
    return {"nights": nights, "days": days}


# ---------------------------------------------------------------- cases
def _jsonable(x):
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if isinstance(x, timedelta):
        return x.total_seconds()
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def _error(fn, *args):
    try:
        fn(*args)
    except Exception as e:   # noqa: BLE001 -- the type and message are part of the golden output
        return [type(e).__name__, str(e)]
    return None


def sleep_cases(m, data) -> dict:
    S, P = m["sleep"], m["params"]
    out = {"duration": {str(h): S.duration_score(h) for h in (3, 4.5, 5, 6, 6.99, 7, 8, 9, 9.5, 10, 11, 12, 14)},
           "stages": [S.stages_score(a, b) for a, b in ((0, 0), (45, 30), (90, 90), (120, 10), (-5, 200))],
           "invalid": [_error(S.sleep_score, 0, 0, 0, _at(FIRST, 23), [], 0, []),
                       _error(S.sleep_score, 1441, 0, 0, _at(FIRST, 23), [], 0, [])],
           "nights": []}
    bedtimes, actual = [], []
    trimps = strain_trimps(m, data)
    for i, n in enumerate(data["nights"]):
        segs = n["segments"]
        awake, wakes = S.waso(segs)
        asleep = sum((b - a).total_seconds() / 60 for a, b, s in segs if s != "awake")
        deep = sum((b - a).total_seconds() / 60 for a, b, s in segs if s == "deep")
        rem = sum((b - a).total_seconds() / 60 for a, b, s in segs if s == "rem")
        bed = next(a for a, b, s in segs if s != "awake")
        history = bedtimes[-P.CONSISTENCY_WINDOW_NIGHTS:]
        debt = S.sleep_debt(list(reversed(actual))[:P.DEBT_NIGHTS] + ([None] if i % 7 == 3 else []))
        prev_strain = trimps[i - 1]["strain"] if i else None
        need = S.sleep_need(debt, prev_strain)
        out["nights"].append({
            "waso": [awake, wakes], "score": S.sleep_score(asleep, deep, rem, bed, history, awake, wakes),
            "hours_past_noon": S.hours_past_noon(bed), "debt": debt, "need": need,
            "performance": S.performance(asleep, need["need"]),
            "consistency_empty": S.consistency_score([], bed),
            "interruptions": S.interruptions_score(awake + 30 * (i % 3), wakes + [6.0] * (i % 4))})
        bedtimes.append(bed)
        actual.append(asleep if i % 11 != 5 else None)
    out["waso_only_awake"] = S.waso([(_at(FIRST, 1), _at(FIRST, 2), "awake")])
    return out


def recovery_cases(m, data) -> dict:
    R, P = m["recovery"], m["params"]
    out = {"errors": [_error(R.recovery_score, {"rhr": 1.0})],
           "baseline_short": R.baseline([1.0] * (P.MIN_BASELINE_NIGHTS - 1)),
           "zscore": [R.zscore(x, 50, sd, 0.05) for x, sd in ((60, 5), (40, 0.01), (500, 1), (-500, 1))],
           "sparse": R.lowest_window([(_at(FIRST, 1), 60.0), (_at(FIRST, 2), 55.0)], _at(FIRST, 0), _at(FIRST, 3)),
           "empty": R.lowest_window([], _at(FIRST, 0), _at(FIRST, 3)),
           "spo2_empty": R.spo2_flag([]), "spo2_low": [R.spo2_flag([95.0, 89.0, 88.5, 97.0]), R.spo2_flag([93.0, 93.5, 95.0])],
           "temp_short": R.wrist_temp_flag(36.5, [35.5] * (P.MIN_TEMP_BASELINE - 1)), "nights": []}
    hist = {"hrv": [], "rhr": [], "resp": [], "temp": []}
    for i, n in enumerate(data["nights"]):
        start, end = n["segments"][0][0], n["segments"][-1][1]
        hrv_ln, n_hrv = R.night_hrv_ln(n["hrv"], start, end)
        rhr, at = R.lowest_window(n["hr"], start, end)
        mean_only = R.lowest_window_mean(n["hr"], start, end)
        resp = sorted(n["resp"])[len(n["resp"]) // 2]
        z, bases = {}, {}
        for key, x in (("hrv", hrv_ln), ("rhr", rhr), ("resp_rate", resp)):
            b = R.baseline(hist[{"resp_rate": "resp"}.get(key, key)][-P.BASELINE_DAYS:])
            bases[key] = b
            if b and x is not None:
                z[key] = P.RECOVERY_SIGNS[key] * R.zscore(x, b[0], b[1], P.SD_FLOOR[key])
        if i % 4 == 0 and "hrv" in z:
            z["sleep_performance"] = 0.3 * ((i % 9) - 4) / 4
        row = {"hrv_ln": [hrv_ln, n_hrv], "rhr": [rhr, at], "rhr_mean": mean_only, "baselines": bases, "z": z,
               "score": R.recovery_score(z) if "hrv" in z else None,
               "illness": R.illness_warning(rhr, bases["rhr"] and bases["rhr"][0], hrv_ln,
                                            bases["hrv"] and bases["hrv"][0]),
               "illness_forced": R.illness_warning(70.0, 60.0, math.log(30), math.log(50)),
               "temp": R.wrist_temp_flag(n["temp"], hist["temp"][-P.BASELINE_DAYS:]),
               "spo2": R.spo2_flag(n["spo2"])}
        out["nights"].append(row)
        hist["hrv"].append(hrv_ln)
        hist["rhr"].append(rhr)
        hist["resp"].append(resp)
        hist["temp"].append(n["temp"])
    return out


def strain_trimps(m, data) -> list[dict]:
    L, P = m["strain"], m["params"]
    hist = L.HrHistogram()
    window: list[Counter] = []
    rows = []
    for i, d in enumerate(data["days"]):
        samples = data["nights"][i]["hr"] + d["hr"]
        c = Counter(int(v) for _, v in samples)
        hist.add(c)
        window.append(c)
        if len(window) > 30:
            hist.add(window.pop(0), -1)
        p995 = hist.percentile(P.HRMAX_PERCENTILE)
        age = L.age_on(BIRTH, d["date"])
        hr_max = L.hrmax(age, p995)
        minutes = L.sample_minutes([t for t, _ in samples])
        trimp, zones = L.edwards_trimp(samples, minutes, hr_max)
        rows.append({"age": age, "p995": p995, "hrmax": hr_max, "minutes_total": sum(minutes), "trimp": trimp,
                     "zones": zones, "strain": L.strain(trimp), "strain_k100": L.strain(trimp, 100.0)})
    return rows


def strain_cases(m, data) -> dict:
    L = m["strain"]
    rows = strain_trimps(m, data)
    trimps = [None if i % 13 == 12 else r["trimp"] for i, r in enumerate(rows)]
    empty = L.HrHistogram()
    return {"days": rows, "load": L.training_load(trimps), "load_empty": L.training_load([None, None]),
            "formula": [L.hrmax_formula(a) for a in (20, 36.5, 70)],
            "age_on": [L.age_on(date(2000, 2, 29), date(2026, 2, 28)), L.age_on(date(2000, 2, 29), date(2026, 3, 1))],
            "zone_weight": [L.zone_weight(hr, 190) for hr in (80, 95, 114, 133, 152, 171, 200)],
            "percentile_empty": empty.percentile(99.5)}


def muscle_cases(m, data) -> dict:
    sessions = [s for d in data["days"] for s in d["sessions"]]
    sessions.append(dict(sessions[0], name="Second session"))   # two sessions on one day add up
    return m["muscle"].daily_muscle_load(sessions)


def healthspan_cases(m, data) -> dict:
    HS, H = m["healthspan"], m["healthspan_params"]
    as_of = FIRST + timedelta(days=DAYS - 1)
    vals = {d["date"]: sum(v for _, v in d["steps"]) for d in data["days"]}
    vals[FIRST - timedelta(days=400)] = 1e9   # outside the window: ignored
    hist = [(as_of - timedelta(weeks=k), None if k == 5 else 0.8 - 0.03 * k + 0.1 * math.sin(k)) for k in range(40, -1, -1)]
    return {
        "weighted_mean": HS.weighted_mean(vals, as_of), "weighted_mean_empty": HS.weighted_mean({}, as_of),
        "interp": [HS.interp_log_hr(x, H.SRI_KNOTS) for x in (50, 65.1, 70, 81, 88, 95)],
        "self_report": [HS.self_report_hours(h) for h in (4, 6.5, 9)],
        "sleep": [HS.hr_sleep_duration(h) for h in (4, 5.5, 6.5, 7, 8, 10)],
        "regularity": [HS.hr_regularity(x) for x in (55, 70, 81, 92)],
        "steps": [[HS.hr_steps(s, a) for s in (2000, 5000, 8078, 12000)] for a in (35, 60, 72)],
        "mvpa": [HS.hr_mvpa(x) for x in (0, 75, 150, 300, 900)],
        "strength": [HS.hr_strength(x) for x in (0, 20, 40, 90, 200)],
        "vo2_norm": [[HS.vo2_norm(a, s) for a in (18, 25, 36.5, 90)] for s in ("male", "female", "other")],
        "vo2_pct": [[HS.vo2_percentile(v, a, s) for v in (10, 30, 40.2, 70)] for a, s in ((25, "male"), (52, "female"))]
        + [HS.vo2_percentile(40, 30, "other")],
        "vo2": [HS.hr_vo2(v, 39.7) for v in (20, 35, 39.7, 45, 60)],
        "rhr": [HS.hr_rhr(x) for x in (40, 45, 65, 80)],
        "bmi": [HS.hr_bmi(x) for x in (16, 22, 25, 31, 50)],
        "whtr": [HS.hr_whtr(x) for x in (0.4, 0.5, 0.55, 0.9)],
        "years": [HS.years_from_hr(x) for x in (0.2, 0.9, 1.0, 1.2, 5.0)],
        "combine": [HS.combine([{"years": y} for y in ys]) for ys in ([], [1.0, -0.5], [4, 4, 4, 4, 4])],
        "chrono_age": HS.chrono_age(BIRTH, as_of),
        "smooth": HS.smooth(hist), "smooth_2": HS.smooth(hist, 2),
        "pace": HS.pace(hist, as_of), "pace_short": HS.pace(hist[-6:], as_of),
        "pace_flat": HS.pace([(as_of, 0.5)] * 9, as_of),
        "pace_past": HS.pace(hist, as_of - timedelta(weeks=10)),
    }


def hrv_cases(m, data) -> dict:
    X = m["hrv"]
    out = []
    for n in data["nights"][:20]:
        for beats, gaps in n["beats"]:
            r = X.analyze(beats, gaps)
            out.append({**asdict(r), "artifact_ratio": r.artifact_ratio, "no_gaps": asdict(X.analyze(beats))})
    clock = ["23:59:58.10", "23:59:59.05", "bad", "00:00:00.01", "00:00:00.95", None]
    return {"readings": out, "rr": X.rr_intervals([0, 0.8, 0.8, 1.7, 2.5], [False, False, False, True, False]),
            "clean_none": X.clean([None, 100.0, 5000.0]), "clock": X.beats_from_clock(clock),
            "parse": [X.parse_clock(s) for s in ("01:02:03.5", "x", None)],
            "short": asdict(X.analyze([0.0, 1.0]))}


def stress_cases(m, data) -> dict:
    T = m["stress"]
    sleep = [(n["segments"][0][0], n["segments"][-1][1]) for n in data["nights"]]
    workouts = [d["workout"] for d in data["days"] if d["workout"]]
    hr = sorted(p for n, d in zip(data["nights"], data["days"]) for p in n["hr"] + d["hr"])
    steps = sorted(p for d in data["days"] for p in d["steps"])
    hrv = sorted(p for d in data["days"] for p in d["hrv"])
    calm = T.calm_samples(hr, sleep, workouts, steps)
    calm_hrv = T.calm_samples(hrv, sleep, workouts, steps)
    by_day: dict[date, list] = {}
    for t, v in calm:
        by_day.setdefault(t.astimezone(TZ).date(), []).append((t, v))
    days = []
    for i, d in enumerate(data["days"]):
        dd = d["date"]
        base_vals = [v for k in range(1, T.BASELINE_DAYS + 1) for _, v in by_day.get(dd - timedelta(days=k), [])]
        hr_base = T.robust_baseline(base_vals)
        hrv_base = (3.9, 0.2) if i % 2 else None
        days.append({"base": hr_base, "day": None if hr_base is None else
                     T.score_day(by_day.get(dd, []), calm_hrv, dd, TZ, hr_base, hrv_base)})
    return {"calm_n": len(calm), "calm_first": calm[:20], "calm_hrv": calm_hrv[:20], "days": days,
            "bands": [T.band(x) for x in (None, 0.0, 0.99, 1.0, 2.5)],
            "empty_day": T.score_day([], [], FIRST, TZ, (60.0, 5.0), None)}


def compute(m: dict) -> dict:
    """m: module name -> module (params, sleep, recovery, strain, muscle, healthspan, healthspan_params, hrv, stress)."""
    data = generate()
    out = {"sleep": sleep_cases(m, data), "recovery": recovery_cases(m, data), "strain": strain_cases(m, data),
           "muscle": muscle_cases(m, data), "healthspan": healthspan_cases(m, data), "hrv": hrv_cases(m, data),
           "stress": stress_cases(m, data),
           "versions": {"scores": m["params"].ALGO_VERSION, "healthspan": m["healthspan_params"].ALGO_VERSION,
                        "hrv": m["hrv"].ALGO, "stress": m["stress"].STRESS_VERSION}}
    return json.loads(json.dumps(_jsonable(out)))   # floats round-trip exactly through repr


def package_modules() -> dict:
    from openbodyscore import healthspan, healthspan_params, hrv, muscle, params, recovery, sleep, strain, stress
    return {"params": params, "sleep": sleep, "recovery": recovery, "strain": strain, "muscle": muscle,
            "healthspan": healthspan, "healthspan_params": healthspan_params, "hrv": hrv, "stress": stress}


def body_engine_modules() -> dict:
    from body_engine.insights import healthspan, healthspan_params, stress
    from body_engine.rawdata import hrv
    from body_engine.scores import muscle, params, recovery, sleep, strain
    return {"params": params, "sleep": sleep, "recovery": recovery, "strain": strain, "muscle": muscle,
            "healthspan": healthspan, "healthspan_params": healthspan_params, "hrv": hrv, "stress": stress}


if __name__ == "__main__":
    # --write: regenerate the golden file from the old body_engine paths (only ever done before the extraction)
    if sys.argv[1:] != ["--write"]:
        sys.exit("usage: python golden_cases.py --write   (run inside the body_engine environment)")
    GOLDEN.write_text(json.dumps(compute(body_engine_modules()), indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {GOLDEN}")
