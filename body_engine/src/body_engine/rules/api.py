"""HTTP endpoints for reminders (scope reminders). Reading someone else's needs a shares grant; changing status only works on your own, and the token needs write."""
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from ..records.api import _owner, _self, current_user, get_conn
from . import store
from .engine import RULE_KEYS

router = APIRouter(prefix="/v1/users/{user}", tags=["reminders"])
Status = Literal["new", "seen", "dismissed"]


class ReminderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status


@router.get("/reminders")
def list_reminders(user: str, start: date | None = None, end: date | None = None,
                   status: Annotated[list[Status] | None, Query(description="默认 new + seen")] = None,
                   rule: Annotated[list[str] | None, Query(description=f"规则 key：{' / '.join(RULE_KEYS)}")] = None,
                   evidence: bool = True, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Reminders (by default only the end day = today, excluding those "dismissed"). Each day's evaluation re-lists the reminders that still hold that day,
    so looking at the current day is enough; pass start to see history."""
    end = end or date.today()
    start = start or end
    return store.list_reminders(conn, _owner(conn, viewer, user, "reminders"), start, end,
                                status or ["new", "seen"], rule, evidence)


@router.get("/reminders/runs")
def rule_runs(user: str, start: date | None = None, end: date | None = None,
              conn=Depends(get_conn), viewer=Depends(current_user)):
    """Result of each rule on each evaluate-rules run (fired / not fired / insufficient data / error) and the reason."""
    end = end or date.today()
    return store.rule_runs(conn, _owner(conn, viewer, user, "reminders"), start or end - timedelta(days=6), end)


@router.patch("/reminders/{reminder_id}")
def patch_reminder(user: str, reminder_id: int, body: ReminderPatch, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Change status: seen = viewed / dismissed = no more reminders / new = restore."""
    row = store.set_status(conn, _self(viewer, user), reminder_id, body.status)
    if row is None:
        raise HTTPException(404, "没有这条提醒（或不是你的）")
    return row
