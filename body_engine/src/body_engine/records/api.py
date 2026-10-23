"""HTTP endpoints for health records. Auth is the same as the other /v1 endpoints: Bearer token, `{user}` may be me;
reading someone else's data needs a shares grant (scope: supplements / labs / journal / events / exercises);
writes only go to oneself, and the token needs write permission."""
import re
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import auth, queries
from . import analysis, defaults, lab_history, store

router = APIRouter(prefix="/v1/users/{user}", tags=["records"])


# Dependencies: fetch the connection pool and current_user from api.py at request time, to avoid circular imports with api.py
def get_conn():
    from .. import api
    with api.pool.connection() as conn:
        yield conn


def current_user(request: Request, conn=Depends(get_conn), authorization: Annotated[str | None, Header()] = None) -> dict:
    from .. import api
    return api.current_user(request, conn, authorization)


def _owner(conn, viewer: dict, user: str, scope: str) -> int:
    try:
        owner_id = viewer["id"] if user == "me" else int(user)
    except ValueError:
        raise HTTPException(404, "用户写 me 或数字 ID")
    if not auth.can_view(conn, viewer["id"], owner_id, scope):
        raise HTTPException(403, f"没有查看用户 {owner_id} 的 {scope} 的授权")
    return owner_id


def _self(viewer: dict, user: str) -> int:
    """Write operations: only for oneself, and the token needs write permission."""
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, "写操作只能写给自己")
    if "write" not in (viewer.get("token_scopes") or []):
        raise HTTPException(403, "这个 token 没有 write 权限")
    return viewer["id"]


def _found(row, what: str):
    if row is None:
        raise HTTPException(404, f"没有这条{what}")
    return row


def _bad_request(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise HTTPException(422, str(e))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def fields(self) -> dict:
        return self.model_dump(exclude_unset=True)


# ---------- Supplements ----------
SupplementStatus = Literal["active", "as_needed", "paused", "stopped", "not_needed"]


class SupplementIn(_Model):
    name: str = Field(min_length=1)
    status: SupplementStatus
    dose: str | None = None
    timing: str | None = None
    ingredients: str | None = None
    started_on: date | None = None
    stopped_on: date | None = None
    rationale: str | None = None
    advice: str | None = None
    journal_key: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]*$", description="对应的日记项，没有会自动建")


class SupplementPatch(_Model):
    name: str | None = Field(None, min_length=1)
    status: SupplementStatus | None = None
    dose: str | None = None
    timing: str | None = None
    ingredients: str | None = None
    started_on: date | None = None
    stopped_on: date | None = None
    rationale: str | None = None
    advice: str | None = None
    journal_key: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]*$")


@router.get("/supplements")
def list_supplements(user: str, status: Annotated[list[SupplementStatus] | None, Query()] = None,
                     conn=Depends(get_conn), viewer=Depends(current_user)):
    return store.list_supplements(conn, _owner(conn, viewer, user, "supplements"), status)


@router.post("/supplements", status_code=201)
def add_supplement(user: str, body: SupplementIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return store.add_supplement(conn, _self(viewer, user), body.fields())


@router.patch("/supplements/{supplement_id}")
def update_supplement(user: str, supplement_id: int, body: SupplementPatch,
                      conn=Depends(get_conn), viewer=Depends(current_user)):
    if body.fields().get("status") is None and "status" in body.fields():
        raise HTTPException(422, "status 不能为空")
    return _found(_bad_request(store.update_supplement, conn, _self(viewer, user), supplement_id, body.fields()), "补剂")


@router.delete("/supplements/{supplement_id}")
def delete_supplement(user: str, supplement_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Entered by mistake / duplicate: removes it and its own journal item. To stop taking it, PATCH status=stopped."""
    return _found(store.delete_supplement(conn, _self(viewer, user), supplement_id), "补剂")


# ---------- Lab results ----------
LabFlag = Literal["H", "L", "abnormal", "normal"]
LabSource = Literal["lab", "home_device"]


class LabIn(_Model):
    collected_on: date
    test_name: str = Field(min_length=1)
    test_key: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]*$", description="跨报告统一的英文键，看趋势用")
    category: str | None = None
    value: float | None = None
    value_text: str | None = None
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    flag: LabFlag | None = None
    lab: str | None = None
    note: str | None = None
    source: LabSource | None = Field(None, description="lab 化验所 / home_device 家用仪器（如尿酸仪）；不填 = lab")
    fasting: bool | None = Field(None, description="是否空腹；null = 不知道")


class LabPatch(_Model):
    collected_on: date | None = None
    test_name: str | None = Field(None, min_length=1)
    test_key: str | None = Field(None, pattern=r"^[a-z][a-z0-9_]*$")
    category: str | None = None
    value: float | None = None
    value_text: str | None = None
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    flag: LabFlag | None = None
    lab: str | None = None
    note: str | None = None
    source: LabSource | None = Field(None, description="lab 化验所 / home_device 家用仪器（如尿酸仪）；不填 = lab")
    fasting: bool | None = Field(None, description="是否空腹；null = 不知道")


@router.get("/labs")
def list_labs(user: str, start: date | None = None, end: date | None = None,
              test: Annotated[list[str] | None, Query(description="test_key 或 test_name，可重复")] = None,
              conn=Depends(get_conn), viewer=Depends(current_user)):
    return store.list_labs(conn, _owner(conn, viewer, user, "labs"), start, end, test)


@router.get("/labs/summary")
def lab_summary(user: str, test: Annotated[list[str] | None, Query()] = None,
                conn=Depends(get_conn), viewer=Depends(current_user)):
    """Latest result of each lab test, comparison with the reference range, and past trend."""
    return store.lab_report(conn, _owner(conn, viewer, user, "labs"), test)


@router.get("/labs/{test_key}/history")
def lab_history_view(user: str, test_key: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Every reading of one test (old -> new): value, date, source (lab / home_device), fasting, status, change from the previous one;
    plus the reference range and which direction is better (records/lab_params.py). Trend page of the Today page."""
    h = lab_history.history(conn, _owner(conn, viewer, user, "labs"), test_key)
    return _found(h, "化验项目")


def _sync_cards(conn, owner: int):
    from ..assistant import status
    status.sync_labs(conn, owner)


@router.post("/labs", status_code=201)
def add_lab(user: str, body: LabIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    f = body.fields()
    if f.get("value") is None and not f.get("value_text"):
        raise HTTPException(422, "value 和 value_text 至少要有一个")
    if f.get("source") is None:
        f.pop("source", None)
    owner = _self(viewer, user)
    row = store.add_lab(conn, owner, f)
    _sync_cards(conn, owner)
    return row


@router.patch("/labs/{lab_id}")
def update_lab(user: str, lab_id: int, body: LabPatch, conn=Depends(get_conn), viewer=Depends(current_user)):
    f = body.fields()
    for k in ("collected_on", "test_name", "source"):
        if k in f and f[k] is None:
            raise HTTPException(422, f"{k} 不能为空")
    owner = _self(viewer, user)
    row = _found(_bad_request(store.update_lab, conn, owner, lab_id, f), "化验结果")
    _sync_cards(conn, owner)
    return row


# ---------- Body events ----------
Precision = Literal["minute", "day", "approx", "month"]


class EventIn(_Model):
    occurred_at: str = Field(description="ISO 8601；不带时区按用户时区")
    title: str = Field(min_length=1)
    time_precision: Precision = "minute"
    category: str | None = None
    body_part: str | None = None
    severity: int | None = Field(None, ge=1, le=5)
    note: str | None = None
    resolved_on: date | None = None


class EventPatch(_Model):
    occurred_at: str | None = None
    title: str | None = Field(None, min_length=1)
    time_precision: Precision | None = None
    category: str | None = None
    body_part: str | None = None
    severity: int | None = Field(None, ge=1, le=5)
    note: str | None = None
    resolved_on: date | None = None


@router.get("/events")
def list_events(user: str, start: date | None = None, end: date | None = None, category: str | None = None,
                conn=Depends(get_conn), viewer=Depends(current_user)):
    owner = _owner(conn, viewer, user, "events")
    end = end or queries.local_today(conn, owner)
    start = start or end - timedelta(days=364)
    return store.list_events(conn, owner, start, end, category)


@router.post("/events", status_code=201)
def add_event(user: str, body: EventIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    return _bad_request(store.add_event, conn, _self(viewer, user), body.fields())


@router.patch("/events/{event_id}")
def update_event(user: str, event_id: int, body: EventPatch, conn=Depends(get_conn), viewer=Depends(current_user)):
    f = body.fields()
    if "time_precision" in f and f["time_precision"] is None:
        raise HTTPException(422, "time_precision 不能为空")
    return _found(_bad_request(store.update_event, conn, _self(viewer, user), event_id, f), "事件")


# ---------- Journal ----------
class JournalItemIn(_Model):
    label_zh: str | None = Field(None, min_length=1)
    kind: Literal["bool", "number"] | None = None
    unit: str | None = None
    category: str | None = None
    description: str | None = None
    sort: int | None = None
    active: bool | None = None


class JournalEntryIn(_Model):
    value: float = Field(description="是否项：1 是 / 0 否；数量项：数值")
    note: str | None = None


KEY_PATTERN = r"^[a-z][a-z0-9_]*$"


@router.get("/journal/items")
def journal_items(user: str, active_only: bool = False, conn=Depends(get_conn), viewer=Depends(current_user)):
    return defaults.with_label(store.list_journal_items(conn, _owner(conn, viewer, user, "journal"), active_only))


@router.put("/journal/items/{key}")
def put_journal_item(user: str, key: str, body: JournalItemIn,
                     conn=Depends(get_conn), viewer=Depends(current_user)):
    """Create or modify a journal item (label_zh is required when creating)."""
    if not re.fullmatch(KEY_PATTERN, key):
        raise HTTPException(422, "key 只能用小写字母、数字、下划线，字母开头")
    return _found(_bad_request(store.upsert_journal_item, conn, _self(viewer, user), key, body.fields()), "日记项")


@router.get("/journal")
def journal(user: str, start: date | None = None, end: date | None = None,
            key: Annotated[list[str] | None, Query()] = None, conn=Depends(get_conn), viewer=Depends(current_user)):
    owner = _owner(conn, viewer, user, "journal")
    end = end or queries.local_today(conn, owner)
    start = start or end - timedelta(days=29)
    return defaults.with_label(store.journal_entries(conn, owner, start, end, key))


@router.put("/journal/{day}/{key}")
def put_journal_entry(user: str, day: date, key: str, body: JournalEntryIn,
                      conn=Depends(get_conn), viewer=Depends(current_user)):
    """Record an item for a day (date = the day the behavior happened). Overwrites if already recorded."""
    return _bad_request(store.log_journal, conn, _self(viewer, user), day, key, body.value, body.note)


@router.delete("/journal/{day}/{key}", status_code=204)
def delete_journal_entry(user: str, day: date, key: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Undo an item for a day (back to "no record")."""
    if not store.delete_journal_entry(conn, _self(viewer, user), day, key):
        raise HTTPException(404, "这天没有记这一项")
    return Response(status_code=204)


@router.get("/journal/analysis")
def journal_analysis(user: str, key: str | None = None, end: date | None = None,
                     days: Annotated[int, Query(ge=14, le=730)] = 90, threshold: float = 0.0, regression: bool = False,
                     conn=Depends(get_conn), viewer=Depends(current_user)):
    """Effect of a behavior on next-day recovery score, resting heart rate and ln HRV (two-group mean comparison). Without key, analyze all enabled journal items.
    regression=true adds the second version (OLS + Newey-West, Benjamini-Hochberg across items) as outcomes[...].regression."""
    owner_id = _owner(conn, viewer, user, "journal")
    if key is None:   # the item carries `label` (its name in the request's language) like the other Today page journal endpoints
        return [{**a, "item": defaults.with_label([a["item"]])[0]} if isinstance(a.get("item"), dict) else a
                for a in analysis.analyze_all(conn, owner_id, end, days, regression)]
    return _found(_bad_request(analysis.analyze, conn, owner_id, key, end, days, threshold, regression), "日记项")


# ---------- Exercise extra info ----------
ExerciseStatus = Literal["active", "caution", "banned"]


class ExerciseIn(_Model):
    name_zh: str | None = None
    name_en: str | None = None
    aliases: list[str] | None = None
    status: ExerciseStatus | None = None
    per_side: bool | None = None
    cues: str | None = None
    reason: str | None = None
    app_exercise_id: str | None = Field(None, description="记录 app（SparkyFitness）里的动作 ID")


@router.get("/exercises")
def list_exercises(user: str, status: Annotated[list[ExerciseStatus] | None, Query()] = None,
                   conn=Depends(get_conn), viewer=Depends(current_user)):
    return store.list_exercises(conn, _owner(conn, viewer, user, "exercises"), status)


@router.put("/exercises/{exercise_ref}")
def put_exercise(user: str, exercise_ref: str, body: ExerciseIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Create or modify exercise extra info (exercise_ref is an English slug, e.g. upright_row)."""
    f = {k: v for k, v in body.fields().items() if v is not None or k in ("cues", "reason", "app_exercise_id")}
    if "aliases" in body.fields() and body.aliases is None:
        f["aliases"] = []
    return store.upsert_exercise(conn, _self(viewer, user), exercise_ref, f)
