"""Chat photos go after 30 days (ai/image_retention.py, owner's decision 2026-11-04). Only pictures inside the person's own chat
messages: the words, the AI's answers and everyone else's newer photos stay. Synthetic data (a 1x1 PNG, every text made up).
Own test database, recreated each run; skipped without Postgres.
"""
import argparse
import json
from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from body_engine import batch
from body_engine.ai import context, image_retention, posture
from body_engine.ai import posture_params as pp
from body_engine.config import Settings
from body_engine.db import migrate

TEST_DB = "body_test_ai_image_retention"
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
IMAGE = {"type": "image", "media_type": "image/png", "data": PNG}
TEXT = {"type": "text", "text": "这顿饭怎么样"}
LINE = "（这里有 2 张照片，现在看不到了）"


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


def conversation(conn) -> tuple[int, int]:
    uid = conn.execute("INSERT INTO users (display_name) VALUES ('S') RETURNING id").fetchone()["id"]
    cid = conn.execute("INSERT INTO ai_conversations (user_id, system_prompt) VALUES (%s, 's') RETURNING id", (uid,)).fetchone()["id"]
    conn.commit()
    return uid, cid


def message(conn, uid: int, cid: int, role: str, content: list, age_days: float) -> int:
    mid = conn.execute("""INSERT INTO ai_messages (conversation_id, user_id, role, content, created_at)
                          VALUES (%s, %s, %s, %s, now() - %s) RETURNING id""",
                       (cid, uid, role, Jsonb(content), timedelta(days=age_days))).fetchone()["id"]
    conn.commit()
    return mid


def content(conn, mid: int) -> list:
    return conn.execute("SELECT content FROM ai_messages WHERE id = %s", (mid,)).fetchone()["content"]


def test_the_period_is_thirty_days():
    assert pp.CHAT_IMAGE_KEEP_DAYS == 30


def test_only_pictures_past_the_period_go_and_the_words_stay(conn):
    uid, cid = conversation(conn)
    old = message(conn, uid, cid, "user", [TEXT, IMAGE, IMAGE], 30.1)
    fresh = message(conn, uid, cid, "user", [TEXT, IMAGE], 29.9)
    words = message(conn, uid, cid, "user", [TEXT], 90)
    answer = message(conn, uid, cid, "assistant", [{"type": "text", "text": "不错"}], 90)
    assert image_retention.expire(conn) >= 1

    assert content(conn, old) == [TEXT, {"type": "image_redacted", "purpose": None, "n": 2}]
    assert content(conn, fresh) == [TEXT, IMAGE]
    assert content(conn, words) == [TEXT]
    assert content(conn, answer) == [{"type": "text", "text": "不错"}]
    assert PNG not in json.dumps(content(conn, old))


def test_the_app_still_counts_them_and_the_model_gets_a_line(conn):
    uid, cid = conversation(conn)
    old = message(conn, uid, cid, "user", [TEXT, IMAGE, IMAGE], 40)
    image_retention.expire(conn)
    stored = content(conn, old)
    assert posture.image_count(stored) == 2
    assert context.show_redacted([{"content": stored}])[0]["content"] == [TEXT, {"type": "text", "text": LINE}]


def test_it_is_idempotent_and_leaves_other_people_alone(conn):
    uid, cid = conversation(conn)
    other, other_cid = conversation(conn)
    mine = message(conn, uid, cid, "user", [IMAGE], 31)
    theirs = message(conn, other, other_cid, "user", [IMAGE], 2)
    image_retention.expire(conn)
    once = content(conn, mine)
    assert image_retention.expire(conn) == 0
    assert content(conn, mine) == once and content(conn, theirs) == [IMAGE]


def test_the_hourly_batch_runs_it(conn, dsn, monkeypatch, capsys):
    uid, cid = conversation(conn)
    old = message(conn, uid, cid, "user", [IMAGE], 31)
    monkeypatch.setattr(batch.db, "connect", lambda: psycopg.connect(dsn, row_factory=dict_row))
    monkeypatch.setattr(batch, "list_users", lambda conn, only=None: [])
    args = argparse.Namespace(user_id=None, steps="", workers=1, full=False, no_skip=False, max_age_hours=6.0)
    assert batch.main(args) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1].removeprefix(batch.RESULT_PREFIX))
    assert result["ai_images_expired"] >= 1 and not result["errors"]
    assert PNG not in json.dumps(content(conn, old))
