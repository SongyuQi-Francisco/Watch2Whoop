"""HTTP endpoints for 「我 → 反馈记录」 (web/static/feedback.js): the caller's own feedback reports, their feedback mode, withdrawing
a report the maintainer has not looked at yet. Only one's own: shares never include feedback. Writing needs a write token and,
with cookie login, the X-Body-Web: 1 header (web/session.py)."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ..records.api import _self, current_user, get_conn
from . import params as P, store

router = APIRouter(prefix="/v1/users/{user}", tags=["feedback"])


def _own(viewer: dict, user: str) -> int:
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, "反馈记录只能看自己的")
    return viewer["id"]


class ModeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(pattern="^(auto|ask|off)$")


@router.get("/feedback")
def list_feedback(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _own(viewer, user)
    blocked = conn.execute("SELECT feedback_blocked_at FROM users WHERE id = %s", (uid,)).fetchone()["feedback_blocked_at"]
    return {"mode": store.mode(conn, uid), "chosen_mode": conn.execute(
                "SELECT feedback_mode FROM users WHERE id = %s", (uid,)).fetchone()["feedback_mode"],
            "available": store.server_mode() != "off" and not blocked,
            "statuses": P.STATUS_LABEL, "reports": store.list_for_user(conn, uid, body=True)}


@router.put("/feedback-mode")
def put_mode(user: str, body: ModeIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return {"mode": store.set_mode(conn, _self(viewer, user), body.mode)}


@router.delete("/feedback/{ref}", status_code=204)
def withdraw(user: str, ref: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    try:
        ok = store.withdraw(conn, uid, store.parse_ref(ref))
    except ValueError as e:
        raise HTTPException(409, str(e))
    if not ok:
        raise HTTPException(404, "没有这条反馈（或不是你的）")
    return Response(status_code=204)
