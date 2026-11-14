"""The inbox behind 「我 → 消息」 (inbox/store.py, issue #359 P1). Every endpoint is behind the flag `inbox` (404 while it is off for
the person) and is about oneself only (`me` or one's own id; anyone else: 403). Writes need write scope.

  GET    /v1/users/{user}/inbox?since=&before=&limit=     {items, unread, unread_from, more, next, now, keep_days}; since = ISO 8601 with a zone
  POST   /v1/users/{user}/inbox/read {ids} | {all: true}  {marked, unread}
  DELETE /v1/users/{user}/inbox/items/{id}                hide one item (notice:3, invite:12 …)
  POST   /v1/users/{user}/inbox/invites/{id}/accept       join with the circle's default scopes; the circle as /v1/friends/join
  POST   /v1/users/{user}/inbox/invites/{id}/ignore       decline
  POST   /v1/users/{user}/inbox/invites/{id}/mute         decline, and no more invites from whoever sent it (card.can_mute)
  GET    /v1/users/{user}/inbox/invites/{id}/avatar       the circle's picture, for the person the open invite is addressed to

Refusals are {code, message}: 404 invite_gone (no such invite for you, expired, withdrawn, blocked: all look the same),
409 consent_required (show the consent, then send again; has required_version), 428 rules_required (the same with the community
rules), 4xx join_refused (the circle is full, too many circles; message says which), 404 not_in_inbox, 422 bad_cursor / bad_request.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ..avatars.api import _image_response
from ..i18n import L
from ..records.api import _self, current_user, get_conn
from ..rollout import flags
from . import params as P, store

router = APIRouter(prefix="/v1/users/{user}/inbox", tags=["inbox"], dependencies=[Depends(flags.require(P.FLAG))])
InviteId = Annotated[int, Path(ge=1, le=2**31 - 1)]


def _own(viewer: dict, user: str) -> int:
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, L("只能看自己的消息", "You can only see your own messages"))
    return viewer["id"]


def _run(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except store.InboxError as e:
        raise HTTPException(e.status, {"code": e.code, "message": str(e), **e.extra})


@router.get("")
def inbox(user: str, since: AwareDatetime | None = None, before: str | None = Query(None, max_length=120),
          limit: int = Query(P.PAGE_LIMIT, ge=1, le=P.PAGE_LIMIT_MAX), conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(store.listing, conn, _own(viewer, user), since, before, limit)


class ReadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] | None = Field(None, max_length=P.READ_IDS_MAX)
    all: bool = False


@router.post("/read")
def mark_read(user: str, body: ReadIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    if (body.ids is None) != body.all:   # exactly one of the two, so an empty body never marks everything
        raise HTTPException(422, {"code": "bad_request", "message": L("要给 ids 或 all（只给一个）", "Give ids or all (one of them)")})
    out = store.mark_read(conn, uid, body.ids)
    conn.commit()
    return out


@router.delete("/items/{item_id}", status_code=204)
def hide(user: str, item_id: Annotated[str, Path(max_length=60)], conn=Depends(get_conn), viewer=Depends(current_user)):
    if not store.hide(conn, _self(viewer, user), item_id):
        raise HTTPException(404, {"code": "not_in_inbox", "message": L("没有这条消息", "No such message")})
    conn.commit()
    return Response(status_code=204)


@router.post("/invites/{invite_id}/accept")
def accept(user: str, invite_id: InviteId, conn=Depends(get_conn), viewer=Depends(current_user)):
    out = _run(store.accept, conn, _self(viewer, user), invite_id)
    conn.commit()
    return out


@router.post("/invites/{invite_id}/ignore")
def ignore(user: str, invite_id: InviteId, conn=Depends(get_conn), viewer=Depends(current_user)):
    _run(store.ignore, conn, _self(viewer, user), invite_id)
    conn.commit()
    return {"ignored": True}


@router.post("/invites/{invite_id}/mute")
def mute(user: str, invite_id: InviteId, conn=Depends(get_conn), viewer=Depends(current_user)):
    _run(store.mute_sender, conn, _self(viewer, user), invite_id)
    conn.commit()
    return {"muted": True}


@router.get("/invites/{invite_id}/avatar")
def invite_avatar(user: str, invite_id: InviteId, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _image_response(request, store.invite_avatar(conn, _own(viewer, user), invite_id))
