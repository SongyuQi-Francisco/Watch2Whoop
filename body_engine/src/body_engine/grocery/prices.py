"""Prices the user's own AI looked up (docs/grocery-plan.md §9, migration 420). The server never visits store websites: the user's AI
reads the store pages it may read (or the user's screenshots) and writes what it saw with record_store_prices; we keep structure and
storage and do the arithmetic.

- Product pool (store_products): one row per physical product per store, de-duplicated by store + the store's product id (from the
  Coles / WWS product URL), else store + barcode, else store + normalised name and size. Pool rows hold product metadata only.
- Visibility (user_store_products): a user sees a pool product only after uploading or linking it themselves. Every read below joins
  that table, and a missing link raises the same LookupError as a missing product, so ids cannot be probed.
- Prices are per user (price_observations.user_id) and never expire; views carry their age. Purchases (grocery_purchases) record
  what was actually paid, where and whether on special; stats give the price curve, min / average / latest, how often it was on
  special, and a "dear / cheap / normal" verdict against the user's own history (thresholds in params.py).
The caller is responsible for authorization (writes only for the caller)."""
import math
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L
from . import links as L_, params as P
from .sync import offers as SO
from .sync import common as C

CURRENCY = "AUD"   # both supported stores are Australian
_SIZE_RE = re.compile(r"\s+")


# ---------------------------------------------------------------- helpers
def _tz(conn, user_id: int) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])


def _num(v) -> float | None:
    return None if v is None else float(v)


def _key(s: str | None) -> str:
    return _SIZE_RE.sub(" ", (s or "").strip().lower())


def name_key(name: str, size_text: str | None) -> str:
    """Normalised name + size for products without a store id or barcode: lower case, punctuation dropped, size as "500g"."""
    words = re.findall(r"[a-z0-9]+|[一-鿿]+", (name or "").lower())
    size, unit = C.parse_size(size_text)
    tail = f"{size:g}{unit}" if size else _key(size_text)
    return (" ".join(words) + ("|" + tail if tail else ""))[:200]


def _not_found() -> LookupError:
    return LookupError(L("没有这个商品（或不是你的）", "No such product (or it isn't yours)"))


def _connected(conn, user_id: int) -> list[str]:
    row = conn.execute("SELECT stores FROM grocery_settings WHERE user_id = %s", (user_id,)).fetchone()
    return [s for s in (row["stores"] if row else []) if s in P.STORES]


def _label(store: str | None) -> str | None:
    return P.STORES[store]["label"] if store in P.STORES else store


# ---------------------------------------------------------------- pool + links
def _clean_item(it: dict, connected: list[str], now: datetime, tz: ZoneInfo) -> dict:
    store = (it.get("store") or "").strip().lower()
    store = L_.store_key(store) or store
    if store not in P.STORES:
        raise ValueError(L(f"超市只能是 {', '.join(P.STORES)}", f"store must be one of {', '.join(P.STORES)}"))
    if store not in connected:
        raise ValueError(L(f"{_label(store)} 还没连接：先让用户在采购页「超市」里连接（或 set_stores）",
                           f"{_label(store)} is not connected yet: ask the user to connect it first (采购 → 超市, or set_stores)"))
    name = (it.get("name") or "").strip()
    if not 1 <= len(name) <= 160:
        raise ValueError(L("商品名 1–160 字", "name: 1–160 characters"))
    sku = None
    url = (it.get("url") or "").strip() or None
    if url:
        link = L_.parse_product_url(url)
        if link["store"] != store:
            raise ValueError(L(f"{name}：商品页是 {_label(link['store'])} 的，不是 {_label(store)}",
                               f"{name}: the product page is {_label(link['store'])}'s, not {_label(store)}'s"))
        url, sku = link["url"], link["sku"]
    barcode = re.sub(r"\s", "", it.get("barcode") or "") or None
    if barcode and not re.fullmatch(r"[0-9]{6,14}", barcode):
        raise ValueError(L(f"{name}：条码只能是 6–14 位数字", f"{name}: barcode must be 6–14 digits"))
    image = (it.get("image_url") or "").strip() or None
    if image and (not image.startswith("https://") or len(image) > 500):
        image = None   # a reference only; never downloaded
    price = it.get("price")
    if not isinstance(price, (int, float)) or isinstance(price, bool) or not 0 <= price <= 10000:
        raise ValueError(L(f"{name}：price 要是 0–10000 的数（每包，AUD）", f"{name}: price must be 0–10000 (per package, AUD)"))
    was = it.get("was_price")
    if was is not None and (not isinstance(was, (int, float)) or was < 0 or was > 10000):
        raise ValueError(L(f"{name}：was_price 不对", f"{name}: invalid was_price"))
    special = it.get("special") or None
    if special is not None and special not in P.SPECIAL_TYPES:
        raise ValueError(L(f"special 只能是 {', '.join(P.SPECIAL_TYPES)}", f"special must be one of {', '.join(P.SPECIAL_TYPES)}"))
    if special is None and was and was > price:
        special = C.classify_special(price, was, False, False, True)
    unit_price, uunit = it.get("unit_price"), it.get("unit_price_unit")
    if unit_price is not None and (not isinstance(unit_price, (int, float)) or unit_price < 0 or uunit not in ("kg", "L", "ea")):
        raise ValueError(L(f"{name}：unit_price 要带 unit_price_unit（kg / L / ea）", f"{name}: unit_price needs unit_price_unit (kg / L / ea)"))
    size_text = (it.get("size") or "").strip()[:40] or None
    pkg, pkg_unit = C.parse_size(size_text)
    if unit_price is None and pkg and price:   # per kg / L / piece from the size
        unit_price, uunit = round(price / pkg * (1000 if pkg_unit in ("g", "ml") else 1), 2), {"g": "kg", "ml": "L"}.get(pkg_unit, "ea")
    at = it.get("observed_at")
    if at:
        try:
            at = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(L(f"{name}：observed_at 用 ISO 时间", f"{name}: observed_at must be ISO 8601"))
        at = at if at.tzinfo else at.replace(tzinfo=tz)
        if at > now + timedelta(hours=P.PRICE_MAX_AGE_FUTURE_H):
            raise ValueError(L(f"{name}：observed_at 不能在未来", f"{name}: observed_at is in the future"))
    else:
        at = now
    source = it.get("source") or "ai"
    if source not in P.PRICE_SOURCES:
        raise ValueError(L(f"source 只能是 {', '.join(P.PRICE_SOURCES)}", f"source must be one of {', '.join(P.PRICE_SOURCES)}"))
    packages = it.get("packages")
    if packages is not None and (not isinstance(packages, (int, float)) or not 0 < packages <= 100):
        raise ValueError(L(f"{name}：packages 要是 0–100 的数", f"{name}: packages must be 0–100"))
    paid = it.get("paid_total")
    if paid is not None and (not isinstance(paid, (int, float)) or not 0 <= paid <= 100000):
        raise ValueError(L(f"{name}：paid_total 不对", f"{name}: invalid paid_total"))
    label = (it.get("special_label") or "").strip()[:60] or None
    return {"store": store, "name": name, "brand": (it.get("brand") or "").strip()[:60] or None, "size_text": size_text,
            "package_size": pkg, "package_unit": pkg_unit, "sku": sku, "url": url, "barcode": barcode, "image_url": image,
            "price": round(float(price), 2), "was_price": None if was is None else round(float(was), 2),
            "unit_price": unit_price, "unit_price_unit": uunit if unit_price is not None else None, "special": special,
            "special_label": label, "observed_at": at, "source": source, "for_item": (it.get("for_item") or "").strip()[:60] or None,
            "bought": bool(it.get("bought")), "packages": packages, "paid_total": paid,
            "name_key": name_key(name, size_text)}


def _find_pool(conn, x: dict) -> dict | None:
    if x["sku"]:
        row = conn.execute("SELECT * FROM store_products WHERE store = %s AND sku = %s", (x["store"], x["sku"])).fetchone()
        if row:
            return row
    if x["barcode"]:
        row = conn.execute("SELECT * FROM store_products WHERE store = %s AND barcode = %s ORDER BY sku NULLS LAST LIMIT 1",
                           (x["store"], x["barcode"])).fetchone()
        if row and (row["sku"] is None or x["sku"] is None):
            return row
    row = conn.execute("""SELECT * FROM store_products WHERE store = %s AND name_key = %s AND sku IS NULL
                            AND (barcode IS NULL OR barcode = %s) LIMIT 1""", (x["store"], x["name_key"], x["barcode"])).fetchone()
    return row


def upsert_pool(conn, x: dict) -> dict:
    """The one pool row for this product (created if new). Existing rows only get missing fields filled: what one user's AI wrote
    never overwrites another user's view of the product."""
    row = _find_pool(conn, x)
    if row is None:
        return conn.execute(
            """INSERT INTO store_products (store, sku, name, brand, size_text, package_size, package_unit, barcode, url, image_url,
                                           name_key, origin, active)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'user', TRUE) RETURNING *""",
            (x["store"], x["sku"], x["name"], x["brand"], x["size_text"], x["package_size"], x["package_unit"], x["barcode"],
             x["url"], x["image_url"], x["name_key"])).fetchone()
    fill = {k: x[k] for k in ("sku", "brand", "size_text", "package_size", "package_unit", "barcode", "url", "image_url")
            if x[k] is not None and row[k] is None}
    if fill.get("barcode") and row["sku"] is None and x["sku"] is None and conn.execute(
            "SELECT 1 FROM store_products WHERE store = %s AND barcode = %s AND sku IS NULL AND id <> %s",
            (x["store"], fill["barcode"], row["id"])).fetchone():
        fill.pop("barcode")   # another sku-less row already owns this barcode
    sets = ", ".join(f"{k} = %s" for k in fill)
    return conn.execute(f"UPDATE store_products SET {sets + ', ' if sets else ''}last_seen = greatest(last_seen, current_date), "
                        "updated_at = now() WHERE id = %s RETURNING *", (*fill.values(), row["id"])).fetchone()


def link(conn, user_id: int, store_product_id: int, source: str, match: str | None = None) -> None:
    m = [match] if match else []
    conn.execute("""INSERT INTO user_store_products (user_id, store_product_id, matches, source) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, store_product_id) DO UPDATE SET
                      matches = ARRAY(SELECT DISTINCT unnest(user_store_products.matches || EXCLUDED.matches))""",
                 (user_id, store_product_id, m, source))


def _linked(conn, user_id: int, store_product_id: int) -> dict:
    row = conn.execute("""SELECT s.*, u.matches, u.source AS link_source, u.linked_at FROM user_store_products u
                          JOIN store_products s ON s.id = u.store_product_id
                          WHERE u.user_id = %s AND u.store_product_id = %s""", (user_id, store_product_id)).fetchone()
    if row is None:
        raise _not_found()
    return row


# ---------------------------------------------------------------- record (the AI writes back what it found)
def record(conn: psycopg.Connection, user_id: int, items: list[dict]) -> dict:
    """Batch of prices the user's AI read from store pages / screenshots. Atomic: one bad item rejects the batch with the reason."""
    if not items:
        raise ValueError(L("至少给一样", "Give at least one item"))
    if len(items) > P.RECORD_MAX_ITEMS:
        raise ValueError(L(f"一次最多 {P.RECORD_MAX_ITEMS} 样：只查清单上的东西",
                           f"At most {P.RECORD_MAX_ITEMS} items per call: look up the list only"))
    tz = _tz(conn, user_id)
    now = datetime.now(timezone.utc)
    connected = _connected(conn, user_id)
    clean = [_clean_item(it, connected, now, tz) for it in items]
    out = []
    for x in clean:
        row = upsert_pool(conn, x)
        link(conn, user_id, row["id"], x["source"], x["for_item"])
        day = x["observed_at"].astimezone(tz).date()
        new = conn.execute(
            """INSERT INTO price_observations (store_product_id, observed_on, observed_at, user_id, source, price, was_price, unit_price,
                                               unit_price_unit, special_type, special_label, currency)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (user_id, store_product_id, observed_at, source) WHERE user_id IS NOT NULL DO NOTHING RETURNING id""",
            (row["id"], day, x["observed_at"], user_id, x["source"], x["price"], x["was_price"], x["unit_price"],
             x["unit_price_unit"], x["special"], x["special_label"], CURRENCY)).fetchone()
        r = {"store_product_id": row["id"], "store": x["store"], "name": row["name"], "price": x["price"],
             "special": x["special"], "saved": new is not None}
        if x["bought"]:
            packs = x["packages"] or 1
            paid = x["paid_total"] if x["paid_total"] is not None else round(x["price"] * packs, 2)
            r["purchase"] = add_purchase(conn, user_id, name=row["name"], store=x["store"], store_product_id=row["id"],
                                         bought_on=day, packages=packs, paid_total=paid, on_special=x["special"] is not None,
                                         was_price=x["was_price"], source="ai")
        out.append(r)
    return {"recorded": sum(r["saved"] for r in out), "items": out}


# ---------------------------------------------------------------- purchases
def add_purchase(conn, user_id: int, *, name: str, store: str | None, store_product_id: int | None, bought_on: date,
                 packages: float | None, paid_total: float | None, on_special: bool, was_price: float | None, source: str,
                 product_id: int | None = None, shopping_item_id: int | None = None, estimated: bool = False) -> dict:
    unit = round(paid_total / packages, 2) if paid_total is not None and packages else paid_total
    row = conn.execute(
        """INSERT INTO grocery_purchases (user_id, store_product_id, product_id, shopping_item_id, name, store, bought_on, packages,
                                          paid_total, unit_paid, paid_estimated, on_special, was_price, source)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (shopping_item_id) DO UPDATE SET paid_total = EXCLUDED.paid_total, unit_paid = EXCLUDED.unit_paid,
             paid_estimated = EXCLUDED.paid_estimated, on_special = EXCLUDED.on_special, was_price = EXCLUDED.was_price,
             store = EXCLUDED.store, store_product_id = EXCLUDED.store_product_id
           RETURNING *""",
        (user_id, store_product_id, product_id, shopping_item_id, name[:160], store, bought_on, packages, paid_total, unit,
         estimated, on_special, was_price, source)).fetchone()
    v = verdict(conn, user_id, row) if store_product_id and unit is not None and not estimated else None
    return {"id": row["id"], "unit_paid": _num(row["unit_paid"]), "on_special": row["on_special"], "verdict": v}


def remove_item_purchase(conn, user_id: int, shopping_item_id: int) -> None:
    conn.execute("DELETE FROM grocery_purchases WHERE user_id = %s AND shopping_item_id = %s", (user_id, shopping_item_id))


# ---------------------------------------------------------------- stats
def _points(conn, user_id: int, store_product_id: int) -> list[dict]:
    """This user's own price points for one product: observations and (non-estimated) purchases, oldest first."""
    tz = _tz(conn, user_id)
    pts = [{"at": r["observed_at"], "date": r["observed_at"].astimezone(tz).date().isoformat(), "price": float(r["price"]),
            "was": _num(r["was_price"]), "special": r["special_type"], "kind": "seen", "source": r["source"]}
           for r in conn.execute("""SELECT * FROM price_observations WHERE user_id = %s AND store_product_id = %s AND price IS NOT NULL
                                    ORDER BY observed_at""", (user_id, store_product_id))]
    pts += [{"at": datetime.combine(r["bought_on"], datetime.min.time(), tz), "date": r["bought_on"].isoformat(),
             "price": float(r["unit_paid"]), "was": _num(r["was_price"]), "special": "special" if r["on_special"] else None,
             "kind": "paid", "id": r["id"]}
            for r in conn.execute("""SELECT * FROM grocery_purchases WHERE user_id = %s AND store_product_id = %s
                                     AND unit_paid IS NOT NULL AND NOT paid_estimated ORDER BY bought_on""", (user_id, store_product_id))]
    pts.sort(key=lambda p: p["at"])
    return pts


def _verdict_from(price: float, on_special: bool, refs: list[dict], day: date) -> str | None:
    since = day - timedelta(days=P.VERDICT_HISTORY_DAYS)
    hist = [p["price"] for p in refs if date.fromisoformat(p["date"]) >= since]
    if len(hist) < P.VERDICT_MIN_POINTS:
        return None
    avg = sum(hist) / len(hist)
    recent = [p["price"] for p in refs if p["special"] and date.fromisoformat(p["date"]) >= day - timedelta(days=P.VERDICT_SPECIAL_DAYS)]
    best_special = min(recent) if recent else None
    if price <= avg * (1 - P.VERDICT_CHEAP_PCT) or (best_special is not None and price <= best_special * P.VERDICT_SPECIAL_CHEAP):
        return "cheap"
    if price >= avg * (1 + P.VERDICT_DEAR_PCT) or (best_special is not None and not on_special
                                                   and price >= best_special * P.VERDICT_SPECIAL_DEAR):
        return "dear"
    return "normal"


def verdict(conn, user_id: int, purchase: dict) -> str | None:
    refs = [p for p in _points(conn, user_id, purchase["store_product_id"]) if not (p["kind"] == "paid" and p["id"] == purchase["id"])]
    return _verdict_from(float(purchase["unit_paid"]), purchase["on_special"], refs, purchase["bought_on"])


VERDICT_LABEL = {"cheap": ("买得便宜", "Good price"), "dear": ("买贵了", "Paid more"), "normal": ("正常", "Normal")}


def verdict_label(v: str | None) -> str | None:
    return L(*VERDICT_LABEL[v]) if v in VERDICT_LABEL else None


def stats(conn: psycopg.Connection, user_id: int, store_product_id: int) -> dict:
    """Price curve, min / average / latest, specials (how many, how often) and the last purchase with its verdict."""
    row = _linked(conn, user_id, store_product_id)
    pts = _points(conn, user_id, store_product_id)
    prices = [p["price"] for p in pts]
    special_days = sorted({p["date"] for p in pts if p["special"]})
    gaps = [(date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in zip(special_days, special_days[1:])]
    last_buy = conn.execute("""SELECT * FROM grocery_purchases WHERE user_id = %s AND store_product_id = %s
                               ORDER BY bought_on DESC, id DESC LIMIT 1""", (user_id, store_product_id)).fetchone()
    lb = None
    if last_buy:
        v = verdict(conn, user_id, last_buy) if last_buy["unit_paid"] is not None and not last_buy["paid_estimated"] else None
        lb = {"date": last_buy["bought_on"].isoformat(), "unit_paid": _num(last_buy["unit_paid"]), "on_special": last_buy["on_special"],
              "estimated": last_buy["paid_estimated"], "verdict": v, "verdict_label": verdict_label(v)}
    latest = pts[-1] if pts else None
    today = datetime.now(_tz(conn, user_id)).date()
    return {**product_view(row), "points": [{k: p.get(k) for k in ("date", "price", "was", "special", "kind")} for p in pts],
            "min": min(prices) if prices else None, "avg": round(sum(prices) / len(prices), 2) if prices else None,
            "latest": latest["price"] if latest else None,
            "latest_age_days": (today - date.fromisoformat(latest["date"])).days if latest else None,
            "specials": {"count": len(special_days), "share": round(len(special_days) / len({p["date"] for p in pts}), 2) if pts else None,
                         "every_days": round(sum(gaps) / len(gaps)) if gaps else None},
            "last_purchase": lb}


# ---------------------------------------------------------------- views / search (own products only)
def product_view(row: dict) -> dict:
    return {"id": row["id"], "store": row["store"], "store_label": _label(row["store"]), "name": row["name"], "brand": row["brand"],
            "size": row["size_text"], "barcode": row["barcode"], "url": row["url"], "image_url": row["image_url"],
            "matches": row.get("matches") or []}


def _with_latest(conn, user_id: int, stores: list[str] | None = None, q: str | None = None, ids: list[int] | None = None) -> list[dict]:
    """This user's linked products with their own latest observation (LATERAL: one index probe each)."""
    where, args = ["u.user_id = %s"], [user_id]
    if stores is not None:
        where.append("s.store = ANY(%s)")
        args.append(stores)
    if ids is not None:
        where.append("s.id = ANY(%s)")
        args.append(ids)
    words = [w for w in re.findall(r"[a-z0-9]+|[一-鿿]+", (q or "").lower()) if w]
    for w in words:
        where.append("(s.name ILIKE %s OR %s = ANY(u.matches) OR s.barcode = %s)")
        args += [f"%{w}%", w, w]
    return conn.execute(
        f"""SELECT s.*, u.matches, o.price AS o_price, o.was_price AS o_was, o.unit_price AS o_unit, o.unit_price_unit AS o_unit_unit,
                   o.special_type AS o_special, o.special_label AS o_label, o.observed_at AS o_at, o.source AS o_source
            FROM user_store_products u JOIN store_products s ON s.id = u.store_product_id
            LEFT JOIN LATERAL (SELECT * FROM price_observations p WHERE p.user_id = %s AND p.store_product_id = s.id
                               ORDER BY p.observed_at DESC LIMIT 1) o ON TRUE
            WHERE {' AND '.join(where)} ORDER BY s.store, s.name""", [user_id, *args]).fetchall()   # LATERAL's %s first


def offer_of(row: dict, today: date, tz: ZoneInfo) -> dict | None:
    if row["o_price"] is None:
        return None
    age = (today - row["o_at"].astimezone(tz).date()).days
    return {"store_product_id": row["id"], "store": row["store"], "label": _label(row["store"]), "name": row["name"],
            "size": row["size_text"], "price": float(row["o_price"]), "was_price": _num(row["o_was"]),
            "unit_price": _num(row["o_unit"]), "unit_price_unit": row["o_unit_unit"], "special": row["o_special"],
            "special_label": row["o_label"], "half_price": row["o_special"] == "half_price",
            "save": round(float(row["o_was"]) - float(row["o_price"]), 2) if row["o_was"] and row["o_special"] else None,
            "url": row["url"], "package_size": _num(row["package_size"]), "package_unit": row["package_unit"],
            "observed_at": row["o_at"].isoformat(), "age_days": age, "stale": age > P.PRICE_STALE_DAYS, "source": row["o_source"]}


def list_products(conn: psycopg.Connection, user_id: int, q: str | None = None) -> list[dict]:
    tz = _tz(conn, user_id)
    today = datetime.now(tz).date()
    return [{**product_view(r), "offer": offer_of(r, today, tz)} for r in _with_latest(conn, user_id, q=q)]


# ---------------------------------------------------------------- the shopping list: which store, totals, savings
def _match(cands: list[dict], name: str, product: dict | None) -> list[dict]:
    if product and product.get("store_sku"):
        hit = [c for c in cands if c["sku"] == product["store_sku"] and c["store"] == L_.parse_product_url(product["product_url"])["store"]]
        if hit:
            return hit
    keys = {_key(name)} | ({_key(product["name"])} | {_key(m) for m in product.get("matches") or []} if product else set())
    hit = [c for c in cands if keys & {_key(m) for m in c["matches"] or []}]
    if hit:
        return hit
    for words in SO.terms_for(name, product):
        hit = [c for c in cands if SO._hits(words, SO._words(c["name"])) and not SO._processed(c["name"], words)]
        if hit:
            return hit
    return []


def best_offer(cands: list[dict], item: dict, product: dict | None, today: date, tz: ZoneInfo) -> tuple[dict | None, list[dict]]:
    """Cheapest priced match for what the item needs (whole packages); ties go to the special. Returns (best, others)."""
    priced = []
    for c in _match(cands, item["name"], product):
        o = offer_of(c, today, tz)
        if o is None:
            continue
        packs, cost, _ = SO.item_cost(o, item["buy_qty"], item["unit"])
        save = round(packs * o["save"], 2) if o["special"] and o["save"] else None   # any special with a was-price, not only half price
        priced.append({**o, "packages": packs, "cost": cost, "save_total": save})
    if not priced:
        return None, []
    priced.sort(key=lambda o: (o["cost"] if o["cost"] is not None else math.inf, o["special"] is None, o["age_days"]))
    best = priced[0]
    others = [o for o in priced[1:] if o["store"] != best["store"]][:1]
    return best, others


def price_list(conn, user_id: int, items: list[dict], products: dict) -> dict | None:
    """Put this user's own recorded prices on the unbought list lines (connected stores only). Returns the list summary:
    which store, totals per store, savings on specials, and what still needs a price (missing / stale). None = nothing recorded."""
    stores = _connected(conn, user_id)
    if not stores:
        return None
    cands = _with_latest(conn, user_id, stores=stores)
    tz = _tz(conn, user_id)
    today = datetime.now(tz).date()
    by_store: dict[str, dict] = {}
    half = {"items": 0, "save": {}}
    missing, stale, savings = [], [], 0.0
    for i in items:
        if i["bought"]:
            continue
        best, others = best_offer(cands, i, products.get(i["product_id"]), today, tz) if cands else (None, [])
        if best is None:
            missing.append(i["name"])
            continue
        i["offer"] = best
        if others:
            i["alt_offer"] = {k: others[0][k] for k in ("store", "label", "price", "cost", "special", "age_days")}
        prod = products.get(i["product_id"])
        own_newer = (i["est_cost"] is not None and prod is not None and prod["price_on"] is not None
                     and prod["price_on"] > date.fromisoformat(best["observed_at"][:10]))
        if best["cost"] is not None and not own_newer:   # a newer own price on the library product (paid / label) stays
            i["est_cost"], i["currency"], i["est_from"] = best["cost"], CURRENCY, best["store"]
        if best["cost"] is not None:
            s = by_store.setdefault(best["store"], {"label": best["label"], "items": 0, "total": 0.0})
            s["items"] += 1
            s["total"] = round(s["total"] + best["cost"], 2)
        if best["stale"]:
            stale.append(i["name"])
        if best["save_total"]:
            savings += best["save_total"]
        if best["half_price"]:
            half["items"] += 1
            if best["save_total"]:
                half["save"][CURRENCY] = round(half["save"].get(CURRENCY, 0) + best["save_total"], 2)
        if best["url"]:   # the recorded product page replaces that store's search link (no page: the search link stays)
            links = [lk for lk in i.get("links", []) if not (lk["store"] == best["store"] and lk["kind"] == "search")]
            if not any(lk["store"] == best["store"] for lk in links):
                links.insert(0, {"store": best["store"], "label": best["label"], "kind": "product", "url": best["url"]})
            i["links"] = links
    return {"by_store": by_store, "savings": {CURRENCY: round(savings, 2)} if savings else {}, "half_price": half,
            "missing": missing, "stale": stale}


def offer_for_item(conn, user_id: int, item_row: dict, product: dict | None) -> dict | None:
    stores = _connected(conn, user_id)
    if not stores:
        return None
    tz = _tz(conn, user_id)
    item = {"name": item_row["name"], "buy_qty": float(item_row["buy_qty"]), "unit": item_row["unit"]}
    best, _ = best_offer(_with_latest(conn, user_id, stores=stores), item, product, datetime.now(tz).date(), tz)
    return best


def on_bought(conn, user_id: int, item_row: dict, product: dict | None, paid: float | None) -> dict | None:
    """Ticking a list line: record the purchase (the price the user gave, else the recorded store price, marked estimated)."""
    off = offer_for_item(conn, user_id, item_row, product)
    packs = float(item_row["packages"] or (off or {}).get("packages") or 1)
    if paid is None and (off is None or off["cost"] is None):
        total, estimated = None, False
    elif paid is None:
        total, estimated = off["cost"], True
    else:
        total, estimated = float(paid), False
    tz = _tz(conn, user_id)
    fresh = off is not None and off["age_days"] <= P.PRICE_STALE_DAYS
    on_special = bool(fresh and off["special"] and (paid is None or abs(float(paid) / packs - off["price"]) <= 0.02 * off["price"] + 0.01))
    return add_purchase(conn, user_id, name=item_row["name"], store=(off or {}).get("store") or L_.store_key(item_row["store"]) or None,
                        store_product_id=(off or {}).get("store_product_id"), bought_on=datetime.now(tz).date(), packages=packs,
                        paid_total=total, on_special=on_special, was_price=(off or {}).get("was_price") if on_special else None,
                        source="list", product_id=item_row["product_id"], shopping_item_id=item_row["id"], estimated=estimated)


def purchases_for_items(conn, user_id: int, item_ids: list[int]) -> dict[int, dict]:
    out = {}
    for r in conn.execute("SELECT * FROM grocery_purchases WHERE user_id = %s AND shopping_item_id = ANY(%s)", (user_id, item_ids)):
        v = verdict(conn, user_id, r) if r["store_product_id"] and r["unit_paid"] is not None and not r["paid_estimated"] else None
        out[r["shopping_item_id"]] = {"store": r["store"], "unit_paid": _num(r["unit_paid"]), "on_special": r["on_special"],
                                      "verdict": v, "verdict_label": verdict_label(v)}
    return out


# ---------------------------------------------------------------- context for the user's AI
GUARDRAILS = ("只查 needs_price 里的商品（这个用户自己清单上的），一次最多 {n} 样，不要整站抓取、不要翻目录页。",
              "用已连接超市的商品页 / 搜索页；打不开、要登录或被拦截（403 / 验证码），就告诉用户，改用截图，不要尝试绕过。",
              "查到后用 record_store_prices 一次写回（带商品页 URL、规格、价格、是否特价和原价、查询时间）。")


def shopping_context(conn: psycopg.Connection, user_id: int, day: date | None = None) -> dict:
    from . import pantry_auto, store   # store imports this module
    lst = store.get_list(conn, user_id, day, with_pantry=True)
    connected = _connected(conn, user_id)
    hc = conn.execute("SELECT value FROM assistant_profile WHERE user_id = %s AND key = 'health_constraints'", (user_id,)).fetchone()
    summary = conn.execute("SELECT summary FROM shopping_lists WHERE user_id = %s AND week_start = %s",
                           (user_id, lst["week_start"])).fetchone()
    from ..nutrition import constraints as diet_constraints
    items, needs = [], []
    for i in lst["items"]:
        if i["bought"]:
            continue
        o = i.get("offer")
        status = "missing" if o is None else ("stale" if o.get("stale") else "ok")
        row = {"id": i["id"], "name": i["name"], "need": i["qty_label"], "status": status}
        if o:
            row.update(store=o["label"], price=o["price"], special=o.get("special"), age_days=o.get("age_days"))
        items.append(row)
        if status != "ok" and len(needs) < P.CONTEXT_NEEDS_MAX:
            needs.append(i["name"])
    return {
        "week_start": lst["week_start"], "plan": lst["plan"], "items": items, "needs_price": needs,
        "connected_stores": [{"store": s, "label": P.STORES[s]["label"], "search": P.STORES[s]["search"]} for s in connected],
        "pantry": [f"{p['name']} {p['label']}" for p in lst.get("pantry", [])],
        "health": {"constraints": (hc["value"][:300] if hc else None),
                   "red_meat_g_planned": (summary["summary"] or {}).get("red_meat_g") if summary else None,
                   # the user's own limits (nutrition/constraints.py, issue #76); [] = none
                   "diet_constraints": [c["text"] for c in diet_constraints.listing(conn, user_id)["constraints"]],
                   "warnings": [w["label"] for w in lst["warnings"]]},
        "rules": [g.format(n=P.CONTEXT_NEEDS_MAX) for g in GUARDRAILS],
        # usual-item candidates from this user's own purchases (issue #103): ask before set_staple, never mark on your own
        "staple_suggestions": store.staple_suggestions(conn, user_id),
        # best before within a few days or past (issue #104): suggest using them up; pantry_auto = the switch
        "expiring": pantry_auto.expiring(conn, user_id),
        "pantry_auto": pantry_auto.enabled(conn, user_id),
    }
