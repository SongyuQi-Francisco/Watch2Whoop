"""Streamable HTTP transport (moved from ai/mcp_http.py, issue #116): an ASGI app that authenticates the Bearer token
of every request first, then hands over to the MCP SDK's StreamableHTTPSessionManager.

- authenticate(token) -> Caller | None runs in a worker thread on every request (it may read the database), so a
  revoked token stops working at once; identity comes only from the token, never from what the client says.
- unauthorized(scope, detail, error) -> the 401 response (the application adds its WWW-Authenticate challenge).
- Stateless mode (stateless + JSON responses): no session is kept in memory, a restart does not affect clients, and
  one caller's session can never be handed to another.
A manager can only run once, so a new one is created each time the lifespan starts (tests start it repeatedly)."""
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

import anyio
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import JSONResponse

from ..messages import Messages
from ..policy import Caller

IDENTITY_KEY = "datalayer_caller"


class McpHttpApp:
    """server_factory(identity_of) -> the MCP server; identity_of reads the caller this app put into the request scope."""

    def __init__(self, server_factory: Callable[[Callable[[object], Caller]], Server],
                 authenticate: Callable[[str], Caller | None],
                 unauthorized: Callable[[dict, str, str | None], Callable[..., Awaitable[None]]],
                 messages: Messages, identity_key: str = IDENTITY_KEY):
        self.server_factory = server_factory
        self.authenticate = authenticate
        self.unauthorized = unauthorized
        self.messages = messages
        self.identity_key = identity_key
        self.manager: StreamableHTTPSessionManager | None = None

    def identity_of(self, ctx) -> Caller:
        request = getattr(ctx, "request", None)
        caller = request.scope.get(self.identity_key) if request is not None else None
        if not isinstance(caller, Caller):  # should not happen: a request that did not pass authentication cannot reach here
            raise PermissionError(self.messages.mcp_not_signed_in)
        return caller

    @asynccontextmanager
    async def lifespan(self):
        self.manager = StreamableHTTPSessionManager(app=self.server_factory(self.identity_of), stateless=True,
                                                    json_response=True)
        async with self.manager.run():
            yield
        self.manager = None

    async def __call__(self, scope, receive, send):
        m = self.messages
        if scope["type"] != "http" or self.manager is None:
            await JSONResponse({"detail": m.mcp_not_started}, status_code=503)(scope, receive, send)
            return
        header = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
        if not header.startswith("Bearer "):
            await self.unauthorized(scope, m.mcp_missing_bearer, None)(scope, receive, send)
            return
        caller = await anyio.to_thread.run_sync(self.authenticate, header.removeprefix("Bearer ").strip())
        if caller is None:
            await self.unauthorized(scope, m.mcp_invalid_token, "invalid_token")(scope, receive, send)
            return
        scope[self.identity_key] = caller
        await self.manager.handle_request(scope, receive, send)
