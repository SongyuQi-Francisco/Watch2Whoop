"""Runs the example the way a client does: real processes on both transports, through the MCP SDK's client. No database
server and nothing of Watch2Whoop's business code: the framework's contract checks (datalayer.testing) prove the second."""
import json
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import anyio
import httpx2
import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

import reading_notes
from body_engine.datalayer import ENGLISH, Budgets, testing, toolsets

HERE = Path(__file__).parent
SCRIPT = str(HERE / "reading_notes.py")
LISTED = ["list_books", "get_notes", "add_note", "delete_note", "rollback_write", "find_tools", "run_read_tool", "run_write_tool"]


def _json(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def _error(result) -> str:
    assert result.is_error, result.content[0].text
    return result.content[0].text


# ---------------------------------------------------------------- the framework's contract checks
def test_the_example_only_imports_the_framework():
    allowed = testing.ALLOWED_THIRD_PARTY | {"uvicorn", "pytest", "httpx2", "reading_notes", "sqlite_store"}
    assert testing.check_imports(HERE, "body_engine", "body_engine.datalayer", allowed) == []
    # and at run time: nothing of the application under the framework was loaded along the way
    code = ("import sys, reading_notes\n"
            "print([m for m in sys.modules if m.startswith('body_engine.') and not m.startswith('body_engine.datalayer')])")
    done = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True, timeout=120, check=True)
    assert done.stdout.strip() == "[]"


def test_the_contract_checks_pass():
    app = reading_notes.APP
    assert testing.check_handlers(app.tools) == []
    budgets = Budgets(core=1000, meta=1500, listing=2500, context=2500, groups={"notes": 1500, "history": 500, "guides": 300})
    assert testing.check_toolset(app.toolset(), app.tools, budgets) == []
    assert testing.check_messages(ENGLISH) == []
    assert app.confirm == {"delete_note", "rollback_write"}
    assert app.executor.messages is ENGLISH and app.toolset().messages is ENGLISH


def test_the_playbook_only_mentions_tools_that_exist():
    assert reading_notes.PLAYBOOKS.tools_mentioned("book_review") == {"list_books", "get_notes", "add_note"}
    assert reading_notes.PLAYBOOKS.tools_mentioned("book_review") <= set(reading_notes.APP.by_name)


def test_the_listing_is_the_core_the_confirm_tools_and_the_meta_tools():
    app = reading_notes.APP
    assert [t.name for t in toolsets.for_listing(app.toolset(), app.tools, True)] == LISTED


# ---------------------------------------------------------------- stdio: one process, one person
def _stdio(db, *flags, user=1):
    return Client(StdioServerParameters(command=sys.executable, args=[SCRIPT, "stdio", "--db", str(db), "--user", str(user), *flags]))


def test_stdio_reads_writes_and_undoes(tmp_path):
    db = tmp_path / "notes.db"

    async def run():
        async with _stdio(db) as c:
            assert [t.name for t in (await c.list_tools()).tools] == LISTED
            assert _json(await c.call_tool("list_books", {})) == []
            first = _json(await c.call_tool("add_note", {"book": "Book A", "text": "Example note one."}))["id"]
            assert _json(await c.call_tool("list_books", {})) == [{"book": "Book A", "notes": 1}]
            # a tool that is not in the list: find it, then run it through the meta tool
            found = _json(await c.call_tool("find_tools", {"query": "text"}))
            assert found["tools"][0]["name"] == "edit_note" and found["tools"][0]["run"] == "run_write_tool"
            _json(await c.call_tool("run_write_tool", {"name": "edit_note", "args": {"note_id": first, "text": "Changed."}}))
            # what the AI changed, and undoing it
            log = _json(await c.call_tool("run_read_tool", {"name": "get_write_log"}))
            assert [(r["tool"], r["rollback"]) for r in log] == [("edit_note", "ok"), ("add_note", "ok")]
            newest = log[0]["id"]
            assert _json(await c.call_tool("rollback_write", {"log_id": newest}))["rolled_back"] == newest
            assert "Example note one." in (await c.call_tool("get_notes", {"book": "Book A"})).content[0].text
            assert "already rolled back" in _error(await c.call_tool("rollback_write", {"log_id": newest}))
            # the delete asks first: only by its own name, which is what a client's approval rule goes by
            assert "by its own name" in _error(await c.call_tool("run_write_tool", {"name": "delete_note", "args": {"note_id": first}}))
            assert _json(await c.call_tool("delete_note", {"note_id": first})) == {"deleted": first}
            assert "Missing argument note_id" in _error(await c.call_tool("delete_note", {}))
            # playbook: the prompt and the tool are the same text
            assert [p.name for p in (await c.list_prompts()).prompts] == ["book_review"]
            prompt = (await c.get_prompt("book_review")).messages[0].content.text
            assert _json(await c.call_tool("run_read_tool", {"name": "get_playbook", "args": {"name": "book_review"}}))["text"] == prompt
            # resources are off unless asked for: not announced, and the methods do not exist
            with pytest.raises(MCPError, match="(?i)method not found"):
                await c.list_resources()
    anyio.run(run)

    audit = sqlite3.connect(db).execute("SELECT tool, ok, channel FROM audit ORDER BY id").fetchall()
    assert ("edit_note", 1, "mcp:stdio") in audit                 # the audit records the real tool, not run_write_tool
    assert ("delete_note", 0, "mcp:stdio") in audit               # failed calls too
    assert not any(row[0].startswith("run_") for row in audit)


def test_a_failed_tool_undoes_only_itself_and_stays_in_the_audit(tmp_path):
    db = tmp_path / "notes.db"
    reading_notes.init_db(db)
    ex = reading_notes.APP.executor
    with reading_notes.Db(db) as conn:
        with pytest.raises(ValueError, match="No note 99"):
            try:
                ex.run(conn, 1, "test", "delete_note", {"note_id": 99})
            except ValueError:
                conn.commit()    # what the MCP transport does: the audit row stays, the tool's half-done work does not
                raise
        with conn.transaction():
            conn.execute("INSERT INTO notes (user_id, book, text) VALUES (1, 'Kept', 'x')")
        with pytest.raises(RuntimeError):
            with conn.transaction():
                conn.execute("INSERT INTO notes (user_id, book, text) VALUES (1, 'Undone', 'x')")
                raise RuntimeError
    rows = sqlite3.connect(db)
    assert rows.execute("SELECT tool, ok FROM audit").fetchall() == [("delete_note", 0)]
    assert rows.execute("SELECT count(*) FROM write_log").fetchone() == (0,)
    assert rows.execute("SELECT book FROM notes WHERE user_id = 1").fetchall() == [("Kept",)]


# ---------------------------------------------------------------- Streamable HTTP: many people, a token per request
@pytest.fixture
def http_url(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = tmp_path / "server.log"
    with log.open("w") as out:
        server = subprocess.Popen([sys.executable, SCRIPT, "http", "--db", str(tmp_path / "notes.db"), "--port", str(port), "--resources"],
                                  stdout=out, stderr=out)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert server.poll() is None, log.read_text()
                time.sleep(0.2)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.terminate()
        server.wait(timeout=10)


def _http(url, token):
    client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30)
    return Client(streamable_http_client(url, http_client=client))


def test_http_needs_a_token(http_url):
    hello = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    plain = httpx2.post(http_url, json=hello, timeout=30)
    assert plain.status_code == 401 and plain.json()["detail"] == ENGLISH.mcp_missing_bearer
    bad = httpx2.post(http_url, json=hello, headers={"Authorization": "Bearer nope"}, timeout=30)
    assert bad.status_code == 401 and bad.json() == {"error": "invalid_token", "detail": ENGLISH.mcp_invalid_token}


def test_http_each_token_is_one_person(http_url):
    async def run():
        async with _http(http_url, "ann-demo") as ann:
            # Ben shared his notes with Ann: she reads them, and cannot write as him
            assert _json(await ann.call_tool("list_books", {"user": "2"})) == [{"book": "Example Book", "notes": 1}]
            assert ENGLISH.write_only_self in _error(await ann.call_tool("add_note", {"user": "2", "book": "B", "text": "x"}))
            mine = _json(await ann.call_tool("add_note", {"book": "Ann's book", "text": "Example note."}))["id"]
            assert _json(await ann.call_tool("list_books", {})) == [{"book": "Ann's book", "notes": 1}]
        async with _http(http_url, "ben-demo") as ben:
            # Ann shared nothing with Ben, and her notes are not his to change
            assert ENGLISH.no_grant.format(owner=1, scope="notes") in _error(await ben.call_tool("get_notes", {"user": "1"}))
            assert f"No note {mine}" in _error(await ben.call_tool("delete_note", {"note_id": mine}))
        async with _http(http_url, "cai-demo-read-only") as cai:
            # a read-only token sees no write tool and cannot reach one by name or through the meta tool
            assert [t.name for t in (await cai.list_tools()).tools] == ["list_books", "get_notes", "find_tools", "run_read_tool"]
            assert _error(await cai.call_tool("add_note", {"book": "B", "text": "x"})) == ENGLISH.mcp_read_only
            assert ENGLISH.read_only in _error(await cai.call_tool("run_write_tool", {"name": "add_note", "args": {"book": "B", "text": "x"}}))
    anyio.run(run)


def test_http_resources_when_asked_for(http_url):
    async def run():
        async with _http(http_url, "ann-demo") as ann:
            listed = (await ann.list_resources()).resources
            assert [str(r.uri) for r in listed] == ["datalayer://playbooks/book_review", "datalayer://scopes", "datalayer://rules/root"]
            read = {str(r.uri): (await ann.read_resource(str(r.uri))).contents[0].text for r in listed}
            assert read["datalayer://playbooks/book_review"] == (await ann.get_prompt("book_review")).messages[0].content.text
            assert json.loads(read["datalayer://scopes"]) == reading_notes.SCOPES
            assert read["datalayer://rules/root"] == reading_notes.RULES
            with pytest.raises(MCPError, match="No such resource"):
                await ann.read_resource("datalayer://nope")
    anyio.run(run)
