"""Reusable activation codes (web/activation.py): redeem many times, revoke / expiry, OW invitation minting (HTTP mocked),
the JWT signature, behaviour without OW config, the per-IP failure limit, and the activation page under the CSP.

Uses its own test database (body_test_activation), dropped and recreated on every run; skipped if Postgres is unreachable.
No real Open Wearables server is contacted.
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.web import activation, login_codes, login_limit

TEST_DB = "body_test_activation"
OW_USER = "0b7a3c52-6f0e-4b8e-9a51-3f2d1c0e9b77"
DEV_ID = "5d1e2f3a-4b5c-4d6e-8f70-819293a4b5c6"
SECRET = "test-ow-secret-key"


@pytest.fixture(scope="module")
def dsn():
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # no password file, or the database is not running
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
        synced = conn.execute("INSERT INTO users (display_name, ow_user_id) VALUES ('S', %s) RETURNING id",
                              (OW_USER,)).fetchone()["id"]
        plain = conn.execute("INSERT INTO users (display_name) VALUES ('P') RETURNING id").fetchone()["id"]
    return {"synced": synced, "plain": plain}


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    login_limit.reset()
    monkeypatch.setenv("BODY_PUBLIC_URL", "https://body.example.com/")
    monkeypatch.setenv("BODY_OW_PUBLIC_URL", "https://sync.example.com")
    monkeypatch.delenv("BODY_OW_INVITE_SIGNING_FILE", raising=False)
    yield
    login_limit.reset()


@pytest.fixture
def ow(monkeypatch, tmp_path):
    """OW signing configured; the HTTP call is captured and answers like OW (201 UserInvitationCodeRead)."""
    f = tmp_path / "ow_invite_signing.json"
    f.write_text(json.dumps({"secret_key": SECRET, "algorithm": "HS256", "developer_id": DEV_ID}))
    monkeypatch.setenv("BODY_OW_INVITE_SIGNING_FILE", str(f))
    calls = []
    codes = iter(["ABCDEFGH", "JKLMNPQR", "STUVWXYZ", "23456789"])

    def fake_post(url, headers, timeout):
        calls.append({"url": url, "headers": headers})
        return {"id": "x", "code": next(codes), "user_id": OW_USER, "expires_at": "2026-10-26T00:00:00Z",
                "created_at": "2026-10-19T00:00:00Z"}
    monkeypatch.setattr(activation, "_post_json", fake_post)
    return calls


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _params(link: str) -> dict:
    assert link.startswith("watch2whoop://setup?")
    return dict(urllib.parse.parse_qsl(link.split("?", 1)[1]))


def _new(dsn, user_id, **kw) -> dict:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return activation.create(conn, user_id, **kw)


def test_redeem_many_times_with_ow(client, users, dsn, ow):
    res = _new(dsn, users["synced"], label="Mum")
    code = res["code"]
    assert len(code) == activation.LENGTH and set(code) <= set(activation.ALPHABET)
    logins = []
    for i in range(3):
        pretty = f"{code[:4].lower()}-{code[4:8]} {code[8:]}"
        r = client.post("/v1/web/activate", json={"code": pretty if i == 1 else code})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sync"] is True and body["note"] is None
        p = _params(body["setup_link"])
        assert p["today"] == "https://body.example.com" and p["ow"] == "https://sync.example.com"
        assert p["code"] == ["ABCDEFGH", "JKLMNPQR", "STUVWXYZ"][i]
        assert len(p["login"]) == login_codes.LENGTH
        logins.append(p["login"])
    assert len(set(logins)) == 3
    assert r.headers["cache-control"] == "private, no-store"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("SELECT uses, last_used_at FROM activation_codes WHERE id = %s", (res["id"],)).fetchone()
        assert row["uses"] == 3 and row["last_used_at"] is not None
        lc = conn.execute("SELECT label, expires_at - created_at AS ttl FROM web_login_codes WHERE code_sha256 = %s",
                          (login_codes._sha(logins[0]),)).fetchone()
        assert lc["label"] == "activation:Mum" and lc["ttl"].total_seconds() <= 3600
    # The minted login code works like any one-time login code
    client.cookies.clear()
    assert client.post("/v1/web/login-code", json={"code": logins[-1]}).status_code == 200
    assert client.get("/v1/web/me").json()["me"]["id"] == users["synced"]
    client.cookies.clear()

    # The OW call: right URL, developer JWT verifiable with the OW secret, short-lived
    assert ow[0]["url"].endswith(f"/api/v1/users/{OW_USER}/invitation-code")
    token = ow[0]["headers"]["Authorization"].removeprefix("Bearer ")
    head, payload, sig = token.split(".")
    expected = hmac.new(SECRET.encode(), f"{head}.{payload}".encode(), hashlib.sha256).digest()
    assert hmac.compare_digest(_b64d(sig), expected)
    assert json.loads(_b64d(head)) == {"alg": "HS256", "typ": "JWT"}
    claims = json.loads(_b64d(payload))
    assert claims["sub"] == DEV_ID and 0 < claims["exp"] - time.time() <= activation.OW_JWT_TTL_SECONDS


def test_revoked_and_expired_are_401(client, users, dsn):
    revoked = _new(dsn, users["plain"], label="old")
    expired = _new(dsn, users["plain"], days=1)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert activation.revoke(conn, revoked["id"]) is True
        assert activation.revoke(conn, revoked["id"]) is False
        conn.execute("UPDATE activation_codes SET expires_at = now() - interval '1 minute' WHERE id = %s", (expired["id"],))
        listed = {r["id"]: r for r in activation.list_codes(conn, users["plain"])}
        assert listed[revoked["id"]]["active"] is False and listed[expired["id"]]["active"] is False
        assert "code_sha256" not in listed[revoked["id"]]
    for c in (revoked["code"], expired["code"], "ABCDEFGHJKLMNPQR", "SHORT123"):
        r = client.post("/v1/web/activate", json={"code": c})
        assert r.status_code == 401 and r.json()["detail"] == "激活码无效或已停用"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT uses FROM activation_codes WHERE id = %s", (revoked["id"],)).fetchone()["uses"] == 0
    with pytest.raises(ValueError), psycopg.connect(dsn, row_factory=dict_row) as conn:
        activation.create(conn, users["plain"], days=0)


def test_without_ow_link_has_no_code(client, users, dsn, monkeypatch):
    """No ow_user_id or no signing file: still a valid link (today + ow + login), without `code`; the app asks for it later."""
    no_user = _new(dsn, users["plain"])["code"]
    r = client.post("/v1/web/activate", json={"code": no_user})
    assert r.status_code == 200
    assert r.json()["sync"] is False and r.json()["note"] == "no_ow_user"
    p = _params(r.json()["setup_link"])
    assert "code" not in p and p["login"] and p["ow"] == "https://sync.example.com"

    unsigned = _new(dsn, users["synced"])["code"]
    r = client.post("/v1/web/activate", json={"code": unsigned})
    assert r.json()["sync"] is False and r.json()["note"] == "ow_signing_not_configured"
    assert "code" not in _params(r.json()["setup_link"])

    # Without BODY_PUBLIC_URL the request's own address is used for `today`
    monkeypatch.delenv("BODY_PUBLIC_URL")
    monkeypatch.delenv("BODY_OW_PUBLIC_URL")
    p = _params(client.post("/v1/web/activate", json={"code": unsigned}).json()["setup_link"])
    assert p["today"] == "https://testserver" and "ow" not in p


def test_ow_failure_still_gives_a_link(client, users, dsn, ow, monkeypatch):
    def down(url, headers, timeout):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(activation, "_post_json", down)
    code = _new(dsn, users["synced"])["code"]
    r = client.post("/v1/web/activate", json={"code": code})
    assert r.status_code == 200 and r.json()["sync"] is False and r.json()["note"] == "ow_unavailable"


def test_failures_are_rate_limited(client, users, dsn, monkeypatch):
    monkeypatch.setattr(login_limit, "MAX_FAILURES", 3)
    good = _new(dsn, users["plain"])["code"]
    for _ in range(3):
        assert client.post("/v1/web/activate", json={"code": "ABCDEFGHJKLMNPQR"}).status_code == 401
    r = client.post("/v1/web/activate", json={"code": good})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # Token login shares the same per-IP counter
    assert client.post("/v1/web/login", json={"token": "be_wrong_wrong"}).status_code == 429


def test_success_does_not_reset_failures(client, users, dsn, monkeypatch):
    monkeypatch.setattr(login_limit, "MAX_FAILURES", 3)
    good = _new(dsn, users["plain"])["code"]
    for _ in range(2):
        assert client.post("/v1/web/activate", json={"code": "ABCDEFGHJKLMNPQR"}).status_code == 401
    assert client.post("/v1/web/activate", json={"code": good}).status_code == 200
    assert client.post("/v1/web/activate", json={"code": "ABCDEFGHJKLMNPQR"}).status_code == 401
    assert client.post("/v1/web/activate", json={"code": good}).status_code == 429


def test_token_revoke_all_revokes_activation_codes(client, users, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = conn.execute("INSERT INTO users (display_name) VALUES ('R') RETURNING id").fetchone()["id"]
        code = activation.create(conn, uid)["code"]
        assert auth.revoke_tokens(conn, uid, everything=True)["activation_codes"] == 1
    assert client.post("/v1/web/activate", json={"code": code}).status_code == 401


def test_activation_page_served_with_csp(client):
    r = client.get("/app/activate.html")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    html = r.text
    assert "激活 Watch2Whoop" in html and "打开 App 完成设置" in html
    assert '<script src="activate.js"></script>' in html
    assert "<script>" not in html                     # no inline script (blocked by the CSP)
    js = client.get("/app/activate.js")
    assert js.status_code == 200 and "/v1/web/activate" in js.text and "location.hash" in js.text


def test_sign_jwt_rejects_unknown_algorithm():
    with pytest.raises(ValueError):
        activation.sign_jwt({"sub": "x"}, "k", "RS256")
