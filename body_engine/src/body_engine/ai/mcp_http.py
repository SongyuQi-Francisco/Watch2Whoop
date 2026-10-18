"""Remote MCP endpoint /mcp (Streamable HTTP), for subscription-type users to connect from their own official client (docs/ai-assistant-plan.md section 9).

- Authentication: Authorization: Bearer <AI-only token> (ai/tokens.py, scope includes mcp). The database is queried on every request: revocation takes effect immediately,
  identity comes only from the token (the client's self-reported name is not trusted). Ordinary API tokens and the Today page cookie cannot be used here.
- Tools, permissions and audit are exactly the same as stdio MCP (mcp_server.build_server); read-only tokens do not see write tools;
  audit channel = token name (mcp:<client>).
- Stateless mode (stateless + JSON responses): no session is kept in memory, a restart does not affect clients, and A's session can never be handed to B.
- Only open inside the tailnet (tailscale serve -> 127.0.0.1:8100), no Funnel, not on the public internet.
"""
from contextlib import asynccontextmanager

import anyio
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import JSONResponse

from ..mcp_server import Identity, build_server
from . import tokens

IDENTITY_KEY = "body_identity"


def _identity_of(ctx) -> Identity:
    request = getattr(ctx, "request", None)
    ident = request.scope.get(IDENTITY_KEY) if request is not None else None
    if not isinstance(ident, Identity):  # Should not happen: a request that did not pass authentication cannot reach here
        raise PermissionError("没有登录")
    return ident


def _pool_conn():
    from .. import api
    return api.pool.connection()


def _lookup(token: str) -> Identity | None:
    from .. import api
    with api.pool.connection() as conn:
        user = tokens.user_for_mcp_token(conn, token)
    if user is None:
        return None
    return Identity(user["id"], user["token_name"], "write" in (user["token_scopes"] or []))


def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse({"error": "unauthorized", "detail": message}, status_code=401,
                        headers={"WWW-Authenticate": 'Bearer realm="body-engine"'})


class McpHttpApp:
    """ASGI app: authenticate the token first, then hand over to the MCP SDK's StreamableHTTPSessionManager.
    A manager can only run once, so a new one is created each time the FastAPI lifespan starts (tests start it repeatedly)."""

    def __init__(self):
        self.manager: StreamableHTTPSessionManager | None = None

    @asynccontextmanager
    async def lifespan(self):
        self.manager = StreamableHTTPSessionManager(app=build_server(_identity_of, _pool_conn), stateless=True, json_response=True)
        async with self.manager.run():
            yield
        self.manager = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.manager is None:
            await JSONResponse({"detail": "MCP 没有启动"}, status_code=503)(scope, receive, send)
            return
        header = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
        if not header.startswith("Bearer "):
            await _unauthorized("缺少 Bearer token（用今天页「AI 设置」里生成的 AI token）")(scope, receive, send)
            return
        ident = await anyio.to_thread.run_sync(_lookup, header.removeprefix("Bearer ").strip())
        if ident is None:
            await _unauthorized("token 无效、已吊销，或不是 AI 专用 token")(scope, receive, send)
            return
        scope[IDENTITY_KEY] = ident
        await self.manager.handle_request(scope, receive, send)


mcp_app = McpHttpApp()
