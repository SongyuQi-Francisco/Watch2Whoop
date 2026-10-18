#!/usr/bin/env python3
"""Generate a synthetic Apple Health export (export.zip) for the Quick Start demo user.

Everything here is made up: a fictional 35-year-old with a 4-days-a-week training habit.
The file has the same shape as iPhone Health -> "Export All Health Data", so it goes through the
real importer (`body-engine import-apple-zip`) and the real scoring code.

Usage: python3 quickstart/demo_data.py --days 60 --out demo-export.zip [--tz Europe/London] [--seed 7]
Standard library only.
"""
import argparse
import io
import math
import random
import zipfile
from datetime import date, datetime, time, timedelta
from xml.sax.saxutils import quoteattr
from zoneinfo import ZoneInfo

HK = "HKQuantityTypeIdentifier"
WATCH, PHONE, SCALE = "Demo Watch", "Demo iPhone", "Demo Scale"
# Weekday -> (workout type, minutes, avg HR, kcal per minute); 0 = Monday
PLAN = {0: ("TraditionalStrengthTraining", 60, 118, 6.0),
        1: ("Running", 40, 152, 11.0),
        3: ("TraditionalStrengthTraining", 60, 120, 6.0),
        5: ("Basketball", 75, 145, 9.5),
        6: ("Hiking", 90, 112, 6.5)}


class Writer:
    def __init__(self, tz: ZoneInfo):
        self.tz, self.buf = tz, io.StringIO()

    def ts(self, dt: datetime) -> str:
        return dt.astimezone(self.tz).strftime("%Y-%m-%d %H:%M:%S %z")

    def record(self, type_: str, start: datetime, end: datetime, value, unit: str | None = None,
               source: str = WATCH) -> None:
        unit_attr = f" unit={quoteattr(unit)}" if unit else ""
        self.buf.write(f" <Record type={quoteattr(type_)} sourceName={quoteattr(source)}{unit_attr}"
                       f' creationDate="{self.ts(end)}" startDate="{self.ts(start)}" endDate="{self.ts(end)}"'
                       f" value={quoteattr(str(value))}/>\n")

    def workout(self, kind: str, start: datetime, end: datetime, kcal: float, avg_hr: float, max_hr: float,
                km: float | None) -> None:
        minutes = (end - start).total_seconds() / 60
        w = self.buf.write
        w(f' <Workout workoutActivityType="HKWorkoutActivityType{kind}" duration="{minutes:.2f}" durationUnit="min"'
          f' sourceName="{WATCH}" creationDate="{self.ts(end)}" startDate="{self.ts(start)}" endDate="{self.ts(end)}">\n')
        w(f'  <WorkoutStatistics type="{HK}ActiveEnergyBurned" startDate="{self.ts(start)}" endDate="{self.ts(end)}"'
          f' sum="{kcal:.1f}" unit="kcal"/>\n')
        w(f'  <WorkoutStatistics type="{HK}HeartRate" startDate="{self.ts(start)}" endDate="{self.ts(end)}"'
          f' average="{avg_hr:.0f}" minimum="{avg_hr - 25:.0f}" maximum="{max_hr:.0f}" unit="count/min"/>\n')
        if km:
            w(f'  <WorkoutStatistics type="{HK}DistanceWalkingRunning" startDate="{self.ts(start)}"'
              f' endDate="{self.ts(end)}" sum="{km:.2f}" unit="km"/>\n')
        w(" </Workout>\n")


def sleep_stages(rng: random.Random, start: datetime, minutes: int) -> list[tuple[datetime, datetime, str]]:
    """~90-minute cycles: more deep sleep early in the night, more REM late, a few short awakenings."""
    out, t, end = [], start, start + timedelta(minutes=minutes)
    cycle = 0
    while t < end:
        frac = (t - start).total_seconds() / max(minutes * 60, 1)
        deep = max(5, int(rng.gauss(28 * (1 - frac), 5)))
        rem = max(5, int(rng.gauss(10 + 25 * frac, 5)))
        core = max(20, int(rng.gauss(50, 8)))
        parts = [("AsleepCore", core // 2), ("AsleepDeep", deep), ("AsleepCore", core - core // 2), ("AsleepREM", rem)]
        if cycle and rng.random() < 0.5:
            parts.insert(0, ("Awake", rng.randint(2, 8)))
        for stage, m in parts:
            seg_end = min(t + timedelta(minutes=m), end)
            if seg_end > t:
                out.append((t, seg_end, stage))
            t = seg_end
        cycle += 1
    return out


def generate(days: int, tz: ZoneInfo, seed: int, now: datetime) -> bytes:
    """`days` full days up to yesterday, plus last night if it is already over (so today has a recovery score)."""
    today = now.date()
    rng = random.Random(seed)
    w = Writer(tz)
    first = today - timedelta(days=days)
    fatigue = 0.0  # carries training load into next morning's HRV / resting HR
    weight = 84.0
    for i in range(days + 1):
        d = first + timedelta(days=i)
        if d > today:
            break
        wd = d.weekday()
        trend = i / max(days, 1)  # slow fitness gain over the period

        # --- night before d: bedtime on d-1, wake on d ---
        late = 1 if wd in (5, 6) else 0  # weekend nights run later
        bed = datetime.combine(d - timedelta(days=1), time(23, 15), tz) + timedelta(
            minutes=rng.gauss(25 + 40 * late, 35))
        sleep_min = int(max(300, min(560, rng.gauss(445 - 25 * fatigue + 20 * late, 35))))
        stages = sleep_stages(rng, bed, sleep_min)
        wake = stages[-1][1]
        if d == today and wake > now:
            break
        w.record("HKCategoryTypeIdentifierSleepAnalysis", bed - timedelta(minutes=10), bed,
                 "HKCategoryValueSleepAnalysisInBed", source=WATCH)
        for s, e, stage in stages:
            w.record("HKCategoryTypeIdentifierSleepAnalysis", s, e, f"HKCategoryValueSleepAnalysis{stage}")

        rhr = 58 - 3 * trend + 3.5 * fatigue + rng.gauss(0, 1.2)
        hrv = 52 + 8 * trend - 9 * fatigue + rng.gauss(0, 5)
        t = bed
        while t < wake:  # overnight heart rate every 5 minutes
            w.record(HK + "HeartRate", t, t, round(rhr + 2 + rng.gauss(0, 2.5)), "count/min")
            t += timedelta(minutes=5)
        for k in range(4):  # Apple Watch takes a few SDNN readings per night
            at = bed + timedelta(minutes=60 + k * 90)
            if at < wake:
                w.record(HK + "HeartRateVariabilitySDNN", at, at + timedelta(minutes=1),
                         round(max(15, hrv + rng.gauss(0, 6)), 1), "ms")
        for k in range(6):
            at = bed + timedelta(minutes=30 + k * 60)
            if at < wake:
                w.record(HK + "RespiratoryRate", at, at + timedelta(minutes=1),
                         round(14.5 + 0.6 * fatigue + rng.gauss(0, 0.4), 1), "count/min")
                w.record(HK + "OxygenSaturation", at, at, round(min(1.0, rng.gauss(0.965, 0.008)), 3), "%")
        w.record(HK + "AppleSleepingWristTemperature", bed, wake, round(35.6 + 0.2 * fatigue + rng.gauss(0, 0.15), 2),
                 "degC")
        w.record(HK + "RestingHeartRate", datetime.combine(d, time(0), tz), datetime.combine(d, time(23, 59), tz),
                 round(rhr), "count/min")

        if d == today:
            break  # nothing after this morning
        # --- daytime ---
        plan = PLAN.get(wd) if rng.random() > 0.1 else None  # ~10% skipped sessions
        w_start = w_end = None
        if plan:
            kind, minutes, avg_hr, kcal_min = plan
            minutes = int(minutes * rng.uniform(0.85, 1.15))
            w_start = datetime.combine(d, time(18 if wd < 5 else 10), tz) + timedelta(minutes=rng.randint(-30, 45))
            w_end = w_start + timedelta(minutes=minutes)
        day_t = max(wake, datetime.combine(d, time(7), tz))
        night = datetime.combine(d, time(23), tz)
        workout_hr = []
        while day_t < night:
            if w_start and w_start <= day_t < w_end:
                prog = (day_t - w_start) / (w_end - w_start)
                hr = plan[2] + 12 * math.sin(math.pi * prog) + rng.gauss(0, 7)
                workout_hr.append(hr)
                step = 2
            else:
                hr = rhr + 22 + rng.gauss(0, 9)
                step = 6
            w.record(HK + "HeartRate", day_t, day_t, round(max(rhr, hr)), "count/min")
            day_t += timedelta(minutes=step)

        steps_total = 0
        for h in range(7, 23):
            s = datetime.combine(d, time(h), tz)
            steps = max(0, int(rng.gauss(550, 300)))
            if w_start and w_start.hour == h and plan[0] in ("Running", "Hiking", "Basketball"):
                steps += 4000
            steps_total += steps
            w.record(HK + "StepCount", s, s + timedelta(minutes=59), steps, "count", PHONE)
            w.record(HK + "ActiveEnergyBurned", s, s + timedelta(minutes=59), round(steps * 0.04 + rng.uniform(5, 20), 1),
                     "kcal")
        for h in range(24):
            s = datetime.combine(d, time(h), tz)
            w.record(HK + "BasalEnergyBurned", s, s + timedelta(minutes=59), round(rng.gauss(73, 2), 1), "kcal")
        exercise_min = int(steps_total / 900)

        load = 0.0
        if plan:
            kind, _, avg_hr, kcal_min = plan
            minutes = (w_end - w_start).total_seconds() / 60
            avg = sum(workout_hr) / len(workout_hr)
            w.workout(kind, w_start, w_end, minutes * kcal_min, avg, max(workout_hr),
                      round(minutes / 6, 2) if kind == "Running" else (round(minutes / 15, 2) if kind == "Hiking" else None))
            w.record(HK + "ActiveEnergyBurned", w_start, w_end, round(minutes * kcal_min, 1), "kcal")
            exercise_min += int(minutes)
            load = minutes * (avg - 60) / 4000
        w.record(HK + "AppleExerciseTime", datetime.combine(d, time(12), tz), datetime.combine(d, time(12, 1), tz),
                 exercise_min, "min")
        fatigue = max(0.0, min(2.0, fatigue * 0.55 + load + rng.gauss(0, 0.15)))

        if i % 3 == 0:
            weight += rng.gauss(-0.12, 0.25)
            at = datetime.combine(d, time(7, 30), tz)
            w.record(HK + "BodyMass", at, at, round(weight, 1), "kg", SCALE)
        if wd == 1 and plan:
            w.record(HK + "VO2Max", w_end, w_end, round(42 + 3 * trend + rng.gauss(0, 0.5), 1), "mL/min·kg", WATCH)

    # The importer drops the export day itself (incomplete in a real export); export "tomorrow" so last night counts.
    export_at = datetime.combine(today + timedelta(days=1), time(0, 5), tz)
    head = ('<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_US">\n'
            f' <ExportDate value="{w.ts(export_at)}"/>\n'
            ' <Me HKCharacteristicTypeIdentifierDateOfBirth="1990-01-01"'
            ' HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexNotSet"/>\n')
    return (head + w.buf.getvalue() + "</HealthData>\n").encode()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--tz", default="UTC", help="IANA time zone of the demo user")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--now", type=datetime.fromisoformat, help="pretend it is this local time (default: now in --tz)")
    p.add_argument("--out", default="demo-export.zip")
    a = p.parse_args()
    tz = ZoneInfo(a.tz)
    now = a.now.replace(tzinfo=tz) if a.now else datetime.now(tz)
    xml = generate(a.days, tz, a.seed, now)
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("apple_health_export/export.xml", xml)
    print(f"wrote {a.out} ({len(xml) / 1e6:.1f} MB XML, {a.days} days, synthetic)")


if __name__ == "__main__":
    main()
