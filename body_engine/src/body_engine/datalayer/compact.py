"""Token-lean JSON for tool results sent to an AI (remote / stdio MCP and the in-page chat), docs/ai-assistant-plan.md §9.13.

The data is the same as what run_tool returns (tests and the HTTP API keep the full shape); only the text the model reads is smaller:
- keys whose value is null / {} / [] are left out (the prompts say: a missing field = no data);
- a list of 3+ objects becomes a table {"cols": [...], "rows": [[...], ...]} (each key written once; columns that are null in every row are dropped);
- floats are rounded (2 decimals, 3 below 1), datetimes are cut to minutes, separators have no spaces.
False and 0 are kept: they carry meaning (logged=false, 0 sets)."""
import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

TABLE_MIN_ROWS = 3


def _num(x: float) -> float | int:
    if x != x or x in (float("inf"), float("-inf")):  # NaN / inf are not valid JSON
        return None
    r = round(x, 3 if abs(x) < 1 else 2)
    return int(r) if r == int(r) and abs(r) < 1e15 else r


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, (dict, list, tuple)) and not v)


def _table(rows: list[dict]) -> dict:
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    cols = [c for c in cols if any(not _empty(r.get(c)) for r in rows)]
    return {"cols": cols, "rows": [[r.get(c) for c in cols] for r in rows]}


def compact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): c for k, v in obj.items() if not _empty(c := compact(v))}
    if isinstance(obj, (list, tuple)):
        items = [compact(v) for v in obj]
        if len(items) >= TABLE_MIN_ROWS and all(isinstance(v, dict) for v in items):
            return _table(items)
        return items
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, float):
        return _num(obj)
    if isinstance(obj, Decimal):
        return _num(float(obj))
    if isinstance(obj, datetime):
        return obj.isoformat(timespec="minutes")
    if isinstance(obj, (date, time)):
        return obj.isoformat()
    return str(obj)


def dumps(result: Any) -> str:
    return json.dumps(compact(result), ensure_ascii=False, separators=(",", ":"), default=str)
