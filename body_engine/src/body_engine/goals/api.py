"""HTTP endpoints for the bedtime goal (scope sleep). Reading someone else's needs a shares grant; writing is only for oneself, and the token needs write;
when the Today page logs in with a cookie, PUT must carry X-Body-Web: 1 (checked uniformly by web/session.py)."""
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ..records.api import _bad_request, _owner, _self, current_user, get_conn
from . import params as P
from . import store

router = APIRouter(prefix="/v1/users/{user}/goals", tags=["goals"])
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class SleepGoalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_bedtime: str = Field(pattern=HHMM, description="目标入睡时间 HH:MM，例 00:00")
    target_wake: str | None = Field(None, pattern=HHMM, description="目标起床时间 HH:MM；不填 = 今晚目标 + 睡眠时长")
    pace_min_per_step: int | None = Field(None, ge=P.PACE_RANGE[0], le=P.PACE_RANGE[1],
                                          description=f"每档提前几分钟，默认 {P.DEFAULT_PACE_MIN}")
    step_days: int | None = Field(None, ge=P.STEP_DAYS_RANGE[0], le=P.STEP_DAYS_RANGE[1],
                                  description=f"每档几天，默认 {P.DEFAULT_STEP_DAYS}")
    target_duration_min: int | None = Field(None, ge=240, le=720, description="睡眠时长；不填 = 评分的睡眠需求")
    status: Literal["active", "paused"] | None = None


@router.get("/sleep")
def get_sleep_goal(user: str, night: Annotated[date | None, Query(description="哪一晚（入睡前的傍晚日期），默认今晚")] = None,
                   conn=Depends(get_conn), viewer=Depends(current_user)):
    """Bedtime goal, the step-by-step plan, tonight's target and the progress over the last 7 nights. When no goal is set, target_bedtime is null (progress is still given)."""
    return store.view(conn, _owner(conn, viewer, user, "sleep"), night)


@router.put("/sleep")
def put_sleep_goal(user: str, body: SleepGoalIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Set / change the goal: each time the stepping restarts from the average bedtime of the last 7 nights (pace and duration not given fall back to the old goal or defaults)."""
    return _bad_request(store.set_goal, conn, _self(viewer, user), **body.model_dump())
