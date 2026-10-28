"""The JS scoring bundle for the iOS app (issue #340 N1): packages/openbodyscore-js/dist, served as it is in the repository.

The algorithms are the server's (Python); the bundle is their port, kept equal by the cross-language golden tests. The app
downloads the bundle named by the manifest, checks its sha256 and runs it in JavaScriptCore. Nothing here computes anything.
"""
import hashlib
import json
import os
import re
from pathlib import Path

# repo checkout: <repo>/body_engine/src/body_engine/algo -> <repo>/packages/...; Docker image: /app/src/body_engine/algo ->
# /packages/... (the build context `openbodyscore_js`, see body_engine/Dockerfile). BODY_ALGO_JS_DIR overrides both.
DEFAULT_DIR = Path(__file__).resolve().parents[4] / "packages" / "openbodyscore-js" / "dist"
FILE = "openbodyscore.js"
VERSION_RE = re.compile(r'BUNDLE_VERSION: "([^"]+)"')
_cache: dict = {}


class Unavailable(Exception):
    """No bundle in this deployment, or the files do not agree with each other."""


def dist_dir() -> Path:
    return Path(os.environ.get("BODY_ALGO_JS_DIR") or DEFAULT_DIR)


def bundle() -> tuple[dict, bytes]:
    """(manifest, file bytes) of the bundle. The manifest is dist/manifest.json, checked against the file itself: its
    sha256 and size, and the version written inside the file. Cached until either file changes."""
    d = dist_dir()
    js, man = d / FILE, d / "manifest.json"
    try:
        key = (str(d), js.stat().st_mtime_ns, man.stat().st_mtime_ns)
    except OSError as e:
        raise Unavailable(f"no bundle in {d}") from e
    if _cache.get("key") == key:
        return _cache["manifest"], _cache["body"]
    body = js.read_bytes()
    manifest = json.loads(man.read_text())
    inside = VERSION_RE.search(body[:4096].decode("utf-8", "replace"))
    if hashlib.sha256(body).hexdigest() != manifest.get("sha256") or len(body) != manifest.get("size") \
            or inside is None or inside.group(1) != manifest.get("version"):
        raise Unavailable("dist/manifest.json does not match dist/openbodyscore.js (rebuild the bundle)")
    _cache.update(key=key, manifest=manifest, body=body)
    return manifest, body


def file_name(version: str) -> str:
    return f"openbodyscore-{version}.js"
