#!/usr/bin/env python3
"""Annotated screenshots for docs/tester-handbook.md (red boxes / arrows on real iPhone screens).

    python3 tools/handbook_images.py            # src/*.jpg -> docs/images/tester-handbook/*.jpg
    python3 tools/handbook_images.py --lang en  # en/src/*.jpg + en/spec.json -> docs/images/tester-handbook/en/*.jpg

Sources: docs/images/tester-handbook/src/ (iOS 27 simulator in Simplified Chinese, iPhone 17, half size; the Today
page screens use a placeholder test account with Quick Start's synthetic data, never a real user). The spec below
is in iPhone points (402 x 874, what `idb ui describe-all` reports), so new captures can be re-annotated without
measuring pixels. consent.jpg (the one-tap first-run screen) is a headless Chrome capture of the Today page at 402 x 874
CSS px, 1.5x (same size, no status bar). Output: 420 px wide JPEG (~30-60 KB each) so the single-file personal handbook stays < 2 MB.
English: the sources are captured by tools/handbook_shots_en.mjs (headless Chrome, synthetic data only), which also writes the
boxes (en/spec.json, in the same points); the 802-px-wide activation page is kept at its own size.
Needs Pillow (`pip install pillow`; macOS /usr/bin/python3 has it).
"""
import json
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "docs" / "images" / "tester-handbook"
SRC = DIR / "src"
PT_W = 402                       # source coordinates are iPhone points
OUT_W = 420
RED = (230, 40, 40)

# name -> list of shapes: ("box", x0, y0, x1, y1) | ("arrow", x0, y0, x1, y1) | ("mask", x0, y0, x1, y1)
SPEC = {
    "safari-login":  [("box", 318, 792, 372, 842), ("arrow", 300, 700, 338, 784)],
    "safari-more":   [("box", 128, 522, 362, 562)],
    "safari-share":  [("box", 18, 566, 384, 614)],
    "add-home":      [("box", 318, 88, 388, 132)],
    "home-icon":     [("box", 212, 184, 284, 276)],
    "login-code":    [("box", 24, 396, 378, 450), ("arrow", 201, 560, 201, 506)],
    "consent":       [("box", 18, 756, 384, 821)],
    "profile":       [("box", 18, 774, 198, 822)],
    "shortcut-open": [("box", 24, 758, 378, 806)],
    "shortcut-url":  [("box", 8, 284, 394, 326)],
    "shortcut-token": [("box", 170, 284, 350, 326)],
    "shortcut-no-access": [("box", 40, 222, 362, 292)],
    "settings-health-access": [("box", 14, 178, 388, 218)],
    "settings-shortcuts-health": [("box", 14, 184, 388, 224)],
    "shortcut-run":  [("box", 16, 228, 196, 340)],
    "auto-triggers": [("box", 14, 210, 388, 256), ("box", 14, 334, 388, 380)],
    "auto-app":      [("box", 14, 252, 388, 298)],
    "auto-time":     [("box", 14, 752, 388, 852)],
    "auto-pick":     [("box", 200, 426, 360, 496)],
    "import":        [("box", 18, 774, 384, 822)],
    "health-export": [("box", 14, 696, 388, 738)],
    "health-permission": [("box", 18, 436, 384, 488), ("box", 36, 722, 366, 776)],
    "today":         [],
    "me-ai":         [("box", 18, 432, 384, 482)],
    "ai-settings":   [("box", 18, 716, 199, 766), ("box", 203, 716, 384, 766)],
    "ai-kinds":      [("box", 98, 444, 324, 652)],
    "ai-api":        [("box", 14, 378, 388, 612)],
    "ai-mcp":        [("box", 14, 604, 388, 646), ("box", 18, 772, 384, 822)],
    "jarvis-none":   [("box", 18, 772, 384, 822)],
}


def arrow(d: ImageDraw.ImageDraw, x0, y0, x1, y1, w):
    import math
    d.line((x0, y0, x1, y1), fill=RED, width=w)
    a = math.atan2(y1 - y0, x1 - x0)
    L = w * 4.5
    for s in (-0.45, 0.45):
        d.line((x1, y1, x1 - L * math.cos(a + s), y1 - L * math.sin(a + s)), fill=RED, width=w)


def render(name: str, shapes: list, src: Path = SRC, out_dir: Path = DIR, keep_size: bool = False) -> Path:
    im = Image.open(src / f"{name}.jpg").convert("RGB")
    k = im.width / PT_W if not keep_size else im.width / 390
    d = ImageDraw.Draw(im)
    w = max(3, round(2.2 * k))
    for kind, *c in shapes:
        x0, y0, x1, y1 = (v * k for v in c)
        if kind == "box":
            d.rounded_rectangle((x0, y0, x1, y1), radius=10 * k, outline=RED, width=w)
        elif kind == "arrow":
            arrow(d, x0, y0, x1, y1, w)
        elif kind == "mask":
            d.rounded_rectangle((x0, y0, x1, y1), radius=6 * k, fill=(220, 220, 224))
    if not keep_size:
        im = im.resize((OUT_W, round(im.height * OUT_W / im.width)), Image.LANCZOS)
    out = out_dir / f"{name}.jpg"
    im.save(out, quality=72, optimize=True, progressive=True)
    return out


def main():
    total = 0
    if "--lang" in sys.argv and sys.argv[sys.argv.index("--lang") + 1] == "en":
        en = DIR / "en"
        spec = json.loads((en / "spec.json").read_text())
        items = [(n, [("box", *b) for b in boxes], en / "src", en, n == "activate") for n, boxes in spec.items()]
    else:
        items = [(n, shapes, SRC, DIR, False) for n, shapes in SPEC.items()]
    for name, shapes, src, out_dir, keep in items:
        out = render(name, shapes, src, out_dir, keep)
        if out_dir.name == "en":   # the rendered English handbook carries the same files
            shutil.copy(out, ROOT / "body_engine/src/body_engine/web/static/handbook/en/img" / out.name)
        total += out.stat().st_size
        print(f"{out.relative_to(ROOT)}  {out.stat().st_size // 1024} KB")
    print(f"{len(items)} images, {total // 1024} KB")


if __name__ == "__main__":
    main()
