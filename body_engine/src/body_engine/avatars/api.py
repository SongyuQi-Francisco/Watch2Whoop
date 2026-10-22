"""HTTP endpoints for avatars (#200). The body of an upload is the image file itself (Content-Type image/png, image/jpeg or
image/webp; the type is checked from the file, not trusted from the header), at most images/params.MAX_UPLOAD_BYTES.
Uploading and deleting need the `avatars` flag and a write token; fetching a picture does not (pictures that exist are
shown regardless of the flag, e.g. one the admin set for a circle). Uploads are limited per person (params.py) → 429."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..friends import store as friends
from ..images import params as IP, store as images
from ..records.api import _self, current_user, get_conn
from ..rollout import flags
from ..web import login_limit
from . import params as P, store

router = APIRouter(prefix="/v1/avatars", tags=["avatars"])
_uploads = login_limit.Limiter(P.UPLOADS_PER_WINDOW, P.WINDOW_SECONDS)
_flag = Depends(flags.require("avatars"))


async def _body(request: Request) -> bytes:
    """The request body, refused as soon as it is over the limit (the declared length first, then what actually arrives)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > IP.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"图片太大（最多 {IP.MAX_UPLOAD_BYTES // (1024 * 1024)} MB）")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > IP.MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"图片太大（最多 {IP.MAX_UPLOAD_BYTES // (1024 * 1024)} MB）")
        chunks.append(chunk)
    return b"".join(chunks)


def _limit(uid: int) -> None:
    wait = _uploads.retry_after(f"u:{uid}")
    if wait:
        raise HTTPException(429, "上传太频繁，请稍后再试", headers={"Retry-After": str(wait)})
    _uploads.record(f"u:{uid}")


def _run(fn, *args):
    try:
        return fn(*args)
    except images.ImageError as e:
        raise HTTPException(400, str(e))
    except store.AvatarError as e:
        raise HTTPException(e.status, str(e))


def _image_response(request: Request, img: dict | None) -> Response:
    if img is None:
        raise HTTPException(404, "没有头像")
    etag = f'"{img["sha256"][:32]}"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=bytes(img["data"]), media_type=img["mime"], headers=headers)


@router.put("/me", dependencies=[_flag])
def put_me(data: bytes = Depends(_body), conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, "me")
    _limit(uid)
    return {"avatar": store.url(uid, _run(store.set_user, conn, uid, data))}


@router.delete("/me", status_code=204, dependencies=[_flag])
def delete_me(conn=Depends(get_conn), viewer=Depends(current_user)):
    _run(store.set_user, conn, _self(viewer, "me"), None)
    return Response(status_code=204)


@router.put("/circles/{circle_id}", dependencies=[_flag])
def put_circle(circle_id: int, data: bytes = Depends(_body), conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, "me")
    _limit(uid)
    image_id = _run(store.set_circle, conn, uid, circle_id, data)
    return {"avatar": friends.avatar_url({"id": circle_id, "avatar_image_id": image_id})}


@router.delete("/circles/{circle_id}", status_code=204, dependencies=[_flag])
def delete_circle(circle_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    _run(store.set_circle, conn, _self(viewer, "me"), circle_id, None)
    return Response(status_code=204)


@router.get("/users/{user_id}")
def get_user(user_id: int, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    """A personal avatar (WebP): 404 for anyone who is not that person or in a circle with them, and for no avatar."""
    return _image_response(request, store.get_user_image(conn, viewer["id"], user_id))
