"""Daily nutrition: SparkyFitness food entries + food_entry_extras + the goal in effect each day (goals/nutrition.py).

Used by get_nutrition, the Today page nutrition block, rules, the status card and reports, so they all count the same way:
- calories / macros: sum of the day's entries (0 on a day without entries; logged=false says it was not logged, which
  is not the same as eating nothing);
- sugar_g / added_sugar_g / fiber_g / sodium_mg: sum of the entries that know them, None when none do; `known` says how
  many of the day's entries knew each (unknown is never counted as 0);
- adherence: calories within +-CALORIE_ADHERENCE_TOL of that day's goal, protein >= PROTEIN_ADHERENCE_MIN of it
  (rules/params.py); None when the day is not logged or has no goal.
"""
from datetime import date, timedelta

import psycopg

from .. import sparky_client
from ..goals import nutrition as goals
from ..rules import params as P
from . import extras

MACRO_KEYS = ("calories", "protein_g", "carbs_g", "fat_g", "saturated_fat_g")
GOAL_KEYS = ("name", "day_type", "source", "calories", "protein_g", "carbs_g", "fat_g", "sat_fat_max_g",
             "added_sugar_max_g", "fiber_min_g")


def entries(conn: psycopg.Connection, user_id: int, start: date, end: date, client=None) -> list[dict]:
    """Food entries in [start, end] with sugar / fiber / sodium merged in (extras.merge), oldest first."""
    client = client or sparky_client.reader_for_user(conn, user_id)
    rows = sparky_client.food_entries(client, start, end)
    merged = {r["entry_id"]: r["id"] for r in conn.execute(   # entries merged from several (issue #93): the page can split them
        "SELECT id, entry_id FROM food_merges WHERE user_id = %s AND date BETWEEN %s AND %s AND undone_at IS NULL",
        (user_id, start, end))}
    for r in rows:
        if str(r.get("id")) in merged:
            r["merge_id"] = merged[str(r["id"])]
    return sorted(extras.merge(rows, extras.load(conn, user_id, start, end)), key=lambda r: (r["date"], r.get("time") or ""))


def adherence(day: dict, goal: dict | None) -> dict:
    out = {"calories": None, "protein": None}
    if not day.get("logged") or not goal:
        return out
    if goal.get("calories"):
        out["calories"] = abs(day["calories"] - goal["calories"]) <= P.CALORIE_ADHERENCE_TOL * goal["calories"]
    if goal.get("protein_g"):
        out["protein"] = day["protein_g"] >= P.PROTEIN_ADHERENCE_MIN * goal["protein_g"]
    return out


def summarize_day(day: date, rows: list[dict], goal: dict | None) -> dict:
    d = {"date": day.isoformat(), "logged": bool(rows),
         **{k: round(sum(r.get(k) or 0 for r in rows), 1) for k in MACRO_KEYS}, **extras.day_totals(rows)}
    d["goal"] = {k: goal.get(k) for k in GOAL_KEYS} if goal else None
    d["adherence"] = adherence(d, goal)
    return d


def nutrition(conn: psycopg.Connection, user_id: int, start: date, end: date, include_entries: bool = True,
              client=None) -> dict:
    client = client or sparky_client.reader_for_user(conn, user_id)
    rows = entries(conn, user_id, start, end, client)
    goal_of = goals.resolver(conn, user_id, client)
    by_day: dict[str, list] = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    days = []
    d = start
    while d <= end:
        days.append(summarize_day(d, by_day.get(d.isoformat(), []), goal_of(d)))
        d += timedelta(days=1)
    out = {"days": days}
    if include_entries:
        out["entries"] = rows
    return out
