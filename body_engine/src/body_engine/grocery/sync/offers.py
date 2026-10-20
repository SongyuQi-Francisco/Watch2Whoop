"""Read side of grocery-sync (docs/grocery-store-data-plan.md §8.6–8.7): store offers for list items / library products, this
week's half-price recommendations, and half-price hits for meal suggestions.

Who sees what: only stores that are both turned on by the deployer (params.enabled_stores) and connected by the user
(grocery_settings, issue #44). With the component off everything here returns nothing, so the pages fall back to the user's own
products. The user's own data wins: a product with a saved store page matches that exact store product (by SKU), and the user's
own price stays the estimate; store prices are shown as "this week's price"."""
import math
import re
from datetime import date

import psycopg

from ...rules import params as rule_params
from ...rules.nutrition import is_short_rib, red_meat_estimate
from .. import links as L, params as GP
from . import params as P
from .runner import specials_week, sydney_today

WORD = re.compile(r"[a-z0-9]+")
CURRENCY = "AUD"   # both supported stores are Australian
_PROCESSED = ("schnitzel", "kebab", "marinated", "crumbed", "seasoned", "nugget", "burger", "slider", "kiev", "sauce",
              "flavoured", "pie", "roll", "dumpling", "pizza", "chips", "bites")


def visible_stores(conn: psycopg.Connection, user_id: int) -> list[str]:
    enabled = P.enabled_stores()
    if not enabled:
        return []
    row = conn.execute("SELECT stores FROM grocery_settings WHERE user_id = %s", (user_id,)).fetchone()
    return [s for s in (row["stores"] if row else []) if s in enabled]


# ---------------------------------------------------------------- search terms
def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3]
    if len(w) > 4 and w.endswith("oes"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _ascii(s: str | None) -> bool:
    s = (s or "").strip()
    return bool(s) and bool(re.search(r"[a-z]", s, re.IGNORECASE)) and all(ord(c) < 128 for c in s)


def terms_for(name: str, product: dict | None = None) -> list[list[str]]:
    """Candidate English search terms (each a list of word stems), best first: the product's English name, English `matches`,
    the item name if English, then the common-ingredient dictionary for Chinese names."""
    texts = []
    if product:
        if _ascii(product.get("name")):
            texts.append(product["name"])
        texts += [m for m in product.get("matches") or [] if _ascii(m)]
    if _ascii(name):
        texts.append(name)
    for src in [name, *((product or {}).get("matches") or []), (product or {}).get("name") or ""]:
        for zh, en in sorted(P.ZH_EN, key=lambda x: -len(x[0])):
            if zh in (src or ""):
                texts.append(en)
                break
    out, seen = [], set()
    for t in texts:
        words = tuple(_stem(w) for w in WORD.findall(t.lower()) if len(w) > 1 and not w.isdigit())[:4]
        if words and words not in seen:
            seen.add(words)
            out.append(list(words))
    return out


def _words(name: str) -> set[str]:
    return set(WORD.findall((name or "").lower()))


def _hits(words: list[str], name_words: set[str]) -> bool:
    return all(any(nw.startswith(w) for nw in name_words) for w in words)


def _processed(name: str) -> bool:
    nw = _words(name)
    return any(any(x.startswith(p) for x in nw) for p in _PROCESSED)


def _pick(rows: list[dict], words: list[str]) -> dict | None:
    """Plain products before processed ones; close matches (few extra words); then half price, then the lowest unit price."""
    if not rows:
        return None
    plain = [r for r in rows if not _processed(r["name"])] or rows

    def extra(r):
        bw = _words(r["brand"] or "")
        return len(_words(r["name"]) - bw) - len(words)
    best = min(extra(r) for r in plain)
    pool = [r for r in plain if extra(r) <= best + P.MAX_EXTRA_WORDS]
    inf = float("inf")
    pool.sort(key=lambda r: (r["special_type"] != "half_price", float(r["unit_price"]) if r["unit_price"] is not None else inf,
                             extra(r), float(r["price"]) if r["price"] is not None else inf))
    return pool[0]


def offer_view(r: dict) -> dict:
    num = lambda v: None if v is None else float(v)  # noqa: E731
    return {"store": r["store"], "label": GP.STORES[r["store"]]["label"], "sku": r["sku"], "name": r["name"],
            "size": r["size_text"], "price": num(r["price"]), "was_price": num(r["was_price"]),
            "unit_price": num(r["unit_price"]), "unit_price_unit": r["unit_price_unit"], "special": r["special_type"],
            "special_label": r["special_label"], "half_price": r["special_type"] == "half_price",
            "save": num(r["save_amount"]), "url": r["url"], "package_size": num(r["package_size"]),
            "package_unit": r["package_unit"]}


def find_offer(conn: psycopg.Connection, stores: list[str], name: str, product: dict | None = None) -> dict | None:
    """Best store product for a list item / library product among `stores` (already filtered to visible ones)."""
    if not stores:
        return None
    if product and product.get("product_url"):   # the user's own saved page: exactly that product, if its store is visible
        key = L.parse_product_url(product["product_url"])["store"]
        if key not in stores:
            return None
        row = conn.execute("SELECT * FROM store_products WHERE store = %s AND sku = %s AND active",
                           (key, product["store_sku"])).fetchone()
        return offer_view(row) if row else None
    chain = L.store_key((product or {}).get("store"))
    if chain:   # the product says which chain it is from
        stores = [s for s in stores if s == chain]
        if not stores:
            return None
    for words in terms_for(name, product):
        rows = conn.execute("SELECT * FROM store_products WHERE active AND store = ANY(%s) AND name ~* ALL(%s) LIMIT 200",
                            (stores, [rf"\m{w}" for w in words])).fetchall()
        if (best := _pick(rows, words)) is not None:
            return offer_view(best)
    return None


def item_cost(offer: dict, buy_qty: float, unit: str) -> tuple[int, float | None, float | None]:
    """(packages, cost, saving) when buying `buy_qty` (g / ml / pcs) of the offer's product."""
    size, punit = offer.get("package_size"), offer.get("package_unit")
    same = punit == unit or {punit, unit} == {"g", "ml"}
    packs = max(1, math.ceil(buy_qty / size - 1e-9)) if size and same else 1
    cost = round(packs * offer["price"], 2) if offer.get("price") is not None else None
    save = round(packs * offer["save"], 2) if offer.get("half_price") and offer.get("save") else None
    return packs, cost, save


# ---------------------------------------------------------------- this week's half price
def _excluded(name: str, category: str | None) -> bool:
    if category in P.EXCLUDE_CATEGORIES:
        return True
    low = " " + " ".join(WORD.findall((name or "").lower())) + " "
    for u in P.EXCLUDE_UNLESS:
        low = low.replace(f" {u} ", " ")
    return any(f" {k} " in low for k in P.EXCLUDE_KEYWORDS)


def _flags(name: str) -> list[str]:
    out = []
    if red_meat_estimate(name):
        out.append("红肉")
    if is_short_rib(name):
        out.append("牛肋条")
    nw = _words(name)
    if any(any(w.startswith(k) for w in nw) for k in P.HIGH_PURINE_KEYWORDS if " " not in k) or \
            any(k in (name or "").lower() for k in P.HIGH_PURINE_KEYWORDS if " " in k):
        out.append("高嘌呤")
    return out


def _user_terms(conn, user_id: int) -> list[tuple[str, list[str]]]:
    """(label, words) for everything the user buys: library products and this week's list items."""
    out = []
    for p in conn.execute("SELECT name, matches, store, product_url, store_sku FROM grocery_products WHERE user_id = %s AND active",
                          (user_id,)):
        out += [(p["name"], t) for t in terms_for(p["name"], p)[:2]]
    for i in conn.execute("""SELECT DISTINCT i.name FROM shopping_items i JOIN shopping_lists l ON l.id = i.list_id
                             WHERE i.user_id = %s AND l.week_start >= current_date - 7""", (user_id,)):
        out += [(i["name"], t) for t in terms_for(i["name"])[:1]]
    return out


def specials_rows(conn: psycopg.Connection, stores: list[str], week: date, only_half: bool = True) -> list[dict]:
    if not stores:
        return []
    return conn.execute(f"""SELECT s.*, w.save_amount AS w_save FROM weekly_specials w JOIN store_products s ON s.id = w.store_product_id
                            WHERE w.store = ANY(%s) AND w.week_start = %s AND s.active
                            {"AND w.special_type = 'half_price'" if only_half else ""}""", (stores, week)).fetchall()


def weekly_specials(conn: psycopg.Connection, user_id: int, q: str | None = None, limit: int | None = None,
                    red_meat_left: float | None = None) -> dict:
    """This week's half-price products in the user's connected stores that fit the health rules: things the user buys first, then
    lean protein / produce, larger savings first. Red meat is dropped when the week's red-meat budget is used up."""
    stores = visible_stores(conn, user_id)
    day = sydney_today()
    week = specials_week(day)
    out = {"enabled": bool(P.enabled_stores()), "stores": stores, "week_start": week.isoformat(),
           "valid_to": date.fromordinal(week.toordinal() + 6).isoformat(), "items": [], "excluded": 0}
    if not stores:
        return out
    wanted = _user_terms(conn, user_id)
    qwords = [_stem(w) for w in WORD.findall((q or "").lower())]
    items = []
    for r in specials_rows(conn, stores, week):
        nw = _words(r["name"])
        if qwords and not _hits(qwords, nw):
            continue
        if _excluded(r["name"], r["category"]):
            out["excluded"] += 1
            continue
        flags = _flags(r["name"])
        if "红肉" in flags and red_meat_left is not None and red_meat_left <= 0:
            out["excluded"] += 1
            continue
        matched = sorted({label for label, words in wanted if _hits(words, nw)})
        v = offer_view(r)
        items.append((not matched, P.CATEGORY_RANK.get(r["category"], 4), "高嘌呤" in flags, "红肉" in flags,
                      -(v["save"] or 0), {**v, "category": r["category"], "flags": flags, "for": matched[:3]}))
    items.sort(key=lambda x: x[:5])
    out["items"] = [x[-1] for x in items[: max(1, min(limit or P.SPECIALS_LIMIT, 50))]]
    out["total"] = len(items)
    return out


def half_price_hits(conn: psycopg.Connection, user_id: int, foods: list[str]) -> dict[str, dict]:
    """food name -> the half-price offer it matches this week (for meal suggestions). One query, matched in memory."""
    stores = visible_stores(conn, user_id)
    rows = [r for r in specials_rows(conn, stores, specials_week(sydney_today())) if not _excluded(r["name"], r["category"])]
    if not rows:
        return {}
    products = conn.execute("SELECT * FROM grocery_products WHERE user_id = %s AND active", (user_id,)).fetchall()
    out = {}
    for food in dict.fromkeys(foods):
        prod = next((p for p in products if food.strip().lower() in {p["name"].lower(), *(m.lower() for m in p["matches"])}), None)
        for words in terms_for(food, prod):
            best = _pick([r for r in rows if _hits(words, _words(r["name"]))], words)
            if best:
                out[food] = offer_view(best)
                break
    return out


def red_meat_left(summary: dict | None) -> float:
    return rule_params.RED_MEAT_G_PER_WEEK - float((summary or {}).get("red_meat_g") or 0)
