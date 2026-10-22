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


def stress_days(conn, user_id: int, start: date | None, end: date | None, hours: bool = True) -> dict:
    end = end or today(conn, user_id)
    start = start or end
    rows = stress.read(conn, user_id, start, end, hours)
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
    r7 = sleep_detail.sri_for(conn, user_id, tz, end - timedelta(days=6), end)
    r30 = sleep_detail.sri_for(conn, user_id, tz, end - timedelta(days=29), end)
    return {"start": start, "end": end,
            "consistency_pct_7d": sleep_detail.consistency_pct(r7["sri"]), "sri_7d": r7["sri"], "sri_7d_pairs": r7["pairs"],
            "consistency_pct_30d": sleep_detail.consistency_pct(r30["sri"]), "sri_30d": r30["sri"],
            "sri_30d_pairs": r30["pairs"],
            "nights": sleep_detail.nights(conn, user_id, tz, start, end),
            "notes": "规律性 = 睡眠规律性指数 SRI（相隔 24 小时睡 / 醒状态相同的比例，−100～100，这里截到 0–100）；"
                     "入睡潜伏期只在睡着前有手表记的「清醒」片段时才有；效率 = 睡着 /（潜伏期开始 → 醒来）"}


def hr_zones(conn, user_id: int, start: date | None, end: date | None, viewer_id: int | None = None) -> dict:
    end = end or today(conn, user_id)
    with_workouts = "training" in auth.visible_scopes(conn, viewer_id, user_id, ("training",))
    return fitness.hr_zones(conn, user_id, start or end - timedelta(days=6), end, with_workouts)


def vo2max(conn, user_id: int, end: date | None = None, days: int = 365) -> dict:
    return fitness.vo2max(conn, user_id, end or today(conn, user_id), days)


def report(conn, user_id: int, period: str = "week", end: date | None = None, viewer_id: int | None = None) -> dict:
    """viewer_id: blocks outside the viewer's shares are left out and listed in `hidden` (None = the owner)."""
    return reports.report(conn, user_id, period, end or today(conn, user_id), viewer_id=viewer_id)
