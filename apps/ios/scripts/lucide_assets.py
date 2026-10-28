#!/usr/bin/env python3
"""Turns Lucide icons into template image sets of the native app's asset catalog (#340 N0).

    python3 apps/ios/scripts/lucide_assets.py              # (re)generate every icon in ICONS
    python3 apps/ios/scripts/lucide_assets.py --check      # fail if an image set is missing or differs (CI, offline)

Each icon is downloaded from the pinned Lucide release (the same line as the Today page's icons, THIRD_PARTY_NOTICES.md),
`currentColor` becomes black and the stroke width 1.75 (the page's tab icons use 1.7), and it is written as
`Resources/Assets.xcassets/Icons/lucide.<name>.imageset` with template rendering, so SwiftUI tints it
(`Icon(.chartLine)`). Adding an icon: add its name here, run the script, add it to `Icon.Name` and to the Lucide row of
THIRD_PARTY_NOTICES.md. Never draw icons by hand.
"""
import json
import pathlib
import re
import sys
import urllib.request

LUCIDE_TAG = "1.61.0"
STROKE = "1.75"
# name -> where it is used (kept in THIRD_PARTY_NOTICES.md)
ICONS = {
    "circle-gauge": "tab 今天",
    "chart-line": "tab 趋势",
    "users": "tab 圈子",
    "soup": "tab 饮食",
    "square-plus": "tab 记录",
    "user": "tab 我",
    "chevron-right": "list rows",
    "x": "sheet close",
    "refresh-cw": "sync / retry",
}
ROOT = pathlib.Path(__file__).resolve().parent.parent / "Watch2Whoop" / "Resources" / "Assets.xcassets" / "Icons"


def convert(svg: str) -> str:
    svg = svg.replace("currentColor", "#000000")
    svg = re.sub(r'stroke-width="[^"]*"', f'stroke-width="{STROKE}"', svg, count=1)
    svg = re.sub(r"<!--.*?-->\s*", "", svg, flags=re.S)   # licence comment: the licence is in THIRD_PARTY_NOTICES.md
    return svg.strip() + "\n"


def contents(name: str) -> str:
    return json.dumps({
        "images": [{"filename": f"{name}.svg", "idiom": "universal"}],
        "info": {"author": "xcode", "version": 1},
        "properties": {"preserves-vector-representation": True, "template-rendering-intent": "template"},
    }, indent=2) + "\n"


def main() -> int:
    check = "--check" in sys.argv
    ROOT.mkdir(parents=True, exist_ok=True)
    folder = ROOT / "Contents.json"
    want_folder = json.dumps({"info": {"author": "xcode", "version": 1}, "properties": {"provides-namespace": False}},
                             indent=2) + "\n"
    bad = []
    if check:
        if not folder.exists():
            bad.append(str(folder))
    else:
        folder.write_text(want_folder)
    for name in ICONS:
        d = ROOT / f"lucide.{name}.imageset"
        if check:
            if not (d / f"{name}.svg").exists() or (d / "Contents.json").read_text() != contents(name):
                bad.append(name)
            continue
        url = f"https://raw.githubusercontent.com/lucide-icons/lucide/{LUCIDE_TAG}/icons/{name}.svg"
        with urllib.request.urlopen(url, timeout=20) as r:   # noqa: S310 (fixed https URL)
            svg = r.read().decode()
        d.mkdir(exist_ok=True)
        (d / f"{name}.svg").write_text(convert(svg))
        (d / "Contents.json").write_text(contents(name))
        print(f"lucide.{name}")
    if bad:
        print("missing or changed icons:", ", ".join(bad), "(run apps/ios/scripts/lucide_assets.py)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
