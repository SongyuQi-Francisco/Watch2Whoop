"""HTTP endpoints for circles (Friends page, docs/friends-social-plan.md §4). Everything acts for the caller only: there is
no user in the path. Reading needs a normal sign-in; writing needs a write token (and X-Body-Web: 1 with cookie sign-in).
Wrong invite codes count per client IP and per user (params.JOIN_MAX_FAILURES in JOIN_WINDOW_SECONDS) → 429."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import auth
from ..records.api import _self, current_user, get_conn
from ..rollout import flags
from ..web import login_limit
from ..images import store as images
from . import cards, codes, leaderboard, params as P, remarks, store

# Every endpoint is behind the `friends` feature flag (docs/rollout.md): 404 while it is off for the caller
router = APIRouter(prefix="/v1/friends", tags=["friends"], dependencies=[Depends(flags.require(auth.CIRCLES_FLAG))])
_failures = login_limit.Limiter(P.JOIN_MAX_FAILURES, P.JOIN_WINDOW_SECONDS)


def _keys(request: Request, uid: int) -> list[str]:
    return [f"ip:{request.client.host if request.client else '?'}", f"u:{uid}"]


def _check_limit(request: Request, uid: int) -> None:
    wait = max(_failures.retry_after(k) for k in _keys(request, uid))
    if wait:
        raise HTTPException(429, "失败次数太多，请稍后再试", headers={"Retry-After": str(wait)})


def _fail(request: Request, uid: int) -> HTTPException:
    for k in _keys(request, uid):
        _failures.record(k)
    return HTTPException(404, "邀请码无效或已过期")


def _run(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except store.FriendsError as e:
        raise HTTPException(e.status, str(e))


class CircleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=200)
    scopes: list[str] = Field(default_factory=lambda: list(P.DEFAULT_SCOPES), max_length=30)


class RenameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=200)


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reset: bool = False
    days: int | None = None          # params.INVITE_DAYS_CHOICES; none = params.INVITE_DEFAULT_DAYS
    permanent: bool = False          # never expires (not together with days)


class JoinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str | None = Field(default=None, max_length=80)
    invite_id: int | None = None
    scopes: list[str] = Field(max_length=30)


class NudgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: int
    kind: str = Field(max_length=10)


class RemarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remark: str | None = Field(default=None, max_length=200)   # params.REMARK_MAX after trimming; empty / null clears


class ScopesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[str] = Field(max_length=30)


@router.get("")
def overview(conn=Depends(get_conn), viewer=Depends(current_user)):
    # first use of circles: make the personal circle and pick up a referral invite made while circles were off;
    # join the circles of activation codes used at sign-up once the consent allows it
    store.referral_offer(conn, viewer["id"])
    codes.complete_pending(conn, viewer["id"])
    return store.overview(conn, viewer["id"])


@router.get("/referral")
def referral(conn=Depends(get_conn), viewer=Depends(current_user)):
    """Onboarding's 「加入 XX 的圈子」 step: the open invite from the person whose referral code I signed up with, or null."""
    offer = store.referral_offer(conn, viewer["id"])
    return {"offer": offer and {**offer, "scopes": list(P.DEFAULT_SCOPES)}}


@router.post("/circles", status_code=201)
def create(body: CircleIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(store.create, conn, _self(viewer, "me"), body.name, body.scopes)


@router.patch("/circles/{circle_id}")
def rename(circle_id: int, body: RenameIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(store.rename, conn, _self(viewer, "me"), circle_id, body.name)


@router.post("/circles/{circle_id}/invite")
def invite(circle_id: int, body: InviteIn | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    body = body or InviteIn()
    return _run(store.mint_invite, conn, _self(viewer, "me"), circle_id, body.reset, days=body.days, permanent=body.permanent)


@router.delete("/circles/{circle_id}/invites/{invite_id}", status_code=204)
def revoke_invite(circle_id: int, invite_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Owner: stop one live invite code early."""
    _run(store.revoke_invite, conn, _self(viewer, "me"), circle_id, invite_id)
    return Response(status_code=204)


@router.get("/circles/{circle_id}/avatar")
def avatar(circle_id: int, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    """The circle's avatar (WebP), for its members only: 404 for anyone else and for a circle without one."""
    c = _run(store._circle_for_member, conn, circle_id, viewer["id"])
    img = images.get(conn, c["avatar_image_id"]) if c["avatar_image_id"] else None
    if img is None:
        raise HTTPException(404, "没有头像")
    etag = f'"{img["sha256"][:32]}"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=bytes(img["data"]), media_type=img["mime"], headers=headers)


@router.get("/invite")
def preview(code: str, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    """What an invite code opens, before the person picks what to share."""
    _check_limit(request, viewer["id"])
    row = _run(store.preview, conn, code)
    if row is None:
        raise _fail(request, viewer["id"])
    return row


@router.post("/join")
def join(body: JoinIn, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, "me")
    if (body.code is None) == (body.invite_id is None):
        raise HTTPException(400, "要给邀请码或邀请 ID（只给一个）")
    _check_limit(request, uid)
    try:
        return _run(store.join, conn, uid, body.scopes, code=body.code, invite_id=body.invite_id)
    except LookupError:
        raise _fail(request, uid)


@router.patch("/circles/{circle_id}/me")
def set_scopes(circle_id: int, body: ScopesIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(store.set_scopes, conn, _self(viewer, "me"), circle_id, body.scopes)


@router.delete("/circles/{circle_id}/me", status_code=204)
def leave(circle_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    _run(store.leave, conn, _self(viewer, "me"), circle_id)
    return Response(status_code=204)


@router.delete("/circles/{circle_id}/members/{member_id}", status_code=204)
def remove(circle_id: int, member_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    _run(store.remove_member, conn, _self(viewer, "me"), circle_id, member_id)
    return Response(status_code=204)


@router.delete("/circles/{circle_id}", status_code=204)
def dissolve(circle_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    _run(store.dissolve, conn, _self(viewer, "me"), circle_id)
    return Response(status_code=204)


@router.delete("/invites/{invite_id}", status_code=204)
def decline(invite_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    if not store.decline(conn, _self(viewer, "me"), invite_id):
        raise HTTPException(404, "这个邀请已失效")
    return Response(status_code=204)


@router.get("/overview")
def member_cards(conn=Depends(get_conn), viewer=Depends(current_user)):
    """Friends tab: my card and every co-member's (today + last 7 days), each block only if shared with me."""
    return cards.overview(conn, viewer["id"])


@router.post("/nudges")
def nudge(body: NudgeIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(cards.nudge, conn, _self(viewer, "me"), body.to, body.kind)


@router.get("/leaderboard")
def leaderboard_board(board: str = "body", conn=Depends(get_conn), viewer=Depends(current_user)):
    """One board (body / activity / lifestyle / recomp / longevity): me + circle members, ranked among those who share."""
    if board not in P.BOARDS:
        raise HTTPException(400, f"排行榜只能是 {', '.join(P.BOARDS)}")
    return leaderboard.board(conn, viewer["id"], board)


# ---------------------------------------------------------------- private remarks (#212, flag member_remarks)
REMARKS = [Depends(flags.require(remarks.FLAG))]


def _remark(fn, *args):
    try:
        return fn(*args)
    except remarks.RemarkError as e:
        raise HTTPException(e.status, str(e))


@router.put("/remarks/{target_id}", dependencies=REMARKS)
def set_remark(target_id: int, body: RemarkIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """What I call a circle co-member (only I see it); an empty remark clears it. 404 when we share no circle."""
    return _remark(remarks.set_remark, conn, _self(viewer, "me"), target_id, body.remark)


@router.delete("/remarks/{target_id}", status_code=204, dependencies=REMARKS)
def clear_remark(target_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    _remark(remarks.set_remark, conn, _self(viewer, "me"), target_id, None)
    return Response(status_code=204)
