"""`body-engine static-bundle-pack --out DIR [--since N]`: write the bundle for shipping inside the app (and for a CDN / release asset).

DIR gets `bundle-v<version>.json.gz` (everything), with --since N also `delta-v<N>-to-v<version>.json.gz`, and `manifest.json` (version,
modules, attribution, and for every file its size, uncompressed size and sha256). Same data, same bytes. The app ships the full file
as an asset; `GET /v1/static-bundle?since=<its version>` brings it up to date. The flags in a packed bundle say the openGym
exercises are visible (the launch state); the server tells the app otherwise in the manifest when the emergency flag is off.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import psycopg

from . import build

PACK_FLAGS = {"exercise_opengym": True}


def _write(path: Path, doc: dict) -> dict:
    data = build.encode(doc)
    path.write_bytes(data)
    return {"file": path.name, "bytes": len(data), "uncompressed_bytes": len(gzip.decompress(data)), "sha256": hashlib.sha256(data).hexdigest()}


def pack(conn: psycopg.Connection, out: Path, since: int | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    version = build.current_version(conn)
    files = [{"kind": "full", "version": version, **_write(out / f"bundle-v{version}.json.gz", build.document(conn, None, flags=PACK_FLAGS))}]
    if since:
        if since >= version:
            raise ValueError(f"--since {since} is not below the current version {version}")
        files.append({"kind": "delta", "since": since, "version": version,
                      **_write(out / f"delta-v{since}-to-v{version}.json.gz", build.document(conn, since, flags=PACK_FLAGS))})
    man = {**build.manifest(conn, flags=PACK_FLAGS), "files": files}
    (out / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return man
