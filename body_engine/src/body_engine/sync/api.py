"""On-demand refresh endpoints (own data only; the Today page with its cookie, or the app's account token):

- POST /v1/users/{user}/sync/refresh -> 202 {accepted, throttled?, retry_after?, reason?, status, busy, imported_at, scored_at,
  last_import_at, linked}: pull new data from Open Wearables in the background, then recompute the last 3 days (refresh.py).
  At most one accepted request per user every 2 minutes; later ones just return the status (no error, so callers can poll).
- GET  /v1/users/{user}/sync/status -> the same status fields (「同步中… / 已更新 07:42」 on the Today page).
- GET  /v1/users/{user}/sync/connection -> {broken, reason, last_data_at, hours, after_hours, …}: whether the phone's
  Open Wearables sync looks broken (broken.py; 「手机同步已断开」 banner and sheet, web/static/sync-repair.js). Own data only.
- PUT  /v1/users/{user}/health-history {scope: days90 | all, upload_percent?, sent?, upload_done?} -> the app's read range
  and progress (sent by the Today page in the app's WebView); GET -> {scope, status, active, percent, …} for
  「历史数据导入中 x%」 (history.py). Own data only; PUT needs write.
- GET  /v1/users/{user}/data-status -> per health data type what the server received (data_status.py, issue #163): the
  Today page's 「我 → 数据状态」 (flag data_status). Own data only, read scope is enough.
- GET  /v1/ingest/data-status -> the same for the Watch2Whoop app's sync page: Bearer only, its account token or its
  upload-only token (like GET /v1/ingest/healthkit; dates and counts, no health values).
- /v1/ingest/resync (flag sync_center, 404 when off; Bearer like the above): the app's forced re-sync (resync.py).
  GET -> the job + the refresh status; POST {days: 1–3650 | null} -> 202 {accepted: true, …} or 200 {accepted: false,
  reason: busy | throttled | consent, retry_after?} (Retry-After header when throttled); PUT {phone_percent?, counts?,
  phone_done?} -> the phone's progress; DELETE -> cancel before the server part started.
- GET  /v1/users/{user}/sync/engine/days?date=&end= (flag apple_sync_engine, 404 when off for this user): the two sync
  paths (Open Wearables and the app's own sync engine) side by side per day, at most 31 days (rawdata/side_by_side.py,
  for the shadow comparison of docs/apple-sync-engine.md §4). Own data only, read scope is enough.
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ..i18n import L
from ..records.api import current_user, get_conn
from ..rawdata import engine as rx_engine, side_by_side
from ..rawdata.api import ingest_user
from . import broken, data_status, history, refresh, resync

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


@router.get("/v1/users/{user}/sync/connection")
def sync_connection(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, L("只能看自己的同步状态", "You can only see your own sync status"))
    res = broken.check(conn, viewer["id"])
    res.pop("user_id", None)
    return res


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


@router.get("/v1/users/{user}/data-status")
def get_data_status(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, L("只能看自己的数据状态", "You can only see your own data status"))
    return data_status.collect(conn, viewer["id"])


@router.get("/v1/users/{user}/sync/engine/days")
def get_engine_days(user: str, start: date = Query(alias="date"), end: date | None = None, conn=Depends(get_conn),
                    viewer=Depends(current_user)):
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, L("只能看自己的数据", "You can only see your own data"))
    if not rx_engine.enabled(conn, viewer["id"]):
        raise HTTPException(404, L("没有开启", "Not enabled"))
    try:
        return side_by_side.days(conn, viewer["id"], start, end)
    except ValueError:
        raise HTTPException(422, L(f"日期范围要在 1–{side_by_side.MAX_DAYS} 天之间",
                                   f"The date range must be 1–{side_by_side.MAX_DAYS} days")) from None


@router.get("/v1/ingest/data-status")
def get_ingest_data_status(conn=Depends(get_conn), viewer=Depends(ingest_user)):
    return data_status.collect(conn, viewer["id"])


# ---------------------------------------------------------------- forced re-sync (the app's sync center)
def _resync_user(conn=Depends(get_conn), viewer=Depends(ingest_user)) -> dict:
    from ..rollout import flags
    if not flags.enabled(conn, viewer["id"], "sync_center"):
        raise HTTPException(404, L("这个功能还没有对你开放", "This feature is not available to you yet"))
    return viewer


class ResyncIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int | None = Field(None, ge=1, le=3650)   # null = all history


class ResyncProgressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_percent: int | None = Field(None, ge=0, le=100)
    counts: dict[str, int] | None = Field(None, max_length=20)
    phone_done: bool = False


@router.get("/v1/ingest/resync")
def get_resync(conn=Depends(get_conn), viewer=Depends(_resync_user)):
    resync.wake_if_waiting(conn, viewer["id"])
    return resync.status(conn, viewer["id"])


@router.post("/v1/ingest/resync", status_code=202)
def post_resync(body: ResyncIn, response: Response, conn=Depends(get_conn), viewer=Depends(_resync_user)):
    res = resync.request(conn, viewer["id"], body.days)
    if not res["accepted"]:
        response.status_code = 200
        if res.get("retry_after"):
            response.headers["Retry-After"] = str(res["retry_after"])
    return res


@router.put("/v1/ingest/resync")
def put_resync(body: ResyncProgressIn, conn=Depends(get_conn), viewer=Depends(_resync_user)):
    return resync.progress(conn, viewer["id"], body.phone_percent, body.counts, body.phone_done)


@router.delete("/v1/ingest/resync")
def delete_resync(conn=Depends(get_conn), viewer=Depends(_resync_user)):
    return resync.cancel(conn, viewer["id"])
