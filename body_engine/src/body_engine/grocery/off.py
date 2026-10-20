"""Barcode -> product name and nutrition per 100 g from Open Food Facts (docs/grocery-plan.md §7).

- Off unless BODY_OFF_CONTACT is set (an e-mail or URL): OFF asks API users for a User-Agent "App/Version (contact)".
- Single lookups only (the user asks their AI about one product); per-process and per-user limits in params.py; results cached
  in memory for a day so asking twice does not call OFF twice.
- Data is ODbL: every result carries the attribution, and save_product stores it as nutrition_source so the page shows it."""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from importlib import metadata

from . import params as P

_lock = threading.Lock()
_recent: deque[float] = deque()                 # call times, all users (per minute)
_per_user: dict[tuple[int, str], int] = {}      # (user_id, day) -> calls
_cache: dict[str, tuple[float, dict]] = {}
CACHE_S = 86400

# OFF nutriments key (per 100 g) -> ours, factor
_MAP = {"energy-kcal_100g": ("kcal", 1), "proteins_100g": ("protein_g", 1), "carbohydrates_100g": ("carbs_g", 1),
        "fat_100g": ("fat_g", 1), "saturated-fat_100g": ("sat_fat_g", 1), "sugars_100g": ("sugar_g", 1),
        "sodium_100g": ("sodium_mg", 1000)}


def contact() -> str | None:
    return (os.environ.get("BODY_OFF_CONTACT") or "").strip() or None


def user_agent() -> str:
    try:
        version = metadata.version("body-engine")
    except metadata.PackageNotFoundError:
        version = "0"
    return f"Watch2Whoop/{version} ({contact()})"


def _reserve(user_id: int) -> None:
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    with _lock:
        while _recent and now - _recent[0] > 60:
            _recent.popleft()
        if len(_recent) >= P.OFF_PER_MINUTE:
            raise RuntimeError("条码查询太频繁，过一分钟再试")
        if _per_user.get((user_id, day), 0) >= P.OFF_PER_USER_DAY:
            raise RuntimeError(f"今天的条码查询用完了（每人每天 {P.OFF_PER_USER_DAY} 次）")
        _recent.append(now)
        _per_user[(user_id, day)] = _per_user.get((user_id, day), 0) + 1
        for k in [k for k in _per_user if k[1] != day]:
            del _per_user[k]


def _fetch(code: str) -> dict:
    url = P.OFF_PRODUCT_URL.format(code=code) + "?fields=" + P.OFF_FIELDS
    req = urllib.request.Request(url, headers={"User-Agent": user_agent(), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=P.OFF_TIMEOUT_S) as resp:  # noqa: S310 (fixed https host)
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"status": 0}
        raise RuntimeError(f"Open Food Facts 暂时查不了（HTTP {e.code}）") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise RuntimeError("Open Food Facts 暂时查不了（网络）") from e


def parse(code: str, data: dict) -> dict:
    """OFF product JSON -> {found, barcode, name, brand, quantity, nutrition, source, url}."""
    base = {"barcode": code, "source": P.OFF_ATTRIBUTION, "url": P.OFF_PAGE_URL.format(code=code)}
    prod = data.get("product") if data.get("status") == 1 else None
    if not prod:
        return {"found": False, **base}
    nut = prod.get("nutriments") or {}
    nutrition = {}
    for k, (ours, f) in _MAP.items():
        v = nut.get(k)
        if isinstance(v, (int, float)) and v >= 0:
            nutrition[ours] = round(float(v) * f, 1)
    brand = (prod.get("brands") or "").split(",")[0].strip() or None
    name = (prod.get("product_name_en") or prod.get("product_name") or "").strip() or None
    return {"found": True, **base, "name": name[:60] if name else None, "brand": brand[:40] if brand else None,
            "quantity": (prod.get("quantity") or "").strip() or None, "nutrition": nutrition}


def lookup(user_id: int, barcode: str) -> dict:
    code = re.sub(r"\s", "", barcode or "")
    if not re.fullmatch(r"[0-9]{6,14}", code):
        raise ValueError("条码只能是 6–14 位数字")
    if not contact():
        return {"enabled": False, "barcode": code,
                "note": "服务器没开条码查询（管理员设置 BODY_OFF_CONTACT 后可用）；请用户拍营养成分表"}
    hit = _cache.get(code)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    _reserve(user_id)
    out = parse(code, _fetch(code))
    if len(_cache) >= 2000:
        _cache.clear()
    _cache[code] = (time.time(), out)
    return out
