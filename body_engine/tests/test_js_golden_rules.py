"""Cross-language goldens of package M6, rules part (docs/ios-native-2.0.md sections 2.1 and 4): the Python side of
packages/openbodyscore-js/test/golden/rules.json and rules_local.json.

packages/openbodyscore-js/test/cases_rules.py builds synthetic inputs; this test runs the reference code on them —
rules/engine.evaluate_data (every rule, on the Ctx as plain data), rules/context.assemble (the phone's raw sources -> data)
and goals/nutrition.goal_for / day_from_totals — and compares with the golden file. test/rules.test.js replays the same
inputs through the bundle (src/36_rules.js). Reminder texts (Chinese and English) are compared verbatim. No database (the
database side: tests/test_native_rules.py).

After an intentional change: bump rules/params.RULES_VERSION, then BODY_WRITE_GOLDEN=1 uv run pytest
tests/test_js_golden_rules.py, port the change to src/36_rules.js, rebuild (node packages/openbodyscore-js/build.js) and run
the Node test.
"""
import importlib.util
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from body_engine.goals import nutrition as NG, params as GP, store as goal_store
from body_engine.i18n import use_lang
from body_engine.nutrition import constraint_params as CP, daily
from body_engine.rules import context, engine, params as RP
from body_engine.water import params as WP

REPO = Path(__file__).resolve().parents[2]
JS_TESTS = REPO / "packages" / "openbodyscore-js" / "test"
GOLDEN = JS_TESTS / "golden" / "rules.json"
IOS_FIXTURE = JS_TESTS / "golden" / "rules_local.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("openbodyscore_js_cases_rules", JS_TESTS / "cases_rules.py")
G = C.dump.__globals__["G"]   # golden_cases (mismatch with its float tolerance), loaded by cases.py


def _dump(x) -> object:
    return json.loads(json.dumps(C.dump(x), sort_keys=True, ensure_ascii=False))


@pytest.fixture(scope="module")
def cases():
    return C.all_cases()


def params() -> dict:
    keys = ("INCREMENT_FALLBACK_KG", "MIN_RIR_FOR_INCREASE", "FAIL_STREAK", "REQUIRE_PLANNED_SETS", "PROGRESSION_LOOKBACK_DAYS",
            "SLOW_LOSS_KG_PER_WEEK", "SLOW_LOSS_KCAL", "FAST_LOSS_KG_PER_WEEK", "FAST_LOSS_KCAL", "MIN_WEIGHINS_PER_WEEK",
            "TARGET_LOSS_RANGE", "PRE_MEAL_CARBS_G", "PRE_MEAL_PROTEIN_G", "DAY_CARBS_WARN_G", "WATER_TARGET_L",
            "SYMPTOM_LOOKBACK_DAYS", "RED_MEAT_KEYWORDS", "RED_MEAT_EXCLUDE", "BANNED_LOG_LOOKBACK_DAYS",
            "CAUTION_LOG_LOOKBACK_DAYS", "CAUTION_MAX_WEIGHT_KG", "RECOVERY_RED", "HEAVY_CARDIO_KEYWORDS", "SLEEP_DEBT_HIGH_MIN",
            "ADDED_SUGAR_CAP_G", "ADDED_SUGAR_WARN_G", "SUGARY_DRINKS_PER_WEEK", "CALORIE_ADHERENCE_TOL", "PROTEIN_ADHERENCE_MIN")
    rules = {k: getattr(RP, k) for k in keys}
    rules["WARMUP_SET_TYPES"] = sorted(RP.WARMUP_SET_TYPES)
    rules["LEG_MUSCLES"] = sorted(RP.LEG_MUSCLES)
    rules["SPARKY_STATUS_MARKERS"] = [list(x) for x in RP.SPARKY_STATUS_MARKERS.items()]
    return {
        "rules": rules,
        "goals": {k: getattr(GP, k) for k in ("DEFAULT_DURATION_MIN", "ON_TARGET_TOL_MIN", "DEVIATION_MIN", "DEVIATION_NIGHTS",
                                                "SCREEN_OFF_BEFORE_MIN", "CAFFEINE_BEFORE_MIN", "CAFFEINE_NOON_CUTOFF_MIN",
                                                "DINNER_BEFORE_MIN", "MORNING_LIGHT", "MORNING_LIGHT_EN")} | {"HISTORY_DAYS": goal_store.HISTORY_DAYS},
        "water": {"REMIND_FROM_HOUR": WP.REMIND_FROM_HOUR, "REMIND_SHARE": WP.REMIND_SHARE},
        "constraints": {"WARN_FRACTION": CP.WARN_FRACTION, "FOOD_ALIASES": {k: list(v) for k, v in CP.FOOD_ALIASES.items()}},
        "nutrition": {"FIELDS": list(NG.FIELDS), "DAY_TYPE_ZH": NG.DAY_TYPE_ZH, "DAY_TYPE_EN": NG.DAY_TYPE_EN,
                      "GOAL_KEYS": list(daily.GOAL_KEYS), "TOTAL_KEYS": list(NG.TOTAL_KEYS),
                      "UNKNOWN_ON_PHONE": list(NG.UNKNOWN_ON_PHONE), "OPTIONAL": ["sugar_g", "added_sugar_g", "fiber_g", "sodium_mg"]},
        "context": {"TRAINING_DAYS": context.TRAINING_DAYS, "FOOD_DAYS": context.FOOD_DAYS, "WEIGHT_DAYS": context.WEIGHT_DAYS,
                    "SCORE_DAYS": context.SCORE_DAYS, "JOURNAL_DAYS": context.JOURNAL_DAYS},
    }


def nutrition(c: dict) -> list:
    D = date.fromisoformat
    versions = [{**v, "effective_from": D(v["effective_from"])} for v in c["versions"]]
    out = []
    for call in c["calls"]:
        a, b = D(call["from"]), D(call["to"])
        days = [a + timedelta(days=k) for k in range((b - a).days + 1)]
        with use_lang(call["lang"]):
            goals = [NG.goal_for(d, versions, c["sparky"].get(d.isoformat())) for d in days]
            rows = [NG.day_from_totals(d, c["totals"].get(d.isoformat()), g) for d, g in zip(days, goals)]
        out.append({"goals": goals, "days": rows})
    return out


def compute_all(c: dict) -> dict:
    out = {"version": RP.RULES_VERSION, "params": params(), "runs": [], "raw": [], "nutrition": nutrition(c["nutrition"])}
    for r in c["runs"]:
        out["runs"].append(engine.evaluate_data(json.loads(json.dumps(r["data"]))))
    for r in c["raw"]:
        data = context.assemble(json.loads(json.dumps(r["raw"])))
        out["raw"].append({"data": data, "result": engine.evaluate_data(data)})
    return _dump(out)


def test_rules_goldens(cases):
    got = compute_all(cases)
    by = {r["name"]: g for r, g in zip(cases["runs"], got["runs"])}
    statuses = {(x["rule_key"], x["status"]) for g in got["runs"] for x in g["rules"]}
    for key in engine.RULE_KEYS:   # every rule fires, stays quiet and runs short of data somewhere
        assert {(key, "triggered"), (key, "not_triggered"), (key, "insufficient_data")} <= statuses, key
    assert {k for k, s in statuses if s == "error"} >= {"progression", "pre_workout_fuel", "exercise_restrictions",
                                                         "calorie_adjust", "frequency_caps"}
    subjects = {(x["rule_key"], x["subject"].split(":")[0], x["level"]) for g in got["runs"] for x in g["reminders"]}
    assert {("exercise_restrictions", "plan", "alert"), ("exercise_restrictions", "log", "warning"),
            ("exercise_restrictions", "weight", "warning"), ("exercise_restrictions", "", "info")} <= subjects
    assert {("frequency_caps", "red_meat_g", "warning"), ("frequency_caps", "red_meat_g", "info"),
            ("frequency_caps", "food", "warning"), ("frequency_caps", "food", "info"),
            ("frequency_caps", "red_meat_meals", "warning")} <= subjects
    assert {("sleep_goal", "deviation", "warning"), ("sleep_goal", "final", "info"), ("sleep_goal", "stage", "info")} <= subjects
    assert {("progression", "pull", "info"), ("progression", "pull", "warning"), ("pre_workout_fuel", "carbs", "warning")} <= subjects
    assert all(x["title_en"] and x["message_en"] for g in got["runs"] for x in g["reminders"])
    assert any("今天别再喝咖啡" in x["message"] for x in by["sleep_wake_target"]["reminders"])
    assert any("下肢 B" in x["title"] for x in by["fuel_day_change"]["reminders"])
    assert [g["result"]["reminders"] for g in got["raw"]][0]
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {GOLDEN}")
    diff = G.mismatch(got, json.loads(GOLDEN.read_text()))
    assert diff is None, diff


def test_data_round_trip(cases):
    """to_data(from_data(data)) keeps the data, so the server's Ctx and the phone's data evaluate the same."""
    for r in cases["runs"]:
        data = json.loads(json.dumps(r["data"]))
        ctx = context.from_data(data)
        again = context.to_data(ctx)
        assert engine.evaluate_data(again) == engine.evaluate_data(data), r["name"]
        assert again["scores"] == data["scores"] and again["journal"] == sorted(data["journal"]), r["name"]


def test_ios_fixture(cases):
    """Watch2WhoopTests/LocalRulesTests.swift: one raw input as the phone assembles it and the Python's reminders (the
    Swift loader on local tables + the bundle must give the same rows)."""
    raw = cases["raw"][0]["raw"]
    data = context.assemble(json.loads(json.dumps(raw)))
    got = _dump({"raw": raw, "result": engine.evaluate_data(data)})
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        IOS_FIXTURE.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {IOS_FIXTURE}")
    diff = G.mismatch(got, json.loads(IOS_FIXTURE.read_text()))
    assert diff is None, diff


def test_raw_keys_cover_the_inputs_document():
    """context.inputs (GET /rules/inputs) + the phone's LOCAL_KEYS are exactly what assemble reads."""
    assert set(context.INPUT_KEYS) | set(context.LOCAL_KEYS) | {"day", "tz", "now"} == set(C.raw_base(C.FRI))
