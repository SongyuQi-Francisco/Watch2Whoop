"""Muscle load map (rollout flag muscle_load): which muscles were trained today / this week and how much.

Sources of sets: the user's SparkyFitness strength workouts (which already hold what the training page submitted and what the AI
logged) and, when the Hevy import (hevy_import) is there, its imported sets in training_session_logs (status 'imported').
Unfinished page drafts and sets without reps or duration are not counted; warm-up sets are not counted; cardio / stretching are not strength.
Each set is credited to the muscles of its exercise in the catalog: primary muscles x PRIMARY_FACTOR (1.0), secondary x SECONDARY_FACTOR (0.5).
Two numbers per muscle and range: effective sets (the weighted count) and volume in kg (reps x weight, weighted the same way; bodyweight
sets add sets but no kg). Colours compare this week's effective sets with training/params.WEEKLY_SETS_RANGE (sources there).
Sports (issue #189): watch workouts of the workouts table (basketball, swimming, running ...; not duplicates, not excluded / hidden,
not strength types) are a separate layer counted in minutes: primary muscles of params.SPORT_MUSCLES x 1, secondary x 0.5. They never
become sets and never change the colours; types without a mapping count for no muscle and only appear in the overall list.
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
from . import catalog, loads, params as P, plans
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
    """Watch workouts that are not strength training, one row per workout (duplicates and excluded / hidden ones left out)."""
    rows = conn.execute("""SELECT local_date, type, duration_min FROM workouts
                           WHERE user_id = %s AND local_date BETWEEN %s AND %s AND dup_of IS NULL AND excluded IS NULL
                             AND NOT (type = ANY(%s)) ORDER BY start_at""",
                        (uid, start, end, sorted(P.STRENGTH_TYPES))).fetchall()
    return [{"date": r["local_date"], "type": r["type"], "min": float(r["duration_min"] or 0)} for r in rows if r["duration_min"]]


def sport_name(t: str) -> str:
    return L(P.TYPE_NAMES.get(t, t), P.TYPE_NAMES_EN.get(t, t))


def aggregate_sports(sports: list[dict], today: date) -> dict:
    """Minutes per muscle and window (primary x SPORT_PRIMARY_FACTOR, secondary x SPORT_SECONDARY_FACTOR), the sports behind
    each muscle and the overall list per window (also the types without a muscle mapping)."""
    win = {k: v for k, v in windows(today).items() if k in P.MUSCLE_RANGES}
    stats = {m: {w: 0.0 for w in win} for m in CD.MUSCLE_ZH}
    by_muscle: dict[str, dict[str, dict[str, dict]]] = {m: {w: {} for w in win} for m in CD.MUSCLE_ZH}
    overall: dict[str, dict[str, dict]] = {w: {} for w in win}
    for s in sports:
        prim, sec = P.SPORT_MUSCLES.get(s["type"], ((), ()))
        for wname, (a, b) in win.items():
            if not a <= s["date"] <= b:
                continue
            o = overall[wname].setdefault(s["type"], {"type": s["type"], "name": sport_name(s["type"]), "min": 0.0,
                                                       "mapped": s["type"] in P.SPORT_MUSCLES})
            o["min"] += s["min"]
            for muscles, factor in ((prim, P.SPORT_PRIMARY_FACTOR), (sec, P.SPORT_SECONDARY_FACTOR)):
                for m in muscles:
                    if m not in stats:
                        continue
                    stats[m][wname] += s["min"] * factor
                    it = by_muscle[m][wname].setdefault(s["type"], {"type": s["type"], "name": sport_name(s["type"]), "min": 0.0,
                                                                   "primary": factor == P.SPORT_PRIMARY_FACTOR})
                    it["min"] += s["min"]

    def done(items: dict) -> list[dict]:
        return [{**x, "min": round(x["min"])} for x in sorted(items.values(), key=lambda x: -x["min"])]
    return {"stats": stats, "by_muscle": {m: {w: done(v) for w, v in ws.items()} for m, ws in by_muscle.items()},
            "overall": {w: done(v) for w, v in overall.items()}}


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
    return {"stats": stats, "last": last, "unmatched": sorted(unmatched)}


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
    sp = aggregate_sports(_sports(conn, owner_id, min(win["week"][0], win["last7"][0]), today), today)
    sex = (conn.execute("SELECT sex FROM users WHERE id = %s", (owner_id,)).fetchone() or {}).get("sex")
    muscles = []
    for key in CD.MUSCLE_ZH:
        st, lst = agg["stats"][key], agg["last"].get(key)
        low, high = weekly_range(key)
        week, prev = st["week"], st["prev_week"]
        muscles.append({
            "key": key, "name": L(CD.MUSCLE_ZH[key], MUSCLE_EN.get(key, key)), "group": CD.MUSCLE_GROUP.get(key),
            "ranges": {w: {"sets": _r(st[w]["sets"]), "volume_kg": round(st[w]["volume_kg"])} for w in P.MUSCLE_RANGES},
            "prev_week": {"sets": _r(prev["sets"]), "volume_kg": round(prev["volume_kg"])},
            "delta_sets": _r(week["sets"] - prev["sets"]),
            "target": {"low": low, "high": high},
            "status": {"week": status_for(key, week["sets"]), "last7": status_for(key, st["last7"]["sets"])},
            "trained_today": st["today"]["sets"] > 0,
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
