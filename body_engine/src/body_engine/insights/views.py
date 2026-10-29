"""One read function per analytics feature: shared by the HTTP API (api.py) and the body butler tools (tools.py), so both sides see the same thing.
An empty date parameter = today in the user's time zone."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import auth
from . import coach, fitness, healthspan_store, reports, sleep_detail, stress


def user_tz(conn: psycopg.Connection, user_id: int) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])


def today(conn, user_id: int) -> date:
    return datetime.now(user_tz(conn, user_id)).date()


# body-age item -> the scope of the data behind it (the item's value, e.g. weight_kg or steps, is that data)
HEALTHSPAN_SCOPE = {"sleep_duration": "scores", "sleep_regularity": "sleep", "steps": "metrics", "mvpa": "scores",
                    "strength": "training", "vo2max": "metrics", "rhr": "metrics", "body_comp": "weight",
                    "activity": ("metrics", "training")}   # v1.2 domain: steps + training minutes
ACTIVITY_PART_SCOPE = {"steps": "metrics", "exercise_equiv": "training", "strength": "training", "mvpa": "scores"}


def _scopes(key) -> set[str]:
    s = HEALTHSPAN_SCOPE.get(key, "scores")
    return {s} if isinstance(s, str) else set(s)


def healthspan(conn, user_id: int, weeks: int = 26, viewer_id: int | None = None) -> dict:
    """Someone else's body age: items built from data the viewer may not see are left out, their scopes in `hidden`."""
    res = healthspan_store.read(conn, user_id, weeks)
    if res is None:
        res = _healthspan_fallback(conn, user_id, weeks)
    if res is not None and viewer_id is not None and viewer_id != user_id:
        every = set().union(*(_scopes(k) for k in HEALTHSPAN_SCOPE))
        can = auth.visible_scopes(conn, viewer_id, user_id, every | set(ACTIVITY_PART_SCOPE.values()))
        ok = lambda it: _scopes(it.get("key")) <= can
        hidden = sorted(every - can)

        def parts(it):
            if not it.get("parts"):
                return it
            return {**it, "parts": [p for p in it["parts"] if ACTIVITY_PART_SCOPE.get(p.get("key"), "scores") in can]}
        res = {**res, "contributors": [parts(i) for i in res["contributors"] or [] if ok(i)],
               "insufficient": [i for i in res["insufficient"] or [] if ok(i)], **({"hidden": hidden} if hidden else {})}
    if res is None:
        return {"body_age": None, "chrono_age": None, "delta_years": None, "pace_of_aging": None, "pace_raw": None,
                "window_weeks": None, "as_of": None,
                "history": [], "contributors": [], "insufficient": [],
                "missing_reason": "还没算（body-engine compute-scores 会顺带算），或者没有生日 / 手表数据"}
    return res


def _healthspan_fallback(conn, user_id: int, weeks: int) -> dict | None:
    """2.0.0 S9: body age is the phone's (package `coach` primary) and it has not uploaded a week yet: computed on demand
    with the Python twin, not stored, recorded as a fallback. None for everyone else (unchanged behaviour)."""
    from ..nativesync import primary
    if not primary.server_primary(conn, user_id, "coach"):
        return None
    rows = healthspan_store.compute(conn, user_id)
    primary.fallback(conn, user_id, "coach", "healthspan", today(conn, user_id))
    rows = [{**r, "algo_version": healthspan_store.CURRENT.ALGO_VERSION} for r in reversed(rows[-weeks:])]
    return healthspan_store.view(rows)


def _stress_fallback(conn, user_id: int, start: date, end: date, rows: list[dict], hours: bool) -> list[dict]:
    """2.0.0 S9: stress is the phone's (package `stress` primary) and days of the range have no row: the newest of them
    (at most FALLBACK_MAX_DAYS) are computed on demand with the Python twin, not stored, recorded as a fallback."""
    from ..nativesync import params as NP, primary
    have = {r["date"] for r in rows}
    missing = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    missing = [d for d in missing if d not in have][-NP.FALLBACK_MAX_DAYS:]
    if not missing or not primary.server_primary(conn, user_id, "stress"):
        return rows
    cols = ("date", "score", "level", "low_h", "medium_h", "high_h", "peak_hour", "baseline", "flags", "algo_version",
            *(("hours",) if hours else ()))
    want = set(missing)
    got = [{k: ({**r, "algo_version": stress.STRESS_VERSION}).get(k) for k in cols}
           for r in stress.compute(conn, None, user_id, missing[0], missing[-1], ow_cache="use") if r["date"] in want]
    primary.fallback(conn, user_id, "stress", "stress", missing[-1])
    return sorted(rows + got, key=lambda r: r["date"])


def stress_days(conn, user_id: int, start: date | None, end: date | None, hours: bool = True) -> dict:
    end = end or today(conn, user_id)
    start = start or end
    rows = _stress_fallback(conn, user_id, start, end, stress.read(conn, user_id, start, end, hours), hours)
    return {"start": start, "end": end, "scale": "0–3：< 1 低、1–2 中、≥ 2 高（醒着不动时心率 / HRV 相对平静基线）",
            "days": rows}


def strain_coach(conn, user_id: int, d: date | None = None) -> dict:
    return coach.strain_coach(conn, user_id, d or today(conn, user_id))


SLEEP_COACH_NEED = ("date", "need_min", "need_parts", "sleep_debt_min", "strain_today")   # from the scores row


def sleep_coach(conn, user_id: int, d: date | None = None, viewer_id: int | None = None) -> dict:
    """Without the sleep share only tonight's need is given: wake times, latency and the bedtime goal are sleep data."""
    out = coach.sleep_coach(conn, user_id, d or today(conn, user_id), user_tz(conn, user_id))
    if "sleep" in auth.visible_scopes(conn, viewer_id, user_id, ("sleep",)):
        return out
    return {**{k: out[k] for k in SLEEP_COACH_NEED}, "hidden": ["sleep"]}


def health_monitor(conn, user_id: int, d: date | None = None) -> dict:
    return coach.health_monitor(conn, user_id, d or today(conn, user_id))


def sleep_details(conn, user_id: int, start: date | None, end: date | None) -> dict:
    tz = user_tz(conn, user_id)
    end = end or datetime.now(tz).date()
    start = start or end - timedelta(days=13)
    lo = min(start, end - timedelta(days=29))
    sessions = sleep_detail.load_intervals(conn, user_id, lo, end)
    return sleep_detail.details(sessions, sleep_detail.nights(conn, user_id, tz, start, end), tz, start, end)


def hr_zones(conn, user_id: int, start: date | None, end: date | None, viewer_id: int | None = None) -> dict:
    end = end or today(conn, user_id)
    with_workouts = "training" in auth.visible_scopes(conn, viewer_id, user_id, ("training",))
    return fitness.hr_zones(conn, user_id, start or end - timedelta(days=6), end, with_workouts)


def vo2max(conn, user_id: int, end: date | None = None, days: int = 365) -> dict:
    return fitness.vo2max(conn, user_id, end or today(conn, user_id), days)


def report(conn, user_id: int, period: str = "week", end: date | None = None, viewer_id: int | None = None) -> dict:
    """viewer_id: blocks outside the viewer's shares are left out and listed in `hidden` (None = the owner)."""
    return reports.report(conn, user_id, period, end or today(conn, user_id), viewer_id=viewer_id)
