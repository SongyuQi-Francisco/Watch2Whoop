"""Adapter: the nutrition goals 「我的饮食档案」 shows and computes the deficit from.

Goals are owned elsewhere: versioned nutrition goals (issue #31, training / rest day sets with history) when that module
is installed, otherwise the goal SparkyFitness has in effect (sparky_client.goal_for_date, which follows the weekly goal
plan). The diet profile never stores goals itself.

Hook for #31: a module at one of GOAL_MODULES exposing
    current_goals(conn, user_id, day) -> {"training_day": {...}, "rest_day": {...}, "effective_from": date?, "version": str?}
(each set: calories / kcal, protein_g, carbs_g, fat_g, optional sat_fat_max_g, added_sugar_max_g, fiber_g, water_ml)
is used automatically; anything else falls back to SparkyFitness.
"""
import importlib
from datetime import date, timedelta

from .. import sparky_client
from . import diet_profile_params as P

GOAL_MODULES = ("body_engine.goals.nutrition", "body_engine.nutrition_goals", "body_engine.assistant.nutrition_goals")
FIELDS = ("calories", "protein_g", "carbs_g", "fat_g", "sat_fat_max_g", "added_sugar_max_g", "fiber_g", "water_ml")
SET_LABEL = {"training_day": "训练日", "rest_day": "休息日"}


def _goals_module():
    for name in GOAL_MODULES:
        try:
            m = importlib.import_module(name)
        except ImportError:
            continue
        if callable(getattr(m, "current_goals", None)):
            return m
    return None


def _norm(name: str | None, g: dict) -> dict:
    out = {"name": name}
    for k in FIELDS:
        v = g.get(k)
        if v is None and k == "calories":
            v = g.get("kcal")
        out[k] = v
    return out


def _from_module(m, conn, user_id: int, day: date) -> dict:
    g = m.current_goals(conn, user_id, day) or {}
    sets = [_norm(SET_LABEL.get(k, k), g[k]) for k in ("training_day", "rest_day") if isinstance(g.get(k), dict)]
    kcal = [s["calories"] for s in sets if s["calories"]]
    return {"source": "nutrition_goals", "sets": sets, "avg_kcal": sum(kcal) / len(kcal) if kcal else None,
            "effective_from": g.get("effective_from"), "version": g.get("version"), "error": None}


def _from_sparky(conn, user_id: int, day: date, client=None) -> dict:
    try:
        client = client or sparky_client.client_for_user(conn, user_id)
        days = [sparky_client.goal_for_date(client, day + timedelta(days=k)) for k in range(P.GOAL_LOOKAHEAD_DAYS)]
    except (sparky_client.SparkyError, ValueError) as e:
        return {"source": "sparkyfitness", "sets": [], "avg_kcal": None, "error": str(e)}
    sets: dict[tuple, dict] = {}
    for g in days:
        if not g.get("calories"):
            continue
        key = (g.get("name"), g.get("calories"), g.get("protein_g"), g.get("carbs_g"), g.get("fat_g"))
        if key not in sets:
            sets[key] = {**_norm(g.get("name"), g), "days": 0}
        sets[key]["days"] += 1
    kcal = [g["calories"] for g in days if g.get("calories")]
    return {"source": "sparkyfitness", "sets": list(sets.values()),
            "avg_kcal": sum(kcal) / len(kcal) if kcal else None, "error": None}


def current(conn, user_id: int, day: date, client=None) -> dict:
    """{source, sets: [{name, calories, protein_g, carbs_g, fat_g, ...}], avg_kcal (mean daily target over a week), error}."""
    m = _goals_module()
    if m is not None:
        try:
            with conn.transaction():  # savepoint: a failing query there must not abort the caller's transaction
                return _from_module(m, conn, user_id, day)
        except Exception as e:  # a broken goals module must not hide the diet profile
            out = _from_sparky(conn, user_id, day, client)
            return {**out, "error": out["error"] or f"饮食目标模块出错：{e}"}
    return _from_sparky(conn, user_id, day, client)
