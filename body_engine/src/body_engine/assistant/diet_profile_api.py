"""HTTP endpoints of 「我的饮食档案」 (assistant/diet_profile.py). Reading someone else's needs a share with scope memory (the
sections are profile card items); the goals block additionally needs nutrition and the energy block metrics. Writing is done
by the user's AI (tool update_diet_profile); there is no web form."""
from fastapi import APIRouter, Depends, Query

from ..records.api import _owner, current_user, get_conn
from . import diet_profile

router = APIRouter(prefix="/v1/users/{user}", tags=["diet-profile"])


@router.get("/diet-profile")
def get_diet_profile(user: str, brief: bool = Query(False, description="true = 只要入口一行（版本、更新日期），不读目标和能量"),
                     conn=Depends(get_conn), viewer=Depends(current_user)):
    owner = _owner(conn, viewer, user, "memory")
    if brief:
        return diet_profile.summary(conn, owner, viewer["id"])
    return diet_profile.view(conn, viewer["id"], owner)


@router.get("/diet-constraints")
def get_diet_constraints(user: str, history: bool = Query(False), conn=Depends(get_conn), viewer=Depends(current_user)):
    """The user's own diet constraints (issue #76; empty = no limits), the kinds that can be set with reference values,
    and with history=true every version. Scope nutrition. Writing is done by the user's AI (set_diet_constraint)."""
    from ..nutrition import constraints
    return constraints.listing(conn, _owner(conn, viewer, user, "nutrition"), history)
