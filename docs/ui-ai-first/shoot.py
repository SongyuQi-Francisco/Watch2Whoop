"""Screenshots of the clickable prototype (docs/ui-ai-first-redesign.md, #279).
Usage: python3 shoot.py "$PWD/prototype.html" .   (Playwright for Python with WebKit: pip install playwright && playwright install webkit)"""
import sys, asyncio
from playwright.async_api import async_playwright
URL = "file://" + sys.argv[1]
OUT = sys.argv[2]
async def main():
    async with async_playwright() as p:
        b = await p.webkit.launch()
        pg = await b.new_page(viewport={"width": 760, "height": 900}, device_scale_factor=2)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        async def shot(name, theme="light", p_="week2", setup=None, wait=400):
            await pg.goto(f"{URL}?p={p_}&theme={theme}")
            await pg.wait_for_timeout(300)
            if setup: await setup()
            await pg.wait_for_timeout(wait)
            await pg.locator("#phone").screenshot(path=f"{OUT}/{name}.jpg", type="jpeg", quality=82)
        await shot("01-day1-today", p_="day1")
        await shot("02-week2-today")
        await shot("03-veteran-today", p_="veteran")
        async def jv(): await pg.evaluate("Proto.jarvisSheet()")
        await shot("04-day1-jarvis", p_="day1", setup=jv)
        # demo grow: three frames
        async def g1(): await pg.evaluate("() => { Proto.demoGrow(false); }"); await pg.wait_for_timeout(1700)
        await shot("05-grow-1-voice", setup=g1)
        async def g2(): await pg.evaluate("() => { Proto.demoGrow(false); }"); await pg.wait_for_timeout(3600)
        await shot("06-grow-2-card", setup=g2)
        async def g3():
            await pg.evaluate("() => { Proto.demoGrow(false); }"); await pg.wait_for_timeout(3600)
            await pg.click("#pin-water"); await pg.wait_for_timeout(1500)
        await shot("07-grow-3-today", setup=g3, wait=900)
        async def intro(): await pg.evaluate("() => { Proto.demoIntro(); }"); await pg.wait_for_timeout(700)
        await shot("08-ready-intro", setup=intro)
        async def body(): await pg.evaluate("Proto.go('body')")
        await shot("09-week2-body", setup=body)
        async def det(): await pg.evaluate("Proto.detailSheet('hrv')"); await pg.wait_for_timeout(300)
        await shot("10-detail-layers", setup=det)
        async def rec(): await pg.evaluate("Proto.go('record')")
        await shot("11-week2-record", setup=rec)
        async def mods(): await pg.evaluate("Proto.go('me','modules')")
        await shot("09b-day1-body", p_="day1", setup=body)
        await shot("12-week2-modules", setup=mods)
        await shot("13-veteran-today-dark", theme="dark", p_="veteran")
        print("errors:", errs)
        await b.close()
asyncio.run(main())
