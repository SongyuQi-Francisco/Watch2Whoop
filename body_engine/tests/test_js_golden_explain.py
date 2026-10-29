"""Cross-language goldens of package M6, explain part (docs/ios-native-2.0.md sections 2.1 and 4): the Python side of
packages/openbodyscore-js/test/golden/explain.json.

packages/openbodyscore-js/test/cases_explain.py builds synthetic inputs (metric_data's JSON); this test runs the reference
code on them — explain/personal.metric_out + metric_view (every `calc_*` key), lab_out + lab_view, and
records/lab_history._history_of + card — in Chinese and English, and compares with the golden file. The static entry of
each sheet (glossary, request language) is written into the golden file too: on the phone it comes from the server's
cached sheet. test/explain.test.js replays the same inputs through the bundle (src/37_explain.js). No database (the
database side: tests/test_native_rules.py).

After an intentional change: bump explain/personal.EXPLAIN_VERSION, then BODY_WRITE_GOLDEN=1 uv run pytest
tests/test_js_golden_explain.py, port the change to src/37_explain.js, rebuild and run the Node test.
"""
import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

from body_engine.explain import glossary as GL, personal
from body_engine.i18n import use_lang
from body_engine.records import lab_history, lab_params as LP
from body_engine.scores import params as SP
from body_engine.web import views

REPO = Path(__file__).resolve().parents[2]
JS_TESTS = REPO / "packages" / "openbodyscore-js" / "test"
GOLDEN = JS_TESTS / "golden" / "explain.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("openbodyscore_js_cases_explain", JS_TESTS / "cases_explain.py")
G = C.dump.__globals__["G"]


def _dump(x) -> object:
    return json.loads(json.dumps(C.dump(x), sort_keys=True, ensure_ascii=False))


@pytest.fixture(scope="module")
def cases():
    return C.all_cases()


def _entry(e: dict) -> dict:
    return {k: e[k] for k in ("title", "what", "read", "recovery", "tips", "avoid") if k in e}


def lab_entry(key: str) -> dict:
    if key in GL.LABS:
        return _entry(GL.lab(key))
    return {"title": key, "what": "化验 / 体检指标。", "read": [], "recovery": None, "tips": [], "avoid": []}


def params() -> dict:
    trend = {}
    for k, e in GL.TREND_METRICS.items():
        with use_lang("en"):
            en = GL.metric(k)
        trend[k] = [e.get("series"), e.get("status"), e.get("digits", 0), e.get("unit", ""), en.get("unit", "")]
    return {"BASELINE_DAYS": SP.BASELINE_DAYS, "MIN_BASELINE_NIGHTS": SP.MIN_BASELINE_NIGHTS,
            "ILLNESS_RHR_DELTA": SP.ILLNESS_RHR_DELTA, "RECOVERY_PART": {k: list(v) for k, v in personal.RECOVERY_PART.items()},
            "SLEEP_PART": {k: list(v) for k, v in personal.SLEEP_PART.items()},
            "SLEEP_PART_TIP": {k: list(v) for k, v in personal.SLEEP_PART_TIP.items()},
            "VO2_LEVEL_EN": personal.VO2_LEVEL_EN, "TONE": personal.TONE,
            "LAB_SOURCE": {k: list(v) for k, v in personal.LAB_SOURCE.items()}, "TREND": trend,
            "LAB": {"TREND_FLAT_FRACTION": LP.TREND_FLAT_FRACTION, "SPARK_POINTS": LP.SPARK_POINTS,
                    "HIGH_BAD_FRACTION": LP.HIGH_BAD_FRACTION, "SOURCE_LABEL": LP.SOURCE_LABEL,
                    "GOOD_DIRECTION": LP.GOOD_DIRECTION},
            "RECOVERY_GREEN": views.RECOVERY_GREEN}


def compute_all(c: dict) -> dict:
    out = {"version": personal.EXPLAIN_VERSION, "params": params(), "entries": {}, "lab_entries": {}, "metric": [], "lab": [],
           "lab_history": []}
    for lang in C.LANGS:
        with use_lang(lang):
            out["entries"][lang] = {r["key"]: _entry(GL.metric(r["key"])) for r in c["runs"]}
            out["lab_entries"][lang] = {r["key"]: lab_entry(r["key"]) for r in c["labs"]}
    for r in c["runs"]:
        data = json.loads(json.dumps(C.dump(r["data"])))
        with use_lang(r["lang"]):
            out["metric"].append(personal.metric_view(r["key"], GL.metric(r["key"]),
                                                      personal.metric_out(r["key"], data, date.fromisoformat(r["day"]))))
    for r in c["labs"]:
        with use_lang(r["lang"]):
            o = personal.lab_out({"report": r["report"]}, r["key"], date.fromisoformat(r["day"]))
            out["lab"].append(personal.lab_view(r["key"], lab_entry(r["key"]), o, None))
    for r in c["lab_history"]:
        rows = [{**x, "collected_on": date.fromisoformat(x["collected_on"])} for x in r["rows"]]
        h = lab_history._history_of(r["key"], rows)
        out["lab_history"].append({"history": h, "card": lab_history.card(h)})
    return _dump(out)


def test_explain_goldens(cases):
    got = compute_all(cases)
    views_ = got["metric"]
    assert {v["key"] for v in views_} >= set(personal.CALC) - set(GL.TREND_METRICS) | {"illness", "steps", "resp_rate"}
    tones = {(v["key"], (v["current"] or {}).get("tone")) for v in views_}
    for key in ("hrv", "sleep_hours", "sleep_debt", "sleep_consistency", "sleep_efficiency", "bedtime", "recovery", "strain"):
        assert {t for k, t in tones if k == key} >= {"good", "mid", "bad"} - ({"mid"} if key == "hrv" else set()), key
    assert any(v["missing"] for v in views_) and any(v["current"] is None for v in views_)
    assert any(v["ai_prompt"].startswith("Please explain") for v in views_)
    assert {x["card"]["trend"] for x in got["lab_history"] if x["card"]} >= {"flat", "up", "down"}
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {GOLDEN}")
    diff = G.mismatch(got, json.loads(GOLDEN.read_text()))
    assert diff is None, diff


def test_calculators_are_load_plus_compute():
    """Every CALC wrapper hands its rows to the pure function (the DB test checks the answers are unchanged)."""
    import inspect
    src = inspect.getsource
    assert "hrv_compute(_daily_values(" in src(personal.calc_hrv)
    assert "sleep_efficiency_compute(rows)" in src(personal.calc_sleep_efficiency)
    assert "weight_compute(w, _goal_weight(conn, owner), day)" in src(personal.calc_weight)
    assert "trend_compute(key, d, day)" in src(personal.calc_trend)
    assert "metric_view(key, e, CALC[key](conn, owner, day))" in src(personal.explain_metric)
    assert personal.EXPLAIN_VERSION.startswith("explain-")
