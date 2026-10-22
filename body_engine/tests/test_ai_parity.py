"""Rollout flag ai_parity (ai/parity.py, T0): every channel reaches the same tools for the user's own data, and the back
end's name never reaches the AI or the user (ai/naming.py).

Pure tests need nothing. The rest uses a separate test database BODY_TEST_DB_NAME_PARITY (default body_test_ai_parity),
dropped and recreated every run, skipped without Postgres. No paid API (FakeAdapter), SparkyFitness is an in-memory fake.
Users: P (member, fallback AI, flag on via the beta cohort), Q (member who shares everything with P, has a food entry of
their own), R (member, fallback AI, flag off).
"""
import json
import os
import random
import re
from datetime import date, datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth, sparky_client
from body_engine.ai import adapters, chat, crypto, fallback, naming, parity
from body_engine.ai import params as P
from body_engine.ai.adapters.fake import FakeAdapter
from body_engine.assistant import sparky_write, toolsets
from body_engine.assistant.tools import TOOLS, TOOLS_BY_NAME
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_PARITY", "body_test_ai_parity")
NAME = re.compile(r"sparky", re.I)
ALL_SCOPES = ["profile", "metrics", "sleep", "scores", "events", "memory", "journal", "nutrition", "training",
              "weight", "supplements", "labs", "exercises", "reminders"]


# ---------------------------------------------------------------- tool parity (no database)
def _consent_excluded_by_nature() -> set[str]:
    """Tools that read what the fallback consent keeps from DeepSeek: ECG (scope ecg), the raw heart data module and
    other people's data (the friends module)."""
    return {t.name for t in TOOLS if t.scope == "ecg"
            or t.handler.__module__.startswith(("body_engine.cardio", "body_engine.friends"))}


def test_fallback_parity_set_is_everything_but_the_consent_exclusions():
    """Drift guard: a tool added to the registry is in the fallback's set with ai_parity unless the consent excludes it.
    Forgetting it is impossible; excluding one needs CONSENT_EXCLUDED (a consent question for the owner)."""
    names = {t.name for t in fallback.tools(parity=True)}
    assert names == set(TOOLS_BY_NAME) - set(fallback.CONSENT_EXCLUDED)
    assert set(fallback.CONSENT_EXCLUDED) <= set(TOOLS_BY_NAME)                 # every exclusion is a real tool
    assert set(fallback.CONSENT_EXCLUDED) == _consent_excluded_by_nature()     # and nothing else is left out
    assert set(fallback.CONSENT_EXCLUDED.values()) <= {"心电图", "逐拍心跳", "家人的数据"}
    for t in fallback.tools(parity=True):                                      # own data only, as before
        assert "user" not in t.input_schema.get("properties", {}) and "user" not in t.input_schema.get("required", [])
    # what the report named: food, training, write log, meal templates, recipes, memory
    assert {"update_food_entry", "combine_food_entries", "update_training_set", "get_write_log", "rollback_write",
            "log_meal_template", "create_meal_template", "update_meal_template", "undo_meal_log", "create_recipe",
            "update_recipe", "log_recipe", "set_status_items", "delete_observation"} <= names
    assert not set(fallback.CONSENT_EXCLUDED) & set(P.CORE_TOOLS)


def test_every_channel_reaches_the_same_tools():
    """In-page chat with the user's own API key and MCP (/mcp, the body butler) reach the whole registry; the fallback with
    ai_parity the same minus CONSENT_EXCLUDED. Through find_tools: every tool is listed somewhere."""
    api_access = toolsets.Access({t.name: t for t in TOOLS}, True)
    mcp_access = toolsets.Access(TOOLS_BY_NAME, True, frozenset(P.ALWAYS_CONFIRM))
    fb_access = toolsets.Access({t.name: t for t in fallback.tools(parity=True)}, True)
    listed = lambda acc: {n for g in toolsets.find(acc)["groups"] for n in g["tools"]}   # noqa: E731
    assert listed(api_access) == listed(mcp_access) == set(TOOLS_BY_NAME)
    assert listed(fb_access) == set(TOOLS_BY_NAME) - set(fallback.CONSENT_EXCLUDED)


def test_fallback_parity_token_budget():
    """With ai_parity the fallback sends the core + the first group of the page (the rest via find_tools): every page and
    every walk through pages stays under FALLBACK_PARITY_PAGE_BUDGET_CHARS; never the whole set."""
    fb = fallback.tools(parity=True)
    n = P.FALLBACK_PARITY_PAGE_GROUPS
    for page in [None, *P.PAGE_GROUPS]:
        size = toolsets.definition_chars(toolsets.for_page(fb, page, n))
        assert size <= P.FALLBACK_PARITY_PAGE_BUDGET_CHARS, (page, size)
    pages = []
    for page in [*P.PAGE_GROUPS, *reversed(list(P.PAGE_GROUPS))]:
        pages = toolsets.visit_page(fb, pages, page, P.FALLBACK_PARITY_PAGE_BUDGET_CHARS, n)
        assert toolsets.definition_chars(toolsets.for_page(fb, pages, n)) <= P.FALLBACK_PARITY_PAGE_BUDGET_CHARS
    assert toolsets.definition_chars(toolsets.for_page(fb, "food", n)) < toolsets.definition_chars(fb) / 2


def test_confirm_lists_unchanged():
    """Deleting / undoing still asks first in every channel (same list as the butler's settings, tests/test_ai.py)."""
    assert {"undo_sparky_entry", "undo_meal_log", "rollback_write", "delete_observation"} <= P.ALWAYS_CONFIRM
    assert fallback.CONSENT_VERSION == "2026-10-22"   # the consent text is not touched by parity


# ---------------------------------------------------------------- naming (no database)
CASES = {
    "已写进 SparkyFitness 了。": "已写进 底层数据库 了。",
    "I saved it to SparkyFitness.": "I saved it to our database.",
    "It is in the Sparky Fitness database now": "It is in our database now",
    "The SPARKY  fitness app": "Our database app",
    "记在 sparky 里": "记在 底层数据库 里",
    "SparkyFitness 数据库里有 3 条": "底层数据库里有 3 条",
    "Sparky​Fitness": "底层数据库",
    "用户 3 还没连 SparkyFitness：body-engine sparky-link --user-id 3 --key-name ...": "用户 3 还没连 底层数据库",
    "找不到 SparkyFitness 只读 key：/s/sparkyfitness_api_key_a_ro（deploy/sparkyfitness/bootstrap.py 生成）":
        "找不到 底层数据库 只读 key：/s/key",
    "a sparkle and a spark": "a sparkle and a spark",
    "SparkyError: 连不上 SparkyFitness（http://x）：timeout": "DatabaseError: 连不上 底层数据库（http://x）：timeout",
}


def test_scrub_ai_facing_keeps_identifiers():
    for raw, want in CASES.items():
        assert naming.scrub(raw) == want, raw
    assert naming.scrub("删用 `undo_sparky_entry`，看 sparky_error") == "删用 `undo_sparky_entry`，看 sparky_error"
    assert naming.scrub("GET /v1/users/me/training/sparky") == "GET /v1/users/me/training/sparky"
    schema = {"type": "object", "properties": {"sync_sparky": {"type": "boolean", "description": "同步到 SparkyFitness"}}}
    assert naming.scrub_schema(schema) == {"type": "object", "properties": {
        "sync_sparky": {"type": "boolean", "description": "同步到 底层数据库"}}}


def test_scrub_output_and_stream_chunks():
    """What the user sees: identifiers lose the name too; a stream split anywhere gives the same text as the whole."""
    out = {**CASES, "我用 undo_sparky_entry 删了": "我用 undo_entry 删了", "字段 sparky_error 是真": "字段 error 是真"}
    rnd = random.Random(7)
    for raw, want in out.items():
        assert naming.scrub(raw, output=True) == want, raw
        for _ in range(100):
            s, got, i = naming.StreamScrubber(), "", 0
            while i < len(raw):
                k = rnd.randint(1, 6)
                got += s.feed(raw[i:i + k])
                i += k
            assert got + s.flush() == want, (raw, got)
    long = "Logged. " * 40 + "Saved to Sparky" + "Fitness. " + "Done. " * 40
    s = naming.StreamScrubber()
    first = s.feed(long[:330])
    assert len(first) > 300 - naming.HOLD and "sparky" not in first.lower()   # only a short tail is held back
    assert first + s.feed(long[330:]) + s.flush() == long.replace("Sparky" + "Fitness", "our database")


def test_ai_facing_texts_have_no_name_after_scrub():
    """Every tool definition and both system prompts come out clean (identifiers aside)."""
    from body_engine.ai import prompt
    strip_ids = lambda s: re.sub(r"[A-Za-z0-9_\-/.]*sparky[A-Za-z0-9_\-/.]*", "", s, flags=re.I)  # noqa: E731
    for t in TOOLS:
        assert not NAME.search(strip_ids(naming.scrub(t.description))), t.name
        assert not NAME.search(strip_ids(json.dumps(naming.scrub_schema(t.input_schema), ensure_ascii=False))), t.name
    for lite in (False, True):
        assert not NAME.search(strip_ids(naming.scrub(prompt.base_prompt(lite))))
    assert not NAME.search(parity.prompt(True))


# ---------------------------------------------------------------- database
class FakeSparky:
    """One user's food entries, written like SparkyFitness does (enough for update_food_entry / log_food)."""

    def __init__(self, uid, entries):
        self.uid, self.food, self.writes = uid, entries, []

    def get(self, path, **params):
        if path == "/meal-types":
            return [{"id": f"mt-{n}", "name": n} for n in ("breakfast", "lunch", "snacks", "dinner")]
        if path.startswith("/food-entries/by-date/"):
            return [dict(r) for r in self.food if r["entry_date"] == path.rsplit("/", 1)[1]]
        raise AssertionError(path)

    def request(self, method, path, body=None, params=None, multipart_field=None):
        self.writes.append((method, path))
        if method == "PUT" and path.startswith("/food-entries/"):
            row = next(r for r in self.food if r["id"] == path.rsplit("/", 1)[1])
            row.update(body)
            return dict(row)
        raise AssertionError((method, path))


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
def world(dsn, tmp_path_factory):
    d = tmp_path_factory.mktemp("parity")
    mp = pytest.MonkeyPatch()
    mp.setenv("BODY_AI_MASTER_KEY_FILE", str(d / "ai_master_key"))
    crypto.init_master_key()
    (d / "deepseek_api_key").write_text("test-parity-key-not-real\n")
    mp.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(d / "deepseek_api_key"))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO users (display_name, role) VALUES ('O', 'owner')")
        ids = {n: conn.execute("""INSERT INTO users (display_name, role, consent_at, timezone)
                                  VALUES (%s, 'member', %s, 'Australia/Sydney') RETURNING id""",
                               (n, datetime.now(timezone.utc))).fetchone()["id"] for n in ("P", "Q", "R")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["Q"], ids["P"], ALL_SCOPES))
        for n in ("P", "R"):
            conn.execute("INSERT INTO ai_fallback_consents (user_id, version) VALUES (%s, %s)", (ids[n], fallback.CONSENT_VERSION))
        conn.execute("UPDATE ai_fallback_state SET enabled = true")
        flags.sync(conn)
        flags.set_flag(conn, parity.FLAG, "cohort", cohort="beta", actor="test")
        flags.cohort_add(conn, ids["P"], actor="test")
    yield ids, tok
    mp.undo()
    flags.invalidate()


@pytest.fixture(scope="module")
def client(dsn, world):
    mp = pytest.MonkeyPatch()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    with TestClient(api.app, base_url="https://testserver") as c:
        yield c
    mp.undo()


@pytest.fixture()
def sparky(world, monkeypatch):
    """Each user's own fake back end, keyed by the user id the write client is made for."""
    ids, _ = world
    day = date.today().isoformat()
    fakes = {uid: FakeSparky(uid, [{"id": f"fe-{n}", "food_name": "米饭", "meal_type": "lunch", "entry_date": day,
                                     "entry_time": "12:00:00", "quantity": 1, "unit": "碗", "serving_size": 1,
                                     "calories": 200, "protein": 4, "carbs": 44, "fat": 1}])
             for n, uid in ids.items()}
    monkeypatch.setattr(sparky_write.sparky_client, "write_client_for_user", lambda conn, uid: fakes[uid])
    return fakes, day


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    for kind in ("anthropic_api", "openai_api", "gemini_api", "openai_compatible", "system_fallback"):
        monkeypatch.setitem(adapters.ADAPTERS, kind, lambda: FakeAdapter)
    FakeAdapter.script, FakeAdapter.calls = [], []
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 50.0)
    flags.invalidate()


def H(token):
    return {"Authorization": f"Bearer {token}"}


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_fallback_edits_own_food_and_never_someone_elses(client, world, sparky):
    """The report: the fallback could add a food entry but not change one. With ai_parity it can, directly and through
    run_write_tool; `user` pointing at someone who shares with P is ignored (forced to P), so Q's entry is untouched."""
    ids, tok = world
    fakes, day = sparky
    p, q = ids["P"], ids["Q"]
    FakeAdapter.script = [
        {"tool_calls": [{"id": "u1", "name": "update_food_entry", "args": {"date": day, "id": "fe-P", "portion": 0.5}},
                        {"id": "u2", "name": "run_write_tool", "args": {"name": "update_food_entry", "args": {
                            "date": day, "id": "fe-Q", "portion": 2, "user": str(q)}}}]},
        {"text": "改好了"}]
    r = client.post("/v1/assistant/chat", headers=H(tok["P"]), json={"message": "米饭只吃了半碗", "page": "food"})
    assert r.status_code == 200, r.text
    first = FakeAdapter.calls[0]
    assert "update_food_entry" in first["tools"]                       # resident: it is in the core
    assert first["tools"] == [t.name for t in toolsets.for_page(fallback.tools(parity=True), "food", 1)]
    results = {b["id"]: b for b in FakeAdapter.calls[1]["history"][-1]["content"]}
    assert not results["u1"]["is_error"], results["u1"]
    assert fakes[p].food[0]["quantity"] == 0.5
    assert results["u2"]["is_error"]                                   # P has no fe-Q: the call ran as P
    assert fakes[q].writes == [] and fakes[q].food[0]["quantity"] == 1
    assert ("tool", {"name": "update_food_entry", "status": "done"}) in sse(r.text)


def test_flag_off_keeps_the_old_whitelist(client, world, sparky):
    ids, tok = world
    fakes, day = sparky
    FakeAdapter.script = [{"tool_calls": [{"id": "u1", "name": "update_food_entry",
                                           "args": {"date": day, "id": "fe-R", "portion": 0.5}}]}, {"text": "好"}]
    r = client.post("/v1/assistant/chat", headers=H(tok["R"]), json={"message": "米饭只吃了半碗"})
    assert r.status_code == 200, r.text
    assert FakeAdapter.calls[0]["tools"] == [t.name for t in toolsets.for_page(fallback.tools(), None)]
    assert "update_food_entry" not in FakeAdapter.calls[0]["tools"]
    res = FakeAdapter.calls[1]["history"][-1]["content"][0]
    assert res["is_error"] and fakes[ids["R"]].writes == []


def test_names_scrubbed_in_chat(client, world, dsn, monkeypatch):
    """Flag on: no back end name in the system prompt, tool definitions, tool results, the streamed answer (split across
    chunks), the stored message or the reopened conversation. Flag off: the name goes through as before."""
    ids, tok = world

    def unlinked(conn, uid):
        raise sparky_client.SparkyError(f"用户 {uid} 还没连 SparkyFitness：body-engine sparky-link --user-id {uid} --key-name ...")
    monkeypatch.setattr(sparky_write.sparky_client, "write_client_for_user", unlinked)
    script = [{"tool_calls": [{"id": "w1", "name": "log_weight", "args": {"date": date.today().isoformat(), "weight_kg": 80}}]},
              {"text": "没写进 Spar|ky|Fitness，先在 sparky fit|ness 里连上；undo_spar|ky_entry 也用不了"}]
    FakeAdapter.script = list(script)
    r = client.post("/v1/assistant/chat", headers=H(tok["P"]), json={"message": "体重 80"})
    assert r.status_code == 200, r.text
    first, second = FakeAdapter.calls
    clean = lambda s: re.sub(r"[A-Za-z0-9_\-/.]*sparky[A-Za-z0-9_\-/.]*", "", s, flags=re.I)  # noqa: E731
    assert not NAME.search(clean(first["system"])) and "底层数据库" in first["system"]
    assert not NAME.search(clean(json.dumps(first["tool_defs"], ensure_ascii=False)))
    result = second["history"][-1]["content"][0]["content"]
    assert result == "DatabaseError: 用户 {} 还没连 底层数据库".format(ids["P"])
    text = "".join(d["delta"] for e, d in sse(r.text) if e == "text")
    want = "没写进 底层数据库，先在 底层数据库 里连上；undo_entry 也用不了"
    assert text == want
    conv = next(d for e, d in sse(r.text) if e == "conversation")["id"]
    view = client.get(f"/v1/assistant/conversations/{conv}", headers=H(tok["P"])).json()
    assert view["messages"][-1]["text"] == want
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        stored = c.execute("SELECT content FROM ai_messages WHERE conversation_id = %s AND role = 'assistant' ORDER BY id DESC",
                           (conv,)).fetchone()["content"]
        assert stored[0]["text"] == want
    FakeAdapter.calls = []
    FakeAdapter.script = list(script)
    r = client.post("/v1/assistant/chat", headers=H(tok["R"]), json={"message": "体重 80"})   # flag off: as before
    assert "".join(d["delta"] for e, d in sse(r.text) if e == "text") == script[1]["text"].replace("|", "")
    assert "SparkyFitness" in FakeAdapter.calls[1]["history"][-1]["content"][0]["content"]


def test_names_scrubbed_on_mcp(client, world):
    ids, tok = world
    mcp_h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

    def rpc(token, method, params=None):
        return client.post("/mcp", headers={**mcp_h, "Authorization": f"Bearer {token}"},
                           json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).json()["result"]
    found = {}
    for n in ("P", "R"):
        t = client.post("/v1/users/me/ai-tokens", headers=H(tok[n]), json={"client": "pytest"}).json()["token"]
        rpc(t, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}})
        tools = rpc(t, "tools/list")["tools"]
        listed = json.dumps([{"d": x["description"], "s": x["inputSchema"]} for x in tools], ensure_ascii=False)
        found[n] = rpc(t, "tools/call", {"name": "find_tools", "arguments": {"group": "training"}})["content"][0]["text"]
        prompt = rpc(t, "prompts/get", {"name": "butler"})["messages"][0]["content"]["text"]
        if n == "P":
            assert "SparkyFitness" not in listed and "SparkyFitness" not in found[n] and "SparkyFitness" not in prompt
            assert "底层数据库" in prompt and "undo_sparky_entry" in listed      # tool names stay callable
        else:
            assert "SparkyFitness" in prompt                                     # flag off: unchanged
    assert "SparkyFitness" in found["R"]


def test_page_error_texts(client, world, monkeypatch):
    """HTTP error details on the Today page lose the name for users with the flag on (codes stay as they are)."""
    ids, tok = world
    from fastapi import HTTPException

    @api.app.get("/v1/test-parity-error")
    def boom(viewer=api.Depends(api.current_user)):
        raise HTTPException(503, {"code": "sparky_down", "message": "连不上 SparkyFitness（http://x）：timeout"})
    try:
        on = client.get("/v1/test-parity-error", headers=H(tok["P"])).json()["detail"]
        off = client.get("/v1/test-parity-error", headers=H(tok["R"])).json()["detail"]
    finally:
        api.app.router.routes = [r for r in api.app.router.routes if getattr(r, "path", "") != "/v1/test-parity-error"]
    assert on == {"code": "sparky_down", "message": "连不上 底层数据库（http://x）：timeout"}
    assert off["message"] == "连不上 SparkyFitness（http://x）：timeout"


def test_scrub_event_keeps_names_and_codes():
    e = chat.ev("tool", name="undo_sparky_entry", status="error", message="SparkyFitness 不可用")
    assert chat._scrub_event(e) == chat.ev("tool", name="undo_sparky_entry", status="error", message="底层数据库 不可用")
