"""Cross-language goldens of package M4 (docs/ios-native-2.0.md sections 2.1 and 4): the Python side of
packages/openbodyscore-js/test/golden/coach.json (and coach_local.json, the iOS fixture).

packages/openbodyscore-js/test/cases_coach.py builds synthetic inputs; this test runs the reference code on them —
insights/coach.py (`strain_coach_compute`, `sleep_coach_compute`, `health_monitor_compute`), goals/ (`view_compute` and the
helpers of goals/sleep.py), sleep_night/view.compute, web/views.py (`scores_today_compute`, `sleep_today_compute`,
`hypnogram`, `frequent_journal_compute`), web/recovery_empty.explain_from and insights/healthspan_store.weekly_local — and
compares with the golden files. test/coach.test.js replays the same inputs through the bundle (src/34_coach.js). The
thresholds the JS carries (insights/coach.py, goals/params.py, sleep_night/params.py, web/views.py, web/params.py) are
compared too. No database (the DB test that these pure parts are what the endpoints answer: test_native_coach.py).

After an intentional change: bump insights/coach.COACH_VERSION, then BODY_WRITE_GOLDEN=1 uv run pytest
tests/test_js_golden_coach.py, port the change to src/34_coach.js, rebuild (node packages/openbodyscore-js/build.js) and
run the Node test.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from body_engine.goals import params as GP, sleep as GS, store as goal_store
from body_engine.i18n import use_lang
from body_engine.importers.common import Segment
from body_engine.insights import coach, healthspan_store
from body_engine.rules import params as RP
from body_engine.sleep_night import params as NP, view as night_view
from body_engine.web import params as WP, recovery_empty, views

REPO = Path(__file__).resolve().parents[2]
JS_TESTS = REPO / "packages" / "openbodyscore-js" / "test"
GOLDEN = JS_TESTS / "golden" / "coach.json"
IOS_FIXTURE = JS_TESTS / "golden" / "coach_local.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("openbodyscore_js_cases_coach", JS_TESTS / "cases_coach.py")
G = C.dump.__globals__["G"]   # golden_cases (mismatch with its float tolerance), loaded by cases.py


def _dump(x) -> object:
    return json.loads(json.dumps(C.dump(x), sort_keys=True, ensure_ascii=False))


@pytest.fixture(scope="module")
def cases():
    return C.all_cases()


def params() -> dict:
    return {
        "coach": {"LATENCY_DEFAULT_MIN": coach.LATENCY_DEFAULT_MIN, "MONITOR_DAYS": coach.MONITOR_DAYS,
                  "MONITOR_MIN_NIGHTS": coach.MONITOR_MIN_NIGHTS, "MONITOR_K": coach.MONITOR_K,
                  "HRV_MIN_HALF_LN": coach.MONITOR_HRV_MIN_HALF_LN,
                  "MONITOR": [[k, v[0], v[1], v[3]] for k, v in coach.MONITOR.items()],
                  "MONITOR_EN": {k: list(v) for k, v in coach.MONITOR_EN.items()}, "RHR_APPLE_LABEL": list(coach.RHR_APPLE_LABEL)},
        "goals": {k: getattr(GP, k) for k in ("DEFAULT_PACE_MIN", "DEFAULT_STEP_DAYS", "WINDOW_NIGHTS", "MIN_NIGHTS",
                                                "TREND_NIGHTS", "MIN_TREND_NIGHTS", "ON_TARGET_TOL_MIN",
                                                "DEFAULT_DURATION_MIN")} | {"HISTORY_DAYS": goal_store.HISTORY_DAYS},
        "sleep_night": {k: getattr(NP, k) for k in ("BASELINE_SHORT", "BASELINE_LONG", "MIN_BASELINE_NIGHTS", "PART_WEAK",
                                                     "SHORT_OF_NEED_MIN", "SHORT_OF_NEED_BAD_MIN", "DEEP_LOW_PCT", "REM_LOW_PCT",
                                                     "EFFICIENCY_GOOD", "EFFICIENCY_POOR", "LATENCY_LONG_MIN",
                                                     "LATENCY_POOR_MIN", "WASO_LONG_MIN", "WASO_POOR_MIN", "BEDTIME_SHIFT_MIN",
                                                     "HRV_LOW_PCT", "RHR_HIGH_BPM", "RESP_HIGH", "TEMP_DELTA_C", "SPO2_LOW",
                                                     "MAX_NOTES")},
        "today": {"RECOVERY_GREEN": views.RECOVERY_GREEN, "RECOVERY_RED": RP.RECOVERY_RED,
                  "LATEST_LOOKBACK_DAYS": views.LATEST_LOOKBACK_DAYS, "MERGE_GAP_US": views.MERGE_GAP.total_seconds() * 1e6,
                  "SLEEP_LOOKBACK_DAYS": recovery_empty.SLEEP_LOOKBACK_DAYS,
                  "FIELD_FLAGS": {k: list(v) for k, v in views.FIELD_FLAGS.items()}, "FIELD_FALLBACK": views.FIELD_FALLBACK,
                  "SCORE_FIELDS": list(views.SCORE_FIELDS), "RECOVERY_WORD": views.RECOVERY_WORD,
                  "RECOVERY_WORD_EN": views.RECOVERY_WORD_EN, "LOAD_STATUS": views.LOAD_STATUS,
                  "LOAD_STATUS_EN": views.LOAD_STATUS_EN},
        "today_params": WP.as_dict(),
    }


def segments(xs: list[dict] | None) -> list[Segment]:
    return [Segment(x["start"], x["end"], x["stage"]) for x in xs or []]


def timeline_of(row: dict | None, segs: list[dict] | None, tz: ZoneInfo):
    """The night's stage timeline as web/views.night_stages answers it, from the case's raw segments."""
    if row is None:
        return None
    stages = views.hypnogram(segments(segs), row["start_at"], row["end_at"], tz)
    return (stages, "app", None) if stages else ([], None, "这晚没有阶段明细")


def night_data(c: dict) -> dict:
    tz = ZoneInfo(c["tz"])
    row = views.main_rows(c["sessions"], c["day"], c["day"]).get(c["day"])
    return {"tz": tz, "today": c["today"], "day": c["day"], "can_scores": c["can_scores"], "sessions": c["sessions"],
            "scores": c["scores"], "prev": c["prev"], "next": c["next"], "timeline": timeline_of(row, c["segments"], tz)}


def compute_all(c: dict) -> dict:
    tz = ZoneInfo(c["tz"])
    out = {"version": coach.COACH_VERSION, "params": params()}
    out["strain"] = [coach.strain_coach_compute(x["today"], x["yday"], x["d"]) for x in c["strain"]]
    out["sleep_coach"] = [coach.sleep_coach_compute(x["rows"], x["d"], tz, x["goal"]) for x in c["sleep_coach"]]
    out["monitor"] = []
    for x in c["monitor"]:
        with use_lang(x["lang"]):
            out["monitor"].append(coach.health_monitor_compute(x["rows"], x["d"]))
    out["goals"] = [goal_store.view_compute(goal_store.with_plan(x["goal"]) if x["goal"] else None, x["sessions"], tz,
                                            x["night"], x["need"]) for x in c["goals"]]
    fns = {"to_off": GS.to_off, "fmt": GS.fmt, "round5": GS.round5, "build_plan": GS.build_plan,
           "night_of": lambda t, tzname: GS.night_of(t.astimezone(ZoneInfo(tzname)))}
    out["goal_calls"] = [fns[x["fn"]](*x["args"]) for x in c["goal_calls"]]
    out["sleep_night"] = []
    for x in c["sleep_night"]:
        with use_lang(x["lang"]):
            out["sleep_night"].append(night_view.compute(night_data(x)))
    out["notes"] = []
    for x in c["notes"]:
        with use_lang(x["lang"]):
            out["notes"].append(night_view.notes(x["night"], x["score"], x["base"], x["vitals"], x["flags"]))
    t = c["today"]
    out["today"] = {"scores": [], "empty": [], "sleep": [], "hypnogram": [], "journal": []}
    for x in t["scores"]:
        with use_lang(x["lang"]):
            out["today"]["scores"].append(views.scores_today_compute(x["rows"], x["day"], x["last"], x["last_sleep"],
                                                                     x["computed_at"], x["empty"]))
    for x in t["empty"]:
        with use_lang(x["lang"]):
            out["today"]["empty"].append(recovery_empty.explain_from(x["day"], x["today"], x["local_today"],
                                                                     x["needs_consent"], x["origin"], x["any_data"],
                                                                     x["recent_watch_sleep"]))
    for x in t["sleep"]:
        row = views.latest_main(x["sessions"], x["day"])
        out["today"]["sleep"].append(views.sleep_today_compute(x["sessions"], x["day"], tz,
                                                               timeline_of(row, x["segments"], tz)))
    out["today"]["hypnogram"] = [views.hypnogram(segments(x["segments"]), x["start"], x["end"], tz) for x in t["hypnogram"]]
    out["today"]["journal"] = [views.frequent_journal_compute(x["items"], x["counts"], x["days"], x["limit"], x["min_items"])
                               for x in t["journal"]]
    out["healthspan"] = {v: healthspan_store.weekly_local(p, c["end"]) for v, p in c["healthspan"].items()}
    return _dump(out)


def test_coach_goldens(cases):
    got = compute_all(cases)
    # coverage of the cases themselves
    assert {x["zone"] for x in got["strain"]} >= {"green", "yellow", "red", None}
    assert {x["status"] for x in got["strain"]} >= {"below", "in_range", "above", None}
    assert any(len(x["reasons"]) == 2 for x in got["strain"]) and any(x["current_missing_reason"] for x in got["strain"])
    sc = got["sleep_coach"]
    assert {x["wake_source"] for x in sc} >= {"goal", "goal_plus_need", "recent_median", None}
    assert {x["source"] for x in sc} >= {"goal", "need", None} and any(x["shortfall_min"] for x in sc)
    mon = got["monitor"]
    assert mon[0]["all_in_range"] is not None and mon[1]["metrics"][0]["label"].startswith("Resting")
    assert "Apple" in mon[2]["metrics"][0]["label"] and any(m["missing_reason"] for m in mon[4]["metrics"])
    assert any(m["status"] in ("low", "high") for x in mon for m in x["metrics"])
    goals = got["goals"]
    assert any(g["tonight_target_bedtime"] for g in goals) and any(g["progress"]["eta_date"] for g in goals)
    assert any(g["progress"]["trend_min_per_week"] is not None for g in goals) and any(g["recent_nights"] for g in goals)
    nights = got["sleep_night"]
    assert any(n["main"] is None for n in nights) and any(n["hidden"] for n in nights)
    assert any(n["main"] and n["main"]["stages"] for n in nights) and any(n["main"] and not n["main"]["stages"] for n in nights)
    assert any(n["naps"] for n in nights) and any(n["vitals"] for n in nights)
    keys = {x["key"] for n in got["notes"] for x in n}
    assert keys >= {"short_of_need", "need_met", "deep_low", "rem_low", "efficiency_low", "latency_long", "waso_long",
                    "bedtime_late", "bedtime_early", "hrv_low", "rhr_high", "resp_high", "temp_off", "spo2_low", "illness",
                    "all_good"}, keys
    today = got["today"]
    assert {x["load_status"]["key"] for x in today["scores"] if x["load_status"]} >= {"fresh", "balanced", "loading", "fatigued"}
    assert any(x["load_missing_days"] for x in today["scores"])
    assert {x["code"] for x in today["empty"] if x} == set(recovery_empty.CODES)
    assert any(x["main"] is None for x in today["sleep"]) and any(x.get("stages") for x in today["sleep"])
    assert all(today["hypnogram"]) and len(today["journal"][0]) == 6
    hs = got["healthspan"]
    assert hs["full"] and hs["no_strength"] and hs["sparky_error"] and hs["no_birth"] == [] and hs["no_origin"] == []
    assert hs["sparky_error"][-1]["notes"] and hs["full"][-1]["body_age"] is not None
    full_keys = {i["key"] for i in hs["full"][-1]["contributors"]}
    assert "activity" in full_keys and "body_comp" in full_keys
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {GOLDEN}")
    diff = G.mismatch(got, json.loads(GOLDEN.read_text()))
    assert diff is None, diff


def test_the_endpoints_are_load_plus_compute():
    """The views call the pure parts (the DB test checks the answers are unchanged)."""
    import inspect
    assert "strain_coach_compute(rows.get(d)" in inspect.getsource(coach.strain_coach)
    assert "sleep_coach_compute(rows, d, tz, goal)" in inspect.getsource(coach.sleep_coach)
    assert "health_monitor_compute(rows, d)" in inspect.getsource(coach.health_monitor)
    assert "compute(load(conn, viewer_id, owner_id, day))" in inspect.getsource(night_view.build)
    assert "view_compute(goal, sessions, tz, night, need)" in inspect.getsource(goal_store.view)
    assert "from_parts(load_parts(" in inspect.getsource(healthspan_store.Data.__init__)
    assert coach.COACH_VERSION.startswith("coach-") and healthspan_store.CURRENT.ALGO_VERSION in coach.COACH_VERSION


def test_ios_fixture(cases):
    """Watch2WhoopTests/LocalCoachTests.swift: the body-age parts of the full case as the phone has them (no Hevy import,
    no training plan) and their Python rows (the Swift loader on the local tables + bundle must give the same rows), a
    strain-coach call and the Today thresholds."""
    p = {**cases["healthspan"]["full"], "imported": {}, "plan": None}
    got = _dump({"parts": p, "today": cases["end"], "rows": healthspan_store.weekly_local(p, cases["end"]),
                 "strain": cases["strain"][-9], "strain_out": coach.strain_coach_compute(**{k: cases["strain"][-9][k]
                                                                                             for k in ("today", "yday", "d")}),
                 "today_params": WP.as_dict()})
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        IOS_FIXTURE.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {IOS_FIXTURE}")
    diff = G.mismatch(got, json.loads(IOS_FIXTURE.read_text()))
    assert diff is None, diff
