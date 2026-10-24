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
    # e-mail first, then Apple with the same verified e-mail → NOT merged (accounts are only joined by explicit binding)
    first = _email_login(client, "linked@example.com", "app", "iPhone 17")
    assert first["token"].startswith(auth.TOKEN_PREFIX)
    r = client.post("/v1/auth/apple", json={"identity_token": apple_token(sub="apple-linked", email="linked@example.com"),
                                            "nonce": "raw-nonce", "client": "app", "device": "iPhone 17", "given_name": "Ann"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["me"]["id"] != first["me"]["id"] and out["new_user"] is True
    h = {"Authorization": f"Bearer {out['token']}"}
    assert [i["provider"] for i in client.get("/v1/account", headers=h).json()["identities"]] == ["apple"]
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
        assert codes[0]["redeemed"][0]["identities"] == [{"provider": "email", "hint": "i***@e***.com"}]
        assert codes[0]["hint"] == code[-3:] and "invited@example.com" not in json.dumps(codes, default=str)

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


def _pending(conn, provider, subject, email=None):
    user, new = signin.find_or_create_user(conn, provider, subject, email, True)
    assert new
    return user["id"]


def test_one_code_many_people_then_exhausted_and_listed(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        res = invites.create_code(conn, "club", 3, days=30)
        code = res["code"]
        users = [_pending(conn, "email", f"club{i}@example.com", f"club{i}@example.com") for i in range(3)]
        users.append(_pending(conn, "apple", "001234.apple-sub.9876", None))
        assert [invites.redeem(conn, u, code) for u in users] == [True, True, True, False]   # 4th: used up
        assert conn.execute("SELECT status FROM users WHERE id = %s", (users[3],)).fetchone()["status"] == "pending"
        b = next(x for x in invites.list_batches(conn) if x["id"] == res["batch_id"])
        assert (b["codes"], b["uses"], b["capacity"], b["remaining"], b["kind"], b["state"]) == (1, 3, 3, 0, "admin", "active")
        assert [r["identities"] for r in b["redemptions"]] == [[{"provider": "email", "hint": "c***@e***.com"}]] * 3
        assert [r["user_id"] for r in b["redemptions"]] == users[:3] and all(r["redeemed_at"] for r in b["redemptions"])
        assert all(r["inviter_user_id"] is None for r in b["redemptions"])
        # a fresh code for the Apple user: the hint is the tail of Apple's subject (no e-mail shared)
        other = invites.create_code(conn, "solo", 1)
        assert invites.redeem(conn, users[3], other["code"])
        c = invites.list_codes(conn, other["batch_id"])[0]
        assert c["redeemed"][0]["identities"] == [{"provider": "apple", "hint": "Apple ID ···9876"}]
        with pytest.raises(ValueError):
            invites.create_code(conn, "none", 0)
    assert invites.mask_email("John.Doe@Mail.Example.org") == "j***@m***.org" and invites.mask_email("x") == "***"


def test_readable_code_chosen_by_the_admin(client, dsn, _env):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        res = invites.create_code(conn, "channel", 2, text="Sample Note")
        assert res["code"] == "SAMPLENOTE" and res["max_uses"] == 2
        assert invites.list_codes(conn, res["batch_id"])[0]["hint"] == "OTE"
        users = [_pending(conn, "email", f"note{i}@example.com", f"note{i}@example.com") for i in range(3)]
        # matched like random codes: case, spaces and dashes do not matter; max_uses still holds
        assert [invites.redeem(conn, u, t) for u, t in zip(users, ["samplenote", " Sample-Note ", "SAMPLENOTE"])] \
            == [True, True, False]
        for bad in ("abc", "x" * 41, "note_1!", "笔记笔记", "ABCDEFGHJKLM", "A" * 16):   # short, long, symbols, 12 / 16
            with pytest.raises(ValueError):
                invites.create_code(conn, "bad", 5, text=bad)
        with pytest.raises(ValueError):   # the same text again, in any spelling
            invites.create_code(conn, "again", 5, text="sample note")
        with pytest.raises(ValueError):
            invites.create_batch(conn, 2, "two", text="TwoCodes")
        conn.commit()
    # through the sign-up endpoint: a 7-letter code reaches the invite codes (not the login / activation-code path)
    client.cookies.clear()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        invites.create_code(conn, "short", 1, text="redwood")
        conn.commit()
    s = _email_login(client, "readable@example.com", "app", "iPhone")
    h = {"Authorization": f"Bearer {s['token']}"}
    r = client.post("/v1/account/redeem-invite", headers=h, json={"code": "Red Wood"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"


def test_last_use_race_only_one_wins(dsn):
    import threading
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        code = invites.create_code(conn, "race", 1)["code"]
        racers = [_pending(conn, "email", f"race{i}@example.com", f"race{i}@example.com") for i in range(4)]
        conn.commit()
    barrier, results = threading.Barrier(len(racers)), {}

    def go(uid):
        with psycopg.connect(dsn, row_factory=dict_row) as c:
            barrier.wait()
            results[uid] = invites.redeem(c, uid, code)
            c.commit() if results[uid] else c.rollback()

    threads = [threading.Thread(target=go, args=(u,)) for u in racers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values()) == [False, False, False, True]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("SELECT uses, max_uses FROM invite_codes WHERE code_sha256 = %s", (invites._sha(code),)).fetchone()
        assert (row["uses"], row["max_uses"]) == (1, 1)
        assert conn.execute("SELECT count(*) AS n FROM users WHERE id = ANY(%s) AND status = 'active'", (racers,)).fetchone()["n"] == 1


def test_same_identity_cannot_redeem_twice(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        code = invites.create_code(conn, "twice", 5)["code"]
        uid = _pending(conn, "email", "twice@example.com", "twice@example.com")
        assert invites.redeem(conn, uid, code) and invites.redeem(conn, uid, code)   # already active: no second use
        conn.commit()
        account_delete.delete_account(conn, uid)
        # deleted the account and signed up again with the same e-mail: the identity has had its invite
        again = _pending(conn, "email", "twice@example.com", "twice@example.com")
        assert not invites.redeem(conn, again, code)
        assert not invites.redeem(conn, again, invites.create_code(conn, "fresh", 1)["code"])
        assert conn.execute("SELECT uses FROM invite_codes WHERE code_sha256 = %s", (invites._sha(code),)).fetchone()["uses"] == 1
        b = invites.list_batches(conn)
        assert any(r["user_id"] is None and r["identities"] == [{"provider": "email", "hint": "t***@e***.com"}]
                   for x in b for r in x["redemptions"])   # the record outlives the account, masked


def test_referral_code_records_inviter_and_pending_reward(client, dsn, _env, monkeypatch):
    from body_engine.account import params
    client.cookies.clear()
    s = _email_login(client, "inviter@example.com", "app")
    h = {"Authorization": f"Bearer {s['token']}"}
    inviter = s["me"]["id"]
    assert client.post("/v1/account/redeem-invite", headers=h, json={"code": _batch(dsn)["codes"][0]}).status_code == 200
    monkeypatch.delenv(params.REFERRALS_ENV, raising=False)
    assert client.get("/v1/users/me/invite-code", headers=h).status_code == 404   # off by default
    monkeypatch.setenv(params.REFERRALS_ENV, "1")
    r = client.get("/v1/users/me/invite-code", headers=h)
    assert r.status_code == 200, r.text
    mine = r.json()
    assert mine["max_uses"] == params.REFERRAL_MAX_USES and mine["uses"] == mine["joined"] == 0 and len(invites.normalize(mine["code"])) == invites.LENGTH
    assert mine["link"] == f"/app/?invite={mine['code']}" and mine["remaining"] == params.REFERRAL_MAX_USES
    assert client.get("/v1/users/me/invite-code", headers=h).json()["code"] == mine["code"]   # same code next time
    assert client.get("/v1/users/1/invite-code", headers=h).status_code == 403
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        friend = _pending(conn, "email", "friend@example.com", "friend@example.com")
        assert invites.redeem(conn, friend, mine["code"])
        red = conn.execute("SELECT * FROM invite_redemptions WHERE user_id = %s", (friend,)).fetchone()
        assert red["inviter_user_id"] == inviter and red["circle_id"] is None
        rw = conn.execute("SELECT * FROM invite_rewards WHERE redemption_id = %s", (red["id"],)).fetchone()
        assert (rw["user_id"], rw["invitee_user_id"], rw["state"], rw["reward_type"]) == (
            inviter, friend, "pending", params.REFERRAL_REWARD["type"])
        assert float(rw["amount"]) == params.REFERRAL_REWARD["amount"]
        b = next(x for x in invites.list_batches(conn) if x["label"] == f"referral:u{inviter}")
        assert (b["kind"], b["owner_user_id"], b["uses"], b["remaining"]) == ("referral", inviter, 1, params.REFERRAL_MAX_USES - 1)
        # an admin code leaves no inviter and no reward
        plain = _pending(conn, "email", "plain@example.com", "plain@example.com")
        assert invites.redeem(conn, plain, _batch(dsn)["codes"][0])
        assert conn.execute("SELECT inviter_user_id FROM invite_redemptions WHERE user_id = %s", (plain,)).fetchone()["inviter_user_id"] is None
        assert conn.execute("SELECT count(*) AS n FROM invite_rewards WHERE invitee_user_id = %s", (plain,)).fetchone()["n"] == 0
        conn.commit()
        # the inviter deletes their account: the code stops working (never an owner-less admin code); the friend's record stays
        conn.execute("UPDATE users SET ow_user_id = NULL, sparky_user_id = NULL WHERE id = %s", (inviter,))
        conn.commit()
        account_delete.delete_account(conn, inviter)
        late = _pending(conn, "email", "late@example.com", "late@example.com")
        assert not invites.redeem(conn, late, mine["code"])
        assert conn.execute("SELECT inviter_user_id FROM invite_redemptions WHERE user_id = %s", (friend,)).fetchone()["inviter_user_id"] is None
        assert conn.execute("SELECT count(*) AS n FROM invite_redemptions WHERE user_id = %s", (friend,)).fetchone()["n"] == 1


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
    monkeypatch.delenv("BODY_REFERRALS_ENABLED", raising=False)
    assert client.get("/v1/auth/methods").json() == {"email": True, "apple": True, "referrals": False, "ios_web": "warn"}
    monkeypatch.setenv("BODY_MAIL_MODE", "off")
    monkeypatch.delenv("BODY_APPLE_AUDIENCES")
    monkeypatch.setenv("BODY_REFERRALS_ENABLED", "1")
    assert client.get("/v1/auth/methods").json() == {"email": False, "apple": False, "referrals": True, "ios_web": "warn"}
    assert '<script src="account.js?v=' in client.get("/app/").text   # linked as name?v=<fingerprint>
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
    assert "登录 / 注册" in forms["ls-email"] and "请管理员先绑定你的邮箱" in forms["ls-email"]
    for body in forms.values():
        assert len(re.findall(r'type="submit"', body)) == 1
    for step in ("ls-email", "ls-code", "ls-invite"):
        assert re.search(rf'<form id="{step}"[^>]*\bhidden\b', login)
    other = login[login.index('<details class="login-other"'):login.index("</details>")]
    assert " open" not in other.split(">")[0] and "<summary data-i18n=\"其他登录方式\">其他登录方式</summary>" in other and 'id="login-form"' in other
    assert 'data-step="email"' in login and 'data-step="code"' in login and 'data-step="invite"' in login
    js = client.get("/app/account.js").text
    assert "loginShown" in js and "showPending" in js and "apple-sign-in" in js


# ---------------------------------------------------------------------------------------------------- identities of existing accounts

def _reset_mail_limits():
    for limiter in (account_api._per_ip, account_api._per_address, account_api._resend):
        limiter.reset()


def _admin_user(conn, name):
    """An account the admin created (onboarding / activation code): active, no identity."""
    return conn.execute("INSERT INTO users (display_name, role, status) VALUES (%s, 'member', 'active') RETURNING id",
                        (name,)).fetchone()["id"]


def test_identity_link_uniqueness_move_unlink(dsn):
    from body_engine.account import identities
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        a, b = _admin_user(conn, "IdA"), _admin_user(conn, "IdB")
        assert identities.link_email(conn, a, " Same@Example.com ") == "linked"
        assert identities.link_email(conn, a, "same@example.com") == "already"            # idempotent
        with pytest.raises(identities.IdentityConflict) as e:
            identities.link_email(conn, b, "same@example.com")                             # one e-mail, one account
        assert e.value.user_id == a
        assert identities.link_email(conn, b, "same@example.com", move=True) == "moved"
        rows = identities.list_identities(conn, b)
        assert [(r["provider"], r["email"], r["display_name"]) for r in rows] == [("email", "same@example.com", "IdB")]
        assert identities.list_identities(conn, a) == []
        assert identities.unlink(conn, user_id=b, email="SAME@example.com") == 1
        assert identities.unlink(conn, identity_id=rows[0]["id"]) == 0
        with pytest.raises(ValueError):
            identities.link_email(conn, a, "not-an-email")
        with pytest.raises(ValueError):
            identities.link_email(conn, 999999, "x@example.com")


def test_existing_account_signs_in_by_email_new_email_is_pending(client, dsn, _env):
    _reset_mail_limits()
    from body_engine.account import identities
    client.cookies.clear()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = _admin_user(conn, "Onboarded")
        identities.link_email(conn, uid, "onboarded@example.com")
    s = _email_login(client, "onboarded@example.com", "app", "iPhone")
    assert s["me"]["id"] == uid and s["new_user"] is False and s["me"]["status"] == "active"
    acct = client.get("/v1/account", headers={"Authorization": f"Bearer {s['token']}"}).json()
    assert acct["invite_required"] is False and acct["identities"] == [{"provider": "email", "email": "onboarded@example.com"}]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:   # the first code sign-in verified the admin-bound address
        assert conn.execute("SELECT verified_at FROM user_identities WHERE user_id = %s", (uid,)).fetchone()["verified_at"]
    other = _email_login(client, "brand-new@example.com", "app")
    assert other["new_user"] is True and other["me"]["status"] == "pending" and other["me"]["id"] != uid


def test_bind_apple_and_email_while_signed_in(client, dsn, _env):
    _reset_mail_limits()
    from body_engine.account import identities
    client.cookies.clear()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = _admin_user(conn, "Binder")
        identities.link_email(conn, uid, "binder@example.com")
    s = _email_login(client, "binder@example.com", "app")
    h = {"Authorization": f"Bearer {s['token']}"}
    assert client.get("/v1/account", headers=h).json()["apple_link"] is True
    # Apple: a fresh token from the app binds this Apple ID; signing in with Apple then opens the same account
    r = client.post("/v1/account/identities/apple", headers=h, json={"identity_token": apple_token(sub="apple-binder", email="relay-x@privaterelay.example.com"), "nonce": "raw-nonce"})
    assert r.status_code == 200 and r.json()["result"] == "linked", r.text
    assert {i["provider"] for i in r.json()["identities"]} == {"email", "apple"}
    again = client.post("/v1/auth/apple", json={"identity_token": apple_token(sub="apple-binder"), "nonce": "raw-nonce"}).json()
    assert again["me"]["id"] == uid and again["new_user"] is False
    assert client.post("/v1/account/identities/apple", headers=h, json={"identity_token": apple_token(sub="apple-binder"), "nonce": "raw-nonce"}).json()["result"] == "already"
    # an Apple ID that already has its own account is not taken over
    stranger = client.post("/v1/auth/apple", json={"identity_token": apple_token(sub="apple-stranger"), "nonce": "raw-nonce"}).json()
    assert stranger["new_user"] is True
    assert client.post("/v1/account/identities/apple", headers=h, json={"identity_token": apple_token(sub="apple-stranger"), "nonce": "raw-nonce"}).status_code == 409
    assert client.post("/v1/account/identities/apple", headers=h, json={"identity_token": apple_token(sub="x"), "nonce": "wrong"}).status_code == 401
    # a pending account cannot bind
    ph = {"Authorization": f"Bearer {stranger['token']}"}
    assert client.post("/v1/account/identities/apple", headers=ph, json={"identity_token": apple_token(sub="apple-other"), "nonce": "raw-nonce"}).status_code == 403
    # e-mail: the code sent to the new address proves it
    _reset_mail_limits()
    assert client.post("/v1/auth/email/start", json={"email": "binder.work@example.com"}).status_code == 200
    code = mailer.OUTBOX[-1][2].split("：")[1].split("\n")[0].strip()
    assert client.post("/v1/account/identities/email", headers=h, json={"email": "binder.work@example.com", "code": "000000" if code != "000000" else "111111"}).status_code == 401
    r = client.post("/v1/account/identities/email", headers=h, json={"email": "binder.work@example.com", "code": code})
    assert r.status_code == 200 and r.json()["result"] == "linked"
    _reset_mail_limits()
    assert _email_login(client, "binder.work@example.com", "app")["me"]["id"] == uid
    # an address that signs in to another account is refused
    _reset_mail_limits()
    assert client.post("/v1/auth/email/start", json={"email": "brand-new@example.com"}).status_code == 200
    code = mailer.OUTBOX[-1][2].split("：")[1].split("\n")[0].strip()
    assert client.post("/v1/account/identities/email", headers=h, json={"email": "brand-new@example.com", "code": code}).status_code == 409


def test_identity_cli_and_admin_creation(dsn, monkeypatch, capsys):
    import sys
    from body_engine import cli, db
    monkeypatch.setattr(db, "settings", Settings(db_name=TEST_DB))
    monkeypatch.setattr(cli, "connect", lambda: psycopg.connect(Settings(db_name=TEST_DB).dsn, row_factory=dict_row), raising=False)

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["body-engine", *argv])
        cli.main()
        return capsys.readouterr().out

    out = run("user-add", "--name", "CliPerson", "--email", "cli.person@example.com")
    uid = int(out.split("user id:")[1].split()[0])
    assert "email: linked" in out
    assert "already" in run("identity-link", "--user-id", str(uid), "--email", "cli.person@example.com")
    other = int(run("user-add", "--name", "CliOther").split("user id:")[1].split()[0])
    with pytest.raises(SystemExit, match="already signs in to user"):
        run("identity-link", "--user-id", str(other), "--email", "cli.person@example.com")
    out = run("activation-code", "create", "--user-id", str(other), "--email", "cli.other@example.com")
    assert "# email: linked" in out
    listed = [json.loads(x) for x in run("identity-link", "--list", "--user-id", str(other)).splitlines()]
    assert [x["email"] for x in listed] == ["cli.other@example.com"]
    assert "removed: 1" in run("identity-link", "--unlink", "--user-id", str(other), "--email", "cli.other@example.com")


# ---------------------------------------------------------------------------------------------------- once activated, sign in directly

def _pending_ident(conn, provider, subject, email=None):
    user, new = signin.find_or_create_user(conn, provider, subject, email or subject, True)
    assert new
    return user["id"]


def _apple(client, sub, email, verified=True, client_kind="app"):
    r = client.post("/v1/auth/apple", json={"identity_token": apple_token(sub=sub, email=email, verified=verified),
                                            "nonce": "raw-nonce", "client": client_kind})
    assert r.status_code == 200, r.text
    return r.json()


def _events(dsn, kind):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return conn.execute("SELECT * FROM identity_events WHERE kind = %s ORDER BY id", (kind,)).fetchall()


def test_apple_joins_existing_account_only_on_an_exact_verified_match(client, dsn, _env):
    from body_engine.account import identities
    _reset_mail_limits()
    client.cookies.clear()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = _admin_user(conn, "AppleMatch")
        identities.link_email(conn, uid, "apple.match@example.com")
        pend = _pending_ident(conn, "email", "pending.match@example.com")            # a pending account's e-mail
        a, b = _admin_user(conn, "AmbA"), _admin_user(conn, "AmbB")
        identities.link_email(conn, a, "ambiguous@example.com")
        conn.execute("""INSERT INTO user_identities (user_id, provider, subject, email, verified_at)
                        VALUES (%s, 'apple', 'apple-amb-b', 'ambiguous@example.com', now())""", (b,))
    # exact verified, non-relay match → that account, Apple ID bound, audited
    out = _apple(client, "apple-match-1", "apple.match@example.com")
    assert out["me"]["id"] == uid and out["new_user"] is False and out["status"] == "active" and "hint" not in out
    ev = [e for e in _events(dsn, "auto_link_apple") if e["user_id"] == uid]
    assert len(ev) == 1 and ev[0]["provider"] == "apple" and ev[0]["actor"] == "sign-in" and "apple.match" not in ev[0]["hint"]
    # already bound → straight in (no new audit row)
    assert _apple(client, "apple-match-1", "apple.match@example.com")["me"]["id"] == uid
    assert len([e for e in _events(dsn, "auto_link_apple") if e["user_id"] == uid]) == 1
    # every other case: a new pending account and the hint to bind Apple from the old account
    for sub, email, verified in (("apple-relay", "abc123@" + identities.APPLE_RELAY_DOMAIN, True),   # private relay
                                 ("apple-unverified", "apple.match@example.com", False),        # not verified by Apple
                                 ("apple-nomatch", "nobody.here@example.com", True),            # no account
                                 ("apple-amb", "ambiguous@example.com", True),                  # two accounts carry it
                                 ("apple-pending", "pending.match@example.com", True)):         # only a pending account
        o = _apple(client, sub, email, verified)
        assert o["new_user"] is True and o["status"] == "pending" and o["hint"] == "existing_account_bind_apple", sub
        assert o["me"]["id"] not in (uid, a, b, pend), sub
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM user_identities WHERE user_id = %s", (uid,)).fetchone()["n"] == 2


def test_pending_sign_in_claims_existing_account_with_its_code(client, dsn, _env):
    from body_engine.account import identities
    from body_engine.web import activation, login_codes
    _reset_mail_limits()
    client.cookies.clear()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = _admin_user(conn, "Claimed")
        code = activation.create(conn, uid, "test")["code"]
        other = _admin_user(conn, "ClaimedWeb")
        lcode = login_codes.create(conn, other, "test", 1)
    # app: an unbound e-mail made a pending account; the invite step takes the old account's activation code
    s = _email_login(client, "claimer@example.com", "app")
    assert s["me"]["status"] == "pending"
    h = {"Authorization": f"Bearer {s['token']}"}
    assert client.post("/v1/account/redeem-invite", headers=h, json={"code": "AAAAABBBBBCCCCCD"}).status_code == 401
    r = client.post("/v1/account/redeem-invite", headers=h, json={"code": code.lower()})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["claimed"] is True and out["me"]["id"] == uid and out["status"] == "active" and out["token"]
    acct = client.get("/v1/account", headers={"Authorization": f"Bearer {out['token']}"}).json()
    assert acct["id"] == uid and acct["identities"] == [{"provider": "email", "email": "claimer@example.com"}]
    assert client.get("/v1/account", headers=h).status_code == 401                      # the pending account is gone
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT 1 FROM users WHERE id = %s", (s["me"]["id"],)).fetchone() is None
    ev = [e for e in _events(dsn, "claim_by_code") if e["user_id"] == uid]
    assert len(ev) == 1 and ev[0]["from_user_id"] == s["me"]["id"]
    # next time: the e-mail code goes straight into the old account, no invite step
    _reset_mail_limits()
    again = _email_login(client, "claimer@example.com", "app")
    assert again["me"]["id"] == uid and again["new_user"] is False and again["me"]["status"] == "active"
    # web: a one-time login code works too, and the cookie session switches to that account
    _reset_mail_limits()
    client.cookies.clear()
    w = _email_login(client, "claimer.web@example.com")
    assert w["me"]["status"] == "pending"
    r = client.post("/v1/account/redeem-invite", headers=WEB, json={"code": lcode})
    assert r.status_code == 200 and r.json()["claimed"] is True and "token" not in r.json()
    assert client.get("/v1/web/me").json()["me"]["id"] == other
    assert client.post("/v1/account/redeem-invite", headers=WEB, json={"code": lcode}).status_code in (200, 401)   # active now: no-op or used
    client.cookies.clear()
    # an active account is never sent to the invite step; a brand-new e-mail still is (pending → invite code)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        identities.link_email(conn, other, "claimed.bound@example.com")
    _reset_mail_limits()
    bound = _email_login(client, "claimed.bound@example.com", "app")
    assert bound["me"]["status"] == "active" and client.get("/v1/account", headers={"Authorization": f"Bearer {bound['token']}"}).json()["invite_required"] is False
    _reset_mail_limits()
    fresh = _email_login(client, "claimed.fresh@example.com", "app")
    fh = {"Authorization": f"Bearer {fresh['token']}"}
    assert client.get("/v1/account", headers=fh).json()["invite_required"] is True
    assert client.post("/v1/account/redeem-invite", headers=fh, json={"code": _batch(dsn)["codes"][0]}).json()["status"] == "active"


def test_identity_texts_are_bilingual(client):
    """Every tr("中文") key in identities.js / account.js and every data-i18n key on the login screen that identities.js
    owns has English in i18n-en.js with the same placeholders."""
    import re
    ids = client.get("/app/identities.js").text
    en = dict(re.findall(r'^\s*"([^"]+)": "([^"]+)",$', client.get("/app/i18n-en.js").text, re.M))   # #20's English table
    keys = set(re.findall(r'\btr\("([^"]+)"', ids)) | set(re.findall(r'\btr\("([^"]+)"', client.get("/app/account.js").text))
    html = re.sub(r"\?v=[0-9a-f]+", "", client.get("/app/").text)   # files are linked as name?v=<fingerprint>
    keys |= {k for k in re.findall(r'data-i18n="([^"]+)"', html)}
    keys = {k for k in keys if re.search(r"[一-鿿]", k)}   # dotted keys live in i18n.js
    assert keys and keys <= set(en), keys - set(en)
    for zh, e in en.items():
        assert set(re.findall(r"\{(\w+)\}", zh)) == set(re.findall(r"\{(\w+)\}", e)), zh
        assert not re.search(r"[一-鿿]", e), e
    assert '<script src="identities.js" defer></script>\n<script src="account.js" defer></script>' in html
# ---------------------------------------------------------------------------------------------------- referral codes (launch)

def _active_with_referral(conn, monkeypatch, email):
    """An active user (redeemed an admin code) and their referral code (plaintext)."""
    monkeypatch.setenv("BODY_REFERRALS_ENABLED", "1")
    uid = _pending_ident(conn, "email", email, email)
    assert invites.redeem(conn, uid, invites.create_code(conn, "seed", 1)["code"])
    return uid, invites.referral_code(conn, uid)["code"]


def test_referral_rules_daily_cap_total_cap_disabled_switch_and_revoke(dsn, monkeypatch):
    from body_engine.account import params
    monkeypatch.setattr(params, "REFERRAL_DAILY_REDEEMS", 2)
    monkeypatch.setattr(params, "REFERRAL_MAX_USES", 3)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        inviter, code = _active_with_referral(conn, monkeypatch, "rules@example.com")
        friends = [_pending_ident(conn, "email", f"rules{i}@example.com", f"rules{i}@example.com") for i in range(5)]
        assert invites.redeem(conn, friends[0], code) and invites.redeem(conn, friends[1], code)
        assert not invites.redeem(conn, friends[2], code)                  # 3rd in 24 hours: daily cap
        conn.execute("""UPDATE invite_redemptions SET redeemed_at = now() - interval '25 hours'
                        WHERE inviter_user_id = %s""", (inviter,))
        assert invites.redeem(conn, friends[2], code)                      # a day later: allowed again
        assert not invites.redeem(conn, friends[3], code)                  # total cap (3) reached
        info = invites.referral_code(conn, inviter)
        assert (info["uses"], info["joined"], info["remaining"], info["expires_at"]) == (3, 3, 0, None)
        # raising the limit in params applies to the existing code
        monkeypatch.setattr(params, "REFERRAL_MAX_USES", 4)
        assert invites.referral_code(conn, inviter)["remaining"] == 1
        # switch off: referral codes stop, admin codes still work
        monkeypatch.setenv("BODY_REFERRALS_ENABLED", "0")
        assert not invites.redeem(conn, friends[3], code)
        monkeypatch.setenv("BODY_REFERRALS_ENABLED", "1")
        # a disabled inviter's code stops working
        conn.execute("UPDATE users SET status = 'disabled' WHERE id = %s", (inviter,))
        assert not invites.redeem(conn, friends[3], code)
        conn.execute("UPDATE users SET status = 'active' WHERE id = %s", (inviter,))
        assert invites.redeem(conn, friends[3], code)
        # the admin revokes it: it stops, and no new code is handed out to replace it
        batch = next(b for b in invites.list_batches(conn, "referral") if b["owner_user_id"] == inviter)
        invites.revoke(conn, code_id=conn.execute("SELECT id FROM invite_codes WHERE batch_id = %s", (batch["id"],)).fetchone()["id"])
        assert not invites.redeem(conn, friends[4], code)
        with pytest.raises(ValueError, match="revoked"):
            invites.referral_code(conn, inviter)
        assert conn.execute("SELECT count(*) AS n FROM invite_rewards WHERE user_id = %s AND state = 'pending'",
                            (inviter,)).fetchone()["n"] == 4


def test_referral_list_shows_who_invited_whom(dsn, monkeypatch):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        inviter, code = _active_with_referral(conn, monkeypatch, "lister@example.com")
        conn.execute("UPDATE users SET display_name = 'Inviter' WHERE id = %s", (inviter,))
        friend = _pending_ident(conn, "apple", "000111.sub.4242", "relay@privaterelay.example.com")
        assert invites.redeem(conn, friend, code)
        refs = invites.list_batches(conn, "referral")
        assert refs and all(b["kind"] == "referral" for b in refs)
        assert not any(b["kind"] == "referral" for b in invites.list_batches(conn, "admin"))
        b = next(b for b in refs if b["owner_user_id"] == inviter)
        assert b["owner_name"] == "Inviter" and b["remaining"] == b["capacity"] - 1
        assert b["redemptions"] == [{**b["redemptions"][0], "user_id": friend, "inviter_user_id": inviter,
                                     "identities": [{"provider": "apple", "hint": "r***@p***.com"}]}]


def test_referral_code_through_the_api_and_web(client, dsn, _env, monkeypatch):
    """A friend signs up with a referral code through /v1/account/redeem-invite (the same call the iOS app's native
    onboarding and the Today page's invite step make); the Me page row and the ?invite= prefill are in place."""
    monkeypatch.setenv("BODY_REFERRALS_ENABLED", "1")
    client.cookies.clear()
    s = _email_login(client, "host@example.com", "app")
    h = {"Authorization": f"Bearer {s['token']}"}
    assert client.get("/v1/users/me/invite-code", headers=h).status_code == 403       # pending: no code yet
    assert client.post("/v1/account/redeem-invite", headers=h, json={"code": _batch(dsn)["codes"][0]}).status_code == 200
    mine = client.get("/v1/users/me/invite-code", headers=h).json()
    g = _email_login(client, "guest@example.com", "app", "iPhone")
    gh = {"Authorization": f"Bearer {g['token']}"}
    typed = mine["code"].lower().replace("-", " ")                                      # typed loosely, like the app allows
    r = client.post("/v1/account/redeem-invite", headers=gh, json={"code": typed})
    assert r.status_code == 200 and r.json()["status"] == "active"
    again = client.get("/v1/users/me/invite-code", headers=h).json()
    assert again["code"] == mine["code"] and again["joined"] == 1 and again["remaining"] == mine["remaining"] - 1
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        red = conn.execute("SELECT inviter_user_id FROM invite_redemptions WHERE user_id = %s", (g["me"]["id"],)).fetchone()
        assert red["inviter_user_id"] == s["me"]["id"]
        assert conn.execute("SELECT state FROM invite_rewards WHERE invitee_user_id = %s", (g["me"]["id"],)).fetchone()["state"] == "pending"
    monkeypatch.setenv("BODY_REFERRALS_ENABLED", "0")
    assert client.get("/v1/users/me/invite-code", headers=h).status_code == 404       # switched off: hidden
    js = client.get("/app/account.js").text
    for needle in ('id="me-invite" hidden', "/v1/users/me/invite-code", "referrals", 'get("invite")', "sessionStorage", "navigator.share"):
        assert needle in js, needle
    assert "好友的邀请码" in client.get("/app/").text
    client.cookies.clear()


def test_referral_and_invite_step_texts_are_bilingual(client):
    """Every tr("中文") key in account.js and every data-i18n key of the invite step has English in #20's i18n-en.js
    (the Chinese source text is the key, {name} placeholders match)."""
    import re
    js = client.get("/app/account.js").text
    table = client.get("/app/i18n-en.js").text
    en = dict(re.findall(r'^\s*"([^"]+)": "([^"]+)",$', table, re.M))
    keys = set(re.findall(r'\btr\("([^"]+)"', js))
    html = client.get("/app/").text
    step = html[html.index('<form id="ls-invite"'):html.index("</form>", html.index('<form id="ls-invite"'))]
    keys |= set(re.findall(r'data-i18n(?:-aria)?="([^"]+)"', step))
    keys = {k for k in keys if re.search(r"[\u4e00-\u9fff]", k)}   # dotted keys (err.request) live in i18n.js
    assert keys and keys <= set(en), keys - set(en)
    for zh, e in en.items():
        assert set(re.findall(r"\{(\w+)\}", zh)) == set(re.findall(r"\{(\w+)\}", e)), zh
        assert not re.search(r"[\u4e00-\u9fff]", e), e
    assert "Invite friends" in en.values()


def test_login_screen_layout_issue_36(client):
    """Issue #36: every step's error sits right under its field (account.js marks that field aria-invalid); the language
    switch and the sign-in card are styled in app.css (the switch had lost its CSS in a merge); motion respects
    prefers-reduced-motion; a centred card from 720px."""
    import re
    html = client.get("/app/").text
    login = html[html.index('<div id="login"'):html.index('<div id="sheet"')]
    for field, err in (("le-email", "le-email-error"), ("le-code", "le-code-error"), ("ap-code", "ap-error"), ("token", "login-error")):
        m = re.search(rf'<input id="{field}"[^>]*>\s*<p class="login-error" id="{err}"', login)
        assert m, f"#{err} is not right under #{field}"
    css = client.get("/app/app.css").text
    for rule in (".login-lang {", '.login-lang button[aria-pressed="true"]', '.login-card .input[aria-invalid="true"]',
                 ".login-card .login-error:empty", "@keyframes login-in", "prefers-reduced-motion: reduce", "@media (min-width: 720px)"):
        assert rule in css, rule
    js = client.get("/app/account.js").text
    assert 'field.setAttribute("aria-invalid"' in js and 'box.className = "login-lang"' in js
