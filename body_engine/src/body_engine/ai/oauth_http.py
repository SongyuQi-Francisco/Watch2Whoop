"""HTTP side of the MCP OAuth server (ai/oauth.py): discovery metadata, the SDK's protocol endpoints under /oauth, and the
401 challenge /mcp sends.

Paths (all on the public host, docs/ai-assistant-plan.md 9.16):
- GET /.well-known/oauth-protected-resource/mcp (+ /.well-known/oauth-protected-resource): RFC 9728, resource = <base>/mcp,
  authorization server = <base>/oauth
- GET /.well-known/oauth-authorization-server/oauth (+ /.well-known/oauth-authorization-server, /oauth/.well-known/…):
  RFC 8414; public clients only (`none`), PKCE S256, scopes read / write
- /oauth/authorize, /oauth/token, /oauth/register, /oauth/revoke: mcp.server.auth handlers with BodyOAuthProvider.
  /oauth/register is wrapped here: per-IP and server-wide rate limits, and token_endpoint_auth_method forced to `none`
  (RFC 7591 §3.2.1 lets the server replace requested metadata), so no client secret ever exists.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs

import anyio
from mcp.server.auth.routes import build_metadata
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.auth.routes import create_auth_routes
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from ..web.login_limit import Limiter
from . import oauth

REGISTER_PER_IP_HOUR = 20
REGISTER_PER_DAY = 500
_register_ip = Limiter(REGISTER_PER_IP_HOUR, 3600)   # in-process first line; the database counts are the real limit

REG_OPTIONS = ClientRegistrationOptions(enabled=True, valid_scopes=list(oauth.SCOPES), default_scopes=list(oauth.SCOPES))
REV_OPTIONS = RevocationOptions(enabled=True)


def resource_metadata_url(base: str) -> str:
    return base.rstrip("/") + "/.well-known/oauth-protected-resource/mcp"


def challenge(base: str, error: str | None = None) -> str:
    """WWW-Authenticate value for a 401 from /mcp: where the protected resource metadata is (RFC 9728 §5.1)."""
    parts = [f'resource_metadata="{resource_metadata_url(base)}"', f'scope="{" ".join(oauth.SCOPES)}"']
    if error:
        parts.insert(0, f'error="{error}"')
    return "Bearer " + ", ".join(parts)


def protected_resource_metadata(base: str) -> dict:
    return {"resource": oauth.resource_url(base), "authorization_servers": [oauth.issuer_url(base)],
            "scopes_supported": list(oauth.SCOPES), "bearer_methods_supported": ["header"],
            "resource_name": "Watch2Whoop Body Engine"}


def authorization_server_metadata(base: str) -> dict:
    m = build_metadata(AnyHttpUrl(oauth.issuer_url(base)), None, REG_OPTIONS, REV_OPTIONS).model_dump(mode="json", exclude_none=True)
    m["token_endpoint_auth_methods_supported"] = ["none"]
    m["revocation_endpoint_auth_methods_supported"] = ["none"]
    return m


def _ip_sha(scope) -> str:
    client = scope.get("client") or ("", 0)
    return hashlib.sha256(f"oauth-register|{client[0]}".encode()).hexdigest()


class OAuthApp:
    """ASGI app mounted at /oauth. The SDK routes are built per issuer (BODY_PUBLIC_URL, else the request's host)."""

    def __init__(self):
        self._apps: dict[str, Starlette] = {}

    def _app(self, base: str) -> Starlette:
        app = self._apps.get(base)
        if app is None:
            routes = [r for r in create_auth_routes(oauth.BodyOAuthProvider(base), AnyHttpUrl(oauth.issuer_url(base)),
                                                    client_registration_options=REG_OPTIONS, revocation_options=REV_OPTIONS)
                      if getattr(r, "path", "") != "/.well-known/oauth-authorization-server"]
            app = self._apps[base] = Starlette(routes=routes)
        return app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        base = oauth.base_url(scope)
        path = scope.get("path", "")
        if path.endswith("/.well-known/oauth-authorization-server"):
            await JSONResponse(authorization_server_metadata(base), headers={"Access-Control-Allow-Origin": "*"})(scope, receive, send)
            return
        if path.endswith("/register") and scope.get("method") == "POST":
            await self._register(base, scope, receive, send)
            return
        if path.endswith("/revoke") and scope.get("method") == "POST":
            # the SDK's revocation request model requires a client_secret field; public clients send none
            body = await _read(receive)
            form = parse_qs(body.decode("latin-1"), keep_blank_values=True)
            if "client_secret" not in form:
                body += (b"&" if body else b"") + b"client_secret="
            await self._app(base)(scope, _replay(body), send)
            return
        await self._app(base)(scope, receive, send)

    async def _register(self, base, scope, receive, send):
        ip = _ip_sha(scope)
        if _register_ip.retry_after(ip):
            await _too_many(scope, receive, send)
            return
        per_ip, per_day = await anyio.to_thread.run_sync(_counts, ip)
        if per_ip >= REGISTER_PER_IP_HOUR or per_day >= REGISTER_PER_DAY:
            await _too_many(scope, receive, send)
            return
        _register_ip.record(ip)
        body = await _read(receive)
        try:
            data = json.loads(body or b"{}")
            if isinstance(data, dict):
                data["token_endpoint_auth_method"] = "none"   # public clients only: PKCE, no client secret
                body = json.dumps(data).encode()
        except ValueError:
            pass   # the SDK answers invalid_client_metadata
        token = oauth.REGISTER_IP.set(ip)
        try:
            await self._app(base)(scope, _replay(body), send)
        finally:
            oauth.REGISTER_IP.reset(token)


async def _read(receive, limit: int = 64_000) -> bytes:
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body") or len(body) > limit:
            return body


def _replay(body: bytes):
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}
    return receive


def _counts(ip: str) -> tuple[int, int]:
    from .. import api
    with api.pool.connection() as conn:
        return oauth.registrations(conn, ip)


async def _too_many(scope, receive, send):
    await JSONResponse({"error": "slow_down", "error_description": "too many client registrations, try again later"},
                       status_code=429, headers={"Retry-After": "3600"})(scope, receive, send)


oauth_app = OAuthApp()
