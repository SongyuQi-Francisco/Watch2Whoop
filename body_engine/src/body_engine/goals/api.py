"""HTTP endpoints for the bedtime goal (scope sleep) and nutrition goals (scope nutrition). Reading someone else's needs a shares grant; writing is only for oneself, and the token needs write;
when the Today page logs in with a cookie, PUT must carry X-Body-Web: 1 (checked uniformly by web/session.py)."""
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..records.api import _bad_request, _owner, _self, current_user, get_conn
from ..rules.context import goal_data
from . import params as P
from . import nutrition as store_nutrition, store

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


def _with_settings(conn, owner: int, view: dict) -> dict:
    """The view plus `settings`: the stored goal row as plain data (rules/context.GOAL_FIELDS, null without a goal), so
    the app can cache it and compute the same view on the phone (JS OBS.sleepGoal, docs/ios-native-2.0.md M4)."""
    return {**view, "settings": goal_data(store.get_goal(conn, owner))}


@router.get("/sleep")
def get_sleep_goal(user: str, night: Annotated[date | None, Query(description="哪一晚（入睡前的傍晚日期），默认今晚")] = None,
                   conn=Depends(get_conn), viewer=Depends(current_user)):
    """Bedtime goal, the step-by-step plan, tonight's target and the progress over the last 7 nights. When no goal is set, target_bedtime is null (progress is still given)."""
    owner = _owner(conn, viewer, user, "sleep")
    return _with_settings(conn, owner, store.view(conn, owner, night))


@router.put("/sleep")
def put_sleep_goal(user: str, body: SleepGoalIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Set / change the goal: each time the stepping restarts from the average bedtime of the last 7 nights (pace and duration not given fall back to the old goal or defaults)."""
    owner = _self(viewer, user)
    return _with_settings(conn, owner, _bad_request(store.set_goal, conn, owner, **body.model_dump()))


@router.delete("/sleep")
def delete_sleep_goal(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Clear the goal (only for oneself); the answer is the view without a goal. Clearing twice is fine."""
    owner = _self(viewer, user)
    store.clear_goal(conn, owner)
    return _with_settings(conn, owner, store.view(conn, owner))


# ---------------------------------------------------------------- nutrition goals (goals/nutrition.py, scope nutrition)
class NutritionSetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calories: float | None = Field(None, description="kcal")
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    sat_fat_max_g: float | None = Field(None, description="饱和脂肪上限 g")
    added_sugar_max_g: float | None = Field(None, description="添加糖上限 g；不设 = 默认 25")
    fiber_min_g: float | None = Field(None, description="纤维下限 g")


class NutritionGoalsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    training: NutritionSetIn | None = Field(None, description="训练日；null 或不填的字段 = 沿用当前版本")
    rest: NutritionSetIn | None = Field(None, description="休息日")
    training_weekdays: list[Annotated[int, Field(ge=1, le=7)]] | None = Field(
        None, description="用训练日目标的星期 1=周一…7=周日；null = 沿用")
    effective_from: date | None = Field(None, description="生效日期，默认今天")
    name: str | None = Field(None, max_length=40, description="版本名")
    reason: str = Field(min_length=1, max_length=300, description="为什么改")


@router.get("/nutrition")
def get_nutrition_goals(user: str, day: Annotated[date | None, Query(alias="date", description="哪天，默认今天")] = None,
                        conn=Depends(get_conn), viewer=Depends(current_user)):
    """Nutrition goals: `goal` = the goal in effect on `date` (training / rest day resolved), `version` = the version in
    effect with both sets, `upcoming` = versions starting later, `history` = every version newest first (who / why;
    superseded = replaced by a later row with the same effective date, never in effect)."""
    return store_nutrition.view(conn, _owner(conn, viewer, user, "nutrition"), day)


@router.put("/nutrition")
def put_nutrition_goals(user: str, body: NutritionGoalsIn, request: Request, conn=Depends(get_conn),
                        viewer=Depends(current_user)):
    """Add a goal version for oneself (earlier versions are kept). Fields left out carry over from the version in effect."""
    owner = _self(viewer, user)
    d = body.model_dump()
    sets = {k: ({f: v for f, v in d[k].items() if v is not None} or None) if d[k] else None for k in ("training", "rest")}
    return _bad_request(store_nutrition.set_goals, conn, owner, training=sets["training"], rest=sets["rest"],
                        training_weekdays=d["training_weekdays"], effective_from=d["effective_from"], name=d["name"],
                        reason=d["reason"], set_by="api" if request.headers.get("authorization") else "web",
                        actor_user_id=viewer["id"])
