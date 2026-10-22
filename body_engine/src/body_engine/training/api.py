"""HTTP endpoints for training history and Training V2 (exercise catalog, splits, weekly plan, exercise swap).
Authorization:
- Read endpoints require shares scope `training` (per-user restrictions additionally check `exercises`, readiness checks `scores`).
- Training day v2 (`/session`, rollout flag training_day_v2, FB-5): reading needs `training`; the draft and submit are self-only.
- Muscle load map (`/muscle-load`, `/muscle-load/{muscle}`, rollout flag muscle_load): needs `training` (a friend with the share can look); `/goals` is self-only.
- Add to plan (`/plan/add-options`, `/plan/add-exercise`, rollout flag exercise_add_to_plan, issue #154) is self-only.
- Remove a planned exercise (`/plan/remove-exercise`, `/plan/restore-exercise`, issue #172; no flag) is self-only.
- Write endpoints (`PUT /plan`, `POST /plan/swap`) are self-only and require `write` token scope (plus `X-Body-Web: 1` for cookie sessions, enforced globally).
"""
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import auth, sparky_client
from ..records.api import _owner, _self, current_user, get_conn
from ..i18n import L
from ..rollout import flags
from . import catalog, goals, history, loads, muscle_load, plan_add, plan_remove, plans, session_log

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
    training_days: list[int] | None = None   # issue #65: the user's weekdays, 1 = Monday
    start_date: date | None = None           # any day from today
    addons: list[str] | None = None          # optional mobility warm-ups (issue #66 FB-3)


class SavePlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    split_type: str = "4_day_upper_lower"
    name: str | None = None
    start_date: date | None = None
    notes: str | None = None
    auto_swap_banned: bool = False
    sync_sparky: bool = True
    custom_days: list[PlanDayIn] | None = None
    training_days: list[int] | None = None
    addons: list[str] | None = None


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
    """Full exercise detail: bilingual names, muscles, equipment, instructions, cues, demo image URLs, joint stress flags, user status,
    safe alternatives, the recommended working weight and the last 3 logged sessions."""
    owner_id = _owner(conn, viewer, user, "training")
    with_meta = auth.can_view(conn, viewer["id"], owner_id, "exercises")
    try:
        ex = catalog.get_exercise(conn, owner_id if with_meta else None, slug)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {**ex, **loads.for_exercise(conn, viewer["id"], owner_id, ex, plans._user_today(conn, owner_id))}


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


@router.get("/sparky")
def training_log_link(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Whether the training log (SparkyFitness) is connected / not_linked / unreadable (asks the server), issue #66 FB-4."""
    owner_id = _owner(conn, viewer, user, "training")
    st = sparky_client.link_state(conn, owner_id, probe=True)
    if owner_id != viewer["id"]:
        st.pop("reason", None)
    return {**st, "can_connect": owner_id == viewer["id"] and st["state"] == "not_linked"}


@router.post("/sparky/connect")
def connect_training_log(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Self-service connect: create this user's SparkyFitness account and keys (the provisioning step of self-signup)."""
    from ..account import provision
    uid = _self(viewer, user)
    if sparky_client.link_state(conn, uid)["state"] == "connected":
        return {**sparky_client.link_state(conn, uid, probe=True), "can_connect": False}
    res = provision.connect_sparky(conn, uid)
    st = sparky_client.link_state(conn, uid, probe=True)
    if not res["ok"]:
        admin = res.get("error") == "admin_linked"
        st["error"], st["error_en"] = (
            ("这个账号的训练记录由管理员关联，请联系管理员。", "The admin links this account's training log; please contact the admin.")
            if admin else
            ("现在连不上：服务器的训练记录服务没开放注册或暂时不可用，请联系管理员。",
             "Cannot connect now: the server's training log service does not allow sign-up or is unavailable; please contact the admin."))
    return {**st, "can_connect": st["state"] == "not_linked"}


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
            training_days=body.training_days,
            start_date=body.start_date,
            addons=body.addons,
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
            training_days=body.training_days,
            addons=body.addons,
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


# ---------------------------------------------------------------- add to plan (issue #154, rollout flag exercise_add_to_plan)
ADD = [Depends(flags.require("exercise_add_to_plan"))]


class AddExerciseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exercise: str = Field(min_length=1)
    scope: Literal["template", "day"]
    day_of_week: int | None = Field(default=None, ge=1, le=7)   # template: which session (with session_name)
    session_name: str | None = None
    day: date | None = None                                     # day: which date
    sets: int | None = Field(default=None, ge=1, le=20)         # left out = the recommendation (params.ADD_*)
    rep_low: int | None = Field(default=None, ge=1, le=100)
    rep_high: int | None = Field(default=None, ge=1, le=100)
    duration_sec: int | None = Field(default=None, ge=1, le=7200)
    rest_sec: int | None = Field(default=None, ge=0, le=600)
    sync_sparky: bool = True


@router.get("/plan/add-options", dependencies=ADD)
def add_to_plan_options(user: str, exercise: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """What the exercise detail's "add to plan" sheet offers: the plan's sessions, the next days, default sets / reps, banned or not."""
    uid = _self(viewer, user)
    try:
        return plan_add.options(conn, uid, exercise)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/plan/add-exercise", dependencies=ADD)
def add_plan_exercise(user: str, body: AddExerciseIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Add an exercise to one session of the weekly plan (scope template, every week) or to one date only (scope day).
    A banned exercise (exercise_meta) is refused with 409 and the alternatives."""
    uid = _self(viewer, user)
    try:
        return plan_add.add(conn, uid, body.scope, body.exercise, day_of_week=body.day_of_week, session_name=body.session_name,
                            day=body.day, sets=body.sets, rep_low=body.rep_low, rep_high=body.rep_high,
                            duration_sec=body.duration_sec, rest_sec=body.rest_sec, sync_sparky=body.sync_sparky)
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


# ---------------------------------------------------------------- remove a planned exercise (issue #172)
class RemoveExerciseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exercise: str = Field(min_length=1)
    scope: Literal["day", "template"] = "day"
    day: date | None = None                                     # day: which date (today or later)
    day_of_week: int | None = Field(default=None, ge=1, le=7)   # template: which session (with session_name)
    session_name: str | None = None
    confirm: bool = False                                       # template: the page asked "every week, sure?"
    sync_sparky: bool = True


class RestoreExerciseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exercise: str = Field(min_length=1)
    day: date


@router.post("/plan/remove-exercise")
def remove_plan_exercise(user: str, body: RemoveExerciseIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Remove a planned exercise for one date (scope day, undo with /plan/restore-exercise) or from the session template
    (scope template, every week; needs confirm=true and never empties a session)."""
    uid = _self(viewer, user)
    try:
        return plan_remove.remove(conn, uid, body.scope, body.exercise, day=body.day, day_of_week=body.day_of_week,
                                  session_name=body.session_name, confirm=body.confirm, sync_sparky=body.sync_sparky)
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/plan/restore-exercise")
def restore_plan_exercise(user: str, body: RestoreExerciseIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Undo a day-only removal."""
    uid = _self(viewer, user)
    return plan_remove.restore_for_day(conn, uid, body.exercise, body.day)


# ---------------------------------------------------------------- training day v2 (FB-5): planned exercise cards + set-by-set log
class SessionSetIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reps: int | None = Field(default=None, ge=0, le=100)
    weight_kg: float | None = Field(default=None, ge=0, le=400)
    duration_s: float | None = Field(default=None, gt=0, le=6 * 3600)
    rir: float | None = Field(default=None, ge=0, le=10)
    done: bool = False


class SessionExerciseIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    slug: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=120)
    sets: list[SessionSetIn] = Field(default_factory=list, max_length=session_log.MAX_SETS)


class SessionDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exercises: list[SessionExerciseIn] = Field(default_factory=list, max_length=session_log.MAX_EXERCISES)
    notes: str | None = Field(default=None, max_length=500)


class SessionSubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_duplicate: bool = False


V2 = [Depends(flags.require("training_day_v2"))]


@router.get("/session", dependencies=V2)
def training_session(user: str, day: Annotated[date | None, Query(alias="date")] = None,
                     conn=Depends(get_conn), viewer=Depends(current_user)):
    """The day's planned exercises as cards (prescription, recommended weight, restriction, thumbnail, last session,
    pre-fill target) and the page's draft / submitted set log."""
    owner_id = _owner(conn, viewer, user, "training")
    return session_log.view(conn, viewer["id"], owner_id, day)


@router.put("/session/{day}", dependencies=V2)
def save_training_session(user: str, day: date, body: SessionDraftIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Save the sets being filled in (draft); each set has its own reps / weight and a done tick."""
    uid = _self(viewer, user)
    try:
        return session_log.save_draft(conn, uid, day, [e.model_dump() for e in body.exercises], body.notes)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/session/{day}/submit", dependencies=V2)
def submit_training_session(user: str, day: date, body: SessionSubmitIn | None = None,
                            conn=Depends(get_conn), viewer=Depends(current_user)):
    """Write the sets marked done to the training log (SparkyFitness) as one workout; a resubmit replaces it."""
    uid = _self(viewer, user)
    try:
        return session_log.submit(conn, uid, day, allow_duplicate=bool(body and body.allow_duplicate))
    except PermissionError as e:
        raise HTTPException(409, str(e))
    except LookupError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except sparky_client.SparkyError as e:
        raise HTTPException(502, str(e))


# ---------------------------------------------------------------- muscle load map (rollout flag muscle_load)
MUSCLE = [Depends(flags.require("muscle_load"))]


class TrainingGoalsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus_muscles: list[str] = Field(default_factory=list, max_length=goals.MAX_FOCUS)
    physique_goal: str | None = Field(default=None, max_length=goals.MAX_GOAL_CHARS)


@router.get("/muscle-load", dependencies=MUSCLE)
def muscle_load_map(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Effective sets and volume per muscle for today / this week / the last 7 days, the weekly range and colour status, last trained.
    The owner also gets their own training goals (they are not part of the training share)."""
    owner_id = _owner(conn, viewer, user, "training")
    out = muscle_load.compute(conn, owner_id)
    if owner_id == viewer["id"]:
        out["goals"] = goals.get(conn, owner_id)
    return out


@router.get("/muscle-load/{muscle}", dependencies=MUSCLE)
def muscle_load_detail(user: str, muscle: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """One muscle: its numbers, the last session and recommended exercises (banned ones left out)."""
    owner_id = _owner(conn, viewer, user, "training")
    try:
        return muscle_load.detail(conn, owner_id, muscle)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/goals", dependencies=MUSCLE)
def training_goals(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    owner_id = _owner(conn, viewer, user, "training")
    if owner_id != viewer["id"]:
        raise HTTPException(403, L("训练目标只能看自己的", "Training goals are private"))
    return goals.get(conn, owner_id)


@router.put("/goals", dependencies=MUSCLE)
def save_training_goals(user: str, body: TrainingGoalsIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    try:
        return goals.save(conn, uid, body.focus_muscles, body.physique_goal)
    except ValueError as e:
        raise HTTPException(422, str(e))
