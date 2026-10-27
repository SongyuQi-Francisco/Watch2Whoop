"""What one in-page chat call actually sends to the model, measured on a scripted conversation (issue #78, #116).

A five-message conversation on the Today page (briefing, a month of sleep, HRV / resting heart rate, the week's training,
the week's scores) is run through the real chat loop (ai/chat.py) twice: with the owner's own AI (Anthropic, strict
tools) and with the fallback AI (DeepSeek, OpenAI-compatible), every rollout flag as in production. The adapters are the
real classes with stream_turn replaced by a script that records what would go on the wire (system, tool definitions,
history): no network, no paid API.
Per call the parts are measured in characters: tool definitions, system prompt, history (and the tool results in it).
BODY_TOKEN_DUMP=<file.json> writes every recorded call, so the parts can be counted with a real tokenizer outside the
test (docs/ai-assistant-plan.md §9.23 has the counts).

Uses its own test database BODY_TEST_DB_NAME (default body_test_token_economy), dropped and recreated on every run;
skipped without Postgres. Never the production DB.
"""
import copy
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).parent))
import test_tool_groups as TG  # noqa: E402  (the dense synthetic year and the read-tool arguments)

from body_engine.ai import adapters, cards, chat, context, fallback, parity, prompt, scope_guard  # noqa: E402
from body_engine.ai import params as P  # noqa: E402
from body_engine.ai.adapters.base import TurnResult  # noqa: E402
from body_engine.assistant.tools import TOOLS_BY_NAME  # noqa: E402
from body_engine.config import Settings  # noqa: E402
from body_engine.db import migrate  # noqa: E402
from body_engine.rollout import flags  # noqa: E402

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_token_economy")
TZ = TG.TZ

# (user message, tool the model calls first, answer after the result)
SCRIPT = [
    ("今天怎么样", "get_briefing", "恢复：61（黄），HRV 46ms 低于上周，静息心率 52 正常。\n睡眠：7.2 小时，睡眠债 40 分钟。\n今天：照常练，强度降一档。"),
    ("最近一个月睡眠怎么样", "get_sleep", "最近 30 天平均 7.2 小时，入睡多在 00:30–01:20，比你的目标晚 40 分钟左右；深睡稳定在 80 分钟。"),
    ("HRV 和静息心率呢", "get_daily_metrics", "HRV 30 天均值 48ms，最近一周 46，略低；静息心率 52–55，稳定。没有明显异常。"),
    ("这周训练怎么排", "get_training_context", "这周已练 3 次（2 次力量、1 次篮球），恢复偏黄：周四练下肢，周五休息，周六篮球照打。"),
    ("这周哪天恢复最差", "get_scores", "周二最差：恢复 41（黄），前一晚只睡了 5.8 小时；其余几天在 55–68 之间。"),
]


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture(scope="module")
def heavy(dsn):
    uid = TG.heavy.__wrapped__(dsn)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for k, label, v in (("health_constraints", "健康约束", "乳糖不耐：少喝牛奶，奶制品换无乳糖的"),
                            ("injuries", "伤病", "左膝旧伤：深蹲不过 90 度"),
                            ("communication", "沟通方式", "中文，简短，先给结论")):
            conn.execute("INSERT INTO assistant_profile (user_id, key, label, value, source) VALUES (%s,%s,%s,%s,'seed')",
                         (uid, k, label, v))
        for key in (cards.FLAG, cards.FLAG_V2, cards.FLAG_PRODUCT, parity.FLAG, scope_guard.FLAG):   # as in production
            flags.set_flag(conn, key, "all", actor="test")
        conn.execute("""INSERT INTO ai_providers (user_id, kind, display_name, model, is_default)
                        VALUES (%s, 'anthropic_api', 'mine', 'claude-test', true)""", (uid,))
        conn.commit()
    return uid


def _recorder(kind: str, script: list[dict], calls: list[dict]):
    """The real adapter class of `kind`, with stream_turn replaced: records the wire parts, plays the script."""
    base = adapters.ADAPTERS[kind]()

    class Recorder(base):
        def stream_turn(self, system, history, tools):
            calls.append({"system": system, "tools": self.tool_defs(tools), "history": copy.deepcopy(history)})
            turn = script.pop(0) if script else {"text": "好的。"}
            if turn.get("text"):
                yield "text", turn["text"]
            yield "result", TurnResult(text=turn.get("text") or "", tool_calls=turn.get("tool_calls") or [],
                                       native=None, usage={"input_tokens": 1000, "output_tokens": 100},
                                       stop="tool_use" if turn.get("tool_calls") else "end")
    return Recorder(fallback.fp.MODEL if kind == "system_fallback" else "claude-test", "k", None)


def _script(today) -> list[dict]:
    out = []
    for i, (_, tool, answer) in enumerate(SCRIPT):
        args = TG._args_for(TOOLS_BY_NAME[tool], today)
        if tool == "get_sleep":
            args["start"] = (today - timedelta(days=29)).isoformat()
        out += [{"tool_calls": [{"id": f"c{i}", "name": tool, "args": args}]}, {"text": answer}]
    return out


def _converse(conn, uid: int, kind: str, monkeypatch) -> list[dict]:
    calls: list[dict] = []
    script = _script(datetime.now(TZ).date())
    adapter = _recorder(kind, script, calls)
    if kind == "system_fallback":
        monkeypatch.setattr(chat, "resolve_provider", lambda *a, **k: fallback.provider())
        monkeypatch.setattr(fallback, "precheck", lambda *a, **k: (0.0, 3.0))
        monkeypatch.setattr(fallback, "summary", lambda *a, **k: {"spent_cny": 0.0, "quota_cny": 3.0, "remaining_cny": 3.0,
                                                                   "reset_on": None, "message": None})
    monkeypatch.setattr(chat, "make_adapter", lambda *a, **k: adapter)
    monkeypatch.setattr(chat, "check_budget", lambda *a, **k: (0.0, 5.0))
    viewer = {"id": uid, "token_scopes": ["read", "write"]}
    conv_id = None
    for message, _, _ in SCRIPT:
        events = list(chat.chat_events(conn, viewer, message, None, conv_id, page="today"))
        conv_id = next(e["data"]["id"] for e in events if e["event"] == "conversation")
        assert not [e for e in events if e["event"] == "error"], events
    return calls


def _parts(call: dict) -> dict:
    results = [b for m in call["history"] for b in m["content"] if b.get("type") == "tool_result"]
    return {"tools": len(json.dumps(call["tools"], ensure_ascii=False)),
            "system": len(call["system"]),
            "history": len(json.dumps([m["content"] for m in call["history"]], ensure_ascii=False, default=str)),
            "results": sum(len(b["content"]) for b in results)}


def _report(name: str, calls: list[dict]) -> dict:
    parts = [_parts(c) for c in calls]
    total = {k: sum(p[k] for p in parts) for k in parts[0]}
    print(f"\n{name}: {len(calls)} calls; last call {parts[-1]}; conversation total {total}")
    if os.environ.get("BODY_TOKEN_DUMP"):
        path = Path(os.environ["BODY_TOKEN_DUMP"])
        dump = json.loads(path.read_text()) if path.exists() else {}
        dump[name] = [{"system": c["system"], "tools": json.dumps(c["tools"], ensure_ascii=False),
                       "history": json.dumps([m["content"] for m in c["history"]], ensure_ascii=False, default=str),
                       "results": [b["content"] for m in c["history"] for b in m["content"] if b.get("type") == "tool_result"]}
                      for c in calls]
        path.write_text(json.dumps(dump, ensure_ascii=False))
    return {"parts": parts, "total": total}


# Whole-conversation input (all 10 calls, characters) after #78's changes, with ~5% headroom: a change that makes every
# call carry more (a longer persona, bigger tool definitions, a chattier tool result) shows up here. Before #78:
# anthropic_api 699,144 (tools 428,110 / system 174,140 / history 96,894), system_fallback 494,098.
CONVERSATION_BUDGET_CHARS = {"anthropic_api": 690_000, "system_fallback": 445_000}


def _results(call: dict) -> dict:
    return {b["id"]: b["content"] for m in call["history"] for b in m["content"] if b.get("type") == "tool_result"}


@pytest.mark.parametrize("kind", ["anthropic_api", "system_fallback"])
def test_conversation_composition(dsn, heavy, kind, monkeypatch):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        calls = _converse(conn, heavy, kind, monkeypatch)
        conv = conn.execute("SELECT * FROM ai_conversations WHERE user_id = %s ORDER BY id DESC LIMIT 1", (heavy,)).fetchone()
    assert len(calls) == 2 * len(SCRIPT)
    rep = _report(kind, calls)
    assert sum(rep["total"][k] for k in ("tools", "system", "history")) <= CONVERSATION_BUDGET_CHARS[kind], rep["total"]

    # Nobody shares data with this user: the conversation is own_only and no tool carries a `user` parameter
    assert conv["own_only"] is True
    params = [d.get("input_schema") or d.get("parameters") or d.get("function", {}).get("parameters") for d in calls[0]["tools"]]
    assert all("user" not in (p or {}).get("properties", {}) for p in params)
    if kind == "anthropic_api":   # strict tools keep the strict form; the others get the relaxed one
        assert all(p.get("additionalProperties") is False for p in params if p.get("properties"))
    else:
        assert not any("additionalProperties" in p for p in params)

    # Fixed text first: the per-call additions sit before this user's part; the marker never goes out
    system = calls[0]["system"]
    assert prompt.EXTRAS_MARK not in system and system.index(cards.PROMPT_V2) < system.index("\n## 用户\n")

    # The chat's own briefing leaves out what its system prompt already has
    brief = json.loads(_results(calls[1])["c0"])
    assert "text" not in brief["system_rules"] and "feedback_mode" in brief["system_rules"]
    assert "profile_card" not in json.dumps(brief.get("memory") or {}) and "乳糖不耐" not in json.dumps(brief, ensure_ascii=False)

    # Older turns' tool results: shortened for the fallback only, never the briefing / scene context; the last turns whole
    first, last = _results(calls[1]), _results(calls[-1])
    assert last["c0"] == first["c0"] and last["c3"] == _results(calls[7])["c3"]   # get_briefing, get_training_context
    shortened = [cid for cid in ("c1", "c2") if context.OLD_RESULT_NOTE.split("{")[0] in last[cid]]
    if kind == "system_fallback":
        assert shortened == ["c1", "c2"] and all(len(last[c]) < P.HISTORY_RESULT_CHARS + 80 for c in shortened)
        assert last["c4"] == _results(calls[-1])["c4"] and context.OLD_RESULT_NOTE.split("{")[0] not in last["c4"]
    else:   # appended history only: what was sent once is sent again unchanged (preserved thinking, prompt cache)
        assert shortened == [] and all(last[c] == _results(calls[2 * i + 1])[c] for i, c in enumerate(("c0", "c1", "c2", "c3")))


def test_shared_users_keep_the_user_parameter(dsn, heavy):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        other = conn.execute("INSERT INTO users (display_name, timezone) VALUES ('S', 'Australia/Sydney') RETURNING id").fetchone()["id"]
        assert not chat._shared_with(conn, heavy)
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, '{scores}')", (other, heavy))
        assert chat._shared_with(conn, heavy)
        provider = {"kind": "anthropic_api", "id": None}
        with_user, _ = chat._tool_sets(conn, heavy, provider, own_only=False)
        own, _ = chat._tool_sets(conn, heavy, provider, own_only=True)
        conn.rollback()
    assert "user" in {t.name: t for t in with_user}["get_scores"].input_schema["properties"]
    assert "user" not in {t.name: t for t in own}["get_scores"].input_schema["properties"]
    assert [t.name for t in own] == [t.name for t in with_user]
