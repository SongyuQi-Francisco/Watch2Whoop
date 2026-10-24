"""Remote MCP endpoint /mcp (Streamable HTTP), for subscription-type users to connect from their own official client (docs/ai-assistant-plan.md section 9).

- Authentication: Authorization: Bearer <AI-only token> (ai/tokens.py, scope includes mcp): a static token made under 「AI 设置」
  or an OAuth access token (ai/oauth.py; the same api_tokens kind, expiring, tied to an approved connection). The database is
  queried on every request: revocation takes effect immediately, identity comes only from the token (the client's self-reported
  name is not trusted). Ordinary API tokens and the Today page cookie cannot be used here.
- A request without a valid token gets 401 with WWW-Authenticate pointing at the protected resource metadata (RFC 9728), which
  is how OAuth clients (Claude, ChatGPT, the Gemini app) find the authorization server.
- Tools, permissions and audit are exactly the same as stdio MCP (mcp_server.build_server); read-only tokens do not see write tools;
  audit channel = token name (mcp:<client>).
- Stateless mode (stateless + JSON responses): no session is kept in memory, a restart does not affect clients, and A's session can never be handed to B.
The ASGI app itself is the datalayer framework's (datalayer/mcp/http.py, issue #116); this module injects the token
lookup, the connection pool and the 401 challenge.
"""
from starlette.responses import JSONResponse

from ..datalayer.mcp.http import McpHttpApp as _McpHttpApp
from ..mcp_server import Identity, build_server
from . import oauth, oauth_http, tokens
from .datalayer_texts import MESSAGES

IDENTITY_KEY = "body_identity"


def _pool_conn():
    from .. import api
    return api.pool.connection()


def _lookup(token: str) -> Identity | None:
    from .. import api
    with api.pool.connection() as conn:
        user = tokens.user_for_mcp_token(conn, token)
    if user is None or (user.get("status") or "active") != "active":   # pending / disabled accounts (account/api.py)
        return None
    return Identity(user["id"], user["token_name"], "write" in (user["token_scopes"] or []))


def _unauthorized(scope, message: str, error: str | None = None) -> JSONResponse:
    return JSONResponse({"error": error or "unauthorized", "detail": message}, status_code=401,
                        headers={"WWW-Authenticate": oauth_http.challenge(oauth.base_url(scope), error)})


class McpHttpApp(_McpHttpApp):
    """The framework's ASGI app (datalayer/mcp/http.py) with Watch2Whoop's server, token lookup and 401 challenge.
    Token lookups go through the module's _lookup at call time."""

    def __init__(self):
        super().__init__(lambda identity_of: build_server(identity_of, _pool_conn), lambda token: _lookup(token),
                         _unauthorized, MESSAGES, identity_key=IDENTITY_KEY)


mcp_app = McpHttpApp()
