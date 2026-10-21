"""The polite HTTP client every store fetcher goes through (docs/grocery-store-data-plan.md §8.3).

- robots.txt is read once per run and checked before every request; unreadable robots.txt (401 / 403 / 5xx / network) = Blocked.
- One request at a time, DELAY_S (+ jitter) apart; timeouts / 5xx retried with backoff; 403 / 429 raise Blocked at once.
- Honest User-Agent with the deployer's contact; no cookies kept, no JavaScript, no browser disguise.
The opener and sleep are injectable so tests never touch the network."""
import gzip
import random
import time
import urllib.error
import urllib.request
from importlib import metadata

from . import params as P
from .robots import Robots


class Blocked(Exception):
    """The site refused us (403 / 429 / robots.txt unreadable or disallowing). Stop this store; a person has to look."""


class Unclear(Exception):
    """The page does not have the structure the parser knows (changed site, challenge page). Stop; do not work around it."""


def user_agent(contact: str) -> str:
    try:
        version = metadata.version("body-engine")
    except metadata.PackageNotFoundError:
        version = "0"
    return f"Watch2Whoop-grocery-sync/{version} (+{contact})"


def _urlopen(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (fixed https hosts from params)


class Fetcher:
    def __init__(self, base: str, contact: str, *, delay_s: float | None = None, max_requests: int | None = None,
                 opener=None, sleep=time.sleep):
        self.base = base.rstrip("/")
        self.ua = user_agent(contact)
        self.delay = P.delay_s() if delay_s is None else delay_s
        self.max_requests = P.max_requests() if max_requests is None else max_requests
        self.opener = opener or _urlopen
        self.sleep = sleep
        self.requests = 0
        self._last = 0.0
        self.robots: Robots | None = None

    def _wait(self) -> None:
        if self._last:
            gap = self.delay + random.uniform(0, P.JITTER_S if self.delay >= P.DELAY_MIN_S else 0)
            left = gap - (time.monotonic() - self._last)
            if left > 0:
                self.sleep(left)

    def _get(self, url: str) -> tuple[int, str]:
        if self.requests >= self.max_requests:
            raise Blocked(f"到了每次最多 {self.max_requests} 个请求的上限")
        for attempt in range(P.RETRIES + 1):
            self._wait()
            self.requests += 1
            self._last = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept": "text/html,application/xml,text/plain",
                                                       "Accept-Encoding": "gzip", "Accept-Language": "en-AU"})
            try:
                with self.opener(req, P.TIMEOUT_S) as resp:
                    body = resp.read()
                    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                        body = gzip.decompress(body)
                    return resp.status, body.decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 429):
                    raise Blocked(f"HTTP {e.code}：{url}") from e
                if e.code == 404:
                    return 404, ""
                if e.code < 500 or attempt == P.RETRIES:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == P.RETRIES:
                    raise
            self.sleep(P.BACKOFF_S[min(attempt, len(P.BACKOFF_S) - 1)])
        raise RuntimeError("unreachable")

    def load_robots(self) -> Robots:
        url = self.base + "/robots.txt"
        try:
            status, text = self._get(url)
        except Blocked:
            raise
        except Exception as e:
            raise Blocked(f"读不到 robots.txt（{e}）") from e
        if status != 200:
            raise Blocked(f"读不到 robots.txt（HTTP {status}）")
        self.robots = Robots(text, self.ua)
        return self.robots

    def get(self, url: str) -> str:
        if self.robots is None:
            self.load_robots()
        if not self.robots.allowed(url):
            raise Blocked(f"robots.txt 不允许：{url}")
        status, text = self._get(url)
        if status != 200:
            raise Unclear(f"HTTP {status}：{url}")
        return text
