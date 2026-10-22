"""HTTP endpoints for grocery (Today page 饮食 -> 采购, docs/grocery-plan.md). Authorization is the same as recipes:
reading someone else's needs nutrition in shares; writing only for the caller, the token needs write,
and with cookie login write requests must carry X-Body-Web: 1."""
import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from ..assistant.memory_api import _meal_call
from ..records.api import _owner, _self, current_user, get_conn
from . import pantry_auto, prices, store
from .sync import offers as store_offers

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
    product_url: str | None = Field(None, max_length=300, description="Coles / Woolworths 商品页；\"\" = 清除")
    nutrition_source: str | None = Field(None, max_length=60)

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
    plan: list[PlanEntry] = Field(default_factory=list, description="empty = only the usual items that are due (needs some)")


class StapleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    every_days: int | None = Field(None, ge=1, le=180)
    qty: float | None = Field(None, gt=0)
    unit: Literal["pack", "g", "ml", "pcs"] | None = None


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
    best_before: dt.date | None = None   # None = keep (issue #104)


class PantryAutoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class StoresIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connected: list[str] = Field(max_length=10, description="已连接的超市：coles / woolworths，[] = 都不连")


class PantryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[PantryEntry] = Field(min_length=1)


@router.get("/stores")
def get_stores(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Connected supermarkets (issue #44): store links / prices are shown only for these. None by default."""
    return store.stores_view(conn, _owner(conn, viewer, user, "nutrition"))


@router.put("/stores")
def put_stores(user: str, body: StoresIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _meal_call(store.set_connected_stores, conn, _self(viewer, user), body.connected)


class StorePriceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store: str = Field(max_length=20)
    name: str = Field(max_length=160)
    price: float = Field(ge=0, le=10000, description="每包，AUD")
    url: str | None = Field(None, max_length=300)
    brand: str | None = Field(None, max_length=60)
    size: str | None = Field(None, max_length=40)
    unit_price: float | None = Field(None, ge=0)
    unit_price_unit: Literal["kg", "L", "ea"] | None = None
    was_price: float | None = Field(None, ge=0)
    special: Literal["half_price", "price_drop", "multibuy", "special"] | None = None
    special_label: str | None = Field(None, max_length=60)
    barcode: str | None = Field(None, max_length=20)
    image_url: str | None = Field(None, max_length=500)
    observed_at: dt.datetime | None = None
    source: Literal["ai", "screenshot", "manual"] = "manual"
    for_item: str | None = Field(None, max_length=60)
    bought: bool = False
    packages: float | None = Field(None, gt=0, le=100)
    paid_total: float | None = Field(None, ge=0)


class StorePricesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[StorePriceIn] = Field(min_length=1, max_length=30)


@router.get("/context")
def get_context(user: str, week: dt.date | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    """What the user's AI needs to look prices up: this week's plan and list, health constraints, connected stores, pantry, and which
    items still need a price (missing / older than a week). The server itself never visits store websites."""
    return _meal_call(prices.shopping_context, conn, _owner(conn, viewer, user, "nutrition"), week)


@router.post("/store-prices", status_code=201)
def post_store_prices(user: str, body: StorePricesIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Prices the user's AI (or the user) read from store pages / screenshots, as a batch. Only for oneself."""
    items = [{**i.model_dump(), "observed_at": i.observed_at.isoformat() if i.observed_at else None} for i in body.items]
    return _meal_call(prices.record, conn, _self(viewer, user), items)


@router.get("/store-products")
def get_store_products(user: str, q: str | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    """The store products this user uploaded or linked (no one else's), each with their own latest price and its age."""
    return _meal_call(prices.list_products, conn, _owner(conn, viewer, user, "nutrition"), q)


@router.get("/store-products/{store_product_id}")
def get_store_product(user: str, store_product_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Price history of one of the user's store products: curve, min / average / latest, specials, last purchase verdict.
    404 for anything the user has not linked (whether or not it exists)."""
    return _meal_call(prices.stats, conn, _owner(conn, viewer, user, "nutrition"), store_product_id)


@router.get("/specials")
def get_specials(user: str, q: str | None = None, limit: int = 20, conn=Depends(get_conn), viewer=Depends(current_user)):
    """This week's half price in the user's connected supermarkets (grocery-sync; empty when the component is off), health-filtered,
    things the user buys first."""
    owner = _owner(conn, viewer, user, "nutrition")
    lst = store.get_list(conn, owner)
    return store_offers.weekly_specials(conn, owner, q, max(1, min(limit, 50)),
                                        red_meat_left=store_offers.red_meat_left(conn, owner, _summary(conn, owner, lst["week_start"])))


def _summary(conn, user_id: int, week_start: str) -> dict:
    row = conn.execute("SELECT summary FROM shopping_lists WHERE user_id = %s AND week_start = %s", (user_id, week_start)).fetchone()
    return row["summary"] if row else {}


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


@router.put("/products/{product_id}/staple")
def put_staple(user: str, product_id: int, body: StapleIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Mark a usual item (issue #103): interval, amount each time. Own products only."""
    return _meal_call(store.set_staple, conn, _self(viewer, user), product_id, body.model_dump())


@router.delete("/products/{product_id}/staple")
def delete_staple(user: str, product_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _meal_call(store.remove_staple, conn, _self(viewer, user), product_id)


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


@router.get("/pantry-auto")
def get_pantry_auto(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Whether logged meals take from the pantry (issue #104); the Today page only shows it."""
    return pantry_auto.status(conn, _owner(conn, viewer, user, "nutrition"))


@router.put("/pantry-auto")
def put_pantry_auto(user: str, body: PantryAutoIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _meal_call(pantry_auto.set_enabled, conn, _self(viewer, user), body.enabled)


@router.put("/pantry")
def put_pantry(user: str, body: PantryIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Set what is at home (quantity 0 = used up)."""
    return _meal_call(store.set_pantry, conn, _self(viewer, user), [i.model_dump() for i in body.items])
