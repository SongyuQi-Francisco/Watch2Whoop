"""History of one lab test (every reading, any source) and the status card values derived from it.

Used by: GET /v1/users/{u}/labs/{test_key}/history (trend page), get_lab_history (butler / MCP, compact),
and assistant/status.py (status cards whose key is a lab test_key: value / date / trend / spark always come from here,
read at request time, so a result written by add_lab_results shows up on the card straight away).
"""
from . import lab_params as P, store

ROW_FIELDS = ("id", "collected_on", "value", "value_text", "unit", "ref_low", "ref_high", "flag", "source", "fasting",
              "lab", "note")


def _rows(conn, user_id: int, keys: list[str] | None = None) -> list[dict]:
    q = "SELECT * FROM lab_results WHERE user_id = %s"
    params: list = [user_id]
    if keys is not None:
        q += " AND coalesce(test_key, test_name) = ANY(%s)"
        params.append(keys)
    return conn.execute(q + " ORDER BY collected_on, id", params).fetchall()


def trend(cur: float | None, prev: float | None) -> str | None:
    """up / down / flat between the last two readings (flat = change below TREND_FLAT_FRACTION of the previous one)."""
    if cur is None or prev is None:
        return None
    if abs(cur - prev) <= abs(prev) * P.TREND_FLAT_FRACTION:
        return "flat"
    return "up" if cur > prev else "down"


def _delta(cur: dict, prev: dict | None) -> dict:
    if prev is None or cur["value"] is None or prev["value"] is None:
        return {"delta": None, "pct": None}
    d = cur["value"] - prev["value"]
    return {"delta": round(d, 4), "pct": round(d / prev["value"] * 100, 1) if prev["value"] else None}


def _history_of(key: str, rows: list[dict]) -> dict:
    out_rows, prev = [], None
    for r in rows:
        item = {k: r.get(k) for k in ROW_FIELDS}
        item["status"] = store.compare_to_range(r)
        item.update(_delta(r, prev))
        if r["value"] is not None:
            prev = r
        out_rows.append(item)
    last = rows[-1]
    ref_row = next((r for r in reversed(rows) if r["ref_low"] is not None or r["ref_high"] is not None), last)
    return {"test_key": key, "test_name": last["test_name"], "category": last["category"],
            "unit": next((r["unit"] for r in reversed(rows) if r["unit"]), None),
            "ref_low": ref_row["ref_low"], "ref_high": ref_row["ref_high"],
            "good_direction": P.GOOD_DIRECTION.get(key), "rows": out_rows}


def history(conn, user_id: int, test_key: str) -> dict | None:
    """All readings of one test, old -> new, each with source, fasting, status and the change from the previous numeric reading."""
    rows = _rows(conn, user_id, [test_key])
    return _history_of(test_key, rows) if rows else None


def compact(h: dict, limit: int | None = None) -> dict:
    """For the butler / MCP: only what is needed to talk about the trend (notes are in get_lab_results)."""
    rows = h["rows"][-limit:] if limit else h["rows"]
    pts = []
    for r in rows:
        p = {"date": r["collected_on"].isoformat(), "value": r["value"] if r["value"] is not None else r["value_text"],
             "source": r["source"]}
        if r["fasting"] is not None:
            p["fasting"] = r["fasting"]
        if r["delta"] is not None:
            p["delta"], p["pct"] = r["delta"], r["pct"]
        if r["status"] not in ("normal", "unknown"):
            p["status"] = r["status"]
        pts.append(p)
    return {"test_key": h["test_key"], "name": h["test_name"], "unit": h["unit"],
            "ref": [h["ref_low"], h["ref_high"]], "better": h["good_direction"], "n": len(h["rows"]), "points": pts}


# ---------------------------------------------------------------- status cards
def card(h: dict) -> dict | None:
    """Status card fields from a test's history: value "偏高 489", unit, tone, trend of the last two readings, spark (old -> new),
    note "10-18 · 家测 · 上限 420". None when the test has no numeric reading."""
    nums = [r for r in h["rows"] if r["value"] is not None]
    if not nums:
        return None
    last = nums[-1]
    status = store.compare_to_range(last)
    word = {"high": "偏高", "low": "偏低", "normal": "正常"}.get(status, "")
    over = (last["value"] - last["ref_high"]) / last["ref_high"] if status == "high" and last["ref_high"] else 0
    tone = {"normal": "good", "high": "bad" if over > P.HIGH_BAD_FRACTION else "warn", "low": "warn"}.get(status, "neutral")
    limit = f" · 上限 {last['ref_high']:g}" if last["ref_high"] is not None else ""
    return {"value": f"{word} {last['value']:g}".strip(), "unit": last["unit"], "tone": tone,
            "trend": trend(last["value"], nums[-2]["value"]) if len(nums) >= 2 else None,
            "good_direction": h["good_direction"],
            "spark": [r["value"] for r in nums[-P.SPARK_POINTS:]] if len(nums) >= 2 else None,
            "note": f"{last['collected_on']:%m-%d} · {P.SOURCE_LABEL.get(last['source'], last['source'])}{limit}",
            "lab_key": h["test_key"], "lab_date": last["collected_on"], "lab_source": last["source"]}


def cards(conn, user_id: int, keys: list[str] | None = None) -> dict[str, dict]:
    """test_key -> status card fields, for every test with a numeric reading (one query)."""
    by_key: dict[str, list[dict]] = {}
    for r in _rows(conn, user_id, keys):
        by_key.setdefault(r["test_key"] or r["test_name"], []).append(r)
    out = {}
    for k, rows in by_key.items():
        c = card(_history_of(k, rows))
        if c:
            out[k] = c
    return out

