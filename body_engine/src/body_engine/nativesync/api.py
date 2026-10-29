"""Endpoints of the native app's sync (#340 N2; docs/ios-native-app.md sections 5 and 7.2). Rollout flag native_sync:
404 while it is off for the user, and /v1/web/me lists it in `flags` when on, so the app knows the server has them.

POST /v1/sync/pull             {cursors: {stream: cursor|""}, limit?, raw_days?, raw_since?, history_before?, kinds?}
                                                                   -> changes (pull.py; raw_history alone, throttle.py)
GET  /v1/sync/scoring          ?from&to&hist_from&hist_to                         -> the phone's scoring context (scoring.py)
POST /v1/sync/push             {mutations: [...]}                                 -> one result per mutation (push.py)
PUT  /v1/users/{user}/scores/app {algo_version, bundle_version, days}             -> stored / checked (appscores.py)
2.0.0 local compute (docs/ios-native-2.0.md):
GET  /v1/sync/compute          same query as /v1/sync/scoring              -> its answer + per package off|shadow|primary (compute.py)
PUT  /v1/users/{user}/derived/app {kind, algo_version, bundle_version, days}      -> checked / stored (derived.py)
GET  /v1/users/{user}/ext-records ?from&to[&fingerprints=1]                       -> raw log rows per day (ext_records.py)
Only one's own data; pull needs a read token, the writes a write token.
"""
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..i18n import L
from ..records.api import _self, current_user, get_conn
from ..rollout import flags
from . import appscores, compute, derived, ext_records, params as P, pull as PL, push as PS, scoring, throttle

router = APIRouter(tags=["native-sync"], dependencies=[Depends(flags.require(P.FLAG))])


class PullIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cursors: dict[str, str | None] = Field(min_length=1, max_length=len(P.STREAMS))
    limit: int = Field(P.PULL_LIMIT_DEFAULT, ge=1, le=P.PULL_LIMIT_MAX)
    raw_days: list[date] = Field(default_factory=list, max_length=P.RAW_DAYS_MAX)
    raw_since: date | None = None   # a raw restore stops at this day (the phone keeps params.APP_RAW_DAYS)
    history_before: date | None = None   # raw_history: only the days before this one (the phone has the rest)
    kinds: dict[str, list[str]] = Field(default_factory=dict)   # {"records": ["workout"]}: only those kinds (pull.py)


@router.post("/v1/sync/pull")
def sync_pull(body: PullIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = viewer["id"]
    try:
        if P.HISTORY_STREAM in body.cursors:
            if len(body.cursors) > 1 or body.raw_days:
                raise HTTPException(422, L(f"{P.HISTORY_STREAM} 要单独拉", f"Pull {P.HISTORY_STREAM} on its own"))
            try:
                with throttle.history_page(uid):
                    return PL.pull(conn, uid, body.cursors, body.limit, None, None, body.history_before, body.kinds)
            except throttle.SlowDown as s:
                return JSONResponse({"detail": {"message": L("服务器忙，请稍后再拉历史", "Server busy; fetch the history later"),
                                                "code": "slow_down", "retry_after": s.retry_after}},
                                    status_code=429, headers={"Retry-After": str(s.retry_after)})
        return PL.pull(conn, uid, body.cursors, body.limit, body.raw_days, body.raw_since, None, body.kinds)
    except KeyError as e:
        raise HTTPException(422, L(f"没有这个 stream：{e.args[0]}", f"Unknown stream: {e.args[0]}"))
    except PL.BadKinds as e:
        raise HTTPException(422, {"message": L(f"kinds 不对：{e}", f"Bad kinds: {e}"), "code": "bad_kinds"})
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


# ---------------------------------------------------------------- 2.0.0: every own-data computation on the phone
def _own(viewer: dict, user: str) -> int:
    """Reads of one's own sync data only (the phone's copy; shares never cover these)."""
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, L("只能读自己的同步数据", "Only your own sync data can be read"))
    return viewer["id"]


@router.get("/v1/sync/compute")
def sync_compute(from_: date | None = Query(None, alias="from"), to: date | None = None, hist_from: date | None = None,
                 hist_to: date | None = None, conn=Depends(get_conn), viewer=Depends(current_user),
                 x_app_version: Annotated[str | None, Header()] = None, x_app_build: Annotated[str | None, Header()] = None):
    """GET /v1/sync/scoring's answer plus `compute`: where each 2.0.0 package is computed for this user and client
    (compute.py). Everything is `off` until a package is implemented and its flag is on."""
    try:
        out = scoring.context(conn, viewer["id"], from_, to, hist_from, hist_to)
    except ValueError as e:
        raise HTTPException(422, str(e))
    out["compute"] = compute.overview(conn, viewer["id"], x_app_version, x_app_build)
    return out


class DerivedDayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=P.DERIVED_MAX_ROWS_PER_DAY)


class DerivedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=40)
    algo_version: str = Field(min_length=1, max_length=60)
    bundle_version: str | None = Field(None, max_length=40)
    days: list[DerivedDayIn] = Field(max_length=P.DERIVED_MAX_DAYS)


@router.put("/v1/users/{user}/derived/app")
def put_derived(user: str, body: DerivedIn, conn=Depends(get_conn), viewer=Depends(current_user),
                x_app_version: Annotated[str | None, Header()] = None, x_app_build: Annotated[str | None, Header()] = None):
    uid = _self(viewer, user)
    try:
        return derived.upload(conn, uid, body.kind, body.algo_version, body.bundle_version,
                              [d.model_dump() for d in body.days], x_app_version, x_app_build)
    except KeyError:
        conn.rollback()
        raise HTTPException(422, L(f"没有这个类型：{body.kind}", f"Unknown kind: {body.kind}"))
    except derived.KindOff:
        conn.rollback()
        raise HTTPException(409, {"message": L("这个类型现在不在手机上计算", "This kind is not computed on the phone now"),
                                  "code": "kind_off"})
    except ValueError as e:
        conn.rollback()
        raise HTTPException(422, str(e))


@router.get("/v1/users/{user}/ext-records")
def get_ext_records(user: str, from_: date | None = Query(None, alias="from"), to: date | None = None,
                    fingerprints: bool = False, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _own(viewer, user)
    if to is None:
        tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])
        to = datetime.now(tz).date()
    from_ = from_ or to - timedelta(days=P.EXT_DEFAULT_DAYS - 1)
    if to < from_ or (to - from_).days >= P.EXT_MAX_DAYS:
        raise HTTPException(422, L(f"from / to：最多 {P.EXT_MAX_DAYS} 天", f"from / to: at most {P.EXT_MAX_DAYS} days"))
    return (ext_records.fingerprints if fingerprints else ext_records.days)(conn, uid, from_, to)
