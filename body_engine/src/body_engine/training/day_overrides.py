"""Day-level changes to the weekly training plan (table training_day_overrides, migration 230).

The weekly plan (Body Engine training_plans, or the SparkyFitness weekly plan when there is none) says what each weekday holds.
A day change says "on this date do the content of weekday N instead" (or a free activity, or rest):
- reschedule_day: the user wants a different session on one date ("今天不练上肢 A，想练下肢 B"), then by default
  replan_week re-plans the rest of that week;
- swap_days: exchange what two dates hold;
- replan_week: put the plan sessions not yet done this week on the remaining days, so that the week still holds every
  session once (weekly volume per muscle group unchanged), the same major muscle group is not trained on two days in a row
  and as few days as possible move; recovery red today keeps tomorrow free;
- undo_day_changes: back to the plan's own weekdays (deletes rows; the AI asks before calling it).
Readers: plans.get_week_plan (Body Engine plan), rules.context.Ctx.planned_for (SparkyFitness plan, rules), web/views.py.
Writes only for the user themself (assistant.tools.run_tool); no confirmation except undo (ai/chat.py ALWAYS_CONFIRM).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import permutations

import psycopg

from .. import sparky_client
from ..rules.context import Ctx, load_overrides
from . import catalog, params as P, plans
from .catalog_data import GROUP_ZH, MUSCLE_GROUP

WEEKDAY_ZH = plans.WEEKDAY_ZH
REST_WORDS = {"休息", "休息日", "rest", "off", "不练", "无", "休"}
_WD_CHARS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_WD_EN = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 7}
MAJOR_GROUPS = set(P.WEEKLY_SET_TARGETS)


@dataclass
class Weekday:
    """The plan's content of one weekday."""
    dow: int
    names: list[str] = field(default_factory=list)
    groups: set[str] = field(default_factory=set)
    cautions: list[dict] = field(default_factory=list)   # [{name, status}] caution / banned exercises
    items: list[dict] = field(default_factory=list)      # Body Engine plan: normalized exercises (for volume_summary)


@dataclass
class WeekModel:
    source: str | None                      # body_engine / sparkyfitness / None (no plan)
    plan_name: str | None
    by_dow: dict[int, Weekday]
    error: str | None = None                # SparkyFitness could not be read

    def has(self, dow: int) -> bool:
        return bool(self.by_dow.get(dow) and self.by_dow[dow].names)

    def base(self, d: date) -> list[dict]:
        return [{"dow": d.isoweekday()}] if self.has(d.isoweekday()) else []

    def names(self, refs: list[dict]) -> list[str]:
        out = []
        for r in refs:
            if r.get("dow"):
                out += (self.by_dow.get(int(r["dow"])) or Weekday(0)).names
            elif r.get("label"):
                out.append(r["label"])
        return out

    def text(self, refs: list[dict]) -> str:
        return " + ".join(self.names(refs)) or "休息"

    def groups(self, refs: list[dict]) -> set[str]:
        out: set[str] = set()
        for r in refs:
            if r.get("dow"):
                out |= (self.by_dow.get(int(r["dow"])) or Weekday(0)).groups
            elif r.get("label"):
                out |= _name_groups(r["label"])
        return out

    def catalog(self) -> list[dict]:
        """Sessions the plan has, for the AI: [{weekday, session}]."""
        return [{"weekday": WEEKDAY_ZH[k], "session": " + ".join(w.names)} for k, w in sorted(self.by_dow.items()) if w.names]


def _name_groups(name: str) -> set[str]:
    n = (name or "").lower()
    out: set[str] = set()
    for words, groups in P.SESSION_NAME_GROUPS:
        if any(w in n for w in words):
            out |= set(groups)
    return out


def _groups_of_muscles(muscles) -> set[str]:
    return {MUSCLE_GROUP[m.lower()] for m in muscles or [] if MUSCLE_GROUP.get((m or "").lower())} & MAJOR_GROUPS


# ---------------------------------------------------------------- the plan, by weekday
def model_from_plan_row(conn: psycopg.Connection, user_id: int, plan_row: dict) -> WeekModel:
    by_dow: dict[int, Weekday] = {}
    for d_obj in plan_row.get("days") or []:
        dow = int(d_obj["day_of_week"])
        w = by_dow.setdefault(dow, Weekday(dow))
        w.names.append(d_obj.get("name") or WEEKDAY_ZH[dow])
        named = _name_groups(d_obj.get("name") or "")
        for raw in d_obj.get("exercises") or []:
            item = plans._normalize_item(conn, user_id, raw)
            w.items.append(item)
            if (item.get("category") or "strength") == "strength":
                w.groups |= _groups_of_muscles(item.get("primary_muscles"))
            if item["status"] != "active":
                w.cautions.append({"name": item["name_zh"], "status": item["status"]})
        w.groups |= named if not w.groups else set()
    return WeekModel("body_engine", plan_row["name"], by_dow)


def model_from_ctx(ctx: Ctx, day: date) -> WeekModel:
    plan = ctx.active_plan(day)
    by_dow: dict[int, Weekday] = {}
    if plan is not None:
        for dow in range(1, 8):
            items = ctx.weekday_items(dow, day)
            if not items:
                continue
            w = by_dow.setdefault(dow, Weekday(dow))
            for it in items:
                if it["kind"] == "preset":
                    w.names.append(it["preset"]["name"] or "训练")
                    ex_ids = [x["exercise_id"] for x in it["preset"]["exercises"]]
                else:
                    w.names.append(it["name"])
                    ex_ids = [it["exercise_id"]]
                for ex_id in ex_ids:
                    w.groups |= _groups_of_muscles(ctx.muscles_of(ex_id))
                    status, _m, _b = ctx.status_of(ex_id)
                    if status != "active":
                        w.cautions.append({"name": ctx.exercise_name(ex_id, None), "status": status})
            if not w.groups:
                w.groups = set().union(*(_name_groups(n) for n in w.names))
    return WeekModel("sparkyfitness" if plan else None, plan and plan["name"], by_dow)


def sparky_ctx(conn: psycopg.Connection, user_id: int, day: date,
               client: sparky_client.SparkyClient | None = None) -> Ctx:
    """SparkyFitness exercise library, templates and weekly plan, exercise_meta and day changes (raises SparkyError)."""
    client = client or sparky_client.reader_for_user(conn, user_id)
    ctx = Ctx(user_id=user_id, day=day)
    ctx.exercises = {e["id"]: e for e in sparky_client.exercises(client)}
    ctx.presets = sparky_client.workout_presets(client)
    ctx.plans = sparky_client.workout_plans(client)
    ctx.meta = conn.execute("SELECT * FROM exercise_meta WHERE user_id = %s", (user_id,)).fetchall()
    ctx.overrides = load_overrides(conn, user_id, day - timedelta(days=13), day + timedelta(days=13))
    return ctx


def build_model(conn: psycopg.Connection, user_id: int, day: date,
                client: sparky_client.SparkyClient | None = None) -> WeekModel:
    """The active weekly plan by weekday: Body Engine training_plans first, else the SparkyFitness weekly plan."""
    catalog.ensure_seeded(conn)
    plan_row = plans._active_plan_row(conn, user_id)
    if plan_row is not None:
        return model_from_plan_row(conn, user_id, plan_row)
    if client is None:
        try:
            client = sparky_client.reader_for_user(conn, user_id)
        except sparky_client.SparkyError:   # SparkyFitness not connected: no weekly plan (rest / free activities still work)
            return WeekModel(None, None, {})
    try:
        return model_from_ctx(sparky_ctx(conn, user_id, day, client), day)
    except sparky_client.SparkyError as e:
        return WeekModel(None, None, {}, error=str(e))


# ---------------------------------------------------------------- rows
def _week(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def _write(conn: psycopg.Connection, user_id: int, d: date, refs: list[dict], source: str,
           reason: str | None, model: WeekModel) -> None:
    """Upsert the day change; the plan's own weekday content = no row."""
    if refs == model.base(d):
        conn.execute("DELETE FROM training_day_overrides WHERE user_id = %s AND date = %s", (user_id, d))
        return
    conn.execute(
        """INSERT INTO training_day_overrides (user_id, date, sessions, source, reason)
           VALUES (%s, %s, %s::jsonb, %s, %s)
           ON CONFLICT (user_id, date) DO UPDATE SET sessions = EXCLUDED.sessions, source = EXCLUDED.source,
             reason = COALESCE(EXCLUDED.reason, training_day_overrides.reason), updated_at = now()""",
        (user_id, d, json.dumps(refs, ensure_ascii=False), source, (reason or "")[:200] or None))


def clear_from(conn: psycopg.Connection, user_id: int, d: date) -> int:
    """A new weekly plan replaces the old one: day changes from d on no longer apply (plans.save_plan)."""
    return conn.execute("DELETE FROM training_day_overrides WHERE user_id = %s AND date >= %s", (user_id, d)).rowcount


def effective(model: WeekModel, ovs: dict[date, dict], d: date) -> list[dict]:
    return list(ovs[d]["sessions"]) if d in ovs else model.base(d)


def day_change(model_names, ov: dict | None, d: date) -> dict | None:
    """What the Today page / week strip shows for a changed day: {from: plan's own sessions, source, reason}."""
    if ov is None:
        return None
    return {"from": model_names(d.isoweekday()), "source": ov["source"], "reason": ov.get("reason")}


# ---------------------------------------------------------------- resolving "下肢 B" / "周五的" / "休息" / "篮球"
def resolve_session(model: WeekModel, text: str, d: date) -> tuple[list[dict], str]:
    """(refs, how): how = rest / name / weekday / custom (not a plan session: kept as a free activity)."""
    raw = (text or "").strip()
    t = catalog.norm(raw)
    if not t or t in REST_WORDS:
        return [], "rest"
    names = {dow: [catalog.norm(n) for n in w.names] for dow, w in model.by_dow.items() if w.names}
    order = sorted(names, key=lambda k: (k - d.isoweekday()) % 7)   # the next one from that weekday on
    exact = [k for k in order if t in names[k] or t == "".join(names[k])]
    if exact:
        return [{"dow": exact[0]}], "name"
    m = re.match(r"^(?:周|星期|礼拜)([一二三四五六日天])", t) or re.match(r"^(mon|tue|wed|thu|fri|sat|sun)", t)
    if m or re.fullmatch(r"[1-7]", t):
        dow = _WD_CHARS.get(m.group(1)) or _WD_EN.get(m.group(1)) if m else int(t)
        return ([{"dow": dow}] if model.has(dow) else []), "weekday"
    partial = [k for k in order if any(t in n or (n and n in t) for n in names[k])]
    distinct = {" + ".join(model.by_dow[k].names) for k in partial}
    if len(distinct) > 1:
        raise ValueError(f"「{raw}」对应多个训练：{'、'.join(sorted(distinct))}；请说具体是哪一个")
    if partial:
        return [{"dow": partial[0]}], "name"
    return [{"label": raw[:40]}], "custom"


def _check_date(conn: psycopg.Connection, user_id: int, d: date) -> date:
    today = plans._user_today(conn, user_id)
    if not (today - timedelta(days=P.OVERRIDE_PAST_DAYS) <= d <= today + timedelta(days=P.OVERRIDE_FUTURE_DAYS)):
        raise ValueError(f"只能调整 {today - timedelta(days=P.OVERRIDE_PAST_DAYS)} 到 "
                         f"{today + timedelta(days=P.OVERRIDE_FUTURE_DAYS)} 之间的日子")
    return today


def _need_plan(model: WeekModel) -> None:
    if model.error:
        raise ValueError(f"读不到周训练计划：{model.error}")


# ---------------------------------------------------------------- re-planning the rest of a week
def _cost(seq: list[list[dict]], model: WeekModel, base: list[list[dict]], free_idx: list[int],
          red_next: int | None) -> float:
    cost, streak = 0.0, 0
    groups = [model.groups(r) for r in seq]
    for i, refs in enumerate(seq):
        streak = streak + 1 if refs else 0
        if streak > P.REPLAN_MAX_STREAK:
            cost += P.REPLAN_COST_STREAK
        if i and seq[i - 1] and refs:
            shared = groups[i - 1] & groups[i]
            cost += P.REPLAN_COST_SAME_GROUP * len(shared) + (P.REPLAN_COST_SAME_LEGS if "legs" in shared else 0)
    for i in free_idx:
        if seq[i] != base[i]:
            cost += P.REPLAN_COST_MOVED
            for r in seq[i]:
                if r.get("dow"):
                    cost += P.REPLAN_COST_DISTANCE * abs(int(r["dow"]) - 1 - i)
    if red_next is not None and seq[red_next]:
        cost += P.REPLAN_COST_RED_NEXT_DAY
    return cost


def _warnings(seq: list[list[dict]], days: list[date], model: WeekModel) -> list[str]:
    out = []
    for i in range(1, len(seq)):
        shared = model.groups(seq[i - 1]) & model.groups(seq[i]) if seq[i - 1] and seq[i] else set()
        if shared:
            out.append(f"{WEEKDAY_ZH[days[i - 1].isoweekday()]}、{WEEKDAY_ZH[days[i].isoweekday()]}连续练"
                       + "、".join(GROUP_ZH.get(g, g) for g in sorted(shared)))
    return out


def _summary(model: WeekModel, days: list[date], seq: list[list[dict]], base: list[list[dict]]) -> dict:
    schedule = [{"date": d.isoformat(), "wd": WEEKDAY_ZH[d.isoweekday()], "sessions": model.text(r),
                 **({"changed": True} if r != base[i] else {})} for i, (d, r) in enumerate(zip(days, seq))]
    group_days = Counter(g for r in seq if r for g in model.groups(r))
    out = {"schedule": schedule, "group_days": {GROUP_ZH.get(g, g): n for g, n in sorted(group_days.items())},
           "warnings": _warnings(seq, days, model)}
    if model.source == "body_engine":
        vol = plans.volume_summary([{"day_of_week": i + 1, "exercises": [x for ref in r if ref.get("dow")
                                                                         for x in model.by_dow[int(ref["dow"])].items]}
                                    for i, r in enumerate(seq)])
        out["volume"] = {v["name_zh"]: f"{v['direct_sets']} 组（{v['status_zh']}）" for v in vol.values()}
    cautions = {c["name"]: c["status"] for r in seq for ref in r if ref.get("dow")
                for c in model.by_dow[int(ref["dow"])].cautions}
    if cautions:
        out["cautions"] = [{"name": n, "status": s} for n, s in cautions.items()]
    return out


def replan_week(conn: psycopg.Connection, user_id: int, day: date | None = None, apply: bool = True,
                model: WeekModel | None = None, client: sparky_client.SparkyClient | None = None) -> dict:
    """Re-plan the days after `day` (default today) in its Monday–Sunday week. Days up to `day`, past days and days the user
    set explicitly stay; the plan sessions they do not already hold go onto the other days (cheapest by training/params REPLAN_*)."""
    today = plans._user_today(conn, user_id)
    day = day or today
    model = model or build_model(conn, user_id, day, client)
    _need_plan(model)
    start, end = _week(day)
    days = [start + timedelta(days=k) for k in range(7)]
    ovs = load_overrides(conn, user_id, start, end)
    base = [model.base(d) for d in days]
    current = [effective(model, ovs, d) for d in days]
    free_idx = [i for i, d in enumerate(days)
                if d > day and d >= today and not (d in ovs and ovs[d]["source"] == "user")]
    if model.source is None:
        return {"applied": False, "changes": [], "note": "没有周训练计划，无需重排"}

    consumed = Counter(int(r["dow"]) for i, refs in enumerate(current) if i not in free_idx for r in refs if r.get("dow"))
    needed = [dow for dow in sorted(model.by_dow) if model.has(dow) and not consumed[dow]]
    slots = len(free_idx)
    items = needed + [None] * max(0, slots - len(needed))
    red_next = None
    if day == today and day < end:
        if plans.readiness_advice(conn, user_id, day)["tier"] == "red":
            red_next = days.index(day + timedelta(days=1))

    best, best_cost = None, None
    for perm in sorted(set(permutations(items, slots)), key=lambda p: [x or 0 for x in p]):
        seq = list(current)
        for i, dow in zip(free_idx, perm):
            seq[i] = [{"dow": dow}] if dow else []
        c = _cost(seq, model, base, free_idx, red_next)
        if best_cost is None or c < best_cost - 1e-9:
            best, best_cost = seq, c
    best = best or current

    placed = Counter(int(r["dow"]) for refs in best for r in refs if r.get("dow"))
    skipped = [model.text([{"dow": dow}]) for dow in needed if not placed[dow]]
    changes = [{"date": days[i].isoformat(), "wd": WEEKDAY_ZH[days[i].isoweekday()],
                "before": model.text(current[i]), "after": model.text(best[i])}
               for i in free_idx if best[i] != current[i]]
    if apply:
        for i in free_idx:
            if best[i] != current[i]:
                _write(conn, user_id, days[i], best[i], "replan", None, model)
    out = {"applied": apply, "week": f"{start}–{end}", "changes": changes, **_summary(model, days, best, base)}
    if skipped:
        out["skipped"] = skipped
    if red_next is not None:
        out["recovery"] = "red"
    return out


# ---------------------------------------------------------------- tools
def reschedule_day(conn: psycopg.Connection, user_id: int, day: date, session: str, reason: str | None = None,
                   replan: bool = True, client: sparky_client.SparkyClient | None = None) -> dict:
    """Do `session` (a plan session name, a weekday, 休息 or a free activity) on `day`; by default re-plan the rest of that week."""
    today = _check_date(conn, user_id, day)
    model = build_model(conn, user_id, day, client)
    _need_plan(model)
    refs, how = resolve_session(model, session, day)
    ovs = load_overrides(conn, user_id, day, day)
    before = model.text(effective(model, ovs, day))
    _write(conn, user_id, day, refs, "user", reason, model)
    out = {"date": day.isoformat(), "wd": WEEKDAY_ZH[day.isoweekday()], "before": before, "after": model.text(refs),
           "matched": how}
    if how == "custom":
        out["plan_sessions"] = model.catalog()
    banned = [c["name"] for r in refs if r.get("dow") for c in model.by_dow[int(r["dow"])].cautions if c["status"] == "banned"]
    if banned:
        out["banned_in_session"] = banned
    if replan and model.source and _week(day)[1] >= today:
        out["rest_of_week"] = replan_week(conn, user_id, day, apply=True, model=model)
    return out


def swap_days(conn: psycopg.Connection, user_id: int, date_a: date, date_b: date,
              client: sparky_client.SparkyClient | None = None) -> dict:
    """Exchange what two dates hold (each becomes a day the user set)."""
    if date_a == date_b:
        raise ValueError("两个日期相同")
    _check_date(conn, user_id, date_a)
    _check_date(conn, user_id, date_b)
    model = build_model(conn, user_id, date_a, client)
    _need_plan(model)
    ovs = load_overrides(conn, user_id, min(date_a, date_b), max(date_a, date_b))
    ra, rb = effective(model, ovs, date_a), effective(model, ovs, date_b)
    _write(conn, user_id, date_a, rb, "user", "互换", model)
    _write(conn, user_id, date_b, ra, "user", "互换", model)
    out = {"swapped": [{"date": d.isoformat(), "wd": WEEKDAY_ZH[d.isoweekday()], "before": model.text(b), "after": model.text(a)}
                       for d, b, a in ((date_a, ra, rb), (date_b, rb, ra))]}
    warnings = []
    for wk_start in sorted({_week(date_a)[0], _week(date_b)[0]}):
        days = [wk_start + timedelta(days=k) for k in range(7)]
        wk_ovs = load_overrides(conn, user_id, days[0], days[-1])
        warnings += _warnings([effective(model, wk_ovs, d) for d in days], days, model)
    if warnings:
        out["warnings"] = warnings
    return out


def undo_day_changes(conn: psycopg.Connection, user_id: int, start: date | None = None, end: date | None = None) -> dict:
    """Back to the plan's own weekdays for [start, end] (default: today to the end of this week). Deletes rows: asks first."""
    today = plans._user_today(conn, user_id)
    start = start or today
    end = end or _week(today)[1]
    if end < start:
        raise ValueError("结束日期早于开始日期")
    rows = conn.execute(
        "DELETE FROM training_day_overrides WHERE user_id = %s AND date BETWEEN %s AND %s RETURNING date",
        (user_id, start, end)).fetchall()
    return {"restored": sorted(r["date"].isoformat() for r in rows)}
