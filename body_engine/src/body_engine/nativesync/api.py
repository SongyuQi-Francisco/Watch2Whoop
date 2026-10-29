"""Endpoints of the native app's sync (#340 N2; docs/ios-native-app.md sections 5 and 7.2). Rollout flag native_sync:
404 while it is off for the user, and /v1/web/me lists it in `flags` when on, so the app knows the server has them.

POST /v1/sync/pull             {cursors: {stream: cursor|""}, limit?, raw_days?, raw_since?, history_before?}
                                                                   -> changes (pull.py; raw_history alone, throttle.py)
GET  /v1/sync/scoring          ?from&to&hist_from&hist_to                         -> the phone's scoring context (scoring.py)
POST /v1/sync/push             {mutations: [...]}                                 -> one result per mutation (push.py)
PUT  /v1/users/{user}/scores/app {algo_version, bundle_version, days}             -> stored / checked (appscores.py)
Only one's own data; pull needs a read token, the writes a write token.
"""
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..i18n import L
from ..records.api import _self, current_user, get_conn
from ..rollout import flags
from . import appscores, params as P, pull as PL, push as PS, scoring, throttle

router = APIRouter(tags=["native-sync"], dependencies=[Depends(flags.require(P.FLAG))])


class PullIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cursors: dict[str, str | None] = Field(min_length=1, max_length=len(P.STREAMS))
    limit: int = Field(P.PULL_LIMIT_DEFAULT, ge=1, le=P.PULL_LIMIT_MAX)
    raw_days: list[date] = Field(default_factory=list, max_length=P.RAW_DAYS_MAX)
    raw_since: date | None = None   # a raw restore stops at this day (the phone keeps params.APP_RAW_DAYS)
    history_before: date | None = None   # raw_history: only the days before this one (the phone has the rest)


@router.post("/v1/sync/pull")
def sync_pull(body: PullIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = viewer["id"]
    try:
        if P.HISTORY_STREAM in body.cursors:
            if len(body.cursors) > 1 or body.raw_days:
                raise HTTPException(422, L(f"{P.HISTORY_STREAM} 要单独拉", f"Pull {P.HISTORY_STREAM} on its own"))
            try:
                with throttle.history_page(uid):
                    return PL.pull(conn, uid, body.cursors, body.limit, None, None, body.history_before)
            except throttle.SlowDown as s:
                return JSONResponse({"detail": {"message": L("服务器忙，请稍后再拉历史", "Server busy; fetch the history later"),
                                                "code": "slow_down", "retry_after": s.retry_after}},
                                    status_code=429, headers={"Retry-After": str(s.retry_after)})
        return PL.pull(conn, uid, body.cursors, body.limit, body.raw_days, body.raw_since)
    except KeyError as e:
        raise HTTPException(422, L(f"没有这个 stream：{e.args[0]}", f"Unknown stream: {e.args[0]}"))
    except PL.BadCursor:
        raise HTTPException(422, {"message": L("游标不对，请从头拉取", "Bad cursor; pull from the start"), "code": "bad_cursor"})


class MutationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=P.CLIENT_ID_MAX)
    kind: Literal[PS.KINDS]   # type: ignore[valid-type]
    op: Literal["upsert", "delete"] = "upsert"
    key: str | None = Field(None, max_length=120)
    base_version: int | None = Field(None, ge=0)
    body: dict[str, Any] = Field(default_factory=dict)


class PushIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mutations: list[MutationIn] = Field(min_length=1, max_length=P.PUSH_MAX_MUTATIONS)


@router.post("/v1/sync/push")
def sync_push(body: PushIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    _self(viewer, "me")
    conn.commit()   # whatever the auth step wrote; each mutation is its own transaction
    return PS.push(conn, viewer, [m.model_dump() for m in body.mutations])


@router.get("/v1/sync/scoring")
def sync_scoring(from_: date | None = Query(None, alias="from"), to: date | None = None, hist_from: date | None = None,
                 hist_to: date | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    """What the phone needs from the server to score the user itself (scoring.py): hrv_kind, sync mode, app scoring
    state; muscle load of [from, to]; per-day heart-rate histograms of [hist_from, hist_to]."""
    try:
        return scoring.context(conn, viewer["id"], from_, to, hist_from, hist_to)
    except ValueError as e:
        raise HTTPException(422, str(e))


class ScoreDayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    row: dict[str, Any]


class AppScoresIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    algo_version: str = Field(min_length=1, max_length=60)
    bundle_version: str | None = Field(None, max_length=40)
    days: list[ScoreDayIn] = Field(max_length=P.UPLOAD_MAX_DAYS)


@router.put("/v1/users/{user}/scores/app")
def put_app_scores(user: str, body: AppScoresIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    try:
        return appscores.upload(conn, uid, body.algo_version, body.bundle_version, [d.model_dump() for d in body.days])
    except ValueError as e:
        conn.rollback()
        raise HTTPException(422, str(e))
