"""Sign in with Apple token revocation on account deletion (account/apple_tokens.py, guideline 5.1.1(v)).

A fake Apple (/auth/token, /auth/revoke) runs on 127.0.0.1 in a thread: nothing leaves the machine. Unit tests need no
database; the end-to-end ones use their own test database (body_test_apple_revoke) and skip without Postgres.
"""
import base64
import hashlib
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api
from body_engine.account import apple_tokens, delete as account_delete, identities, signin
from body_engine.ai import crypto
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.web import login_limit

TEST_DB = "body_test_apple_revoke"
AUD = "com.example.watch2whoop"
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_KEY = ec.generate_private_key(ec.SECP256R1())


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _jwks():
    n = RSA_KEY.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "kid": "K1", "n": _b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")),
                      "e": _b64(n.e.to_bytes(3, "big"))}]}


def apple_token(sub, email="someone@example.com", nonce="raw-nonce"):
    head = _b64(json.dumps({"alg": "RS256", "kid": "K1"}).encode())
    claims = {"iss": signin.APPLE_ISSUER, "aud": AUD, "sub": sub, "iat": int(time.time()), "exp": int(time.time()) + 600,
              "email": email, "email_verified": "true", "nonce": hashlib.sha256(nonce.encode()).hexdigest()}
    body = _b64(json.dumps(claims).encode())
    return f"{head}.{body}.{_b64(RSA_KEY.sign(f'{head}.{body}'.encode(), padding.PKCS1v15(), hashes.SHA256()))}"


# ---------------------------------------------------------------------------------------------------- fake Apple

class FakeApple:
    def __init__(self):
        self.calls, self.fail_revoke, self.counter = [], False, 0
        apple = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
                apple.calls.append((self.path, form))
                if self.path == "/auth/token" and form.get("code", "").startswith("good"):
                    apple.counter += 1
                    out, status = {"access_token": "a", "refresh_token": f"refresh-{apple.counter}", "id_token": "x"}, 200
                elif self.path == "/auth/revoke" and not apple.fail_revoke:
                    out, status = None, 200
                else:
                    out, status = {"error": "invalid_grant"}, 400
                data = b"" if out is None else json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def paths(self):
        return [p for p, _ in self.calls]


@pytest.fixture(scope="module")
def fake_apple():
    f = FakeApple()
    yield f
    f.server.shutdown()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path, fake_apple):
    login_limit.reset()
    signin.reset_jwks_cache()
    fake_apple.calls.clear()
    fake_apple.fail_revoke = False
    master = tmp_path / "ai_master_key"
    master.write_text(base64.b64encode(b"k" * 32).decode())
    key = tmp_path / "apple_signin_key.p8"
    key.write_bytes(EC_KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    monkeypatch.setenv("BODY_AI_MASTER_KEY_FILE", str(master))
    monkeypatch.setenv("BODY_APPLE_AUDIENCES", AUD)
    monkeypatch.setenv("BODY_APPLE_SIGNIN_KEY_FILE", str(key))
    monkeypatch.setenv("BODY_APPLE_SIGNIN_KEY_ID", "KEYID12345")
    monkeypatch.setenv("BODY_APPLE_TEAM_ID", "TEAM123456")
    monkeypatch.delenv("BODY_APPLE_CLIENT_ID", raising=False)
    monkeypatch.setattr(signin, "_fetch_jwks", _jwks)
    monkeypatch.setattr(apple_tokens, "APPLE_AUTH_BASE", fake_apple.base)
    yield
    login_limit.reset()


def _unconfigure(monkeypatch):
    monkeypatch.delenv("BODY_APPLE_SIGNIN_KEY_ID")


# ---------------------------------------------------------------------------------------------------- unit

def test_client_secret_is_a_valid_es256_jwt():
    cfg = apple_tokens.load_config()
    assert cfg is not None and cfg.client_id is None
    jwt = apple_tokens.client_secret(cfg, AUD, now=1_700_000_000)
    head_b64, claims_b64, sig_b64 = jwt.split(".")
    assert json.loads(_b64d(head_b64)) == {"alg": "ES256", "kid": "KEYID12345"}
    assert json.loads(_b64d(claims_b64)) == {"iss": "TEAM123456", "iat": 1_700_000_000, "exp": 1_700_000_300,
                                             "aud": apple_tokens.APPLE_AUTH_BASE, "sub": AUD}
    raw = _b64d(sig_b64)
    assert len(raw) == 64
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    EC_KEY.public_key().verify(der, f"{head_b64}.{claims_b64}".encode(), ec.ECDSA(hashes.SHA256()))


def test_config_needs_key_id_team_and_file(monkeypatch, tmp_path):
    assert apple_tokens.load_config() is not None
    monkeypatch.setenv("BODY_APPLE_SIGNIN_KEY_FILE", str(tmp_path / "missing.p8"))
    assert apple_tokens.load_config() is None
    _unconfigure(monkeypatch)
    assert apple_tokens.load_config() is None


def test_ciphertext_is_bound_to_its_subject():
    blob = crypto.encrypt_with("apple_signin_tokens:subject:a", "secret-refresh")
    assert b"secret-refresh" not in blob
    assert crypto.decrypt_with("apple_signin_tokens:subject:a", blob) == "secret-refresh"
    with pytest.raises(Exception):
        crypto.decrypt_with("apple_signin_tokens:subject:b", blob)


def test_exchange_without_code_or_config_does_nothing(monkeypatch, fake_apple):
    claims = {"sub": "s", "aud": AUD}
    assert apple_tokens.exchange_and_store(None, 1, claims, None) == "no_code"
    _unconfigure(monkeypatch)
    assert apple_tokens.exchange_and_store(None, 1, claims, "good-code") == "not_configured"
    assert fake_apple.calls == []


def test_exchange_failure_never_raises(fake_apple):
    assert apple_tokens.exchange_and_store(None, 1, {"sub": "s", "aud": AUD}, "bad-code") == "failed"
    path, form = fake_apple.calls[-1]
    assert path == "/auth/token" and form["grant_type"] == "authorization_code" and form["client_id"] == AUD
    assert form["client_secret"].count(".") == 2


# ---------------------------------------------------------------------------------------------------- end to end

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


def _sign_in(client, sub, code):
    body = {"identity_token": apple_token(sub), "nonce": "raw-nonce", "client": "app"}
    if code:
        body["authorization_code"] = code
    r = client.post("/v1/auth/apple", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _tokens(dsn, **where):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        col, val = next(iter(where.items()))
        return conn.execute(f"SELECT * FROM apple_signin_tokens WHERE {col} = %s", (val,)).fetchall()


def _delete(client, token):
    r = client.request("DELETE", "/v1/account", headers={"Authorization": f"Bearer {token}"}, json={"confirm": "DELETE"})
    assert r.status_code == 200, r.text
    return r.json()


def test_sign_in_stores_encrypted_token_and_deletion_revokes_it(client, dsn, fake_apple):
    s = _sign_in(client, "apple-revoke-1", "good-1")
    uid = s["me"]["id"]
    rows = _tokens(dsn, user_id=uid)
    assert len(rows) == 1 and rows[0]["subject"] == "apple-revoke-1" and rows[0]["client_id"] == AUD
    assert b"refresh-" not in bytes(rows[0]["refresh_token_enc"])
    stored = crypto.decrypt_with("apple_signin_tokens:subject:apple-revoke-1", rows[0]["refresh_token_enc"])
    # a later sign-in replaces the token (one row per Apple ID)
    _sign_in(client, "apple-revoke-1", "good-2")
    rows = _tokens(dsn, user_id=uid)
    assert len(rows) == 1
    latest = crypto.decrypt_with("apple_signin_tokens:subject:apple-revoke-1", rows[0]["refresh_token_enc"])
    assert latest != stored
    fake_apple.calls.clear()
    assert _delete(client, s["token"]) == {"deleted": True, "leftovers": 0}
    assert fake_apple.paths() == ["/auth/revoke"]
    form = fake_apple.calls[0][1]
    assert form["token"] == latest and form["token_type_hint"] == "refresh_token" and form["client_id"] == AUD
    assert _tokens(dsn, subject="apple-revoke-1") == []


def test_not_configured_is_reported_and_deletion_goes_on(client, dsn, fake_apple, monkeypatch):
    s = _sign_in(client, "apple-revoke-2", "good-x")
    _unconfigure(monkeypatch)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        res = account_delete.delete_account(conn, s["me"]["id"])
        assert conn.execute("SELECT 1 FROM users WHERE id = %s", (s["me"]["id"],)).fetchone() is None
    assert res["deleted"] and res["leftovers"] == [{"system": "apple", "id": "···ke-2", "error": "not_configured"}]
    assert "/auth/revoke" not in fake_apple.paths()
    assert _tokens(dsn, subject="apple-revoke-2") == []


def test_no_stored_token_and_apple_failure_are_leftovers(client, dsn, fake_apple):
    old = _sign_in(client, "apple-revoke-3", None)       # an older app that sends no code: nothing stored
    assert _tokens(dsn, user_id=old["me"]["id"]) == []
    assert _delete(client, old["token"]) == {"deleted": True, "leftovers": 1}
    fake_apple.fail_revoke = True
    s = _sign_in(client, "apple-revoke-4", "good-y")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        res = account_delete.delete_account(conn, s["me"]["id"])
    assert len(res["leftovers"]) == 1 and res["leftovers"][0]["system"] == "apple"
    assert "400" in res["leftovers"][0]["error"]
    assert _tokens(dsn, subject="apple-revoke-4") == []


def test_email_only_account_never_calls_apple(client, dsn, fake_apple):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        user, _ = signin.find_or_create_user(conn, "email", "plain@example.com", "plain@example.com", True)
        conn.commit()
        res = account_delete.delete_account(conn, user["id"])
    assert res["leftovers"] == [] and fake_apple.calls == []


def test_token_follows_a_moved_identity_and_pending_purge_revokes(client, dsn, fake_apple):
    s = _sign_in(client, "apple-revoke-5", "good-z")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        target = conn.execute("INSERT INTO users (display_name, role, status) VALUES ('Target', 'member', 'active') "
                              "RETURNING id").fetchone()["id"]
        identities._link(conn, target, "apple", "apple-revoke-5", None, True, move=True, actor="test")
        conn.commit()
    assert [r["user_id"] for r in _tokens(dsn, subject="apple-revoke-5")] == [target]
    # a stale pending account that signed in with Apple: the purge revokes its token too
    p = _sign_in(client, "apple-revoke-6", "good-p")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE users SET created_at = now() - interval '8 days' WHERE id = %s", (p["me"]["id"],))
        conn.commit()
        fake_apple.calls.clear()
        assert account_delete.purge_pending(conn) >= 1
    assert fake_apple.paths().count("/auth/revoke") >= 1
    assert _tokens(dsn, subject="apple-revoke-6") == []


def test_binding_apple_while_signed_in_stores_the_token(client, dsn, fake_apple):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = conn.execute("INSERT INTO users (display_name, role, status) VALUES ('Binder', 'member', 'active') "
                           "RETURNING id").fetchone()["id"]
        from body_engine import auth
        token = auth.create_token(conn, uid, "t", ["read", "write"])
        conn.commit()
    r = client.post("/v1/account/identities/apple", headers={"Authorization": f"Bearer {token}"},
                    json={"identity_token": apple_token("apple-revoke-7"), "nonce": "raw-nonce",
                          "authorization_code": "good-b"})
    assert r.status_code == 200, r.text
    assert [r["user_id"] for r in _tokens(dsn, subject="apple-revoke-7")] == [uid]
