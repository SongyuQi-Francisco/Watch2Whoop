"""GET /v1/users/{user}/sleep/night?date=: one night of the sleep detail page (rollout flag sleep_detail, 404 while off).
Needs the sleep share; the score, need and overnight vitals also need scores (view.py)."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..records.api import _owner, current_user, get_conn
from ..rollout import flags
from . import params as P, view

FLAG = "sleep_detail"
router = APIRouter(prefix="/v1/users/{user}", tags=["sleep"])


@router.get("/sleep/night", dependencies=[Depends(flags.require(FLAG))])
def sleep_night(user: str, day: Annotated[date | None, Query(alias="date")] = None,
                series_nights: Annotated[int, Query(ge=0, le=P.SERIES_MAX_NIGHTS)] = P.SERIES_NIGHTS,
                conn=Depends(get_conn), viewer=Depends(current_user)):
    """One night by wake date (default: the latest night): times, stage composition + timeline, sleep score and parts,
    7 / 30-night averages, regularity, overnight vitals, rule-based notes, the previous / next night with data, and
    `ai_prompt` (the question for the viewer's own AI; nothing here calls an AI). `series`: deep sleep and 7-night
    regularity of the `series_nights` nights up to this night with the previous period's mean (0 = left out)."""
    owner = _owner(conn, viewer, user, "sleep")
    out = view.build(conn, viewer["id"], owner, day)
    if series_nights:
        out["series"] = view.series(conn, owner, out["date"], series_nights)
    return out
