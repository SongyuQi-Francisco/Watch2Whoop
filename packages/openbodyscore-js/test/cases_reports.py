"""Cross-language golden cases of package M5 (docs/ios-native-2.0.md sections 2.1 and 4): the weekly / monthly report and the
journal analysis (two-group means, OLS with Newey-West standard errors, Benjamini-Hochberg) on synthetic inputs, run by the
Python reference (body_engine/tests/test_js_golden_reports.py, which writes test/golden/reports.json and reports_local.json
with BODY_WRITE_GOLDEN=1) and replayed on the bundle (test/reports.test.js).

Standard library only, synthetic data only (a made-up person in Australia/Sydney). Random numbers come from
random.Random (integer arithmetic) and every float input is rounded, so the inputs do not depend on the platform's libm:
- regression: the matrix inverse (regular, singular), the normal p-value over a grid of z (both branches of the erfc),
  Benjamini-Hochberg (ties, one test, none) and ols_hac on designs with gaps in the days, unsorted days and two rows per day;
- regress: one outcome's second version — a usual case, too few yes days, too few residual degrees of freedom, a behaviour
  that is the Tuesday dummy (collinear), zero residuals, an all-zero behaviour, no observations;
- analysis: 730 days of journal (lags in calendar days: one item logged every 4th day), items with an effect, without, a
  duplicate item (BH ties), a Tuesday-only item, an all-zero item, a rare item, a numeric item with a threshold; missing
  scores / metrics / covariates, an HRV of 0; windows 14 / 90 / 730 days, with and without regression;
- reports: the report on 70 days (week and month, the end of DST inside), each block with its edge cases (no stress table,
  a block's input absent = taken from the server's document on the phone, SparkyFitness errors, nutrition summary scope,
  scopes hidden, an end before any data).
In the dump, instants are epoch milliseconds, dates ISO strings (cases.dump).
"""
import json
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cases import dump  # noqa: E402

TZ_NAME = "Australia/Sydney"
TZ = ZoneInfo(TZ_NAME)
LAST = date(2026, 10, 20)          # last day with data (the end of DST, 2026-10-04, is inside the report windows)


def noise(r: random.Random) -> float:
    """About N(0, 1) without libm: the Irwin-Hall sum of 12 uniforms minus 6."""
    s = 0.0
    for _ in range(12):
        s += r.random()
    return s - 6.0


# ---------------------------------------------------------------- regression
def regression_case() -> dict:
    r = random.Random(11)
    mats = [
        [[4.0, 1.0, 0.5], [1.0, 3.0, 0.25], [0.5, 0.25, 2.0]],
        [[2.0, 1.0], [1.0, 2.0]],
        [[1.0, 2.0, 3.0], [2.0, 4.0, 6.0], [3.0, 6.0, 10.0]],      # singular (second column)
        [[0.0, 0.0], [0.0, 0.0]],                                  # singular, scale 0
    ]
    zs = [0.0, 0.001, 0.3, 1.0, 1.4, 1.959964, 1.96, 2.5, 2.8284, 2.9, 3.5, 5.0, 8.0, 12.0, 38.0, 40.0, -1.96]
    bhs = [[0.01, 0.04, 0.03, 0.2], [0.05, 0.05, 0.05], [0.3], [], [0.001, 0.9, 0.04, 0.04, 0.5, 0.0]]
    ols = []
    for name, n, gap, shuffle in (("daily", 60, 1, False), ("gappy", 45, 3, False), ("unsorted", 50, 1, True),
                                  ("two_per_day", 40, 0, False)):
        days, y, x = [], [], []
        d0 = date(2026, 3, 1)
        for i in range(n):
            d = d0 + timedelta(days=i * gap if gap else i // 2)
            b = 1.0 if r.random() < 0.4 else 0.0
            s = round(8 + 6 * r.random(), 1)
            h = round(6 + 2 * r.random(), 2)
            e = round(noise(r) * 4, 2)
            days.append(d)
            x.append([b, s, h])
            y.append(round(50 + 5 * b - 1.1 * s + 2 * h + e, 2))
        if shuffle:
            order = list(range(n))
            r.shuffle(order)
            days, y, x = [days[i] for i in order], [y[i] for i in order], [x[i] for i in order]
        ols.append({"name": name, "days": days, "y": y, "x": x, "names": ["behavior", "strain", "sleep"]})
    return {"inverse": mats, "norm_p": zs, "bh": bhs, "ols": ols}


def _obs(r: random.Random, n: int, start: date, behaviour, y_of=None) -> list[dict]:
    out = []
    for i in range(n):
        d = start + timedelta(days=i)
        b = behaviour(i, d)
        s = round(7 + 8 * r.random(), 1)
        h = round(5.5 + 3 * r.random(), 2)
        y = y_of(i, b, s, h) if y_of else round(60 + 4 * b - 1.2 * s + 1.5 * h + 5 * noise(r), 1)
        out.append({"date": d, "behavior": b, "y": y, "strain": s, "sleep_h": h})
    return out


def regress_case() -> list[dict]:
    r = random.Random(23)
    s = date(2026, 5, 4)   # a Monday
    return [
        {"name": "usual", "higher": True, "obs": _obs(r, 70, s, lambda i, d: 1.0 if r.random() < 0.35 else 0.0)},
        {"name": "lower_is_better", "higher": False, "obs": _obs(r, 70, s, lambda i, d: 1.0 if r.random() < 0.5 else 0.0)},
        {"name": "few_yes", "higher": True, "obs": _obs(r, 60, s, lambda i, d: 1.0 if i in (3, 9, 30) else 0.0)},
        {"name": "few_df", "higher": True, "obs": _obs(r, 25, s, lambda i, d: float(i % 2))},
        {"name": "tuesday_only", "higher": True, "obs": _obs(r, 63, s, lambda i, d: 1.0 if d.weekday() == 1 else 0.0)},
        {"name": "zero_residuals", "higher": True, "obs": _obs(r, 40, s, lambda i, d: float(i % 3 == 0), lambda i, b, st, h: 0.0)},
        {"name": "all_zero", "higher": True, "obs": _obs(r, 40, s, lambda i, d: 0.0)},
        {"name": "empty", "higher": True, "obs": []},
    ]


# ---------------------------------------------------------------- journal analysis (730 days)
ITEMS = [  # (key, label, kind, unit): the order is the server's (sort, key)
    ("sauna", "桑拿", "bool", None), ("alcohol", "喝酒", "bool", None), ("coffee", "咖啡", "bool", None),
    ("sauna_copy", "桑拿（副本）", "bool", None), ("tuesday_class", "周二课程", "bool", None),
    ("never", "从不", "bool", None), ("rare", "偶尔", "bool", None), ("gappy", "隔几天记", "bool", None),
    ("coffee_cups", "咖啡杯数", "number", "杯"),
]


def journal_case(days: int = 730, last: date = LAST) -> dict:
    """The phone's journal inputs (analysis.analyze_data): items, entries, scores {date: …}, metrics {date: …}."""
    r = random.Random(37)
    first = last - timedelta(days=days - 1)
    entries, scores, metrics = [], {}, {}
    e_prev, b_prev, strain_prev = 0.0, (False, False), 11.0
    for i in range(days + 1):   # the day after `last` carries the outcomes of the last day's behaviour
        d = first + timedelta(days=i)
        sauna = r.random() < 0.35
        alcohol = r.random() < 0.2
        strain = round(8 + 6 * r.random() + (3 if sauna else 0), 1)
        sleep = round(400 + 50 * noise(r) - (25 if b_prev[1] else 0))
        e_prev = 0.5 * e_prev + 5 * noise(r)               # autocorrelated errors (what Newey-West is for)
        # yesterday's behaviour and strain drive today's outcomes (the analysis pairs behaviour day D with D + 1)
        rec = round(min(100.0, max(0.0, 62 - 1.1 * (strain_prev - 11) + 0.06 * (sleep - 420) + 5 * b_prev[0]
                                   - 7 * b_prev[1] + e_prev)), 1)
        rhr = round(54 + 3 * b_prev[1] + 1.5 * noise(r), 1)
        hrv = round(48 - 6 * b_prev[1] + 5 * noise(r), 1)
        if i % 97 == 50:
            hrv = 0.0                                      # ln HRV undefined: left out of that outcome
        if i % 23 != 7:                                    # some days have no score row
            scores[d] = {"recovery": rec if i % 31 != 3 else None, "sleep_actual_min": sleep if i % 29 != 5 else None,
                         "strain": strain if i % 37 != 11 else None}
        if i % 19 != 4:                                    # some days have no metrics
            metrics[d] = {"resting_heart_rate": rhr, "heart_rate_variability_sdnn": hrv}
        b_prev, strain_prev = (sauna, alcohol), strain
        if i >= days:
            continue
        logged = i % 7 != 6 or i < 30                       # most days are logged; no record = missing, not "no"
        if logged:
            entries += [{"date": d, "key": "sauna", "value": 1.0 if sauna else 0.0},
                        {"date": d, "key": "sauna_copy", "value": 1.0 if sauna else 0.0},
                        {"date": d, "key": "alcohol", "value": 1.0 if alcohol else 0.0},
                        {"date": d, "key": "coffee", "value": 1.0 if r.random() < 0.5 else 0.0},
                        {"date": d, "key": "tuesday_class", "value": 1.0 if d.weekday() == 1 else 0.0},
                        {"date": d, "key": "never", "value": 0.0},
                        {"date": d, "key": "rare", "value": 1.0 if i % 150 == 3 else 0.0},
                        {"date": d, "key": "coffee_cups", "value": float(r.randrange(0, 5))}]
        if i % 4 == 1:
            entries.append({"date": d, "key": "gappy", "value": 1.0 if r.random() < 0.4 else 0.0})
    items = [{"key": k, "label_zh": label, "kind": kind, "unit": unit} for k, label, kind, unit in ITEMS]
    return {"items": items, "entries": entries, "scores": scores, "metrics": metrics}


def analysis_runs() -> list[dict]:
    runs = []
    for days in (14, 90, 730):
        for regression in (False, True):
            runs.append({"end": LAST, "days": days, "regression": regression})
    runs += [{"end": LAST - timedelta(days=200), "days": 90, "regression": True},
             {"end": LAST, "days": 90, "regression": True, "key": "coffee_cups", "threshold": 1.0},
             {"end": LAST, "days": 365, "regression": False, "key": "coffee_cups", "threshold": 2.0},
             {"end": LAST, "days": 90, "regression": True, "key": "sauna"}]
    return runs


# ---------------------------------------------------------------- reports (70 days)
def at(d: date, h: float) -> datetime:
    return datetime.combine(d, time(0), TZ) + timedelta(minutes=round(h * 60))


def report_data() -> dict:
    """The data of insights/reports.compute (what reports.load reads), for the owner (every scope)."""
    r = random.Random(53)
    first = LAST - timedelta(days=69)
    scores, stress, sessions, workouts, log, weights, nut = [], [], [], [], [], [], []
    for i in range(70):
        d = first + timedelta(days=i)
        bed = 22.5 + 2.2 * r.random()                     # 22:30 … 00:42 (some after midnight)
        wake = 6.0 + 2.0 * r.random()
        start, end = at(d - timedelta(days=1), bed), at(d, wake)
        stages = i % 5 != 2
        sessions.append({"wake_date": d, "start_at": start, "end_at": end, "asleep_min": round((end - start).total_seconds() / 60 - 25),
                         "awake_min": 25, "deep_min": 70 if stages else None, "rem_min": 90 if stages else None,
                         "core_min": 200 if stages else None, "is_main": True,
                         "device": "Apple Watch" if i % 9 != 4 else None})
        if i % 11 == 6:
            sessions.append({"wake_date": d, "start_at": at(d, 14.0), "end_at": at(d, 14.5), "asleep_min": 28, "awake_min": 2,
                             "deep_min": None, "rem_min": None, "core_min": 28, "is_main": False, "device": "Apple Watch"})
        if i % 13 == 8:                                    # the phone's in-bed copy: not a watch session
            sessions.append({"wake_date": d, "start_at": start, "end_at": end, "asleep_min": 400, "awake_min": 0,
                             "deep_min": None, "rem_min": None, "core_min": None, "is_main": True, "device": "iPhone"})
        if i % 17 == 9:
            continue                                       # no score row that day
        rec = round(25 + 70 * r.random()) if i % 10 != 3 else None
        sleep = round(330 + 140 * r.random()) if i % 12 != 5 else None
        scores.append({
            "date": d, "recovery": rec, "sleep_score": round(50 + 45 * r.random()), "sleep_actual_min": sleep,
            "sleep_performance": round(60 + 40 * r.random()), "sleep_debt_min": round(90 * r.random()),
            "strain": round(4 + 15 * r.random(), 1) if i % 8 != 2 else None,
            "trimp": round(150 * r.random(), 1) if i % 6 != 1 else None,
            "muscle_load": round(4000 * r.random(), 1) if i % 3 == 0 else None,
            "ctl": round(40 + i * 0.1, 2), "atl": round(45 + 5 * r.random(), 2), "tsb": round(-5 + 10 * r.random(), 2),
            "rhr_night": round(50 + 8 * r.random(), 1), "hrv_night_ln": round(3.6 + 0.4 * r.random(), 4) if i % 7 != 0 else None,
            "s": {"bedtime": start.isoformat(timespec="minutes"), "wake_time": end.isoformat(timespec="minutes")} if i % 9 != 5 else None,
            "rhr_daily": i % 14 == 3})
        stress.append({"date": d, "score": round(3 * r.random(), 2) if i % 5 != 4 else None, "high_h": r.randrange(0, 4)})
        if i % 3 != 1:
            workouts.append({"local_date": d, "name_zh": ["跑步", "力量训练", "游泳"][i % 3], "duration_min": round(20 + 50 * r.random(), 2)})
        if i % 4 == 0:
            cat = [None, "strength", "cardio", ""][i % 16 // 4]
            log.append({"date": d.isoformat(), "name": f"训练 {i}", "exercises": [{"name": "深蹲", "category": cat, "sets": []}]})
        if i % 6 == 0:
            weights.append({"date": d.isoformat(), "weight_kg": round(82 - i * 0.03 + noise(r) * 0.3, 1)})
        elif i % 6 == 3:
            weights.append({"date": d.isoformat(), "waist_cm": 86.0})
        logged = i % 4 != 2
        goal = {"name": "减脂" if i < 40 else "维持", "added_sugar_max_g": 25} if i % 15 != 7 else None
        nut.append({"date": d.isoformat(), "logged": logged, "calories": round(1800 + 600 * r.random(), 1) if logged else 0.0,
                    "protein_g": round(110 + 60 * r.random(), 1) if logged else 0.0,
                    "sugar_g": round(40 * r.random(), 1) if logged and i % 5 else None,
                    "added_sugar_g": round(35 * r.random(), 1) if logged and i % 3 else None,
                    "sugary_drinks": i % 7 == 0 and logged and 1 or 0,
                    "adherence": {"calories": (i % 2 == 0) if logged and goal else None,
                                  "protein": (i % 3 != 0) if logged and goal else None},
                    "goal": goal})
    weights.insert(0, {"date": (first - timedelta(days=20)).isoformat(), "weight_kg": 84.0})   # before the window
    # Instants in UTC, as the database hands them over when its session time zone is not the user's: Python subtracts two
    # datetimes that share one ZoneInfo as wall clocks, so sleep_detail.asleep_minutes would be an hour off across a DST
    # change; the bundle (and these goldens) work in instants.
    for s in sessions:
        s["start_at"], s["end_at"] = s["start_at"].astimezone(timezone.utc), s["end_at"].astimezone(timezone.utc)
    return {"tz": TZ_NAME, "can": ["journal", "nutrition", "nutrition_summary", "scores", "sleep", "training", "weight"],
            "nutrition_mode": "full", "scores": scores, "stress": stress, "sleep_sessions": sessions,
            "training": {"log": log, "error": None, "workouts": workouts}, "weights": {"rows": weights},
            "nutrition": {"days": nut}, "journal": journal_case(160, LAST)}


ERR = "SparkyFitness：SparkyError: 401 未授权"
VARIANTS = {   # name -> {"drop": keys removed from data, "set": keys replaced}
    "base": {},
    "no_stress_table": {"set": {"stress": None}},
    "phone_without_server_blocks": {"drop": ["stress", "training", "weights", "nutrition", "journal"]},
    "sparky_errors": {"set": {"training": {"log": None, "error": ERR, "workouts": None}, "weights": {"error": ERR},
                              "nutrition": {"error": ERR}}},
    "nutrition_summary": {"set": {"can": ["journal", "nutrition_summary", "scores", "sleep", "training", "weight"],
                                  "nutrition_mode": "summary"}},
    "scores_only": {"set": {"can": ["scores"], "nutrition_mode": None}},
    "scores_sleep_training": {"set": {"can": ["scores", "sleep", "training"], "nutrition_mode": None}},
    "nothing": {"set": {"can": [], "nutrition_mode": None}},
    "journal_results_failed": {"drop": ["journal"], "set": {"journal_results": None}},
}


def variant(data: dict, name: str) -> dict:
    v = VARIANTS[name]
    out = {k: val for k, val in data.items() if k not in v.get("drop", ())}
    out.update(v.get("set", {}))
    return out


def report_runs() -> list[dict]:
    runs = [{"period": p, "end": e, "variant": "base"} for p in ("week", "month")
            for e in (LAST, LAST - timedelta(days=16), LAST - timedelta(days=45))]
    runs.append({"period": "week", "end": LAST - timedelta(days=200), "variant": "base"})   # before any data
    runs += [{"period": p, "end": LAST, "variant": v} for v in VARIANTS if v != "base" for p in ("week", "month")]
    return runs


def all_cases() -> dict:
    return {"regression": regression_case(), "regress": regress_case(), "journal": journal_case(),
            "analysis_runs": analysis_runs(), "report": report_data(), "report_runs": report_runs(), "variants": VARIANTS}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--dump":
        sys.exit("usage: python3 cases_reports.py --dump FILE")
    Path(sys.argv[2]).write_text(json.dumps(dump(all_cases()), ensure_ascii=False))
    print(f"wrote {sys.argv[2]}")
