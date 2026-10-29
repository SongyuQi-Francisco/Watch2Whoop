"""Cross-language goldens of package M3 (docs/ios-native-2.0.md sections 2.1 and 4): the Python side of
packages/openbodyscore-js/test/golden/series.json.

packages/openbodyscore-js/test/cases_series.py builds synthetic inputs (what each `load` returns); this test runs the
reference code on them — trends/series.compute, web/views.month_days_compute and sleep_nights_compute,
insights/sleep_detail.watch_sessions + details, insights/fitness.hr_zones_compute / vo2max_compute / level — and compares
with the golden file. test/series.test.js replays the same inputs through the bundle (src/33_series.js). No database.

After an intentional change: bump trends/series.SERIES_VERSION, then BODY_WRITE_GOLDEN=1 uv run pytest
tests/test_js_golden_series.py, port the change to src/33_series.js, rebuild (node packages/openbodyscore-js/build.js) and
run the Node test.
"""
import importlib.util
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from body_engine.i18n import use_lang
from body_engine.insights import fitness, sleep_detail
from body_engine.trends import series
from body_engine.web import views

REPO = Path(__file__).resolve().parents[2]
JS_TESTS = REPO / "packages" / "openbodyscore-js" / "test"
GOLDEN = JS_TESTS / "golden" / "series.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CS = _load("openbodyscore_js_cases_series", JS_TESTS / "cases_series.py")
G = CS.dump.__globals__["G"]   # golden_cases (mismatch with its float tolerance), loaded by cases.py


def _dump(x) -> object:
    return json.loads(json.dumps(x, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v),
                                 sort_keys=True, ensure_ascii=False))


@pytest.fixture(scope="module")
def cases():
    return CS.all_cases()


def compute_all(c: dict) -> dict:
    tz = ZoneInfo(c["series"]["tz"])
    s = c["series"]
    out = {"series": [], "month": [], "nights": [], "sleep_detail": [], "vo2": [], "zones": [], "levels": []}
    for r in s["runs"]:
        data = CS.variant(s["data"], s["variants"][r["data"]])
        with use_lang(r["lang"]):
            out["series"].append(series.compute(data, r["end"], r["days"], r["today"], tz, r["now_hour"]))
    for m in c["month"]:
        out["month"].append(views.month_days_compute(m["month"], m["today"], m["scores"], m["present"], m["firsts"], m["hidden"]))
    for n in c["nights"]:
        out["nights"].append(views.sleep_nights_compute(n["sessions"], n["scores"], n["can_scores"], n["end"], n["days"], tz))
    for d in c["sleep_detail"]:
        out["sleep_detail"].append(sleep_detail.details(sleep_detail.watch_sessions(d["rows"]),
                                                        sleep_detail.nights_from(d["nights"]), tz, d["start"], d["end"]))
    f = c["fitness"]
    for v in f["vo2"]:
        with use_lang(v["lang"]):
            out["vo2"].append(fitness.vo2max_compute(v["rows"], v["birth"], v["sex"], v["end"]))
    for z in f["zones"]:
        out["zones"].append(fitness.hr_zones_compute(z["rows"], z["workouts"], f["zones_start"], f["end"], z["with"]))
    out["levels"] = [list(fitness.level(p)) for p in f["levels"]]
    return _dump(out)


def test_series_goldens(cases):
    got = compute_all(cases)
    # coverage of the cases themselves
    runs = got["series"]
    full = runs[3]["series"]
    assert len(full) == len(series.SERIES) and all(k in full for k, *_ in series.SERIES), sorted(set(dict.fromkeys(
        k for k, *_ in series.SERIES)) - set(full))
    assert full["hrv"]["baseline"]["mean"][-1] is not None and full["recovery"]["prev_mean"] is not None
    early = runs[7]["series"]["hrv"]["baseline"]["mean"]
    assert early[0] is None, "the early run must start with < 14 baseline days"
    status = runs[0]["status"]
    assert all(status.get(k) is not None for k in ("hrv_status", "training_status", "load_focus", "resilience",
                                                   "body_battery", "chronotype", "hr_recovery")), status
    assert runs[9]["series"]["recovery"]["label"] == "Recovery"
    assert "weight" in runs[-1]["unavailable"] and "hrv" in runs[10]["series"] and "recovery" not in runs[10]["series"]
    assert {r["sri_7d"] is not None for r in got["sleep_detail"]} == {True, False}
    assert any(n["reason"] for n in got["nights"][0]["nights"]) and any(n["naps"] for n in got["nights"][0]["nights"])
    assert {v["level"] for v in got["vo2"]} >= {None, "good"} or len({v["level"] for v in got["vo2"]}) >= 2
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {GOLDEN}")
    diff = G.mismatch(got, json.loads(GOLDEN.read_text()))
    assert diff is None, diff


def test_build_is_load_plus_compute():
    """The HTTP view still calls both halves (the DB tests in test_trends.py check the full output)."""
    import inspect
    src = inspect.getsource(series.build)
    assert "compute(load(" in src
    assert series.SERIES_VERSION and series.SERIES_VERSION.startswith("series-")


IOS_FIXTURE = JS_TESTS / "golden" / "series_local.json"


def test_ios_fixture():
    """Watch2WhoopTests/TrendsSeriesTests.swift: the phone's local tables of 120 synthetic days and what the server's
    Python answers for them (the Swift loader + bundle must give the same, docs/ios-native-2.0.md M3 acceptance)."""
    c = CS.ios_case()
    tz = ZoneInfo(c["tz"])
    m, n = c["month"], c["nights"]
    expected = {
        "trends": series.compute(c["trends_data"], c["end"], c["days"], c["today"], tz, c["now_hour"]),
        "month": views.month_days_compute(m["month"], m["today"], m["scores"], m["present"], m["firsts"], m["hidden"]),
        "nights": views.sleep_nights_compute(n["sessions"], n["scores"], True, c["end"], 14, tz),
        "details": sleep_detail.details(sleep_detail.watch_sessions(n["sessions"]), sleep_detail.nights_from(c["detail_nights"]),
                                        tz, c["end"] - timedelta(days=13), c["end"]),
        "vo2": fitness.vo2max_compute(c["vo2"], c["profile"]["birth_date"], fitness.SEXES.get(c["profile"]["sex"].lower()),
                                      c["end"]),
    }
    got = _dump({"tz": c["tz"], "end": c["end"], "today": c["today"], "days": c["days"], "now_hour": c["now_hour"],
                 "month": m["month"], "profile": c["profile"], "server_only": c["server_only"],
                 "tables": CS.dump(c["tables"]), "expected": expected})
    assert got["expected"]["trends"]["series"]["recovery"]["n"] > 20 and got["expected"]["details"]["sri_30d"] is not None
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        IOS_FIXTURE.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {IOS_FIXTURE}")
    diff = G.mismatch(got, json.loads(IOS_FIXTURE.read_text()))
    assert diff is None, diff
