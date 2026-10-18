"""ECG and heartbeat-series endpoints.

ECG uses its own share scope 'ecg' (ordinary shares do not include it, docs/raw-data-plan.md §7.1); the owner always sees their own.
Heartbeat readings (per-reading RMSSD / SDNN summary, no beat arrays) use scope 'scores'.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from ..records.api import _found, _owner, _self, current_user, get_conn
from . import store

router = APIRouter(prefix="/v1/users/{user}", tags=["rawdata"])


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
