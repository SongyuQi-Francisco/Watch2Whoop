"""Data gaps (FB-18, sync/gaps.py): types that used to arrive regularly and stopped, per user.

Pure rule tests plus one database test on a separate test database BODY_TEST_DB_NAME (default body_test_data_gaps),
dropped and recreated on every run; skipped if it cannot connect.
"""
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.sync import gaps, params as P

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_data_gaps")
TZ = ZoneInfo("Australia/Sydney")
TODAY = date(2026, 10, 27)


def daily(last_gap: int, n: int) -> set[date]:
    """n consecutive days ending last_gap days before TODAY."""
    last = TODAY - timedelta(days=last_gap)
    return {last - timedelta(days=i) for i in range(n)}


def test_judge_rules():
    assert gaps.judge(set(), TODAY, 3) is None                              # never had data: not a gap (data status says "never")
    assert gaps.judge(daily(3, 30), TODAY, 3) is None                       # only 2 full days missing
    g = gaps.judge(daily(4, 30), TODAY, 3)                                  # 3 full days missing, daily before
    assert g == {"last_date": (TODAY - timedelta(days=4)).isoformat(), "days": 4, "prior_days": P.GAP_LOOKBACK_DAYS}
    assert gaps.judge(daily(4, 30), TODAY, 2)["days"] == 4                  # types stale after 2 days still wait 3
    assert gaps.judge(daily(10, 30), TODAY, 30) is None                     # weight / VO2max: their own 30 days
    sparse = {TODAY - timedelta(days=d) for d in (5, 15, 25, 35)}           # a few nights a month (e.g. wrist temperature)
    assert gaps.judge(sparse, TODAY, 3) is None
    assert gaps.judge(daily(4, P.GAP_MIN_PRIOR_DAYS), TODAY, 3) is not None  # exactly the minimum
    assert gaps.judge(daily(4, P.GAP_MIN_PRIOR_DAYS - 1), TODAY, 3) is None
    assert gaps.judge(daily(P.GAP_GIVE_UP_DAYS + 1, 30), TODAY, 3) is None  # gone too long: given up


@pytest.fixture(scope="module")
def conn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    with psycopg.connect(Settings(db_name=TEST_DB).dsn, row_factory=dict_row) as c:
        migrate(c)
        yield c


def _user(conn, name):
    return conn.execute("""INSERT INTO users (display_name, role, consent_at, consent_version, timezone)
                           VALUES (%s, 'member', now(), 'v', 'Australia/Sydney') RETURNING id""", (name,)).fetchone()["id"]


def _metric(conn, uid, metric, days):
    for d in days:
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                        VALUES (%s, %s, %s, 1, 'x', 'avg', 1, 'ow')""", (uid, d, metric))


def _sleep(conn, uid, days):
    for d in days:
        start = datetime.combine(d - timedelta(days=1), time(23), TZ)
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, source)
                        VALUES (%s, %s, %s, %s, 420, 'ow')""", (uid, d, start, start + timedelta(hours=8)))


def test_scan(conn):
    a, b, c = _user(conn, "a"), _user(conn, "b"), _user(conn, "c")
    # a: heart rate and sleep keep arriving, wrist temperature was nightly and stopped 6 days ago, weight is occasional
    _metric(conn, a, "heart_rate", daily(0, 40))
    _sleep(conn, a, daily(1, 40))
    _metric(conn, a, "apple_sleeping_wrist_temperature", daily(6, 20))
    _metric(conn, a, "body_mass", {TODAY - timedelta(days=d) for d in (12, 40)})
    # b: everything stopped 5 days ago (phone not syncing)
    _metric(conn, b, "heart_rate", daily(5, 30))
    _sleep(conn, b, daily(5, 30))
    # c: all fine
    _metric(conn, c, "heart_rate", daily(0, 30))
    res = gaps.scan(conn, today=TODAY)
    by = {g["user_id"]: g for g in res["gaps"]}
    assert res["checked_users"] == 3 and set(by) == {a, b}
    assert by[a]["all"] is False
    assert [(t["key"], t["days"], t["last_date"]) for t in by[a]["types"]] == [
        ("wrist_temperature", 6, (TODAY - timedelta(days=6)).isoformat())]
    assert by[b]["all"] is True and {t["key"] for t in by[b]["types"]} == {"heart_rate", "sleep"}
    assert gaps.scan(conn, [c], today=TODAY)["gaps"] == []
    text = gaps.render(res)
    assert f"用户 {a}：手腕温度 6 天" in text and f"用户 {b}（全部类型）" in text
