"""Rollout endpoints for the signed-in user (the Today page reads the same data from /v1/web/me; native app screens use these).

- GET /v1/users/{user}/flags: {"flags": [keys on for me], "experiments": {key: variant}} (own only).
- POST /v1/users/{user}/experiments/{key}/exposure: the page showed my variant -> one exposure counted for today
  (the server decides the variant; nothing from the client is stored). {"variant": … or null}.
"""
from fastapi import APIRouter, Depends, HTTPException

from ..i18n import L
from ..records.api import current_user, get_conn
from . import experiments, flags

router = APIRouter(prefix="/v1/users/{user}", tags=["rollout"])


def _own(viewer: dict, user: str) -> int:
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, L("只能看自己的", "Only your own"))
    return viewer["id"]


def for_user(conn, user_id: int) -> dict:
    return {"flags": flags.enabled_keys(conn, user_id), "experiments": experiments.assignments(conn, user_id)}


@router.get("/flags")
def my_flags(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return for_user(conn, _own(viewer, user))


@router.post("/experiments/{key}/exposure")
def exposure(user: str, key: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return {"variant": experiments.variant(conn, _own(viewer, user), key, expose=True)}
