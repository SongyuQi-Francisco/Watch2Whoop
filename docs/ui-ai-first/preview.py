"""Screenshots of the v0.4 preview: the AI-first structure in the CURRENT visual style (#279, docs/ui-ai-first-redesign.md).

Runs the real Today page (web/static from this checkout, served through Playwright) against a Quick Start server with
synthetic data, then injects preview-compose.js, which only rearranges what the real app rendered.

    ./quickstart.sh                       # once; then ./quickstart.sh login prints a one-time link
    python3 docs/ui-ai-first/preview.py "<login link>" body_engine/src/body_engine/web/static docs/ui-ai-first

Feature flags on the Quick Start server should match production (all on except ai_cards, apple_sync_engine,
app_types_pause): body-engine flags set <flag> --state all. Needs Playwright for Python with WebKit."""
import asyncio
import os
import re
import sys

from playwright.async_api import async_playwright

LOGIN, STATIC, OUT = sys.argv[1], os.path.abspath(sys.argv[2]), os.path.abspath(sys.argv[3])
COMPOSE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview-compose.js")
TYPES = {"js": "text/javascript", "css": "text/css"}


async def local_static(route):
    """The server's image may be older than this checkout: serve the page's scripts and styles from the checkout."""
    path = re.sub(r"\?.*$", "", route.request.url.split("/app/", 1)[1])
    f = os.path.join(STATIC, path)
    if os.path.isfile(f):
        return await route.fulfill(status=200, body=open(f, "rb").read(),
                                   headers={"content-type": TYPES[path.rsplit(".", 1)[-1]], "cache-control": "no-store"})
    return await route.continue_()


async def main():
    async with async_playwright() as p:
        browser = await p.webkit.launch()
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="zh-CN", color_scheme="light", bypass_csp=True)
        await ctx.route(re.compile(r".*/app/(?!handbook)[^?#]*\.(js|css)(\?.*)?$"), local_static)
        pg = await ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(LOGIN)
        await pg.wait_for_timeout(5000)

        async def tab(name, wait=2500):
            await pg.evaluate(f"() => document.querySelector('.tabbar [data-tab=\"{name}\"]').click()")
            await pg.wait_for_timeout(wait)

        async def shot(name):
            await pg.wait_for_timeout(500)
            await pg.screenshot(path=f"{OUT}/{name}.jpg", type="jpeg", quality=84)

        async def sheet_off():
            await pg.evaluate("() => { const s = document.querySelector('#sheet'); s.classList.remove('open'); s.hidden = true; }")

        await tab("today")
        await shot("v04-0-now-today")   # the page as it is today, for comparison
        await pg.add_script_tag(path=COMPOSE)

        await pg.evaluate("() => { PV.today(); PV.day1(); }")
        await shot("v04-1-day1-overview")
        await sheet_off()
        await shot("v04-2-today")
        await pg.evaluate("""() => { const h = [...document.querySelectorAll('#view .section-head h2')].find((x) => x.textContent.startsWith('我的首页'));
          window.scrollTo(0, h.getBoundingClientRect().top + window.scrollY - 170); }""")
        await shot("v04-2b-today-more")
        await pg.evaluate("() => PV.longpress()")
        await shot("v04-3-longpress")
        await sheet_off()
        await pg.evaluate("() => { window.scrollTo(0, 0); window.BodyAI.isJarvis = () => true; document.querySelector('#ask').click(); }")
        await pg.wait_for_timeout(1200)
        await shot("v04-4-jarvis")
        await pg.evaluate("() => document.querySelector('#ask').click()")
        await pg.wait_for_timeout(800)
        await pg.evaluate("() => window.BodyAI.openChat()")
        await pg.wait_for_timeout(1500)
        await shot("v04-4b-jarvis-chat")
        await pg.evaluate("() => { document.querySelectorAll('.ai-close, [data-ai-close], #ai-close').forEach((b) => b.click()); }")
        await pg.goto(LOGIN.split("#")[0])   # a clean page (the chat panel and the composed DOM are gone)
        await pg.wait_for_timeout(4000)
        await pg.add_script_tag(path=COMPOSE)

        await tab("trends")
        await pg.evaluate("() => PV.body()")
        await shot("v04-5-body")
        await tab("friends")
        await pg.evaluate("() => document.querySelector('[data-fmode2=\"feed\"]')?.click()")
        await pg.wait_for_timeout(2500)
        await pg.evaluate("() => PV.circles()")
        await shot("v04-6-circles")
        await tab("me")
        await pg.evaluate("() => PV.me()")
        await shot("v04-7-me")
        await pg.evaluate("() => PV.all()")
        await shot("v04-8-all-features")
        await tab("record", 1500)
        await pg.evaluate("() => document.querySelector('[data-rsub=\"food\"]')?.click()")
        await pg.wait_for_timeout(2500)
        await pg.evaluate("() => PV.food()")
        await shot("v04-9-food")
        print("errors:", errors[:5])
        await browser.close()

asyncio.run(main())
