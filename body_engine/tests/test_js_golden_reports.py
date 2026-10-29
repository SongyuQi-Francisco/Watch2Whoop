"""Cross-language goldens of package M5 (docs/ios-native-2.0.md sections 2.1 and 4): the Python side of
packages/openbodyscore-js/test/golden/reports.json.

packages/openbodyscore-js/test/cases_reports.py builds synthetic inputs; this test runs the reference code on them —
records/regression (inverse, normal p, Benjamini-Hochberg, ols_hac), records/analysis (regress, analyze_data) and
insights/reports.compute — and compares with the golden file. test/reports.test.js replays the same inputs through the bundle
(src/35_reports.js). No database (the database side: tests/test_native_reports.py).

Tolerance: the Node test's 1e-9 relative (harness.js). The analysis numbers the product shows are rounded (effect, se, p, q to
4 decimals, z to 3), so the rounded values must be identical; the raw ols_hac outputs are compared within 1e-9 (the
contract's 1e-6 for effect / p / q, docs/ios-native-2.0.md section 3.4, with a wide margin).

After an intentional change: bump insights/reports_params.REPORTS_VERSION, then BODY_WRITE_GOLDEN=1 uv run pytest
tests/test_js_golden_reports.py, port the change to src/35_reports.js, rebuild (node packages/openbodyscore-js/build.js) and
run the Node test.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from body_engine.insights import reports, reports_params as RP
from body_engine.records import analysis, analysis_params as AP, regression as RG

REPO = Path(__file__).resolve().parents[2]
JS_TESTS = REPO / "packages" / "openbodyscore-js" / "test"
GOLDEN = JS_TESTS / "golden" / "reports.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("openbodyscore_js_cases_reports", JS_TESTS / "cases_reports.py")
G = C.dump.__globals__["G"]   # golden_cases (mismatch with its float tolerance), loaded by cases.py


def _dump(x) -> object:
    return json.loads(json.dumps(C.dump(x), sort_keys=True, ensure_ascii=False))


@pytest.fixture(scope="module")
def cases():
    return C.all_cases()


def params() -> dict:
    return {"reports": {"REPORTS_VERSION": RP.REPORTS_VERSION, "PERIOD_DAYS": RP.PERIOD_DAYS,
                        "RECOVERY_GREEN": RP.RECOVERY_GREEN, "RECOVERY_RED": RP.RECOVERY_RED,
                        "SHORT_SLEEP_MIN": RP.SHORT_SLEEP_MIN, "HIGH_STRAIN": RP.HIGH_STRAIN,
                        "JOURNAL_DAYS": RP.JOURNAL_DAYS, "JOURNAL_TOP": RP.JOURNAL_TOP},
            "analysis": {"MIN_N": AP.MIN_N, "RELIABLE_N": AP.RELIABLE_N, "HAC_LAG_DAYS": AP.HAC_LAG_DAYS,
                         "FDR_Q": AP.FDR_Q, "REG_MIN_DF": AP.REG_MIN_DF, "SINGULAR_TOL": AP.SINGULAR_TOL}}


def _inverse(m):
    try:
        return RG._inverse(m, AP.SINGULAR_TOL)
    except RG.Singular as e:
        return {"singular": e.args[0]}


def compute_all(c: dict) -> dict:
    rg = c["regression"]
    out = {"version": reports.REPORTS_VERSION, "params": params(),
           "regression": {"inverse": [_inverse(m) for m in rg["inverse"]],
                          "norm_p": [RG._norm_p(z) for z in rg["norm_p"]],
                          "bh": [RG.bh(p) for p in rg["bh"]],
                          "ols": [RG.ols_hac(o["days"], o["y"], o["x"], o["names"]) for o in rg["ols"]]},
           "regress": [], "analysis": [], "reports": []}
    for k in c["regress"]:
        res = analysis.regress(k["obs"], k["higher"])
        res.pop("_higher_is_better", None)
        out["regress"].append(res)
    for r in c["analysis_runs"]:
        out["analysis"].append(analysis.analyze_data(c["journal"], r["end"], r["days"], r["regression"], r.get("key"),
                                                     r.get("threshold", 0.0)))
    for r in c["report_runs"]:
        out["reports"].append(reports.compute(C.variant(c["report"], r["variant"]), r["period"], r["end"]))
    return _dump(out)


def test_reports_goldens(cases):
    got = compute_all(cases)
    # coverage of the cases themselves
    inv = got["regression"]["inverse"]
    assert inv[2] == {"singular": 1} and inv[3] == {"singular": 0} and isinstance(inv[0], list)
    statuses = [r["status"] for r in got["regress"]]
    assert statuses == ["ok", "ok", "insufficient", "insufficient", "collinear", "collinear", "insufficient", "insufficient"]
    assert "残差为 0" in got["regress"][5]["message"] and "是 0.0 天" in got["regress"][6]["message"]
    assert "是 0 天" in got["regress"][7]["message"], "sum() of nothing is the int 0"
    full = {a["item"]["key"]: a for a in got["analysis"][5]}   # 730 days, regression
    reg = lambda k: full[k]["outcomes"]["next_recovery"]["regression"]   # noqa: E731
    assert reg("tuesday_class")["status"] == "collinear" and reg("never")["status"] == "insufficient"
    assert reg("rare")["status"] == "insufficient" and reg("gappy")["status"] == "ok"
    assert reg("sauna")["p"] == reg("sauna_copy")["p"] and reg("sauna")["p_adj"] == reg("sauna_copy")["p_adj"]
    assert reg("sauna")["direction"] == "better" and reg("alcohol")["direction"] == "worse"
    assert reg("coffee")["direction"] == "unclear" and reg("sauna")["bh_tests"] >= 4
    assert reg("sauna")["p_adj"] >= reg("sauna")["p"]
    assert all(o["status"] == "insufficient" for a in got["analysis"][0] for o in a["outcomes"].values()), "14 days"
    assert any(o.get("regression", {}).get("status") == "ok" and o["regression"]["n"] < o["yes"]["n"] + o["no"]["n"]
               for a in got["analysis"][5] for o in a["outcomes"].values()), "missing covariates leave days out"
    reps = got["reports"]
    assert reps[0]["current"]["recovery"]["days"] > 0 and reps[0]["journal_findings"] and "hidden" not in reps[0]
    assert reps[0]["current"]["training"]["sessions"] is not None and reps[0]["current"]["weight"]["change_kg"] is not None
    assert reps[0]["current"]["sleep"]["consistency_pct"] is not None and reps[0]["current"]["sleep"]["avg_bedtime"]
    assert reps[6]["current"]["recovery"]["days"] == 0 and reps[6]["current"]["sleep"]["avg_bedtime"] is None
    by = {(r["variant"], r["period"]): reps[i] for i, r in enumerate(cases["report_runs"])}
    assert by[("no_stress_table", "week")]["current"]["stress"] == {"days": 0, "avg": None}
    phone = by[("phone_without_server_blocks", "week")]
    assert not {"stress", "training", "weight", "nutrition"} & set(phone["current"]) and "journal_findings" not in phone
    assert by[("sparky_errors", "week")]["current"]["weight"] == {"error": C.ERR}
    assert set(by[("nutrition_summary", "week")]["current"]["nutrition"]) == set(reports.NUTRITION_SUMMARY)
    assert by[("nothing", "week")]["current"] == {} and len(by[("nothing", "week")]["hidden"]) == 6
    assert by[("journal_results_failed", "week")]["journal_findings"] == []
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        pytest.skip(f"wrote {GOLDEN}")
    diff = G.mismatch(got, json.loads(GOLDEN.read_text()))
    assert diff is None, diff


def test_report_is_load_plus_compute_and_analysis_shares_analyze_rows():
    import inspect
    assert "compute(load(conn, user_id, period, end, viewer_id, sparky), period, end)" in inspect.getsource(reports.report)
    assert "analyze_rows(item, rows, start, end, threshold, regression)" in inspect.getsource(analysis._analyze)
    assert reports.REPORTS_VERSION == RP.REPORTS_VERSION and reports.REPORTS_VERSION.startswith("reports-")
