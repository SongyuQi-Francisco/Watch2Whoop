"""POST /v1/sync/push: the app's queued record writes in one request (docs/ios-native-app.md section 5.3).

Each mutation {client_id, kind, op, key, base_version?, body} runs through the same code as the record endpoint it
stands for (same checks, same answers), in its own transaction, and gets one result:
  applied    written; `body` is the endpoint's answer, `version` the (kind, day) version after it
  duplicate  this client_id was applied before: the first answer again, nothing written
  conflict   409 of the endpoint (e.g. this meal already logged today), or base_version is stale where it matters
  rejected   any other 4xx: the app shows it in the "not submitted" list
  error      5xx / the food log unreachable: keep it queued and retry later

Kinds and keys (`key` is what the app's local row is called):
  water           upsert body = POST /water's {ml, source?, at?}; delete key = entry id
  weight          upsert key = date, body = POST /weight's fields (overwrite defaults to true: last write wins)
  journal         key = "YYYY-MM-DD/<item key>"; upsert body {value, note?}; delete. Last write wins; an edit of an entry
                  (base_version given) that was deleted on the server since is a conflict (deletion wins)
  meal_log        upsert body = POST /meal-templates/{template_id}/log's fields + template_id; delete key = meal log id
  recipe_log      upsert body = POST /recipes/{recipe_id}/log's fields + recipe_id
  training_session key = date; upsert body {exercises, notes?, submit?, allow_duplicate?}: the whole day is replaced, so
                  a base_version older than the server's version of that day is a conflict
"""
from __future__ import annotations

import logging
from datetime import date

import psycopg
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from ..i18n import L
from . import idem, store

log = logging.getLogger(__name__)
KINDS = ("water", "weight", "journal", "meal_log", "recipe_log", "training_session")
RECORD_KIND = {"meal_log": "meal_log", "recipe_log": "food"}   # push kind -> the pull stream's record kind


class Reject(Exception):
    def __init__(self, status: int, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _day(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        raise Reject(422, L("key 应该是日期 YYYY-MM-DD", "key must be a date YYYY-MM-DD")) from None


def _int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        raise Reject(422, L("key 应该是数字 id", "key must be a numeric id")) from None


# ---------------------------------------------------------------- handlers: (conn, viewer, mutation) -> (answer, day)
def _water(conn, viewer, m):
    from ..rollout import flags
    from ..water import api as W, params as WP
    if not flags.enabled(conn, viewer["id"], WP.FEATURE):   # the endpoint's own router dependency
        raise Reject(404, L("喝水记录还没有对你开放", "Water logging is not available to you yet"))
    if m["op"] == "delete":
        out = W.water_delete("me", _int(m["key"]), conn=conn, viewer=viewer)
        return out, date.fromisoformat(str(out["deleted"]["date"]))
    out = W.water_add("me", W.WaterIn(**{**m["body"], "client_id": m["client_id"]}), conn=conn, viewer=viewer)
    return out, date.fromisoformat(str(out["entry"]["date"]))


def _weight(conn, viewer, m):
    from ..offline import api as O
    if m["op"] == "delete":
        raise Reject(422, L("体重不能用 push 删除", "Weight cannot be deleted through push"))
    d = _day(m["key"])
    body = {"overwrite": True, **m["body"], "date": d.isoformat(), "client_id": m["client_id"]}
    return O.weight_add("me", O.WeightIn(**body), conn=conn, viewer=viewer), d


def _journal(conn, viewer, m):
    from ..records import api as R
    try:
        day_s, item = m["key"].split("/", 1)
    except (AttributeError, ValueError):
        raise Reject(422, L("journal 的 key 应该是 YYYY-MM-DD/<日记项>", "A journal key is YYYY-MM-DD/<item>")) from None
    d = _day(day_s)
    exists = conn.execute("SELECT 1 FROM journal_entries WHERE user_id = %s AND date = %s AND key = %s",
                          (viewer["id"], d, item)).fetchone() is not None
    if m["op"] == "delete":
        if exists:
            R.delete_journal_entry("me", d, item, conn=conn, viewer=viewer)
        return {"deleted": True, "date": d.isoformat(), "key": item}, d
    base = m.get("base_version")
    if base and not exists and store.version(conn, viewer["id"], "records", "journal", d.isoformat()) > base:
        raise Reject(409, {"message": L("这一项在服务器上已经删掉了", "This entry was deleted on the server"), "conflict": "deleted"})
    return R.put_journal_entry("me", d, item, R.JournalEntryIn(**m["body"]), conn=conn, viewer=viewer), d


def _meal_log(conn, viewer, m):
    from ..assistant import memory_api as M
    if m["op"] == "delete":
        row = conn.execute("SELECT date FROM meal_logs WHERE id = %s AND user_id = %s", (_int(m["key"]), viewer["id"])).fetchone()
        out = M.undo_meal_log("me", _int(m["key"]), conn=conn, viewer=viewer)
        return out, row and row["date"]
    body = dict(m["body"])
    tid = body.pop("template_id", None)
    if tid is None:
        raise Reject(422, L("meal_log 需要 template_id", "meal_log needs template_id"))
    out = M.log_meal_template("me", int(tid), M.MealLogIn(**body), conn=conn, viewer=viewer)
    return out, date.fromisoformat(str(out["date"])) if out.get("date") else None


def _recipe_log(conn, viewer, m):
    from ..assistant import recipes_api as RA
    if m["op"] == "delete":
        raise Reject(422, L("食谱记录用 food 条目删除", "Delete a recipe log through its food entry"))
    body = dict(m["body"])
    rid = body.pop("recipe_id", None)
    if rid is None:
        raise Reject(422, L("recipe_log 需要 recipe_id", "recipe_log needs recipe_id"))
    out = RA.log_recipe("me", int(rid), RA.RecipeLogIn(**body), conn=conn, viewer=viewer)
    return out, date.fromisoformat(str(out["date"])) if isinstance(out, dict) and out.get("date") else None


def _training_session(conn, viewer, m):
    from ..training import api as T
    if m["op"] == "delete":
        raise Reject(422, L("训练日不能用 push 删除", "A training day cannot be deleted through push"))
    d = _day(m["key"])
    base = m.get("base_version")
    current = store.version(conn, viewer["id"], "records", "training_session", d.isoformat())
    if base is not None and current != base:
        raise Reject(409, {"message": L("这一天的训练在别处改过了", "This training day was changed elsewhere"), "conflict": "version", "version": current})
    body = dict(m["body"])
    submit, allow_dup = bool(body.pop("submit", False)), bool(body.pop("allow_duplicate", False))
    out = T.save_training_session("me", d, T.SessionDraftIn(**body), conn=conn, viewer=viewer)
    if submit:
        conn.commit()
        out = T.submit_training_session("me", d, T.SessionSubmitIn(allow_duplicate=allow_dup), conn=conn, viewer=viewer)
    return out, d


HANDLERS = {"water": _water, "weight": _weight, "journal": _journal, "meal_log": _meal_log, "recipe_log": _recipe_log,
            "training_session": _training_session}


def _result(m: dict, status: str, **kw) -> dict:
    return {"client_id": m["client_id"], "kind": m["kind"], "key": m.get("key"), "status": status, **kw}


def apply_one(conn: psycopg.Connection, viewer: dict, m: dict) -> dict:
    uid = viewer["id"]
    scope = f"push:{m['kind']}:{m['op']}"
    try:
        prev = idem.stored(conn, uid, m["client_id"], scope)
    except HTTPException as e:
        return _result(m, "rejected", detail=e.detail)
    if prev is not None:
        return _result(m, "duplicate", **prev["response"])
    try:
        answer, day = HANDLERS[m["kind"]](conn, viewer, m)
        answer = jsonable_encoder(answer)
        kind = RECORD_KIND.get(m["kind"], m["kind"])
        res = {"body": answer, "date": day.isoformat() if day else None,
               "version": store.version(conn, uid, "records", kind, day.isoformat()) if day else 0}
        idem.remember(conn, uid, m["client_id"], scope, 200, res)
        conn.commit()
        return _result(m, "applied", **res)
    except Reject as e:
        conn.rollback()
        return _result(m, "conflict" if e.status == 409 else "rejected", detail=e.detail)
    except HTTPException as e:
        conn.rollback()
        status = "conflict" if e.status_code == 409 else "error" if e.status_code >= 500 else "rejected"
        return _result(m, status, detail=e.detail)
    except ValidationError as e:
        conn.rollback()
        return _result(m, "rejected", detail=jsonable_encoder(e.errors(include_url=False, include_context=False)))
    except Exception as e:  # noqa: BLE001  one mutation must not fail the others; the app retries "error"
        conn.rollback()
        log.exception("sync push %s failed", m["kind"])
        return _result(m, "error", detail=type(e).__name__)


def push(conn: psycopg.Connection, viewer: dict, mutations: list[dict]) -> dict:
    """Apply in the order sent (the app keeps per-group order). Returns {"results": [...], "data_version"}."""
    results = [apply_one(conn, viewer, m) for m in mutations]
    return {"results": results, "data_version": store.data_version(conn, viewer["id"])}
