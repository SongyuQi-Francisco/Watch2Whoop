"""Adapter: the nutrition goals 「我的饮食档案」 shows and computes the deficit from.

Goals are owned by goals/nutrition.py (versioned training / rest-day sets, issue #31). That module already falls back to
SparkyFitness /goals/by-date for dates before a user's first version, so this adapter never calls SparkyFitness itself.
The diet profile never stores goals. Set names are built here at read time: English when the Today page asks for it
(X-Body-Lang: en); AI tools always run in zh.
"""
from datetime import date, timedelta

from ..goals import nutrition
from ..i18n import L
from . import diet_profile_params as P

SET_FIELDS = ("calories", "protein_g", "carbs_g", "fat_g", "sat_fat_max_g", "added_sugar_max_g", "fiber_min_g")


def _set(name: str, s: dict, days: int | None) -> dict:
    return {"name": name, **{f: s.get(f) for f in SET_FIELDS}, "days": days}


def current(conn, user_id: int, day: date, client=None) -> dict:
    """{source, sets: [{name, calories, protein_g, …, days (per week)}], avg_kcal (mean daily target over a week),
    version_id, effective_from, name, reason, error}."""
    cur = nutrition.current(conn, user_id, day, client)
    v = cur["version"]
    if v is not None:
        n = len(v["training_weekdays"])
        if n and v["training"] != v["rest"]:
            sets = [_set(L("训练日", "Training day"), v["training"], n), _set(L("休息日", "Rest day"), v["rest"], 7 - n)]
        else:   # no training days: every day is a rest day; or both sets equal
            sets = [_set(L("每天", "Every day"), v["training"] if n else v["rest"], 7)]
        kcal = (n * (v["training"].get("calories") or 0) + (7 - n) * (v["rest"].get("calories") or 0)) / 7
        return {"source": "body_engine", "sets": sets, "avg_kcal": kcal or None, "version_id": v["id"],
                "effective_from": v["effective_from"], "name": v["name"], "reason": v["reason"], "error": None}
    # No version yet: whatever nutrition.resolve gives (SparkyFitness fallback), over one week
    res = nutrition.resolver(conn, user_id, client)
    days = [res(day + timedelta(days=k)) for k in range(P.GOAL_LOOKAHEAD_DAYS)]
    groups: dict[tuple, dict] = {}
    for g in days:
        if not g.get("calories"):
            continue
        key = (g.get("name"), g.get("calories"), g.get("protein_g"), g.get("carbs_g"), g.get("fat_g"))
        if key not in groups:
            groups[key] = _set(g.get("name") or L("目标", "Target"), g, 0)
        groups[key]["days"] += 1
    kcal = [g["calories"] for g in days if g.get("calories")]
    return {"source": days[0]["source"] if kcal else None, "sets": list(groups.values()),
            "avg_kcal": sum(kcal) / len(kcal) if kcal else None, "version_id": None, "effective_from": None,
            "name": None, "reason": None, "error": None if kcal else L("还没有饮食目标", "No nutrition goals yet")}


def history(conn, user_id: int) -> list[dict]:
    """Goal versions that were ever in effect (newest first), compact for the version log."""
    return [{"version_id": h["id"], "effective_from": h["effective_from"], "created_at": h["created_at"],
             "training_kcal": h["training"].get("calories"), "rest_kcal": h["rest"].get("calories"),
             "training_days": len(h["training_weekdays"]), "reason": h["reason"], "set_by": h["set_by"]}
            for h in nutrition.history(conn, user_id) if not h.get("superseded")]
