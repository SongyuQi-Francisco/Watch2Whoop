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

        async def shot(name, p_="week2", setup=None, wait=400):
            await pg.goto(f"{URL}?p={p_}&theme=light")
            await pg.wait_for_timeout(300)
            if setup:
                await setup()
            await pg.wait_for_timeout(wait)
            await pg.locator("#phone").screenshot(path=f"{OUT}/{name}.jpg", type="jpeg", quality=82)

        def run(js, ms):   # start a demo without awaiting it (demos wait for clicks), then let it play for ms
            async def f():
                await pg.evaluate(f"() => {{ {js}; }}")
                await pg.wait_for_timeout(ms)
            return f

        await shot("01-day1-overview", setup=run("Proto.demoOnboard(true)", 2000))
        await shot("02-day1-goals", setup=run("Proto.demoOnboard(true)", 4600))
        await shot("03-day1-today", setup=run("Proto.demoOnboard(true)", 9600))
        await shot("04-week2-today")
        await shot("05-veteran-today", p_="veteran")
        await shot("06-grow-card", setup=run("Proto.demoGrow(false)", 3600))

        async def grown():
            await run("Proto.demoGrow(false)", 3600)()
            await pg.click("#pin-water")
            await pg.wait_for_timeout(1500)
        await shot("07-grow-today", setup=grown, wait=900)
        await shot("08-finding", setup=run("Proto.demoFind()", 700))
        await shot("09-circles", setup=run("Proto.go('circles')", 200))
        await shot("10-all-features", setup=run("Proto.go('all')", 200))
        print("errors:", errs)
        await b.close()

asyncio.run(main())
