"""AI tool specs for grocery (wrapped into Tool and registered by assistant/tools.py). Descriptions are kept short (tool budget,
tests/test_briefing.py). Write tools have no user parameter: they write only for the caller. Deleting (remove_*) asks first
(ai/chat.ALWAYS_CONFIRM and assistant/.claude/settings.json)."""
from datetime import date

from ..toolspec import USER, nullable, schema
from . import off, params as P, prices, store
from .sync import offers as SO

_n = nullable
WEEK = _n("string", "这周任一天 YYYY-MM-DD，null=本周")
UNIT = {"type": "string", "enum": list(P.UNITS)}
UNIT_N = {"type": ["string", "null"], "enum": [*P.UNITS, None]}
IDS = {"type": "array", "items": {"type": "integer"}}


def _d(s):
    return date.fromisoformat(s) if s else None


def _slim_product(p: dict) -> dict:
    out = {k: v for k, v in p.items() if v not in (None, [], {}, "") and k not in ("category_label", "size_label", "store_label", "links")}
    if p.get("offer"):
        out["offer"] = _slim_offer({**p["offer"], "save_total": p["offer"].get("save")})
    return out


OFFER_KEYS = ("store", "name", "size", "price", "was_price", "unit_price", "unit_price_unit", "special", "special_label", "save_total",
              "age_days", "store_product_id")


def _slim_offer(o: dict | None) -> dict | None:
    return {k: o[k] for k in OFFER_KEYS if o.get(k) not in (None, "")} if o else None


def _slim_list(lst: dict) -> dict:   # store links are for the page; the AI has product_url via find_products
    items = []
    for i in lst.get("items", []):
        x = {k: v for k, v in i.items() if k not in ("links", "store_label", "offer", "pantry_label")}
        if i.get("offer"):
            x["offer"] = _slim_offer(i["offer"])
        items.append(x)
    out = {**lst, "items": items}
    if out.get("specials"):
        out["specials"] = [_slim_special(s) for s in out["specials"]]
    return out


def _slim_special(s: dict) -> dict:
    return {k: s[k] for k in ("store", "name", "size", "price", "was_price", "save", "unit_price", "unit_price_unit", "flags", "for")
            if s.get(k) not in (None, "", [])}


def _history(conn, uid, a):
    if a["store_product_id"] is not None:
        st = prices.stats(conn, uid, a["store_product_id"])
        st["points"] = st["points"][-20:]
        return {k: v for k, v in st.items() if v not in (None, [], {}, "") and k not in ("image_url", "store_label")}
    return [{k: v for k, v in {**p, "offer": _slim_offer({**p["offer"], "save_total": p["offer"]["save"],
                                                          "age_days": p["offer"]["age_days"]}) if p["offer"] else None}.items()
             if v not in (None, [], {}, "") and k not in ("image_url", "store_label")} for p in prices.list_products(conn, uid, a["q"])[:30]]


def _specials(conn, uid, a):
    lst = store.get_list(conn, uid)
    row = conn.execute("SELECT summary FROM shopping_lists WHERE user_id = %s AND week_start = %s", (uid, lst["week_start"])).fetchone()
    out = SO.weekly_specials(conn, uid, a["q"], a["limit"], red_meat_left=SO.red_meat_left(conn, uid, row["summary"] if row else {}))
    return {**out, "items": [_slim_special(s) for s in out["items"]]}


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
        description="存商品（价签 / 小票 / 营养表照片、商品链接）。product_id=null 时同条码 / 商品页 / 同名同品牌的更新，"
                    "否则新建；null=不改。nutrition 每 100g（不知道就 {}）；matches=能顶的食材名；price 每包。",
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
            "product_url": _n("string", "Coles / WWS 商品页，\"\"=清除"),
            "nutrition_source": _n("string", "如 lookup_barcode 的 source"),
        }),
        handler=lambda conn, uid, a: _slim_product(store.save_product(conn, uid, a, a["product_id"])),
        scope="nutrition", writes=True,
    ),
    dict(
        name="lookup_barcode",
        description="按条码查 Open Food Facts（名字、规格、每 100g 营养）。",
        input_schema=schema({"barcode": {"type": "string"}}),
        handler=lambda conn, uid, a: off.lookup(uid, a["barcode"]),
        scope="nutrition",
    ),
    dict(
        name="set_stores",
        description="设已连接的超市（woolworths=WWS），只显示它们的链接 / 价格；[]=都不连。",
        input_schema=schema({"stores": {"type": "array", "items": {"type": "string", "enum": list(P.STORES)}}}),
        handler=lambda conn, uid, a: store.set_connected_stores(conn, uid, a["stores"]),
        scope="nutrition", writes=True,
    ),
    dict(
        name="get_weekly_specials",
        description="已连接超市本周半价（按健康约束筛过，用户常买的在前；for=对上的商品）。推荐 / 列清单时优先用。",
        input_schema=schema({"user": USER, "q": _n("string", "英文关键词"), "limit": _n("integer")}),
        handler=_specials,
        scope="nutrition",
    ),
    dict(
        name="get_shopping_context",
        description="查价前先调：本周计划和清单、健康约束、已连接超市、存货、needs_price（缺价或超过 7 天的，最多 15 样）、rules（查价规则，必须遵守）。",
        input_schema=schema({"user": USER, "week": WEEK}),
        handler=lambda conn, uid, a: prices.shopping_context(conn, uid, _d(a["week"])),
        scope="nutrition",
    ),
    dict(
        name="record_store_prices",
        description="写回你在超市商品页 / 截图里看到的价格（一次 ≤30 样，只写清单上的）。price 每包 AUD；special+was_price=特价和原价；"
                    "for_item=对应的清单项；bought=true 记成用户实际买了（paid_total 实付）。",
        input_schema=schema({"items": {"type": "array", "items": schema({
            "store": {"type": "string", "enum": list(P.STORES)}, "url": _n("string", "商品页"), "name": {"type": "string"},
            "brand": _n("string"), "size": _n("string", "规格，如 500g"), "price": {"type": "number"},
            "unit_price": _n("number"), "unit_price_unit": {"type": ["string", "null"], "enum": ["kg", "L", "ea", None]},
            "was_price": _n("number"), "special": {"type": ["string", "null"], "enum": [*P.SPECIAL_TYPES, None]},
            "special_label": _n("string"), "barcode": _n("string"), "image_url": _n("string", "超市图片地址（只存引用）"),
            "observed_at": _n("string", "ISO，null=现在"),
            "source": {"type": ["string", "null"], "enum": [*P.PRICE_SOURCES, None], "description": "ai=网页 screenshot=截图"},
            "for_item": _n("string"), "bought": _n("boolean"), "packages": _n("number"), "paid_total": _n("number")})}}),
        handler=lambda conn, uid, a: prices.record(conn, uid, a["items"]),
        scope="nutrition", writes=True,
    ),
    dict(
        name="get_price_history",
        description="用户自己的超市商品价格：q 搜（最近价和几天前），或 store_product_id 看历史（最低 / 平均 / 最近、特价频率、上次买贵没）。",
        input_schema=schema({"user": USER, "q": _n("string"), "store_product_id": _n("integer")}),
        handler=lambda conn, uid, a: _history(conn, uid, a),
        scope="nutrition",
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
        description="按这周要吃的生成采购清单（合并食材、对商品库、扣存货、按包装取整、估价，加到期的常买）；重新生成只换计划 / 常买来的未买项；plan 空=只补常买、留计划行。"
                    "warnings（按用户自己的饮食约束 / 高盐糖脂）要转告。",
        input_schema=schema({"week": WEEK, "plan": {"type": "array", "items": PLAN}}),
        handler=lambda conn, uid, a: _slim_list(store.generate(conn, uid, _d(a["week"]), a["plan"])),
        scope="nutrition", writes=True,
    ),
    dict(
        name="get_shopping_list",
        description="一周的采购清单（按类别，买了没有、估价、warnings）、存货 pantry、connected_stores。",
        input_schema=schema({"user": USER, "week": WEEK}),
        handler=lambda conn, uid, a: _slim_list(store.get_list(conn, uid, _d(a["week"]), with_pantry=True)),
        scope="nutrition",
    ),
    dict(
        name="add_shopping_items",
        description="往清单加东西；qty=null = 一包（或 1 个）。同名未买的会累加。",
        input_schema=schema({"week": WEEK, "items": {"type": "array", "items": schema({
            "name": _n("string"), "product_id": _n("integer"), "qty": _n("number"), "unit": UNIT_N, "note": _n("string")})}}),
        handler=lambda conn, uid, a: _slim_list(store.add_items(conn, uid, _d(a["week"]), a["items"])),
        scope="nutrition", writes=True,
    ),
    dict(
        name="mark_bought",
        description="勾掉已买（bought=false 取消）。买了自动进存货；paid 给实付总价会更新商品价格。",
        input_schema=schema({"item_ids": IDS, "bought": {"type": "boolean"},
                             "paid": {"type": ["array", "null"], "items": schema({"item_id": {"type": "integer"}, "price": {"type": "number"}})}}),
        handler=lambda conn, uid, a: _slim_list(store.set_bought(conn, uid, a["item_ids"], a["bought"],
                                                                 {p["item_id"]: p["price"] for p in a["paid"] or []})),
        scope="nutrition", writes=True,
    ),
    dict(
        name="remove_shopping_items",
        description="从清单删掉几行。",
        input_schema=schema({"item_ids": IDS}),
        handler=lambda conn, uid, a: _slim_list(store.remove_items(conn, uid, a["item_ids"])),
        scope="nutrition", writes=True,
    ),
    dict(
        name="set_staple",
        description="用户确认后标常买（推荐在 get_shopping_context.staple_suggestions）；every_days/qty null=按历史/一包。",
        input_schema=schema({"product_id": {"type": "integer"}, "every_days": _n("integer"), "qty": _n("number"),
                             "unit": {"type": ["string", "null"], "enum": [*P.STAPLE_UNITS, None]}}),
        handler=lambda conn, uid, a: _slim_product(store.set_staple(conn, uid, a["product_id"], a)),
        scope="nutrition", writes=True,
    ),
    dict(
        name="remove_staple",
        description="取消常买。",
        input_schema=schema({"product_id": {"type": "integer"}}),
        handler=lambda conn, uid, a: _slim_product(store.remove_staple(conn, uid, a["product_id"])),
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
