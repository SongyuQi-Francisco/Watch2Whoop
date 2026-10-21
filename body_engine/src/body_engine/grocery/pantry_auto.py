"""The pantry goes down when meals are logged; best-before reminders (issue #104, docs/grocery-plan.md §3, migration 510).

- Every food entry written through sparky_write.log_food (log_food, quick meal templates, recipes) can take from the pantry:
  a logged food / ingredient whose name equals a pantry item's name, or the name / `matches` of the product it is linked to, is
  deducted, converted like the shopping list (g <-> ml 1:1, pieces via the product's piece_g). Names that do not match, and
  amounts that do not parse or convert, are skipped silently. Never below 0; a row that reaches 0 is removed (as 「用完了」).
- Rows closest to their best before go first; rows without one last.
- One pantry_deductions row per pantry row touched, keyed by the SparkyFitness entry id: undo_meal_log / undo_sparky_entry, the
  Today page's delete and rollback_write of the entry put it back (restore). AI writes are also in ai_write_log (kind pantry), so
  rollback_write on that row restores the pantry and leaves the entry.
- Per-user switch grocery_settings.pantry_auto (NULL = params.PANTRY_AUTO_DEFAULT); off = nothing is deducted.
The caller is responsible for authorization: everything here reads and writes only `user_id`'s rows."""
import re
from datetime import date, datetime

import psycopg
from psycopg.types.json import Jsonb

from ..i18n import L as Li
from . import params as P
from . import store

_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(" + "|".join(sorted(map(re.escape, P.AMOUNT_UNITS), key=len, reverse=True))
                     + r")(?![A-Za-z])", re.IGNORECASE)


# ---------------------------------------------------------------- switch
def enabled(conn: psycopg.Connection, user_id: int) -> bool:
    row = conn.execute("SELECT pantry_auto FROM grocery_settings WHERE user_id = %s", (user_id,)).fetchone()
    return P.PANTRY_AUTO_DEFAULT if row is None or row["pantry_auto"] is None else row["pantry_auto"]


def status(conn: psycopg.Connection, user_id: int) -> dict:
    on = enabled(conn, user_id)
    return {"enabled": on, "default": P.PANTRY_AUTO_DEFAULT,
            "label": Li("记餐自动扣存货：开", "Logged meals come off the pantry: on") if on
            else Li("记餐自动扣存货：关", "Logged meals come off the pantry: off")}


def set_enabled(conn: psycopg.Connection, user_id: int, on: bool) -> dict:
    conn.execute("""INSERT INTO grocery_settings (user_id, pantry_auto) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET pantry_auto = EXCLUDED.pantry_auto, updated_at = now()""",
                 (user_id, bool(on)))
    return status(conn, user_id)


# ---------------------------------------------------------------- matching
def parse_amount(amount: str | None) -> tuple[float, str] | None:
    """「2 个」 -> (2, pcs); 「1 碗（约 250g 熟饭）」 -> (250, g); 「1 份」 -> None. The first amount with a known unit wins."""
    m = _AMOUNT.search(amount or "")
    if not m:
        return None
    factor, unit = P.AMOUNT_UNITS[m.group(2).lower()]
    q = float(m.group(1)) * factor
    return (q, unit) if q > 0 else None


def _name(food: str) -> str:
    """Compare key: without a trailing note in brackets (「鸡蛋（水煮）」 -> 鸡蛋)."""
    return store._key(re.sub(r"\s*[（(][^（）()]*[）)]\s*$", "", food or ""))


def _candidates(rows: list[dict], products: dict[int, dict], food: str) -> list[dict]:
    k = _name(food)
    if not k:
        return []
    pids = {pid for pid, p in products.items() if k == store._key(p["name"]) or k in {store._key(m) for m in p["matches"] or []}}
    out = [r for r in rows if store._key(r["name"]) == k or r["product_id"] in pids]
    out.sort(key=lambda r: (r["best_before"] is None, r["best_before"] or date.max, r["id"]))   # soonest best before first
    return out


def _piece_g(row: dict, products: dict[int, dict], food: str) -> float | None:
    p = products.get(row["product_id"])
    if p is None:
        p = next((x for x in products.values() if store._key(x["name"]) == _name(food)), None)
    return store._num(p["piece_g"]) if p else None


# ---------------------------------------------------------------- deduct / restore
def deduct(conn: psycopg.Connection, user_id: int, entry_id, day: date, items: list[tuple[str, float, str]]) -> list[dict]:
    """items: [(food name, qty, unit g / ml / pcs)] eaten in this entry. Returns what was taken ([] when the switch is off)."""
    if not items or not enabled(conn, user_id):
        return []
    from ..assistant import writelog   # assistant imports grocery
    products = {p["id"]: p for p in store._products(conn, user_id)}
    taken, before = [], []
    for food, qty, unit in items:
        if not qty or qty <= 0 or unit not in P.UNITS:
            continue
        rows = conn.execute("SELECT * FROM pantry_items WHERE user_id = %s FOR UPDATE", (user_id,)).fetchall()
        left = qty   # in the logged unit
        for r in _candidates(rows, products, food):
            if left <= 1e-9:
                break
            piece_g = _piece_g(r, products, food)
            want = store._convert(left, unit, r["unit"], piece_g)
            have = float(r["quantity"])
            if want is None or have <= 0:
                continue
            take = min(want, have)
            snap = {"name": r["name"], "unit": r["unit"], "product_id": r["product_id"], "quantity": have,
                    "best_before": r["best_before"].isoformat() if r["best_before"] else None}
            before.append(snap)
            if have - take <= 1e-9:
                conn.execute("DELETE FROM pantry_items WHERE id = %s", (r["id"],))
            else:
                conn.execute("UPDATE pantry_items SET quantity = %s, updated_at = now() WHERE id = %s", (round(have - take, 3), r["id"]))
            d = conn.execute("""INSERT INTO pantry_deductions (user_id, entry_id, entry_date, food, pantry_name, qty, unit, snapshot)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                             (user_id, str(entry_id), day, food.strip()[:200], r["name"], round(take, 3), r["unit"],
                              Jsonb(snap))).fetchone()
            taken.append({"id": d["id"], "food": food.strip(), "pantry": r["name"], "qty": round(take, 3), "unit": r["unit"],
                          "label": store.fmt_qty(round(take, 3), r["unit"])})
            back = store._convert(take, r["unit"], unit, piece_g)
            left -= back if back is not None else left
    if taken:
        writelog.record(conn, "pantry", "update", str(entry_id), before, taken)
    return taken


def deduct_logged(conn: psycopg.Connection, user_id: int, entry_id, day: date, food: str, amount: str) -> list[dict]:
    """A log_food entry: the whole food name against the pantry, the amount parsed from its text."""
    parsed = parse_amount(amount)
    return deduct(conn, user_id, entry_id, day, [(food, *parsed)]) if parsed else []


def restore(conn: psycopg.Connection, user_id: int, entry_id) -> list[dict]:
    """Put back what this entry took (undo / delete / rollback). Idempotent: each deduction is restored once."""
    rows = conn.execute("""UPDATE pantry_deductions SET restored_at = now()
                           WHERE user_id = %s AND entry_id = %s AND restored_at IS NULL RETURNING *""",
                        (user_id, str(entry_id))).fetchall()
    out = []
    for d in rows:
        s, qty = d["snapshot"], float(d["qty"])
        cur = conn.execute("SELECT * FROM pantry_items WHERE user_id = %s AND lower(name) = lower(%s) FOR UPDATE",
                           (user_id, s["name"])).fetchone()
        if cur is None:   # used up and removed meanwhile: bring the row back as it was
            pid = s.get("product_id")
            if pid is not None and not conn.execute("SELECT 1 FROM grocery_products WHERE id = %s AND user_id = %s",
                                                    (pid, user_id)).fetchone():
                pid = None
            conn.execute("""INSERT INTO pantry_items (user_id, name, product_id, quantity, unit, best_before)
                            VALUES (%s, %s, %s, %s, %s, %s)""", (user_id, s["name"], pid, qty, d["unit"], s.get("best_before")))
        else:
            add = store._convert(qty, d["unit"], cur["unit"], None)
            if add is None:   # the row changed unit meanwhile (e.g. set_pantry): leave it
                continue
            conn.execute("UPDATE pantry_items SET quantity = quantity + %s, updated_at = now() WHERE id = %s", (add, cur["id"]))
        out.append({"pantry": s["name"], "qty": qty, "unit": d["unit"]})
    return out


# ---------------------------------------------------------------- best before
def expiring(conn: psycopg.Connection, user_id: int, days: int | None = None) -> list[dict]:
    """Pantry items with a best before within `days` (default PANTRY_EXPIRY_DAYS) or already past, soonest first."""
    days = P.PANTRY_EXPIRY_DAYS if days is None else days
    today = datetime.now(store._tz(conn, user_id)).date()
    rows = conn.execute("""SELECT name, quantity, unit, best_before FROM pantry_items
                           WHERE user_id = %s AND best_before IS NOT NULL AND best_before <= %s::date + %s
                           ORDER BY best_before, lower(name) LIMIT %s""",
                        (user_id, today, days, P.PANTRY_EXPIRY_MAX)).fetchall()
    return [{"name": r["name"], "label": store.fmt_qty(float(r["quantity"]), r["unit"]),
             "best_before": r["best_before"].isoformat(), "days_left": (r["best_before"] - today).days} for r in rows]


def briefing_lines(conn: psycopg.Connection, user_id: int) -> list[str]:
    """For get_briefing (AI-facing, Chinese)."""
    out = []
    for e in expiring(conn, user_id):
        n = e["days_left"]
        when = f"已过期 {-n} 天" if n < 0 else "今天到期" if n == 0 else f"{n} 天后到期"
        out.append(f"{e['name']} {e['label']}：{when}（{e['best_before']}）")
    return out
