"""Missing days (sync/missing_days.py, GET /v1/users/{user}/sync/missing-days, FB-24): the days of the last 30 the server has
nothing of sleep / heart rate / HRV / steps, newest first, with the kinds missing; not today, not before the user's first data.

Synthetic example values only. Separate test database BODY_TEST_DB_NAME_MISSING (default body_test_missing_days),
recreated each run; skipped without Postgres.
"""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers.common import upsert_daily
from body_engine.sync import missing_days as MD, params as P

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_MISSING", "body_test_missing_days")
TZ = ZoneInfo("Australia/Sydney")
T0 = date(2026, 11, 10)
KEYS = list(P.MISSING_DAY_KINDS)


def test_judge_is_newest_first_and_starts_at_the_first_data():
    end, start = T0 - timedelta(days=1), T0 - timedelta(days=30)
    d = lambda n: T0 - timedelta(days=n)   # noqa: E731
    have = {"sleep": {d(1), d(3)}, "heart_rate": {d(1), d(2), d(3)}, "hrv": {d(1), d(3)}, "steps": {d(1), d(2), d(3)}}
    got = MD.judge(have, start, end, first=d(3))
    assert got == [{"date": d(2).isoformat(), "kinds": ["sleep", "hrv"]}]
    # an account that started long ago: every day of the window without data is listed, newest first
    got = MD.judge(have, start, end, first=d(100))
    assert len(got) == 28 and got[0]["date"] == d(2).isoformat() and got[-1]["date"] == start.isoformat()
    assert got[-1]["kinds"] == KEYS
    assert MD.judge(have, start, end, first=None) == []                 # nothing ever arrived: no missing days


def test_kinds_follow_the_data_status():
    assert [k.key for k in MD.kinds()] == KEYS
    assert all(k.metrics for k in MD.kinds() if k.key != "sleep")


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "") or not TEST_DB.startswith("body_test_"):
        pytest.exit("the test DB must be a body_test_* database")
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


def _metric(d: date, metric: str) -> dict:
    return {"date": d, "metric": metric, "value": 1, "unit": "", "agg": "avg", "n": 1, "min_value": None, "max_value": None}


@pytest.fixture(scope="module")
def world(dsn):
    today = datetime.now(TZ).date()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        def add(name):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                                   VALUES (%s, 'member', 'Australia/Sydney', '2026-10-01', '2026-10-22.1')
                                   RETURNING id""", (name,)).fetchone()["id"]
        ids = {"a": add("a"), "b": add("b"), "new": add("new")}
        tok = {n: auth.create_token(conn, i, "t", ["read"]) for n, i in ids.items()}
        rows, nights = [], []
        for n in range(1, 41):                      # 40 days of data, with holes on day 2 (all) and day 4 (sleep, HRV)
            d = today - timedelta(days=n)
            if n == 2:
                continue
            rows += [_metric(d, "heart_rate"), _metric(d, "step_count")]
            if n != 4:
                rows.append(_metric(d, "heart_rate_variability_sdnn"))
                nights.append(d)
        upsert_daily(conn, ids["a"], "ow", rows, today)
        upsert_daily(conn, ids["a"], "ow", [_metric(today, "heart_rate")], today)   # today: never listed
        for d in nights:
            start = datetime.combine(d, datetime.min.time(), TZ) - timedelta(hours=1)
            conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, is_main, source)
                            VALUES (%s, %s, %s, %s, 420, true, 'ow')""", (ids["a"], d, start, start + timedelta(hours=7)))
        # new: started syncing 3 days ago, nothing missing since
        upsert_daily(conn, ids["new"], "shortcut", [_metric(today - timedelta(days=n), m) for n in (1, 2, 3)
                                               for m in ("heart_rate", "heart_rate_variability_rmssd", "step_count")], today)
        for n in (1, 2, 3):
            d = today - timedelta(days=n)
            start = datetime.combine(d, datetime.min.time(), TZ) - timedelta(hours=1)
            conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, is_main, source)
                            VALUES (%s, %s, %s, %s, 420, true, 'shortcut')""", (ids["new"], d, start, start + timedelta(hours=7)))
        conn.commit()
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield {"ids": ids, "tok": tok, "today": today, "client": TestClient(api.app, base_url="https://testserver")}
    api.pool.close()


def H(world, n):
    return {"Authorization": f"Bearer {world['tok'][n]}"}


def test_missing_days_of_a_user(world):
    today = world["today"]
    r = world["client"].get("/v1/users/me/sync/missing-days", headers=H(world, "a"))
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["start"], j["end"], j["window_days"]) == ((today - timedelta(days=30)).isoformat(),
                                                         (today - timedelta(days=1)).isoformat(), 30)
    assert [k["key"] for k in j["kinds"]] == KEYS and j["kinds"][0]["label"] == "睡眠"
    assert j["days"] == [{"date": (today - timedelta(days=2)).isoformat(), "kinds": KEYS},
                         {"date": (today - timedelta(days=4)).isoformat(), "kinds": ["sleep", "hrv"]}]
    assert j["count"] == 2
    en = world["client"].get("/v1/users/me/sync/missing-days", headers={**H(world, "a"), "X-Body-Lang": "en"}).json()
    assert en["kinds"][0]["label"] == "Sleep"


def test_no_data_and_new_accounts_have_no_missing_days(world):
    c = world["client"]
    assert c.get("/v1/users/me/sync/missing-days", headers=H(world, "b")).json()["days"] == []
    j = c.get("/v1/users/me/sync/missing-days", headers=H(world, "new")).json()
    assert j["days"] == [] and j["since"] == (world["today"] - timedelta(days=3)).isoformat()


def test_own_data_only(world):
    c, ids = world["client"], world["ids"]
    assert c.get(f"/v1/users/{ids['a']}/sync/missing-days", headers=H(world, "b")).status_code == 403
    assert c.get(f"/v1/users/{ids['a']}/sync/missing-days", headers=H(world, "a")).status_code == 200
    assert c.get("/v1/users/me/sync/missing-days").status_code == 401
