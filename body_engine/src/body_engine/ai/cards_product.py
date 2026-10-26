"""Product card of the in-page chat (FB-12; rollout flag product_card on top of jarvis_cards_v2; ai/cards.py dispatches here).

One card per product, built only from what the user's own account holds:
- nutrition per 100 g / 100 ml, the energy split, and per piece / per pack when the size is known: the product library
  (grocery_products), i.e. what a label photo or Open Food Facts (lookup_barcode, attribution kept) gave save_product;
- prices per store: the latest price the user recorded (receipt, shelf-label screenshot, or their own AI reading a product page:
  record_store_prices -> price_observations, ticking a list line -> grocery_purchases), plus the library product's own price.
  The server never visits a store website and never reads Coles / Woolworths data of its own (they block automated access and we
  do not get around it, ai/web/params.NO_FETCH_HOSTS; the weekly catalogue sync is not used here): no price recorded = no price shown;
- the price history of that product (the existing prices.stats), drawn as a line only from TREND_MIN_POINTS days on;
- what is at home (pantry).
A block with no data is left out (None / []), never filled in. Buttons are send actions the page words itself (ai.js SEND).
"""
from decimal import Decimal

import psycopg

from ..grocery import links as GL, params as GP, prices, store

PRODUCT = "product"
FLAG = "product_card"
TREND_MIN_POINTS = 3     # days with a price before the card draws a line; fewer = numbers only
TREND_MAX_POINTS = 30
MAX_STORES, MAX_PANTRY = 6, 3
ENERGY_KCAL_PER_G = {"protein_g": 4.0, "carbs_g": 4.0, "fat_g": 9.0}   # Atwater factors, only for the split bar
SERVING_KEYS = ("kcal", "protein_g", "carbs_g", "fat_g")   # what the card's per-serving line shows
OFFER_KEYS = ("store", "label", "name", "size", "price", "was_price", "unit_price", "unit_price_unit", "special", "special_label",
              "half_price", "age_days", "stale", "source")


def _key(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _num(v) -> float | None:
    return None if v is None or isinstance(v, bool) or not isinstance(v, (int, float, Decimal)) else float(v)


# ---------------------------------------------------------------- nutrition
def _split(per100: dict) -> dict | None:
    """Share of the energy from protein / carbs / fat (whole percent, adding up to 100); needs all three."""
    if not all(k in per100 for k in ENERGY_KCAL_PER_G):
        return None
    kcal = {k: per100[k] * f for k, f in ENERGY_KCAL_PER_G.items()}
    total = sum(kcal.values())
    if total <= 0:
        return None
    pct = {k: round(100 * v / total) for k, v in kcal.items()}
    pct[max(pct, key=pct.get)] += 100 - sum(pct.values())   # rounding goes to the biggest part
    return pct


def _serving(row: dict, per100: dict, unit: str) -> dict | None:
    """The label's own per-serving column when the product has one (#291: serving_size + nutrition_serving); else one piece
    (piece_g) or the whole pack, when its weight / volume is known: kcal and macros for that amount."""
    ns = row.get("nutrition_serving") or {}
    if _num(row.get("serving_size")) and any(_num(ns.get(k)) is not None for k in SERVING_KEYS):
        return {"kind": "serving", "amount": round(float(row["serving_size"]), 1), "unit": row.get("serving_unit") or unit,
                **{k: round(float(ns[k]), 1) for k in SERVING_KEYS if _num(ns.get(k)) is not None}}
    if _num(row.get("piece_g")):
        kind, amount = "piece", float(row["piece_g"])
    elif row.get("package_unit") in ("g", "ml") and _num(row.get("package_size")):
        kind, amount = "pack", float(row["package_size"])
    else:
        return None
    f = amount / 100
    return {"kind": kind, "amount": round(amount, 1), "unit": unit,
            **{k: round(per100[k] * f, 1) for k in ("kcal", "protein_g", "carbs_g", "fat_g") if k in per100}}


def nutrition(row: dict) -> dict | None:
    n = row.get("nutrition") or {}
    per100 = {k: round(float(n[k]), 1) for k in GP.NUTRITION_KEYS if _num(n.get(k)) is not None}
    if not per100:
        return None
    unit = "ml" if row.get("package_unit") == "ml" else "g"
    return {"unit": unit, "per100": per100, "split": _split(per100), "serving": _serving(row, per100, unit),
            "flags": store.flag_labels(store.product_flags(row)), "source": row.get("nutrition_source")}


# ---------------------------------------------------------------- prices
def _own_row(conn, uid: int, row: dict, taken: set) -> dict | None:
    """The library product's own price (label / receipt, set by save_product or by ticking a list line) as a store row, unless that
    store already has a recorded price."""
    price = _num(row.get("price"))
    if price is None:
        return None
    key = GL.store_key(row.get("store")) or None   # coles / woolworths; None for another shop or none given
    if key and key in taken:
        return None
    size, unit = _num(row.get("package_size")), row.get("package_unit")
    unit_price = round(price / size * (1000 if unit in ("g", "ml") else 1), 2) if size and unit else None
    age = (store.today(conn, uid) - row["price_on"]).days if row.get("price_on") else None
    return {"store": key, "label": GP.STORES[key]["label"] if key else ((row.get("store") or "").strip() or None), "name": None,
            "size": store.fmt_qty(size, unit) if size and unit else None, "price": price, "was_price": None,
            "unit_price": unit_price, "unit_price_unit": {"g": "kg", "ml": "L"}.get(unit, "ea") if unit_price is not None else None,
            "special": None, "special_label": None, "half_price": False, "age_days": age,
            "stale": age is not None and age > GP.PRICE_STALE_DAYS, "source": "library",
            "currency": row.get("currency") or prices.CURRENCY}


def _mark_lowest(rows: list[dict]) -> None:
    """Flag the cheapest row among those not stale, when at least two can be compared: all with a unit price in the same unit,
    else all the same pack size. A tie flags nobody."""
    fresh = [r for r in rows if not r["stale"] and r["age_days"] is not None]
    if len(fresh) < 2:
        return
    if all(r["unit_price"] is not None for r in fresh) and len({r["unit_price_unit"] for r in fresh}) == 1:
        def value(r):
            return r["unit_price"]
    elif len({r["size"] for r in fresh}) == 1 and fresh[0]["size"]:
        def value(r):
            return r["price"]
    else:
        return
    low = min(value(r) for r in fresh)
    winners = [r for r in fresh if value(r) == low]
    if len(winners) == 1:
        winners[0]["lowest"] = True


def _store_rows(conn, uid: int, row: dict | None, offers: list[dict]) -> list[dict]:
    out = [{**{k: o.get(k) for k in OFFER_KEYS}, "currency": prices.CURRENCY} for o in offers]
    if row:
        own = _own_row(conn, uid, row, {o["store"] for o in offers})
        if own:
            out.append(own)
    _mark_lowest(out)
    return out[:MAX_STORES]


def _per_day(points: list[dict]) -> list[dict]:
    """One point per day, oldest first: the last price of the day, and whether any price that day was a special / what was paid."""
    by: dict[str, dict] = {}
    for p in points:
        cur = by.setdefault(p["date"], {"date": p["date"], "price": p["price"], "special": False, "paid": False})
        cur["price"] = p["price"]
        cur["special"] = cur["special"] or bool(p.get("special"))
        cur["paid"] = cur["paid"] or p.get("kind") == "paid"
    return [by[d] for d in sorted(by)]


def _history(conn, uid: int, row: dict | None, offers: list[dict]) -> dict | None:
    """The product's price history: prices.stats of the store product with the most days, else what the user paid for the library
    product. None when there are fewer than two days of it and no purchase verdict (one price is just the store row)."""
    cands = []
    for o in offers:
        st = prices.stats(conn, uid, o["store_product_id"])
        cands.append({"label": o["label"], "days": _per_day(st["points"]), "min": st["min"], "avg": st["avg"], "latest": st["latest"],
                      "specials": st["specials"], "last_purchase": st["last_purchase"]})
    paid = _per_day(prices.purchase_points(conn, uid, row["id"])) if row else []
    if paid:
        vals = [p["price"] for p in paid]
        cands.append({"label": None, "days": paid, "min": min(vals), "avg": round(sum(vals) / len(vals), 2), "latest": vals[-1],
                      "specials": None, "last_purchase": None})
    if not cands:
        return None
    best = max(cands, key=lambda c: (len(c["days"]), c["days"][-1]["date"] if c["days"] else ""))
    lp = best["last_purchase"]
    lp = lp if lp and not lp.get("estimated") else None
    if len(best["days"]) < 2 and not lp:
        return None
    sp = best["specials"] or {}
    days = best["days"][-TREND_MAX_POINTS:]
    return {"store": best["label"], "count": len(best["days"]), "points": days if len(days) >= TREND_MIN_POINTS else [],
            "min": best["min"], "avg": best["avg"], "latest": best["latest"],
            "specials": {"count": sp["count"], "share": sp.get("share")} if sp.get("count") else None,
            "last_purchase": {k: lp[k] for k in ("date", "unit_paid", "on_special", "verdict")} if lp else None}


# ---------------------------------------------------------------- pantry
def _pantry(conn, uid: int, row: dict | None, q: str | None) -> list[dict]:
    """What is at home that is this product: linked by id, or the same name as the product / its `matches` (exact, not fuzzy)."""
    names = {_key(row["name"]), *(_key(m) for m in row["matches"] or [])} if row else {_key(q)}
    names.discard("")
    out = []
    for p in store.list_pantry(conn, uid):
        if (row and p["product_id"] == row["id"]) or _key(p["name"]) in names:
            out.append({k: p[k] for k in ("name", "quantity", "unit", "label", "days_ago", "best_before", "days_left", "expiring")})
    return out[:MAX_PANTRY]


# ---------------------------------------------------------------- the card
def product(conn: psycopg.Connection, uid: int, product_id=None, q: str | None = None) -> dict:
    """The card for a library product (product_id = its id, find_products) or, by name / barcode, the best match in the library
    or else in the user's own recorded store prices. ValueError (readable by the model) when there is no such product."""
    if product_id is None and not (q or "").strip():
        raise ValueError("商品卡要 id（商品库里的商品 id）或 metric（商品名 / 条码）")
    try:
        row = store.find_product(conn, uid, None if product_id is None else int(product_id), q)
    except (LookupError, TypeError, ValueError):
        raise ValueError("没有这个商品（id 用 find_products 里的）") from None
    offers = prices.offers_for(conn, uid, row, None if row else q, MAX_STORES)
    if row is None and not offers:
        raise ValueError("商品库和用户记录的价格里都没有这个商品（先 save_product 或 record_store_prices）")
    first = offers[0] if offers else {}
    size = (store.fmt_qty(_num(row.get("package_size")), row["package_unit"])
            if row and row.get("package_size") and row.get("package_unit") else first.get("size"))
    nut = nutrition(row) if row else None
    actions = [{"kind": "send", "key": "log_product"}] if nut else []
    actions.append({"kind": "send", "key": "record_price"})
    return {"type": PRODUCT, "id": row["id"] if row else None, "name": row["name"] if row else first["name"],
            "brand": row.get("brand") if row else None, "size": size or None, "nutrition": nut,
            "allergens": (row.get("allergens") or []) if row else [], "traces": (row.get("allergen_traces") or []) if row else [],
            "image_url": row.get("image_url") if row else None,
            "stores": _store_rows(conn, uid, row, offers), "history": _history(conn, uid, row, offers),
            "pantry": _pantry(conn, uid, row, q), "actions": actions}


def summary(card: dict) -> str | None:
    """The model's one line for a product card (AI-facing, Chinese); None for other types."""
    if card["type"] != PRODUCT:
        return None
    parts = []
    n = card["nutrition"]
    p = (n or {}).get("per100") or {}
    if n:
        parts.append(f"每 100{n['unit']} {p['kcal']:g} kcal、蛋白 {p.get('protein_g', 0):g} g" if "kcal" in p else f"有每 100{n['unit']} 的营养")
    else:
        parts.append("商品库里没有营养（可以请用户拍营养成分表或给条码）")
    if card.get("allergens"):
        parts.append("含过敏原：" + "、".join(card["allergens"]))
    rows = card["stores"]
    if rows:
        low = next((r for r in rows if r.get("lowest")), rows[0])
        age = "日期不详" if low["age_days"] is None else "今天" if low["age_days"] <= 0 else f"{low['age_days']} 天前"
        parts.append(f"{len(rows)} 条用户自己记录的价格（{'最低 ' if low.get('lowest') else ''}{low['label'] or '?'} {low['price']:g}，{age}）")
    else:
        parts.append("没有价格记录")
    h = card["history"]
    if h:
        parts.append(f"价格历史 {h['count']} 天（最低 {h['min']:g}、平均 {h['avg']:g}）")
    if card["pantry"]:
        parts.append("家里有 " + "、".join(f"{x['name']} {x['label']}" for x in card["pantry"]))
    tail = ("价格都是用户自己记录的（小票、价签截图），不是实时价，没有的块卡片没显示。"
            + ("" if rows else "请用户拍价签或小票发来，你用 record_store_prices 记（超市没连接先 set_stores）。")
            + "不要用文字复述卡片里的数字。")
    return f"已显示商品卡「{card['name']}」：{'；'.join(parts)}。{tail}"
