"""Whose data a /v1/users/{user}/… request is about, and whether the caller may touch it: the one place for it.

Every endpoint with {user} in its path calls one of these (tests/test_auth_boundary.py checks the behaviour):
- resolve_owner: reads that shares may open. 'me' or the caller's own id = the caller; someone else needs a live share
  with the scope (auth.can_view). 404 for anything that is not 'me' or a number, 403 without the share.
- require_self: own data only (settings, tokens, sync, anything shares never cover). Someone else's id is 403 whatever
  is shared; write=True also needs the write scope on the caller's login.
Each module passes its own 403 text, so what users see does not change.
"""
from fastapi import HTTPException

from . import auth

NOT_A_USER = "用户写 me 或数字 ID"
NO_WRITE = "这个 token 没有 write 权限"


def parse_user(viewer: dict, user: str) -> int:
    """The {user} path segment -> a user id ('me' = the caller). 404 for anything else."""
    if user == "me":
        return viewer["id"]
    try:
        return int(user)
    except ValueError:
        raise HTTPException(404, NOT_A_USER)


def resolve_owner(conn, viewer: dict, user: str, scope: str) -> int:
    """Read access to `user`'s data of `scope`: the owner id, or 403 without a share that covers it."""
    owner_id = parse_user(viewer, user)
    if not auth.can_view(conn, viewer["id"], owner_id, scope):
        raise HTTPException(403, f"没有查看用户 {owner_id} 的 {scope} 的授权")
    return owner_id


def is_self(viewer: dict, user: str) -> bool:
    return user in ("me", str(viewer["id"]))


def require_self(viewer: dict, user: str, detail: str = "只能看自己的", write: bool = False,
                 write_detail: str = NO_WRITE) -> int:
    """Own data only: the caller's id, or 403 `detail` for anyone else (shares never open these). write=True: the
    caller's login must carry the write scope too (403 `write_detail`)."""
    if not is_self(viewer, user):
        raise HTTPException(403, detail)
    if write and "write" not in (viewer.get("token_scopes") or []):
        raise HTTPException(403, write_detail)
    return viewer["id"]
