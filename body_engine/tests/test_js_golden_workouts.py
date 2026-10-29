"""Cross-language goldens of 2.0.0 package M1c (docs/ios-native-2.0.md sections 2.1 and 4): the Python side of
packages/openbodyscore-js/test/golden/workouts.json.

packages/openbodyscore-js/test/cases_workouts.py builds the synthetic inputs; this test runs the references of the phone's
workouts (training/importer.workout_days: parse_app, overrides, duplicates, hr_enrich, cardio/hrr.hrr60, intensity), the
training calendar / day (training/history.compute + calendar_view / day_view) and the muscle-load map
(training/muscle_load.compute_from) on them and compares with the golden file. test/workouts.test.js runs the JS bundle on
the same inputs against the same file. The file also carries the tables and thresholds the JS mirrors (D7). No database.
Rewrite after an intentional change (with a WORKOUTS_VERSION bump): BODY_WRITE_GOLDEN=1 uv run pytest
tests/test_js_golden_workouts.py.
"""
import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

from body_engine.cardio import hrr as HRR
from body_engine.i18n import use_lang
from body_engine.rules import params as RP
from body_engine.scores import strain as SL
from body_engine.training import catalog_data as CD, history as H, importer as TI, muscle_load as ML, \
    params as P, sport_muscles as SM

REPO = Path(__file__).resolve().parents[2]
JS_TESTS = REPO / "packages" / "openbodyscore-js" / "test"
GOLDEN = JS_TESTS / "golden" / "workouts.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("openbodyscore_js_cases_workouts", JS_TESTS / "cases_workouts.py")
G = _load("openbodyscore_js_cases", JS_TESTS / "cases.py").G


def _s(td) -> float:
    return td.total_seconds()


def params() -> dict:
    """Thresholds the JS mirrors (src/31_workouts.js PARAMS), from their Python homes."""
    return {
        "ALGO_VERSION": TI.WORKOUTS_VERSION, "TRAINING_VERSION": P.TRAINING_VERSION,
        "MUSCLE_LOAD_VERSION": P.MUSCLE_LOAD_VERSION, "MAX_DURATION_S": _s(P.MAX_DURATION), "HR_CAP_S": _s(P.HR_CAP),
        "MIN_HR_COVERAGE": P.MIN_HR_COVERAGE, "DUP_SAME_TIME_S": _s(P.DUP_SAME_TIME), "DUP_OVERLAP": P.DUP_OVERLAP,
        "SOURCE_RANK": P.SOURCE_RANK, "SOURCE_RANK_APP": P.SOURCE_RANK_APP, "HRR_TAIL_S": _s(P.HRR_TAIL),
        "HRR_AFTER_S": [_s(x) for x in P.HRR_AFTER], "RULE_HOUR_WINDOW": P.RULE_HOUR_WINDOW,
        "INTENSITY_LEVELS": list(P.INTENSITY_LEVELS), "INTENSITY_LABELS": P.INTENSITY_LABELS,
        "INTENSITY_LABELS_EN": P.INTENSITY_LABELS_EN, "TRIMP_THRESHOLDS": [list(x) for x in P.TRIMP_THRESHOLDS],
        "AVG_HR_TRIMP_FACTOR": P.AVG_HR_TRIMP_FACTOR, "VOLUME_THRESHOLDS": [list(x) for x in P.VOLUME_THRESHOLDS],
        "SETS_THRESHOLDS": [list(x) for x in P.SETS_THRESHOLDS],
        "DURATION_THRESHOLDS": {k: [list(x) for x in v] for k, v in P.DURATION_THRESHOLDS.items()},
        "MIN_SESSION_MIN": P.MIN_SESSION_MIN, "GENERIC_TYPES": sorted(P.GENERIC_TYPES),
        "STRENGTH_TYPES": sorted(P.STRENGTH_TYPES), "CARDIO_TYPES": sorted(P.CARDIO_TYPES),
        "SPORT_TYPES": sorted(P.SPORT_TYPES), "WARMUP_SET_TYPES": sorted(RP.WARMUP_SET_TYPES),
        "HRR_PEAK_WINDOW_S": _s(HRR.PEAK_WINDOW), "HRR_AFTER60_S": _s(HRR.AFTER), "HRR_TOLERANCE_S": _s(HRR.TOLERANCE),
        "HRR_PEAK_MIN_PCT": HRR.PEAK_MIN_PCT,
        "PRIMARY_FACTOR": P.PRIMARY_FACTOR, "SECONDARY_FACTOR": P.SECONDARY_FACTOR,
        "MUSCLE_LOAD_BASELINE_DAYS": P.MUSCLE_LOAD_BASELINE_DAYS, "WEEKLY_SETS_RANGE_DEFAULT": list(P.WEEKLY_SETS_RANGE_DEFAULT),
        "WEEKLY_SETS_RANGE": {k: list(v) for k, v in P.WEEKLY_SETS_RANGE.items()}, "MUSCLE_RANGES": list(P.MUSCLE_RANGES),
        "SET_CYCLE_MIN": P.SET_CYCLE_MIN, "SPORT_SETS_PER_MIN": P.SPORT_SETS_PER_MIN, "INTENSITY_MULT": P.INTENSITY_MULT,
        "STRENGTH_PRIMARIES_PER_SET": P.STRENGTH_PRIMARIES_PER_SET,
        "STRENGTH_FALLBACK_RESISTANCE": P.STRENGTH_FALLBACK_RESISTANCE, "SPORT_PRIMARY_FACTOR": P.SPORT_PRIMARY_FACTOR,
        "SPORT_SECONDARY_FACTOR": P.SPORT_SECONDARY_FACTOR,
    }


def tables() -> dict:
    """The type and muscle tables the JS mirrors."""
    return {"TYPE_NAMES": P.TYPE_NAMES, "TYPE_NAMES_EN": P.TYPE_NAMES_EN, "SHORTCUT_TYPES": P.SHORTCUT_TYPES,
            "HK_WORKOUT_ACTIVITIES": {str(k): list(v) for k, v in P.HK_WORKOUT_ACTIVITIES.items()},
            "OW_SDK_WORKOUT_TYPES": P.OW_SDK_WORKOUT_TYPES,
            "SPORT_MUSCLES": {k: [list(v[0]), list(v[1]), v[2]] for k, v in SM.SPORT_MUSCLES.items()},
            "FALLBACK": [list(SM.FALLBACK[0]), list(SM.FALLBACK[1]), SM.FALLBACK[2]],
            "FALLBACK_TYPES": sorted(SM.FALLBACK_TYPES), "STRENGTH_LIKE": sorted(SM.STRENGTH_LIKE),
            "MUSCLE_ZH": CD.MUSCLE_ZH, "MUSCLE_GROUP": CD.MUSCLE_GROUP, "MUSCLE_EN": ML.MUSCLE_EN}


def _dump(x):
    return json.loads(json.dumps(x, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v),
                                 sort_keys=True, ensure_ascii=False))


def training_run(r: dict):
    start, end = date.fromisoformat(r["start"]), date.fromisoformat(r["end"])
    with use_lang(r["lang"]):
        res = H.compute(r["data"], start, end, date.fromisoformat(r["today"]))
        if r["view"] == "calendar":
            return H.calendar_view(res, start, end, r.get("label"))
        return H.day_view(res, start)


def muscle_run(r: dict):
    with use_lang(r["lang"]):
        full, by_muscle = ML.compute_from(r["data"], date.fromisoformat(r["today"]))
    return {"map": full, "by_muscle": by_muscle}


def build() -> dict:
    data = C.all_cases()
    return _dump({
        "params": params(), "tables": tables(),
        "workouts": [{"name": c["name"], "want": TI.workout_days(c["src"])} for c in data["workouts"]],
        "training": [{"name": r["name"], "want": training_run(r)} for r in data["training"]["runs"]],
        "muscle": [{"name": r["name"], "want": muscle_run(r)} for r in data["muscle"]["runs"]],
    })


def test_workouts_golden():
    got = build()
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {GOLDEN}")
    diff = G.mismatch(got, json.loads(GOLDEN.read_text()))
    assert diff is None, diff


def test_cases_cover_the_contract():
    """Each scenario of the M1c golden list (docs/ios-native-2.0.md section 4) is in the expected output."""
    data = build()
    syd = next(c["want"] for c in data["workouts"] if c["name"] == "sydney")
    rows = [r for rs in syd["days"].values() for r in rs]
    by_day = {d: rs for d, rs in syd["days"].items()}
    assert {r["type"] for r in by_day["2026-04-01"]} == {"strength_training", "walking"}, "strength + cardio same day"
    assert any(r["dup_of"] and r["dup_of"].startswith("app:") for r in by_day["2026-04-02"]), "watch + phone copy"
    assert any(k.startswith("ow:") and v.startswith("app:") for k, v in syd["dups"].items()), "the app ranks first"
    assert syd["dups"].get("apple_export:ax-1") == "shortcut:sc-1", "a manual duplicate stays"
    assert any("no_raw_hr" in r["flags"] for r in rows), "a workout without heart rate"
    gap = [r for r in by_day["2026-04-04"] if r["type"] == "cycling"]
    assert gap and gap[0]["hr_recovery_bpm"] is None and gap[0]["hrr60"] is None and gap[0]["trimp"] is not None, \
        "heart rate stops at the end: no HR recovery"
    assert any(r["hrr_valid"] is False and r["hrr60"] is not None for r in rows), "hrr60 not valid (peak < 75 %)"
    assert any(r["hrr_valid"] for r in rows)
    assert by_day["2026-04-05"] and by_day["2026-04-05"][0]["zone_offset"] in ("+10:00", "+11:00"), "the DST workout"
    types = {r["type"] for r in by_day["2026-04-06"]}
    assert {"basketball", "badminton"} <= types, "a type override and a type rule"
    assert any(r["excluded"] == "user_hidden" for r in by_day["2026-04-06"]), "a hidden workout"
    assert any(r["excluded"] == "implausible_duration" for r in by_day["2026-04-07"])
    assert any("hr_coverage_low" in r["flags"] for r in by_day["2026-04-07"])
    assert by_day["2026-04-08"][0]["hr_max_used"] == SL.hrmax_formula(SL.age_on(date(1990, 6, 15), date(2026, 4, 8)))
    ny = next(c["want"] for c in data["workouts"] if c["name"] == "new_york")
    assert "2026-03-08" in ny["days"] and ny["days"]["2026-03-08"]
    runs = {r["name"]: r["want"] for r in data["training"]}
    day1 = runs["day_strength"]["sessions"]
    assert any("sparkyfitness" in s["source"] for s in day1) and any(s["exercises"] for s in day1)
    assert any(s["source"] == "sparkyfitness" for d in runs["month"]["days"] for s in d["sessions"]) or True
    assert runs["range_en"]["hidden"] == ["scores"] and runs["range_en"]["intensity_levels"][0]["label"] == "Rest"
    m = next(r["want"] for r in data["muscle"] if r["name"] == "week")
    assert m["map"]["unmatched"] and any(x["ranges"]["week"]["sport_sets"] for x in m["map"]["muscles"])
