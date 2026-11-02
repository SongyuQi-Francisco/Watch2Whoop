"""Cross-language golden cases (issue #340 N1): one set of synthetic inputs that both the Python code and the JS bundle run.

Standard library only. Two users:
- body_engine/tests/test_js_golden.py runs the Python code (body_engine: scores/engine.score_days, insights/stress.score_range,
  insights/healthspan_store.snapshot / weekly, trends/calc + series helpers, insights/coach) on the native inputs built here
  and compares with test/golden/*.json (rewrite: BODY_WRITE_GOLDEN=1).
- the Node test (test/golden.test.js) runs `python3 cases.py --dump FILE` and replays the same inputs through the bundle.
  It also replays packages/openbodyscore/tests/golden_cases.py (scores.json, healthspan_v12.json) from `base`.
In the dump, instants are epoch milliseconds with the microseconds as fraction (exact after JSON), dates ISO strings.
Sums here use math.fsum: built-in sum() of floats changed in Python 3.12 (compensated), and the inputs must not depend on
which Python writes the dump.

The synthetic person is golden_cases.generate() (made up, 60 days, 4 training days a week), with a few gaps added so every
branch of the engine runs (no sleep, sleep without stages, no heart rate, a nap, a Shortcut-only day, wrist temperature dated
before the night).
"""
import json
import math
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "openbodyscore" / "tests"))
import golden_cases as G  # noqa: E402

TZ = G.TZ
TZ_MINUTES = 600
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
GOLDEN = HERE / "golden"


def ms(t: datetime) -> float:
    return ((t - EPOCH) // timedelta(microseconds=1)) / 1000


def dump(x):
    """Native -> JSON for the Node test: instants as milliseconds, dates as ISO strings, sets sorted, tuples as lists."""
    if isinstance(x, datetime):
        return ms(x)
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, timedelta):
        return x // timedelta(microseconds=1)
    if isinstance(x, dict):
        return {(k.isoformat() if isinstance(k, date) else str(k)): dump(v) for k, v in x.items()}
    if isinstance(x, (set, frozenset)):
        return [dump(v) for v in sorted(x)]
    if isinstance(x, (list, tuple)):
        return [dump(v) for v in x]
    return x


def _mins(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60


# ---------------------------------------------------------------- engine (scores/engine.score_days)
def engine_case(data: dict) -> dict:
    nights, segments, hr, hrv, resp, spo2, temp = [], [], [], [], [], [], []
    last = G.FIRST + timedelta(days=G.DAYS - 1)
    for i, n in enumerate(data["nights"]):
        segs = n["segments"]
        start, end = segs[0][0], segs[-1][1]
        if i in (30, 45):   # 30: only a non-watch sleep that night; 45: no sleep at all
            continue
        asleep = math.fsum(_mins(a, b) for a, b, s in segs if s != "awake")
        nights.append({"wake_date": n["wake_date"], "start": start, "end": end, "asleep_min": asleep,
                       "deep_min": math.fsum(_mins(a, b) for a, b, s in segs if s == "deep"),
                       "rem_min": math.fsum(_mins(a, b) for a, b, s in segs if s == "rem"),
                       "awake_min": math.fsum(_mins(a, b) for a, b, s in segs if s == "awake"),
                       "source": "ow" if i % 2 else "shortcut"})
        if i % 10 != 7:   # every tenth night has no stage detail
            segments += segs
        if i % 9 == 4:    # an afternoon nap the day before
            nap0 = datetime.combine(n["wake_date"] - timedelta(days=1), time(15, 10), TZ)
            nights.append({"wake_date": n["wake_date"] - timedelta(days=1), "start": nap0, "end": nap0 + timedelta(minutes=40),
                           "asleep_min": 35.0, "deep_min": 0.0, "rem_min": 0.0, "awake_min": 5.0, "source": "ow"})
        if i % 17 != 11:  # some nights have no raw heart rate (falls back to the daily resting heart rate)
            hr += n["hr"]
        hrv += n["hrv"]
        resp += [(start + timedelta(minutes=30 + 60 * k), v) for k, v in enumerate(n["resp"])]
        spo2 += [(start + timedelta(minutes=45 + 50 * k), v) for k, v in enumerate(n["spo2"])]
        temp.append((start - timedelta(hours=3) if i % 3 == 0 else start + timedelta(hours=1), n["temp"]))
    for d in data["days"]:
        hr += d["hr"]
        hrv += d["hrv"]
    # heart-rate coverage cases by local date: none (one of them a Shortcut-only day), 3 hours without a workout,
    # 4 hours around a workout (scored with the "may be low" flag)
    hours = {G.FIRST + timedelta(days=25): (), G.FIRST + timedelta(days=37): (),
             G.FIRST + timedelta(days=41): (17, 18, 19), G.FIRST + timedelta(days=43): (9, 10, 11, 12)}
    hr = [p for p in hr if (lt := p[0].astimezone(TZ)).date() not in hours or lt.hour in hours[lt.date()]]
    nights.sort(key=lambda n: n["start"])
    inputs = {"tz": TZ_MINUTES, "birth_date": G.BIRTH, "hrv_kind": "sdnn", "nights": nights,
              "non_watch_dates": [G.FIRST + timedelta(days=30)], "segments": sorted(segments),
              "hr": sorted(hr), "hrv": sorted(hrv), "resp": sorted(resp), "spo2": sorted(spo2), "temp": sorted(temp),
              "daily_rhr": {d["date"]: 55.0 + (i % 5) for i, d in enumerate(data["days"])},
              "shortcut_dates": [G.FIRST + timedelta(days=18), G.FIRST + timedelta(days=37)],
              "muscle_sessions": [s for d in data["days"] for s in d["sessions"]], "muscle_error": None}
    runs = [{"first": G.FIRST, "last": last, "today": last},
            {"first": G.FIRST + timedelta(days=20), "last": last - timedelta(days=3), "today": last}]
    return {"inputs": inputs, "runs": runs}


# ---------------------------------------------------------------- stress (insights/stress.score_range)
def stress_case(data: dict) -> dict:
    sleep = [(n["segments"][0][0], n["segments"][-1][1]) for n in data["nights"]]
    workouts = [d["workout"] for d in data["days"] if d["workout"]]
    hr = sorted(p for n, d in zip(data["nights"], data["days"]) for p in n["hr"] + d["hr"])
    steps = sorted(p for d in data["days"] for p in d["steps"])
    hrv = sorted(p for d in data["days"] for p in d["hrv"])
    hrv_night = {n["wake_date"]: math.log(math.fsum(v for _, v in n["hrv"]) / len(n["hrv"])) for n in data["nights"]}
    last = G.FIRST + timedelta(days=G.DAYS - 1)
    return {"tz": TZ_MINUTES, "sleep": sleep, "workouts": workouts, "hr": hr, "steps": steps, "hrv": hrv,
            "hrv_night": hrv_night, "shortcut": [G.FIRST + timedelta(days=18)],
            "runs": [{"start": G.FIRST + timedelta(days=10), "end": last, "steps": True},
                     {"start": G.FIRST + timedelta(days=40), "end": last, "steps": False}]}


# ---------------------------------------------------------------- body age (insights/healthspan_store.snapshot / weekly)
def healthspan_case() -> dict:
    """220 days of made-up daily values (deterministic, no randomness): enough for every item and the pace of aging."""
    origin = G.FIRST - timedelta(days=160)
    days = [origin + timedelta(days=k) for k in range(220)]
    sleep, steps, rhr, vo2, weight, zones, strength, workouts, intervals, wake = {}, {}, {}, {}, {}, {}, {}, {}, [], []
    for k, d in enumerate(days):
        s = math.sin(k / 9)
        if k % 11 != 5:
            bed = datetime.combine(d - timedelta(days=1), time(23, 0), TZ) + timedelta(minutes=round(40 * s + 25 * math.cos(k / 4)))
            end = bed + timedelta(minutes=round(430 + 30 * math.cos(k / 5)))
            intervals.append((bed, end))
            wake.append(d)
            sleep[d] = (end - bed).total_seconds() / 3600 - 0.4
            if k % 13 == 3:   # a nap the afternoon before
                intervals.append((datetime.combine(d - timedelta(days=1), time(14, 30), TZ),
                                  datetime.combine(d - timedelta(days=1), time(15, 5), TZ)))
        if k % 6 != 2:
            steps[d] = 6500 + 2500 * s + 60 * (k % 7)
        rhr[d] = 61 - k / 40 + 1.5 * math.cos(k / 3)
        if k % 17 == 0:
            vo2[d] = 38.5 + k / 60
        if k % 10 == 0:
            weight[d] = 71.0 - k / 40
        z = [30 + 10 * s, 25 + (k % 5) * 4, 10 + (k % 3) * 6, 4 * (k % 4), 1.5 * (k % 2)]
        zones[d] = [round(x, 1) for x in z]
        if d.weekday() in (0, 3):
            strength[d] = 50.0 + (k % 3) * 10
            workouts[d] = [("traditional_strength_training", strength[d], [5.0, 10.0, 8.0, 2.0, 0.0])]
        elif d.weekday() == 5:
            workouts[d] = [("swimming", 40.0, None), ("walking", 30.0, [10.0, 5.0, 0.0, 0.0, 0.0])]
    mvpa = {d: (z[1] + z[2]) + 2.0 * (z[3] + z[4]) for d, z in zones.items()}
    data = {"birth": G.BIRTH, "sex": "male", "height": 169.0, "goal_kg": 66.0, "notes": ["示例备注"],
            "sleep": sleep, "mvpa": mvpa, "zones": zones, "steps": steps, "rhr": rhr, "vo2": vo2, "weight": weight,
            "waist": {}, "strength": strength, "workouts": workouts, "strength_from": origin + timedelta(days=3),
            "strength_journal": [days[-3], days[-10], days[-12]], "strength_plan": {"days_per_week": 4, "start_date": origin}}
    as_of = [days[29], days[40], days[100], days[-1]]
    variants = {   # changes on top of `data` for one snapshot each: other branches
        "waist": {"waist": {days[-20]: 84.0}},
        "no_birth_no_sex": {"birth": None, "sex": None},
        "female_no_height": {"sex": "female", "height": None, "strength_journal": [], "strength_plan": None},
        "no_steps": {"steps": {}},
        "no_training": {"strength_from": None, "workouts": {}},
    }
    return {"tz": TZ_MINUTES, "data": data, "intervals": intervals, "wake_dates": wake, "origin": origin, "today": days[-1],
            "as_of": as_of, "variants": variants}


# ---------------------------------------------------------------- trends (trends/calc.py, series helpers, insights/coach.py)
def trends_calls(data: dict) -> list[dict]:
    """[{fn, args}]: every Trends function on the synthetic days plus hand-made edge cases."""
    days = [d["date"] for d in data["days"]]
    hrv_ms = {n["wake_date"]: round(math.fsum(v for _, v in n["hrv"]) / len(n["hrv"]), 1) for n in data["nights"]}
    trimp, ctl, vo2, zones, recovery, perf, stress, strain, tsb, aerobic = {}, {}, {}, {}, {}, {}, {}, {}, {}, {}
    c = 60.0
    for i, d in enumerate(data["days"]):
        dd = d["date"]
        if i % 13 != 12:
            w = d["workout"]
            trimp[dd] = round(_mins(*w) * 1.7, 1) if w else 20.0 + 3 * (i % 7)
            c += (trimp[dd] - c) * (1 - math.exp(-1 / 42))
        ctl[dd] = round(c, 1)
        if i % 6 == 0:
            vo2[dd] = round(41.0 + 0.06 * i + 0.4 * math.sin(i), 1)
        zones[dd] = [round(20 + 5 * math.sin(i), 1), round(15 + i % 9, 1), float(i % 11), float(i % 4), 0.5 * (i % 3)]
        if i % 8 != 3:
            recovery[dd] = 25 + (i * 37) % 70
        perf[dd] = round(0.62 + (i % 6) * 0.08, 4)
        if i % 5:
            stress[dd] = round((i % 7) * 0.43, 2)
        strain[dd] = round(4 + (i * 7) % 15 + 0.3 * (i % 4), 2)
        tsb[dd] = round(10 - (i * 11) % 60, 1)
        aerobic[dd] = float((i * 17) % 90)
    nights = [(n["segments"][0][0], n["segments"][-1][1]) for n in data["nights"]]
    end = days[-1]
    # load patterns for the training-status branches: 21 ordinary days, then a different last week
    span = [end - timedelta(days=k) for k in range(40)]
    pattern = lambda base, last7: {d: (last7 if d > end - timedelta(days=7) else base) for d in span}
    hard, easy = pattern(60.0, 200.0), pattern(80.0, 5.0)
    ctl_drop = {end: 40.0, end - timedelta(days=28): 70.0}
    vo2_up = {end - timedelta(days=k): 40.0 + (60 - k) * 0.03 for k in range(0, 60, 5)}
    vo2_down = {d: 80.0 - v for d, v in vo2_up.items()}
    calls = []
    add = lambda fn, *args: calls.append({"fn": fn, "args": list(args)})
    for e in days[13::4]:
        add("hrv_status", hrv_ms, e)
        add("acwr", trimp, e)
        add("rolling_mean", trimp, e, 7)
        add("rolling_mean", trimp, e, 28, False)
        add("slope_per", vo2, e, 60)
        add("load_focus", zones, e)
        add("load_focus", zones, e, 7)
        add("resilience", recovery, perf, stress, e)
        add("resilience", recovery, perf, {}, e)
        add("recovery_time", strain, tsb, ["green", "yellow", "red", None][e.day % 4], e)
    for hs in (None, {"status": "low"}, {"status": "balanced"}):
        add("training_status", trimp, ctl, vo2, hs, end)
    add("training_status", hard, ctl, vo2, None, end)
    add("training_status", easy, ctl_drop, vo2, None, end)
    add("training_status", easy, ctl, vo2, None, end)
    add("training_status", pattern(80.0, 80.0), ctl, vo2_up, None, end)
    add("training_status", pattern(80.0, 80.0), ctl, vo2_down, None, end)
    add("training_status", pattern(80.0, 80.0), ctl, {}, None, end)
    add("training_status", {end: 50.0}, ctl, vo2, None, end)
    add("training_status", pattern(0.0, 0.0), ctl, vo2, None, end)
    add("hrv_status", {}, end)
    add("hrv_status", {**{end - timedelta(days=k): 55.0 + (k % 3) for k in range(7, 60)},
                       **{end - timedelta(days=k): 30.0 for k in range(7)}}, end)
    add("hrv_status", {**{end - timedelta(days=k): 40.0 + (k % 4) for k in range(7, 60)},
                       **{end - timedelta(days=k): 52.0 for k in range(7)}}, end)
    add("slope_per", {end: 1.0, end - timedelta(days=1): 1.0}, end, 60)
    add("slope_per", {end: 1.0, end - timedelta(days=1): 2.0, end - timedelta(days=2): 4.0}, end, 60)
    for z in (None, [], [10.0, 20.0, 30.0, 5.0, 1.0], [0.0, None, 3.5, None, 2.0]):
        add("aerobic_minutes", z)
    add("weekly_sum", aerobic, days)
    add("weekly_sum", aerobic, days[3:12])
    for args in ((None, 80, 0.9), (0.85, None, None), (1.2, 70, 1.3), (0.6, 0, 0.5), (0.95, 88, None)):
        add("sleep_performance_whoop", *args)
    for t in (0.0, 50.0, 145.0, 500.0):
        add("strain_from_trimp", t)
    for args in ((None, None, None), (None, 0.0, 1000.0), (120.0, None, 1500.0), (120.0, 3000.0, 1500.0),
                 (None, 2000.0, 1500.0), (80.0, 2000.0, None)):
        add("combined_strain", *args)
    for args in ((None, None, None), ({}, None, None), ({}, 0.6, None), ({"hrv": -1.5, "rhr": -0.8}, None, 92.0),
                 ({"hrv": 0.4, "rhr": 0.2, "resp_rate": -2.0}, 0.2, 97.0), ({"hrv": -3.0, "rhr": -3.0, "resp_rate": -3.0}, 1.0, 90.0)):
        add("illness_risk", *args)
    add("chronotype", nights)
    add("chronotype", nights[:9])
    add("chronotype", [n for n in nights if n[1].weekday() < 5])
    add("chronotype", [(a - timedelta(hours=5), b - timedelta(hours=5)) for a, b in nights[:20]])
    add("chronotype", [(a + timedelta(hours=4), b + timedelta(hours=6)) for a, b in nights[:20]])
    hours = [{"hour": h, "score": (h % 4) * 0.7, "level": ["low", "low", "medium", "high"][h % 4]} for h in range(6, 22)]
    hours[3]["score"] = None
    for args in ((None, None, 7, [], None, 23), (75, 0.9, 6, hours, 12.5, 23), (None, 1.3, None, hours, None, 15),
                 (10, None, 20, [], 20.0, 23), (90, None, 8, hours, 3.0, 5)):
        add("body_battery", *args)
    add("recovery_time", {}, {}, None, end)
    add("recovery_time", {end - timedelta(days=1): 5.0}, {}, None, end)
    add("recovery_time", {end - timedelta(days=1): 19.0, end - timedelta(days=2): 15.5}, {end - timedelta(days=1): -40.0}, "red", end)
    for args in ((None, 400.0), (100.0, 0.0), (100.0, 400.0), (250.0, 400.0), (380.0, 400.0), (-10.0, 400.0), (900.0, 400.0)):
        add("recovery_index", *args)
    for args in ((None, 120.0), (150.0, 133.5), (160.0, 140.0)):
        add("hr_recovery", *args)
    for b in (None, 8.0, 12.0, 15.0, 20.0, 31.5):
        add("hr_recovery_level", b)
    add("baseline_band", hrv_ms, days[-25:])
    add("baseline_band", {d: v for d, v in hrv_ms.items() if d.day % 2}, days[-10:])
    add("rolling", [None if i % 4 == 1 else float(i * 3 % 17) for i in range(30)])
    add("rolling", [1.0, None, None, 2.0, 3.0], 3, 1)
    add("hr_zone_summary", zones, days[-17:])
    add("hr_zone_summary", {}, days[-5:])
    for v in (None, 0, 33, 34, 66, 67, 100):
        add("recovery_zone", v)
    for args in ((80, None, False), (50, -35.0, False), (20, -10.0, False), (None, None, False), (80, -45.0, True),
                 (70, -31.4, False)):
        add("strain_target", *args)
    return calls


# ---------------------------------------------------------------- the dump for the Node test
def all_cases() -> dict:
    data = G.generate()
    return {"base": data, "engine": engine_case(data), "stress": stress_case(data), "healthspan": healthspan_case(),
            "trends": trends_calls(data)}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--dump":
        sys.exit("usage: python3 cases.py --dump FILE")
    Path(sys.argv[2]).write_text(json.dumps(dump(all_cases()), ensure_ascii=False))
    print(f"wrote {sys.argv[2]}")
