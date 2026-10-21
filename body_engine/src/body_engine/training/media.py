"""Download free-exercise-db (Unlicense) once into the deployment's own media storage (issue #41).

- dist/exercises.json of the pinned commit (params.FREE_DB_COMMIT), checked against params.FREE_DB_JSON_SHA256;
- with images: every exercises/<id>/<n>.jpg it references -> <media>/exercises/<id>/<n>.jpg, served by the Today page at
  /app/media/exercises/... (web/api.py). Pages never load images from GitHub.
Idempotent and resumable: files already on disk are skipped.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import catalog, params as P

RAW = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"
_SAFE_IMG = re.compile(r"^(?!\.)[A-Za-z0-9_\-.,()']+/\d+\.jpg$")


def json_path(media: Path | None = None) -> Path:
    return (media or catalog.media_dir()) / "free-exercise-db" / P.FREE_DB_COMMIT / "exercises.json"


def _get(path: str, timeout: float = 60) -> bytes:
    url = RAW.format(repo=P.FREE_DB_REPO, commit=P.FREE_DB_COMMIT, path=urllib.parse.quote(path))
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def fetch_json(media: Path | None = None) -> Path:
    dest = json_path(media)
    if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == P.FREE_DB_JSON_SHA256:
        return dest
    data = _get("dist/exercises.json")
    digest = hashlib.sha256(data).hexdigest()
    if digest != P.FREE_DB_JSON_SHA256:
        raise ValueError(f"free-exercise-db exercises.json sha256 {digest} ≠ {P.FREE_DB_JSON_SHA256}（上游被改过？不导入）")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def fetch_images(src_json: Path, media: Path | None = None, workers: int = 8) -> dict:
    root = (media or catalog.media_dir()) / "exercises"
    wanted = sorted({img for x in json.loads(src_json.read_text(encoding="utf-8")) for img in x.get("images") or []})
    bad = [img for img in wanted if not _SAFE_IMG.match(img)]
    todo = [img for img in wanted if img not in bad and not (root / img).is_file()]
    failed: list[str] = []

    def one(img: str) -> None:
        try:
            data = _get(f"exercises/{img}")
        except OSError as e:
            failed.append(f"{img}: {e}")
            return
        if not data.startswith(b"\xff\xd8"):   # JPEG magic: never store anything else under a .jpg name
            failed.append(f"{img}: not a JPEG")
            return
        dest = root / img
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))
    return {"images": len(wanted), "downloaded": len(todo) - len(failed), "already_there": len(wanted) - len(todo) - len(bad),
            "skipped_unsafe_names": bad[:10], "failed": failed[:10], "failed_count": len(failed), "dir": str(root)}


def fetch(images: bool = False, media: Path | None = None) -> dict:
    src = fetch_json(media)
    out = {"commit": P.FREE_DB_COMMIT, "json": str(src)}
    if images:
        out["images"] = fetch_images(src, media)
    return out
