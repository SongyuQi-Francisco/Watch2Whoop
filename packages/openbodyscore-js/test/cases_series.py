"""Cross-language golden cases of package M3 (docs/ios-native-2.0.md section 2.1): the Trends series and the views around
them on synthetic inputs, run by the Python reference (body_engine/tests/test_js_golden_series.py, which writes
test/golden/series.json with BODY_WRITE_GOLDEN=1) and replayed on the bundle (test/series.test.js).

Standard library only, synthetic data only (a made-up person, 420 days ending 2026-10-20 in Australia/Sydney, so the
daylight-saving change of 2026-10-04 is inside the ranges). The inputs are what each Python `load` side returns:
- trends/series.compute: score rows with details, stress rows, weekly body age, daily metrics, main sleep sessions,
  heart-rate recovery, weights (SparkyFitness / records merged) — with missing days, a missing week, days without
  scores, illness flags, < 14 baseline days early in the history, and variants without scopes / blocks / with a weight error;
- web/views month_days_compute, sleep_nights_compute; insights/sleep_detail watch_sessions + details; insights/fitness
  hr_zones_compute, vo2max_compute, level.
In the dump, instants are epoch milliseconds (exact after JSON), dates ISO strings (cases.dump).
"""
import json
import math
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cases import dump  # noqa: E402

TZ_NAME = "Australia/Sydney"
TZ = ZoneInfo(TZ_NAME)
END = date(2026, 10, 20)
N = 420
START = END - timedelta(days=N - 1)
PAST = END - timedelta(days=50)
EARLY = START + timedelta(days=24)
GAP = range(200, 207)   # a week without any data


def at(d: date, h: float) -> datetime:
    """Local wall time h hours after midnight of d (h may be negative / > 24), as a UTC instant in whole seconds (the
    phone keeps UTC seconds)."""
    return (datetime.combine(d, time(0), TZ) + timedelta(hours=h)).astimezone(timezone.utc).replace(microsecond=0)


def _r(x: float, n: int = 1) -> float:
    return round(x, n)


def person() -> dict:
    """The synthetic history: every block trends/series.load reads."""
    scores, stress, body_age, metrics, sleep, hrr_apple, hrr_workouts, weights = [], [], [], [], [], {}, {}, {}
    ctl = atl = 40.0
    for i in range(N):
        d = START + timedelta(days=i)
        if i in GAP:
            continue
        s = math.sin(i / 3.0)
        has_sleep = i % 23 != 7
        # main sleep: bed ~23:30 (later on weekends), wake ~07:00; asleep minutes from the stages
        bed = -0.5 + 0.8 * math.sin(i / 5.0) + (1.2 if d.weekday() >= 5 else 0)
        wake = 7.0 + 0.6 * math.cos(i / 4.0) + (1.0 if d.weekday() >= 5 else 0)
        start_at, end_at = at(d, bed), at(d, wake)
        in_bed = (end_at - start_at).total_seconds() / 60
        deep, rem = (None, None) if i % 37 == 11 else (_r(60 + 25 * s), _r(80 + 20 * math.cos(i)))
        asleep = 0 if i % 41 == 13 else _r(in_bed * (0.86 + 0.08 * math.sin(i / 7.0)))
        if has_sleep:
            sleep.append({"wake_date": d, "start_at": start_at, "end_at": end_at, "asleep_min": asleep, "in_bed": in_bed})
        # metrics (Apple's daily values), with their own gaps
        for metric, value, every in (
                ("heart_rate_variability_sdnn", _r(48 + 9 * s + (i % 5)), 1), ("resting_heart_rate", _r(55 - 3 * s, 0), 1),
                ("vo2_max", _r(42 + 0.004 * i + 0.5 * math.sin(i)), 6), ("step_count", float(6000 + (i * 773) % 9000), 1),
                ("active_energy_burned", _r(350 + 200 * abs(s), 2), 1), ("basal_energy_burned", _r(1850 + 20 * s, 2), 2),
                ("time_in_daylight", float((i * 7) % 120), 3), ("oxygen_saturation", _r(96 + s, 1), 1),
                ("respiratory_rate", _r(14.5 + 0.8 * s, 2), 1)):
            if i % every == 0 and (i + len(metric)) % 19 != 0:
                metrics.append({"date": d, "metric": metric, "value": value})
        if i % 11 == 2:
            hrr_apple[d] = _r(18 + 6 * s, 1)
        if i % 7 == 3:
            hrr_workouts[d] = _r(22 + 5 * math.cos(i), 2)
        if i % 3 == 0 or i % 10 == 4:
            weights[d] = _r(92 - 0.02 * i + 0.4 * s, 2)
        if i % 7 == 6:
            body_age.append({"as_of": d, "body_age": 33.4 - 0.004 * i + 0.3 * s})
        if i % 5:
            hours = [{"hour": h, "score": None if h == 10 else _r((h % 4) * 0.7, 2),
                      "level": ["low", "low", "medium", "high"][h % 4]} for h in range(6, 23)]
            stress.append({"date": d, "score": None if i % 29 == 8 else _r((i % 7) * 0.43, 3),
                           "level": ["low", "medium", "high"][i % 3], "hours": hours})
        if i % 17 == 5:   # a day without a score row
            continue
        trimp = None if i % 13 == 12 else _r(30 + 90 * abs(math.sin(i * 0.7)))
        if trimp is not None:
            ctl += (trimp - ctl) * (1 - math.exp(-1 / 42))
            atl += (trimp - atl) * (1 - math.exp(-1 / 7))
        flags = {}
        recovery = None if i % 11 == 3 else 15 + (i * 37) % 84
        if recovery is None:
            flags["hrv_missing"] = "这晚没有 HRV"
        if i % 29 == 4:
            flags["illness_warning"] = "体温偏高"
        sleep_score = None if i % 31 in (9, 10) else 45 + (i * 13) % 55
        if i % 31 == 9:
            flags["no_sleep"] = "这晚没有手表睡眠"
        zones = None if i % 9 == 4 else [_r(20 + 5 * s), _r(15 + i % 9), float(i % 11), float(i % 4), 0.5 * (i % 3)]
        z = None if i % 15 == 2 else {"hrv": _r(-2.4 + 0.11 * (i % 40), 2), "rhr": _r(1.1 - 0.07 * (i % 30), 2),
                                      "resp_rate": _r(0.6 - 0.05 * (i % 25), 2)}
        rec = {"z": z, "spo2_night": None if i % 6 == 1 else _r(95.5 + 2.2 * math.sin(i / 2.0), 1),
               "wrist_temp": None if i % 8 == 5 else _r(35.9 + 0.25 * math.sin(i / 3.3) + (0.7 if i % 53 == 0 else 0), 2),
               "rhr_low_offset_min": None if i % 10 == 7 else _r((i * 37) % 420, 0)}
        sl = {"asleep_min": asleep, "deep_min": deep, "rem_min": rem, "consistency": None if i % 12 == 5 else 55 + (i * 7) % 45,
              "efficiency": None if i % 14 == 6 else _r(0.82 + 0.17 * abs(math.sin(i)), 3), "awakenings": i % 6,
              "bedtime": start_at.astimezone(TZ).isoformat(), "wake_time": end_at.astimezone(TZ).isoformat(),
              "latency_min": None if i % 4 else _r(8 + i % 20), "waso_min": _r(10 + i % 30), "nap_min": 25 if i % 9 == 0 else 0}
        details = {"recovery": rec, "load": {"zone_minutes": zones} if zones else {}}
        if has_sleep:
            details["sleep"] = sl
        scores.append({"date": d, "recovery": recovery, "sleep_score": sleep_score,
                       "sleep_actual_min": None if sleep_score is None else asleep, "sleep_need_min": 450 + (i % 7) * 5.5,
                       "sleep_debt_min": (i * 23) % 180, "sleep_performance": round(0.55 + ((i * 7) % 50) / 100, 4),
                       "resp_rate_night": _r(14 + (i % 9) * 0.37, 2), "trimp": trimp,
                       "strain": None if i % 19 == 7 else _r(4 + (i * 7) % 15 + 0.3 * (i % 4), 2),
                       "muscle_load": _r(800 + (i * 97) % 2500) if i % 3 == 0 else (0.0 if i % 3 == 1 else None),
                       "ctl": _r(ctl), "atl": _r(atl), "tsb": _r(ctl - atl - (25 if i % 16 == 0 else 0)),
                       "flags": flags, "details": details})
    return {"can": {"scores": True, "metrics": True, "sleep": True, "weight": True, "training": True},
            "scores": scores, "stress": stress, "body_age": body_age, "metrics": metrics, "sleep": sleep,
            "hrr_apple": hrr_apple, "hrr_workouts": hrr_workouts, "weights": weights}


def variant(data: dict, spec: dict) -> dict:
    """A variant of the data: spec {drop: [blocks], can: {scope: bool}, set: {block: value}} (the Node test does the same)."""
    out = {k: v for k, v in data.items() if k not in spec.get("drop", [])}
    out["can"] = {**data["can"], **spec.get("can", {})}
    out.update(spec.get("set", {}))
    return out


def series_case() -> dict:
    """data + variants + runs: [{data (variant name), end, days, today, now_hour, lang}] of trends/series.compute."""
    data = person()
    variants = {
        "full": {},
        "no_scores": {"can": {"scores": False}},
        "no_metrics_sleep": {"can": {"metrics": False, "sleep": False}},
        "no_weight_training": {"can": {"weight": False, "training": False}},
        "missing_blocks": {"drop": ["stress", "body_age", "hrr_apple", "hrr_workouts", "weights"]},
        "weights_error": {"drop": ["weights"], "set": {"weights_error": "体重记录暂时读不到"}},
    }
    runs = []
    for days in (7, 30, 90, 365):
        runs.append({"data": "full", "end": END, "days": days, "today": END, "now_hour": 14, "lang": "zh"})
    for days in (7, 30, 90):
        runs.append({"data": "full", "end": PAST, "days": days, "today": END, "now_hour": 14, "lang": "zh"})
    runs.append({"data": "full", "end": EARLY, "days": 30, "today": END, "now_hour": 9, "lang": "zh"})       # < 14 baseline days
    runs.append({"data": "full", "end": END + timedelta(days=3), "days": 7, "today": END + timedelta(days=3),
                 "now_hour": 6, "lang": "zh"})                                                                 # last days empty
    runs.append({"data": "full", "end": END, "days": 30, "today": END, "now_hour": 20, "lang": "en"})
    for v in ("no_scores", "no_metrics_sleep", "no_weight_training", "missing_blocks", "weights_error"):
        runs.append({"data": v, "end": END, "days": 30, "today": END, "now_hour": 14, "lang": "zh"})
    return {"tz": TZ_NAME, "data": data, "variants": variants, "runs": runs}


# ---------------------------------------------------------------- web/views: month calendar and the last nights
def month_case(data: dict) -> list[dict]:
    """[{month, today, scores, present, firsts, hidden}] of month_days_compute."""
    sc = {r["date"]: {"recovery": r["recovery"], "sleep_score": r["sleep_score"], "strain": r["strain"]} for r in data["scores"]}
    sleep = {r["wake_date"] for r in data["sleep"]}
    training = {d for d in data["hrr_workouts"]}
    metrics = {r["date"] for r in data["metrics"]}
    first = START
    out = []

    def pick(s: set, month: str) -> set:
        y, m = (int(x) for x in month.split("-"))
        return {d for d in s if (d.year, d.month) == (y, m)}

    for month in ("2026-10", "2026-09", "2026-02", "2025-12"):
        out.append({"month": month, "today": END, "scores": {d: v for d, v in sc.items() if d.isoformat()[:7] == month},
                    "present": {"sleep": pick(sleep, month), "training": pick(training, month), "metrics": pick(metrics, month)},
                    "firsts": [first, first + timedelta(days=3), None, first + timedelta(days=1)], "hidden": []})
    out.append({"month": "2026-10", "today": END, "scores": {}, "present": {"sleep": pick(sleep, "2026-10"), "metrics": set()},
                "firsts": [None, None], "hidden": ["scores", "training"]})
    out.append({"month": "2027-01", "today": END, "scores": {}, "present": {"sleep": set(), "training": set(), "metrics": set()},
                "firsts": [], "hidden": []})
    return out


def nights_case(data: dict) -> list[dict]:
    """[{sessions, scores, can_scores, end, days}] of sleep_nights_compute: main sleeps, naps, two mains, no session."""
    sessions = []
    for r in data["sleep"]:
        d = r["wake_date"]
        if d < END - timedelta(days=20) or d.day % 9 == 0:
            continue
        a = r["asleep_min"]
        sessions.append({"wake_date": d, "start_at": r["start_at"], "end_at": r["end_at"], "asleep_min": a,
                         "deep_min": _r(a * 0.18) if a else None, "core_min": _r(a * 0.55) if a else None,
                         "rem_min": _r(a * 0.22) if a else None, "awake_min": 12.0, "unspecified_min": 0.0,
                         "is_main": True, "source": "app", "device": "Apple Watch"})
        if d.day % 4 == 1:   # an afternoon nap
            sessions.append({"wake_date": d, "start_at": at(d, 14.25), "end_at": at(d, 14.9), "asleep_min": 35.0,
                             "deep_min": None, "core_min": 35.0, "rem_min": None, "awake_min": 0.0, "unspecified_min": None,
                             "is_main": False, "source": "app", "device": "Apple Watch"})
        if d.day % 7 == 3:   # a second, shorter main sleep (the longer one wins)
            sessions.append({"wake_date": d, "start_at": at(d, 2.0), "end_at": at(d, 3.0), "asleep_min": None,
                             "deep_min": None, "core_min": None, "rem_min": None, "awake_min": None, "unspecified_min": None,
                             "is_main": True, "source": "ow", "device": None})
    sessions.sort(key=lambda s: s["start_at"])
    scores = {r["date"]: {k: r[k] for k in ("sleep_score", "sleep_actual_min", "sleep_need_min", "sleep_debt_min", "flags")}
              for r in data["scores"] if r["date"] >= END - timedelta(days=20)}
    return [{"sessions": sessions, "scores": scores, "can_scores": True, "end": END, "days": 14},
            {"sessions": sessions, "scores": {}, "can_scores": False, "end": END, "days": 14},
            {"sessions": sessions, "scores": scores, "can_scores": True, "end": END - timedelta(days=12), "days": 7}]


# ---------------------------------------------------------------- insights/sleep_detail and fitness
def sleep_detail_case(data: dict) -> list[dict]:
    """[{rows (sleep_sessions by start), nights (nights_from rows), start, end}] of details()."""
    rows = []
    for r in data["sleep"]:
        if r["wake_date"] < END - timedelta(days=40):
            continue
        k = (r["wake_date"] - START).days
        stage = 0 if k % 10 == 3 else 60.0
        rows.append({"wake_date": r["wake_date"], "start_at": r["start_at"], "end_at": r["end_at"], "asleep_min": r["asleep_min"],
                     "awake_min": 10.0, "deep_min": stage, "rem_min": stage, "core_min": stage, "is_main": True,
                     "device": None if k % 10 == 3 else ("Apple Watch" if k % 3 else None)})
        if k % 5 == 0:   # a nap; and an iPhone-only session that never counts
            rows.append({"wake_date": r["wake_date"], "start_at": at(r["wake_date"], 13.5), "end_at": at(r["wake_date"], 14.2),
                         "asleep_min": 40.0, "awake_min": 0.0, "deep_min": None, "rem_min": None, "core_min": 40.0,
                         "is_main": False, "device": "Apple Watch Series 9"})
            rows.append({"wake_date": r["wake_date"], "start_at": at(r["wake_date"], -1.0), "end_at": at(r["wake_date"], 9.0),
                         "asleep_min": 600.0, "awake_min": 0.0, "deep_min": 0, "rem_min": None, "core_min": 0,
                         "is_main": True, "device": "iPhone"})
    rows.sort(key=lambda x: x["start_at"])
    nights = [{"date": r["date"], "sleep_score": r["sleep_score"], "s": r["details"]["sleep"]}
              for r in data["scores"] if "sleep" in r["details"] and END - timedelta(days=13) <= r["date"] <= END]
    short = [r for r in rows if r["wake_date"] >= END - timedelta(days=3)]
    return [{"rows": rows, "nights": nights, "start": END - timedelta(days=13), "end": END},
            {"rows": rows, "nights": nights[:5], "start": END - timedelta(days=20), "end": END - timedelta(days=9)},
            {"rows": short, "nights": [], "start": END - timedelta(days=13), "end": END}]


def fitness_case(data: dict) -> dict:
    vo2 = [{"date": r["date"], "value": r["value"]} for r in data["metrics"]
           if r["metric"] == "vo2_max" and r["date"] > END - timedelta(days=365)]
    old = [r for r in vo2 if r["date"] < END - timedelta(days=120)]
    vo2_runs = [{"rows": vo2, "birth": date(1990, 5, 17), "sex": "male", "end": END, "lang": "zh"},
                {"rows": vo2, "birth": date(1971, 1, 2), "sex": "female", "end": END, "lang": "en"},
                {"rows": vo2, "birth": None, "sex": "male", "end": END, "lang": "zh"},
                {"rows": vo2, "birth": date(1990, 5, 17), "sex": None, "end": END, "lang": "zh"},
                {"rows": old, "birth": date(2000, 3, 9), "sex": "male", "end": END, "lang": "zh"},
                {"rows": vo2[:2], "birth": date(1990, 5, 17), "sex": "female", "end": END, "lang": "zh"},
                {"rows": [{"date": END, "value": 75.0}, {"date": END, "value": 75.0}, {"date": END, "value": 75.0}],
                 "birth": date(1990, 5, 17), "sex": "male", "end": END, "lang": "zh"},
                {"rows": [], "birth": date(1990, 5, 17), "sex": "male", "end": END, "lang": "zh"}]
    zrows = [{"date": r["date"], "strain": r["strain"], "hr_max": 186.0 if r["date"].day % 2 else None,
              "z": r["details"]["load"].get("zone_minutes"),
              "missing": "这天没有原始心率" if r["date"].day % 3 == 0 else None}
             for r in data["scores"] if r["date"] > END - timedelta(days=14)]
    workouts = [{"id": 7, "date": "2026-10-18", "name_zh": "跑步", "type": "running", "start_at": "2026-10-18T07:10:00+11:00",
                 "duration_min": 42.5, "avg_hr": 151.0, "max_hr": 176.0, "trimp": 88.4, "zone_minutes": [1, 5, 20, 14, 2.5],
                 "flags": {"hr_recovery_bpm": 24.0}}]
    zone_runs = [{"rows": zrows, "workouts": workouts, "with": True}, {"rows": zrows, "workouts": None, "with": True},
                 {"rows": zrows, "workouts": workouts, "with": False}, {"rows": [], "workouts": [], "with": True}]
    return {"vo2": vo2_runs, "zones": zone_runs, "zones_start": END - timedelta(days=13), "end": END,
            "levels": [None, 0.0, 5.0, 19.9, 20.0, 39.99, 40.0, 60.0, 79.5, 80.0, 95.0]}


# ---------------------------------------------------------------- the iOS loader (Watch2WhoopTests/TrendsSeriesTests.swift)
IOS_DAYS = 120


def _secs(t: datetime) -> float:
    return t.timestamp()


def ios_case() -> dict:
    """The last 120 days of the person as the phone's local tables (the pull streams' shapes: daily_scores.payload = the
    scores body, sleep_sessions with the sleep body as payload, daily_metrics, records kind `weight`) and the inputs the
    server's load would read for the same data: the Swift loader must turn the tables into the same data. The phone has
    no stress_daily / healthspan_weekly / workouts here (M2 / M4 / M1c), so those blocks are absent."""
    data = person()
    lo = END - timedelta(days=IOS_DAYS - 1)
    scores = [r for r in data["scores"] if r["date"] >= lo]
    metrics = [r for r in data["metrics"] if r["date"] >= lo]
    hrr = {d: v for d, v in data["hrr_apple"].items() if d >= lo}
    sleep = [r for r in data["sleep"] if r["wake_date"] >= lo]
    weights = {d: v for d, v in data["weights"].items() if d >= lo}
    sessions = []
    for i, r in enumerate(sleep):
        a = r["asleep_min"]
        stages = {"deep_min": _r(a * 0.18) if a else None, "core_min": _r(a * 0.55) if a else None,
                  "rem_min": _r(a * 0.22) if a else None, "awake_min": 12.0, "unspecified_min": 0.0}
        sessions.append({"id": f"s:{i}", "wake_date": r["wake_date"], "start_at": r["start_at"], "end_at": r["end_at"],
                         "asleep_min": a, **stages, "is_main": True, "source": "app", "device": "Apple Watch"})
        if i % 6 == 2:   # a nap (not main) and a shorter second main sleep of the same night
            sessions.append({"id": f"n:{i}", "wake_date": r["wake_date"], "start_at": at(r["wake_date"], 14.0),
                             "end_at": at(r["wake_date"], 14.5), "asleep_min": 28.0, "deep_min": None, "core_min": 28.0,
                             "rem_min": None, "awake_min": 2.0, "unspecified_min": None, "is_main": False, "source": "app",
                             "device": "Apple Watch"})
            sessions.append({"id": f"m:{i}", "wake_date": r["wake_date"], "start_at": at(r["wake_date"], 9.0),
                             "end_at": at(r["wake_date"], 9.5), "asleep_min": 20.0, "deep_min": None, "core_min": 20.0,
                             "rem_min": None, "awake_min": 0.0, "unspecified_min": None, "is_main": True, "source": "ow",
                             "device": "Apple Watch"})
    sessions.sort(key=lambda x: x["start_at"])
    tables = {
        "daily_scores": [{"date": r["date"], "recovery": r["recovery"], "sleep_score": r["sleep_score"], "strain": r["strain"],
                          "payload": {**r, "origin": "server"}} for r in scores],
        "daily_metrics": [{"date": r["date"], "metric": r["metric"], "value": r["value"]} for r in metrics]
        + [{"date": d, "metric": "heart_rate_recovery_one_minute", "value": v} for d, v in sorted(hrr.items())],
        "sleep_sessions": [{"id": s["id"], "date": s["wake_date"], "start": _secs(s["start_at"]), "end": _secs(s["end_at"]),
                            "is_main": s["is_main"], "source": s["source"],
                            "payload": {k: (_secs(v) if isinstance(v, datetime) else v) for k, v in s.items()
                                        if k not in ("wake_date", "start_at", "end_at")}
                            | {"start": _secs(s["start_at"]), "end": _secs(s["end_at"])}} for s in sessions],
        "records_weight": [{"id": f"w:{d.isoformat()}", "date": d, "payload": {"date": d, "weight_kg": v}}
                           for d, v in sorted(weights.items())],
    }
    mains = {}
    for s in sessions:   # what the server's DISTINCT ON (wake_date) … ORDER BY asleep_min DESC reads
        if s["is_main"] and (s["wake_date"] not in mains or (s["asleep_min"] or -1) > (mains[s["wake_date"]]["asleep_min"] or -1)):
            mains[s["wake_date"]] = s
    trends_data = {"can": {"scores": True, "metrics": True, "sleep": True, "weight": True, "training": True},
                   "scores": scores, "metrics": [r for r in metrics if r["metric"] in ("resting_heart_rate",
                   "heart_rate_variability_sdnn", "vo2_max", "step_count", "active_energy_burned", "basal_energy_burned",
                   "time_in_daylight", "oxygen_saturation", "respiratory_rate", "apple_sleeping_wrist_temperature")],
                   "sleep": [{"wake_date": d, "start_at": m["start_at"], "end_at": m["end_at"], "asleep_min": m["asleep_min"],
                              "in_bed": (m["end_at"] - m["start_at"]).total_seconds() / 60} for d, m in sorted(mains.items())],
                   "hrr_apple": hrr, "weights": weights}
    month = "2026-10"
    in_month = lambda d: d.isoformat()[:7] == month   # noqa: E731
    month_in = {"month": month, "today": END,
                "scores": {r["date"]: {"recovery": r["recovery"], "sleep_score": r["sleep_score"], "strain": r["strain"]}
                           for r in scores if in_month(r["date"])},
                "present": {"sleep": {d for d in mains if in_month(d)}, "metrics": {r["date"] for r in tables["daily_metrics"]
                                                                                  if in_month(r["date"])}},
                "firsts": [min(r["date"] for r in scores), min(mains), min(r["date"] for r in tables["daily_metrics"])],
                "hidden": []}
    night_scores = {r["date"]: {k: r[k] for k in ("sleep_score", "sleep_actual_min", "sleep_need_min", "sleep_debt_min", "flags")}
                    for r in scores}
    vo2 = [{"date": r["date"], "value": r["value"]} for r in metrics if r["metric"] == "vo2_max"]
    detail_nights = [{"date": r["date"], "sleep_score": r["sleep_score"], "s": r["details"]["sleep"]}
                     for r in scores if "sleep" in r["details"] and r["date"] >= END - timedelta(days=13)]
    return {"tz": TZ_NAME, "end": END, "today": END, "days": 30, "now_hour": 14, "tables": tables,
            "profile": {"birth_date": date(1990, 5, 17), "sex": "M"},
            "trends_data": trends_data, "month": month_in, "nights": {"sessions": sessions, "scores": night_scores},
            "detail_nights": detail_nights, "vo2": vo2,
            # filled by the server only while M2 / M4 / M1c are not on the phone: the Swift test leaves them out
            "server_only": {"series": ["stress", "resilience", "body_age", "hr_recovery"],
                            "status": ["resilience", "body_battery", "hr_recovery"]}}


def all_cases() -> dict:
    s = series_case()
    data = s["data"]
    return {"series": s, "month": month_case(data), "nights": nights_case(data), "sleep_detail": sleep_detail_case(data),
            "fitness": fitness_case(data)}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--dump":
        sys.exit("usage: python3 cases_series.py --dump FILE")
    Path(sys.argv[2]).write_text(json.dumps(dump(all_cases()), ensure_ascii=False))
    print(f"wrote {sys.argv[2]}")
