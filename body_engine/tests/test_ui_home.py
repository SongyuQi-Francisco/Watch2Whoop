"""The AI-first page (#279, flag ui_ai_first, docs/ui-ai-first-redesign.md v0.4): findings, my Today's order and reasons, the
focus, the first-screen overview, the endpoints (oneself only, 404 with the flag off) and the AI tool set_home.

Separate test database BODY_TEST_DB_NAME (default body_test_ui_home), recreated each run; skipped without Postgres."""
import math
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.ui import findings as F
from body_engine.ui import home as H
from body_engine.ui import params as P

TZ = ZoneInfo("Australia/Sydney")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_ui_home")
TODAY = date(2026, 10, 27)


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


def conn_of(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def scores(conn, uid, days=70, hrv=60.0, hrv_last7=None, sleep=480, need=480, sleep_last7=None, strain=10.0, strain_last=None,
           recovery=60, recovery_last30=None):
    """daily_scores up to yesterday-and-today; the *_last arguments change the most recent days."""
    for i in range(days):
        d = TODAY - timedelta(days=i)
        h = hrv_last7 if hrv_last7 is not None and i < 7 else hrv
        s = sleep_last7 if sleep_last7 is not None and i < 7 else sleep
        st = strain_last[i] if strain_last and i < len(strain_last) else strain
        rc = recovery_last30 if recovery_last30 is not None and i < 30 else recovery
        conn.execute("""INSERT INTO daily_scores (user_id, date, recovery, hrv_night_ln, sleep_actual_min, sleep_need_min, strain, algo_version)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'test')""", (uid, d, rc, math.log(h), s, need, st))


def bedtimes(conn, uid, starts):
    for i, (hh, mm) in enumerate(starts):
        wake = TODAY - timedelta(days=i)
        start = datetime(wake.year, wake.month, wake.day, hh, mm, tzinfo=TZ) - (timedelta(days=1) if hh >= 12 else timedelta())
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, is_main, source)
                        VALUES (%s, %s, %s, %s, 420, true, 'test')""", (uid, wake, start, start + timedelta(hours=7.5)))


@pytest.fixture(scope="module")
def users(dsn):
    with conn_of(dsn) as conn:
        def add(name):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                                   VALUES (%s, 'member', 'Australia/Sydney', now(), '2026-10-22.2') RETURNING id""", (name,)).fetchone()["id"]
        ids = {k: add(k) for k in ("tired", "fine", "fresh", "other")}
        # tired: HRV down 20 % this week, 50 min short of sleep, three hard days, recovery down 8 on the month; bedtimes all over
        scores(conn, ids["tired"], hrv_last7=48.0, sleep_last7=430, strain_last=[8, 16, 15, 17, 9], recovery=62, recovery_last30=54)
        bedtimes(conn, ids["tired"], [(22, 0), (1, 30), (23, 0), (2, 15), (21, 45), (0, 50), (3, 0), (22, 30), (1, 0), (23, 40), (2, 40), (21, 30)])
        # fine: steady, nothing worth saying
        scores(conn, ids["fine"])
        bedtimes(conn, ids["fine"], [(23, m) for m in (0, 5, 10, 0, 15, 5, 0, 10, 20, 0, 5, 10)])
        # fresh: three nights of data only (a new watch)
        scores(conn, ids["fresh"], days=3)
        conn.commit()
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in ids.items()}
        tok["ro"] = auth.create_token(conn, ids["tired"], "ro", ["read"])
        conn.commit()
    return ids, tok


def test_findings_say_what_is_worth_saying(dsn, users):
    ids, _ = users
    with conn_of(dsn) as conn:
        got = F.findings(conn, ids["tired"], TODAY)
        keys = [f["key"] for f in got]
        assert set(keys) == {"hrv_low", "sleep_short", "strain_high", "recovery_down", "bedtime_irregular"}, keys
        assert keys[0] == "hrv_low"                                   # 20 % under the baseline weighs most
        hrv = got[0]
        assert hrv["module"] == "health_monitor" and hrv["value"] == "−20%" and hrv["tone"] == "mid"
        sleep = next(f for f in got if f["key"] == "sleep_short")
        assert sleep["value"] == "7:10" and "0:50" in sleep["label"]
        strain = next(f for f in got if f["key"] == "strain_high")
        assert strain["value"] == "3"                                 # yesterday back to three days ago; today (8) is still going
        assert F.findings(conn, ids["fine"], TODAY) == []
        assert F.findings(conn, ids["fresh"], TODAY) == []           # too little history: no baseline, no finding
        assert F.stats(conn, ids["tired"])["days"] == 70


def test_home_order_reasons_and_choices(dsn, users):
    ids, _ = users
    with conn_of(dsn) as conn:
        uid = ids["tired"]
        h = H.home(conn, uid, TODAY)
        assert h["overview"]["findings"][0]["key"] == "hrv_low" and len(h["overview"]["findings"]) == P.OVERVIEW_FINDINGS
        assert [m["key"] for m in h["modules"]][:3] == ["health_monitor", "sleep_goal", "strain_coach"]
        assert {m["reason"] for m in h["modules"][:3]} == {"finding"}
        assert len(h["modules"]) == len(P.MODULES) and h["modules"][-1]["reason"] is None
        # focus comes before findings; an AI that logged food puts the training / food card next
        H.set_focus(conn, uid, ["sleep", "sleep", "nope"])
        conn.execute("INSERT INTO assistant_audit (user_id, channel, tool, ok) VALUES (%s, 'mcp:claude', 'log_food', true)", (uid,))
        conn.execute("INSERT INTO assistant_audit (user_id, channel, tool, ok) VALUES (%s, 'mcp:claude', 'get_journal', false)", (uid,))
        h = H.home(conn, uid, TODAY)
        assert h["focus"] == ["sleep"]
        order = [(m["key"], m["reason"]) for m in h["modules"]]
        assert order[:2] == [("sleep_goal", "focus"), ("sleep", "focus")]
        assert ("training", "used") in order and ("journal", None) in order   # a failed call does not count
        # pinned first in pin order; hidden never comes back by itself; auto hands it back to the rules
        H.set_module(conn, uid, "journal", "pinned")
        H.set_module(conn, uid, "muscle_load", "pinned")
        H.set_module(conn, uid, "health_monitor", "hidden")
        h = H.home(conn, uid, TODAY)
        assert [(m["key"], m["reason"]) for m in h["modules"][:2]] == [("journal", "pinned"), ("muscle_load", "pinned")]
        assert "health_monitor" not in [m["key"] for m in h["modules"]] and h["hidden"] == ["health_monitor"]
        H.set_module(conn, uid, "health_monitor", "auto")
        assert H.home(conn, uid, TODAY)["hidden"] == []
        with pytest.raises(ValueError):
            H.set_module(conn, uid, "weather", "pinned")
        H.overview_seen(conn, uid)
        assert H.home(conn, uid, TODAY)["overview"] is None
        conn.rollback()


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def Hd(t):
    return {"Authorization": f"Bearer {t}"}


def test_endpoints(dsn, users, client):
    ids, tok = users
    with conn_of(dsn) as conn:
        flags.set_flag(conn, P.FEATURE, "off", actor="test")
        conn.commit()
    flags.invalidate()
    assert client.get("/v1/users/me/ui/home", headers=Hd(tok["fine"])).status_code == 404   # flag off: as if it did not exist
    with conn_of(dsn) as conn:
        flags.set_flag(conn, P.FEATURE, "all", actor="test")
        conn.commit()
    flags.invalidate()
    r = client.get("/v1/users/me/ui/home", headers=Hd(tok["fine"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overview"] == {"findings": [], "stats": {"days": 70, "since": "2026-08-19", "nights": 12, "workouts": 0}}
    assert body["focus_options"] == ["fat", "muscle", "sleep", "recover", "markers"] and body["max_modules"] == 6
    r = client.put("/v1/users/me/ui/focus", json={"focus": ["muscle"]}, headers=Hd(tok["fine"]))
    assert r.status_code == 200 and r.json()["focus"] == ["muscle"]
    assert [m["key"] for m in r.json()["modules"][:2]] == ["training", "muscle_load"]
    assert client.put("/v1/users/me/ui/focus", json={"focus": ["weather"]}, headers=Hd(tok["fine"])).status_code == 422
    r = client.put("/v1/users/me/ui/modules/sleep", json={"state": "pinned"}, headers=Hd(tok["fine"]))
    assert r.status_code == 200 and r.json()["modules"][0] == {"key": "sleep", "reason": "pinned"}
    assert client.put("/v1/users/me/ui/modules/weather", json={"state": "pinned"}, headers=Hd(tok["fine"])).status_code == 422
    assert client.post("/v1/users/me/ui/overview-seen", headers=Hd(tok["fine"])).json() == {"ok": True}
    assert client.get("/v1/users/me/ui/home", headers=Hd(tok["fine"])).json()["overview"] is None
    # oneself only; a read-only token cannot write
    assert client.get(f"/v1/users/{ids['tired']}/ui/home", headers=Hd(tok["fine"])).status_code == 403
    assert client.put("/v1/users/me/ui/focus", json={"focus": []}, headers=Hd(tok["ro"])).status_code == 403
    # English page: finding labels in English
    r = client.get("/v1/users/me/ui/home", headers={**Hd(tok["tired"]), "X-Body-Lang": "en"})
    assert r.json()["overview"]["findings"][0]["label"] == "HRV is lower than usual this week"


def test_ai_tool(dsn, users):
    ids, _ = users
    with conn_of(dsn) as conn:
        flags.set_flag(conn, P.FEATURE, "all", actor="test")
        conn.commit()
        flags.invalidate()
        out = run_tool(conn, ids["other"], "mcp", "set_home", {"module": "sleep", "state": None, "focus": ["sleep"]})
        assert out["focus"] == ["sleep"] and out["today"][0] == "sleep" and out["hidden"] == []
        out = run_tool(conn, ids["other"], "mcp", "set_home", {"module": "journal", "state": "hidden", "focus": None})
        assert out["hidden"] == ["journal"] and out["focus"] == ["sleep"]
        conn.commit()
        flags.set_flag(conn, P.FEATURE, "off", actor="test")
        conn.commit()
        flags.invalidate()
        with pytest.raises(Exception):
            run_tool(conn, ids["other"], "mcp", "set_home", {"module": "sleep", "state": "pinned", "focus": None})
        conn.rollback()
