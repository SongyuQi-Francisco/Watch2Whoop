"""Sleep details: sleep regularity index (SRI), sleep efficiency, sleep latency, number of night wake-ups. Pure functions + database reads.

SRI (Phillips 2017, Sci Rep 7:3216; the same definition used by Windred 2024, Sleep 47:zsad253):
the probability P that the sleep / wake state is the same at two moments 24 hours apart, SRI = 200 × P − 100 (−100 is completely random/reversed, 100 is identical every day).
Here each "day" = [d−1 12:00, d 12:00) for wake date d, with the state taken per minute: inside an asleep stretch of a watch sleep segment (main sleep + naps) = asleep;
only pairs where both consecutive days have a watch main sleep are compared (a day without the watch looks like awake all day and cannot be counted).
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

DAY_MIN = 1440
MIN_PAIRS = 4          # at least 4 pairs of adjacent days for an SRI


def _day_block(d: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(d - timedelta(days=1), time(12), tz)
    return start, start + timedelta(days=1)


def asleep_minutes(intervals: list[tuple[datetime, datetime]], d: date, tz: ZoneInfo) -> bytearray:
    """Whether each minute of the 24-hour block for wake date d is asleep (1 = asleep)."""
    lo, hi = _day_block(d, tz)
    out = bytearray(DAY_MIN)
    for a, b in intervals:
        if b <= lo or a >= hi:
            continue
        i0 = max(0, int((a - lo).total_seconds() // 60))
        i1 = min(DAY_MIN, int((b - lo).total_seconds() // 60))
        out[i0:i1] = b"\x01" * (i1 - i0)
    return out


def sri(days: dict[date, bytearray]) -> tuple[float | None, int]:
    """days: wake date -> per-minute state. Only pairs where both adjacent days exist are used. Returns (SRI, number of pairs)."""
    same = total = pairs = 0
    for d, cur in days.items():
        nxt = days.get(d + timedelta(days=1))
        if nxt is None:
            continue
        pairs += 1
        same += sum(1 for x, y in zip(cur, nxt) if x == y)
        total += DAY_MIN
    if pairs < MIN_PAIRS:
        return None, pairs
    return round(200 * same / total - 100, 1), pairs


def load_intervals(conn: psycopg.Connection, user_id: int, start: date, end: date) -> tuple[dict, dict]:
    """Read watch sleep segments (only those with stage data count, consistent with scoring): returns (wake date -> main-sleep row, all sleep intervals).
    Asleep interval = the segment's start and end (awake time inside a segment is ignored in SRI: Apple gives only summary minutes, and this is the most stable when there is no detail)."""
    rows = conn.execute(
        """SELECT wake_date, start_at, end_at, asleep_min, awake_min, deep_min, rem_min, core_min, is_main, device
           FROM sleep_sessions WHERE user_id = %s AND wake_date BETWEEN %s AND %s ORDER BY start_at""",
        (user_id, start - timedelta(days=1), end)).fetchall()
    watch = [r for r in rows if (r["device"] and "watch" in r["device"].lower())
             or (r["core_min"] or 0) + (r["deep_min"] or 0) + (r["rem_min"] or 0) > 0]
    mains: dict[date, dict] = {}
    for r in watch:
        if r["wake_date"] not in mains or r["asleep_min"] > mains[r["wake_date"]]["asleep_min"]:
            mains[r["wake_date"]] = r
    return mains, [(r["start_at"], r["end_at"]) for r in watch]


def sri_for(conn, user_id: int, tz: ZoneInfo, start: date, end: date) -> dict:
    mains, intervals = load_intervals(conn, user_id, start, end)
    days = {d: asleep_minutes(intervals, d, tz) for d in mains if start <= d <= end}
    value, pairs = sri(days)
    return {"sri": value, "pairs": pairs, "nights": len(days), "start": start, "end": end}


def consistency_pct(sri_value: float | None) -> int | None:
    """The "sleep regularity %" for the front end: SRI clamped to 0–100."""
    return None if sri_value is None else int(round(max(0.0, min(100.0, sri_value))))


def nights(conn, user_id: int, tz: ZoneInfo, start: date, end: date) -> list[dict]:
    """Per-night details (from daily_scores.details.sleep, computed by the scoring engine)."""
    out = []
    for r in conn.execute("""SELECT date, sleep_score, sleep_actual_min, details->'sleep' AS s FROM daily_scores
                             WHERE user_id = %s AND date BETWEEN %s AND %s AND details ? 'sleep' ORDER BY date""",
                          (user_id, start, end)):
        s = r["s"] or {}
        out.append({"date": r["date"], "sleep_score": r["sleep_score"], "bedtime": s.get("bedtime"),
                    "wake_time": s.get("wake_time"), "asleep_min": s.get("asleep_min"), "nap_min": s.get("nap_min"),
                    "efficiency": s.get("efficiency"), "latency_min": s.get("latency_min"),
                    "waso_min": s.get("waso_min"), "awakenings": s.get("awakenings"),
                    "deep_min": s.get("deep_min"), "rem_min": s.get("rem_min")})
    return out
