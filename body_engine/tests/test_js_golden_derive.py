"""Cross-language goldens of 2.0.0 package M1 (docs/ios-native-2.0.md sections 2.1 and 4): the Python side of
packages/openbodyscore-js/test/golden/derive.json.

packages/openbodyscore-js/test/cases_derive.py builds the synthetic inputs; this test runs rawdata/derive.derive_days (the
reference of the phone's derivation: daily values, phase-1 types, common.resolve with the incomplete rules and
SOURCE_PRIORITY, the OW sleep rules, sessionize) on them and compares with the golden file. test/derive.test.js runs the
JS bundle on the same inputs against the same file. The file also carries the HealthKit registry rows and the thresholds
the JS mirrors (D7), and the inputs of the `new_york` case for the iOS test (Watch2WhoopTests/LocalDeriveTests.swift).
No database. Rewrite after an intentional change (with a DERIVE_VERSION bump): BODY_WRITE_GOLDEN=1 uv run pytest
tests/test_js_golden_derive.py.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from body_engine.importers import common
from body_engine.rawdata import derive, hk_types as T, params as RP
from body_engine.rawdata.engine import MAX_SAMPLE_SPAN

REPO = Path(__file__).resolve().parents[2]
JS_TESTS = REPO / "packages" / "openbodyscore-js" / "test"
GOLDEN = JS_TESTS / "golden" / "derive.json"
IOS_CASE = "new_york"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("openbodyscore_js_cases_derive", JS_TESTS / "cases_derive.py")
G = _load("openbodyscore_js_cases", JS_TESTS / "cases.py").G


def registry() -> list[dict]:
    """The rows of rawdata/hk_types the phone's derivation needs (src/30_derive.js TYPES)."""
    return [{"hk": t.hk, "key": t.key, "kind": t.kind, "agg": t.agg, "factor": t.factor, "store_unit": t.store_unit,
             "lo": t.lo, "hi": t.hi, "stats": t.stats, "phase1": t.hk in T.BY_HK,
             "phase1_unit": T.BY_HK[t.hk].unit if t.hk in T.BY_HK else None}
            for t in T.ENGINE_TYPES if t.kind in ("quantity", "category")]


def params() -> dict:
    """Thresholds and rules the JS mirrors (src/30_derive.js PARAMS), with their sources in the Python."""
    return {"SOURCE_PRIORITY": common.SOURCE_PRIORITY, "DEGRADED_FLAGS": sorted(common.DEGRADED_FLAGS),
            "ENERGY_METRICS": sorted(common.ENERGY_METRICS), "INCOMPLETE_BASAL_SHARE": common.INCOMPLETE_BASAL_SHARE,
            "BASAL_REF_DAYS": common.BASAL_REF_DAYS, "BASAL_REF_MIN_DAYS": common.BASAL_REF_MIN_DAYS,
            "WORKOUT_KCAL_SLACK": common.WORKOUT_KCAL_SLACK, "SLEEP_GAP_MIN": common.SLEEP_GAP.total_seconds() / 60,
            "WAKE_DAY_SHIFT_H": common.WAKE_DAY_SHIFT.total_seconds() / 3600, "MIN_ASLEEP_MIN": common.MIN_ASLEEP_MIN,
            "SLEEP_STAGES": {str(k): v for k, v in RP.SLEEP_STAGES.items()},
            "SESSION_GAP_MIN": RP.SESSION_GAP.total_seconds() / 60, "SESSION_START": sorted(RP.SESSION_START),
            "DETAILED_STAGES": sorted(RP.DETAILED_STAGES), "ASLEEP_STAGES": sorted(RP.ASLEEP_STAGES),
            "SLEEP_DAYS_BEFORE": RP.SLEEP_DAYS_BEFORE, "SLEEP_DAYS_AFTER": RP.SLEEP_DAYS_AFTER,
            "MAX_SAMPLE_SPAN_S": MAX_SAMPLE_SPAN.total_seconds(), "ALGO_VERSION": derive.DERIVE_VERSION}


def _call(c: dict):
    if c["fn"] == "normalize":
        return list(common.normalize(*c["args"]))
    return getattr(common, c["fn"])(*c["args"])


def _dump(x):
    return json.loads(json.dumps(x, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v),
                                 sort_keys=True, ensure_ascii=False))


def _columns(rows: list[dict], cols: list[str]) -> dict:
    return {"cols": cols, "rows": [[r.get(c) for c in cols] for r in rows]}


def build() -> dict:
    data = C.all_cases()
    cases = []
    for case in data["cases"]:
        entry = {"name": case["name"], "want": derive.derive_days(case["src"])}
        if case["name"] == IOS_CASE:   # the iOS test's fixture: the same rows, columnar
            src = dict(case["src"])
            src["samples"] = _columns(src["samples"], ["uuid", "type", "start", "end", "value", "source", "source_id",
                                                       "device_type", "device_name"])
            src["buckets"] = _columns(src["buckets"], ["type", "start", "value"])
            entry["src"] = src
        cases.append(entry)
    return _dump({"registry": registry(), "params": params(), "cases": cases,
                  "calls": [{**c, "want": _call(c)} for c in data["calls"]]})


def test_derive_golden():
    got = build()
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {GOLDEN}")
    diff = G.mismatch(got, json.loads(GOLDEN.read_text()))
    assert diff is None, diff


def test_cases_cover_the_contract():
    """Each scenario of the M1 golden list (docs/ios-native-2.0.md section 4) is in the expected output."""
    data = build()
    syd = next(c["want"] for c in data["cases"] if c["name"] == "sydney")
    assert len(syd["daily"]) >= 60
    chosen = {(d, r["metric"]): r for d, v in syd["daily"].items() for r in v["chosen"]}
    sources = {(d, r["metric"]): r for d, v in syd["daily"].items() for r in v["sources"]}
    flags = [r["flags"] for r in chosen.values()]
    assert any("hourly_sum" in f for f in flags), "a day with only the Shortcut's hourly sums"
    assert any("incomplete" in r["flags"] for r in chosen.values()) or any(
        r["source"] == "ow" and r["metric"] in common.ENERGY_METRICS for r in chosen.values()), "partial energy"
    assert any(r["source"] == "ow" and r["metric"] == "basal_energy_burned" for r in chosen.values()), "basal < 50 %"
    assert any(r["source"] == "ow" and r["metric"] == "active_energy_burned" for r in chosen.values()), "workout > active"
    assert any(r["source"] == "apple_export" and r["metric"] == "heart_rate" for r in chosen.values()), "deleted samples"
    assert any(r["source"] == "app" and r["metric"] == "heart_rate" for r in chosen.values())
    ex = [r for k, r in sources.items() if k[1] == "apple_exercise_time"]
    assert ex and all(r["agg"] == "sum" for r in ex), "phase-1 types (largest single source)"
    assert any(k[1] == "oxygen_saturation" and 90 <= r["value"] <= 100 for k, r in sources.items()), "SpO2 factor"
    nights = [s for v in syd["sleep"].values() for s in v]
    assert any(not s["is_main"] for s in nights), "a nap / second session"
    assert any(len(v) >= 2 for v in syd["sleep"].values())
    assert any(s["device"] == "iPhone" for s in nights), "an in-bed-only night counts"
    assert "2026-04-05" in syd["sleep"] and syd["sleep"]["2026-04-05"], "the DST night"
