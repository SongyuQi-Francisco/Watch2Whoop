"""AI tool specs for grocery (wrapped into Tool and registered by assistant/tools.py). Descriptions are kept short (tool budget,
tests/test_briefing.py). Write tools have no user parameter: they write only for the caller. Deleting (remove_*) asks first
(ai/chat.ALWAYS_CONFIRM and assistant/.claude/settings.json)."""
from datetime import date

from ..toolspec import USER, nullable, schema
from . import params as P, store

_n = nullable
WEEK = _n("string", "这周任一天 YYYY-MM-DD，null=本周")
UNIT = {"type": "string", "enum": list(P.UNITS)}
UNIT_N = {"type": ["string", "null"], "enum": [*P.UNITS, None]}
IDS = {"type": "array", "items": {"type": "integer"}}


def _d(s):
    return date.fromisoformat(s) if s else None


def _slim_product(p: dict) -> dict:
    return {k: v for k, v in p.items() if v not in (None, [], {}, "") and k not in ("category_label", "size_label")}


NUTRITION = schema({k: _n("number") for k in P.NUTRITION_KEYS})
PLAN = schema({
    "kind": {"type": "string", "enum": ["recipe", "template", "food"]},
    "ref_id": _n("integer", "recipe / template 的 id"),
    "food": _n("string", "kind=food 时的食材名"),
    "amount": {"type": "number", "description": "recipe 份数 / template 次数 / food 数量"},
    "unit": UNIT_N,
})

TOOL_SPECS: list[dict] = [
    dict(
        name="find_products",
        description="商品库：规格、价格、单价、每 100g 营养、flags。",
        input_schema=schema({"user": USER, "q": _n("string", "搜名字 / 品牌 / 门店 / 条码"),
                             "category": {"type": ["string", "null"], "enum": [*P.CATEGORIES, None]}}),
        handler=lambda conn, uid, a: [_slim_product(p) for p in store.list_products(conn, uid, a["q"], a["category"])],
        scope="nutrition",
    ),
    dict(
        name="save_product",
        description="存商品（读价签 / 小票 / 营养成分表照片）。product_id=null 时同条码或同名同品牌的会更新，否则新建；"
                    "null = 不改。nutrition 每 100g（不知道就 {}）；matches = 能顶哪些食材名；price 每包。",
        input_schema=schema({
            "product_id": _n("integer"),
            "name": _n("string"), "brand": _n("string"), "store": _n("string"),
            "category": {"type": ["string", "null"], "enum": [*P.CATEGORIES, None]},
            "package_size": _n("number"), "package_unit": UNIT_N, "piece_g": _n("number", "每个几克"),
            "price": _n("number"), "currency": _n("string"),
            "nutrition": NUTRITION,
            "purine_level": {"type": ["string", "null"], "enum": ["low", "medium", "high", None]},
            "red_meat": _n("boolean"), "barcode": _n("string"),
            "matches": {"type": ["array", "null"], "items": {"type": "string"}}, "note": _n("string"),
        }),
        handler=lambda conn, uid, a: store.save_product(conn, uid, a, a["product_id"]),
        scope="nutrition", writes=True,
    ),
    dict(
        name="remove_product",
        description="从商品库删掉一个商品。",
        input_schema=schema({"product_id": {"type": "integer"}}),
        handler=lambda conn, uid, a: store.remove_product(conn, uid, a["product_id"]),
        scope="nutrition", writes=True,
    ),
    dict(
        name="generate_shopping_list",
        description="按这周要吃的生成采购清单（合并食材、对商品库、扣存货、按包装取整、估价）；重新生成只换计划来的未买项。"
                    "warnings（红肉 / 牛肋条 / 嘌呤 / 高盐糖脂）要转告。",
        input_schema=schema({"week": WEEK, "plan": {"type": "array", "items": PLAN}}),
        handler=lambda conn, uid, a: store.generate(conn, uid, _d(a["week"]), a["plan"]),
        scope="nutrition", writes=True,
    ),
    dict(
        name="get_shopping_list",
        description="一周的采购清单（按类别，买了没有、估价、warnings）和家里存货 pantry。",
        input_schema=schema({"user": USER, "week": WEEK}),
        handler=lambda conn, uid, a: store.get_list(conn, uid, _d(a["week"]), with_pantry=True),
        scope="nutrition",
    ),
    dict(
        name="add_shopping_items",
        description="往清单加东西；qty=null = 一包（或 1 个）。同名未买的会累加。",
        input_schema=schema({"week": WEEK, "items": {"type": "array", "items": schema({
            "name": _n("string"), "product_id": _n("integer"), "qty": _n("number"), "unit": UNIT_N, "note": _n("string")})}}),
        handler=lambda conn, uid, a: store.add_items(conn, uid, _d(a["week"]), a["items"]),
        scope="nutrition", writes=True,
    ),
    dict(
        name="mark_bought",
        description="勾掉已买（bought=false 取消）。买了自动进存货；paid 给实付总价会更新商品价格。",
        input_schema=schema({"item_ids": IDS, "bought": {"type": "boolean"},
                             "paid": {"type": ["array", "null"], "items": schema({"item_id": {"type": "integer"}, "price": {"type": "number"}})}}),
        handler=lambda conn, uid, a: store.set_bought(conn, uid, a["item_ids"], a["bought"],
                                                      {p["item_id"]: p["price"] for p in a["paid"] or []}),
        scope="nutrition", writes=True,
    ),
    dict(
        name="remove_shopping_items",
        description="从清单删掉几行。",
        input_schema=schema({"item_ids": IDS}),
        handler=lambda conn, uid, a: store.remove_items(conn, uid, a["item_ids"]),
        scope="nutrition", writes=True,
    ),
    dict(
        name="set_pantry",
        description="设家里现在有多少（粗略），例如「鸡蛋还剩 6 个」；quantity=0 = 用完了。",
        input_schema=schema({"items": {"type": "array", "items": schema({
            "name": {"type": "string"}, "quantity": {"type": "number"}, "unit": UNIT, "product_id": _n("integer")})}}),
        handler=lambda conn, uid, a: store.set_pantry(conn, uid, a["items"]),
        scope="nutrition", writes=True,
    ),
]
