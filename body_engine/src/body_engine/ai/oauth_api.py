"""Today page side of the MCP OAuth server (ai/oauth.py): discovery documents, the consent step and the user's connections.

- GET /.well-known/oauth-protected-resource[/mcp], GET /.well-known/oauth-authorization-server[/oauth]: metadata (oauth_http.py).
- GET /v1/oauth/requests/{req}: what the consent page shows (app name, redirect host, scopes). POST: approve / deny.
  The consent page (/app/oauth.html) is opened by a redirect from the AI app's site, so its own GET carries no SameSite=Strict
  cookie; it loads first and then calls these endpoints from our own origin, where the session cookie is sent. Cookie writes
  also need X-Body-Web: 1 (web/session.py), and AI-client tokens cannot use them (current_user).
- GET /v1/users/{user}/ai-connections, DELETE /v1/users/{user}/ai-connections/{id}: approved OAuth connections, own only.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..i18n import L
from ..records.api import current_user, get_conn
from . import oauth, oauth_http

router = APIRouter(tags=["oauth"])
well_known = APIRouter(include_in_schema=False)
CORS = {"Access-Control-Allow-Origin": "*"}


@well_known.get("/.well-known/oauth-protected-resource")
@well_known.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource(request: Request):
    base = _base()
    return JSONResponse(oauth_http.protected_resource_metadata(base), headers=CORS)


@well_known.get("/.well-known/oauth-authorization-server")
@well_known.get("/.well-known/oauth-authorization-server/oauth")
def authorization_server(request: Request):
    return JSONResponse(oauth_http.authorization_server_metadata(_base()), headers=CORS)


def _base() -> str:
    base = oauth.base_url()
    if base is None:   # OAuth needs BODY_PUBLIC_URL; never derived from the Host header
        raise HTTPException(404, "OAuth is not enabled on this server")
    return base


def _write(viewer: dict) -> int:
    if "write" not in (viewer.get("token_scopes") or []):
        raise HTTPException(403, L("这个登录没有 write 权限", "This sign-in has no write permission"))
    return viewer["id"]


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    read_only: bool = Field(False, description="只给读权限")


@router.get("/v1/oauth/requests/{req}")
def get_request(req: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    v = oauth.request_view(conn, req)
    if v is None:
        raise HTTPException(404, L("授权请求已过期，请回到 AI 应用重新连接", "This authorization request expired; connect again from the AI app"))
    return {**v, "user": viewer.get("display_name")}


@router.post("/v1/oauth/requests/{req}")
def decide(req: str, body: DecisionIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    try:
        return {"redirect": oauth.decide(conn, req, _write(viewer), body.approve, body.read_only)}
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


def _own(viewer: dict, user: str) -> int:
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, L("AI 连接只能管自己的", "You can only manage your own AI connections"))
    return viewer["id"]


@router.get("/v1/users/{user}/ai-connections")
def list_connections(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return {"connections": oauth.list_grants(conn, _own(viewer, user))}


@router.delete("/v1/users/{user}/ai-connections/{grant_id}", status_code=204)
def revoke_connection(user: str, grant_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _own(viewer, user)
    _write(viewer)
    if not oauth.revoke_grant(conn, grant_id, "user", user_id=uid):
        raise HTTPException(404, L("没有这个连接（或已断开）", "No such connection (or already disconnected)"))
    return Response(status_code=204)
