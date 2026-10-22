from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from ..records.api import _owner, current_user, get_conn
from . import views

router = APIRouter(prefix="/v1/users/{user}", tags=["cardio"])


@router.get("/cardio")
def cardio(user: str, start: date | None = None, end: date | None = None, conn=Depends(get_conn),
           viewer=Depends(current_user)):
    """Heart rate recovery 60 s after each workout (HRR60, 30-day mean vs the previous 30 days) and the recovery index per night
    (when the lowest 5-minute heart rate came, minutes after falling asleep and % of the night). Default the last 90 days."""
    uid = _owner(conn, viewer, user, "scores")
    if start and end and (start > end or (end - start).days > 730):
        raise HTTPException(422, "start 要早于 end，最多 2 年")
    return views.cardio(conn, uid, start, end, viewer["id"])
