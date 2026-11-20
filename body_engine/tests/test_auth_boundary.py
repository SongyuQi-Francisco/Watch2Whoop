"""Auth boundary of the whole API: the route table against public_routes.py, and negative tests for every endpoint that
issues a credential (CONTRIBUTING.md: "An endpoint that issues a token, code or session never grants more than the
caller has, and a negative-permission test covers it").

1. Route table: every route of the app is called with no credentials, a revoked token, an expired token and a wrong
   Authorization header; anything that answers other than 401 / 403 must be in public_routes.PUBLIC_ROUTES, every listed
   route must exist, every mount must be listed. The restricted tokens (AI-client mcp, upload-only ingest) authenticate
   nowhere but their own endpoints. The path-prefix lists that loosen checks (pending accounts, the upgrade block)
   only name prefixes that exist.
2. Issuers: for each endpoint that hands out a token, a code, a session or a sign-in method, the cases that apply of
   - no credential, - someone else's credential / data, - a scope that is not enough (read-only, restricted token),
   - a share that does not cover it, - a revoked / used / expired credential,
   and that what it hands out never carries more than the caller had. The table of endpoints and cases is in the pull
   request that added this file; cases that cannot apply to an endpoint (a public sign-in endpoint has no "someone
   else's share") are left out there with the reason.
3. The Shortcut receiver (health_sync/health_server.py): tokens required by default.

Separate test database BODY_TEST_DB_NAME_AUTHB (default body_test_auth_boundary), dropped and recreated every run;
skipped without Postgres. Synthetic users only; no external service is called.
"""
import http.client
import importlib.util
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from starlette.routing import Mount

from body_engine import api, auth, plugins, public_routes
from body_engine.account import api as account_api, mailer, params as account_params, signin
from body_engine.ai import tokens as ai_tokens
from body_engine.client import params as client_params
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.web import activation, login_codes, login_limit

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_AUTHB", "body_test_auth_boundary")
REPO = Path(__file__).resolve().parents[2]
CSRF = {"X-Body-Web": "1"}


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def dsn():
    if not TEST_DB.startswith("body_test_"):
        pytest.exit("the test DB must be a body_test_* database")
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


def db(dsn):
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


@pytest.fixture(scope="module")
def world(dsn):
    """A and B are active, P is a pending sign-up. A shares scores / sleep / memory with B (nothing account-level)."""
    now = datetime.now(timezone.utc)
    with db(dsn) as conn:
        ids = {}
        for n, status in (("A", "active"), ("B", "active"), ("P", "pending")):
            ids[n] = conn.execute("""INSERT INTO users (display_name, role, consent_at, timezone, status)
                                     VALUES (%s, 'member', %s, 'Australia/Sydney', %s) RETURNING id""",
                                  (n, now, status)).fetchone()["id"]
        tok = {"A": auth.create_token(conn, ids["A"], "ios:signin:iPhone", ["read", "write"]),
               "A_ro": auth.create_token(conn, ids["A"], "ios:signin:iPad", ["read"]),
               "A_revoked": auth.create_token(conn, ids["A"], "old-phone", ["read", "write"]),
               "A_expired": auth.create_token(conn, ids["A"], "expired", ["read", "write"]),
               "A_mcp": ai_tokens.create(conn, ids["A"], "claude-code")["token"],
               "A_ingest": auth.create_token(conn, ids["A"], "app:ingest:iPhone", [auth.INGEST_SCOPE]),
               "B": auth.create_token(conn, ids["B"], "ios:signin:iPhone", ["read", "write"]),
               "P": auth.create_token(conn, ids["P"], "ios:signin:iPhone", ["read", "write"])}
        auth.revoke_tokens(conn, ids["A"], "old-phone")
        conn.execute("UPDATE api_tokens SET expires_at = now() - interval '1 minute' WHERE name = 'expired'")
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["A"], ids["B"], ["scores", "sleep", "memory"]))
        flags.set_flag(conn, auth.CIRCLES_FLAG, "all", actor="test")
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn, world):
    mp = pytest.MonkeyPatch()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    with TestClient(api.app, base_url="https://testserver", raise_server_exceptions=False) as c:
        yield c
    mp.undo()


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    login_limit.reset()   # the negative tests count as failed logins from one address
    mailer.OUTBOX.clear()
    monkeypatch.setenv("BODY_MAIL_MODE", "console")
    yield
    login_limit.reset()


def fresh_client() -> TestClient:
    """Another client on the same app and pool: its own cookie jar."""
    return TestClient(api.app, base_url="https://testserver", raise_server_exceptions=False)


def session_client(client, token: str) -> TestClient:
    c = fresh_client()
    r = c.post("/v1/web/login", json={"token": token})
    assert r.status_code == 200, r.text
    return c


def token_scopes(dsn, token: str) -> list[str] | None:
    with db(dsn) as conn:
        row = conn.execute("SELECT scopes FROM api_tokens WHERE token_sha256 = %s", (auth.hash_token(token),)).fetchone()
    return None if row is None else sorted(row["scopes"])


# ================================================================ 1. the route table
def all_routes(app) -> set[tuple[str, str]]:
    """(method, path) of every HTTP route (routers FastAPI includes lazily too, and the OpenAPI schema's paths)."""
    out = set()
    for r in app.routes:
        for c in (r.effective_candidates() if hasattr(r, "effective_candidates") else [r]):
            if getattr(c, "methods", None) and isinstance(getattr(c, "path", None), str):
                out |= {(m, c.path) for m in c.methods}
    for path, item in app.openapi()["paths"].items():
        out |= {(m.upper(), path) for m in item if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE")}
    return {(m, p) for m, p in out if m not in ("HEAD", "OPTIONS")}


def mounts(app) -> set[str]:
    return {r.path for r in app.routes if isinstance(r, Mount)}


def concrete(route: str) -> str:
    path = route.replace("{user}", "me")
    path = re.sub(r"\{[^}]+:path\}", "x", path)
    return re.sub(r"\{[^}]+\}", "1", path)


def sweep(headers: dict) -> dict[tuple[str, str], int]:
    c = fresh_client()
    return {(m, route): c.request(m, concrete(route), headers=headers).status_code
            for m, route in sorted(all_routes(api.app))}


def test_public_list_matches_the_route_table(client):
    routes = all_routes(api.app)
    assert len(routes) > 250
    missing = sorted(set(public_routes.PUBLIC_ROUTES) - routes)
    assert missing == [], f"listed in public_routes.py but not a route any more: {missing}"
    assert all(why.strip() for why in public_routes.PUBLIC_ROUTES.values())
    assert mounts(api.app) == set(public_routes.PUBLIC_MOUNTS), "a mount was added or removed: list it in public_routes.py"


@pytest.mark.parametrize("who", ["none", "revoked", "expired", "garbage", "not-bearer"])
def test_every_unlisted_route_refuses_without_a_valid_login(client, world, who):
    _, tok = world
    headers = {"none": {}, "revoked": H(tok["A_revoked"]), "expired": H(tok["A_expired"]),
               "garbage": H("be_not-a-token"), "not-bearer": {"Authorization": "Basic YTpi"}}[who]
    open_ = {k: v for k, v in sweep(headers).items() if v not in (401, 403) and k not in public_routes.PUBLIC_ROUTES}
    assert open_ == {}, f"answer without a login but are not in public_routes.py: {open_}"


def test_boot_needs_a_login(client):
    r = client.post("/v1/web/boot", json={"paths": ["/v1/users/me/scores"]})
    assert r.status_code == 401


@pytest.mark.parametrize("kind,own", [("A_mcp", set()),
                                      ("A_ingest", {("POST", "/v1/ingest/healthkit"), ("GET", "/v1/ingest/healthkit")})])
def test_restricted_tokens_authenticate_only_their_own_endpoints(client, world, kind, own):
    """An AI-client token works on /mcp only, an upload token on /v1/ingest/* only: every other route treats them like
    no login (X-Body-User is set only once a request is authenticated, obs.py)."""
    ids, tok = world
    c = fresh_client()
    authed = set()
    for m, route in sorted(all_routes(api.app)):
        if route == "/mcp":   # its own endpoint (a valid AI token opens the stream there); test_mcp_* cover it
            continue
        r = c.request(m, concrete(route), headers=H(tok[kind]))
        if r.headers.get("x-body-user"):
            authed.add((m, route))
    assert {(m, p) for m, p in authed if not p.startswith("/v1/ingest/")} == set()
    assert own <= authed or kind == "A_mcp"


def test_loosening_prefix_lists_name_real_routes():
    """account_api.PENDING_OK (what a pending account may reach) and the upgrade block's EXEMPT_PREFIXES only list
    prefixes some route has: a renamed route must not leave a stale exemption behind."""
    paths = {p for _, p in all_routes(api.app)}
    for prefix in (*account_api.PENDING_OK, *client_params.EXEMPT_PREFIXES):
        p = prefix.rstrip("/")
        assert any(x == p or x.startswith(p + "/") for x in paths), prefix


# ================================================================ 2. issuers
# ---- POST /v1/web/login: API token -> Today page cookie
def test_web_login(client, world):
    _, tok = world
    for bad in ("be_not-a-token-at-all", tok["A_revoked"], tok["A_expired"], tok["A_mcp"], tok["A_ingest"]):
        r = fresh_client().post("/v1/web/login", json={"token": bad})
        assert r.status_code == 401 and "set-cookie" not in r.headers
        login_limit.reset()
    assert fresh_client().post("/v1/web/login", json={}).status_code == 422
    # a read-only token gives a read-only session
    c = session_client(client, tok["A_ro"])
    assert c.get("/v1/me").json()["token_scopes"] == ["read"]


# ---- POST /v1/web/login-code: one-time code -> device token (app) or cookie (web)
def test_login_code_redeem(client, world, dsn):
    ids, _ = world
    with db(dsn) as conn:
        ro = login_codes.create(conn, ids["A"], "ro", scopes=["read"])
        used = login_codes.create(conn, ids["A"], "used")
        expired = login_codes.create(conn, ids["A"], "expired")
        conn.execute("UPDATE web_login_codes SET expires_at = now() - interval '1 second' WHERE label = 'expired'")
    c = fresh_client()
    assert c.post("/v1/web/login-code", json={"code": "ABCDEFGHJKLM", "client": "app"}).status_code == 401
    r = c.post("/v1/web/login-code", json={"code": used, "client": "app"})
    assert r.status_code == 200 and r.json()["id"] == ids["A"]
    assert c.post("/v1/web/login-code", json={"code": used, "client": "app"}).status_code == 401   # one use
    assert c.post("/v1/web/login-code", json={"code": expired}).status_code == 401
    # never more than the code: a read-only code gives a read-only token
    r = c.post("/v1/web/login-code", json={"code": ro, "client": "app"})
    assert r.status_code == 200 and token_scopes(dsn, r.json()["token"]) == ["read"]
    # a sign-in code is not a watch code, and the other way round
    with db(dsn) as conn:
        login = login_codes.create(conn, ids["A"], "for-watch")
    assert c.post("/v1/web/login-code", json={"code": login, "client": "watch"}).status_code == 401


# ---- POST /v1/account/web-login-code: signed-in app -> one-time code for its web view
def test_web_login_code_mint(client, world, dsn):
    ids, tok = world
    assert client.post("/v1/account/web-login-code").status_code == 401
    for bad in ("A_revoked", "A_expired", "A_mcp", "A_ingest"):
        assert client.post("/v1/account/web-login-code", headers=H(tok[bad])).status_code == 401, bad
    for who, scopes in (("A", ["read", "write"]), ("A_ro", ["read"])):
        code = client.post("/v1/account/web-login-code", headers=H(tok[who])).json()["code"]
        r = fresh_client().post("/v1/web/login-code", json={"code": code, "client": "app"})
        assert r.status_code == 200 and r.json()["id"] == ids["A"]
        assert token_scopes(dsn, r.json()["token"]) == scopes, who


# ---- POST /v1/web/ingest-token: Today page session -> upload-only token
def test_ingest_token(client, world, dsn):
    _, tok = world
    assert fresh_client().post("/v1/web/ingest-token", headers=CSRF, json={}).status_code == 401
    assert client.post("/v1/web/ingest-token", headers=H(tok["A"]), json={}).status_code == 403     # Bearer: not a session
    for bad in ("A_revoked", "A_expired", "A_mcp", "A_ingest"):
        assert client.post("/v1/web/ingest-token", headers=H(tok[bad]), json={}).status_code == 401, bad
    ro = session_client(client, tok["A_ro"])
    assert ro.post("/v1/web/ingest-token", headers=CSRF, json={}).status_code == 403
    rw = session_client(client, tok["A"])
    r = rw.post("/v1/web/ingest-token", headers=CSRF, json={"device": "iPhone-test"})
    assert r.status_code == 200 and token_scopes(dsn, r.json()["token"]) == [auth.INGEST_SCOPE]
    # the upload token itself reads nothing and signs nothing in
    up = r.json()["token"]
    assert client.get("/v1/me", headers=H(up)).status_code == 401
    assert fresh_client().post("/v1/web/login", json={"token": up}).status_code == 401
    # read-only logins never upload
    assert client.post("/v1/ingest/healthkit", headers=H(tok["A_ro"]), json={}).status_code == 401


# ---- POST /v1/account/watch-code -> POST /v1/web/login-code client=watch
def test_watch_code(client, world, dsn):
    _, tok = world
    assert client.post("/v1/account/watch-code").status_code == 401
    for bad in ("A_revoked", "A_expired", "A_mcp", "A_ingest"):
        assert client.post("/v1/account/watch-code", headers=H(tok[bad])).status_code == 401, bad
    s = session_client(client, tok["A"])
    assert s.post("/v1/account/watch-code", headers=CSRF).status_code == 403   # bearer_only
    r = client.post("/v1/account/watch-code", headers=H(tok["A_ro"]))
    assert r.status_code == 200 and r.json()["scopes"] == ["read"]
    w = fresh_client().post("/v1/web/login-code", json={"code": r.json()["code"], "client": "watch"})
    assert w.status_code == 200 and token_scopes(dsn, w.json()["token"]) == ["read", auth.WATCH_SCOPE]
    watch = w.json()["token"]
    # a watch token cannot mint anything
    for path in ("/v1/account/watch-code", "/v1/account/web-login-code"):
        assert client.post(path, headers=H(watch)).status_code == 401
    assert client.post("/v1/users/me/ai-tokens", headers=H(watch), json={"client": "x"}).status_code == 401


# ---- POST /v1/users/{user}/ai-tokens: AI-client (MCP) token
def test_ai_token(client, world, dsn):
    ids, tok = world
    body = {"client": "boundary-test"}
    assert client.post("/v1/users/me/ai-tokens", json=body).status_code == 401
    for bad in ("A_revoked", "A_expired", "A_mcp", "A_ingest"):
        assert client.post("/v1/users/me/ai-tokens", headers=H(tok[bad]), json=body).status_code == 401, bad
    assert client.post("/v1/users/me/ai-tokens", headers=H(tok["A_ro"]), json=body).status_code == 403
    # B holds a share from A, but shares never cover AI settings
    assert client.post(f"/v1/users/{ids['A']}/ai-tokens", headers=H(tok["B"]), json=body).status_code == 403
    assert client.get(f"/v1/users/{ids['A']}/ai-tokens", headers=H(tok["B"])).status_code == 403
    r = client.post("/v1/users/me/ai-tokens", headers=H(tok["A"]), json={**body, "read_only": True})
    assert r.status_code == 201 and token_scopes(dsn, r.json()["token"]) == [auth.MCP_SCOPE, "read"]
    # B cannot revoke A's token either
    assert client.delete(f"/v1/users/{ids['A']}/ai-tokens/{r.json()['id']}", headers=H(tok["B"])).status_code == 403


def test_expired_ai_token_is_dead_on_mcp(client, world, dsn):
    ids, tok = world
    with db(dsn) as conn:
        t = ai_tokens.create(conn, ids["A"], "expiring")
        conn.execute("UPDATE api_tokens SET expires_at = now() - interval '1 second' WHERE id = %s", (t["id"],))
    r = client.post("/mcp", headers={**H(t["token"]), "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


# ---- POST /v1/oauth/requests/{req}: approving an AI connector (mints an OAuth grant)
def test_oauth_decision_needs_write(client, world):
    _, tok = world
    body = {"approve": True}
    assert client.post("/v1/oauth/requests/x", json=body).status_code == 401
    for bad in ("A_revoked", "A_expired", "A_mcp", "A_ingest"):
        assert client.post("/v1/oauth/requests/x", headers=H(tok[bad]), json=body).status_code == 401, bad
    assert client.post("/v1/oauth/requests/x", headers=H(tok["A_ro"]), json=body).status_code == 403
    assert client.post("/v1/oauth/requests/x", headers=H(tok["A"]), json=body).status_code == 404   # unknown request


# ---- POST /v1/web/me-links/{key}/open: may carry a one-time login code for a plugin's page
def test_me_link_code_never_more_than_the_caller(client, world, dsn, monkeypatch):
    ids, tok = world
    monkeypatch.setattr(plugins, "me_links", lambda conn, viewer: [
        {"key": "k", "title": "t", "url": "https://testserver/app/x.html", "login": True}])
    assert client.post("/v1/web/me-links/k/open").status_code == 401
    assert client.post("/v1/web/me-links/k/open", headers=H(tok["A_revoked"])).status_code == 401
    assert "#login=" not in client.post("/v1/web/me-links/k/open", headers=H(tok["A_ro"])).json()["url"]
    s = session_client(client, tok["A"])
    assert "#login=" not in s.post("/v1/web/me-links/k/open", headers=CSRF).json()["url"]
    url = client.post("/v1/web/me-links/k/open", headers=H(tok["A"])).json()["url"]
    code = re.search(r"#login=([A-Z0-9]+)", url).group(1)
    r = fresh_client().post("/v1/web/login-code", json={"code": code, "client": "app"})
    assert r.status_code == 200 and r.json()["id"] == ids["A"]


# ---- POST /v1/web/activate: reusable activation code -> one-time setup link
def test_activation_code(client, world, dsn):
    ids, _ = world
    with db(dsn) as conn:
        act = activation.create(conn, ids["A"], label="boundary")["code"]
        dead = activation.create(conn, ids["A"], label="dead")["code"]
        conn.execute("UPDATE activation_codes SET revoked_at = now() WHERE label = 'dead'")
    c = fresh_client()
    assert c.post("/v1/web/activate", json={"code": "ABCDEFGHJKLMNPQR", "web": True}).status_code == 401
    assert c.post("/v1/web/activate", json={"code": dead, "web": True}).status_code == 401
    login_limit.reset()
    r = c.post("/v1/web/activate", json={"code": act, "web": True})
    assert r.status_code == 200, r.text


# ---- POST /v1/account/redeem-invite: a pending sign-up claims an existing account with that account's code
def test_claim_needs_a_code_that_can_write(client, world, dsn):
    """Claiming signs the pending person in to the account with read + write and moves their sign-in identity there,
    so a read-only login code (what a read-only login's web-login-code gives) must not do it."""
    ids, tok = world
    assert client.post("/v1/account/redeem-invite", json={"code": "ABCDEFGHJKLM"}).status_code == 401
    assert client.post("/v1/account/redeem-invite", headers=H(tok["A_revoked"]),
                       json={"code": "ABCDEFGHJKLM"}).status_code == 401
    ro_code = client.post("/v1/account/web-login-code", headers=H(tok["A_ro"])).json()["code"]
    r = client.post("/v1/account/redeem-invite", headers=H(tok["P"]), json={"code": ro_code})
    assert r.status_code == 401
    with db(dsn) as conn:
        assert conn.execute("SELECT status FROM users WHERE id = %s", (ids["P"],)).fetchone()["status"] == "pending"
        assert conn.execute("SELECT used_at FROM web_login_codes WHERE code_sha256 = %s",
                            (login_codes._sha(ro_code),)).fetchone()["used_at"] is None


# ---- POST /v1/account/identities/{email,apple}: binding a sign-in method
def test_binding_a_sign_in_method_needs_write(client, world, dsn, monkeypatch):
    """A bound e-mail or Apple ID signs in with read + write later, so a read-only login cannot add one."""
    _, tok = world
    with db(dsn) as conn:
        code = signin.new_email_code(conn, "boundary.ro@example.com")
    body = {"email": "boundary.ro@example.com", "code": code}
    assert client.post("/v1/account/identities/email", json=body).status_code == 401
    for bad in ("A_revoked", "A_expired", "A_mcp", "A_ingest"):
        assert client.post("/v1/account/identities/email", headers=H(tok[bad]), json=body).status_code == 401, bad
    r = client.post("/v1/account/identities/email", headers=H(tok["A_ro"]), json=body)
    assert r.status_code == 403 and r.json()["detail"] == "write_required"
    with db(dsn) as conn:   # refused before the code was spent, and nothing bound
        assert signin.check_email_code(conn, "boundary.ro@example.com", code)
        assert conn.execute("SELECT 1 FROM user_identities WHERE email = 'boundary.ro@example.com'").fetchone() is None
        code = signin.new_email_code(conn, "boundary.ro@example.com")
    r = client.post("/v1/account/identities/email", headers=H(tok["A"]), json={**body, "code": code})
    assert r.status_code == 200 and r.json()["result"] == "linked"
    monkeypatch.setenv("BODY_APPLE_AUDIENCES", "com.example.test")
    r = client.post("/v1/account/identities/apple", headers=H(tok["A_ro"]),
                    json={"identity_token": "x" * 40, "nonce": "n"})
    assert r.status_code == 403 and r.json()["detail"] == "write_required"


# ---- POST /v1/auth/email/verify, /v1/auth/apple: sign-in
def test_sign_in_rejects_bad_proofs(client, monkeypatch):
    c = fresh_client()
    r = c.post("/v1/auth/email/verify", json={"email": "nobody@example.com", "code": "123456"})
    assert r.status_code in (401, 503) and "set-cookie" not in r.headers
    monkeypatch.setenv("BODY_APPLE_AUDIENCES", "com.example.test")
    monkeypatch.setattr(signin, "_fetch_jwks", lambda: {"keys": []})
    r = c.post("/v1/auth/apple", json={"identity_token": "x" * 40, "nonce": "n"})
    assert r.status_code == 401 and "set-cookie" not in r.headers


# ---- POST /v1/account/ow-invitation: lets the Open Wearables SDK upload for this account
def test_ow_invitation_needs_write(client, world):
    _, tok = world
    assert client.post("/v1/account/ow-invitation").status_code == 401
    for bad in ("A_revoked", "A_expired", "A_mcp", "A_ingest"):
        assert client.post("/v1/account/ow-invitation", headers=H(tok[bad])).status_code == 401, bad
    r = client.post("/v1/account/ow-invitation", headers=H(tok["A_ro"]))
    assert (r.status_code, r.json()["detail"]) == (403, "write_required")
    assert client.post("/v1/account/ow-invitation", headers=H(tok["P"])).json()["detail"] == "account_pending"
    assert client.post("/v1/account/ow-invitation", headers=H(tok["A"])).status_code == 409   # no OW account in the test


# ---- GET /v1/users/{user}/invite-code: the referral code
def test_referral_code_is_own_only(client, world, monkeypatch):
    ids, tok = world
    monkeypatch.setenv(account_params.REFERRALS_ENV, "1")
    assert client.get("/v1/users/me/invite-code").status_code == 401
    assert client.get("/v1/users/me/invite-code", headers=H(tok["A_revoked"])).status_code == 401
    r = client.get(f"/v1/users/{ids['A']}/invite-code", headers=H(tok["B"]))
    assert (r.status_code, r.json()["detail"]) == (403, "own_code_only")


# ---- POST /v1/friends/circles/{id}/invite, POST /v1/friends/join: circle invite codes (they open circle sharing)
def test_circle_invites_need_write(client, world):
    _, tok = world
    for path, body in (("/v1/friends/circles/1/invite", {}), ("/v1/friends/join", {"code": "ABCDEFGH", "scopes": []})):
        assert client.post(path, json=body).status_code == 401, path
        for bad in ("A_revoked", "A_expired", "A_mcp", "A_ingest"):
            assert client.post(path, headers=H(tok[bad]), json=body).status_code == 401, (path, bad)
        assert client.post(path, headers=H(tok["A_ro"]), json=body).status_code == 403, path
    # someone else's circle: B cannot mint a code for a circle that is not theirs
    r = client.post("/v1/friends/circles/999999/invite", headers=H(tok["B"]), json={})
    assert r.status_code in (403, 404)


# ---- revoking: what a revoked phone token made stops with it
def test_revoking_ends_sessions_and_watches(client, world, dsn):
    ids, _ = world
    with db(dsn) as conn:
        t = auth.create_token(conn, ids["A"], "short-lived", ["read", "write"])
    s = session_client(client, t)
    w = client.post("/v1/account/watch-code", headers=H(t)).json()["code"]
    watch = fresh_client().post("/v1/web/login-code", json={"code": w, "client": "watch"}).json()["token"]
    with db(dsn) as conn:
        auth.revoke_tokens(conn, ids["A"], "short-lived")
        assert auth.user_for_watch_token(conn, watch) is None
    assert s.get("/v1/web/me").status_code == 401


# ================================================================ 3. the Shortcut receiver
@pytest.fixture
def receiver(tmp_path, monkeypatch):
    def start(**env):
        for k in ("HEALTH_SYNC_REQUIRE_TOKEN", "HEALTH_SYNC_ALLOW_NO_TOKEN", "BODY_LEGACY_SOURCES_CUTOFF"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("HEALTH_SYNC_BASE", str(tmp_path / "health_data"))
        monkeypatch.setenv("HEALTH_SYNC_SECRETS", str(tmp_path / "secrets"))
        monkeypatch.setenv("HEALTH_SYNC_IMPORT", "0")
        spec = importlib.util.spec_from_file_location("health_server_authb", REPO / "health_sync" / "health_server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        os.makedirs(mod.ARCHIVE, exist_ok=True)
        import hashlib
        mod.save_tokens({hashlib.sha256(b"good-token").hexdigest(): {"user_id": 5, "name": "good"},
                         hashlib.sha256(b"old-token").hexdigest(): {"user_id": 5, "name": "old", "revoked_at": "x"}})
        srv = mod.ThreadingHTTPServer(("127.0.0.1", 0), mod.H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)

        def post(headers=None):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            c.request("POST", "/upload?name=health", body=b"x", headers={"Content-Length": "1", **(headers or {})})
            r = c.getresponse()
            out = r.status
            r.read()
            c.close()
            return out
        return post, tmp_path / "health_data"
    servers = []
    yield start
    for s in servers:
        s.shutdown()
    sys.modules.pop("health_server_authb", None)


def test_receiver_requires_a_token_by_default(receiver):
    post, base = receiver()
    assert post() == 401
    assert post({"X-Upload-Token": "wrong"}) == 401
    assert post({"X-Upload-Token": "old-token"}) == 401   # revoked
    assert not list((base / "archive").glob("health_2*.txt"))   # nothing stored for user 1
    assert post({"Authorization": "Bearer good-token"}) == 200
    assert len(list((base / "archive" / "u5").glob("health_2*.txt"))) == 1


def test_receiver_transition_switch_is_explicit(receiver):
    post, base = receiver(HEALTH_SYNC_ALLOW_NO_TOKEN="1")
    assert post() == 200 and len(list((base / "archive").glob("health_2*.txt"))) == 1
    assert post({"X-Upload-Token": "wrong"}) == 401   # a wrong token is still refused
    post, _ = receiver(HEALTH_SYNC_ALLOW_NO_TOKEN="1", HEALTH_SYNC_REQUIRE_TOKEN="1")   # the older switch wins
    assert post() == 401


def test_issuer_list_is_complete():
    """Every route whose handler mints a credential is one this file tests. A new issuer (a function calling
    auth.create_token / login_codes.create / a code minting helper) must be added here and tested above."""
    tested = {"web.api.login", "web.api.login_with_code", "web.api.ingest_token", "web.api.activate",
              "web.api.open_me_link", "account.api.web_login_code", "account.api.watch_code", "account.api.redeem_invite",
              "account.api.bind_email", "account.api.bind_apple", "account.api.email_verify", "account.api.apple",
              "account.api.my_invite_code", "account.api.ow_invitation", "ai.api.create_ai_token",
              "ai.oauth_api.decide", "friends.api.invite", "friends.api.join"}
    src = Path(api.__file__).parent
    minting = re.compile(r"auth\.create_token\(|login_codes\.create\(|_session_response\(|session\.login\(|"
                         r"login_codes\.redeem|watch_tokens\.(mint_code|redeem)\(|tokens\.create,|"
                         r"activation\.(activate|mint_ow_invitation)\(|store\.mint_invite|invites\.referral_code\(|"
                         r"oauth\.decide\(|identities\.link_|_bind\(|store\.join|circle_codes\.redeem")
    found = set()
    for p in src.rglob("*api.py"):
        text = p.read_text()
        mod = ".".join(p.relative_to(src).with_suffix("").parts)
        for m in re.finditer(r"\n@[^\n]*\n(?:@[^\n]*\n)*(?:async )?def (\w+)\(.*?(?=\n@|\n\S|\Z)", text, re.S):
            if minting.search(m.group(0)):
                found.add(f"{mod}.{m.group(1)}")
    assert found - tested == set(), f"new credential-issuing endpoints: add negative tests for {sorted(found - tested)}"
