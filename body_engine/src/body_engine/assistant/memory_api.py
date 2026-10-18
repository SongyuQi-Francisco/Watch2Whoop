"""HTTP endpoints for long-term memory and quick meal logging (used by the Today page "Me" tab and the log-meal button). Authorization is the same as the health profile:
reading someone else's needs shares authorization (memory scope memory, meal templates scope nutrition); writing only for the caller, the token needs write,
and with cookie login write requests must carry X-Body-Web: 1 (web/session.py)."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import sparky_client
from ..records.api import _owner, _self, current_user, get_conn
from . import meals, memory, status

router = APIRouter(prefix="/v1/users/{user}", tags=["memory"])


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=memory.MAX_VALUE)
    label: str | None = Field(None, max_length=50, description="中文名；标准键不用填，新键必填")


@router.get("/memory")
def get_memory(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Status cards (status, ordered by group) + body profile card (the butler's underlying memory) + observation notes.
    Auto items computed by the backend in the status cards are refreshed first if older than 30 minutes."""
    owner = _owner(conn, viewer, user, "memory")
    status.refresh_if_stale(conn, owner)
    m = memory.get_memory(conn, owner)
    return {"status": status.list_status(conn, owner), "profile_card": m["profile_card"],
            "observations": [{k: o[k] for k in ("id", "text", "created_at", "source")} for o in m["observations"]]}


@router.put("/memory/profile/{key}")
def put_profile(user: str, body: ProfileIn, key: str = Path(pattern=r"^[a-z][a-z0-9_]*$"),
                conn=Depends(get_conn), viewer=Depends(current_user)):
    """The user edits one profile card item themselves (replaced whole), source recorded as user."""
    try:
        return memory.update_profile(conn, _self(viewer, user), key, body.value, body.label, source="user")
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.delete("/memory/observations/{observation_id}", status_code=204)
def delete_observation(user: str, observation_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    if memory.delete_observation(conn, _self(viewer, user), observation_id) is None:
        raise HTTPException(404, "没有这条观察笔记（或不是你的）")
    return Response(status_code=204)


# ---------------------------------------------------------------- quick meal logging
class MealOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    food: str = Field(min_length=1, description="模板里那样食物的名字")
    grams: float = Field(ge=0, le=2000, description="这次的克数；0 = 这次没吃这样")


class MealLogIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: dt.date | None = Field(None, description="默认用户当地今天")
    time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM，默认现在")
    items: list[MealOverride] | None = Field(None, description="覆盖部分食物的克数，营养按比例换算")
    allow_duplicates: bool = False


def _meal_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except meals.DuplicateMealError as e:
        raise HTTPException(409, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except sparky_client.SparkyError as e:
        raise HTTPException(502, f"SparkyFitness：{e}")


@router.get("/meal-templates")
def list_meal_templates(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Quick meal templates (with total nutrition, whether grams await confirmation, and whether it was already logged today: today_log)."""
    return meals.list_templates(conn, _owner(conn, viewer, user, "nutrition"))


@router.post("/meal-templates/{template_id}/log", status_code=201)
def log_meal_template(user: str, template_id: int, body: MealLogIn | None = None,
                      conn=Depends(get_conn), viewer=Depends(current_user)):
    """One tap logs a meal: one SparkyFitness food entry per food. Duplicate (this meal already logged on this day) -> 409."""
    b = body or MealLogIn()
    owner = _self(viewer, user)
    return _meal_call(meals.log_template, conn, owner, template_id, b.date, b.time,
                      [o.model_dump() for o in b.items] if b.items else None, b.allow_duplicates, channel="web")


@router.delete("/meal-logs/{log_id}")
def undo_meal_log(user: str, log_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Undo one quick meal logging: delete the entries this logging wrote into SparkyFitness."""
    return _meal_call(meals.undo_log, conn, _self(viewer, user), log_id)
