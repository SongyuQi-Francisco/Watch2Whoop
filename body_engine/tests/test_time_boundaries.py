"""Which day is it for this user? — on the boundaries that cost a rework (retrospective 2026-11, section 10 F).

The server runs in UTC; one user may live in Sydney (UTC+10 / +11), another in Los Angeles (UTC−8 / −7) or Shanghai (UTC+8). For part of every day the server's date and the user's differ; the week and the month turn over at the user's midnight;
daylight-saving days have 23 or 25 hours. Each test freezes "now" with the `frozen_clock` / `moment` fixtures (tests/clock.py) at
the canonical instants and asks a path that assigns data to a day: it must answer with the USER's day, for three users in three
zones, so nothing is hard-coded to Sydney.

Own test database BODY_TEST_DB_NAME (default body_test_time_boundaries), recreated each run; skipped without Postgres.
Synthetic data only. SparkyFitness is not configured for these users (every path degrades to its Body Engine-only answer).
"""
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

import clock
from clock import MON_0005, MONTH_END_2355, MONTH_START_0005, SUN_2355, USER_ZONES, WED_0005

from body_engine import queries
from body_engine.assistant import briefing, diet_profile, scenes, sparky_write
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.goals import nutrition as goals_nutrition, store as goal_store
from body_engine.grocery import store as grocery_store
from body_engine.insights import views as insight_views
from body_engine.nutrition import constraints as diet_constraints
from body_engine.ownstore import migrate as ownstore_migrate
from body_engine.rules import context as rules_context
from body_engine.training import history as training_history, muscle_load, plans, session_log
from body_engine.ui import api as ui_api

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_time_boundaries")
SRC = Path(__file__).resolve().parents[1] / "src" / "body_engine"
ZONES = {"SY": "Australia/Sydney", "LA": "America/Los_Angeles", "SH": "Asia/Shanghai"}
assert tuple(ZONES.values()) == USER_ZONES


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture(scope="module")
def users(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {k: conn.execute("""INSERT INTO users (display_name, role, timezone, birth_date, consent_at, consent_version)
                                  VALUES (%s, 'member', %s, '1995-06-15', '2026-10-01', '2026-10-22.1') RETURNING id""",
                               (k, tz)).fetchone()["id"] for k, tz in ZONES.items()}
        conn.commit()
    return ids


@pytest.fixture(autouse=True)
def _no_briefing_cache_left_behind():
    """briefing._cache lives in the process, keyed by user id (1, 2, 3 ... in every test file's own database): empty before and after, also when a test fails."""
    briefing.clear()
    yield
    briefing.clear()


@pytest.fixture
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


def expected(moment, key: str) -> date:
    """What day it is for user `key` at `moment`, from the instant and the zone alone (the definition under test)."""
    return moment.when.astimezone(ZoneInfo(ZONES[key])).date()


# ---------------------------------------------------------------- the one shared function, and every copy of it
def test_local_today_is_the_users_day_not_the_servers(frozen_clock, moment, users, conn):
    frozen_clock(moment)
    server_day = moment.when.astimezone(clock.UTC).date()
    for key, uid in users.items():
        assert queries.local_today(conn, uid) == expected(moment, key), (key, moment.name)
    # the cases the server's own date gets wrong, written out (a regression to date.today() / UTC fails here by name)
    if moment is WED_0005:
        assert server_day == date(2026, 11, 24) and queries.local_today(conn, users["SY"]) == date(2026, 11, 25)
    if moment is MON_0005:
        assert server_day == date(2026, 11, 22) and queries.local_today(conn, users["SY"]) == date(2026, 11, 23)


# Every module keeps its own "today for this user" helper (twelve copies of the same three lines, 2026-11-22). They must agree with
# queries.local_today at every instant; the scan below fails when a thirteenth appears, so it gets listed here (or, better, deleted).
TODAY_HELPERS = {
    "queries.local_today": lambda c, u, tz: queries.local_today(c, u),
    "insights.views.today": lambda c, u, tz: insight_views.today(c, u),
    "grocery.store.today": lambda c, u, tz: grocery_store.today(c, u),
    "assistant.sparky_write.user_today": lambda c, u, tz: sparky_write.user_today(c, u),
    "assistant.scenes._today": lambda c, u, tz: scenes._today(c, u),
    "ui.api._today": lambda c, u, tz: ui_api._today(c, u, None),
    "goals.nutrition._today": lambda c, u, tz: goals_nutrition._today(c, u),
    "training.session_log._today": lambda c, u, tz: session_log._today(c, u),
    "training.plans._user_today": lambda c, u, tz: plans._user_today(c, u),
    "nutrition.constraints._today": lambda c, u, tz: diet_constraints._today(c, u),
    "ownstore.migrate.user_today": lambda c, u, tz: ownstore_migrate.user_today(c, u),
    "assistant.diet_profile._local_today": lambda c, u, tz: diet_profile._local_today(c, u),
    "rules.context._today": lambda c, u, tz: rules_context._today(tz),
    "rules.context.Ctx.now_local": lambda c, u, tz: rules_context.Ctx(user_id=u, day=date(2000, 1, 1), tz=tz).now_local().date(),
}


@pytest.mark.parametrize("helper", sorted(TODAY_HELPERS))
def test_every_per_module_today_helper_gives_the_users_day(helper, frozen_clock, moment, users, conn):
    frozen_clock(moment)
    for key, uid in users.items():
        got = TODAY_HELPERS[helper](conn, uid, ZoneInfo(ZONES[key]))
        assert got == expected(moment, key), (helper, key, moment.name)


# `def …today…` names that are not "today for this user": they take the day as an argument, count a rolling window, or are on purpose
# not the user's zone. Each with the reason, so nobody has to wonder.
NOT_USER_DAY = {
    ("grocery.sync.runner", "sydney_today"): "supermarket specials are a Sydney region fact (grocery params P.TZ), not the user's zone",
    ("friends.direct", "_sent_today"): "rolling 24 hours of the database clock, not a calendar day",
    ("friends.cards", "_trained_today"): "takes the day as an argument",
    ("assistant.memory_api", "_with_today"): "takes the date of the write it answers",
    ("web.views", "today"): "the Today page; takes `day`, defaulting through queries.local_today",
    ("web.views", "scores_today"): "takes the day", ("web.views", "scores_today_compute"): "takes the day",
    ("web.views", "sleep_today"): "takes the day", ("web.views", "sleep_today_compute"): "takes the day",
    ("web.views", "training_today"): "takes the day", ("web.views", "nutrition_today"): "takes the day",
    ("web.api", "today"): "route handler: calls web.views.today",
    ("ai.asr", "usage_today"): "quota since the user's local midnight: test_quotas_count_from_the_users_midnight",
    ("ai.fallback", "calls_today"): "quota since the user's local midnight: test_quotas_count_from_the_users_midnight",
    ("ai.web.__init__", "used_today"): "quota since the user's local midnight: test_quotas_count_from_the_users_midnight",
}


def test_no_unlisted_today_helper_exists():
    """A new `def …today…` is one more place that can answer with the server's date: list it in TODAY_HELPERS (it is then checked at every
    instant), explain it in NOT_USER_DAY, or use queries.local_today."""
    found = set()
    for path in SRC.rglob("*.py"):
        mod = ".".join(path.relative_to(SRC).with_suffix("").parts)
        for m in re.finditer(r"^\s*def (\w*today\w*)\(", path.read_text(encoding="utf-8"), re.M):
            found.add((mod, m.group(1)))
    listed = {tuple(k.rsplit(".", 1)) for k in TODAY_HELPERS if "today" in k.rsplit(".", 1)[1]}
    unlisted = found - listed - set(NOT_USER_DAY)
    assert not unlisted, f"day helpers neither in TODAY_HELPERS nor in NOT_USER_DAY: {sorted(unlisted)}"
    assert not (set(NOT_USER_DAY) - found), f"NOT_USER_DAY names that no longer exist: {sorted(set(NOT_USER_DAY) - found)}"


# ---------------------------------------------------------------- quotas: "since the user's local midnight"
def _midnight_rows(moment, key: str) -> list[datetime]:
    """Timestamps one second either side of the user's last midnight before `moment`, and of the one before that."""
    tz = ZoneInfo(ZONES[key])
    midnight = datetime.combine(moment.when.astimezone(tz).date(), datetime.min.time(), tz)
    return [midnight - timedelta(seconds=1), midnight, midnight + timedelta(seconds=1)]


def test_quotas_count_from_the_users_midnight(frozen_clock, moment, users, conn):
    """The per-day limits (voice 50 / day, the fallback AI's calls / day, web searches / day) start at the user's own midnight: a row one
    second before it belongs to yesterday, the one at it and after it to today. On a 23- or 25-hour day the window is that long, not 24 h."""
    from body_engine.ai import asr, fallback, web
    frozen_clock(moment)
    for key, uid in users.items():
        conn.execute("DELETE FROM asr_usage WHERE user_id = %s", (uid,))
        conn.execute("DELETE FROM ai_usage WHERE user_id = %s", (uid,))
        before, at, after = _midnight_rows(moment, key)
        for t in (before, at, after):
            conn.execute("INSERT INTO asr_usage (user_id, at, audio_ms, proc_ms, ok) VALUES (%s, %s, 1000, 100, true)", (uid, t))
            conn.execute("""INSERT INTO ai_usage (user_id, kind, model, purpose, at) VALUES (%s, %s, 'm', 'chat', %s)""",
                         (uid, fallback.KIND, t))
            conn.execute("""INSERT INTO ai_usage (user_id, kind, model, purpose, at) VALUES (%s, 'anthropic_api', 'm', 'web_search', %s)""",
                         (uid, t))
        assert asr.usage_today(conn, uid) == 2, (key, moment.name)
        assert fallback.calls_today(conn, uid) == 2, (key, moment.name)
        assert web.used_today(conn, uid, "web_search") == 2, (key, moment.name)


# ---------------------------------------------------------------- the opening briefing: its date, its clock line and its cache
def test_briefing_date_and_clock_line_follow_the_user(frozen_clock, moment, users, conn):
    frozen_clock(moment)
    for key, uid in users.items():
        out = briefing.get(conn, uid, uid)
        want = expected(moment, key)
        assert out["date"] == want and out["timezone"] == ZONES[key], (key, moment.name)
        local = moment.when.astimezone(ZoneInfo(ZONES[key]))
        assert out["now"] == f"{local:%Y-%m-%d %H:%M} 周{'一二三四五六日'[local.weekday()]}", (key, moment.name)
        briefing.invalidate(uid)


def test_briefing_cache_is_dropped_at_the_users_midnight(frozen_clock, users, conn):
    """The cache key holds the user's local date: at the turn of the day inside the 10-minute TTL it must miss, not serve yesterday's
    briefing under today's date (and the TTL is real time, which the frozen clock does not advance)."""
    uid = users["SY"]
    clock_ = frozen_clock(SUN_2355)                      # Sydney Sunday 23:55
    first = briefing.get(conn, uid, uid)
    assert first["date"] == date(2026, 11, 22) and "cached" not in first
    assert briefing.get(conn, uid, uid).get("cached") is True        # still the same local day: a hit
    clock_.advance(minutes=3)                                         # 23:58, same day
    assert briefing.get(conn, uid, uid).get("cached") is True
    clock_.advance(minutes=7)                                         # Monday 00:05: new local day, the TTL (600 s) has not run out
    after = briefing.get(conn, uid, uid)
    assert after["date"] == date(2026, 11, 23) and "cached" not in after
    assert after["now"].endswith("周一") and "00:05" in after["now"]


def test_briefing_cache_survives_the_servers_midnight_for_a_user_who_is_not_there(frozen_clock, users, conn):
    """UTC midnight is 11:00 / 10:00 in the morning in Sydney and 16:00 / 17:00 the day before in Los Angeles: neither is a new day for them."""
    clock_ = frozen_clock(clock.local("UTC", 2026, 11, 22, 23, 55))
    for key in ("SY", "LA"):
        assert "cached" not in briefing.get(conn, users[key], users[key])
        assert briefing.get(conn, users[key], users[key]).get("cached") is True
    clock_.advance(minutes=10)                                          # 00:05 UTC on the 23rd
    sy, la = briefing.get(conn, users["SY"], users["SY"]), briefing.get(conn, users["LA"], users["LA"])
    assert sy.get("cached") is True and sy["date"] == date(2026, 11, 23)    # Sydney's Monday started 11 hours ago
    assert la.get("cached") is True and la["date"] == date(2026, 11, 22)    # Los Angeles still has Sunday afternoon


# ---------------------------------------------------------------- week and month turn over at the user's midnight
def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def test_the_users_week_turns_over_at_their_own_midnight(frozen_clock, moment, users, conn):
    """Four places that bucket by Monday-based week: the weekly training plan, the muscle-load week, the grocery week and the body-age
    weeks. Sunday 23:55 is still the old week in Sydney; five minutes later it is the new one there and still the old one on the server."""
    frozen_clock(moment)
    for key, uid in users.items():
        day = expected(moment, key)
        wk = monday_of(day)
        assert grocery_store.week_start(conn, uid) == wk, (key, moment.name)
        plan = plans.get_week_plan(conn, uid, uid)
        assert (str(plan["start"]), str(plan["end"])) == (wk.isoformat(), (wk + timedelta(days=6)).isoformat()), (key, moment.name)
        mload = muscle_load.compute(conn, uid)
        assert (str(mload["today"]), str(mload["week_start"])) == (day.isoformat(), wk.isoformat()), (key, moment.name)
        weeks = healthspan_weeks(conn, uid)
        assert weeks[-1] == (wk, day), (key, moment.name)          # the last body-age week is "this week up to today"


def healthspan_weeks(conn, uid):
    from body_engine.insights import healthspan_store
    today = insight_views.today(conn, uid)
    return healthspan_store.week_ends(today - timedelta(days=30), today)


def test_the_week_changes_in_sydney_five_minutes_before_it_does_on_the_server(frozen_clock, users, conn):
    clock_ = frozen_clock(SUN_2355)
    sy, la = users["SY"], users["LA"]
    assert grocery_store.week_start(conn, sy) == date(2026, 11, 16) and grocery_store.week_start(conn, la) == date(2026, 11, 16)
    clock_.advance(minutes=10)                                  # Sydney Monday 00:05 = UTC Sunday 13:05 = Los Angeles Sunday 05:05
    assert grocery_store.week_start(conn, sy) == date(2026, 11, 23), "Sydney's new week"
    assert grocery_store.week_start(conn, la) == date(2026, 11, 16), "the Los Angeles week is not over"
    assert datetime.now().date() == date(2026, 11, 22), "and the server (UTC) is still in the old one"


def test_the_training_calendar_default_month_follows_the_user(frozen_clock, users, conn):
    """Monthly view: at Sydney 00:05 on 12-01 it is December there and still November on the server; LA is still on November's last day."""
    clock_ = frozen_clock(MONTH_END_2355)
    for key, uid in users.items():
        cal = training_history.calendar(conn, uid, uid)
        assert cal["month"] == f"{expected(MONTH_END_2355, key):%Y-%m}", key
    clock_.move_to(MONTH_START_0005)
    labels = {key: training_history.calendar(conn, uid, uid)["month"] for key, uid in users.items()}
    assert labels == {"SY": "2026-12", "LA": "2026-11", "SH": "2026-11"}
    cal = training_history.calendar(conn, users["SY"], users["SY"])
    assert (cal["start"], cal["end"]) == (date(2026, 12, 1), date(2026, 12, 31))
    rng = training_history.calendar(conn, users["SY"], users["SY"], start=date(2026, 11, 20))     # open-ended range ends on the user's today
    assert rng["end"] == date(2026, 12, 1)


def test_default_report_window_ends_on_the_users_day(frozen_clock, moment, users, conn):
    """Weekly / monthly reports with no `end` look back from the user's today (7 / 30 days, inclusive)."""
    from body_engine.insights import reports
    frozen_clock(moment)
    for key, uid in users.items():
        day = expected(moment, key)
        assert insight_views.today(conn, uid) == day
        start, prev_start, prev_end = reports.window("week", day)
        assert (start, prev_start, prev_end) == (day - timedelta(days=6), day - timedelta(days=13), day - timedelta(days=7))
        start, prev_start, prev_end = reports.window("month", insight_views.today(conn, uid))
        assert prev_end == day - timedelta(days=30) and start == day - timedelta(days=29), (key, moment.name)


def test_rules_evaluate_the_users_day(frozen_clock, moment, users, conn):
    from body_engine.rules import engine as rules_engine
    frozen_clock(moment)
    for key, uid in users.items():
        ctx = rules_context.load(conn, uid)
        assert ctx.day == expected(moment, key) and ctx.now_local().date() == ctx.day, (key, moment.name)
        assert rules_engine.run(conn, uid)["date"] == expected(moment, key), (key, moment.name)


def test_tonight_is_the_night_that_started_in_the_evening(frozen_clock, moment, users):
    """Sleep-goal day ("night of"): before 12:00 it is still the night that began yesterday evening, at 00:05 and at 23:55 alike."""
    frozen_clock(moment)
    for key in users:
        tz = ZoneInfo(ZONES[key])
        local = moment.when.astimezone(tz)
        want = local.date() - timedelta(days=1) if local.hour < 12 else local.date()
        assert goal_store.tonight(tz) == want, (key, moment.name)


# ---------------------------------------------------------------- scores: which day is "still in progress"
@pytest.mark.parametrize("key", sorted(ZONES))
def test_the_day_in_progress_is_the_users_today(key, frozen_clock, moment):
    """The strain of "today" is partial and flagged; at the user's 00:05 yesterday is final and today (just started) is the flagged one,
    while the server (UTC) may still be on yesterday (or already on tomorrow, for Los Angeles)."""
    from body_engine.scores import engine as score_engine, inputs as score_inputs
    frozen_clock(moment)
    tz = ZoneInfo(ZONES[key])
    today = expected(moment, key)
    rows = score_engine.score_days(score_inputs.Inputs(tz, None), today - timedelta(days=2), today + timedelta(days=1))
    flagged = {r["date"] for r in rows if "day_in_progress" in (r["flags"] or {})}
    assert flagged == {today}, (key, moment.name, flagged)
