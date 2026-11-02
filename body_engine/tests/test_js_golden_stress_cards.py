"""Cross-language goldens of package M2 (docs/ios-native-2.0.md sections 2.1 and 4): the Python side of
packages/openbodyscore-js/test/golden/stress_cards.json (and stress_local.json, the iOS fixture).

packages/openbodyscore-js/test/cases_stress_cards.py builds synthetic inputs; this test runs the reference code on them —
insights/stress.days_local (the phone's tables -> merge_app -> score_range), insights/stress.cards (resilience, body battery,
heart-rate recovery) and cardio/views.compute — and compares with the golden files. test/stress_cards.test.js replays the
same inputs through the bundle (src/32_stress.js). No database (the DB tests of stress.compute: test_apple_sync_read.py,
test_apple_sync_cutover.py, test_openbodyscore_golden.py, test_native_stress.py).

After an intentional change: bump insights/stress.STRESS_CARDS_VERSION, then BODY_WRITE_GOLDEN=1 uv run pytest
tests/test_js_golden_stress_cards.py, port the change to src/32_stress.js, rebuild (node packages/openbodyscore-js/build.js)
and run the Node test.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from body_engine.cardio import hrr as H, views as cardio_views
from body_engine.i18n import use_lang
from body_engine.insights import stress
from body_engine.trends import series

REPO = Path(__file__).resolve().parents[2]
JS_TESTS = REPO / "packages" / "openbodyscore-js" / "test"
GOLDEN = JS_TESTS / "golden" / "stress_cards.json"
IOS_FIXTURE = JS_TESTS / "golden" / "stress_local.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("openbodyscore_js_cases_stress_cards", JS_TESTS / "cases_stress_cards.py")
G = C.dump.__globals__["G"]   # golden_cases (mismatch with its float tolerance), loaded by cases.py


def _dump(x) -> object:
    return json.loads(json.dumps(C.dump(x), sort_keys=True, ensure_ascii=False))


@pytest.fixture(scope="module")
def cases():
    return C.all_cases()


def compute_all(c: dict) -> dict:
    out = {"local": [], "cards": [], "cardio": [],
           "params": {"HRR_ALGO": H.ALGO, "TREND_DAYS": H.TREND_DAYS, "PEAK_MIN_PCT": H.PEAK_MIN_PCT},
           "version": stress.STRESS_CARDS_VERSION}
    loc = c["local"]
    for r in loc["runs"]:
        out["local"].append(stress.days_local(C.variant_src(loc["src"], r)))
    k = c["cards"]
    tz = ZoneInfo(k["tz"])
    for name in [None, *sorted(k["variants"])]:
        data = {**k["data"], **(k["variants"][name] if name else {})}
        for r in (k["runs"] if name is None else k["runs"][:1]):
            with use_lang(r["lang"]):
                out["cards"].append(stress.cards(data, r["end"], r["today"], tz, r["now_hour"]))
    d = c["cardio"]
    data = {"tz": ZoneInfo(d["tz"]), "hrr": d["hrr"], "scores": d["scores"]}
    for r in d["runs"]:
        out["cardio"].append(cardio_views.compute(data, r["start"], r["end"]))
    return _dump(out)


def test_stress_cards_goldens(cases):
    got = compute_all(cases)
    # coverage of the cases themselves
    rows = got["local"][0]
    assert len(rows) == C.SCORED
    flags = {f for r in rows for f in r["flags"]}
    assert flags >= {"no_raw_hr"}, flags
    assert any("快捷指令" in (r["flags"].get("no_raw_hr") or "") for r in rows)
    assert {r["level"] for r in rows} >= {"low", "medium"} and sum(r["high_h"] for r in rows) > 0
    assert any(r["hours"] and len(r["hours"]) < 14 for r in rows), "steps-only hours must drop out"
    assert any("baseline_insufficient" in r["flags"] for r in got["local"][1])
    assert all("no_motion_data" in r["flags"] for r in got["local"][2] if r["score"] is not None)
    cards = got["cards"]
    assert all(cards[0][k] is not None for k in ("resilience", "body_battery", "hr_recovery")), cards[0]
    assert cards[1]["resilience"]["label"] != cards[0]["resilience"]["label"], "the English run"
    assert any(x["body_battery"] is None for x in cards) and any(x["hr_recovery"] is None for x in cards)
    cardio = got["cardio"]
    assert cardio[0]["hrr"]["last30_n"] and cardio[0]["hrr"]["prev30_n"] and cardio[0]["recovery_index"]["median_pct"]
    assert any(n["pct"] is None for n in cardio[0]["recovery_index"]["nights"])
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {GOLDEN}")
    diff = G.mismatch(got, json.loads(GOLDEN.read_text()))
    assert diff is None, diff


def test_cards_equal_the_trends_status(cases):
    """insights/stress.cards is the status part of trends/series.compute for these keys (the phone's TrendsLocal calls
    the bundle's stressCards; the server's Trends document builds the same cards inside series.compute)."""
    k = cases["cards"]
    tz = ZoneInfo(k["tz"])
    data = C.CS.person()
    for r in k["runs"]:
        with use_lang(r["lang"]):
            status = series.compute(data, r["end"], 30, r["today"], tz, r["now_hour"])["status"]
            got = stress.cards(data, r["end"], r["today"], tz, r["now_hour"])
        assert _dump(got) == _dump({key: status.get(key) for key in got}), r


def test_compute_is_load_plus_score_range_and_cardio_is_load_plus_compute():
    import inspect
    assert "load(conn, client, user_id, start, end, ow_cache)" in inspect.getsource(stress.compute)
    assert "compute(load(conn, user_id, start, end), start, end)" in inspect.getsource(cardio_views.cardio)
    assert stress.STRESS_CARDS_VERSION.startswith("stress-cards-") and stress.STRESS_VERSION in stress.STRESS_CARDS_VERSION


def test_ios_fixture(cases):
    """Watch2WhoopTests/LocalStressTests.swift: the local case's samples as the phone's tables and the Python answers
    (the Swift loader + bundle must give the same rows, docs/ios-native-2.0.md M2 acceptance: 60 synthetic days)."""
    loc = cases["local"]
    got = _dump({"src": loc["src"], "rows": stress.days_local(loc["src"])})
    assert len(got["rows"]) == C.SCORED
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        IOS_FIXTURE.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {IOS_FIXTURE}")
    diff = G.mismatch(got, json.loads(IOS_FIXTURE.read_text()))
    assert diff is None, diff
