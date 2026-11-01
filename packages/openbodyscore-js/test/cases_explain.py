"""Cross-language golden cases of package M6, explain part (docs/ios-native-2.0.md sections 2.1 and 4): the personal part
of the explanation sheets (explain/personal.py, rules only, no AI) and the lab history / status card
(records/lab_history.py) on synthetic inputs, run by the Python reference (body_engine/tests/test_js_golden_explain.py,
which writes test/golden/explain.json with BODY_WRITE_GOLDEN=1) and replayed on the bundle (test/explain.test.js).

Standard library only, synthetic data only. Inputs are explain/personal.metric_data's JSON (ISO dates):
- every metric calculator (`calc_*`) on M3's synthetic person (cases_series.person: 420 days in Australia/Sydney) at the
  last day, early in the history (< 14 baseline days), before any data and on a day without a score row, plus handmade rows
  for every tone / branch (short / enough sleep, sleep need above the average, the weakest sleep part, sleep debt and
  regularity bands, efficiency bands, early / late / very late bedtimes, recovery zones with the part that pulls it down,
  strain bands and TSB < −30, VO₂max levels with / without sex and a falling trend, weight with / without a goal and each
  weekly change band, every status card shown on a Trends metric, series-only metrics, a metric with nothing);
- the lab sheet (`lab_compute`): high (> 20 % above the limit, from a home device, after a meal, not retested for 12+
  weeks), low, normal, a range with one limit only, no reading;
- the lab history and card: readings with and without a range, a flag only, text values, flat / up / down;
every run in Chinese and English (texts compared verbatim; `missing` stays Chinese as on the server).
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cases_series as CS  # noqa: E402
from cases import dump  # noqa: E402

TZ_NAME = CS.TZ_NAME
END = CS.END
LANGS = ("zh", "en")
SCORE_FIELDS = ("date", "recovery", "sleep_score", "sleep_actual_min", "sleep_need_min", "sleep_debt_min", "strain", "tsb",
                "details")


def iso(d) -> str:
    return d.isoformat()


def person_data(p: dict, key: str, day: date) -> dict:
    """explain/personal.metric_data's JSON from the synthetic person."""
    if key in ("hrv", "resting_hr"):
        metric = {"hrv": "heart_rate_variability_sdnn", "resting_hr": "resting_heart_rate"}[key]
        lo = day - timedelta(days=66)
        return {"values": {iso(m["date"]): m["value"] for m in p["metrics"] if m["metric"] == metric and lo <= m["date"] <= day}}
    if key in ("sleep_hours", "sleep_score", "sleep_debt", "sleep_consistency", "recovery", "strain"):
        n = 8 if key == "strain" else 7
        lo = day - timedelta(days=n - 1)
        return {"scores": [{k: (iso(s[k]) if k == "date" else s.get(k)) for k in SCORE_FIELDS}
                           for s in p["scores"] if lo <= s["date"] <= day]}
    if key == "sleep_efficiency":
        lo = day - timedelta(days=6)
        return {"nights": [{"wake_date": iso(s["wake_date"]), "asleep_min": s["asleep_min"], "in_bed": s["in_bed"]}
                           for s in p["sleep"] if lo <= s["wake_date"] <= day]}
    if key == "bedtime":
        lo = day - timedelta(days=13)
        return {"tz": TZ_NAME, "mains": [{"wake_date": iso(s["wake_date"]), "start_at": s["start_at"].astimezone(CS.TZ).isoformat()}
                                         for s in p["sleep"] if lo <= s["wake_date"] <= day]}
    raise KeyError(key)


def _days(day: str, n: int) -> list[str]:
    d = date.fromisoformat(day)
    return [iso(d - timedelta(days=n - 1 - k)) for k in range(n)]


def srow(d: str, **kw) -> dict:
    row = {"date": d, "recovery": 60, "sleep_score": 82, "sleep_actual_min": 450, "sleep_need_min": 460, "sleep_debt_min": 30,
           "strain": 11.0, "tsb": -5.0, "details": {"sleep": {"duration": 90, "stages": 85, "consistency": 88, "interruptions": 92},
                                                     "recovery": {"z": {"hrv": 0.5, "rhr": 0.1, "resp_rate": -0.1}}}}
    row.update(kw)
    return row


def hand_runs() -> list[dict]:
    """[{key, day, data}] for the branches the person does not reach."""
    day = "2026-10-20"
    d7, d8, d14 = _days(day, 7), _days(day, 8), _days(day, 14)
    r = []

    def add(key, data, d=day):
        r.append({"key": key, "day": d, "data": data})
    d67 = _days(day, 67)
    for key, base, recent in (("hrv", 50.0, 56.0), ("hrv", 50.0, 46.5), ("hrv", 50.0, 40.0), ("hrv", 50.0, 50.4),
                              ("resting_hr", 55.0, 61.0), ("resting_hr", 55.0, 58.0), ("resting_hr", 55.0, 55.5),
                              ("resting_hr", 55.0, 51.0)):
        add(key, {"values": {x: (recent + (k % 3) * 0.5 if k >= 60 else base + ((k * 7) % 11 - 5) * 0.8)
                             for k, x in enumerate(d67) if k % 9 != 4}})
    add("sleep_hours", {"scores": [srow(x, sleep_actual_min=330, sleep_need_min=480) for x in d7]})
    add("sleep_hours", {"scores": [srow(x, sleep_actual_min=395.5, sleep_need_min=None) for x in d7]})
    add("sleep_hours", {"scores": [srow(x, sleep_actual_min=None) for x in d7]})
    add("sleep_score", {"scores": [srow(x, sleep_score=55, details={"sleep": {"duration": 40, "stages": 70, "interruptions": None}})
                                   for x in d7]})
    add("sleep_score", {"scores": [srow(x, sleep_score=70, details={"sleep": {"stages": 65, "consistency": 65}}) for x in d7]})
    add("sleep_score", {"scores": [srow(x) for x in d7]})
    add("sleep_score", {"scores": [srow(x, sleep_score=90, details={}) for x in d7]})
    for debt in (45, 100, 200):
        add("sleep_debt", {"scores": [srow(x, sleep_debt_min=debt) for x in d7]})
    for c in (90, 65, 40):
        add("sleep_consistency", {"scores": [srow(x, details={"sleep": {"consistency": c + (k % 3)}}) for k, x in enumerate(d7)]})
    for eff in (0.95, 0.8, 0.6):
        add("sleep_efficiency", {"nights": [{"wake_date": x, "asleep_min": round(480 * eff), "in_bed": 480.0} for x in d7]
                                 + [{"wake_date": d7[0], "asleep_min": None, "in_bed": 300.0}]})
    add("sleep_efficiency", {"nights": []})
    for h in (23, 1, 3):   # bedtimes 23:xx, 01:xx, 03:xx
        add("bedtime", {"tz": TZ_NAME, "mains": [
            {"wake_date": x, "start_at": f"{iso(date.fromisoformat(x) - timedelta(days=1)) if h >= 18 else x}T{h:02d}:{(k * 7) % 60:02d}:00+11:00"}
            for k, x in enumerate(d14) if k % 4 != 1]})
    add("bedtime", {"tz": TZ_NAME, "mains": [{"wake_date": d14[0], "start_at": "2026-10-06T23:10:00+11:00"}]})
    for rec, z in ((80, {"hrv": 0.6, "rhr": 0.4}), (50, {"hrv": -0.8, "rhr": -0.4, "resp_rate": -0.2, "spo2": -3.0}),
                   (20, {"hrv": None, "rhr": -1.2, "sleep_performance": -0.5})):
        add("recovery", {"scores": [srow(x, recovery=rec - (k % 3), details={"recovery": {"z": z}}) for k, x in enumerate(d7)]})
    add("recovery", {"scores": [srow(x, recovery=None if x == day else 44) for x in d7]})
    for s, rec, tsb in ((8.5, 70, -5.0), (12.25, 50, -35.0), (15.0, 20, None), (19.4, None, 2.0)):
        add("strain", {"scores": [srow(x, strain=s + (k % 2), recovery=rec, tsb=tsb) for k, x in enumerate(d8)]})
    add("strain", {"scores": [srow(x, strain=None) for x in d8]})
    add("strain", {"scores": [srow(x, strain=10.0) for x in d8[:-2]] + [srow(d8[-2], strain=None), srow(d8[-1], strain=9.0)]})
    vo2 = {"current": 47.3, "level": "good", "level_zh": "良好", "percentile": 72.4, "norm_median": 44.15, "trend_per_year": 1.26,
           "missing_reason": None}
    add("vo2max", {"vo2max": vo2})
    add("vo2max", {"vo2max": dict(vo2, level="fair", level_zh="一般", percentile=None, trend_per_year=-1.85)})
    add("vo2max", {"vo2max": dict(vo2, level=None, level_zh=None, percentile=None, norm_median=None, trend_per_year=None,
                                  missing_reason="没填性别（users.sex），没法对照常模", current=39.0)})
    add("vo2max", {"vo2max": dict(vo2, current=None)})
    w21 = _days(day, 21)
    for goal, slope in ((None, -0.1), (66.0, -0.25), (66.0, -0.08), (66.0, -0.01), (81.0, -0.1)):
        add("weight", {"weights": {x: round(76 + slope * k, 2) for k, x in enumerate(w21) if k % 2 == 0}, "goal": goal})
    add("weight", {"weights": {w21[-1]: 74.4, w21[-2]: 74.6}, "goal": 70.0})
    add("weight", {"weights": {}, "goal": 66.0})
    series = {"resp_rate": {"n": 28, "values": [14.2, None, 14.61], "mean": 14.437, "prev_mean": 14.9},
              "steps": {"n": 30, "values": [8000.0, 9123.0], "mean": 8765.4, "prev_mean": None},
              "hr_recovery": {"n": 5, "values": [22.5], "mean": 21.04, "prev_mean": 20.0},
              "sleep_perf_whoop": {"n": 0, "values": [], "mean": None, "prev_mean": None},
              "body_age": {"n": 4, "values": [33.12, None], "mean": 33.3, "prev_mean": 33.5}}
    status = {"illness": {"label": "需要留意", "score": 2, "level": "watch"},
              "body_battery": {"current": 45}, "recovery_time": {"hours": 30}, "hr_recovery": {"bpm": 24.6, "level": "good"},
              "strain_combined": {"value": 12.345}, "hrv_status": {"label": "平衡", "status": "balanced"},
              "training_status": {"label": "过度", "status": "overreaching"}, "chronotype": {"label": "夜猫子"}}
    for key in ("resp_rate", "steps", "illness", "body_battery", "recovery_time", "hr_recovery", "strain_combined", "hrv_status",
                "training_status", "chronotype", "sleep_perf_whoop", "body_age", "behavior", "spo2"):
        add(key, {"series": {"series": series, "status": status}})
    add("body_battery", {"series": {"series": {}, "status": {"body_battery": {"current": 75}}}})
    add("body_battery", {"series": {"series": {}, "status": {"body_battery": {"current": 12}}}})
    add("recovery_time", {"series": {"series": {}, "status": {"recovery_time": {"hours": 0}}}})
    add("recovery_time", {"series": {"series": {}, "status": {"recovery_time": {"hours": 12}}}})
    return r


def runs() -> list[dict]:
    p = CS.person()
    out = []
    early = CS.START + timedelta(days=10)
    before = CS.START - timedelta(days=3)
    no_score = CS.START + timedelta(days=22)    # i % 17 == 5: no score row that day
    for key in ("hrv", "resting_hr", "sleep_hours", "sleep_score", "sleep_debt", "sleep_consistency", "recovery", "strain",
                "sleep_efficiency", "bedtime"):
        for d in (END, CS.PAST, early, before, no_score):
            out.append({"key": key, "day": iso(d), "data": person_data(p, key, d)})
    out += hand_runs()
    return [dict(r, lang=lang) for r in out for lang in LANGS]


def lab_runs() -> list[dict]:
    hist = [{"collected_on": "2026-03-02", "value": 452.0, "value_text": None, "unit": "μmol/L", "status": "high", "source": "lab"},
            {"collected_on": "2026-06-20", "value": None, "value_text": "阴性", "unit": None, "status": "unknown", "source": "lab"},
            {"collected_on": "2026-07-01", "value": 471.5, "value_text": None, "unit": "μmol/L", "status": "high",
             "source": "home_device"}]
    latest = {"collected_on": "2026-07-01", "value": 471.5, "value_text": None, "unit": "μmol/L", "ref_low": 208.0,
              "ref_high": 428.0, "flag": None, "note": None, "source": "home_device", "fasting": False}
    rep = {"test_key": "uric_acid", "test_name": "尿酸", "category": "生化", "latest": latest, "status": "high",
           "history": hist, "above_upper_pct": 23.5}
    r = [{"key": "uric_acid", "day": "2026-10-20", "report": rep},
         {"key": "uric_acid", "day": "2026-07-20", "report": dict(rep, above_upper_pct=12.0,
                                                                  latest=dict(latest, fasting=True, source="lab"))},
         {"key": "uric_acid", "day": "2026-10-20", "report": dict(rep, above_upper_pct=None,
                                                                  latest=dict(latest, ref_low=None, fasting=None))},
         {"key": "alt", "day": "2026-10-20", "report": dict(rep, test_key="alt", status="low", history=hist[:1],
                                                            latest=dict(latest, value=6.5, unit="U/L", ref_low=9.0, ref_high=None,
                                                                        source="lab", fasting=None))},
         {"key": "hdl", "day": "2026-10-20", "report": dict(rep, test_key="hdl", status="normal", above_upper_pct=None,
                                                            latest=dict(latest, value=1.25, unit=None, ref_low=1.0,
                                                                        ref_high=None, source=None, fasting=True))},
         {"key": "ferritin", "day": "2026-10-20", "report": None},
         {"key": "ferritin", "day": "2026-10-20", "report": dict(rep, latest=dict(latest, value=None, value_text="阳性"))}]
    return [dict(x, lang=lang) for x in r for lang in LANGS]


def lab_history_runs() -> list[dict]:
    base = {"test_name": "甘油三酯", "category": "血脂", "lab": None, "note": None}
    rows = [
        {**base, "id": 1, "collected_on": "2025-08-01", "value": 2.05, "value_text": None, "unit": "mmol/L", "ref_low": None,
         "ref_high": 1.7, "flag": "H", "source": "lab", "fasting": True},
        {**base, "id": 2, "collected_on": "2025-12-01", "value": None, "value_text": "复查未做", "unit": None, "ref_low": None,
         "ref_high": None, "flag": None, "source": "lab", "fasting": None},
        {**base, "id": 3, "collected_on": "2026-03-01", "value": 1.9, "value_text": None, "unit": "mmol/L", "ref_low": None,
         "ref_high": None, "flag": "H", "source": "home_device", "fasting": False},
        {**base, "id": 4, "collected_on": "2026-08-01", "value": 1.89, "value_text": None, "unit": None, "ref_low": 0.5,
         "ref_high": 1.7, "flag": None, "source": "lab", "fasting": True},
    ]
    return [{"key": "triglycerides", "rows": rows}, {"key": "triglycerides", "rows": rows[:1]},
            {"key": "uric_acid", "rows": [dict(rows[0], value=400.0, ref_high=420.0, unit="μmol/L"),
                                          dict(rows[3], value=520.0, ref_high=420.0, ref_low=None, unit="μmol/L")]},
            {"key": "custom_test", "rows": [dict(rows[1]), dict(rows[1], id=5, flag="abnormal")]},
            {"key": "vitamin_d", "rows": [dict(rows[0], value=70.0, ref_low=50.0, ref_high=None, flag=None),
                                          dict(rows[3], value=42.0, ref_low=50.0, ref_high=None)]}]


def all_cases() -> dict:
    return {"tz": TZ_NAME, "runs": runs(), "labs": lab_runs(), "lab_history": lab_history_runs()}


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--dump":
        Path(sys.argv[2]).write_text(json.dumps(dump(all_cases()), ensure_ascii=False))
    else:
        print(json.dumps(dump(all_cases()), ensure_ascii=False)[:2000])
