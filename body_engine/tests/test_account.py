"""Self-service sign-up (account/): e-mail codes (console mailer), Sign in with Apple against a fake JWKS, pending gate,
invite batches and redemption, provisioning with fake Open Wearables / SparkyFitness, the OW invitation and WebView
login-code endpoints, and account deletion.

Uses its own test database (body_test_account), dropped and recreated on every run; skipped if Postgres is unreachable.
Nothing leaves the machine: no mail is sent, Apple / OW / SparkyFitness are fakes.
"""
import base64
import hashlib
import json
import time
import uuid

import psycopg
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.account import api as account_api, delete as account_delete, invites, mailer, provision, signin
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.web import activation, login_limit

TEST_DB = "body_test_account"
AUD = "com.example.watch2whoop"
WEB = {"X-Body-Web": "1"}


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
        conn.execute("INSERT INTO users (display_name, role) VALUES ('Owner', 'owner')")
    return dsn


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


# ---------------------------------------------------------------------------------------------------- fakes

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _jwks():
    n = KEY.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": "K1", "alg": "RS256", "use": "sig",
                      "n": _b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")), "e": _b64(n.e.to_bytes(3, "big"))}]}


def apple_token(sub="apple-sub-1", email="someone@example.com", nonce="raw-nonce", aud=AUD, exp_in=600, kid="K1",
                key=KEY, iss=signin.APPLE_ISSUER, verified=True):
    head = _b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
    claims = {"iss": iss, "aud": aud, "sub": sub, "iat": int(time.time()), "exp": int(time.time()) + exp_in,
              "email": email, "email_verified": "true" if verified else "false"}
    if nonce is not None:
        claims["nonce"] = hashlib.sha256(nonce.encode()).hexdigest()
    body = _b64(json.dumps(claims).encode())
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(sig)}"


class FakeOW:
    def __init__(self):
        self.created, self.deleted, self.users = [], [], {}

    def get(self, path, **params):
        ext = params.get("external_user_id")
        return {"items": [{"id": i} for i, e in self.users.items() if e == ext], "total": 0, "page": 1, "limit": 20}

    def request(self, method, path, params=None, body=None):
        if method == "POST":
            uid = str(uuid.uuid4())
            self.users[uid] = body["external_user_id"]
            self.created.append(body)
            return {"id": uid}
        if method == "DELETE":
            self.deleted.append(path)
            return {}
        raise AssertionError(method)


class FakeSparky:
    def __init__(self, fail=False):
        self.fail, self.accounts, self.keys = fail, [], []

    def ensure_account(self, email, password, name):
        if self.fail:
            raise RuntimeError("SparkyFitness sign-up 400: Signups are currently disabled by the administrator.")
        self.accounts.append((email, password))
        return "sparky-" + email.split("@")[0]

    def api_key(self, scope, name):
        self.keys.append(scope)
        return f"key-{scope}-{len(self.keys)}"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    login_limit.reset()
    signin.reset_jwks_cache()
    mailer.OUTBOX.clear()
    monkeypatch.setenv("BODY_MAIL_MODE", "console")
    monkeypatch.setenv("BODY_APPLE_AUDIENCES", AUD + ",com.example.other")
    master = tmp_path / "ai_master_key"
    master.write_text(base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("BODY_AI_MASTER_KEY_FILE", str(master))
    monkeypatch.setattr(signin, "_fetch_jwks", _jwks)
    ow, sparky = FakeOW(), FakeSparky()
    monkeypatch.setattr(provision, "OWClient", lambda **kw: ow)
    monkeypatch.setattr(provision, "SparkySignup", lambda **kw: sparky)
    # provisioning runs synchronously in tests (the real endpoint uses a background thread)
    monkeypatch.setattr(account_api, "start_provisioning", account_api._provision_in_background)
    yield {"ow": ow, "sparky": sparky}
    login_limit.reset()


def _email_login(client, email, client_kind="web", device=None):
    assert client.post("/v1/auth/email/start", json={"email": email}).status_code == 200
    code = mailer.OUTBOX[-1][2].split("：")[1].split("\n")[0].strip()
    r = client.post("/v1/auth/email/verify", json={"email": email, "code": code, "client": client_kind, "device": device})
    assert r.status_code == 200, r.text
    return r.json()


def _batch(dsn, count=1, **kw):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return invites.create_batch(conn, count, kw.pop("label", "test"), **kw)


# ---------------------------------------------------------------------------------------------------- e-mail sign-in

def test_email_code_signs_in_pending_web_user(client, dsn):
    client.cookies.clear()
    r = client.post("/v1/auth/email/start", json={"email": "  New.Person@Example.com ", "lang": "en"})
    assert r.status_code == 200 and r.json() == {"sent": True, "expires_in": 600}
    to, subject, body = mailer.OUTBOX[-1]
    assert to == "new.person@example.com" and "sign-in code" in subject and "验证码" in body
    assert body.startswith("Your Watch2Whoop")   # lang en: English first
    code = subject.rsplit(" ", 1)[1]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:   # only a salted hash is stored
        row = conn.execute("SELECT * FROM email_login_codes WHERE email = 'new.person@example.com'").fetchone()
        assert code not in json.dumps(row, default=str)
    r = client.post("/v1/auth/email/verify", json={"email": "NEW.person@example.com", "code": code})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "pending" and out["new_user"] is True and "token" not in out
    assert client.get("/v1/web/me").json()["me"]["status"] == "pending"
    acct = client.get("/v1/account").json()
    assert acct["invite_required"] is True and acct["identities"] == [{"provider": "email", "email": "new.person@example.com"}]
    r = client.get("/v1/users/me/today")
    assert r.status_code == 403 and r.json()["detail"] == "account_pending"
    assert client.get("/v1/users/me/profile").status_code == 403
    # the code is single-use; signing in again finds the same user
    assert client.post("/v1/auth/email/verify", json={"email": "new.person@example.com", "code": code}).status_code == 401
    account_api._resend.reset()   # past the 60 s resend cooldown
    again = _email_login(client, "new.person@example.com")
    assert again["new_user"] is False and again["me"]["id"] == out["me"]["id"]
    client.cookies.clear()


def test_email_code_attempts_and_rate_limits(client, monkeypatch):
    client.cookies.clear()
    assert client.post("/v1/auth/email/start", json={"email": "guess@example.com"}).status_code == 200
    code = mailer.OUTBOX[-1][1].rsplit(" ", 1)[1]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(signin.MAX_ATTEMPTS):
        assert client.post("/v1/auth/email/verify", json={"email": "guess@example.com", "code": wrong}).status_code == 401
    # dead after 5 wrong guesses, even with the right code
    assert client.post("/v1/auth/email/verify", json={"email": "guess@example.com", "code": code}).status_code == 401
    # resend cooldown, then per-address cap
    r = client.post("/v1/auth/email/start", json={"email": "guess@example.com"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # per-IP failure limit (shared with token login)
    monkeypatch.setattr(login_limit, "MAX_FAILURES", 3)
    r = client.post("/v1/auth/email/verify", json={"email": "guess@example.com", "code": code})
    assert r.status_code == 429
    login_limit.reset()
    assert client.post("/v1/auth/email/start", json={"email": "not-an-email"}).status_code == 400
    monkeypatch.setenv("BODY_MAIL_MODE", "off")
    assert client.post("/v1/auth/email/start", json={"email": "x@example.com"}).status_code == 503


def test_new_code_voids_old_one(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        first = signin.new_email_code(conn, "two@example.com")
        second = signin.new_email_code(conn, "two@example.com")
        assert not signin.check_email_code(conn, "two@example.com", first) or first == second
        assert signin.check_email_code(conn, "two@example.com", second)


def test_mail_message_and_smtp_requirements(monkeypatch):
    subject, body = mailer.code_message("654321", 10, "zh")
    assert "654321" in subject and body.index("验证码") < body.index("sign-in code")
    monkeypatch.setenv("BODY_MAIL_MODE", "smtp")
    monkeypatch.delenv("BODY_SMTP_HOST", raising=False)
    with pytest.raises(mailer.MailNotConfigured):
        mailer.send("a@example.com", "s", "b")


# ---------------------------------------------------------------------------------------------------- Apple

def test_apple_token_verification():
    claims = signin.verify_apple_token(apple_token(), [AUD], "raw-nonce")
    assert claims["sub"] == "apple-sub-1"
    assert signin.apple_email(claims) == ("someone@example.com", True)
    bad = [
        (apple_token(), "other-nonce"),
        (apple_token(nonce=None), "raw-nonce"),
        (apple_token(aud="com.evil.app"), "raw-nonce"),
        (apple_token(exp_in=-3600), "raw-nonce"),
        (apple_token(iss="https://evil.example.com"), "raw-nonce"),
        (apple_token(key=OTHER_KEY), "raw-nonce"),
        (apple_token(kid="NOPE"), "raw-nonce"),
        ("not.a.jwt", "raw-nonce"),
    ]
    for token, nonce in bad:
        with pytest.raises(signin.SignInError):
            signin.verify_apple_token(token, [AUD], nonce)
    with pytest.raises(signin.SignInError):   # token carries a nonce, the app sent none
        signin.verify_apple_token(apple_token(), [AUD], None)


def test_apple_sign_in_app_token_and_linking(client, monkeypatch):
    client.cookies.clear()
    # e-mail first, then Apple with the same verified e-mail → the same account
    first = _email_login(client, "linked@example.com", "app", "iPhone 17")
    assert first["token"].startswith(auth.TOKEN_PREFIX)
    r = client.post("/v1/auth/apple", json={"identity_token": apple_token(sub="apple-linked", email="linked@example.com"),
                                            "nonce": "raw-nonce", "client": "app", "device": "iPhone 17", "given_name": "Ann"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["me"]["id"] == first["me"]["id"] and out["new_user"] is False
    h = {"Authorization": f"Bearer {out['token']}"}
    assert {i["provider"] for i in client.get("/v1/account", headers=h).json()["identities"]} == {"email", "apple"}
    # private relay address, not verified elsewhere → a new pending account named after the given name
    r = client.post("/v1/auth/apple", json={"identity_token": apple_token(sub="apple-new", email="relay-abc@example.com"),
                                            "nonce": "raw-nonce", "given_name": "Bo"})
    assert r.status_code == 200 and r.json()["new_user"] is True and r.json()["me"]["display_name"] == "Bo"
    assert client.post("/v1/auth/apple", json={"identity_token": apple_token(), "nonce": "wrong"}).status_code == 401
    monkeypatch.delenv("BODY_APPLE_AUDIENCES")
    assert client.post("/v1/auth/apple", json={"identity_token": apple_token(), "nonce": "raw-nonce"}).status_code == 503
    # app sign-out revokes the token
    assert client.post("/v1/account/logout", headers=h).status_code == 204
    assert client.get("/v1/account", headers=h).status_code == 401


# ---------------------------------------------------------------------------------------------------- invites + provisioning

def test_invite_batch_redeem_and_provisioning(client, dsn, _env, monkeypatch):
    client.cookies.clear()
    batch = _batch(dsn, 2, label="beta-1", days=30)
    assert len(batch["codes"]) == 2 and all(len(c) == invites.LENGTH for c in batch["codes"])
    s = _email_login(client, "invited@example.com", "app", "iPhone")
    h = {"Authorization": f"Bearer {s['token']}"}
    assert client.post("/v1/account/ow-invitation", headers=h).status_code == 403
    assert client.post("/v1/account/redeem-invite", headers=h, json={"code": "WRONGCODE1"}).status_code == 401
    code = batch["codes"][0]
    typed = f"{code[:5].lower()} - {code[5:]}"
    r = client.post("/v1/account/redeem-invite", headers=h, json={"code": typed})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active" and r.json()["provisioning"]["state"] == "done"
    uid = s["me"]["id"]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        u = conn.execute("SELECT * FROM users WHERE id = %s", (uid,)).fetchone()
        assert u["status"] == "active" and u["ow_user_id"] and u["sparky_user_id"] == f"sparky-w2w-u{uid}"
        assert conn.execute("SELECT count(*) AS n FROM journal_items WHERE user_id = %s", (uid,)).fetchone()["n"] > 0
        p = conn.execute("SELECT * FROM user_provisioning WHERE user_id = %s", (uid,)).fetchone()
        assert b"key-read" not in bytes(p["sparky_key_ro_enc"])   # stored encrypted
        assert sparky_client.client_for_user(conn, uid).api_key == "key-read-2"
        assert sparky_client.write_client_for_user(conn, uid).api_key == "key-full-1"
        # idempotent: nothing is created twice
        provision.run(conn, uid)
    assert len(_env["ow"].created) == 1 and len(_env["sparky"].accounts) == 1
    assert _env["ow"].created[0]["external_user_id"] == f"w2w-u{uid}"
    assert _env["sparky"].accounts[0][0] == f"w2w-u{uid}@users.watch2whoop.invalid"   # never the real e-mail
    # active now: normal endpoints work; the single-use code cannot open a second account
    assert client.get("/v1/users/me/journal/frequent", headers=h).status_code == 200
    other = _email_login(client, "second@example.com", "app")
    h2 = {"Authorization": f"Bearer {other['token']}"}
    assert client.post("/v1/account/redeem-invite", headers=h2, json={"code": code}).status_code == 401
    assert client.post("/v1/account/redeem-invite", headers=h2, json={"code": batch["codes"][1]}).status_code == 200
    # listing: uses, free codes, who redeemed
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        b = next(x for x in invites.list_batches(conn) if x["id"] == batch["batch_id"])
        assert (b["codes"], b["uses"], b["free_codes"], b["state"]) == (2, 2, 0, "active")
        codes = invites.list_codes(conn, batch["batch_id"])
        assert codes[0]["redeemed"][0]["email"] == "invited@example.com" and codes[0]["hint"] == code[-3:]

    # OW invitation for the app: minted for this user's OW account
    f = _ow_signing(monkeypatch)
    monkeypatch.setattr(activation, "_post_json", lambda url, headers, timeout: {"code": "ABCDEFGH"})
    monkeypatch.setenv("BODY_OW_PUBLIC_URL", "https://sync.example.com/")
    r = client.post("/v1/account/ow-invitation", headers=h)
    assert r.status_code == 200 and r.json() == {"code": "ABCDEFGH", "ow_url": "https://sync.example.com"}
    assert f.exists()


def _ow_signing(monkeypatch):
    import tempfile
    from pathlib import Path
    f = Path(tempfile.mkdtemp()) / "sign.json"
    f.write_text(json.dumps({"secret_key": "s", "developer_id": "d"}))
    monkeypatch.setenv("BODY_OW_INVITE_SIGNING_FILE", str(f))
    return f


def test_invite_max_uses_expiry_and_revoke(client, dsn):
    multi = _batch(dsn, 1, label="group", max_uses=2)["codes"][0]
    revoked = _batch(dsn, 1, label="gone")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        invites.revoke(conn, batch_id=revoked["batch_id"])
        expired = invites.create_batch(conn, 1, "old", days=1)
        conn.execute("UPDATE invite_batches SET expires_at = now() - interval '1 minute' WHERE id = %s", (expired["batch_id"],))
    tokens = [_email_login(client, f"multi{i}@example.com", "app")["token"] for i in range(3)]
    hs = [{"Authorization": f"Bearer {t}"} for t in tokens]
    assert client.post("/v1/account/redeem-invite", headers=hs[0], json={"code": revoked["codes"][0]}).status_code == 401
    assert client.post("/v1/account/redeem-invite", headers=hs[0], json={"code": expired["codes"][0]}).status_code == 401
    assert client.post("/v1/account/redeem-invite", headers=hs[0], json={"code": multi}).status_code == 200
    assert client.post("/v1/account/redeem-invite", headers=hs[1], json={"code": multi}).status_code == 200
    assert client.post("/v1/account/redeem-invite", headers=hs[2], json={"code": multi}).status_code == 401
    # an active account redeeming again does not use up a code
    fresh = _batch(dsn, 1, label="spare")["codes"][0]
    assert client.post("/v1/account/redeem-invite", headers=hs[0], json={"code": fresh}).status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT uses FROM invite_codes WHERE hint = %s ORDER BY id DESC LIMIT 1",
                            (fresh[-3:],)).fetchone()["uses"] == 0
    with pytest.raises(ValueError):
        _batch(dsn, 0)


def test_provisioning_failure_is_retried(client, dsn, _env, monkeypatch):
    _env["sparky"].fail = True
    s = _email_login(client, "retry@example.com", "app")
    h = {"Authorization": f"Bearer {s['token']}"}
    r = client.post("/v1/account/redeem-invite", headers=h, json={"code": _batch(dsn)["codes"][0]})
    assert r.status_code == 200 and r.json()["status"] == "active"
    prov = r.json()["provisioning"]
    assert prov["state"] == "failed" and prov["steps"] == {"ow": "done", "sparky": "error", "journal": "done", "memory": "done"}
    _env["sparky"].fail = False
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        done = [x for x in provision.run_pending(conn) if x["user_id"] == s["me"]["id"]]
        assert done and done[0]["state"] == "done"
        # the password saved before the first attempt is reused (same SparkyFitness account)
        assert _env["sparky"].accounts[-1][0] == f"w2w-u{s['me']['id']}@users.watch2whoop.invalid"


def test_web_login_code_for_app_webview(client):
    s = _email_login(client, "webview@example.com", "app")
    r = client.post("/v1/account/web-login-code", headers={"Authorization": f"Bearer {s['token']}"})
    assert r.status_code == 200 and r.json()["expires_in"] == 600
    client.cookies.clear()
    assert client.post("/v1/web/login-code", json={"code": r.json()["code"]}).status_code == 200
    assert client.get("/v1/web/me").json()["me"]["id"] == s["me"]["id"]
    client.cookies.clear()


# ---------------------------------------------------------------------------------------------------- deletion, disabled

def test_delete_account_removes_everything(client, dsn, _env, monkeypatch):
    client.cookies.clear()
    s = _email_login(client, "leaver@example.com")
    assert client.post("/v1/account/redeem-invite", headers=WEB, json={"code": _batch(dsn)["codes"][0]}).status_code == 200
    uid = s["me"]["id"]
    admin_calls = []

    class Admin:
        def request(self, method, path, **kw):
            admin_calls.append((method, path))

    monkeypatch.setattr(account_delete, "_sparky_admin", lambda: Admin())
    monkeypatch.setattr(account_delete, "OWClient", lambda **kw: _env["ow"])
    assert client.request("DELETE", "/v1/account", headers=WEB, json={"confirm": "no"}).status_code == 400
    r = client.request("DELETE", "/v1/account", headers=WEB, json={"confirm": "DELETE"})
    assert r.status_code == 200 and r.json() == {"deleted": True, "leftovers": 0}
    assert admin_calls == [("DELETE", f"/admin/users/sparky-w2w-u{uid}")]
    assert _env["ow"].deleted and _env["ow"].deleted[-1].startswith("/users/")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT 1 FROM users WHERE id = %s", (uid,)).fetchone() is None
        for t in ("user_identities", "journal_items", "api_tokens", "web_sessions", "user_provisioning"):
            assert conn.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id = %s", (uid,)).fetchone()["n"] == 0, t
        assert conn.execute("SELECT count(*) AS n FROM invite_redemptions WHERE user_id IS NULL").fetchone()["n"] >= 1
    assert client.get("/v1/web/me").status_code == 401
    # the same e-mail can sign up again from scratch
    account_api._resend.reset()
    assert _email_login(client, "leaver@example.com")["new_user"] is True
    client.cookies.clear()


def test_owner_cannot_delete_and_disabled_is_blocked(client, dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        owner_token = auth.create_token(conn, 1, "t", ["read", "write"])
    h = {"Authorization": f"Bearer {owner_token}"}
    assert client.get("/v1/me", headers=h).json()["status"] == "active"
    assert client.request("DELETE", "/v1/account", headers=h, json={"confirm": "DELETE"}).status_code == 403
    s = _email_login(client, "blocked@example.com", "app")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE users SET status = 'disabled' WHERE id = %s", (s["me"]["id"],))
    assert client.get("/v1/account", headers={"Authorization": f"Bearer {s['token']}"}).status_code == 403
    account_api._resend.reset()
    assert client.post("/v1/auth/email/start", json={"email": "blocked@example.com"}).status_code == 200
    code = mailer.OUTBOX[-1][1].rsplit(" ", 1)[1]
    assert client.post("/v1/auth/email/verify", json={"email": "blocked@example.com", "code": code}).status_code == 403


def test_purge_stale_pending(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid, _ = signin.find_or_create_user(conn, "email", "stale@example.com", "stale@example.com", True)
        uid = uid["id"]
        auth.create_token(conn, uid, "app:signin:x", ["read"])
        conn.execute("UPDATE users SET created_at = now() - interval '8 days' WHERE id = %s", (uid,))
        conn.commit()
        assert account_delete.purge_pending(conn) >= 1
        assert conn.execute("SELECT 1 FROM users WHERE id = %s", (uid,)).fetchone() is None


def test_methods_and_page_assets(client, monkeypatch):
    assert client.get("/v1/auth/methods").json() == {"email": True, "apple": True}
    monkeypatch.setenv("BODY_MAIL_MODE", "off")
    monkeypatch.delenv("BODY_APPLE_AUDIENCES")
    assert client.get("/v1/auth/methods").json() == {"email": False, "apple": False}
    assert '<script src="account.js" defer></script>' in client.get("/app/").text
    js = client.get("/app/account.js")
    assert js.status_code == 200 and "watch2whoop://account-deleted" in js.text


def test_login_page_is_a_step_flow(client):
    """Issue #38: one thing per screen. Each step is its own form with exactly one submit button (Enter submits that step
    only), the steps start hidden (account.js shows one), and the older ways sit in a collapsed 「其他登录方式」."""
    import re
    html = client.get("/app/").text
    login = html[html.index('<div id="login"'):html.index('<div id="sheet"')]
    forms = dict(re.findall(r'<form id="([\w-]+)"[^>]*>(.*?)</form>', login, re.S))
    assert set(forms) == {"ls-email", "ls-code", "ls-invite", "login-form"}
    for body in forms.values():
        assert len(re.findall(r'type="submit"', body)) == 1
    for step in ("ls-email", "ls-code", "ls-invite"):
        assert re.search(rf'<form id="{step}"[^>]*\bhidden\b', login)
    other = login[login.index('<details class="login-other"'):login.index("</details>")]
    assert " open" not in other.split(">")[0] and "<summary data-i18n=\"其他登录方式\">其他登录方式</summary>" in other and 'id="login-form"' in other
    assert 'data-step="email"' in login and 'data-step="code"' in login and 'data-step="invite"' in login
    js = client.get("/app/account.js").text
    assert "loginShown" in js and "showPending" in js and "apple-sign-in" in js
