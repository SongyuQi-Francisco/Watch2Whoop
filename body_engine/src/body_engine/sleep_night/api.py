"""GET /v1/users/{user}/sleep/night?date=: one night of the sleep detail page (rollout flag sleep_detail, 404 while off).
Needs the sleep share; the score, need and overnight vitals also need scores (view.py)."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..records.api import _owner, current_user, get_conn
from ..rollout import flags
from . import view

FLAG = "sleep_detail"
router = APIRouter(prefix="/v1/users/{user}", tags=["sleep"])


@router.get("/sleep/night", dependencies=[Depends(flags.require(FLAG))])
def sleep_night(user: str, day: Annotated[date | None, Query(alias="date")] = None,
                conn=Depends(get_conn), viewer=Depends(current_user)):
    """One night by wake date (default: the latest night): times, stage composition + timeline, sleep score and parts,
    7 / 30-night averages, regularity, overnight vitals, rule-based notes, the previous / next night with data, and
    `ai_prompt` (the question for the viewer's own AI; nothing here calls an AI)."""
    return view.build(conn, viewer["id"], _owner(conn, viewer, user, "sleep"), day)
