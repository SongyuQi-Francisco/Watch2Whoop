"""Long-term memory hygiene, phase 2 (issue #113): review candidates, the apply_memory_changes batch (merge / update / archive /
confirm / promote / restore, guardrails, idempotency, audit and rollback), the briefing reminder, the nightly check and cleanup,
and cross-user isolation.

Needs Postgres: uses a separate test database BODY_TEST_DB_NAME (default body_test_memory_review), dropped and recreated
on every run. All texts are made-up examples.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.ai import params as ai_params, playbooks
from body_engine.assistant import briefing, memory, memory_params as mp, memory_review
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_memory_review")
SETTINGS = Path(__file__).resolve().parents[2] / "assistant" / ".claude" / "settings.json"


# ---------------------------------------------------------------- no database needed
def test_tools_confirm_and_playbook():
    assert "apply_memory_changes" in ai_params.ALWAYS_CONFIRM                     # one confirmation card for the whole batch
    ask = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["ask"]
    assert "mcp__body-engine__apply_memory_changes" in ask
    review, apply = TOOLS_BY_NAME["get_memory_review"], TOOLS_BY_NAME["apply_memory_changes"]
    assert not review.writes and apply.writes and review.group == apply.group == "memory"
    assert "user" not in apply.input_schema["properties"]                            # only ever the caller's own memory
    assert set(apply.input_schema["properties"]["items"]["items"]["properties"]["action"]["enum"]) == set(memory_review.ACTIONS)
    assert "memory_review" in playbooks.NAMES and playbooks.FALLBACK["memory_review"]
    assert set(mp.PROTECTED_TOPICS) <= set(memory.STANDARD_KEYS)


def test_ref_parsing():
    assert memory_review.REF_RE.match("n12") and memory_review.REF_RE.match("k:goals")
    for bad in ("12", "n", "k:", "N12", "k:Goals", "n12; DROP"):
        assert not memory_review.REF_RE.match(bad)


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
    """A owns the memory; B has a memory share from A; C has none. The flag memory_hygiene is on for everyone."""
    ids = {n: conn.execute("INSERT INTO users (display_name, timezone) VALUES (%s, 'Australia/Sydney') RETURNING id",
                           (n,)).fetchone()["id"] for n in "ABC"}
    conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["memory"]))
    flags.set_flag(conn, "memory_hygiene", "all", actor="test")
    conn.commit()
    flags.invalidate()
    briefing.clear()
    return ids


def _note(conn, uid, text, days=0, origin=None, topic=None):
    """A lasting note, written `days` ago; origin None = unknown (how AI writes are stored)."""
    n = memory.add_observation(conn, uid, text, origin=origin, topic=topic)
    conn.execute("UPDATE assistant_observations SET confirmed_at = now() - make_interval(days => %s) WHERE id = %s",
                 (days, n["id"]))
    return n["id"]


def _card(conn, uid, key, value, days=0, origin=None):
    memory.update_profile(conn, uid, key, value, "示例名称" if key not in memory.STANDARD_KEYS else None, origin=origin)
    conn.execute("UPDATE assistant_profile SET confirmed_at = now() - make_interval(days => %s), "
                 "updated_at = now() - make_interval(days => %s) WHERE user_id = %s AND key = %s", (days, days, uid, key))


def _apply(conn, uid, *items):
    return run_tool(conn, uid, "butler", "apply_memory_changes", {"items": [
        {"into": None, "text": None, "user_ok": False, **it} for it in items]})


def _status(conn, nid):
    return conn.execute("SELECT status, archived_reason, merged_into FROM assistant_observations WHERE id = %s", (nid,)).fetchone()


def test_candidates(conn, users):
    a = users["A"]
    e1 = _note(conn, a, "每周三晚上打篮球")
    e2 = _note(conn, a, "每周三晚上打篮球！")                 # the same text apart from punctuation
    n1 = _note(conn, a, "喜欢在周日长跑")
    n2 = _note(conn, a, "周日喜欢长跑")                       # a rewording, not exact
    old = _note(conn, a, "早餐吃燕麦", days=400, topic="diet_preferences")
    _note(conn, a, "午餐常吃沙拉", topic="diet_preferences")
    _card(conn, a, "current_plan", "示例：旧计划", days=200)
    _card(conn, a, "diet_day_plan", "示例：由饮食档案管理", days=900)   # other features own it: never a candidate
    c = memory_review.candidates(conn, a)
    groups = {g["keep"]: g for g in c["duplicates"]}
    exact = next(g for g in c["duplicates"] if g["exact"])
    assert {n["ref"] for n in exact["notes"]} == {f"n{e1}", f"n{e2}"} and exact["keep"] in {f"n{e1}", f"n{e2}"}
    loose = next(g for g in c["duplicates"] if not g["exact"])
    assert {n["ref"] for n in loose["notes"]} == {f"n{n1}", f"n{n2}"} and len(groups) == 2
    stale = {s["ref"]: s for s in c["stale"]}
    assert stale[f"n{old}"]["long_stale"] is True and stale["k:current_plan"]["days"] >= 190
    assert "k:diet_day_plan" not in stale and f"n{e1}" not in stale
    assert any(t["topic"] == "diet_preferences" and len(t["entries"]) == 2 for t in c["same_topic"])
    assert c["count"] == len(c["duplicates"]) + len(c["stale"]) and c["size"]["near_full"] is False
    assert all(len(e["text"]) <= mp.REVIEW_TEXT_CHARS + 1 for t in c["same_topic"] for e in t["entries"])


def test_apply_merge_archive_confirm_restore_and_audit(conn, users):
    a = users["A"]
    x1 = _note(conn, a, "每周三晚上打篮球")
    x2 = _note(conn, a, "每周三晚上打篮球！")
    r1 = _note(conn, a, "喜欢在周日长跑", origin="ai_inferred")
    r2 = _note(conn, a, "周日喜欢长跑", origin="ai_inferred")
    stale = _note(conn, a, "目标是三个月减两公斤", days=300, origin="ai_inferred")
    keep_it = _note(conn, a, "喜欢晨练", days=300, origin="ai_inferred")

    res = _apply(conn, a,
                 {"action": "merge", "ref": f"n{x2}", "into": f"n{x1}", "reason": "重复"},                    # exact: no user_ok needed
                 {"action": "merge", "ref": f"n{r2}", "into": f"n{r1}", "text": "周日喜欢长跑（示例）", "reason": "同一件事"},
                 {"action": "archive", "ref": f"n{stale}", "reason": "过时"},
                 {"action": "confirm", "ref": f"n{keep_it}", "reason": "用户说还准确"})
    assert [r["status"] for r in res["results"]] == ["ok"] * 4 and res["errors"] == 0
    assert _status(conn, x2) == {"status": "archived", "archived_reason": "重复", "merged_into": x1}
    assert _status(conn, r2)["merged_into"] == r1 and _status(conn, stale)["status"] == "archived"
    m = memory.get_memory(conn, a, archived=True)
    assert {o["id"] for o in m["observations"]} == {x1, r1, keep_it}
    assert next(o for o in m["observations"] if o["id"] == r1)["text"] == "周日喜欢长跑（示例）"
    assert next(o for o in m["observations"] if o["id"] == keep_it)["confirmed_days"] == 0
    assert {o["id"] for o in m["archived"]["observations"]} == {x2, r2, stale}

    # the same batch again changes nothing more
    again = _apply(conn, a,
                   {"action": "merge", "ref": f"n{x2}", "into": f"n{x1}", "reason": "重复"},
                   {"action": "archive", "ref": f"n{stale}", "reason": "过时"})
    assert [r["status"] for r in again["results"]] == ["noop", "noop"]

    # audit: one log row per change, and rollback_write puts each back
    logs = {r["target"]: r for r in conn.execute("SELECT * FROM ai_write_log WHERE user_id = %s AND kind = 'memory_state'", (a,))
            if r["action"] != "rollback"}
    assert logs[f"n{stale}"]["before"]["status"] == "active" and logs[f"n{stale}"]["after"]["status"] == "archived"
    run_tool(conn, a, "butler", "rollback_write", {"log_id": logs[f"n{stale}"]["id"]})
    assert _status(conn, stale)["status"] == "active"
    run_tool(conn, a, "butler", "rollback_write", {"log_id": logs[f"n{x2}"]["id"]})
    assert _status(conn, x2)["status"] == "active" and _status(conn, x2)["merged_into"] is None
    # restore brings r2 back; the earlier merge log of r2 can no longer be rolled back over that
    back = _apply(conn, a, {"action": "restore", "ref": f"n{r2}", "reason": "用户要回来"},
                  {"action": "restore", "ref": f"n{r2}", "reason": "再来一次"})
    assert [r["status"] for r in back["results"]] == ["ok", "noop"] and _status(conn, r2)["status"] == "active"
    with pytest.raises(ValueError, match="又变过"):
        run_tool(conn, a, "butler", "rollback_write", {"log_id": logs[f"n{r2}"]["id"]})


def test_guardrails(conn, users):
    a = users["A"]
    own = _note(conn, a, "我喜欢游泳", origin="user_said")
    unknown = _note(conn, a, "常去健身房")                                 # AI wrote it: origin unknown, treated as the user's
    inferred = _note(conn, a, "可能喜欢爬坡", origin="ai_inferred")
    injury = _note(conn, a, "左膝盖疼，不做深蹲", origin="ai_inferred")      # protected topic whatever the origin
    other = _note(conn, a, "常去游泳馆")
    res = _apply(conn, a,
                 {"action": "archive", "ref": f"n{own}", "reason": "过时"},
                 {"action": "archive", "ref": f"n{unknown}", "reason": "过时"},
                 {"action": "archive", "ref": f"n{injury}", "reason": "过时"},
                 {"action": "archive", "ref": f"n{inferred}", "reason": "过时"},
                 {"action": "update", "ref": f"n{own}", "text": "我喜欢蛙泳（示例）", "reason": "改"},
                 {"action": "merge", "ref": f"n{own}", "into": f"n{other}", "reason": "合并"})
    assert [r["status"] for r in res["results"]] == ["error", "error", "error", "ok", "error", "error"]
    assert all("user_ok" in r["msg"] for r in res["results"] if r["status"] == "error")
    assert _status(conn, own)["status"] == "active" and _status(conn, injury)["status"] == "active"
    # with the user's explicit say-so they go through
    ok = _apply(conn, a, {"action": "archive", "ref": f"n{own}", "reason": "用户说不再适用", "user_ok": True},
                {"action": "update", "ref": f"n{injury}", "text": "左膝盖已好，可以深蹲（示例）", "reason": "用户说好了", "user_ok": True})
    assert [r["status"] for r in ok["results"]] == ["ok", "ok"]
    assert memory.get_memory(conn, a)["observations"] and any(
        "已好" in o["text"] for o in memory.get_memory(conn, a)["observations"])
    # a card item the user wrote needs it too; one the diet profile manages cannot be touched at all
    memory.update_profile(conn, a, "goals", "示例：增肌", source="user")
    memory.update_profile(conn, a, "diet_day_plan", "示例", label="一天怎么吃")
    bad = _apply(conn, a, {"action": "archive", "ref": "k:goals", "reason": "过时"},
                 {"action": "archive", "ref": "k:diet_day_plan", "reason": "过时"},
                 {"action": "confirm", "ref": "n999999", "reason": "x"},
                 {"action": "archive", "ref": "oops", "reason": "x"},
                 {"action": "archive", "ref": f"n{other}", "reason": ""})
    assert [r["status"] for r in bad["results"]] == ["error"] * 5
    assert "别的功能管理" in bad["results"][1]["msg"] and "不存在" in bad["results"][2]["msg"]
    with pytest.raises(ValueError, match="一次最多"):
        _apply(conn, a, *[{"action": "confirm", "ref": f"n{other}", "reason": "x"}] * (mp.APPLY_MAX_ITEMS + 1))


def test_update_and_promote(conn, users):
    a = users["A"]
    n = _note(conn, a, "喜欢爬坡有氧", origin="ai_inferred", days=100)
    p = _note(conn, a, "每周练五天", origin="ai_inferred")
    _card(conn, a, "training_preferences", "示例：家里有健身房", origin="ai_inferred")
    memory.update_profile(conn, a, "goals", "示例：增肌", source="user")
    res = _apply(conn, a,
                 {"action": "update", "ref": f"n{n}", "text": "爬坡有氧，每次 30 分钟（示例）", "reason": "补充"},
                 {"action": "update", "ref": f"n{n}", "text": "爬坡有氧，每次 30 分钟（示例）", "reason": "补充"},
                 {"action": "promote", "ref": f"n{p}", "into": "k:training_preferences",
                  "text": "示例：家里有健身房；每周练五天", "reason": "稳定规律"},
                 {"action": "promote", "ref": f"n{p}", "into": "k:training_preferences",
                  "text": "示例：家里有健身房；每周练五天", "reason": "稳定规律"},
                 {"action": "promote", "ref": f"n{n}", "into": "k:goals", "text": "示例：增肌 + 爬坡", "reason": "x"})
    assert [r["status"] for r in res["results"]] == ["ok", "noop", "ok", "noop", "error"]     # goals is the user's own words
    card = {c["key"]: c for c in memory.get_memory(conn, a)["profile_card"]}
    assert card["training_preferences"]["value"].endswith("每周练五天") and card["goals"]["value"] == "示例：增肌"
    assert _status(conn, p)["status"] == "archived" and "稳定规律" in _status(conn, p)["archived_reason"]
    assert next(o for o in memory.get_memory(conn, a)["observations"] if o["id"] == n)["confirmed_days"] == 0   # a rewrite confirms
    # the rewrites are logged and can be rolled back like any AI write
    log = conn.execute("SELECT * FROM ai_write_log WHERE user_id = %s AND kind = 'observation' ORDER BY id DESC LIMIT 1", (a,)).fetchone()
    run_tool(conn, a, "butler", "rollback_write", {"log_id": log["id"]})
    assert next(o for o in memory.get_memory(conn, a)["observations"] if o["id"] == n)["text"] == "喜欢爬坡有氧"


def test_one_bad_item_does_not_spoil_the_batch(conn, users):
    a = users["A"]
    good = _note(conn, a, "示例：甲", origin="ai_inferred")
    other = _note(conn, a, "示例：乙", origin="ai_inferred")
    res = _apply(conn, a, {"action": "archive", "ref": "n99999999", "reason": "x"},
                 {"action": "merge", "ref": f"n{good}", "into": f"n{good}", "reason": "x"},
                 {"action": "archive", "ref": f"n{good}", "reason": "过时"},
                 {"action": "merge", "ref": f"n{other}", "into": f"n{good}", "reason": "x"})
    assert [r["status"] for r in res["results"]] == ["error", "error", "ok", "error"]
    assert (res["ok"], res["errors"]) == (1, 3) and _status(conn, good)["status"] == "archived"
    assert _status(conn, other)["status"] == "active"


def test_reminder_state_and_nightly_check(conn, users):
    a = users["A"]
    assert memory_review.reminder_line(conn, a) is None                                  # never checked
    for text in ("示例：想在三月前跑完半程马拉松", "家里养了两只猫", "周末常去海边冲浪"):
        _note(conn, a, text, days=500, origin="ai_inferred", topic="goals")
    out = memory_review.run_all(conn)
    mine = next(r for r in out if r["user_id"] == a)
    assert mine["candidates"] >= 3 and "error" not in mine
    line = memory_review.reminder_line(conn, a)
    assert line and "memory_review" in line and "待复核" in line
    assert briefing._memory(conn, a)["memory_review"] == line
    assert "memory_review" not in briefing._memory(conn, users["C"])                    # others have their own state
    assert memory_review.run_all(conn, only_user=a)[0]["candidates"] == mine["candidates"]   # idempotent

    # a finished review (also with nothing to change) silences it for REVIEW_INTERVAL_DAYS
    _apply(conn, a)
    assert memory_review.reminder_line(conn, a) is None
    conn.execute("UPDATE memory_review_state SET last_reviewed_at = now() - make_interval(days => %s) WHERE user_id = %s",
                 (mp.REVIEW_INTERVAL_DAYS + 1, a))
    assert memory_review.reminder_line(conn, a)
    # too few candidates: no reminder, unless the memory is nearly full
    conn.execute("UPDATE memory_review_state SET candidates = 1 WHERE user_id = %s", (a,))
    assert memory_review.reminder_line(conn, a) is None
    conn.execute("UPDATE memory_review_state SET near_full = TRUE WHERE user_id = %s", (a,))
    assert "快满" in memory_review.reminder_line(conn, a)
    # the flag is the kill switch
    flags.set_flag(conn, "memory_hygiene", "off", actor="test")
    conn.commit()
    flags.invalidate()
    assert memory_review.reminder_line(conn, a) is None and all(r["user_id"] != a for r in memory_review.run_all(conn))


def test_near_full(conn, users, monkeypatch):
    a = users["A"]
    monkeypatch.setattr(mp, "PROMPT_FACTS_MAX", 5)
    for i in range(4):
        _note(conn, a, ("示例：一二三四五", "喜欢爬山看日出", "对虾过敏要小心", "每晚读书半小时")[i])
    assert memory_review.candidates(conn, a)["size"] == {"notes": 4, "limit": 5, "card_items": 0, "near_full": True}


def test_archived_entries_are_purged_after_the_grace_period(conn, users):
    a, b = users["A"], users["B"]
    old, fresh, active = (_note(conn, a, f"示例：{t}") for t in ("很久前归档", "刚归档", "没归档"))
    theirs = _note(conn, b, "示例：B 的很久前归档")
    for nid, days in ((old, mp.ARCHIVE_PURGE_DAYS + 1), (fresh, mp.ARCHIVE_PURGE_DAYS - 1), (theirs, mp.ARCHIVE_PURGE_DAYS + 1)):
        conn.execute("UPDATE assistant_observations SET status = 'archived', archived_reason = 'x', "
                     "archived_at = now() - make_interval(days => %s) WHERE id = %s", (days, nid))
    memory.update_profile(conn, a, "current_plan", "示例")
    conn.execute("UPDATE assistant_profile SET status = 'archived', archived_reason = 'x', "
                 "archived_at = now() - make_interval(days => %s) WHERE user_id = %s", (mp.ARCHIVE_PURGE_DAYS + 5, a))
    assert memory_review.purge_archived(conn, a) == {"observations": 1, "profile": 1}
    assert memory_review.purge_archived(conn, a) == {"observations": 0, "profile": 0}
    left = {r["id"] for r in conn.execute("SELECT id FROM assistant_observations WHERE id = ANY(%s)", ([old, fresh, active, theirs],))}
    assert left == {fresh, active, theirs}                       # only A's, only past the grace period; B's is B's own job


def test_cross_user_isolation(conn, users):
    a, b, c = users["A"], users["B"], users["C"]
    mine = _note(conn, a, "示例：A 的笔记", origin="ai_inferred")
    _note(conn, a, "示例：A 的另一条", days=500, origin="ai_inferred")
    _card(conn, a, "goals", "示例：A 的目标", origin="ai_inferred")
    for viewer in (b, c):
        rv = run_tool(conn, viewer, "butler", "get_memory_review", {})
        assert "A 的" not in json.dumps(rv, ensure_ascii=False) and rv["count"] == 0         # their own memory, which is empty
        res = _apply(conn, viewer, {"action": "archive", "ref": f"n{mine}", "reason": "x"},
                     {"action": "archive", "ref": "k:goals", "reason": "x"},
                     {"action": "confirm", "ref": f"n{mine}", "reason": "x"})
        assert [r["status"] for r in res["results"]] == ["error"] * 3 and "不存在" in res["results"][0]["msg"]
    # B has a memory share from A: can read A's review, never write it
    seen = run_tool(conn, b, "butler", "get_memory_review", {"user": str(a)})
    assert seen["count"] >= 1
    with pytest.raises(PermissionError):
        run_tool(conn, b, "butler", "apply_memory_changes", {"user": str(a), "items": []})
    with pytest.raises(PermissionError):
        run_tool(conn, c, "butler", "get_memory_review", {"user": str(a)})
    assert _status(conn, mine)["status"] == "active"
    # B's nightly state and A's are separate rows
    memory_review.run_all(conn)
    states = {r["user_id"] for r in conn.execute("SELECT user_id FROM memory_review_state")}
    assert {a, b, c} <= states
    assert datetime.now(timezone.utc) - memory_review.get_state(conn, a)["checked_at"] < timedelta(minutes=5)
