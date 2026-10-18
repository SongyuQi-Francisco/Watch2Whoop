"""HTTP endpoints for training history (scope training; score fields additionally need scores, daily activity needs metrics; ungranted ones are null and listed in hidden)."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from ..records.api import _owner, current_user, get_conn
from . import history

router = APIRouter(prefix="/v1/users/{user}/training", tags=["training"])


@router.get("/calendar")
def training_calendar(user: str, month: str | None = None, start: date | None = None, end: date | None = None,
                      conn=Depends(get_conn), viewer=Depends(current_user)):
    """Per-day training count, duration, intensity, types, active energy, exercise minutes, strain and muscle load for a month (month=YYYY-MM, default this month; or start / end, at most 92 days);
    days without training are included too. summary aggregates this period."""
    owner_id = _owner(conn, viewer, user, "training")
    try:
        return history.calendar(conn, viewer["id"], owner_id, month, start, end)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/day/{day}")
def training_day(user: str, day: date, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Each workout of one day (watch + SparkyFitness merged): time, duration, energy, heart-rate zones, TRIMP, intensity, exercises and sets; that day's daily metrics."""
    return history.day(conn, viewer["id"], _owner(conn, viewer, user, "training"), day)
