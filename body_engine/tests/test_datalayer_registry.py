"""The registration interface (issue #116, docs/datalayer-design.md section 4, phase 3).

The first half runs the framework on a small non-health application (reading notes, no database): registration order,
freezing, confirm read off the tools, the (ctx, args) handlers next to the legacy adapter, views. The second half pins
what Watch2Whoop's registration (dl_app.py) must keep exactly as it was: the tools that ask first are the safety
boundary of writes, and who may see what must not move by a hair."""
import dataclasses
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from body_engine import auth, datalayer, dl_app
from body_engine.ai import chat, fallback, fallback_params, params as P
from body_engine.assistant import briefing, toolsets, tools, writelog
from body_engine.assistant.tools import TOOLS, TOOLS_BY_NAME
from body_engine.datalayer import Messages, Registry, Tool, View, testing
from body_engine.datalayer.spec import nullable, schema
from body_engine.datalayer.views import without_owner
from body_engine.i18n import is_en, use_lang
from body_engine.toolspec import OPTIONAL_DEFAULTS

TEXTS = Messages(**{f.name: f.name for f in dataclasses.fields(Messages)})
OWNER = {"type": "string", "description": "me or a user id"}


# ---------------------------------------------------------------- a small application
class FakeConn:
    """Only what the executor and the write log use; every INSERT is one row."""

    def __init__(self):
        self.rows: list[tuple] = []

    @contextmanager
    def transaction(self):
        yield

    def execute(self, query, params=None):
        self.rows.append(params)
        return SimpleNamespace(fetchone=lambda: {"id": len(self.rows)})


class FakeAudit:
    def __init__(self):
        self.rows: list[tuple] = []

    def record(self, conn, user_id, channel, tool, args, ok, error, **extra):
        self.rows.append((user_id, channel, tool, ok, error))


class Shares:
    """Viewer 1 may see user 2's books; nobody sees anything else of anybody."""

    def can_view(self, conn, viewer_id, owner_id, scope):
        return owner_id == viewer_id or (viewer_id, owner_id, scope) == (1, 2, "books")


def _toolset(confirm):
    meta = datalayer.toolsets.meta_tools({"books": ""}, TEXTS)
    return datalayer.ToolsetConfig(core=(), groups={"books": ""}, contexts={}, confirm=confirm, meta=meta, find_max=5,
                                   messages=TEXTS)


def _freeze(reg: Registry, **kw):
    log = datalayer.WriteLog("notes_log", TEXTS)
    audit = FakeAudit()
    app = reg.freeze(policy=Shares(), audit=audit, writelog=log, toolset=_toolset, **kw)
    return app, audit, log


def _book_tool(name="list_books", **kw):
    return Tool(name, "Books.", schema({"user": OWNER}), kw.pop("handler", lambda ctx, a: []), scope="books", group="books", **kw)


def _note_tool(name="add_note", handler=lambda ctx, a: {"ok": True}, **kw):
    return Tool(name, "A note.", schema({"text": {"type": "string"}, "why": nullable("string")}), handler, scope="",
                group="books", writes=True, **kw)


def test_tools_keep_the_registration_order_and_the_registry_freezes():
    reg = Registry(TEXTS)
    reg.tools([_note_tool("z_first")])
    reg.tools([dataclasses.asdict(_book_tool("m_second"))])
    reg.tools([_note_tool("a_third")])
    app, _, _ = _freeze(reg)
    assert [t.name for t in app.tools] == ["z_first", "m_second", "a_third"] == list(app.by_name)
    assert app.executor.tools is app.by_name
    for late in (lambda: reg.tools([_book_tool()]), lambda: reg.rollback("note", print), lambda: reg.view("v", View()),
                 lambda: reg.freeze(policy=Shares(), audit=FakeAudit(), writelog=None, toolset=_toolset)):
        with pytest.raises(RuntimeError, match="frozen"):
            late()
    with pytest.raises(TypeError):   # no way around it through the mapping either
        app.by_name["extra"] = _book_tool()


@pytest.mark.parametrize("specs, message", [
    ([_book_tool(), _book_tool()], "registered twice"),
    ([_book_tool(confirm=True)], "only for write tools"),
    ([_note_tool(confirm_label="Add a note")], "without confirm"),
])
def test_inconsistent_tools_are_refused_when_registered(specs, message):
    with pytest.raises(ValueError, match=message):
        Registry(TEXTS).tools(specs)


def test_a_view_naming_no_tool_and_a_second_rollback_of_a_kind_are_refused():
    reg = Registry(TEXTS)
    reg.tools([_book_tool()])
    reg.view("typo", View(exclude=("get_bookz",)))
    with pytest.raises(ValueError, match="view typo: no tool get_bookz"):
        _freeze(reg)
    reg = Registry(TEXTS)
    reg.rollback("note", print)
    with pytest.raises(ValueError, match="registered twice"):
        reg.rollback("note", print)


def test_the_tools_that_ask_first_are_read_off_the_definitions():
    reg = Registry(TEXTS)
    reg.tools([_book_tool(), _note_tool(), _note_tool("delete_note", confirm=True, confirm_label="Delete a note")])
    app, _, _ = _freeze(reg)
    assert app.confirm == {"delete_note"} == app.toolset().confirm
    assert [t.name for t in datalayer.toolsets.for_listing(app.toolset(), app.tools, True)][0] == "delete_note"


# ---------------------------------------------------------------- handlers
def test_a_new_handler_gets_ctx_and_clean_args():
    seen = {}

    def handler(ctx, a):
        seen.update(ctx=ctx, args=a)
        return ["a book"]
    reg = Registry(TEXTS)
    reg.tools([_book_tool(handler=handler)])
    app, audit, _ = _freeze(reg)
    conn = FakeConn()
    assert app.executor.run(conn, 1, "mcp:test", "list_books", {"user": "2"}) == ["a book"]
    ctx = seen["ctx"]
    assert (ctx.conn, ctx.owner_id, ctx.viewer_id, ctx.channel) == (conn, 2, 1, "mcp:test")
    assert seen["args"] == {"user": "2"}                                # nothing of the framework's among the arguments
    assert ctx.log("book", "create", 1) is None                         # a read tool has no write log
    assert audit.rows == [(1, "mcp:test", "list_books", True, None)]


def test_a_legacy_handler_keeps_its_old_signature():
    seen = {}

    def handler(conn, owner_id, a):
        seen.update(conn=conn, owner=owner_id, args=a)
    reg = Registry(TEXTS)
    reg.tools([_book_tool(handler=handler)], legacy=True)
    reg.tools([dataclasses.asdict(_note_tool("moved_one")) | {"legacy": False}], legacy=True)   # one tool of a module moved
    app, _, _ = _freeze(reg)
    conn = FakeConn()
    app.executor.run(conn, 1, "butler", "list_books", {"user": "2"})
    assert seen == {"conn": conn, "owner": 2, "args": {"user": "2", "_viewer_id": 1, "_channel": "butler"}}
    assert app.by_name["list_books"].legacy and not app.by_name["moved_one"].legacy


def test_ctx_log_writes_the_write_log_and_a_silent_write_gets_a_generic_row():
    def noted(ctx, a):
        assert ctx.log("note", "create", 7, None, {"text": a["text"]}) == 1

    reg = Registry(TEXTS)
    reg.tools([_note_tool("noted", noted), _note_tool("silent")])
    app, _, _ = _freeze(reg)
    conn = FakeConn()
    app.executor.run(conn, 1, "mcp", "noted", {"text": "x"})
    assert conn.rows[0][:6] == (1, "mcp", "noted", "note", "create", "7")   # user, channel, tool, kind, action, target
    assert len(conn.rows) == 1
    conn = FakeConn()
    app.executor.run(conn, 1, "mcp", "silent", {"text": "x"})
    assert conn.rows[0][3:5] == ("other", "other")


def test_the_guardrails_are_the_same_for_both_signatures():
    ran = []
    reg = Registry(TEXTS)
    reg.tools([_book_tool(handler=lambda ctx, a: ran.append(a)), _note_tool(handler=lambda ctx, a: ran.append(a["text"]))])
    reg.tools([_book_tool("old_books", handler=lambda c, o, a: ran.append(a)),
               _note_tool("old_note", handler=lambda c, o, a: ran.append(a["text"]))], legacy=True)
    app, audit, _ = _freeze(reg)
    conn = FakeConn()
    for read, write in (("list_books", "add_note"), ("old_books", "old_note")):
        with pytest.raises(PermissionError, match="write_only_self"):   # writes only for oneself, whatever is shared
            app.executor.run(conn, 1, "mcp", write, {"user": "2", "text": "x"})
        with pytest.raises(PermissionError, match="no_grant"):          # viewer 3 has no share from user 2
            app.executor.run(conn, 3, "mcp", read, {"user": "2"})
        with pytest.raises(ValueError, match="missing_param"):          # a required argument left out: a message, not KeyError
            app.executor.run(conn, 1, "mcp", write, {})
    assert ran == []
    assert [r[3] for r in audit.rows] == [False] * 6                    # failed calls are audited too


def test_check_handlers_names_a_tool_registered_with_the_wrong_signature():
    good = [_book_tool(), _book_tool("old", handler=lambda conn, owner_id, a: [], legacy=True)]
    assert testing.check_handlers(good) == []
    assert testing.check_handlers([_book_tool(handler=lambda conn, owner_id, a: [])]) == \
        ["list_books: the handler does not take (ctx, args)"]
    assert testing.check_handlers([_book_tool(handler=lambda ctx, a: [], legacy=True)]) == \
        ["list_books: the handler does not take (conn, owner_id, args)"]


# ---------------------------------------------------------------- views
def _library():
    reg = Registry(TEXTS)
    reg.tools([_book_tool("a"), _book_tool("b", legacy=True), _note_tool("c", confirm=True), _book_tool("d")])
    reg.view("only", View(tools=("d", "a", "c")))
    reg.view("but", View(exclude=("b",)))
    reg.view("hidden", View(force_self=False))
    return _freeze(reg)[0]


def test_a_view_is_its_tools_in_its_order_without_the_owner_argument():
    app = _library()
    assert [t.name for t in app.view_tools("only")] == ["d", "a", "c"]
    assert [t.name for t in app.view_tools("only", ("b", "a", "nope"))] == ["d", "a", "c", "b"]   # extra: added, unknown skipped
    assert [t.name for t in app.view_tools("but")] == ["a", "c", "d"]                             # registration order
    assert [t.name for t in app.view_tools("but", ("b",))] == ["a", "c", "d"]                     # extra only widens a whitelist
    a = app.view_tools("only")[1]
    assert a.input_schema == {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    assert app.by_name["a"].input_schema["required"] == ["user"]                                  # the registry keeps its own
    assert app.view_tools("hidden")[1].legacy and app.view_tools("only")[2].confirm               # the rest of the definition stays


def test_a_forced_view_answers_for_the_caller_and_a_hidden_owner_does_not():
    app = _library()
    book, note = app.by_name["a"], app.by_name["c"]
    assert app.views["only"].call_args(book, {"user": "2"}) == {"user": "me"}
    assert app.views["only"].call_args(book, {}) == {"user": "me"}
    assert app.views["only"].call_args(note, {"text": "x"}) == {"text": "x"}                      # it has no owner argument
    assert app.views["hidden"].call_args(book, {"user": "2"}) == {"user": "2"}                    # goes through Policy.can_view


# ================================================================ Watch2Whoop's registration
# The list before it moved to the tool definitions (ai/params.py on main before phase 3): the safety boundary of AI writes.
ALWAYS_CONFIRM = {"undo_sparky_entry", "undo_meal_log", "undo_day_changes", "delete_journal_entry", "delete_lab_results",
                  "delete_observation", "delete_supplement", "remove_product", "remove_shopping_items", "remove_staple",
                  "delete_workout", "rollback_write", "delete_custom_exercise", "apply_memory_changes"}
# ai/chat.CONFIRM_EN before it moved to confirm_label; apply_memory_changes never had one (the card shows the tool name).
CONFIRM_LABELS = {"undo_sparky_entry": "Delete a food entry / strength workout / set / weigh-in",
                  "undo_meal_log": "Undo a quick meal log", "undo_day_changes": "Undo day changes to the training plan",
                  "delete_journal_entry": "Clear a journal answer for a day",
                  "delete_lab_results": "Delete lab results for a sample date",
                  "delete_observation": "Delete an observation note",
                  "delete_supplement": "Delete a supplement and its journal item", "remove_product": "Remove a product",
                  "remove_shopping_items": "Remove shopping list items",
                  "remove_staple": "Stop treating a product as a usual item",
                  "delete_workout": "Hide a duplicate / wrong workout",
                  "rollback_write": "Roll back an AI change", "delete_custom_exercise": "Delete one of your own exercises"}


def test_the_derived_confirm_list_is_exactly_the_old_one_everywhere():
    assert {t.name for t in TOOLS if t.confirm} == ALWAYS_CONFIRM
    assert dl_app.APP.confirm == ALWAYS_CONFIRM
    assert P.ALWAYS_CONFIRM == chat.ALWAYS_CONFIRM == toolsets._cfg().confirm == ALWAYS_CONFIRM
    assert all(TOOLS_BY_NAME[n].writes for n in ALWAYS_CONFIRM)
    settings = json.loads((Path(__file__).resolve().parents[2] / "assistant/.claude/settings.json").read_text())
    assert {r.removeprefix("mcp__body-engine__") for r in settings["permissions"]["ask"]} == ALWAYS_CONFIRM


def test_the_confirm_card_says_what_it_said():
    assert {t.name: t.confirm_label for t in TOOLS if t.confirm_label} == CONFIRM_LABELS
    with use_lang("en"):
        assert chat._summary("delete_observation", {"observation_id": 3}) == 'Delete an observation note: {"observation_id": 3}'
        assert chat._summary("apply_memory_changes", {}) == "apply_memory_changes: {}"
        assert chat._summary("add_event", {}) == "add_event: {}"     # a test's own confirm tool (they patch chat.ALWAYS_CONFIRM)
        assert chat._summary("no_such_tool", {}) == "no_such_tool: {}"
    with use_lang("zh"):
        assert chat._summary("delete_observation", {"observation_id": 3}).startswith("删除一条观察笔记（id 见 get_memory）：")


def test_the_executor_hooks_are_the_same_as_before_the_move(monkeypatch):
    ex = tools.EXECUTOR
    assert ex is dl_app.APP.executor and ex.writelog is writelog.LOG and ex.messages is dl_app.MESSAGES
    assert ex.audit is tools.AUDIT and ex.audit.table == "assistant_audit"
    assert ex.optional_defaults is OPTIONAL_DEFAULTS
    calls = []
    monkeypatch.setattr(auth, "can_view", lambda conn, v, o, s: calls.append((conn, v, o, s)) or False)   # read at call time
    assert ex.policy.can_view("c", 1, 2, "sleep") is False and calls == [("c", 1, 2, "sleep")]
    dropped = []
    monkeypatch.setattr(briefing, "invalidate", dropped.append)
    ex.after_write(5)
    assert dropped == [5]
    with use_lang("en"), ex.call_context():
        assert not is_en()                                           # AI-facing texts stay Chinese in an English request


def test_old_paths_are_the_registry():
    assert tools.TOOLS is dl_app.APP.tools and tools.TOOLS_BY_NAME is dl_app.APP.by_name
    assert toolsets.META_TOOLS is dl_app.META_TOOLS and toolsets._cfg() == dl_app.APP.toolset()
    assert fallback.TOOL_NAMES is fallback_params.TOOL_NAMES and fallback.CONSENT_EXCLUDED is fallback_params.CONSENT_EXCLUDED
    assert fallback.own_schema is without_owner


def test_every_handler_takes_what_its_registration_says():
    assert testing.check_handlers(TOOLS) == []


def test_the_views_give_what_the_old_expressions_gave():
    """Written out as they were in ai/fallback.py and ai/chat.py before they became views."""
    def own(s):
        return {**s, "properties": {k: v for k, v in s["properties"].items() if k != "user"},
                "required": [r for r in s["required"] if r != "user"]}

    def defs(ts):
        return [t.api_definition() for t in ts]
    assert defs(chat._own_tools()) == defs(dataclasses.replace(t, input_schema=own(t.input_schema))
                                           if "user" in t.input_schema["properties"] else t for t in TOOLS)
    names = [*fallback.TOOL_NAMES]
    assert defs(fallback.tools()) == defs(dataclasses.replace(TOOLS_BY_NAME[n], input_schema=own(TOOLS_BY_NAME[n].input_schema))
                                          for n in names)
    parity = [n for n in TOOLS_BY_NAME if n not in fallback.CONSENT_EXCLUDED]
    assert defs(fallback.tools(parity=True)) == defs(dataclasses.replace(TOOLS_BY_NAME[n], input_schema=own(TOOLS_BY_NAME[n].input_schema))
                                                     for n in parity)
    assert "get_ecg_list" not in {t.name for t in fallback.tools(parity=True)}
    # the fallback's calls are about the caller, whatever the model asks for; own_only conversations only hide the parameter
    view = dl_app.APP.views[fallback.VIEW]
    assert view.call_args(TOOLS_BY_NAME["get_profile"], {"user": "2"}) == {"user": "me"}
    assert view.call_args(TOOLS_BY_NAME["log_water"], {"ml": 200}) == {"ml": 200}
    assert dl_app.APP.views[fallback.PARITY_VIEW].force_self
    assert not dl_app.APP.views["own_only"].force_self


@pytest.mark.parametrize("first", [
    "body_engine.dl_app", "body_engine.assistant.tools", "body_engine.assistant.toolsets", "body_engine.assistant.writelog",
    "body_engine.ai.params", "body_engine.ai.chat", "body_engine.ai.fallback", "body_engine.ai.cards", "body_engine.ai.web",
    "body_engine.mcp_server", "body_engine.records.tools", "body_engine.assistant.sparky_tools",
])
def test_any_module_can_be_imported_first(first):
    """The registry imports the tool modules and the old paths import the registry: no order of first imports may break
    it, and the old names that read the registry work wherever the process started."""
    code = (f"import importlib; importlib.import_module({first!r})\n"
            "from body_engine.ai import params\n"
            "from body_engine.assistant import writelog\n"
            "assert len(params.ALWAYS_CONFIRM) == 14 and 'food' in writelog.ROLLBACK_KINDS\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
