"""Tool groups and on-demand loading (issue #99, docs/ai-assistant-plan.md §9.17): Watch2Whoop's configuration of the
datalayer framework's toolsets (datalayer/toolsets.py, issue #116), under the old import path and signatures.

Only the resident core (ai/params.CORE_TOOLS) and, in the in-page chat, the groups of the pages it has been on are sent
with each model call; the rest is reached through the meta tools find_tools / run_read_tool / run_write_tool. The caller
(mcp_server.call_tool, ai/chat._loop) resolves a meta call to the real tool with `resolve`, then runs that tool through
assistant.tools.run_tool as if it had been called directly. A "context" in the framework is a Today page id here.
The configuration is read from ai/params on every call, so it stays the single place to change it.
"""
from typing import Iterable

from ..ai import params
from ..ai.datalayer_texts import MESSAGES
from ..datalayer import toolsets as _ts
from ..datalayer.toolsets import FIND, RUN_NAMES, RUN_READ, RUN_WRITE, Access  # noqa: F401  (old import path)
from ..toolspec import OPTIONAL_DEFAULTS
from .tools import TOOLS, Tool

META_TOOLS: list[Tool] = _ts.meta_tools(params.GROUPS, MESSAGES)
META_BY_NAME = {t.name: t for t in META_TOOLS}


def _cfg() -> _ts.ToolsetConfig:
    return _ts.ToolsetConfig(core=params.CORE_TOOLS, groups=params.GROUPS, contexts=params.PAGE_GROUPS,
                             confirm=params.ALWAYS_CONFIRM, meta=META_TOOLS, find_max=params.FIND_TOOLS_MAX,
                             messages=MESSAGES, optional_defaults=OPTIONAL_DEFAULTS)


def definition_chars(tools: Iterable[Tool]) -> int:
    """Size of the tool definitions as tools/list sends them (relaxed schema), in characters."""
    return _ts.definition_chars(tools, OPTIONAL_DEFAULTS)


def page_groups(page: str | None) -> tuple[str, ...]:
    """ai/params.PAGE_GROUPS for a Today page id; "<tab>.<sub>" falls back to "<tab>"; unknown or empty = no extra group."""
    return _ts.context_groups(_cfg(), page)


def for_page(tools: Iterable[Tool], page: str | list[str] | None) -> list[Tool]:
    """In-page chat: core + the groups of the page (or of every page the conversation has been on), then the meta tools."""
    return _ts.for_context(_cfg(), tools, page)


def known_page(page: str | None) -> bool:
    """A page id ai/params.PAGE_GROUPS knows (directly or by its tab); only these are kept on a conversation."""
    return _ts.known_context(_cfg(), page)


def fit_pages(tools: Iterable[Tool], pages: list[str], budget: int) -> list[str]:
    """Pages oldest -> newest; drop the oldest until core + their groups + meta fit the budget (the current page stays)."""
    return _ts.fit_contexts(_cfg(), tools, pages, budget)


def visit_page(tools: Iterable[Tool], pages: list[str], page: str | None, budget: int) -> list[str]:
    """The conversation's pages after a message from `page` (moved to the end, then trimmed to the budget)."""
    return _ts.visit_context(_cfg(), tools, pages, page, budget)


def for_mcp(can_write: bool) -> list[Tool]:
    """MCP tools/list: core + the ALWAYS_CONFIRM tools + the meta tools; read-only tokens see no write tool."""
    return _ts.for_listing(_cfg(), TOOLS, can_write)


def find(access: Access, group: str | None = None, query: str | None = None) -> dict:
    return _ts.find(_cfg(), access, group, query)


def resolve(access: Access, meta: str, args: dict) -> tuple[Tool, dict]:
    """run_read_tool / run_write_tool -> (the real tool, its args). Raises PermissionError / ValueError with a message for the model."""
    return _ts.resolve(_cfg(), access, meta, args)
