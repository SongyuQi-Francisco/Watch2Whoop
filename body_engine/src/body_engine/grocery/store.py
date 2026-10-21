"""Grocery: product library, pantry and weekly shopping lists (docs/grocery-plan.md, migration 240).

- Products are structured by the user's AI from label / receipt photos (save_product upserts by id, barcode, or name + brand).
- A weekly list is generated from a plan (recipes x servings, meal templates x times, free foods): ingredients merged by name,
  matched to products, minus the pantry, rounded up to package sizes, estimated cost; health hints in `warnings`.
- Ticking an item adds what was bought to the pantry (unticking takes it back); a paid price updates the product's price.
- Store links are limited to the supermarkets the user has connected (grocery_settings, none by default, issue #44).
- Prices the user's own AI recorded (prices.py, record_store_prices) price the list: per line the cheapest connected store,
  totals per store, savings on specials, what still needs a price; ticking a line records the purchase (grocery_purchases).
- With the opt-in grocery-sync component on (grocery/sync/), lines without an own price get the best store `offer` from the
  connected supermarkets' weekly catalogue; this week's half price comes first and its saving is summed. The user's own price
  stays the estimate when there is one (docs/grocery-store-data-plan.md §8.6–8.7).
The caller is responsible for authorization (writes only for the caller)."""
import math
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ..assistant import meals, recipes
from ..i18n import L as Li, tl
from ..nutrition import constraints as diet_constraints
from ..rules.nutrition import red_meat_estimate
from . import links as L, params as P
from . import prices as PR
from .sync import offers as SO

PURINE = ("low", "medium", "high")
PRODUCT_FIELDS = ("name", "brand", "store", "category", "package_size", "package_unit", "piece_g", "price", "currency",
                  "nutrition", "purine_level", "red_meat", "barcode", "matches", "note", "product_url", "nutrition_source")
PLAN_KINDS = ("recipe", "template", "food")


# ---------------------------------------------------------------- helpers
def _tz(conn, user_id: int) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])


def today(conn, user_id: int) -> date:
    return datetime.now(_tz(conn, user_id)).date()


def week_start(conn, user_id: int, day: date | None = None) -> date:
    d = day or today(conn, user_id)
    return d - timedelta(days=d.weekday())


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _num(v) -> float | None:
    return None if v is None else float(v)


def fmt_qty(qty: float | None, unit: str) -> str:
    if qty is None:
        return ""
    if unit in ("g", "ml") and qty >= 1000:
        return f"{qty / 1000:g} {'kg' if unit == 'g' else 'L'}"
    return Li(f"{qty:g} 个", f"{qty:g} pcs") if unit == "pcs" else f"{qty:g} {unit}"


def category_label(cat: str | None) -> str:
    return Li(P.CATEGORIES.get(cat, "其他"), P.CATEGORIES_EN.get(cat, "Other"))


def flag_labels(flags: list[str]) -> list[str]:
    """product_flags (Chinese, compared in _warnings) in the request's language."""
    return [tl(f, P.FLAGS_EN) for f in flags]


def guess_category(name: str) -> str:
    s = _key(name)
    for cat, kws in P.CATEGORY_KEYWORDS:
        if any(k.lower() in s for k in kws):
            return cat
    return "other"


# ---------------------------------------------------------------- products
def _check_nutrition(n: dict, name: str) -> dict:
    if not isinstance(n, dict):
        raise ValueError("nutrition 要是对象（每 100g）")
    unknown = set(n) - set(P.NUTRITION_KEYS)
    if unknown:
        raise ValueError(f"nutrition 不认识：{'、'.join(sorted(unknown))}（可用 {', '.join(P.NUTRITION_KEYS)}）")
    out = {}
    for k, v in n.items():
        if v is None:
            continue
        if not isinstance(v, (int, float)) or v < 0:
            raise ValueError(f"{name}：{k} 要是 ≥ 0 的数")
        out[k] = float(v)
    for k in ("protein_g", "carbs_g", "fat_g", "sat_fat_g", "sugar_g"):
        if out.get(k, 0) > 100:
            raise ValueError(f"{name}：{k} 每 100g 不能超过 100")
    if out.get("sat_fat_g", 0) > out.get("fat_g", 100) + 0.5:
        raise ValueError(f"{name}：饱和脂肪不能多于总脂肪")
    if out.get("sugar_g", 0) > out.get("carbs_g", 100) + 0.5:
        raise ValueError(f"{name}：糖不能多于碳水")
    if all(k in out for k in ("kcal", "protein_g", "carbs_g", "fat_g")):
        est = 4 * out["protein_g"] + 4 * out["carbs_g"] + 9 * out["fat_g"]
        if abs(est - out["kcal"]) > max(20, 0.3 * out["kcal"]):  # fibre / alcohol / polyols explain small gaps
            raise ValueError(f"{name}：每 100g 热量 {out['kcal']:g} kcal 和三大营养素算出来的 {est:.0f} kcal 差太多")
    return out


def _check_product(f: dict, current: dict | None = None) -> dict:
    """f: fields to write (None already dropped). current: existing row on update (for cross-field checks)."""
    out = dict(f)
    name = (out.get("name") if "name" in out else (current or {}).get("name")) or ""
    if "name" in out:
        out["name"] = name = out["name"].strip()
        if not 1 <= len(name) <= 60:
            raise ValueError("商品名 1–60 字")
    for k, n in (("brand", 40), ("store", 40)):
        if k in out:
            out[k] = (out[k] or "").strip() or None
            if out[k] and len(out[k]) > n:
                raise ValueError(f"{k} 最多 {n} 字")
    if "category" in out and out["category"] not in P.CATEGORIES:
        raise ValueError(f"category 只能是 {', '.join(P.CATEGORIES)}")
    if "package_unit" in out and out["package_unit"] not in P.UNITS:
        raise ValueError("package_unit 只能是 g / ml / pcs")
    for k in ("package_size", "piece_g"):
        if k in out and (not isinstance(out[k], (int, float)) or out[k] <= 0):
            raise ValueError(f"{name}：{k} 要是 > 0 的数")
    size = out.get("package_size", (current or {}).get("package_size"))
    unit = out.get("package_unit", (current or {}).get("package_unit"))
    if (size is None) != (unit is None):
        raise ValueError(f"{name}：package_size 和 package_unit 要一起给")
    if "price" in out and (not isinstance(out["price"], (int, float)) or out["price"] < 0):
        raise ValueError(f"{name}：price 要是 ≥ 0 的数（每包）")
    if "currency" in out:
        out["currency"] = (out["currency"] or "").strip().upper() or None
        if out["currency"] and not re.fullmatch(r"[A-Z]{3}", out["currency"]):
            raise ValueError("currency 用 3 位代码，例如 AUD / CNY")
    if "nutrition" in out:
        out["nutrition"] = Jsonb(_check_nutrition(out["nutrition"], name))
    if "purine_level" in out and out["purine_level"] not in PURINE:
        raise ValueError("purine_level 只能是 low / medium / high")
    if "barcode" in out:
        out["barcode"] = re.sub(r"\s", "", out["barcode"] or "") or None
        if out["barcode"] and not re.fullmatch(r"[0-9]{6,14}", out["barcode"]):
            raise ValueError("条码只能是 6–14 位数字")
    if "matches" in out:
        m = [x.strip() for x in out["matches"] if isinstance(x, str) and x.strip()]
        if len(m) > 20 or any(len(x) > 60 for x in m):
            raise ValueError("matches 最多 20 个、每个 ≤ 60 字")
        out["matches"] = list(dict.fromkeys(m))
    if "red_meat" in out:
        out["red_meat"] = bool(out["red_meat"])
    if "product_url" in out:   # "" clears; otherwise only a Coles / Woolworths product page, stored canonical with its SKU
        out["store_sku"] = None
        if (out["product_url"] or "").strip():
            link = L.parse_product_url(out["product_url"])
            out["product_url"], out["store_sku"] = link["url"], link["sku"]
            store = out.get("store", (current or {}).get("store"))
            if L.store_key(store) != link["store"]:   # the link says which chain it is
                out["store"] = P.STORES[link["store"]]["name"]
        else:
            out["product_url"] = None
    if "nutrition_source" in out:
        out["nutrition_source"] = (out["nutrition_source"] or "").strip() or None
        if out["nutrition_source"] and len(out["nutrition_source"]) > 60:
            raise ValueError("nutrition_source 最多 60 字")
    return out


def product_flags(row: dict) -> list[str]:
    n = row.get("nutrition") or {}
    high = P.HIGH_PER_100ML if row.get("package_unit") == "ml" else P.HIGH_PER_100G
    flags = [P.FLAG_LABELS[k] for k, lim in high.items() if (n.get(k) or 0) > lim]
    if row.get("purine_level") == "high":
        flags.append("高嘌呤")
    if row.get("red_meat"):
        flags.append("红肉")
    return flags


# ---------------------------------------------------------------- connected stores (issue #44)
def connected_stores(conn: psycopg.Connection, user_id: int) -> list[str]:
    row = conn.execute("SELECT stores FROM grocery_settings WHERE user_id = %s", (user_id,)).fetchone()
    return L.check_stores(row["stores"]) if row else []


def stores_view(conn: psycopg.Connection, user_id: int) -> dict:
    con = connected_stores(conn, user_id)
    return {"connected": con, "stores": [{"key": k, "label": s["label"], "connected": k in con} for k, s in P.STORES.items()]}


def set_connected_stores(conn: psycopg.Connection, user_id: int, stores: list[str]) -> dict:
    """Replace the set of connected supermarkets ([] = none)."""
    stores = L.check_stores(stores)
    conn.execute("""INSERT INTO grocery_settings (user_id, stores) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET stores = EXCLUDED.stores, updated_at = now()""", (user_id, stores))
    return stores_view(conn, user_id)


def product_view(row: dict, connected: list[str] | tuple = ()) -> dict:
    size, unit, price = _num(row["package_size"]), row["package_unit"], _num(row["price"])
    unit_price = None
    if size and price is not None:   # per kg / L / piece
        unit_price = round(price / size * (1000 if unit in ("g", "ml") else 1), 2)
    return {"id": row["id"], "name": row["name"], "brand": row["brand"], "store": row["store"],
            "store_label": L.store_label(row["store"]), "category": row["category"], "category_label": category_label(row["category"]),
            "package_size": size, "package_unit": unit, "size_label": fmt_qty(size, unit) if unit else None,
            "piece_g": _num(row["piece_g"]), "price": price, "currency": row["currency"],
            "price_on": row["price_on"].isoformat() if row["price_on"] else None, "unit_price": unit_price,
            "nutrition": row["nutrition"] or {}, "purine_level": row["purine_level"], "red_meat": row["red_meat"],
            "barcode": row["barcode"], "matches": row["matches"], "note": row["note"], "flags": flag_labels(product_flags(row)),
            "product_url": row["product_url"], "store_sku": row["store_sku"], "nutrition_source": row["nutrition_source"],
            "links": product_links(row, connected)}


def product_links(row: dict, connected: list[str] | tuple = ()) -> list[dict]:
    return L.links(row["name"], row["store"], row["product_url"], row["brand"], row["matches"], connected)


def _products(conn, user_id: int) -> list[dict]:
    return conn.execute("SELECT * FROM grocery_products WHERE user_id = %s AND active ORDER BY category, name",
                        (user_id,)).fetchall()


def list_products(conn: psycopg.Connection, user_id: int, q: str | None = None, category: str | None = None) -> list[dict]:
    if category is not None and category not in P.CATEGORIES:
        raise ValueError(f"category 只能是 {', '.join(P.CATEGORIES)}")
    qq = _key(q or "")
    con = connected_stores(conn, user_id)
    vis = SO.visible_stores(conn, user_id)
    out = []
    for r in _products(conn, user_id):
        if category and r["category"] != category:
            continue
        if qq and not any(qq in _key(t or "") for t in (r["name"], r["brand"], r["store"], r["barcode"], *r["matches"])):
            continue
        v = product_view(r, con)
        if vis and (off := SO.find_offer(conn, vis, r["name"], r)):
            v["offer"] = off
        out.append(v)
    return out


def _product_row(conn, user_id: int, product_id: int) -> dict:
    row = conn.execute("SELECT * FROM grocery_products WHERE id = %s AND user_id = %s AND active",
                       (product_id, user_id)).fetchone()
    if row is None:
        raise LookupError("没有这个商品（或不是你的）")
    return row


def save_product(conn: psycopg.Connection, user_id: int, data: dict, product_id: int | None = None) -> dict:
    """Create or update. product_id given -> update it; otherwise an existing product with the same barcode, or the same
    name + brand, is updated; else a new one. Fields that are None are left unchanged (defaults for a new product)."""
    f = {k: data[k] for k in PRODUCT_FIELDS if data.get(k) is not None}
    if isinstance(f.get("nutrition"), dict):   # null values inside = unknown, not "clear"
        f["nutrition"] = {k: v for k, v in f["nutrition"].items() if v is not None}
        if not f["nutrition"]:
            del f["nutrition"]
    cur = None
    if product_id is not None:
        cur = _product_row(conn, user_id, product_id)
    else:
        if f.get("barcode"):
            cur = conn.execute("SELECT * FROM grocery_products WHERE user_id = %s AND barcode = %s AND active",
                               (user_id, re.sub(r"\s", "", f["barcode"]))).fetchone()
        if cur is None and (f.get("product_url") or "").strip():   # the same store product page
            cur = conn.execute("SELECT * FROM grocery_products WHERE user_id = %s AND product_url = %s AND active",
                               (user_id, L.parse_product_url(f["product_url"])["url"])).fetchone()
        if cur is None and f.get("name"):
            cur = conn.execute("""SELECT * FROM grocery_products WHERE user_id = %s AND active AND lower(name) = lower(%s)
                                  AND lower(coalesce(brand, '')) = lower(coalesce(%s, ''))""",
                               (user_id, f["name"].strip(), (f.get("brand") or "").strip() or None)).fetchone()
    if "price" in f and "price_on" not in f:
        f["price_on"] = today(conn, user_id)
    if cur is None:
        if not f.get("name"):
            raise ValueError("新商品要有名字（name）")
        f.setdefault("category", guess_category(f["name"]))
        f = _check_product(f)
        cols = list(f)
        row = conn.execute(f"INSERT INTO grocery_products (user_id, {', '.join(cols)}) VALUES (%s, {', '.join(['%s'] * len(cols))}) "
                           "RETURNING *", (user_id, *f.values())).fetchone()
        return _saved(conn, user_id, row, True)
    if not f:
        raise ValueError("没有要改的内容")
    if "nutrition" in f and isinstance(f["nutrition"], dict):   # merge: a label photo may add only some values
        f["nutrition"] = {**(cur["nutrition"] or {}), **f["nutrition"]}
    f = _check_product(f, cur)
    sets = ", ".join(f"{k} = %s" for k in f)
    row = conn.execute(f"UPDATE grocery_products SET {sets}, updated_at = now() WHERE id = %s AND user_id = %s RETURNING *",
                       (*f.values(), cur["id"], user_id)).fetchone()
    return _saved(conn, user_id, row, False)


def _saved(conn, user_id: int, row: dict, created: bool) -> dict:
    con = connected_stores(conn, user_id)
    out = {**product_view(row, con), "created": created}
    key = L.parse_product_url(row["product_url"])["store"] if row["product_url"] else None
    if key and key not in con:   # saved, but its link stays hidden until the user connects that supermarket
        out["store_not_connected"] = P.STORES[key]["label"]
    return out


def remove_product(conn: psycopg.Connection, user_id: int, product_id: int) -> dict:
    row = _product_row(conn, user_id, product_id)
    conn.execute("UPDATE grocery_products SET active = FALSE, updated_at = now() WHERE id = %s", (row["id"],))
    return {"removed": {"id": row["id"], "name": row["name"], "brand": row["brand"]}}


def match_product(products: list[dict], name: str) -> dict | None:
    """Exact name / matches first, then containment either way (at least 2 characters)."""
    k = _key(name)
    for p in products:
        if k == _key(p["name"]) or k in {_key(m) for m in p["matches"]}:
            return p
    if len(k) >= 2:
        for p in products:
            for t in (p["name"], *p["matches"]):
                tk = _key(t)
                if len(tk) >= 2 and (tk in k or k in tk):
                    return p
    return None


# ---------------------------------------------------------------- pantry
def _pantry_view(r: dict, tz: ZoneInfo) -> dict:
    """days_ago: whole days since the last change in the user's time zone (the Today page shows 「x 天前更新」)."""
    cat = r.get("category") or guess_category(r["name"])
    return {"id": r["id"], "name": r["name"], "quantity": float(r["quantity"]), "unit": r["unit"],
            "label": fmt_qty(float(r["quantity"]), r["unit"]), "product_id": r["product_id"],
            "category": cat, "category_label": category_label(cat), "updated_at": r["updated_at"],
            "days_ago": max(0, (datetime.now(tz).date() - r["updated_at"].astimezone(tz).date()).days)}


def list_pantry(conn: psycopg.Connection, user_id: int) -> list[dict]:
    """What is at home, sorted by category (the product's, else guessed from the name) then name."""
    tz = _tz(conn, user_id)
    rows = conn.execute("""SELECT pi.*, gp.category FROM pantry_items pi
                           LEFT JOIN grocery_products gp ON gp.id = pi.product_id AND gp.user_id = pi.user_id AND gp.active
                           WHERE pi.user_id = %s""", (user_id,)).fetchall()
    order = list(P.CATEGORIES)
    out = [_pantry_view(r, tz) for r in rows]
    out.sort(key=lambda p: (order.index(p["category"]) if p["category"] in order else 99, p["name"].lower()))
    return out


def _pantry_row(conn, user_id: int, name: str):
    return conn.execute("SELECT * FROM pantry_items WHERE user_id = %s AND lower(name) = lower(%s)",
                        (user_id, name.strip())).fetchone()


def set_pantry(conn: psycopg.Connection, user_id: int, items: list[dict]) -> list[dict]:
    """items: [{name, quantity, unit, product_id?}]: set what is at home now; quantity 0 = used up (row removed)."""
    if not items:
        raise ValueError("至少给一样")
    for it in items:
        name = (it.get("name") or "").strip()
        q, unit = it.get("quantity"), it.get("unit")
        if not 1 <= len(name) <= 60:
            raise ValueError("存货名 1–60 字")
        if unit not in P.UNITS:
            raise ValueError(f"{name}：unit 只能是 g / ml / pcs")
        if not isinstance(q, (int, float)) or q < 0:
            raise ValueError(f"{name}：quantity 要是 ≥ 0 的数")
        pid = it.get("product_id")
        if pid is not None:
            _product_row(conn, user_id, pid)
        if q == 0:
            conn.execute("DELETE FROM pantry_items WHERE user_id = %s AND lower(name) = lower(%s)", (user_id, name))
            continue
        conn.execute("""INSERT INTO pantry_items (user_id, name, product_id, quantity, unit) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (user_id, lower(name)) DO UPDATE SET quantity = EXCLUDED.quantity, unit = EXCLUDED.unit,
                          product_id = coalesce(EXCLUDED.product_id, pantry_items.product_id), updated_at = now()""",
                     (user_id, name, pid, q, unit))
    return list_pantry(conn, user_id)


def _convert(qty: float, frm: str, to: str, piece_g: float | None) -> float | None:
    if frm == to or {frm, to} == {"g", "ml"}:   # 1 ml ≈ 1 g: rough on purpose
        return qty
    if piece_g and frm == "pcs":
        return qty * piece_g
    if piece_g and to == "pcs":
        return qty / piece_g
    return None


def _pantry_change(conn, user_id: int, name: str, product_id: int | None, qty: float, unit: str, piece_g=None) -> None:
    row = _pantry_row(conn, user_id, name)
    if row is None:
        if qty > 0:
            conn.execute("INSERT INTO pantry_items (user_id, name, product_id, quantity, unit) VALUES (%s, %s, %s, %s, %s)",
                         (user_id, name, product_id, qty, unit))
        return
    delta = _convert(qty, unit, row["unit"], piece_g)
    if delta is None:   # units do not convert: replace with the new unit when adding, ignore when taking back
        if qty > 0:
            conn.execute("UPDATE pantry_items SET quantity = %s, unit = %s, updated_at = now() WHERE id = %s",
                         (qty, unit, row["id"]))
        return
    conn.execute("UPDATE pantry_items SET quantity = GREATEST(0, quantity + %s), updated_at = now() WHERE id = %s",
                 (delta, row["id"]))


# ---------------------------------------------------------------- shopping lists
def _list_row(conn, user_id: int, ws: date, create: bool = False):
    row = conn.execute("SELECT * FROM shopping_lists WHERE user_id = %s AND week_start = %s", (user_id, ws)).fetchone()
    if row is None and create:
        row = conn.execute("INSERT INTO shopping_lists (user_id, week_start) VALUES (%s, %s) RETURNING *",
                           (user_id, ws)).fetchone()
    return row


def _sized(product: dict | None, need: float, unit: str) -> dict:
    """What to buy for `need` (in unit): whole packages of the product, or rounded up."""
    if product and product["package_size"] and product["package_unit"]:
        punit, size = product["package_unit"], float(product["package_size"])
        n = _convert(need, unit, punit, _num(product["piece_g"]))
        if n is not None:
            packs = max(1, math.ceil(n / size - 1e-9))
            price = _num(product["price"])
            return {"unit": punit, "need_qty": round(n, 1), "packages": packs, "buy_qty": packs * size,
                    "est_cost": round(packs * price, 2) if price is not None else None, "currency": product["currency"]}
    buy = math.ceil(need - 1e-9) if unit == "pcs" else max(P.ROUND_G, math.ceil(need / P.ROUND_G - 1e-9) * P.ROUND_G)
    return {"unit": unit, "need_qty": round(need, 1), "packages": None, "buy_qty": buy, "est_cost": None, "currency": None}


def _check_plan(plan: list[dict]) -> list[dict]:
    if not plan:
        raise ValueError("计划是空的：给这周要吃的菜（recipe × 份数）、模板（template × 次数）或食材（food）")
    out = []
    for p in plan:
        kind, amount = p.get("kind"), p.get("amount")
        if kind not in PLAN_KINDS:
            raise ValueError("kind 只能是 recipe / template / food")
        if not isinstance(amount, (int, float)) or not 0 < amount <= 5000:
            raise ValueError("amount 要是 > 0 的数（份数 / 次数 / 数量）")
        if kind in ("recipe", "template") and not isinstance(p.get("ref_id"), int):
            raise ValueError(f"{kind} 要给 ref_id")
        if kind == "food":
            if not (p.get("food") or "").strip():
                raise ValueError("food 要给名字")
            if p.get("unit") not in P.UNITS:
                raise ValueError(f"{p['food']}：unit 只能是 g / ml / pcs")
        out.append({"kind": kind, "ref_id": p.get("ref_id") if kind != "food" else None,
                    "food": (p.get("food") or "").strip() or None, "amount": float(amount),
                    "unit": p.get("unit") if kind == "food" else None})
    return out


def _needs(conn, user_id: int, plan: list[dict]) -> tuple[dict, dict]:
    """Merge the plan into {key: {name, g, pcs, store, used_by}} and health totals {red_meat_g, food_times}
    (food_times: how often the plan has each food the user limits per week, nutrition/constraints.py, issue #76)."""
    needs: dict[str, dict] = {}
    red = 0.0
    diet = diet_constraints.for_user(conn, user_id)
    food_times: dict[str, float] = {f["target"]: 0 for f in diet.food_limits}

    def count(foods: list[str], times: float) -> None:
        for target in {lim["target"] for f in foods if (lim := diet.food_limit_hit(f)) is not None}:
            food_times[target] += times

    def add(name, qty, unit, used_by, store=None):
        n = needs.setdefault(_key(name), {"name": name.strip(), "g": 0.0, "pcs": 0.0, "store": None, "used_by": []})
        n["pcs" if unit == "pcs" else "g"] += qty
        n["store"] = n["store"] or store
        if used_by and used_by not in n["used_by"]:
            n["used_by"].append(used_by)

    for p in plan:
        if p["kind"] == "recipe":
            r = recipes.get_row(conn, user_id, p["ref_id"])
            k = p["amount"] / float(r["servings"])
            for i in r["ingredients"]:
                add(i["food"], i["grams"] * k, "g", r["name"], i.get("store"))
                red += (i.get("red_meat_g") or 0) * k
            count([i["food"] for i in r["ingredients"]], math.ceil(p["amount"]))
        elif p["kind"] == "template":
            t = meals._get(conn, user_id, p["ref_id"])
            for i in t["items"]:
                add(i["food"], i["grams"] * p["amount"], "g", t["name"])
                red += (i.get("red_meat_g") or 0) * p["amount"]
            count([i["food"] for i in t["items"]], math.ceil(p["amount"]))
        else:
            add(p["food"], p["amount"], p["unit"], None)
            if p["unit"] in ("g", "ml") and red_meat_estimate(p["food"]):
                red += p["amount"]
            count([p["food"]], 1)
    return needs, {"red_meat_g": round(red), "food_times": food_times}


def generate(conn: psycopg.Connection, user_id: int, day: date | None, plan: list[dict]) -> dict:
    plan = _check_plan(plan)
    ws = week_start(conn, user_id, day)
    needs, summary = _needs(conn, user_id, plan)
    products = _products(conn, user_id)
    pantry = {_key(r["name"]): r for r in conn.execute("SELECT * FROM pantry_items WHERE user_id = %s", (user_id,))}
    pantry_by_product = {r["product_id"]: r for r in pantry.values() if r["product_id"]}
    lst = _list_row(conn, user_id, ws, create=True)
    conn.execute("DELETE FROM shopping_items WHERE list_id = %s AND source = 'plan' AND bought_at IS NULL", (lst["id"],))
    manual = {_key(r["name"]) for r in conn.execute(
        "SELECT name FROM shopping_items WHERE list_id = %s AND source = 'manual' AND bought_at IS NULL", (lst["id"],))}
    covered, added = [], 0
    for k, n in needs.items():
        if k in manual:
            continue
        prod = match_product(products, n["name"])
        piece_g = _num(prod["piece_g"]) if prod else None
        unit = prod["package_unit"] if prod and prod["package_unit"] else ("pcs" if n["g"] == 0 else "g")
        conv = [_convert(q, u, unit, piece_g) for q, u in ((n["g"], "g"), (n["pcs"], "pcs")) if q]
        note = None
        if any(c is None for c in conv):   # e.g. grams of eggs but the product is sold by count without piece_g
            unit = "g" if n["g"] else "pcs"
            need = n["g"] or n["pcs"]
            note = f"另 {n['pcs']:g} 个" if n["g"] and n["pcs"] else None
        else:
            need = sum(conv)
        stock = pantry.get(k) or (pantry_by_product.get(prod["id"]) if prod else None)
        used = None   # how much the pantry covered (shown on the line as 「家里有 …」, issue #102)
        if stock:
            have = _convert(float(stock["quantity"]), stock["unit"], unit, piece_g)
            if have:
                used = round(min(have, need), 1)
                need -= have
        if need <= 0:
            covered.append(n["name"])
            continue
        s = _sized(prod, need, unit)
        conn.execute("""INSERT INTO shopping_items (list_id, user_id, name, product_id, category, store, need_qty, unit, packages,
                          buy_qty, est_cost, currency, source, used_by, note, pantry_qty)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'plan',%s,%s,%s)""",
                     (lst["id"], user_id, n["name"], prod["id"] if prod else None,
                      prod["category"] if prod else guess_category(n["name"]),
                      (prod["store"] if prod else None) or n["store"], s["need_qty"], s["unit"], s["packages"], s["buy_qty"],
                      s["est_cost"], s["currency"], n["used_by"], note, used or None))
        added += 1
    conn.execute("UPDATE shopping_lists SET plan = %s, summary = %s, generated_at = now() WHERE id = %s",
                 (Jsonb(plan), Jsonb(summary), lst["id"]))
    out = {**get_list(conn, user_id, ws), "added": added, "covered_by_pantry": covered}
    if out.get("connected_stores"):   # this week's half price that fits the health rules, for swapping things in (AI)
        sp = SO.weekly_specials(conn, user_id, limit=8, red_meat_left=SO.red_meat_left(conn, user_id, summary))
        out["specials"] = sp["items"]
    return out


def _item_view(r: dict, prod: dict | None = None, connected: list[str] | tuple = ()) -> dict:
    buy, unit = float(r["buy_qty"]), r["unit"]
    label = (Li(f"{r['packages']} 包", f"{r['packages']} pk") + f" · {fmt_qty(buy, unit)}" if r["packages"] else fmt_qty(buy, unit))
    return {"id": r["id"], "name": r["name"], "product_id": r["product_id"], "category": r["category"],
            "category_label": category_label(r["category"]), "store": r["store"], "store_label": L.store_label(r["store"]),
            "need_qty": _num(r["need_qty"]), "unit": unit, "packages": r["packages"], "buy_qty": buy, "qty_label": label,
            "est_cost": _num(r["est_cost"]), "currency": r["currency"], "source": r["source"], "used_by": r["used_by"],
            "note": r["note"], "bought": r["bought_at"] is not None, "paid_price": _num(r["paid_price"]),
            "pantry_qty": _num(r["pantry_qty"]),
            "pantry_label": Li(f"家里有 {fmt_qty(float(r['pantry_qty']), unit)}", f"{fmt_qty(float(r['pantry_qty']), unit)} at home")
            if r["pantry_qty"] else None,
            "links": product_links(prod, connected) if prod else L.links(r["name"], r["store"], connected=connected)}


def _warnings(summary: dict, rows: list[dict], products: dict, diet) -> list[dict]:
    """Weekly totals against the user's own diet constraints (none without constraints), and product flags."""
    out = []
    red = summary.get("red_meat_g") or 0
    for r in rows:   # free foods / manual items that are red-meat products, counted by what is bought
        p = products.get(r["product_id"])
        if p and p["red_meat"] and r["source"] == "manual" and r["unit"] in ("g", "ml"):
            red += float(r["buy_qty"])
    cap = diet.red_meat_g
    if cap is not None and red > cap:
        out.append({"code": "red_meat", "label": Li(f"红肉 {red:.0f}g", f"Red meat {red:.0f}g"),
                    "detail": Li(f"这周计划的红肉约 {red:.0f}g，超过你设的每周 ≤ {cap:g}g",
                                 f"About {red:.0f} g of red meat planned this week, over your {cap:g} g a week")})
    for lim in diet.food_limits:
        n = (summary.get("food_times") or {}).get(lim["target"]) or 0
        if n > lim["max"]:
            out.append({"code": "food_limit", "label": Li(f"{lim['target']} ×{n:g}", f"{lim['target']} ×{n:g}"),
                        "detail": Li(f"{lim['target']}计划 {n:g} 次，你设的上限每周 {lim['max']:g} 次",
                                     f"{lim['target']} planned {n:g} times, your limit is {lim['max']:g} a week")})
    for r in rows:
        p = products.get(r["product_id"])
        hidden = {"红肉"} | (set() if diet.low_purine else {"高嘌呤"})   # red meat is the weekly total above; purine only matters with a purine constraint
        flags = [f for f in product_flags(p) if f not in hidden] if p else []
        if flags:
            out.append({"code": "product", "label": f"{r['name']} " + Li("、".join(flags), ", ".join(flag_labels(flags))),
                        "item_id": r["id"],
                        "detail": f"{p['name']} 标记：{'、'.join(flags)}（每 100g 按英国 FSA 红绿灯「高」线；嘌呤按商品库）"})
    return out


def _add_offers(conn, user_id: int, items: list[dict], products: dict) -> dict | None:
    """Store offers for unbought items (grocery-sync on + store connected). Returns {items, save} for this week's half price,
    None when no store data is visible. Items without a user price get the store price as the estimate (est_from = store)."""
    vis = SO.visible_stores(conn, user_id)
    if not vis:
        return None
    half = {"items": 0, "save": {}}
    for i in items:
        if i["bought"] or i.get("offer"):   # the user's own recorded price wins
            continue
        off = SO.find_offer(conn, vis, i["name"], products.get(i["product_id"]))
        if not off:
            continue
        packs, cost, save = SO.item_cost(off, i["buy_qty"], i["unit"])
        i["offer"] = {**off, "packages": packs, "cost": cost, "save_total": save}
        i["links"] = [{"store": off["store"], "label": off["label"], "kind": "product", "url": off["url"]} if
                      (lk["store"] == off["store"] and lk["kind"] == "search") else lk for lk in i["links"]]
        if i["est_cost"] is None and cost is not None:
            i["est_cost"], i["currency"], i["est_from"] = cost, SO.CURRENCY, off["store"]
        if off["half_price"]:
            half["items"] += 1
            if save:
                half["save"][SO.CURRENCY] = round(half["save"].get(SO.CURRENCY, 0) + save, 2)
    return half


def get_list(conn: psycopg.Connection, user_id: int, day: date | None = None, with_pantry: bool = False) -> dict:
    ws = week_start(conn, user_id, day)
    lst = _list_row(conn, user_id, ws)
    rows = conn.execute("SELECT * FROM shopping_items WHERE list_id = %s ORDER BY category, bought_at NULLS FIRST, name",
                        (lst["id"],)).fetchall() if lst else []
    products = {p["id"]: p for p in _products(conn, user_id)}
    con = connected_stores(conn, user_id)
    items = [_item_view(r, products.get(r["product_id"]), con) for r in rows]
    priced = PR.price_list(conn, user_id, items, products)
    half = _add_offers(conn, user_id, items, products)
    if priced is not None:
        own = priced.pop("half_price")
        if half is None:
            half = own
        else:
            half["items"] += own["items"]
            for c, v in own["save"].items():
                half["save"][c] = round(half["save"].get(c, 0) + v, 2)
    bought_ids = [i["id"] for i in items if i["bought"]]
    for iid, pur in (PR.purchases_for_items(conn, user_id, bought_ids).items() if bought_ids else ()):
        next(i for i in items if i["id"] == iid)["purchase"] = pur
    order = list(P.CATEGORIES)
    items.sort(key=lambda i: (order.index(i["category"]) if i["category"] in order else 99, i["bought"],
                              not (i.get("offer") or {}).get("half_price"), i["name"]))
    cost: dict[str, float] = {}
    for i in items:
        if i["est_cost"] is not None and i["currency"]:
            cost[i["currency"]] = round(cost.get(i["currency"], 0) + i["est_cost"], 2)
    out = {"week_start": ws.isoformat(), "week_end": (ws + timedelta(days=6)).isoformat(),
           "generated_at": lst["generated_at"] if lst else None, "plan": lst["plan"] if lst else [],
           "items": items, "progress": {"bought": sum(i["bought"] for i in items), "total": len(items)},
           "est_total": cost, "unpriced": sum(1 for i in items if i["est_cost"] is None),
           "warnings": _warnings(lst["summary"] if lst else {}, rows, products, diet_constraints.for_user(conn, user_id)),
           "connected_stores": con,
           **({"half_price": half} if half is not None and half["items"] else {}),
           **({"prices": priced} if priced is not None else {})}
    if with_pantry:
        out["pantry"] = list_pantry(conn, user_id)
    return out


def add_items(conn: psycopg.Connection, user_id: int, day: date | None, items: list[dict]) -> dict:
    """items: [{name, qty, unit, product_id, note}]: qty None = one package of the product (or 1 piece).
    An unbought item with the same name gets the quantity added."""
    if not items:
        raise ValueError("至少加一样")
    ws = week_start(conn, user_id, day)
    lst = _list_row(conn, user_id, ws, create=True)
    products = _products(conn, user_id)
    by_id = {p["id"]: p for p in products}
    for it in items:
        name = (it.get("name") or "").strip()
        pid = it.get("product_id")
        if pid is not None and pid not in by_id:
            raise LookupError(f"没有商品 {pid}（或不是你的）")
        prod = by_id[pid] if pid is not None else match_product(products, name) if name else None
        name = name or (prod["name"] if prod else "")
        if not 1 <= len(name) <= 60:
            raise ValueError("每样要有名字（1–60 字）")
        qty, unit = it.get("qty"), it.get("unit")
        if qty is not None and (not isinstance(qty, (int, float)) or qty <= 0):
            raise ValueError(f"{name}：qty 要是 > 0 的数")
        if unit is not None and unit not in P.UNITS:
            raise ValueError(f"{name}：unit 只能是 g / ml / pcs")
        if qty is None:
            if prod and prod["package_size"]:
                qty, unit = float(prod["package_size"]), prod["package_unit"]
            else:
                qty, unit = 1, unit or "pcs"
        unit = unit or (prod["package_unit"] if prod and prod["package_unit"] else "g")
        old = conn.execute("""SELECT * FROM shopping_items WHERE list_id = %s AND lower(name) = lower(%s) AND bought_at IS NULL
                              ORDER BY id LIMIT 1""", (lst["id"], name)).fetchone()
        if old:
            more = _convert(qty, unit, old["unit"], _num(prod["piece_g"]) if prod else None)
            if more is not None:
                qty, unit = float(old["need_qty"] or old["buy_qty"]) + more, old["unit"]
            conn.execute("DELETE FROM shopping_items WHERE id = %s", (old["id"],))
        s = _sized(prod, qty, unit) if prod else {"unit": unit, "need_qty": qty, "packages": None, "buy_qty": qty,
                                                  "est_cost": None, "currency": None}
        conn.execute("""INSERT INTO shopping_items (list_id, user_id, name, product_id, category, store, need_qty, unit, packages,
                          buy_qty, est_cost, currency, source, used_by, note)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                     (lst["id"], user_id, name, prod["id"] if prod else None,
                      prod["category"] if prod else guess_category(name), prod["store"] if prod else None,
                      s["need_qty"], s["unit"], s["packages"], s["buy_qty"], s["est_cost"], s["currency"],
                      old["source"] if old else "manual", old["used_by"] if old else [],
                      (it.get("note") or "").strip() or (old["note"] if old else None)))
    return get_list(conn, user_id, ws)


def _item_rows(conn, user_id: int, item_ids: list[int]) -> list[dict]:
    if not item_ids:
        raise ValueError("要给 item_ids")
    rows = conn.execute("SELECT * FROM shopping_items WHERE user_id = %s AND id = ANY(%s)", (user_id, item_ids)).fetchall()
    missing = set(item_ids) - {r["id"] for r in rows}
    if missing:
        raise LookupError(f"清单里没有 {', '.join(map(str, sorted(missing)))}（或不是你的）")
    return rows


def set_bought(conn: psycopg.Connection, user_id: int, item_ids: list[int], bought: bool = True,
               paid: dict[int, float] | None = None) -> dict:
    """Tick / untick. Ticking adds buy_qty to the pantry; unticking takes it back. paid {item_id: total paid for that line}
    updates the product's price per package."""
    paid = paid or {}
    rows = _item_rows(conn, user_id, item_ids)
    for r in rows:
        prod = conn.execute("SELECT * FROM grocery_products WHERE id = %s", (r["product_id"],)).fetchone() if r["product_id"] else None
        piece_g = _num(prod["piece_g"]) if prod else None
        price = paid.get(r["id"])
        if price is not None and (not isinstance(price, (int, float)) or price < 0):
            raise ValueError("实付价格要是 ≥ 0 的数")
        if bought and r["bought_at"] is None:
            conn.execute("UPDATE shopping_items SET bought_at = now(), paid_price = %s WHERE id = %s", (price, r["id"]))
            _pantry_change(conn, user_id, r["name"], r["product_id"], float(r["buy_qty"]), r["unit"], piece_g)
            PR.on_bought(conn, user_id, r, prod, price)   # purchase history (actual or recorded store price)
        elif bought and price is not None:
            conn.execute("UPDATE shopping_items SET paid_price = %s WHERE id = %s", (price, r["id"]))
            PR.on_bought(conn, user_id, r, prod, price)
        elif not bought and r["bought_at"] is not None:
            conn.execute("UPDATE shopping_items SET bought_at = NULL, paid_price = NULL WHERE id = %s", (r["id"],))
            _pantry_change(conn, user_id, r["name"], r["product_id"], -float(r["buy_qty"]), r["unit"], piece_g)
            PR.remove_item_purchase(conn, user_id, r["id"])
        if bought and price is not None and prod and prod["active"]:
            conn.execute("UPDATE grocery_products SET price = %s, price_on = %s, updated_at = now() WHERE id = %s",
                         (round(price / (r["packages"] or 1), 2), today(conn, user_id), prod["id"]))
    lst = conn.execute("SELECT week_start FROM shopping_lists WHERE id = %s", (rows[0]["list_id"],)).fetchone()
    return get_list(conn, user_id, lst["week_start"])


def remove_items(conn: psycopg.Connection, user_id: int, item_ids: list[int]) -> dict:
    """Delete list lines (the pantry is left as it is)."""
    rows = _item_rows(conn, user_id, item_ids)
    conn.execute("DELETE FROM shopping_items WHERE user_id = %s AND id = ANY(%s)", (user_id, item_ids))
    lst = conn.execute("SELECT week_start FROM shopping_lists WHERE id = %s", (rows[0]["list_id"],)).fetchone()
    return {"removed": [r["name"] for r in rows], **get_list(conn, user_id, lst["week_start"])}
