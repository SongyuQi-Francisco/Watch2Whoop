"""HTTP endpoints for recipe management (used by the Today page "Recipes", scope nutrition). Authorization is the same as quick meal logging: reading someone else's needs nutrition in shares;
writing only for the caller, the token needs write, and with cookie login write requests must carry X-Body-Web: 1. Undoing a log uses DELETE /meal-logs/{log_id}."""
import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..records.api import _owner, _self, current_user, get_conn
from . import recipes
from .memory_api import _meal_call

router = APIRouter(prefix="/v1/users/{user}/recipes", tags=["recipes"])
Slot = Literal["breakfast", "lunch", "dinner", "snack"]


class RecipeLogIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: dt.date | None = Field(None, description="默认用户当地今天")
    meal: Slot | None = Field(None, description="默认按现在的时间，不在这道菜的 slots 里就用它的第一个")
    servings: float = Field(1, gt=0, le=10)
    time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM，默认现在")
    allow_duplicates: bool = False


class RecipePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    favorite: bool | None = None
    can_make: bool | None = None
    slots: list[Slot] | None = Field(None, min_length=1)
    tags: list[str] | None = Field(None, description="手动标签（整份替换）；自动标签（高蛋白、低嘌呤…）不用写")


@router.get("")
def list_recipes(user: str, slot: Slot | None = None, tag: str | None = None, q: str | None = None,
                 conn=Depends(get_conn), viewer=Depends(current_user)):
    """Recipe library (favourites first). slot: which meal it suits; tag: by tag (including automatic tags); q: search dish name, cuisine, tags, ingredients."""
    return _meal_call(recipes.list_recipes, conn, _owner(conn, viewer, user, "nutrition"), slot, tag, q)


@router.get("/suggest")
def suggest(user: str, date: dt.date | None = None, slot: Slot | None = None,
            conn=Depends(get_conn), viewer=Depends(current_user)):
    """Recommend 3 dishes: by the calories / protein left for the day, weekly red meat quota, beef short rib frequency, purine, saturated fat, whether eaten recently."""
    return _meal_call(recipes.suggest, conn, _owner(conn, viewer, user, "nutrition"), date, slot)


@router.get("/{recipe_id}")
def get_recipe(user: str, recipe_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _meal_call(recipes.get_recipe, conn, _owner(conn, viewer, user, "nutrition"), recipe_id)


@router.post("/{recipe_id}/log", status_code=201)
def log_recipe(user: str, recipe_id: int, body: RecipeLogIn | None = None,
               conn=Depends(get_conn), viewer=Depends(current_user)):
    """Ate this dish: write one SparkyFitness food entry. The same dish already logged for this meal on this day -> 409."""
    b = body or RecipeLogIn()
    return _meal_call(recipes.log_recipe, conn, _self(viewer, user), recipe_id, b.date, b.meal, b.servings, b.time,
                      b.allow_duplicates, channel="web")


@router.patch("/{recipe_id}")
def patch_recipe(user: str, recipe_id: int, body: RecipePatch, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Favourite, can-cook, which meal it suits, manual tags. Only changes metadata outside SparkyFitness."""
    return _meal_call(recipes.update_recipe, conn, _self(viewer, user), recipe_id,
                      body.model_dump(exclude_unset=True), sync=False)
