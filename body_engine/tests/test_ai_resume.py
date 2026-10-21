"""In-page chat survives the page going to the background (issue #114).

A real ASGI client disconnect is simulated: the response's `send` fails after the first `text` event, the way a write fails
when iOS suspends the WebView. Uses its own test database (body_test_ai_resume); FakeAdapter, no paid API.
"""
import asyncio
import json
import os
import threading
import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth
from body_engine.ai import adapters, crypto, run_params, runs
from body_engine.ai import chat as chat_mod
from body_engine.ai.adapters.fake import FakeAdapter
from body_engine.config import Settings
from body_engine.db import migrate

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_RESUME", "body_test_ai_resume")


@pytest.fixture(scope="module")
def dsn():
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
def world(dsn, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("BODY_AI_MASTER_KEY_FILE", str(tmp_path_factory.mktemp("ai") / "ai_master_key"))
    crypto.init_master_key()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"]
               for n in ("A", "B")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
    yield ids, tok
    mp.undo()


@pytest.fixture(scope="module")
def client(dsn, world):
    mp = pytest.MonkeyPatch()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    runs._runs.clear()   # turns kept by other test modules (other databases, same conversation ids)
    with TestClient(api.app, base_url="https://testserver") as c:
        ids, tok = world
        for n in ("A", "B"):
            r = c.post("/v1/users/me/ai-providers", headers=H(tok[n]),
                       json={"kind": "anthropic_api", "api_key": f"sk-ant-test-{n}", "monthly_budget_usd": 5})
            assert r.status_code == 201, r.text
        yield c
    mp.undo()


@pytest.fixture(autouse=True)
def fake_adapters(monkeypatch):
    monkeypatch.setitem(adapters.ADAPTERS, "anthropic_api", lambda: FakeAdapter)
    FakeAdapter.script, FakeAdapter.calls = [], []


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def sse(text: str, ids: bool = False) -> list[tuple]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            e = (lines["event"], json.loads(lines["data"]))
            out.append((*e, int(lines["id"]) if "id" in lines else None) if ids else e)
    return out


def post_then_disconnect(path: str, token: str, body: dict) -> str:
    """POST through the ASGI app and drop the connection right after the first `text` event; returns what arrived."""
    got = []

    async def run():
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
            await asyncio.sleep(3600)

        async def send(msg):
            if msg["type"] == "http.response.body":
                got.append(msg.get("body", b"").decode())
                if "event: text" in "".join(got):
                    raise OSError("client went away")   # what a write to a suspended WebView's socket does

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
                 "method": "POST", "scheme": "https", "path": path, "raw_path": path.encode(), "query_string": b"",
                 "root_path": "", "server": ("testserver", 443), "client": ("testclient", 1),
                 "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                             (b"authorization", f"Bearer {token}".encode())]}
        try:
            await api.app(scope, receive, send)
        except Exception:   # the client is gone; whatever the server raises about it is not the test's concern
            pass

    asyncio.run(run())
    return "".join(got)


def db(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def wait_for(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def assistant_texts(dsn, conv_id):
    with db(dsn) as conn:
        rows = conn.execute("SELECT content FROM ai_messages WHERE conversation_id = %s AND role = 'assistant' ORDER BY id",
                            (conv_id,)).fetchall()
    return ["".join(b.get("text", "") for b in r["content"] if b["type"] == "text") for r in rows]


def turn_done(conv_id, timeout=5.0):
    return wait_for(lambda: (r := runs._runs.get(conv_id)) is not None and r.done, timeout)


def resume(client, token, conv_id, after):
    return client.get(f"/v1/assistant/chat/{conv_id}/events", params={"after": after}, headers=H(token))


def test_disconnect_after_first_text_turn_finishes_and_resumes(client, world, dsn):
    """Before #114 the turn stopped with the connection: only the user's message was stored, the answer was lost."""
    ids, tok = world
    FakeAdapter.script = [{"text": "第一段|第二段|第三段"}]
    got = sse(post_then_disconnect("/v1/assistant/chat", tok["A"], {"message": "今天怎么样"}), ids=True)
    assert [e for e, _, _ in got] == ["conversation", "text"]
    conv_id, last = got[0][1]["id"], got[-1][2]
    assert last == 2
    assert turn_done(conv_id)
    assert assistant_texts(dsn, conv_id) == ["第一段第二段第三段"]      # the whole answer is stored
    rest = sse(resume(client, tok["A"], conv_id, last).text, ids=True)
    assert [(e, d.get("delta")) for e, d, _ in rest if e == "text"] == [("text", "第二段"), ("text", "第三段")]
    assert [e for e, _, _ in rest][-2:] == ["usage", "done"] and [n for _, _, n in rest] == list(range(3, 3 + len(rest)))
    # From the start: every event again, the same numbers
    assert [e for e, _, _ in sse(resume(client, tok["A"], conv_id, 0).text, ids=True)][:2] == ["conversation", "text"]
    with db(dsn) as conn:
        assert conn.execute("SELECT state FROM ai_conversations WHERE id = %s", (conv_id,)).fetchone()["state"] == "idle"
        assert conn.execute("SELECT count(*) AS n FROM ai_usage WHERE conversation_id = %s", (conv_id,)).fetchone()["n"] == 1


def test_confirm_card_after_resume_and_confirm_works(client, world, dsn, monkeypatch):
    ids, tok = world
    monkeypatch.setattr(chat_mod, "ALWAYS_CONFIRM", chat_mod.ALWAYS_CONFIRM | {"add_event"})   # a simple tool through the confirm flow
    args = {"occurred_at": "2026-10-21T18:00", "title": "测试：恢复后确认", "category": "pain", "body_part": "膝",
            "severity": 1, "note": None}
    FakeAdapter.script = [{"text": "我来|记一下。", "tool_calls": [{"id": "w1", "name": "add_event", "args": args}]},
                          {"text": "记好了。"}]
    got = sse(post_then_disconnect("/v1/assistant/chat", tok["A"], {"message": "膝盖疼"}), ids=True)
    conv_id, last = got[0][1]["id"], got[-1][2]
    assert turn_done(conv_id)
    rest = sse(resume(client, tok["A"], conv_id, last).text)
    assert rest[-1][0] == "confirm" and rest[-1][1]["calls"][0]["tool"] == "add_event"
    call_id = rest[-1][1]["calls"][0]["call_id"]
    with db(dsn) as conn:
        assert conn.execute("SELECT count(*) AS n FROM events WHERE title = '测试：恢复后确认'").fetchone()["n"] == 0
    evs = sse(client.post("/v1/assistant/chat/confirm", headers=H(tok["A"]), json={"call_id": call_id, "approve": True}).text)
    assert ("tool", {"name": "add_event", "status": "done", "call_id": call_id}) in evs and evs[-1][0] == "done"
    with db(dsn) as conn:
        assert conn.execute("SELECT user_id FROM events WHERE title = '测试：恢复后确认'").fetchone()["user_id"] == ids["A"]
    assert assistant_texts(dsn, conv_id)[-1] == "记好了。"


def test_confirm_stream_disconnect_also_finishes(client, world, dsn, monkeypatch):
    """The confirmation's own stream (the write, then the model's reply) runs to the end without its connection too."""
    ids, tok = world
    monkeypatch.setattr(chat_mod, "ALWAYS_CONFIRM", chat_mod.ALWAYS_CONFIRM | {"add_event"})
    args = {"occurred_at": "2026-10-21T19:00", "title": "测试：确认时断开", "category": None, "body_part": None,
            "severity": None, "note": None}
    FakeAdapter.script = [{"tool_calls": [{"id": "w2", "name": "add_event", "args": args}]}, {"text": "写好|了。"}]
    evs = sse(client.post("/v1/assistant/chat", headers=H(tok["A"]), json={"message": "记一下"}).text)
    call_id, conv_id = evs[-1][1]["calls"][0]["call_id"], evs[-1][1]["conversation_id"]
    got = sse(post_then_disconnect("/v1/assistant/chat/confirm", tok["A"], {"call_id": call_id, "approve": True}), ids=True)
    assert got[-1][0] == "text"
    assert turn_done(conv_id)
    assert sse(resume(client, tok["A"], conv_id, got[-1][2]).text)[-1][0] == "done"
    assert assistant_texts(dsn, conv_id)[-1] == "写好了。"
    with db(dsn) as conn:
        assert conn.execute("SELECT count(*) AS n FROM events WHERE title = '测试：确认时断开'").fetchone()["n"] == 1


def test_other_user_cannot_resume(client, world):
    ids, tok = world
    FakeAdapter.script = [{"text": "A 的回答"}]
    conv_id = sse(client.post("/v1/assistant/chat", headers=H(tok["A"]), json={"message": "hi"}).text)[0][1]["id"]
    assert resume(client, tok["B"], conv_id, 0).status_code == 404          # in memory
    assert resume(client, tok["B"], 999999, 0).status_code == 404
    runs._runs.pop(conv_id)
    assert resume(client, tok["B"], conv_id, 0).status_code == 404          # only in the database
    r = resume(client, tok["A"], conv_id, 3)
    assert r.status_code == 200 and sse(r.text) == [("resync", {"conversation_id": conv_id})]


class SlowAdapter(FakeAdapter):
    """Streams the first piece, then waits until the test lets it go on."""
    go = threading.Event()

    def stream_turn(self, system, history, tools):
        for i, item in enumerate(super().stream_turn(system, history, tools)):
            if i == 1:
                SlowAdapter.go.wait(5)
            yield item


def test_live_resume_and_busy_while_running(client, world, dsn, monkeypatch):
    ids, tok = world
    monkeypatch.setitem(adapters.ADAPTERS, "anthropic_api", lambda: SlowAdapter)
    SlowAdapter.go.clear()
    FakeAdapter.script = [{"text": "慢|慢|说"}]
    got = sse(post_then_disconnect("/v1/assistant/chat", tok["A"], {"message": "慢慢说"}), ids=True)
    conv_id = got[0][1]["id"]
    run = runs._runs[conv_id]
    assert not run.done
    # Another message (or a second tab) on the same conversation while it is still answering: refused, nothing stored
    r = client.post("/v1/assistant/chat", headers={**H(tok["A"]), "X-Body-Lang": "en"},
                    json={"message": "还有", "conversation_id": conv_id})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "busy" and "Still answering" in r.json()["detail"]["message"]
    threading.Timer(0.3, SlowAdapter.go.set).start()
    rest = sse(resume(client, tok["A"], conv_id, got[-1][2]).text)    # waits for the live events
    assert "".join(d["delta"] for e, d in rest if e == "text") == "慢说" and rest[-1][0] == "done"
    with db(dsn) as conn:
        roles = [r["role"] for r in conn.execute("SELECT role FROM ai_messages WHERE conversation_id = %s ORDER BY id",
                                                 (conv_id,))]
    assert roles == ["user", "assistant"]


def test_turn_time_limit(client, world, monkeypatch):
    ids, tok = world
    monkeypatch.setattr(run_params, "MAX_RUN_SECONDS", -1)
    FakeAdapter.script = [{"text": "a|b"}]
    evs = sse(client.post("/v1/assistant/chat", headers={**H(tok["A"]), "X-Body-Lang": "en"}, json={"message": "hi"}).text)
    assert [e for e, _ in evs] == ["conversation", "error"] and evs[-1][1]["code"] == "too_slow"
    assert "minutes" in evs[-1][1]["message"]          # the request's language reaches the background thread


def test_finished_turns_expire(client, world, monkeypatch):
    ids, tok = world
    FakeAdapter.script = [{"text": "好"}]
    conv_id = sse(client.post("/v1/assistant/chat", headers=H(tok["A"]), json={"message": "hi"}).text)[0][1]["id"]
    assert turn_done(conv_id) and runs.get(ids["A"], conv_id) is not None
    monkeypatch.setattr(run_params, "KEEP_SECONDS", -1)
    assert runs.get(ids["A"], conv_id) is None and conv_id not in runs._runs
    assert sse(resume(client, tok["A"], conv_id, 0).text)[0][0] == "resync"


def test_open_stream_does_not_block_the_same_login(client, world, monkeypatch):
    """A live chat stream must not hold its login's transaction: the session / token row lock would block the resume
    (and every other request) of the same login until the stream ends."""
    ids, tok = world
    monkeypatch.setitem(adapters.ADAPTERS, "anthropic_api", lambda: SlowAdapter)
    SlowAdapter.go.clear()
    FakeAdapter.script = [{"text": "等|一下"}]
    t = threading.Thread(target=lambda: client.post("/v1/assistant/chat", headers=H(tok["A"]), json={"message": "等"}))
    t.start()
    try:
        assert wait_for(lambda: any(not r.done for r in runs._runs.values()))
        t0 = time.monotonic()
        assert client.get("/v1/assistant/conversations", headers=H(tok["A"])).status_code == 200
        assert time.monotonic() - t0 < 2
    finally:
        SlowAdapter.go.set()
        t.join(10)
