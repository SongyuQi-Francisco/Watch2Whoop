"""Read one public web page for the server's web_fetch tool (issue #150), politely and never around a block.

For one URL the user's request needs (the model calls this once per page; no crawling, no following links, no schedules):
1. only http(s) on the default ports, no user:password@, hosts in params.NO_FETCH_HOSTS are never fetched;
2. the host must resolve to public addresses only, and the connection goes to the address that was checked (no requests
   into the server's own network, also not through DNS tricks);
3. robots.txt of the host is read first (RFC 9309) and obeyed for params.ROBOTS_AGENT: allowed by 200 + rules or a 404-type
   answer; a 401 / 403 / 429 / 5xx / unreachable robots.txt means "don't";
4. the request says who we are (params.user_agent(), never a browser's), follows at most MAX_REDIRECTS redirects, re-checking
   1–3 on every hop;
5. 401 / 403 / 429 / 503 ..., a bot challenge or CAPTCHA page, or a login wall -> Blocked: stop there, no retry, no other
   address, and the host is not asked again for BLOCKED_HOST_CACHE_S. The model then asks the user for a screenshot or receipt.
What comes back is a short excerpt (title, schema.org Product data, a few thousand characters of text); nothing is stored here.
"""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.parse
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser

from . import params as P


class Blocked(Exception):
    """The site does not want us there (robots.txt, bot protection, login, a refusing status). reason: robots / bot / login /
    status / no_fetch_host"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason, self.detail = reason, detail


class FetchError(Exception):
    """Could not read the page for an ordinary reason (bad URL, private address, not found, not a web page, network)."""


_robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}   # "scheme://host" -> (at, parser | None = disallow all)
_blocked: dict[str, tuple[float, str]] = {}                                          # host -> (at, reason)
_clock = time.monotonic


# ---------------------------------------------------------------- transport (tests replace _resolve and _request)
def _resolve(host: str) -> list[str]:
    return sorted({ai[4][0] for ai in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, **kw):
        super().__init__(host, **kw)
        self._ip = ip

    def connect(self):   # connect to the checked address; SNI and certificate check still use the host name
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, ip: str, **kw):
        super().__init__(host, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


def _request(scheme: str, host: str, ip: str, path: str, limit: int) -> tuple[int, dict, bytes]:
    """One GET to `ip` for `host` -> (status, lower-case headers, body up to `limit` bytes)."""
    conn = (_PinnedHTTPS(host, ip, timeout=P.FETCH_TIMEOUT_S, context=ssl.create_default_context()) if scheme == "https"
            else _PinnedHTTP(host, ip, timeout=P.FETCH_TIMEOUT_S))
    try:
        conn.request("GET", path, headers={"User-Agent": P.user_agent(), "Accept": "text/html,text/plain;q=0.9",
                                           "Accept-Language": "en-AU,en;q=0.9,zh;q=0.8"})
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read(limit + 1)[:limit + 1]
    finally:
        conn.close()


# ---------------------------------------------------------------- checks
def _split(url: str) -> tuple[str, str, str]:
    """-> (scheme, host, path?query). FetchError / Blocked for what we never fetch."""
    if len(url) > P.URL_MAX_CHARS:
        raise FetchError("链接太长")
    try:
        u = urllib.parse.urlsplit(url.strip())
        port = u.port
    except ValueError:
        raise FetchError("不是有效的链接") from None
    if u.scheme not in ("http", "https") or not u.hostname:
        raise FetchError("只能读 http / https 网页")
    if u.username or u.password:
        raise FetchError("链接里不能带用户名 / 密码")
    if port not in (None, 80, 443):
        raise FetchError("只能读标准端口的网页")
    host = u.hostname.lower().rstrip(".")
    if any(host == h or host.endswith("." + h) for h in P.NO_FETCH_HOSTS):
        raise Blocked("no_fetch_host", host)
    return u.scheme, host, (u.path or "/") + (f"?{u.query}" if u.query else "")


def _public_ip(host: str) -> str:
    try:
        ips = [host] if _is_ip(host) else _resolve(host)
    except (socket.gaierror, UnicodeError, OSError):
        raise FetchError("找不到这个网站") from None
    if not ips:
        raise FetchError("找不到这个网站")
    if not all(_global(ip) for ip in ips):
        raise FetchError("不能读内网地址")
    return ips[0]


def _global(ip: str) -> bool:
    return ipaddress.ip_address(ip.split("%")[0]).is_global


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _robots_for(scheme: str, host: str) -> urllib.robotparser.RobotFileParser | None:
    key = f"{scheme}://{host}"
    hit = _robots.get(key)
    if hit and _clock() - hit[0] < P.ROBOTS_CACHE_S:
        return hit[1]
    parser: urllib.robotparser.RobotFileParser | None
    try:
        s, h, path = scheme, host, "/robots.txt"
        for _ in range(P.MAX_REDIRECTS + 1):
            status, headers, body = _request(s, h, _public_ip(h), path, P.MAX_ROBOTS_BYTES)
            if status in (301, 302, 303, 307, 308) and headers.get("location"):
                s, h, path = _split(urllib.parse.urljoin(f"{s}://{h}{path}", headers["location"]))
                continue
            break
        if 200 <= status < 300:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(body[:P.MAX_ROBOTS_BYTES].decode("utf-8", "replace").splitlines())
        elif 400 <= status < 500 and status not in P.BLOCK_STATUSES:
            parser = urllib.robotparser.RobotFileParser()   # RFC 9309 "unavailable" (4xx) -> no rules
            parser.parse([])
        else:
            parser = None                                   # 401 / 403 / 429 / 5xx: the site is closed to us
    except Blocked:
        raise
    except Exception:   # RFC 9309 "unreachable" -> complete disallow
        parser = None
    _robots[key] = (_clock(), parser)
    return parser


def _allowed(scheme: str, host: str, path: str) -> bool:
    parser = _robots_for(scheme, host)
    return parser is not None and parser.can_fetch(P.ROBOTS_AGENT, f"{scheme}://{host}{path}")


def _remember_block(host: str, reason: str) -> Blocked:
    _blocked[host] = (_clock(), reason)
    return Blocked(reason, host)


# ---------------------------------------------------------------- extraction
class _Page(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.meta, self.ld, self.text = "", {}, [], []
        self.password = False
        self._stack: list[str] = []
        self._ld_buf: list[str] | None = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            k = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if k and a.get("content") and k in ("og:title", "og:description", "description", "product:price:amount",
                                                "product:price:currency", "og:price:amount", "og:price:currency", "price",
                                                "pricecurrency"):
                self.meta.setdefault(k, a["content"][:300])
            return
        if tag == "input" and a.get("type", "").lower() == "password":
            self.password = True
        if tag == "title":
            self._in_title = True
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._ld_buf = []
        if tag in self.SKIP:
            self._stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script" and self._ld_buf is not None:
            self.ld.append("".join(self._ld_buf))
            self._ld_buf = None
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def handle_data(self, data):
        if self._ld_buf is not None:
            self._ld_buf.append(data)
        elif self._in_title:
            self.title += data
        elif not self._stack:
            self.text.append(data)


def _products(blobs: list[str]) -> list[dict]:
    found: list[dict] = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            t = o.get("@type")
            if t == "Product" or (isinstance(t, list) and "Product" in t):
                found.append(o)
            for k in ("@graph", "itemListElement", "item", "mainEntity"):
                if k in o:
                    walk(o[k])

    for b in blobs:
        try:
            walk(json.loads(b))
        except ValueError:
            continue
    out = []
    for p in found[:P.MAX_PRODUCTS]:
        offers = p.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        brand, weight = p.get("brand"), p.get("weight")
        if isinstance(weight, dict):
            weight = f"{weight.get('value', '')}{weight.get('unitText') or weight.get('unitCode') or ''}"
        out.append({k: v for k, v in {
            "name": p.get("name"), "brand": brand.get("name") if isinstance(brand, dict) else brand,
            "size": p.get("size") or weight,
            "gtin": p.get("gtin13") or p.get("gtin") or p.get("gtin14") or p.get("gtin12"), "sku": p.get("sku"),
            "price": offers.get("price") or offers.get("lowPrice"), "currency": offers.get("priceCurrency"),
            "availability": str(offers.get("availability") or "").rsplit("/", 1)[-1] or None, "url": p.get("url"),
        }.items() if v not in (None, "")})
    return out


def _decode(body: bytes, ctype: str) -> str:
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    try:
        return body.decode(m.group(1) if m else "utf-8", "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def extract(html: str, ctype: str = "text/html") -> dict:
    """Page -> {"title", "products", "meta", "text"}; Blocked when it is a bot challenge or a login wall."""
    head = html[:P.CHALLENGE_SCAN_CHARS].lower()
    if any(m in head for m in P.CHALLENGE_MARKERS):
        raise Blocked("bot")
    if ctype.startswith("text/plain"):
        return {"title": "", "products": [], "meta": {}, "text": " ".join(html.split())[:P.TEXT_CHARS]}
    page = _Page()
    try:
        page.feed(html)
        page.close()
    except Exception:   # badly broken HTML: keep what was parsed
        pass
    text = " ".join(" ".join(page.text).split())
    if page.password and len(text) < P.LOGIN_TEXT_MAX:
        raise Blocked("login")
    return {"title": " ".join(page.title.split())[:200], "products": _products(page.ld), "meta": page.meta,
            "text": text[:P.TEXT_CHARS]}


# ---------------------------------------------------------------- fetch
def fetch(url: str) -> dict:
    """-> {"url" (final), "fetched_at", "title", "products", "meta", "text"}. Blocked / FetchError otherwise."""
    scheme, host, path = _split(url)
    for _ in range(P.MAX_REDIRECTS + 1):
        hit = _blocked.get(host)
        if hit and _clock() - hit[0] < P.BLOCKED_HOST_CACHE_S:
            raise Blocked(hit[1], host)
        ip = _public_ip(host)
        if not _allowed(scheme, host, path):
            raise Blocked("robots", host)
        try:
            status, headers, body = _request(scheme, host, ip, path, P.MAX_PAGE_BYTES)
        except (OSError, http.client.HTTPException, ssl.SSLError):
            raise FetchError("打不开这个网页（网络或证书问题）") from None
        if status in (301, 302, 303, 307, 308) and headers.get("location"):
            scheme, host, path = _split(urllib.parse.urljoin(f"{scheme}://{host}{path}", headers["location"]))
            continue
        if status in P.BLOCK_STATUSES:
            raise _remember_block(host, "status")
        if status >= 400:
            raise FetchError(f"网页打不开（{status}）")
        ctype = (headers.get("content-type") or "text/html").lower()
        if not ctype.startswith(P.CONTENT_TYPES):
            raise FetchError("不是网页（只读 HTML / 文本）")
        try:
            page = extract(_decode(body[:P.MAX_PAGE_BYTES], ctype), ctype)
        except Blocked as e:
            raise _remember_block(host, e.reason) from None
        return {"url": f"{scheme}://{host}{path}", "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **page}
    raise FetchError("跳转太多次")
