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


CARD_OUT = ("key", "label", "value", "updated_at", "source", "topic", "confirmed", "confirmed_days", "stale", "origin")
OBS_OUT = ("id", "text", "created_at", "source", "kind", "valid_until", "topic", "confirmed", "confirmed_days", "stale",
           "origin")
ARCH_OUT = ("archived_at", "archived_reason", "merged_into")


@router.get("/memory")
def get_memory(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Status cards (status, ordered by group) + body profile card (the butler's underlying memory) + observation notes.
    Auto items computed by the backend in the status cards are refreshed first if older than 30 minutes.
    Observations carry kind (fact / recent) and valid_until; expired recent notes are left out and only counted (#112).
    Every entry carries topic, confirmed (last confirmed), confirmed_days and stale; with the rollout flag memory_hygiene
    (#113) also `archived` (archived entries, newest first, restorable). Read access needs a memory share; writes are self only."""
    owner = _owner(conn, viewer, user, "memory")
    status.refresh_if_stale(conn, owner)
    hygiene = flags.enabled(conn, viewer["id"], "memory_hygiene")
    m = memory.get_memory(conn, owner, archived=hygiene)
    out = {"status": status.localize(status.list_status(conn, owner, viewer["id"])),
           "profile_card": [{k: c[k] for k in CARD_OUT} for c in m["profile_card"]],
           "observations": [{k: o[k] for k in OBS_OUT} for o in m["observations"]],
           "expired_recent": m["expired_recent"]}
    if hygiene:
        a = m["archived"]
        out["archived"] = {"profile_card": [{k: c[k] for k in CARD_OUT + ARCH_OUT} for c in a["profile_card"]],
                           "observations": [{k: o[k] for k in OBS_OUT + ARCH_OUT} for o in a["observations"]]}
    return out


@router.put("/memory/profile/{key}")
def put_profile(user: str, body: ProfileIn, key: str = Path(pattern=r"^[a-z][a-z0-9_]*$"),
                conn=Depends(get_conn), viewer=Depends(current_user)):
    """The user edits one profile card item themselves (replaced whole), source recorded as user."""
    try:
        row = memory.update_profile(conn, _self(viewer, user), key, body.value, body.label, source="user")
    except ValueError as e:
        raise HTTPException(422, str(e))
    row.pop("previous", None)
    return row


@router.delete("/memory/observations/{observation_id}", status_code=204)
def delete_observation(user: str, observation_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    if memory.delete_observation(conn, _self(viewer, user), observation_id) is None:
        raise HTTPException(404, "没有这条观察笔记（或不是你的）")
    return Response(status_code=204)


# ---------------------------------------------------------------- 「还准确」 / delete / restore (issue #113 phase 1)
HYGIENE = [Depends(flags.require("memory_hygiene"))]
KEY = Path(pattern=r"^[a-z][a-z0-9_]*$")


def _done(conn, owner: int, row, missing: str):
    if row is None:
        raise HTTPException(404, missing)
    briefing.invalidate(owner)
    return row


@router.post("/memory/observations/{observation_id}/confirm", dependencies=HYGIENE)
def confirm_observation(user: str, observation_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    """The user says the note is still right: confirmed_at = now."""
    owner = _self(viewer, user)
    return _done(conn, owner, memory.confirm_observation(conn, owner, observation_id), "没有这条观察笔记（或不是你的）")


@router.post("/memory/observations/{observation_id}/restore", dependencies=HYGIENE)
def restore_observation(user: str, observation_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    """An archived note comes back into the memory."""
    owner = _self(viewer, user)
    return _done(conn, owner, memory.restore_observation(conn, owner, observation_id), "没有这条已归档的笔记（或不是你的）")


@router.post("/memory/profile/{key}/confirm", dependencies=HYGIENE)
def confirm_profile(user: str, key: str = KEY, conn=Depends(get_conn), viewer=Depends(current_user)):
    owner = _self(viewer, user)
    return _done(conn, owner, memory.confirm_profile(conn, owner, key), "档案卡里没有这一项（或不是你的）")


@router.post("/memory/profile/{key}/restore", dependencies=HYGIENE)
def restore_profile(user: str, key: str = KEY, conn=Depends(get_conn), viewer=Depends(current_user)):
    owner = _self(viewer, user)
    return _done(conn, owner, memory.restore_profile(conn, owner, key), "档案卡里没有这一项已归档的（或不是你的）")


@router.delete("/memory/profile/{key}", status_code=204, dependencies=HYGIENE)
def delete_profile(user: str, key: str = KEY, conn=Depends(get_conn), viewer=Depends(current_user)):
    """The user deletes one profile card item (a real delete, as for notes)."""
    owner = _self(viewer, user)
    if not memory.delete_profile(conn, owner, key):
        raise HTTPException(404, "档案卡里没有这一项（或不是你的）")
    briefing.invalidate(owner)
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
    client_id: str | None = Field(None, max_length=64, description="app 的重发键：同一个 client_id 只记一次（#340 N2）")


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


def _with_today(conn, owner: int, out: dict) -> dict:
    """optimistic_writes (#247 3.4): the write's answer carries the Today page's nutrition block of that day, so the page
    replaces just that block instead of loading the whole /today again. Left out when it cannot be read right now."""
    if not flags.enabled(conn, owner, "optimistic_writes") or not out.get("date"):
        return out
    from ..web import views
    try:
        out["today"] = {"nutrition": views.nutrition_today(conn, owner, dt.date.fromisoformat(out["date"]))}
    except sparky_client.SparkyError:
        pass
    return out


@router.post("/meal-templates/{template_id}/log", status_code=201)
def log_meal_template(user: str, template_id: int, body: MealLogIn | None = None,
                      conn=Depends(get_conn), viewer=Depends(current_user)):
    """One tap logs a meal: one SparkyFitness food entry per food. Duplicate (this meal already logged on this day) -> 409."""
    b = body or MealLogIn()
    owner = _self(viewer, user)
    from ..nativesync import idem
    return idem.once(conn, owner, b.client_id, f"meal_template.log:{template_id}", lambda: _with_today(
        conn, owner, _meal_call(meals.log_template, conn, owner, template_id, b.date, b.time,
                                [o.model_dump() for o in b.items] if b.items else None, b.allow_duplicates, channel="web")),
        status=201)


@router.delete("/meal-logs/{log_id}")
def undo_meal_log(user: str, log_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Undo one quick meal logging: delete the entries this logging wrote into SparkyFitness."""
    owner = _self(viewer, user)
    return _with_today(conn, owner, _meal_call(meals.undo_log, conn, owner, log_id))


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
