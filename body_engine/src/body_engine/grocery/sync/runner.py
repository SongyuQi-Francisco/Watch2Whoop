"""grocery-sync run / status / purge (CLI `body-engine grocery-sync`, docs/grocery-store-data-plan.md §8).

run: for each enabled store, crawl through the polite client and upsert store_products, one price_observation per product per
Sydney day, and this specials week's weekly_specials. Commits after every page, so a stopped run keeps what it has. A complete run
marks products it did not see as inactive and drops this week's specials it did not see again. Blocked / Unclear stop the store and
are recorded (status blocked / unclear); nothing tries to get around them."""
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from . import coles, params as P, wws
from .http import Blocked, Fetcher, Unclear

FETCHERS = {"coles": (coles, P.COLES_BASE), "woolworths": (wws, P.WWS_BASE)}
COLS = ("name", "brand", "size_text", "package_size", "package_unit", "barcode", "category", "url", "price", "was_price",
        "unit_price", "unit_price_unit", "special_type", "special_label", "save_amount", "multibuy_qty", "multibuy_unit_price")


def sydney_today() -> date:
    return datetime.now(ZoneInfo(P.TZ)).date()


def specials_week(day: date) -> date:
    """The Wednesday that starts the specials week containing `day`."""
    return day - timedelta(days=(day.weekday() - P.SPECIALS_WEEKDAY) % 7)


def _last_ok(conn, store: str):
    return conn.execute("SELECT * FROM grocery_sync_runs WHERE store = %s AND status = 'ok' ORDER BY started_at DESC LIMIT 1",
                        (store,)).fetchone()


def _save(conn, run_id: int, day: date, week: date, cat: str, products: list[dict]) -> tuple[int, int, int]:
    specials = half = 0
    for p in products:
        vals = {**{k: p.get(k) for k in COLS}, "category": cat}
        row = conn.execute(
            f"""INSERT INTO store_products (store, sku, {', '.join(COLS)}, first_seen, last_seen, active)
                VALUES (%s, %s, {', '.join(['%s'] * len(COLS))}, %s, %s, TRUE)
                ON CONFLICT (store, sku) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in COLS if c != 'category')},
                  category = coalesce(store_products.category, EXCLUDED.category), last_seen = EXCLUDED.last_seen,
                  active = TRUE, updated_at = now()
                RETURNING id""",
            (p["store"], p["sku"], *vals.values(), day, day)).fetchone()
        conn.execute("""INSERT INTO price_observations (store_product_id, observed_on, price, was_price, unit_price, special_type, run_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (store_product_id, observed_on) DO UPDATE SET price = EXCLUDED.price,
                          was_price = EXCLUDED.was_price, unit_price = EXCLUDED.unit_price, special_type = EXCLUDED.special_type,
                          run_id = EXCLUDED.run_id""",
                     (row["id"], day, p.get("price"), p.get("was_price"), p.get("unit_price"), p.get("special_type"), run_id))
        if p.get("special_type"):
            specials += 1
            half += p["special_type"] == "half_price"
            conn.execute("""INSERT INTO weekly_specials (store, week_start, store_product_id, special_type, price, was_price,
                              save_amount, label, valid_to)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (store, week_start, store_product_id) DO UPDATE SET special_type = EXCLUDED.special_type,
                              price = EXCLUDED.price, was_price = EXCLUDED.was_price, save_amount = EXCLUDED.save_amount,
                              label = EXCLUDED.label""",
                         (p["store"], week, row["id"], p["special_type"], p.get("price"), p.get("was_price"),
                          p.get("save_amount"), p.get("special_label"), week + timedelta(days=6)))
        else:   # no longer on special this week (seen again without a promotion)
            conn.execute("DELETE FROM weekly_specials WHERE store = %s AND week_start = %s AND store_product_id = %s",
                         (p["store"], week, row["id"]))
    return len(products), specials, half


def run_store(conn: psycopg.Connection, store: str, *, force: bool = False, max_pages: int | None = None,
              fetcher: Fetcher | None = None, day: date | None = None, log=print) -> dict:
    if store not in FETCHERS:
        raise ValueError(f"不认识的超市：{store}（可用 {', '.join(FETCHERS)}）")
    if store not in P.enabled_stores():
        return {"store": store, "status": "disabled", "note": f"没开：{P.ENV_STORES} 里没有 {store}"}
    contact = P.contact()
    if not contact:
        return {"store": store, "status": "disabled", "note": f"没设 {P.ENV_CONTACT}（写进 User-Agent，必填）"}
    day = day or sydney_today()
    last = _last_ok(conn, store)
    if last and not force and last["started_at"].astimezone(ZoneInfo(P.TZ)).date() > day - timedelta(days=P.MIN_DAYS_BETWEEN_RUNS):
        return {"store": store, "status": "skipped", "note": f"{last['started_at']:%Y-%m-%d} 已经跑过（--force 可以重跑）"}
    module, base = FETCHERS[store]
    fetcher = fetcher or Fetcher(base, contact)
    week = specials_week(day)
    run_id = conn.execute("INSERT INTO grocery_sync_runs (store, status) VALUES (%s, 'running') RETURNING id", (store,)).fetchone()["id"]
    conn.commit()
    t0, seen, pages, specials, half, status, error = time.monotonic(), set(), 0, 0, 0, "ok", None
    try:
        for cat, page, products in module.crawl(fetcher, max_pages=max_pages):
            new = [p for p in products if p["sku"] not in seen]
            seen.update(p["sku"] for p in new)
            _, s, h = _save(conn, run_id, day, week, cat, new)
            pages, specials, half = pages + 1, specials + s, half + h
            conn.execute("UPDATE grocery_sync_runs SET requests = %s, pages = %s, products = %s, specials = %s, half_price = %s "
                         "WHERE id = %s", (fetcher.requests, pages, len(seen), specials, half, run_id))
            conn.commit()
            log(f"{store} {cat} p{page}: {len(new)} 个商品（累计 {len(seen)}，特价 {specials}，半价 {half}，请求 {fetcher.requests}）")
        if max_pages is not None:
            status = "partial"
    except Blocked as e:
        conn.rollback()
        status, error = "blocked", str(e)
    except Unclear as e:
        conn.rollback()
        status, error = "unclear", str(e)
    except Exception as e:  # network gave up after retries, database error...
        conn.rollback()
        status, error = "failed", f"{e.__class__.__name__}: {e}"
    if status == "ok":   # a complete run: what it did not see is gone / no longer on special
        conn.execute("UPDATE store_products SET active = FALSE, updated_at = now() WHERE store = %s AND active AND NOT (sku = ANY(%s))",
                     (store, list(seen)))
        conn.execute("""DELETE FROM weekly_specials w USING store_products s WHERE w.store_product_id = s.id AND w.store = %s
                          AND w.week_start = %s AND NOT (s.sku = ANY(%s))""", (store, week, list(seen)))
    conn.execute("""UPDATE grocery_sync_runs SET status = %s, error = %s, finished_at = now(), requests = %s, pages = %s,
                      products = %s, specials = %s, half_price = %s WHERE id = %s""",
                 (status, error, fetcher.requests, pages, len(seen), specials, half, run_id))
    conn.commit()
    return {"store": store, "status": status, "error": error, "requests": fetcher.requests, "pages": pages,
            "products": len(seen), "specials": specials, "half_price": half, "seconds": round(time.monotonic() - t0, 1),
            "week_start": week.isoformat()}


def run(conn: psycopg.Connection, stores: list[str] | None = None, **kw) -> list[dict]:
    stores = stores or P.enabled_stores()
    if not stores:
        return [{"status": "disabled", "note": f"grocery-sync 没开（设 {P.ENV_STORES}=coles 和 {P.ENV_CONTACT}）"}]
    return [run_store(conn, s, **kw) for s in stores]


def status(conn: psycopg.Connection) -> dict:
    day = sydney_today()
    out = {"enabled": P.enabled_stores(), "contact_set": bool(P.contact()), "week_start": specials_week(day).isoformat(),
           "stores": []}
    for store in FETCHERS:
        last = conn.execute("SELECT * FROM grocery_sync_runs WHERE store = %s ORDER BY started_at DESC LIMIT 1", (store,)).fetchone()
        ok = _last_ok(conn, store)
        counts = conn.execute("""SELECT count(*) FILTER (WHERE active) AS products,
                                   (SELECT count(*) FROM weekly_specials w WHERE w.store = %s AND w.week_start = %s) AS specials,
                                   (SELECT count(*) FROM weekly_specials w WHERE w.store = %s AND w.week_start = %s
                                      AND w.special_type = 'half_price') AS half_price
                                 FROM store_products WHERE store = %s""",
                              (store, specials_week(day), store, specials_week(day), store)).fetchone()
        stale = store in out["enabled"] and (ok is None or ok["started_at"].astimezone(ZoneInfo(P.TZ)).date()
                                              < day - timedelta(days=P.STALE_DAYS))
        out["stores"].append({
            "store": store, "enabled": store in out["enabled"], **counts, "stale": stale,
            "last_run": None if last is None else {
                "status": last["status"], "started_at": last["started_at"].isoformat(),
                "finished_at": last["finished_at"].isoformat() if last["finished_at"] else None,
                "seconds": round((last["finished_at"] - last["started_at"]).total_seconds()) if last["finished_at"] else None,
                "requests": last["requests"], "pages": last["pages"], "products": last["products"],
                "specials": last["specials"], "half_price": last["half_price"], "error": last["error"]},
            "last_ok": ok["started_at"].isoformat() if ok else None})
    return out


def purge(conn: psycopg.Connection, store: str | None = None) -> dict:
    """Kill switch: delete every scraped row (products, price history, specials, run log) for one store or all."""
    if store is not None and store not in FETCHERS:
        raise ValueError(f"不认识的超市：{store}")
    where, args = ("WHERE store = %s", (store,)) if store else ("", ())
    n = conn.execute(f"DELETE FROM store_products {where}", args).rowcount   # cascades to price_observations / weekly_specials
    conn.execute(f"DELETE FROM weekly_specials {where}", args)
    runs = conn.execute(f"DELETE FROM grocery_sync_runs {where}", args).rowcount
    conn.commit()
    return {"store": store or "all", "products_deleted": n, "runs_deleted": runs}
