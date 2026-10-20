"""HTTP endpoints for grocery (Today page 饮食 -> 采购, docs/grocery-plan.md). Authorization is the same as recipes:
reading someone else's needs nutrition in shares; writing only for the caller, the token needs write,
and with cookie login write requests must carry X-Body-Web: 1."""
import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from ..assistant.memory_api import _meal_call
from ..records.api import _owner, _self, current_user, get_conn
from . import store

router = APIRouter(prefix="/v1/users/{user}/grocery", tags=["grocery"])
Unit = Literal["g", "ml", "pcs"]


class Nutrition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kcal: float | None = Field(None, ge=0)
    protein_g: float | None = Field(None, ge=0)
    carbs_g: float | None = Field(None, ge=0)
    fat_g: float | None = Field(None, ge=0)
    sat_fat_g: float | None = Field(None, ge=0)
    sugar_g: float | None = Field(None, ge=0)
    sodium_mg: float | None = Field(None, ge=0)


class ProductIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, max_length=60)
    brand: str | None = None
    store: str | None = None
    category: str | None = None
    package_size: float | None = Field(None, gt=0)
    package_unit: Unit | None = None
    piece_g: float | None = Field(None, gt=0)
    price: float | None = Field(None, ge=0)
    currency: str | None = None
    nutrition: Nutrition | None = Field(None, description="每 100 g（或 100 ml）")
    purine_level: Literal["low", "medium", "high"] | None = None
    red_meat: bool | None = None
    barcode: str | None = None
    matches: list[str] | None = None
    note: str | None = None

    def fields(self) -> dict:
        d = self.model_dump(exclude_unset=True)
        if d.get("nutrition") is not None:
            d["nutrition"] = {k: v for k, v in d["nutrition"].items() if v is not None}
        return d


class PlanEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["recipe", "template", "food"]
    ref_id: int | None = None
    food: str | None = None
    amount: float = Field(gt=0, le=5000, description="recipe 份数 / template 次数 / food 数量")
    unit: Unit | None = None


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week: dt.date | None = Field(None, description="这周任一天，默认本周")
    plan: list[PlanEntry] = Field(min_length=1)


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, max_length=60)
    product_id: int | None = None
    qty: float | None = Field(None, gt=0)
    unit: Unit | None = None
    note: str | None = None


class ItemsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week: dt.date | None = None
    items: list[ItemIn] = Field(min_length=1)


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bought: bool
    paid_price: float | None = Field(None, ge=0, description="这一行实付总价")


class PantryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    quantity: float = Field(ge=0)
    unit: Unit
    product_id: int | None = None


class PantryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[PantryEntry] = Field(min_length=1)


@router.get("/products")
def list_products(user: str, q: str | None = None, category: str | None = None,
                  conn=Depends(get_conn), viewer=Depends(current_user)):
    """Product library: size, price (per package and per kg / L / piece), nutrition per 100 g, flags."""
    return _meal_call(store.list_products, conn, _owner(conn, viewer, user, "nutrition"), q, category)


@router.post("/products", status_code=201)
def create_product(user: str, body: ProductIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """New product (or updates the one with the same barcode / name + brand)."""
    return _meal_call(store.save_product, conn, _self(viewer, user), body.fields())


@router.patch("/products/{product_id}")
def patch_product(user: str, product_id: int, body: ProductIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _meal_call(store.save_product, conn, _self(viewer, user), body.fields(), product_id)


@router.delete("/products/{product_id}", status_code=204)
def delete_product(user: str, product_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    _meal_call(store.remove_product, conn, _self(viewer, user), product_id)
    return Response(status_code=204)


@router.get("/list")
def get_list(user: str, week: dt.date | None = None, pantry: bool = False,
             conn=Depends(get_conn), viewer=Depends(current_user)):
    """This week's shopping list (week = any day in the week, default this week): items by category, progress, estimated total,
    health hints; pantry=true adds what is at home."""
    return _meal_call(store.get_list, conn, _owner(conn, viewer, user, "nutrition"), week, pantry)


@router.post("/list/generate")
def generate(user: str, body: GenerateIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """(Re)generate the week's list from a plan; manual and bought lines stay."""
    return _meal_call(store.generate, conn, _self(viewer, user), body.week, [p.model_dump() for p in body.plan])


@router.post("/list/items", status_code=201)
def add_items(user: str, body: ItemsIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _meal_call(store.add_items, conn, _self(viewer, user), body.week, [i.model_dump() for i in body.items])


@router.patch("/list/items/{item_id}")
def patch_item(user: str, item_id: int, body: ItemPatch, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Tick / untick one line (the Today page tap). Ticking adds it to the pantry."""
    paid = {item_id: body.paid_price} if body.paid_price is not None else None
    return _meal_call(store.set_bought, conn, _self(viewer, user), [item_id], body.bought, paid)


@router.delete("/list/items/{item_id}", status_code=204)
def delete_item(user: str, item_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    _meal_call(store.remove_items, conn, _self(viewer, user), [item_id])
    return Response(status_code=204)


@router.get("/pantry")
def get_pantry(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return store.list_pantry(conn, _owner(conn, viewer, user, "nutrition"))


@router.put("/pantry")
def put_pantry(user: str, body: PantryIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Set what is at home (quantity 0 = used up)."""
    return _meal_call(store.set_pantry, conn, _self(viewer, user), [i.model_dump() for i in body.items])
