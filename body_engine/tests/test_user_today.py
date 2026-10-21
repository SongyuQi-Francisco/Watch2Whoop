"""Default "today" is the data owner's own date, not the server's (issue #89).

The server runs in Sydney; one user lives in Shanghai. At UTC 13:30 on 2026-10-21 it is already 10-22 00:30 in Sydney but
still 10-21 21:30 in Shanghai, so every default date range (API, AI tools, age) must end on 10-21 for the Shanghai user,
also when someone in Sydney looks at their shared data.

Uses its own test database (body_test_user_today), dropped and recreated on every run; skipped if Postgres is unreachable.
"""
import time
from datetime import date, datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, queries
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.records import tools as record_tools
from body_engine.rules import tools as rule_tools

TEST_DB = "body_test_user_today"
NOW = datetime(2026, 10, 21, 13, 30, tzinfo=timezone.utc)   # Sydney 10-22 00:30, Shanghai 10-21 21:30
D21, D22 = date(2026, 10, 21), date(2026, 10, 22)


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


@pytest.fixture(scope="module")
def world(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids, tok = {}, {}
        for name, tz in (("SH", "Asia/Shanghai"), ("SY", "Australia/Sydney")):
            uid = conn.execute("""INSERT INTO users (display_name, role, timezone, birth_date, consent_at)
                                  VALUES (%s, 'member', %s, '2000-10-22', now()) RETURNING id""", (name, tz)).fetchone()["id"]
            ids[name], tok[name] = uid, auth.create_token(conn, uid, "web", ["read", "write"])
            conn.execute("INSERT INTO journal_items (user_id, key, label_zh) VALUES (%s, 'coffee', '咖啡')", (uid,))
            for d in (D21, D22):
                conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, agg, source)
                                VALUES (%s, %s, 'step_count', 5000, 'sum', 'ow')""", (uid, d))
                conn.execute("INSERT INTO journal_entries (user_id, date, key, value) VALUES (%s, %s, 'coffee', 1)", (uid, d))
                conn.execute("""INSERT INTO reminders (user_id, date, rule_key, level, title, message, rules_version)
                                VALUES (%s, %s, 'readiness', 'info', %s, 'x', 't')""", (uid, d, f"r{d.day}"))
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["SH"], ids["SY"], ["metrics", "journal", "reminders"]))
    return ids, tok


@pytest.fixture
def frozen(monkeypatch):
    """Freeze "now" and run the process in Sydney time, like the server."""
    monkeypatch.setattr(queries, "_now", lambda: NOW)
    monkeypatch.setenv("TZ", "Australia/Sydney")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token):
    return {"Authorization": f"Bearer {token}"}


def dates(rows) -> set[date]:
    return {date.fromisoformat(str(r["date"])) for r in rows}


def test_local_today_follows_the_user_not_the_server(world, dsn, frozen):
    ids, _ = world
    assert NOW.astimezone().date() == D22                       # what the server's own clock calls today
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert queries.local_today(conn, ids["SH"]) == D21
        assert queries.local_today(conn, ids["SY"]) == D22
        assert queries.profile(conn, ids["SH"])["age"] == 25    # 26th birthday is tomorrow in Shanghai
        assert queries.profile(conn, ids["SY"])["age"] == 26


def test_default_ranges_in_the_api(client, world, frozen):
    ids, tok = world
    for path in ("daily-metrics", "journal", "reminders"):
        sh = client.get(f"/v1/users/me/{path}", headers=H(tok["SH"]))
        sy = client.get(f"/v1/users/me/{path}", headers=H(tok["SY"]))
        assert sh.status_code == sy.status_code == 200, (path, sh.text, sy.text)
        assert D22 not in dates(sh.json()) and D21 in dates(sh.json()), path
        assert D22 in dates(sy.json()), path
    # a viewer in Sydney looking at the Shanghai user's shared data: the owner's today counts
    for path in ("daily-metrics", "journal", "reminders"):
        r = client.get(f"/v1/users/{ids['SH']}/{path}", headers=H(tok["SY"]))
        assert r.status_code == 200 and D22 not in dates(r.json()) and D21 in dates(r.json()), (path, r.text)


def test_default_ranges_in_ai_tools(world, dsn, frozen):
    ids, _ = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        journal = record_tools._journal(conn, ids["SH"], {"start": None, "end": None, "keys": None})
        assert dates(journal) == {D21}
        reminders = rule_tools._get(conn, ids["SH"], {"start": None, "end": None, "include_dismissed": False,
                                                       "rule": None, "detail": True})
        assert {r["title"] for r in reminders["reminders"]} == {"r21"}
