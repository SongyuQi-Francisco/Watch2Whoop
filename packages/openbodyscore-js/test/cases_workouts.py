"""Cross-language golden cases of 2.0.0 package M1c (docs/ios-native-2.0.md section 4): the phone's workouts, the
training calendar / day and the muscle-load map (body_engine training/importer.workout_days, training/history.compute +
calendar_view / day_view, training/muscle_load.compute_from <-> src/31_workouts.js).

Standard library only, synthetic data only (a made-up person; no export, no real values). Used by
- body_engine/tests/test_js_golden_workouts.py: runs the Python on these inputs and writes / compares
  test/golden/workouts.json (rewrite: BODY_WRITE_GOLDEN=1);
- test/workouts.test.js: `python3 cases_workouts.py --dump FILE`, then the bundle on the same inputs.

Workouts (`workout_days`, Australia/Sydney over the end of daylight saving time, 2026-04-05, and New York over its
start, 2026-03-08): strength + cardio on the same day, a watch + iPhone copy of one workout and an Open Wearables copy
(duplicates, the app ranks first), a workout without heart rate, a heart-rate gap at the end (no heart-rate recovery), a
workout across the DST change, user corrections (a type override, a hidden workout, a type rule for a generic one), a
manual duplicate, an unfinished 13-hour record, low heart-rate coverage, a winding-down walk (hrr60 not valid), the
birth-date HRmax when the day has no scores. Calendar / day (`training`): watch + SparkyFitness strength and cardio
merge, a SparkyFitness-only session, a Hevy import on its watch copy, daily metrics with the partial-energy flag,
scores hidden, English labels. Muscle load (`muscle`): catalog matches by slug / name / SparkyFitness id, unmatched and
warm-up sets, a cardio exercise, the sports layer (known, unknown, strength-like with and without a planned target, a
logged day), English names.
"""
import json
import random
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WATCH = {"source": "Apple Watch", "source_name": "Apple Watch", "device_type": "watch", "device_name": "Apple Watch"}
PHONE = {"source": "iPhone", "source_name": "iPhone", "device_type": "phone", "device_name": "iPhone"}


class Person:
    def __init__(self, tz: str, seed: int):
        self.tz = ZoneInfo(tz)
        self.tzname = tz
        self.rnd = random.Random(seed)
        self.hk: list[dict] = []
        self.hr: list[list] = []
        self.n = 0

    def at(self, d: date, h: int, m: int = 0) -> datetime:
        return datetime.combine(d, time(h, m), self.tz)

    def uuid(self) -> str:
        self.n += 1
        return f"{self.n:08X}-1111-4000-8000-{self.n:012X}"

    def workout(self, a: datetime, minutes: float, activity: int | None, src: dict = WATCH, *, name: str | None = None,
                active: float | None = 300.0, basal: float | None = 60.0, avg_hr: float | None = 130.0,
                pause_min: float = 0.0, uuid: str | None = None) -> str:
        u = uuid or self.uuid()
        b = a + timedelta(minutes=minutes)
        stats = {k: v for k, v in (("active_kcal", active), ("basal_kcal", basal), ("avg_hr", avg_hr),
                                    ("max_hr", None if avg_hr is None else avg_hr + 30)) if v is not None}
        if activity == 52:
            stats["distance_m"] = 3200.0
        self.hk.append({"uuid": u, "start": a.timestamp(), "end": b.timestamp(), "value": minutes - pause_min,
                        "value_text": name, "meta": {"activity": activity, "stats": stats}, "tz": self.tzname, **src})
        return u

    def heart(self, a: datetime, minutes: float, lo: float, hi: float, step_s: int = 5, tail_s: int = 0,
              wind_down: float = 0.0) -> None:
        """Heart rate every step_s seconds rising from lo to hi, then tail_s seconds after the end falling by wind_down per
        second (the watch keeps recording after a workout)."""
        n = int(minutes * 60 // step_s)
        for i in range(n + 1):
            v = lo + (hi - lo) * i / max(n, 1) + self.rnd.uniform(-3, 3)
            self.hr.append([a.timestamp() + i * step_s, round(v)])
        end = a + timedelta(minutes=minutes)
        for k in range(1, tail_s // 5 + 1):
            self.hr.append([end.timestamp() + 5 * k, round(hi - wind_down * 5 * k)])


def sydney() -> dict:
    p = Person("Australia/Sydney", 11)
    d0 = date(2026, 4, 1)
    days = [d0 + timedelta(days=i) for i in range(8)]
    # 04-01: strength + cardio, HR covering both, a heart-rate tail after each (HRR)
    a = p.at(days[0], 7, 0)
    p.workout(a, 55, 50, active=210, basal=70, avg_hr=112)
    p.heart(a, 55, 95, 150, tail_s=120, wind_down=0.35)
    b = p.at(days[0], 18, 30)
    p.workout(b, 42, 52, active=260, basal=55, avg_hr=138)
    p.heart(b, 42, 110, 165, tail_s=150, wind_down=0.4)
    # 04-02: the same run from the watch and from the iPhone (iPhone copy is a duplicate); an OW copy of it (others)
    c = p.at(days[1], 6, 10)
    watch_run = p.workout(c, 35, 37, active=380, basal=45, avg_hr=152)
    p.workout(c + timedelta(seconds=40), 34, 37, PHONE, active=360, basal=44, avg_hr=None)
    p.heart(c, 35, 120, 178, tail_s=100, wind_down=0.5)
    # 04-03: basketball without any heart rate (avg HR from the workout's statistics only) and a yoga without avg HR
    p.workout(p.at(days[2], 19, 0), 75, 6, active=600, basal=95, avg_hr=150)
    p.workout(p.at(days[2], 21, 0), 25, 57, active=60, basal=30, avg_hr=None)
    # 04-04: heart rate stops at the end (no HRR), and a walk that winds down slowly (hrr60 not valid: peak < 75 %)
    e = p.at(days[3], 12, 0)
    p.workout(e, 48, 13, active=350, basal=60, avg_hr=140, pause_min=3)
    p.heart(e, 48, 115, 170)
    f = p.at(days[3], 17, 0)
    p.workout(f, 30, 52, active=120, basal=40, avg_hr=98)
    p.heart(f, 30, 85, 104, tail_s=120, wind_down=0.05)
    # 04-05 (DST ends at 03:00 local): a workout from 01:40 to 02:40 standard time, crossing the change
    g = datetime(2026, 4, 4, 15, 40, tzinfo=ZoneInfo("UTC"))   # 02:40 AEDT -> runs through the repeated hour
    p.workout(g, 70, 3000, name="other", active=400, basal=80, avg_hr=125)
    p.heart(g, 70, 100, 150, step_s=10, tail_s=90, wind_down=0.3)
    # 04-06: user corrections: a generic workout typed basketball, a hidden one, a type rule for another generic one
    h = p.at(days[5], 10, 0)
    over = p.workout(h, 60, 3000, active=500, basal=80, avg_hr=145)
    p.heart(h, 60, 120, 170, tail_s=90, wind_down=0.6)
    hidden = p.workout(p.at(days[5], 14, 0), 20, 52, active=60, basal=20, avg_hr=95)
    p.workout(p.at(days[5], 20, 5), 50, 3000, active=300, basal=60, avg_hr=None)
    # 04-07: low heart-rate coverage (samples every 5 minutes for only the first half) and an unfinished 13-hour record
    k = p.at(days[6], 9, 0)
    p.workout(k, 60, 20, active=280, basal=70, avg_hr=118)
    p.heart(k, 22, 100, 140, step_s=300)
    p.workout(p.at(days[6], 6, 0), 13 * 60, 52, active=900, basal=700, avg_hr=80)
    # 04-08: no scores that day (HRmax from the birth date); an Open Wearables workout that a manual duplicate points to
    m = p.at(days[7], 8, 0)
    p.workout(m, 40, 46, active=330, basal=50, avg_hr=135)
    p.heart(m, 40, 110, 160, tail_s=90, wind_down=0.5)
    others = [
        {"source": "ow", "external_id": "ow-run-1", "type": "running", "start": c.timestamp() + 20,
         "end": (c + timedelta(minutes=35)).timestamp(), "is_xml": False, "device": "Apple Watch", "excluded": None},
        {"source": "shortcut", "external_id": "sc-1", "type": "generic", "start": m.timestamp() + 60,
         "end": (m + timedelta(minutes=39)).timestamp(), "is_xml": False, "device": None, "excluded": None},
        {"source": "apple_export", "external_id": "ax-1", "type": "swimming", "start": m.timestamp(),
         "end": (m + timedelta(minutes=40)).timestamp(), "is_xml": True, "device": "Watch", "excluded": None,
         "manual_dup": True, "dup_of": "shortcut:sc-1"},
    ]
    p.hr.append(p.hr[5])   # the same sample twice (one value per instant)
    hr_max = {d.isoformat(): 186.0 + i * 0.25 for i, d in enumerate(days[:-1])}
    src = {"tz": p.tzname, "days": [d.isoformat() for d in days], "hk_workouts": p.hk, "others": others,
           "overrides": [{"source": "app", "external_id": over.lower(), "type": "basketball", "name_zh": None, "hidden": False},
                         {"source": "app", "external_id": hidden.lower(), "type": None, "name_zh": None, "hidden": True}],
           "type_rules": [{"weekday": 0, "start_hour": 20.0, "to_type": "badminton", "name_zh": None}],
           "hr": p.hr, "hr_max": hr_max, "birth_date": "1990-06-15"}
    return {"name": "sydney", "src": src, "watch_run": watch_run}


def new_york() -> dict:
    p = Person("America/New_York", 7)
    d0 = date(2026, 3, 6)
    days = [d0 + timedelta(days=i) for i in range(4)]
    for i, d in enumerate(days):
        a = p.at(d, 7 if i != 2 else 1, 30)   # 03-08 01:30: the clocks jump at 02:00
        p.workout(a, 45 + 5 * i, 37 if i % 2 else 13, active=320 + i, basal=50, avg_hr=140)
        p.heart(a, 45 + 5 * i, 110, 168, tail_s=90, wind_down=0.45)
    src = {"tz": p.tzname, "days": [d.isoformat() for d in days], "hk_workouts": p.hk, "others": [], "overrides": [],
           "type_rules": [], "hr": {"cols": ["t", "value"], "rows": p.hr},
           "hr_max": {d.isoformat(): 190.0 for d in days}, "birth_date": None}
    return {"name": "new_york", "src": src}


# ---------------------------------------------------------------- training calendar / day (history.compute)
def _w(i, source, typ, name, a: datetime, minutes, *, device="Apple Watch", origin="Apple Watch", active=None,
       avg_hr=None, trimp=None, zones=None, hr_max=None, flags=None, d: date):
    return {"id": i, "source": source, "external_id": f"{source}-{i}", "device": device, "origin": origin, "type": typ,
            "name_zh": name, "start_at": a.timestamp(), "end_at": (a + timedelta(minutes=minutes)).timestamp(),
            "local_date": d.isoformat(), "duration_min": minutes, "active_kcal": active,
            "total_kcal": None if active is None else active + 50.5, "distance_km": 3.21 if typ == "walking" else None,
            "avg_hr": avg_hr, "max_hr": None if avg_hr is None else avg_hr + 25.4, "hr_zone_min": zones, "trimp": trimp,
            "hr_max_used": hr_max, "flags": flags or {}}


def _set(n, reps, kg, typ="normal"):
    return {"set": n, "type": typ, "reps": reps, "weight_kg": kg, "rir": 2, "rpe": None, "duration_s": None,
            "distance_km": None, "notes": None}


def training() -> dict:
    tz = ZoneInfo("Australia/Sydney")
    first = date(2026, 4, 1)
    at = lambda d, h, m=0: datetime.combine(d, time(h, m), tz)   # noqa: E731
    d1, d2, d3, d4, d5 = (first + timedelta(days=i) for i in (0, 1, 2, 4, 5))
    workouts = [
        _w(1, "app", "strength_training", "力量训练", at(d1, 7), 55.4, active=210.4, avg_hr=112.6, trimp=64.3,
           zones=[10.0, 20.5, 15.2, 4.1, 0.0], hr_max=186.0, d=d1),
        _w(2, "app", "walking", "步行 / 爬坡", at(d1, 18, 30), 42.0, active=260.0, avg_hr=138.0, trimp=128.0,
           zones=[2.0, 8.0, 20.0, 12.0, 0.0], hr_max=186.0, d=d1),
        _w(3, "ow", "basketball", "篮球", at(d2, 19), 75.0, device="Watch", origin="Keep", active=600.0, avg_hr=150.0,
           d=d2, flags={"hr_coverage_low": "x", "computed": "v", "basal_kcal": 80}),
        _w(4, "shortcut", "generic", "其他运动", at(d3, 12), 3.0, device=None, origin=None, d=d3),
        _w(5, "hevy", "strength_training", "力量训练", at(d4, 17), 60.0, device=None, origin="Hevy", d=d4),
        _w(6, "app", "running", "跑步", at(d5, 6), 30.0, active=380.0, avg_hr=None, trimp=None, d=d5),
        _w(7, "app", "yoga", "瑜伽", at(d5, 21), 25.0, active=60.0, avg_hr=95.0, d=d5),
    ]
    sparky = [
        {"id": 101, "date": d1.isoformat(), "name": "Push day", "notes": "felt good", "source": "manual",
         "total_duration_minutes": 50, "exercises": [
             {"exercise_id": "ex-bench", "name": "卧推", "category": "strength", "notes": None,
              "sets": [_set(1, 10, 20.0, "warm-up"), _set(2, 8, 60.0), _set(3, 8, 62.5), _set(4, 6, 65.0)]},
             {"exercise_id": "ex-dip", "name": "双杠臂屈伸", "category": "strength", "notes": None,
              "sets": [_set(1, 12, None), _set(2, 10, None)]},
             {"exercise_id": "ex-climb", "name": "爬坡（跑步机）", "category": "cardio", "duration_minutes": 40,
              "notes": None, "sets": [{**_set(1, None, None), "duration_s": 2400}]}]},
        {"id": 102, "date": d2.isoformat(), "name": None, "notes": None, "source": "manual",
         "total_duration_minutes": None, "exercises": [
             {"exercise_id": "ex-row", "name": "划船", "category": "strength", "duration_minutes": 20, "notes": "x",
              "sets": [_set(1, 10, 50.0)] * 22}]},
    ]
    imported = [{"id": "hevy:hevy-5", "date": d4.isoformat(), "source": "hevy", "workout": "hevy-5", "name": "Legs",
                 "total_duration_minutes": 60, "notes": "imported", "exercises": [
                     {"exercise_id": None, "slug": "barbell_squat", "name": "深蹲", "category": "strength",
                      "primary_muscles": ["quadriceps"], "notes": None, "sets": [_set(1, 5, 100.0)] * 5}]}]
    metrics = {d1.isoformat(): {"active_kcal": 400.4, "basal_kcal": 1700.6, "exercise_min": 95.0, "steps": 9123.0},
               d2.isoformat(): {"active_kcal": 500.0, "basal_kcal": 1650.0}, d5.isoformat(): {"steps": 4000.5}}
    scores = {d1.isoformat(): {"strain": 14.2, "muscle_load": 3200.0, "recovery": 66.0, "trimp": 180.0, "hr_max": 186.0},
              d2.isoformat(): {"strain": 12.0, "muscle_load": None, "recovery": 40.0, "trimp": 150.0, "hr_max": None}}
    data = {"timezone": "Australia/Sydney", "workouts": workouts, "sparky": sparky, "sparky_error": None,
            "imported": imported, "targets": {"hevy-5": 5}, "status": {"ex-bench": "active", "ex-dip": "banned",
                                                                       "ex-row": "caution"},
            "metrics": metrics, "scores": scores, "hr_max": {d1.isoformat(): 186.0}, "birth_date": "1990-06-15",
            "can_scores": True, "can_metrics": True}
    runs = [
        {"name": "month", "view": "calendar", "start": "2026-04-01", "end": "2026-04-30", "label": "2026-04",
         "today": "2026-04-06", "lang": "zh", "data": {**data, "status": None}},
        {"name": "range_en", "view": "calendar", "start": "2026-03-30", "end": "2026-04-06", "label": None,
         "today": "2026-04-06", "lang": "en", "data": {**data, "status": None, "can_scores": False,
                                                        "sparky_error": "读不到 SparkyFitness：x"}},
        {"name": "day_strength", "view": "day", "start": "2026-04-01", "end": "2026-04-01", "today": "2026-04-06",
         "lang": "zh", "data": data},
        {"name": "day_sport_en", "view": "day", "start": "2026-04-02", "end": "2026-04-02", "today": "2026-04-06",
         "lang": "en", "data": {**data, "can_metrics": False}},
        {"name": "day_hevy", "view": "day", "start": "2026-04-05", "end": "2026-04-05", "today": "2026-04-06",
         "lang": "zh", "data": data},
        {"name": "day_birth_hrmax", "view": "day", "start": "2026-04-06", "end": "2026-04-06", "today": "2026-04-06",
         "lang": "zh", "data": data},
    ]
    return {"runs": runs}


# ---------------------------------------------------------------- muscle load (muscle_load.compute_from)
def muscle() -> dict:
    today = date(2026, 4, 8)   # a Wednesday
    rows = {
        "barbell_bench_press": {"slug": "barbell_bench_press", "name_zh": "杠铃卧推", "name_en": "Barbell bench press",
                                "category": "strength", "primary_muscles": ["chest"],
                                "secondary_muscles": ["triceps", "shoulders"]},
        "barbell_squat": {"slug": "barbell_squat", "name_zh": "杠铃深蹲", "name_en": "Barbell squat",
                          "category": "strength", "primary_muscles": ["quadriceps", "glutes"],
                          "secondary_muscles": ["hamstrings", "lower back"]},
        "pull_up": {"slug": "pull_up", "name_zh": "引体向上", "name_en": "Pull-up", "category": "strength",
                    "primary_muscles": ["lats"], "secondary_muscles": ["biceps", "middle back"]},
        "hamstring_stretch": {"slug": "hamstring_stretch", "name_zh": "腘绳肌拉伸", "name_en": "Hamstring stretch",
                              "category": "stretching", "primary_muscles": ["hamstrings"], "secondary_muscles": []},
    }
    index = {"rows": rows, "by_name": {"杠铃卧推": "barbell_bench_press", "卧推": "barbell_bench_press",
                                       "barbellsquat": "barbell_squat", "深蹲": "barbell_squat", "引体向上": "pull_up",
                                       "腘绳肌拉伸": "hamstring_stretch"},
             "by_app": {"sp-pull": "pull_up"}}
    s = lambda reps, kg=None, typ="normal", dur=None: {"reps": reps, "weight_kg": kg, "type": typ, "duration_s": dur}  # noqa: E731
    sessions = [
        {"date": "2026-04-06", "exercises": [
            {"exercise_id": "sp-bench", "name": "卧推（杠铃）", "category": "strength",
             "sets": [s(10, 20, "warm-up"), s(8, 60), s(8, 62.5), s(6, 65)]},
            {"exercise_id": "sp-pull", "name": "Pull ups", "category": "strength", "sets": [s(8), s(7), s(None)]},
            {"exercise_id": "sp-x", "name": "神秘动作", "category": "strength", "sets": [s(10, 10)]},
            {"exercise_id": "sp-y", "name": "Cable thing", "category": "strength", "primary_muscles": ["Biceps"],
             "sets": [s(12, 15), s(12, 15)]},
            {"exercise_id": "sp-run", "name": "跑步", "category": "cardio", "sets": [s(None, None, dur=1200)]},
            {"exercise_id": "sp-st", "name": "腘绳肌拉伸", "category": "strength", "sets": [s(None, None, dur=60)]}]},
        {"date": "2026-04-08", "exercises": [
            {"exercise_id": "sp-sq", "name": "深蹲", "category": "strength", "sets": [s(5, 100), s(5, 100), s(5, 102.5)]}]},
        {"date": "2026-03-31", "exercises": [
            {"exercise_id": "sp-sq", "name": "深蹲", "category": "strength", "sets": [s(5, 95)] * 4}]},
        {"date": "2026-04-09", "exercises": [   # after today: left out
            {"exercise_id": "sp-sq", "name": "深蹲", "category": "strength", "sets": [s(5, 95)]}]},
    ]
    imported = [{"date": "2026-04-07", "exercises": [{"slug": "barbell_squat", "name": "Squat",
                                                      "sets": [s(8, 80), s(8, 80)]}]}]
    sports = [
        {"date": "2026-04-06", "type": "strength_training", "min": 50.0, "trimp": 60.0, "avg_hr": 110.0, "hr_max": 186.0},
        {"date": "2026-04-07", "type": "basketball", "min": 75.0, "trimp": 160.0, "avg_hr": 150.0, "hr_max": 186.0},
        {"date": "2026-04-07", "type": "running", "min": 30.0, "trimp": None, "avg_hr": 150.0, "hr_max": 186.0},
        {"date": "2026-04-08", "type": "walking", "min": 4.0, "trimp": None, "avg_hr": None, "hr_max": None},
        {"date": "2026-04-03", "type": "functional_strength_training", "min": 45.0, "trimp": None, "avg_hr": None,
         "hr_max": None},
        {"date": "2026-04-02", "type": "fitness_equipment", "min": 30.0, "trimp": None, "avg_hr": None, "hr_max": None},
        {"date": "2026-04-01", "type": "Traditional Strength Training", "min": 40.0, "trimp": None, "avg_hr": 120.0,
         "hr_max": 186.0},
        {"date": "2026-04-04", "type": "kayaking", "min": 50.0, "trimp": 90.0, "avg_hr": None, "hr_max": None},
        {"date": "2026-04-05", "type": "underwater_hockey", "min": 20.0, "trimp": None, "avg_hr": None, "hr_max": None},
        {"date": "2026-03-30", "type": "cycling", "min": 60.0, "trimp": 130.0, "avg_hr": None, "hr_max": None},
    ]
    targets = {"5": [["chest", "triceps"], ["shoulders"]], "4": [["quadriceps"], []]}
    base = {"sessions": sessions, "sparky_state": "ok", "imported": imported, "index": index, "sports": sports,
            "plan_targets": targets, "sex": "female"}
    return {"runs": [{"name": "week", "today": today.isoformat(), "lang": "zh", "data": base},
                     {"name": "week_en_no_plan", "today": today.isoformat(), "lang": "en",
                      "data": {**base, "plan_targets": None, "sex": None, "sparky_state": "error", "sessions": []}},
                     {"name": "monday", "today": "2026-04-06", "lang": "zh", "data": {**base, "sex": "male"}}]}


def all_cases() -> dict:
    return {"workouts": [sydney(), new_york()], "training": training(), "muscle": muscle()}


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--dump":
        with open(sys.argv[2], "w") as f:
            json.dump(all_cases(), f, ensure_ascii=False)
    else:
        print(json.dumps(all_cases(), ensure_ascii=False)[:2000])
