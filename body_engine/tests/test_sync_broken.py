"""「手机同步已断开」 (sync/broken.py, sync/api.py, web/static/sync-repair.js): who counts as broken (linked, synced before,
older than 36 h but not given up, the server reached OW lately, uses the app), the endpoint (own only), `sync-broken`, and
the page script's pure helpers (run in node) and wiring.

Separate test database BODY_TEST_DB_NAME (default body_test_sync_broken), recreated each run; skipped without Postgres.
"""
import json
import os
import secrets
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.sync import broken, params as P

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_sync_broken")
UTC = timezone.utc
NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
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


@pytest.fixture
def conn(dsn):
    c = psycopg.connect(dsn, row_factory=dict_row)
    yield c
    c.close()


def user(conn, ow="auto", last_upload=None, cursor=None, imported=None, web_seen=None, app_upload=None) -> int:
    """A user; times are offsets before NOW (timedelta) or None."""
    ow_id = f"ow-{uuid.uuid4().hex[:8]}" if ow == "auto" else ow
    uid = conn.execute("""INSERT INTO users (display_name, role, timezone, ow_user_id, consent_at, consent_version)
                          VALUES ('u', 'member', 'Australia/Sydney', %s, now(), '1') RETURNING id""", (ow_id,)).fetchone()["id"]
    if last_upload is not None:
        conn.execute("INSERT INTO ow_watch (user_id, ow_synced_at) VALUES (%s, %s)", (uid, NOW - last_upload))
    if cursor is not None or imported is not None:
        conn.execute("INSERT INTO sync_state (user_id, source, last_synced_at, cursor) VALUES (%s, 'ow', %s, %s)",
                     (uid, NOW - imported if imported is not None else None,
                      (NOW - cursor).isoformat() if cursor is not None else None))
    if web_seen is not None:
        auth.create_token(conn, uid, "web", ["read", "write"])
        tid = conn.execute("SELECT max(id) AS id FROM api_tokens WHERE user_id = %s", (uid,)).fetchone()["id"]
        conn.execute("""INSERT INTO web_sessions (user_id, token_id, session_sha256, last_seen_at, expires_at)
                        VALUES (%s, %s, %s, %s, %s)""", (uid, tid, secrets.token_hex(16), NOW - web_seen, NOW + timedelta(days=30)))
    if app_upload is not None:
        conn.execute("INSERT INTO rawdata_uploads (user_id, batch_id, received_at) VALUES (%s, %s, %s)",
                     (uid, uuid.uuid4(), NOW - app_upload))
    conn.commit()
    return uid


H = timedelta(hours=1)
D = timedelta(days=1)


def judge(conn, uid, visiting=False):
    return broken.check(conn, uid, NOW, visiting=visiting)


def test_broken_when_data_stopped_but_app_used(conn):
    uid = user(conn, last_upload=3 * D, cursor=3 * D + H, imported=H, web_seen=2 * H)
    r = judge(conn, uid)
    assert r["broken"] and r["reason"] == "broken"
    assert r["last_data_at"] == NOW - 3 * D and r["hours"] == 72.0 and r["after_hours"] == 36
    # the app's own upload counts as using the app too
    uid2 = user(conn, cursor=2 * D, imported=2 * H, app_upload=5 * H)
    assert judge(conn, uid2)["broken"]


def test_threshold_and_newest_source(conn):
    # just under 36 h: fine; the cursor (newest sample) is newer than OW's stamp here: the newer one counts
    ok = user(conn, last_upload=3 * D, cursor=P.SYNC_BROKEN_AFTER - H, imported=H, web_seen=H)
    assert judge(conn, ok)["reason"] == "ok"
    over = user(conn, last_upload=P.SYNC_BROKEN_AFTER + H, imported=H, web_seen=H)
    assert judge(conn, over)["broken"]


@pytest.mark.parametrize("kw, reason", [
    (dict(ow=None, cursor=3 * D, imported=H, web_seen=H), "not_linked"),
    (dict(imported=H, web_seen=H), "never"),                                     # linked, never synced: onboarding
    (dict(cursor=40 * D, imported=H, web_seen=H), "given_up"),                    # 30+ days: probably on purpose
    (dict(cursor=3 * D, imported=P.SYNC_BROKEN_SERVER_OK + H, web_seen=H), "server_stale"),   # OW / our import down
    (dict(cursor=3 * D, web_seen=H), "never_imported"),
    (dict(cursor=3 * D, imported=H, web_seen=P.SYNC_BROKEN_APP_SEEN + H), "not_seen"),   # does not open the app
    (dict(cursor=3 * D, imported=H), "not_seen"),
])
def test_not_broken(conn, kw, reason):
    uid = user(conn, **kw)
    r = judge(conn, uid)
    assert not r["broken"]
    assert r["reason"] == ("server_stale" if reason == "never_imported" else reason)


def test_visiting_counts_as_using_the_app(conn):
    uid = user(conn, cursor=3 * D, imported=H)
    assert judge(conn, uid)["reason"] == "not_seen"
    assert judge(conn, uid, visiting=True)["broken"]


def test_broken_users_lists_only_broken_oldest_first(conn):
    a = user(conn, cursor=5 * D, imported=H, web_seen=H)
    b = user(conn, cursor=2 * D, imported=H, web_seen=H)
    user(conn, cursor=H, imported=H, web_seen=H)
    ids = [x["user_id"] for x in broken.broken_users(conn, NOW)]
    assert ids.index(a) < ids.index(b)
    assert all(x["broken"] for x in broken.broken_users(conn, NOW))


def test_app_mode_judges_the_engine_uploads(conn, monkeypatch):
    """#219 S5b: in the sync mode app the engine's batches (rawdata_uploads with a phase) are the phone's channel: OW's
    stamps, the OW link and the server's OW import do not count; channel says which one was judged."""
    def engine_batch(uid, ago, phase="incremental"):
        conn.execute("INSERT INTO rawdata_uploads (user_id, batch_id, received_at, phase) VALUES (%s, %s, %s, %s)",
                     (uid, uuid.uuid4(), NOW - ago, phase))
        conn.commit()
    stopped = user(conn, ow=None, web_seen=H)                       # engine-only account, the engine stopped 3 days ago
    engine_batch(stopped, 3 * D)
    conn.execute("INSERT INTO sync_mode_state (user_id, mode) VALUES (%s, 'app')", (stopped,))   # listed by sync-broken
    fresh = user(conn, last_upload=5 * D, cursor=5 * D, web_seen=H)   # OW long silent (stopped in the app mode), engine fine
    engine_batch(fresh, H)
    new = user(conn, ow=None, web_seen=H, app_upload=H)             # only non-engine uploads so far: onboarding
    app = {stopped, fresh, new}
    monkeypatch.setattr(broken, "_mode", lambda c, u: "app" if u in app else "ow")
    r = judge(conn, stopped)
    assert (r["broken"], r["reason"], r["channel"]) == (True, "broken", "app")   # no server_stale: nothing is pulled
    assert r["last_data_at"] == NOW - 3 * D
    assert (judge(conn, fresh)["reason"], judge(conn, new)["reason"]) == ("ok", "never")
    listed = [x["user_id"] for x in broken.broken_users(conn, NOW)]
    assert stopped in listed and fresh not in listed
    monkeypatch.setattr(broken, "_mode", lambda c, u: "ow")
    assert judge(conn, fresh)["channel"] == "ow" and judge(conn, stopped)["reason"] == "not_linked"


# ---------------------------------------------------------------- endpoint
@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None


def test_endpoint_own_only(conn, client):
    now = datetime.now(UTC)
    uid = conn.execute("""INSERT INTO users (display_name, role, timezone, ow_user_id, consent_at, consent_version)
                          VALUES ('e', 'member', 'Australia/Sydney', 'ow-e', now(), '1') RETURNING id""").fetchone()["id"]
    conn.execute("INSERT INTO sync_state (user_id, source, last_synced_at, cursor) VALUES (%s, 'ow', %s, %s)",
                 (uid, now - H, (now - 3 * D).isoformat()))
    tok = auth.create_token(conn, uid, "app", ["read", "write"])
    other = user(conn, cursor=3 * D, imported=H)
    conn.commit()
    h = {"Authorization": f"Bearer {tok}"}
    r = client.get("/v1/users/me/sync/connection", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["broken"] is True and body["reason"] == "broken" and "user_id" not in body
    assert client.get(f"/v1/users/{other}/sync/connection", headers=h).status_code == 403
    assert client.get("/v1/users/me/sync/connection").status_code == 401


# ---------------------------------------------------------------- page script (sync-repair.js, run in node)
def node_json(expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = (f"const P = require({json.dumps(str(STATIC / 'sync-repair.js'))});\n"
             f"console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)


def test_page_helpers():
    out = node_json("""[P.setupLink({ code: "ABCD2345", ow_url: "https://sync.example.org" }), P.setupLink({ code: "ABCD2345", ow_url: null }),
      P.localDay(new Date(2026, 0, 5, 23, 59)),
      P.wantsSheet({ broken: true }, null, "2026-10-25"), P.wantsSheet({ broken: true }, "2026-10-25", "2026-10-25"),
      P.wantsSheet({ broken: true }, "2026-10-24", "2026-10-25"), P.wantsSheet({ broken: false }, null, "2026-10-25"), P.wantsSheet(null, null, "x")]""")
    assert out[0] == "watch2whoop://setup?ow=https%3A%2F%2Fsync.example.org&code=ABCD2345"
    assert out[1] == "watch2whoop://setup?code=ABCD2345"
    assert out[2] == "2026-01-05"
    assert out[3:] == [True, False, True, False, False]   # once a day, only when broken


def test_page_wiring():
    js = (STATIC / "sync-repair.js").read_text(encoding="utf-8")
    assert '"/v1/users/me/sync/connection"' in js and '"/v1/account/ow-invitation"' in js
    assert "Watch2WhoopFeatures?.syncRepair" in js and 'href="watch2whoop://sync"' in js   # new apps: link; 1.0.6 / 1.0.7: sync page
    # the sync mode app (#219 S5b): no OW invitation, the app's sync page restarts the engine
    assert js.index('st?.channel === "app"') < js.index('"/v1/account/ow-invitation"')
    # the only thing stored is the day the sheet was shown, never the code
    assert js.count("localStorage.setItem(") == 1 and "localStorage.setItem(DAY_KEY, v)" in js and "sessionStorage" not in js
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="sync-repair.js"') < html.index('src="app.js"')
    assert '"sync-repair.js"' in (STATIC / "sw.js").read_text(encoding="utf-8")
