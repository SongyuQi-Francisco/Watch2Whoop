"""Muscle load map (rollout flag muscle_load): which muscles were trained today / this week and how much.

Sources of sets: the user's SparkyFitness strength workouts (which already hold what the training page submitted and what the AI
logged) and, when the Hevy import (hevy_import) is there, its imported sets in training_session_logs (status 'imported').
Unfinished page drafts and sets without reps or duration are not counted; warm-up sets are not counted; cardio / stretching are not strength.
Each set is credited to the muscles of its exercise in the catalog: primary muscles x PRIMARY_FACTOR (1.0), secondary x SECONDARY_FACTOR (0.5).
Two numbers per muscle and range: effective sets (the weighted count) and volume in kg (reps x weight, weighted the same way; bodyweight
sets add sets but no kg). Colours compare this week's effective sets with training/params.WEEKLY_SETS_RANGE (sources there).
Workouts of the workouts table (cycling, swimming, basketball, a watch-only strength session ...; not duplicates, not excluded / hidden)
are the sports layer and ALWAYS credit muscles: every type has a mapping in training/sport_muscles.py (an unlisted one gets a whole-body low
rating, never nothing). Each workout gives minutes per muscle (primary x 1, secondary x 0.5, "sport_min") and equivalent sets on the same
scale as logged sets (minutes x resistance of the type x intensity multiplier, see sport_load; formula and sources in params.py). The
equivalent sets are added to the logged sets for the colours / ranges ("sets" = logged + sport, both also given separately). A strength
workout on a day that has logged sets is not counted again (its sets are already there); without logged sets its minutes become estimated
working sets spread over the muscles of that weekday's planned session, else the whole body.
Exercises are matched against the shared catalog plus the user's own custom exercises (custom.py, through loads.Index).
The body is drawn by the profile's sex (users.sex); the page shows the male body and a hint when it is not set.
The result is read-only; callers do the authorization (training scope).
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import psycopg

from .. import sparky_client
from ..i18n import L
from . import catalog, history, loads, params as P, plans, sport_muscles
from .importer import avg_hr_trimp
from . import catalog_data as CD

MUSCLE_EN = {
    "chest": "Chest", "lats": "Lats", "middle back": "Mid back", "lower back": "Lower back", "traps": "Traps",
    "shoulders": "Shoulders", "quadriceps": "Quads", "hamstrings": "Hamstrings", "glutes": "Glutes", "calves": "Calves",
    "biceps": "Biceps", "triceps": "Triceps", "forearms": "Forearms", "abdominals": "Core / abs", "adductors": "Inner thigh",
    "abductors": "Glute med / abductors", "neck": "Neck",
}
NOT_STRENGTH = {"cardio", "stretching", "mobility"}
STATUSES = ("none", "low", "ok", "high")
_WARMUP = {"warm-up", "warmup", "warm up", "热身"}


def weekly_range(muscle: str) -> tuple[int, int]:
    return P.WEEKLY_SETS_RANGE.get(muscle, P.WEEKLY_SETS_RANGE_DEFAULT)


def status_for(muscle: str, sets: float) -> str:
    """none (no set) / low (< low) / ok (low..high) / high (> high) against the weekly effective-set range."""
    if sets <= 0:
        return "none"
    low, high = weekly_range(muscle)
    return "low" if sets < low else "high" if sets > high else "ok"


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())   # Monday


def windows(today: date) -> dict[str, tuple[date, date]]:
    mon = week_start(today)
    return {"today": (today, today), "week": (mon, today), "last7": (today - timedelta(days=6), today),
            "prev_week": (mon - timedelta(days=7), mon - timedelta(days=1))}


# ---------------------------------------------------------------- inputs
def _sparky(conn: psycopg.Connection, uid: int, start: date, end: date) -> tuple[list[dict], str]:
    key = (conn.info.dbname, uid, start, end)   # same cache (and clearing) as loads.load_history
    hit = loads._HISTORY_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < P.LOAD_HISTORY_TTL_S:
        return hit[1], "ok"
    try:
        client = sparky_client.client_for_user(conn, uid)
    except (sparky_client.SparkyError, ValueError):
        return [], "not_linked"
    try:
        sessions = sparky_client.training_log(client, start, end)
    except sparky_client.SparkyError:
        return [], "error"
    loads._HISTORY_CACHE[key] = (time.monotonic(), sessions)
    return sessions, "ok"


def _imported(conn: psycopg.Connection, uid: int, start: date, end: date) -> list[dict]:
    """Sets imported from other apps (Hevy): training_session_logs rows with status 'imported' (none before that import exists)."""
    rows = conn.execute("""SELECT date, exercises FROM training_session_logs
                           WHERE user_id = %s AND status = 'imported' AND date BETWEEN %s AND %s ORDER BY date""",
                        (uid, start, end)).fetchall()
    out = []
    for r in rows:
        exs = []
        for e in r["exercises"] or []:
            sets = [{"reps": s.get("reps"), "weight_kg": s.get("weight_kg"), "duration_s": s.get("duration_s"), "type": s.get("type")}
                    for s in e.get("sets") or [] if s.get("done", True)]
            exs.append({"slug": e.get("slug"), "name": e.get("name") or e.get("slug"), "sets": sets})
        out.append({"date": r["date"].isoformat(), "exercises": exs})
    return out


def _sports(conn: psycopg.Connection, uid: int, start: date, end: date) -> list[dict]:
    """Watch workouts, one row per workout (duplicates and excluded / hidden ones left out). Strength types are included: whether they count
    is decided per day in aggregate_sports (only when no sets are logged that day)."""
    rows = conn.execute("""SELECT local_date, type, duration_min, trimp, avg_hr, hr_max_used FROM workouts
                           WHERE user_id = %s AND local_date BETWEEN %s AND %s AND dup_of IS NULL AND excluded IS NULL
                             AND duration_min > 0 ORDER BY start_at""", (uid, start, end)).fetchall()
    return [{"date": r["local_date"], "type": r["type"], "min": float(r["duration_min"]),
             "trimp": r["trimp"], "avg_hr": r["avg_hr"], "hr_max": r["hr_max_used"]} for r in rows]


def sport_name(t: str) -> str:
    return L(P.TYPE_NAMES.get(t, t), P.TYPE_NAMES_EN.get(t, t))


def intensity_level(w: dict) -> str:
    """The workout's intensity level, graded like the training calendar (history.classify without the strength-volume step):
    TRIMP from the raw heart rate, else a TRIMP estimated from the average heart rate, else its duration by category."""
    minutes = w["min"]
    if w.get("trimp") is not None:
        level = history._level(float(w["trimp"]), P.TRIMP_THRESHOLDS)
    elif w.get("avg_hr") and w.get("hr_max"):
        level = history._level(avg_hr_trimp(float(w["avg_hr"]), minutes, float(w["hr_max"])) * P.AVG_HR_TRIMP_FACTOR, P.TRIMP_THRESHOLDS)
    else:
        level = history._level(minutes, P.DURATION_THRESHOLDS[history._category(sport_muscles.normalize(w["type"]))])
    return "light" if minutes < P.MIN_SESSION_MIN else level


def sport_load(w: dict, strength_target: tuple[list[str], list[str]] | None = None) -> dict:
    """What one workout credits: {"muscles": {muscle: (equivalent sets, is_primary)}, "level", "known", "estimated"}. Never empty.
    Sport: equivalent sets of a muscle = minutes x SPORT_SETS_PER_MIN x resistance(type) x INTENSITY_MULT[level] x (1 primary | 0.5 secondary).
    Strength-like type with no logged sets: working sets = minutes / SET_CYCLE_MIN spread over `strength_target` (planned primary and secondary
    muscles of that weekday) or the type's whole-body mapping; a target of n primaries gives each sets x STRENGTH_PRIMARIES_PER_SET / n."""
    m = sport_muscles.resolve(w["type"])
    if m["type"] in sport_muscles.STRENGTH_LIKE:
        prim, sec = (list(strength_target[0]), list(strength_target[1])) if strength_target and strength_target[0] else (list(m["primary"]), list(m["secondary"]))
        per = w["min"] / P.SET_CYCLE_MIN * P.STRENGTH_PRIMARIES_PER_SET / len(prim) * P.STRENGTH_FALLBACK_RESISTANCE
        level = "moderate"
        loads_ = {x: (per, True) for x in prim} | {x: (per * P.SPORT_SECONDARY_FACTOR, False) for x in sec if x not in prim}
        return {"muscles": loads_, "level": level, "known": True, "estimated": True}
    level = intensity_level(w)
    base = w["min"] * P.SPORT_SETS_PER_MIN * m["resistance"] * P.INTENSITY_MULT[level]
    loads_ = {x: (base, True) for x in m["primary"]} | {x: (base * P.SPORT_SECONDARY_FACTOR, False) for x in m["secondary"]}
    return {"muscles": loads_, "level": level, "known": m["known"], "estimated": not m["known"]}


# strength-like types whose sets are already counted when the day has logged sets (generic / other come from SparkyFitness strength records too)
_COUNTED_BY_SETS = sport_muscles.STRENGTH_LIKE | P.STRENGTH_TYPES


def aggregate_sports(sports: list[dict], today: date, logged_days: set[date] | frozenset = frozenset(),
                     strength_targets=None) -> dict:
    """Minutes and equivalent sets per muscle and window, the sports behind each muscle and the overall list per window.
    logged_days: days that already have logged sets (their strength-type workouts are not counted again).
    strength_targets: date -> (primary muscles, secondary muscles) of the planned session, or None (whole body)."""
    wins = windows(today)
    stats = {m: {w: 0.0 for w in P.MUSCLE_RANGES} for m in CD.MUSCLE_ZH}
    eq = {m: {w: 0.0 for w in wins} for m in CD.MUSCLE_ZH}
    by_muscle: dict[str, dict[str, dict[str, dict]]] = {m: {w: {} for w in P.MUSCLE_RANGES} for m in CD.MUSCLE_ZH}
    overall: dict[str, dict[str, dict]] = {w: {} for w in P.MUSCLE_RANGES}
    for s in sports:
        if s["date"] > today or (s["date"] in logged_days and sport_muscles.normalize(s["type"]) in _COUNTED_BY_SETS):
            continue
        key = sport_muscles.normalize(s["type"])
        target = strength_targets(s["date"]) if strength_targets and key in sport_muscles.STRENGTH_LIKE else None
        load = sport_load(s, target)
        for wname, (a, b) in wins.items():
            if not a <= s["date"] <= b:
                continue
            show = wname in P.MUSCLE_RANGES
            if show:
                o = overall[wname].setdefault(s["type"], {"type": s["type"], "name": sport_name(s["type"]), "min": 0.0, "mapped": True,
                                                           "estimated": False})
                o["min"] += s["min"]
                o["estimated"] = o["estimated"] or load["estimated"]
            for m, (sets, primary) in load["muscles"].items():
                if m not in eq:
                    continue
                eq[m][wname] += sets
                if show:
                    factor = P.SPORT_PRIMARY_FACTOR if primary else P.SPORT_SECONDARY_FACTOR
                    stats[m][wname] += s["min"] * factor
                    it = by_muscle[m][wname].setdefault(s["type"], {"type": s["type"], "name": sport_name(s["type"]), "min": 0.0,
                                                                   "sets": 0.0, "primary": primary})
                    it["min"] += s["min"]
                    it["sets"] += sets

    def done(items: dict) -> list[dict]:
        return [{**x, "min": round(x["min"]), **({"sets": _r(x["sets"])} if "sets" in x else {})}
                for x in sorted(items.values(), key=lambda x: -x["min"])]
    return {"stats": stats, "sets": eq, "by_muscle": {m: {w: done(v) for w, v in ws.items()} for m, ws in by_muscle.items()},
            "overall": {w: done(v) for w, v in overall.items()}}


def _plan_targets(conn: psycopg.Connection, uid: int):
    """date -> (primary, secondary muscles) of the strength exercises the active plan has on that weekday, else None. Read once, lazily;
    any trouble reading the plan means None (whole body), never an error."""
    cache: dict[int, tuple[list[str], list[str]] | None] = {}
    state: dict = {}

    def for_day(day: date):
        dow = day.isoweekday()
        if dow in cache:
            return cache[dow]
        try:
            if "model" not in state:
                from . import day_overrides
                row = plans._active_plan_row(conn, uid)
                state["model"] = day_overrides.model_from_plan_row(conn, uid, row) if row else None
            wd = state["model"] and state["model"].by_dow.get(dow)
            prim: list[str] = []
            sec: list[str] = []
            for it in (wd.items if wd else []):
                if (it.get("category") or "strength") != "strength":
                    continue
                prim += [x for x in it.get("primary_muscles") or [] if x in CD.MUSCLE_ZH and x not in prim]
                sec += [x for x in it.get("secondary_muscles") or [] if x in CD.MUSCLE_ZH and x not in sec]
            cache[dow] = (prim, [x for x in sec if x not in prim]) if prim else None
        except Exception:  # noqa: BLE001 a plan that cannot be read must not hide the workout: whole body instead
            cache[dow] = None
        return cache[dow]
    return for_day


def _muscles_of(index: loads.Index, ex: dict) -> tuple[list[str], list[str], str | None]:
    """(primary, secondary, catalog slug) of a logged exercise; the catalog first, else SparkyFitness's own primary muscles."""
    slug = ex.get("slug") if ex.get("slug") in index.rows else index.slug_for(ex.get("exercise_id"), ex.get("name"))
    row = index.rows.get(slug or "")
    if row and (row.get("primary_muscles") or row.get("secondary_muscles")):
        prim = [m for m in row.get("primary_muscles") or [] if m in CD.MUSCLE_ZH]
        sec = [m for m in row.get("secondary_muscles") or [] if m in CD.MUSCLE_ZH and m not in prim]
        return prim, sec, slug
    prim = [str(m).lower() for m in ex.get("primary_muscles") or [] if str(m).lower() in CD.MUSCLE_ZH]
    return prim, [], slug


def _is_set(s: dict) -> bool:
    if str(s.get("type") or "").lower() in _WARMUP:
        return False
    return bool(s.get("reps")) or bool(s.get("duration_s"))


# ---------------------------------------------------------------- aggregation
def _empty() -> dict:
    return {"sets": 0.0, "volume_kg": 0.0}


def aggregate(workouts: list[dict], index: loads.Index, today: date) -> dict:
    """Per muscle: sets / volume in each window, the last day it was trained and with what. Also the exercises that matched nothing."""
    win = windows(today)
    stats: dict[str, dict[str, dict]] = {m: {w: _empty() for w in win} for m in CD.MUSCLE_ZH}
    last: dict[str, dict] = {}
    unmatched: set[str] = set()
    logged_days: set[date] = set()   # days with at least one credited set (a watch strength workout of such a day is not counted again)
    for w in workouts:
        day = date.fromisoformat(w["date"][:10])
        if day > today:
            continue
        for ex in w["exercises"]:
            if (ex.get("category") or "strength") in NOT_STRENGTH:
                continue
            sets = [s for s in ex.get("sets") or [] if _is_set(s)]
            if not sets:
                continue
            prim, sec, slug = _muscles_of(index, ex)
            if slug and (index.rows[slug].get("category") or "strength") in NOT_STRENGTH:
                continue
            if not prim and not sec:
                unmatched.add(str(ex.get("name") or ex.get("slug") or "?"))
                continue
            logged_days.add(day)
            kg = sum(float(s["reps"]) * float(s["weight_kg"]) for s in sets if s.get("reps") and s.get("weight_kg"))
            top = max(((float(s["weight_kg"]), int(s["reps"])) for s in sets if s.get("reps") and s.get("weight_kg")), default=None)
            name = ex.get("name") or (index.rows.get(slug or "") or {}).get("name_zh") or slug
            name_en = (index.rows.get(slug or "") or {}).get("name_en") or name
            for muscles, factor in ((prim, P.PRIMARY_FACTOR), (sec, P.SECONDARY_FACTOR)):
                for m in muscles:
                    for wname, (a, b) in win.items():
                        if a <= day <= b:
                            stats[m][wname]["sets"] += len(sets) * factor
                            stats[m][wname]["volume_kg"] += kg * factor
                    if day >= today - timedelta(days=P.MUSCLE_LOAD_BASELINE_DAYS - 1):
                        cur = last.get(m)
                        item = {"name": name, "name_en": name_en, "sets": len(sets), "top": f"{top[0]:g}×{top[1]}" if top else None,
                                "primary": factor == P.PRIMARY_FACTOR}
                        if cur is None or day > cur["date"]:
                            last[m] = {"date": day, "exercises": [item]}
                        elif day == cur["date"] and not any(e["name"] == name for e in cur["exercises"]):
                            cur["exercises"].append(item)
    return {"stats": stats, "last": last, "unmatched": sorted(unmatched), "logged_days": logged_days}


def _r(x: float, d: int = 1) -> float:
    return round(x + 1e-9, d)


def compute(conn: psycopg.Connection, owner_id: int, today: date | None = None) -> dict:
    """The whole map for one user (all three ranges at once, so the page switches without asking again)."""
    return _build(conn, owner_id, today)[0]


def _build(conn: psycopg.Connection, owner_id: int, today: date | None) -> tuple[dict, dict]:
    """compute()'s result and the sports behind each muscle (only the one-muscle detail lists them)."""
    today = today or plans._user_today(conn, owner_id)
    win = windows(today)
    start = min(today - timedelta(days=P.MUSCLE_LOAD_BASELINE_DAYS - 1), win["prev_week"][0])
    sessions, sparky_state = _sparky(conn, owner_id, start, today)
    imported = _imported(conn, owner_id, start, today)
    index = loads.Index(conn, owner_id)
    agg = aggregate([*sessions, *imported], index, today)
    sp = aggregate_sports(_sports(conn, owner_id, start, today), today, agg["logged_days"], _plan_targets(conn, owner_id))
    sex = (conn.execute("SELECT sex FROM users WHERE id = %s", (owner_id,)).fetchone() or {}).get("sex")
    muscles = []
    for key in CD.MUSCLE_ZH:
        st, lst = agg["stats"][key], agg["last"].get(key)
        low, high = weekly_range(key)
        eq = sp["sets"][key]

        def total(w: str) -> float:   # logged sets + the equivalent sets of the workouts (one scale)
            return st[w]["sets"] + eq[w] if w in st else eq[w]
        week, prev = total("week"), total("prev_week")
        muscles.append({
            "key": key, "name": L(CD.MUSCLE_ZH[key], MUSCLE_EN.get(key, key)), "group": CD.MUSCLE_GROUP.get(key),
            "ranges": {w: {"sets": _r(total(w)), "logged_sets": _r(st[w]["sets"]), "sport_sets": _r(eq[w]),
                           "volume_kg": round(st[w]["volume_kg"])} for w in P.MUSCLE_RANGES},
            "prev_week": {"sets": _r(prev), "logged_sets": _r(st["prev_week"]["sets"]), "sport_sets": _r(eq["prev_week"]),
                          "volume_kg": round(st["prev_week"]["volume_kg"])},
            "delta_sets": _r(week - prev),
            "target": {"low": low, "high": high},
            "status": {"week": status_for(key, week), "last7": status_for(key, total("last7"))},
            "trained_today": total("today") > 0,
            "sport_min": {w: round(sp["stats"][key][w]) for w in P.MUSCLE_RANGES},
            "last": lst and {"date": lst["date"].isoformat(),
                             "exercises": [{k: v for k, v in e.items() if v is not None} for e in lst["exercises"]][:8]},
        })
    return {"version": P.MUSCLE_LOAD_VERSION, "today": today.isoformat(), "week_start": win["week"][0].isoformat(),
            "sex": sex if sex in ("male", "female") else None,
            "source": {"sparky": sparky_state, "imported_days": len(imported)},
            "unmatched": agg["unmatched"], "sports": sp["overall"], "muscles": muscles}, sp["by_muscle"]


def detail(conn: psycopg.Connection, owner_id: int, muscle: str, today: date | None = None) -> dict:
    """One muscle of compute() plus recommended exercises from the catalog (banned ones left out, 'caution' marked)."""
    if muscle not in CD.MUSCLE_ZH:
        raise ValueError(L(f"没有这个肌群：{muscle}", f"Unknown muscle: {muscle}"))
    full, by_muscle = _build(conn, owner_id, today)
    sports = by_muscle[muscle]
    m = next(x for x in full["muscles"] if x["key"] == muscle)
    recs = []
    for it in catalog.search(conn, owner_id, muscle=muscle, include_banned=False, limit=P.RECOMMEND_LIMIT * 4):
        if it.get("status") == "banned" or muscle not in (it.get("primary_muscles") or []):
            continue
        recs.append({"slug": it["slug"], "name_zh": it.get("name_zh"), "name_en": it.get("name_en"), "status": it.get("status") or "active",
                     "equipment_zh": it.get("equipment_zh") or []})
        if len(recs) >= P.RECOMMEND_LIMIT:
            break
    return {**m, "today": full["today"], "recommended": recs, "source": full["source"], "sports": sports, "sex": full["sex"]}
