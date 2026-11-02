"""Appearance endpoints for the signed-in user (own only). 404 while the slim_mode flag is off for them.
GET /v1/users/{user}/appearance -> {theme, quotes}; PUT sets either or both. The Today page gets the same object in /v1/web/me."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from ..i18n import L
from ..records.api import _self, current_user, get_conn
from ..rollout import flags
from . import store

router = APIRouter(prefix="/v1/users/{user}", tags=["appearance"], dependencies=[Depends(flags.require(store.FEATURE))])


class AppearanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: str | None = None
    quotes: bool | None = None


@router.get("/appearance")
def get_appearance(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, L("只能看自己的", "Only your own"))
    return store.get(conn, viewer["id"])


@router.put("/appearance")
def put_appearance(user: str, body: AppearanceIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    try:
        out = store.update(conn, uid, body.theme, body.quotes)
    except ValueError as e:
        raise HTTPException(422, str(e))
    conn.commit()
    return out
