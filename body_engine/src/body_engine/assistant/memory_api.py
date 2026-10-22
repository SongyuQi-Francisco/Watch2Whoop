"""HTTP endpoints for long-term memory and quick meal logging (used by the Today page "Me" tab and the log-meal button). Authorization is the same as the health profile:
reading someone else's needs shares authorization (memory scope memory, meal templates scope nutrition); writing only for the caller, the token needs write,
and with cookie login write requests must carry X-Body-Web: 1 (web/session.py)."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import sparky_client
from ..records.api import _owner, _self, current_user, get_conn
from ..rollout import flags
from . import briefing, food_combine, meals, memory, sparky_write, status

router = APIRouter(prefix="/v1/users/{user}", tags=["memory"])


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=memory.MAX_VALUE)
    label: str | None = Field(None, max_length=50, description="中文名；标准键不用填，新键必填")


@router.get("/memory")
def get_memory(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Status cards (status, ordered by group) + body profile card (the butler's underlying memory) + observation notes.
    Auto items computed by the backend in the status cards are refreshed first if older than 30 minutes.
    Observations carry kind (fact / recent) and valid_until; expired recent notes are left out and only counted (#112)."""
    owner = _owner(conn, viewer, user, "memory")
    status.refresh_if_stale(conn, owner)
    m = memory.get_memory(conn, owner)
    return {"status": status.localize(status.list_status(conn, owner, viewer["id"])), "profile_card": m["profile_card"],
            "observations": [{k: o[k] for k in ("id", "text", "created_at", "source", "kind", "valid_until")}
                             for o in m["observations"]],
            "expired_recent": m["expired_recent"]}


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


# ---------------------------------------------------------------- several entries -> a template / one entry (issue #93)
class CombineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: dt.date
    entry_ids: list[str] = Field(min_length=1, max_length=20)
    action: str = Field(pattern="^(template|merge|both)$")
    name: str | None = Field(None, max_length=60)
    meal: str | None = Field(None, pattern="^(breakfast|lunch|dinner|snacks)$")


@router.post("/nutrition/combine", dependencies=[Depends(flags.require("meal_merge"))])
def combine_food_entries(user: str, body: CombineIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Selected food entries of one day -> a quick-meal template, merged into one entry, or both. Only for oneself."""
    owner = _self(viewer, user)
    out = _meal_call(food_combine.combine, conn, owner, body.date, body.entry_ids, body.action, body.name, body.meal)
    briefing.invalidate(owner)
    return out


@router.delete("/nutrition/merges/{merge_id}", dependencies=[Depends(flags.require("meal_merge"))])
def split_food_merge(user: str, merge_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Undo a merge (「拆开」): the merged entry goes, the original entries come back."""
    owner = _self(viewer, user)
    out = _meal_call(food_combine.split, conn, owner, merge_id)
    briefing.invalidate(owner)
    return out


# ---------------------------------------------------------------- deleting a food entry (Today page, 饮食 day detail)
@router.delete("/nutrition/entries/{entry_id}")
def delete_food_entry(user: str, entry_id: str = Path(pattern=r"^[A-Za-z0-9-]{1,64}$"),
                      date: dt.date = Query(description="这条记录的日期 YYYY-MM-DD"),
                      conn=Depends(get_conn), viewer=Depends(current_user)):
    """The user deletes one of their own SparkyFitness food entries (any source) with their read-write key; only for oneself, needs write."""
    owner = _self(viewer, user)
    out = _meal_call(sparky_write.delete_food_entry, conn, owner, date, entry_id)
    briefing.invalidate(owner)
    return out
