"""Admin notices (notices/store.py).

Own (the Today page; `me` or one's own id):
  GET  /v1/users/{user}/notices              {items: [{id, title, body, link, link_label, created_at}]} not seen yet
  POST /v1/users/{user}/notices/{id}/seen    marks it seen (shown once); needs write
Admins (BODY_ADMIN_USER_IDS; 403 for everyone else, also with write scope):
  POST   /v1/admin/notices {user_id, title, body?, link?, link_label?, expires_days?} -> 201 {id, …}
  GET    /v1/admin/notices?user_id=&limit=   the latest notices with seen_at
  DELETE /v1/admin/notices/{id}              withdraw one that was not seen yet
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..i18n import L
from ..records.api import _self, current_user, get_conn
from . import store

router = APIRouter(tags=["notices"])


@router.get("/v1/users/{user}/notices")
def my_notices(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, L("只能看自己的消息", "You can only see your own messages"))
    return {"items": store.pending(conn, viewer["id"])}


@router.post("/v1/users/{user}/notices/{notice_id}/seen")
def seen(user: str, notice_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    ok = store.mark_seen(conn, _self(viewer, user), notice_id)
    conn.commit()
    return {"seen": ok}


def admin(viewer=Depends(current_user)) -> dict:
    if not store.is_admin(viewer["id"]) or "write" not in (viewer.get("token_scopes") or []):
        raise HTTPException(403, L("只有管理员可以发消息", "Only admins can send messages"))
    return viewer


class NoticeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int
    title: str = Field(min_length=1, max_length=store.MAX_TITLE)
    body: str = Field("", max_length=store.MAX_BODY)
    link: str | None = Field(None, max_length=store.MAX_LINK)
    link_label: str | None = Field(None, max_length=store.MAX_LABEL)
    expires_days: int | None = Field(None, ge=1, le=365)


@router.post("/v1/admin/notices", status_code=201)
def send(body: NoticeIn, conn=Depends(get_conn), viewer=Depends(admin)):
    try:
        row = store.send(conn, body.user_id, body.title, body.body, body.link, body.link_label, viewer["id"], body.expires_days)
    except ValueError as e:
        conn.rollback()
        raise HTTPException(422, str(e)) from None
    conn.commit()
    return row


@router.get("/v1/admin/notices")
def list_notices(user_id: int | None = None, limit: int = Query(50, ge=1, le=200), conn=Depends(get_conn),
                 viewer=Depends(admin)):
    return {"items": store.list_all(conn, user_id, limit)}


@router.delete("/v1/admin/notices/{notice_id}")
def revoke(notice_id: int, conn=Depends(get_conn), viewer=Depends(admin)):
    ok = store.revoke(conn, notice_id)
    conn.commit()
    if not ok:
        raise HTTPException(404, L("没有这条未读消息", "No such unseen message"))
    return {"revoked": True}
