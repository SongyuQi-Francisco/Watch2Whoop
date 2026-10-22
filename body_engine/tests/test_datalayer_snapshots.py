"""Behaviour snapshots of the AI tool layer, taken before it moves into body_engine/datalayer/ (issue #116,
docs/datalayer-design.md section 4, phase 1). Every later phase must leave these files byte for byte unchanged.

What is pinned (tests/fixtures/datalayer/*.json):
- mcp_tools_list.json: MCP tools/list, every field and the order, for a write token and a read-only token;
- mcp_prompts.json: the server instructions and prompts/list;
- chat_toolsets.json: what the in-page chat sends on every Today page (owner AI, fallback AI, fallback AI with the
  ai_web grocery tools), the page accumulation over all pages, and the definitions of every tool it can send;
- fallback.json: the fallback AI's whitelist, ALWAYS_CONFIRM and the core list;
- find_tools.json: find_tools without arguments (write token, read-only token, fallback AI);
- guardrails.json: the exact error texts of the MCP guardrails (read-only, cross-user, read / write mixed up, confirm
  tools called through run_write_tool, unknown tool, missing arguments);
- audit.json: the assistant_audit and ai_write_log rows a few representative calls leave (MCP read / write / meta
  write / failure, an in-page chat call with a model). Needs Postgres: test DB BODY_TEST_DB_NAME (default
  body_test_datalayer), dropped and recreated on every run.

Files are compared as text, so key order (e.g. the order of a schema's properties) counts too.
To rewrite them after an intentional change: BODY_WRITE_GOLDEN=1 uv run pytest tests/test_datalayer_snapshots.py
"""
import difflib
import json
import os
import re
from pathlib import Path

import anyio
import mcp.types as types
import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine import mcp_server
from body_engine.ai import cards, fallback
from body_engine.ai import params as P
from body_engine.ai import web
from body_engine.assistant import toolsets
from body_engine.assistant.tools import TOOLS, run_tool
from body_engine.config import Settings
from body_engine.db import migrate

GOLDEN = Path(__file__).parent / "fixtures" / "datalayer"
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_datalayer")


def _text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str) + "\n"


def check(name: str, obj) -> None:
    path = GOLDEN / name
    got = _text(obj)
    if os.environ.get("BODY_WRITE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(got, encoding="utf-8")
        return
    want = path.read_text(encoding="utf-8")
    if got != want:
        diff = "".join(list(difflib.unified_diff(want.splitlines(True), got.splitlines(True), f"golden/{name}", "now"))[:80])
        pytest.fail(f"{name} changed (rewrite with BODY_WRITE_GOLDEN=1 only for an intentional change):\n{diff}")


# ---------------------------------------------------------------- MCP server, driven through its request handlers
def _server(viewer_id: int = 1, can_write: bool = True, channel: str = "mcp:snapshot", connect_fn=None):
    ident = mcp_server.Identity(viewer_id, channel, can_write)
    kw = {"connect_fn": connect_fn} if connect_fn else {}
    return mcp_server.build_server(lambda ctx: ident, **kw)


def _request(server, method: str, params=None):
    return anyio.run(server.get_request_handler(method).handler, None, params)


def _dump(model) -> object:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _call(server, name: str, args: dict | None = None) -> dict:
    r = _request(server, "tools/call", types.CallToolRequestParams(name=name, arguments=args))
    return {"is_error": bool(r.is_error), "text": r.content[0].text}


def test_mcp_tools_list():
    check("mcp_tools_list.json", {
        "write": _dump(_request(_server(can_write=True), "tools/list"))["tools"],
        "read_only": _dump(_request(_server(can_write=False), "tools/list"))["tools"],
    })


def test_mcp_instructions_and_prompts():
    server = _server()
    check("mcp_prompts.json", {"instructions": server.instructions,
                               "prompts": _dump(_request(server, "prompts/list"))["prompts"]})


def test_find_tools_index():
    fb = toolsets.Access({t.name: t for t in fallback.tools()}, True)
    check("find_tools.json", {
        "mcp_write": json.loads(_call(_server(can_write=True), toolsets.FIND)["text"]),
        "mcp_read_only": json.loads(_call(_server(can_write=False), toolsets.FIND)["text"]),
        "fallback": toolsets.find(fb),
    })


def test_guardrail_messages():
    w, ro = _server(can_write=True), _server(can_write=False)
    check("guardrails.json", {
        "read_only_direct_write": _call(ro, "add_event", {"occurred_at": "2026-01-01T08:00", "title": "示例"}),
        "read_only_run_write_tool": _call(ro, toolsets.RUN_WRITE, {"name": "add_event", "args": {}}),
        "read_only_hidden_from_find": _call(ro, toolsets.FIND, {"query": "add_event"}),
        "write_through_run_read_tool": _call(w, toolsets.RUN_READ, {"name": "add_event", "args": {}}),
        "read_through_run_write_tool": _call(w, toolsets.RUN_WRITE, {"name": "get_profile", "args": {}}),
        "confirm_tool_through_run_write_tool": _call(w, toolsets.RUN_WRITE, {"name": "delete_journal_entry", "args": {}}),
        "unknown_tool": _call(w, "no_such_tool", {}),
        "unknown_tool_through_meta": _call(w, toolsets.RUN_READ, {"name": "no_such_tool"}),
        "meta_tool_through_meta": _call(w, toolsets.RUN_READ, {"name": toolsets.FIND}),
        "args_not_an_object": _call(w, toolsets.RUN_READ, {"name": "get_profile", "args": "{oops"}),
        "missing_args": _call(w, toolsets.RUN_WRITE, {"name": "add_event", "args": {"title": "示例"}}),
        "unknown_group": _call(w, toolsets.FIND, {"group": "no_such_group"}),
    })


# ---------------------------------------------------------------- in-page chat tool sets and the fallback whitelist
def _names(tools) -> list[str]:
    return [t.name for t in tools]


def _per_page(allowed, budget: int) -> dict:
    allowed = tuple(allowed)
    out = {"(none)": _names(toolsets.for_page(allowed, toolsets.fit_pages(allowed, [], budget)))}
    for page in P.PAGE_GROUPS:
        out[page] = _names(toolsets.for_page(allowed, toolsets.fit_pages(allowed, [page], budget)))
    pages: list[str] = []
    for page in P.PAGE_GROUPS:   # a conversation that walks over every page in turn: accumulation and the budget cap
        pages = toolsets.visit_page(allowed, pages, page, budget)
    out["(all pages visited)"] = {"pages": pages, "tools": _names(toolsets.for_page(allowed, pages))}
    return out


def test_chat_toolsets_per_page():
    fb, fb_web = fallback.tools(), fallback.tools(web.FALLBACK_GROCERY)
    check("chat_toolsets.json", {
        "owner": _per_page(TOOLS, P.PAGE_BUDGET_CHARS),
        "fallback": _per_page(fb, P.FALLBACK_PAGE_BUDGET_CHARS),
        "fallback_web": _per_page(fb_web, P.FALLBACK_PAGE_BUDGET_CHARS),
        "definitions": {t.name: t.api_definition() for t in (*TOOLS, *toolsets.META_TOOLS)},
        # the fallback's own schemas (no `user`); fb_web is a superset of fb
        "fallback_definitions": {t.name: t.api_definition() for t in fb_web},
        "extra_definitions": {t.name: t.api_definition() for t in (cards.TOOL, web.SEARCH_TOOL, web.FETCH_TOOL)},
    })


def test_fallback_whitelist():
    check("fallback.json", {
        "TOOL_NAMES": list(fallback.TOOL_NAMES),
        "tools": _names(fallback.tools()),
        "tools_with_web": _names(fallback.tools(web.FALLBACK_GROCERY)),
        "FALLBACK_GROCERY": list(web.FALLBACK_GROCERY),
        "ALWAYS_CONFIRM": sorted(P.ALWAYS_CONFIRM),
        "CORE_TOOLS": list(P.CORE_TOOLS),
        "all_tools": [{"name": t.name, "group": t.group, "scope": t.scope, "writes": t.writes, "strict": t.strict}
                      for t in TOOLS],
    })


# ---------------------------------------------------------------- audit and write-log rows (Postgres)
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


_VOLATILE = {"id", "created_at", "at", "rolled_back_at", "rolled_back_by"}


def _scrub(obj, ids: dict):
    """Ids and timestamps vary between runs: user ids become their role, other ids / times placeholders."""
    if isinstance(obj, dict):
        return {k: ("<volatile>" if k in _VOLATILE and v is not None else _scrub(v, ids)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v, ids) for v in obj]
    if isinstance(obj, str):
        for uid, role in ids.items():
            obj = re.sub(rf"(?<!\d){uid}(?!\d)", role, obj)
        return obj
    return obj


def test_audit_and_write_log_rows(dsn):
    def connect_fn():
        return psycopg.connect(dsn, row_factory=dict_row)

    with connect_fn() as conn:
        a, b = (conn.execute("""INSERT INTO users (display_name, birth_date, sex, height_cm, timezone)
                                VALUES (%s, '1990-01-01', 'male', 180, 'Australia/Sydney') RETURNING id""",
                             (n,)).fetchone()["id"] for n in ("示例A", "示例B"))
    ids = {a: "<viewer>", b: "<other>"}
    w = _server(a, True, "mcp:snapshot", connect_fn)
    calls = {
        "read": _call(w, "get_profile", {}),
        "write": _call(w, "add_event", {"occurred_at": "2026-01-01T08:00", "title": "示例事件", "category": "other"}),
        "meta_write": _call(w, toolsets.RUN_WRITE,
                            {"name": "add_event", "args": {"occurred_at": "2026-01-02T08:00", "title": "示例二"}}),
        "cross_user_read": _call(w, "get_profile", {"user": str(b)}),
        "handler_error": _call(w, "get_sleep", {"start": "not-a-date", "end": "2026-01-01"}),
    }
    with connect_fn() as conn:   # the in-page chat path: provider_id / model columns
        run_tool(conn, a, "chat:snapshot", "list_metrics", {"user": "me"}, model="example-model")
        conn.commit()
        audit = conn.execute("""SELECT user_id, channel, tool, args, ok, error, provider_id, model
                                FROM assistant_audit ORDER BY id""").fetchall()
        writes = conn.execute("""SELECT user_id, channel, tool, kind, action, target, before, after, rolled_back_at
                                 FROM ai_write_log ORDER BY id""").fetchall()
    for r in (*audit, *writes):
        r["user_id"] = ids.get(r["user_id"], r["user_id"])
    for c in calls.values():   # get_profile: now_local / weekday / age depend on the clock
        if not c["is_error"] and c["text"].startswith("{"):
            c["text"] = json.loads(c["text"])
            for k in ("now_local", "weekday", "age"):
                if k in c["text"]:
                    c["text"][k] = "<clock>"
    check("audit.json", _scrub({"results": calls, "assistant_audit": audit, "ai_write_log": writes}, ids))
