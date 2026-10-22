"""Behaviour snapshots of the AI tool layer, taken before it moves into body_engine/datalayer/ (issue #116,
docs/datalayer-design.md section 4, phase 1). Every later phase must leave these files byte for byte unchanged.

What is pinned (tests/fixtures/datalayer/*.json):
- mcp_tools_list.json: MCP tools/list, every field and the order, for a write token and a read-only token, with the
  rollout flag ai_parity off and on (on: names scrubbed, ai/naming.py);
- mcp_prompts.json: the server instructions and prompts/list;
- chat_toolsets.json: what the in-page chat sends on every Today page (owner AI, fallback AI, fallback AI with the
  ai_web grocery tools, fallback AI with ai_parity: one group per page), the page accumulation over all pages, the
  definitions of every tool it can send, and those ai_parity changes (as ai/chat._scrub_tool sends them);
- fallback.json: the fallback AI's whitelist (also with ai_web / ai_parity), ALWAYS_CONFIRM and the core list;
- find_tools.json: find_tools without arguments (write token, read-only token, fallback AI; ai_parity on);
- guardrails.json: the exact error texts of the MCP guardrails (read-only, cross-user, read / write mixed up, confirm
  tools called through run_write_tool, unknown tool, missing arguments);
- audit.json: the assistant_audit and ai_write_log rows a few representative calls leave (MCP read / write / meta
  write / failure, an in-page chat call with a model).
The MCP parts and audit.json need Postgres (MCP reads the ai_parity flag per call): test DB BODY_TEST_DB_NAME (default
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
from body_engine.ai import cards, chat, fallback, parity
from body_engine.ai import params as P
from body_engine.ai import web
from body_engine.assistant import toolsets
from body_engine.assistant.tools import TOOLS, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags

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


# ---------------------------------------------------------------- test database
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
def world(dsn):
    """Three users: the viewer (ai_parity off), a second viewer with ai_parity on, and another user (no share)."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        a, p, b = (conn.execute("""INSERT INTO users (display_name, birth_date, sex, height_cm, timezone)
                                   VALUES (%s, '1990-01-01', 'male', 180, 'Australia/Sydney') RETURNING id""",
                                (n,)).fetchone()["id"] for n in ("示例A", "示例P", "示例B"))
        flags.sync(conn)
        flags.set_flag(conn, parity.FLAG, "cohort", cohort="beta", actor="test")
        flags.cohort_add(conn, p, actor="test")
    flags.invalidate()
    yield {"viewer": a, "parity": p, "other": b, "connect": lambda: psycopg.connect(dsn, row_factory=dict_row)}
    flags.invalidate()


# ---------------------------------------------------------------- MCP server, driven through its request handlers
def _server(world, can_write: bool = True, who: str = "viewer", channel: str = "mcp:list"):
    ident = mcp_server.Identity(world[who], channel, can_write)
    return mcp_server.build_server(lambda ctx: ident, world["connect"])


def _request(server, method: str, params=None):
    return anyio.run(server.get_request_handler(method).handler, None, params)


def _dump(model) -> object:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _call(server, name: str, args: dict | None = None) -> dict:
    r = _request(server, "tools/call", types.CallToolRequestParams(name=name, arguments=args))
    return {"is_error": bool(r.is_error), "text": r.content[0].text}


def test_mcp_tools_list(world):
    check("mcp_tools_list.json", {
        "write": _dump(_request(_server(world, True), "tools/list"))["tools"],
        "read_only": _dump(_request(_server(world, False), "tools/list"))["tools"],
        "write_ai_parity": _dump(_request(_server(world, True, "parity"), "tools/list"))["tools"],
        "read_only_ai_parity": _dump(_request(_server(world, False, "parity"), "tools/list"))["tools"],
    })


def test_mcp_instructions_and_prompts(world):
    server = _server(world)
    check("mcp_prompts.json", {"instructions": server.instructions,
                               "prompts": _dump(_request(server, "prompts/list"))["prompts"]})


def test_find_tools_index(world):
    fb = toolsets.Access({t.name: t for t in fallback.tools()}, True)
    fb_parity = toolsets.Access({t.name: t for t in fallback.tools(parity=True)}, True)
    check("find_tools.json", {
        "mcp_write": json.loads(_call(_server(world, True), toolsets.FIND)["text"]),
        "mcp_read_only": json.loads(_call(_server(world, False), toolsets.FIND)["text"]),
        "fallback": toolsets.find(fb),
        "mcp_write_ai_parity": json.loads(_call(_server(world, True, "parity"), toolsets.FIND)["text"]),
        "fallback_ai_parity": toolsets.find(fb_parity),
    })


def test_guardrail_messages(world):
    w, ro = _server(world, True), _server(world, False)
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
        "ai_parity_confirm_tool_through_run_write_tool": _call(_server(world, True, "parity"), toolsets.RUN_WRITE,
                                                               {"name": "undo_sparky_entry", "args": {}}),
    })


# ---------------------------------------------------------------- in-page chat tool sets and the fallback whitelist
def _names(tools) -> list[str]:
    return [t.name for t in tools]


def _per_page(allowed, budget: int, per_page: int | None = None) -> dict:
    """As ai/chat._turns builds it: for_page(allowed, fit_pages(allowed, pages, budget, per_page), per_page)."""
    allowed = tuple(allowed)

    def sent(pages):
        return _names(toolsets.for_page(allowed, toolsets.fit_pages(allowed, pages, budget, per_page), per_page))
    out = {"(none)": sent([])}
    for page in P.PAGE_GROUPS:
        out[page] = sent([page])
    pages: list[str] = []
    for page in P.PAGE_GROUPS:   # a conversation that walks over every page in turn: accumulation and the budget cap
        pages = toolsets.visit_page(allowed, pages, page, budget, per_page)
    out["(all pages visited)"] = {"pages": pages, "tools": sent(pages)}
    return out


def _scrubbed(tools) -> dict:
    """The definitions ai_parity changes, as the in-page chat sends them."""
    out = {}
    for t in tools:
        d = chat._scrub_tool(t).api_definition()
        if d != t.api_definition():
            out[t.name] = d
    return out


def test_chat_toolsets_per_page():
    fb, fb_web, fb_parity = fallback.tools(), fallback.tools(web.FALLBACK_GROCERY), fallback.tools(parity=True)
    extras = (cards.TOOL, web.SEARCH_TOOL, web.FETCH_TOOL)
    check("chat_toolsets.json", {
        "owner": _per_page(TOOLS, P.PAGE_BUDGET_CHARS),
        "fallback": _per_page(fb, P.FALLBACK_PAGE_BUDGET_CHARS),
        "fallback_web": _per_page(fb_web, P.FALLBACK_PAGE_BUDGET_CHARS),
        "definitions": {t.name: t.api_definition() for t in (*TOOLS, *toolsets.META_TOOLS)},
        # the fallback's own schemas (no `user`); fb_web is a superset of fb
        "fallback_definitions": {t.name: t.api_definition() for t in fb_web},
        "extra_definitions": {t.name: t.api_definition() for t in extras},
        "fallback_ai_parity": _per_page(fb_parity, P.FALLBACK_PARITY_PAGE_BUDGET_CHARS, P.FALLBACK_PARITY_PAGE_GROUPS),
        # the parity fallback's own schemas that fallback_definitions does not have yet
        "fallback_ai_parity_definitions": {t.name: t.api_definition() for t in fb_parity
                                           if t.name not in {f.name for f in fb_web}},
        "cards_v2_definition": cards.TOOL_V2.api_definition(),
        "ai_parity_scrubbed_definitions": _scrubbed((*TOOLS, *toolsets.META_TOOLS, *extras, cards.TOOL_V2)),
        "ai_parity_scrubbed_fallback_definitions": _scrubbed(fb_parity),
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
        "tools_ai_parity": _names(fallback.tools(parity=True)),
        "CONSENT_EXCLUDED": sorted(fallback.CONSENT_EXCLUDED),
    })


# ---------------------------------------------------------------- audit and write-log rows (Postgres)
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


def test_audit_and_write_log_rows(world):
    connect_fn, a, b = world["connect"], world["viewer"], world["other"]
    ids = {a: "<viewer>", b: "<other>"}
    w = _server(world, True, channel="mcp:snapshot")
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
        audit = conn.execute("""SELECT user_id, channel, tool, args, ok, error, provider_id, model FROM assistant_audit
                                WHERE channel LIKE '%%:snapshot' ORDER BY id""").fetchall()
        writes = conn.execute("""SELECT user_id, channel, tool, kind, action, target, before, after, rolled_back_at
                                 FROM ai_write_log WHERE channel LIKE '%%:snapshot' ORDER BY id""").fetchall()
    for r in (*audit, *writes):
        r["user_id"] = ids.get(r["user_id"], r["user_id"])
    for c in calls.values():   # get_profile: now_local / weekday / age depend on the clock
        if not c["is_error"] and c["text"].startswith("{"):
            c["text"] = json.loads(c["text"])
            for k in ("now_local", "weekday", "age"):
                if k in c["text"]:
                    c["text"][k] = "<clock>"
    check("audit.json", _scrub({"results": calls, "assistant_audit": audit, "ai_write_log": writes}, ids))
