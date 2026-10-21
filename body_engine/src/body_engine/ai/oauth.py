"""OAuth 2.1 authorization server for the remote MCP endpoint /mcp (MCP authorization spec; docs/ai-assistant-plan.md 9.16).

Claude (web / desktop / mobile), ChatGPT and the Gemini app only connect remote MCP servers through OAuth (static Bearer
headers are not offered to normal users), so /mcp also accepts OAuth access tokens. The protocol endpoints come from the
official MCP Python SDK (`mcp.server.auth`: /oauth/authorize, /oauth/token, /oauth/register, /oauth/revoke with PKCE checks);
this module is the storage and policy behind them (`BodyOAuthProvider`) plus the consent step (ai/oauth_api.py,
web/static/oauth.html).

- Clients register themselves (RFC 7591). All are public clients: token_endpoint_auth_method is forced to `none`, no client
  secret exists, PKCE S256 protects the code. Redirect URIs must be https, a loopback http address, or a private-use scheme;
  loopback ones match with any port (RFC 8252 §7.3). Registration is rate limited (oauth_http.py) and clients that never got
  a grant are deleted after CLIENT_UNUSED_DAYS (`cleanup`).
- /oauth/authorize stores the request and sends the browser to the consent page; the logged-in user approves or denies there.
  Approving creates a grant (one connection, listed under 「我 → AI 设置」) and a one-time code (5 minutes, sha256 only).
- Tokens: the access token is an api_tokens row with scope mcp (the same kind as static AI tokens, so /mcp, audit and
  read-only handling stay one code path), expiring after ACCESS_TTL_S and tied to the grant. The refresh token rotates on
  every use; presenting a used refresh token (or a used code) again revokes the whole grant.
- RFC 8707: a `resource` other than this server's /mcp is refused at /authorize.
- Only sha256 of codes, request ids, access and refresh tokens is stored.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import anyio
import psycopg
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
                                      RefreshToken, RegistrationError, TokenError, construct_redirect_uri)
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull, OAuthToken
from psycopg.types.json import Jsonb

from .. import auth
from ..i18n import L

SCOPES = ("read", "write")
ACCESS_TTL_S = 3600
REFRESH_TTL = timedelta(days=90)
CODE_TTL = timedelta(minutes=5)
REQUEST_TTL = timedelta(minutes=10)
CLIENT_UNUSED_DAYS = 1           # registered but never approved -> deleted by cleanup()
EXPIRED_TOKEN_KEEP_DAYS = 7      # expired OAuth access token rows are deleted after this
MAX_GRANTS = 20                  # live connections per user
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")
BAD_SCHEMES = ("javascript", "data", "file", "vbscript", "about", "blob")

# Set by oauth_http for the register call: the caller's IP (sha256), for the rate limit
REGISTER_IP: ContextVar[str | None] = ContextVar("oauth_register_ip", default=None)


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def base_url(scope_or_request=None) -> str:
    """This server's public origin: BODY_PUBLIC_URL, else the request's own scheme + host."""
    env = (os.environ.get("BODY_PUBLIC_URL") or "").rstrip("/")
    if env or scope_or_request is None:
        return env or "http://localhost:8100"
    scope = getattr(scope_or_request, "scope", scope_or_request)
    headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers") or []}
    host = headers.get("host") or "localhost"
    return f"{scope.get('scheme', 'http')}://{host}"


def resource_url(base: str) -> str:
    return base.rstrip("/") + "/mcp"


def issuer_url(base: str) -> str:
    return base.rstrip("/") + "/oauth"


def is_loopback(uri: str) -> bool:
    p = urlparse(uri)
    return p.scheme == "http" and (p.hostname or "") in LOOPBACK_HOSTS


def check_redirect_uri(uri: str) -> None:
    p = urlparse(uri)
    scheme = (p.scheme or "").lower()
    if not scheme or scheme in BAD_SCHEMES or p.fragment:
        raise ValueError(uri)
    if scheme == "http" and not is_loopback(uri):
        raise ValueError(uri)
    if scheme in ("http", "https") and not p.hostname:
        raise ValueError(uri)


def redirect_host(uri: str) -> str:
    p = urlparse(uri)
    return p.hostname or f"{p.scheme}:"


def _slug(name: str | None) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:24]
    return s or "oauth"


class BodyClient(OAuthClientInformationFull):
    """Loopback redirect URIs match with any port (RFC 8252 §7.3; Claude Code uses an ephemeral port)."""

    def validate_redirect_uri(self, redirect_uri):
        if redirect_uri is not None and is_loopback(str(redirect_uri)):
            want = urlparse(str(redirect_uri))
            for reg in self.redirect_uris or []:
                r = urlparse(str(reg))
                if is_loopback(str(reg)) and r.hostname == want.hostname and r.path == want.path:
                    return redirect_uri
            raise InvalidRedirectUriError(f"Redirect URI '{redirect_uri}' not registered for client")
        return super().validate_redirect_uri(redirect_uri)


# ---------------------------------------------------------------- clients
def save_client(conn: psycopg.Connection, info: OAuthClientInformationFull, ip_sha: str | None) -> None:
    uris = [str(u) for u in info.redirect_uris or []]
    if not uris:
        raise RegistrationError("invalid_redirect_uri", "redirect_uris is required")
    for u in uris:
        try:
            check_redirect_uri(u)
        except ValueError:
            raise RegistrationError("invalid_redirect_uri", f"redirect URI not allowed: {u}")
    if info.client_secret:   # oauth_http forces public clients; never store a secret
        raise RegistrationError("invalid_client_metadata", "only public clients (token_endpoint_auth_method none)")
    meta = info.model_dump(mode="json", exclude_none=True)
    conn.execute("""INSERT INTO oauth_clients (client_id, client_name, redirect_uris, scope, metadata, registered_ip_sha256)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                 (info.client_id, (info.client_name or "")[:80] or None, uris, info.scope, Jsonb(meta), ip_sha))


def load_client(conn: psycopg.Connection, client_id: str) -> BodyClient | None:
    row = conn.execute("SELECT metadata FROM oauth_clients WHERE client_id = %s", (client_id,)).fetchone()
    return BodyClient.model_validate(row["metadata"]) if row else None


def registrations(conn: psycopg.Connection, ip_sha: str | None) -> tuple[int, int]:
    """(this IP in the last hour, everyone in the last day)."""
    r = conn.execute("""SELECT count(*) FILTER (WHERE registered_ip_sha256 = %s AND created_at > now() - interval '1 hour') AS ip,
                               count(*) AS day FROM oauth_clients WHERE created_at > now() - interval '1 day'""",
                     (ip_sha,)).fetchone()
    return r["ip"], r["day"]


# ---------------------------------------------------------------- authorization requests (consent page)
def create_request(conn: psycopg.Connection, client: OAuthClientInformationFull, params: AuthorizationParams,
                   base: str) -> str:
    if params.resource and params.resource.rstrip("/") != resource_url(base).rstrip("/"):
        raise AuthorizeError("invalid_request", f"resource must be {resource_url(base)}")
    scopes = [s for s in (params.scopes or list(SCOPES)) if s in SCOPES] or ["read"]
    rid = secrets.token_urlsafe(32)
    conn.execute("""INSERT INTO oauth_requests (request_sha256, client_id, redirect_uri, redirect_uri_provided_explicitly,
                        code_challenge, state, scopes, resource, expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (_h(rid), client.client_id, str(params.redirect_uri), params.redirect_uri_provided_explicitly,
                  params.code_challenge, params.state, scopes, params.resource or resource_url(base), _now() + REQUEST_TTL))
    return rid


def get_request(conn: psycopg.Connection, rid: str) -> dict | None:
    return conn.execute("""SELECT r.*, c.client_name, c.metadata->>'client_uri' AS client_uri FROM oauth_requests r
                           JOIN oauth_clients c USING (client_id)
                           WHERE r.request_sha256 = %s AND r.expires_at > now()""", (_h(rid),)).fetchone()


def request_view(conn: psycopg.Connection, rid: str) -> dict | None:
    r = get_request(conn, rid)
    if r is None:
        return None
    return {"client_name": r["client_name"] or L("未命名的应用", "Unnamed app"), "redirect_host": redirect_host(r["redirect_uri"]),
            "redirect_uri": r["redirect_uri"], "loopback": is_loopback(r["redirect_uri"]), "scopes": r["scopes"],
            "client_uri": r["client_uri"], "expires_at": r["expires_at"]}


def decide(conn: psycopg.Connection, rid: str, user_id: int, approve: bool, read_only: bool = False) -> str:
    """The user's decision; returns the URL to send the browser to (the client's redirect URI with code or error)."""
    r = conn.execute("DELETE FROM oauth_requests WHERE request_sha256 = %s AND expires_at > now() RETURNING *",
                     (_h(rid),)).fetchone()
    if r is None:
        raise LookupError(L("授权请求已过期，请回到 AI 应用重新连接", "This authorization request expired; connect again from the AI app"))
    if not approve:
        return construct_redirect_uri(r["redirect_uri"], error="access_denied", state=r["state"])
    n = conn.execute("SELECT count(*) AS n FROM oauth_grants WHERE user_id = %s AND revoked_at IS NULL",
                     (user_id,)).fetchone()["n"]
    if n >= MAX_GRANTS:
        raise ValueError(L(f"最多 {MAX_GRANTS} 个 AI 连接，先在「我 → AI 设置」断开不用的",
                           f"At most {MAX_GRANTS} AI connections; disconnect unused ones in Me → AI settings first"))
    scopes = [s for s in r["scopes"] if not (read_only and s == "write")] or ["read"]
    client = conn.execute("SELECT client_name FROM oauth_clients WHERE client_id = %s", (r["client_id"],)).fetchone()
    grant = conn.execute("""INSERT INTO oauth_grants (user_id, client_id, client_name, redirect_host, scopes, resource)
                            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                         (user_id, r["client_id"], client["client_name"], redirect_host(r["redirect_uri"]), scopes,
                          r["resource"])).fetchone()
    code = secrets.token_urlsafe(32)
    conn.execute("""INSERT INTO oauth_codes (code_sha256, grant_id, client_id, redirect_uri, redirect_uri_provided_explicitly,
                        code_challenge, scopes, resource, expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (_h(code), grant["id"], r["client_id"], r["redirect_uri"], r["redirect_uri_provided_explicitly"],
                  r["code_challenge"], scopes, r["resource"], _now() + CODE_TTL))
    conn.execute("UPDATE oauth_clients SET last_used_at = now() WHERE client_id = %s", (r["client_id"],))
    return construct_redirect_uri(r["redirect_uri"], code=code, state=r["state"])


# ---------------------------------------------------------------- codes and tokens
def revoke_grant(conn: psycopg.Connection, grant_id: int, reason: str, user_id: int | None = None) -> bool:
    """End a connection: the grant, its live access tokens, refresh tokens and unused codes."""
    row = conn.execute(f"""UPDATE oauth_grants SET revoked_at = now(), revoke_reason = %s
                           WHERE id = %s AND revoked_at IS NULL {'AND user_id = %s' if user_id is not None else ''}
                           RETURNING id""", (reason, grant_id, *([user_id] if user_id is not None else []))).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE api_tokens SET revoked_at = now() WHERE oauth_grant_id = %s AND revoked_at IS NULL", (grant_id,))
    conn.execute("UPDATE oauth_refresh_tokens SET used_at = COALESCE(used_at, now()), expires_at = now() WHERE grant_id = %s",
                 (grant_id,))
    conn.execute("UPDATE oauth_codes SET used_at = COALESCE(used_at, now()) WHERE grant_id = %s", (grant_id,))
    return True


def load_code(conn: psycopg.Connection, client_id: str, code: str) -> AuthorizationCode | None:
    r = conn.execute("""SELECT c.*, g.revoked_at FROM oauth_codes c JOIN oauth_grants g ON g.id = c.grant_id
                        WHERE c.code_sha256 = %s AND c.client_id = %s""", (_h(code), client_id)).fetchone()
    if r is None or r["revoked_at"]:
        return None
    if r["used_at"]:   # a code presented twice: someone else may hold it -> end the connection (RFC 6749 §4.1.2)
        revoke_grant(conn, r["grant_id"], "code_reuse")
        return None
    return AuthorizationCode(code=code, scopes=r["scopes"], expires_at=r["expires_at"].timestamp(), client_id=client_id,
                             code_challenge=r["code_challenge"], redirect_uri=r["redirect_uri"],
                             redirect_uri_provided_explicitly=r["redirect_uri_provided_explicitly"], resource=r["resource"])


def _issue(conn: psycopg.Connection, grant: dict, scopes: list[str]) -> OAuthToken:
    """A new access token (api_tokens, scope mcp, expiring) + a new refresh token; older live access tokens of the grant end."""
    conn.execute("UPDATE api_tokens SET revoked_at = now() WHERE oauth_grant_id = %s AND revoked_at IS NULL", (grant["id"],))
    access = auth.TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_scopes = [auth.MCP_SCOPE, "read"] + (["write"] if "write" in scopes else [])
    conn.execute("""INSERT INTO api_tokens (user_id, name, token_sha256, scopes, expires_at, oauth_grant_id)
                    VALUES (%s, %s, %s, %s, now() + make_interval(secs => %s), %s)""",
                 (grant["user_id"], f"mcp:{_slug(grant['client_name'])}", auth.hash_token(access), token_scopes,
                  ACCESS_TTL_S, grant["id"]))
    refresh = secrets.token_urlsafe(32)
    conn.execute("""INSERT INTO oauth_refresh_tokens (token_sha256, grant_id, client_id, scopes, expires_at)
                    VALUES (%s, %s, %s, %s, %s)""", (_h(refresh), grant["id"], grant["client_id"], scopes, _now() + REFRESH_TTL))
    return OAuthToken(access_token=access, expires_in=ACCESS_TTL_S, scope=" ".join(scopes), refresh_token=refresh)


def exchange_code(conn: psycopg.Connection, client_id: str, code: str) -> OAuthToken:
    r = conn.execute("""UPDATE oauth_codes SET used_at = now() WHERE code_sha256 = %s AND client_id = %s AND used_at IS NULL
                        AND expires_at > now() RETURNING grant_id, scopes""", (_h(code), client_id)).fetchone()
    if r is None:
        raise TokenError("invalid_grant", "authorization code is invalid or already used")
    grant = conn.execute("SELECT * FROM oauth_grants WHERE id = %s AND revoked_at IS NULL", (r["grant_id"],)).fetchone()
    if grant is None:
        raise TokenError("invalid_grant", "connection was revoked")
    return _issue(conn, grant, r["scopes"])


def load_refresh(conn: psycopg.Connection, client_id: str, token: str) -> RefreshToken | None:
    r = conn.execute("""SELECT t.*, g.revoked_at FROM oauth_refresh_tokens t JOIN oauth_grants g ON g.id = t.grant_id
                        WHERE t.token_sha256 = %s AND t.client_id = %s""", (_h(token), client_id)).fetchone()
    if r is None or r["revoked_at"]:
        return None
    if r["used_at"]:   # replay of a rotated refresh token: treat the chain as stolen
        revoke_grant(conn, r["grant_id"], "refresh_reuse")
        return None
    if r["expires_at"] <= _now():
        return None
    return RefreshToken(token=token, client_id=client_id, scopes=r["scopes"], expires_at=int(r["expires_at"].timestamp()))


def exchange_refresh(conn: psycopg.Connection, client_id: str, token: str, scopes: list[str]) -> OAuthToken:
    r = conn.execute("""UPDATE oauth_refresh_tokens SET used_at = now() WHERE token_sha256 = %s AND client_id = %s
                        AND used_at IS NULL AND expires_at > now() RETURNING grant_id, scopes""",
                     (_h(token), client_id)).fetchone()
    if r is None:
        raise TokenError("invalid_grant", "refresh token is invalid or already used")
    grant = conn.execute("SELECT * FROM oauth_grants WHERE id = %s AND revoked_at IS NULL", (r["grant_id"],)).fetchone()
    if grant is None:
        raise TokenError("invalid_grant", "connection was revoked")
    return _issue(conn, grant, [s for s in (scopes or r["scopes"]) if s in r["scopes"]] or r["scopes"])


def load_access(conn: psycopg.Connection, token: str) -> AccessToken | None:
    r = conn.execute("""SELECT t.scopes, t.expires_at, g.client_id, g.resource, g.user_id FROM api_tokens t
                        JOIN oauth_grants g ON g.id = t.oauth_grant_id
                        WHERE t.token_sha256 = %s AND t.revoked_at IS NULL AND g.revoked_at IS NULL
                          AND t.expires_at > now()""", (auth.hash_token(token),)).fetchone()
    if r is None:
        return None
    return AccessToken(token=token, client_id=r["client_id"], scopes=[s for s in r["scopes"] if s in SCOPES],
                       expires_at=int(r["expires_at"].timestamp()), resource=r["resource"], subject=str(r["user_id"]))


def revoke_token(conn: psycopg.Connection, token: str) -> None:
    """RFC 7009 revocation of an access or refresh token: ends the whole connection."""
    r = conn.execute("SELECT oauth_grant_id AS g FROM api_tokens WHERE token_sha256 = %s AND oauth_grant_id IS NOT NULL",
                     (auth.hash_token(token),)).fetchone() \
        or conn.execute("SELECT grant_id AS g FROM oauth_refresh_tokens WHERE token_sha256 = %s", (_h(token),)).fetchone()
    if r:
        revoke_grant(conn, r["g"], "revoke_endpoint")


# ---------------------------------------------------------------- connections (Me → AI settings) and cleanup
def list_grants(conn: psycopg.Connection, user_id: int) -> list[dict]:
    return conn.execute("""SELECT id, client_name, redirect_host, scopes, created_at, last_used_at FROM oauth_grants
                           WHERE user_id = %s AND revoked_at IS NULL ORDER BY COALESCE(last_used_at, created_at) DESC""",
                        (user_id,)).fetchall()


def cleanup(conn: psycopg.Connection) -> dict:
    out = {}
    out["requests"] = len(conn.execute("DELETE FROM oauth_requests WHERE expires_at < now() RETURNING 1").fetchall())
    out["codes"] = len(conn.execute("DELETE FROM oauth_codes WHERE expires_at < now() - interval '1 day' RETURNING 1").fetchall())
    out["refresh_tokens"] = len(conn.execute(
        "DELETE FROM oauth_refresh_tokens WHERE expires_at < now() - interval '1 day' RETURNING 1").fetchall())
    out["access_tokens"] = len(conn.execute(
        """DELETE FROM api_tokens WHERE oauth_grant_id IS NOT NULL
           AND COALESCE(revoked_at, expires_at) < now() - make_interval(days => %s) RETURNING 1""",
        (EXPIRED_TOKEN_KEEP_DAYS,)).fetchall())
    out["clients"] = len(conn.execute(
        """DELETE FROM oauth_clients c WHERE created_at < now() - make_interval(days => %s)
           AND NOT EXISTS (SELECT 1 FROM oauth_grants g WHERE g.client_id = c.client_id) RETURNING 1""",
        (CLIENT_UNUSED_DAYS,)).fetchall())
    return out


# ---------------------------------------------------------------- the SDK provider interface
def _pool():
    from .. import api
    return api.pool


async def _db(fn, *args):
    """Run fn(conn, *args) in a worker thread. The SDK's OAuth errors are frozen dataclasses, which contextlib cannot re-raise
    through the connection's `with` (it assigns __traceback__), so they come back as values and are raised here."""
    def run():
        with _pool().connection() as conn:
            try:
                return fn(conn, *args), None
            except (RegistrationError, AuthorizeError, TokenError) as e:
                return None, e
    result, error = await anyio.to_thread.run_sync(run)
    if error is not None:
        raise error
    return result


class BodyOAuthProvider:
    """mcp.server.auth OAuthAuthorizationServerProvider backed by Postgres (functions above)."""

    def __init__(self, base: str):
        self.base = base

    async def get_client(self, client_id: str):
        return await _db(load_client, client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await _db(save_client, client_info, REGISTER_IP.get())

    async def authorize(self, client, params: AuthorizationParams) -> str:
        rid = await _db(create_request, client, params, self.base)
        return f"{self.base}/app/oauth.html?req={rid}"

    async def load_authorization_code(self, client, authorization_code: str):
        return await _db(load_code, client.client_id, authorization_code)

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode) -> OAuthToken:
        return await _db(exchange_code, client.client_id, authorization_code.code)

    async def load_refresh_token(self, client, refresh_token: str):
        return await _db(load_refresh, client.client_id, refresh_token)

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        return await _db(exchange_refresh, client.client_id, refresh_token.token, scopes)

    async def load_access_token(self, token: str):
        return await _db(load_access, token)

    async def revoke_token(self, token) -> None:
        await _db(revoke_token, token.token)

    async def exchange_identity_assertion(self, *args, **kwargs):   # SEP-990 enterprise grant: not offered
        raise TokenError("unsupported_grant_type", "not supported")

