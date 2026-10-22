"""Daily water intake (issue #125): store, goal, API, AI tool + rollback, briefing, Apple Health uploads, reminder rule,
rollout hook.

Separate test database BODY_TEST_DB_NAME (default body_test_water), recreated each run; skipped without Postgres.
"""
import os
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rawdata import ingest as I
from body_engine.rules import hydration
from body_engine.rules.context import Ctx
from body_engine.rollout import flags
from body_engine.water import params as P
from body_engine.water import store

TZ = ZoneInfo("Australia/Sydney")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_water")


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
def users(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        def add(name, sex=None, role="member"):
            return conn.execute("""INSERT INTO users (display_name, role, sex, timezone, consent_at, consent_version)
                                   VALUES (%s, %s, %s, 'Australia/Sydney', now(), %s) RETURNING id""",
                                (name, role, sex, I.RAW_CONSENT_VERSION)).fetchone()["id"]
        ids = {"a": add("a", "male", "owner"), "b": add("b", "female"), "c": add("c")}
        flags.set_flag(conn, P.FEATURE, "all", actor="test")   # the water flag (default off) is on for these tests
        conn.commit()
        flags.invalidate()
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in ids.items()}
        tok["a_ro"] = auth.create_token(conn, ids["a"], "ro", ["read"])
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(t):
    return {"Authorization": f"Bearer {t}"}


def conn_of(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def test_goal_default_profile_and_extras(dsn, users):
    ids, _ = users
    day = date(2026, 10, 20)
    with conn_of(dsn) as conn:
        assert store.goal(conn, ids["a"], day)["ml"] == P.DEFAULT_GOAL_ML["male"]
        assert store.goal(conn, ids["b"], day)["ml"] == P.DEFAULT_GOAL_ML["female"]
        assert store.goal(conn, ids["c"], day) == {"ml": P.DEFAULT_GOAL_UNKNOWN_ML, "base_ml": P.DEFAULT_GOAL_UNKNOWN_ML,
                                                   "source": "default", "extras": []}
        conn.execute("""INSERT INTO assistant_profile (user_id, key, label, value, sort, source, data)
                        VALUES (%s, 'diet_targets', 'x', 'x', 61, 'assistant', %s)""", (ids["c"], Jsonb({"water_ml": 3000})))
        conn.execute("""INSERT INTO workouts (user_id, source, external_id, local_date, start_at, end_at, duration_min, type, name_zh)
                        VALUES (%s, 'shortcut', 'w1', %s, %s, %s, 45, 'running', '跑步')""",
                     (ids["c"], day, datetime(2026, 10, 20, 7, tzinfo=TZ), datetime(2026, 10, 20, 7, 45, tzinfo=TZ)))
        conn.execute("""INSERT INTO journal_items (user_id, key, label_zh, kind, category, sort)
                        VALUES (%s, 'sauna', '蒸桑拿', 'bool', 'lifestyle', 1) ON CONFLICT DO NOTHING""", (ids["c"],))
        conn.execute("INSERT INTO journal_entries (user_id, date, key, value) VALUES (%s, %s, 'sauna', 1)", (ids["c"], day))
        g = store.goal(conn, ids["c"], day)
        assert g["source"] == "profile" and g["base_ml"] == 3000
        assert g["ml"] == 3000 + P.TRAINING_EXTRA_ML + P.SAUNA_EXTRA_ML
        assert {e["kind"] for e in g["extras"]} == {"training", "sauna"}


def test_api_add_idempotent_undo_and_auth(client, users):
    ids, tok = users
    r = client.post("/v1/users/me/water", json={"ml": 250, "client_id": "tap-1"}, headers=H(tok["a"]))
    assert r.status_code == 200, r.text
    assert r.json()["day"]["total_ml"] == 250 and r.json()["entry"]["source"] == "button"
    again = client.post("/v1/users/me/water", json={"ml": 250, "client_id": "tap-1"}, headers=H(tok["a"])).json()
    assert again["entry"]["duplicate"] is True and again["day"]["total_ml"] == 250
    two = client.post("/v1/users/me/water", json={"ml": 500, "client_id": "tap-2"}, headers=H(tok["a"])).json()
    assert two["day"]["total_ml"] == 750 and two["day"]["undo"]["ml"] == 500
    d = client.get("/v1/users/me/water", headers=H(tok["a"])).json()
    assert d["goal"]["ml"] == P.DEFAULT_GOAL_ML["male"] and d["percent"] == round(75000 / P.DEFAULT_GOAL_ML["male"])
    assert d["quick_ml"] == list(P.QUICK_ML) and len(d["entries"]) == 2
    u = client.delete(f"/v1/users/me/water/{d['undo']['id']}", headers=H(tok["a"])).json()
    assert u["day"]["total_ml"] == 250 and u["day"]["undo"]["ml"] == 250
    # read-only token cannot write; nobody writes or reads someone else's without a share
    assert client.post("/v1/users/me/water", json={"ml": 250}, headers=H(tok["a_ro"])).status_code == 403
    assert client.post(f"/v1/users/{ids['b']}/water", json={"ml": 250}, headers=H(tok["a"])).status_code == 403
    assert client.get(f"/v1/users/{ids['b']}/water", headers=H(tok["a"])).status_code == 403
    assert client.delete(f"/v1/users/me/water/{d['undo']['id']}", headers=H(tok["b"])).status_code == 404
    # validation: 0, too much, future time
    assert client.post("/v1/users/me/water", json={"ml": 0}, headers=H(tok["a"])).status_code == 422
    assert client.post("/v1/users/me/water", json={"ml": P.MAX_ML + 1}, headers=H(tok["a"])).status_code == 422
    future = (datetime.now(TZ) + timedelta(hours=3)).isoformat(timespec="minutes")
    assert client.post("/v1/users/me/water", json={"ml": 250, "at": future}, headers=H(tok["a"])).status_code == 422


def test_ai_tool_rollback_and_briefing(dsn, users):
    ids, _ = users
    with conn_of(dsn) as conn:
        r = run_tool(conn, ids["b"], "mcp", "log_water", {"ml": 400, "at": None})
        assert r["logged"] == 400 and r["today_ml"] == 400 and r["goal_ml"] == P.DEFAULT_GOAL_ML["female"]
        b = run_tool(conn, ids["b"], "mcp", "get_briefing", {"user": "me"})
        assert b["water"] == {"ml": 400, "goal_ml": P.DEFAULT_GOAL_ML["female"]}
        log = run_tool(conn, ids["b"], "mcp", "get_write_log", {"days": None})
        row = next(x for x in log if x["tool"] == "log_water")
        assert row["kind"] == "water" and row["rollback"] == "ok"
        run_tool(conn, ids["b"], "mcp", "rollback_write", {"log_id": row["id"]})
        conn.commit()
        assert store.day_view(conn, ids["b"])["total_ml"] == 0
        with pytest.raises(Exception):
            run_tool(conn, ids["b"], "mcp", "log_water", {"ml": -1, "at": None})


def test_apple_health_upload_dedup_and_delete(client, users, dsn):
    ids, tok = users
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)   # today, not "an hour ago" (that is yesterday just after midnight)
    batch = {"batch_id": str(uuid.uuid4()), "water": [
        {"uuid": s1, "start": start.isoformat(), "ml": 330}, {"uuid": s2, "start": start.isoformat(), "ml": 200.4}]}
    r = client.post("/v1/ingest/healthkit", json=batch, headers=H(tok["c"])).json()
    assert r["water"] == 2 and r["skipped"] == 0 and r["water_enabled"] is True
    st = client.get("/v1/ingest/healthkit", headers=H(tok["c"])).json()
    assert st["water"] == 2 and st["water_enabled"] is True          # what the app checks before it reads water
    r2 = client.post("/v1/ingest/healthkit", json={**batch, "batch_id": str(uuid.uuid4())}, headers=H(tok["c"])).json()
    assert r2["water"] == 0 and r2["skipped"] == 2
    d = client.get("/v1/users/me/water", headers=H(tok["c"])).json()
    assert d["total_ml"] == 530 and {e["source"] for e in d["entries"]} == {"apple_health"}
    assert d["undo"] is None                            # Health samples are removed in Health, not undone here
    r3 = client.post("/v1/ingest/healthkit", json={"batch_id": str(uuid.uuid4()), "deleted": [s1]}, headers=H(tok["c"])).json()
    assert r3["deleted"] == 1
    assert client.get("/v1/users/me/water", headers=H(tok["c"])).json()["total_ml"] == 200


def ctx(day, hour, total, goal=2000):
    now = datetime.combine(day, datetime.min.time(), TZ).replace(hour=hour)
    return Ctx(user_id=1, day=day, tz=TZ, water={day: total}, water_goal=goal, now=now)


def test_reminder_rule():
    day = date(2026, 10, 22)
    assert hydration.evaluate(ctx(day, P.REMIND_FROM_HOUR - 1, 0)).status == "not_triggered"
    r = hydration.evaluate(ctx(day, P.REMIND_FROM_HOUR, 900))
    assert r.status == "triggered" and r.reminders[0].level == "info" and "900" in r.reminders[0].message
    assert r.reminders[0].title_en and r.reminders[0].message_en
    assert hydration.evaluate(ctx(day, 18, 1000)).status == "not_triggered"            # half reached
    yesterday = Ctx(user_id=1, day=day - timedelta(days=1), tz=TZ, water={}, water_goal=2000,
                    now=datetime(2026, 10, 22, 18, tzinfo=TZ))
    assert hydration.evaluate(yesterday).status == "not_triggered"                       # only today
    assert hydration.evaluate(Ctx(user_id=1, day=day, tz=TZ)).status == "insufficient_data"   # feature off


def test_flag_off_and_beta_cohort(client, users, dsn):
    """The water flag (rollout/registry.py) off: endpoints 404, no tool, no briefing section, no rule input, uploads skip
    water; cohort state: only the beta group gets it."""
    ids, tok = users
    with conn_of(dsn) as conn:
        flags.set_flag(conn, P.FEATURE, "off", actor="test")
        conn.commit()
    flags.invalidate()
    try:
        assert client.get("/v1/users/me/water", headers=H(tok["a"])).status_code == 404
        assert client.post("/v1/users/me/water", json={"ml": 250}, headers=H(tok["a"])).status_code == 404
        up = client.post("/v1/ingest/healthkit", json={"batch_id": str(uuid.uuid4()), "water": [
            {"uuid": str(uuid.uuid4()), "start": datetime.now(TZ).isoformat(), "ml": 300}]}, headers=H(tok["b"])).json()
        assert up["water"] == 0 and up["water_enabled"] is False   # the app keeps its water anchor (nothing lost)
        st = client.get("/v1/ingest/healthkit", headers=H(tok["b"])).json()
        assert st["water_enabled"] is False                       # ... and does not read water while the flag is off
        with conn_of(dsn) as conn:
            with pytest.raises(Exception):
                run_tool(conn, ids["a"], "mcp", "log_water", {"ml": 250, "at": None})
            assert run_tool(conn, ids["a"], "mcp", "get_briefing", {"user": "me"}).get("water") is None
            from body_engine.rules.context import load
            assert load(conn, ids["a"], client=None).water is None
            flags.set_flag(conn, P.FEATURE, "cohort", actor="test")
            flags.cohort_add(conn, ids["b"], actor="test")
            conn.commit()
        flags.invalidate()
        assert client.get("/v1/users/me/water", headers=H(tok["a"])).status_code == 404
        assert client.get("/v1/users/me/water", headers=H(tok["b"])).status_code == 200
        assert client.get("/v1/ingest/healthkit", headers=H(tok["b"])).json()["water_enabled"] is True
        assert client.get("/v1/ingest/healthkit", headers=H(tok["a"])).json()["water_enabled"] is False
    finally:
        with conn_of(dsn) as conn:
            flags.set_flag(conn, P.FEATURE, "all", actor="test")
            conn.commit()
        flags.invalidate()
