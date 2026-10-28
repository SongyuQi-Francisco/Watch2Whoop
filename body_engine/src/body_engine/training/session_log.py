"""Training day v2 (FB-5, rollout flag training_day_v2): today's planned exercises as cards, logged set by set on the page.

- `view()`: the day's plan (Body Engine plan via plans.get_week_plan, else the SparkyFitness weekly plan) as exercise cards:
  prescription, recommended weight (training/loads.py), restriction status / cues from exercise_meta, a demo thumbnail,
  the last logged session, and a `target` the page pre-fills the set rows from. Plus the page's draft / submitted log.
- `save_draft()`: the sets being filled in (each set its own reps / weight / done), stored per user and day in
  training_session_logs, so the app's WebView and a browser show the same rows and a reload loses nothing.
- `submit()`: the sets marked done become one workout in SparkyFitness through assistant/sparky_write.log_workout, the same
  path the AI's log_workout takes (banned check, duplicate check, the workout's exercise ids); the entry id is kept, so a
  resubmit replaces that workout instead of adding a second one. Watch workouts (table workouts) stay as they are and are
  merged with this entry by training/history.py, as for any SparkyFitness workout.
The AI reads submitted workouts through get_training_log / get_training_day, and an unsubmitted draft through get_training_day.
"""
from __future__ import annotations

import json
import re
from datetime import date

import psycopg

from .. import auth, sparky_client
from ..assistant import briefing, sparky_write
from ..i18n import L
from . import catalog, loads, params as P, plans

MAX_SETS = 20
MAX_EXERCISES = 30


def _today(conn: psycopg.Connection, user_id: int) -> date:
    return plans._user_today(conn, user_id)


def _row(conn: psycopg.Connection, user_id: int, day: date) -> dict | None:
    return conn.execute("SELECT * FROM training_session_logs WHERE user_id = %s AND date = %s AND source = 'page'",
                        (user_id, day)).fetchone()


def _log_out(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {"status": row["status"], "exercises": row["exercises"] or [], "notes": row.get("notes"),
            "submitted_at": row.get("submitted_at"), "updated_at": row["updated_at"],
            "changed_since_submit": bool(row.get("sparky_entry_id")) and row["status"] == "draft"}


def _target(item: dict, set_factor: float) -> dict:
    """Pre-filled rows: the plan's sets (fewer on a red / yellow day), the target reps and the recommended weight."""
    ld = item.get("load") or {}
    n = max(1, round(int(item.get("sets") or 3) * (set_factor or 1.0)))
    timed = bool(item.get("duration_sec")) and not item.get("rep_low")
    reps = None if timed else (ld.get("target_reps") or loads.target_reps(item.get("rep_low"), item.get("rep_high")))
    weight = ld.get("kg") if (item.get("modality") or "weight_reps") == "weight_reps" else None
    return {"sets": n, "reps": reps, "rep_low": item.get("rep_low"), "rep_high": item.get("rep_high"),
            "weight_kg": weight, "duration_s": item.get("duration_sec") if timed else None,
            "rest_sec": item.get("rest_sec")}


def _card(item: dict, rows: dict[str, dict], ctx: loads.Context, set_factor: float) -> dict:
    slug = item.get("slug") or ""
    row = rows.get(slug) or {}
    thumb = catalog.image_urls(row.get("images") or [])[:1]
    hist = ctx.history_for(item) or []
    return {
        "slug": slug or None,
        "exercise_id": item.get("app_exercise_id"),   # SparkyFitness id; None = matched by name when submitted
        "name_zh": item.get("name_zh") or item.get("name"),
        "name_en": item.get("name_en") or row.get("name_en"),
        "status": item.get("status") or "active",
        "reason": item.get("reason"),
        "cues": item.get("cues"),
        "note": item.get("note"),
        "added_for_day": bool(item.get("added_for_day")),
        "modality": item.get("modality") or row.get("modality") or "weight_reps",
        "per_side": bool(item.get("per_side")),
        "sets_text": item.get("sets_text"),
        "load": item.get("load"),
        "load_text": item.get("load_text"),
        "primary_muscles": item.get("primary_muscles") or row.get("primary_muscles") or [],   # muscle map when there is no image
        "secondary_muscles": item.get("secondary_muscles") or row.get("secondary_muscles") or [],
        "primary_muscles_zh": item.get("primary_muscles_zh") or [],
        "equipment_zh": item.get("equipment_zh") or [],
        "thumb": thumb[0] if thumb else None,
        "target": _target(item, set_factor),
        "last": [{"date": h["date"], "sets": [f"{s['weight_kg']:g}×{s['reps']}" if s.get("weight_kg") else f"{s['reps']}"
                                              for s in h["sets"] if s.get("reps")]} for h in hist[:1]],
    }


def _minutes(cards: list[dict]) -> int | None:
    """The collapsed plan card's "about N minutes": sets x (work + rest) of the exercises that can be done (params EST_*)."""
    secs = 0
    for c in cards:
        if c["status"] == "banned":
            continue
        t = c["target"]
        rest = P.EST_DEFAULT_REST_SEC if t.get("rest_sec") is None else t["rest_sec"]
        secs += t["sets"] * ((t.get("duration_s") or P.EST_SET_WORK_SEC) + rest)
    if not secs:
        return None
    step = P.EST_ROUND_MIN
    return max(step, round(secs / 60 / step) * step)


def _sparky_plan_items(conn: psycopg.Connection, viewer_id: int, owner_id: int, day: date, ctx: loads.Context) -> list[dict]:
    """A SparkyFitness weekly plan (no Body Engine plan): the Today page's own view of that day, mapped onto catalog rows."""
    from ..web import views
    sessions = []
    for p in views.training_today(conn, viewer_id, owner_id, day).get("planned") or []:
        items = []
        for x in p["exercises"]:
            slug = ctx.index.slug_for(x.get("exercise_id"), x.get("name"))
            row = {k: v for k, v in (ctx.index.rows.get(slug or "") or {}).items() if not k.startswith("_")}
            n = re.match(r"(\d+)", x.get("sets_text") or "")
            items.append({**row, **x, "slug": slug, "name_zh": x.get("name"), "app_exercise_id": x.get("exercise_id"),
                          "sets": int(n.group(1)) if n else 3})
        sessions.append({"name": p["name"], "exercises": items})
    return sessions


def view(conn: psycopg.Connection, viewer_id: int, owner_id: int, day: date | None = None) -> dict:
    if not auth.can_view(conn, viewer_id, owner_id, "training"):
        raise PermissionError(L("没有授权查看该用户的训练（需要 training 权限）", "No permission to view this user's training (needs the training scope)"))
    day = day or _today(conn, owner_id)
    is_today = day == _today(conn, owner_id)
    wk = plans.get_week_plan(conn, viewer_id, owner_id, day, detail=True)
    readiness = wk.get("readiness") or {}
    tier = readiness.get("tier") if is_today else None
    set_factor = float(readiness.get("set_factor") or 1.0) if tier in ("red", "yellow") else 1.0
    ctx = loads.Context(conn, viewer_id, owner_id, day)
    if wk["plan"] is not None:
        entry = next((d for d in wk["days"] if d["date"] == day), {"planned": [], "rest_day": True, "change": None})
        sessions, plan, rest_day, change = entry["planned"], wk["plan"], entry["rest_day"], entry.get("change")
        removed = entry.get("removed") or []
    else:
        try:
            sessions = _sparky_plan_items(conn, viewer_id, owner_id, day, ctx)
        except sparky_client.SparkyError:
            sessions = []
        plan, rest_day, change, removed = None, False, None, []
    policy = catalog.media_policy(conn)   # which demo pictures may be shown (exercise_media_licensed / _free_db_off)
    need = {item.get("slug") for p in sessions for item in p["exercises"]}   # only the planned rows, not all ~6,300
    rows = {r["slug"]: catalog.media_view(r, *policy) for r in catalog.catalog_rows(conn) if r["slug"] in need}
    out_sessions = []
    for p in sessions:
        exs = []
        for item in p["exercises"]:
            if tier in ("red", "yellow"):
                item["load"] = loads.apply_readiness(item.get("load"), tier)
                item["load_text"] = loads.short_text(item["load"])
            exs.append(_card(item, rows, ctx, set_factor))
        out_sessions.append({"name": p["name"], "day_of_week": p.get("day_of_week"), "added_for_day": bool(p.get("added_for_day")),
                             "exercises": exs, "minutes": _minutes(exs)})
    link = sparky_client.link_state(conn, owner_id)
    return {
        "date": day,
        "is_today": is_today,
        "plan": plan and {"name": plan["name"], "split_type": plan.get("split_type")},
        "sessions": out_sessions,
        "rest_day": rest_day and not out_sessions,
        "change": change,
        "minutes": _minutes([c for s in out_sessions for c in s["exercises"]]),
        "removed": removed,   # removed for this date only (issue #172): the page offers 恢复
        # the plan card's remove / restore: own Body Engine plan, today or later (training/plan_remove.py)
        "can_edit_plan": viewer_id == owner_id and plan is not None and day >= _today(conn, owner_id),
        "readiness": readiness and {k: readiness.get(k) for k in ("tier", "label", "set_factor", "rir_delta")},
        "log": _log_out(_row(conn, owner_id, day)),
        "training_log": link["state"],
        "can_log": viewer_id == owner_id and day <= _today(conn, owner_id),
        **({"load_history_error": ctx.history_error} if ctx.history_error else {}),
    }


# ---------------------------------------------------------------- draft
def _num(v, kind=float):
    if v is None or v == "":
        return None
    return kind(v)


def clean_exercises(exercises: list[dict]) -> list[dict]:
    """Validate and normalize the page's rows; empty rows are kept (a set still being filled in)."""
    if len(exercises) > MAX_EXERCISES:
        raise ValueError(L(f"最多 {MAX_EXERCISES} 个动作", f"At most {MAX_EXERCISES} exercises"))
    out = []
    for ex in exercises:
        slug = str(ex.get("slug") or "").strip()
        if not slug:
            raise ValueError(L("每个动作要有 slug", "Every exercise needs a slug"))
        sets = ex.get("sets") or []
        if len(sets) > MAX_SETS:
            raise ValueError(L(f"一个动作最多 {MAX_SETS} 组", f"At most {MAX_SETS} sets per exercise"))
        clean = []
        for s in sets:
            c = {"reps": _num(s.get("reps"), int), "weight_kg": _num(s.get("weight_kg")),
                 "duration_s": _num(s.get("duration_s")), "rir": _num(s.get("rir")), "done": bool(s.get("done"))}
            if c["reps"] is not None and not 0 <= c["reps"] <= 100:
                raise ValueError(L(f"次数 {c['reps']} 不合理", f"{c['reps']} reps is not plausible"))
            if c["weight_kg"] is not None and not 0 <= c["weight_kg"] <= 400:
                raise ValueError(L(f"重量 {c['weight_kg']:g} kg 不合理", f"{c['weight_kg']:g} kg is not plausible"))
            if c["duration_s"] is not None and not 0 < c["duration_s"] <= 6 * 3600:
                raise ValueError(L(f"时长 {c['duration_s']:g} 秒不合理", f"{c['duration_s']:g} s is not plausible"))
            if c["rir"] is not None and not 0 <= c["rir"] <= 10:
                raise ValueError(L("RIR 只能是 0–10", "RIR must be 0–10"))
            clean.append({k: v for k, v in c.items() if v is not None})
        out.append({"slug": slug, **({"name": str(ex["name"])[:120]} if ex.get("name") else {}), "sets": clean})
    return out


def _check_day(conn: psycopg.Connection, user_id: int, day: date) -> None:
    today = _today(conn, user_id)
    if day > today:
        raise ValueError(L("不能记未来的训练", "Can't log a future workout"))
    if (today - day).days > sparky_write.MAX_BACKDATE_DAYS:
        raise ValueError(L(f"只能补记 {sparky_write.MAX_BACKDATE_DAYS} 天内的训练",
                           f"Only workouts of the last {sparky_write.MAX_BACKDATE_DAYS} days can be logged"))


def save_draft(conn: psycopg.Connection, user_id: int, day: date, exercises: list[dict], notes: str | None = None) -> dict:
    _check_day(conn, user_id, day)
    ex = clean_exercises(exercises)
    row = conn.execute(
        """INSERT INTO training_session_logs (user_id, date, exercises, notes, status)
           VALUES (%s, %s, %s::jsonb, %s, 'draft')
           ON CONFLICT (user_id, date, source) DO UPDATE SET exercises = EXCLUDED.exercises, notes = EXCLUDED.notes,
             status = 'draft', updated_at = now()
           RETURNING *""",
        (user_id, day, json.dumps(ex, ensure_ascii=False), (notes or "").strip()[:500] or None),
    ).fetchone()
    return {"log": _log_out(row)}


# ---------------------------------------------------------------- submit
def _plan_items(conn: psycopg.Connection, user_id: int, day: date) -> tuple[dict[str, dict], list[str]]:
    """slug -> the plan's exercise for that day, and the session names (the workout's name in SparkyFitness)."""
    v = view(conn, user_id, user_id, day)
    items = {x["slug"]: x for s in v["sessions"] for x in s["exercises"] if x.get("slug")}
    return items, [s["name"] for s in v["sessions"]]


def submit(conn: psycopg.Connection, user_id: int, day: date, allow_duplicate: bool = False,
           client=None) -> dict:
    _check_day(conn, user_id, day)
    row = _row(conn, user_id, day)
    if row is None:
        raise ValueError(L("还没有填任何一组", "No sets filled in yet"))
    items, names = _plan_items(conn, user_id, day)
    exercises, banned = [], []
    for ex in row["exercises"] or []:
        sets = [{k: s[k] for k in ("reps", "weight_kg", "duration_s", "rir") if s.get(k) is not None}
                for s in ex.get("sets") or [] if s.get("done")]
        sets = [s for s in sets if any(s.get(k) is not None for k in ("reps", "weight_kg", "duration_s"))]
        if not sets:
            continue
        item = items.get(ex["slug"]) or {}
        if not item:
            hit = catalog.resolve(conn, ex["slug"], user_id=user_id)
            item = {"name_zh": (hit or {}).get("name_zh") or ex.get("name") or ex["slug"],
                    "exercise_id": (hit or {}).get("app_exercise_id"), "status": (hit or {}).get("status")}
        if item.get("status") == "banned":
            banned.append(item["name_zh"])
            continue
        exercises.append({"exercise": item["name_zh"], "exercise_id": item.get("exercise_id"), "sets": sets})
    if banned:
        raise PermissionError(L(f"禁用动作不能记录：{'、'.join(banned)}", f"Banned exercises can't be logged: {', '.join(banned)}"))
    if not exercises:
        raise ValueError(L("先勾选做完的组", "Tick the sets you have done first"))
    try:   # SparkyFitness, or the own tables when own_food_only (issue #252)
        be = sparky_write.backend(conn, user_id, client)
    except (sparky_client.SparkyError, ValueError):
        raise LookupError(L("训练记录还没连接，先连接再提交", "The training log is not connected yet; connect it, then submit"))
    old_id = row.get("sparky_entry_id")
    res = sparky_write.log_workout(conn, user_id, {
        "date": day.isoformat(), "name": " · ".join(names) or L("训练", "Workout"), "notes": row.get("notes"),
        "exercises": exercises,
        # a resubmit replaces the page's own earlier workout (deleted below), so it is not a duplicate of it
        "allow_duplicate": allow_duplicate or bool(old_id),
        "create_missing_exercises": True,   # exercises of the user's own plan
    }, client=be)
    if res.get("skipped_duplicate"):
        return {"submitted": False, "duplicate": True,
                "reason": L("今天已经记过这些动作（可能是 AI 或 SparkyFitness 里记的）", "These exercises are already logged today (by your AI or in SparkyFitness)")}
    new_id = res["created"]["id"]
    if old_id and str(old_id) != str(new_id):
        try:
            be.delete_session_id(old_id)
        except sparky_client.SparkyError:
            pass   # deleted in SparkyFitness already
        be.after_write(workouts=[day])   # dual write (issue #252): the replaced one is gone
    row = conn.execute(
        """UPDATE training_session_logs SET status = 'submitted', sparky_entry_id = %s, submitted_at = now(), updated_at = now()
           WHERE user_id = %s AND date = %s AND source = 'page' RETURNING *""", (str(new_id), user_id, day)).fetchone()
    loads.clear_history_cache(user_id)
    briefing.invalidate(user_id)
    return {"submitted": True, "replaced": bool(old_id), "exercises": len(exercises),
            "sets": sum(len(e["sets"]) for e in exercises), "log": _log_out(row)}


def draft_for_ai(conn: psycopg.Connection, user_id: int, day: date) -> dict | None:
    """get_training_day: sets filled in on the page but not submitted yet (submitted ones are in SparkyFitness already)."""
    row = _row(conn, user_id, day)
    if row is None or row["status"] != "draft":
        return None
    exs = [{"slug": e["slug"], "sets_done": [f"{s['weight_kg']:g}×{s.get('reps', '?')}" if s.get("weight_kg") else
                                             f"{s.get('reps') or s.get('duration_s')}" for s in e.get("sets") or [] if s.get("done")]}
           for e in row["exercises"] or []]
    exs = [e for e in exs if e["sets_done"]]
    return {"note": "今天页正在逐组记录、还没提交", "exercises": exs, "updated_at": row["updated_at"]} if exs else None
