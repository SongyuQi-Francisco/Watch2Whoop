"""HTTP endpoints for history import (Today page 「我」 (Me) -> 「导入健康历史」 (Import health history), web/static/history.js). Only your own; changes need write.

- GET    /v1/users/{user}/history-import                 -> {job: latest (or null), chunk_bytes, max_bytes}
- POST   /v1/users/{user}/history-import  {filename, size} -> create or resume (same name and size still uploading)
- PUT    /v1/users/{user}/history-import/{id}/data?offset=N  request body = raw bytes (<= 1.5MB) -> {received_bytes}
- POST   /v1/users/{user}/history-import/{id}/complete   -> queue (the background job body-engine history-import-run takes over)
- DELETE /v1/users/{user}/history-import/{id}            -> cancel (delete file)
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import store

router = APIRouter(tags=["history"])


def get_conn():
    from .. import api
    with api.pool.connection() as conn:
        yield conn


def current_user(request: Request, conn=Depends(get_conn)) -> dict:
    from .. import api
    return api.current_user(request, conn, request.headers.get("authorization"))


def _self(viewer: dict, user: str, write: bool = False) -> int:
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, "只能给自己导入")
    if write and "write" not in (viewer.get("token_scopes") or []):
        raise HTTPException(403, "这个登录没有 write 权限")
    return viewer["id"]


def _call(fn, *args):
    try:
        return fn(*args)
    except store.ImportRefused as e:
        raise HTTPException(e.status, str(e)) from e


def _out(job: dict | None) -> dict:
    return {"job": store.public(job), "chunk_bytes": store.CHUNK_BYTES, "max_bytes": store.max_bytes()}


class CreateIn(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    size: int = Field(gt=0)


@router.get("/v1/users/{user}/history-import")
def status(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _out(store.latest(conn, _self(viewer, user)))


@router.post("/v1/users/{user}/history-import")
def create(user: str, body: CreateIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _out(_call(store.create, conn, _self(viewer, user, True), body.filename, body.size))


@router.put("/v1/users/{user}/history-import/{job_id}/data")
async def data(user: str, job_id: int, offset: int, request: Request, conn=Depends(get_conn),
               viewer=Depends(current_user)):
    uid = _self(viewer, user, True)
    if int(request.headers.get("content-length") or 0) > store.MAX_CHUNK_BYTES:
        raise HTTPException(413, "分片太大")
    body = await request.body()
    job = await run_in_threadpool(_call, store.append, conn, uid, job_id, offset, body)
    return {"received_bytes": job["received_bytes"], "size_bytes": job["size_bytes"]}


@router.post("/v1/users/{user}/history-import/{job_id}/complete")
def complete(user: str, job_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _out(_call(store.complete, conn, _self(viewer, user, True), job_id))


@router.delete("/v1/users/{user}/history-import/{job_id}")
def cancel(user: str, job_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _out(_call(store.cancel, conn, _self(viewer, user, True), job_id))
