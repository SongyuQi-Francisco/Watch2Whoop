"""On-demand refresh (app opened / pull to refresh): own data only, rate limit, background import, queued score recompute.

Uses a separate test database BODY_TEST_DB_NAME (default body_test_sync_refresh), dropped and recreated on every run; skipped
if it cannot connect. Open Wearables and the score engine are replaced by fakes (no network, no real scoring).
"""
import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.sync import refresh

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_sync_refresh")
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
        def add(name, role="member", ow=None, consent=True):
            return conn.execute("""INSERT INTO users (display_name, role, ow_user_id, consent_at, consent_version)
                                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                                (name, role, ow, datetime.now(timezone.utc) if consent else None, "v" if consent else None)
                                ).fetchone()["id"]
        ids = {"a": add("a", "owner", "ow-a"), "b": add("b", ow="ow-b"), "nolink": add("nolink"),
               "noconsent": add("noconsent", ow="ow-c", consent=False)}
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in ids.items()}
        tok["a_ro"] = auth.create_token(conn, ids["a"], "ro", ["read"])
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


@pytest.fixture()
def started(monkeypatch):
    """Record background starts instead of running real threads."""
    calls = []
    monkeypatch.setattr(refresh, "_start_thread", calls.append)
    return calls


def H(t):
    return {"Authorization": f"Bearer {t}"}


def test_auth_own_data_only(client, users, started):
    ids, tok = users
    assert client.post("/v1/users/me/sync/refresh").status_code == 401
    assert client.post(f"/v1/users/{ids['a']}/sync/refresh", headers=H(tok["b"])).status_code == 403
    assert client.get(f"/v1/users/{ids['a']}/sync/status", headers=H(tok["b"])).status_code == 403
    st = client.get("/v1/users/me/sync/status", headers=H(tok["b"])).json()
    assert st["status"] == "idle" and st["linked"] is True and st["busy"] is False and st["last_import_at"] is None
    assert started == []
    # Today page cookie: the CSRF header is required for the POST
    web = TestClient(api.app, base_url="https://testserver")
    assert web.post("/v1/web/login", json={"token": tok["b"]}).status_code == 200
    assert web.post("/v1/users/me/sync/refresh").status_code == 403
    r = web.post("/v1/users/me/sync/refresh", headers=CSRF)
    assert r.status_code == 202 and r.json()["accepted"] is True and started == [ids["b"]]


def test_rate_limit_and_collapse(client, users, started, dsn):
    ids, tok = users
    r = client.post("/v1/users/me/sync/refresh", headers=H(tok["a"]))
    assert r.status_code == 202 and r.json()["accepted"] and r.json()["status"] == "importing" and r.json()["busy"]
    r2 = client.post("/v1/users/me/sync/refresh", headers=H(tok["a_ro"]))   # own data: a read-only login may refresh too
    body = r2.json()
    assert r2.status_code == 202 and body["accepted"] is False and body["throttled"] is True
    assert 1 <= body["retry_after"] <= 121 and r2.headers["Retry-After"] == str(body["retry_after"])
    assert started == [ids["a"]], "one background run per accepted request"
    # After the window: a still-running import is not started twice; a finished one may run again
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        conn.execute("UPDATE sync_refresh SET requested_at = now() - interval '3 minutes' WHERE user_id = %s", (ids["a"],))
    assert client.post("/v1/users/me/sync/refresh", headers=H(tok["a"])).json()["reason"] == "busy"
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        conn.execute("UPDATE sync_refresh SET status = 'idle' WHERE user_id = %s", (ids["a"],))
    assert client.post("/v1/users/me/sync/refresh", headers=H(tok["a"])).json()["accepted"] is True
    assert started == [ids["a"], ids["a"]]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM sync_refresh WHERE user_id = %s", (ids["a"],)).fetchone()["n"] == 1


def test_not_linked_or_not_consented_is_not_processed(client, users, started):
    _, tok = users
    r = client.post("/v1/users/me/sync/refresh", headers=H(tok["nolink"])).json()
    assert r["accepted"] is False and r["reason"] == "not_linked"
    r = client.post("/v1/users/me/sync/refresh", headers=H(tok["noconsent"])).json()
    assert r["accepted"] is False and r["reason"] == "consent"
    assert started == []


def test_background_import_then_queued_scores(dsn, users, monkeypatch):
    """The thread runs the import-ow step (importers/ow.run) and queues the recompute; sync-refresh-run does the recompute."""
    ids, _ = users
    uid = ids["b"]
    imported = []
    from body_engine.importers import ow
    monkeypatch.setattr(ow, "run", lambda conn, client, user_id, *a, **k: imported.append(user_id) or {"samples": 0})
    monkeypatch.setattr(refresh, "SCORES_INLINE", False)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO sync_refresh (user_id, status) VALUES (%s, 'importing') "
                     "ON CONFLICT (user_id) DO UPDATE SET status = 'importing'", (uid,))
        conn.commit()
    refresh._run_import(uid, client=object(), connect=lambda: psycopg.connect(dsn, row_factory=dict_row))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        st = refresh.status(conn, uid)
        assert imported == [uid] and st["status"] == "scores_queued" and st["imported_at"] and st["busy"]
        scored = []
        out = refresh.run_queued(conn, scorer=lambda c, u, cl: scored.append(u) or {"rows": 3})
        assert scored == [uid] and out == [{"user_id": uid, "rows": 3}]
        st = refresh.status(conn, uid)
        assert st["status"] == "idle" and st["scored_at"] and not st["busy"]
        assert refresh.run_queued(conn, scorer=lambda *a: pytest.fail("nothing queued")) == []   # idempotent
        # A queued recompute that no worker picked up stops counting as busy after STALE (the page shows the last time)
        conn.execute("UPDATE sync_refresh SET status = 'scores_queued', updated_at = now() - interval '16 minutes' "
                     "WHERE user_id = %s", (uid,))
        assert refresh.status(conn, uid)["busy"] is False
        conn.commit()


def test_import_failure_is_recorded(dsn, users, monkeypatch):
    ids, _ = users
    uid = ids["b"]
    from body_engine.importers import ow

    def boom(*a, **k):
        raise RuntimeError("OW down")
    monkeypatch.setattr(ow, "run", boom)
    refresh._run_import(uid, client=object(), connect=lambda: psycopg.connect(dsn, row_factory=dict_row))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        st = refresh.status(conn, uid)
        assert st["status"] == "error" and "OW down" in st["error"] and not st["busy"]


def test_last_import_counts_the_hourly_job(dsn, users):
    ids, _ = users
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO sync_state (user_id, source, last_synced_at) VALUES (%s, 'ow', now() - interval '20 minutes') "
                     "ON CONFLICT (user_id, source) DO UPDATE SET last_synced_at = EXCLUDED.last_synced_at", (ids["nolink"],))
        t = refresh.status(conn, ids["nolink"])["last_import_at"]
        assert t is not None and datetime.now(timezone.utc) - t > timedelta(minutes=19)


def test_today_page_wiring(client):
    """sync.js: loaded and cached; the status line on Today; pull to refresh and 「数据更新」 ask the server too."""
    html = client.get("/app/").text
    assert html.index('src="sync.js"') < html.index('src="app.js"')
    js = client.get("/app/sync.js").text
    assert "/v1/users/me/sync/refresh" in js and "/v1/users/me/sync/status" in js and "window.BodySync" in js
    app_js = client.get("/app/app.js").text
    assert 'window.BodySync?.refresh("pull")' in app_js and 'window.BodySync?.refresh("me")' in app_js
    assert "window.BodySync?.header(" in app_js and "reload: refresh" in app_js
    assert '"sync.js"' in client.get("/app/sw.js").text
