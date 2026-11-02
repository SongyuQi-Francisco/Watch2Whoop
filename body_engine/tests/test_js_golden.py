"""Cross-language goldens (issue #340 N1): the Python side of packages/openbodyscore-js/test/golden/*.json.

packages/openbodyscore-js/test/cases.py builds one set of synthetic inputs; this test runs the server's Python code on them —
scores/engine.score_days, insights/stress.score_range, insights/healthspan_store.snapshot / weekly, trends/calc and the pure
helpers of trends/series and insights/coach — and compares with the golden files. The Node test runs the JS bundle on the same
inputs and compares with the same files, so a pass on both sides means the two languages agree (the package's own goldens,
scores.json and healthspan_v12.json, are shared the same way). No database.

After an intentional algorithm change: bump the version, then BODY_WRITE_GOLDEN=1 uv run pytest tests/test_js_golden.py,
port the change to packages/openbodyscore-js/src, rebuild (node packages/openbodyscore-js/build.js) and run the Node test.
"""
import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from body_engine.insights import coach, healthspan_store, sleep_detail, stress
from body_engine.scores import engine
from body_engine.scores.inputs import Inputs, Night
from body_engine.scores.muscle import daily_muscle_load
from body_engine.trends import calc, series

REPO = Path(__file__).resolve().parents[2]
JS_TESTS = REPO / "packages" / "openbodyscore-js" / "test"
GOLDEN = JS_TESTS / "golden"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("openbodyscore_js_cases", JS_TESTS / "cases.py")
G = C.G


def _dump(x) -> object:
    """Through JSON as the golden files store it: dates / datetimes as ISO strings."""
    return json.loads(json.dumps(x, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v),
                                 sort_keys=True, ensure_ascii=False))


def _check(name: str, got) -> None:
    path = GOLDEN / f"{name}.json"
    got = _dump(got)
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(json.dumps(got, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
        pytest.skip(f"wrote {path}")
    diff = G.mismatch(got, json.loads(path.read_text()))
    assert diff is None, diff


@pytest.fixture(scope="module")
def data():
    return G.generate()


def engine_rows(case: dict) -> list:
    i = case["inputs"]
    inp = Inputs(tz=G.TZ, birth_date=i["birth_date"], hrv_kind=i["hrv_kind"], nights=[Night(**n) for n in i["nights"]],
                 non_watch_dates=set(i["non_watch_dates"]), segments=i["segments"], hr=i["hr"], hrv=i["hrv"],
                 resp=i["resp"], spo2=i["spo2"], temp=i["temp"], daily_rhr=i["daily_rhr"],
                 shortcut_dates=set(i["shortcut_dates"]), muscle=daily_muscle_load(i["muscle_sessions"]),
                 muscle_error=i["muscle_error"])
    return [engine.score_days(inp, r["first"], r["last"], r["today"]) for r in case["runs"]]


def test_engine(data):
    runs = engine_rows(C.engine_case(data))
    # coverage of the case itself: every branch the engine has
    rows = runs[0]
    flags = {k for r in rows for k in r["flags"]}
    assert {"no_sleep", "no_stage_segments", "rhr_from_daily", "baseline_insufficient", "day_in_progress", "ctl_warmup",
            "prev_strain_missing", "no_raw_hr", "hr_coverage_low"} <= flags, flags
    assert sum(r["recovery"] is not None for r in rows) >= 30 and sum(r["strain"] is not None for r in rows) >= 50
    assert any(r["details"].get("sleep", {}).get("nap_min") for r in rows)
    _check("engine", {"runs": runs})


def test_stress(data):
    case = C.stress_case(data)
    out = [stress.score_range(G.TZ, r["start"], r["end"], case["sleep"], case["workouts"], case["hr"],
                              case["steps"] if r["steps"] else [], case["hrv"], case["hrv_night"], set(case["shortcut"]))
           for r in case["runs"]]
    assert sum(x["score"] is not None for x in out[0]) >= 20
    _check("stress", {"runs": out})


def _healthspan_data(case: dict, changes: dict | None = None) -> SimpleNamespace:
    d = {**case["data"], **(changes or {})}
    d["sleep_days"] = {w: sleep_detail.asleep_minutes(case["intervals"], w, G.TZ) for w in case["wake_dates"]}
    d["strength_journal"] = set(d["strength_journal"])
    return SimpleNamespace(**d)


def test_healthspan():
    case = C.healthspan_case()
    base = _healthspan_data(case)
    snaps = [[a, v, healthspan_store.snapshot(base, a, healthspan_store.VERSIONS[v])]
             for a in case["as_of"] for v in ("v1.1", "v1.2")]
    variants = {k: healthspan_store.snapshot(_healthspan_data(case, ch), case["today"]) for k, ch in case["variants"].items()}
    weekly = healthspan_store.weekly(base, case["origin"], case["today"])
    assert weekly[-1]["pace_of_aging"] is not None and weekly[-1]["body_age"] is not None
    _check("healthspan", {"snapshots": snaps, "variants": variants, "weekly": weekly})


SERIES_FNS = {"baseline_band", "rolling", "hr_zone_summary"}
COACH_FNS = {"recovery_zone", "strain_target"}


def trends_fn(name: str):
    return getattr(series if name in SERIES_FNS else coach if name in COACH_FNS else calc, name)


def test_trends(data):
    calls = C.trends_calls(data)
    out = [{"fn": c["fn"], "value": trends_fn(c["fn"])(*c["args"])} for c in calls]
    statuses = {x["value"]["status"] for x in out if x["fn"] == "training_status" and x["value"]}
    assert {"overreaching", "detraining", "recovery", "strained", "productive", "unproductive",
            "maintaining"} <= statuses, statuses
    _check("trends", out)


def test_cases_dump_round_trips_instants(data):
    """Milliseconds with the microseconds as fraction come back exact (what the Node test relies on)."""
    from datetime import timedelta
    for n in data["nights"][:5]:
        for t, _ in n["hr"]:
            us = round(json.loads(json.dumps(C.ms(t))) * 1000)
            assert C.EPOCH + timedelta(microseconds=us) == t
    assert C.dump({date(2026, 8, 1): {1, 2}}) == {"2026-08-01": [1, 2]}
