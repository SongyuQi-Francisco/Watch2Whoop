"""Endpoints for the personal profile and electronic consent (Today page web/static/profile.js). Only your own; changes need write (cookie write requests also need X-Body-Web: 1).

- GET   /v1/users/{user}/profile   -> profile + missing + consent (required / version / accepted_at / reconfirm)
- PATCH /v1/users/{user}/profile   {display_name?, birth_date?, sex?, height_cm?, timezone?, goal_weight_kg?, language?}(null = clear; language null = follow the device)
- GET   /v1/consent                 -> {version, summary, text} (full consent form in Markdown)
- POST  /v1/users/{user}/consent   {version, agree: true} -> records time and version (table user_consents); then joins the
                                    circles of activation codes used at sign-up (circles_joined, friends/codes.py)
- PUT   /v1/users/{user}/intro     {seen: bool} -> the first-run intro was finished / skipped (true) or is to be shown again (false);
                                    users.intro_seen_at, read back as /v1/web/me -> me.intro_seen (issue #60)
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import consent, store

router = APIRouter(tags=["profile"])


def get_conn():
    from .. import api
    with api.pool.connection() as conn:
        yield conn


def current_user(request: Request, conn=Depends(get_conn)) -> dict:
    from .. import api
    return api.current_user(request, conn, request.headers.get("authorization"))


def _self(viewer: dict, user: str, write: bool = False) -> int:
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, "个人资料只能看 / 改自己的")
    if write and "write" not in (viewer.get("token_scopes") or []):
        raise HTTPException(403, "这个登录没有 write 权限")
    return viewer["id"]


def _out(conn, uid: int) -> dict:
    from ..friends import codes
    # circle_pending: circles of activation codes used at sign-up, joined once the consent is given (the consent sheet
    # lists them with what will be shared there)
    return {**store.get(conn, uid), "consent": consent.status(conn, uid), "circle_pending": codes.pending_for(conn, uid)}


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=60)
    birth_date: date | None = None
    sex: Literal["male", "female"] | None = None
    height_cm: float | None = None
    timezone: str | None = Field(default=None, max_length=60)
    goal_weight_kg: float | None = None
    language: Literal["zh", "en"] | None = None


class IntroIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seen: bool = Field(strict=True)


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=40)
    agree: Literal[True]
    language: Literal["zh", "en"] | None = None   # the text that was read; default the request's language


@router.get("/v1/users/{user}/profile")
def get_profile(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _out(conn, _self(viewer, user))


@router.patch("/v1/users/{user}/profile")
def patch_profile(user: str, body: ProfileIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user, True)
    patch = body.model_dump(exclude_unset=True)
    if "display_name" in patch and patch["display_name"] is None:
        raise HTTPException(422, "称呼不能清空")
    if "timezone" in patch and patch["timezone"] is None:
        raise HTTPException(422, "时区不能清空")
    try:
        store.update(conn, uid, patch)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return _out(conn, uid)


@router.get("/v1/consent")
def get_consent(viewer=Depends(current_user)):
    """In the request's language (X-Body-Lang); `language` says which text this is (both carry the same version)."""
    return {"version": consent.version(), "summary": consent.summary(), "text": consent.display_text(), "language": consent.lang()}


@router.post("/v1/users/{user}/consent")
def post_consent(user: str, body: ConsentIn, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user, True)
    try:
        consent.accept(conn, uid, body.version, "app", request.headers.get("user-agent"), language=body.language)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    from ..friends import codes
    joined = codes.complete_pending(conn, uid)   # circles of activation codes used at sign-up (friends/codes.py)
    return {**_out(conn, uid), "circles_joined": joined}


@router.put("/v1/users/{user}/intro")
def set_intro(user: str, body: IntroIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """First-run intro on the Today page: seen (finished or skipped) or reset so it shows again. Only your own."""
    uid = _self(viewer, user, write=True)
    row = conn.execute("UPDATE users SET intro_seen_at = CASE WHEN %s THEN coalesce(intro_seen_at, now()) END "
                       "WHERE id = %s RETURNING intro_seen_at", (body.seen, uid)).fetchone()
    return {"intro_seen": row["intro_seen_at"] is not None, "intro_seen_at": row["intro_seen_at"]}
