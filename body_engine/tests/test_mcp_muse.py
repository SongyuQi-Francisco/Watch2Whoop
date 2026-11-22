"""Meta Muse as a subscription MCP client (docs/ai-assistant-plan.md 9.24), driven by the official MCP Python SDK's own
client (`mcp.client.Client` over Streamable HTTP, `mcp.client.auth.OAuthClientProvider` for OAuth) against Body Engine.

Meta documents Muse's custom connectors only as "ask Muse to create a Custom Connector", the credential kept in Muse's
Secure Credentials Store; it publishes no server-URL field, transport, protocol version or OAuth callback for them. So
both ways a connector can reach /mcp are walked end to end:
- token: an AI token (Bearer header, client "muse") — tools/list, a read tool, a write tool, revoke → 401 at once;
- OAuth: protected resource metadata → authorization server metadata → dynamic client registration → PKCE S256 → the
  Today page consent → code exchange → tools → the user disconnects → access and refresh token dead at once. Client name
  and redirect URI are the ones in Meta's own client ID metadata document for Muse (the developer portal's).
Plus what Body Engine does not do yet (client ID metadata documents), every initialize protocol version, the catalog kind.

No network: the SDK's HTTP client calls the ASGI app in the event loop that runs the app's lifespan (the TestClient's
portal). No Meta account, no production, no paid API. Separate test DB BODY_TEST_DB_NAME (default body_test_mcp_muse)."""
import os
from urllib.parse import parse_qs, urlparse

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, KNOWN_PROTOCOL_VERSIONS
from psycopg.rows import dict_row

from body_engine import api, auth, dl_app
from body_engine.ai import catalog, oauth, oauth_http
from body_engine.config import Settings
from body_engine.db import migrate

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_mcp_muse")
BASE = "https://body.example.com"
MCP_URL = f"{BASE}/mcp"
# Meta's client ID metadata document for Muse (https://platform.muse.ai/oauth/client-metadata.json, read 2026-11-22)
MUSE_CIMD = "https://platform.muse.ai/oauth/client-metadata.json"
MUSE_NAME = "Muse"
MUSE_CALLBACK = "https://platform.muse.ai/oauth/credential-test/callback"
JSON_H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


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
    out = {}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for key, name in (("t", "Muse token tester"), ("o", "Muse OAuth tester"), ("x", "Someone else")):
            out[key] = conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (name,)).fetchone()["id"]
            out[f"{key}_web"] = auth.create_token(conn, out[key], "web", ["read", "write"])
    return out


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
    oauth_http._authorize_ip.reset()


def H(token):
    return {"Authorization": f"Bearer {token}"}


def _asgi(**kw) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=api.app), timeout=30, **kw)


def _text(result) -> str:
    assert not result.is_error, result.content[0].text
    return result.content[0].text


async def _use(muse: Client, me: str, other_id: int, title: str) -> dict:
    """What Muse's agent does once connected: list the tools, read, write, try someone else's data."""
    tools = {t.name: t for t in (await muse.list_tools()).tools}
    assert {"get_briefing", "add_event"} <= tools.keys()
    assert tools["get_briefing"].annotations.read_only_hint is True
    assert tools["add_event"].annotations.read_only_hint is False
    # Muse asks before writes it is told about; clients that ask only before destructive calls must see delete / undo
    confirm = {n for n in tools if n in dl_app.APP.confirm}
    assert confirm, "the delete / undo tools are listed by name"
    assert {n for n, t in tools.items() if t.annotations.destructive_hint} == confirm
    briefing = _text(await muse.call_tool("get_briefing", {}))
    assert me in briefing and "Someone else" not in briefing
    _text(await muse.call_tool("add_event", {"occurred_at": "2026-11-20T09:00:00", "title": title, "category": "other"}))
    assert (await muse.call_tool("get_briefing", {"user": str(other_id)})).is_error   # one connection, one person's data
    return tools


def _audit(dsn, uid: int) -> tuple[list[str], list[str]]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        events = [r["title"] for r in conn.execute("SELECT title FROM events WHERE user_id = %s", (uid,)).fetchall()]
        channels = [r["channel"] for r in conn.execute("SELECT DISTINCT channel FROM assistant_audit WHERE user_id = %s",
                                                       (uid,)).fetchall()]
    return events, channels


def _dead(client, access_token: str) -> None:
    r = client.post("/mcp", headers={**JSON_H, **H(access_token)},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"], r.text


# ---------------------------------------------------------------- 1. AI token (Bearer), what Muse keeps in its credentials store
def test_muse_with_an_ai_token(client, world, dsn):
    made = client.post("/v1/users/me/ai-tokens", headers=H(world["t_web"]), json={"client": "muse"})
    assert made.status_code == 201, made.text
    token = made.json()["token"]
    assert made.json()["name"] == "mcp:muse"
    setup = client.get("/v1/users/me/ai-tokens", headers=H(world["t_web"])).json()["mcp"]
    assert setup["url"] == MCP_URL and MCP_URL in setup["clients"]["muse"]      # the message to paste into Muse

    async def run():
        async with Client(streamable_http_client(MCP_URL, http_client=_asgi(headers=H(token)))) as muse:
            await _use(muse, "Muse token tester", world["x"], "muse token test")
            return muse.protocol_version
    assert client.portal.call(run) in KNOWN_PROTOCOL_VERSIONS                   # SDK 2.x: 2026-07-28 (per-request envelope)
    assert _audit(dsn, world["t"]) == (["muse token test"], ["mcp:muse"])

    assert client.delete(f"/v1/users/me/ai-tokens/{made.json()['id']}", headers=H(world["t_web"])).status_code == 204
    _dead(client, token)


# ---------------------------------------------------------------- 2. OAuth, the official SDK client end to end
class Memory:
    """The SDK's TokenStorage, in memory (what the connector keeps on Muse's side)."""

    def __init__(self):
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info):
        self.client_info = client_info


class Person:
    """The user's side: the browser Muse opens at /oauth/authorize, the consent page, the approve button."""

    def __init__(self, web_token: str):
        self.web = web_token
        self.views: list[dict] = []
        self.result: AuthorizationCodeResult | None = None

    async def open(self, authorization_url: str) -> None:
        q = parse_qs(urlparse(authorization_url).query)
        assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0] and q["resource"] == [MCP_URL]
        async with _asgi() as browser:
            r = await browser.get(authorization_url)
            assert r.status_code == 302, r.text
            loc = r.headers["location"]
            assert loc.startswith(f"{BASE}/app/oauth.html?req="), loc          # our consent page, nothing else
            req = parse_qs(urlparse(loc).query)["req"][0]
            view = await browser.get(f"{BASE}/v1/oauth/requests/{req}", headers=H(self.web))
            assert view.status_code == 200, view.text
            self.views.append(view.json())
            d = await browser.post(f"{BASE}/v1/oauth/requests/{req}", headers=H(self.web), json={"approve": True})
            assert d.status_code == 200, d.text
            back = d.json()["redirect"]
            assert back.startswith(MUSE_CALLBACK + "?"), back                   # back to Muse's registered callback
            bq = parse_qs(urlparse(back).query)
            self.result = AuthorizationCodeResult(code=bq["code"][0], state=bq["state"][0])

    async def callback(self) -> AuthorizationCodeResult:
        assert self.result is not None
        return self.result


def _oauth(person: Person, storage: Memory, **kw) -> OAuthClientProvider:
    meta = OAuthClientMetadata(client_name=MUSE_NAME, client_uri="https://muse.ai/", redirect_uris=[MUSE_CALLBACK],
                               grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                               token_endpoint_auth_method="none")
    return OAuthClientProvider(server_url=MCP_URL, client_metadata=meta, storage=storage,
                               redirect_handler=person.open, callback_handler=person.callback, **kw)


def test_muse_with_oauth(client, world, dsn):
    person, storage = Person(world["o_web"]), Memory()

    async def run():
        async with Client(streamable_http_client(MCP_URL, http_client=_asgi(auth=_oauth(person, storage)))) as muse:
            await _use(muse, "Muse OAuth tester", world["x"], "muse oauth test")
            return muse.protocol_version
    assert client.portal.call(run) in KNOWN_PROTOCOL_VERSIONS

    # registration: a public client (no secret), the consent page names Muse and warns (callback host not in the list)
    assert storage.client_info.token_endpoint_auth_method == "none" and not storage.client_info.client_secret
    assert storage.tokens.access_token and storage.tokens.refresh_token and storage.tokens.expires_in == oauth.ACCESS_TTL_S
    [view] = person.views
    assert view["client_name"] == MUSE_NAME and view["redirect_host"] == "platform.muse.ai" and view["known_vendor"] is False
    assert _audit(dsn, world["o"]) == (["muse oauth test"], ["mcp:muse"])     # the audit names Muse
    conns = client.get("/v1/users/me/ai-connections", headers=H(world["o_web"])).json()["connections"]
    [conn] = [c for c in conns if c["client_name"] == MUSE_NAME]
    assert conn["redirect_host"] == "platform.muse.ai"

    # the user disconnects in Me → AI settings: the access token and the refresh token stop working at once
    assert client.delete(f"/v1/users/me/ai-connections/{conn['id']}", headers=H(world["o_web"])).status_code == 204
    _dead(client, storage.tokens.access_token)
    again = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": storage.tokens.refresh_token,
                                              "client_id": storage.client_info.client_id})
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def test_client_id_metadata_documents_not_supported_yet(client, world):
    """Meta's portal identifies Muse by a client ID metadata document (client_id = an https URL). Body Engine does not
    advertise that (MCP 2025-11-25 optional), so the SDK falls back to dynamic registration; a client that can only use
    the URL as client_id is refused on our own error page, never redirected (gap, docs/ai-assistant-plan.md 9.24)."""
    meta = client.get("/.well-known/oauth-authorization-server/oauth").json()
    assert not meta.get("client_id_metadata_document_supported") and meta["registration_endpoint"] == f"{BASE}/oauth/register"
    r = client.get("/oauth/authorize", follow_redirects=False,
                   params={"response_type": "code", "client_id": MUSE_CIMD, "redirect_uri": MUSE_CALLBACK, "state": "s",
                           "code_challenge": "x" * 43, "code_challenge_method": "S256", "resource": MCP_URL})
    assert r.status_code == 400 and "location" not in r.headers

    person, storage = Person(world["o_web"]), Memory()

    async def run():
        provider = _oauth(person, storage, client_metadata_url=MUSE_CIMD)
        async with Client(streamable_http_client(MCP_URL, http_client=_asgi(auth=provider))) as muse:
            return len((await muse.list_tools()).tools)
    assert client.portal.call(run) > 0
    assert storage.client_info.client_id != MUSE_CIMD                            # registered, not the URL


@pytest.mark.parametrize("version", HANDSHAKE_PROTOCOL_VERSIONS)
def test_every_initialize_protocol_version(client, world, version):
    """Meta does not say which MCP version Muse speaks: every initialize version is answered with itself."""
    made = client.post("/v1/users/me/ai-tokens", headers=H(world["t_web"]), json={"client": "muse", "read_only": True})
    token = made.json()["token"]
    r = client.post("/mcp", headers={**JSON_H, **H(token), "MCP-Protocol-Version": version},
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "muse", "version": "0"}}})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["protocolVersion"] == version
    client.delete(f"/v1/users/me/ai-tokens/{made.json()['id']}", headers=H(world["t_web"]))


# ---------------------------------------------------------------- audit channel, catalog
def test_channel_client_recognises_muse():
    cc = oauth.channel_client
    assert cc("Muse", "platform.muse.ai") == cc("Meta Muse", "example.org") == cc("x", "muse.ai") == "muse"
    assert cc("Claude", "claude.ai") == "claude" and cc("Museum Guide", "example.org") == "museum-guide"
    assert cc("x", "evil.app://platform.muse.ai") == "x"                          # only an https domain counts
    assert cc("x", "notmuse.ai") == "x"


def test_muse_is_a_subscription_kind(client, world):
    """The AI settings kind; picked for Jarvis, the in-app chat sends the user to Muse (a user who never revoked MCP access:
    once every AI token is revoked a subscription kind stops being usable, choice.usable)."""
    assert catalog.is_mcp("muse_plan_mcp") and catalog.KINDS["muse_plan_mcp"]["client"] == "other"
    assert catalog.KINDS_EN["muse_plan_mcp"]["label"] == "Meta Muse"
    made = client.post("/v1/users/me/ai-providers", headers=H(world["x_web"]), json={"kind": "muse_plan_mcp", "is_default": True})
    assert made.status_code == 201, made.text
    assert made.json()["kind"] == "muse_plan_mcp" and made.json()["label"] == "Meta Muse"
    listed = client.get("/v1/users/me/ai-providers", headers={**H(world["x_web"]), "X-Body-Lang": "en"}).json()
    entry = next(k for k in listed["kinds"] if k["kind"] == "muse_plan_mcp")
    assert entry["category"] == "mcp" and "custom connector" in entry["note"]
    r = client.post("/v1/assistant/chat", headers=H(world["x_web"]), json={"message": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "subscription" and "Muse" in r.json()["detail"]["message"]
    assert client.delete(f"/v1/users/me/ai-providers/{made.json()['id']}", headers=H(world["x_web"])).status_code == 204
