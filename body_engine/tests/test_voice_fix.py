"""Personal hotword correction of recognised speech (Jarvis voice input, #53, flag jarvis_orbit): the matching rules, the
user's own term list (never another user's), POST /v1/assistant/voice-fix and transcribe?fix=1.

The DB tests use their own database BODY_TEST_DB_NAME (default body_test_voice_fix), recreated each run; skipped without Postgres."""
import io
import os
import wave

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.ai import asr, voice_fix
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags, registry

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_voice_fix")
FLAG = "jarvis_orbit"


# ---------------------------------------------------------------- matching rules (no database)
def test_flags_are_declared():
    assert FLAG in registry.FLAGS and "jarvis_voice_direct" in registry.FLAGS


@pytest.mark.parametrize("text, terms, want", [
    # English near misses of the user's own terms (spelling + sound)
    ("刚做完Romania deadlift，腰有点酸", ["Romanian deadlift"], "刚做完Romanian deadlift，腰有点酸"),
    ("lat pool down加到五十五公斤了", ["lat pulldown"], "lat pulldown加到五十五公斤了"),
    ("Two eggs, Greek yoga and blueberries", ["Greek yogurt"], "Two eggs, Greek yogurt and blueberries"),
    ("下周想把Cartio换成快走", ["cardio"], "下周想把cardio换成快走"),
    # Chinese: same pinyin (two-character terms also the same tones)
    ("补寄吃了肌酸五克", ["补剂", "肌酸"], "补剂吃了肌酸五克"),
    ("卧推六十公斤", ["卧推"], "卧推六十公斤"),
])
def test_near_misses_become_the_users_term(text, terms, want):
    out, changes = voice_fix.fix(text, terms)
    assert out == want
    assert all(c["to"] in terms or c["to"].rstrip("s") in terms for c in changes)


@pytest.mark.parametrize("text, terms", [
    ("我不及格", ["补剂"]),                       # bu ji, other tones: an everyday word stays
    ("刚才打了一个小时篮球，信誉有点高", ["鱼油"]),   # 誉有 yù yǒu is not 鱼油 yú yóu (a wrong replacement in the study)
    ("I'll give them some calls", ["Coles"]),     # a short term needs a closer match
    ("cardiac output", ["cardio"]),
    ("lat pull down at fifty", ["lat pulldown"]),  # already right apart from spacing
    ("", ["deadlift"]),
    ("nothing to fix here", []),
])
def test_everyday_words_stay(text, terms):
    out, changes = voice_fix.fix(text, terms)
    assert out == text and changes == []


def test_plural_is_kept():
    out, _ = voice_fix.fix("I did three sets of Romanian dedlifts", ["Romanian deadlift"])
    assert out == "I did three sets of Romanian deadlifts"


def test_collect_cleans_dedupes_and_caps():
    names = ["杠铃卧推（平板）", "杠铃卧推", "Bench Press", "bench press", "x", "AB", "Greek 酸奶", None, "", "罗马尼亚硬拉"]
    out = voice_fix.collect(names)
    assert out == ["杠铃卧推", "Bench Press", "罗马尼亚硬拉"]   # bracket dropped, case-insensitive dedupe, unusable skipped
    assert len(voice_fix.collect([f"term{chr(97 + i % 26)}{chr(97 + i // 26)}" for i in range(400)])) == voice_fix.MAX_TERMS


# ---------------------------------------------------------------- the user's terms, the endpoints
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
        def add(name):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at)
                                   VALUES (%s, 'owner', 'Australia/Sydney', now()) RETURNING id""", (name,)).fetchone()["id"]
        a, b = add("a"), add("b")
        conn.execute("""INSERT INTO meal_templates (user_id, name, meal, items) VALUES (%s, '燕麦酸奶碗', 'breakfast', %s)""",
                     (a, Jsonb([{"food": "oats", "grams": 50}])))
        conn.execute("INSERT INTO supplements (user_id, name, status) VALUES (%s, '肌酸', 'active'), (%s, '褪黑素', 'paused')", (a, a))
        conn.execute("INSERT INTO custom_exercises (user_id, name_zh, name_en, primary_muscles, source) VALUES (%s, '罗马尼亚硬拉', 'Romanian deadlift', '{hamstrings}', 'manual')", (a,))
        conn.execute("INSERT INTO grocery_products (user_id, name, brand, store) VALUES (%s, 'Greek yogurt', 'Chobani', 'Woolworths')", (a,))
        conn.execute("INSERT INTO supplements (user_id, name, status) VALUES (%s, '鱼油', 'active')", (b,))   # b's own, never a's
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in (("a", a), ("b", b))}
        conn.commit()
    voice_fix.forget()
    return {"a": a, "b": b}, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def set_flag(dsn, state):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, FLAG, state, actor="test")
        conn.commit()
    flags.invalidate()


def test_terms_are_the_users_own(dsn, users):
    ids, _ = users
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        a = voice_fix.terms(conn, ids["a"])
        b = voice_fix.terms(conn, ids["b"])
    assert {"燕麦酸奶碗", "肌酸", "罗马尼亚硬拉", "Romanian deadlift", "Greek yogurt", "Chobani", "Woolworths"} <= set(a)
    assert "褪黑素" not in a          # paused supplements are not everyday words
    assert "鱼油" not in a and b == ["鱼油"]


def test_voice_fix_endpoint_follows_the_flag(dsn, users, client):
    _, tok = users
    set_flag(dsn, "off")
    r = client.post("/v1/assistant/voice-fix", json={"text": "Romania deadlift"}, headers={"Authorization": f"Bearer {tok['a']}"})
    assert r.status_code == 404
    set_flag(dsn, "all")
    r = client.post("/v1/assistant/voice-fix", json={"text": "三组 Romania deadlift，吃了鸡酸"}, headers={"Authorization": f"Bearer {tok['a']}"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["text"] == "三组 Romanian deadlift，吃了肌酸" and d["raw"] == "三组 Romania deadlift，吃了鸡酸"
    assert {c["to"] for c in d["fixed"]} == {"Romanian deadlift", "肌酸"}
    # another user: their own terms only
    r = client.post("/v1/assistant/voice-fix", json={"text": "Romania deadlift"}, headers={"Authorization": f"Bearer {tok['b']}"})
    assert r.json() == {"text": "Romania deadlift", "fixed": []}
    too_long = client.post("/v1/assistant/voice-fix", json={"text": "a" * (voice_fix.MAX_TEXT + 1)}, headers={"Authorization": f"Bearer {tok['a']}"})
    assert too_long.status_code == 422


def _wav(seconds=1.0, rate=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x10" * int(seconds * rate))
    return buf.getvalue()


def test_transcribe_corrects_only_when_asked_and_on(dsn, users, client, monkeypatch):
    _, tok = users
    monkeypatch.setenv("BODY_ASR_URL", "http://asr.invalid")
    monkeypatch.setattr(asr, "_post", lambda data: {"text": "罗马尼亚硬拉三组，吃了鸡酸"})
    h = {"Authorization": f"Bearer {tok['a']}", "Content-Type": "audio/wav"}
    set_flag(dsn, "all")
    plain = client.post("/v1/assistant/transcribe", content=_wav(), headers=h).json()
    assert plain["text"] == "罗马尼亚硬拉三组，吃了鸡酸" and "fixed" not in plain   # the chat's own mic: unchanged
    fixed = client.post("/v1/assistant/transcribe?fix=1", content=_wav(), headers=h).json()
    assert fixed["text"] == "罗马尼亚硬拉三组，吃了肌酸" and fixed["raw"] == plain["text"]
    set_flag(dsn, "off")
    off = client.post("/v1/assistant/transcribe?fix=1", content=_wav(), headers=h).json()
    assert off["text"] == plain["text"] and "fixed" not in off
