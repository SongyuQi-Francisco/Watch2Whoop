#!/usr/bin/env python3
"""Check and render the App Store listing texts.

    python3 docs/appstore/render_listing.py                       # check only, placeholders counted at worst case
    APP_NAME="<final name>" SERVER_URL="https://<host>" python3 docs/appstore/render_listing.py --write

Reads docs/appstore/listing/<locale>/*.txt (fastlane `deliver` metadata layout), where the app name is the placeholder
{{APP_NAME}} and the server address {{SERVER_URL}}. Prints the character count of every field against Apple's limit and
fails (exit 1) on: a limit exceeded, a competitor trademark (WHOOP, Oura, ...), a medical-claim word that is not in a
negated sentence ("not a medical device"), keywords with a space next to a comma / duplicates / the word "apple",
or an APP_NAME containing WHOOP. `--write` renders into docs/appstore/out/<locale>/ (git-ignored), ready for
`fastlane deliver --metadata_path docs/appstore/out` or for pasting into App Store Connect.
Standard library only.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LISTING = HERE / "listing"
OUT = HERE / "out"

# field file -> Apple's character limit (App Store Connect, iOS)
LIMITS = {"name": 30, "subtitle": 30, "promotional_text": 170, "description": 4000, "keywords": 100,
          "release_notes": 4000}
FIELDS = list(LIMITS) + ["privacy_url"]

# other companies' marks: App Review 2.3.7 / 5.2 reject them in names, subtitles, keywords, texts
TRADEMARKS = ["whoop", "oura", "garmin", "fitbit", "polar", "strava", "peloton", "myfitnesspal", "samsung",
              "huawei", "xiaomi", "amazfit", "withings", "coros", "suunto"]
# medical-claim words; allowed only in a sentence that negates them ("not a medical device", 不用于诊断)
MEDICAL = ["诊断", "治疗", "治愈", "疾病", "医疗", "处方", "diagnos", "treat", "cure", "disease", "medical", "prescri"]
NEGATION = ["不是", "不用于", "不提供", "不能", "not a", "not for", "not provide", "cannot", "no medical"]
SENTENCE = re.compile(r"[^。.\n]+[。.]?")


def load(locale: str, app_name: str, server_url: str) -> dict[str, str]:
    out = {}
    for f in FIELDS:
        p = LISTING / locale / f"{f}.txt"
        if p.exists():
            text = p.read_text(encoding="utf-8").rstrip("\n")
            out[f] = text.replace("{{APP_NAME}}", app_name).replace("{{SERVER_URL}}", server_url)
    return out


def check(locale: str, fields: dict[str, str]) -> tuple[list[str], list[str]]:
    errors, warns = [], []
    for f, limit in LIMITS.items():
        if f not in fields:
            errors.append(f"{locale}: {f}.txt is missing")
            continue
        n = len(fields[f])
        mark = "ok " if n <= limit else "ERR"
        print(f"  {mark} {locale:8s} {f:16s} {n:5d} / {limit}")
        if n > limit:
            errors.append(f"{locale}: {f} is {n} characters, the limit is {limit}")
    for f, text in fields.items():
        if f in ("privacy_url", "name"):
            continue
        low = text.lower()
        for t in TRADEMARKS:
            if re.search(rf"\b{re.escape(t)}\b", low):
                errors.append(f"{locale}: {f} mentions the trademark '{t}'")
        for sent in SENTENCE.findall(text):
            s = sent.lower()
            for w in MEDICAL:
                if w in s and not any(neg in s for neg in NEGATION):
                    errors.append(f"{locale}: {f} uses the medical word '{w}' outside a negation: {sent.strip()[:60]}")
    kw = fields.get("keywords", "")
    if ", " in kw or " ," in kw:
        errors.append(f"{locale}: keywords has a space next to a comma (wasted characters)")
    words = [w for w in kw.split(",") if w]
    dup = {w for w in words if words.count(w) > 1}
    if dup:
        errors.append(f"{locale}: duplicate keywords {sorted(dup)}")
    # words already indexed through the name / subtitle need not be repeated
    indexed = (fields.get("name", "") + " " + fields.get("subtitle", "")).lower().split()
    for w in words:
        if w.lower() in indexed:
            warns.append(f"{locale}: keyword '{w}' is already in the name / subtitle")
    if "apple" in kw.lower():
        errors.append(f"{locale}: keywords contain 'apple' (use descriptive words, not another company's mark)")
    return errors, warns


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="render into docs/appstore/out/<locale>/")
    args = ap.parse_args()
    env_name = os.environ.get("APP_NAME", "").strip()
    app_name = env_name or "X" * LIMITS["name"]   # worst case when the name is not decided
    server_url = os.environ.get("SERVER_URL", "").rstrip("/") or "https://example.invalid"
    errors: list[str] = []
    warns: list[str] = []
    if env_name:
        if "whoop" in env_name.lower():
            errors.append("APP_NAME contains WHOOP (trademark): choose another name")
        if len(env_name) > LIMITS["name"]:
            errors.append(f"APP_NAME is {len(env_name)} characters, the limit is {LIMITS['name']}")
    else:
        print(f"APP_NAME not set: counting the name as {LIMITS['name']} characters (worst case)")
    locales = sorted(p.name for p in LISTING.iterdir() if p.is_dir())
    for loc in locales:
        fields = load(loc, app_name, server_url)
        e, w = check(loc, fields)
        errors += e
        warns += w
        if args.write and not e:
            d = OUT / loc
            d.mkdir(parents=True, exist_ok=True)
            for f, text in fields.items():
                (d / f"{f}.txt").write_text(text + "\n", encoding="utf-8")
    for w in warns:
        print("warning:", w)
    for e in errors:
        print("ERROR:", e)
    if errors:
        return 1
    print("all limits ok" + (f"; rendered into {OUT}" if args.write else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
