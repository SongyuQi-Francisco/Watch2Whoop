"""Tab bar concepts (D14, docs/ui-ai-first-redesign.md section 7.8): a still per concept and the switch 今天 -> 身体 as frames + a GIF.
Frames are exact: after the switch every animation on the page is paused and moved to each time point (Web Animations API).
Usage: python3 bars.py "$PWD/prototype.html" .   (Playwright for Python with WebKit, Pillow)"""
import sys, asyncio, io
from PIL import Image
from playwright.async_api import async_playwright

URL = "file://" + sys.argv[1]
OUT = sys.argv[2]
BARS = [("dials", "dials"), ("arc", "arc"), ("depth", "depth")]
TIMES = [0, 90, 180, 270, 380, 520, 700, 950]


async def main():
    async with async_playwright() as p:
        b = await p.webkit.launch()
        pg = await b.new_page(viewport={"width": 760, "height": 900}, device_scale_factor=2)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        for i, (bar, name) in enumerate(BARS, 1):
            await pg.goto(f"{URL}?p=week2&look=a&bar={bar}")
            await pg.wait_for_timeout(1800)
            if bar == "depth":   # show the parallax: as if the phone is tilted a little
                await pg.evaluate("() => Proto.parallax(.9, -.6)")
                await pg.wait_for_timeout(600)
            await pg.locator("#phone").screenshot(path=f"{OUT}/bar-{i}-{name}.jpg", type="jpeg", quality=84)
            if bar == "depth":
                await pg.evaluate("() => Proto.parallax(0, 0)")
                await pg.wait_for_timeout(600)
            # the switch 今天 -> 身体, frame by frame
            await pg.evaluate("""() => { window.__freeze = true; Proto.go('body');
              document.querySelector('#tabbar').getBoundingClientRect(); getComputedStyle(document.querySelector('#tabbar .tab[aria-current]')).transform;
              window.__a = document.getAnimations(); window.__a.forEach((a) => a.pause()); }""")
            frames = []
            for t in TIMES:
                await pg.evaluate(f"() => window.__a.forEach((a) => {{ try {{ a.currentTime = {t}; }} catch (e) {{}} }})")
                await pg.wait_for_timeout(60)
                frames.append(Image.open(io.BytesIO(await pg.locator("#phone").screenshot(type="png"))).convert("RGB"))
            if bar == "depth":   # the whole screen: the page opens as a ring from the centre
                frames = [f.resize((260, 563)) for f in frames]
            else:                # the bottom of the screen: the tab bar at full size
                frames = [f.crop((0, f.height - 640, f.width, f.height)).resize((390, 320)) for f in frames]
            w, h = frames[0].size
            grid = Image.new("RGB", (w * 4, h * 2), "black")
            for k, f in enumerate(frames):
                grid.paste(f, ((k % 4) * w, (k // 4) * h))
            grid.save(f"{OUT}/bar-{i}-{name}-frames.jpg", quality=84)
            g = [f.convert("P", palette=Image.ADAPTIVE, colors=128) for f in frames]
            g[0].save(f"{OUT}/bar-{i}-{name}.gif", save_all=True, append_images=g[1:], duration=[110] * (len(g) - 1) + [1200], loop=0, optimize=True)
        print("errors:", errs)
        await b.close()

asyncio.run(main())
