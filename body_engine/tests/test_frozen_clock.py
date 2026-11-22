"""The frozen-time fixture itself (tests/clock.py, tests/conftest.py `frozen_clock` / `moment`): what it freezes, what it does not, and
that it leaves nothing behind. No database.

The entry points to the wall clock in body_engine/src (counted 2026-11-22): `datetime.now(<tz>)` ~500 call sites in ~120 modules
(all through `from datetime import datetime`, so a patch of one module's name would miss almost all of them), `date.today()` 4,
`time.time()` / `monotonic()` ~56 (durations and TTLs), the per-module helpers on top of those (`queries._now`, `rules.context._today`,
`goals.nutrition._today`, ...) and, outside Python, SQL `now()` / `CURRENT_DATE` (not covered, see tests/clock.py).
"""
import os
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import clock
from clock import (DST_END_2355, DST_START_0005, DST_START_2355, LA_SUN_2355, MON_0005, MOMENTS, SUN_2355, SYDNEY, WED_0005, Moment)

from body_engine import queries
from body_engine.rules import context as rules_context

UTC = timezone.utc


# ---------------------------------------------------------------- the canonical instants are what their names say
# Literal expectations (not computed from the code under test): (UTC date, Sydney date, Los Angeles date, Shanghai date)
DAYS = {
    "wed-0005": ("2026-11-24", "2026-11-25", "2026-11-24", "2026-11-24"),             # Sydney is already on the next day
    "sun-2355": ("2026-11-22", "2026-11-22", "2026-11-22", "2026-11-22"),
    "mon-0005": ("2026-11-22", "2026-11-23", "2026-11-22", "2026-11-22"),             # the week turned in Sydney, not yet on the server
    "month-end-2355": ("2026-11-30", "2026-11-30", "2026-11-30", "2026-11-30"),
    "month-start-0005": ("2026-11-30", "2026-12-01", "2026-11-30", "2026-11-30"),       # December in Sydney, still November elsewhere
    "dst-start-0005": ("2026-10-03", "2026-10-04", "2026-10-03", "2026-10-03"),
    "dst-start-2355": ("2026-10-04", "2026-10-04", "2026-10-04", "2026-10-04"),
    "dst-start-next-0005": ("2026-10-04", "2026-10-05", "2026-10-04", "2026-10-04"),
    "dst-end-0005": ("2027-04-03", "2027-04-04", "2027-04-03", "2027-04-03"),
    "dst-end-0230-second": ("2027-04-03", "2027-04-04", "2027-04-03", "2027-04-04"),
    "dst-end-2355": ("2027-04-04", "2027-04-04", "2027-04-04", "2027-04-04"),
    "la-sun-2355": ("2026-11-23", "2026-11-23", "2026-11-22", "2026-11-23"),          # the server is a day AHEAD of Los Angeles
    "la-mon-0005": ("2026-11-23", "2026-11-23", "2026-11-23", "2026-11-23"),
}


@pytest.mark.parametrize("name", sorted(DAYS))
def test_moments_fall_on_the_days_they_claim(name):
    m = next(m for m in MOMENTS if m.name == name)
    got = (m.when.astimezone(UTC).date(), m.day_in("Australia/Sydney"), m.day_in("America/Los_Angeles"), m.day_in("Asia/Shanghai"))
    assert tuple(d.isoformat() for d in got) == DAYS[name]


def test_the_daylight_saving_days_are_23_and_25_hours_long():
    def hours(y, m, d):
        a, b = clock.local(SYDNEY, y, m, d), clock.local(SYDNEY, y, m, d) + timedelta(days=1)
        return (b.astimezone(UTC) - a.astimezone(UTC)).total_seconds() / 3600
    assert hours(2026, 10, 4) == 23 and hours(2027, 4, 4) == 25 and hours(2026, 11, 22) == 24
    la = clock.LOS_ANGELES
    assert (clock.local(la, 2027, 3, 15).astimezone(UTC) - clock.local(la, 2027, 3, 14).astimezone(UTC)).total_seconds() / 3600 == 23
    assert (clock.local(la, 2026, 11, 2).astimezone(UTC) - clock.local(la, 2026, 11, 1).astimezone(UTC)).total_seconds() / 3600 == 25


def test_the_repeated_hour_has_two_distinct_instants():
    first, second = clock.DST_END_0230_FIRST, clock.DST_END_0230_SECOND
    assert second.when.astimezone(UTC) - first.when.astimezone(UTC) == timedelta(hours=1)
    assert first.when.utcoffset() == timedelta(hours=11) and second.when.utcoffset() == timedelta(hours=10)


# ---------------------------------------------------------------- what is frozen
def test_every_wall_clock_entry_point_reads_the_frozen_instant(frozen_clock):
    frozen_clock(MON_0005)                                   # Sydney Mon 2026-11-23 00:05 = UTC Sun 2026-11-22 13:05
    want = MON_0005.when
    assert datetime.now(UTC) == want
    assert datetime.now(SYDNEY) == want and datetime.now(SYDNEY).date() == date(2026, 11, 23)
    assert datetime.now(ZoneInfo("America/Los_Angeles")).date() == date(2026, 11, 22)
    assert datetime.now().astimezone() == want               # naive now(): the process zone, UTC here
    assert datetime.now() == datetime(2026, 11, 22, 13, 5)   # ... so the server's wall clock still says Sunday
    assert date.today() == date(2026, 11, 22)                # the server's date: Sunday, one day behind the Sydney user
    assert time.time() == want.timestamp()
    assert time.localtime().tm_mday == 22 and time.strftime("%Y-%m-%d %H:%M") == "2026-11-22 13:05"


def test_already_imported_names_and_helpers_are_frozen_too(frozen_clock):
    """The code under test imported `datetime` / `date` long before this test ran: a patch of the module attribute would not reach it."""
    frozen_clock(WED_0005)
    assert queries._now() == WED_0005.when
    assert rules_context._today(SYDNEY) == date(2026, 11, 25)
    assert rules_context._today(ZoneInfo("America/Los_Angeles")) == date(2026, 11, 24)


def test_the_clock_stands_still_but_can_be_moved(frozen_clock):
    clock_ = frozen_clock(SUN_2355)
    a = datetime.now(UTC)
    time.sleep(0.02)
    assert datetime.now(UTC) == a == SUN_2355.when                      # does not tick
    assert clock_.advance(minutes=10).now == MON_0005.when              # Sunday 23:55 + 10 min = Monday 00:05 in Sydney
    assert datetime.now(SYDNEY).date() == date(2026, 11, 23)
    clock_.move_to(DST_END_2355).advance(hours=1)
    assert datetime.now(SYDNEY) == clock.local(SYDNEY, 2027, 4, 5, 0, 55)   # 23:55 + 1 h = 00:55 on the next day
    clock_.move_to(1_800_000_000)                                       # epoch seconds are accepted too
    assert time.time() == 1_800_000_000


def test_a_naive_time_is_refused(frozen_clock):
    with pytest.raises(ValueError, match="needs a time zone"):
        frozen_clock(datetime(2026, 11, 22, 23, 55))


def test_only_the_wall_clock_is_frozen_not_durations(frozen_clock):
    frozen_clock(MON_0005)
    a = time.monotonic()
    time.sleep(0.02)
    assert time.monotonic() > a      # TTLs (briefing.CACHE_TTL_S, the AI quota windows ...) keep running in real time


# ---------------------------------------------------------------- the process zone is the fixture's, not the user's
def test_the_server_zone_defaults_to_utc_whatever_ci_sets(frozen_clock):
    frozen_clock(MON_0005)
    assert os.environ["TZ"] == "UTC"
    assert date.today() == date(2026, 11, 22)


def test_the_server_zone_can_be_the_users_own(frozen_clock):
    """The old setup (CI runs with TZ=Australia/Sydney): the same naive call gives the Sydney user the right day by accident."""
    frozen_clock(MON_0005, server_tz="Australia/Sydney")
    assert date.today() == date(2026, 11, 23) and datetime.now() == datetime(2026, 11, 23, 0, 5)


# ---------------------------------------------------------------- nothing is left behind
def test_the_fixture_puts_everything_back():
    before_tz = os.environ.get("TZ")
    travel = clock._Travel()
    travel(LA_SUN_2355, server_tz="Asia/Tokyo")
    travel(DST_START_2355, server_tz="UTC")                    # nested, like two fixtures in one test
    assert datetime.now(UTC) == DST_START_2355.when
    travel.stop()
    assert os.environ.get("TZ") == before_tz
    assert datetime.now(UTC) != DST_START_2355.when and time.time() != LA_SUN_2355.when.timestamp()
    assert datetime.now(UTC).year >= 2026 and time.time() > 1_790_000_000   # the real clock again, and it moves on
    a = time.time()
    time.sleep(0.02)
    assert time.time() > a


def test_nothing_leaks_between_tests_after_a_failure(frozen_clock):
    """An exception inside a frozen test must still unfreeze: the next test sees the real clock (checked by the one after this)."""
    frozen_clock(DST_START_0005)
    with pytest.raises(RuntimeError):
        raise RuntimeError("a test that fails while the clock is frozen")


def test_the_real_clock_is_back_in_the_next_test():
    now = datetime.now(UTC)
    assert now != DST_START_0005.when and abs((now - datetime.fromtimestamp(time.time(), UTC)).total_seconds()) < 1
    assert now.year >= 2026 and now > datetime(2026, 11, 22, tzinfo=UTC)


def test_a_test_that_does_not_use_the_clock_is_not_marked(request):
    assert request.node.get_closest_marker("time_sensitive") is None


def test_moment_names_are_unique():
    names = [m.name for m in MOMENTS]
    assert len(names) == len(set(names))


def test_moment_fixture_runs_for_every_instant(moment, request):
    assert request.node.get_closest_marker("time_sensitive") is not None   # added by conftest for tests using `moment`
    assert isinstance(moment, Moment) and moment.when.tzinfo is not None
