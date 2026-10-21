"""Usual items / 常买 (issue #103, docs/grocery-plan.md §3, migration 500).

- A product is a usual item only after the user confirms (set_staple); suggestions come from this user's own purchase history
  (grocery_purchases): bought at least STAPLE_SUGGEST_MIN times in the last STAPLE_SUGGEST_WEEKS weeks.
- next_due = last purchase + every_days (never bought -> due now). generate_shopping_list adds the usual items that fall due by the
  end of the list's week, unless the pantry already holds one purchase's worth (store.generate).
The caller is responsible for authorization (writes only for the caller)."""
import statistics
from datetime import date, timedelta

import psycopg

from . import params as P


def _matches(product: dict, name: str) -> bool:
    k = (name or "").strip().lower()
    return k == product["name"].strip().lower() or k in {m.strip().lower() for m in product["matches"] or []}


def purchase_dates(conn: psycopg.Connection, user_id: int, products: list[dict], since: date | None = None) -> dict[int, list[date]]:
    """{product_id: sorted purchase dates} from grocery_purchases (by product_id, else by name / matches; one per day)."""
    rows = conn.execute("""SELECT product_id, name, bought_on FROM grocery_purchases WHERE user_id = %s
                           AND (%s::date IS NULL OR bought_on >= %s) ORDER BY bought_on""", (user_id, since, since)).fetchall()
    by_id = {p["id"]: p for p in products}
    out: dict[int, set] = {}
    for r in rows:
        pid = r["product_id"] if r["product_id"] in by_id else next((p["id"] for p in products if _matches(p, r["name"])), None)
        if pid is not None:
            out.setdefault(pid, set()).add(r["bought_on"])
    return {k: sorted(v) for k, v in out.items()}


def typical_gap(dates: list[date]) -> int | None:
    gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
    if not gaps:
        return None
    return max(P.STAPLE_MIN_DAYS, min(P.STAPLE_MAX_DAYS, round(statistics.median(gaps))))


def view(product: dict, dates: list[date], today: date) -> dict:
    """The staple fields of a product view (only for usual items)."""
    last = dates[-1] if dates else None
    every = product["staple_every_days"] or P.STAPLE_DEFAULT_EVERY_DAYS
    nxt = last + timedelta(days=every) if last else today
    return {"every_days": every, "qty": float(product["staple_qty"] or 1), "unit": product["staple_unit"] or "pack",
            "last_bought": last.isoformat() if last else None, "next_due": nxt.isoformat(), "due": nxt <= today}


def suggestions(conn: psycopg.Connection, user_id: int, products: list[dict], today: date) -> list[dict]:
    """Products bought often lately that are not usual items yet (most often first). Only this user's purchases."""
    since = today - timedelta(weeks=P.STAPLE_SUGGEST_WEEKS)
    hist = purchase_dates(conn, user_id, products, since)
    out = []
    for p in products:
        ds = hist.get(p["id"], [])
        if p["staple"] or len(ds) < P.STAPLE_SUGGEST_MIN:
            continue
        out.append({"product_id": p["id"], "name": p["name"], "times": len(ds), "weeks": P.STAPLE_SUGGEST_WEEKS,
                    "every_days": typical_gap(ds) or P.STAPLE_DEFAULT_EVERY_DAYS, "last_bought": ds[-1].isoformat()})
    out.sort(key=lambda s: (-s["times"], s["name"]))
    return out[:P.STAPLE_SUGGEST_MAX]


def due(conn: psycopg.Connection, user_id: int, products: list[dict], week_end: date, today: date) -> list[dict]:
    """Usual items whose next purchase falls on or before week_end: [{product, need, unit}] (need in the product's unit)."""
    staples = [p for p in products if p["staple"]]
    if not staples:
        return []
    hist = purchase_dates(conn, user_id, staples)
    out = []
    for p in staples:
        v = view(p, hist.get(p["id"], []), today)
        if date.fromisoformat(v["next_due"]) > week_end:
            continue
        size, punit = (float(p["package_size"]), p["package_unit"]) if p["package_size"] and p["package_unit"] else (None, None)
        if v["unit"] == "pack":
            need, unit = (v["qty"] * size, punit) if size else (v["qty"], "pcs")
        else:
            need, unit = v["qty"], v["unit"]
        out.append({"product": p, "need": need, "unit": unit, "next_due": v["next_due"]})
    return out


def check(data: dict) -> dict:
    """set_staple arguments -> columns (None = keep / default)."""
    out = {}
    every, qty, unit = data.get("every_days"), data.get("qty"), data.get("unit")
    if every is not None:
        if not isinstance(every, int) or not P.STAPLE_MIN_DAYS <= every <= P.STAPLE_MAX_DAYS:
            raise ValueError(f"every_days 要是 {P.STAPLE_MIN_DAYS}–{P.STAPLE_MAX_DAYS} 的整数（天）")
        out["staple_every_days"] = every
    if qty is not None:
        if not isinstance(qty, (int, float)) or not 0 < qty <= 5000:
            raise ValueError("qty 要是 > 0 的数")
        out["staple_qty"] = qty
    if unit is not None:
        if unit not in P.STAPLE_UNITS:
            raise ValueError("unit 只能是 pack / g / ml / pcs")
        out["staple_unit"] = unit
    return out

