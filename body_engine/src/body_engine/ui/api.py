"""Endpoints of the AI-first page (#279): only for oneself, and 404 while the ui_ai_first flag is off for the signed-in user.
Writes need the write permission; with the Today page's cookie they carry X-Body-Web: 1 (web/session.py)."""
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..i18n import L
from ..records.api import _self, current_user, get_conn
from ..rollout import flags
from ..water.store import tz_of
from . import home as H
from . import params as P

router = APIRouter(prefix="/v1/users/{user}/ui", tags=["ui"], dependencies=[Depends(flags.require(P.FEATURE))])
Focus = Literal["fat", "muscle", "sleep", "recover", "markers"]


class FocusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus: list[Focus] = Field(max_length=len(P.FOCUS), description="关注点，可多选；空 = 不选")


class ModuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["pinned", "hidden", "auto"]


def _today(conn, uid: int, day: date | None) -> date:
    return day or datetime.now(tz_of(conn, uid)).date()


@router.get("/home")
def get_home(user: str, date: date | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    """My Today: the cards in order with why each is there, the focus, and (until it was seen) the first screen's body overview."""
    uid = _self(viewer, user)
    return H.home(conn, uid, _today(conn, uid, date))


@router.put("/focus")
def put_focus(user: str, body: FocusIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    H.set_focus(conn, uid, list(body.focus))
    conn.commit()
    return H.home(conn, uid, _today(conn, uid, None))


@router.post("/overview-seen")
def post_overview_seen(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    H.overview_seen(conn, uid)
    conn.commit()
    return {"ok": True}


@router.put("/modules/{key}")
def put_module(user: str, key: str, body: ModuleIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """pinned = on my Today first; hidden = off my Today (it stays in 全部功能); auto = back to the rules."""
    uid = _self(viewer, user)
    if key not in P.MODULES:
        raise HTTPException(422, L("没有这个模块", "No such module"))
    H.set_module(conn, uid, key, body.state)
    conn.commit()
    return H.home(conn, uid, _today(conn, uid, None))
