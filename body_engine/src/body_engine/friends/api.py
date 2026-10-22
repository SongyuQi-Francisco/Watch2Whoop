"""HTTP endpoints for circles (Friends page, docs/friends-social-plan.md §4). Everything acts for the caller only: there is
no user in the path. Reading needs a normal sign-in; writing needs a write token (and X-Body-Web: 1 with cookie sign-in).
Wrong invite codes count per client IP and per user (params.JOIN_MAX_FAILURES in JOIN_WINDOW_SECONDS) → 429."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import auth
from ..records.api import _self, current_user, get_conn
from ..rollout import flags
from ..web import login_limit
from . import cards, leaderboard, params as P, store

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


class JoinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str | None = Field(default=None, max_length=40)
    invite_id: int | None = None
    scopes: list[str] = Field(max_length=30)


class NudgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: int
    kind: str = Field(max_length=10)


class ScopesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[str] = Field(max_length=30)


@router.get("")
def overview(conn=Depends(get_conn), viewer=Depends(current_user)):
    # first use of circles: make the personal circle and pick up a referral invite made while circles were off
    store.referral_offer(conn, viewer["id"])
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
    return _run(store.mint_invite, conn, _self(viewer, "me"), circle_id, bool(body and body.reset))


@router.get("/invite")
def preview(code: str, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    """What an invite code opens, before the person picks what to share."""
    _check_limit(request, viewer["id"])
    row = store.preview(conn, code)
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
