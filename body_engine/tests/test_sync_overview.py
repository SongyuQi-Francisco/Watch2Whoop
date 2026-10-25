"""Sync overview (sync/overview.py, sync/api.py, web/static/sync-status.js): the state and issue of each situation (skipped
the setup, broken link, server behind, app not opened, behind with / without a phone report, a type missing, all fine,
consent), the endpoints (own only, the phone report's validation and write scope, the ingest path), and the page script's
pure helpers (run in node) and wiring.

Separate test database BODY_TEST_DB_NAME (default body_test_sync_overview), recreated each run; skipped without Postgres.
"""
import json
import os
import secrets
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.sync import overview, params as P

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_sync_overview")
UTC = timezone.utc
TZ = ZoneInfo("Australia/Sydney")
NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)          # 20:00 in Sydney
TODAY = NOW.astimezone(TZ).date()
STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"
H = timedelta(hours=1)
D = timedelta(days=1)


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


def user(conn, *, consent=True, ow=True, last=None, imported=H, web_seen=2 * H, created=30 * D) -> int:
    """A user linked to OW whose newest OW data is `last` before NOW (None = never synced)."""
    uid = conn.execute("""INSERT INTO users (display_name, role, timezone, ow_user_id, consent_at, consent_version, created_at)
                          VALUES ('u', 'member', 'Australia/Sydney', %s, %s, %s, %s) RETURNING id""",
                       (f"ow-{uuid.uuid4().hex[:8]}" if ow else None, NOW if consent else None, "1" if consent else None,
                        NOW - created)).fetchone()["id"]
    if last is not None:
        conn.execute("INSERT INTO ow_watch (user_id, ow_synced_at) VALUES (%s, %s)", (uid, NOW - last))
        conn.execute("INSERT INTO sync_state (user_id, source, last_synced_at, cursor) VALUES (%s, 'ow', %s, %s)",
                     (uid, NOW - imported if imported is not None else None, (NOW - last).isoformat()))
    if web_seen is not None:
        auth.create_token(conn, uid, "web", ["read", "write"])
        tid = conn.execute("SELECT max(id) AS id FROM api_tokens WHERE user_id = %s", (uid,)).fetchone()["id"]
        conn.execute("""INSERT INTO web_sessions (user_id, token_id, session_sha256, last_seen_at, expires_at)
                        VALUES (%s, %s, %s, %s, %s)""", (uid, tid, secrets.token_hex(16), NOW - web_seen, NOW + 30 * D))
    conn.commit()
    return uid


def data(conn, uid, days=7, skip=(), upto=0):
    """Watch data for the last `days` days ending `upto` days before today; `skip`: types left out."""
    metrics = {"heart_rate": 62, "heart_rate_variability_sdnn": 48, "resting_heart_rate": 55, "step_count": 8000}
    for i in range(upto, upto + days):
        day = TODAY - timedelta(days=i)
        for m, v in metrics.items():
            if m in skip:
                continue
            conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, agg, n, source)
                            VALUES (%s, %s, %s, %s, 'avg', 10, 'ow')""", (uid, day, m, v))
        if "sleep" not in skip:
            end = datetime(day.year, day.month, day.day, 7, tzinfo=TZ)
            conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, core_min, deep_min,
                            rem_min, source, device) VALUES (%s, %s, %s, %s, 420, 240, 80, 100, 'ow', 'Apple Watch')""",
                         (uid, day, end - 7 * H, end))
    conn.commit()


def report(conn, uid, **kw):
    overview.put_device(conn, uid, kw)
    conn.execute("UPDATE sync_device_state SET reported_at = %s WHERE user_id = %s", (NOW - H, uid))
    conn.commit()


def ov(conn, uid, in_app=False):
    return overview.collect(conn, uid, NOW, in_app=in_app)


def code(o):
    return o["issue"]["code"] if o["issue"] else None


# ---------------------------------------------------------------- situations
def test_skipped_setup_is_off_with_one_button(conn):
    uid = user(conn)
    o = ov(conn, uid, in_app=True)
    assert o["state"] == "off" and o["setup_needed"] is True
    assert o["issue"]["code"] == "not_connected" and o["issue"]["actions"] == ["connect"]
    assert o["last_sync_at"] is None and o["minutes_ago"] is None
    assert all(t["status"] == "never" for t in o["types"])
    assert o["scores"]["ready"] is False and o["scores"]["empty"]["code"] == "awaiting_sync"


def test_not_provisioned_yet_is_off_too(conn):
    o = ov(conn, user(conn, ow=False))
    assert o["state"] == "off" and o["setup_needed"] and code(o) == "not_connected"


def test_consent_first(conn):
    o = ov(conn, user(conn, consent=False))
    assert o["state"] == "consent" and code(o) == "consent" and o["issue"]["actions"] == ["consent"]
    assert o["setup_needed"] is False   # the consent sheet comes first, not the sync card


def test_normal(conn):
    uid = user(conn, last=20 * timedelta(minutes=1))
    data(conn, uid)
    o = ov(conn, uid, in_app=True)
    assert o["state"] == "ok" and o["issue"] is None and o["setup_needed"] is False
    assert o["minutes_ago"] == 20 and o["label"] == "同步正常"
    assert {t["key"]: t["status"] for t in o["types"]} == dict.fromkeys(P.OVERVIEW_TYPES, "ok") | {"workouts": "never"}
    sleep = next(t for t in o["types"] if t["key"] == "sleep")
    assert sleep["last_at"].startswith(TODAY.isoformat()) and sleep["days_since"] == 0


def test_broken_link_needs_reconnect(conn):
    uid = user(conn, last=3 * D)
    data(conn, uid, upto=3)
    o = ov(conn, uid, in_app=True)
    assert o["state"] == "issue" and code(o) == "credentials" and o["issue"]["actions"] == ["reconnect"]
    assert o["issue"]["tone"] == "bad"


def test_app_reported_auth_error(conn):
    uid = user(conn, last=8 * H)
    data(conn, uid)
    report(conn, uid, last_error="auth", app_version="1.0.8")
    assert code(ov(conn, uid, in_app=True)) == "credentials"


def test_server_behind(conn):
    uid = user(conn, last=3 * D, imported=10 * H)
    data(conn, uid, upto=3)
    o = ov(conn, uid)
    assert code(o) == "server" and o["issue"]["actions"] == ["retry"]


def test_app_not_opened_outside_the_app(conn):
    uid = user(conn, last=3 * D, web_seen=4 * D)
    data(conn, uid, upto=3)
    assert code(ov(conn, uid)) == "app_closed"
    assert code(ov(conn, uid, in_app=True)) == "credentials"   # inside the app the app is open: it is the link


@pytest.mark.parametrize("rep, expected, actions", [
    ({}, "lagging", ["sync_now", "retry"]),
    ({"low_power": True}, "low_power", ["sync_now"]),
    ({"health_readable": False}, "locked", ["sync_now"]),
    ({"last_error": "locked"}, "locked", ["sync_now"]),
    ({"background_refresh": "denied"}, "background_off", ["open_settings", "sync_now"]),
])
def test_behind_by_phone_report(conn, rep, expected, actions):
    uid = user(conn, last=8 * H)
    data(conn, uid)
    if rep:
        report(conn, uid, **rep)
    o = ov(conn, uid, in_app=True)
    assert code(o) == expected and o["issue"]["actions"] == actions


def test_background_off_shows_while_fresh_and_old_reports_are_ignored(conn):
    uid = user(conn, last=10 * timedelta(minutes=1))
    data(conn, uid)
    report(conn, uid, background_refresh="restricted", low_power=True)
    assert code(ov(conn, uid, in_app=True)) == "background_off"
    conn.execute("UPDATE sync_device_state SET reported_at = %s WHERE user_id = %s", (NOW - P.SYNC_DEVICE_FRESH - H, uid))
    conn.commit()
    o = ov(conn, uid, in_app=True)
    assert o["state"] == "ok" and o["device"] is None


def test_only_sleep_missing(conn):
    uid = user(conn, last=30 * timedelta(minutes=1))
    data(conn, uid, skip=("sleep",))
    o = ov(conn, uid, in_app=True)
    assert code(o) == "type_missing" and o["issue"]["types"] == ["sleep"]
    assert o["issue"]["actions"] == ["sleep_help", "authorize"] and o["issue"]["label"] == "没收到睡眠"
    assert o["scores"]["empty"]["code"] == "no_sleep"


def test_missing_type_waits_for_a_new_connection(conn):
    uid = user(conn, last=5 * timedelta(minutes=1), created=H)
    data(conn, uid, skip=("sleep", "heart_rate_variability_sdnn"))
    assert ov(conn, uid, in_app=True)["state"] == "ok"   # the first sync may still bring them
    uid2 = user(conn, last=5 * timedelta(minutes=1))
    data(conn, uid2, days=2, skip=("sleep",))            # only 2 days of heart rate yet
    assert ov(conn, uid2, in_app=True)["state"] == "ok"


def test_english_labels(conn):
    from body_engine.i18n import use_lang
    uid = user(conn, last=30 * timedelta(minutes=1))
    data(conn, uid, skip=("sleep", "heart_rate_variability_sdnn"))
    with use_lang("en"):
        o = ov(conn, uid, in_app=True)
    assert o["issue"]["label"] == "Missing: HRV, Sleep" and o["types"][0]["label"] == "Heart rate"


def test_put_device_replaces(conn):
    uid = user(conn)
    overview.put_device(conn, uid, {"low_power": True, "unasked_types": ["sleep"]})
    r = overview.put_device(conn, uid, {"background_refresh": "available"})
    assert r["low_power"] is None and r["background_refresh"] == "available" and r["unasked_types"] == []


# ---------------------------------------------------------------- endpoints
@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None


def test_endpoints(conn, client):
    uid = user(conn, last=None)
    other = user(conn)
    tok = auth.create_token(conn, uid, "app", ["read", "write"])
    ro = auth.create_token(conn, uid, "ro", ["read"])
    conn.commit()
    h = {"Authorization": f"Bearer {tok}"}
    r = client.get("/v1/users/me/sync/overview?in_app=1", headers=h)
    assert r.status_code == 200 and r.json()["state"] == "off" and r.json()["setup_needed"] is True
    assert client.get(f"/v1/users/{other}/sync/overview", headers=h).status_code == 403
    assert client.get("/v1/users/me/sync/overview").status_code == 401

    body = {"app_version": "1.0.8", "background_refresh": "denied", "low_power": False, "health_readable": True,
            "last_attempt_at": "2026-10-25T08:00:00+00:00", "last_error": "network", "unasked_types": ["sleep"], "future": 1}
    r = client.put("/v1/users/me/sync/device", headers=h, json=body)
    assert r.status_code == 200 and r.json()["background_refresh"] == "denied" and r.json()["unasked_types"] == ["sleep"]
    assert client.put("/v1/users/me/sync/device", headers=h, json={"last_error": "boom"}).status_code == 422
    assert client.put("/v1/users/me/sync/device", headers=h, json={"unasked_types": ["x" * 61]}).status_code == 422
    assert client.put("/v1/users/me/sync/device", headers={"Authorization": f"Bearer {ro}"}, json={}).status_code == 403
    assert client.put(f"/v1/users/{other}/sync/device", headers=h, json={}).status_code == 403
    r = client.put("/v1/ingest/sync-device", headers=h, json={"low_power": True})
    assert r.status_code == 200 and r.json()["low_power"] is True
    assert client.put("/v1/ingest/sync-device", json={}).status_code == 401
    assert client.get("/v1/users/me/sync/overview", headers=h).json()["device"]["low_power"] is True


# ---------------------------------------------------------------- page script (sync-status.js, run in node)
def node_json(expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = ("globalThis.window = undefined;\n"
             "globalThis.I18N = { t: (k, p) => k.replace(/\\{(\\w+)\\}/g, (_, n) => (p || {})[n]) };\n"
             f"const src = require('fs').readFileSync({json.dumps(str(STATIC / 'sync-status.js'))}, 'utf8')"
             ".replace('window.I18N.t(k, p)', 'I18N.t(k, p)');\n"
             "const module = { exports: {} }; new Function('module', src)(module); const P = module.exports;\n"
             f"console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)


def test_page_helpers():
    out = node_json("""[
      [P.agoText(null), P.agoText(0.4), P.agoText(5.9), P.agoText(125), P.agoText(3 * 1440 + 5)],
      [P.typeAgo({ last_at: "2026-10-25T08:50:00Z" }, new Date("2026-10-25T09:00:00Z")), P.typeAgo({ days_since: null }, new Date()),
       P.typeAgo({ days_since: 0 }, new Date()), P.typeAgo({ days_since: 1 }, new Date()), P.typeAgo({ days_since: 4 }, new Date())],
      [P.actionLink("sync_now", {}, true), P.actionLink("sync_now", { syncNow: true }, true), P.actionLink("sync_now", {}, false),
       P.actionLink("open_settings", {}, true), P.actionLink("open_settings", { appSettings: true }, true),
       P.actionLink("authorize", { healthSettings: true }, true), P.actionLink("retry", {}, true)],
      [P.connectLink({ code: "ABCD2345", ow_url: "https://sync.example.org" }), P.connectLink(null)],
      [P.wantsCard({ setup_needed: true }, "today", true), P.wantsCard({ setup_needed: true }, "trends", true),
       P.wantsCard({ setup_needed: true }, "friends", true), P.wantsCard({ setup_needed: true }, "me", true),
       P.wantsCard({ setup_needed: true }, "today", false), P.wantsCard({ setup_needed: false }, "today", true), P.wantsCard(null, "today", true)],
      [P.wantsAsk({ setup_needed: true }, false), P.wantsAsk({ setup_needed: true }, true), P.wantsAsk({ setup_needed: false }, false)],
      [P.bannerFor({ state: "issue", issue: { code: "lagging", label: "L" } }), P.bannerFor({ state: "issue", issue: { code: "credentials", label: "C" } }),
       P.bannerFor({ state: "issue", issue: { code: "type_missing", label: "T" } }), P.bannerFor({ state: "ok", issue: null })],
      [P.toneOf(null), P.toneOf({ state: "ok" }), P.toneOf({ state: "off" }), P.toneOf({ state: "issue", issue: { tone: "bad" } })],
    ]""")
    assert out[0] == ["—", "刚刚", "5 分钟前", "2 小时前", "3 天前"]
    assert out[1] == ["10 分钟前", "从没收到", "今天", "昨天", "4 天前"]
    assert out[2] == ["watch2whoop://sync", "watch2whoop://sync-now", "watch2whoop://sync", None, "watch2whoop://app-settings",
                      "watch2whoop://health-settings", None]
    assert out[3] == ["watch2whoop://sync-connect?ow=https%3A%2F%2Fsync.example.org&code=ABCD2345", "watch2whoop://sync-connect"]
    assert out[4] == [True, True, True, False, False, False, False]
    assert out[5] == [True, False, False]
    assert out[6] == ["L", "", "", ""]   # credentials keep sync-repair.js's banner; a missing type only the dot
    assert out[7] == ["plain", "good", "plain", "bad"]


def test_page_actions_cover_the_server():
    js = (STATIC / "sync-status.js").read_text(encoding="utf-8")
    for a in overview.ACTIONS:
        assert f"{a}:" in js and (f'a === "{a}"' in js or f'"{a}"' in js), a   # a label and a branch / link for each
    for c in overview.ISSUES:
        assert c in overview.TONE


def test_page_wiring():
    js = (STATIC / "sync-status.js").read_text(encoding="utf-8")
    assert "/v1/users/me/sync/overview" in js and '"/v1/account/ow-invitation"' in js and "/v1/users/me/sync/device" in js
    assert "Watch2WhoopFeatures" in js and "watch2whoop://sync-connect" in js
    # the only thing stored is that the first-open sheet was shown, never the code
    assert js.count("localStorage.setItem(") == 1 and "sessionStorage" not in js
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="sync-repair.js"') < html.index('src="sync-status.js"') < html.index('src="app.js"')
    assert '"sync-status.js"' in (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "window.BodySyncRepair = {" in (STATIC / "sync-repair.js").read_text(encoding="utf-8")
    assert "isSelf, openHandbook" in (STATIC / "app.js").read_text(encoding="utf-8")
