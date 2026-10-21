"""On-demand refresh endpoints (own data only; the Today page with its cookie, or the app's account token):

- POST /v1/users/{user}/sync/refresh -> 202 {accepted, throttled?, retry_after?, reason?, status, busy, imported_at, scored_at,
  last_import_at, linked}: pull new data from Open Wearables in the background, then recompute the last 3 days (refresh.py).
  At most one accepted request per user every 2 minutes; later ones just return the status (no error, so callers can poll).
- GET  /v1/users/{user}/sync/status -> the same status fields (「同步中… / 已更新 07:42」 on the Today page).
"""
from fastapi import APIRouter, Depends, HTTPException, Response

from ..i18n import L
from ..records.api import current_user, get_conn
from . import refresh

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
