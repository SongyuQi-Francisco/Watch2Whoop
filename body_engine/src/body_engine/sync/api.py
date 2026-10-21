"""On-demand refresh endpoints (own data only; the Today page with its cookie, or the app's account token):

- POST /v1/users/{user}/sync/refresh -> 202 {accepted, throttled?, retry_after?, reason?, status, busy, imported_at, scored_at,
  last_import_at, linked}: pull new data from Open Wearables in the background, then recompute the last 3 days (refresh.py).
  At most one accepted request per user every 2 minutes; later ones just return the status (no error, so callers can poll).
- GET  /v1/users/{user}/sync/status -> the same status fields (「同步中… / 已更新 07:42」 on the Today page).
- PUT  /v1/users/{user}/health-history {scope: days90 | all, upload_percent?, sent?, upload_done?} -> the app's read range
  and progress (sent by the Today page in the app's WebView); GET -> {scope, status, active, percent, …} for
  「历史数据导入中 x%」 (history.py). Own data only; PUT needs write.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ..i18n import L
from ..records.api import current_user, get_conn
from . import history, refresh

router = APIRouter(tags=["sync"])


def _own(viewer: dict, user: str) -> int:
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, L("只能刷新自己的数据", "You can only refresh your own data"))
    return viewer["id"]


@router.post("/v1/users/{user}/sync/refresh", status_code=202)
def sync_refresh(user: str, response: Response, conn=Depends(get_conn), viewer=Depends(current_user)):
    res = refresh.request(conn, _own(viewer, user))
    if res.get("throttled"):
        response.headers["Retry-After"] = str(res["retry_after"])
    return res


@router.get("/v1/users/{user}/sync/status")
def sync_status(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return refresh.status(conn, _own(viewer, user))


class HealthHistoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["days90", "all"]
    upload_percent: int | None = Field(None, ge=0, le=100)
    sent: int | None = Field(None, ge=0)
    upload_done: bool = False


@router.put("/v1/users/{user}/health-history")
def put_health_history(user: str, body: HealthHistoryIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _own(viewer, user)
    if "write" not in (viewer.get("token_scopes") or []):
        raise HTTPException(403, L("这个登录没有 write 权限", "This sign-in cannot write"))
    return history.report(conn, uid, body.scope, body.upload_percent, body.sent, body.upload_done)


@router.get("/v1/users/{user}/health-history")
def get_health_history(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return history.status(conn, _own(viewer, user))
