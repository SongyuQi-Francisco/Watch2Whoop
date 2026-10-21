"""ECG and heartbeat-series endpoints.

ECG uses its own share scope 'ecg' (ordinary shares do not include it, docs/raw-data-plan.md §7.1); the owner always sees their own.
Heartbeat readings (per-reading RMSSD / SDNN summary, no beat arrays) use scope 'scores'.
POST /v1/ingest/healthkit: uploads from the Watch2Whoop app (ingest.py), with the app's account token or its upload-only token.
"""
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .. import auth
from ..account import api as account_api
from ..i18n import L
from ..records.api import _found, _owner, _self, current_user, get_conn
from . import ingest as I
from . import store

router = APIRouter(prefix="/v1/users/{user}", tags=["rawdata"])
ingest_router = APIRouter(tags=["rawdata"])


def _tz(conn, user_id: int) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])


@router.get("/ecg")
def ecg_list(user: str, limit: int = Query(100, ge=1, le=500), conn=Depends(get_conn), viewer=Depends(current_user)):
    """ECG recordings, newest first: Apple's classification, heart rate, symptoms (no waveform)."""
    uid = _owner(conn, viewer, user, "ecg")
    return {"recordings": store.list_ecg(conn, uid, limit, _tz(conn, uid))}


@router.get("/ecg/{ecg_id}")
def ecg_detail(user: str, ecg_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    """One recording with its waveform (µV integers at sample_hz) and the detected R peaks (sample indexes)."""
    uid = _owner(conn, viewer, user, "ecg")
    return _found(store.get_ecg(conn, uid, ecg_id, _tz(conn, uid)), "心电图")


@router.delete("/ecg/{ecg_id}")
def ecg_delete(user: str, ecg_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    if not store.delete_ecg(conn, uid, ecg_id):
        raise HTTPException(404, "没有这条心电图")
    conn.commit()
    return {"deleted": ecg_id}


@router.get("/heartbeats")
def heartbeats(user: str, start: date | None = None, end: date | None = None, conn=Depends(get_conn),
               viewer=Depends(current_user)):
    """Apple HRV readings with our RMSSD / SDNN from the beat-to-beat series (default the last 7 days, at most 92)."""
    uid = _owner(conn, viewer, user, "scores")
    tz = _tz(conn, uid)
    end = end or datetime.now(tz).date()
    start = start or end - timedelta(days=6)
    if start > end or (end - start).days > 91:
        raise HTTPException(422, "start 要早于 end，最多 92 天")
    rows = store.heartbeat_readings(conn, uid, datetime.combine(start, time.min, tz),
                                    datetime.combine(end + timedelta(days=1), time.min, tz))
    return {"start": start, "end": end, "readings": [
        {**r, "start_at": r["start_at"].astimezone(tz).isoformat(timespec="seconds"),
         "end_at": r["end_at"].astimezone(tz).isoformat(timespec="seconds")} for r in rows]}


# ---------------------------------------------------------------------------- uploads from the app

class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HeartbeatIn(_In):
    uuid: UUID
    start: AwareDatetime
    end: AwareDatetime
    source: str = Field("", max_length=120)
    beats: list[float] = Field(min_length=2, max_length=I.MAX_BEATS)   # seconds since the first beat
    gaps: list[bool] | None = Field(None, max_length=I.MAX_BEATS)      # precededByGap per beat


class EcgIn(_In):
    uuid: UUID
    start: AwareDatetime
    sample_hz: float = Field(gt=0, le=2000)
    voltage_b64: str = Field(min_length=4, max_length=200_000)       # int16 little-endian µV
    classification: str = Field("notSet", max_length=60)             # HKElectrocardiogram.Classification case name
    symptoms_status: Literal["notSet", "none", "present"] | None = None
    avg_hr: float | None = Field(None, ge=0, le=400)
    device: str | None = Field(None, max_length=120)
    software_version: str | None = Field(None, max_length=40)


class WaterSampleIn(_In):
    uuid: UUID
    start: AwareDatetime
    ml: float = Field(gt=0, le=5000)


class IngestIn(_In):
    batch_id: UUID
    device: str | None = Field(None, max_length=80)
    app_version: str | None = Field(None, max_length=40)
    heartbeats: list[HeartbeatIn] = Field(default_factory=list, max_length=I.MAX_HEARTBEATS)
    ecgs: list[EcgIn] = Field(default_factory=list, max_length=I.MAX_ECGS)
    water: list[WaterSampleIn] = Field(default_factory=list, max_length=I.MAX_WATER)   # Apple Health drinks (issue #125)
    deleted: list[UUID] = Field(default_factory=list, max_length=I.MAX_DELETED)


def ingest_user(request: Request, conn=Depends(get_conn), authorization: Annotated[str | None, Header()] = None) -> dict:
    """Bearer only (no cookie): the account token (scope write) or the upload-only token (scope ingest)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少 Bearer token")
    user = auth.user_for_ingest_token(conn, authorization.removeprefix("Bearer ").strip())
    if user is None:
        raise HTTPException(401, "token 无效或已吊销")
    account_api.gate(user, request.url.path)
    return user


@ingest_router.post("/v1/ingest/healthkit")
def ingest_healthkit(body: IngestIn, request: Request, conn=Depends(get_conn), viewer=Depends(ingest_user)):
    """One batch of beat-to-beat HRV readings and ECG recordings read by the app; idempotent (HealthKit UUID, batch_id)."""
    size = int(request.headers.get("content-length") or 0)
    if size > I.MAX_BODY_BYTES:
        raise HTTPException(413, L("这一批太大，请分小一点", "This batch is too large; send smaller batches"))
    for h in body.heartbeats:
        if h.end < h.start or (h.gaps is not None and len(h.gaps) != len(h.beats)):
            raise HTTPException(422, L("心跳序列的时间或 gaps 不对", "Bad heartbeat series: times or gaps"))
    try:
        res = I.ingest(conn, viewer["id"], body.model_dump(), size or None)
    except PermissionError:
        raise HTTPException(409, "consent_required")
    except I.IngestError as e:
        raise HTTPException(422, str(e))
    return {**res, "status": I.status(conn, viewer["id"])}


@ingest_router.get("/v1/ingest/healthkit")
def ingest_status(conn=Depends(get_conn), viewer=Depends(ingest_user)):
    """What this account has uploaded from the app so far (counts and the last upload time)."""
    return I.status(conn, viewer["id"])
