"""Strain forecast for Today's strain sheet (2.0.1): what the day's strain usually ends at on days with today's planned
session.

- Today's planned sessions: the active Body Engine plan's sessions of this weekday (training_plans.days), or the day change
  of this date (training_day_overrides: other weekdays' sessions or a free activity label; rest = nothing). Nothing
  planned (no plan, rest day, before the plan's start date) -> no forecast.
- Past days of the same session: dates before today whose logged session (logged_workouts.name; a training-day submit
  names it after the plan sessions joined by " · ") or watch workout (workouts.name_zh / its type name) has one of the
  planned names (case and spaces ignored).
- Forecast: the mean daily strain (daily_scores.strain) of the latest params.FORECAST_SESSIONS such days with a strain,
  within params.FORECAST_LOOKBACK_DAYS. Fewer days give the mean of what there is, with n; none gives strain = null.
Reads need the training share (plan and logs) and scores (strain); the endpoint checks both (insights/api.py).
"""
from __future__ import annotations

from datetime import date, timedelta

import psycopg

from . import params as P
from .importer import type_name

SEP = " · "   # training/session_log.py names a submitted session after the plan sessions joined by this


def norm(name: str | None) -> str:
    return "".join((name or "").split()).casefold()


def planned_names(conn: psycopg.Connection, user_id: int, d: date) -> list[str]:
    """Names of the sessions planned for date d (in plan order); [] when nothing is planned."""
    plan = conn.execute(
        """SELECT days, start_date FROM training_plans WHERE user_id = %s AND active = TRUE
           ORDER BY updated_at DESC, id DESC LIMIT 1""", (user_id,)).fetchone()
    if plan is None or d < plan["start_date"]:
        return []
    by_dow: dict[int, list[str]] = {}
    for day in plan["days"] or []:
        by_dow.setdefault(int(day["day_of_week"]), []).append(day["name"])
    ov = conn.execute("SELECT sessions FROM training_day_overrides WHERE user_id = %s AND date = %s",
                      (user_id, d)).fetchone()
    refs = (ov["sessions"] or []) if ov is not None else [{"dow": d.isoweekday()}]
    names: list[str] = []
    for ref in refs:
        if ref.get("dow"):
            names += by_dow.get(int(ref["dow"]), [])
        elif ref.get("label"):
            names.append(ref["label"])
    return list(dict.fromkeys(n for n in names if n and n.strip()))


def session_days(conn: psycopg.Connection, user_id: int, start: date, end: date) -> dict[date, set[str]]:
    """Normalised names of what was trained on each date of [start, end]: logged sessions and watch workouts."""
    out: dict[date, set[str]] = {}
    for r in conn.execute("SELECT date, name FROM logged_workouts WHERE user_id = %s AND date BETWEEN %s AND %s",
                          (user_id, start, end)):
        out.setdefault(r["date"], set()).update(norm(p) for p in [r["name"] or "", *(r["name"] or "").split(SEP)] if p)
    for r in conn.execute("""SELECT local_date, type, name_zh FROM workouts WHERE user_id = %s AND local_date BETWEEN %s AND %s
                             AND dup_of IS NULL AND excluded IS NULL""", (user_id, start, end)):
        out.setdefault(r["local_date"], set()).update(n for n in (norm(r["name_zh"]), norm(type_name(r["type"]))) if n)
    return out


def compute(names: list[str], days: dict[date, set[str]], strain: dict[date, float], d: date,
            sessions: int = P.FORECAST_SESSIONS) -> dict | None:
    """The pure part: planned names, trained names by date, daily strain by date, today -> the forecast or None."""
    if not names:
        return None
    want = {norm(n) for n in names}
    hits = [x for x in sorted(days, reverse=True) if x < d and days[x] & want and strain.get(x) is not None][:sessions]
    vals = [float(strain[x]) for x in hits]
    return {"sessions": names, "strain": round(sum(vals) / len(vals), 1) if vals else None, "n": len(vals),
            "of": sessions, "dates": hits}


def forecast(conn: psycopg.Connection, user_id: int, d: date) -> dict | None:
    names = planned_names(conn, user_id, d)
    if not names:
        return None
    lo = d - timedelta(days=P.FORECAST_LOOKBACK_DAYS)
    hi = d - timedelta(days=1)
    strain = {r["date"]: r["strain"] for r in conn.execute(
        "SELECT date, strain FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s AND strain IS NOT NULL",
        (user_id, lo, hi))}
    return compute(names, session_days(conn, user_id, lo, hi), strain, d)
