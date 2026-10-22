"""Remove a planned exercise (issue #172), from the training page's 「今日训练计划」 card or a future day's plan sheet.

Two scopes, like plan_add.py:
- `day` (default): only that date. A row in training_day_removals; plans.get_week_plan leaves the exercise out of that date
  (template and day-added exercises alike), so the training page, training day v2 (session_log.view) and the AI's
  get_week_plan all see the day without it, and the day lists it under `removed` — a re-plan must not put it back.
  Undo = delete the row (`restore_for_day`). Today and the next OVERRIDE_FUTURE_DAYS days only; whole-session moves stay in
  day_overrides.py.
- `template`: delete it from one session of the active weekly plan (training_plans.days), so it is gone every week; mirrored
  to SparkyFitness like plans.swap_exercise. Needs `confirm=True` (the page asks twice) and never empties a session.
Self only; the HTTP layer takes the user from the token.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import psycopg

from ..i18n import L
from . import catalog, params as P, plan_add, plans

SCOPES = ("day", "template")


def removals_for(conn: psycopg.Connection, user_id: int, start: date, end: date) -> dict[date, list[dict]]:
    out: dict[date, list[dict]] = {}
    for r in conn.execute("SELECT date, slug, name_zh, name_en FROM training_day_removals WHERE user_id = %s AND date BETWEEN %s AND %s"
                          " ORDER BY created_at, slug", (user_id, start, end)):
        out.setdefault(r["date"], []).append({"slug": r["slug"], "name_zh": r["name_zh"], "name_en": r["name_en"]})
    return out


def _check_day(conn: psycopg.Connection, user_id: int, day: date) -> None:
    today = plans._user_today(conn, user_id)
    hi = today + timedelta(days=P.OVERRIDE_FUTURE_DAYS)
    if not today <= day <= hi:
        raise ValueError(L(f"只能改今天到 {hi} 之间的计划", f"Only plans from today to {hi} can be changed"))


def _planned_slugs(conn: psycopg.Connection, user_id: int, day: date) -> dict[str, dict]:
    wk = plans.get_week_plan(conn, user_id, user_id, day, detail=False)
    entry = next((d for d in wk["days"] if d["date"] == day), None)
    return {e["slug"]: e for p in (entry or {}).get("planned", []) for e in p["exercises"] if e.get("slug")}


def remove_for_day(conn: psycopg.Connection, user_id: int, exercise: str, day: date) -> dict:
    _check_day(conn, user_id, day)
    if plans._active_plan_row(conn, user_id) is None:
        raise ValueError(L("当前没有生效的周训练计划", "There is no active weekly plan"))
    ex = catalog.resolve(conn, exercise, user_id=user_id)
    slug = ex["slug"] if ex else exercise
    again = conn.execute("SELECT name_zh, name_en FROM training_day_removals WHERE user_id = %s AND date = %s AND slug = %s",
                         (user_id, day, slug)).fetchone()
    if again:   # already removed for that day (a double tap, a retried request)
        return {"removed": True, "scope": "day", "date": day.isoformat(), "exercise": {"slug": slug, **again}}
    planned = _planned_slugs(conn, user_id, day)
    if slug not in planned:
        raise ValueError(L(f"{day} 的计划里没有这个动作", f"That exercise is not in the plan of {day}"))
    name_zh = (ex or {}).get("name_zh") or planned[slug].get("name") or slug
    name_en = (ex or {}).get("name_en")
    conn.execute("INSERT INTO training_day_removals (user_id, date, slug, name_zh, name_en) VALUES (%s, %s, %s, %s, %s)"
                 " ON CONFLICT DO NOTHING", (user_id, day, slug, name_zh, name_en))
    return {"removed": True, "scope": "day", "date": day.isoformat(), "exercise": {"slug": slug, "name_zh": name_zh, "name_en": name_en}}


def restore_for_day(conn: psycopg.Connection, user_id: int, exercise: str, day: date) -> dict:
    """Undo remove_for_day; a restored exercise comes back where the plan has it."""
    ex = catalog.resolve(conn, exercise, user_id=user_id)
    slug = ex["slug"] if ex else exercise
    n = conn.execute("DELETE FROM training_day_removals WHERE user_id = %s AND date = %s AND slug = %s",
                     (user_id, day, slug)).rowcount
    return {"restored": bool(n), "scope": "day", "date": day.isoformat(), "exercise": {"slug": slug}}


def remove_from_template(conn: psycopg.Connection, user_id: int, exercise: str, day_of_week: int | None = None,
                         session_name: str | None = None, confirm: bool = False, sync_sparky: bool = True,
                         client=None) -> dict:
    if not confirm:
        raise ValueError(L("这会改训练组模板（以后每周都没有这个动作），需要确认", "This edits the session template (the exercise is gone every week); confirmation is required"))
    plan_row = plans._active_plan_row(conn, user_id)
    if plan_row is None:
        raise ValueError(L("当前没有生效的周训练计划", "There is no active weekly plan"))
    ex = catalog.resolve(conn, exercise, user_id=user_id)
    slug = ex["slug"] if ex else exercise
    days = plan_row.get("days") or []
    d_obj = days[plan_add._find_session(days, day_of_week, session_name)]
    exs = d_obj.get("exercises") or []
    kept = [e for e in exs if (e.get("slug") or "") != slug]
    if len(kept) == len(exs):
        raise ValueError(L(f"{plan_add._session_label(d_obj)} 里没有这个动作", f"That exercise is not in {plan_add._session_label(d_obj)}"))
    if not kept:
        raise ValueError(L("训练组至少要留一个动作；要整组不练，请改计划或调整这一天",
                           "A session needs at least one exercise; to skip the whole session, change the plan or the day"))
    d_obj["exercises"] = kept
    conn.execute("UPDATE training_plans SET days = %s::jsonb, updated_at = now() WHERE id = %s",
                 (json.dumps(days, ensure_ascii=False), plan_row["id"]))
    plan_row["days"] = days
    sync = {"synced": False, "reason": "未请求同步"}
    if sync_sparky:
        sync = plans._sync_plan_to_sparky(conn, user_id, plan_row, client=client)
    return {"removed": True, "scope": "template", "plan_id": plan_row["id"], "day_of_week": d_obj["day_of_week"],
            "session_name": d_obj["name"], "exercise": {"slug": slug, "name_zh": (ex or {}).get("name_zh") or slug},
            "volume_by_group": plans.volume_summary(days), "sparky_sync": sync}


def remove(conn: psycopg.Connection, user_id: int, scope: str, exercise: str, *, day: date | None = None, **kw) -> dict:
    if scope == "day":
        if day is None:
            raise ValueError(L("要给日期", "A date is required"))
        return remove_for_day(conn, user_id, exercise, day)
    if scope == "template":
        return remove_from_template(conn, user_id, exercise, **kw)
    raise ValueError(L("scope 只能是 day 或 template", "scope must be day or template"))
