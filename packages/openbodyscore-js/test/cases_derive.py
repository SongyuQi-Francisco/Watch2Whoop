"""Cross-language golden cases of 2.0.0 package M1 (docs/ios-native-2.0.md section 4): the phone's derivation of daily
values and sleep (body_engine rawdata/derive.derive_days <-> src/30_derive.js deriveDays).

Standard library only, synthetic data only (a made-up person; no export, no real values). Used by
- body_engine/tests/test_js_golden_derive.py: runs the Python on these inputs and writes / compares test/golden/derive.json
  (rewrite: BODY_WRITE_GOLDEN=1); the golden file keeps the inputs too, so the iOS test (LocalDeriveTests.swift) can feed
  the same rows through its local tables;
- test/derive.test.js: `python3 cases_derive.py --dump FILE`, then the bundle on the same inputs.

Instants are UTC seconds (what the phone's tables hold), values in the registry's HealthKit units (SpO2 and body fat as
fractions), so the ingest rules (factor, plausible range, zero buckets) run too.

Case `sydney` (62 days over the end of daylight saving time, 2026-04-05) covers: iPhone + watch overlap (heart rate from
both; exercise minutes from both: the largest single source counts), a day with only the Shortcut's hourly sums, a day
whose basal energy is < 50 % of the usual one, a day whose workouts burned more than its active energy, a day whose heart
rate samples were all deleted (no app value: another source's is chosen), samples outside the plausible range, zero
buckets, sleep with a gap > 60 min (two sessions) and > 2 h (two OW sessions), a nap, an iPhone in-bed copy of a watch
night (dropped), an in-bed-only night (counted as asleep), a third-party app night, the same sample twice, a night across
the DST change. Case `new_york` (30 days over the start of DST, 2026-03-08) checks the other direction; its inputs are also kept in the
golden file for the iOS test.
"""
import json
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

Q = "HKQuantityTypeIdentifier"
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
WATCH = {"source": "Apple Watch", "source_id": 1, "device_type": "watch", "device_name": "Apple Watch"}
PHONE = {"source": "iPhone", "source_id": 2, "device_type": "phone", "device_name": "iPhone"}
OTHER = {"source": "Sleep App", "source_id": 3, "device_type": "other", "device_name": ""}
# HKCategoryValueSleepAnalysis
IN_BED, ASLEEP, AWAKE, CORE, DEEP, REM = 0, 1, 2, 3, 4, 5


def _ts(t: datetime) -> float:
    return t.timestamp()


class Person:
    """Builds one case's rows."""

    def __init__(self, tz: str, first: date, days: int, seed: int):
        self.tz = ZoneInfo(tz)
        self.tzname = tz
        self.first = first
        self.days = [first + timedelta(days=i) for i in range(days)]
        self.rnd = random.Random(seed)
        self.samples: list[dict] = []
        self.buckets: dict[tuple[str, int], float] = {}
        self.other: list[dict] = []
        self.history: list[dict] = []
        self.workout_kcal: dict[str, float] = {}
        self.n = 0

    def at(self, d: date, h: int, m: int = 0) -> datetime:
        return datetime.combine(d, time(h, m), self.tz)

    def sample(self, hk: str, a: datetime, b: datetime, value: float, src: dict, uuid: str | None = None) -> None:
        self.n += 1
        self.samples.append({"uuid": uuid or f"{self.n:08x}-0000-4000-8000-{self.n:012x}", "type": hk, "start": _ts(a),
                             "end": _ts(b), "value": value, **src})

    def bucket(self, hk: str, t: datetime, value: float) -> None:
        start = int(_ts(t)) // 900 * 900
        self.buckets[(hk, start)] = self.buckets.get((hk, start), 0.0) + value

    def row(self, d: date, metric: str, source: str, value: float, agg: str, unit: str, flags=None, n=1,
            mn=None, mx=None) -> dict:
        return {"date": d.isoformat(), "metric": metric, "source": source, "value": value, "unit": unit, "agg": agg,
                "n": n, "min_value": mn, "max_value": mx, "flags": flags or {}}

    # ---------------------------------------------------------------- one ordinary day
    def day(self, d: date, *, hr=True, phone_hr=False, steps=True, basal_hours=24, active=1.0) -> None:
        r = self.rnd
        if hr:
            for h in range(0, 24, 3):
                a = self.at(d, h, 7)
                self.sample(Q + "HeartRate", a, a, float(r.randint(52, 95)), WATCH)
                if phone_hr:
                    self.sample(Q + "HeartRate", a + timedelta(minutes=1), a + timedelta(minutes=1),
                                float(r.randint(60, 80)), PHONE)
        for h in (1, 3, 5):
            a = self.at(d, h, 30)
            self.sample(Q + "HeartRateVariabilitySDNN", a, a + timedelta(minutes=1), round(r.uniform(30, 80), 3), WATCH)
        a = self.at(d, 8, 0)
        self.sample(Q + "RestingHeartRate", a, a, float(r.randint(50, 60)), WATCH)
        for h in (2, 4):
            a = self.at(d, h, 15)
            self.sample(Q + "OxygenSaturation", a, a, r.choice([0.95, 0.96, 0.97, 0.98, 0.99]), WATCH)
            self.sample(Q + "RespiratoryRate", a, a + timedelta(minutes=5), round(r.uniform(12, 17), 1), WATCH)
        a = self.at(d, 7, 0)
        self.sample(Q + "AppleSleepingWristTemperature", a - timedelta(hours=6), a, round(r.uniform(35.6, 36.4), 2), WATCH)
        self.sample(Q + "WalkingHeartRateAverage", self.at(d, 21), self.at(d, 21), float(r.randint(90, 110)), WATCH)
        # exercise minutes: the watch's and the phone's (the largest single source counts, not their sum)
        for i in range(r.randint(2, 8)):
            a = self.at(d, 18, 0) + timedelta(minutes=5 * i)
            self.sample(Q + "AppleExerciseTime", a, a + timedelta(minutes=5), 5.0, WATCH)
        for i in range(r.randint(0, 3)):
            a = self.at(d, 12, 0) + timedelta(minutes=5 * i)
            self.sample(Q + "AppleExerciseTime", a, a + timedelta(minutes=5), 5.0, PHONE)
        for h in range(8, 20, 2):
            self.sample(Q + "AppleStandTime", self.at(d, h), self.at(d, h, 3), 3.0, WATCH)
        if d.weekday() == 0:
            self.sample(Q + "BodyMass", self.at(d, 7, 5), self.at(d, 7, 5), round(r.uniform(78, 82), 2), PHONE)
            self.sample(Q + "BodyFatPercentage", self.at(d, 7, 5), self.at(d, 7, 5), round(r.uniform(0.18, 0.22), 3), PHONE)
        # 15-minute statistics buckets
        if steps:
            for h in range(7, 22):
                self.bucket(Q + "StepCount", self.at(d, h, 15), float(r.choice([0, 0, 160, 480, 1240])))
        for h in range(24):   # hourly here (the phone has 15-minute ones; only the sums matter)
            t = self.at(d, h, 30)
            if h < basal_hours:
                self.bucket(Q + "BasalEnergyBurned", t, round(r.uniform(66, 74), 3))
            if 7 <= h < 22:
                self.bucket(Q + "ActiveEnergyBurned", t, round(active * r.uniform(8, 36), 3))
        self.bucket(Q + "FlightsClimbed", self.at(d, 9), float(r.randint(0, 6)))
        # raw steps too (minute steps for stress): the daily value counts the buckets only
        for m in (0, 1, 2):
            a = self.at(d, 10, m)
            self.sample(Q + "StepCount", a, a + timedelta(minutes=1), float(r.randint(60, 120)), WATCH)

    # ---------------------------------------------------------------- nights (waking on d)
    def night(self, d: date, src=WATCH, start=(23, 20), wake=(6, 50), stages=True) -> None:
        a = self.at(d - timedelta(days=1), *start) if start[0] >= 12 else self.at(d, *start)
        b = self.at(d, *wake)
        if not stages:
            self.sample(SLEEP, a, b, IN_BED, src)
            return
        self.sample(SLEEP, a - timedelta(minutes=10), b + timedelta(minutes=5), IN_BED, src)   # the in-bed wrapper
        t, cycle = a, [CORE, DEEP, CORE, REM, AWAKE]
        i = 0
        while t < b:
            stage = cycle[i % len(cycle)]
            span = timedelta(minutes={CORE: 37, DEEP: 23, REM: 19, AWAKE: 4}[stage] + self.rnd.randint(0, 6))
            e = min(t + span, b)
            self.sample(SLEEP, t, e, stage, src)
            t, i = e, i + 1

    def nap(self, d: date) -> None:
        self.sample(SLEEP, self.at(d, 14, 0), self.at(d, 14, 40), CORE, WATCH)


def sydney() -> dict:
    p = Person("Australia/Sydney", date(2026, 3, 1), 62, seed=20261030)
    last = p.days[-1]
    special = {k: p.days[i] for k, i in (("phone_hr", 3), ("hourly_only", 9), ("low_basal", 20), ("workout", 25),
                                          ("deleted_hr", 31), ("out_of_range", 33), ("dst", 35), ("gap60", 40),
                                          ("gap2h", 44), ("in_bed_copy", 47), ("in_bed_only", 50), ("third_party", 53),
                                          ("twice", 56), ("nap", 58))}
    assert special["dst"] == date(2026, 4, 5)
    for d in p.days:
        p.day(d, phone_hr=d == special["phone_hr"], steps=d != special["hourly_only"], hr=d != special["deleted_hr"],
              basal_hours=6 if d == special["low_basal"] else 24, active=0.4 if d == special["workout"] else 1.0)
        if d == special["gap60"]:
            p.sample(SLEEP, p.at(d - timedelta(days=1), 23, 0), p.at(d, 1, 0), CORE, WATCH)
            p.sample(SLEEP, p.at(d, 2, 15), p.at(d, 7, 0), CORE, WATCH)     # 75 minutes later: a second session
        elif d == special["gap2h"]:
            p.sample(SLEEP, p.at(d - timedelta(days=1), 21, 0), p.at(d - timedelta(days=1), 22, 0), CORE, WATCH)
            p.sample(SLEEP, p.at(d, 0, 30), p.at(d, 6, 30), DEEP, WATCH)    # 2.5 h later: another OW session
        elif d == special["in_bed_only"]:
            p.night(d, src=PHONE, stages=False)
        elif d == special["third_party"]:
            p.night(d, src=OTHER, start=(23, 50), wake=(7, 10))
        elif d.day % 11 != 0:      # some nights without any sleep
            p.night(d, start=(23, p.rnd.randint(0, 59)), wake=(6, p.rnd.randint(0, 59)))
        if d == special["in_bed_copy"]:
            p.night(d, src=PHONE, stages=False, start=(23, 0), wake=(7, 0))
        if d == special["nap"]:
            p.nap(d)
        if d == special["twice"]:
            a = p.at(d, 13, 0)
            for _ in range(2):   # the same sample twice (two uuids, same times and stage): counted once
                p.sample(SLEEP, a, a + timedelta(minutes=30), CORE, WATCH)
    # out of range: SpO2 30 % and a 9-day heart-rate sample are dropped like the server drops them
    d = special["out_of_range"]
    p.sample(Q + "OxygenSaturation", p.at(d, 3), p.at(d, 3), 0.30, WATCH)
    p.sample(Q + "HeartRate", p.at(d, 4), p.at(d, 4) + timedelta(days=9), 70.0, WATCH)
    p.sample(Q + "HeartRate", p.at(d, 5), p.at(d, 4), 70.0, WATCH)   # ends before it starts
    # other sources (pulled `daily.sources`)
    for i, d in enumerate(p.days):
        if i % 4 == 0:
            p.other.append(p.row(d, "heart_rate", "ow", float(70 + i % 5), "avg", "count/min", n=40, mn=50.0, mx=120.0))
            p.other.append(p.row(d, "step_count", "ow", float(6000 + 37 * i), "sum", "count", n=60))
        if i % 7 == 0:
            p.other.append(p.row(d, "step_count", "shortcut", float(12000 + 11 * i), "sum", "count",
                                 flags={"hourly_sum": "按小时分组：各来源相加"}, n=24))
        p.other.append(p.row(d, "basal_energy_burned", "ow", round(1650.0 + 3.5 * (i % 9), 2), "sum", "kcal", n=96))
        p.other.append(p.row(d, "active_energy_burned", "ow", round(500.0 + 7.25 * (i % 6), 2), "sum", "kcal", n=60))
    d = special["hourly_only"]
    p.other.append(p.row(d, "step_count", "shortcut", 15500.0, "sum", "count", flags={"hourly_sum": "按小时分组：各来源相加"}, n=24))
    p.other = [r for r in p.other if not (r["date"] == d.isoformat() and r["metric"] == "step_count" and r["source"] == "ow")]
    p.other.append(p.row(special["deleted_hr"], "heart_rate", "apple_export", 66.5, "avg", "count/min", n=300, mn=48.0, mx=140.0))
    p.workout_kcal[special["workout"].isoformat()] = 900.0
    p.workout_kcal[special["phone_hr"].isoformat()] = 120.0
    # the phone's own basal rows of the weeks before (the basal reference of the first days)
    for k in range(1, 21):
        d = p.first - timedelta(days=k)
        p.history.append(p.row(d, "basal_energy_burned", "app", round(1700.0 - 4 * k, 3), "sum", "kcal", n=96))
    return p.case(today=last, name="sydney")


def new_york() -> dict:
    p = Person("America/New_York", date(2026, 2, 20), 30, seed=7)
    for d in p.days:
        p.day(d)
        p.night(d, start=(22, 45), wake=(6, 15))
    p.nap(p.days[5])
    for i, d in enumerate(p.days):
        p.other.append(p.row(d, "basal_energy_burned", "apple_export", 1500.0 + i, "sum", "kcal", n=24))
    return p.case(today=p.days[-1] + timedelta(days=1), name="new_york")


def _case(self: Person, today: date, name: str) -> dict:
    buckets = [{"type": hk, "start": s, "value": round(v, 4)} for (hk, s), v in sorted(self.buckets.items())]
    days = [d.isoformat() for d in self.days]
    return {"name": name, "src": {"tz": self.tzname, "today": today.isoformat(), "days": days,
                                  "wake_dates": days + [(self.days[-1] + timedelta(days=1)).isoformat()],
                                  "samples": self.samples, "buckets": buckets, "other": self.other,
                                  "app_history": self.history, "workout_kcal": self.workout_kcal}}


Person.case = _case

# small direct calls of the helpers the phone also uses on pulled rows
CALLS = [
    {"fn": "normalize", "args": ["active_energy_burned", 4184.0, "kJ"]},
    {"fn": "normalize", "args": ["oxygen_saturation", 0.97, "%"]},
    {"fn": "normalize", "args": ["oxygen_saturation", 97.0, "%"]},
    {"fn": "normalize", "args": ["step_count", 1234.0, "count"]},
    {"fn": "snake", "args": ["HKQuantityTypeIdentifierHeartRateVariabilitySDNN"]},
    {"fn": "snake", "args": ["HKCategoryTypeIdentifierSleepAnalysis"]},
    {"fn": "snake", "args": ["HKQuantityTypeIdentifierVO2Max"]},
]


def all_cases() -> dict:
    return {"cases": [sydney(), new_york()], "calls": CALLS}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--dump":
        sys.exit("usage: python3 cases_derive.py --dump FILE")
    Path(sys.argv[2]).write_text(json.dumps(all_cases(), ensure_ascii=False))
    print(f"wrote {sys.argv[2]}")
