"""Requests of one login never wait for each other (Today page slowdown, 2026-10-26).

The Today page's cookie session and Bearer tokens used to write last_seen_at / last_used_at on every request, which kept
that row locked until the request ended: the page's ~20 parallel start-up reads then ran one after another. Now the stamp
is written at most every auth.TOUCH_EVERY, with SKIP LOCKED, so a request that finds the row locked just reads it.
Uses its own test database (body_test_login_touch), dropped and recreated on every run; skipped if Postgres is unreachable.
"""
import os

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine import auth
from body_engine.ai import tokens as ai_tokens
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.web import session

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_LOGIN_TOUCH", "body_test_login_touch")


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
def login(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        uid = c.execute("INSERT INTO users (display_name, role) VALUES ('T', 'owner') RETURNING id").fetchone()["id"]
        token = auth.create_token(c, uid, "web", ["read", "write"])
        mcp = auth.create_token(c, uid, "ai", [auth.MCP_SCOPE])
        sid, _ = session.login(c, token)
    return {"uid": uid, "token": token, "mcp": mcp, "sid": sid}


def _connect(dsn):
    c = psycopg.connect(dsn, row_factory=dict_row)
    c.execute("SET lock_timeout = '2s'")   # a request that waited for the other one fails here instead of hanging
    c.commit()
    return c


def _age(dsn, sql, *args):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        c.execute(sql, args)


@pytest.mark.parametrize("kind", ["session", "token", "mcp"])
def test_parallel_requests_of_one_login_do_not_wait(dsn, login, kind):
    if kind == "session":
        _age(dsn, "UPDATE web_sessions SET last_seen_at = now() - interval '1 hour' WHERE user_id = %s", login["uid"])
        who = lambda c: session.user_for_session(c, login["sid"])   # noqa: E731
    elif kind == "token":
        _age(dsn, "UPDATE api_tokens SET last_used_at = now() - interval '1 hour' WHERE user_id = %s", login["uid"])
        who = lambda c: auth.user_for_token(c, login["token"])   # noqa: E731
    else:
        _age(dsn, "UPDATE api_tokens SET last_used_at = NULL WHERE user_id = %s", login["uid"])
        who = lambda c: ai_tokens.user_for_mcp_token(c, login["mcp"])   # noqa: E731
    first, second = _connect(dsn), _connect(dsn)
    try:
        assert who(first)["id"] == login["uid"]    # stale: writes the stamp and keeps its transaction (the request runs on)
        assert who(second)["id"] == login["uid"]   # a parallel request: no lock wait (lock_timeout would raise)
        second.commit()
        first.commit()
    finally:
        first.close()
        second.close()


def test_stamp_is_written_when_stale_only(dsn, login):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        c.execute("UPDATE web_sessions SET last_seen_at = now() - interval '1 hour', expires_at = now() + interval '1 day'"
                  " WHERE user_id = %s", (login["uid"],))
        assert session.user_for_session(c, login["sid"])["id"] == login["uid"]
        row = c.execute("SELECT last_seen_at > now() - interval '1 minute' AS fresh, expires_at > now() + interval '300 days' AS long"
                        " FROM web_sessions WHERE user_id = %s", (login["uid"],)).fetchone()
        assert row["fresh"] and row["long"]   # renewed: seen now, lifetime extended
        c.execute("UPDATE web_sessions SET last_seen_at = now() - interval '1 minute' WHERE user_id = %s", (login["uid"],))
        assert session.user_for_session(c, login["sid"])["id"] == login["uid"]
        assert not c.execute("SELECT last_seen_at > now() - interval '30 seconds' AS fresh FROM web_sessions WHERE user_id = %s",
                             (login["uid"],)).fetchone()["fresh"]   # within TOUCH_EVERY: left alone
        c.execute("UPDATE api_tokens SET last_used_at = NULL WHERE id = (SELECT token_id FROM web_sessions WHERE user_id = %s)",
                  (login["uid"],))
        assert auth.user_for_token(c, login["token"])["id"] == login["uid"]
        assert c.execute("SELECT last_used_at FROM api_tokens WHERE name = 'web' AND user_id = %s",
                         (login["uid"],)).fetchone()["last_used_at"] is not None   # first use is always stamped
