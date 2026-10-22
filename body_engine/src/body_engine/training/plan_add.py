"""Add an exercise to the training plan from the exercise detail (issue #154, rollout flag exercise_add_to_plan).

Two scopes:
- `template`: append to one session of the active weekly plan (training_plans.days, e.g. 上肢 B), so it is there every week;
  mirrored to SparkyFitness like plans.swap_exercise.
- `day`: only one date. Stored in training_day_extras next to the day changes (day_overrides.py: those move whole sessions;
  this adds one exercise), and plans.get_week_plan shows it on that date as an extra session — so the training page,
  training day v2 (session_log.view) and the AI's get_week_plan all see it.
A banned exercise (exercise_meta) is refused in both scopes with the same rule as swap_exercise; `options()` tells the page
which sessions / days to offer and the default sets / reps (params.ADD_*), without writing anything.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import psycopg

from ..i18n import L
from . import catalog, params as P, plans

SCOPES = ("template", "day")
EXTRA_SESSION_ZH, EXTRA_SESSION_EN = "加入的动作", "Added exercises"


def extra_session_name() -> str:
    return L(EXTRA_SESSION_ZH, EXTRA_SESSION_EN)


def defaults(ex: dict) -> dict:
    """Sets / reps / duration the page pre-fills: 3 x 8-12 for weights and reps, a fixed time for cardio and stretching."""
    if ex.get("modality") == "duration":
        return {"sets": 1 if ex.get("category") == "cardio" else P.ADD_DEFAULT_SETS, "rep_low": None, "rep_high": None,
                "duration_sec": P.ADD_DEFAULT_DURATION_SEC.get(ex.get("category") or "", 30),
                "rest_sec": P.ADD_DEFAULT_REST_SEC}
    lo, hi = P.ADD_DEFAULT_REPS
    return {"sets": P.ADD_DEFAULT_SETS, "rep_low": lo, "rep_high": hi, "duration_sec": None, "rest_sec": P.ADD_DEFAULT_REST_SEC}


def _resolve(conn: psycopg.Connection, user_id: int, exercise: str, *, allow_banned: bool = False) -> dict:
    catalog.ensure_seeded(conn)
    ex = catalog.resolve(conn, exercise, user_id=user_id)
    if ex is None:
        near = [c["name_zh"] for c in catalog.search(conn, user_id=user_id, query=exercise, limit=5)]
        raise ValueError(L(f"动作库里找不到「{exercise}」" + (f"；相近动作：{'、'.join(near)}" if near else ""),
                           f"No exercise “{exercise}” in the library" + (f"; similar: {', '.join(near)}" if near else "")))
    if ex["status"] == "banned" and not allow_banned:
        alts = catalog.recommend_alternatives(conn, ex["slug"], user_id=user_id, limit=3)
        why = f"（{ex['reason']}）" if ex.get("reason") else ""
        why_en = f" ({ex['reason']})" if ex.get("reason") else ""
        raise PermissionError(L(
            f"「{ex['name_zh']}」是禁用动作{why}，不能加入训练计划" + (f"；可以改用：{'、'.join(a['name_zh'] for a in alts)}" if alts else ""),
            f"“{ex.get('name_en') or ex['name_zh']}” is a banned exercise{why_en} and can't be added to the plan"
            + (f"; try instead: {', '.join(a.get('name_en') or a['name_zh'] for a in alts)}" if alts else "")))
    return ex


def _prescription(ex: dict, sets, rep_low, rep_high, duration_sec, rest_sec) -> dict:
    """The page's numbers over the recommendation; validated, so a bad request is a 422 and not a stored oddity."""
    d = defaults(ex)
    timed = ex.get("modality") == "duration"
    sets = d["sets"] if sets is None else int(sets)
    if not 1 <= sets <= 20:
        raise ValueError(L("组数要在 1–20 之间", "Sets must be between 1 and 20"))
    if timed:
        dur = d["duration_sec"] if duration_sec is None else int(duration_sec)
        if not 1 <= dur <= 7200:
            raise ValueError(L("时长要在 1 秒到 2 小时之间", "Duration must be between 1 second and 2 hours"))
        lo = hi = None
    else:
        dur = None
        lo = d["rep_low"] if rep_low is None else int(rep_low)
        hi = int(rep_high) if rep_high is not None else (lo if rep_low is not None else d["rep_high"])
        if not (1 <= lo <= 100 and 1 <= hi <= 100) or lo > hi:
            raise ValueError(L("次数要在 1–100 之间，下限不能大于上限", "Reps must be between 1 and 100, low not above high"))
    rest = d["rest_sec"] if rest_sec is None else int(rest_sec)
    if not 0 <= rest <= 600:
        raise ValueError(L("休息时间要在 0–600 秒之间", "Rest must be between 0 and 600 seconds"))
    return {"slug": ex["slug"], "sets": sets, "rep_low": lo, "rep_high": hi, "rest_sec": rest, "duration_sec": dur,
            "weight_kg": None, "note": "每侧" if ex.get("per_side") else None}


def _session_label(d_obj: dict) -> str:
    return f"{plans.WEEKDAY_ZH.get(int(d_obj.get('day_of_week') or 0), '')} {d_obj.get('name') or ''}".strip()


def _find_session(days: list[dict], day_of_week: int | None, session_name: str | None) -> int:
    hits = [i for i, d in enumerate(days)
            if (day_of_week is None or int(d.get("day_of_week") or 0) == int(day_of_week))
            and (not session_name or catalog.norm(session_name) == catalog.norm(d.get("name") or ""))]
    if not hits:
        raise ValueError(L("计划里没有这个训练组，可选：" + "、".join(_session_label(d) for d in days),
                           "No such session in the plan; choose one of: " + ", ".join(_session_label(d) for d in days)))
    if len(hits) > 1:
        raise ValueError(L("有多个训练组符合，请指定星期几和名称：" + "、".join(_session_label(days[i]) for i in hits),
                           "Several sessions match; give the weekday and name: " + ", ".join(_session_label(days[i]) for i in hits)))
    return hits[0]


def add_to_template(conn: psycopg.Connection, user_id: int, exercise: str, day_of_week: int | None = None,
                    session_name: str | None = None, sets=None, rep_low=None, rep_high=None, duration_sec=None,
                    rest_sec=None, sync_sparky: bool = True, client=None) -> dict:
    ex = _resolve(conn, user_id, exercise)
    plan_row = plans._active_plan_row(conn, user_id)
    if plan_row is None:
        raise ValueError(L("当前没有生效的周训练计划，先创建一个周计划", "There is no active weekly plan yet; create one first"))
    days = plan_row.get("days") or []
    idx = _find_session(days, day_of_week, session_name)
    d_obj = days[idx]
    if any((e.get("slug") or "") == ex["slug"] for e in d_obj.get("exercises") or []):
        raise ValueError(L(f"「{ex['name_zh']}」已经在 {_session_label(d_obj)} 里了",
                           f"“{ex.get('name_en') or ex['name_zh']}” is already in {_session_label(d_obj)}"))
    item = plans._normalize_item(conn, user_id, _prescription(ex, sets, rep_low, rep_high, duration_sec, rest_sec), with_meta=True)
    d_obj.setdefault("exercises", []).append(item)
    conn.execute("UPDATE training_plans SET days = %s::jsonb, updated_at = now() WHERE id = %s",
                 (json.dumps(days, ensure_ascii=False), plan_row["id"]))
    plan_row["days"] = days
    sync = {"synced": False, "reason": "未请求同步"}
    if sync_sparky:
        sync = plans._sync_plan_to_sparky(conn, user_id, plan_row, client=client)
    return {"added": True, "scope": "template", "plan_id": plan_row["id"], "day_of_week": d_obj["day_of_week"],
            "weekday_zh": plans.WEEKDAY_ZH.get(int(d_obj["day_of_week"])), "session_name": d_obj["name"],
            "exercise": {"slug": ex["slug"], "name_zh": ex["name_zh"], "name_en": ex.get("name_en"), "status": ex["status"]},
            "sets_text": item["sets_text"], "volume_by_group": plans.volume_summary(days), "sparky_sync": sync}


def check_day(conn: psycopg.Connection, user_id: int, d: date) -> date:
    today = plans._user_today(conn, user_id)
    lo, hi = today - timedelta(days=P.OVERRIDE_PAST_DAYS), today + timedelta(days=P.OVERRIDE_FUTURE_DAYS)
    if not lo <= d <= hi:
        raise ValueError(L(f"只能调整 {lo} 到 {hi} 之间的日子", f"Only days from {lo} to {hi} can be changed"))
    return today


def extras_for(conn: psycopg.Connection, user_id: int, start: date, end: date) -> dict[date, list[dict]]:
    return {r["date"]: r["exercises"] or [] for r in conn.execute(
        "SELECT date, exercises FROM training_day_extras WHERE user_id = %s AND date BETWEEN %s AND %s", (user_id, start, end))}


def add_to_day(conn: psycopg.Connection, user_id: int, exercise: str, day: date, sets=None, rep_low=None,
               rep_high=None, duration_sec=None, rest_sec=None) -> dict:
    ex = _resolve(conn, user_id, exercise)
    check_day(conn, user_id, day)
    if plans._active_plan_row(conn, user_id) is None:
        raise ValueError(L("当前没有生效的周训练计划，先创建一个周计划", "There is no active weekly plan yet; create one first"))
    cur = extras_for(conn, user_id, day, day).get(day, [])
    if any(e.get("slug") == ex["slug"] for e in cur):
        raise ValueError(L(f"「{ex['name_zh']}」已经加在 {day} 了", f"“{ex.get('name_en') or ex['name_zh']}” is already added on {day}"))
    item = _prescription(ex, sets, rep_low, rep_high, duration_sec, rest_sec)
    conn.execute(
        """INSERT INTO training_day_extras (user_id, date, exercises) VALUES (%s, %s, %s::jsonb)
           ON CONFLICT (user_id, date) DO UPDATE SET exercises = EXCLUDED.exercises, updated_at = now()""",
        (user_id, day, json.dumps([*cur, item], ensure_ascii=False)))
    return {"added": True, "scope": "day", "date": day.isoformat(), "weekday_zh": plans.WEEKDAY_ZH[day.isoweekday()],
            "exercise": {"slug": ex["slug"], "name_zh": ex["name_zh"], "name_en": ex.get("name_en"), "status": ex["status"]},
            "sets_text": plans.format_sets_text(item["sets"], item["rep_low"], item["rep_high"], item["duration_sec"], None, item["note"])}


def add(conn: psycopg.Connection, user_id: int, scope: str, exercise: str, *, day_of_week: int | None = None,
        session_name: str | None = None, day: date | None = None, **kw) -> dict:
    if scope == "template":
        return add_to_template(conn, user_id, exercise, day_of_week, session_name, **kw)
    if scope == "day":
        if day is None:
            raise ValueError(L("要给日期", "A date is required"))
        kw.pop("sync_sparky", None)
        kw.pop("client", None)
        return add_to_day(conn, user_id, exercise, day, **kw)
    raise ValueError(L("scope 只能是 template 或 day", "scope must be template or day"))


def options(conn: psycopg.Connection, user_id: int, exercise: str) -> dict:
    """What the add sheet offers: the plan's sessions, the next days, the default numbers and whether the exercise is banned."""
    ex = _resolve(conn, user_id, exercise, allow_banned=True)
    out = {"exercise": {"slug": ex["slug"], "name_zh": ex["name_zh"], "name_en": ex.get("name_en"), "status": ex["status"],
                        "reason": ex.get("reason"), "modality": ex.get("modality")},
           "blocked": ex["status"] == "banned", "defaults": defaults(ex), "plan": None, "sessions": [], "days": []}
    if out["blocked"]:
        out["alternatives"] = [{"slug": a["slug"], "name_zh": a["name_zh"], "name_en": a.get("name_en")}
                               for a in catalog.recommend_alternatives(conn, ex["slug"], user_id=user_id, limit=3)]
        return out
    plan_row = plans._active_plan_row(conn, user_id)
    if plan_row is None:
        return out
    out["plan"] = {"id": plan_row["id"], "name": plan_row["name"]}
    out["sessions"] = [{"day_of_week": d["day_of_week"], "weekday_zh": plans.WEEKDAY_ZH.get(int(d["day_of_week"])), "name": d["name"],
                        "exercise_count": len(d.get("exercises") or []),
                        "has_exercise": any((e.get("slug") or "") == ex["slug"] for e in d.get("exercises") or [])}
                       for d in plan_row.get("days") or []]
    today = plans._user_today(conn, user_id)
    wk: list[dict] = []
    for start in (today, today + timedelta(days=7)):
        wk += plans.get_week_plan(conn, user_id, user_id, start, detail=False)["days"]
    seen: set[date] = set()
    for d in wk:
        if d["date"] < today or d["date"] in seen or len(seen) >= P.ADD_DAY_OPTIONS:
            continue
        seen.add(d["date"])
        has = any(e.get("slug") == ex["slug"] for p in d["planned"] for e in p["exercises"])
        out["days"].append({"date": d["date"].isoformat(), "weekday_zh": d["weekday_zh"], "names": [p["name"] for p in d["planned"]],
                            "rest_day": d["rest_day"], "has_exercise": has})
    return out
