"""Apple Health read range + full-history backfill (sync/history.py): the app reports, the backfill runs in steps.

Separate test database BODY_TEST_DB_NAME (default body_test_health_history), dropped and recreated each run; skipped
without Postgres. Open Wearables, the importer and the score engine are fakes.
"""
import os
from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.sync import history

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_health_history")
CSRF = {"X-Body-Web": "1"}


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
        def add(name, role="member", consent=True):
            return conn.execute("""INSERT INTO users (display_name, role, ow_user_id, consent_at, consent_version)
                                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                                (name, role, f"ow-{name}", "2026-10-01" if consent else None, "v" if consent else None)
                                ).fetchone()["id"]
        ids = {"a": add("a", "owner"), "b": add("b"), "c": add("c"), "noconsent": add("noconsent", consent=False)}
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


def conn_for(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def test_auth_and_validation(client, users):
    ids, tok = users
    assert client.get("/v1/users/me/health-history").status_code == 401
    assert client.get(f"/v1/users/{ids['b']}/health-history", headers=H(tok["a"])).status_code == 403
    assert client.put(f"/v1/users/{ids['b']}/health-history", headers=H(tok["a"]), json={"scope": "all"}).status_code == 403
    assert client.put("/v1/users/me/health-history", headers=H(tok["a_ro"]), json={"scope": "all"}).status_code == 403
    assert client.put("/v1/users/me/health-history", headers=H(tok["a"]), json={"scope": "365d"}).status_code == 422
    assert client.put("/v1/users/me/health-history", headers=H(tok["a"]), json={"scope": "all", "upload_percent": 120}).status_code == 422
    st = client.get("/v1/users/me/health-history", headers=H(tok["a"])).json()
    assert st["scope"] == "days90" and st["status"] == "idle" and st["active"] is False and st["percent"] is None
    web = TestClient(api.app, base_url="https://testserver")
    assert web.post("/v1/web/login", json={"token": tok["a"]}).status_code == 200
    assert web.put("/v1/users/me/health-history", json={"scope": "days90"}).status_code == 403   # no CSRF header
    assert web.put("/v1/users/me/health-history", headers=CSRF, json={"scope": "days90"}).json()["active"] is False


def test_upload_phase_then_queued(client, users):
    _, tok = users
    put = lambda body: client.put("/v1/users/me/health-history", headers=H(tok["b"]), json=body).json()
    st = put({"scope": "all"})
    assert st["status"] == "uploading" and st["active"] and st["percent"] == 0
    st = put({"scope": "all", "upload_percent": 50, "sent": 120000})
    assert st["percent"] == 35 and st["sent"] == 120000                       # upload = 0–70 % of the bar
    st = put({"scope": "all", "upload_percent": 60})                          # periodic reports are idempotent
    assert st["status"] == "uploading" and st["percent"] == 42
    st = put({"scope": "all", "upload_done": True})
    assert st["status"] == "queued" and st["percent"] == 70
    st = put({"scope": "all", "upload_done": True})                           # the app keeps saying so: nothing restarts
    assert st["status"] == "queued"


def test_backfill_in_budgeted_steps_then_scores(dsn, users, monkeypatch):
    ids, _ = users
    uid = ids["b"]
    today = date.today()
    monkeypatch.setattr(history, "_first_day", lambda conn, u, client: today - timedelta(days=75))
    imported, scored = [], []
    importer = lambda c, cl, u, s, e: imported.append((s, e))
    scorer = lambda c, u, cl: scored.append(u) or {"rows": 1}
    ticks = iter(range(0, 10_000, 100))   # every clock() call moves 100 s: a 150 s budget allows one window per run
    with conn_for(dsn) as conn:
        out = history.run_queued(conn, client=object(), importer=importer, scorer=scorer, budget=150, clock=lambda: next(ticks))
        assert out[0]["status"] == "importing" and out[0]["windows"] == 1
        st = history.status(conn, uid)
        assert st["active"] and 70 < st["percent"] < 95
        for _ in range(5):
            history.run_queued(conn, client=object(), importer=importer, scorer=scorer, budget=150, clock=lambda: next(ticks))
        st = history.status(conn, uid)
        assert st["status"] == "done" and st["percent"] == 100 and st["active"] is False and st["finished_at"]
        # three 30-day windows covering the earliest day to today, oldest first, no gaps or overlaps
        assert imported[0][0] == today - timedelta(days=75) and imported[-1][1] == today
        assert all(imported[i + 1][0] == imported[i][1] + timedelta(days=1) for i in range(len(imported) - 1))
        assert len(imported) == 3 and scored == [uid]
        assert history.run_queued(conn, client=object(), importer=lambda *a: pytest.fail("nothing queued")) == []


def test_error_then_retry_from_cursor(client, dsn, users, monkeypatch):
    ids, tok = users
    uid = ids["c"]
    client.put("/v1/users/me/health-history", headers=H(tok["c"]), json={"scope": "all", "upload_done": False})
    client.put("/v1/users/me/health-history", headers=H(tok["c"]), json={"scope": "all", "upload_done": True})
    monkeypatch.setattr(history, "_first_day", lambda conn, u, client: date.today() - timedelta(days=40))
    calls = []

    def flaky(c, cl, u, s, e):
        calls.append(s)
        if len(calls) == 2:
            raise RuntimeError("OW down")
    with conn_for(dsn) as conn:
        out = history.run_queued(conn, client=object(), importer=flaky, scorer=lambda *a: {})
        assert out[0]["status"] == "error"
        st = history.status(conn, uid)
        assert st["status"] == "error" and "OW down" in st["error"] and st["active"] is False
        cursor = history._row(conn, uid)["import_cursor"]
    # the app keeps reporting "done": the backfill resumes where it stopped
    st = client.put("/v1/users/me/health-history", headers=H(tok["c"]), json={"scope": "all", "upload_done": True}).json()
    assert st["status"] == "importing"
    with conn_for(dsn) as conn:
        history.run_queued(conn, client=object(), importer=lambda c, cl, u, s, e: calls.append(s), scorer=lambda *a: {})
        assert calls[2] == cursor and history.status(conn, uid)["status"] == "done"


def test_stale_upload_and_consent(client, dsn, users, monkeypatch):
    ids, tok = users
    client.put("/v1/users/me/health-history", headers=H(tok["a"]), json={"scope": "all"})
    with conn_for(dsn) as conn:
        conn.execute("UPDATE health_history SET updated_at = now() - interval '3 days' WHERE user_id = %s", (ids["a"],))
        conn.commit()
        assert history.status(conn, ids["a"])["active"] is False     # the app stopped reporting: not shown as running
        conn.execute("INSERT INTO health_history (user_id, scope, status) VALUES (%s, 'all', 'queued')", (ids["noconsent"],))
        conn.commit()
        out = history.run_queued(conn, client=object(), importer=lambda *a: pytest.fail("no consent"))
        assert all(o["user_id"] != ids["noconsent"] for o in out)


def test_switching_back_to_90_days_keeps_the_choice(client, users):
    _, tok = users
    st = client.put("/v1/users/me/health-history", headers=H(tok["c"]), json={"scope": "days90"}).json()
    assert st["scope"] == "days90" and st["status"] == "done"      # what was read stays; nothing restarts
    st = client.put("/v1/users/me/health-history", headers=H(tok["c"]), json={"scope": "all"}).json()
    assert st["status"] == "uploading"                               # choosing all again reads the history again
