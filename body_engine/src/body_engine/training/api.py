"""HTTP endpoints for training history and Training V2 (exercise catalog, splits, weekly plan, exercise swap).
Authorization:
- Read endpoints require shares scope `training` (per-user restrictions additionally check `exercises`, readiness checks `scores`).
- Write endpoints (`PUT /plan`, `POST /plan/swap`) are self-only and require `write` token scope (plus `X-Body-Web: 1` for cookie sessions, enforced globally).
"""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import auth
from ..records.api import _owner, _self, current_user, get_conn
from . import catalog, history, plans

router = APIRouter(prefix="/v1/users/{user}/training", tags=["training"])


class PlanExerciseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exercise: str = Field(min_length=1)
    sets: int = Field(default=3, ge=1, le=20)
    rep_low: int | None = Field(default=None, ge=1, le=100)
    rep_high: int | None = Field(default=None, ge=1, le=100)
    rest_sec: int | None = Field(default=90, ge=0, le=600)
    duration_sec: int | None = Field(default=None, ge=1, le=7200)
    weight_kg: float | None = Field(default=None, ge=0, le=500)
    note: str | None = None


class PlanDayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day_of_week: int = Field(ge=1, le=7)
    name: str = Field(min_length=1, max_length=80)
    exercises: list[PlanExerciseIn] = Field(default_factory=list)


class ProposePlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    split_type: str = "4_day_upper_lower"
    name: str | None = None
    notes: str | None = None
    auto_swap_banned: bool = False
    custom_days: list[PlanDayIn] | None = None


class SavePlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    split_type: str = "4_day_upper_lower"
    name: str | None = None
    start_date: date | None = None
    notes: str | None = None
    auto_swap_banned: bool = False
    sync_sparky: bool = True
    custom_days: list[PlanDayIn] | None = None


class SwapExerciseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_exercise: str = Field(min_length=1)
    to_exercise: str | None = None
    day_of_week: int | None = Field(default=None, ge=1, le=7)
    session_name: str | None = None
    sync_sparky: bool = True


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


@router.get("/exercises")
def list_catalog_exercises(
    user: str,
    q: str | None = None,
    muscle: str | None = None,
    equipment: str | None = None,
    pattern: str | None = None,
    category: str | None = None,
    include_banned: bool = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
    conn=Depends(get_conn),
    viewer=Depends(current_user),
):
    """Search or browse the bilingual exercise catalog, merged with the user's exercise_meta restrictions when authorized."""
    owner_id = _owner(conn, viewer, user, "training")
    with_meta = auth.can_view(conn, viewer["id"], owner_id, "exercises")
    items = catalog.search(
        conn,
        user_id=owner_id if with_meta else None,
        query=q,
        muscle=muscle,
        equipment=equipment,
        pattern=pattern,
        category=category,
        include_banned=include_banned,
        limit=limit,
    )
    return {"exercises": items, "total": len(items)}


@router.get("/exercises/{slug}")
def get_catalog_exercise(user: str, slug: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Full exercise detail: bilingual names, muscles, equipment, instructions, cues, joint stress flags, user status, and safe alternatives."""
    owner_id = _owner(conn, viewer, user, "training")
    with_meta = auth.can_view(conn, viewer["id"], owner_id, "exercises")
    try:
        return catalog.get_exercise(conn, owner_id if with_meta else None, slug)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/splits")
def get_training_splits(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """List built-in 3 / 4 / 5-day training split templates."""
    _owner(conn, viewer, user, "training")
    return {"splits": plans.list_splits()}


@router.get("/plan")
def get_active_plan(user: str, day: Annotated[date | None, Query(alias="date")] = None,
                    conn=Depends(get_conn), viewer=Depends(current_user)):
    """Return the active weekly training plan with per-muscle-group volume, progression suggestions, and readiness advice."""
    owner_id = _owner(conn, viewer, user, "training")
    return plans.get_week_plan(conn, viewer["id"], owner_id, day=day, detail=True)


@router.post("/plan/propose")
def propose_training_plan(user: str, body: ProposePlanIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Preview a 3 / 4 / 5-day or custom weekly training plan without saving."""
    owner_id = _owner(conn, viewer, user, "training")
    custom = [d.model_dump() for d in body.custom_days] if body.custom_days is not None else None
    try:
        return plans.propose_plan(
            conn,
            user_id=owner_id,
            split_type=body.split_type,
            custom_days=custom,
            name=body.name,
            notes=body.notes,
            auto_swap_banned=body.auto_swap_banned,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.put("/plan")
def save_training_plan(user: str, body: SavePlanIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Save and activate a weekly training plan for the current user."""
    uid = _self(viewer, user)
    custom = [d.model_dump() for d in body.custom_days] if body.custom_days is not None else None
    try:
        return plans.save_plan(
            conn,
            user_id=uid,
            split_type=body.split_type,
            days=custom,
            name=body.name,
            start_date=body.start_date,
            notes=body.notes,
            auto_swap_banned=body.auto_swap_banned,
            sync_sparky=body.sync_sparky,
        )
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/plan/swap")
def swap_plan_exercise(user: str, body: SwapExerciseIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Swap an exercise in the user's active weekly plan (or preview alternatives when `to_exercise` is omitted)."""
    uid = _self(viewer, user)
    try:
        return plans.swap_exercise(
            conn,
            user_id=uid,
            from_exercise=body.from_exercise,
            to_exercise=body.to_exercise,
            day_of_week=body.day_of_week,
            session_name=body.session_name,
            sync_sparky=body.sync_sparky,
        )
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
