"""AI integration layer: provider config and encryption, isolation between users, in-page chat on the Today page (fake adapter), budget, remote MCP.

Uses a separate test database BODY_TEST_DB_NAME (default body_test_ai), dropped and recreated on every run, never touching the production DB; skipped if it cannot connect.
No paid API is called: chat swaps every provider adapter for FakeAdapter from ai/adapters/fake.py.
The master key is a temp file (BODY_AI_MASTER_KEY_FILE); secrets/ is never touched.

Users: A (anthropic_api, key KEY_A), B (openai_api, key KEY_B), C (the deployer: no API configured, subscription), D (Codex subscription).
B shares all of their data with A (all shares scopes) -- AI config and conversations are still invisible to each other.
"""
import json
import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth
from body_engine.ai import adapters, crypto, providers
from body_engine.ai import chat as chat_mod
from body_engine.ai.adapters.fake import FakeAdapter
from body_engine.config import Settings
from body_engine.db import migrate

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_ai")
CSRF = {"X-Body-Web": "1"}
KEY_A = "sk-ant-test-AAAA-1111-secretA"
KEY_B = "sk-test-BBBB-2222-secretB"
ALL_SCOPES = ["profile", "metrics", "sleep", "scores", "events", "memory", "journal", "nutrition", "training",
              "weight", "supplements", "labs", "exercises", "reminders"]


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
def master_key(tmp_path_factory):
    path = tmp_path_factory.mktemp("ai") / "ai_master_key"
    mp = pytest.MonkeyPatch()
    mp.setenv("BODY_AI_MASTER_KEY_FILE", str(path))
    crypto.init_master_key()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    yield path
    mp.undo()


@pytest.fixture(scope="module")
def world(dsn, master_key):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"]
               for n in ("A", "B", "C", "D")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        tok["A_ro"] = auth.create_token(conn, ids["A"], "web-ro", ["read"])
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["B"], ids["A"], ALL_SCOPES))
        conn.execute("""INSERT INTO assistant_observations (user_id, text, source) VALUES
                        (%s, 'A 喜欢早上练', 'user'), (%s, 'B 乳糖不耐', 'user')""", (ids["A"], ids["B"]))
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn, world):
    """Runs the full lifespan (remote MCP needs it), but the connection pool points at the test DB."""
    mp = pytest.MonkeyPatch()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    assert api.settings.db_name == TEST_DB
    with TestClient(api.app, base_url="https://testserver") as c:
        yield c
    mp.undo()


@pytest.fixture(autouse=True)
def fake_adapters(monkeypatch):
    for kind in ("anthropic_api", "openai_api", "gemini_api", "openai_compatible"):
        monkeypatch.setitem(adapters.ADAPTERS, kind, lambda: FakeAdapter)
    FakeAdapter.script, FakeAdapter.calls = [], []


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def db(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


@pytest.fixture(scope="module")
def setup(client, world):
    ids, tok = world
    a = client.post("/v1/users/me/ai-providers", headers=H(tok["A"]),
                    json={"kind": "anthropic_api", "api_key": KEY_A, "monthly_budget_usd": 5})
    b = client.post("/v1/users/me/ai-providers", headers=H(tok["B"]),
                    json={"kind": "openai_api", "api_key": KEY_B, "model": "gpt-6.1-sol", "monthly_budget_usd": 1})
    d = client.post("/v1/users/me/ai-providers", headers=H(tok["D"]), json={"kind": "codex_plan_mcp"})
    assert a.status_code == b.status_code == d.status_code == 201, (a.text, b.text, d.text)
    return {"A": a.json(), "B": b.json(), "D": d.json()}


# ---------------------------------------------------------------- provider config and encryption
def test_provider_defaults_and_secret_never_returned(client, world, setup, dsn):
    ids, tok = world
    pa = setup["A"]
    assert pa["is_default"] and pa["model"] == "claude-opus-5-5" and pa["secret_last4"] == "retA" and pa["has_secret"]
    r = client.get("/v1/users/me/ai-providers", headers=H(tok["A"]))
    assert r.status_code == 200 and KEY_A not in r.text and "encrypted_secret" not in r.text
    body = r.json()
    assert [p["id"] for p in body["providers"]] == [pa["id"]]
    assert body["mcp"]["url"].endswith("/mcp") and "BODY_ENGINE_MCP_TOKEN" in body["mcp"]["clients"]["codex"]
    with db(dsn) as conn:
        row = conn.execute("SELECT encrypted_secret FROM ai_providers WHERE id = %s", (pa["id"],)).fetchone()
        assert KEY_A.encode() not in bytes(row["encrypted_secret"])
        assert providers.decrypt_secret(providers.get_provider(conn, ids["A"], pa["id"]), ids["A"]) == KEY_A


def test_provider_validation(client, world, setup):
    _, tok = world
    bad = [{"kind": "anthropic_api"},                                       # missing key
           {"kind": "claude_plan_mcp", "api_key": "sk-whatever-123"},       # subscription kinds take no key
           {"kind": "openai_compatible", "api_key": "x" * 10, "model": "m"},  # missing base_url
           {"kind": "openai_compatible", "api_key": "x" * 10, "model": "m", "base_url": "http://example.com/v1"},
           {"kind": "openai_compatible", "api_key": "x" * 10, "model": "m", "base_url": "https://10.0.0.5/v1"},
           {"kind": "openai_compatible", "api_key": "x" * 10, "model": "m", "base_url": "https://u:p@api.x.com/v1"},
           {"kind": "nope"}]
    for body in bad:
        r = client.post("/v1/users/me/ai-providers", headers=H(tok["C"]), json=body)
        assert r.status_code == 422, (body, r.text)
    ok = client.post("/v1/users/me/ai-providers", headers=H(tok["C"]),
                     json={"kind": "openai_compatible", "model": "qwen3", "base_url": "http://host.docker.internal:11434/v1/"})
    assert ok.status_code == 201 and ok.json()["base_url"] == "http://host.docker.internal:11434/v1" and not ok.json()["has_secret"]
    assert client.delete(f"/v1/users/me/ai-providers/{ok.json()['id']}", headers=H(tok["C"])).status_code == 204
    # a read-only token cannot modify
    assert client.post("/v1/users/me/ai-providers", headers=H(tok["A_ro"]),
                       json={"kind": "claude_plan_mcp"}).status_code == 403


def test_isolation_config(client, world, setup, dsn):
    """B shares all their data with A, but AI config is outside the sharing scope: A cannot read, change or delete B's providers."""
    ids, tok = world
    pb = setup["B"]
    assert client.get(f"/v1/users/{ids['B']}/ai-providers", headers=H(tok["A"])).status_code == 403
    assert client.get(f"/v1/users/{ids['B']}/ai-tokens", headers=H(tok["A"])).status_code == 403
    assert client.patch(f"/v1/users/me/ai-providers/{pb['id']}", headers=H(tok["A"]),
                        json={"api_key": "sk-hijack-0000000"}).status_code == 404
    assert client.patch(f"/v1/users/{ids['B']}/ai-providers/{pb['id']}", headers=H(tok["A"]),
                        json={"monthly_budget_usd": 999}).status_code == 403
    assert client.delete(f"/v1/users/me/ai-providers/{pb['id']}", headers=H(tok["A"])).status_code == 404
    with db(dsn) as conn:
        row = conn.execute("SELECT * FROM ai_providers WHERE id = %s", (pb["id"],)).fetchone()
        assert float(row["monthly_budget_usd"]) == 1 and providers.decrypt_secret(row, ids["B"]) == KEY_B
        # Ciphertext is bound to user_id: A's ciphertext copied under B cannot be decrypted; a row not owned by the current user is rejected too
        enc_a = conn.execute("SELECT encrypted_secret FROM ai_providers WHERE user_id = %s", (ids["A"],)).fetchone()
        with pytest.raises(Exception):
            crypto.decrypt(ids["B"], enc_a["encrypted_secret"])
        with pytest.raises(PermissionError):
            providers.decrypt_secret(row, ids["A"])


# ---------------------------------------------------------------- chat
def chat(client, token, message="今天怎么样", **kw):
    return client.post("/v1/assistant/chat", headers=H(token), json={"message": message, **kw})


def test_chat_uses_own_key_only(client, world, setup):
    ids, tok = world
    FakeAdapter.script = [{"text": "A 你|好"}]
    r = chat(client, tok["A"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = sse(r.text)
    assert [e for e, _ in evs][0] == "conversation" and evs[-1][0] == "done"
    assert "".join(d["delta"] for e, d in evs if e == "text") == "A 你好"
    FakeAdapter.script = [{"text": "B 好"}]
    assert chat(client, tok["B"]).status_code == 200
    call_a, call_b = FakeAdapter.calls
    assert (call_a["api_key"], call_a["model"]) == (KEY_A, "claude-opus-5-5")
    assert (call_b["api_key"], call_b["model"]) == (KEY_B, "gpt-6.1-sol")
    # Memory is per-user: A's system prompt has none of B's observation notes (even if B shared memory with A)
    assert "A 喜欢早上练" in call_a["system"] and "乳糖不耐" not in call_a["system"]
    assert "乳糖不耐" in call_b["system"] and "A 喜欢早上练" not in call_b["system"]
    assert KEY_A not in json.dumps(call_b, ensure_ascii=False, default=str)
    # Every user message is prefixed with the local time
    assert call_a["history"][-1]["content"][0]["text"].startswith("[当前本地时间")


def test_chat_read_tool_loop_and_audit(client, world, setup, dsn):
    ids, tok = world
    FakeAdapter.script = [
        {"tool_calls": [{"id": "t1", "name": "get_profile", "args": {"user": "me"}}]},
        {"text": "你是 A。"},
    ]
    evs = sse(chat(client, tok["A"], "我是谁").text)
    names = [e for e, _ in evs]
    assert ("tool", {"name": "get_profile", "status": "done"}) in evs and names[-1] == "done"
    assert names.count("usage") == 2
    second = FakeAdapter.calls[1]["history"]
    assert second[-1]["role"] == "tool" and '"display_name":"A"' in second[-1]["content"][0]["content"]
    with db(dsn) as conn:
        a = conn.execute("""SELECT channel, provider_id, model, ok FROM assistant_audit WHERE tool = 'get_profile'
                            ORDER BY id DESC LIMIT 1""").fetchone()
        assert a == {"channel": "api:anthropic_api", "provider_id": setup["A"]["id"], "model": "claude-opus-5-5", "ok": True}
        u = conn.execute("SELECT count(*) AS n, sum(cost_usd) AS c FROM ai_usage WHERE user_id = %s", (ids["A"],)).fetchone()
        assert u["n"] >= 3 and float(u["c"]) > 0


def test_chat_tool_cannot_read_unshared_or_write_others(client, world, setup, dsn):
    """Tools and MCP share one authorization: A reading D's data (not shared) errors; writes only go to oneself."""
    ids, tok = world
    FakeAdapter.script = [
        {"tool_calls": [{"id": "x1", "name": "get_profile", "args": {"user": str(ids["D"])}}]},
        {"text": "看不到。"},
    ]
    evs = sse(chat(client, tok["A"], "看看 D").text)
    assert ("tool", {"name": "get_profile", "status": "error"}) in evs
    assert "没有查看用户" in FakeAdapter.calls[1]["history"][-1]["content"][0]["content"]


def test_chat_write_needs_confirmation(client, world, setup, dsn, monkeypatch):
    ids, tok = world
    monkeypatch.setattr(chat_mod, "ALWAYS_CONFIRM", chat_mod.ALWAYS_CONFIRM | {"add_event"})   # exercise the confirm flow with a simple tool
    args = {"occurred_at": "2026-10-16T18:00", "title": "测试：肩响", "category": "pain", "body_part": "肩",
            "severity": 1, "note": None}
    FakeAdapter.script = [{"text": "我来记一下。", "tool_calls": [{"id": "w1", "name": "add_event", "args": args}]}]
    evs = sse(chat(client, tok["A"], "肩响了").text)
    assert evs[-1][0] == "confirm"
    pending = evs[-1][1]["calls"]
    assert len(pending) == 1 and pending[0]["tool"] == "add_event" and "测试：肩响" in pending[0]["summary"]
    conv_id = evs[-1][1]["conversation_id"]
    with db(dsn) as conn:
        assert conn.execute("SELECT count(*) AS n FROM events WHERE title = '测试：肩响'").fetchone()["n"] == 0
    # B cannot confirm A's write operation, nor see A's conversation
    assert client.post("/v1/assistant/chat/confirm", headers=H(tok["B"]),
                       json={"call_id": pending[0]["call_id"], "approve": True}).status_code == 404
    assert client.get(f"/v1/assistant/conversations/{conv_id}", headers=H(tok["B"])).status_code == 404
    assert client.get(f"/v1/assistant/conversations/{conv_id}", headers=H(tok["A"])).json()["pending"][0]["tool"] == "add_event"
    # A confirms -> written -> the model continues
    FakeAdapter.script = [{"text": "记好了。"}]
    evs = sse(client.post("/v1/assistant/chat/confirm", headers=H(tok["A"]),
                          json={"call_id": pending[0]["call_id"], "approve": True}).text)
    assert ("tool", {"name": "add_event", "status": "done", "call_id": pending[0]["call_id"]}) in evs and evs[-1][0] == "done"
    with db(dsn) as conn:
        assert conn.execute("SELECT user_id FROM events WHERE title = '测试：肩响'").fetchone()["user_id"] == ids["A"]
    # The same operation cannot be confirmed twice
    assert client.post("/v1/assistant/chat/confirm", headers=H(tok["A"]),
                       json={"call_id": pending[0]["call_id"], "approve": True}).status_code == 409


def test_chat_write_rejected_and_new_message_closes_pending(client, world, setup, dsn, monkeypatch):
    ids, tok = world
    monkeypatch.setattr(chat_mod, "ALWAYS_CONFIRM", chat_mod.ALWAYS_CONFIRM | {"add_event"})   # exercise the confirm flow with a simple tool
    args = {"occurred_at": "2026-10-16T19:00", "title": "测试：不该写", "category": None, "body_part": None,
            "severity": None, "note": None}
    FakeAdapter.script = [{"tool_calls": [{"id": "w2", "name": "add_event", "args": args}]}]
    evs = sse(chat(client, tok["A"], "记一下").text)
    call_id, conv_id = evs[-1][1]["calls"][0]["call_id"], evs[-1][1]["conversation_id"]
    FakeAdapter.script = [{"text": "好的，不记。"}]
    evs = sse(client.post("/v1/assistant/chat/confirm", headers=H(tok["A"]),
                          json={"call_id": call_id, "approve": False}).text)
    assert evs[-1][0] == "done"
    assert "用户拒绝" in FakeAdapter.calls[-1]["history"][-1]["content"][0]["content"]
    # Another pending confirmation, then ignore it and send a new message: the pending one counts as "not written", history stays intact
    FakeAdapter.script = [{"tool_calls": [{"id": "w3", "name": "add_event", "args": args}]}, {"text": "好"}]
    evs = sse(chat(client, tok["A"], "记一下", conversation_id=conv_id).text)
    assert evs[-1][0] == "confirm"
    sse(chat(client, tok["A"], "算了", conversation_id=conv_id).text)
    hist = FakeAdapter.calls[-1]["history"]
    assert [m["role"] for m in hist[-3:]] == ["assistant", "tool", "user"] and hist[-2]["content"][0]["is_error"]
    with db(dsn) as conn:
        assert conn.execute("SELECT count(*) AS n FROM events WHERE title = '测试：不该写'").fetchone()["n"] == 0


def test_logging_runs_directly_only_delete_asks(client, world, setup, dsn):
    """Owner's rule: logging writes run without confirmation; deleting / undoing still asks."""
    ids, tok = world
    args = {"occurred_at": "2026-10-16T20:00", "title": "测试：直接记", "category": None, "body_part": None,
            "severity": None, "note": None}
    FakeAdapter.script = [{"tool_calls": [{"id": "d1", "name": "add_event", "args": args}]}, {"text": "记好了"}]
    evs = sse(chat(client, tok["A"], "记一下").text)
    assert ("tool", {"name": "add_event", "status": "done"}) in evs and evs[-1][0] == "done"
    with db(dsn) as conn:
        assert conn.execute("SELECT user_id FROM events WHERE title = '测试：直接记'").fetchone()["user_id"] == ids["A"]
    FakeAdapter.script = [{"tool_calls": [{"id": "d2", "name": "delete_observation", "args": {"id": 999999}}]}]
    evs = sse(chat(client, tok["A"], "删掉那条").text)
    assert evs[-1][0] == "confirm" and evs[-1][1]["calls"][0]["tool"] == "delete_observation"
    assert chat_mod.ALWAYS_CONFIRM == {"undo_sparky_entry", "undo_meal_log", "delete_journal_entry", "delete_lab_results", "delete_observation"}


def test_memory_writes_without_confirmation(client, world, setup, dsn):
    """Same as the body butler: observation notes are written directly without confirmation (only to oneself)."""
    ids, tok = world
    FakeAdapter.script = [{"tool_calls": [{"id": "m1", "name": "add_observation",
                                           "args": {"text": "测试：A 周三打篮球", "evidence": "用户原话"}}]}, {"text": "记住了"}]
    evs = sse(chat(client, tok["A"], "我每周三打篮球").text)
    assert ("tool", {"name": "add_observation", "status": "done"}) in evs and evs[-1][0] == "done"
    with db(dsn) as conn:
        assert conn.execute("SELECT user_id FROM assistant_observations WHERE text = '测试：A 周三打篮球'").fetchone()["user_id"] == ids["A"]


def test_read_only_login_cannot_write_via_chat(client, world, setup, dsn):
    ids, tok = world
    args = {"occurred_at": "2026-10-16T20:00", "title": "测试：只读", "category": None, "body_part": None,
            "severity": None, "note": None}
    FakeAdapter.script = [{"tool_calls": [{"id": "w4", "name": "add_event", "args": args}]}, {"text": "只读。"}]
    evs = sse(chat(client, tok["A_ro"], "记一下").text)
    assert ("tool", {"name": "add_event", "status": "error"}) in evs and evs[-1][0] == "done"


def test_images(client, world, setup):
    _, tok = world
    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    FakeAdapter.script = [{"text": "看到了"}]
    r = chat(client, tok["A"], "这是什么", images=[{"media_type": "image/png", "data": png}])
    assert r.status_code == 200
    blocks = FakeAdapter.calls[-1]["history"][-1]["content"]
    assert blocks[1] == {"type": "image", "media_type": "image/png", "data": png}
    assert chat(client, tok["A"], "x", images=[{"media_type": "image/svg+xml", "data": png}]).status_code == 422
    assert chat(client, tok["A"], "x", images=[{"media_type": "image/png", "data": "@@@"}]).status_code == 422


def test_budget(client, world, setup, dsn):
    """B has a $1 monthly budget: one call (with a huge fake usage) overspends -> the loop stops before the next model call; later new messages get 402 directly."""
    ids, tok = world
    FakeAdapter.script = [{"tool_calls": [{"id": "b1", "name": "get_profile", "args": {"user": "me"}}],
                           "usage": {"input_tokens": 200_000, "output_tokens": 100_000}}]  # 0.4 + 1.0 = $1.4
    evs = sse(chat(client, tok["B"], "贵的问题").text)
    assert evs[-1][0] == "error" and evs[-1][1]["code"] == "budget"
    r = chat(client, tok["B"], "再问")
    assert r.status_code == 402 and r.json()["detail"]["code"] == "budget"
    # A's budget is unaffected (budget is per user + provider)
    FakeAdapter.script = [{"text": "ok"}]
    assert chat(client, tok["A"]).status_code == 200
    # Raising the budget lets it continue
    assert client.patch(f"/v1/users/me/ai-providers/{setup['B']['id']}", headers=H(tok["B"]),
                        json={"monthly_budget_usd": 50}).status_code == 200
    FakeAdapter.script = [{"text": "ok"}]
    assert chat(client, tok["B"]).status_code == 200


def test_subscription_users_get_redirect(client, world, setup, monkeypatch):
    ids, tok = world
    monkeypatch.setenv("BODY_ASSISTANT_USER_ID", str(ids["C"]))
    monkeypatch.setenv("BODY_ASSISTANT_URL", "https://claude.ai/code/session_owner")
    r = chat(client, tok["C"])  # The deployer: no API configured -> go to the Claude app
    assert r.status_code == 409 and r.json()["detail"] == {
        "code": "subscription", "message": "请在 Claude app 中使用身体管家", "assistant_url": "https://claude.ai/code/session_owner"}
    r = chat(client, tok["D"])  # Codex subscription -> use it in Codex, no access to someone else's butler link
    assert r.status_code == 409 and r.json()["detail"]["code"] == "subscription"
    assert r.json()["detail"]["assistant_url"] is None and "Codex" in r.json()["detail"]["message"]
    assert FakeAdapter.calls == []
    # /v1/web/me: how "Ask the butler" works
    for who, mode, url in (("A", "api", None), ("C", "butler", "https://claude.ai/code/session_owner"), ("D", "mcp", None)):
        client.cookies.clear()
        client.post("/v1/web/login", json={"token": tok[who]})
        me = client.get("/v1/web/me").json()
        assert me["ai"]["mode"] == mode and me["assistant_url"] == url, who
    client.cookies.clear()


def test_cookie_chat_needs_csrf_header(client, world, setup):
    _, tok = world
    client.cookies.clear()
    client.post("/v1/web/login", json={"token": tok["A"]})
    assert client.post("/v1/assistant/chat", json={"message": "hi"}).status_code == 403
    FakeAdapter.script = [{"text": "hi"}]
    assert client.post("/v1/assistant/chat", json={"message": "hi"}, headers=CSRF).status_code == 200
    client.cookies.clear()


# ---------------------------------------------------------------- remote MCP
MCP_H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
         "MCP-Protocol-Version": "2025-06-18"}


def rpc(client, token, method, params=None, id_=1):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    headers = {**MCP_H, **({"Authorization": f"Bearer {token}"} if token else {})}
    return client.post("/mcp", headers=headers, json=body)


def test_mcp_token_scope(client, world, setup):
    """An AI token only works on /mcp; ordinary tokens and cookies cannot be used on /mcp."""
    ids, tok = world
    r = client.post("/v1/users/me/ai-tokens", headers=H(tok["A"]), json={"client": "claude-code"})
    assert r.status_code == 201 and r.json()["name"] == "mcp:claude-code"
    mcp_tok = r.json()["token"]
    assert client.get("/v1/me", headers=H(mcp_tok)).status_code == 401
    assert client.get("/v1/users/me/ai-providers", headers=H(mcp_tok)).status_code == 401
    assert client.post("/v1/web/login", json={"token": mcp_tok}).status_code == 401
    assert rpc(client, None, "tools/list").status_code == 401
    assert rpc(client, tok["A"], "tools/list").status_code == 401
    assert client.post("/v1/users/me/ai-tokens", headers=H(tok["A"]), json={"client": "Bad Name!"}).status_code == 422
    assert client.post(f"/v1/users/{ids['A']}/ai-tokens", headers=H(tok["B"]), json={"client": "x"}).status_code == 403
    world[1]["A_mcp"] = mcp_tok


def test_mcp_tools_own_data_and_audit(client, world, setup, dsn):
    ids, tok = world
    t = tok["A_mcp"]
    init = rpc(client, t, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "pytest", "version": "1"}})
    assert init.status_code == 200, init.text
    assert init.json()["result"]["serverInfo"]["name"] == "body-engine"
    tools = rpc(client, t, "tools/list", id_=2).json()["result"]["tools"]
    by = {x["name"]: x for x in tools}
    assert "get_profile" in by and by["add_event"]["annotations"]["readOnlyHint"] is False
    res = rpc(client, t, "tools/call", {"name": "get_profile", "arguments": {"user": "me"}}, id_=3).json()["result"]
    assert not res.get("isError") and json.loads(res["content"][0]["text"])["display_name"] == "A"
    res = rpc(client, t, "tools/call", {"name": "get_profile", "arguments": {"user": str(ids["D"])}}, id_=4).json()["result"]
    assert res["isError"] and "没有查看用户" in res["content"][0]["text"]
    prompts = rpc(client, t, "prompts/list", id_=5).json()["result"]["prompts"]
    assert prompts[0]["name"] == "butler"
    p = rpc(client, t, "prompts/get", {"name": "butler"}, id_=6).json()["result"]
    assert "身体管家" in p["messages"][0]["content"]["text"]
    with db(dsn) as conn:
        a = conn.execute("""SELECT user_id, channel, ok FROM assistant_audit WHERE tool = 'get_profile'
                            ORDER BY id DESC LIMIT 2""").fetchall()
        assert a == [{"user_id": ids["A"], "channel": "mcp:claude-code", "ok": False},
                     {"user_id": ids["A"], "channel": "mcp:claude-code", "ok": True}]


def test_mcp_relaxed_schema_and_briefing(client, world, setup):
    """tools/list carries the relaxed schemas (nullable / defaulted args optional), omitted args are filled in server-side,
    results are compact JSON; get_briefing only shows the caller's own data unless shared."""
    ids, tok = world
    t = tok["A_mcp"]
    by = {x["name"]: x for x in rpc(client, t, "tools/list").json()["result"]["tools"]}
    assert "user" not in by["get_briefing"]["inputSchema"].get("required", [])
    food = by["log_food"]["inputSchema"]["properties"]["items"]["items"]
    assert "time" not in food["required"] and "calories" in food["required"] and "additionalProperties" not in food
    res = rpc(client, t, "tools/call", {"name": "get_profile", "arguments": {}}).json()["result"]
    assert not res.get("isError") and json.loads(res["content"][0]["text"])["display_name"] == "A"
    res = rpc(client, t, "tools/call", {"name": "get_briefing", "arguments": {}}).json()["result"]
    text = res["content"][0]["text"]
    assert not res.get("isError") and '":' in text and '": ' not in text
    b = json.loads(text)
    assert b["me"]["display_name"] == "A" and "now" in b
    assert "A 喜欢早上练" in text and "乳糖" not in text           # B shared everything with A, but "me" is A
    res = rpc(client, t, "tools/call", {"name": "get_briefing", "arguments": {"user": str(ids["D"])}}).json()["result"]
    assert res["isError"] and "没有查看用户" in res["content"][0]["text"]


def test_mcp_read_only_and_revoke(client, world, setup):
    ids, tok = world
    r = client.post("/v1/users/me/ai-tokens", headers=H(tok["B"]), json={"client": "codex", "read_only": True}).json()
    ro = r["token"]
    names = {x["name"] for x in rpc(client, ro, "tools/list").json()["result"]["tools"]}
    assert "get_profile" in names and "add_event" not in names and "log_food" not in names
    res = rpc(client, ro, "tools/call", {"name": "add_event", "arguments": {
        "occurred_at": "2026-10-16T10:00", "title": "x", "category": None, "body_part": None, "severity": None,
        "note": None}}).json()["result"]
    assert res["isError"] and "只读" in res["content"][0]["text"]
    # B's token only represents B (only what A shared with B is visible; here A shared nothing with B)
    res = rpc(client, ro, "tools/call", {"name": "get_profile", "arguments": {"user": str(ids["A"])}}).json()["result"]
    assert res["isError"]
    listed = client.get("/v1/users/me/ai-tokens", headers=H(tok["B"])).json()["tokens"]
    assert [x["name"] for x in listed] == ["mcp:codex"] and "token" not in listed[0]
    # A cannot revoke B's token
    assert client.delete(f"/v1/users/me/ai-tokens/{r['id']}", headers=H(tok["A"])).status_code == 404
    assert client.delete(f"/v1/users/me/ai-tokens/{r['id']}", headers=H(tok["B"])).status_code == 204
    assert rpc(client, ro, "tools/list").status_code == 401
