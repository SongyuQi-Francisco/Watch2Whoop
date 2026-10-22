"""Search API providers behind the server's web_search tool (issue #150). One interface, swappable by configuration:

  BODY_WEB_SEARCH_PROVIDER = brave (default) | tavily | serper
  key file secrets/web_search_api_key (or BODY_WEB_SEARCH_API_KEY_FILE), read only when needed, never logged or returned.
No key -> configured() is False and web_search is not offered (no error). Prices and limits: params.py.
Adding a provider: a SearchProvider subclass with `name`, `request` (method, url, headers, JSON body) and `parse`, plus its
price in params.PRICE_USD.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ...config import REPO_ROOT
from . import params as P


class SearchError(Exception):
    pass


class SearchProvider:
    name = ""

    def request(self, key: str, query: str, count: int, country: str | None) -> tuple[str, str, dict, dict | None]:
        raise NotImplementedError

    def parse(self, data: dict) -> list[dict]:
        """-> [{"title", "url", "snippet"}]"""
        raise NotImplementedError

    @property
    def price_usd(self) -> float:
        return P.PRICE_USD[self.name]


class Brave(SearchProvider):
    """https://api-dashboard.search.brave.com/app/documentation/web-search/get-started"""
    name = "brave"

    def request(self, key, query, count, country):
        q = {"q": query, "count": count, **({"country": country} if country else {})}
        return ("GET", "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(q),
                {"X-Subscription-Token": key, "Accept": "application/json"}, None)

    def parse(self, data):
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description")}
                for r in ((data.get("web") or {}).get("results") or [])]


class Tavily(SearchProvider):
    """https://docs.tavily.com/documentation/api-reference/endpoint/search"""
    name = "tavily"

    def request(self, key, query, count, country):
        return ("POST", "https://api.tavily.com/search", {"Authorization": f"Bearer {key}"},
                {"query": query, "max_results": count, "search_depth": "basic"})

    def parse(self, data):
        return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")} for r in data.get("results") or []]


class Serper(SearchProvider):
    """https://serper.dev (Google results)"""
    name = "serper"

    def request(self, key, query, count, country):
        return ("POST", "https://google.serper.dev/search", {"X-API-KEY": key},
                {"q": query, "num": count, **({"gl": country.lower()} if country else {})})

    def parse(self, data):
        return [{"title": r.get("title"), "url": r.get("link"), "snippet": r.get("snippet")} for r in data.get("organic") or []]


PROVIDERS = {p.name: p for p in (Brave(), Tavily(), Serper())}


def key_path() -> Path:
    default = Path(os.environ.get("BODY_SECRETS_DIR", REPO_ROOT / "secrets")) / P.KEY_FILE_NAME
    return Path(os.environ.get("BODY_WEB_SEARCH_API_KEY_FILE", default))


def read_key() -> str | None:
    try:
        key = key_path().read_text().strip()
    except OSError:
        return None
    return key or None


def provider() -> SearchProvider | None:
    return PROVIDERS.get(os.environ.get(P.PROVIDER_ENV, P.DEFAULT_PROVIDER).strip().lower())


def configured() -> bool:
    return provider() is not None and read_key() is not None


def _http(method: str, url: str, headers: dict, body: dict | None) -> dict:
    """The provider's HTTP call (tests replace it)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**headers, **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=P.SEARCH_TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))


def search(query: str, country: str | None = None) -> tuple[SearchProvider, list[dict]]:
    p, key = provider(), read_key()
    if p is None or key is None:
        raise SearchError("搜索服务没有配置")
    try:
        data = _http(*p.request(key, query, P.SEARCH_RESULTS, country))
    except urllib.error.HTTPError as e:
        raise SearchError(f"搜索服务出错（{e.code}），稍后再试") from None   # no URL / headers in the message: they hold the key
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise SearchError("连不上搜索服务，稍后再试") from None
    out = []
    for r in p.parse(data)[:P.SEARCH_RESULTS]:
        if r.get("url"):
            out.append({"title": (r.get("title") or "")[:200], "url": r["url"],
                        "snippet": " ".join((r.get("snippet") or "").split())[:P.SNIPPET_CHARS]})
    return p, out


def country_for(tz: str | None) -> str | None:
    for prefix, country in P.TZ_COUNTRY.items():
        if tz and tz.startswith(prefix):
            return country
    return None
