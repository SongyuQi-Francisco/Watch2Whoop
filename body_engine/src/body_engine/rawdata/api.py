"""ECG and heartbeat-series endpoints.

ECG uses its own share scope 'ecg' (ordinary shares do not include it, docs/raw-data-plan.md §7.1); the owner always sees their own.
Heartbeat readings (per-reading RMSSD / SDNN summary, no beat arrays) use scope 'scores'.
POST /v1/ingest/healthkit: uploads from the Watch2Whoop app (ingest.py), with the app's account token or its upload-only token.
The body may be gzip-compressed (Content-Encoding: gzip; ≤ 2 MB on the wire, ≤ engine.MAX_INFLATED_BYTES decompressed).
The sync engine's columnar fields (engine.py, flag apple_sync_engine): times are UTC seconds since 1970, every list of a
group has one entry per sample, `src` is an index into the batch's sources[].
"""
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from .. import auth
from ..account import api as account_api
from ..i18n import L
from ..records.api import _found, _owner, _self, current_user, get_conn
from . import engine as EN
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


class QuantitySampleIn(_In):
    uuid: UUID
    start: AwareDatetime
    end: AwareDatetime
    value: float = Field(allow_inf_nan=False)
    source: str = Field("", max_length=120)


class QuantityGroupIn(_In):
    """Samples of one HealthKit quantity type, in the unit of the registry (rawdata/hk_types.py; checked)."""
    type: str = Field(max_length=100)
    unit: str = Field(max_length=40)
    items: list[QuantitySampleIn] = Field(default_factory=list, max_length=I.MAX_QUANTITIES)


# ---- the sync engine (engine.py, flag apple_sync_engine)
Epoch = Annotated[float, Field(ge=EN.EPOCH_MIN, le=EN.EPOCH_MAX)]
Short = Annotated[str, Field(max_length=120)]
Code = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,29}$")]


class SourceIn(_In):
    name: Short = ""
    bundle: str = Field("", max_length=200)
    product: str = Field("", max_length=60)                 # HKSourceRevision productType
    device_type: Literal["watch", "phone", "other", ""] = ""
    device_name: Short = ""
    device_model: Short = ""
    manufacturer: Short = ""
    os_version: str = Field("", max_length=40)


class SampleGroupIn(_In):
    """Samples of one type, one entry per sample in every list. samples[]: quantities in the registry's unit;
    categories[]: value = HealthKit's integer (unit '')."""
    type: str = Field(max_length=100)
    unit: str = Field("", max_length=40)
    uuid: list[UUID] = Field(max_length=EN.MAX_GROUP_ITEMS)
    start: list[Epoch] = Field(max_length=EN.MAX_GROUP_ITEMS)
    end: list[Epoch] = Field(max_length=EN.MAX_GROUP_ITEMS)
    value: list[Annotated[float, Field(allow_inf_nan=False)]] = Field(max_length=EN.MAX_GROUP_ITEMS)
    src: list[int | None] | None = Field(None, max_length=EN.MAX_GROUP_ITEMS)
    tz: list[Annotated[str, Field(max_length=64)] | None] | None = Field(None, max_length=EN.MAX_GROUP_ITEMS)

    @model_validator(mode="after")
    def _same_length(self):
        n = len(self.uuid)
        if any(len(x) != n for x in (self.start, self.end, self.value, self.src or [None] * n, self.tz or [None] * n)):
            raise ValueError("every list of a group needs one entry per sample")
        return self


class WorkoutStatsIn(_In):
    active_kcal: float | None = Field(None, ge=0, le=20000)
    basal_kcal: float | None = Field(None, ge=0, le=20000)
    distance_m: float | None = Field(None, ge=0, le=1_000_000)
    steps: float | None = Field(None, ge=0, le=500_000)
    strokes: float | None = Field(None, ge=0, le=100_000)
    min_hr: float | None = Field(None, ge=0, le=300)
    avg_hr: float | None = Field(None, ge=0, le=300)
    max_hr: float | None = Field(None, ge=0, le=300)
    avg_power_w: float | None = Field(None, ge=0, le=3000)
    max_power_w: float | None = Field(None, ge=0, le=5000)
    avg_speed_mps: float | None = Field(None, ge=0, le=100)
    avg_cadence: float | None = Field(None, ge=0, le=400)
    elevation_m: float | None = Field(None, ge=0, le=10000)
    effort: float | None = Field(None, ge=0, le=10)


class WorkoutEventIn(_In):
    type: Code                                              # pause / resume / lap / segment / marker ...
    start: Epoch
    end: Epoch


class WorkoutIn(_In):
    uuid: UUID
    activity: int = Field(ge=0, le=100_000)                 # HKWorkoutActivityType raw value
    activity_name: str = Field(max_length=60)
    start: Epoch
    end: Epoch
    duration_s: float = Field(ge=0, le=7 * 86400)          # without pauses
    stats: WorkoutStatsIn = Field(default_factory=WorkoutStatsIn)
    events: list[WorkoutEventIn] = Field(default_factory=list, max_length=EN.MAX_WORKOUT_EVENTS)
    meta: dict[Annotated[str, Field(max_length=40)], Annotated[str, Field(max_length=200)] | float | bool | None] = \
        Field(default_factory=dict, max_length=20)          # indoor, tz, avg_met, swim location ...
    src: int | None = None


class StatsGroupIn(_In):
    """15-minute statistics buckets of a cumulative type from `start` (UTC seconds, aligned to 900), in the registry's unit."""
    type: str = Field(max_length=100)
    unit: str = Field(max_length=40)
    bucket_s: int = EN.BUCKET_S
    start: int = Field(ge=EN.EPOCH_MIN, le=EN.EPOCH_MAX)
    values: list[Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(max_length=EN.MAX_BUCKETS)


class RangeIn(_In):
    id: UUID
    start: Epoch
    end: Epoch
    types: list[str] = Field(min_length=1, max_length=EN.MAX_RANGE_TYPES)


class RunIn(_In):
    """One sync round of the app: counts only."""
    id: UUID
    trigger: Code                                           # foreground / launch / observer / refresh / processing / range
    result: Code                                            # ok / partial / error / expired / locked / offline / nothing
    error: str | None = Field(None, max_length=80, pattern=r"^[A-Za-z0-9_.:\- ]*$")
    started: Epoch
    duration_ms: int = Field(ge=0, le=86_400_000)
    batches: int = Field(0, ge=0, le=1_000_000)
    bytes: int = Field(0, ge=0, le=10**12)
    samples: int = Field(0, ge=0, le=10**9)


class IngestIn(_In):
    batch_id: UUID
    device: str | None = Field(None, max_length=80)
    app_version: str | None = Field(None, max_length=40)
    heartbeats: list[HeartbeatIn] = Field(default_factory=list, max_length=I.MAX_HEARTBEATS)
    ecgs: list[EcgIn] = Field(default_factory=list, max_length=I.MAX_ECGS)
    water: list[WaterSampleIn] = Field(default_factory=list, max_length=I.MAX_WATER)   # Apple Health drinks (issue #125)
    quantities: list[QuantityGroupIn] = Field(default_factory=list, max_length=I.MAX_QUANTITY_GROUPS)   # rawdata/hk_types.py
    deleted: list[UUID] = Field(default_factory=list, max_length=I.MAX_DELETED)
    # the sync engine (engine.py): only while GET says engine.enabled
    phase: Literal["recent", "backfill", "incremental", "range"] | None = None
    tz: str | None = Field(None, max_length=64)
    sources: list[SourceIn] = Field(default_factory=list, max_length=EN.MAX_SOURCES)
    samples: list[SampleGroupIn] = Field(default_factory=list, max_length=EN.MAX_GROUPS)
    categories: list[SampleGroupIn] = Field(default_factory=list, max_length=EN.MAX_GROUPS)
    workouts: list[WorkoutIn] = Field(default_factory=list, max_length=EN.MAX_WORKOUTS)
    stats: list[StatsGroupIn] = Field(default_factory=list, max_length=EN.MAX_GROUPS)
    range: RangeIn | None = None
    range_done: UUID | None = None
    runs: list[RunIn] = Field(default_factory=list, max_length=EN.MAX_RUNS)


def ingest_user(request: Request, conn=Depends(get_conn), authorization: Annotated[str | None, Header()] = None) -> dict:
    """Bearer only (no cookie): the account token (scope write) or the upload-only token (scope ingest)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少 Bearer token")
    user = auth.user_for_ingest_token(conn, authorization.removeprefix("Bearer ").strip())
    if user is None:
        raise HTTPException(401, "token 无效或已吊销")
    account_api.gate(user, request.url.path)
    return user


def _too_large():
    return HTTPException(413, L("这一批太大，请分小一点", "This batch is too large; send smaller batches"))


async def ingest_body(request: Request) -> tuple[IngestIn, int]:
    """The batch and its size on the wire; the body may be gzip (Content-Encoding)."""
    if int(request.headers.get("content-length") or 0) > I.MAX_BODY_BYTES:
        raise _too_large()
    raw = await request.body()
    size = len(raw)
    if size > I.MAX_BODY_BYTES:
        raise _too_large()
    encoding = (request.headers.get("content-encoding") or "identity").strip().lower()
    if encoding == "gzip":
        try:
            raw = EN.gunzip(raw)
        except EN.TooLarge:
            raise _too_large()
        except EN.BadBatch as e:
            raise HTTPException(400, str(e))
    elif encoding != "identity":
        raise HTTPException(415, "Content-Encoding must be gzip or identity")
    try:
        return IngestIn.model_validate_json(raw), size
    except ValidationError as e:   # no input values in the answer (health data)
        raise HTTPException(422, e.errors(include_url=False, include_context=False, include_input=False))


@ingest_router.post("/v1/ingest/healthkit")
def ingest_healthkit(conn=Depends(get_conn), viewer=Depends(ingest_user), payload=Depends(ingest_body)):
    """One batch read by the app (beat-to-beat HRV, ECGs, water, quantities; with the flag apple_sync_engine the sync
    engine's samples, workouts, statistics, ranges and runs); idempotent (HealthKit UUID, batch_id). JSON, optionally
    gzip. 409 consent_required / engine_disabled / type_paused and 429 (Retry-After) store nothing."""
    body, size = payload
    for h in body.heartbeats:
        if h.end < h.start or (h.gaps is not None and len(h.gaps) != len(h.beats)):
            raise HTTPException(422, L("心跳序列的时间或 gaps 不对", "Bad heartbeat series: times or gaps"))
    try:
        res = I.ingest(conn, viewer["id"], body.model_dump(), size or None)
    except PermissionError:
        raise HTTPException(409, "consent_required")
    except EN.Refused as e:
        conn.rollback()
        raise HTTPException(409, str(e))
    except EN.RateLimited as e:
        conn.rollback()
        return JSONResponse({"detail": "rate_limited", "retry_after": e.retry_after}, status_code=429,
                            headers={"Retry-After": str(e.retry_after)})
    except I.IngestError as e:
        conn.rollback()
        raise HTTPException(422, str(e))
    return {**res, "status": I.status(conn, viewer["id"])}


@ingest_router.get("/v1/ingest/healthkit")
def ingest_status(conn=Depends(get_conn), viewer=Depends(ingest_user)):
    """What this account has uploaded from the app so far (counts and the last upload time)."""
    return I.status(conn, viewer["id"])
