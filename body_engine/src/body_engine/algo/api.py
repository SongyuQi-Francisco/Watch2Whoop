"""Algorithm bundle endpoints (issue #340 N1). Public: the bundle is the open-source code of this repository, nothing personal.

- GET /v1/algo/manifest: {name, version, sha256, size, algo_versions, url, signature, key_id} (signature: N2, algo/signing.py). The app compares `version` / `sha256` with the
  copy it runs; `url` (relative to the server) downloads the file. Never cached by the client (no-cache + ETag).
- GET /v1/algo/openbodyscore-{version}.js: the file itself, only for the current version (404 for any other: the server
  keeps one). Immutable (the name carries the version); header X-Algo-SHA256 repeats the hash the app must check.
503 when this deployment has no bundle (the build context is missing) or the manifest does not match the file.
"""
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response

from . import Unavailable, bundle, file_name

router = APIRouter(prefix="/v1/algo", tags=["algo"])


def _bundle() -> tuple[dict, bytes]:
    try:
        return bundle()
    except Unavailable:
        raise HTTPException(503, "算法包不可用") from None


@router.get("/manifest")
def manifest(response: Response, if_none_match: Annotated[str | None, Header()] = None):
    m, _ = _bundle()
    sig = m.get("signature") or ""
    etag = f'"{m["sha256"]}{"." + sig[:16] if sig else ""}"'   # signing the same file later changes the ETag
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if if_none_match == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return {"name": m["name"], "version": m["version"], "sha256": m["sha256"], "size": m["size"],
            "algo_versions": m["algo_versions"], "global": m.get("global", "OpenBodyScore"),
            "url": f"/v1/algo/{file_name(m['version'])}",
            # Ed25519 over the file's bytes, by the maintainer's key (algo/signing.py); null = unsigned (Debug apps only)
            "signature": m.get("signature"), "key_id": m.get("key_id")}


@router.get("/openbodyscore-{version}.js")
def download(version: str):
    m, body = _bundle()
    if version != m["version"]:
        raise HTTPException(404, "没有这个版本的算法包")
    return Response(body, media_type="application/javascript; charset=utf-8",
                    headers={"ETag": f'"{m["sha256"]}"', "Cache-Control": "public, max-age=31536000, immutable",
                             "X-Algo-SHA256": m["sha256"], "X-Algo-Version": m["version"]})
