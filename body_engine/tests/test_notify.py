"""Notifications (issue #175): notify() (in-app row, push decisions, merging, quiet hours, settings), device registration
(own only, sign-out), invalid tokens removed, the APNs provider token, the in-app API behind the flag.

APNs is always a fake client here, never Apple. Separate test database BODY_TEST_DB_NAME (default body_test_notify),
recreated each run; skipped without Postgres.
"""
import base64
import json
import os
import secrets
import shutil
import subprocess
from datetime import datetime
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
from body_engine.notify import apns, core, notify, params as P
from body_engine.rollout import flags

TZ = ZoneInfo("Australia/Sydney")
NOON = datetime(2026, 10, 22, 12, 0, tzinfo=TZ)
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_notify")


class FakeApns:
    """Records every send; `answers[(token, env)]` = apns.Result to return (default 200)."""

    def __init__(self):
        self.sent: list[dict] = []
        self.answers: dict[tuple[str, str], apns.Result] = {}

    def send(self, token, environment, payload, headers):
        self.sent.append({"token": token, "env": environment, "payload": payload, "headers": headers})
        return self.answers.get((token, environment), apns.Result(200))


def tok():
    return secrets.token_hex(32)


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
        def add(name, lang=None):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, language, consent_at, consent_version)
                                   VALUES (%s, 'member', 'Australia/Sydney', %s, now(), '1') RETURNING id""",
                                (name, lang)).fetchone()["id"]
        ids = {"a": add("a"), "b": add("b", "en"), "c": add("c")}
        flags.set_flag(conn, P.FEATURE, "cohort", cohort="beta", actor="test")   # on for a and b only
        flags.cohort_add(conn, ids["a"], "beta", actor="test")
        flags.cohort_add(conn, ids["b"], "beta", actor="test")
        conn.commit()
        flags.invalidate()
        t = {n: auth.create_token(conn, i, "app", ["read", "write"]) for n, i in ids.items()}
        t["a_ro"] = auth.create_token(conn, ids["a"], "ro", ["read"])
        t["a2"] = auth.create_token(conn, ids["a"], "app2", ["read", "write"])
        conn.commit()
    return ids, t


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None


@pytest.fixture
def fake():
    f = FakeApns()
    core.set_client(f)
    yield f
    core.set_client(None)


def H(t, lang=None):
    return {"Authorization": f"Bearer {t}", **({"X-Body-Lang": lang} if lang else {})}


def conn_of(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def reset(dsn, uid):
    with conn_of(dsn) as conn:
        for t in ("notifications", "push_devices", "notification_settings"):
            conn.execute(f"DELETE FROM {t} WHERE user_id = %s", (uid,))


def register(client, t, token, env="production", lang=None):
    r = client.post("/v1/users/me/push-devices", json={"token": token, "environment": env, "app_version": "1.0.7",
                                                         **({"lang": lang} if lang else {})}, headers=H(t))
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------------------ devices
def test_device_registration_only_for_yourself(client, dsn, users, fake):
    ids, t = users
    reset(dsn, ids["a"]); reset(dsn, ids["b"])
    d = tok()
    assert register(client, t["a"], d)["push_configured"] is True
    # someone else's name, a read-only token, a malformed token: refused
    assert client.post(f"/v1/users/{ids['b']}/push-devices", json={"token": tok(), "environment": "production"},
                       headers=H(t["a"])).status_code == 403
    assert client.post("/v1/users/me/push-devices", json={"token": tok(), "environment": "production"},
                       headers=H(t["a_ro"])).status_code == 403
    assert client.post("/v1/users/me/push-devices", json={"token": "z" * 64, "environment": "production"},
                       headers=H(t["a"])).status_code == 422
    assert client.post("/v1/users/me/push-devices", json={"token": tok(), "environment": "dev"},
                       headers=H(t["a"])).status_code == 422
    # nobody can list or delete another user's devices
    assert client.get(f"/v1/users/{ids['a']}/push-devices", headers=H(t["b"])).status_code == 403
    assert client.request("DELETE", f"/v1/users/{ids['a']}/push-devices", json={"token": d},
                          headers=H(t["b"])).status_code == 403
    assert client.request("DELETE", "/v1/users/me/push-devices", json={"token": d}, headers=H(t["b"])).json()["removed"] == 0
    devs = client.get("/v1/users/me/push-devices", headers=H(t["a"])).json()["devices"]
    assert [x["token_tail"] for x in devs] == [d[-6:]] and "token" not in devs[0]
    # the same phone signed in as b now: the token moves to b (one app install = one account)
    register(client, t["b"], d)
    assert client.get("/v1/users/me/push-devices", headers=H(t["a"])).json()["devices"] == []
    assert len(client.get("/v1/users/me/push-devices", headers=H(t["b"])).json()["devices"]) == 1
    assert client.request("DELETE", "/v1/users/me/push-devices", json={"token": d.upper()}, headers=H(t["b"])).json()["removed"] == 1


def test_sign_out_removes_the_device(client, dsn, users, fake):
    ids, t = users
    reset(dsn, ids["a"])
    with conn_of(dsn) as conn:
        out = auth.create_token(conn, ids["a"], "app-out", ["read", "write"])
    keep, gone = tok(), tok()
    register(client, t["a"], keep)
    register(client, out, gone)
    # DELETE without a token = the devices of the Bearer token used
    assert client.request("DELETE", "/v1/users/me/push-devices", headers=H(out)).json()["removed"] == 1
    register(client, out, gone)
    assert client.post("/v1/account/logout", headers=H(out)).status_code == 204
    assert [x["token_tail"] for x in client.get("/v1/users/me/push-devices", headers=H(t["a"])).json()["devices"]] == [keep[-6:]]
    # a token revoked some other way (admin, account settings): its devices are dropped before the next push
    register(client, t["a2"], gone)
    with conn_of(dsn) as conn:
        conn.execute("UPDATE api_tokens SET revoked_at = now() WHERE user_id = %s AND name = 'app2'", (ids["a"],))
    with conn_of(dsn) as conn:
        notify(conn, ids["a"], "circle.like", "x", "", now=NOON)
    assert [s["token"] for s in fake.sent] == [keep]


# ------------------------------------------------------------------------------------------------ notify()
def test_no_key_writes_in_app_only(client, dsn, users):
    ids, t = users
    reset(dsn, ids["a"])
    core.set_client(None)
    register(client, t["a"], tok())
    with conn_of(dsn) as conn:
        r = notify(conn, ids["a"], "circle.comment", "小明评论了你的训练", "练得不错", url="/app/#tab=friends", now=NOON)
        row = conn.execute("SELECT * FROM notifications WHERE id = %s", (r["id"],)).fetchone()
    assert r["push"] == "no_key" and row["title"] == "小明评论了你的训练" and row["read_at"] is None
    assert client.get("/v1/users/me/notifications/unread", headers=H(t["a"])).json() == {"unread": 1}


def test_push_payload_and_language(client, dsn, users, fake):
    ids, t = users
    reset(dsn, ids["b"])
    d = tok()
    register(client, t["b"], d, env="sandbox")
    with conn_of(dsn) as conn:
        r = notify(conn, ids["b"], "circle.like", {"zh": "小明给你点了 ❤️", "en": "Ming ❤️ your workout"}, "",
                   url="/app/#tab=friends&post=9", data={"actor_id": 7}, now=NOON)
    assert r["push"] == "pushed"
    (s,) = fake.sent
    assert s["token"] == d and s["env"] == "sandbox"
    aps = s["payload"]["aps"]
    assert aps["alert"] == {"title": "Ming ❤️ your workout"}   # b's profile language is English
    assert aps["sound"] == "default" and aps["badge"] == 1 and aps["interruption-level"] == "active"
    assert s["payload"]["url"] == "/app/#tab=friends&post=9" and "actor_id" not in json.dumps(s["payload"])
    assert s["headers"]["apns-push-type"] == "alert" and s["headers"]["apns-collapse-id"] == "circle.like"
    # the in-app list follows the page's language
    zh = client.get("/v1/users/me/notifications", headers=H(t["b"], "zh")).json()
    en = client.get("/v1/users/me/notifications", headers=H(t["b"], "en")).json()
    assert zh["items"][0]["title"] == "小明给你点了 ❤️" and en["items"][0]["title"] == "Ming ❤️ your workout"


def test_quiet_hours_and_settings(client, dsn, users, fake):
    ids, t = users
    reset(dsn, ids["a"])
    register(client, t["a"], tok())
    with conn_of(dsn) as conn:
        assert notify(conn, ids["a"], "circle.like", "x", "", now=datetime(2026, 10, 22, 23, 30, tzinfo=TZ))["push"] == "quiet"
        assert notify(conn, ids["a"], "circle.like", "x", "", now=datetime(2026, 10, 23, 6, 59, tzinfo=TZ))["push"] == "quiet"
        # the user's own time zone counts: 13:00 UTC is 00:00 in Sydney
        assert notify(conn, ids["a"], "system", "x", "", now=datetime(2026, 10, 22, 13, 0, tzinfo=ZoneInfo("UTC")))["push"] == "quiet"
    assert fake.sent == []
    assert core.in_quiet_hours(datetime(2026, 1, 1, 7, 0).time(), True, P.QUIET_START, P.QUIET_END) is False
    assert core.in_quiet_hours(datetime(2026, 1, 1, 23, 0).time(), False, P.QUIET_START, P.QUIET_END) is False
    # custom quiet hours and a category switched off
    r = client.put("/v1/users/me/notification-settings", headers=H(t["a"]),
                   json={"categories": {"circle": False}, "quiet": {"start": "12:00", "end": "13:00"}})
    assert r.status_code == 200, r.text
    assert r.json()["categories"] == {"circle": False, "system": True, "updates": True} and r.json()["quiet"]["start"] == "12:00"
    assert r.json()["labels"]["circle"] == "圈子互动"
    with conn_of(dsn) as conn:
        assert notify(conn, ids["a"], "circle.like", "x", "", now=NOON)["push"] == "off"
        assert notify(conn, ids["a"], "system", "x", "", now=NOON)["push"] == "quiet"
        # outside the custom hours: one push that also counts the system messages held back in quiet hours, with a sound
        assert notify(conn, ids["a"], "system", "x", "", now=datetime(2026, 10, 22, 23, 30, tzinfo=TZ))["push"] == "merged"
    (s,) = fake.sent
    assert s["payload"]["aps"]["alert"]["title"] == "3 条新的系统消息" and s["payload"]["aps"]["sound"] == "default"
    assert client.put("/v1/users/me/notification-settings", json={"categories": {"nope": True}}, headers=H(t["a"])).status_code == 422
    assert client.put("/v1/users/me/notification-settings", json={"quiet": {"start": "25:00"}}, headers=H(t["a"])).status_code == 422
    assert client.put(f"/v1/users/{ids['b']}/notification-settings", json={}, headers=H(t["a"])).status_code == 403


def test_merging(client, dsn, users, fake):
    ids, t = users
    reset(dsn, ids["a"])
    register(client, t["a"], tok())
    with conn_of(dsn) as conn:
        results = [notify(conn, ids["a"], "circle.like", f"{n} 给你点了 ❤️", "", url="/app/#tab=friends", data={"actor_id": i}, now=NOON)
                   for i, n in enumerate(["小明", "小红", "小刚"])]
        # the same person twice more: still 3 people
        notify(conn, ids["a"], "circle.like", "小刚 给你点了 ❤️", "", url="/app/#tab=friends", data={"actor_id": 2}, now=NOON)
    assert [r["push"] for r in results] == ["pushed", "merged", "merged"]
    first, second, third, fourth = fake.sent
    assert first["payload"]["aps"]["alert"]["title"] == "小明 给你点了 ❤️" and first["payload"]["aps"]["sound"] == "default"
    assert third["payload"]["aps"]["alert"]["title"] == "3 个人给你点了 ❤️"
    assert fourth["payload"]["aps"]["alert"]["title"] == "3 个人给你点了 ❤️"
    assert second["payload"]["aps"]["alert"]["title"] == "2 个人给你点了 ❤️"
    for s in (second, third, fourth):   # updates the one notification on the phone, silently
        assert "sound" not in s["payload"]["aps"] and s["payload"]["aps"]["interruption-level"] == "passive"
        assert s["headers"]["apns-collapse-id"] == first["headers"]["apns-collapse-id"] and s["headers"]["apns-priority"] == "5"
    assert fourth["payload"]["aps"]["badge"] == 4 and fourth["payload"]["url"] == "/app/#tab=friends"
    # another kind is not merged with likes; without actors the count is of notifications
    with conn_of(dsn) as conn:
        assert notify(conn, ids["a"], "circle.comment", "小明评论了你", "", now=NOON)["push"] == "pushed"
        assert notify(conn, ids["a"], "circle.comment", "小明又评论了你", "", url="/app/#x", now=NOON)["push"] == "merged"
    assert fake.sent[-1]["payload"]["aps"]["alert"]["title"] == "2 条新的圈子互动"
    assert fake.sent[-1]["payload"]["url"] == "/app/#notifications"   # different links -> the list
    # after reading, the next one starts fresh with a sound
    assert client.post("/v1/users/me/notifications/read", json={"kind": "circle"}, headers=H(t["a"])).json()["unread"] == 0
    with conn_of(dsn) as conn:
        assert notify(conn, ids["a"], "circle.like", "小明 给你点了 ❤️", "", data={"actor_id": 0}, now=NOON)["push"] == "pushed"
    assert fake.sent[-1]["payload"]["aps"]["sound"] == "default"
    # a flood: after MAX_PUSHES_PER_WINDOW pushes the rest stay in the app
    n0 = len(fake.sent)
    with conn_of(dsn) as conn:
        out = [notify(conn, ids["a"], "circle.like", "x", "", data={"actor_id": i}, now=NOON)["push"] for i in range(P.MAX_PUSHES_PER_WINDOW + 2)]
    assert out[-1] == "merged_quiet" and len(fake.sent) - n0 == P.MAX_PUSHES_PER_WINDOW - 1


def test_english_merged_text(client, dsn, users, fake):
    ids, t = users
    reset(dsn, ids["b"])
    register(client, t["b"], tok())
    with conn_of(dsn) as conn:
        for i in range(3):
            notify(conn, ids["b"], "circle.like", "x", "", data={"actor_id": i}, now=NOON)
    assert fake.sent[-1]["payload"]["aps"]["alert"] == {"title": "3 people ❤️ your workout", "body": "Tap to see them"}


def test_invalid_tokens_are_removed(client, dsn, users, fake):
    ids, t = users
    reset(dsn, ids["a"])
    gone, bad, wrong_env, ok = tok(), tok(), tok(), tok()
    for d in (gone, bad, wrong_env, ok):
        register(client, t["a"], d)
    fake.answers[(gone, "production")] = apns.Result(410, "Unregistered")
    fake.answers[(bad, "production")] = apns.Result(400, "BadDeviceToken")
    fake.answers[(bad, "sandbox")] = apns.Result(400, "BadDeviceToken")
    fake.answers[(wrong_env, "production")] = apns.Result(400, "BadDeviceToken")   # a development build: sandbox works
    fake.answers[(ok, "production")] = apns.Result(500, "InternalServerError")    # Apple trouble: keep the device
    with conn_of(dsn) as conn:
        notify(conn, ids["a"], "system", "x", "", now=NOON)
        left = {r["token"]: r for r in conn.execute("SELECT token, environment, last_status FROM push_devices WHERE user_id = %s",
                                                    (ids["a"],)).fetchall()}
    assert set(left) == {wrong_env, ok}
    assert left[wrong_env]["environment"] == "sandbox" and left[wrong_env]["last_status"] == "200"
    assert left[ok]["last_status"] == "500 InternalServerError"


def test_flag_off_unknown_kind_and_validation(client, dsn, users, fake):
    ids, t = users
    reset(dsn, ids["c"])
    register(client, t["c"], tok())   # registering works without the flag (the app may ask before the page is on)
    with conn_of(dsn) as conn:
        assert notify(conn, ids["c"], "circle.like", "x", "", now=NOON)["push"] == "flag_off"
        with pytest.raises(ValueError):
            notify(conn, ids["c"], "cirle.like", "x", "")
        with pytest.raises(ValueError):
            notify(conn, ids["c"], "circle.like", "x", "", url="https://evil.example/")
        with pytest.raises(ValueError):
            notify(conn, ids["c"], "circle.like", "", "")
        with pytest.raises(ValueError):
            notify(conn, ids["c"], "circle.like", "x", "", data={"big": "x" * 3000})
    assert fake.sent == []
    for path in ("/v1/users/me/notifications", "/v1/users/me/notifications/unread", "/v1/users/me/notification-settings"):
        assert client.get(path, headers=H(t["c"])).status_code == 404
    assert client.request("DELETE", "/v1/users/me/push-devices", headers=H(t["c"])).status_code == 200


def test_list_paging_and_mark_read(client, dsn, users, fake):
    ids, t = users
    reset(dsn, ids["a"])
    with conn_of(dsn) as conn:
        nids = [notify(conn, ids["a"], "system", f"n{i}", "body", now=NOON)["id"] for i in range(5)]
        notify(conn, ids["b"], "system", "not yours", "", now=NOON)
    page = client.get("/v1/users/me/notifications?limit=3", headers=H(t["a"])).json()
    assert [x["title"] for x in page["items"]] == ["n4", "n3", "n2"] and page["more"] is True and page["unread"] == 5
    rest = client.get(f"/v1/users/me/notifications?before={page['items'][-1]['id']}", headers=H(t["a"])).json()
    assert [x["title"] for x in rest["items"]] == ["n1", "n0"] and rest["more"] is False
    assert client.get(f"/v1/users/{ids['b']}/notifications", headers=H(t["a"])).status_code == 403
    r = client.post("/v1/users/me/notifications/read", json={"ids": nids[:2]}, headers=H(t["a"])).json()
    assert r == {"marked": 2, "unread": 3}
    assert client.post("/v1/users/me/notifications/read", json={"ids": nids[:2]}, headers=H(t["a_ro"])).status_code == 403
    assert client.post("/v1/users/me/notifications/read", json={}, headers=H(t["a"])).json()["unread"] == 0
    assert client.get("/v1/users/me/notifications/unread", headers=H(t["b"])).json()["unread"] >= 1   # b's own untouched


# ------------------------------------------------------------------------------------------------ APNs details
def test_provider_token_is_a_valid_es256_jwt():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    cfg = apns.Config(pem, "ABC123DEFG", "TEAM123456", "org.example.app")
    jwt = apns.provider_token(cfg, now=1_800_000_000)
    head, claims, sig = jwt.split(".")
    dec = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
    assert json.loads(dec(head)) == {"alg": "ES256", "kid": "ABC123DEFG"}
    assert json.loads(dec(claims)) == {"iss": "TEAM123456", "iat": 1_800_000_000}
    raw = dec(sig)
    assert len(raw) == 64
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))   # raises if wrong


def test_config_needs_every_part(tmp_path, monkeypatch):
    key = tmp_path / "k.p8"
    key.write_text("placeholder")   # load_config only checks that the file exists
    monkeypatch.setenv("BODY_APNS_KEY_FILE", str(key))
    for k in ("KEY_ID", "TEAM_ID", "BUNDLE_ID"):
        monkeypatch.delenv(f"BODY_APNS_{k}", raising=False)
    assert apns.load_config() is None
    monkeypatch.setenv("BODY_APNS_KEY_ID", "K"); monkeypatch.setenv("BODY_APNS_TEAM_ID", "T")
    assert apns.load_config() is None
    monkeypatch.setenv("BODY_APNS_BUNDLE_ID", "org.example.app")
    assert apns.load_config().bundle_id == "org.example.app"
    monkeypatch.setenv("BODY_APNS_KEY_FILE", str(tmp_path / "missing.p8"))
    assert apns.load_config() is None
    assert apns.Result(410).invalid_token and apns.Result(400, "BadDeviceToken").invalid_token
    assert not apns.Result(400, "PayloadTooLarge").invalid_token and not apns.Result(500).invalid_token


def test_background_sending(client, dsn, users):
    """Production path: the push goes out from the worker thread with its own connection (api.pool)."""
    ids, t = users
    reset(dsn, ids["a"])
    gone = tok()
    register(client, t["a"], gone)
    f = FakeApns()
    f.answers[(gone, "production")] = apns.Result(410, "Unregistered")
    core.set_client(f, sync=False)
    try:
        with conn_of(dsn) as conn:
            assert notify(conn, ids["a"], "system", "x", "", now=NOON)["push"] == "pushed"
        core.drain(10)
    finally:
        core.set_client(None)
    assert len(f.sent) == 1
    with conn_of(dsn) as conn:
        assert conn.execute("SELECT count(*) AS n FROM push_devices WHERE user_id = %s", (ids["a"],)).fetchone()["n"] == 0


def test_admin_test_send(dsn, users, fake):
    from body_engine.notify import admin
    ids, _ = users
    reset(dsn, ids["c"])   # flag off for c: the test push still goes out
    with conn_of(dsn) as conn:
        from body_engine.notify import store
        store.register_device(conn, ids["c"], tok(), "production", None)
        out = admin.test(conn, ids["c"])
        assert [s["status"] for s in out["sent"]] == [200] and len(fake.sent) == 1
        assert admin.status(conn)["apns_configured"] is True
        assert store.unread(conn, ids["c"]) == 1


# ------------------------------------------------------------------------------------------------ page script (notify.js, node)
def test_page_links_and_times():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = Path(__file__).resolve().parents[1] / "src/body_engine/web/static/notify.js"
    probe = ("globalThis.window = { I18N: { t: (k, p) => k.replace(/\\{(\\w+)\\}/g, (_, n) => (p || {})[n]), locale: () => 'zh-CN' } };\n"
             f"const P = require({json.dumps(str(js))});\n"
             "const now = Date.parse('2026-10-22T12:00:00Z');\n"
             "console.log(JSON.stringify({a: P.parseLink('/app/#tab=friends&post=9'), b: P.parseLink('/app/#notifications'),"
             " c: P.parseLink('/app/'), d: P.parseLink('/app/?tab=friends&feed=3'), t: ['2026-10-22T11:59:30Z', '2026-10-22T11:55:00Z', '2026-10-22T09:00:00Z',"
             " '2026-10-20T12:00:00Z'].map((x) => P.timeAgo(x, now))}));")
    out = json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)
    assert out["a"] == {"tab": "friends", "post": "9"} and out["b"] == {"notifications": ""} and out["c"] == {}
    assert out["d"] == {"tab": "friends", "feed": "3"}   # the circle feed's links use the query (friends/feed.py)
    assert out["t"] == ["刚刚", "5 分钟前", "3 小时前", "2 天前"]


def test_circle_feed_kinds(client, dsn, users, fake):
    """The circle feed (#176, friends/feed.py) sends circle.like / circle.comment with ?tab=friends links."""
    ids, t = users
    reset(dsn, ids["a"])
    register(client, t["a"], tok())
    assert core.category_of("circle_feed_like") == "circle" and core.category_of("circle.like") == "circle"
    assert core.category_of("circlex") == "circlex"
    with conn_of(dsn) as conn:
        r = notify(conn, ids["a"], "circle.like", "小明 给你的训练点了 ❤️", "今天的训练动态",
                   url="/app/?tab=friends&feed=3", data={"post_id": 3, "actor_id": 9}, now=NOON)
        notify(conn, ids["a"], "circle.like", "小红 给你的训练点了 ❤️", "", url="/app/?tab=friends&feed=3",
               data={"post_id": 3, "actor_id": 8}, now=NOON)
    assert r["push"] == "pushed" and fake.sent[0]["payload"]["url"] == "/app/?tab=friends&feed=3"
    assert fake.sent[-1]["payload"]["aps"]["alert"]["title"] == "2 个人给你点了 ❤️"
    # switching 圈子互动 off covers them; marking the category read too
    client.put("/v1/users/me/notification-settings", json={"categories": {"circle": False}}, headers=H(t["a"]))
    with conn_of(dsn) as conn:
        assert notify(conn, ids["a"], "circle.comment", "x", "y", now=NOON)["push"] == "off"
    assert client.post("/v1/users/me/notifications/read", json={"kind": "circle"}, headers=H(t["a"])).json() == {"marked": 3, "unread": 0}
