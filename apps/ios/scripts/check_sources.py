#!/usr/bin/env python3
"""Source checks for the native iOS app that need no Xcode (CI job "iOS native", also fine to run locally):

1. Bilingual UI: no Chinese in a Swift string literal outside `tr("中文", "English")`, and no Chinese in the English
   half (CONTRIBUTING.md "Bilingual UI"). Comments are ignored.
2. Colour tokens: every token in DesignSystem/Palette.swift that has a twin in the Today page's app.css (light `:root`
   and the dark block) has the same hex value, so the native app and the page cannot drift apart.
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
    errors = check_i18n() + check_tokens() + check_licences()
    for e in errors:
        print(e)
    print("ok" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
