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
import html
import json
from urllib.parse import parse_qs, urlparse

import anyio
from mcp.server.auth.routes import build_metadata
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.auth.routes import create_auth_routes
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse

from ..web.login_limit import Limiter
from . import oauth
from . import oauth_params as P

_register_ip = Limiter(P.REGISTER_PER_IP_HOUR, 3600)   # in-process first line; the database counts are the real limit
_authorize_ip = Limiter(P.AUTHORIZE_PER_IP, P.AUTHORIZE_WINDOW_S)

REG_OPTIONS = ClientRegistrationOptions(enabled=True, valid_scopes=list(oauth.SCOPES), default_scopes=list(oauth.SCOPES))
REV_OPTIONS = RevocationOptions(enabled=True)


def resource_metadata_url(base: str) -> str:
    return base.rstrip("/") + "/.well-known/oauth-protected-resource/mcp"


def challenge(base: str | None, error: str | None = None) -> str:
    """WWW-Authenticate value for a 401 from /mcp: where the protected resource metadata is (RFC 9728 §5.1).
    Without BODY_PUBLIC_URL OAuth is off and the challenge names no metadata."""
    if base is None:
        return 'Bearer realm="body-engine"' + (f', error="{error}"' if error else "")
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


def _ip_sha(scope, what: str = "register") -> str:
    client = scope.get("client") or ("", 0)
    return hashlib.sha256(f"oauth-{what}|{client[0]}".encode()).hexdigest()


class OAuthApp:
    """ASGI app mounted at /oauth. The SDK routes are built per issuer (BODY_PUBLIC_URL; OAuth is off without it), cached.

    /oauth/authorize is wrapped: per-IP rate limit, and the only redirect it may answer with is the one to our own consent
    page. When the SDK rejects a request (missing parameter, bad scope / resource / PKCE method) it would redirect straight
    to the client's registered URI with an error, without the user doing anything, which makes this host an open redirector
    (RFC 9700 §4.11.2); such answers become our own 400 page instead."""

    def __init__(self):
        self._apps: dict[str, Starlette] = {}

    def _app(self, base: str) -> Starlette:
        app = self._apps.get(base)
        if app is None:
            while len(self._apps) >= P.APPS_CACHE_MAX:
                self._apps.pop(next(iter(self._apps)))
            routes = [r for r in create_auth_routes(oauth.BodyOAuthProvider(base), AnyHttpUrl(oauth.issuer_url(base)),
                                                    client_registration_options=REG_OPTIONS, revocation_options=REV_OPTIONS)
                      if getattr(r, "path", "") != "/.well-known/oauth-authorization-server"]
            app = self._apps[base] = Starlette(routes=routes)
        return app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        base = oauth.base_url(scope)
        if base is None:
            await JSONResponse({"error": "not_found", "error_description": "OAuth is not enabled on this server (BODY_PUBLIC_URL)"},
                               status_code=404)(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.endswith("/authorize"):
            await self._authorize(base, scope, receive, send)
            return
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
        per_ip, unapproved = await anyio.to_thread.run_sync(_counts, ip)
        if per_ip >= P.REGISTER_PER_IP_HOUR or unapproved >= P.REGISTER_UNAPPROVED_PER_DAY:
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


    async def _authorize(self, base, scope, receive, send):
        ip = _ip_sha(scope, "authorize")
        if _authorize_ip.retry_after(ip):
            await error_page(429, "slow_down", "too many authorization requests, try again later")(scope, receive, send)
            return
        _authorize_ip.record(ip)
        consent = f"{base}/app/oauth.html?"
        state = {"blocked": False}

        async def guarded_send(message):
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in message.get("headers", [])}
                if 300 <= status < 400 and not headers.get("location", "").startswith(consent):
                    state["blocked"] = True
                    q = parse_qs(urlparse(headers.get("location", "")).query)
                    resp = error_page(400, (q.get("error") or ["invalid_request"])[0],
                                      (q.get("error_description") or [""])[0])
                    await resp(scope, receive, send)
                    return
            if state["blocked"]:
                return   # the SDK's redirect body: dropped, our page was sent instead
            await send(message)

        await self._app(base)(scope, receive, guarded_send)


def error_page(status: int, error: str, description: str = "") -> HTMLResponse:
    """Our own error page for /oauth/authorize (never a redirect to the client)."""
    e, d = html.escape(error[:60]), html.escape(description[:300])
    body = (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            f'initial-scale=1"><meta name="robots" content="noindex"><title>授权请求无效 / Invalid request</title>'
            f'<style>body{{font:16px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;max-width:420px;margin:15vh auto;'
            f'padding:0 16px}}code{{word-break:break-all}}</style></head><body><h1>授权请求无效</h1>'
            f'<p>这个 AI 应用发来的连接请求不完整或不被接受，请回到应用重新连接。</p><h1>Invalid authorization request</h1>'
            f'<p>The AI app sent a connection request that is incomplete or not accepted; connect again from the app.</p>'
            f'<p><code>{e}</code>{(" — " + d) if d else ""}</p></body></html>')
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


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
