#!/usr/bin/env python3
"""Source checks for the native iOS app that need no Xcode (CI job "iOS native", also fine to run locally):

1. Bilingual UI: no Chinese in a Swift string literal outside `tr("中文", "English")`, and no Chinese in the English
   half (CONTRIBUTING.md "Bilingual UI"). Comments are ignored.
2. Colour tokens: every token in DesignSystem/Palette.swift that has a twin in the Today page's app.css (light `:root`
   and the dark block) has the same hex value, so the native app and the page cannot drift apart.
3. Licences: Resources/licenses.json (the app's 开源许可 page) names every pinned SPM package of project.yml at its
   version, and every entry has a licence text.
4. Product name: `AppBrand.name` ("Hale 息") is the name under the icon in both InfoPlist.strings and in the xcconfig, and
   no string literal or permission text still says "Watch2Whoop" (the project name stays in identifiers and the URL scheme).

    python3 apps/ios/scripts/check_sources.py
"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent
APP = HERE / "Watch2Whoop"
CSS = REPO / "body_engine" / "src" / "body_engine" / "web" / "static" / "app.css"
HAN = re.compile(r"[一-鿿]")
STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
TR = re.compile(r'\btr\(\s*("(?:[^"\\\n]|\\.)*")\s*,\s*("(?:[^"\\\n]|\\.)*")\s*\)')
# Files copied byte for byte from the Flutter app (their own rules apply there)
SKIP = {"SyncEngine.swift", "RawSync.swift", "Backfill.swift"}
# The product name has one spelling in both languages ("Hale 息"), so it is the one place Chinese may sit outside tr()
BRAND_FILE = "AppBrand.swift"


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
        if f.name in SKIP or f.name == BRAND_FILE:
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


def check_brand() -> list[str]:
    """The product name users read is `AppBrand.name` ("Hale 息"): the name under the icon is the same in both InfoPlist.strings
    and in the xcconfig, and no string literal in the app (or permission text) still names the project "Watch2Whoop". The
    project name stays in identifiers, the URL scheme, paths and the licence list, none of which is a string a person reads."""
    errors = []
    brand_file = APP / "App" / "AppBrand.swift"
    m = re.search(r'static let name = "([^"\n]+)"', brand_file.read_text())
    if not m:
        return [f"{brand_file.relative_to(REPO)}: no `static let name = \"…\"`"]
    name = m.group(1)
    for lang in ("zh-Hans", "en"):
        path = APP / "Resources" / f"{lang}.lproj" / "InfoPlist.strings"
        text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)   # comments may name files
        d = re.search(r'"CFBundleDisplayName"\s*=\s*"([^"\n]*)"\s*;', text)
        if not d or d.group(1) != name:
            errors.append(f"{path.relative_to(REPO)}: CFBundleDisplayName must be {name!r} (AppBrand.name)")
        if "Watch2Whoop" in text:
            errors.append(f"{path.relative_to(REPO)}: a permission text still says Watch2Whoop (use {name!r})")
    xc = re.search(r"^W2W_DISPLAY_NAME\s*=\s*(.+?)\s*$", (HERE / "Config" / "Watch2Whoop.xcconfig").read_text(), re.M)
    if not xc or xc.group(1) != name:
        errors.append(f"Config/Watch2Whoop.xcconfig: W2W_DISPLAY_NAME must be {name!r} (AppBrand.name)")
    for m in re.finditer(r"^\s+NS\w+UsageDescription: (.*)$", (HERE / "project.yml").read_text(), re.M):
        if "Watch2Whoop" in m.group(1):
            errors.append("project.yml: a permission text still says Watch2Whoop (use the product name)")
    for f in sorted(APP.rglob("*.swift")):
        if f.name in SKIP:
            continue
        for n, raw in enumerate(f.read_text().splitlines(), 1):
            for s in STRING.finditer(strip_comments(raw)):
                if "Watch2Whoop" in s.group(0):
                    errors.append(f"{f.relative_to(REPO)}:{n}: the string names Watch2Whoop; text a person reads uses AppBrand.name")
    return errors


def main() -> int:
    errors = check_i18n() + check_tokens() + check_licences() + check_brand()
    for e in errors:
        print(e)
    print("ok" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
