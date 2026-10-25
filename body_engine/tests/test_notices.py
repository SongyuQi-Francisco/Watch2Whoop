"""Admin notices (notices/, migration 775): send (texts and link checked), shown once (pending → seen), withdrawn, expired;
the endpoints (own only; admin endpoints only for BODY_ADMIN_USER_IDS with write scope); the CLI; the page script.

Separate test database BODY_TEST_DB_NAME (default body_test_notices), recreated each run; skipped without Postgres.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.notices import store

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_notices")
STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"


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
        def add(name):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                                   VALUES (%s, 'member', 'Australia/Sydney', now(), '1') RETURNING id""", (name,)).fetchone()["id"]
        ids = {n: add(n) for n in ("admin", "a", "b")}
        t = {n: auth.create_token(conn, i, "app", ["read", "write"]) for n, i in ids.items()}
        t["admin_ro"] = auth.create_token(conn, ids["admin"], "ro", ["read"])
        conn.commit()
    return ids, t


@pytest.fixture
def conn(dsn):
    c = psycopg.connect(dsn, row_factory=dict_row)
    yield c
    c.close()


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None


def H(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.mark.parametrize("link", ["https://example.org/x?a=1", "/app/#tab=me", "watch2whoop://sync", "watch2whoop://sync-center", None, ""])
def test_good_links(link):
    assert store.check_link(link) == (link or None)


@pytest.mark.parametrize("link", ["javascript:alert(1)", "http://example.org", "data:text/html,x", "//evil.org", "watch2whoop://x y",
                                  "https://a.org/<script>", "/other", "https://" + "a" * 600])
def test_bad_links(link):
    with pytest.raises(ValueError):
        store.check_link(link)


def test_send_shown_once(conn, users):
    ids, _ = users
    with pytest.raises(ValueError):
        store.send(conn, ids["a"], "  ")                           # no title
    with pytest.raises(ValueError):
        store.send(conn, ids["a"], "t", link_label="Go")           # a label without a link
    with pytest.raises(ValueError):
        store.send(conn, 999999, "t")                              # unknown user
    conn.rollback()
    n1 = store.send(conn, ids["a"], "第一条", "正文", "watch2whoop://sync", "去同步")
    n2 = store.send(conn, ids["a"], "第二条")
    store.send(conn, ids["b"], "给 b 的")
    conn.commit()
    assert [n["id"] for n in store.pending(conn, ids["a"])] == [n1["id"], n2["id"]]   # oldest first
    assert not store.mark_seen(conn, ids["b"], n1["id"])          # someone else's
    assert store.mark_seen(conn, ids["a"], n1["id"]) and not store.mark_seen(conn, ids["a"], n1["id"])
    assert store.revoke(conn, n2["id"]) and not store.revoke(conn, n1["id"])   # seen ones cannot be withdrawn
    conn.commit()
    assert store.pending(conn, ids["a"]) == []
    old = store.send(conn, ids["a"], "过期", expires_days=1)
    conn.execute("UPDATE admin_notices SET expires_at = now() - interval '1 minute' WHERE id = %s", (old["id"],))
    conn.commit()
    assert store.pending(conn, ids["a"]) == []
    listed = store.list_all(conn, ids["a"])
    assert {r["title"] for r in listed} >= {"第一条", "第二条", "过期"} and all(r["user_id"] == ids["a"] for r in listed)


def test_api_own_and_admin(client, users, monkeypatch):
    ids, t = users
    monkeypatch.setenv("BODY_ADMIN_USER_IDS", f" {ids['admin']} ,x")
    body = {"user_id": ids["b"], "title": "手机同步已断开", "body": "点一下重新连接", "link": "watch2whoop://sync", "link_label": "打开"}
    assert client.post("/v1/admin/notices", json=body, headers=H(t["a"])).status_code == 403          # not an admin
    assert client.post("/v1/admin/notices", json=body, headers=H(t["admin_ro"])).status_code == 403   # admin, read-only token
    assert client.post("/v1/admin/notices", json={**body, "link": "javascript:x"}, headers=H(t["admin"])).status_code == 422
    r = client.post("/v1/admin/notices", json=body, headers=H(t["admin"]))
    assert r.status_code == 201
    nid = r.json()["id"]
    assert client.get("/v1/users/me/notices", headers=H(t["a"])).json()["items"] == []
    assert client.get(f"/v1/users/{ids['b']}/notices", headers=H(t["a"])).status_code == 403
    items = client.get("/v1/users/me/notices", headers=H(t["b"])).json()["items"]
    assert (nid, "手机同步已断开", "watch2whoop://sync", "打开") in [(i["id"], i["title"], i["link"], i["link_label"]) for i in items]
    assert client.post(f"/v1/users/me/notices/{nid}/seen", headers=H(t["a"])).json() == {"seen": False}
    assert client.post(f"/v1/users/me/notices/{nid}/seen", headers=H(t["b"])).json() == {"seen": True}
    assert nid not in [i["id"] for i in client.get("/v1/users/me/notices", headers=H(t["b"])).json()["items"]]
    listed = client.get(f"/v1/admin/notices?user_id={ids['b']}", headers=H(t["admin"])).json()["items"]
    assert listed[0]["id"] == nid and listed[0]["seen_at"] is not None and listed[0]["sent_by"] == ids["admin"]
    assert client.delete(f"/v1/admin/notices/{nid}", headers=H(t["admin"])).status_code == 404   # already seen
    monkeypatch.delenv("BODY_ADMIN_USER_IDS")
    assert client.get("/v1/admin/notices", headers=H(t["admin"])).status_code == 403   # nobody is an admin by default


def test_cli_send(dsn, users):
    ids, _ = users
    env = {**os.environ, "BODY_DB_NAME": TEST_DB}
    run = lambda *a: subprocess.run(["body-engine", *a], capture_output=True, text=True, env=env)   # noqa: E731
    if shutil.which("body-engine") is None:
        pytest.skip("body-engine not on PATH")
    r = run("notice-send", "--user-id", str(ids["a"]), "--title", "来自命令行", "--link", "watch2whoop://sync")
    assert r.returncode == 0, r.stderr
    nid = json.loads(r.stdout)["id"]
    assert run("notice-send", "--user-id", str(ids["a"]), "--title", "x", "--link", "javascript:1").returncode != 0
    listed = json.loads(run("notice-list", "--user-id", str(ids["a"])).stdout)
    assert listed[0]["id"] == nid and listed[0]["sent_by"] is None
    assert json.loads(run("notice-revoke", "--id", str(nid)).stdout) == {"revoked": True}


# ---------------------------------------------------------------- page script (notices.js, run in node)
def test_page_link_attrs():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = (f"const P = require({json.dumps(str(STATIC / 'notices.js'))});\n"
             "console.log(JSON.stringify(['https://a.org/x', '/app/#tab=me', 'watch2whoop://sync', 'javascript:1', 'http://a.org', null]"
             ".map(P.linkAttrs)));")
    out = json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)
    assert out == [{"href": "https://a.org/x", "external": True}, {"href": "/app/#tab=me", "external": False},
                   {"href": "watch2whoop://sync", "external": False}, None, None, None]


def test_page_wiring():
    js = (STATIC / "notices.js").read_text(encoding="utf-8")
    assert '"/v1/users/me/notices"' in js and "/seen`" in js
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="notices.js"') < html.index('src="app.js"')
    assert '"notices.js"' in (STATIC / "sw.js").read_text(encoding="utf-8")
