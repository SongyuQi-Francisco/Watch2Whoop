"""Static data bundle endpoints (rollout flag static_bundle: 404 while it is off). Any signed-in user; nothing personal in the answer.

- GET /v1/static-bundle/manifest: {format, version, modules {name: {rev, count | hash}}, attribution, flags}. A few hundred bytes;
  the app compares `version` with the one it keeps.
- GET /v1/static-bundle?since=N: everything (no `since`, 0, or a version ahead of the server's: `full` true) or what changed after
  version N: modules.exercises {upsert: [rows], delete: [slugs]} plus every code module (taxonomy, glossary, split_templates) whose
  rev is above N, each whole. Gzip when the client accepts it; ETag = version + since + flags, so an unchanged answer is a 304.
`flags` carries the switches the app has to respect on its local copy: exercise_opengym false = hide exercises whose source is in
taxonomy.imported_sources (the emergency switch of the openGym import; the rows stay in the bundle so the version axis does not move).
Format and content: build.py. Header `X-Static-Bundle-Version` repeats the version.
"""
import gzip
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from ..records.api import current_user, get_conn
from ..rollout import flags
from ..training import catalog
from . import FLAG, build

router = APIRouter(prefix="/v1/static-bundle", tags=["static-bundle"], dependencies=[Depends(flags.require(FLAG))])
_FULL: dict[tuple, bytes] = {}       # (database, version, flags) -> gzip bytes of the full bundle (it is the same for every caller)


def _user_flags(conn, viewer: dict) -> dict:
    return {"exercise_opengym": catalog.imported_visible(conn, viewer["id"])}


@router.get("/manifest")
def get_manifest(conn=Depends(get_conn), viewer=Depends(current_user)):
    return build.manifest(conn, flags=_user_flags(conn, viewer))


@router.get("")
def get_bundle(request: Request, since: Annotated[int | None, Query(ge=0)] = None, conn=Depends(get_conn), viewer=Depends(current_user),
               if_none_match: Annotated[str | None, Header()] = None):
    fl = _user_flags(conn, viewer)
    version = build.current_version(conn)
    full = not since or since > version
    etag = f'"{version}-{0 if full else since}-{int(fl["exercise_opengym"])}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Accept-Encoding", "X-Static-Bundle-Version": str(version)}
    if if_none_match == etag:
        return Response(status_code=304, headers=headers)
    key = (conn.info.dbname, version, fl["exercise_opengym"])
    body = _FULL.get(key) if full else None
    if body is None:
        body = build.encode(build.document(conn, None if full else since, flags=fl))
        if full:
            _FULL.clear()          # one entry is enough: only the newest version is asked for
            _FULL[key] = body
    if "gzip" in (request.headers.get("accept-encoding") or ""):
        return Response(body, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(gzip.decompress(body), media_type="application/json", headers=headers)
