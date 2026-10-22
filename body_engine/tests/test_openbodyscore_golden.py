"""The scoring algorithms moved into packages/openbodyscore (issue #115); these golden tests prove no number changed.

1. Pure functions through the old `body_engine.*` import paths: the same cases and golden file as the package's own
   test (packages/openbodyscore/tests/test_golden.py).
2. End to end: quickstart/demo_data.py's synthetic export (fixed seed and date) -> the real Apple export importer ->
   scoring engine, body age and stress -> compared with tests/fixtures/golden_e2e_<hrv kind>.json.
Both golden files were written from the code before the extraction. To rewrite the end-to-end file after an
intentional algorithm change (bump ALGO_VERSION): BODY_WRITE_GOLDEN=1 uv run pytest tests/test_openbodyscore_golden.py

HRV kinds: one end-to-end golden file per entry of HRV_KINDS (scores/hrv_kind.py picks one per user, #117). The demo
export has no beat-to-beat series, so for "rmssd" the test adds a synthetic one per HRV reading (synthetic_beats; the
export itself and the "sdnn" file are untouched). details.hrv_kind (added with #117) is checked against the kind and then
removed before comparing, so golden_e2e_sdnn.json is still the file written before the per-user choice existed.
"""
import importlib.util
import io
import json
import os
import sys
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import apple_zip
from body_engine.insights import healthspan_store, stress
from body_engine.scores import engine

REPO = Path(__file__).resolve().parents[2]
PKG_TESTS = REPO / "packages" / "openbodyscore" / "tests"
FIXTURES = Path(__file__).parent / "fixtures"
HRV_KINDS = ["sdnn", "rmssd"]
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_golden")
TZ = ZoneInfo("Australia/Sydney")
NOW = datetime(2026, 9, 30, 9, 0, tzinfo=TZ)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _dump(x) -> object:
    """Round trip through JSON: dates / datetimes as ISO strings, so the result compares with the golden file (golden_cases.mismatch)."""
    return json.loads(json.dumps(x, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v),
                                 sort_keys=True, ensure_ascii=False))


# ---------------------------------------------------------------- 1. pure functions via the old import paths
def test_old_import_paths_match_golden():
    cases = _load("openbodyscore_golden_cases", PKG_TESTS / "golden_cases.py")
    diff = cases.mismatch(cases.compute(cases.body_engine_modules()), json.loads(cases.GOLDEN.read_text()))
    assert diff is None, diff


def test_old_import_paths_are_the_package():
    """The thin re-export layer: old paths give the package's own objects, so monkeypatching and identity checks still work."""
    import openbodyscore
    from body_engine.insights import healthspan, healthspan_params
    from body_engine.rawdata import hrv
    from body_engine.scores import muscle, params, recovery, sleep, strain
    for old, new in ((params, openbodyscore.params), (recovery, openbodyscore.recovery), (sleep, openbodyscore.sleep),
                     (strain, openbodyscore.strain), (muscle, openbodyscore.muscle), (hrv, openbodyscore.hrv)):
        assert old is new
    assert healthspan.pace is openbodyscore.healthspan.pace
    assert healthspan_params.SRI_KNOTS is openbodyscore.healthspan_params.SRI_KNOTS
    assert stress.score_day is openbodyscore.stress.score_day and stress.BASELINE_DAYS == 14


# ---------------------------------------------------------------- 2. end to end through the database
@pytest.fixture(scope="module")
def conn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    c = psycopg.connect(Settings(db_name=TEST_DB).dsn, row_factory=dict_row, autocommit=True)
    migrate(c)
    yield c
    c.close()


class ExportOW:
    """Serves the imported export's raw heart rate / HRV as Open Wearables /timeseries and its workouts as /events/workouts (what stress reads)."""

    TYPES = {"hr": ("heart_rate", "bpm"), "hrv": ("heart_rate_variability_sdnn", "ms")}

    def __init__(self, conn, uid):
        self.raw = [{"timestamp": r["start_at"].isoformat(), "type": self.TYPES[r["kind"]][0], "value": r["value"],
                     "unit": self.TYPES[r["kind"]][1],
                     "source": {"source": "healthkit", "device_type": "watch", "device": "Watch7,9"}}
                    for r in conn.execute("""SELECT kind, start_at, value FROM export_samples WHERE user_id = %s
                                             AND kind IN ('hr', 'hrv') ORDER BY start_at, kind""", (uid,))]
        self.workouts = [{"start_time": r["start_at"].isoformat(), "end_time": r["end_at"].isoformat()}
                         for r in conn.execute("SELECT start_at, end_at FROM workouts WHERE user_id = %s ORDER BY start_at",
                                               (uid,))]

    def paginate(self, path, **params):
        if path.endswith("/timeseries"):
            lo, hi = datetime.fromisoformat(params["start_time"]), datetime.fromisoformat(params["end_time"])
            yield from (r for r in self.raw if r["type"] in params["types"]
                        and lo <= datetime.fromisoformat(r["timestamp"]) < hi)
        elif path.endswith("/events/workouts"):
            yield from self.workouts


def synthetic_beats(conn, uid) -> int:
    """A made-up beat series for most imported HRV readings (example data): alternating RR intervals around 1000 ms, so
    every successive difference is the same and RMSSD = factor × the reading's SDNN, with a factor from 0.6 to 1.05 that
    varies from reading to reading (so the RMSSD scores differ from the SDNN ones, not just by a constant). Readings of
    the nights ending on the 3rd and 13th of a month get no series, so those nights have SDNN but no RMSSD (hrv_missing,
    no recovery: SDNN never fills in). Written through
    rawdata/store.save_heartbeats like an export.xml import."""
    from body_engine.rawdata import store as rx_store
    items = []
    for r in conn.execute("""SELECT start_at, value FROM export_samples WHERE user_id = %s AND kind = 'hrv'
                             ORDER BY start_at""", (uid,)).fetchall():
        if (r["start_at"].astimezone(TZ) + timedelta(hours=12)).day in (3, 13):
            continue
        factor = 0.6 + 0.05 * ((r["start_at"].minute * 7 + r["start_at"].hour) % 10)
        half = factor * r["value"] / 2000
        rr = [1.0 + (half if k % 2 else -half) for k in range(60)]
        beats = [sum(rr[:i]) for i in range(len(rr) + 1)]
        items.append({"start_at": r["start_at"], "end_at": r["start_at"] + timedelta(seconds=beats[-1]),
                      "source": "Example Watch", "origin": "apple_export", "beats_s": beats, "apple_sdnn": r["value"]})
    return rx_store.save_heartbeats(conn, uid, items)


def e2e(conn, tmp_path, hrv_kind: str) -> dict:
    demo = _load("quickstart_demo_data", REPO / "quickstart" / "demo_data.py")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("apple_health_export/export.xml", demo.generate(60, TZ, 7, NOW))
    path = tmp_path / "demo-export.zip"
    path.write_bytes(buf.getvalue())
    uid = conn.execute("""INSERT INTO users (display_name, timezone, birth_date, sex, height_cm, ow_user_id, consent_at)
                          VALUES (%s, 'Australia/Sydney', '1990-01-01', 'male', 180, %s, now())
                          RETURNING id""", (f"golden-{hrv_kind}", f"ow-golden-{hrv_kind}")).fetchone()["id"]
    imported = apple_zip.run(conn, uid, path)
    if hrv_kind == "rmssd":
        assert synthetic_beats(conn, uid) > 50
    today = NOW.date()
    origin = engine.scoring_origin(conn, uid)
    from body_engine.scores import params as P
    inp = engine.load(conn, None, uid, origin - timedelta(days=P.HRMAX_WINDOW_DAYS), today, None, hrv_kind=hrv_kind)
    rows = engine.score_days(inp, origin, today, today)
    engine.write(conn, uid, rows, origin, today)
    for r in rows:   # recorded since #117; checked here, left out of the comparison (see the module docstring)
        assert r["details"].pop("hrv_kind") == hrv_kind == r["hrv_kind"]
    weeks = healthspan_store.compute(conn, uid, today=today)
    stress_rows = stress.compute(conn, ExportOW(conn, uid), uid, today - timedelta(days=29), today)
    return _dump({"import": {k: imported[k] for k in ("records", "days", "nights", "workouts")},
                  "origin": origin, "daily_scores": rows, "healthspan": weeks, "stress": stress_rows})


@pytest.mark.parametrize("hrv_kind", HRV_KINDS)
def test_end_to_end_matches_golden(conn, tmp_path, hrv_kind):
    golden = FIXTURES / f"golden_e2e_{hrv_kind}.json"
    got = e2e(conn, tmp_path, hrv_kind)
    if os.environ.get("BODY_WRITE_GOLDEN") == "1":
        golden.write_text(json.dumps(got, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
        pytest.skip(f"wrote {golden}")
    want = json.loads(golden.read_text())
    assert {d["hrv_kind"] for d in want["daily_scores"]} == {hrv_kind}
    # coverage of the fixture itself: every score kind actually has values
    days = want["daily_scores"]
    assert sum(d["recovery"] is not None for d in days) >= 30 and sum(d["strain"] is not None for d in days) >= 50
    assert sum(d["sleep_score"] is not None for d in days) >= 50
    assert want["healthspan"] and sum(r["score"] is not None for r in want["stress"]) >= 10
    cases = _load("openbodyscore_golden_cases", PKG_TESTS / "golden_cases.py")
    diff = cases.mismatch(got, want)
    assert diff is None, diff
