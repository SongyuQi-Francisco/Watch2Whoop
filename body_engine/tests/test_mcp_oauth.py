"""OAuth 2.1 for the remote MCP (ai/oauth.py, oauth_http.py, oauth_api.py; docs/ai-assistant-plan.md 9.16): discovery, dynamic
client registration (public clients only, rate limited), PKCE S256, consent on the Today page, refresh rotation with replay
detection, code reuse, RFC 8707 resource, revocation, connections under AI settings, per-user isolation, static AI tokens still
working, cleanup. Separate test DB BODY_TEST_DB_NAME (default body_test_mcp_oauth)."""
import base64
import hashlib
import os
import secrets
from urllib.parse import parse_qs, urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth
from body_engine.ai import oauth, oauth_http
from body_engine.config import Settings
from body_engine.db import migrate

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_mcp_oauth")
BASE = "https://body.example.com"
CB = "https://claude.ai/api/mcp/auth_callback"
MCP_H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"}


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture(scope="module")
def world(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"]
               for n in ("A", "B")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        tok["A_ro"] = auth.create_token(conn, ids["A"], "web-ro", ["read"])
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn, world):
    mp = pytest.MonkeyPatch()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    mp.setenv("BODY_PUBLIC_URL", BASE)
    with TestClient(api.app, base_url="https://testserver") as c:
        yield c
    mp.undo()


@pytest.fixture(autouse=True)
def fresh_limits():
    oauth_http._register_ip.reset()


def H(token):
    return {"Authorization": f"Bearer {token}"}


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def rpc(client, token, method, params=None):
    return client.post("/mcp", headers={**MCP_H, **({"Authorization": f"Bearer {token}"} if token else {})},
                       json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def register(client, name="Claude", uris=(CB,), **extra):
    return client.post("/oauth/register", json={"client_name": name, "redirect_uris": list(uris),
                                                "grant_types": ["authorization_code", "refresh_token"],
                                                "response_types": ["code"], **extra})


def authorize(client, client_id, challenge, redirect=CB, **extra):
    q = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "code_challenge": challenge,
         "code_challenge_method": "S256", "state": "st-1", "resource": f"{BASE}/mcp", "scope": "read write", **extra}
    return client.get("/oauth/authorize", params=q, follow_redirects=False)


def consent(client, token, location, approve=True, read_only=False):
    req = parse_qs(urlparse(location).query)["req"][0]
    view = client.get(f"/v1/oauth/requests/{req}", headers=H(token))
    assert view.status_code == 200, view.text
    r = client.post(f"/v1/oauth/requests/{req}", headers=H(token), json={"approve": approve, "read_only": read_only})
    return view.json(), r


def connect(client, token, read_only=False, name="Claude"):
    """Full flow → (client_id, verifier, token response)."""
    cid = register(client, name).json()["client_id"]
    verifier, challenge = pkce()
    loc = authorize(client, cid, challenge).headers["location"]
    _, r = consent(client, token, loc, read_only=read_only)
    q = parse_qs(urlparse(r.json()["redirect"]).query)
    t = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": CB,
                                          "client_id": cid, "code_verifier": verifier, "resource": f"{BASE}/mcp"})
    assert t.status_code == 200, t.text
    return cid, verifier, t.json()


# ---------------------------------------------------------------- discovery
def test_discovery_and_401_challenge(client):
    r = rpc(client, None, "tools/list")
    assert r.status_code == 401
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in r.headers["www-authenticate"]
    bad = rpc(client, "be_not-a-token", "tools/list")
    assert bad.status_code == 401 and 'error="invalid_token"' in bad.headers["www-authenticate"]
    for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
        prm = client.get(path).json()
        assert prm["resource"] == f"{BASE}/mcp" and prm["authorization_servers"] == [f"{BASE}/oauth"]
    for path in ("/.well-known/oauth-authorization-server/oauth", "/.well-known/oauth-authorization-server",
                 "/oauth/.well-known/oauth-authorization-server"):
        m = client.get(path).json()
        assert m["issuer"] == f"{BASE}/oauth" and m["authorization_endpoint"] == f"{BASE}/oauth/authorize"
        assert m["token_endpoint"] == f"{BASE}/oauth/token" and m["registration_endpoint"] == f"{BASE}/oauth/register"
        assert m["code_challenge_methods_supported"] == ["S256"] and m["token_endpoint_auth_methods_supported"] == ["none"]
        assert set(m["grant_types_supported"]) >= {"authorization_code", "refresh_token"}


# ---------------------------------------------------------------- registration
def test_register_public_clients_and_redirect_rules(client, dsn):
    r = register(client, token_endpoint_auth_method="client_secret_post")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token_endpoint_auth_method"] == "none" and "client_secret" not in body
    assert register(client, uris=["http://evil.example/cb"]).status_code == 400          # plain http only for loopback
    assert register(client, uris=["javascript:alert(1)"]).status_code == 400
    assert register(client, uris=["http://127.0.0.1:3118/callback"]).status_code == 201
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("SELECT metadata FROM oauth_clients WHERE client_id = %s", (body["client_id"],)).fetchone()
        assert "client_secret" not in row["metadata"]


def test_register_rate_limit(client, monkeypatch):
    monkeypatch.setattr(oauth_http, "REGISTER_PER_IP_HOUR", 0)   # the database count: this IP already registered clients
    r = register(client)
    assert r.status_code == 429 and r.json()["error"] == "slow_down"


# ---------------------------------------------------------------- authorization code + PKCE
def test_full_flow_pkce_and_tools(client, world):
    ids, tok = world
    cid = register(client).json()["client_id"]
    verifier, challenge = pkce()
    r = authorize(client, cid, challenge)
    assert r.status_code == 302 and r.headers["location"].startswith(f"{BASE}/app/oauth.html?req=")
    loc = r.headers["location"]
    req = parse_qs(urlparse(loc).query)["req"][0]
    assert client.get(f"/v1/oauth/requests/{req}").status_code == 401           # not signed in: the page shows 「先登录」
    view, d = consent(client, tok["A"], loc)
    assert view["client_name"] == "Claude" and view["redirect_host"] == "claude.ai" and not view["loopback"]
    assert d.status_code == 200
    q = parse_qs(urlparse(d.json()["redirect"]).query)
    assert d.json()["redirect"].startswith(CB) and q["state"] == ["st-1"]
    form = {"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": CB, "client_id": cid}
    wrong = client.post("/oauth/token", data={**form, "code_verifier": secrets.token_urlsafe(48)})
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
    t = client.post("/oauth/token", data={**form, "code_verifier": verifier})
    assert t.status_code == 200, t.text
    tj = t.json()
    assert tj["token_type"] == "Bearer" and tj["expires_in"] == oauth.ACCESS_TTL_S and tj["refresh_token"]
    init = rpc(client, tj["access_token"], "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                          "clientInfo": {"name": "pytest", "version": "1"}})
    assert init.status_code == 200, init.text
    res = rpc(client, tj["access_token"], "tools/call", {"name": "get_profile", "arguments": {"user": "me"}}).json()["result"]
    assert '"display_name":"A"' in res["content"][0]["text"].replace(" ", "")
    assert client.get("/v1/me", headers=H(tj["access_token"])).status_code == 401   # OAuth tokens only work on /mcp


def test_pkce_plain_and_wrong_resource_refused(client):
    cid = register(client).json()["client_id"]
    _, challenge = pkce()
    plain = authorize(client, cid, challenge, code_challenge_method="plain")
    assert "error=invalid_request" in plain.headers.get("location", "") or plain.status_code == 400
    other = authorize(client, cid, challenge, resource="https://other.example/mcp")
    assert "error=invalid_request" in other.headers.get("location", "")
    unknown_cb = authorize(client, cid, challenge, redirect="https://evil.example/cb")
    assert unknown_cb.status_code == 400                                           # never redirect to an unregistered URI


def test_loopback_redirect_any_port(client, world):
    _, tok = world
    cid = register(client, "Claude Code", uris=["http://127.0.0.1:3118/callback"]).json()["client_id"]
    verifier, challenge = pkce()
    r = authorize(client, cid, challenge, redirect="http://127.0.0.1:54321/callback")
    assert r.status_code == 302 and "/app/oauth.html?req=" in r.headers["location"]
    view, d = consent(client, tok["A"], r.headers["location"], approve=False)
    assert view["loopback"] and "error=access_denied" in d.json()["redirect"] and "state=st-1" in d.json()["redirect"]
    assert authorize(client, cid, challenge, redirect="http://127.0.0.1:54321/other").status_code == 400


def test_code_is_single_use(client, world):
    _, tok = world
    cid = register(client).json()["client_id"]
    verifier, challenge = pkce()
    _, d = consent(client, tok["A"], authorize(client, cid, challenge).headers["location"])
    code = parse_qs(urlparse(d.json()["redirect"]).query)["code"][0]
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": CB, "client_id": cid, "code_verifier": verifier}
    first = client.post("/oauth/token", data=form).json()
    again = client.post("/oauth/token", data=form)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert rpc(client, first["access_token"], "tools/list").status_code == 401     # reuse ended the whole connection


# ---------------------------------------------------------------- refresh rotation
def test_refresh_rotation_and_replay(client, world):
    _, tok = world
    cid, _, t1 = connect(client, tok["A"])
    t2 = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t1["refresh_token"], "client_id": cid})
    assert t2.status_code == 200, t2.text
    t2 = t2.json()
    assert t2["refresh_token"] != t1["refresh_token"] and t2["access_token"] != t1["access_token"]
    assert rpc(client, t1["access_token"], "tools/list").status_code == 401        # the older access token ended
    assert rpc(client, t2["access_token"], "tools/list").status_code == 200
    replay = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t1["refresh_token"], "client_id": cid})
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert rpc(client, t2["access_token"], "tools/list").status_code == 401        # replay revoked the whole chain
    after = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t2["refresh_token"], "client_id": cid})
    assert after.status_code == 400
    other = register(client, "Other").json()["client_id"]                          # a refresh token only works for its client
    _, _, t3 = connect(client, tok["A"])
    assert client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t3["refresh_token"],
                                             "client_id": other}).status_code == 400


def test_expired_access_token(client, world, dsn):
    _, tok = world
    _, _, t = connect(client, tok["A"])
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE api_tokens SET expires_at = now() - interval '1 second' WHERE token_sha256 = %s",
                     (auth.hash_token(t["access_token"]),))
    assert rpc(client, t["access_token"], "tools/list").status_code == 401


# ---------------------------------------------------------------- connections, isolation, read-only, revocation
def test_connections_listed_and_revocable_per_user(client, world):
    ids, tok = world
    _, _, t = connect(client, tok["B"], name="ChatGPT")
    conns = client.get("/v1/users/me/ai-connections", headers=H(tok["B"])).json()["connections"]
    mine = [c for c in conns if c["client_name"] == "ChatGPT"]
    assert mine and mine[0]["redirect_host"] == "claude.ai" and mine[0]["last_used_at"] is None
    assert rpc(client, t["access_token"], "tools/list").status_code == 200
    listed = client.get("/v1/users/me/ai-tokens", headers=H(tok["B"])).json()
    assert any(c["id"] == mine[0]["id"] and c["last_used_at"] for c in listed["connections"])   # last use shown
    assert all(not x["name"].startswith("mcp:chatgpt") for x in listed["tokens"])               # not in the static list
    assert "oauth" in listed["mcp"]
    gid = mine[0]["id"]
    assert all(c["id"] != gid for c in client.get("/v1/users/me/ai-connections", headers=H(tok["A"])).json()["connections"])
    assert client.get(f"/v1/users/{ids['B']}/ai-connections", headers=H(tok["A"])).status_code == 403
    assert client.delete(f"/v1/users/me/ai-connections/{gid}", headers=H(tok["A"])).status_code == 404   # not A's
    assert client.delete(f"/v1/users/me/ai-connections/{gid}", headers=H(tok["A_ro"])).status_code == 403
    res = rpc(client, t["access_token"], "tools/call", {"name": "get_profile", "arguments": {"user": str(ids["A"])}}).json()
    assert res["result"]["isError"]                                                # B's connection cannot read A
    assert client.delete(f"/v1/users/me/ai-connections/{gid}", headers=H(tok["B"])).status_code == 204
    assert rpc(client, t["access_token"], "tools/list").status_code == 401


def test_read_only_approval_and_read_only_login(client, world):
    _, tok = world
    _, _, t = connect(client, tok["A"], read_only=True)
    assert t["scope"] == "read"
    tools = rpc(client, t["access_token"], "tools/list").json()["result"]["tools"]
    assert tools and not any(x["name"] == "add_event" for x in tools)              # read-only: no write tools
    cid = register(client).json()["client_id"]
    _, challenge = pkce()
    loc = authorize(client, cid, challenge).headers["location"]
    req = parse_qs(urlparse(loc).query)["req"][0]
    r = client.post(f"/v1/oauth/requests/{req}", headers=H(tok["A_ro"]), json={"approve": True})
    assert r.status_code == 403                                                    # a read-only sign-in cannot approve


def test_revoke_endpoint(client, world):
    _, tok = world
    cid, _, t = connect(client, tok["A"])
    r = client.post("/oauth/revoke", data={"token": t["refresh_token"], "client_id": cid})
    assert r.status_code == 200
    assert rpc(client, t["access_token"], "tools/list").status_code == 401


def test_static_ai_tokens_still_work(client, world):
    _, tok = world
    r = client.post("/v1/users/me/ai-tokens", headers=H(tok["A"]), json={"client": "claude-code"})
    assert r.status_code == 201
    assert rpc(client, r.json()["token"], "tools/list").status_code == 200


def test_expired_request_and_cleanup(client, world, dsn):
    _, tok = world
    cid = register(client).json()["client_id"]
    _, challenge = pkce()
    loc = authorize(client, cid, challenge).headers["location"]
    req = parse_qs(urlparse(loc).query)["req"][0]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE oauth_requests SET expires_at = now() - interval '1 minute'")
        conn.execute("UPDATE oauth_clients SET created_at = now() - interval '3 days' WHERE client_id = %s", (cid,))
    assert client.get(f"/v1/oauth/requests/{req}", headers=H(tok["A"])).status_code == 404
    assert client.post(f"/v1/oauth/requests/{req}", headers=H(tok["A"]), json={"approve": True}).status_code == 404
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        out = oauth.cleanup(conn)
        assert out["requests"] >= 1 and out["clients"] >= 1
        assert conn.execute("SELECT 1 FROM oauth_clients WHERE client_id = %s", (cid,)).fetchone() is None
        assert conn.execute("SELECT count(*) AS n FROM oauth_grants").fetchone()["n"] > 0   # approved ones stay


def test_consent_page_served(client):
    html = client.get("/app/oauth.html").text
    assert '<script src="oauth.js"></script>' in html and 'src="i18n.js"' in html
    assert "X-Body-Web" in client.get("/app/oauth.js").text
