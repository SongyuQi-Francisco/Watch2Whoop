"""Tool groups and on-demand loading (moved from assistant/toolsets.py, issue #99 / #116, docs/ai-assistant-plan.md §9.17).

Only the resident core and, in a chat, the groups of the contexts it has been in (Watch2Whoop: Today page ids) are sent
with each model call. The rest stays reachable through three meta tools that work with every MCP client (no
tools/list_changed needed):
- find_tools(group | query): the full (relaxed) schemas of matching tools;
- run_read_tool(name, args): runs a read tool (read_only_hint = true);
- run_write_tool(name, args): runs a write tool (read_only_hint = false, so MCP clients still ask before writing).
The caller resolves a meta call to the real tool with `resolve`, then runs that tool as if it had been called directly:
same authorization, the audit records the real name, confirm tools are judged by the real name, and a restricted
caller only reaches tools in its own Access.tools.
Everything application-specific (core list, groups, contexts, confirm tools, texts, budgets) comes in a ToolsetConfig.
"""
import json
from dataclasses import dataclass, field
from typing import Any, Collection, Iterable, Mapping

from .messages import Messages
from .spec import Tool, nullable, relaxed, schema

FIND, RUN_READ, RUN_WRITE = "find_tools", "run_read_tool", "run_write_tool"
RUN_NAMES = (RUN_READ, RUN_WRITE)


def _dispatched(conn, uid, a):  # never called: meta tools are dispatched by the channel (see module doc)
    raise RuntimeError("meta tools are resolved by the caller")


def meta_tools(groups: Mapping[str, str], messages: Messages) -> list[Tool]:
    """The three meta tools; `groups` (group -> description) names the groups find_tools accepts."""
    args = {"type": ["object", "null"], "description": messages.run_args_param}
    return [
        Tool(name=FIND, description=messages.find_description,
             input_schema=schema({"group": nullable("string", messages.find_group_param_sep.join(groups)),
                                  "query": nullable("string", messages.find_query_param)}),
             handler=_dispatched, scope="", group="meta"),
        Tool(name=RUN_READ, description=messages.run_read_description,
             input_schema=schema({"name": {"type": "string"}, "args": args}),
             handler=_dispatched, scope="", group="meta", strict=False),
        Tool(name=RUN_WRITE, description=messages.run_write_description,
             input_schema=schema({"name": {"type": "string"}, "args": args}),
             handler=_dispatched, scope="", group="meta", writes=True, strict=False),
    ]


@dataclass(frozen=True)
class ToolsetConfig:
    """core: tools sent on every call; groups: group -> one line for find_tools' index (its order is the index order);
    contexts: context id -> extra groups ("<a>.<b>" falls back to "<a>"); confirm: tools that always ask first (listed
    in MCP tools/list and called by their own name, so the client's approval rules by name apply); meta: meta_tools(...);
    find_max: full schemas find_tools(query) returns at most; optional_defaults: see spec.relaxed."""
    core: Collection[str]
    groups: Mapping[str, str]
    contexts: Mapping[str, tuple]
    confirm: Collection[str]
    meta: list
    find_max: int
    messages: Messages
    optional_defaults: Mapping[str, Any] = field(default_factory=dict)


def definition_chars(tools: Iterable[Tool], optional_defaults: Mapping[str, Any] | None = None) -> int:
    """Size of the tool definitions as tools/list sends them (relaxed schema), in characters."""
    return sum(len(json.dumps({"name": t.name, "description": t.description,
                               "input_schema": relaxed(t.input_schema, optional_defaults)},
                              ensure_ascii=False)) for t in tools)


def context_groups(cfg: ToolsetConfig, context: str | None) -> tuple[str, ...]:
    """cfg.contexts for a context id; "<a>.<b>" falls back to "<a>"; unknown or empty = no extra group."""
    if not context:
        return ()
    if context in cfg.contexts:
        return cfg.contexts[context]
    return cfg.contexts.get(context.split(".", 1)[0], ())


def for_context(cfg: ToolsetConfig, tools: Iterable[Tool], context: str | list[str] | None) -> list[Tool]:
    """Core + the groups of the context (or of every context the conversation has been in), in `tools` order so the
    same contexts always give the same list, then the meta tools. Adding a context only inserts tools: the ones already
    sent keep their relative order."""
    contexts = [context] if isinstance(context, str) else (context or [])
    groups = {g for c in contexts for g in context_groups(cfg, c)}
    return [t for t in tools if t.name in cfg.core or t.group in groups] + cfg.meta


def known_context(cfg: ToolsetConfig, context: str | None) -> bool:
    """A context id cfg.contexts knows (directly or by its prefix); only these are kept on a conversation."""
    return bool(context) and (context in cfg.contexts or context.split(".", 1)[0] in cfg.contexts)


def fit_contexts(cfg: ToolsetConfig, tools: Iterable[Tool], contexts: list[str], budget: int) -> list[str]:
    """Contexts oldest -> newest (the last is the current one). Drop the oldest until core + their groups + meta fit the
    budget; the current one always stays. Dropped groups are still reachable through find_tools."""
    tools = list(tools)
    contexts = list(contexts)
    while len(contexts) > 1 and definition_chars(for_context(cfg, tools, contexts), cfg.optional_defaults) > budget:
        contexts = contexts[1:]
    return contexts


def visit_context(cfg: ToolsetConfig, tools: Iterable[Tool], contexts: list[str], context: str | None,
                  budget: int) -> list[str]:
    """The conversation's contexts after a message from `context`: a known one moves to the end (most recent), then the
    oldest are dropped while over budget. Moving or re-visiting never changes the tool set; adding or dropping does once."""
    if not known_context(cfg, context):
        return list(contexts)
    return fit_contexts(cfg, tools, [c for c in contexts if c != context] + [context], budget)


def for_listing(cfg: ToolsetConfig, tools: Iterable[Tool], can_write: bool) -> list[Tool]:
    """MCP tools/list: core + the confirm tools (the client's approval rules go by tool name) + the meta tools.
    Read-only callers see no write tool and no run_write_tool."""
    listed = [t for t in tools if t.name in cfg.core or t.name in cfg.confirm]
    return [t for t in listed + cfg.meta if can_write or not t.writes]


@dataclass(frozen=True)
class Access:
    """What one caller may reach through the meta tools.
    tools: name -> Tool it may run (a restricted caller: its own whitelist with its own schemas);
    direct_only: tools that must be called by their own name, not through run_write_tool (MCP: the confirm tools, so the
    client's approval rule for that name applies)."""
    tools: dict
    can_write: bool
    direct_only: frozenset = field(default_factory=frozenset)


def _entry(cfg: ToolsetConfig, access: Access, t: Tool) -> dict:
    run = cfg.messages.run_direct if t.name in access.direct_only else (RUN_WRITE if t.writes else RUN_READ)
    return {"name": t.name, "run": run, "description": t.description,
            "input_schema": relaxed(t.input_schema, cfg.optional_defaults)}


def find(cfg: ToolsetConfig, access: Access, group: str | None = None, query: str | None = None) -> dict:
    m = cfg.messages
    visible = [t for t in access.tools.values() if access.can_write or not t.writes]
    if group:
        if group not in cfg.groups:
            raise ValueError(m.unknown_group.format(group=group, groups=m.group_sep.join(cfg.groups)))
        return {"group": group, "tools": [_entry(cfg, access, t) for t in visible if t.group == group]}
    terms = [w.lower() for w in (query or "").split() if w]
    if terms:
        scored = [(sum(w in f"{t.name} {t.description}".lower() for w in terms), i, t) for i, t in enumerate(visible)]
        hits = [t for n, _, t in sorted(scored, key=lambda x: (-x[0], x[1])) if n]
        out = {"query": query, "tools": [_entry(cfg, access, t) for t in hits[:cfg.find_max]]}
        if len(hits) > cfg.find_max:
            out["more"] = [t.name for t in hits[cfg.find_max:]]
        if not hits:
            out["hint"] = m.find_no_hits
        return out
    return {"groups": [{"group": g, "about": about, "tools": [t.name for t in visible if t.group == g]}
                       for g, about in cfg.groups.items()],
            "hint": m.find_index_hint}


def resolve(cfg: ToolsetConfig, access: Access, meta: str, args: dict) -> tuple[Tool, dict]:
    """run_read_tool / run_write_tool -> (the real tool, its args). Raises PermissionError / ValueError with a message for the model."""
    m = cfg.messages
    name = (args or {}).get("name")
    inner = (args or {}).get("args")
    if isinstance(inner, str):   # some models send the arguments as a JSON string
        try:
            inner = json.loads(inner) if inner.strip() else {}
        except ValueError:
            raise ValueError(m.args_not_object)
    inner = inner or {}
    if not isinstance(inner, dict):
        raise ValueError(m.args_not_object)
    if not isinstance(name, str) or name in RUN_NAMES or name == FIND:
        raise ValueError(m.name_required)
    tool = access.tools.get(name)
    if tool is None:
        raise ValueError(m.no_such_tool.format(name=name))
    if meta == RUN_READ and tool.writes:
        raise PermissionError(m.use_run_write.format(name=name))
    if meta == RUN_WRITE and not tool.writes:
        raise PermissionError(m.use_run_read.format(name=name))
    if meta == RUN_WRITE and not access.can_write:
        raise PermissionError(m.read_only)
    if name in access.direct_only:
        raise PermissionError(m.call_directly.format(name=name))
    missing = [k for k in relaxed(tool.input_schema, cfg.optional_defaults).get("required", []) if inner.get(k) is None]
    if missing:   # before any confirm card: a call that cannot run is refused here
        raise ValueError(m.missing_args.format(name=name, missing=m.missing_sep.join(missing)))
    return tool, inner
