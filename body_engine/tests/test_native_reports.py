"""2.0.0 package M5 through the database (docs/ios-native-2.0.md section 4): the split of insights/reports.py into `load` +
pure `compute` and of records/analysis.py into the query + pure `analyze_rows` leaves the server's answers unchanged, and
the phone's inputs (analysis.load_data = what the phone holds) give the server's answers through the pure functions.

The synthetic person of packages/openbodyscore-js/test/cases_reports.py is written into the tables (daily_scores,
sleep_sessions, stress_daily, workouts, journal, daily_metrics) with a fake own-store reader for the training log, weigh-ins
and food. `reports_v0.py` is the report before the split. Database BODY_TEST_DB_NAME_NATIVE_REPORTS (default
body_test_native_reports), recreated each run; skipped without Postgres."""
import importlib.util
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from body_engine import sparky_client
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.insights import reports
from body_engine.records import analysis

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reports_v0  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
JS_TESTS = REPO / "packages" / "openbodyscore-js" / "test"
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_NATIVE_REPORTS", "body_test_native_reports")


def _cases():
    spec = importlib.util.spec_from_file_location("openbodyscore_js_cases_reports", JS_TESTS / "cases_reports.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _cases()


class OwnReader:
    """The own-store reader shape (sparky_client._own): training log, weigh-ins, food entries, goals."""
    own = True

    def __init__(self, data: dict, fail: bool = False):
        self.d, self.fail = data, fail

    def _check(self):
        if self.fail:
            raise sparky_client.SparkyError("401 未授权")

    def training_log(self, start, end):
        self._check()
        return [s for s in self.d["training"]["log"] if start.isoformat() <= s["date"][:10] <= end.isoformat()]

    def weight(self, start, end):
        self._check()
        return [w for w in self.d["weights"]["rows"] if start.isoformat() <= w["date"] <= end.isoformat()]

    def food_entries(self, start, end):
        self._check()
        out = []
        for i, n in enumerate(self.d["nutrition"]["days"]):
            if n["logged"] and start.isoformat() <= n["date"] <= end.isoformat():
                out.append({"id": i + 1, "source": "test", "date": n["date"], "time": "12:00", "meal_type": "lunch",
                            "food": "饭", "quantity": 1, "unit": "份", "calories": n["calories"], "protein_g": n["protein_g"],
                            "carbs_g": 100.0, "fat_g": 30.0, "saturated_fat_g": 5.0, "fiber_g": None,
                            "sugar_g": n["sugar_g"], "sodium_mg": None, "notes": None})
        return out

    def goal_for_date(self, day):
        self._check()
        return {"date": day.isoformat(), "name": "默认", "calories": 2200, "protein_g": 140, "carbs_g": 250, "fat_g": 70}


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture(scope="module")
def world(dsn):
    data = C.report_data()
    j = data["journal"]
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        uid = conn.execute("INSERT INTO users (display_name, timezone) VALUES ('example', %s) RETURNING id",
                           (C.TZ_NAME,)).fetchone()["id"]
        viewer = conn.execute("INSERT INTO users (display_name) VALUES ('viewer') RETURNING id").fetchone()["id"]
        jscores = j["scores"]
        rows = {r["date"]: r for r in data["scores"]}
        for d in sorted(set(rows) | set(jscores)):
            r, js = rows.get(d), jscores.get(d) or {}
            flags = {"rhr_from_daily": "test"} if r and r["rhr_daily"] else {}
            details = {"sleep": r["s"]} if r and r["s"] else {}
            cols = {k: (r or {}).get(k) for k in ("recovery", "sleep_score", "sleep_actual_min", "sleep_performance",
                                                   "sleep_debt_min", "strain", "trimp", "muscle_load", "ctl", "atl", "tsb",
                                                   "rhr_night", "hrv_night_ln")}
            if not r:   # journal-only days (before the report's 70 days): the analysis' three columns
                cols.update(recovery=js.get("recovery"), sleep_actual_min=js.get("sleep_actual_min"), strain=js.get("strain"))
            conn.execute(f"""INSERT INTO daily_scores (user_id, date, {", ".join(cols)}, details, flags, algo_version)
                             VALUES (%s, %s, {", ".join(["%s"] * len(cols))}, %s, %s, 'test')""",
                         (uid, d, *cols.values(), Jsonb(details), Jsonb(flags)))
        for d, m in j["metrics"].items():
            for metric, v in m.items():
                conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                                VALUES (%s, %s, %s, %s, '', 'avg', 'ow')""", (uid, d, metric, v))
        for s in data["sleep_sessions"]:
            conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, awake_min, deep_min,
                                                        rem_min, core_min, is_main, device, source)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                         (uid, s["wake_date"], s["start_at"], s["end_at"], s["asleep_min"], s["awake_min"], s["deep_min"],
                          s["rem_min"], s["core_min"], s["is_main"], s["device"],
                          "shortcut" if s["device"] == "iPhone" else "ow"))
        for s in data["stress"]:
            conn.execute("""INSERT INTO stress_daily (user_id, date, score, high_h, algo_version)
                            VALUES (%s, %s, %s, %s, 'test')""", (uid, s["date"], s["score"], s["high_h"]))
        for i, w in enumerate(data["training"]["workouts"]):
            start = C.at(w["local_date"], 18.0)
            conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at,
                                                  duration_min) VALUES (%s, 'ow', %s, 'running', %s, %s, %s, %s, %s)""",
                         (uid, f"w{i}", w["name_zh"], w["local_date"], start, start + timedelta(minutes=w["duration_min"]),
                          w["duration_min"]))
        for k, it in enumerate(j["items"]):
            conn.execute("INSERT INTO journal_items (user_id, key, label_zh, kind, unit, sort) VALUES (%s, %s, %s, %s, %s, %s)",
                         (uid, it["key"], it["label_zh"], it["kind"], it["unit"], k))
        for e in j["entries"]:
            conn.execute("INSERT INTO journal_entries (user_id, date, key, value) VALUES (%s, %s, %s, %s)",
                         (uid, e["date"], e["key"], e["value"]))
    return uid, viewer, data


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


ENDS = (C.LAST, C.LAST - timedelta(days=16), C.LAST - timedelta(days=45), C.LAST - timedelta(days=200))


def test_report_unchanged_by_the_split(dsn, world):
    uid, viewer, data = world
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        for period in ("week", "month"):
            for end in ENDS:
                for fail in (False, True):
                    fake = OwnReader(data, fail)
                    new, old = reports.report(conn, uid, period, end, sparky=fake), reports_v0.report(conn, uid, period, end, sparky=fake)
                    assert _same(new, old), (period, end, fail)
                    assert _same(new, reports.compute(reports.load(conn, uid, period, end, sparky=fake), period, end))
        rep = reports.report(conn, uid, "month", C.LAST, sparky=OwnReader(data))
        assert rep["current"]["training"]["sessions"] and rep["current"]["weight"]["weigh_ins"]
        assert rep["current"]["nutrition"]["days_logged"] and rep["journal_findings"]
        assert rep["current"]["stress"]["days"] and rep["current"]["sleep"]["consistency_pct"] is not None
        # a viewer with some scopes: the same blocks are left out before and after
        for scopes in (["scores"], ["scores", "sleep", "nutrition_summary"], ["scores", "training", "weight", "journal"]):
            conn.execute("""INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)
                            ON CONFLICT (owner_id, viewer_id) DO UPDATE SET scopes = EXCLUDED.scopes""", (uid, viewer, scopes))
            fake = OwnReader(data)
            new = reports.report(conn, uid, "week", C.LAST, sparky=fake, viewer_id=viewer)
            assert _same(new, reports_v0.report(conn, uid, "week", C.LAST, sparky=fake, viewer_id=viewer)), scopes
            assert new["hidden"]


def test_journal_analysis_from_the_phones_inputs(dsn, world):
    """analyze_all / analyze on the tables == analyze_data on load_data (the rows the phone holds), both versions."""
    uid, _, _ = world
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        for end, days in ((C.LAST, 90), (C.LAST, 150), (C.LAST - timedelta(days=30), 60)):
            local = analysis.load_data(conn, uid, end, days)
            for regression in (False, True):
                server = analysis.analyze_all(conn, uid, end, days, regression)
                assert server and _same(server, analysis.analyze_data(local, end, days, regression)), (end, days, regression)
            one = analysis.analyze(conn, uid, "coffee_cups", end, days, 1.0, True)
            assert _same(one, analysis.analyze_data(local, end, days, True, "coffee_cups", 1.0))
        with pytest.raises(ValueError):
            analysis.analyze_data(local, end, days, key="nope")


def test_report_with_the_phones_journal_inputs(dsn, world):
    """The phone passes the journal's raw rows ("journal") instead of the server's results: same findings."""
    uid, _, data = world
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        for period in ("week", "month"):
            loaded = reports.load(conn, uid, period, C.LAST, sparky=OwnReader(data))
            phone = {k: v for k, v in loaded.items() if k != "journal_results"}
            phone["journal"] = analysis.load_data(conn, uid, C.LAST, 90)
            assert _same(reports.compute(phone, period, C.LAST), reports.compute(loaded, period, C.LAST))
