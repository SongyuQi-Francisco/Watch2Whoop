"""Long-term memory hygiene, phase 1 (issue #113): topic inference, similar notes at write time, previous value on profile
card rewrites and their rollback, 「还准确」 / restore / delete on the Today page, archived entries left out of the memory,
and cross-user isolation (a memory share can read, never write).

Needs Postgres: uses a separate test database BODY_TEST_DB_NAME (default body_test_memory_hygiene), dropped and recreated
on every run. All texts are made-up examples.
"""
import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.ai import prompt
from body_engine.assistant import memory, memory_params as mp
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_memory_hygiene")


# ---------------------------------------------------------------- no database needed
def test_topic_inference():
    assert memory.infer_topic("左膝盖疼，暂时不做深蹲") == "injuries"          # specific before broad
    assert memory.infer_topic("乳糖不耐，喝牛奶胀气") == "health_constraints"
    assert memory.infer_topic("回答简短一点，别用表情") == "communication"
    assert memory.infer_topic("每周三晚上打篮球") == memory.infer_topic("周三打篮球")
    assert memory.infer_topic("Prefers short replies") == "communication"
    assert memory.infer_topic("a great day") is None                          # "eat" only at a word start
    assert memory.infer_topic("示例") is None
    assert memory.topic_for_key("injuries") == "injuries"
    assert memory.topic_for_key("diet_day_plan") == "diet_preferences"
    assert memory.topic_for_key("physique_goal") == "goals"
    assert memory.topic_for_key("misc", "备注", "示例") is None
    assert {t for t, _ in mp.TOPIC_KEYWORDS} <= set(memory.STANDARD_KEYS)
    assert set(mp.STALE_DAYS) == set(memory.STANDARD_KEYS)


def test_similarity():
    assert memory.similarity("周三打篮球", "每周三晚上篮球") >= mp.SIMILAR_MIN_RATIO
    assert memory.similarity("周三打篮球。", " 周三 打篮球") == 1.0               # spaces and punctuation do not count
    assert memory.similarity("周三打篮球", "喜欢吃辣") < mp.SIMILAR_MIN_RATIO
    assert memory.similarity("", "x") == 0.0


def test_tool_schemas():
    props = TOOLS_BY_NAME["add_observation"].input_schema["properties"]
    assert "replace_id" in props and "user" not in props


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
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


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture()
def users(conn):
    """A owns the memory; B has a memory share from A; C has none. The rollout flags are on for everyone."""
    ids = {n: conn.execute("INSERT INTO users (display_name, timezone) VALUES (%s, 'Australia/Sydney') RETURNING id",
                           (n,)).fetchone()["id"] for n in "ABC"}
    conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["memory"]))
    for key in ("memory_hygiene", "memory_recent"):
        flags.set_flag(conn, key, "all", actor="test")
    conn.commit()
    flags.invalidate()
    return ids


def _flag(conn, state):
    flags.set_flag(conn, "memory_hygiene", state, actor="test")
    conn.commit()
    flags.invalidate()


def _add(conn, uid, text, **kw):
    args = {"text": text, "evidence": None, "until": None, "replace_id": None, **kw}
    return run_tool(conn, uid, "butler", "add_observation", args)


def _card(conn, uid, key, value, label=None):
    return run_tool(conn, uid, "butler", "update_profile_card", {"key": key, "value": value, "label": label})


def _log(conn, uid, tool):
    return conn.execute("SELECT * FROM ai_write_log WHERE user_id = %s AND tool = %s ORDER BY id DESC LIMIT 1",
                        (uid, tool)).fetchone()


def test_similar_note_is_not_added_twice(conn, users):
    a = users["A"]
    first = _add(conn, a, "每周三晚上打篮球", evidence="用户原话（示例）")
    assert first["duplicate"] is False and first["topic"] == "schedule" and first["origin"] is None   # AI write: unknown
    # a rewording of the same fact: nothing new, the old note comes back as similar
    res = _add(conn, a, "周三打篮球")
    assert res["created"] is False and [s["id"] for s in res["similar"]] == [first["id"]] and "replace_id" in res["hint"]
    assert len(memory.get_memory(conn, a)["observations"]) == 1
    # the AI merges it into the old note: same id, new text, logged with before / after
    merged = _add(conn, a, "每周三晚上打篮球（约 2 小时，示例）", replace_id=first["id"])
    assert merged["id"] == first["id"] and merged["previous"] == "每周三晚上打篮球"
    log = _log(conn, a, "add_observation")
    assert (log["kind"], log["action"], log["before"]["text"]) == ("observation", "update", "每周三晚上打篮球")
    # rollback_write puts the old text back
    run_tool(conn, a, "butler", "rollback_write", {"log_id": log["id"]})
    assert [o["text"] for o in memory.get_memory(conn, a)["observations"]] == ["每周三晚上打篮球"]
    # a different fact the server thinks is similar: replace_id=0 writes it anyway
    other = _add(conn, a, "每周三早上游泳", replace_id=0)
    assert other["duplicate"] is False and len(memory.get_memory(conn, a)["observations"]) == 2
    # another topic is never compared
    assert _add(conn, a, "周三打篮球后膝盖疼")["topic"] == "injuries"


def test_exact_duplicate_confirms_and_flag_off_is_as_before(conn, users):
    a = users["A"]
    o = _add(conn, a, "喝咖啡后睡不好（示例）")
    conn.execute("UPDATE assistant_observations SET confirmed_at = now() - interval '200 days' WHERE id = %s", (o["id"],))
    assert next(x for x in memory.get_memory(conn, a)["observations"] if x["id"] == o["id"])["stale"] is True
    again = _add(conn, a, " 喝咖啡后睡不好（示例） ")
    assert again["duplicate"] and again["id"] == o["id"]
    row = next(x for x in memory.get_memory(conn, a)["observations"] if x["id"] == o["id"])
    assert row["stale"] is False and row["confirmed_days"] == 0
    _flag(conn, "off")
    try:   # no similar check: a rewording is a new note, as before #113
        assert _add(conn, a, "喝了咖啡睡不好（示例）")["duplicate"] is False
    finally:
        _flag(conn, "all")
    with pytest.raises(ValueError, match="topic"):
        memory.add_observation(conn, a, "x", topic="nope")
    with pytest.raises(ValueError, match="origin"):
        memory.add_observation(conn, a, "y", origin="guess")
    assert memory.add_observation(conn, a, "示例：用户在页面上写的", source="user")["origin"] == "user_said"


def test_profile_card_rewrite_returns_previous_and_rolls_back(conn, users):
    a = users["A"]
    new = _card(conn, a, "goals", "示例：3 个月减 2kg")
    assert "previous" not in new and _log(conn, a, "update_profile_card")["action"] == "create"
    res = _card(conn, a, "goals", "示例：增肌")
    assert res["previous"] == "示例：3 个月减 2kg" and "rollback_write" in res["hint"]
    log = _log(conn, a, "update_profile_card")
    assert (log["kind"], log["before"]["value"], log["after"]["value"]) == ("profile", "示例：3 个月减 2kg", "示例：增肌")
    assert next(x for x in run_tool(conn, a, "butler", "get_write_log", {"days": None}) if x["id"] == log["id"])["rollback"] == "ok"
    run_tool(conn, a, "butler", "rollback_write", {"log_id": log["id"]})
    card = {c["key"]: c for c in memory.get_memory(conn, a)["profile_card"]}
    assert card["goals"]["value"] == "示例：3 个月减 2kg" and card["goals"]["topic"] == "goals"
    # the user edited it after the AI: the AI's write is not rolled back over the user's edit
    _card(conn, a, "goals", "示例：保持")
    log = _log(conn, a, "update_profile_card")
    memory.update_profile(conn, a, "goals", "示例：用户自己改的", source="user")
    with pytest.raises(ValueError, match="又改过"):
        run_tool(conn, a, "butler", "rollback_write", {"log_id": log["id"]})
    # rolling back a new item removes it
    _card(conn, a, "sleep_notes", "示例：周末晚睡", label="睡眠备注")
    run_tool(conn, a, "butler", "rollback_write", {"log_id": _log(conn, a, "update_profile_card")["id"]})
    assert "sleep_notes" not in {c["key"] for c in memory.get_memory(conn, a)["profile_card"]}


def test_archived_entries_leave_the_memory_and_can_be_restored(conn, users):
    a = users["A"]
    keep = memory.add_observation(conn, a, "示例：喜欢晨练")
    gone = memory.add_observation(conn, a, "示例：每周跑三次")
    memory.update_profile(conn, a, "current_plan", "示例：旧计划")
    conn.execute("""UPDATE assistant_observations SET status = 'archived', archived_at = now(), archived_reason = '示例：合并',
                      merged_into = %s WHERE id = %s""", (keep["id"], gone["id"]))
    conn.execute("""UPDATE assistant_profile SET status = 'archived', archived_at = now(), archived_reason = '示例：过时'
                    WHERE user_id = %s AND key = 'current_plan'""", (a,))
    m = memory.get_memory(conn, a, archived=True)
    assert gone["id"] not in {o["id"] for o in m["observations"]} and "current_plan" not in {c["key"] for c in m["profile_card"]}
    assert [o["id"] for o in m["archived"]["observations"]] == [gone["id"]]
    assert [c["key"] for c in m["archived"]["profile_card"]] == ["current_plan"]
    block = prompt._memory_block(conn, a)
    assert "每周跑三次" not in block and "旧计划" not in block and "喜欢晨练" in block
    # restating an archived note does not bring it back by itself
    again = memory.add_observation(conn, a, "示例：每周跑三次")
    assert again["duplicate"] and again["status"] == "archived" and "恢复" in again["hint"]
    assert memory.restore_observation(conn, a, gone["id"])["id"] == gone["id"]
    assert memory.restore_observation(conn, a, gone["id"]) is None                        # not archived any more
    assert memory.restore_profile(conn, a, "current_plan")["value"] == "示例：旧计划"
    # writing an archived profile item brings it back too
    conn.execute("UPDATE assistant_profile SET status = 'archived', archived_at = now() WHERE user_id = %s AND key = 'current_plan'", (a,))
    memory.update_profile(conn, a, "current_plan", "示例：新计划")
    assert "current_plan" in {c["key"] for c in memory.get_memory(conn, a)["profile_card"]}


def test_backfill_topics(conn, users):
    a = users["A"]
    o = memory.add_observation(conn, a, "示例：对花生过敏")
    n = memory.add_observation(conn, a, "示例")
    memory.update_profile(conn, a, "diet_day_plan", "示例：三餐", label="一天怎么吃")
    conn.execute("UPDATE assistant_observations SET topic = NULL WHERE user_id = %s", (a,))
    conn.execute("UPDATE assistant_profile SET topic = NULL WHERE user_id = %s", (a,))
    memory.backfill_topics(conn)
    topics = {x["id"]: x["topic"] for x in conn.execute("SELECT id, topic FROM assistant_observations WHERE user_id = %s", (a,))}
    assert topics[o["id"]] == "health_constraints" and topics[n["id"]] is None
    assert conn.execute("SELECT topic FROM assistant_profile WHERE user_id = %s AND key = 'diet_day_plan'",
                        (a,)).fetchone()["topic"] == "diet_preferences"
    assert conn.execute("SELECT 1 FROM schema_migrations WHERE name = '655_memory_hygiene_data'").fetchone()


def test_truncation_order_keeps_priority_items(conn, users):
    """The order of #112 stays: communication -> health constraints -> injuries -> recent notes -> other card -> notes."""
    a = users["A"]
    for key in ("goals", "injuries", "communication", "health_constraints"):
        memory.update_profile(conn, a, key, f"示例 {key}")
    memory.add_observation(conn, a, "示例：出差到下周", until="recent")
    memory.add_observation(conn, a, "示例：喜欢晨练")
    block = prompt._memory_block(conn, a)
    pos = [block.index(s) for s in ("示例 communication", "示例 health_constraints", "示例 injuries", "出差到下周",
                                    "示例 goals", "喜欢晨练")]
    assert pos == sorted(pos)


# ---------------------------------------------------------------- API: 「还准确」 / delete / restore, isolation
@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def h(token):
    return {"Authorization": f"Bearer {token}"}


def test_api_confirm_restore_delete_and_isolation(client, conn, users):
    a, b, c = users["A"], users["B"], users["C"]
    tok = {n: auth.create_token(conn, users[n], "test", ["read", "write"]) for n in "ABC"}
    tok["A_ro"] = auth.create_token(conn, a, "test-ro", ["read"])
    o = memory.add_observation(conn, a, "示例：周日长跑")
    memory.update_profile(conn, a, "schedule", "示例：23 点睡")
    arch = memory.add_observation(conn, a, "示例：旧习惯")
    conn.execute("UPDATE assistant_observations SET confirmed_at = now() - interval '300 days' WHERE id = %s", (o["id"],))
    conn.execute("UPDATE assistant_observations SET status = 'archived', archived_at = now(), archived_reason = '示例' WHERE id = %s",
                 (arch["id"],))
    conn.commit()
    base = "/v1/users/me/memory"

    body = client.get(base, headers=h(tok["A"])).json()
    row = next(x for x in body["observations"] if x["id"] == o["id"])
    assert row["stale"] is True and row["confirmed_days"] >= 300 and row["topic"] is not None
    assert {"topic", "confirmed", "stale"} <= set(body["profile_card"][0])
    assert [x["id"] for x in body["archived"]["observations"]] == [arch["id"]]

    # B (memory share) reads A's memory, archived included, but cannot confirm, restore or delete anything
    assert client.get(f"/v1/users/{a}/memory", headers=h(tok["B"])).json()["archived"]["observations"]
    for method, path in (("post", f"observations/{o['id']}/confirm"), ("post", f"observations/{arch['id']}/restore"),
                         ("post", "profile/schedule/confirm"), ("delete", "profile/schedule")):
        assert getattr(client, method)(f"/v1/users/{a}/memory/{path}", headers=h(tok["B"])).status_code == 403
        assert getattr(client, method)(f"{base}/{path}", headers=h(tok["B"])).status_code == 404   # B's own: none
        assert getattr(client, method)(f"/v1/users/{a}/memory/{path}", headers=h(tok["C"])).status_code == 403
        assert getattr(client, method)(f"{base}/{path}", headers=h(tok["A_ro"])).status_code == 403
    assert client.get(f"/v1/users/{a}/memory", headers=h(tok["C"])).status_code == 403

    # A: 「还准确」 -> not stale any more
    assert client.post(f"{base}/observations/{o['id']}/confirm", headers=h(tok["A"])).status_code == 200
    body = client.get(base, headers=h(tok["A"])).json()
    assert next(x for x in body["observations"] if x["id"] == o["id"])["stale"] is False
    assert client.post(f"{base}/profile/schedule/confirm", headers=h(tok["A"])).status_code == 200
    assert client.post(f"{base}/profile/nope/confirm", headers=h(tok["A"])).status_code == 404
    # restore, then the archived list is empty
    assert client.post(f"{base}/observations/{arch['id']}/restore", headers=h(tok["A"])).status_code == 200
    assert client.post(f"{base}/observations/{arch['id']}/restore", headers=h(tok["A"])).status_code == 404
    assert client.get(base, headers=h(tok["A"])).json()["archived"]["observations"] == []
    # delete a profile item: a real delete
    assert client.delete(f"{base}/profile/schedule", headers=h(tok["A"])).status_code == 204
    assert client.delete(f"{base}/profile/schedule", headers=h(tok["A"])).status_code == 404
    assert conn.execute("SELECT 1 FROM assistant_profile WHERE user_id = %s AND key = 'schedule'", (a,)).fetchone() is None
    # English error details
    r = client.post(f"{base}/profile/nope/confirm", headers={**h(tok["A"]), "X-Body-Lang": "en"})
    assert r.json()["detail"] == "No such profile card item (or not yours)"

    # flag off: the new endpoints are 404 and GET has no `archived`, as before #113
    _flag(conn, "off")
    try:
        assert client.post(f"{base}/observations/{o['id']}/confirm", headers=h(tok["A"])).status_code == 404
        assert client.delete(f"{base}/profile/goals", headers=h(tok["A"])).status_code == 404
        assert "archived" not in client.get(base, headers=h(tok["A"])).json()
    finally:
        _flag(conn, "all")


def test_confirmed_shows_the_later_of_confirmed_and_updated(conn, users):
    """Writers outside memory.py (the diet profile) only bump updated_at; that still counts as confirmed."""
    a = users["A"]
    memory.update_profile(conn, a, "basics", "示例")
    conn.execute("UPDATE assistant_profile SET confirmed_at = now() - interval '400 days', updated_at = now() - interval '2 days' "
                 "WHERE user_id = %s AND key = 'basics'", (a,))
    c = next(x for x in memory.get_memory(conn, a)["profile_card"] if x["key"] == "basics")
    assert c["confirmed_days"] == 2 and c["stale"] is False
    assert abs(c["confirmed"] - (datetime.now(timezone.utc) - timedelta(days=2))) < timedelta(minutes=5)
