"""Store links for products and shopping-list items (docs/grocery-plan.md §7).

Links only, no fetching: a saved Coles / Woolworths product page (validated and parsed here), else a search link per store by the
product's name. Which stores: the chain named in the store field (aliases in params.STORES); both when the store is empty; none for
another known store (Aldi, a market...), which has no product pages to link to."""
import re
import urllib.parse

from . import params as P


def parse_product_url(url: str) -> dict:
    """-> {store, sku, url (canonical: https, www host, no query / fragment)}; ValueError for anything that is not a product page."""
    u = (url or "").strip()
    if not u or len(u) > P.PRODUCT_URL_MAX:
        raise ValueError(f"商品链接 1–{P.PRODUCT_URL_MAX} 字")
    try:
        parts = urllib.parse.urlsplit(u)
    except ValueError as e:
        raise ValueError("商品链接格式不对") from e
    host = (parts.hostname or "").lower()
    for key, s in P.STORES.items():
        if host not in (s["host"], s["host"].removeprefix("www.")):
            continue
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
            raise ValueError("商品链接要是 https:// 开头的官网地址")
        m = re.fullmatch(s["path"], parts.path, re.IGNORECASE)
        if not m:
            raise ValueError(f"不是 {s['label']} 的商品页（要 /product/… 或 /shop/productdetails/<编号>/…）")
        return {"store": key, "sku": m.group(1), "url": f"https://{s['host']}{parts.path.rstrip('/')}"}
    raise ValueError("只支持 Coles（coles.com.au/product/…）和 Woolworths（woolworths.com.au/shop/productdetails/…）的商品页")


def store_key(store: str | None) -> str | None:
    """Chain key for a free-text store field; '' for another known store; None when empty (unknown)."""
    s = (store or "").strip().lower()
    if not s:
        return None
    for key, cfg in P.STORES.items():
        if any(a in s for a in cfg["aliases"]):
            return key
    return ""


def _ascii(s: str) -> bool:
    return bool(s) and bool(re.search(r"[a-z]", s, re.IGNORECASE)) and all(ord(c) < 128 for c in s)


def search_term(name: str, brand: str | None = None, matches: list[str] | tuple = ()) -> str:
    """The stores' search only understands English: brand + name when the name is English, else the first English `matches`
    entry (e.g. 鸡胸肉 -> "chicken breast"), else the name as it is."""
    name = (name or "").strip()
    if _ascii(name):
        return f"{brand.strip()} {name}" if brand and _ascii(brand.strip()) and brand.strip().lower() not in name.lower() else name
    for m in matches or ():
        if _ascii((m or "").strip()):
            return m.strip()
    return name


def search_url(key: str, term: str) -> str:
    return P.STORES[key]["search"] + urllib.parse.quote(term.strip())


def links(name: str, store: str | None = None, product_url: str | None = None, brand: str | None = None,
          matches: list[str] | tuple = ()) -> list[dict]:
    """[{store, label, kind: product | search, url}]: the saved product page first (its own chain only), else searches."""
    if product_url:
        p = parse_product_url(product_url)
        return [{"store": p["store"], "label": P.STORES[p["store"]]["label"], "kind": "product", "url": p["url"]}]
    key = store_key(store)
    keys = list(P.STORES) if key is None else [key] if key else []
    term = search_term(name, brand, matches)
    if not term:
        return []
    return [{"store": k, "label": P.STORES[k]["label"], "kind": "search", "url": search_url(k, term)} for k in keys]
