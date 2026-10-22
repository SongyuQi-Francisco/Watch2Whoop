"""Notification endpoints (issue #175). Everything is about oneself only (`me` or one's own id); writes need write scope.

Devices (the Watch2Whoop app; not behind the flag, so sign-out always works):
  POST   /v1/users/{user}/push-devices {token, environment: sandbox | production, bundle_id?, app_version?, lang?}
  DELETE /v1/users/{user}/push-devices {token?}   no token = every device registered with the Bearer token used
  GET    /v1/users/{user}/push-devices            the user's devices (token tail only) + whether the server can push
In-app list and settings (404 while the rollout flag `notifications` is off for the user):
  GET  /v1/users/{user}/notifications?before=&limit=   {items, more, unread}
  GET  /v1/users/{user}/notifications/unread           {unread}
  POST /v1/users/{user}/notifications/read {ids?, kind?}   neither = all
  GET / PUT /v1/users/{user}/notification-settings {categories: {circle: bool}, quiet: {enabled, start "HH:MM", end}}
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..i18n import L, is_en
from ..records.api import _self, current_user, get_conn
from ..rollout import flags
from . import core, params as P, store

router = APIRouter(prefix="/v1/users/{user}", tags=["notify"])
gated = [Depends(flags.require(P.FEATURE))]


def _own(viewer: dict, user: str) -> int:
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, L("只能看自己的通知", "You can only see your own notifications"))
    return viewer["id"]


class DeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=200)
    environment: Literal["sandbox", "production"]
    bundle_id: str | None = Field(None, max_length=200)
    app_version: str | None = Field(None, max_length=40)
    lang: Literal["zh", "en"] | None = None


class DeviceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str | None = Field(None, max_length=200)


@router.post("/push-devices")
def add_device(user: str, body: DeviceIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    try:
        row = store.register_device(conn, uid, body.token, body.environment, viewer.get("token_id"),
                                    body.bundle_id, body.app_version, body.lang)
    except ValueError:
        raise HTTPException(422, L("设备 token 格式不对", "Invalid device token"))
    conn.commit()
    return {**row, "push_configured": core.configured()}


@router.delete("/push-devices")
def remove_device(user: str, body: DeviceOut | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    n = store.remove_devices(conn, uid, (body.token if body else None), viewer.get("token_id"))
    conn.commit()
    return {"removed": n}


@router.get("/push-devices")
def list_devices(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return {"devices": store.devices(conn, _own(viewer, user)), "push_configured": core.configured()}


@router.get("/notifications", dependencies=gated)
def list_notifications(user: str, before: int | None = None, limit: int = Query(P.LIST_LIMIT, ge=1, le=P.LIST_LIMIT),
                       conn=Depends(get_conn), viewer=Depends(current_user)):
    return store.list_for(conn, _own(viewer, user), is_en(), before, limit)


@router.get("/notifications/unread", dependencies=gated)
def unread(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return {"unread": store.unread(conn, _own(viewer, user))}


class ReadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[int] | None = Field(None, max_length=500)
    kind: str | None = Field(None, max_length=60)


@router.post("/notifications/read", dependencies=gated)
def mark_read(user: str, body: ReadIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    n = store.mark_read(conn, uid, body.ids, body.kind)
    conn.commit()
    return {"marked": n, "unread": store.unread(conn, uid)}


def _settings_view(conn, uid: int) -> dict:
    en = is_en()
    st = core.settings(conn, uid)
    return {**st, "labels": {k: c["en" if en else "zh"] for k, c in P.CATEGORIES.items()},
            "push_configured": core.configured(), "devices": len(store.devices(conn, uid))}


@router.get("/notification-settings", dependencies=gated)
def get_settings(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _settings_view(conn, _own(viewer, user))


class QuietIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    start: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: dict[str, bool] | None = None
    quiet: QuietIn | None = None


@router.put("/notification-settings", dependencies=gated)
def put_settings(user: str, body: SettingsIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    try:
        core.save_settings(conn, uid, body.categories, body.quiet.model_dump(exclude_none=True) if body.quiet else None)
    except ValueError:
        raise HTTPException(422, L("没有这个通知类型", "Unknown notification type"))
    conn.commit()
    return _settings_view(conn, uid)
