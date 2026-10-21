"""Store-independent normalisation for grocery-sync: package size, unit price, special type. Pure functions (tested directly)."""
import math
import re

_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l|litre|litres|ltr)\b", re.IGNORECASE)
_MULTI = re.compile(r"(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b", re.IGNORECASE)
_COUNT = re.compile(r"(\d+)\s*(pack|pk|each|ea|piece|pieces|bunch|eggs?)\b", re.IGNORECASE)
_COMPARABLE = re.compile(r"\$\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)?\s*(kg|g|ml|l|ea|each)\b", re.IGNORECASE)


def parse_size(text: str | None) -> tuple[float | None, str | None]:
    """"500g" -> (500, g); "1.5kg" -> (1500, g); "2L" -> (2000, ml); "6 x 330mL" -> (1980, ml); "12 Pack" -> (12, pcs).
    "approx. 1.6kg each" -> (1600, g). Unknown -> (None, None)."""
    s = (text or "").strip()
    if not s:
        return None, None
    if m := _MULTI.search(s):
        n, q, u = int(m.group(1)), float(m.group(2)), m.group(3).lower()
        base, unit = _to_base(q, u)
        return round(n * base, 1), unit
    if m := _SIZE.search(s):
        base, unit = _to_base(float(m.group(1)), m.group(2).lower())
        return round(base, 1), unit
    if m := _COUNT.search(s):
        return float(m.group(1)), "pcs"
    if s.lower() in ("each", "ea", "per each"):   # sold by the piece (Coles: broccoli "each")
        return 1.0, "pcs"
    return None, None


def _to_base(q: float, u: str) -> tuple[float, str]:
    if u == "kg":
        return q * 1000, "g"
    if u == "g":
        return q, "g"
    if u in ("l", "litre", "litres", "ltr"):
        return q * 1000, "ml"
    return q, "ml"


def parse_comparable(text: str | None) -> tuple[float | None, str | None]:
    """"$19.60/ 1kg" -> (19.6, kg); "$3.85/ 100mL" -> (38.5, L); "$1.59/ 100g" -> (15.9, kg); "$2.63/ 1ea" -> (2.63, ea)."""
    m = _COMPARABLE.search(text or "")
    if not m:
        return None, None
    price, qty, unit = float(m.group(1)), float(m.group(2) or 1), m.group(3).lower()
    if unit in ("ea", "each"):
        return round(price / qty, 2), "ea"
    per_base = price / qty                       # per g or per ml (or per kg / L)
    if unit in ("g", "ml"):
        return round(per_base * 1000, 2), "kg" if unit == "g" else "L"
    return round(per_base, 2), "kg" if unit == "kg" else "L"


def classify_special(now: float | None, was: float | None, half_label: bool, multibuy: bool, promo: bool) -> str | None:
    """half_price: labelled 1/2 price, or was > 0 and now ≤ 51% of was; multibuy; price_drop: was > now; special: promoted
    without a known saving; None: regular price."""
    if half_label or (was and now is not None and was > 0 and now <= was * 0.51 + 1e-9):
        return "half_price"
    if multibuy:
        return "multibuy"
    if was and now is not None and was > now:
        return "price_drop"
    return "special" if promo else None


def money(v) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0:
        return round(float(v), 2)
    return None


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:120] or "product"
