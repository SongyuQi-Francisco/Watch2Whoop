"""Reads and writes sleep_goals, and the view shared by the API / assistant / rules. Callers handle authorization (reading needs can_view, writing is only for oneself)."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import queries
from . import params as P
from . import sleep as S

HISTORY_DAYS = 21


def user_tz(conn: psycopg.Connection, user_id: int) -> ZoneInfo:
    row = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None:
        raise ValueError(f"没有用户 {user_id}")
    return ZoneInfo(row["timezone"] or "Australia/Sydney")


def tonight(tz: ZoneInfo, now: datetime | None = None) -> date:
    """Tonight (sleep day): before 12 noon it still counts as the previous night not yet over (still awake at 3 a.m., "tonight" is still the night that began yesterday evening)."""
    return S.night_of((now or datetime.now(tz)).astimezone(tz))


def load_nights(conn, user_id: int, tz: ZoneInfo, night: date, days: int = HISTORY_DAYS) -> list[dict]:
    sessions = queries.sleep_sessions(conn, user_id, night - timedelta(days=days), night + timedelta(days=1))
    return [n for n in S.nights_from_sessions(sessions, tz) if n["night"] < night]


def get_goal(conn, user_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM sleep_goals WHERE user_id = %s", (user_id,)).fetchone()
    if row is None:
        return None
    row = dict(row)
    row["target_bedtime"] = row["target_bedtime"].strftime("%H:%M")
    row["target_wake"] = row["target_wake"].strftime("%H:%M") if row["target_wake"] else None
    row["plan_rows"] = S.build_plan(row["start_bedtime_off"], S.to_off(row["target_bedtime"]), row["start_date"],
                                    row["pace_min_per_step"], row["step_days"])
    return row


def set_goal(conn, user_id: int, target_bedtime: str, target_wake: str | None = None, pace_min_per_step: int | None = None,
             step_days: int | None = None, target_duration_min: int | None = None, status: str | None = None,
             now: datetime | None = None) -> dict:
    """Set / change the goal. The starting point is reset each time: start_date = tonight, start = average bedtime of the last 7 nights (start directly from the target if there is no data).
    pace / step_days / duration not given fall back to the old goal, and to defaults if there is no old goal either."""
    target = S.parse_hhmm(target_bedtime)
    wake = S.parse_hhmm(target_wake) if target_wake else None
    old = get_goal(conn, user_id) or {}
    pace = pace_min_per_step if pace_min_per_step is not None else old.get("pace_min_per_step", P.DEFAULT_PACE_MIN)
    steps = step_days if step_days is not None else old.get("step_days", P.DEFAULT_STEP_DAYS)
    if not P.PACE_RANGE[0] <= pace <= P.PACE_RANGE[1]:
        raise ValueError(f"每档提前 {P.PACE_RANGE[0]}–{P.PACE_RANGE[1]} 分钟（生物钟一天只能挪 15–30 分钟）")
    if not P.STEP_DAYS_RANGE[0] <= steps <= P.STEP_DAYS_RANGE[1]:
        raise ValueError(f"每档 {P.STEP_DAYS_RANGE[0]}–{P.STEP_DAYS_RANGE[1]} 天")
    duration = target_duration_min if target_duration_min is not None else old.get("target_duration_min")
    if duration is not None and not 240 <= duration <= 720:
        raise ValueError("睡眠时长 240–720 分钟")
    tz = user_tz(conn, user_id)
    night = tonight(tz, now)
    recent = S.avg(S.window(load_nights(conn, user_id, tz, night), night), "bed_off")
    start = S.round5(recent) if recent is not None else S.to_off(target)
    conn.execute(
        """INSERT INTO sleep_goals (user_id, target_bedtime, target_wake, target_duration_min, pace_min_per_step, step_days,
                                    start_date, start_bedtime_off, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (user_id) DO UPDATE SET target_bedtime = EXCLUDED.target_bedtime, target_wake = EXCLUDED.target_wake,
             target_duration_min = EXCLUDED.target_duration_min, pace_min_per_step = EXCLUDED.pace_min_per_step,
             step_days = EXCLUDED.step_days, start_date = EXCLUDED.start_date,
             start_bedtime_off = EXCLUDED.start_bedtime_off, status = EXCLUDED.status, updated_at = now()""",
        (user_id, target, wake, duration, pace, steps, night, start, status or "active"))
    return view(conn, user_id, now=now)


def duration(conn, user_id: int, goal: dict | None, night: date) -> int:
    if goal and goal.get("target_duration_min"):
        return goal["target_duration_min"]
    row = conn.execute("""SELECT sleep_need_min FROM daily_scores WHERE user_id = %s AND date <= %s
                          AND sleep_need_min IS NOT NULL ORDER BY date DESC LIMIT 1""",
                       (user_id, night + timedelta(days=1))).fetchone()
    return row["sleep_need_min"] if row else P.DEFAULT_DURATION_MIN


def compute(goal: dict | None, nights: list[dict], night: date, duration: int) -> dict:
    """Pure function: the API's response structure."""
    prog = S.progress(goal, nights, night)
    recent = {x["night"]: x for x in S.window(nights, night)}
    out = {"night": night, "target_bedtime": None, "target_wake": None, "target_duration_min": None,
           "pace_min_per_step": None, "step_days": None, "start_date": None, "start_bedtime": None, "status": None,
           "tonight_target_bedtime": None, "tonight_target_wake": None, "plan": [], "plan_end_date": None,
           "progress": prog, "recent_nights": []}
    goal_on = goal is not None and goal["status"] == "active"
    tgt = {}
    if goal is not None:
        plan, pace = goal["plan_rows"], goal["pace_min_per_step"]
        tgt = {d: S.target_for(plan, nights, d, pace) for d in recent}
        out.update({k: goal[k] for k in ("target_bedtime", "target_wake", "pace_min_per_step", "step_days", "start_date",
                                          "status")})
        out.update(target_duration_min=goal["target_duration_min"] or duration, start_bedtime=S.fmt(goal["start_bedtime_off"]),
                   plan=[{"from_date": st["from_date"], "bedtime": S.fmt(st["bed_off"])} for st in plan],
                   plan_end_date=plan[-1]["from_date"])
        if goal_on:
            t = S.target_for(plan, nights, night, pace)
            out["tonight_target_bedtime"] = S.fmt(t)
            out["tonight_target_wake"] = goal["target_wake"] or (S.fmt(t + duration) if t is not None else None)
    out["recent_nights"] = [
        {"night": d, "bedtime": x["bedtime"], "wake": x["wake"], "target": S.fmt(tgt.get(d)),
         "on_target": None if tgt.get(d) is None else x["bed_off"] <= tgt[d] + P.ON_TARGET_TOL_MIN}
        for d, x in sorted(recent.items())]
    return out


def view(conn, user_id: int, night: date | None = None, now: datetime | None = None) -> dict:
    tz = user_tz(conn, user_id)
    night = night or tonight(tz, now)
    goal = get_goal(conn, user_id)
    return compute(goal, load_nights(conn, user_id, tz, night), night, duration(conn, user_id, goal, night))
