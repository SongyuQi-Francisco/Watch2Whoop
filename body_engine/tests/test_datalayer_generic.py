"""The datalayer framework on its own (issue #116, docs/datalayer-design.md section 4, phase 4): English default texts next to
Watch2Whoop's Chinese ones, a default policy, a write log that is not tied to Postgres, optional MCP resources and the documentation.
No database here. The non-health application is examples/reading-notes, with its own test file (a CI step of its own)."""
import dataclasses
import importlib
import inspect
import json
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import anyio
import mcp.types as types
import pytest
from mcp.shared.exceptions import MCPError

import body_engine
from body_engine import datalayer, dl_app, mcp_server
from body_engine.ai import playbooks
from body_engine.ai.datalayer_texts import MESSAGES
from body_engine.ai.mcp_http import mcp_app
from body_engine.assistant import toolsets, writelog
from body_engine.datalayer import ENGLISH, Caller, Messages, Registry, SelfOnly, Tool, ToolsetConfig, WriteLog, testing
from body_engine.datalayer import toolsets as ts
from body_engine.datalayer.mcp import server as dl_server
from body_engine.datalayer.mcp.http import McpHttpApp, bearer_unauthorized
from body_engine.datalayer.spec import schema

CJK = re.compile(r"[　-鿿＀-￯]")
DOCS = Path(body_engine.__file__).parent / "datalayer"
TEXTS = Messages(**{f.name: f.name for f in dataclasses.fields(Messages)})


# ---------------------------------------------------------------- texts
def test_the_default_texts_are_english_and_complete():
    for f in dataclasses.fields(ENGLISH):
        text = getattr(ENGLISH, f.name)
        assert text and not CJK.search(text), f.name
    assert testing.check_messages(ENGLISH) == []
    with pytest.raises(TypeError):        # your own texts: all of them, or it does not build
        Messages(read_only="only this one")


def test_every_part_defaults_to_english():
    source = datalayer.PlaybookSource(DOCS, ("README",))
    app = McpHttpApp(lambda identity_of: None, lambda token: None)
    cfg = ToolsetConfig(core=(), groups={"g": ""}, confirm=(), meta=[])
    assert all(m is ENGLISH for m in (Registry().messages, WriteLog("t").messages, source.messages, app.messages, cfg.messages))
    assert app.unauthorized is bearer_unauthorized
    meta = {t.name: t for t in ts.meta_tools({"g": ""})}
    assert meta[ts.FIND].description == ENGLISH.find_description and not CJK.search("".join(map(str, meta.values())))


def test_watch2whoop_passes_its_own_texts_everywhere():
    """The defaults are a new way of forgetting: every place that holds texts still has the Chinese ones."""
    holders = {"registry": dl_app.reg.messages, "executor": dl_app.APP.executor.messages, "write log": writelog.LOG.messages,
               "toolset": toolsets._cfg().messages, "playbooks": playbooks._SOURCE.messages, "mcp http": mcp_app.messages}
    assert {k for k, m in holders.items() if m is not MESSAGES} == set()
    assert all(CJK.search(t.description) for t in toolsets._cfg().meta)
    assert testing.check_messages(MESSAGES) == []    # the same placeholders as the English texts


def test_check_messages_names_a_broken_translation():
    broken = dataclasses.replace(ENGLISH, no_such_tool="missing the name", no_grant="{owner} {scpoe}")
    assert testing.check_messages(broken) == ["no_such_tool: [] instead of ['name']",
                                              "no_grant: ['owner', 'scpoe'] instead of ['owner', 'scope']"]


def _tool(name="read_thing", handler=lambda ctx, a: "ok", **kw):
    return Tool(name, "A thing.", schema({"user": {"type": "string"}}), handler, scope="things", group="things", **kw)


class Conn:
    @contextmanager
    def transaction(self):
        yield


class Audit:
    def __init__(self):
        self.rows = []

    def record(self, conn, viewer_id, channel, tool, args, ok, error, **extra):
        self.rows.append((viewer_id, tool, ok, error))


GROUPS = {"things": "Things"}


def _toolset(messages=ENGLISH):
    return lambda confirm: ToolsetConfig(core=("read_thing",), groups=GROUPS, confirm=confirm,
                                         meta=ts.meta_tools(GROUPS, messages), messages=messages)


@pytest.mark.parametrize("log_texts, set_texts, what", [(ENGLISH, TEXTS, "write log"), (TEXTS, ENGLISH, "toolset")])
def test_a_mix_of_languages_does_not_freeze(log_texts, set_texts, what):
    reg = Registry(TEXTS)
    with pytest.raises(ValueError, match=f"the {what} has other texts"):
        reg.freeze(audit=Audit(), writelog=WriteLog("t", log_texts), toolset=_toolset(set_texts))


# ---------------------------------------------------------------- policy
def test_without_a_policy_nobody_sees_anybody_elses_data():
    assert SelfOnly().can_view(None, 1, 1, "things") and not SelfOnly().can_view(None, 1, 2, "things")
    reg = Registry()
    reg.tools([_tool()])
    audit = Audit()
    app = reg.freeze(audit=audit, writelog=WriteLog("t"), toolset=_toolset(), optional_defaults={"user": "me"})
    assert app.executor.run(Conn(), 1, "test", "read_thing", {}) == "ok"
    with pytest.raises(PermissionError, match="No permission to see things of user 2"):
        app.executor.run(Conn(), 1, "test", "read_thing", {"user": "2"})
    assert [r[2] for r in audit.rows] == [True, False]


# ---------------------------------------------------------------- the write log's storage
class MemoryRows:
    """A WriteLogStore with no SQL at all."""

    def __init__(self):
        self.rows = {}

    def insert(self, conn, user_id, channel, tool, kind, action, target, before, after):
        row_id = len(self.rows) + 1
        self.rows[row_id] = {"id": row_id, "created_at": datetime(2026, 1, 2, 3, 4), "user_id": user_id, "tool": tool, "kind": kind,
                             "action": action, "target": target, "before": before, "after": after, "rolled_back_at": None}
        return row_id

    def recent(self, conn, user_id, days, limit):
        return [r for r in reversed(self.rows.values()) if r["user_id"] == user_id][:limit]

    def row_for_update(self, conn, user_id, log_id):
        row = self.rows.get(log_id)
        return row if row and row["user_id"] == user_id else None

    def mark_rolled_back(self, conn, log_id, by):
        self.rows[log_id]["rolled_back_at"] = datetime(2026, 1, 2, 3, 5)


def test_the_write_log_runs_on_any_store():
    rows = MemoryRows()
    log = WriteLog(rows)
    undone = []
    log.register_rollback("note", lambda conn, user_id, row: undone.append(row["before"]) or "put back")
    assert log.record(None, "note", "update", 7, {"t": 1}, {"t": 2}) is None    # outside a capture nothing is logged
    with log.capture(1, "test", "edit") as cap:
        first = log.record(None, "note", "update", 7, {"t": 1}, {"t": 2})
        log.record_generic(None, {"_viewer_id": 1, "x": 1}, {"ok": True})
        assert cap["ids"] == [first, first + 1]
        assert rows.rows[first]["target"] == "7"
        assert log.rollback(None, 1, first) == {"rolled_back": first, "tool": "edit", "kind": "note", "result": "put back"}
        with pytest.raises(ValueError, match="already rolled back at 2026-01-02 03:05"):
            log.rollback(None, 1, first)
        with pytest.raises(ValueError, match="No such write log entry"):
            log.rollback(None, 2, first)     # someone else's
        with pytest.raises(ValueError, match="no earlier value"):
            log.rollback(None, 1, first + 1)    # the generic row
    assert undone == [{"t": 1}]
    assert [(r["tool"], r["action"], r["rollback"]) for r in reversed(log.list_log(None, 1))] == [
        ("edit", "update", "done"), ("edit", "other", None), ("edit", "rollback", None)]
    assert rows.rows[first + 1]["after"]["args"] == {"x": 1}


def test_a_table_name_is_still_the_postgres_store():
    assert isinstance(WriteLog("some_table").store, datalayer.PgWriteLogStore) and WriteLog("some_table").store.table == "some_table"
    assert writelog.LOG.store.table == "ai_write_log"


# ---------------------------------------------------------------- MCP resources: off by default
def _request(server, method, params=None):
    return anyio.run(server.get_request_handler(method).handler, SimpleNamespace(), params)


def _small_server(**kw):
    reg = Registry()
    reg.tools([_tool()])
    app = reg.freeze(audit=Audit(), writelog=WriteLog("t"), toolset=_toolset())
    me = Caller(1, "test")
    identity = kw.pop("identity_of", lambda ctx: me)
    return dl_server.build_server("things", "About things.", identity, executor=app.executor, tools=app.tools, toolset=app.toolset,
                                  connect_fn=lambda: None, **kw)


@pytest.mark.parametrize("build", [
    lambda: mcp_server.build_server(lambda ctx: Caller(1, "test"), lambda: None),   # Watch2Whoop's server
    lambda: _small_server(),
], ids=["watch2whoop", "framework"])
def test_without_resources_nothing_about_them_exists(build):
    server = build()
    for method in ("resources/list", "resources/read", "resources/templates/list", "resources/subscribe"):
        assert server.get_request_handler(method) is None, method
    caps = server.get_capabilities()
    assert caps.resources is None and "resources" not in caps.model_dump(exclude_none=True)
    assert caps.tools is not None and caps.prompts is not None


RESOURCES = [dl_server.scopes_resource({"things": "Things one person can share"}),
             dl_server.rules_resource(lambda: "Be kind."),
             dl_server.ResourceSpec("datalayer://custom", "custom", "A custom text.", lambda: "custom text", mime_type="text/plain")]


def _dump(model):
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def test_resources_add_only_themselves():
    off, on = _small_server(), _small_server(resources=RESOURCES)
    for method in ("tools/list", "prompts/list"):
        assert _dump(_request(off, method)) == _dump(_request(on, method))
    assert on.instructions == off.instructions
    assert on.get_capabilities().resources is not None and on.get_capabilities().tools == off.get_capabilities().tools
    listed = _dump(_request(on, "resources/list"))["resources"]
    assert [(r["uri"], r["name"], r["mimeType"]) for r in listed] == [("datalayer://scopes", "scopes", "application/json"),
                                                                      ("datalayer://rules/root", "root rules", "text/markdown"),
                                                                      ("datalayer://custom", "custom", "text/plain")]

    def read(uri):
        return _request(on, "resources/read", types.ReadResourceRequestParams(uri=uri)).contents[0]
    assert json.loads(read("datalayer://scopes").text) == {"things": "Things one person can share"}
    assert read("datalayer://rules/root").text == "Be kind." and read("datalayer://custom").mime_type == "text/plain"
    with pytest.raises(MCPError, match="No such resource: datalayer://nope"):
        read("datalayer://nope")


def test_two_resources_with_one_uri_are_refused():
    with pytest.raises(ValueError, match="same uri"):
        _small_server(resources=[RESOURCES[0], RESOURCES[0]])


def test_a_resource_needs_a_signed_in_caller_and_goes_through_the_filter():
    def nobody(ctx):
        raise PermissionError("not signed in")
    server = _small_server(resources=RESOURCES, identity_of=nobody)
    with pytest.raises(PermissionError):
        _request(server, "resources/list")
    with pytest.raises(PermissionError):
        _request(server, "resources/read", types.ReadResourceRequestParams(uri="datalayer://custom"))

    class Upper:
        text = staticmethod(str.upper)
        schema = staticmethod(lambda s: s)
    server = _small_server(resources=RESOURCES, text_filter=lambda caller: Upper)
    assert _request(server, "resources/read", types.ReadResourceRequestParams(uri="datalayer://custom")).contents[0].text == "CUSTOM TEXT"
    assert _dump(_request(server, "resources/list"))["resources"][2]["description"] == "A CUSTOM TEXT."


def test_a_resource_text_is_not_told_who_is_reading():
    """The guarantee that makes a resource safe: nothing in its text can depend on the caller."""
    assert [f.name for f in dataclasses.fields(dl_server.ResourceSpec)] == ["uri", "name", "description", "text", "mime_type"]
    for spec in dl_server.playbook_resources(playbooks._SOURCE):
        assert spec.text() == playbooks.text(spec.name)
        assert spec.uri == f"datalayer://playbooks/{spec.name}"


def test_caller_tools_are_a_generic_extra_group():
    """What Watch2Whoop's web tools use (ai/web/mcp.py), on another kind of tool: only for some callers, never in tools/list,
    in their group in find_tools, under the same read-only rule."""
    extra_read = _tool("look_up_library", handler=None)
    extra_write = dataclasses.replace(extra_read, name="reserve_book", writes=True)

    class Library:
        def tools(self, caller):
            return [extra_read, extra_write] if caller.viewer_id == 1 else []

        def run(self, caller, name, args):
            return {"ran": name, "by": caller.viewer_id}

    def server(viewer, can_write=True):
        me = Caller(viewer, "test", can_write)
        return _small_server(identity_of=lambda ctx: me, caller_tools=Library())

    def call(srv, name, args=None):
        got = _request(srv, "tools/call", types.CallToolRequestParams(name=name, arguments=args))
        return got.is_error, got.content[0].text
    listed = [t.name for t in _request(server(1), "tools/list").tools]
    assert "look_up_library" not in listed and "reserve_book" not in listed
    assert call(server(1), ts.FIND, {"group": "things"})[1].count("look_up_library") == 1
    assert "look_up_library" not in call(server(2), ts.FIND, {"group": "things"})[1]
    assert call(server(1), ts.RUN_READ, {"name": "look_up_library", "args": {"user": "me"}}) == (False, '{"ran":"look_up_library","by":1}')
    assert call(server(1), "reserve_book", {"user": "me"}) == (False, '{"ran":"reserve_book","by":1}')
    assert call(server(1, can_write=False), "reserve_book", {"user": "me"}) == (True, ENGLISH.mcp_read_only)
    assert call(server(2), "look_up_library", {"user": "me"}) == (True, ENGLISH.mcp_no_such_tool.format(name="look_up_library"))


def test_watch2whoop_prompts_are_the_playbooks_next_to_butler():
    specs = mcp_server._prompts(lambda: None)
    assert [p.name for p in specs] == ["butler", *playbooks.NAMES]
    for p, name in zip(specs[1:], playbooks.NAMES):
        assert (p.title, p.description, p.get_description) == (playbooks.title(name), playbooks.description(name), playbooks.title(name))
        assert p.text(None) == playbooks.text(name)


# ---------------------------------------------------------------- the documentation says only what exists
def _resolve(dotted: str):
    parts = dotted.split(".")
    obj = body_engine
    for i, part in enumerate(parts):
        if not hasattr(obj, part):
            obj = importlib.import_module(".".join(["body_engine", *parts[:i + 1]]))
        else:
            obj = getattr(obj, part)
    return obj


def _sections() -> dict[str, str]:
    parts = re.split(r"^### `([\w.]+)`$", (DOCS / "REFERENCE.md").read_text(encoding="utf-8"), flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def _param_names(text: str) -> list[str]:
    """The parameter names of a signature as the reference writes it: commas inside brackets do not count."""
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += ch in "[({"
        depth -= ch in "])}"
        if ch == "," and not depth:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    names = [re.split(r"[:=]", p, maxsplit=1)[0].strip().lstrip("*") for p in parts]
    return [n for n in names if n]


def test_the_reference_only_names_what_exists_and_covers_the_public_interface():
    headings = list(_sections())
    assert len(headings) > 30
    for dotted in headings:
        _resolve(dotted)
    public = {f"datalayer.{n}" for n in datalayer.__all__}
    assert public - set(headings) == {"datalayer.playbooks", "datalayer.spec"}   # modules whose public names have their own headings


def test_the_signatures_in_the_reference_are_the_real_ones():
    """Every `name(params)` the reference writes that is the heading's object, or an attribute of it."""
    checked = 0
    for dotted, body in _sections().items():
        obj, short = _resolve(dotted), dotted.rsplit(".", 1)[1]
        for name, params in re.findall(r"`(\w+)\(([^`]*)\)(?: -> [^`]*)?`", body):
            target = obj if name == short else inspect.getattr_static(obj, name, None) and getattr(obj, name)
            if not callable(target):
                continue
            real = [p for p in inspect.signature(target).parameters if p != "self"]
            assert _param_names(params) == real, f"{dotted}: {name}"
            checked += 1
    assert checked > 50


@pytest.mark.parametrize("cls, heading", [(Tool, "datalayer.Tool"), (ToolsetConfig, "datalayer.ToolsetConfig")])
def test_the_field_tables_in_the_reference_are_the_real_fields(cls, heading):
    documented = re.findall(r"^\| `(\w+)` \|", _sections()[heading], flags=re.M)
    assert documented == [f.name for f in dataclasses.fields(cls)]


def test_the_framework_docs_are_english_and_their_links_work():
    for name in ("README.md", "REFERENCE.md"):
        text = (DOCS / name).read_text(encoding="utf-8")
        assert not CJK.search(text), name
        for link in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
            assert (DOCS / link).exists(), f"{name}: {link}"
