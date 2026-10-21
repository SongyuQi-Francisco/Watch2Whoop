"""HTTP endpoints for the explanation sheets on the Today page (scope per data block, see personal.py).

- GET /v1/users/{user}/explain/metric/{key}?date=  key: recovery, hrv, resting_hr, sleep_hours, sleep_score, sleep_efficiency,
  sleep_debt, sleep_consistency, bedtime, vo2max, strain, weight. The static text is always returned to anyone with some share;
  personal numbers only when the viewer may see that metric's scope.
- GET /v1/users/{user}/explain/status/{key}?date=  one status card item (needs the memory scope, same as /memory).
- GET /v1/users/{user}/explain/lab/{test_key}?date=  one lab test, for the lab trend sheet (needs the labs scope).
- GET /v1/explain/glossary  all static entries (for the butler / other clients).
Texts follow the request language (X-Body-Lang, issue #42); without the header they are Chinese, as the AI clients expect.
"""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ..i18n import L
from ..records.api import _owner, current_user, get_conn
from ..web import api as web_api, views
from . import glossary as G, personal

router = APIRouter(tags=["explain"])


@router.get("/v1/users/{user}/explain/metric/{key}")
def explain_metric(user: str, key: str, day: Annotated[date | None, Query(alias="date")] = None,
                   conn=Depends(get_conn), viewer=Depends(current_user)):
    """What the metric is, how to read it, how it relates to recovery, and advice from the owner's own numbers."""
    if key not in G.METRICS:
        raise HTTPException(404, L(f"没有这个指标的讲解：{key}", f"No explanation for this metric: {key}"))
    owner = web_api.owner(conn, viewer, user)
    return personal.explain_metric(conn, viewer["id"], owner, key, day or views.local_today(conn, owner))


@router.get("/v1/users/{user}/explain/status/{key}")
def explain_status(user: str, key: str = Path(pattern=r"^[a-z][a-z0-9_]*$"),
                   day: Annotated[date | None, Query(alias="date")] = None,
                   conn=Depends(get_conn), viewer=Depends(current_user)):
    """What a status card item means (e.g. 肩 内旋受限), what to watch out for, matching profile card lines and exercise limits."""
    owner = _owner(conn, viewer, user, "memory")
    out = personal.explain_status(conn, viewer["id"], owner, key, day or views.local_today(conn, owner))
    if out is None:
        raise HTTPException(404, L(f"没有这个状态项：{key}", f"No such status item: {key}"))
    return out


@router.get("/v1/users/{user}/explain/lab/{key}")
def explain_lab(user: str, key: str = Path(pattern=r"^[A-Za-z0-9_\u4e00-\u9fff]+$"),
                day: Annotated[date | None, Query(alias="date")] = None,
                conn=Depends(get_conn), viewer=Depends(current_user)):
    """One lab test (opened from the lab trend sheet): what it is, how to read it, latest reading and source, advice (scope labs)."""
    owner = _owner(conn, viewer, user, "labs")
    return personal.explain_lab(conn, viewer["id"], owner, key, day or views.local_today(conn, owner))


@router.get("/v1/explain/glossary")
def glossary(viewer=Depends(current_user)):
    """Static entries only (no personal data)."""
    return G.all_entries()
