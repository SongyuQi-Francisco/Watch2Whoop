"""Cross-language golden cases of package M2 (docs/ios-native-2.0.md sections 2.1 and 4): stress from the phone's local
tables, the stress cards and the cardio view on synthetic inputs, run by the Python reference
(body_engine/tests/test_js_golden_stress_cards.py, which writes test/golden/stress_cards.json and stress_local.json with
BODY_WRITE_GOLDEN=1) and replayed on the bundle (test/stress_cards.test.js).

Standard library only, synthetic data only (a made-up person in Australia/Sydney, the daylight-saving change of 2026-10-04
inside the ranges):
- local: insights/stress.days_local on 74 days of the phone's tables (60 scored + 14 baseline days): the history area
  (hist_samples: heart rate, HRV, 15-minute step buckets) for the first days, the phone's own HealthKit samples after that
  (heart rate, HRV, raw steps of an iPhone and a watch with the same time stamps), a day with both (the history is dropped),
  a day with the app's heart rate but only history steps (per-type rule), sleep crossing midnight, naps, workouts (one of
  them twice: two sources), walking bursts, hours with steps but too few heart-rate readings, a Shortcut-only day and a day
  without heart rate; runs: the whole range, a start before the baseline is long enough, without any steps;
- cards: insights/stress.cards on cases_series.person() (the same data trends/series.compute reads) for several days,
  today with a now hour, both languages, a day without a score row, without heart-rate recovery;
- cardio: cardio/views.compute on synthetic workout_hrr rows (valid / invalid, last / previous 30 days, before the range)
  and score rows (recovery index; zero-length night, missing offset).
In the dump, instants are epoch milliseconds, dates ISO strings (cases.dump); the local case is already JSON (UTC seconds).
"""
import json
import math
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cases_series as CS  # noqa: E402
from cases import dump  # noqa: E402

TZ_NAME = "Australia/Sydney"
TZ = ZoneInfo(TZ_NAME)
FIRST = date(2026, 8, 8)           # first day with samples (the last one: 2026-10-20)
N = 74                             # days with samples (14 baseline + 60 scored)
SCORED = 60
HISTORY_DAYS = 22                  # the first days come from the history area only
WATCH, PHONE = "Apple Watch", "iPhone"


def at(d: date, h: float) -> float:
    """Local wall time h hours after midnight of d as UTC seconds (whole seconds)."""
    return float(round((datetime.combine(d, time(0), TZ) + timedelta(hours=h)).astimezone(timezone.utc).timestamp()))


def _day(i: int) -> date:
    return FIRST + timedelta(days=i)


def local_case() -> dict:
    app = {"hr": [], "hrv": [], "steps": []}
    other = {"hr": [], "hrv": [], "steps": []}
    sleep, workouts, hrv_night, shortcut = [], [], {}, []
    for i in range(N):
        d = _day(i)
        s = math.sin(i / 4.0)
        bed, wake = -0.6 + 0.5 * math.sin(i / 6.0), 7.0 + 0.4 * math.cos(i / 5.0)   # bed before midnight, wake after
        sleep.append([at(d, bed), at(d, wake)])
        if i % 9 == 4:
            sleep.append([at(d, 14.0), at(d, 14.6)])                              # a nap
        hrv_night[d.isoformat()] = round(math.log(46 + 6 * s), 4)
        if i % 3 == 0:
            w = [at(d, 18.0), at(d, 19.0 + 0.25 * (i % 2))]
            workouts.append(w)
            if i % 6 == 0:
                workouts.append(list(w))                                          # the same workout from a second source
        if i == N - 10:
            shortcut.append(d.isoformat())
        no_hr = i in (N - 10, N - 7)
        hist = i < HISTORY_DAYS
        both = i == HISTORY_DAYS + 3                                              # the history has this day too: dropped
        hist_steps_only = i == HISTORY_DAYS + 5                                   # app heart rate, history steps
        # heart rate: every 10 minutes while awake (a few during the night, excluded by the sleep windows)
        hr = []
        if not no_hr:
            for k in range(int((wake + 0.2) * 6), int((23.4 + bed) * 6)):
                h = k / 6
                if 15.0 <= h < 16.0 and k % 6:   # steps-only hour: too few heart-rate readings
                    continue
                v = 64 + 7 * math.sin(h / 2.3 + i) + 4 * s + (9 if 13.0 <= h < 14.0 and i % 4 == 1 else 0)
                hr.append([at(d, h), round(v, 1)])
            hr += [[at(d, 2.0 + 0.5 * k), 52.0 + k] for k in range(4)]
        hrv = [[at(d, h), round(42 + 10 * math.sin(h + i), 1)] for h in (9.5, 12.5, 15.5, 20.5)] if not no_hr else []
        # walking: 10-minute bursts at 8:00, 12:30, 17:40 (and 15:00 in the steps-only hour), one sample a minute
        bursts = [8.0, 12.5, 17.67, 15.0]
        steps_raw = [(at(d, b + m / 60), 80 + (m * 7 + i) % 40) for b in bursts for m in range(10)]
        steps_raw += [(at(d, 10.0 + 0.5 * k), 6 + k % 5) for k in range(10)]  # a few idle steps (< 20 in the window)
        if hist or both:
            other["hr"] += [x for x in hr]
            other["hrv"] += hrv
            buckets: dict = {}
            for t, v in steps_raw:
                b = t // 900 * 900
                buckets[b] = buckets.get(b, 0) + v
            other["steps"] += [[t, float(v)] for t, v in sorted(buckets.items())]
        if not hist:
            app["hr"] += [[t, v, WATCH] for t, v in hr]
            app["hrv"] += [[t, v, WATCH] for t, v in hrv]
            if hist_steps_only:
                buckets = {}
                for t, v in steps_raw:
                    b = t // 900 * 900
                    buckets[b] = buckets.get(b, 0) + v
                other["steps"] += [[t, float(v)] for t, v in sorted(buckets.items())]
            else:   # iPhone and watch count the same walk at the same time stamps (kept per source, as the server)
                app["steps"] += [[t, float(v), WATCH] for t, v in steps_raw]
                app["steps"] += [[t, float(v // 2), PHONE] for t, v in steps_raw[:20]]
    for k in app:
        app[k].sort(key=lambda x: x[0])
    for k in other:
        other[k].sort()
    start = _day(N - SCORED).isoformat()
    end = _day(N - 1).isoformat()
    src = {"tz": TZ_NAME, "start": start, "end": end, "app": app, "other": other, "sleep": sleep, "workouts": workouts,
           "hrv_night": hrv_night, "shortcut": shortcut}
    runs = [{"name": "all", "start": start, "end": end},
            {"name": "early", "start": _day(0).isoformat(), "end": _day(12).isoformat()},
            {"name": "no_steps", "start": _day(N - 20).isoformat(), "end": end, "drop": ["steps"]}]
    return {"src": src, "runs": runs}


def variant_src(src: dict, run: dict) -> dict:
    """The run's src: its range, and the sample groups it drops (the Node test does the same)."""
    out = {**src, "start": run["start"], "end": run["end"]}
    for k in run.get("drop", []):
        out["app"] = {g: v for g, v in out["app"].items() if g != k}
        out["other"] = {g: v for g, v in out["other"].items() if g != k}
    return out


def cards_case() -> dict:
    data = CS.person()
    end = CS.END
    runs = [{"end": end, "today": end, "now_hour": 14, "lang": "zh"},
            {"end": end, "today": end, "now_hour": 6, "lang": "en"},
            {"end": end - timedelta(days=1), "today": end, "now_hour": 14, "lang": "zh"},
            {"end": CS.PAST, "today": end, "now_hour": 14, "lang": "zh"},
            {"end": CS.EARLY, "today": end, "now_hour": 9, "lang": "zh"}]
    # a day without a score row (person(): i % 17 == 5) and one inside the gap week
    runs += [{"end": CS.START + timedelta(days=k), "today": end, "now_hour": 14, "lang": "zh"} for k in (107, 203)]
    variants = {"no_hrr": {"hrr_apple": {}, "hrr_workouts": {}}, "no_stress": {"stress": []}}
    return {"tz": CS.TZ_NAME, "data": {k: data[k] for k in ("scores", "stress", "hrr_apple", "hrr_workouts")},
            "runs": runs, "variants": variants}


def cardio_case() -> dict:
    end = date(2026, 10, 20)
    start = end - timedelta(days=89)
    rows, scores = [], []
    for k in range(70):
        d = end - timedelta(days=k * 2)
        t = datetime.combine(d, time(18, 30), TZ) + timedelta(minutes=(k * 13) % 50)
        valid = k % 5 != 3
        rows.append({"workout_id": 1000 + k, "local_date": d, "end_at": t.astimezone(timezone.utc),
                     "type": ["running", "cycling", "walking"][k % 3], "name_zh": ["跑步", "骑行", "步行"][k % 3],
                     "duration_min": 30.0 + k % 40, "peak_hr": 150.0 + k % 25, "hr_60s": 128.0 + k % 13,
                     "hrr60": round(14 + (k * 7) % 19 + 0.1 * (k % 10), 1), "apple_hrr60": None if k % 4 else 21.0,
                     "valid": valid, "reason": None if valid else "结束前最高心率只有最大心率的 70%（要 ≥ 75%），这次的下降不代表心肺恢复"})
    rows.sort(key=lambda r: r["end_at"])
    for k in range(95):
        d = end - timedelta(days=k)
        bed = datetime.combine(d - timedelta(days=1), time(23, 15), TZ) + timedelta(minutes=(k * 11) % 70)
        wake = bed + timedelta(minutes=0 if k == 12 else 400 + (k * 17) % 90)
        rec = {} if k % 9 == 4 else {"rhr_low_offset_min": float((k * 37) % 460)}
        sl = {} if k % 13 == 6 else {"bedtime": bed.isoformat(), "wake_time": wake.isoformat()}
        scores.append({"date": d, "details": None if k == 30 else {"recovery": rec, "sleep": sl}})
    scores.sort(key=lambda r: r["date"])
    runs = [{"start": start, "end": end}, {"start": end - timedelta(days=13), "end": end},
            {"start": end - timedelta(days=200), "end": end - timedelta(days=40)}]
    return {"tz": TZ_NAME, "hrr": rows, "scores": scores, "runs": runs}


def all_cases() -> dict:
    return {"local": local_case(), "cards": cards_case(), "cardio": cardio_case()}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--dump":
        sys.exit("usage: python3 cases_stress_cards.py --dump FILE")
    Path(sys.argv[2]).write_text(json.dumps(dump(all_cases()), ensure_ascii=False))
    print(f"wrote {sys.argv[2]}")
