"""Pixel check for a page change that should be invisible with its flag off (#279 ui_ai_first; usable for any flag).

Draws the same pages of the Today page twice against one running Quick Start server (synthetic data), once with the static files
of a base commit and once with this checkout's, and compares the screenshots pixel by pixel. It also counts the first-screen API
requests and bytes of both. Service workers are blocked so every script and style comes from the folder given.

    ./quickstart.sh                                     # once; set the server's flags as in production first
    git archive origin/main body_engine/src/body_engine/web/static | tar -x -C /tmp/base
    python3 tools/ui_flag_pixel_diff.py /tmp/base/body_engine/src/body_engine/web/static body_engine/src/body_engine/web/static /tmp/out

Needs Playwright for Python with WebKit and Pillow. Exit status 1 when a page differs."""
import asyncio
import os
import re
import subprocess
import sys

from PIL import Image, ImageChops
from playwright.async_api import async_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ["today", "today-scrolled", "trends", "friends", "record", "food", "me"]
TYPES = {"js": "text/javascript", "css": "text/css"}


def login_link() -> str:
    out = subprocess.run([os.path.join(ROOT, "quickstart.sh"), "login"], cwd=ROOT, capture_output=True, text=True).stdout
    return re.search(r"http://localhost:\d+/app/#login=[A-Z0-9]+", out).group(0)


async def shoot(static: str, out: str, tag: str) -> dict:
    async def local(route):
        path = re.sub(r"\?.*$", "", route.request.url.split("/app/", 1)[1])
        f = os.path.join(static, path)
        if os.path.isfile(f):
            return await route.fulfill(status=200, body=open(f, "rb").read(), headers={"content-type": TYPES[path.rsplit(".", 1)[-1]], "cache-control": "no-store"})
        return await route.continue_()

    async with async_playwright() as p:
        browser = await p.webkit.launch()
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="zh-CN", color_scheme="light",
                                        reduced_motion="reduce", service_workers="block")
        await ctx.route(re.compile(r".*/app/(?!handbook)[^?#]*\.(js|css)(\?.*)?$"), local)
        page = await ctx.new_page()
        stats = {"api": 0, "api_bytes": 0, "static_bytes": 0}

        async def count(resp):
            try:
                n = len(await resp.body())
            except Exception:
                return
            stats["api_bytes" if "/v1/" in resp.url else "static_bytes"] += n
        page.on("request", lambda r: stats.__setitem__("api", stats["api"] + 1) if "/v1/" in r.url else None)
        page.on("response", lambda r: asyncio.ensure_future(count(r)))
        await page.goto(login_link())
        await page.wait_for_timeout(6000)
        first = dict(stats)

        async def tab(name):
            await page.evaluate(f"() => document.querySelector('.tabbar [data-tab=\"{name}\"]').click()")
            await page.wait_for_timeout(2500)
        for name in PAGES:
            if name == "today-scrolled":
                await tab("today")
                await page.evaluate("() => window.scrollTo(0, 700)")
            elif name == "food":
                await tab("record")
                await page.evaluate("() => document.querySelector('[data-rsub=\"food\"]')?.click()")
                await page.wait_for_timeout(2500)
            else:
                await tab(name)
            await page.wait_for_timeout(400)
            await page.screenshot(path=os.path.join(out, f"{tag}-{name}.png"))
        await browser.close()
        return first


async def main():
    base, head, out = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out, exist_ok=True)
    a = await shoot(os.path.abspath(base), out, "base")
    b = await shoot(os.path.abspath(head), out, "head")
    print(f"first screen  base: {a}\n              head: {b}")
    same = True
    for name in PAGES:
        x = Image.open(os.path.join(out, f"base-{name}.png")).convert("RGB")
        y = Image.open(os.path.join(out, f"head-{name}.png")).convert("RGB")
        box = ImageChops.difference(x, y).getbbox() if x.size == y.size else (0, 0) + x.size
        same &= box is None
        print(f"{name:16} {'identical' if box is None else f'DIFFERENT in {box}'}")
    sys.exit(0 if same else 1)

asyncio.run(main())
