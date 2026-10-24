"""HTTP endpoints of the circle training feed (friends/feed.py), under /v1/friends/feed. Behind the `friends` and `circle_feed`
flags (404 while either is off for the caller). Reading needs a normal sign-in; ❤️, comments, reports and "seen" need a write
token. There is deliberately no way to delete or edit a post."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import auth
from ..records.api import _self, current_user, get_conn
from ..rollout import flags
from . import feed, params as P, store

router = APIRouter(prefix="/v1/friends/feed", tags=["friends"],
                   dependencies=[Depends(flags.require(auth.CIRCLES_FLAG)), Depends(flags.require(P.FEED_FLAG))])


def _run(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except store.FriendsError as e:
        raise HTTPException(e.status, str(e))


class CommentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(max_length=2000)   # the real limit (P.COMMENT_MAX) is checked in feed.clean_comment with a readable message


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=500)


@router.get("")
def posts(before: str | None = None, limit: int = P.FEED_PAGE, circle: int | None = None, conn=Depends(get_conn),
          viewer=Depends(current_user)):
    """`circle` (flag circles_by_circle): one circle's feed; without it, every circle merged."""
    return _run(feed.feed, conn, viewer["id"], before, limit, circle)


@router.get("/unread")
def unread(conn=Depends(get_conn), viewer=Depends(current_user)):
    return {"count": feed.unread(conn, viewer["id"])}


@router.post("/seen", status_code=204)
def seen(conn=Depends(get_conn), viewer=Depends(current_user)):
    feed.mark_seen(conn, _self(viewer, "me"))
    return Response(status_code=204)


@router.put("/posts/{post_id}/like")
def like(post_id: int, circle: int | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(feed.like, conn, _self(viewer, "me"), post_id, circle)


@router.delete("/posts/{post_id}/like")
def unlike(post_id: int, circle: int | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(feed.unlike, conn, _self(viewer, "me"), post_id, circle)


@router.get("/posts/{post_id}/comments")
def comments(post_id: int, circle: int | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    return {"comments": _run(feed.comments, conn, viewer["id"], post_id, circle)}


@router.post("/posts/{post_id}/comments", status_code=201)
def comment(post_id: int, body: CommentIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(feed.add_comment, conn, _self(viewer, "me"), post_id, body.body)


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(comment_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    _run(feed.delete_comment, conn, _self(viewer, "me"), comment_id)
    return Response(status_code=204)


@router.post("/comments/{comment_id}/report")
def report(comment_id: int, body: ReportIn | None = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _run(feed.report_comment, conn, _self(viewer, "me"), comment_id, body.reason if body else None)
