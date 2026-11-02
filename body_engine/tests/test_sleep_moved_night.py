"""A night whose end grows keeps its start and moves to a later wake date: the rewrite of the new date has to take the old
row with it, or UNIQUE (user_id, source, start_at) stops the import on every run. Seen in production through import-ow:
a night synced up to before 04:00 woke "yesterday" (end − 4 h), the rest of it arrived later.
common.replace_sleep (Open Wearables, the Shortcut, exports, the app mode) and the phone's kind `sleep` have the same rule.
Synthetic data (every number a made-up example value, 示例). Own test database, recreated each run; skipped without Postgres.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import common
from body_engine.nativesync.derived_kinds import sleep as kind_sleep

TEST_DB = "body_test_sleep_moved"
D1, D2 = date(2026, 3, 1), date(2026, 3, 2)
TZ = timezone(timedelta(hours=11))
START = datetime(2026, 3, 1, 23, 40, tzinfo=TZ)
PART_END = datetime(2026, 3, 2, 3, 10, tzinfo=TZ)    # end − 4 h = D1
FULL_END = datetime(2026, 3, 2, 7, 30, tzinfo=TZ)    # end − 4 h = D2
NAP_START = datetime(2026, 3, 1, 14, 0, tzinfo=TZ)
NAP_END = datetime(2026, 3, 1, 14, 40, tzinfo=TZ)


@pytest.fixture(scope="module")
def dsn():
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # no password file, or the database is not running
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c
        c.rollback()


@pytest.fixture
def uid(conn):
    return conn.execute("INSERT INTO users (display_name) VALUES ('S') RETURNING id").fetchone()["id"]


def session(wake: date, start: datetime, end: datetime, minutes: int) -> common.SleepSession:
    return common.SleepSession(wake, start, end, Counter({"core": minutes}), "Apple Watch")


def stored(conn, uid: int, source: str) -> list[tuple]:
    return [(r["wake_date"], r["start_at"], r["is_main"]) for r in conn.execute(
        "SELECT wake_date, start_at, is_main FROM sleep_sessions WHERE user_id = %s AND source = %s ORDER BY start_at",
        (uid, source))]


def test_replace_sleep_moves_a_night_to_its_new_wake_date(conn, uid):
    common.replace_sleep(conn, uid, "ow", [session(D1, NAP_START, NAP_END, 35), session(D1, START, PART_END, 200)])
    assert stored(conn, uid, "ow") == [(D1, NAP_START, False), (D1, START, True)]

    assert common.replace_sleep(conn, uid, "ow", [session(D2, START, FULL_END, 460)]) == {"inserted": 1, "skipped": 0}
    # the night moved, and what is left of the old date is chosen again
    assert stored(conn, uid, "ow") == [(D1, NAP_START, True), (D2, START, True)]
    # the next run changes nothing
    common.replace_sleep(conn, uid, "ow", [session(D2, START, FULL_END, 460)])
    assert stored(conn, uid, "ow") == [(D1, NAP_START, True), (D2, START, True)]


def test_replace_sleep_leaves_the_same_start_of_another_source(conn, uid):
    common.replace_sleep(conn, uid, "shortcut", [session(D1, START, PART_END, 200)])
    common.replace_sleep(conn, uid, "ow", [session(D2, START, FULL_END, 460)])
    assert stored(conn, uid, "shortcut") == [(D1, START, True)]
    assert stored(conn, uid, "ow") == [(D2, START, True)]


def row(start: datetime, end: datetime, minutes: int) -> dict:
    return {"start": start.timestamp(), "end": end.timestamp(), "asleep_min": minutes, "core_min": minutes, "deep_min": 0,
            "rem_min": 0, "awake_min": 0, "unspecified_min": 0, "is_main": True, "device": "Apple Watch", "segments": []}


def test_phone_kind_moves_a_night_to_its_new_wake_date(conn, uid):
    app = common.APP_SOURCE
    kind_sleep.write(conn, uid, {D1: [row(NAP_START, NAP_END, 35), row(START, PART_END, 200)]}, "v1")
    assert stored(conn, uid, app) == [(D1, NAP_START, False), (D1, START, True)]

    assert kind_sleep.write(conn, uid, {D2: [row(START, FULL_END, 460)]}, "v1") == 1
    assert stored(conn, uid, app) == [(D1, NAP_START, True), (D2, START, True)]
