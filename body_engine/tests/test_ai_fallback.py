"""Fallback AI (ai/fallback.py): users without their own AI chat with the admin's DeepSeek key, opt-in consent, CNY quota per person per month,
worst-case pre-check, rate limits, balance pause, own-data-only tools, admin status.

Separate test database BODY_TEST_DB_NAME_FALLBACK (default body_test_ai_fallback), dropped and recreated every run; skipped without Postgres.
No paid API: system_fallback (and every other kind) is FakeAdapter; DeepSeek's balance endpoint is monkeypatched. The key is a temp file.

Users: F1, F2 (members, consented to the main form, no AI of their own), A (member with their own anthropic_api provider),
N (member who has not accepted the main consent form), O (owner with the body butler link). F2 shares everything with F1.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth
from body_engine.ai import adapters, cards, context, crypto, fallback, providers
from body_engine.ai import fallback_params as fp
from body_engine.ai import params as ai_params
from body_engine.ai.adapters.fake import FakeAdapter
from body_engine.assistant import toolsets
from body_engine.ai.adapters.openai_api import DeepSeekFallbackAdapter, OpenAICompatAdapter
from body_engine.config import Settings
from body_engine.db import migrate

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_FALLBACK", "body_test_ai_fallback")
DS_KEY = "test-fallback-key-not-real-0000"
ALL_SCOPES = ["profile", "metrics", "sleep", "scores", "events", "memory", "journal", "nutrition", "training",
              "weight", "supplements", "labs", "exercises", "reminders"]

# Just after midnight in Sydney (AEDT, UTC+11): 2026-10-22 00:30 local. Daily limits count from the user's local midnight,
# so tests that put earlier calls at "now - 5 minutes" broke in the first minutes of every Sydney day (CI, 13:0x UTC).
SYDNEY_00_30 = datetime(2026, 10, 21, 13, 30, tzinfo=timezone.utc)


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
def keys(tmp_path_factory):
    d = tmp_path_factory.mktemp("fb")
    mp = pytest.MonkeyPatch()
    mp.setenv("BODY_AI_MASTER_KEY_FILE", str(d / "ai_master_key"))
    crypto.init_master_key()
    (d / "deepseek_api_key").write_text(DS_KEY + "\n")
    mp.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(d / "deepseek_api_key"))
    mp.setenv("BODY_ASSISTANT_URL", "https://claude.ai/code/session_test")
    yield d
    mp.undo()


@pytest.fixture(scope="module")
def world(dsn, keys):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {}
        for n, role, consented in (("O", "owner", False), ("F1", "member", True), ("F2", "member", True),
                                   ("A", "member", True), ("N", "member", False)):
            ids[n] = conn.execute("INSERT INTO users (display_name, role, consent_at, timezone) VALUES (%s,%s,%s,%s) RETURNING id",
                                  (n, role, datetime.now(timezone.utc) if consented else None,
                                   "Australia/Sydney")).fetchone()["id"]
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["F2"], ids["F1"], ALL_SCOPES))
        conn.execute("INSERT INTO assistant_observations (user_id, text, source) VALUES (%s, 'F1 喜欢早上练', 'user')", (ids["F1"],))
    os.environ["BODY_ASSISTANT_USER_ID"] = str(ids["O"])
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn, world):
    mp = pytest.MonkeyPatch()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    with TestClient(api.app, base_url="https://testserver") as c:
        yield c
    mp.undo()
    os.environ.pop("BODY_ASSISTANT_USER_ID", None)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    for kind in ("anthropic_api", "openai_api", "gemini_api", "openai_compatible", "system_fallback"):
        monkeypatch.setitem(adapters.ADAPTERS, kind, lambda: FakeAdapter)
    FakeAdapter.script, FakeAdapter.calls = [], []
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 50.0)


def H(token):
    return {"Authorization": f"Bearer {token}"}


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def db(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def reset(dsn, uid):
    with db(dsn) as c:
        c.execute("DELETE FROM ai_usage WHERE user_id = %s", (uid,))
        c.execute("DELETE FROM ai_fallback_quota WHERE user_id = %s", (uid,))
        c.execute("UPDATE ai_fallback_state SET enabled = true, balance_cny = NULL, balance_checked_at = NULL, balance_error = NULL")


def consent(client, tok):
    v = client.get("/v1/users/me/ai-fallback", headers=H(tok)).json()["consent_version"]
    r = client.post("/v1/users/me/ai-fallback/consent", headers=H(tok), json={"version": v, "agree": True})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- pure
def test_tools_are_own_data_subset():
    names = [t.name for t in fallback.tools()]
    assert set(names) == set(fallback.TOOL_NAMES), set(fallback.TOOL_NAMES) - set(names)
    assert not {"get_ecg_list", "get_cardio", "create_recipe"} & set(names)
    for t in fallback.tools():
        assert "user" not in t.input_schema.get("properties", {}) and "user" not in t.input_schema.get("required", [])
    def size(tools):
        return sum(len(t.description) + len(json.dumps(t.input_schema, ensure_ascii=False)) for t in tools)
    # ~28k with every Today page read tool (issue #58), ~30.5k with the scene tools (#100). Since #99 a call sends only the
    # core part of the whitelist + the page's groups (the rest via find_tools): cap what is sent, and the whole whitelist.
    # 32000 -> 32500 for add_custom_exercise (「让 Jarvis 补进动作库」, #189).
    assert size(fallback.tools()) < 32500, size(fallback.tools())
    sent = max(size(toolsets.for_page(fallback.tools(), page)) for page in [None, *ai_params.PAGE_GROUPS])
    assert sent < 25000, sent


def test_cost_and_estimate():
    u = {"input_tokens": 1_000_000, "cache_read_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert fallback.cost_cny(u) == pytest.approx(fp.PRICE_CACHE_MISS_CNY + fp.PRICE_CACHE_HIT_CNY + fp.PRICE_OUTPUT_CNY)
    est = fallback.estimate_call_cny("x" * 2000, [{"content": [{"type": "text", "text": "y" * 2000}]}], [])
    assert est == pytest.approx((2000 + 2000 + 36) / fp.CHARS_PER_TOKEN * 2e-6 + fp.MAX_OUTPUT_TOKENS * 8e-6, rel=0.05)
    # An image counts as IMAGE_TOKENS, however long its base64 is
    big = {"type": "image", "media_type": "image/jpeg", "data": "A" * 2_000_000}
    with_img = fallback.estimate_call_cny("x", [{"content": [{"type": "text", "text": "hi"}, big]}], [])
    no_img = fallback.estimate_call_cny("x", [{"content": [{"type": "text", "text": "hi"}]}], [])
    assert with_img - no_img == pytest.approx(fp.IMAGE_TOKENS * fp.PRICE_CACHE_MISS_CNY / 1e6, rel=0.01)


def test_compat_adapter_passes_reasoning_back_and_deepseek_disables_thinking():
    a = OpenAICompatAdapter("deepseek-flash", "k", "https://api.deepseek.com")
    hist = [{"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [{"type": "tool_call", "id": "c1", "name": "get_scores", "args": {}}],
             "native": {"reasoning_content": "想一想"}, "native_key": a.key},
            {"role": "tool", "content": [{"type": "tool_result", "id": "c1", "name": "get_scores", "content": "{}"}]}]
    msgs = a._messages("sys", hist)
    assert msgs[2]["reasoning_content"] == "想一想" and msgs[2]["tool_calls"][0]["id"] == "c1"
    other = OpenAICompatAdapter("qwen-plus", "k", "https://x")  # different kind:model -> not passed back
    assert "reasoning_content" not in other._messages("sys", hist)[2]
    d = DeepSeekFallbackAdapter(fp.MODEL, "k")
    assert d.extra_body == {"thinking": {"type": "disabled"}} and d.max_tokens == fp.MAX_OUTPUT_TOKENS
    assert d.base_url == fp.BASE_URL and d.kind == "system_fallback"


def test_compat_adapter_switches_tool_calls_off(monkeypatch):
    """#184: the last call of a message sends the same tools with tool_choice none (prompt cache and history stay valid)."""
    import openai
    sent = []

    class Completions:
        def create(self, **kw):
            sent.append(kw)
            return iter(())

    class Client:
        def __init__(self, **kw):
            self.chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr(openai, "OpenAI", Client)
    tool = fallback.tools()[0]
    hist = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    d = DeepSeekFallbackAdapter(fp.MODEL, "k")
    list(d.stream_turn("sys", hist, [tool]))
    d.no_tool_calls = True
    list(d.stream_turn("sys", hist, [tool]))
    list(d.stream_turn("sys", hist, []))   # no tools at all (history fold): no tool_choice either
    assert "tool_choice" not in sent[0] and sent[1]["tool_choice"] == "none" and sent[1]["tools"] == sent[0]["tools"]
    assert "tool_choice" not in sent[2] and "tools" not in sent[2]


# ---------------------------------------------------------------- status / consent
def test_status_modes(client, world):
    ids, tok = world
    me = client.get("/v1/web/me", headers=H(tok["F1"])).json()["ai"]
    assert me["mode"] == "fallback" and me["consent"] is False and me["reason"] == "fallback_consent"
    assert me["quota_cny"] == fp.MONTHLY_QUOTA_CNY and me["spent_cny"] == 0 and me["reset_on"].endswith("-01")
    assert client.get("/v1/web/me", headers=H(tok["O"])).json()["ai"]["mode"] == "butler"  # owner keeps the butler by default
    assert client.get("/v1/web/me", headers=H(tok["N"])).json()["ai"]["reason"] == "needs_consent"
    view = client.get("/v1/users/me/ai-providers", headers=H(tok["F1"])).json()["fallback"]
    assert view["used"] is True and len(view["points"]) >= 5 and "balance_cny" not in view
    owner_view = client.get("/v1/users/me/ai-providers", headers=H(tok["O"])).json()["fallback"]
    assert "balance_cny" in owner_view and owner_view["used"] is False
    assert client.get(f"/v1/users/{ids['F2']}/ai-fallback", headers=H(tok["F1"])).status_code == 403  # shares do not cover AI


def test_no_key_means_no_fallback(client, world, monkeypatch, keys):
    ids, tok = world
    monkeypatch.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(keys / "missing"))
    assert client.get("/v1/web/me", headers=H(tok["F1"])).json()["ai"]["mode"] == "none"
    assert client.get("/v1/users/me/ai-providers", headers=H(tok["F1"])).json()["fallback"] is None


def test_chat_needs_fallback_consent_then_works(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F1"])
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "今天怎么样"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "fallback_consent"
    assert not FakeAdapter.calls
    bad = client.post("/v1/users/me/ai-fallback/consent", headers=H(tok["F1"]), json={"version": "old", "agree": True})
    assert bad.status_code == 409
    consent(client, tok["F1"])
    FakeAdapter.script = [{"text": "恢复|不错", "usage": {"input_tokens": 1000, "cache_read_tokens": 9000, "output_tokens": 200}}]
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "今天怎么样"})
    assert r.status_code == 200, r.text
    evs = sse(r.text)
    conv = next(d for e, d in evs if e == "conversation")
    assert conv["provider"] == "system_fallback" and conv["quota"]["quota_cny"] == fp.MONTHLY_QUOTA_CNY
    call = FakeAdapter.calls[0]
    assert call["api_key"] == DS_KEY and call["model"] == fp.MODEL and call["base_url"] == fp.BASE_URL
    assert call["system"].startswith("## 根规则 v3") and "# 身体管家（精简版）" in call["system"]   # root rules first
    assert "F1 喜欢早上练" in call["system"]
    # core ∩ whitelist + the meta tools (no page sent); the rest of the whitelist via find_tools (#99)
    assert call["tools"] == [t.name for t in toolsets.for_page(fallback.tools(), None)]
    assert set(call["tools"]) - set(toolsets.META_BY_NAME) < set(fallback.TOOL_NAMES)
    usage = next(d for e, d in evs if e == "usage")
    expect = (1000 * 2 + 9000 * 0.04 + 200 * 8) / 1e6
    assert usage["currency"] == "CNY" and usage["cost_cny"] == pytest.approx(expect, abs=1e-4)
    assert usage["remaining_cny"] == pytest.approx(fp.MONTHLY_QUOTA_CNY - expect, abs=1e-4)
    with db(dsn) as c:
        row = c.execute("SELECT * FROM ai_usage WHERE user_id = %s", (ids["F1"],)).fetchone()
        assert row["kind"] == "system_fallback" and row["provider_id"] is None
        assert float(row["cost_usd"]) == 0 and float(row["cost_cny"]) == pytest.approx(expect, abs=1e-6)
        assert row["cache_read_tokens"] == 9000 and row["input_tokens"] == 1000
        assert c.execute("SELECT count(*) AS n FROM ai_fallback_consents WHERE user_id = %s",
                         (ids["F1"],)).fetchone()["n"] == 1
    assert providers.month_cost(psycopg.connect(dsn, row_factory=dict_row), ids["F1"]) == 0  # not the user's own budget
    me = client.get("/v1/web/me", headers=H(tok["F1"])).json()["ai"]
    assert me["available"] and me["spent_cny"] == pytest.approx(expect, abs=1e-4)


def test_tools_read_only_own_data(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F1"])
    FakeAdapter.script = [
        {"tool_calls": [{"id": "t1", "name": "get_profile", "args": {"user": str(ids["F2"])}},
                        {"id": "t2", "name": "get_ecg_list", "args": {}}]},
        {"text": "好"}]
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "看看 F2"})
    assert r.status_code == 200, r.text
    results = FakeAdapter.calls[1]["history"][-1]["content"]
    prof = next(b for b in results if b["id"] == "t1")
    assert '"F1"' in prof["content"] and '"F2"' not in prof["content"]  # forced to me even though F2 shares with F1
    assert next(b for b in results if b["id"] == "t2")["is_error"]  # not in the fallback tool set
    with db(dsn) as c:
        a = c.execute("SELECT channel, args, model FROM assistant_audit WHERE user_id = %s AND tool = 'get_profile' "
                      "ORDER BY id DESC LIMIT 1", (ids["F1"],)).fetchone()
        assert a["channel"] == "api:system_fallback" and a["args"]["user"] == "me" and a["model"] == fp.MODEL



def test_meta_tools_stay_inside_the_whitelist(client, world, dsn):
    """#99: find_tools / run_read_tool give the fallback nothing beyond its whitelist (no ECG), its own schemas (no `user`),
    and reads through run_read_tool are still forced to the caller's own data."""
    ids, tok = world
    reset(dsn, ids["F1"])
    FakeAdapter.script = [
        {"tool_calls": [{"id": "m1", "name": "find_tools", "args": {"group": "body", "query": None}},
                        {"id": "m2", "name": "run_read_tool", "args": {"name": "get_ecg_list", "args": {"limit": 5}}},
                        {"id": "m3", "name": "run_read_tool", "args": {"name": "get_profile", "args": {"user": str(ids["F2"])}}}]},
        {"text": "好"}]
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "看看心电图", "page": "trends"})
    assert r.status_code == 200, r.text
    assert FakeAdapter.calls[0]["tools"] == [t.name for t in toolsets.for_page(fallback.tools(), "trends")]
    results = {b["id"]: b for b in FakeAdapter.calls[1]["history"][-1]["content"]}
    assert "get_health_monitor" in results["m1"]["content"] and "get_ecg_list" not in results["m1"]["content"]
    assert results["m2"]["is_error"] and "get_ecg_list" in results["m2"]["content"]
    assert '"F1"' in results["m3"]["content"] and '"F2"' not in results["m3"]["content"]


def test_playbooks_the_fallback_cannot_do(client, world, dsn):
    """#109 review: get_playbook through the fallback AI returns the playbook only when every tool in it is whitelisted."""
    ids, tok = world
    reset(dsn, ids["F1"])
    FakeAdapter.script = [{"tool_calls": [{"id": "p1", "name": "get_playbook", "args": {"name": "plan_shopping"}},
                                          {"id": "p2", "name": "get_playbook", "args": {"name": "plan_training_week"}}]},
                          {"text": "好"}]
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "帮我规划采购"})
    assert r.status_code == 200, r.text
    results = {b["id"]: b["content"] for b in FakeAdapter.calls[1]["history"][-1]["content"]}
    assert "需要用户自己的 AI" in results["p1"] and "get_shopping_context" not in results["p1"]
    assert "get_training_context" in results["p2"]
    assert fallback.TOOL_NAMES.count("set_nutrition_goals") == 0   # weekly_review is for the fallback without widening it

def _rows(dsn, uid, conv_id):
    with db(dsn) as c:
        msgs = c.execute("SELECT role, content, native FROM ai_messages WHERE user_id = %s AND conversation_id = %s ORDER BY id",
                         (uid, conv_id)).fetchall()
        calls = c.execute("SELECT tool, status FROM ai_tool_calls WHERE user_id = %s AND conversation_id = %s",
                          (uid, conv_id)).fetchall()
    return msgs, calls


def test_multi_step_question_is_answered_at_the_round_limit(client, world, dsn):
    """#184: a question that needs more lookups than MAX_ROUNDS used to end with a too_many_rounds error and no answer.
    Now the last call allowed sends the same tools with calls switched off plus WRAP_UP (not stored): the model answers,
    calls it makes anyway are dropped (never run), a notice says why and that 继续 goes on, and 继续 does."""
    from body_engine.ai import chat as chat_mod
    ids, tok = world
    reset(dsn, ids["F1"])
    look = [{"tool_calls": [{"id": f"r{i}", "name": "get_profile", "args": {}}]} for i in range(fp.MAX_ROUNDS - 1)]
    FakeAdapter.script = look + [{"text": "清单：鸡蛋、牛奶；价格还没查", "tool_calls": [
        {"id": "x", "name": "log_weight", "args": {"weight_kg": 80, "date": "2026-10-22"}}]}]
    evs = sse(client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "按饮食结构列采购清单"}).text)
    names = [e for e, _ in evs]
    assert "error" not in names and names[-1] == "done"
    assert len(FakeAdapter.calls) == fp.MAX_ROUNDS
    assert [c["no_tool_calls"] for c in FakeAdapter.calls] == [False] * (fp.MAX_ROUNDS - 1) + [True]
    final = FakeAdapter.calls[-1]
    assert final["tools"] == FakeAdapter.calls[0]["tools"]   # same tools: the prompt cache prefix stays the same
    assert final["history"][-1]["role"] == "user" and final["history"][-1]["content"][0]["text"] == chat_mod.WRAP_UP
    assert final["history"][-2]["role"] == "tool"
    notice = next(d for e, d in evs if e == "notice" and d.get("code") == "round_limit")
    assert f"{fp.MAX_ROUNDS} 轮" in notice["message"] and "继续" in notice["message"]
    conv_id = next(d for e, d in evs if e == "conversation")["id"]
    msgs, calls = _rows(dsn, ids["F1"], conv_id)
    assert [c["tool"] for c in calls] == ["get_profile"] * (fp.MAX_ROUNDS - 1)   # log_weight from the last call never ran
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["native"] is None
    assert [b["type"] for b in msgs[-1]["content"]] == ["text"]
    assert not any(chat_mod.WRAP_UP in json.dumps(m["content"], ensure_ascii=False) for m in msgs)   # never stored
    # 继续: a fresh set of rounds on a valid history (every tool call has its result, the answer is a plain assistant message)
    FakeAdapter.calls, FakeAdapter.script = [], [{"tool_calls": [{"id": "p1", "name": "get_profile", "args": {}}]},
                                                 {"text": "价格：鸡蛋 6 元"}]
    evs = sse(client.post("/v1/assistant/chat", headers=H(tok["F1"]),
                          json={"message": "继续", "conversation_id": conv_id}).text)
    assert [e for e, _ in evs][-1] == "done" and not any(e == "notice" for e, _ in evs)
    assert [c["no_tool_calls"] for c in FakeAdapter.calls] == [False, False]
    hist = FakeAdapter.calls[0]["history"]
    assert [m["role"] for m in hist[-3:]] == ["tool", "assistant", "user"]


def test_message_cost_cap_ends_with_an_answer(client, world, dsn, monkeypatch):
    """#184: the fallback also stops looking things up once one message cost MAX_TURN_CNY; the next call is the answer."""
    ids, tok = world
    reset(dsn, ids["F1"])
    monkeypatch.setattr(fp, "MAX_TURN_CNY", 0.001)
    usage = {"input_tokens": 1000, "output_tokens": 100}   # ≈ ¥0.0028 at the peak price
    FakeAdapter.script = [{"tool_calls": [{"id": "c1", "name": "get_profile", "args": {}}], "usage": usage},
                          {"text": "先这样", "usage": usage}]
    evs = sse(client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "规划一周"}).text)
    assert [c["no_tool_calls"] for c in FakeAdapter.calls] == [False, True]
    assert any(e == "notice" and d.get("code") == "round_limit" for e, d in evs) and evs[-1][0] == "done"


def test_cut_off_tool_call_says_so(client, world, dsn):
    """A tool call cut off at max_tokens is dropped by the adapter (no text): the notice says it was not saved, not "too long"."""
    ids, tok = world
    reset(dsn, ids["F1"])
    FakeAdapter.script = [{"text": "", "stop": "max_tokens"}]
    evs = sse(client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "保存这周计划"}).text)
    assert "没有写成" in next(d for e, d in evs if e == "notice")["message"]


def test_write_needs_confirmation(client, world, dsn, monkeypatch):
    ids, tok = world
    from body_engine.ai import chat as chat_mod
    monkeypatch.setattr(chat_mod, "ALWAYS_CONFIRM", chat_mod.ALWAYS_CONFIRM | {"add_event"})   # exercise the confirm flow with a simple tool
    reset(dsn, ids["F1"])
    FakeAdapter.script = [{"tool_calls": [{"id": "w1", "name": "add_event", "args": {
        "date": "2026-10-18", "category": "pain", "title": "膝盖", "severity": 2, "notes": None, "end_date": None}}]}]
    evs = sse(client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "膝盖疼"}).text)
    pending = next(d for e, d in evs if e == "confirm")["calls"]
    assert pending[0]["tool"] == "add_event"
    FakeAdapter.script = [{"text": "没记"}]
    r = client.post("/v1/assistant/chat/confirm", headers=H(tok["F1"]), json={"call_id": pending[0]["call_id"], "approve": False})
    assert r.status_code == 200 and ("tool", {"name": "add_event", "status": "rejected", "call_id": pending[0]["call_id"]}) in sse(r.text)


def test_quota_used_up_and_admin_override(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F1"])
    with db(dsn) as c:
        c.execute("INSERT INTO ai_usage (user_id, kind, model, cost_cny) VALUES (%s, 'system_fallback', %s, %s)",
                  (ids["F1"], fp.MODEL, fp.MONTHLY_QUOTA_CNY))
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    d = r.json()["detail"]
    assert r.status_code == 402 and d["code"] == "quota" and "用完" in d["message"] and "¥" not in d["message"] and d["reset_on"].endswith("-01")
    me = client.get("/v1/web/me", headers=H(tok["F1"])).json()["ai"]
    assert me["available"] is False and me["reason"] == "quota" and me["remaining_cny"] == 0
    with db(dsn) as c:
        fallback.set_quota(c, ids["F1"], 5)
    assert client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"}).status_code == 200
    with db(dsn) as c:
        fallback.set_quota(c, ids["F1"], 0)
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "fallback_off"
    with db(dsn) as c:
        fallback.set_quota(c, ids["F1"], None)
        assert fallback.quota_cny(c, ids["F1"]) == fp.MONTHLY_QUOTA_CNY


def test_worst_case_estimate_blocks_last_call(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F1"])
    with db(dsn) as c:  # ¥0.005 left: less than one worst-case call (max output alone is ¥0.033)
        c.execute("INSERT INTO ai_usage (user_id, kind, model, cost_cny) VALUES (%s, 'system_fallback', %s, %s)",
                  (ids["F1"], fp.MODEL, fp.MONTHLY_QUOTA_CNY - 0.005))
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    assert r.status_code == 200  # the pre-checks pass (quota not used up), the loop refuses before calling the model
    err = next(d for e, d in sse(r.text) if e == "error")
    assert err["code"] == "quota" and "不够再问一轮" in err["message"] and not FakeAdapter.calls


def test_long_fallback_conversation_folds_and_the_fold_is_billed(client, world, dsn, monkeypatch):
    """#53: the fallback used to stop at 40 messages ("start a new conversation"); now older turns are folded into a
    summary. The fold call costs CNY from the same monthly quota, and when the quota cannot pay, the usual quota error shows."""
    ids, tok = world
    reset(dsn, ids["F1"])
    monkeypatch.setattr(fp, "CONTEXT_MAX_MESSAGES", 4)
    monkeypatch.setattr(fp, "KEEP_TURNS", 1)

    def say(message, conv=None):
        r = client.post("/v1/assistant/chat", headers=H(tok["F1"]),
                        json={"message": message, **({"conversation_id": conv} if conv else {})})
        assert r.status_code == 200, r.text
        return sse(r.text)
    FakeAdapter.script = [{"text": "答0"}]
    conv = say("问0")[0][1]["id"]
    FakeAdapter.script = [{"text": "答1"}]
    say("问1", conv)
    FakeAdapter.calls = []
    FakeAdapter.script = [{"text": "- 摘要", "usage": {"input_tokens": 2000, "output_tokens": 100}}, {"text": "答2"}]
    evs = say("问2", conv)
    assert evs[-1][0] == "done"
    fold, answer = FakeAdapter.calls
    assert fold["system"] == context.FOLD_SYSTEM and fold["tools"] == [] and fold["model"] == fp.MODEL
    assert [m["role"] for m in answer["history"]] == ["user"] and answer["system"].endswith("- 摘要")
    usages = [d for e, d in evs if e == "usage"]
    expect_fold = (2000 * 2 + 100 * 8) / 1e6
    assert usages[0]["cost_cny"] == pytest.approx(expect_fold, abs=1e-4)
    with db(dsn) as c:
        rows = c.execute("""SELECT purpose, cost_cny, cost_usd FROM ai_usage WHERE user_id = %s AND conversation_id = %s
                            ORDER BY id""", (ids["F1"], conv)).fetchall()
        assert [r["purpose"] for r in rows] == ["chat", "chat", "compact", "chat"]
        compact = rows[2]
        assert float(compact["cost_cny"]) == pytest.approx(expect_fold, abs=1e-6) and float(compact["cost_usd"]) == 0
        assert fallback.month_spent_cny(c, ids["F1"]) == pytest.approx(sum(float(r["cost_cny"]) for r in rows), abs=1e-6)
    # Quota almost gone: neither the fold nor the answer is attempted, and the user gets the usual quota message
    with db(dsn) as c:
        c.execute("INSERT INTO ai_usage (user_id, kind, model, cost_cny) VALUES (%s, 'system_fallback', %s, %s)",
                  (ids["F1"], fp.MODEL, fp.MONTHLY_QUOTA_CNY - fallback.month_spent_cny(c, ids["F1"]) - 0.005))
    FakeAdapter.calls = []
    evs = say("问3", conv)
    err = next(d for e, d in evs if e == "error")
    assert err["code"] == "quota" and "不够再问一轮" in err["message"] and not FakeAdapter.calls


def test_fallback_gets_cards_too(client, world, dsn):
    """#53: cards are a plain tool, so the fallback (DeepSeek, compat adapter) shows them like any other provider."""
    from body_engine.rollout import flags
    ids, tok = world
    reset(dsn, ids["F1"])
    with db(dsn) as c:
        flags.set_flag(c, cards.FLAG, "all", actor="test")
    try:
        FakeAdapter.script = [{"tool_calls": [{"id": "c1", "name": "show_card", "args": {
            "type": "actions", "date": None, "metric": None, "days": None, "buttons": [{"label": "再问", "text": "然后呢"}]}}]},
                              {"text": "好"}]
        evs = sse(client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "给我几个选项"}).text)
        assert FakeAdapter.calls[0]["tools"][-1] == cards.NAME
        assert [d["card"]["type"] for e, d in evs if e == "card"] == ["actions"] and evs[-1][0] == "done"
    finally:
        with db(dsn) as c:
            flags.set_flag(c, cards.FLAG, "off", actor="test")


def test_rate_limits(client, world, dsn, monkeypatch):
    ids, tok = world
    reset(dsn, ids["F1"])
    with db(dsn) as c:
        for _ in range(fp.RATE_PER_MINUTE):
            c.execute("INSERT INTO ai_usage (user_id, kind, model, cost_cny) VALUES (%s, 'system_fallback', %s, 0)",
                      (ids["F1"], fp.MODEL))
    err = next(d for e, d in sse(client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"}).text)
               if e == "error")
    assert err["code"] == "rate_limit" and "一分钟" in err["message"]
    # Per day (from local midnight): "now" frozen at Sydney 00:30, a full day's calls at 00:10
    monkeypatch.setattr(fallback, "_now", lambda: SYDNEY_00_30)
    with db(dsn) as c:
        c.execute("DELETE FROM ai_usage WHERE user_id = %s", (ids["F1"],))
        c.execute("""INSERT INTO ai_usage (user_id, kind, model, cost_cny, at)
                     SELECT %s, 'system_fallback', %s, 0, %s FROM generate_series(1, %s)""",
                  (ids["F1"], fp.MODEL, SYDNEY_00_30 - timedelta(minutes=20), fp.RATE_PER_DAY))
    err = next(d for e, d in sse(client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"}).text)
               if e == "error")
    assert err["code"] == "rate_limit" and "今天" in err["message"]


def test_daily_limits_follow_local_midnight(world, dsn, monkeypatch):
    """At Sydney 00:30 (UTC 13:30) calls and recognitions from 23:50 the evening before are yesterday's, 00:10 is today;
    for a user in UTC the same rows are all today."""
    from body_engine.ai import asr
    ids, _ = world
    uid = ids["F1"]
    monkeypatch.setattr(fallback, "_now", lambda: SYDNEY_00_30)
    monkeypatch.setattr(asr, "_now", lambda: SYDNEY_00_30)
    reset(dsn, uid)
    with db(dsn) as c:
        c.execute("DELETE FROM asr_usage WHERE user_id = %s", (uid,))
        for minutes, n in ((40, 3), (20, 2)):   # 23:50 yesterday, 00:10 today (Sydney)
            at = SYDNEY_00_30 - timedelta(minutes=minutes)
            c.execute("""INSERT INTO ai_usage (user_id, kind, model, cost_cny, at)
                         SELECT %s, 'system_fallback', %s, 0, %s FROM generate_series(1, %s)""", (uid, fp.MODEL, at, n))
            c.execute("""INSERT INTO asr_usage (user_id, audio_ms, proc_ms, ok, at)
                         SELECT %s, 1000, 100, true, %s FROM generate_series(1, %s)""", (uid, at, n))
        assert fallback.calls_today(c, uid) == 2 and asr.usage_today(c, uid) == 2
        c.execute("UPDATE users SET timezone = 'UTC' WHERE id = %s", (uid,))   # 13:30 UTC: the day began 13.5 h ago
        assert fallback.calls_today(c, uid) == 5 and asr.usage_today(c, uid) == 5
        c.rollback()


def test_balance_pause_and_admin_switch(client, world, dsn, monkeypatch):
    ids, tok = world
    reset(dsn, ids["F1"])
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 1.5)
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "fallback_paused" and "Jarvis 暂停" in r.json()["detail"]["message"] \
        and "余额" not in r.json()["detail"]["message"]   # users are not told about the admin's balance
    with db(dsn) as c:
        s = fallback.admin_status(c)
        assert s["balance_cny"] == 1.5 and s["available"] is False
        f1 = next(u for u in s["users"] if u["user_id"] == ids["F1"])
        assert f1["consent"] and f1["uses"] == "fallback"
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 30.0)
    with db(dsn) as c:
        fallback.refresh_balance(c, force=True)
        fallback.set_enabled(c, False)
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    assert r.status_code == 409 and "管理员暂停" in r.json()["detail"]["message"]
    with db(dsn) as c:
        fallback.set_enabled(c, True)
        s = fallback.admin_status(c)
        assert s["available"] and s["balance_cny"] == 30.0 and s["eligible_users"] >= 2  # O excluded? no: O has no own AI
    # Balance endpoint failing (network) does not pause: DeepSeek itself refuses if the balance is really gone
    def boom(key, timeout=8):
        raise OSError("down")
    monkeypatch.setattr(fallback, "fetch_balance", boom)
    with db(dsn) as c:
        st = fallback.refresh_balance(c, force=True)
        assert st["balance_error"] == "OSError" and float(st["balance_cny"]) == 30.0
    assert client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"}).status_code == 200


def test_own_provider_wins(client, world, dsn):
    ids, tok = world
    r = client.post("/v1/users/me/ai-providers", headers=H(tok["A"]),
                    json={"kind": "anthropic_api", "api_key": "sk-ant-test-own-key-AAAA", "monthly_budget_usd": 5})
    assert r.status_code == 201
    assert client.get("/v1/web/me", headers=H(tok["A"])).json()["ai"]["mode"] == "api"
    assert client.post("/v1/assistant/chat", headers=H(tok["A"]), json={"message": "hi"}).status_code == 200
    assert FakeAdapter.calls[0]["api_key"] == "sk-ant-test-own-key-AAAA"
    # #184: the general chat (own provider) also answers at its own round limit instead of stopping with an error
    from body_engine.ai import chat as chat_mod
    FakeAdapter.calls = []
    FakeAdapter.script = [{"tool_calls": [{"id": f"o{i}", "name": "get_profile", "args": {}}]} for i in range(chat_mod.MAX_ROUNDS)]
    evs = sse(client.post("/v1/assistant/chat", headers=H(tok["A"]), json={"message": "多查几样"}).text)
    assert len(FakeAdapter.calls) == chat_mod.MAX_ROUNDS and FakeAdapter.calls[-1]["no_tool_calls"]
    assert evs[-1][0] == "done" and not any(e == "error" for e, _ in evs)
    view = client.get("/v1/users/me/ai-providers", headers=H(tok["A"])).json()["fallback"]
    assert view["used"] is False


def test_images_and_video_frames_then_revoke(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F1"])
    img = {"media_type": "image/jpeg", "data": "/9j/4AAQSkZJRg=="}
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]),
                    json={"message": "［视频 8 帧，约 20 秒］记一下", "images": [img] * 8})
    assert r.status_code == 200, r.text
    user_msg = FakeAdapter.calls[0]["history"][-1]["content"]
    assert sum(b["type"] == "image" for b in user_msg) == 8 and "［视频 8 帧" in user_msg[0]["text"]
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "太多", "images": [img] * 9})
    assert r.status_code == 422
    assert {"add_lab_results", "log_metric_snapshot", "add_supplement"} <= set(fallback.TOOL_NAMES)   # reachable via find_tools
    assert client.delete("/v1/users/me/ai-fallback/consent", headers=H(tok["F1"])).status_code == 204
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "fallback_consent"
    consent(client, tok["F1"])  # consenting again adds a new row
    with db(dsn) as c:
        assert c.execute("SELECT count(*) AS n FROM ai_fallback_consents WHERE user_id = %s", (ids["F1"],)).fetchone()["n"] == 2


def test_old_consent_version_must_reconfirm(client, world, dsn):
    """Consents to an older wording (before photos / voice) do not count: the sheet is shown again."""
    ids, tok = world
    with db(dsn) as c:
        c.execute("UPDATE ai_fallback_consents SET version = 'old' WHERE user_id = %s", (ids["F2"],))
        c.execute("INSERT INTO ai_fallback_consents (user_id, version) VALUES (%s, '2026-10-18')", (ids["F2"],))
    me = client.get("/v1/web/me", headers=H(tok["F2"])).json()["ai"]
    assert me["consent"] is False and me["consent_version"] == fallback.CONSENT_VERSION
    points = client.get("/v1/users/me/ai-fallback", headers=H(tok["F2"])).json()["points"]
    assert any("照片" in p for p in points) and any("语音" in p and "删除" in p for p in points)


# ---------------------------------------------------------------- voice -> text (ai/asr.py)
def make_wav(seconds: float, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    import io
    import math
    import struct
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        n = int(seconds * rate)
        if width == 2:
            w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) * channels for i in range(n)))
        else:
            w.writeframes(bytes(n * channels * width))
    return buf.getvalue()


@pytest.fixture()
def asr_on(monkeypatch):
    from body_engine.ai import asr
    monkeypatch.setenv("BODY_ASR_URL", "http://asr.test:8000")
    sent = []

    def fake_post(data):
        sent.append(len(data))
        return {"text": "今天中午吃了牛肉面", "seconds": 2.0, "ms": 120}
    monkeypatch.setattr(asr, "_post", fake_post)
    return sent


def W(tok):
    return {**H(tok), "Content-Type": "audio/wav"}


def test_asr_off_by_default(client, world):
    ids, tok = world
    assert client.get("/v1/web/me", headers=H(tok["F1"])).json()["ai"]["asr"] == {"enabled": False}
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F1"]), content=make_wav(2))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "asr_off"


def test_asr_transcribe_and_limits(client, world, dsn, asr_on, monkeypatch):
    from body_engine.ai import asr
    ids, tok = world
    with db(dsn) as c:
        c.execute("DELETE FROM asr_usage")
    me = client.get("/v1/web/me", headers=H(tok["F2"])).json()["ai"]["asr"]
    assert me["enabled"] and me["max_seconds"] == asr.MAX_SECONDS and me["used_today"] == 0
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F2"]), content=make_wav(2))
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "今天中午吃了牛肉面", "seconds": 2.0, "used_today": 1, "per_day": asr.ASR_PER_DAY}
    for bad, code in ((make_wav(0.1), "too_short"), (make_wav(61.5), "too_long"), (make_wav(2, channels=2), "bad_audio"),
                      (make_wav(2, width=1), "bad_audio"), (b"not a wav file", "bad_audio")):
        r = client.post("/v1/assistant/transcribe", headers=W(tok["F2"]), content=bad)
        assert r.status_code in (413, 422) and r.json()["detail"]["code"] == code, (code, r.text)
    assert len(asr_on) == 1  # invalid audio never reaches the recogniser
    with db(dsn) as c:
        row = c.execute("SELECT * FROM asr_usage WHERE user_id = %s", (ids["F2"],)).fetchone()
        assert row["ok"] and row["audio_ms"] == 2000 and set(row) == {"id", "user_id", "at", "audio_ms", "proc_ms", "ok", "error"}
    # Limits with "now" frozen at Sydney 00:30: a full day's recognitions at 00:10, then a full minute's at 00:29:50
    monkeypatch.setattr(asr, "_now", lambda: SYDNEY_00_30)
    with db(dsn) as c:
        c.execute("DELETE FROM asr_usage WHERE user_id = %s", (ids["F2"],))
        c.execute("""INSERT INTO asr_usage (user_id, audio_ms, proc_ms, ok, at)
                     SELECT %s, 1000, 100, true, %s FROM generate_series(1, %s)""",
                  (ids["F2"], SYDNEY_00_30 - timedelta(minutes=20), asr.ASR_PER_DAY))
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F2"]), content=make_wav(2))
    assert r.status_code == 429 and "每天" in r.json()["detail"]["message"]
    with db(dsn) as c:
        c.execute("DELETE FROM asr_usage WHERE user_id = %s", (ids["F2"],))
        c.execute("""INSERT INTO asr_usage (user_id, audio_ms, proc_ms, ok, at)
                     SELECT %s, 1000, 100, true, %s FROM generate_series(1, %s)""",
                  (ids["F2"], SYDNEY_00_30 - timedelta(seconds=10), asr.ASR_PER_MINUTE))
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F2"]), content=make_wav(2))
    assert r.status_code == 429 and "太快" in r.json()["detail"]["message"]


def test_asr_service_down_and_cookie_csrf(client, world, dsn, monkeypatch, asr_on):
    from body_engine.ai import asr
    ids, tok = world
    with db(dsn) as c:
        c.execute("DELETE FROM asr_usage")

    def down(data):
        raise asr.AsrError("asr_down", "语音识别暂时不可用，可以用键盘上的听写", 503)
    monkeypatch.setattr(asr, "_post", down)
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F1"]), content=make_wav(2))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "asr_down"
    with db(dsn) as c:  # a failed recognition is still counted (it used the quota), with the error code
        row = c.execute("SELECT ok, error FROM asr_usage WHERE user_id = %s", (ids["F1"],)).fetchone()
        assert row == {"ok": False, "error": "asr_down"}
    r = client.post("/v1/assistant/transcribe", headers={"Content-Type": "audio/wav"}, content=make_wav(2))
    assert r.status_code == 401
    big = b"RIFF" + b"\0" * (asr.MAX_BYTES + 10)
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F1"]), content=big)
    assert r.status_code == 413


def test_main_consent_required_and_owner_try(client, world, dsn):
    ids, tok = world
    r = client.post("/v1/assistant/chat", headers=H(tok["N"]), json={"message": "hi"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "needs_consent"
    r = client.post("/v1/assistant/chat", headers=H(tok["O"]), json={"message": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "subscription"  # owner keeps the butler link
    from body_engine.ai import chat
    with db(dsn) as c:  # the CLI ai-fallback try path: owner may test, no separate consent (the owner pays)
        viewer = {"id": ids["O"], "token_scopes": ["read"]}
        evs = list(chat.chat_events(c, viewer, "hi", [], None, allow_owner_fallback=True))
        assert evs[0]["data"]["provider"] == "system_fallback" and evs[-1]["event"] == "done"


# ---------------------------------------------------------------- which AI Jarvis uses (ai/choice.py)
def me_ai(client, tok):
    r = client.get("/v1/web/me", headers=H(tok)).json()
    return r["ai"], r["assistant_url"]


def test_choice_disable_delete_and_revoke_fall_back(client, world, dsn):
    """The user's report: with their own (subscription) AI disabled, Jarvis still went there. Now disabling, deleting
    or revoking every AI token drops Jarvis back to the fallback at once, and picking it again works."""
    ids, tok = world
    with db(dsn) as c:
        c.execute("DELETE FROM ai_providers WHERE user_id = %s", (ids["F2"],))
        c.execute("DELETE FROM ai_choice WHERE user_id = %s", (ids["F2"],))
    assert me_ai(client, tok["F2"])[0]["mode"] == "fallback"
    p = client.post("/v1/users/me/ai-providers", headers=H(tok["F2"]), json={"kind": "codex_plan_mcp", "is_default": True}).json()
    ai, url = me_ai(client, tok["F2"])
    assert ai["mode"] == "mcp" and ai["choice"] == f"p:{p['id']}" and url is None
    r = client.post("/v1/assistant/chat", headers=H(tok["F2"]), json={"message": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "subscription"
    # disable -> fallback immediately; enable -> back
    client.patch(f"/v1/users/me/ai-providers/{p['id']}", headers=H(tok["F2"]), json={"status": "disabled"})
    ai, _ = me_ai(client, tok["F2"])
    assert ai["mode"] == "fallback" and ai["choice"] == "fallback"
    consent(client, tok["F2"])
    assert client.post("/v1/assistant/chat", headers=H(tok["F2"]), json={"message": "hi"}).status_code == 200
    assert FakeAdapter.calls[-1]["api_key"] == DS_KEY
    opts = client.get("/v1/users/me/ai-choice", headers=H(tok["F2"])).json()["options"]
    codex = next(o for o in opts if o["key"] == f"p:{p['id']}")
    assert codex["status"] == "disabled" and not codex["available"] and next(o for o in opts if o["current"])["key"] == "fallback"
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["F2"]), json={"key": f"p:{p['id']}"})
    assert r.status_code == 422 and "停用" in r.json()["detail"]
    client.patch(f"/v1/users/me/ai-providers/{p['id']}", headers=H(tok["F2"]), json={"status": "active"})
    assert me_ai(client, tok["F2"])[0]["mode"] == "mcp"
    # every AI token revoked -> the subscription cannot reach Body Engine any more -> fallback
    t = client.post("/v1/users/me/ai-tokens", headers=H(tok["F2"]), json={"client": "codex"}).json()
    assert me_ai(client, tok["F2"])[0]["mode"] == "mcp"
    client.delete(f"/v1/users/me/ai-tokens/{t['id']}", headers=H(tok["F2"]))
    assert me_ai(client, tok["F2"])[0]["mode"] == "fallback"
    client.post("/v1/users/me/ai-tokens", headers=H(tok["F2"]), json={"client": "codex"})
    assert me_ai(client, tok["F2"])[0]["mode"] == "mcp"
    # delete -> fallback
    assert client.delete(f"/v1/users/me/ai-providers/{p['id']}", headers=H(tok["F2"])).status_code == 204
    assert me_ai(client, tok["F2"])[0]["mode"] == "fallback"


def test_choice_pick_fallback_over_own_ai_and_back(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F2"])
    p = client.post("/v1/users/me/ai-providers", headers=H(tok["F2"]),
                    json={"kind": "openai_compatible", "base_url": "https://api.example.com/v1", "model": "m1",
                          "api_key": "sk-own-key-F2-123456", "is_default": True}).json()
    assert me_ai(client, tok["F2"])[0]["mode"] == "api"
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["F2"]), json={"key": "fallback"})
    assert r.status_code == 200 and r.json()["ai"]["mode"] == "fallback" and r.json()["assistant_url"] is None
    assert [o["key"] for o in r.json()["options"] if o["current"]] == ["fallback"]
    client.post("/v1/assistant/chat", headers=H(tok["F2"]), json={"message": "hi"})
    assert FakeAdapter.calls[-1]["api_key"] == DS_KEY  # the pick wins over the own provider
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["F2"]), json={"key": f"p:{p['id']}"})
    assert r.json()["ai"]["mode"] == "api" and r.json()["ai"]["choice"] == f"p:{p['id']}"
    client.post("/v1/assistant/chat", headers=H(tok["F2"]), json={"message": "hi"})
    assert FakeAdapter.calls[-1]["api_key"] == "sk-own-key-F2-123456"
    # ticking "use it" on another provider is an explicit pick too
    client.put("/v1/users/me/ai-choice", headers=H(tok["F2"]), json={"key": "fallback"})
    q = client.post("/v1/users/me/ai-providers", headers=H(tok["F2"]),
                    json={"kind": "anthropic_api", "api_key": "sk-ant-F2-xxxxxxxx", "is_default": True}).json()
    assert me_ai(client, tok["F2"])[0]["choice"] == f"p:{q['id']}"
    # someone else's provider / the butler / nonsense
    a_prov = client.get("/v1/users/me/ai-providers", headers=H(tok["A"])).json()["providers"][0]["id"]
    for key in (f"p:{a_prov}", "butler", "nope"):
        assert client.put("/v1/users/me/ai-choice", headers=H(tok["F2"]), json={"key": key}).status_code == 422, key
    assert client.put(f"/v1/users/{ids['A']}/ai-choice", headers=H(tok["F2"]), json={"key": "fallback"}).status_code == 403
    with db(dsn) as c:
        c.execute("DELETE FROM ai_providers WHERE user_id = %s", (ids["F2"],))


def test_choice_owner_can_pick_fallback_and_butler(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["O"])
    ai, url = me_ai(client, tok["O"])
    assert ai["mode"] == "butler" and url == "https://claude.ai/code/session_test"
    opts = client.get("/v1/users/me/ai-choice", headers=H(tok["O"])).json()["options"]
    assert {o["key"] for o in opts} >= {"fallback", "butler"} and next(o for o in opts if o["key"] == "fallback")["consent"]
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["O"]), json={"key": "fallback"}).json()
    assert r["ai"]["mode"] == "fallback" and r["ai"]["consent"] and r["assistant_url"] is None
    assert r["ai"]["quota_cny"] == fp.MONTHLY_QUOTA_CNY  # the deployer gets the same default quota (ai-fallback quota to change)
    assert client.post("/v1/assistant/chat", headers=H(tok["O"]), json={"message": "hi"}).status_code == 200
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["O"]), json={"key": "butler"}).json()
    assert r["ai"]["mode"] == "butler" and r["assistant_url"] == "https://claude.ai/code/session_test"
    # no fallback on this server: a fallback pick falls through to the next source
    with db(dsn) as c:
        c.execute("UPDATE ai_choice SET source = 'fallback' WHERE user_id = %s", (ids["O"],))
    import body_engine.ai.fallback as fbm
    orig = fbm.read_key
    fbm.read_key = lambda: None
    try:
        assert me_ai(client, tok["O"])[0]["mode"] == "butler"
        assert me_ai(client, tok["F1"])[0]["mode"] == "none"
    finally:
        fbm.read_key = orig
