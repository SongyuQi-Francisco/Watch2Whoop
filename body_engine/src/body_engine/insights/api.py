"""Analytics endpoints matching WHOOP (all read-only, shares scope: sleep details use sleep, the others use scores)."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..records.api import _owner, current_user, get_conn
from . import views

router = APIRouter(prefix="/v1/users/{user}", tags=["insights"])


@router.get("/healthspan")
def healthspan(user: str, weeks: int = Query(26, ge=1, le=260), conn=Depends(get_conn), viewer=Depends(current_user)):
    """Body age, pace of aging, per-item contributions (computed weekly, history holds the weekly values)."""
    return views.healthspan(conn, _owner(conn, viewer, user, "scores"), weeks, viewer["id"])


@router.get("/stress")
def stress(user: str, start: date | None = None, end: date | None = None, hours: bool = True,
           conn=Depends(get_conn), viewer=Depends(current_user)):
    """Stress monitor (0–3), one row per day, hours=true adds the hourly scores. Defaults to today only."""
    return views.stress_days(conn, _owner(conn, viewer, user, "scores"), start, end, hours)


@router.get("/strain-coach")
def strain_coach(user: str, date: date | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Today's strain target: the strain range from recovery score and recent fatigue, and the progress completed."""
    return views.strain_coach(conn, _owner(conn, viewer, user, "scores"), date)


@router.get("/sleep-coach")
def sleep_coach(user: str, date: date | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Sleep coach: how long to sleep tonight and when to go to bed (combined with the bedtime goal)."""
    return views.sleep_coach(conn, _owner(conn, viewer, user, "scores"), date, viewer["id"])


@router.get("/health-monitor")
def health_monitor(user: str, date: date | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Health monitor: personal normal ranges of resting heart rate, HRV, respiration, SpO2 and wrist temperature, and whether the latest night is in range."""
    return views.health_monitor(conn, _owner(conn, viewer, user, "scores"), date)


@router.get("/sleep/details")
def sleep_details(user: str, start: date | None = None, end: date | None = None,
                  conn=Depends(get_conn), viewer=Depends(current_user)):
    """Sleep details: regularity (7 / 30-day SRI), nightly efficiency, sleep latency, number of wake-ups (default the last 14 nights)."""
    return views.sleep_details(conn, _owner(conn, viewer, user, "sleep"), start, end)


@router.get("/hr-zones")
def hr_zones(user: str, start: date | None = None, end: date | None = None,
             conn=Depends(get_conn), viewer=Depends(current_user)):
    """Heart-rate zone distribution: Z1–Z5 minutes per day and per workout (default the last 7 days)."""
    return views.hr_zones(conn, _owner(conn, viewer, user, "scores"), start, end, viewer["id"])


@router.get("/vo2max")
def vo2max(user: str, end: date | None = None, days: int = Query(365, ge=30, le=3650),
           conn=Depends(get_conn), viewer=Depends(current_user)):
    """VO₂max trend (monthly average, yearly change) and the fitness level by same-age same-sex norms."""
    return views.vo2max(conn, _owner(conn, viewer, user, "scores"), end, days)


@router.get("/reports")
def reports(user: str, period: Literal["week", "month"] = "week", end: date | None = None,
            conn=Depends(get_conn), viewer=Depends(current_user)):
    """Weekly / monthly report (structured data, the butler writes the text): this period's summary, previous period, change, journal findings.
    Needs scores; every other block only with its own scope (reports.py), the rest is listed in `hidden`."""
    try:
        return views.report(conn, _owner(conn, viewer, user, "scores"), period, end, viewer["id"])
    except ValueError as e:
        raise HTTPException(422, str(e))
