#!/usr/bin/env python3
"""Build the public, open-source copy of this repository in a fresh directory with a fresh git history.

    python3 tools/export_public.py --out ../watch2whoop-public            # export + scan + git init/commit
    python3 tools/export_public.py --scan ../watch2whoop-public           # scan an existing tree only
    python3 tools/export_public.py --out DIR --verify                     # also py_compile / bash -n / pytest

What it does
1. Takes the tracked files of the current checkout (`git ls-files`; untracked and ignored files never leave).
2. Drops what is private by nature (EXCLUDE: the owner's notes and data migrations, the owner's own server set-up).
3. Replaces files that hold one person's data with fictional examples (tools/public/overrides/, same structure),
   and rewrites a few blocks (BLOCK_PATCHES).
4. Applies the literal replacements from the PRIVATE config (names, e-mail, hostnames, domains, paths, health values),
   which never leaves the private repository: --private, else secrets/public_export.json, else
   tools/public/private_replacements.json (tracked here, excluded from the export), else tools/public/private.local.json.
5. Scans the result and fails on any hit: the private config's replaced strings and `deny` words, the words in
   secrets/shortcut_deny.txt and secrets/public_deny.txt, e-mail addresses, public / Tailscale IPv4 addresses, tailnet host names, token-shaped
   strings (known key prefixes and long high-entropy strings), health numbers that look like a real person's (lab values in
   prose, numbers attributed to the maintainer / "user 1" / "real data"), file names that look like keys or databases, and missing
   licence files (LICENSE = AGPL-3.0, apps/watch2whoop/LICENSE and shortcuts/LICENSE = MIT; copied verbatim).
   Findings show the file, line and rule, never the matched private word itself.
6. `git init` + one commit (author from --author, default a neutral placeholder) unless --no-git.

Nothing is pushed or published. Standard library only.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Private deny lists under secrets/ (never in git; one word or phrase per line, # comments): the Shortcut's words, and
# the maintainer's own health values / personal details that must never appear in the public repository.
DENY_FILES = ("shortcut_deny.txt", "public_deny.txt")
PUBLIC = ROOT / "tools" / "public"
OVERRIDES = PUBLIC / "overrides"

EXCLUDE = [
    "body-management.md",                      # the owner's plan, logs and health records
    "deploy/ali/*",                            # the owner's own cloud server (shares the host with another app)
    "docs/cloud-migration.md",                 # evaluation of that server
    "deploy/sparkyfitness/body_md_data.py",    # the owner's training / food log, transcribed
    "deploy/sparkyfitness/migrate_body_md.py",
    "tools/public/private.local.json",
    "tools/public/private_replacements.json",  # the replacement table itself (it lists the private strings)
    "tools/public/RELEASE.md",                 # maintainers' release checklist
]
# path -> override file under tools/public/overrides/ (same relative path)
OVERRIDE_FILES = [
    "CLAUDE.md",
    "body_engine/src/body_engine/records/md_seed.py",
    "body_engine/src/body_engine/assistant/memory_seed.py",
]
SPARKLES = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 18a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2'
            'a2 2 0 0 1 -2 2zm0 -12a2 2 0 0 1 2 2a2 2 0 0 1 2 -2a2 2 0 0 1 -2 -2a2 2 0 0 1 -2 2zm-7 12a6 6 0 0 1 6 -6a6 6 0 0 1 '
            '-6 -6a6 6 0 0 1 -6 6a6 6 0 0 1 6 6z" fill="none" stroke="currentColor" stroke-width="1.7" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>')
# Screenshots that show the private deployment: (x0, y0, x1, y1) in pixels of the 420-px-wide published image (scaled
# for the sources in src/). "text" paints the background colour sampled just right of the box and writes a
# placeholder; "sparkle" swaps the Jarvis glyph for a neutral sparkle. Re-check by eye whenever screenshots change:
# the scan cannot read text inside images.
HANDBOOK_IMAGES = "docs/images/tester-handbook"
IMAGE_EDITS = {
    "add-home": [("text", 92, 228, 362, 257, "https://your-server.example.com/app/", (176, 176, 182), False)],
    "safari-login": [("text", 133, 838, 284, 868, "your-server.example.com", (20, 20, 20), True)],
    "safari-share": [("text", 108, 113, 300, 139, "your-server.example.com", (140, 140, 146), False)],
    "consent": [("text", 221, 270, 356, 295, "（自托管服务器）", (40, 40, 40), False)],
    "today": [("sparkle", 170, 789, 197, 817)],
}

# (path, regex, replacement): structural rewrites that do not need any private string in this file
BLOCK_PATCHES = [
    ("body_engine/src/body_engine/assistant/status.py", r"SEED_ITEMS = \[.*?\n\]\n", '''SEED_ITEMS = [  # Example initial judgement items for `memory-init --from-md` (maintained by the AI coach afterwards)
    {"key": GOAL_KEY, "label": "目标体重", "value": "75", "unit": "kg", "tone": "neutral", "trend": None,
     "good_direction": None, "group": "目标", "spark": None, "note": "示例"},
    {"key": "phase", "label": "当前阶段", "value": "减脂增肌", "unit": None, "tone": "neutral", "trend": None,
     "good_direction": None, "group": "训练", "spark": None, "note": "示例"},
]
'''),
    (".gitmodules", r"url = .*open-wearables(\.git)?", "url = https://github.com/the-momentum/open-wearables.git"),
    # Jarvis button: the private deployment uses a fan-made Iron Man glyph (Marvel trademark); the public copy uses the
    # neutral "sparkles" icon from Tabler Icons (MIT). The name "Jarvis" stays (a common first name, no Marvel imagery).
    ("body_engine/src/body_engine/web/static/app.js", r"    // Jarvis icon: [^\n]*\n    jarvis: `<svg[^`]*`,\n",
     "    // Jarvis icon: \"sparkles\" from Tabler Icons (MIT, https://tabler.io/icons)\n    jarvis: `" + SPARKLES + "`,\n"),
    ("body_engine/src/body_engine/web/static/index.html", r"      <!-- Jarvis icon: [^\n]*-->\n      <svg[^\n]*</svg>\n",
     "      <!-- Jarvis icon: \"sparkles\" from Tabler Icons (MIT, https://tabler.io/icons) -->\n      " + SPARKLES + "\n"),
    ("docs/LICENSES.md", r"- \*\*Jarvis button icon\*\*[^\n]*\n",
     "- **Jarvis button icon** — \"sparkles\" from [Tabler Icons](https://tabler.io/icons), MIT.\n"),
]
# Licence files: copied verbatim and required by the scan; the copyright holder is "Watch2Whoop contributors" (no personal names)
LICENSES = {"LICENSE": "GNU AFFERO GENERAL PUBLIC LICENSE", "apps/watch2whoop/LICENSE": "MIT License",
            "shortcuts/LICENSE": "MIT License"}
COPYRIGHT_LINE = re.compile(r"^Copyright \(c\) \d{4} Watch2Whoop contributors$")
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".sh", ".js", ".css", ".html", ".sql",
                 ".dart", ".swift", ".hujson", ".xcconfig", ".example", ".conf", ".service", ".timer", ".plist",
                 ".webmanifest", ".gitignore", ".gitmodules", ".lock", ".pbxproj", ".xcscheme", ".storyboard",
                 ".entitlements", ".xcworkspacedata", ".svg", ".cfg", ".ini", ".env"}

# ------------------------------------------------------------------ scan rules
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
EMAIL_OK_DOMAINS = re.compile(r"(^|\.)(example\.(com|org|net)|example|invalid|test|localhost|anthropic\.com)$")
EMAIL_OK = {"someone@icloud.com", "p@api.x.com"}
IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
TAILNET = re.compile(r"\btail[0-9a-f]{6}\b|\b[a-z0-9-]+\.(?!your-tailnet\b|example\b)[a-z0-9-]+\.ts\.net\b")
KEY_PREFIX = re.compile(r"(?<![A-Za-z0-9])(?:be_[A-Za-z0-9_-]{20,}|sk-(?:ant-)?[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}"
                        r"|github_pat_[A-Za-z0-9_]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}"
                        r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{32,}(?![A-Za-z0-9+/_-])")
# Third-party trademarks that must not appear in the public copy (the private deployment's Jarvis icon)
TRADEMARK_DENY = ["ironman", "iron man", "marvel", "钢铁侠", "webhostinghub"]
LOCK_FILES = ("uv.lock", "pubspec.lock", "package-lock.json", "project.pbxproj", "Podfile.lock")
BAD_NAMES = ["*.pem", "*.key", "*.p12", "*.mobileprovision", "*.sqlite", "*.db", ".env", "*.env", "*_password",
             "*api_key*", "*token*.txt", "export.zip", "export.xml"]
BAD_NAME_OK = {"deploy/open-wearables/.env.example"}
# Personal health data (generic heuristics, no private words; also run in CI). The public repository must not carry
# anyone's real health values: design notes describe results in words, tests and examples use fictional numbers.
# - LAB_VALUE: a lab value with its unit in prose (Markdown / HTML / text);
# - SELF + METRIC on one line: a line attributing a health number to a real person (the maintainer, "user 1", "real data");
# - in Markdown, a block (until the next blank line or heading) whose heading / bold lead-in introduces real data or
#   back-fill results: every line with a health number or a table row of 3+ numbers.
# A line that says it is an example (EXAMPLE_MARK) or carries the marker `privacy-ok` (an HTML / code comment) is skipped.
LAB_VALUE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s?(?:μmol/L|umol/L|mmol/L|U/L|mg/dL)(?![\w/])")
SELF_REF = re.compile(r"本人|用户 ?1(?!\d)|\buser 1\b|负责人|部署者|真实数据|本机(?:实测|数据|导出)"
                      r"|my own data|the owner'?s|the maintainer'?s", re.I)
REAL_DATA_BLOCK = re.compile(r"真实数据|回算结果|回填结果|本机\S{0,20}\s?实测|real[- ]data results", re.I)
HEALTH_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s?(?:kg|bpm|ms|岁|cm|μmol/L|umol/L|mmol/L|U/L|mg/dL)(?![\w/])"
                           r"|(?:身体年龄|恢复分?|HRV|SDNN|RMSSD|静息心率|体重|睡眠分|BMI|VO₂max|尿酸|ALT|甘油三酯|体脂"
                           r"|步数|strain|TRIMP|入睡|起床)\s?[:：=]?\s?\d")
NUMBERS_ROW = re.compile(r"(?:(?<![\w.])[−-]?\d+(?:\.\d+)?(?![\w.]).*?){3}")
EXAMPLE_MARK = re.compile(r"示例|例如|例：|虚构|合成|e\.g\.|example|fictional|synthetic|privacy-ok", re.I)
PROSE_SUFFIXES = (".md", ".html", ".txt")
# (path glob, regex on the line): fictional examples the heuristics cannot tell from real values
PERSONAL_DATA_ALLOW = [
    ("docs/tester-handbook*.md", r"430 μmol/L"),                         # handbook chat demo (fictional lab photo)
    ("docs/archive/tester-handbook-*.md", r"430 μmol/L"),
    ("body_engine/src/body_engine/web/static/handbook/*", r"430 μmol/L"),  # rendered from the handbook Markdown
]


def personal_data_findings(rel: str, text: str) -> list[str]:
    """Lines that look like someone's real health values (see the rules above)."""
    out, block = [], False
    md, prose = rel.endswith(".md"), rel.endswith(PROSE_SUFFIXES)
    allow = [rx for g, rx in PERSONAL_DATA_ALLOW if fnmatch.fnmatch(rel, g)]
    for n, line in enumerate(text.splitlines(), 1):
        if md and (not line.strip() or line.lstrip().startswith("#")):
            block = False
        selfref = bool(SELF_REF.search(line))
        if not EXAMPLE_MARK.search(line) and not any(re.search(rx, line) for rx in allow):
            if prose and LAB_VALUE.search(line):
                out.append(f"{rel}:{n}: lab value with unit (use a fictional example and say so, or describe it in words)")
            elif selfref and HEALTH_NUMBER.search(line):
                out.append(f"{rel}:{n}: health number attributed to a real person")
            elif block and (HEALTH_NUMBER.search(line) or (line.lstrip().startswith("|") and NUMBERS_ROW.search(line))):
                out.append(f"{rel}:{n}: health number in a real-data block")
        if md and line.lstrip().startswith(("#", "**")) and REAL_DATA_BLOCK.search(line):
            block = True   # a heading / bold lead-in introducing real data
    return out


# (path glob, regex on the finding's line) for known-harmless hits: test fixtures and documentation examples
SCAN_ALLOW = [
    ("apps/watch2whoop/test/*", r"\b(172\.32\.0\.1|192\.169\.0\.1|100\.64\.\d+\.\d+)\b"),  # private-range boundary tests
    ("body_engine/tests/*", r"\b(1\.2\.3\.4|5\.6\.7\.8)\b"),                                  # dummy client IPs
    ("body_engine/tests/*", r"sk-(ant-)?test-"),                                                # fake API keys
    ("deploy/tailscale/policy.hujson", r"100\.64\.0\.\d+"),                                      # placeholder tailnet IPs
    ("tools/export_public.py", r"100\.64\.0\.0"),
]


def _entropy(s: str) -> float:
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def _public_ip(m: re.Match) -> bool:
    o = [int(x) for x in m.groups()]
    if any(x > 255 for x in o):
        return False
    if o[0] in (0, 10, 127, 255) or o[0] >= 224 or (o[0] == 169 and o[1] == 254):
        return False
    if (o[0] == 172 and 16 <= o[1] <= 31) or (o[0] == 192 and o[1] == 168):
        return False
    if (o[0] == 192 and o[1] == 0 and o[2] == 2) or (o[0] == 198 and o[1] == 51) or (o[0] == 203 and o[1] == 0):
        return False  # documentation ranges
    return True       # includes 100.64.0.0/10 (Tailscale addresses)


def is_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES or path.name in ("Dockerfile", "Podfile", ".dockerignore", ".gitignore"):
        return True
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\0" not in chunk and bool(chunk)


def scan(tree: Path, private: dict, deny_files: list[Path]) -> list[str]:
    words = [w for w in private.get("deny", []) if w]
    words += [src for src, _ in private.get("replace", []) if len(src) >= 4]
    for f in deny_files:
        if f.exists():
            words += [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    words = sorted({w.lower() for w in words}, key=len, reverse=True)
    findings = []
    for rel, first in LICENSES.items():
        lic = tree / rel
        if not lic.is_file():
            findings.append(f"{rel}: missing licence file")
        elif first not in lic.read_text(encoding="utf-8", errors="replace")[:200]:
            findings.append(f"{rel}: licence file does not start with {first!r}")
    for path in sorted(p for p in tree.rglob("*") if p.is_file() and ".git" not in p.relative_to(tree).parts):
        rel = path.relative_to(tree).as_posix()
        if any(ord(c) > 127 for c in rel):
            findings.append(f"{rel}: non-ASCII file name")
        if rel not in BAD_NAME_OK and any(fnmatch.fnmatch(path.name, pat) for pat in BAD_NAMES):
            findings.append(f"{rel}: file name looks like a secret / data file")
        if not is_text(path):
            # binary (e.g. the .shortcut plist, images): still look for private words in its bytes
            raw = path.read_bytes().lower()
            for i, w in enumerate(words):
                if w.encode() in raw:
                    findings.append(f"{rel}: binary file contains private word #{i}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"{rel}: not UTF-8")
            continue
        lock = path.name in LOCK_FILES
        if rel != "tools/export_public.py" and not lock:
            findings += personal_data_findings(rel, text)
        for n, line in enumerate(text.splitlines(), 1):
            if rel in LICENSES and COPYRIGHT_LINE.match(line):
                continue  # the intended copyright notice
            low = line.lower()
            for i, w in enumerate(words):
                if w in low:
                    findings.append(f"{rel}:{n}: private word #{i}")
            if rel != "tools/export_public.py":
                for w in TRADEMARK_DENY:
                    if w in low:
                        findings.append(f"{rel}:{n}: third-party trademark / icon source {w!r}")
            if any(fnmatch.fnmatch(rel, g) and re.search(rx, line) for g, rx in SCAN_ALLOW):
                continue
            for m in EMAIL.finditer(line):
                e = m.group(0).lower()
                if re.search(r"\.(png|jpe?g|svg|pdf|webp)$", e):
                    continue  # asset names such as launch@2x.png
                if e not in EMAIL_OK and not EMAIL_OK_DOMAINS.search(e.split("@", 1)[1]) and not lock:
                    findings.append(f"{rel}:{n}: e-mail address {e.split('@')[0][:2]}…@{e.split('@')[1]}")
            for m in IPV4.finditer(line):
                if _public_ip(m) and not lock:
                    findings.append(f"{rel}:{n}: public / Tailscale IP address {m.group(0)}")
            for m in TAILNET.finditer(line):
                findings.append(f"{rel}:{n}: tailnet host name")
            for m in KEY_PREFIX.finditer(line):
                findings.append(f"{rel}:{n}: key-shaped string ({m.group(0)[:6]}…)")
            if lock:
                continue
            for m in LONG_TOKEN.finditer(line):
                s = m.group(0)
                if (re.fullmatch(r"[0-9a-f]+", s) or re.fullmatch(r"[A-Za-z_/-]+", s) or "/" in s.strip("/")
                        or s.startswith("iVBOR")):  # hex digests / commit ids, identifiers, paths, base64 PNG fixtures
                    continue  # hex digests / commit ids, identifiers and paths
                if (re.search(r"[a-z]", s) and re.search(r"[A-Z]", s) and re.search(r"\d", s)
                        and _entropy(s) >= 4.2):
                    findings.append(f"{rel}:{n}: high-entropy token-shaped string ({s[:4]}…, {len(s)} chars)")
    return findings


# ------------------------------------------------------------------ export
def load_private(arg: str | None) -> tuple[dict, Path | None]:
    cands = [Path(arg)] if arg else [ROOT / "secrets" / "public_export.json", PUBLIC / "private_replacements.json",
                                     PUBLIC / "private.local.json"]
    for c in cands:
        if c.exists():
            return json.loads(c.read_text(encoding="utf-8")), c
    if arg:
        sys.exit(f"private config not found: {arg}")
    return {"replace": [], "deny": []}, None


def deny_files() -> list[Path]:
    env = os.environ.get("BODY_ROOT")
    roots = [ROOT] + ([Path(env)] if env else [])
    return [r / "secrets" / name for r in roots for name in DENY_FILES]


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], check=True, capture_output=True).stdout
    return [f for f in out.decode().split("\0") if f]


def gitlink(path: str) -> str | None:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-s", "--", path], capture_output=True, text=True).stdout
    parts = out.split()
    return parts[1] if parts and parts[0] == "160000" else None


def build(out: Path, private: dict) -> dict:
    replace = [(a, b) for a, b in private.get("replace", [])]
    stats = {"files": 0, "excluded": 0, "overridden": 0, "rewritten": 0, "submodules": {}}
    for rel in tracked_files():
        if any(fnmatch.fnmatch(rel, pat) for pat in EXCLUDE):
            stats["excluded"] += 1
            continue
        sha = gitlink(rel)
        if sha:  # submodule: recorded as a gitlink in the new repository (empty directory until `git submodule update`)
            stats["submodules"][rel] = sha
            (out / rel).mkdir(parents=True, exist_ok=True)
            continue
        src = ROOT / rel
        if not src.is_file():
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if rel in OVERRIDE_FILES:
            text = (OVERRIDES / rel).read_text(encoding="utf-8")
            if "# @@EXERCISES@@" in text:  # keep the (generic) exercise catalogue of the original module
                orig = src.read_text(encoding="utf-8")
                block = orig[orig.index("# ---------- Exercises"):orig.index("# ---------- Journal backfill")]
                text = text.replace("# @@EXERCISES@@\n", block)
            stats["overridden"] += 1
        elif rel in LICENSES:
            shutil.copy2(src, dst)
            stats["files"] += 1
            continue
        elif is_text(src):
            text = src.read_text(encoding="utf-8")
        else:
            shutil.copy2(src, dst)
            stats["files"] += 1
            continue
        for path, pattern, repl in BLOCK_PATCHES:
            if path == rel:
                text, n = re.subn(pattern, lambda _m: repl, text, count=1, flags=re.S)
                if n != 1:
                    sys.exit(f"block patch did not match: {rel} /{pattern[:40]}/")
        before = text
        for a, b in replace:
            text = text.replace(a, b)
        stats["rewritten"] += before != text
        dst.write_text(text, encoding="utf-8")
        shutil.copymode(src, dst)
        stats["files"] += 1
    return stats


def _font(size: int, bold: bool, text: str):
    from PIL import ImageFont
    cjk = any(ord(c) > 0x2E80 for c in text)
    for path in (["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"] if cjk else
                 ["/System/Library/Fonts/HelveticaNeue.ttc", "/System/Library/Fonts/Helvetica.ttc",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]):
        try:
            return ImageFont.truetype(path, size, index=1 if bold and path.endswith(".ttc") and not cjk else 0)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def redact_images(out: Path) -> int:
    """Apply IMAGE_EDITS to the published screenshots and their sources (src/). Needs Pillow."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        sys.exit("Pillow is required to redact the handbook screenshots (macOS /usr/bin/python3 has it; or pip install pillow)")
    n = 0
    for name, edits in IMAGE_EDITS.items():
        for path in (out / HANDBOOK_IMAGES / f"{name}.jpg", out / HANDBOOK_IMAGES / "src" / f"{name}.jpg"):
            if not path.exists():
                sys.exit(f"screenshot listed in IMAGE_EDITS is missing: {path.relative_to(out)}")
            im = Image.open(path).convert("RGB")
            k = im.width / 420
            d = ImageDraw.Draw(im)
            for kind, *c in edits:
                x0, y0, x1, y1 = (round(v * k) for v in c[:4])
                bg = im.getpixel((min(x1 + 3, im.width - 1), (y0 + y1) // 2))
                d.rectangle((x0, y0, x1, y1), fill=bg)
                if kind == "text":
                    text, fg, bold = c[4], c[5], c[6]
                    size = int((y1 - y0) * 0.62)
                    font = _font(size, bold, text)
                    while d.textlength(text, font=font) > (x1 - x0) and size > 6:
                        size -= 1
                        font = _font(size, bold, text)
                    d.text((x0, (y0 + y1) / 2), text, fill=fg, font=font, anchor="lm")
                elif kind == "sparkle":  # one large and one small four-point star
                    cx, cy, r = (x0 + x1) / 2 - (x1 - x0) * 0.08, (y0 + y1) / 2 + (y1 - y0) * 0.06, (x1 - x0) * 0.36
                    for sx, sy, sr in ((cx, cy, r), (cx + r * 1.05, cy - r * 1.05, r * 0.42)):
                        q = sr * 0.28
                        d.polygon([(sx, sy - sr), (sx + q, sy - q), (sx + sr, sy), (sx + q, sy + q), (sx, sy + sr),
                                   (sx - q, sy + q), (sx - sr, sy), (sx - q, sy - q)], fill=(30, 30, 30))
            im.save(path, quality=72 if path.parent.name != "src" else 90, optimize=True)
            n += 1
    return n


def git_commit(out: Path, author: str, submodules: dict) -> str:
    def git(*a, **kw):
        return subprocess.run(["git", "-C", str(out), *a], check=True, capture_output=True, text=True, **kw).stdout
    git("init", "-q", "-b", "main")
    git("add", "-A")
    for path, sha in submodules.items():
        git("update-index", "--add", "--cacheinfo", f"160000,{sha},{path}")
    name, email = re.match(r"\s*(.*?)\s*<(.+)>", author).groups()
    env = {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
           "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}
    subprocess.run(["git", "-C", str(out), "-c", "commit.gpgsign=false", "commit", "-q", "-m",
                    "Watch2Whoop: initial public release"], check=True, env=env)
    return git("rev-parse", "--short", "HEAD").strip()


def verify(out: Path) -> bool:
    ok = True
    for py in out.rglob("*.py"):
        if subprocess.run([sys.executable, "-m", "py_compile", str(py)], capture_output=True).returncode:
            print(f"  ✗ py_compile {py.relative_to(out)}")
            ok = False
    for sh in out.rglob("*.sh"):
        if subprocess.run(["bash", "-n", str(sh)], capture_output=True).returncode:
            print(f"  ✗ bash -n {sh.relative_to(out)}")
            ok = False
    if shutil.which("uv"):
        r = subprocess.run(["uv", "run", "pytest", "-q"], cwd=out / "body_engine", capture_output=True, text=True)
        print("  pytest:", (r.stdout.strip().splitlines() or ["?"])[-1])
        ok &= r.returncode == 0
    return ok


def main() -> None:
    p = argparse.ArgumentParser(description="Export a clean public copy of the repository (nothing is pushed).")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", type=Path, help="new directory to create (must not exist, or be empty)")
    g.add_argument("--scan", type=Path, help="only scan an existing tree")
    p.add_argument("--private", help="private config JSON (default: secrets/public_export.json, tools/public/private_replacements.json)")
    p.add_argument("--author", default="Watch2Whoop contributors <contributors@example.com>")
    p.add_argument("--no-git", action="store_true", help="do not git init / commit")
    p.add_argument("--verify", action="store_true", help="py_compile all .py, bash -n all .sh, run pytest")
    a = p.parse_args()
    private, private_path = load_private(a.private)
    print(f"private config: {private_path or 'none (only generic rules)'}"
          f" — {len(private.get('replace', []))} replacements, {len(private.get('deny', []))} deny words")

    if a.scan:
        findings = scan(a.scan.resolve(), private, deny_files())
    else:
        out = a.out.resolve()
        if out.exists() and any(out.iterdir()):
            sys.exit(f"{out} exists and is not empty")
        if ROOT in out.parents or out == ROOT:
            sys.exit("choose an output directory outside the repository")
        out.mkdir(parents=True, exist_ok=True)
        st = build(out, private)
        st["images"] = redact_images(out)
        print(f"exported {st['files']} files to {out} (excluded {st['excluded']}, fictional overrides {st['overridden']},"
              f" rewritten {st['rewritten']}, submodules {len(st['submodules'])}, screenshots redacted {st['images']})")
        findings = scan(out, private, deny_files())
    for f in findings:
        print("  ✗", f)
    if findings:
        sys.exit(f"scan FAILED: {len(findings)} finding(s); fix the private config / overrides and export again")
    print("scan passed: no private words, e-mail addresses, public IPs, tailnet names or token-shaped strings")
    if a.out:
        if a.verify and not verify(out):
            sys.exit("verify FAILED")
        if not a.no_git:
            print("git:", git_commit(out, a.author, st["submodules"]), "(fresh history, nothing pushed)")


if __name__ == "__main__":
    main()
