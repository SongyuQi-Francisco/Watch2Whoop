"""Woolworths (shown as WWS). 2026-10-21: Akamai answers 403 Access Denied to our identifiable User-Agent, already for
robots.txt (docs/grocery-store-data-plan.md §8.4). The rule is: do not crawl, do not work around it. So this module only checks
robots.txt through the polite client (which raises Blocked on 403) and has no page parser; one gets written only once access is
actually allowed (for example Woolworths' written permission or our User-Agent being allow-listed)."""
from . import params as P
from .http import Fetcher, Unclear

STORE = "woolworths"


def crawl(fetcher: Fetcher, categories=None, max_pages: int | None = None):
    fetcher.load_robots()   # raises Blocked on 403 (what happens today)
    raise Unclear("WWS 的 robots.txt 能读到了，但还没有经过确认的页面解析器：先人工确认条款和页面结构，再写 wws.py 的解析器")
    yield  # pragma: no cover  (generator)


BASE = P.WWS_BASE
