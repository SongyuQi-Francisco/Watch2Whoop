"""Sleep regularity (SRI) across a DST change: every path computes on UTC instants.

Python compares and subtracts two aware datetimes that share one tzinfo by wall-clock time. Rows read from Postgres can come
back in the user's own zone (the same ZoneInfo as the day block), so a night that crosses a DST change was an hour off.
Australia/Sydney: DST starts on the first Sunday of October (2026-10-04, 02:00 -> 03:00) and ends on the first Sunday of
April (2026-04-05, 03:00 -> 02:00). No database: pure functions only.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from body_engine.insights import reports, sleep_detail

TZ = ZoneInfo("Australia/Sydney")
UTC = timezone.utc
CHANGES = [date(2026, 10, 4), date(2026, 4, 5)]
IDS = ["dst-start-oct", "dst-end-apr"]


def _local(d: date, minutes: int) -> datetime:
    """Wall time `minutes` after local midnight of d, in the user's zone (a datetime carrying the user's own tzinfo)."""
    return datetime.combine(d, time(0), TZ) + timedelta(minutes=minutes)


def _rows(first_wake: date, nights: int, shift: dict | None = None) -> list[dict]:
    """One watch main sleep per night, in bed at 23:00 and up at 07:00; shift = {wake date: minutes later}."""
    out = []
    for k in range(nights):
        w = first_wake + timedelta(days=k)
        extra = (shift or {}).get(w, 0)
        out.append({"wake_date": w, "start_at": _local(w, -60 + extra), "end_at": _local(w, 420 + extra),
                    "asleep_min": 420.0, "awake_min": 0.0, "deep_min": 60.0, "rem_min": 60.0, "core_min": 300.0,
                    "is_main": True, "device": "Apple Watch"})
    return out


def _utc_rows(rows: list[dict]) -> list[dict]:
    return [{**r, "start_at": r["start_at"].astimezone(UTC), "end_at": r["end_at"].astimezone(UTC)} for r in rows]


def _reference(rows: list[dict], start: date, end: date) -> tuple[float | None, int]:
    """SRI from epoch seconds, written independently of sleep_detail (the block of wake date d starts at local noon of d - 1)."""
    spans = [(r["start_at"].timestamp(), r["end_at"].timestamp()) for r in rows]
    days = {}
    for r in rows:
        d = r["wake_date"]
        if not start <= d <= end:
            continue
        lo = datetime.combine(d - timedelta(days=1), time(12), TZ).timestamp()
        hi = datetime.combine(d, time(12), TZ).timestamp()
        flags = [0] * 1440
        for a, b in spans:
            if b <= lo or a >= hi:
                continue
            for m in range(max(0, int((a - lo) // 60)), min(1440, int((b - lo) // 60))):
                flags[m] = 1
        days[d] = flags
    same = total = pairs = 0
    for d, cur in days.items():
        nxt = days.get(d + timedelta(days=1))
        if nxt is None:
            continue
        pairs += 1
        same += sum(x == y for x, y in zip(cur, nxt))
        total += 1440
    return (None, pairs) if pairs < sleep_detail.MIN_PAIRS else (round(200 * same / total - 100, 1), pairs)


def _both(rows: list[dict], start: date, end: date) -> tuple[dict, dict]:
    """sri_from on the rows as the database returns them (user's zone) and as UTC: they must agree."""
    a = sleep_detail.sri_from(*sleep_detail.watch_sessions(rows), TZ, start, end)
    b = sleep_detail.sri_from(*sleep_detail.watch_sessions(_utc_rows(rows)), TZ, start, end)
    return a, b


@pytest.mark.parametrize("change", CHANGES, ids=IDS)
def test_regular_sleeper_matches_instant_reference_across_dst(change):
    """Same wall-clock bed and wake time every night. One hour of the night moves inside the 24-hour block on the day of the
    change, so the value is not exactly 100, but it is the exact-elapsed-time value in both representations."""
    start, end = change - timedelta(days=8), change + timedelta(days=8)
    rows = _rows(start - timedelta(days=1), 18)
    a, b = _both(rows, start, end)
    assert a["pairs"] >= 10
    assert a["sri"] == b["sri"]
    assert (a["sri"], a["pairs"]) == _reference(rows, start, end)


@pytest.mark.parametrize("change", CHANGES, ids=IDS)
def test_irregular_sleeper_matches_instant_reference(change):
    start, end = change - timedelta(days=6), change + timedelta(days=6)
    shift = {change + timedelta(days=k): m for k, m in ((-3, 90), (-1, 45), (0, 150), (1, -30), (3, 60), (5, 120))}
    rows = _rows(start - timedelta(days=1), 14, shift=shift)
    a, b = _both(rows, start, end)
    assert a["sri"] == b["sri"] == _reference(rows, start, end)[0]
    assert a["sri"] is not None and a["sri"] < 100


@pytest.mark.parametrize("change", CHANGES, ids=IDS)
def test_asleep_minutes_of_the_night_of_the_change(change):
    """Bed 23:00 the evening before, up 07:00 on `change`: the asleep minutes are the elapsed time, not the wall-clock 8 h."""
    row = _rows(change, 1)[0]
    got = sleep_detail.asleep_minutes([(row["start_at"], row["end_at"])], change, TZ)
    lo = datetime.combine(change - timedelta(days=1), time(12), TZ).astimezone(UTC)
    a, b = row["start_at"].astimezone(UTC), row["end_at"].astimezone(UTC)
    want = bytearray(1 if a <= lo + timedelta(minutes=m) < b else 0 for m in range(1440))
    assert got == want
    assert sum(got) == int((b - a).total_seconds() // 60)
    assert sum(got) != 480


def test_reports_consistency_and_details_use_the_same_instants():
    """The report path (reports.consistency) and GET /sleep/details (details) on rows in the user's zone equal the UTC result."""
    change = CHANGES[0]
    shift = {change: 120, change + timedelta(days=2): 60, change - timedelta(days=2): 30}
    rows = _rows(change - timedelta(days=40), 52, shift=shift)
    a, b = change - timedelta(days=6), change + timedelta(days=6)
    assert reports.consistency(rows, TZ, a, b) == reports.consistency(_utc_rows(rows), TZ, a, b)
    end = change + timedelta(days=3)
    d1 = sleep_detail.details(sleep_detail.watch_sessions(rows), [], TZ, end - timedelta(days=13), end)
    d2 = sleep_detail.details(sleep_detail.watch_sessions(_utc_rows(rows)), [], TZ, end - timedelta(days=13), end)
    assert d1 == d2 and d1["sri_7d"] is not None and d1["sri_30d"] is not None
