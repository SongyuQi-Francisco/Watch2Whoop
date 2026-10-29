"""Forced upgrade (R2, client/, migration 1070): the policy (dates, minimum versions, link, audited), which client a request
comes from, notice / block per auth path (Today page cookie, native old / new, Bearer API token, MCP, uploads, unknown
clients), the kill switch, the send command (dry run, idempotent, skips people already upgraded), the page script.

Separate test database BODY_TEST_DB_NAME (default body_test_upgrade), recreated each run; skipped without Postgres.
"""
import json
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth
from body_engine.ai import tokens as ai_tokens
from body_engine.client import notice, params as P, policy
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_upgrade")
STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"
URL = "https://example.com/watch2whoop"
OLD = {"X-App-Client": "ios-native", "X-App-Version": "1.0.9"}
NEW = {"X-App-Client": "ios-native", "X-App-Version": "2.0.0"}
MCP_H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
         "MCP-Protocol-Version": "2025-06-18"}


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
        ids = {n: add(n) for n in ("a", "b", "c")}
        t = {n: auth.create_token(conn, i, "app", ["read", "write"]) for n, i in ids.items()}
        t["mcp"] = ai_tokens.create(conn, ids["a"], "pytest")["token"]
        conn.commit()
    return ids, t


@pytest.fixture
def conn(dsn):
    c = psycopg.connect(dsn, row_factory=dict_row)
    yield c
    c.close()


@pytest.fixture(scope="module")
def client(dsn):
    """Runs the full lifespan (/mcp needs its session manager), with the connection pool on the test DB."""
    mp = pytest.MonkeyPatch()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    assert api.settings.db_name == TEST_DB
    with TestClient(api.app, base_url="https://testserver") as c:
        yield c
    mp.undo()


def H(t, **extra):
    return {"Authorization": f"Bearer {t}", **extra}


def configure(conn, *, block_days: int | None, notice_days: int | None = None, on: bool = True, url: str | None = URL):
    """Block date = today + block_days (None = no block); the flag on / off for everyone."""
    today = date.today()
    policy.update(conn, block_from=None if block_days is None else (today + timedelta(days=block_days)).isoformat(),
                  notice_from=None if notice_days is None else (today + timedelta(days=notice_days)).isoformat(),
                  url=url, actor="pytest")
    flags.set_flag(conn, P.FLAG, "all" if on else "off", actor="pytest")
    conn.commit()


def me_state(client, headers=None):
    r = client.get("/v1/web/me", headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["upgrade"]


def web(client, token):
    client.cookies.clear()
    assert client.post("/v1/web/login", json={"token": token}).status_code == 200
    return client


# ---------------------------------------------------------------- pure parts
def test_versions_and_window():
    assert policy.meets("2.0.0", None, "2.0.0") and policy.meets("2.1", None, "2.0.0")
    assert not policy.meets("1.0.9", None, "2.0.0") and not policy.meets(None, None, "2.0.0")
    assert policy.meets("2.0.0", "57", "2.0.0+57") and not policy.meets("2.0.0", "56", "2.0.0+57")
    utc = policy.ZoneInfo("UTC")
    n, b = policy.window(policy.Policy(block_from="2026-12-10"), utc)
    assert b == datetime(2026, 12, 10, tzinfo=utc) and n == b - timedelta(days=P.NOTICE_DAYS)
    assert policy.window(policy.Policy(), utc) == (None, None)
    pol = policy.Policy()
    assert not policy.below_min(pol, policy.Client("ios-native", None)), "no readable version: unknown, not blocked"
    assert not policy.below_min(pol, policy.Client("ios-native", "garbage"))
    assert policy.below_min(pol, policy.Client("ios-native", "1.0.8")) and policy.below_min(pol, policy.Client("web"))
    assert not policy.below_min(policy.Policy(mins={"ios-native": "none", "web": "none"}), policy.Client("web"))


def test_defaults_are_off(conn, users):
    pol = policy.load_fresh(conn)
    assert pol.block_from is None and pol.notice_from is None
    assert flags.rows(conn).get(P.FLAG, {"state": "off"})["state"] == "off"
    ids, _ = users
    assert policy.status(conn, {"id": ids["a"], "timezone": "UTC"}, policy.Client("web"))["state"] == "none"


def test_update_checks_and_audit(conn):
    with pytest.raises(ValueError):
        policy.update(conn, notice_from="2026-12-10", block_from="2026-12-01")
    with pytest.raises(ValueError):
        policy.update(conn, url="http://example.com/x")
    with pytest.raises(ValueError):
        policy.update(conn, mins={"web": "2.0.0"})
    with pytest.raises(ValueError):
        policy.update(conn, mins={"ios-native": "two"})
    with pytest.raises(ValueError):
        policy.update(conn, block_from="next week")
    conn.rollback()
    policy.update(conn, block_from="2030-01-01", url=URL, mins={"ios-native": "2.0.0+57"}, actor="pytest")
    conn.commit()
    pol = policy.load_fresh(conn)
    assert pol.block_from == "2030-01-01" and pol.url == URL and pol.mins["ios-native"] == "2.0.0+57"
    audit = conn.execute("SELECT target FROM rollout_audit WHERE action = 'setting.set' AND actor = 'pytest'").fetchall()
    assert {"upgrade.block_from", "upgrade.url", "upgrade.min.ios-native"} <= {r["target"] for r in audit}
    policy.update(conn, block_from="none", mins={"ios-native": "2.0.0"}, actor="pytest")   # back to the default: row removed
    conn.commit()
    assert flags.get_setting(conn, "upgrade.min.ios-native") is None and policy.load_fresh(conn).block_from is None


# ---------------------------------------------------------------- per auth path
def test_before_the_notice(client, conn, users):
    _, t = users
    configure(conn, block_days=30)
    assert me_state(web(client, t["a"]))["state"] == "none"
    client.cookies.clear()
    assert me_state(client, H(t["a"], **OLD))["state"] == "none"
    assert client.get("/v1/users/me/notices", headers=H(t["a"], **OLD)).status_code == 200


def test_during_the_notice(client, conn, users):
    _, t = users
    configure(conn, block_days=3)
    st = me_state(web(client, t["a"]))
    assert st["state"] == "notice" and not st["must_upgrade"] and st["url"] == URL and st["min"] == "2.0.0"
    assert st["block_from"] == (date.today() + timedelta(days=3)).isoformat() and st["title"] and st["message"]
    assert client.get("/v1/users/me/notices").status_code == 200, "a notice never refuses anything"
    client.cookies.clear()
    assert me_state(client, H(t["a"], **OLD))["state"] == "notice"
    assert me_state(client, H(t["a"], **NEW))["state"] == "none"
    assert me_state(client, H(t["a"]))["state"] == "none", "a Bearer token without X-App-Client is never asked"
    en = client.get("/v1/web/me", headers=H(t["a"], **OLD, **{"X-Body-Lang": "en"})).json()["upgrade"]
    assert en["title"] == "Please update Watch2Whoop"


def test_explicit_notice_date(client, conn, users):
    _, t = users
    configure(conn, block_days=10, notice_days=-1)   # notice from yesterday although the block is 10 days away
    client.cookies.clear()
    assert me_state(client, H(t["a"], **OLD))["state"] == "notice"
    configure(conn, block_days=3, notice_days=2)     # the notice starts later than the default 7 days before
    assert me_state(client, H(t["a"], **OLD))["state"] == "none"
    policy.update(conn, notice_from="none")
    conn.commit()


def test_after_the_block(client, conn, users, dsn):
    _, t = users
    configure(conn, block_days=-1)
    web(client, t["a"])
    st = me_state(client)
    assert st["state"] == "block" and st["must_upgrade"]
    r = client.get("/v1/users/me/notices")
    assert r.status_code == 426
    d = r.json()["detail"]
    assert d["code"] == "upgrade_required" and d["url"] == URL and d["message"]
    boot = client.post("/v1/web/boot", json={"paths": ["/v1/users/me/notices"]}, headers={"X-Body-Web": "1"})
    if boot.status_code == 200:   # flag boot_api on: each item is checked on its own path
        assert boot.json()["items"][0]["status"] == 426
    client.cookies.clear()
    assert client.get("/v1/users/me/notices", headers=H(t["a"], **OLD)).status_code == 426
    assert client.post("/v1/sync/pull", headers=H(t["a"], **OLD), json={}).status_code == 426
    assert client.get("/v1/users/me/notices", headers=H(t["a"], **NEW)).status_code == 200
    assert me_state(client, H(t["a"], **OLD))["state"] == "block", "/v1/web/me still answers (the app shows its page)"
    # never blocked: API tokens, unknown clients, a native request without a version, uploads, MCP
    assert client.get("/v1/users/me/notices", headers=H(t["a"])).status_code == 200
    assert client.get("/v1/users/me/notices", headers=H(t["a"], **{"X-App-Client": "android", "X-App-Version": "0.1"})).status_code == 200
    assert client.get("/v1/users/me/notices", headers=H(t["a"], **{"X-App-Client": "ios-native"})).status_code == 200
    assert client.post("/v1/ingest/healthkit", headers=H(t["a"], **OLD), json={}).status_code != 426
    init = client.post("/mcp", headers={**MCP_H, **OLD, "Authorization": f"Bearer {t['mcp']}"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}}})
    assert init.status_code == 200, init.text


def test_kill_switch(client, conn, users):
    _, t = users
    configure(conn, block_days=-1, on=False)
    web(client, t["a"])
    assert me_state(client)["state"] == "none"
    assert client.get("/v1/users/me/notices").status_code == 200
    client.cookies.clear()
    assert client.get("/v1/users/me/notices", headers=H(t["a"], **OLD)).status_code == 200


def test_web_minimum_none(client, conn, users):
    _, t = users
    configure(conn, block_days=-1)
    policy.update(conn, mins={"web": "none"})
    conn.commit()
    web(client, t["a"])
    assert client.get("/v1/users/me/notices").status_code == 200
    client.cookies.clear()
    assert client.get("/v1/users/me/notices", headers=H(t["a"], **OLD)).status_code == 426
    policy.update(conn, mins={"web": "all"})
    flags.set_flag(conn, P.FLAG, "off", actor="pytest")
    conn.commit()


# ---------------------------------------------------------------- send command
def test_send_dry_run_idempotent_and_skips_upgraded(conn, users):
    ids, _ = users
    configure(conn, block_days=3)
    conn.execute("INSERT INTO sync_device_state (user_id, app_version) VALUES (%s, '2.0.0') ON CONFLICT (user_id) DO UPDATE SET app_version = '2.0.0'",
                 (ids["c"],))
    conn.commit()
    dry = notice.run(conn, dry_run=True)
    by = {u["user_id"]: u for u in dry["users"]}
    assert by[ids["a"]]["status"] == by[ids["b"]]["status"] == "would_send" and by[ids["a"]]["stage"] == "notice"
    assert by[ids["c"]]["status"] == "upgraded"
    assert conn.execute("SELECT count(*) AS n FROM upgrade_notice_sends").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM admin_notices").fetchone()["n"] == 0
    out = notice.run(conn)
    assert out["counts"]["sent"] == 2
    row = conn.execute("SELECT title, link, link_label, title_en FROM admin_notices WHERE user_id = %s", (ids["a"],)).fetchone()
    assert row["link"] == URL and row["link_label"] == "去升级" and row["title_en"].startswith("Please update")
    kinds = conn.execute("SELECT kind FROM notifications WHERE user_id = %s", (ids["a"],)).fetchall()
    assert [k["kind"] for k in kinds] == [P.NOTIFY_KIND]
    again = notice.run(conn)
    assert again["counts"].get("sent") is None and again["counts"]["already"] == 2
    last = notice.run(conn, stage="last-day", user_id=ids["a"])
    assert last["counts"] == {"sent": 1}
    flags.set_flag(conn, P.FLAG, "off", actor="pytest")
    conn.commit()
    assert notice.run(conn, dry_run=True)["counts"] == {"flag_off": 3}


def test_send_needs_dates_and_link(conn):
    policy.update(conn, block_from="none", notice_from="none", url="none")
    conn.commit()
    with pytest.raises(ValueError):
        notice.run(conn, dry_run=True)


def test_due_stage():
    b = datetime(2026, 12, 10, tzinfo=timezone.utc)
    n = b - timedelta(days=7)
    assert notice.due_stage(n, b, n - timedelta(hours=1)) is None
    assert notice.due_stage(n, b, n) == "notice"
    assert notice.due_stage(n, b, b - timedelta(hours=2)) == "last-day"
    assert notice.due_stage(n, b, b) is None


def test_cli(dsn, users):
    env = {**os.environ, "BODY_DB_NAME": TEST_DB}
    exe = shutil.which("body-engine")
    if not exe:
        pytest.skip("body-engine not on PATH")

    def run(*a):
        return subprocess.run([exe, *a], capture_output=True, text=True, env=env, check=True)
    out = json.loads(run("upgrade", "set", "--block-from", "2031-01-10", "--url", URL, "--by", "pytest").stdout)
    assert out["block_from"] == "2031-01-10" and out["window_utc"]["notice"].startswith("2031-01-03")
    assert json.loads(run("upgrade", "on", "--cohort", "inner").stdout)["flag"]["state"] == "cohort"
    assert json.loads(run("upgrade", "off").stdout)["flag"]["state"] == "off"
    assert json.loads(run("upgrade", "send", "--dry-run").stdout)["dry_run"] is True
    assert "sent" in json.loads(run("upgrade", "status").stdout)


# ---------------------------------------------------------------- page script (upgrade.js, run in node)
def test_page_script():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = (f"const P = require({json.dumps(str(STATIC / 'upgrade.js'))});\n"
             "console.log(JSON.stringify([P.noticeDue('2026-11-01', null), P.noticeDue('2026-11-01', '2026-11-01'),"
             " P.noticeDue('2026-11-02', '2026-11-01'), P.safeUrl('https://example.com/x'), P.safeUrl('javascript:1'),"
             " P.safeUrl('http://example.com'), P.safeUrl(null)]));")
    out = json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)
    assert out == [True, False, True, "https://example.com/x", None, None, None]


def test_page_wiring():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="upgrade.js"') < html.index('src="app.js"')
    assert '"upgrade.js"' in (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "r.status === 426" in (STATIC / "app.js").read_text(encoding="utf-8")
