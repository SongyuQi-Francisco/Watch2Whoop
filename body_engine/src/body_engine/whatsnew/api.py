"""GET /v1/whats-new?weeks=: what shipped, by calendar week (Monday start), newest first, for the 「本周更新」 sheet of the Today
page. The same list for everyone (nothing personal), any signed-in user; texts in the request's language (X-Body-Lang).
  {"weeks": [{"week_start": "2026-10-26", "items": [{"date": "2026-10-28", "text": "…", "url": "/app/?tab=me" | null}]}]}
Entries dated after the user's today are not shown (they are written before they ship)."""
from fastapi import APIRouter, Depends, Query

from ..assistant import sparky_write
from ..i18n import is_en
from ..records.api import current_user, get_conn
from . import entries as E, params as P

router = APIRouter(tags=["whats-new"])


@router.get("/v1/whats-new")
def whats_new(weeks: int = Query(P.LIST_WEEKS, ge=1, le=P.LIST_WEEKS_MAX), conn=Depends(get_conn), viewer=Depends(current_user)):
    en = is_en()
    today = sparky_write.user_today(conn, viewer["id"])
    return {"weeks": [{"week_start": w["week_start"].isoformat(),
                       "items": [{"date": e.day.isoformat(), "text": e.en if en else e.zh, "url": e.url} for e in w["items"]]}
                      for w in E.weeks(today, weeks)]}
