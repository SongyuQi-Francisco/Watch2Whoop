"""Frozen-time helpers for tests that depend on "what day is it for this user" (retrospective 2026-11, section 10 F).

Why: the server's UTC date and the user's local date differ for part of every day; the week turns over at the user's
midnight, not the server's; a daylight-saving day has 23 or 25 hours. Every one of those cost a rework (issues #88, #268,
#296, #383, #411, #90, #368, #372, #397, #414, #423, #424, #293) because no test ever stood on the boundary.

Use the `frozen_clock` fixture (tests/conftest.py); one line:

    def test_something(frozen_clock):
        clock = frozen_clock(MON_0005)              # "now" is Monday 00:05 in Sydney; the process runs in UTC, like the server
        ...
        clock.advance(minutes=20)                   # later in the same test (cross-midnight replays)
        clock.move_to(SUN_2355)

or `moment` (a parametrized fixture over every canonical instant below) when a test must hold at all of them.

What is frozen: everything that reads the wall clock through Python's `time_machine` hooks, i.e. `datetime.now(...)` /
`datetime.utcnow()` (all ~500 call sites, however the module imported `datetime`), `date.today()`, `time.time()` /
`time_ns()`, `time.localtime()` / `strftime()`, and the module-level `_now()` helpers built on them. The clock does not tick.

What is NOT frozen (write the test data so it does not matter):
- the database: SQL `now()`, `CURRENT_DATE`, `DEFAULT now()` columns (`created_at`, `imported_at`, token `expires_at` ...) are
  read by Postgres, on the real time. A test that needs a stored timestamp passes it explicitly. Auth checks `expires_at > now()`
  in SQL, so a frozen date months in the past or future does not log anybody out;
- `time.monotonic()` / `perf_counter()` (durations, cache TTLs such as briefing.CACHE_TTL_S): they keep running in real time, which
  is what a TTL wants. A test that must expire a TTL patches the module's TTL, not the clock;
- other processes (an `asr` container, Postgres itself).

The process time zone: `time_machine.travel(<aware datetime>)` would switch the whole process to that datetime's zone, which hides
exactly the bug "the code used the server's date, not the user's". So this module travels by epoch seconds (the zone is
untouched) and sets the process zone itself: `server_tz` (default "UTC", like a typical server). CI runs the whole job with
TZ=Australia/Sydney (.github/workflows/ci.yml, some older tests need it), which would make every naive `datetime.now()` /
`date.today()` bug invisible for a Sydney user; the fixture puts it back afterwards, so nothing leaks into other test files.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import time_machine

UTC = timezone.utc
SYDNEY = ZoneInfo("Australia/Sydney")
LOS_ANGELES = ZoneInfo("America/Los_Angeles")   # UTC−8 in winter, UTC−7 in summer: its calendar day LAGS the server's, Sydney's leads it
SHANGHAI = ZoneInfo("Asia/Shanghai")            # UTC+8, no daylight saving

# the zones every day-boundary test runs a user in: one ahead of UTC with daylight saving, one behind UTC with it,
# one ahead without it. Not hard-coded to Sydney anywhere.
USER_ZONES = ("Australia/Sydney", "America/Los_Angeles", "Asia/Shanghai")


def local(zone: str | ZoneInfo, year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0,
          fold: int = 0) -> datetime:
    """An aware wall-clock time in `zone` (fold=1 picks the second occurrence of a repeated hour at the end of daylight saving)."""
    z = ZoneInfo(zone) if isinstance(zone, str) else zone
    return datetime(year, month, day, hour, minute, second, tzinfo=z, fold=fold)


@dataclass(frozen=True)
class Moment:
    """One canonical instant. `when` is aware, on the wall clock of the zone the name starts with (Sydney unless the name begins
    with `la-`); what day it is for anyone else follows from it (`day_in`)."""
    name: str
    when: datetime
    why: str

    def __str__(self) -> str:   # shows up in pytest ids
        return self.name

    def day_in(self, zone: str | ZoneInfo):
        return self.when.astimezone(ZoneInfo(zone) if isinstance(zone, str) else zone).date()


# ---- Sydney 00:05: after the user's midnight, the server (UTC) is still on the previous day (Sydney is UTC+11 / +10) ----------------
WED_0005 = Moment("wed-0005", local(SYDNEY, 2026, 11, 25, 0, 5), "Sydney 11-25 00:05 = UTC 11-24 13:05")
# ---- the week turns over: Sunday 23:55 -> Monday 00:05 (Monday-based weeks) -------------------------------------------------------
SUN_2355 = Moment("sun-2355", local(SYDNEY, 2026, 11, 22, 23, 55), "last 5 minutes of the Sydney week (UTC: Sunday 12:55)")
MON_0005 = Moment("mon-0005", local(SYDNEY, 2026, 11, 23, 0, 5), "first 5 minutes of the next week (UTC: still Sunday 13:05)")
# ---- the month turns over (monthly reports, the training calendar's default month) ------------------------------------------------
MONTH_END_2355 = Moment("month-end-2355", local(SYDNEY, 2026, 11, 30, 23, 55), "last minutes of November in Sydney (UTC: Nov 30 12:55)")
MONTH_START_0005 = Moment("month-start-0005", local(SYDNEY, 2026, 12, 1, 0, 5), "December in Sydney, still November on the server (UTC: Nov 30 13:05)")
# ---- Sydney daylight saving starts Sun 2026-10-04: 02:00 AEST jumps to 03:00 AEDT, a 23-hour day ------------------------------
DST_START_0005 = Moment("dst-start-0005", local(SYDNEY, 2026, 10, 4, 0, 5), "AEST +10, 2 h before the gap")
DST_START_0330 = Moment("dst-start-0330", local(SYDNEY, 2026, 10, 4, 3, 30), "AEDT +11, 30 minutes after the gap (2.5 real hours after 00:05)")
DST_START_2355 = Moment("dst-start-2355", local(SYDNEY, 2026, 10, 4, 23, 55), "last minutes of the 23-hour day (UTC: same day 12:55)")
DST_START_NEXT = Moment("dst-start-next-0005", local(SYDNEY, 2026, 10, 5, 0, 5), "the day after (UTC: still the 4th)")
# ---- Sydney daylight saving ends Sun 2027-04-04: 03:00 AEDT falls back to 02:00 AEST, a 25-hour day ------------------------------
DST_END_0005 = Moment("dst-end-0005", local(SYDNEY, 2027, 4, 4, 0, 5), "AEDT +11, start of the 25-hour day (UTC: Apr 3 13:05)")
DST_END_0230_FIRST = Moment("dst-end-0230-first", local(SYDNEY, 2027, 4, 4, 2, 30, fold=0), "02:30 AEDT, the first time it happens")
DST_END_0230_SECOND = Moment("dst-end-0230-second", local(SYDNEY, 2027, 4, 4, 2, 30, fold=1), "02:30 AEST, the repeated hour")
DST_END_2355 = Moment("dst-end-2355", local(SYDNEY, 2027, 4, 4, 23, 55), "last minutes of the 25-hour day (UTC: Apr 4 13:55)")
DST_END_NEXT = Moment("dst-end-next-0005", local(SYDNEY, 2027, 4, 5, 0, 5), "the day after (UTC: Apr 4 14:05)")

# ---- the same boundaries for a user BEHIND UTC (Los Angeles, UTC−8 in winter): the server's date is AHEAD of theirs for 8 hours --------
LA_SUN_2355 = Moment("la-sun-2355", local(LOS_ANGELES, 2026, 11, 22, 23, 55), "UTC is already Monday 07:55; Sydney Monday 18:55")
LA_MON_0005 = Moment("la-mon-0005", local(LOS_ANGELES, 2026, 11, 23, 0, 5), "UTC Monday 08:05: the same day as the server, a day behind Sydney's evening")
LA_DST_END_2355 = Moment("la-dst-end-2355", local(LOS_ANGELES, 2026, 11, 1, 23, 55), "last minutes of the 25-hour day (daylight saving ended 11-01 02:00)")
LA_DST_START_0005 = Moment("la-dst-start-0005", local(LOS_ANGELES, 2027, 3, 14, 0, 5), "start of the 23-hour day (daylight saving starts 03-14 02:00)")

MOMENTS: tuple[Moment, ...] = (
    WED_0005, SUN_2355, MON_0005, MONTH_END_2355, MONTH_START_0005,
    DST_START_0005, DST_START_0330, DST_START_2355, DST_START_NEXT,
    DST_END_0005, DST_END_0230_FIRST, DST_END_0230_SECOND, DST_END_2355, DST_END_NEXT,
    LA_SUN_2355, LA_MON_0005, LA_DST_END_2355, LA_DST_START_0005,
)


class Clock:
    """The running frozen clock of one test. Not constructed directly: use the `frozen_clock` fixture."""

    def __init__(self, traveller, server_tz: str):
        self._traveller = traveller
        self.server_tz = server_tz

    @staticmethod
    def _epoch(when: datetime | Moment | float | int) -> float | int:
        if isinstance(when, Moment):
            when = when.when
        if isinstance(when, datetime):
            if when.tzinfo is None:
                raise ValueError("a frozen time needs a time zone (use clock.local(...) or a Moment): "
                                 "a naive time would mean the process zone, which is what these tests move around")
            return when.timestamp()
        return when

    @property
    def now(self) -> datetime:
        return datetime.now(UTC)

    def move_to(self, when: datetime | Moment | float | int) -> "Clock":
        self._traveller.move_to(self._epoch(when))
        return self

    def advance(self, **delta) -> "Clock":
        """advance(minutes=20, hours=1 ...) (a timedelta's keyword arguments)."""
        self._traveller.shift(timedelta(**delta))
        return self


class _Travel:
    """What the `frozen_clock` fixture hands out: call it to start a clock; `stop()` is called by the fixture's teardown."""

    def __init__(self):
        self._stack: list[tuple[time_machine.travel, str | None]] = []
        self._tz_before: tuple[bool, str | None] | None = None

    def __call__(self, when: datetime | Moment | float | int, *, server_tz: str = "UTC") -> Clock:
        epoch = Clock._epoch(when)
        if self._tz_before is None:
            self._tz_before = ("TZ" in os.environ, os.environ.get("TZ"))
        os.environ["TZ"] = server_tz       # set the process zone ourselves: travelling by epoch leaves it alone
        time.tzset()
        trav = time_machine.travel(epoch, tick=False)
        self._stack.append((trav, server_tz))
        return Clock(trav.start(), server_tz)

    def stop(self) -> None:
        while self._stack:
            self._stack.pop()[0].stop()
        if self._tz_before is not None:
            had, value = self._tz_before
            if had:
                os.environ["TZ"] = value or ""
            else:
                os.environ.pop("TZ", None)
            time.tzset()
            self._tz_before = None
