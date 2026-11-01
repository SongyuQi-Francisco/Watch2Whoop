"""HTTP endpoints of circle moderation (friends/moderation.py, friends/rules.py; App Store guideline 1.2).

People (behind the `friends` flag like the rest of circles; reading needs a sign-in, writing a write token):
  GET    /v1/friends/blocks                     {blocks: [{id, display_name, created_at, source}]}  the 「已屏蔽」 list
  PUT    /v1/friends/blocks/{user_id}           block someone who shares a circle with me (404 otherwise)
  DELETE /v1/friends/blocks/{user_id}           unblock
  POST   /v1/friends/reports {kind, id, reason, note?}   kind post / comment / profile, reason params.REPORT_REASONS
  GET    /v1/friends/rules?lang=zh|en           {version, required, accepted, accepted_at, markdown}
  POST   /v1/friends/rules/accept {version}     records the acceptance (409 when the version is not the current one)
Admins (BODY_ADMIN_USER_IDS and a write token; 403 for everyone else):
  GET    /v1/admin/circles/reports?status=pending|open|hidden|kept|removed|all&limit=
  POST   /v1/admin/circles/reports/{id}/resolve {action: keep|remove|ban-user, note?}
  GET    /v1/admin/circles/reports/overdue      {pending, overdue, oldest_hours, within_hours}
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import auth
from ..i18n import L, lang
from ..notices import store as notices
from ..records.api import _self, current_user, get_conn
from ..rollout import flags
from . import moderation, params as P, rules, store

router = APIRouter(prefix="/v1/friends", tags=["friends"], dependencies=[Depends(flags.require(auth.CIRCLES_FLAG))])
admin_router = APIRouter(prefix="/v1/admin/circles", tags=["admin"])


def _run(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except store.FriendsError as e:
        raise HTTPException(e.status, str(e))


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["post", "comment", "profile"]
    id: int
    reason: Literal["spam", "harassment", "hate", "sexual", "violence", "self_harm", "other"] = "other"
    note: str | None = Field(default=None, max_length=1000)   # trimmed to params.REPORT_NOTE_MAX


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(max_length=40)


@router.get("/blocks")
def blocked(conn=Depends(get_conn), viewer=Depends(current_user)):
    return {"blocks": moderation.blocked_list(conn, viewer["id"])}


@router.put("/blocks/{user_id}")
def block(user_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(moderation.block, conn, _self(viewer, "me"), user_id)


@router.delete("/blocks/{user_id}")
def unblock(user_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(moderation.unblock, conn, _self(viewer, "me"), user_id)


@router.post("/reports")
def report(body: ReportIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(moderation.report, conn, _self(viewer, "me"), body.kind, body.id, body.reason, body.note)


@router.get("/rules")
def get_rules(lang_: str | None = Query(None, alias="lang"), conn=Depends(get_conn), viewer=Depends(current_user)):
    language = lang_ if lang_ in ("zh", "en") else lang()
    return {**rules.state(conn, viewer["id"]), "language": language, "markdown": rules.text(language),
            "report_reasons": list(P.REPORT_REASONS)}


@router.post("/rules/accept")
def accept_rules(body: AcceptIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(rules.accept, conn, _self(viewer, "me"), body.version)


# ---------------------------------------------------------------- admins
def admin(viewer=Depends(current_user)) -> dict:
    if not notices.is_admin(viewer["id"]) or "write" not in (viewer.get("token_scopes") or []):
        raise HTTPException(403, L("只有管理员可以处理举报", "Only admins can review reports"))
    return viewer


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["keep", "remove", "ban-user"]
    note: str | None = Field(default=None, max_length=500)


@admin_router.get("/reports")
def reports(status: str = Query("pending", pattern="^(pending|open|hidden|kept|removed|all)$"),
            limit: int = Query(100, ge=1, le=500), conn=Depends(get_conn), viewer=Depends(admin)):
    return {"items": moderation.queue(conn, status, limit), **moderation.overdue(conn)}


@admin_router.get("/reports/overdue")
def overdue(conn=Depends(get_conn), viewer=Depends(admin)):
    return moderation.overdue(conn)


@admin_router.post("/reports/{item_id}/resolve")
def resolve(item_id: int, body: ResolveIn, conn=Depends(get_conn), viewer=Depends(admin)):
    try:
        return moderation.resolve(conn, item_id, body.action, viewer["id"], body.note, actor=f"api:{viewer['id']}")
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
