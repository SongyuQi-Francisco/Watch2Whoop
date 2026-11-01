# datalayer

A framework for letting any AI read and write one person's data through MCP, safely. You say what your data is: its tables,
its tools, who shares what. The framework takes care of the part every such server has to get right: who is calling, who may
see whose data, read-only callers, writes only for oneself, asking before a delete, an audit of every call, a log of every AI
write with rollback, and sending the model only the tools it needs.

`datalayer` is a working name. Interface details: [REFERENCE.md](REFERENCE.md).

## Status
- **Inside another project for now.** The code is a module of Watch2Whoop's Body Engine, where it runs in production
  (`body_engine/src/body_engine/datalayer/`, imported as `body_engine.datalayer`). It never imports the rest of Body Engine,
  and a test enforces that.
- **Not a package yet.** There is no separate install and nothing on PyPI, the name may change, and it has the licence of the
  directory it is in (AGPL-3.0, see the repository's `LICENSE`). Whether and how it gets extracted is tracked in
  [docs/datalayer-design.md](../../../../docs/datalayer-design.md) (mostly in Chinese).
- **The interface may still change**, with no compatibility promise yet.

It depends on `mcp`, `anyio` and `starlette`, and today also on `psycopg`, for the Postgres audit and write log.

## What it does not do
- **It is not a database.** Your tools use whatever connection you give them; it owns no tables.
- **It does not issue tokens.** You write `authenticate(token) -> Caller | None`; issuing, hashing, expiring and revoking
  tokens are yours, and so is OAuth.
- **It does not encrypt.** Put TLS in front of the HTTP transport.
- **It does not run a chat.** It serves MCP clients. A chat loop of your own can use the same executor and tool sets.
- **It does not know what your data means.** Scopes are strings; sharing is your `Policy`.

## See it work
[`examples/reading-notes`](../../../../examples/reading-notes/README.md) is a complete application on SQLite: books and notes,
about 200 lines, both transports. From the repository root:

```sh
cd body_engine && uv sync
uv run python ../examples/reading-notes/reading_notes.py http --port 8000     # or: stdio --user 1
```

The shape of an application:

```python
from body_engine.datalayer import Caller, Registry, Tool, ToolsetConfig, WriteLog, schema, toolsets
from body_engine.datalayer.mcp import server, stdio

def add_note(ctx, args):                                  # ctx: conn, owner_id, viewer_id, channel, log(...)
    note_id = ctx.conn.execute("INSERT ...", (ctx.owner_id, args["text"])).lastrowid
    ctx.log("note", "create", note_id, None, {"text": args["text"]})     # what changed, for the write log
    return {"id": note_id}

GROUPS = {"notes": "Books and notes"}
reg = Registry()                                          # English texts unless you pass your own Messages
reg.tools([Tool("add_note", "Add a note.", schema({"text": {"type": "string"}}), add_note,
                scope="notes", group="notes", writes=True)])
reg.rollback("note", undo_note)                           # how a logged change of this kind is put back

app = reg.freeze(audit=MyAudit(), writelog=WriteLog(MyWriteRows()),      # your storage; Postgres ones are included
                 toolset=lambda confirm: ToolsetConfig(core=("add_note",), groups=GROUPS, confirm=confirm,
                                                       meta=toolsets.meta_tools(GROUPS)))

me = Caller(1, "mcp:stdio")
stdio.run(server.build_server("notes", "The user's notes.", lambda ctx: me, executor=app.executor, tools=app.tools,
                              toolset=app.toolset, connect_fn=connect))
```

## Concepts

**Tools.** A `Tool` has a name, a description, a JSON schema, a handler `handler(ctx, args)`, a data scope and a group. Schemas
are written in strict form: every property required, optional ones nullable. MCP clients get a relaxed copy, and the executor
fills back what was left out, so a handler always sees the full shape. Keep descriptions short: they go to the model again and
again.

**Registration.** One module adds every tool, rollback handler and view to a `Registry`, in order, then freezes it into an
`App`. The order is the order models get, which keeps prompt caches stable, and nothing can be added after freezing, so
`tools/list` cannot change at run time. Nothing is discovered automatically.

**Two kinds of scope.** A *data scope* (`notes`, `profile`, ...) is on the tool: what a viewer needs to be allowed to see
someone else's data. A *calling capability* (`Caller.can_write`) is on the caller: whether this token or process may write at
all. You decide both; the framework treats scopes as opaque strings.

**Whose data.** A tool with a `user` argument can be pointed at another person: `"me"` or a user id. The argument has to be
called `user`. Add `{"user": "me"}` to `optional_defaults` so it can be left out. Every call asks your `Policy` whether the
viewer may see that owner's data in that scope.

**Asking first.** `confirm=True` marks a tool that deletes or undoes. It is always listed in `tools/list`, and it can only be
called by its own name, never through the meta tool, so the approval rule the client has for that name applies. Asking the
user is the client's job; the framework makes sure the tool cannot slip past it.

**Audit and write log.** The executor writes an audit row for every call it runs, failed ones too. For every write tool it
opens a write log: handlers call `ctx.log(kind, action, target, before, after)`, and a tool that logs nothing gets a generic
row that cannot be undone. `reg.rollback(kind, fn)` says how a kind of change is put back; a row is rolled back at most once.

**Tool sets.** Tool definitions go out with every model call, so only some do. `ToolsetConfig.core` is always sent. The rest
stay reachable through three meta tools: `find_tools` returns full schemas, `run_read_tool` and `run_write_tool` run a tool
under its own rules and audit name. `contexts` lets a chat loop add groups for the page a user is on. `Budgets` caps the size
of each set.

**Views.** A restricted caller gets a `View`: a set of tools with the `user` argument taken out, optionally forced to the
caller's own data. The MCP server does not use views; they are for a chat loop that serves a restricted AI.

**Playbooks, prompts and resources.** A playbook is a Markdown file of step-by-step instructions, served as an MCP prompt and,
if you add one, by a tool, with the same text. MCP resources are optional and off unless you pass some: read-only texts about
the server (playbooks, data scopes, root rules). They cannot be per-person by construction, because a resource has no approval
or audit path: people's data stays behind tools.

**Texts.** Everything fixed that the framework says to a model is in `Messages`. `ENGLISH` is the default. For another language
build your own, with every field filled: a missing text fails when it is built. Freezing refuses a write log or tool set with
different texts from the registry, so a model never gets a mix.

## Security model
What the framework guarantees, and where:

| Guarantee | Where |
|---|---|
| Identity comes only from the server's side: the process configuration (stdio) or `authenticate` on each request (HTTP). The client's own account of itself is never used | `mcp.stdio`, `mcp.http` |
| A request that fails `authenticate` never reaches a tool, prompt or resource | `mcp.http` |
| Writes go only to the caller's own data, whatever `Policy` says | `Executor` |
| Every call asks `Policy.can_view`, also for the caller's own data | `Executor` |
| A read-only caller sees no write tool in `tools/list` and `find_tools`, and is refused by name and through `run_write_tool` | `mcp.server`, `toolsets.resolve` |
| Going through a meta tool changes nothing: same checks, and the audit has the real tool's name | `toolsets.resolve`, `mcp.server` |
| A `confirm` tool is always listed and cannot be called through `run_write_tool` | `toolsets.for_listing`, `toolsets.resolve` |
| A call that reaches the executor leaves an audit row, also when it is refused or fails (the transport commits it after an error) | `Executor`, `mcp.server.call` |
| A failed tool's own changes and write log rows are undone by a savepoint | `Executor` |
| A view naming a tool that does not exist fails at freeze; a chat loop built on a view's `Access` cannot reach other tools | `Registry.freeze`, `toolsets` |
| A resource cannot depend on who is reading: `ResourceSpec.text()` gets no caller | `mcp.server` |

What stays with you:

- Issuing, storing and revoking tokens, OAuth, and TLS.
- Your `Policy` being right, including answering `True` for a caller's own data.
- Your handlers using `ctx.owner_id`. The framework checks who may ask; a query that ignores the owner still returns the wrong
  person's rows.
- Showing the user a question before a write. That is the MCP client's job, or your chat loop's.
- A chat loop of your own calling `Executor.run` directly: it must build an `Access` for the caller, resolve meta tools with
  `toolsets.resolve` and refuse writes for read-only callers, because the read-only checks are in the MCP server and in `resolve`,
  not in the executor.
- Committing after an error, in a transport of your own, so the audit row stays.

Calls refused before the executor (no such tool, read-only, wrong meta tool, missing arguments in `resolve`) are answered to the
model but leave no audit row, and neither does a `user` argument that is not a number.

## Testing your application
`datalayer.testing` has contract checks that take one line each in your test suite: `check_imports`, `check_toolset`,
`check_handlers`, `check_messages`. Each returns a list of problems, empty when all is well. See the
[reference](REFERENCE.md#testing), and [`test_reading_notes.py`](../../../../examples/reading-notes/test_reading_notes.py) for
them in use.
