"""Pure computation for the bedtime goal (no database access).

"Sleep day": a night belongs to the evening before falling asleep (night = date of (bedtime − 12h)), and bedtime is expressed as "minutes relative to the midnight after that evening":
00:00 -> 0, 23:00 -> −60, 06:30 -> +390, range [−720, 720). So 06:30 is 6.5 hours later than 00:00 (+390), not 17.5 hours earlier.
Wake time is likewise relative to that midnight (13:30 -> +810), so no other cut-off point is needed.

Plan: start from the average bedtime of the last 7 nights when the goal was set (rounded to 5 minutes); step k (k = 0, 1, …) = max(start − (k+1) × minutes per step, target),
effective from the night of start_date + k × step_days, until the target is reached.
Tonight's target = the later of this plan step and "last-7-night average − one step": when far behind the plan, do not demand catching up overnight (the body clock moves only 15–30 minutes a day).
"""
import math
from datetime import date, datetime, time, timedelta
from statistics import fmean
from zoneinfo import ZoneInfo

from . import params as P


# ---------------------------------------------------------------- time conversion
def to_off(t: time | str) -> int:
    """Clock time -> sleep-day minutes (relative to midnight, [−720, 720))."""
    if isinstance(t, str):
        t = parse_hhmm(t)
    m = t.hour * 60 + t.minute
    return (m + 720) % 1440 - 720


def parse_hhmm(s: str) -> time:
    try:
        h, m = s.strip().split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        raise ValueError(f"时间要写成 HH:MM（24 小时制），收到：{s!r}")


def fmt(off: float | None) -> str | None:
    """Sleep-day minutes -> HH:MM."""
    if off is None:
        return None
    m = round(off) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def night_of(start_local: datetime) -> date:
    return (start_local - timedelta(hours=12)).date()


def nights_from_sessions(sessions: list[dict], tz: ZoneInfo) -> list[dict]:
    """sleep_sessions -> one main sleep per night: {night, bed_off, wake_off, bedtime, wake, asleep_min} (ascending by night)."""
    best: dict[date, dict] = {}
    for s in sessions:
        if not s.get("is_main") or s.get("start_at") is None or s.get("end_at") is None:
            continue
        start, end = s["start_at"].astimezone(tz), s["end_at"].astimezone(tz)
        n = night_of(start)
        midnight = datetime.combine(n + timedelta(days=1), time(0), tz)
        row = {"night": n, "bed_off": round((start - midnight).total_seconds() / 60),
               "wake_off": round((end - midnight).total_seconds() / 60), "asleep_min": s.get("asleep_min") or 0}
        row["bedtime"], row["wake"] = fmt(row["bed_off"]), fmt(row["wake_off"])
        if n not in best or row["asleep_min"] > best[n]["asleep_min"]:
            best[n] = row
    return [best[n] for n in sorted(best)]


# ---------------------------------------------------------------- plan
def round5(x: float) -> int:
    return int(5 * round(x / 5))


def build_plan(start_off: int, target_off: int, start_date: date, pace: int, step_days: int) -> list[dict]:
    """[{from_date, bed_off}]: stepping earlier from the night of start_date, the last step = target. Only one step if the start is no later than the target."""
    if pace <= 0 or step_days <= 0:
        raise ValueError("pace 和 step_days 要大于 0")
    plan, k = [], 0
    while True:
        off = max(start_off - (k + 1) * pace, target_off)
        plan.append({"from_date": start_date + timedelta(days=k * step_days), "bed_off": off})
        if off == target_off:
            return plan
        k += 1


def stage_for(plan: list[dict], night: date) -> dict | None:
    """The step in effect that night (none before start_date)."""
    cur = None
    for st in plan:
        if st["from_date"] <= night:
            cur = st
    return cur


def window(nights: list[dict], night: date, n: int = P.WINDOW_NIGHTS) -> list[dict]:
    """The n nights before night (excluding night itself)."""
    lo = night - timedelta(days=n)
    return [x for x in nights if lo <= x["night"] < night]


def avg(rows: list[dict], key: str, min_n: int = P.MIN_NIGHTS) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(fmean(vals), 1) if len(vals) >= min_n else None


def target_for(plan: list[dict], nights: list[dict], night: date, pace: int) -> int | None:
    """A night's target: the later of this plan step and "previous 7-night average − one step"."""
    st = stage_for(plan, night)
    if st is None:
        return None
    recent = avg(window(nights, night), "bed_off")
    return st["bed_off"] if recent is None else max(st["bed_off"], round5(recent - pace))


def trend_per_week(nights: list[dict], night: date) -> float | None:
    """Slope (minutes / week) of a linear regression over bedtimes of the last 14 nights; negative = moving earlier."""
    rows = window(nights, night, P.TREND_NIGHTS)
    if len(rows) < P.MIN_TREND_NIGHTS:
        return None
    xs = [(r["night"] - night).days for r in rows]
    ys = [r["bed_off"] for r in rows]
    mx, my = fmean(xs), fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx * 7, 1)


# ---------------------------------------------------------------- progress
def progress(goal: dict | None, nights: list[dict], night: date) -> dict:
    """night = tonight (sleep day). Looks at the 7 nights before night."""
    recent = window(nights, night)
    out = {"nights_7d": len(recent), "avg_bedtime_7d": fmt(avg(recent, "bed_off")),
           "avg_wake_7d": fmt(avg(recent, "wake_off")), "trend_min_per_week": trend_per_week(nights, night),
           "on_target_nights_7d": None, "gap_min": None, "eta_date": None, "behind_plan_min": None}
    a = avg(recent, "bed_off")
    if goal is None:
        return out
    target = to_off(goal["target_bedtime"])
    plan = goal["plan_rows"]
    on = 0
    for x in recent:
        t = target_for(plan, nights, x["night"], goal["pace_min_per_step"])
        if t is not None and x["bed_off"] <= t + P.ON_TARGET_TOL_MIN:
            on += 1
    out["on_target_nights_7d"] = on
    if a is not None:
        gap = round(a - target)
        out["gap_min"] = max(gap, 0)
        st = stage_for(plan, night)
        if st is not None:
            out["behind_plan_min"] = round(a - st["bed_off"])
        trend = out["trend_min_per_week"]
        if gap <= 0:
            out["eta_date"] = night
        elif trend is not None and trend < 0:
            out["eta_date"] = night + timedelta(days=math.ceil(gap / -trend * 7))
    return out
