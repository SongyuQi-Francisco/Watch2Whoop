"""Tool groups and on-demand loading (issue #99, docs/ai-assistant-plan.md §9.17).

Only the resident core (ai/params.CORE_TOOLS) and, in the in-page chat, the groups of the pages it has been on are sent with each model
call. The rest stays reachable through three meta tools that work with every MCP client (no tools/list_changed needed):
- find_tools(group | query): the full (relaxed) schemas of matching tools;
- run_read_tool(name, args): runs a read tool (read_only_hint = true);
- run_write_tool(name, args): runs a write tool (read_only_hint = false, so MCP clients still ask before writing).
The caller (mcp_server.call_tool, ai/chat._loop) resolves a meta call to the real tool with `resolve`, then runs that tool
through assistant.tools.run_tool as if it had been called directly: same authorization, the audit records the real name,
ALWAYS_CONFIRM is judged by the real name, and the fallback AI only reaches tools in its own whitelist (Access.tools).
"""
import json
from dataclasses import dataclass, field
from typing import Iterable

from ..ai import params
from ..toolspec import nullable, relaxed, schema
from .tools import TOOLS, Tool

FIND, RUN_READ, RUN_WRITE = "find_tools", "run_read_tool", "run_write_tool"
RUN_NAMES = (RUN_READ, RUN_WRITE)


def _dispatched(conn, uid, a):  # never called: meta tools are dispatched by the channel (see module doc)
    raise RuntimeError("meta tools are resolved by the caller")


_ARGS = {"type": ["object", "null"], "description": "它的参数，和直接调用一样（user 不填 = 自己）"}
META_TOOLS: list[Tool] = [
    Tool(name=FIND,
         description="找这次没加载的工具，返回完整参数说明；再用 run_read_tool / run_write_tool 调用（看 run 字段）。"
                     "group、query 都不填 = 列出所有组和工具名。",
         input_schema=schema({"group": nullable("string", " / ".join(params.GROUPS)),
                              "query": nullable("string", "关键词，空格分开，如「化验 趋势」")}),
         handler=_dispatched, scope="", group="meta"),
    Tool(name=RUN_READ,
         description="调用一个只读工具（find_tools 查到的）。name = 工具名。",
         input_schema=schema({"name": {"type": "string"}, "args": _ARGS}),
         handler=_dispatched, scope="", group="meta", strict=False),
    Tool(name=RUN_WRITE,
         description="调用一个写工具（find_tools 查到的），规矩和直接调用一样：只写给自己，先一句话说要写什么。",
         input_schema=schema({"name": {"type": "string"}, "args": _ARGS}),
         handler=_dispatched, scope="", group="meta", writes=True, strict=False),
]
META_BY_NAME = {t.name: t for t in META_TOOLS}


def definition_chars(tools: Iterable[Tool]) -> int:
    """Size of the tool definitions as tools/list sends them (relaxed schema), in characters."""
    return sum(len(json.dumps({"name": t.name, "description": t.description, "input_schema": relaxed(t.input_schema)},
                              ensure_ascii=False)) for t in tools)


def page_groups(page: str | None, per_page: int | None = None) -> tuple[str, ...]:
    """ai/params.PAGE_GROUPS for a Today page id; "<tab>.<sub>" falls back to "<tab>"; unknown or empty = no extra group.
    per_page: only the first that many (the fallback AI with ai_parity, params.FALLBACK_PARITY_PAGE_GROUPS)."""
    if not page:
        return ()
    if page in params.PAGE_GROUPS:
        return params.PAGE_GROUPS[page][:per_page]
    return params.PAGE_GROUPS.get(page.split(".", 1)[0], ())[:per_page]


def for_page(tools: Iterable[Tool], page: str | list[str] | None, per_page: int | None = None) -> list[Tool]:
    """In-page chat: core + the groups of the page (or of every page the conversation has been on), in `tools` order so
    the same pages always give the same list, then the meta tools. Adding a page only inserts tools: the ones already
    sent keep their relative order."""
    pages = [page] if isinstance(page, str) else (page or [])
    groups = {g for p in pages for g in page_groups(p, per_page)}
    return [t for t in tools if t.name in params.CORE_TOOLS or t.group in groups] + META_TOOLS


def known_page(page: str | None) -> bool:
    """A page id ai/params.PAGE_GROUPS knows (directly or by its tab); only these are kept on a conversation."""
    return bool(page) and (page in params.PAGE_GROUPS or page.split(".", 1)[0] in params.PAGE_GROUPS)


def fit_pages(tools: Iterable[Tool], pages: list[str], budget: int, per_page: int | None = None) -> list[str]:
    """Pages oldest -> newest (the last is the current one). Drop the oldest until core + their groups + meta fit the
    budget; the current page always stays. Dropped groups are still reachable through find_tools."""
    tools = list(tools)
    pages = list(pages)
    while len(pages) > 1 and definition_chars(for_page(tools, pages, per_page)) > budget:
        pages = pages[1:]
    return pages


def visit_page(tools: Iterable[Tool], pages: list[str], page: str | None, budget: int,
               per_page: int | None = None) -> list[str]:
    """The conversation's pages after a message from `page`: a known page moves to the end (most recent), then the oldest
    ones are dropped while over budget. Moving or re-visiting never changes the tool set; adding or dropping does once."""
    if not known_page(page):
        return list(pages)
    return fit_pages(tools, [p for p in pages if p != page] + [page], budget, per_page)


def for_mcp(can_write: bool) -> list[Tool]:
    """MCP tools/list: core + the ALWAYS_CONFIRM tools (the client's approval rules go by tool name) + the meta tools.
    Read-only tokens see no write tool and no run_write_tool."""
    listed = [t for t in TOOLS if t.name in params.CORE_TOOLS or t.name in params.ALWAYS_CONFIRM]
    return [t for t in listed + META_TOOLS if can_write or not t.writes]


@dataclass(frozen=True)
class Access:
    """What one caller may reach through the meta tools.
    tools: name -> Tool it may run (the fallback AI: its own whitelist with its own schemas);
    direct_only: tools that must be called by their own name, not through run_write_tool (MCP: ALWAYS_CONFIRM, so the
    client's approval rule for that name applies)."""
    tools: dict
    can_write: bool
    direct_only: frozenset = field(default_factory=frozenset)


def _entry(access: Access, t: Tool) -> dict:
    run = "直接调用" if t.name in access.direct_only else (RUN_WRITE if t.writes else RUN_READ)
    return {"name": t.name, "run": run, "description": t.description, "input_schema": relaxed(t.input_schema)}


def find(access: Access, group: str | None = None, query: str | None = None) -> dict:
    visible = [t for t in access.tools.values() if access.can_write or not t.writes]
    if group:
        if group not in params.GROUPS:
            raise ValueError(f"没有这个组：{group}；可用的组：{' / '.join(params.GROUPS)}")
        return {"group": group, "tools": [_entry(access, t) for t in visible if t.group == group]}
    terms = [w.lower() for w in (query or "").split() if w]
    if terms:
        scored = [(sum(w in f"{t.name} {t.description}".lower() for w in terms), i, t) for i, t in enumerate(visible)]
        hits = [t for n, _, t in sorted(scored, key=lambda x: (-x[0], x[1])) if n]
        out = {"query": query, "tools": [_entry(access, t) for t in hits[:params.FIND_TOOLS_MAX]]}
        if len(hits) > params.FIND_TOOLS_MAX:
            out["more"] = [t.name for t in hits[params.FIND_TOOLS_MAX:]]
        if not hits:
            out["hint"] = "没找到：不填参数调 find_tools 看所有组，或按 group 查"
        return out
    return {"groups": [{"group": g, "about": about, "tools": [t.name for t in visible if t.group == g]}
                       for g, about in params.GROUPS.items()],
            "hint": "用 find_tools(group=…) 拿完整参数说明"}


def resolve(access: Access, meta: str, args: dict) -> tuple[Tool, dict]:
    """run_read_tool / run_write_tool -> (the real tool, its args). Raises PermissionError / ValueError with a message for the model."""
    name = (args or {}).get("name")
    inner = (args or {}).get("args")
    if isinstance(inner, str):   # some models send the arguments as a JSON string
        try:
            inner = json.loads(inner) if inner.strip() else {}
        except ValueError:
            raise ValueError("args 要是一个对象（JSON）")
    inner = inner or {}
    if not isinstance(inner, dict):
        raise ValueError("args 要是一个对象（JSON）")
    if not isinstance(name, str) or name in META_BY_NAME:
        raise ValueError("name 要填一个工具名（find_tools 查到的）")
    tool = access.tools.get(name)
    if tool is None:
        raise ValueError(f"没有这个工具，或这里不能用：{name}（先用 find_tools 查）")
    if meta == RUN_READ and tool.writes:
        raise PermissionError(f"{name} 是写工具，要用 run_write_tool")
    if meta == RUN_WRITE and not tool.writes:
        raise PermissionError(f"{name} 是只读工具，要用 run_read_tool")
    if meta == RUN_WRITE and not access.can_write:
        raise PermissionError("这个 token / 这次登录是只读的，不能写")
    if name in access.direct_only:
        raise PermissionError(f"{name} 要直接调用（删除 / 撤销类，客户端会先请用户确认）")
    missing = [k for k in relaxed(tool.input_schema).get("required", []) if inner.get(k) is None]
    if missing:   # before any confirm card: a call that cannot run is refused here
        raise ValueError(f"{name} 缺少参数：{'、'.join(missing)}（参数说明用 find_tools 查）")
    return tool, inner
