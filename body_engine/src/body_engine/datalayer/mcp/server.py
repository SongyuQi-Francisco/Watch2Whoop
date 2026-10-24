"""MCP server over a tool registry (moved from mcp_server.build_server, issue #116).

tools/list sends what the toolset lists for the caller (toolsets.for_listing: core + confirm tools + meta tools;
read-only callers see no write tool). call_tool answers find_tools itself, resolves run_read_tool / run_write_tool to the
real tool (same checks, the audit records the real name), then runs it through the Executor in a worker thread with a
connection of its own. Write tools are marked read_only_hint=False, so the client asks the user before running them.
Errors (permission, parameters, database) go back to the model as tool errors with their text.

The application injects everything that is its own: the server name and instructions, who a request is (identity_of),
the database connection, the prompts, an optional per-caller text filter (Watch2Whoop: ai_parity's name scrub) and
optional per-caller extra tools that run outside the Executor (CallerTools; Watch2Whoop: the server's web tools)."""
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server

from .. import compact
from .. import toolsets as ts
from ..execute import Executor
from ..policy import Caller
from ..spec import Tool, relaxed


class TextFilter(Protocol):
    """Applied to everything one caller reads: tool descriptions, schemas and results."""
    def text(self, text: str) -> str: ...
    def schema(self, schema: dict) -> dict: ...


class CallerTools(Protocol):
    """Tools only some callers get, run by the application instead of the Executor (Watch2Whoop: the server's
    web_search / web_fetch behind a rollout flag, ai/web/mcp.py). Both methods run in a worker thread (they may read the
    database).
    tools(caller): the extra tools this caller may reach now. They are never in tools/list: find_tools shows them in
    their group and run_read_tool / run_write_tool (or their own name) runs them, with the same read-only rules. A name
    the registry already has is ignored.
    run(caller, name, args): run one of them -> the result (its own connection, audit and commit)."""
    def tools(self, caller: Caller) -> Sequence[Tool]: ...
    def run(self, caller: Caller, name: str, args: dict) -> object: ...


@dataclass(frozen=True)
class PromptSpec:
    """text(caller) -> the prompt's text (run in a worker thread, may read the database);
    get_description: the description returned with prompts/get (prompts/list shows `description`)."""
    name: str
    title: str
    description: str
    get_description: str
    text: Callable[[Caller], str]


def call(executor: Executor, connect_fn: Callable[[], AbstractContextManager], caller: Caller, name: str,
         args: dict) -> str:
    """Run one tool with a connection of its own; the compact text for the model."""
    with connect_fn() as conn:
        try:
            result = executor.run(conn, caller.viewer_id, caller.channel, name, args)
        except Exception:
            conn.commit()  # failed calls also leave an audit (the tool's own changes were already rolled back in the savepoint)
            raise
    return compact.dumps(result)


def build_server(name: str, instructions: str, identity_of: Callable[[object], Caller], *, executor: Executor,
                 tools: Sequence[Tool], toolset: Callable[[], ts.ToolsetConfig],
                 connect_fn: Callable[[], AbstractContextManager], prompts: Sequence[PromptSpec] = (),
                 text_filter: Callable[[Caller], TextFilter | None] | None = None,
                 caller_tools: CallerTools | None = None) -> Server:
    """identity_of(ctx) -> whom this request represents (stdio: one fixed caller; HTTP: from the request's Bearer token).
    tools: every tool in registration order (executor.tools is the same set by name); toolset() -> the configuration,
    read on every request; text_filter(caller) runs in a worker thread (it may read the database), None = no filter;
    caller_tools: see CallerTools (None = the registry only)."""
    prompts_by_name = {p.name: p for p in prompts}

    async def _filter(caller: Caller) -> TextFilter | None:
        return None if text_filter is None else await anyio.to_thread.run_sync(text_filter, caller)

    async def list_tools(ctx, params) -> types.ListToolsResult:
        caller = identity_of(ctx)
        f = await _filter(caller)
        cfg = toolset()
        return types.ListToolsResult(tools=[
            types.Tool(name=t.name, description=f.text(t.description) if f else t.description,
                       input_schema=f.schema(relaxed(t.input_schema, cfg.optional_defaults)) if f
                       else relaxed(t.input_schema, cfg.optional_defaults),
                       annotations=types.ToolAnnotations(read_only_hint=not t.writes, destructive_hint=False,
                                                         open_world_hint=False))
            for t in ts.for_listing(cfg, tools, caller.can_write)])

    async def call_tool(ctx, params) -> types.CallToolResult:
        caller = identity_of(ctx)
        name, args = params.name, params.arguments or {}
        cfg = toolset()
        m = cfg.messages
        extra: dict = {}
        if caller_tools is not None and (name == ts.FIND or name in ts.RUN_NAMES or name not in executor.tools):
            extra = {t.name: t for t in await anyio.to_thread.run_sync(caller_tools.tools, caller)
                     if t.name not in executor.tools}
        access = ts.Access({**executor.tools, **extra} if extra else executor.tools, caller.can_write,
                           frozenset(cfg.confirm))
        f = await _filter(caller)

        def _text(text: str, error: bool = False) -> types.CallToolResult:
            return types.CallToolResult(content=[types.TextContent(text=f.text(text) if f else text)], is_error=error)

        def _error(text: str) -> types.CallToolResult:
            return _text(text, True)
        try:
            if name == ts.FIND:
                return _text(compact.dumps(ts.find(cfg, access, args.get("group"), args.get("query"))))
            if name in ts.RUN_NAMES:  # run the real tool: same checks, the audit records the real name
                tool, args = ts.resolve(cfg, access, name, args)
                name = tool.name
        except (PermissionError, ValueError) as e:
            return _error(f"{type(e).__name__}: {e}")
        if name in extra:   # an extra tool: same read-only rule, run by the application
            if extra[name].writes and not caller.can_write:
                return _error(m.mcp_read_only)
            try:
                result = await anyio.to_thread.run_sync(caller_tools.run, caller, name, args)
            except Exception as e:  # limits, bad arguments, network: passed to the model as they are
                return _error(f"{type(e).__name__}: {e}")
            return _text(compact.dumps(result))
        tool = executor.tools.get(name)
        if tool is None:
            return _error(m.mcp_no_such_tool.format(name=name))
        if tool.writes and not caller.can_write:
            return _error(m.mcp_read_only)
        try:
            text = await anyio.to_thread.run_sync(call, executor, connect_fn, caller, name, args)
        except Exception as e:  # permission, parameter and database errors are passed to the model as they are
            return _error(f"{type(e).__name__}: {e}")
        return _text(text)

    async def list_prompts(ctx, params) -> types.ListPromptsResult:
        return types.ListPromptsResult(prompts=[types.Prompt(name=p.name, title=p.title, description=p.description)
                                                for p in prompts])

    async def get_prompt(ctx, params) -> types.GetPromptResult:
        p = prompts_by_name.get(params.name)
        if p is None:
            raise ValueError(toolset().messages.mcp_no_such_prompt.format(name=params.name))
        text = await anyio.to_thread.run_sync(p.text, identity_of(ctx))
        return types.GetPromptResult(description=p.get_description, messages=[
            types.PromptMessage(role="user", content=types.TextContent(text=text))])

    return Server(name, instructions=instructions, on_list_tools=list_tools, on_call_tool=call_tool,
                  on_list_prompts=list_prompts, on_get_prompt=get_prompt)
