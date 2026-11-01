"""Starting a weekly plan on any day (issue #65) and optional mobility add-ons (issue #66 FB-3).

- training_days: the weekdays the user can train; the split's sessions are put on them in order (a 4-day split on
  Tue / Thu / Sat / Sun instead of the template's Mon / Tue / Thu / Fri).
- start_date: any day from today on. Days before it are not part of the plan.
- The first (partial) week counts what was already trained this week (SparkyFitness log + watch workouts) and fills the
  remaining available days only with sessions for the muscle groups still missing (each group as often as the full plan
  trains it per week, no group on two days in a row). Those days are written as day changes (training_day_overrides,
  source replan, day_overrides.py) so the week view, the Today page and the rules all see them; from the next week on the
  full cycle runs.
- Add-ons (params.MOBILITY_ADDONS): suggested only from the user's own exercise restrictions; added only when asked.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import psycopg

from .. import sparky_client
from . import catalog, params as P
from .catalog_data import MUSCLE_GROUP

MAJOR_GROUPS = set(P.WEEKLY_SET_TARGETS)
WEEKDAY_ZH = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def _groups(muscles) -> set[str]:
    return {MUSCLE_GROUP[m.lower()] for m in muscles or [] if MUSCLE_GROUP.get((m or "").lower())} & MAJOR_GROUPS


def session_groups(day: dict) -> set[str]:
    """Major muscle groups a plan day trains (strength exercises' primary muscles; add-on warm-ups excluded)."""
    out: set[str] = set()
    for ex in day.get("exercises") or []:
        if ex.get("addon") or (ex.get("category") or "strength") != "strength":
            continue
        out |= _groups(ex.get("primary_muscles"))
    if not out:
        from .day_overrides import _name_groups
        out = _name_groups(day.get("name") or "")
    return out


# ---------------------------------------------------------------- weekdays
def remap_days(raw_days: list, training_days: list[int] | None) -> list:
    """Put the sessions (in template order) on the user's training weekdays. raw_days: [(dow, name, exercises)]."""
    if not training_days:
        return raw_days
    days = sorted({int(d) for d in training_days})
    if any(not 1 <= d <= 7 for d in days):
        raise ValueError("training_days 必须是 1（周一）到 7（周日）")
    ordered = sorted(raw_days, key=lambda d: int(d[0]))
    if len(days) != len(ordered):
        raise ValueError(f"这个分化每周 {len(ordered)} 天，training_days 给了 {len(days)} 天"
                         f"（{'、'.join(WEEKDAY_ZH[d] for d in days)}）；换一个分化或改可练的日子")
    return [(dow, name, exs) for dow, (_old, name, exs) in zip(days, ordered)]


def check_start(start: date | None, today: date) -> date:
    start = start or today
    if start < today:
        raise ValueError(f"开始日期不能早于今天（{today}）")
    if start > today + timedelta(days=P.OVERRIDE_FUTURE_DAYS):
        raise ValueError(f"开始日期最多到 {today + timedelta(days=P.OVERRIDE_FUTURE_DAYS)}")
    return start


# ---------------------------------------------------------------- add-ons from the user's own restrictions
def suggested_addons(conn: psycopg.Connection, user_id: int) -> list[dict]:
    """Add-ons whose joints are loaded by an exercise this user has banned / set to caution (exercise_meta)."""
    rows = conn.execute("SELECT exercise_ref, name_zh, status FROM exercise_meta WHERE user_id = %s AND status IN ('banned', 'caution')",
                        (user_id,)).fetchall()
    joints_by_ex: dict[str, set[str]] = {}
    for r in rows:
        res = catalog.resolve(conn, r["exercise_ref"] or r["name_zh"] or "", user_id=user_id)
        if res:
            joints_by_ex[res["name_zh"]] = set(res.get("joint_stress") or [])
    out = []
    for key, a in P.MOBILITY_ADDONS.items():
        because = sorted(n for n, j in joints_by_ex.items() if j & a["joints"])
        if because:
            out.append({"key": key, "name": a["name"], "name_en": a["name_en"], "because": because[:5]})
    return out


def apply_addons(conn: psycopg.Connection, user_id: int, days: list[dict], keys: list[str] | None, normalize) -> list[str]:
    """Add the chosen add-ons as a warm-up block at the start of the sessions that train their muscle groups."""
    applied = []
    for key in keys or []:
        a = P.MOBILITY_ADDONS.get(key)
        if a is None:
            raise ValueError(f"没有附加项 {key!r}；可选：{'、'.join(P.MOBILITY_ADDONS)}")
        hit = False
        for d in days:
            if session_groups(d) & a["groups"]:
                block = [{**normalize(conn, user_id, row, with_meta=True), "addon": key} for row in a["exercises"]]
                d["exercises"] = block + [x for x in d["exercises"] if x.get("addon") != key]
                hit = True
        if hit:
            applied.append(key)
    return applied


# ---------------------------------------------------------------- what was trained this week
def trained(conn: psycopg.Connection, user_id: int, start: date, end: date,
            client: sparky_client.SparkyClient | None = None) -> dict:
    """Per date in [start, end]: the major muscle groups trained, from the SparkyFitness log (exercise names -> catalog
    muscles) and watch workouts (strength workouts without a log count as trained, groups unknown)."""
    days: dict[date, dict] = {}
    sparky = "connected"
    if end >= start:
        try:
            cl = client or sparky_client.reader_for_user(conn, user_id)
            for s in sparky_client.training_log(cl, start, end):
                d = date.fromisoformat(str(s["date"])[:10])
                g = days.setdefault(d, {"groups": set(), "sessions": []})
                g["sessions"].append(s["name"])
                for ex in s["exercises"]:
                    res = catalog.resolve(conn, ex.get("name") or "", user_id=user_id)
                    if res and (res.get("category") or "strength") == "strength":
                        g["groups"] |= _groups(res.get("primary_muscles"))
        except sparky_client.SparkyError:   # not linked / unreadable: the watch workouts below still count
            sparky = sparky_client.link_state(conn, user_id)["state"]
            if sparky == "connected":
                sparky = "unreadable"
            days.clear()
        for w in conn.execute(
                """SELECT local_date, type, name_zh FROM workouts WHERE user_id = %s AND local_date BETWEEN %s AND %s
                   AND dup_of IS NULL AND excluded IS NULL ORDER BY start_at""", (user_id, start, end)):
            g = days.setdefault(w["local_date"], {"groups": set(), "sessions": []})
            if w["name_zh"] not in g["sessions"]:
                g["sessions"].append(w["name_zh"])
            if w["type"] in P.STRENGTH_TYPES and not g["groups"]:
                g["strength_unknown"] = True
    return {"days": days, "sparky": sparky}


# ---------------------------------------------------------------- the first (partial) week
def first_week(conn: psycopg.Connection, user_id: int, plan_days: list[dict], start: date, today: date,
               client: sparky_client.SparkyClient | None = None) -> dict:
    """Which plan session goes on each remaining day of the start week. plan_days: normalized days with day_of_week."""
    week_start = start - timedelta(days=start.weekday())
    week_end = week_start + timedelta(days=6)
    sessions = {int(d["day_of_week"]): {"name": d["name"], "groups": session_groups(d)} for d in plan_days}
    plan_freq = Counter(g for s in sessions.values() for g in s["groups"])
    done = trained(conn, user_id, week_start, min(today, week_end), client)
    group_count: Counter = Counter()
    for info in done["days"].values():
        group_count.update(info["groups"])
    need = {g: max(0, plan_freq[g] - group_count[g]) for g in plan_freq}

    out_days, used, prev_groups = [], set(), set()
    for k in range(7):
        d = week_start + timedelta(days=k)
        info = done["days"].get(d)
        if d < start or (d <= today and info):   # before the plan, or already trained today
            prev_groups = (info or {}).get("groups", set())
            if d >= start or info:
                out_days.append({"date": d.isoformat(), "wd": WEEKDAY_ZH[d.isoweekday()],
                                 "done": " + ".join(info["sessions"]) if info else None, "groups": sorted(prev_groups)})
            continue
        best, best_gain = None, 0.0
        for dow, s in sessions.items():
            if dow in used:
                continue
            gain = sum(min(need.get(g, 0), 1) for g in s["groups"])
            if s["groups"] & prev_groups:
                gain -= 10
            if dow == d.isoweekday():
                gain += 0.1   # tie-break: keep the plan's own weekday
            if gain > best_gain:
                best, best_gain = dow, gain
        available = d.isoweekday() in sessions
        if best is not None and available:
            used.add(best)
            for g in sessions[best]["groups"]:
                need[g] = max(0, need.get(g, 0) - 1)
            prev_groups = sessions[best]["groups"]
        else:
            best, prev_groups = None, set()
        out_days.append({"date": d.isoformat(), "wd": WEEKDAY_ZH[d.isoweekday()], "dow_session": best,
                         "session": sessions[best]["name"] if best else "休息",
                         "plan_default": sessions.get(d.isoweekday(), {}).get("name", "休息")})
    missing = sorted(g for g, n in need.items() if n > 0)
    return {"week": f"{week_start}–{week_end}", "start": start.isoformat(), "days": out_days,
            "trained_groups": dict(group_count), "still_missing": missing, "sparky": done["sparky"],
            "full_cycle_from": (week_end + timedelta(days=1)).isoformat()}


def apply_first_week(conn: psycopg.Connection, user_id: int, plan_row: dict, fw: dict) -> int:
    """Write the start week's remaining days as day changes (only where they differ from the plan's own weekday)."""
    from . import day_overrides
    model = day_overrides.model_from_plan_row(conn, user_id, plan_row)
    n = 0
    for d in fw["days"]:
        if "dow_session" not in d:
            continue
        refs = [{"dow": d["dow_session"]}] if d["dow_session"] else []
        day = date.fromisoformat(d["date"])
        if refs != model.base(day):
            day_overrides._write(conn, user_id, day, refs, "replan", "本周起步补齐", model)
            n += 1
    return n
