"""Coles: the public category pages /browse/<category>?page=N (robots.txt allows /browse/; checked per URL anyway).

The page is server-rendered (Next.js): <script id="__NEXT_DATA__"> props.pageProps.searchResults has noOfResults, pageSize and
results (48 products per page plus a few ad tiles). Structure checked 2026-10-21 (docs/grocery-store-data-plan.md §8.4).
Only the fields in `normalise` are kept: no images, no descriptions. Anything else on the page -> Unclear (stop, do not work around)."""
import json
import math
import re

from . import common as C, params as P
from .http import Fetcher, Unclear

STORE = "coles"
_NEXT = re.compile(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S)


def page_url(category: str, page: int) -> str:
    url = P.COLES_BROWSE.format(cat=category)
    return url if page <= 1 else f"{url}?page={page}"


def parse_page(html: str) -> dict:
    """-> {total, page_size, products: [normalised]}; Unclear when the page is not what we know."""
    m = _NEXT.search(html or "")
    if not m:
        raise Unclear("Coles 页面里没有 __NEXT_DATA__（可能是挑战页或改版了）")
    try:
        data = json.loads(m.group(1))
        sr = data["props"]["pageProps"]["searchResults"]
        total, size, results = int(sr["noOfResults"]), int(sr.get("pageSize") or P.COLES_PAGE_SIZE), sr["results"]
    except (ValueError, KeyError, TypeError) as e:
        raise Unclear(f"Coles 页面结构变了（{e.__class__.__name__}: {e}）") from e
    if not isinstance(results, list):
        raise Unclear("Coles 页面结构变了（results 不是列表）")
    products, seen = [], set()
    for r in results:
        if not isinstance(r, dict) or r.get("_type") != "PRODUCT":
            continue   # ad tiles
        p = normalise(r)
        if p and p["sku"] not in seen:
            seen.add(p["sku"])
            products.append(p)
    return {"total": total, "page_size": max(size, 1), "products": products}


def normalise(r: dict) -> dict | None:
    pid, name = r.get("id"), (r.get("name") or "").strip()
    if not isinstance(pid, int) or not name:
        return None
    brand = (r.get("brand") or "").strip() or None
    pr = r.get("pricing") or {}
    now, was = C.money(pr.get("now")), C.money(pr.get("was"))
    if was == 0:
        was = None
    mb = pr.get("multiBuyPromotion") or {}
    multibuy = pr.get("specialType") == "MULTI_SAVE" or bool(mb)
    half = "1/2" in (pr.get("priceDescription") or "") or (pr.get("specialType") == "PERCENT_OFF" and (pr.get("savePercent") or 0) >= 50)
    special = C.classify_special(now, was, half, multibuy, pr.get("promotionType") == "SPECIAL")
    unit_price, unit_unit = C.parse_comparable(pr.get("comparable"))
    size_text = (r.get("size") or "").strip() or None
    pkg, pkg_unit = C.parse_size(size_text)
    save = C.money(pr.get("saveAmount"))
    if save is None and was and now is not None:
        save = round(was - now, 2)
    full = f"{brand} {name}" if brand and not name.lower().startswith(brand.lower()) else name
    label = (pr.get("offerDescription") or pr.get("priceDescription") or "").strip() or None
    return {"store": STORE, "sku": str(pid), "name": full[:160], "brand": brand[:60] if brand else None,
            "size_text": size_text[:40] if size_text else None, "package_size": pkg, "package_unit": pkg_unit,
            "barcode": None, "url": P.COLES_PRODUCT.format(slug=C.slug(full), id=pid),
            "price": now, "was_price": was, "unit_price": unit_price, "unit_price_unit": unit_unit,
            "special_type": special, "special_label": label[:60] if label and special else None,
            "save_amount": save if special in ("half_price", "price_drop") else None,
            "multibuy_qty": mb.get("minQuantity") if isinstance(mb.get("minQuantity"), int) else None,
            "multibuy_unit_price": C.money(mb.get("reward"))}


def crawl(fetcher: Fetcher, categories=None, max_pages: int | None = None):
    """Walk every food category page by page. Yields (category, page, products). Stops at the last page of each category."""
    categories = categories or P.COLES_CATEGORIES
    fetcher.load_robots()
    pages = 0
    for cat in categories:
        page, last = 1, 1
        while page <= last:
            if max_pages is not None and pages >= max_pages:
                return
            out = parse_page(fetcher.get(page_url(cat, page)))
            pages += 1
            last = max(1, math.ceil(out["total"] / out["page_size"]))
            if not out["products"] and page == 1 and out["total"]:
                raise Unclear(f"Coles {cat} 第 1 页没有商品，但 noOfResults = {out['total']}")
            yield cat, page, out["products"]
            if not out["products"]:
                break
            page += 1
