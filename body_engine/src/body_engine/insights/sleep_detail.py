"""Sleep details: sleep regularity index (SRI), sleep efficiency, sleep latency, number of night wake-ups. Pure functions + database reads.

SRI (Phillips 2017, Sci Rep 7:3216; the same definition used by Windred 2024, Sleep 47:zsad253):
the probability P that the sleep / wake state is the same at two moments 24 hours apart, SRI = 200 × P − 100 (−100 is completely random/reversed, 100 is identical every day).
Here each "day" = [d−1 12:00, d 12:00) for wake date d, with the state taken per minute: inside an asleep stretch of a watch sleep segment (main sleep + naps) = asleep;
only pairs where both consecutive days have a watch main sleep are compared (a day without the watch looks like awake all day and cannot be counted).
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

DAY_MIN = 1440
MIN_PAIRS = 4          # at least 4 pairs of adjacent days for an SRI


def _utc(t: datetime) -> datetime:
    """The same instant in UTC. Python subtracts and compares two aware datetimes that share one tzinfo by wall-clock time, so
    two datetimes in the user's own zone (how a database session may return them) are an hour off across a DST change;
    in UTC every difference is the exact elapsed time (what the phone's JS does with epoch microseconds)."""
    return t.astimezone(timezone.utc)


def _day_block(d: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[d−1 12:00, d 12:00) local, as UTC instants (so it is 23 or 25 hours long on the day of a DST change)."""
    return (_utc(datetime.combine(d - timedelta(days=1), time(12), tz)), _utc(datetime.combine(d, time(12), tz)))


def asleep_minutes(intervals: list[tuple[datetime, datetime]], d: date, tz: ZoneInfo) -> bytearray:
    """Whether each minute of the 24-hour block for wake date d is asleep (1 = asleep)."""
    lo, hi = _day_block(d, tz)
    out = bytearray(DAY_MIN)
    for a, b in intervals:
        a, b = _utc(a), _utc(b)
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
    return watch_sessions(conn.execute(
        """SELECT wake_date, start_at, end_at, asleep_min, awake_min, deep_min, rem_min, core_min, is_main, device
           FROM sleep_sessions WHERE user_id = %s AND wake_date BETWEEN %s AND %s ORDER BY start_at""",
        (user_id, start - timedelta(days=1), end)).fetchall())


def watch_sessions(rows: list[dict]) -> tuple[dict, list]:
    """The pure part of load_intervals: rows (by start) -> (wake date -> longest watch session, all watch intervals)."""
    watch = [r for r in rows if (r["device"] and "watch" in r["device"].lower())
             or (r["core_min"] or 0) + (r["deep_min"] or 0) + (r["rem_min"] or 0) > 0]
    mains: dict[date, dict] = {}
    for r in watch:
        if r["wake_date"] not in mains or r["asleep_min"] > mains[r["wake_date"]]["asleep_min"]:
            mains[r["wake_date"]] = r
    return mains, [(_utc(r["start_at"]), _utc(r["end_at"])) for r in watch]


def sri_for(conn, user_id: int, tz: ZoneInfo, start: date, end: date) -> dict:
    return sri_from(*load_intervals(conn, user_id, start, end), tz, start, end)


def sri_from(mains: dict, intervals: list, tz: ZoneInfo, start: date, end: date) -> dict:
    """SRI of [start, end] from watch_sessions' output (sessions may reach further back: only these wake dates count)."""
    days = {d: asleep_minutes(intervals, d, tz) for d in mains if start <= d <= end}
    value, pairs = sri(days)
    return {"sri": value, "pairs": pairs, "nights": len(days), "start": start, "end": end}


def consistency_pct(sri_value: float | None) -> int | None:
    """The "sleep regularity %" for the front end: SRI clamped to 0–100."""
    return None if sri_value is None else int(round(max(0.0, min(100.0, sri_value))))


def nights(conn, user_id: int, tz: ZoneInfo, start: date, end: date) -> list[dict]:
    """Per-night details (from daily_scores.details.sleep, computed by the scoring engine)."""
    return nights_from(conn.execute("""SELECT date, sleep_score, sleep_actual_min, details->'sleep' AS s FROM daily_scores
                                       WHERE user_id = %s AND date BETWEEN %s AND %s AND details ? 'sleep' ORDER BY date""",
                                    (user_id, start, end)).fetchall())


def nights_from(rows: list[dict]) -> list[dict]:
    """The pure part of nights: rows {date, sleep_score, s = details.sleep} of the days whose details have a sleep block."""
    out = []
    for r in rows:
        s = r["s"] or {}
        out.append({"date": r["date"], "sleep_score": r["sleep_score"], "bedtime": s.get("bedtime"),
                    "wake_time": s.get("wake_time"), "asleep_min": s.get("asleep_min"), "nap_min": s.get("nap_min"),
                    "efficiency": s.get("efficiency"), "latency_min": s.get("latency_min"),
                    "waso_min": s.get("waso_min"), "awakenings": s.get("awakenings"),
                    "deep_min": s.get("deep_min"), "rem_min": s.get("rem_min")})
    return out


NOTES = ("规律性 = 睡眠规律性指数 SRI（相隔 24 小时睡 / 醒状态相同的比例，−100～100，这里截到 0–100）；"
         "入睡潜伏期只在睡着前有手表记的「清醒」片段时才有；效率 = 睡着 /（潜伏期开始 → 醒来）")


def details(sessions: tuple[dict, list], nights_list: list[dict], tz: ZoneInfo, start: date, end: date) -> dict:
    """GET /sleep/details from watch_sessions' output (covering at least the 30 days before `end`) and nights_from's rows;
    no database. The phone runs it as JS `OBS.series.sleep_detail.details` (M3)."""
    mains, intervals = sessions
    r7 = sri_from(mains, intervals, tz, end - timedelta(days=6), end)
    r30 = sri_from(mains, intervals, tz, end - timedelta(days=29), end)
    return {"start": start, "end": end,
            "consistency_pct_7d": consistency_pct(r7["sri"]), "sri_7d": r7["sri"], "sri_7d_pairs": r7["pairs"],
            "consistency_pct_30d": consistency_pct(r30["sri"]), "sri_30d": r30["sri"],
            "sri_30d_pairs": r30["pairs"], "nights": nights_list, "notes": NOTES}
