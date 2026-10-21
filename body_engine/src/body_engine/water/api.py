"""Water endpoints (scope nutrition). GET for oneself or a viewer with the nutrition share; POST / DELETE only for oneself
with write permission. 404 "feature_off" while the rollout flag is off for this user (water/__init__.enabled)."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..records.api import _owner, _self, current_user, get_conn
from . import enabled, params as P, store

router = APIRouter(prefix="/v1/users/{user}", tags=["water"])


def _on(conn, uid: int) -> None:
    if not enabled(conn, uid):
        raise HTTPException(404, "feature_off")


class WaterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ml: int = Field(gt=0, le=P.MAX_ML)
    source: Literal["button", "manual"] = "button"
    at: str | None = Field(None, max_length=40)            # HH:MM or ISO; null = now
    client_id: str | None = Field(None, max_length=64)     # the page's retry key (a double tap is stored once)


@router.get("/water")
def water_day(user: str, date: date | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    """One day's water: total, goal (diet profile water_ml or the default, plus training / sauna extras), entries, undo."""
    uid = _owner(conn, viewer, user, "nutrition")
    _on(conn, uid)
    return store.day_view(conn, uid, date)


@router.post("/water")
def water_add(user: str, body: WaterIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    _on(conn, uid)
    try:
        at = store.parse_at(body.at, store.tz_of(conn, uid))
        row = store.add(conn, uid, body.ml, body.source, at, client_id=body.client_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    conn.commit()
    return {"entry": row, "day": store.day_view(conn, uid, row["date"])}


@router.delete("/water/{entry_id}")
def water_delete(user: str, entry_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    _on(conn, uid)
    row = store.delete(conn, uid, entry_id)
    if row is None:
        raise HTTPException(404, "没有这条喝水记录")
    conn.commit()
    return {"deleted": row, "day": store.day_view(conn, uid, row["date"])}
