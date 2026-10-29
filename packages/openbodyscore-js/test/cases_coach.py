"""Cross-language golden cases of package M4 (docs/ios-native-2.0.md sections 2.1 and 4): the coaches, the bedtime goal,
the sleep detail, the Today composition and the body-age loader on synthetic inputs, run by the Python reference
(body_engine/tests/test_js_golden_coach.py, which writes test/golden/coach.json and coach_local.json with
BODY_WRITE_GOLDEN=1) and replayed on the bundle (test/coach.test.js).

Standard library only, synthetic data only: the made-up person of cases_series.person() (420 days ending 2026-10-20 in
Australia/Sydney, the daylight-saving change of 2026-10-04 inside the ranges) plus what these views read besides:
- strain: score-row pairs at the recovery zone edges (33 / 34 / 66 / 67), without a recovery, an illness flag, TSB < −30,
  no strain yet (with and without a reason), no row at all;
- sleep coach: every 10th day of the person, with no goal, a goal with a wake time (short and long enough), a goal with a
  bedtime only, a goal after midnight, no recent wake times (the note), the DST morning;
- health monitor: the person's last days (incl. a resting heart rate from Apple's all-day value), < 14 nights, English;
- goals: no goal, a goal mid-plan, a goal reached, a paused goal, the DST night, a far-behind plan;
- sleep night: nights with stages / without stages / without a session, naps, two mains, without the scores share, English;
- today: scores_today on several days (load states, missing load days, no row), every recovery_empty code, sleep_today
  (with a timeline, without a night in the last 7 days), the hypnogram (overlaps, merges, gaps, the DST night), the frequent
  journal items;
- healthspan: from_parts + weekly_local over the person's history with and without strength logs (watch workouts,
  SparkyFitness sessions with / without durations, an import), a SparkyFitness error, weigh-ins / waist / height, the
  journal and an active plan; no birth date / no origin -> [].
In the dump, instants are epoch milliseconds, dates ISO strings (cases.dump).
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

TZ_NAME = CS.TZ_NAME
TZ = CS.TZ
END = CS.END
DST_DAY = date(2026, 10, 4)   # Australia/Sydney: 02:00 -> 03:00
at = CS.at
_r = CS._r


def person() -> dict:
    return CS.person()


def sessions(data: dict) -> list[dict]:
    """sleep_sessions rows (by start) from the person's main sleeps: stages, naps, a second main, a phone-only night.
    Minutes are integers (the table's columns)."""
    out = []
    for i, r in enumerate(data["sleep"]):
        d, a = r["wake_date"], round(r["asleep_min"])
        staged = i % 13 != 6 and bool(a)
        out.append({"wake_date": d, "start_at": r["start_at"], "end_at": r["end_at"], "asleep_min": a,
                    "deep_min": round(a * 0.17) if staged else None, "core_min": round(a * 0.56) if staged else None,
                    "rem_min": round(a * 0.21) if staged else None, "awake_min": 14 if staged else None,
                    "unspecified_min": None if staged else a, "is_main": True, "source": "app",
                    "device": "Apple Watch" if i % 13 != 6 else "iPhone"})
        if i % 5 == 1:   # an afternoon nap
            out.append({"wake_date": d, "start_at": at(d, 14.25), "end_at": at(d, 14.9), "asleep_min": 35,
                        "deep_min": None, "core_min": 35, "rem_min": None, "awake_min": 0, "unspecified_min": None,
                        "is_main": False, "source": "app", "device": "Apple Watch"})
        if i % 23 == 11:  # a second, shorter main sleep the same wake date (the longer one counts)
            out.append({"wake_date": d, "start_at": at(d, 9.5), "end_at": at(d, 10.5), "asleep_min": 50,
                        "deep_min": 5, "core_min": 40, "rem_min": 5, "awake_min": 3, "unspecified_min": None,
                        "is_main": True, "source": "app", "device": "Apple Watch"})
    out.sort(key=lambda s: s["start_at"])
    return out


def _score_rows(data: dict) -> dict:
    return {r["date"]: r for r in data["scores"]}


# ---------------------------------------------------------------- insights/coach.py
def strain_case(data: dict) -> list[dict]:
    rows = _score_rows(data)
    out = []
    base = {"strain": 11.2, "tsb": -4.0, "flags": {}}
    for rec in (33, 34, 66, 67, 100, 0, None):
        out.append({"today": {**base, "recovery": rec}, "yday": {"tsb": -12.0}, "d": END})
    out.append({"today": {**base, "recovery": 80, "flags": {"illness_warning": "生病"}}, "yday": {"tsb": -40.0}, "d": END})
    out.append({"today": {**base, "recovery": 50}, "yday": {"tsb": -31.5}, "d": END})
    out.append({"today": {**base, "recovery": 20, "strain": 15.0}, "yday": {"tsb": -45.0}, "d": END})
    out.append({"today": {**base, "recovery": 70, "strain": 19.5}, "yday": None, "d": END})
    out.append({"today": {**base, "recovery": 70, "strain": None, "flags": {"no_raw_hr": "今天还没有原始心率"}}, "yday": None, "d": END})
    out.append({"today": {**base, "recovery": 70, "strain": None}, "yday": {"tsb": None}, "d": END})
    out.append({"today": None, "yday": None, "d": END})
    for k in range(0, 60, 7):   # the person's own days
        d = END - timedelta(days=k)
        t, y = rows.get(d), rows.get(d - timedelta(days=1))
        pick = lambda r: None if r is None else {x: r[x] for x in ("recovery", "strain", "tsb", "flags")}   # noqa: E731
        out.append({"today": pick(t), "yday": pick(y), "d": d})
    return out


def _coach_rows(data: dict, d: date, n: int = 14) -> list[dict]:
    rows = _score_rows(data)
    out = []
    for k in range(n):
        x = rows.get(d - timedelta(days=k))
        if x is not None:
            out.append({"date": x["date"], "sleep_actual_min": x["sleep_actual_min"], "strain": x["strain"],
                        "s": (x["details"] or {}).get("sleep")})
    return out


def sleep_coach_case(data: dict) -> list[dict]:
    goals = [None,
             {"tonight_target_bedtime": "23:15", "target_wake": "06:30"},     # wake fixed: short of the need
             {"tonight_target_bedtime": "21:30", "target_wake": "07:30"},     # wake fixed: enough
             {"tonight_target_bedtime": "22:45", "target_wake": None},        # bedtime only: wake = bed + latency + need
             {"tonight_target_bedtime": "00:30", "target_wake": None},        # after midnight
             {"tonight_target_bedtime": "23:00", "target_wake": "19:30"}]     # an evening wake time (>= 18:00)
    out = []
    for k, d in enumerate([END - timedelta(days=j) for j in range(0, 120, 9)] + [DST_DAY, DST_DAY - timedelta(days=1)]):
        out.append({"rows": _coach_rows(data, d), "d": d, "goal": goals[k % len(goals)]})
    out.append({"rows": [], "d": END, "goal": None})                          # nothing: the "no wake time" note
    rows = _coach_rows(data, END)
    out.append({"rows": [{**r, "s": None} for r in rows], "d": END, "goal": None})   # no wake times, no latencies
    out.append({"rows": [{**r, "s": None} for r in rows], "d": END, "goal": goals[3]})
    return out


def monitor_case(data: dict) -> list[dict]:
    rows = _score_rows(data)
    apple = {r["date"]: r["value"] for r in data["metrics"] if r["metric"] == "resting_heart_rate"}

    def window(d: date, apple_last: bool = False) -> list[dict]:
        out = []
        for k in range(60, -1, -1):
            x = rows.get(d - timedelta(days=k))
            if x is None:
                continue
            last = k == 0
            rec = (x["details"] or {}).get("recovery")
            ln = None if x["recovery"] is None else round(math.log(40 + (x["date"].toordinal() * 7) % 30), 4)
            rhr = None if last and apple_last else 50.0 + (x["date"].toordinal() * 3) % 9
            out.append({"date": x["date"], "rhr_night": rhr, "hrv_night_ln": ln, "resp_rate_night": x["resp_rate_night"],
                        "rec": rec, "rhr_daily": bool(apple_last and last), "apple_rhr": apple.get(x["date"])})
        if apple_last and out:
            out[-1]["rhr_night"] = 61.0   # the night's value falls back to Apple's all-day one
        return out

    def spike(rows_: list[dict]) -> list[dict]:   # the last night off its range: high resting HR, low HRV
        return rows_[:-1] + [{**rows_[-1], "rhr_night": 72.0, "hrv_night_ln": round(math.log(18.0), 4)}]
    extra = [{"rows": spike(window(END)), "d": END, "lang": "zh"}, {"rows": spike(window(END)), "d": END, "lang": "en"}]
    return [{"rows": window(END), "d": END, "lang": "zh"}, {"rows": window(END), "d": END, "lang": "en"},
            {"rows": window(END, True), "d": END, "lang": "zh"}, {"rows": window(END - timedelta(days=30)), "d": END, "lang": "zh"},
            {"rows": window(END)[-10:], "d": END, "lang": "zh"}, {"rows": [], "d": END, "lang": "zh"},
            {"rows": window(DST_DAY + timedelta(days=1)), "d": DST_DAY + timedelta(days=1), "lang": "zh"}] + extra


# ---------------------------------------------------------------- goals/sleep.py + goals/store.py
def goals_case(data: dict) -> list[dict]:
    ss = sessions(data)
    goal = {"target_bedtime": "22:30", "target_wake": None, "target_duration_min": None, "pace_min_per_step": 20,
            "step_days": 3, "start_date": END - timedelta(days=10), "start_bedtime_off": 30, "status": "active"}
    out = []
    for night in (END, END - timedelta(days=1), DST_DAY - timedelta(days=1), DST_DAY, END - timedelta(days=40)):
        lo = night - timedelta(days=21)
        win = [s for s in ss if lo <= s["wake_date"] <= night + timedelta(days=1)]
        out.append({"goal": None, "sessions": win, "night": night, "need": 470})
        out.append({"goal": goal, "sessions": win, "night": night, "need": 470})
    win = [s for s in ss if END - timedelta(days=21) <= s["wake_date"] <= END + timedelta(days=1)]
    out.append({"goal": {**goal, "target_wake": "06:45", "target_duration_min": 450}, "sessions": win, "night": END, "need": None})
    out.append({"goal": {**goal, "status": "paused"}, "sessions": win, "night": END, "need": None})
    out.append({"goal": {**goal, "target_bedtime": "01:30", "start_bedtime_off": 120}, "sessions": win, "night": END,
                "need": 500})                                                            # reached already
    out.append({"goal": {**goal, "start_bedtime_off": 150, "pace_min_per_step": 15, "step_days": 2,
                         "start_date": END - timedelta(days=2)}, "sessions": win, "night": END, "need": 455.5})
    out.append({"goal": goal, "sessions": [], "night": END, "need": None})               # no nights at all
    return out


def goal_calls() -> list[dict]:
    """Direct calls of the small goals/sleep.py helpers (DST night_of, fmt edges, round5 ties)."""
    calls = [{"fn": "to_off", "args": [t]} for t in ("00:00", "23:00", "06:30", "12:00", "11:59", " 7:05", "13:30:00")]
    calls += [{"fn": "fmt", "args": [x]} for x in (None, 0, -60, 390, 719.5, -720, 1500, 2.5, 3.5)]
    calls += [{"fn": "round5", "args": [x]} for x in (2.5, 7.5, 12.4, -2.5, -7.5, 33.3)]
    calls += [{"fn": "night_of", "args": [at(d, h), TZ_NAME]} for d, h in
              ((DST_DAY, 1.5), (DST_DAY, 3.5), (DST_DAY, 11.99), (DST_DAY, 12.0), (date(2026, 4, 5), 2.5), (END, 23.0))]
    calls += [{"fn": "build_plan", "args": [60, -30, END, 20, 3]}, {"fn": "build_plan", "args": [-30, 0, END, 15, 1]}]
    return calls


# ---------------------------------------------------------------- sleep_night/view.py
def _night_scores(data: dict, lo: date, hi: date, rows: dict) -> dict:
    out = {}
    for d in (lo + timedelta(days=k) for k in range((hi - lo).days + 1)):
        x = rows.get(d)
        if x is None:
            continue
        det = x["details"] or {}
        ln = None if x["recovery"] is None else round(math.log(40 + (d.toordinal() * 7) % 30), 4)
        out[d] = {"date": d, "sleep_score": x["sleep_score"], "sleep_performance": x["sleep_performance"],
                  "sleep_actual_min": x["sleep_actual_min"], "sleep_need_min": x["sleep_need_min"],
                  "sleep_debt_min": x["sleep_debt_min"], "hrv_ms": None if ln is None else round(math.exp(ln), 1),
                  "rhr_night": 50.0 + (d.toordinal() * 3) % 9 + (6 if d == hi and d.day % 2 else 0),
                  "resp_rate_night": x["resp_rate_night"], "sleep": det.get("sleep"), "recovery": det.get("recovery"),
                  "flags": x["flags"]}
        if d == hi and d.day % 3 == 0 and out[d]["recovery"]:
            out[d]["recovery"] = {**out[d]["recovery"], "wrist_temp": 36.9, "spo2_night": 92.4}
    return out


def _segments(s: dict) -> list[dict]:
    """A stage timeline over the night: cycles of core / deep / rem with short awakes, one overlap and one tiny gap."""
    a, b = s["start_at"], s["end_at"]
    out, t, k = [], a - timedelta(minutes=5), 0
    while t < b:
        stage = ("core", "deep", "core", "rem", "awake")[k % 5]
        n = timedelta(minutes=(4 if stage == "awake" else 18 + (k * 7) % 25))
        out.append({"start": t, "end": t + n, "stage": stage})
        t += n + (timedelta(seconds=40) if k % 7 == 3 else timedelta(0)) - (timedelta(minutes=2) if k % 11 == 5 else timedelta(0))
        k += 1
    out.append({"start": a + timedelta(minutes=30), "end": a + timedelta(minutes=33), "stage": "core"})   # a duplicate source
    return out


def sleep_night_case(data: dict) -> list[dict]:
    rows = _score_rows(data)
    ss = sessions(data)
    out = []
    for day, can, lang in ((END, True, "zh"), (END - timedelta(days=1), True, "en"), (DST_DAY, True, "zh"),
                           (DST_DAY + timedelta(days=1), False, "zh"), (END - timedelta(days=33), True, "zh"),
                           (END - timedelta(days=60), True, "zh"), (END - timedelta(days=95), True, "en"),
                           (CS.START + timedelta(days=203), True, "zh"), (CS.START + timedelta(days=12), True, "zh")):
        lo = day - timedelta(days=30)
        win = [s for s in ss if lo <= s["wake_date"] <= day]
        mains = [s for s in win if s["is_main"] and s["wake_date"] == day]
        main = max(mains, key=lambda s: s["asleep_min"] or 0) if mains else None
        # raw stage segments of the night; each side builds the timeline with its hypnogram (timeline_of in the tests)
        segs = (_segments(main) if day.day % 4 else []) if main is not None else None
        before = [s["wake_date"] for s in ss if s["is_main"] and s["wake_date"] < day]
        after = [s["wake_date"] for s in ss if s["is_main"] and day < s["wake_date"] <= END]
        out.append({"tz": TZ_NAME, "today": END, "day": day, "can_scores": can, "sessions": win,
                    "scores": _night_scores(data, lo, day, rows) if can else None,
                    "prev": max(before) if before else None, "next": min(after) if after else None,
                    "segments": segs, "lang": lang})
    return out


def notes_calls() -> list[dict]:
    """sleep_night.notes on hand-made blocks: every note, the tone order, the cap of 6, all good."""
    night = {"start": "01:40", "deep_pct": 8, "rem_pct": 12, "efficiency_pct": 72.5, "latency_min": 48, "waso_min": 55,
             "awakenings": 4}
    score = {"need_min": 480, "actual_min": 360, "parts": [{"key": "duration", "score": 45, "weight": 0.4},
                                                            {"key": "stages", "score": 70, "weight": 0.2},
                                                            {"key": "consistency", "score": None, "weight": 0.2},
                                                            {"key": "interruptions", "score": 90, "weight": 0.2}]}
    base = {"avg30": {"bedtime": "23:10"}}
    vitals = [{"key": "hrv", "value": 30, "unit": "ms", "avg30": 45, "delta": -15},
              {"key": "rhr", "value": 60, "unit": "bpm", "avg30": 53, "delta": 7},
              {"key": "resp_rate", "value": 16.2, "unit": "rpm", "avg30": 14.9, "delta": 1.3},
              {"key": "spo2", "value": 92.5, "unit": "%", "avg30": 96.0, "delta": -3.5},
              {"key": "wrist_temp", "value": 35.2, "unit": "°C", "avg30": 35.95, "delta": -0.75}]
    good_night = {"start": "22:50", "deep_pct": 18, "rem_pct": 22, "efficiency_pct": 92.0, "latency_min": 10, "waso_min": 12,
                  "awakenings": 1}
    good_score = {**score, "actual_min": 470, "parts": [{**p, "score": 85} for p in score["parts"]]}
    return [{"night": night, "score": score, "base": base, "vitals": vitals, "flags": {"illness_warning": "x"}, "lang": "zh"},
            {"night": night, "score": score, "base": base, "vitals": vitals, "flags": None, "lang": "en"},
            {"night": {**night, "start": "21:30", "efficiency_pct": 80.0, "latency_min": 31, "waso_min": 21, "awakenings": 0},
             "score": {**score, "actual_min": 430, "parts": [{**p, "score": 60} for p in score["parts"]]},
             "base": base, "vitals": [], "flags": {}, "lang": "zh"},
            {"night": good_night, "score": good_score, "base": base, "vitals": [], "flags": {}, "lang": "zh"},
            {"night": good_night, "score": None, "base": {}, "vitals": [], "flags": None, "lang": "en"},
            {"night": {**good_night, "start": "01:30"}, "score": good_score, "base": base,
             "vitals": [v for v in vitals if v["key"] != "spo2"], "flags": None, "lang": "zh"},
            {"night": {**good_night, "start": "01:30"}, "score": good_score, "base": base,
             "vitals": [{**v, "delta": -v["delta"]} if v["key"] == "wrist_temp" else v for v in vitals if v["key"] != "spo2"],
             "flags": None, "lang": "en"},
            {"night": {**good_night, "start": "21:00"}, "score": good_score, "base": base, "vitals": [],
             "flags": {"illness_warning": "x"}, "lang": "zh"},
            {"night": {**good_night, "start": "21:00"}, "score": good_score, "base": base, "vitals": [], "flags": {},
             "lang": "en"}]


# ---------------------------------------------------------------- web/views.py + web/recovery_empty.py
FIELDS = ("recovery", "sleep_score", "sleep_performance", "sleep_actual_min", "sleep_need_min", "sleep_debt_min",
          "hrv_night_ms", "rhr_night", "strain", "trimp", "muscle_load", "ctl", "atl", "tsb", "flags")


def today_case(data: dict) -> dict:
    rows = _score_rows(data)

    def week(d: date) -> dict:
        out = {}
        for k in range(6, -1, -1):
            x = rows.get(d - timedelta(days=k))
            if x is None:
                continue
            r = {"date": x["date"], **{f: x.get(f) for f in FIELDS if f != "hrv_night_ms"}}
            r["hrv_night_ms"] = None if x["recovery"] is None else 40.0 + (x["date"].toordinal() * 7) % 30
            if k in (2, 4) and d.day % 2:
                r["flags"] = {**(r["flags"] or {}), "load_missing": "这天没有负荷数据"}
            out[x["date"]] = r
        return out

    def last_of(d: date, key: str, cols: tuple) -> dict | None:
        for k in range(1, 400):
            x = rows.get(d - timedelta(days=k))
            if x is not None and x[key] is not None:
                return {c: x[c] for c in ("date", *cols)}
        return None

    scores = []
    for d in [END - timedelta(days=k) for k in range(0, 70, 3)] + [CS.START + timedelta(days=203)]:
        wk = week(d)
        if d.day % 5 == 0 and d in wk:   # a fatigued / fresh yesterday
            y = d - timedelta(days=1)
            if y in wk:
                wk[y] = {**wk[y], "tsb": -35.4 if d.day % 2 else 12.5, "ctl": 55.5, "atl": 90.9}
        scores.append({"rows": wk, "day": d, "last": last_of(d, "recovery", ("recovery", "sleep_score")),
                       "last_sleep": last_of(d, "sleep_score", ("sleep_score", "sleep_actual_min", "sleep_need_min",
                                                               "sleep_debt_min")),
                       "computed_at": datetime(2026, 10, 20, 9, 10, tzinfo=timezone.utc),
                       "empty": None if (wk.get(d) or {}).get("recovery") is not None else
                       {"code": "pending", "label": "还在计算", "cta": None, "action": None},
                       "lang": "en" if d.day % 7 == 0 else "zh"})
    empty = []
    base = dict(day=END, today=None, local_today=END, needs_consent=False, origin=END - timedelta(days=100), any_data=True,
                recent_watch_sleep=True)
    for change in ({"today": {"recovery": 55, "flags": {}}}, {"needs_consent": True}, {"origin": None},
                   {"origin": None, "any_data": False}, {"origin": END + timedelta(days=1)},
                   {"today": None, "recent_watch_sleep": False}, {"today": {"recovery": None, "flags": {"no_sleep": "x"}}},
                   {"today": {"recovery": None, "flags": {"no_sleep": "x"}}, "day": END - timedelta(days=2)},
                   {"today": {"recovery": None, "flags": {"hrv_missing": "x"}}},
                   {"today": {"recovery": None, "flags": {"baseline_insufficient": "x"}}},
                   {"today": {"recovery": None, "flags": {}}}, {"today": None}):
        for lang in ("zh", "en"):
            empty.append({**base, **change, "lang": lang})
    ss = sessions(data)
    sleep = []
    for d in (END, END - timedelta(days=1), DST_DAY, DST_DAY + timedelta(days=1), CS.START + timedelta(days=206),
              CS.START - timedelta(days=3)):
        win = [s for s in ss if s["is_main"] and d - timedelta(days=7) <= s["wake_date"] <= d]
        mains = [s for s in win if s["wake_date"] == max((x["wake_date"] for x in win), default=None)]
        main = max(mains, key=lambda s: s["asleep_min"] or 0) if mains else None
        # segments of the night sleep_today_compute picks (each side builds the timeline with its hypnogram)
        sleep.append({"sessions": win, "day": d, "segments": _segments(main) if main is not None and d.day % 2 == 0 else None})
    hyp = []
    for s in [x for x in ss if x["is_main"]][-12:] + [x for x in ss if x["is_main"] and x["wake_date"] in (DST_DAY, date(2026, 4, 5))]:
        hyp.append({"segments": _segments(s), "start": s["start_at"], "end": s["end_at"]})
    items = [{"key": f"item{k}", "label_zh": f"项目{k}", "kind": "bool" if k % 3 else "number", "unit": None if k % 3 else "杯",
              "category": ("habit", "food", "body")[k % 3], "sort": (k * 7) % 5 if k != 4 else None} for k in range(12)]
    items.sort(key=lambda i: (i["sort"] is None, i["sort"] or 0, i["key"]))
    journal = [{"items": items, "counts": {"item3": 9, "item7": 9, "item1": 2, "gone": 30, "item10": 4}, "days": 30,
                "limit": 8, "min_items": 6},
               {"items": items, "counts": {f"item{k}": k for k in range(12)}, "days": 14, "limit": 4, "min_items": 6},
               {"items": items, "counts": {}, "days": 30, "limit": 8, "min_items": 6},
               {"items": [], "counts": {"x": 1}, "days": 30, "limit": 8, "min_items": 6}]
    return {"tz": TZ_NAME, "scores": scores, "empty": empty, "sleep": sleep, "hypnogram": hyp, "journal": journal}


# ---------------------------------------------------------------- insights/healthspan_store.py (the loader)
def healthspan_parts(data: dict, variant: str = "full") -> dict:
    """load_parts' rows over the person's history (scoring origin − 1 … END)."""
    origin = CS.START + timedelta(days=2)
    start = origin - timedelta(days=1)
    metrics: dict = {m: {} for m in ("step_count", "resting_heart_rate", "vo2_max", "body_mass")}
    for r in data["metrics"]:
        # the scoring origin is the first day with a resting heart rate (the DB test stores these rows)
        lo = origin if r["metric"] == "resting_heart_rate" else start
        if r["metric"] in metrics and lo <= r["date"] <= END:
            metrics[r["metric"]][r["date"]] = r["value"]
    if origin not in metrics["resting_heart_rate"]:
        metrics["resting_heart_rate"][origin] = 55.0
    for d, w in data["weights"].items():
        if d.day % 3 == 0 and start <= d <= END:
            metrics["body_mass"][d] = w + 0.3
    scores = [{"date": r["date"], "sleep_actual_min": None if r["sleep_actual_min"] is None else round(r["sleep_actual_min"]),
               "strain": r["strain"],
               "z": ((r["details"] or {}).get("load") or {}).get("zone_minutes")} for r in data["scores"] if start <= r["date"] <= END]
    ss = [s for s in sessions(data) if start - timedelta(days=1) <= s["wake_date"] <= END]
    types = ("traditional_strength_training", "running", "cycling", "swimming", "functional_strength_training", "walking",
             "core_training", "rowing")
    workouts, sparky, imported = [], [], {}
    for i in range(0, (END - origin).days + 1):
        d = origin + timedelta(days=i)
        if variant != "no_strength" and i % 3 == 0 and i > 20:
            t = types[i % len(types)]
            workouts.append({"local_date": d, "type": t, "duration_min": 30.0 + (i * 7) % 50,
                             "hr_zone_min": None if i % 4 == 0 else [5.0, 10.0 + i % 7, 8.0, 3.0 + i % 3, 1.0]})
        if variant == "full" and i % 5 == 2 and i > 40:
            ex = [{"name": "squat", "category": "strength", "duration_minutes": None if i % 2 else 12,
                   "sets": [{"reps": 8, "weight": 60}] * (3 + i % 3)},
                  {"name": "run", "category": "cardio", "duration_minutes": 20, "sets": []}]
            sparky.append({"date": f"{d.isoformat()}T07:00:00", "total_duration_minutes": None if i % 4 else 45,
                           "exercises": ex if i % 7 else ex[1:]})
        if variant == "full" and i % 29 == 13:
            imported[d] = 52.5
    weights = []
    if variant == "full":
        for i in range(0, (END - origin).days + 1, 6):
            d = origin + timedelta(days=i)
            w = {"date": d.isoformat(), "weight_kg": _r(91 - 0.01 * i, 2)}
            if i % 30 == 0:
                w["waist_cm"] = 92.0 - 0.02 * i
            if i == 60:
                w["height_cm"] = 179.0
            weights.append(w)
    journal = {origin + timedelta(days=k) for k in range(300, 410, 4)} if variant == "full" else set()
    plan = {"days_per_week": 4, "start_date": END - timedelta(days=200)} if variant in ("full", "no_strength") else None
    return {"tz": TZ_NAME, "birth": date(1994, 5, 17) if variant != "no_birth" else None,
            "sex": {"full": "男", "no_strength": "female", "sparky_error": " M ", "no_birth": "f", "no_origin": None}[variant],
            "height": None if variant == "full" else 168.0, "start": start, "end": END, "scores": scores, "metrics": metrics,
            "goal_kg": 80.0 if variant == "full" else None, "sleep": ss, "workouts": workouts,
            "sparky_sessions": None if variant == "sparky_error" else sparky,
            "sparky_weights": None if variant == "sparky_error" else weights,
            "sparky_error": "ConnectError" if variant == "sparky_error" else None, "imported": imported,
            "origin": None if variant == "no_origin" else origin, "journal": journal, "plan": plan}


HEALTHSPAN_VARIANTS = ("full", "no_strength", "sparky_error", "no_birth", "no_origin")


def all_cases() -> dict:
    data = person()
    return {"strain": strain_case(data), "sleep_coach": sleep_coach_case(data), "monitor": monitor_case(data),
            "goals": goals_case(data), "goal_calls": goal_calls(), "sleep_night": sleep_night_case(data),
            "notes": notes_calls(), "today": today_case(data),
            "healthspan": {v: healthspan_parts(data, v) for v in HEALTHSPAN_VARIANTS}, "tz": TZ_NAME, "end": END}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--dump":
        sys.exit("usage: python3 cases_coach.py --dump FILE")
    Path(sys.argv[2]).write_text(json.dumps(dump(all_cases()), ensure_ascii=False))
    print(f"wrote {sys.argv[2]}")
