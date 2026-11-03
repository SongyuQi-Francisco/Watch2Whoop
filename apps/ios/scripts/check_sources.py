#!/usr/bin/env python3
"""Source checks for the native iOS app that need no Xcode (CI job "iOS native", also fine to run locally):

1. Bilingual UI: no Chinese in a Swift string literal outside `tr("中文", "English")`, and no Chinese in the English
   half (CONTRIBUTING.md "Bilingual UI"). Comments are ignored.
2. Colour tokens: every token in DesignSystem/Palette.swift that has a twin in the Today page's app.css (light `:root`
   and the dark block) has the same hex value, so the native app and the page cannot drift apart. The same for the
   tokens' `.slim(...)` values against slim.js (`html[data-theme="slim"]`, alpha included), and the accent of the normal
   look must be --text on --bg, so that the normal look does not change.
3. Licences: Resources/licenses.json (the app's 开源许可 page) names every pinned SPM package of project.yml at its
   version, and every entry has a licence text.

    python3 apps/ios/scripts/check_sources.py
"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent
APP = HERE / "Watch2Whoop"
CSS = REPO / "body_engine" / "src" / "body_engine" / "web" / "static" / "app.css"
SLIM_JS = CSS.with_name("slim.js")
HAN = re.compile(r"[一-鿿]")
STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
TR = re.compile(r'\btr\(\s*("(?:[^"\\\n]|\\.)*")\s*,\s*("(?:[^"\\\n]|\\.)*")\s*\)')
# Files copied byte for byte from the Flutter app (their own rules apply there)
SKIP = {"SyncEngine.swift", "RawSync.swift", "Backfill.swift"}


def strip_comments(line: str) -> str:
    out, i, in_str = [], 0, False
    while i < len(line):
        c = line[i]
        if c == '"' and (i == 0 or line[i - 1] != "\\"):
            in_str = not in_str
        if not in_str and line.startswith("//", i):
            break
        out.append(c)
        i += 1
    return "".join(out)


def check_i18n() -> list[str]:
    errors = []
    for f in sorted(APP.rglob("*.swift")):
        if f.name in SKIP:
            continue
        for n, raw in enumerate(f.read_text().splitlines(), 1):
            line = strip_comments(raw)
            if not HAN.search(line):
                continue
            covered = set()
            for m in TR.finditer(line):
                zh, en = m.group(1), m.group(2)
                if HAN.search(en):
                    errors.append(f"{f.relative_to(REPO)}:{n}: Chinese in the English half of tr()")
                covered.update(range(m.start(), m.end()))
            for m in STRING.finditer(line):
                if HAN.search(m.group(0)) and m.start() not in covered:
                    errors.append(f"{f.relative_to(REPO)}:{n}: Chinese string outside tr(\"中文\", \"English\")")
    return errors


CSS_NAMES = {"bg": "bg", "surface": "surface", "surface-2": "surface2", "text": "text", "text-2": "text2",
             "text-3": "text3", "good": "good", "mid": "mid", "bad": "bad", "sleep": "sleep", "sheet": "sheet",
             "in-sheet": "inSheet"}


def css_tokens(block: str) -> dict:
    return {m.group(1): m.group(2).lower() for m in re.finditer(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", block)}


def check_tokens() -> list[str]:
    css = CSS.read_text()
    light_block = css[css.index(":root {"):css.index("}", css.index(":root {"))]
    dark_start = css.index("@media (prefers-color-scheme: dark)")
    dark_block = css[dark_start:css.index("}", css.index(":root {", dark_start))]
    light, dark = css_tokens(light_block), css_tokens(dark_block)
    swift = (APP / "DesignSystem" / "Palette.swift").read_text()
    errors = []
    for css_name, swift_name in CSS_NAMES.items():
        m = re.search(rf"static let {swift_name} = dynamic\(0x([0-9A-Fa-f]{{6}}), 0x([0-9A-Fa-f]{{6}})\)", swift)
        if not m:
            errors.append(f"Palette.swift: no plain token {swift_name} (app.css --{css_name})")
            continue
        for mode, table, got in (("light", light, m.group(1)), ("dark", dark, m.group(2))):
            want = table.get(css_name)
            if want and want != "#" + got.lower():
                errors.append(f"Palette.swift {swift_name} ({mode}) is #{got.lower()}, app.css --{css_name} is {want}")
    return errors


# The tokens of the slim look: all of the above and the ones with an alpha value
SLIM_NAMES = {**CSS_NAMES, "train": "train", "hair": "hair", "good-soft": "goodSoft", "mid-soft": "midSoft", "bad-soft": "badSoft",
              "scrim": "scrim", "chrome": "chrome", "accent": "accent", "on-accent": "onAccent"}


def slim_tokens(block: str) -> dict:
    """--name: #rrggbb -> ("rrggbb", 1.0); --name: rgba(r, g, b, a) -> ("rrggbb", a)."""
    out = {m.group(1): (m.group(2).lower(), 1.0) for m in re.finditer(r"--([\w-]+):\s*#([0-9a-fA-F]{6})\s*;", block)}
    for m in re.finditer(r"--([\w-]+):\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)\s*;", block):
        out[m.group(1)] = ("%02x%02x%02x" % (int(m.group(2)), int(m.group(3)), int(m.group(4))), float(m.group(5)))
    return out


def check_slim_tokens() -> list[str]:
    js = SLIM_JS.read_text()
    selector = 'html[data-theme="slim"] {'
    dark_at = js.index("@media (prefers-color-scheme: dark)")
    light_at = js.index(selector)
    dark_inner = js.index(selector, dark_at)
    light = slim_tokens(js[light_at:js.index("}", light_at)])
    dark = slim_tokens(js[dark_inner:js.index("}", dark_inner)])
    swift = (APP / "DesignSystem" / "Palette.swift").read_text()
    errors = []
    for css_name, swift_name in SLIM_NAMES.items():
        m = re.search(rf"static let {swift_name} = dynamic\([^\n]*\)\.slim\(0x([0-9A-Fa-f]{{6}}), 0x([0-9A-Fa-f]{{6}})"
                      r"(?:, lightAlpha: ([\d.]+), darkAlpha: ([\d.]+))?\)", swift)
        if not m:
            errors.append(f"Palette.swift: {swift_name} has no .slim(...) (slim.js --{css_name})")
            continue
        got = ((m.group(1).lower(), float(m.group(3) or 1)), (m.group(2).lower(), float(m.group(4) or 1)))
        for mode, table, value in (("light", light, got[0]), ("dark", dark, got[1])):
            if table.get(css_name) != value:
                errors.append(f"Palette.swift {swift_name} slim ({mode}) is {value}, slim.js --{css_name} is {table.get(css_name)}")
    # The normal look stays what it was: buttons and selected chips are --text on --bg
    for accent, base in (("accent", "text"), ("onAccent", "bg")):
        values = [re.search(rf"static let {n} = dynamic\((0x[0-9A-Fa-f]{{6}}, 0x[0-9A-Fa-f]{{6}})\)", swift) for n in (accent, base)]
        if not all(values) or values[0].group(1) != values[1].group(1):
            errors.append(f"Palette.swift {accent}: in the normal look it must have the values of {base}")
    return errors


def check_licences() -> list[str]:
    import json
    data = json.loads((APP / "Resources" / "licenses.json").read_text())
    by_name = {c["name"].split(".")[0].lower(): c for c in data["components"]}
    errors = [f"licenses.json: {c['name']} has no licence text" for c in data["components"] if not c.get("text", "").strip()]
    spec = (HERE / "project.yml").read_text()
    for name, version in re.findall(r"^  (\w+):\n    url: [^\n]+\n    exactVersion: ([\d.]+)", spec, re.M):
        entry = by_name.get(name.lower())
        if entry is None:
            errors.append(f"licenses.json: SPM package {name} is missing")
        elif entry["version"] != version:
            errors.append(f"licenses.json: {name} is {entry['version']}, project.yml pins {version}")
    return errors


def main() -> int:
    errors = check_i18n() + check_tokens() + check_slim_tokens() + check_licences()
    for e in errors:
        print(e)
    print("ok" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
