"""`body-engine exercises-media-fetch`: download the demo pictures we may use (issue #248) into the deployment's media storage.

Only the high-confidence matches of training/media_matches.json (tools/match_exercise_media.py) are fetched, from:
- wger (https://wger.de, per picture CC-BY-SA 3.0 / 4.0): pictures flagged AI-generated or without an author are skipped;
  the author (license_author / author_history), licence and the exercise page go into the credit;
- Everkinetic (github.com/everkinetic/data at the commit in media_matches.json, CC-BY-SA 4.0): the two pose drawings.
Files land in <media>/exercises/licensed/<source>/ and are served like the other demo pictures (/app/media/exercises/...);
exercise_catalog.media gets one entry per file with its credit. Pictures are stored unchanged (no resizing or cropping), so the
credit needs no change note. Idempotent and resumable: files on disk are not fetched again, a row is only written when its list
changes. Never touches `images` (free-exercise-db) and never passes a picture to any AI.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from . import catalog

MATCHES = Path(__file__).with_name("media_matches.json")
WGER_API = "https://wger.de/api/v2/exerciseinfo/{id}/"
WGER_HOST = "wger.de"
EK_RAW = "https://raw.githubusercontent.com/everkinetic/data/{commit}/dist/png/{id}-{pose}.png"
EK_POSES = ("relaxation", "tension")
LICENSES = {1: ("CC-BY-SA-3.0", "https://creativecommons.org/licenses/by-sa/3.0/"),
            2: ("CC-BY-SA-4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
            3: ("CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
            4: ("CC-BY-4.0", "https://creativecommons.org/licenses/by/4.0/")}
EK_LICENSE = ("CC-BY-SA-4.0", "https://creativecommons.org/licenses/by-sa/4.0/")
MAX_PER_EXERCISE = 4
MAX_BYTES = 8 * 1024 * 1024
MAGIC = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"RIFF": ".webp", b"GIF8": ".gif"}
_SAFE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _get(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Watch2Whoop exercise media fetch"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("too large")
    return data


def _ext(data: bytes) -> str | None:
    return next((e for m, e in MAGIC.items() if data.startswith(m)), None)


def _save(root: Path, rel: str, data: bytes) -> None:
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)


def high_matches(path: Path | None = None) -> tuple[dict, dict[str, list[dict]]]:
    doc = json.loads((path or MATCHES).read_text(encoding="utf-8"))
    by_slug: dict[str, list[dict]] = {}
    for m in doc["matches"]:
        if m["confidence"] == "high" and m["images_usable"]:
            by_slug.setdefault(m["slug"], []).append(m)
    return doc, by_slug


def wger_entries(source_id: int, root: Path, get=_get) -> list[dict]:
    info = json.loads(get(WGER_API.format(id=int(source_id))))
    out = []
    for img in sorted(info.get("images") or [], key=lambda i: (not i.get("is_main"), i["id"])):
        author = img.get("license_author") or ", ".join(img.get("author_history") or [])
        lic = LICENSES.get(img.get("license"))
        url = img.get("image") or ""
        if img.get("is_ai_generated") or not author or not lic or urllib.parse.urlparse(url).hostname != WGER_HOST:
            continue
        uuid = str(img.get("uuid") or "")
        if not _SAFE.match(uuid):
            continue
        have = next((p for p in (root / "licensed" / "wger").glob(f"{uuid}.*") if not p.name.endswith(".tmp")), None)
        if have is None:
            data = get(url)
            ext = _ext(data)
            if ext is None:
                continue
            rel = f"licensed/wger/{uuid}{ext}"
            _save(root, rel, data)
        else:
            rel = f"licensed/wger/{have.name}"
        out.append({"file": rel, "source": "wger", "source_id": int(source_id), "license": lic[0], "license_url": lic[1],
                    "author": author, "origin_url": f"https://wger.de/en/exercise/{int(source_id)}/view/"})
    return out


def everkinetic_entries(source_id: str, commit: str, root: Path, get=_get) -> list[dict]:
    if not _SAFE.match(str(source_id)) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return []
    out = []
    for pose in EK_POSES:
        rel = f"licensed/everkinetic/{source_id}-{pose}.png"
        if not (root / rel).is_file():
            data = get(EK_RAW.format(commit=commit, id=source_id, pose=pose))
            if _ext(data) != ".png":
                continue
            _save(root, rel, data)
        out.append({"file": rel, "source": "everkinetic", "source_id": str(source_id), "license": EK_LICENSE[0],
                    "license_url": EK_LICENSE[1], "author": "Everkinetic (Greg Priday)",
                    "origin_url": f"https://github.com/everkinetic/data/tree/{commit}"})
    return out


def run(conn: psycopg.Connection, *, dry_run: bool = False, matches: Path | None = None, media: Path | None = None,
        get=_get) -> dict:
    doc, by_slug = high_matches(matches)
    root = (media or catalog.media_dir()) / "exercises"
    have = {r["slug"]: r["media"] for r in conn.execute("SELECT slug, media FROM exercise_catalog")}
    report = {"exercises_matched": len(by_slug), "updated": 0, "unchanged": 0, "missing_rows": 0, "failed": [], "dry_run": dry_run}
    if dry_run:
        report["would_fetch"] = sum(len(v) for v in by_slug.values())
        return report
    cache: dict[tuple, list[dict]] = {}
    for slug, ms in sorted(by_slug.items()):
        if slug not in have:
            report["missing_rows"] += 1
            continue
        entries: list[dict] = []
        for m in sorted(ms, key=lambda m: (m["source"] != "wger", m["source"])):   # wger photos first, then the drawings
            key = (m["source"], str(m["source_id"]))
            try:
                if key not in cache:
                    cache[key] = (wger_entries(m["source_id"], root, get) if m["source"] == "wger"
                                  else everkinetic_entries(m["source_id"], doc["everkinetic_commit"], root, get))
            except (OSError, ValueError) as e:
                report["failed"].append(f"{slug} {key}: {type(e).__name__}")
                cache[key] = []
            entries += [e for e in cache[key] if e["file"] not in {x["file"] for x in entries}]
        entries = entries[:MAX_PER_EXERCISE]
        if entries == have[slug]:
            report["unchanged"] += 1
            continue
        conn.execute("UPDATE exercise_catalog SET media = %s, updated_at = now() WHERE slug = %s", (Jsonb(entries), slug))
        report["updated"] += 1
    report["with_media"] = conn.execute("SELECT count(*) AS n FROM exercise_catalog WHERE media <> '[]'").fetchone()["n"]
    report["failed_count"] = len(report["failed"])
    report["failed"] = report["failed"][:20]
    return report
