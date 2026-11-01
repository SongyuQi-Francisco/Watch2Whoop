# datalayer interface reference

What exists today, module by module. For the ideas behind it see [README.md](README.md). Import from `body_engine.datalayer`
and its modules named below; everything else, and every name with a leading underscore, is internal. Tests check that each
heading here resolves to a real object and that everything in `datalayer.__all__` is covered.

## Tools

### `datalayer.Tool`
A frozen dataclass.

| Field | Type, default | Meaning |
|---|---|---|
| `name` | `str` | Unique. Clients' approval rules and the audit go by it, so do not rename |
| `description` | `str` | What the model reads. Keep it short |
| `input_schema` | `dict` | Strict form, from `schema(...)` |
| `handler` | callable | `handler(ctx, args) -> result` |
| `scope` | `str` | Data scope needed to view someone else's data; `""` if it never touches anyone else's |
| `writes` | `bool = False` | Read-only callers do not get it; a write log is opened for it; MCP clients ask first |
| `group` | `str = ""` | The group it belongs to in `ToolsetConfig.groups` |
| `strict` | `bool = True` | For Claude strict tools in `api_definition()`; only free-form tools turn it off |
| `confirm` | `bool = False` | Deletes or undoes. Only on write tools |
| `confirm_label` | `str = ""` | Optional short text for a confirmation card of a chat loop. Needs `confirm` |
| `legacy` | `bool = False` | The handler has the older signature `(conn, owner_id, args)`, with viewer and channel in `args` as `_viewer_id` / `_channel`. For code that predates `(ctx, args)`; new tools do not use it |

`api_definition()` returns `{name, description, input_schema, strict}` for a model API.

### `datalayer.schema`
`schema(props: dict) -> dict`. A strict object schema: every property required, no additional properties.

### `datalayer.nullable`
`nullable(t: str, desc: str | None = None, **extra) -> dict`. A property of type `t` that may be `null`. MCP clients may leave it out.

### `datalayer.relaxed`
`relaxed(s: dict, optional_defaults: Mapping | None = None) -> dict`. The schema MCP clients see: nullable properties and
`optional_defaults` keys are not required, `additionalProperties` is dropped.

### `datalayer.fill_defaults`
`fill_defaults(s: dict, value, optional_defaults: Mapping | None = None)`. The reverse: fills what a relaxed caller left out
(`None`, or the default), so handlers see the full shape.

### `datalayer.ToolContext`
What a handler gets as `ctx`: `conn`, `owner_id`, `viewer_id`, `channel`, `writelog`.
`ctx.log(kind, action, target, before=None, after=None) -> int | None` adds a write log row with the values before and after; it
does nothing in a read tool. Values are stored as JSON; what JSON has no type for (dates, decimals) goes through `str()`.

### `datalayer.compact`
Module. `compact.dumps(result) -> str`: what a tool result looks like to the model. Empty values are left out, a list of three or
more objects becomes `{"cols": [...], "rows": [[...]]}`, floats are rounded.

## Registration

### `datalayer.Registry`
`Registry(messages: Messages = ENGLISH)`

- `tools(specs, *, legacy=False)`: add `Tool`s or dicts of `Tool`'s fields, in order. Refuses a repeated name, `confirm` on a
  read tool, and `confirm_label` without `confirm`.
- `rollback(kind, fn, only_action=None)`: how a write log row of this kind is put back; see `WriteLog.register_rollback`. Refuses a
  kind registered twice.
- `view(name, view)`: add a `View`.
- `freeze(*, audit, writelog, toolset, policy=None, call_context=None, after_write=None, optional_defaults=None) -> App`:
  `toolset(confirm: frozenset) -> ToolsetConfig`, the rest as in `Executor`. Refuses a view naming a tool that does not exist, and
  a `writelog` or toolset whose `messages` differ from the registry's. After it, every method raises `RuntimeError`.

### `datalayer.App`
The frozen result.

| Attribute | Meaning |
|---|---|
| `tools` | Every tool, in registration order |
| `by_name` | The same by name, read-only |
| `executor` | The `Executor` |
| `confirm` | The names with `confirm=True` |
| `views` | Name to `View`, read-only |
| `toolset()` | The `ToolsetConfig`, built on every call, so it can read your settings |
| `view_tools(view, extra=())` | That view's tools as its caller sees them |

### `datalayer.View`
`View(tools=None, exclude=frozenset(), force_self=True, owner_arg="user")`. `tools`: the names, in the order it sends them (`None`
= every tool in registration order). `exclude`: names kept out. `force_self`: a call that still names an owner is about the caller
anyway; `False` only hides the argument, and a call that names someone goes through `Policy.can_view` as usual.

- `select(by_name, extra=()) -> tuple[Tool, ...]`: the view's tools with `owner_arg` taken out of their schemas. `extra` widens a
  `tools` list for one call; names that are not tools are skipped.
- `call_args(tool, args) -> dict`: the arguments to run `tool` with for this caller.

## Who is calling

### `datalayer.Caller`
`Caller(viewer_id: int, channel: str, can_write: bool = True)`. Frozen. `channel` is what the audit records, say `mcp:stdio`.

### `datalayer.Policy`
Protocol: `can_view(conn, viewer_id, owner_id, scope) -> bool`. Asked on every call, also when viewer and owner are the same, where
it must answer `True`. `scope` is the tool's, possibly `""`.

### `datalayer.SelfOnly`
The default `Policy`: `viewer_id == owner_id`.

## Running a tool

### `datalayer.Executor`
`Executor(tools, *, audit, writelog, policy=None, messages=ENGLISH, call_context=None, after_write=None, optional_defaults=None)`.
You normally get one from `Registry.freeze`.

| Argument | Meaning |
|---|---|
| `tools` | Name to `Tool`, read at call time |
| `audit` | An `AuditSink` |
| `writelog` | A `WriteLog` |
| `policy` | A `Policy`; `None` = `SelfOnly` |
| `messages` | `Messages` |
| `call_context` | `() -> context manager` around every call, say to set a language |
| `after_write` | `(viewer_id) -> None`, after a write tool succeeds, say to drop a cache |
| `optional_defaults` | Property name to the value used when it is left out (see `relaxed`) |

`run(conn, viewer_id, channel, name, args, **extra)`: fills defaults, works out the owner from `args["user"]` (`"me"` or left out =
the viewer, otherwise `int(...)`), refuses a write to another owner, asks `policy.can_view`, runs the handler in `conn.transaction()`
(write tools inside a write log capture), writes the audit row and calls `after_write`. `extra` goes to the audit. Raises what the
tool or the checks raise; the caller must commit after an error so the audit row stays. It does not check `Caller.can_write`: the MCP
server and `toolsets.resolve` do.

`conn` is whatever your handlers use, plus `transaction()`: a context manager that is a savepoint. psycopg connections have it;
`examples/reading-notes/sqlite_store.py` shows the few lines it takes for SQLite.

## Audit

### `datalayer.AuditSink`
Protocol: `record(conn, viewer_id, channel, tool, args, ok, error, **extra) -> None`. `tool` is the real tool's name; `error` is the
message of a failed call, or `None`.

### `datalayer.PgAudit`
`PgAudit(table: str, extra_columns: tuple[str, ...] = ())`. Writes to your Postgres table: columns `user_id`, `channel`, `tool`, `args`
(jsonb), `ok`, `error`, and `extra_columns` when any of them is set.

## Write log

### `datalayer.WriteLog`
`WriteLog(store: WriteLogStore | str, messages: Messages = ENGLISH)`. A string means `PgWriteLogStore` on that table.

- `register_rollback(kind, fn, only_action=None)`: `fn(conn, user_id, row, **kw) -> result` puts the row's `before` value back.
  `only_action`: the one action of this kind that can be rolled back (`None` = all but a rollback itself). `Registry.rollback` calls
  this at freeze.
- `kinds`: the kinds that have a handler. `can_roll(kind, action) -> bool`.
- `record(conn, kind, action, target, before=None, after=None) -> int | None`: adds a row inside a capture; does nothing outside one,
  so only AI writes are logged. `ctx.log` calls it.
- `capture(user_id, channel, tool)`, `active()`, `record_generic(conn, args, result)`: what the executor uses around a write tool.
- `list_log(conn, user_id, days=14, limit=30) -> list[dict]`: `id`, `at`, `tool`, `kind`, `action`, `target`, `before`, `after`,
  `rollback` (`"ok"`, `"done"` or `None`).
- `rollback(conn, user_id, log_id, **kw) -> dict`: puts one of that user's rows back and logs the rollback; refuses an unknown row,
  a second rollback and a row without a handler. Call it from a write tool, so that the rollback is logged too.

The framework has no `get_write_log` or `rollback_write` tool of its own: write those two with `list_log` and `rollback` (the example
has them), and mark the second `confirm=True`.

### `datalayer.WriteLogStore`
Protocol for where the rows live, on the connection the tool uses. A row is a mapping with `id`, `created_at`, `user_id`, `channel`,
`tool`, `kind`, `action`, `target`, `before`, `after`, `rolled_back_at`, `rolled_back_by`; the two timestamps are `datetime`s.

- `insert(conn, user_id, channel, tool, kind, action, target, before, after) -> int`
- `recent(conn, user_id, days, limit) -> list[Mapping]`: newest first
- `row_for_update(conn, user_id, log_id) -> Mapping | None`: only that user's row
- `mark_rolled_back(conn, log_id, by) -> None`

### `datalayer.PgWriteLogStore`
`PgWriteLogStore(table: str)`. The same on your Postgres table, with `before` and `after` as jsonb.

## Tool sets

### `datalayer.ToolsetConfig`
A frozen dataclass.

| Field | Default | Meaning |
|---|---|---|
| `core` | | Names always sent |
| `groups` | | Group to a line for the `find_tools` index, in index order |
| `confirm` | | The tools that ask first: the argument `freeze` gives your `toolset` function |
| `meta` | | `toolsets.meta_tools(groups)` |
| `contexts` | `{}` | Context id to extra groups, for a chat loop. `"<a>.<b>"` falls back to `"<a>"` |
| `find_max` | `12` | Full schemas `find_tools(query)` returns at most |
| `messages` | `ENGLISH` | `Messages` |
| `optional_defaults` | `{}` | As in `Executor`; pass the same mapping to both |

### `datalayer.Budgets`
`Budgets(core, meta, listing, context, groups={})`. Size caps in characters of the definitions as sent: the core, the meta tools, MCP
`tools/list`, core + one context's groups + meta, and group to the cap of all its tools.

### `datalayer.Access`
`Access(tools, can_write, direct_only=frozenset())`. What one caller may reach through the meta tools: `tools` is name to `Tool`
(for a restricted caller, its view's), `direct_only` the tools that must be called by their own name.

### `datalayer.toolsets`
Module. `FIND`, `RUN_READ`, `RUN_WRITE`, `RUN_NAMES`: the meta tools' names.

- `meta_tools(groups, messages=ENGLISH) -> list[Tool]`
- `for_listing(cfg, tools, can_write) -> list[Tool]`: MCP `tools/list`: core, confirm tools and meta tools; read-only callers get no
  write tool.
- `for_context(cfg, tools, context, per_context=None) -> list[Tool]`: core, the groups of a context or list of contexts, meta tools.
- `context_groups(cfg, context, per_context=None)`, `known_context(cfg, context)`, `fit_contexts(cfg, tools, contexts, budget, per_context=None)`,
  `visit_context(cfg, tools, contexts, context, budget, per_context=None)`: a chat's contexts, newest last; the oldest are dropped
  when over `budget`.
- `find(cfg, access, group=None, query=None) -> dict`: answers `find_tools`.
- `resolve(cfg, access, meta, args) -> tuple[Tool, dict]`: a `run_read_tool` / `run_write_tool` call to the real tool. Raises `PermissionError`
  or `ValueError` with a text for the model.
- `definition_chars(tools, optional_defaults=None) -> int`, `config_problems(cfg, tools)`, `budget_problems(cfg, tools, budgets)`.

## Playbooks

### `datalayer.PlaybookSource`
`PlaybookSource(files, names, *, messages=ENGLISH, find_line="", tool_re=None)`. `files`: a directory (`pathlib.Path` or
`importlib.resources` traversable) holding `NAME.md` for every name. The first line `# title` is the title, the `> ...` line under it the
description; HTML comments are stripped; `find_line` is put under the description.

`text(name)`, `title(name)`, `description(name)`, `check(name)` (raises for an unknown name), and `tools_mentioned(name)`: what `tool_re`
finds, to test that each exists.

## Texts

### `datalayer.Messages`
A frozen dataclass of every fixed text the framework shows a model, with every field required. Placeholders in braces are filled with
`str.format`; `testing.check_messages` compares them with `ENGLISH`. To change a few: `dataclasses.replace(ENGLISH, read_only="...")`.

### `datalayer.ENGLISH`
The framework's default `Messages`.

## MCP

### `datalayer.mcp.server.build_server`
`build_server(name, instructions, identity_of, *, executor, tools, toolset, connect_fn, prompts=(), text_filter=None, caller_tools=None, resources=()) -> Server`.
Returns the MCP SDK's low-level server.

| Argument | Meaning |
|---|---|
| `identity_of(ctx)` | The `Caller` of this request. stdio: one fixed caller; HTTP: `McpHttpApp.identity_of` |
| `executor`, `tools`, `toolset` | `App.executor`, `App.tools`, `App.toolset` |
| `connect_fn()` | A context manager giving a connection: commits on success, and has `commit()` |
| `prompts` | `PromptSpec`s |
| `text_filter(caller)` | A `TextFilter` for this caller, or `None`. Runs in a worker thread |
| `caller_tools` | A `CallerTools` |
| `resources` | `ResourceSpec`s. Empty: no `resources/*` methods and no `resources` capability at all |

`tools/list` is `toolsets.for_listing`. Write tools are marked `read_only_hint=False`. Errors from tools go back to the model as tool
errors with their text.

### `datalayer.mcp.server.call`
`call(executor, connect_fn, caller, name, args) -> str`. Runs one tool on a connection of its own, commits after an error so the audit
row stays, and returns the compact text.

### `datalayer.mcp.server.PromptSpec`
`PromptSpec(name, title, description, get_description, text)`. `text(caller) -> str` runs in a worker thread and may read the database.
`description` is for `prompts/list`, `get_description` for `prompts/get`.

### `datalayer.mcp.server.ResourceSpec`
`ResourceSpec(uri, name, description, text, mime_type="text/markdown")`. `text() -> str` runs in a worker thread and takes no caller: a
resource says how the server works, and is never anybody's data. Goes through `text_filter` like tool results.

### `datalayer.mcp.server.playbook_prompts`
`playbook_prompts(source: PlaybookSource) -> list[PromptSpec]`. Every playbook as a prompt of the same name.

### `datalayer.mcp.server.playbook_resources`
`playbook_resources(source: PlaybookSource) -> list[ResourceSpec]`. Every playbook as `datalayer://playbooks/NAME`.

### `datalayer.mcp.server.scopes_resource`
`scopes_resource(scopes: Mapping[str, str], description=...) -> ResourceSpec`. `datalayer://scopes`: your data scopes and a line about
each, as JSON. Scopes are not registered anywhere else, so you pass them.

### `datalayer.mcp.server.rules_resource`
`rules_resource(text: Callable[[], str], description=...) -> ResourceSpec`. `datalayer://rules/root`: your root rules.

### `datalayer.mcp.server.CallerTools`
Protocol for tools only some callers get, run by your code instead of the executor, so they also need their own audit.
`tools(caller) -> Sequence[Tool]` and `run(caller, name, args) -> result`, both in a worker thread. They are never in `tools/list`:
`find_tools` shows them in their `group`, and `run_read_tool`, `run_write_tool` or their own name runs them, under the same read-only
rule. A name the registry already has is ignored. This is how an application adds tools behind a feature switch without changing what
everybody else's `tools/list` says.

### `datalayer.mcp.server.TextFilter`
Protocol: `text(text) -> str` and `schema(schema) -> dict`, applied to everything one caller reads except prompts, which build their own
text: tool descriptions, schemas, results, errors and resources.

### `datalayer.mcp.stdio.run`
`run(server)`: serves one server over stdio until the client closes it. One process is one `Caller`, fixed when it starts.

### `datalayer.mcp.http.McpHttpApp`
`McpHttpApp(server_factory, authenticate, unauthorized=bearer_unauthorized, messages=ENGLISH, identity_key=IDENTITY_KEY)`. An ASGI app:
Streamable HTTP, stateless, JSON responses.

- `server_factory(identity_of) -> Server`: usually `build_server` with this `identity_of`.
- `authenticate(token) -> Caller | None`: runs in a worker thread on every request, so a revoked token stops working at once.
- `unauthorized(scope, detail, error) -> ASGI response`.
- `lifespan()`: an async context manager. Run it around your server's life; it can be entered again after it ends.

To mount it in Starlette: `Route("/mcp", app, methods=["GET", "POST", "DELETE"])` and `lifespan=lambda _: app.lifespan()`.

### `datalayer.mcp.http.bearer_unauthorized`
The default `unauthorized`: a JSON 401 with `WWW-Authenticate: Bearer`. Replace it to point OAuth clients at your authorization server.

## Testing

### `datalayer.testing.check_imports`
`check_imports(root, package, framework, allowed_third_party=ALLOWED_THIRD_PARTY) -> list[str]`. The files under `root` import only the
standard library, `framework` (within `package`) and `allowed_third_party`. Imports inside functions and dynamic imports with a literal
name count too.

### `datalayer.testing.check_toolset`
`check_toolset(cfg, tools, budgets) -> list[str]`. The configuration matches the tools, and every set the framework sends fits its budget.

### `datalayer.testing.check_handlers`
`check_handlers(tools) -> list[str]`. Every handler takes the arguments its registration says.

### `datalayer.testing.check_messages`
`check_messages(messages) -> list[str]`. Your texts have the same placeholders as `ENGLISH`.
