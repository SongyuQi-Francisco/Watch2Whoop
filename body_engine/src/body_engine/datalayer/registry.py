"""The registration interface (issue #116, docs/datalayer-design.md section 3.2): an application hands the framework its
tools, rollback handlers and views in one module, then freezes the registry into an App.

The tool order is the registration order, because it is the order models and MCP clients get (a stable cached prefix).
Nothing is discovered automatically and nothing can be added after freeze, so tools/list cannot change at run time.
The tools that ask first are not a list of their own: App.confirm is read off the tools (confirm=True)."""
import dataclasses
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, ContextManager, Iterable, Mapping

from .audit import AuditSink
from .execute import Executor
from .messages import Messages
from .policy import Policy
from .spec import Tool
from .toolsets import ToolsetConfig
from .views import View
from .writelog import WriteLog


@dataclass(frozen=True)
class App:
    """A frozen registry. tools: every tool in registration order (by_name: the same, by name); confirm: the tools that
    always ask first; views: name -> View."""
    tools: tuple[Tool, ...]
    by_name: Mapping[str, Tool]
    executor: Executor
    confirm: frozenset[str]
    views: Mapping[str, View]
    toolset_factory: Callable[[frozenset[str]], ToolsetConfig]

    def toolset(self) -> ToolsetConfig:
        """The toolset configuration, built on every call (the application may read it from its own settings)."""
        return self.toolset_factory(self.confirm)

    def view_tools(self, view: str, extra: tuple[str, ...] = ()) -> tuple[Tool, ...]:
        return self.views[view].select(self.by_name, extra)


class Registry:
    def __init__(self, messages: Messages):
        self.messages = messages
        self._tools: dict[str, Tool] = {}
        self._rollbacks: dict[str, tuple[Callable, str | None]] = {}
        self._views: dict[str, View] = {}
        self._frozen = False

    def _open(self) -> None:
        if self._frozen:
            raise RuntimeError("the registry is frozen")

    def tools(self, specs: Iterable[Tool | dict], *, legacy: bool = False) -> None:
        """Add tools, in order. specs: Tool objects or dicts of Tool's fields. legacy: these handlers have the old
        signature (execute.invoke); a dict can still say legacy=False for itself, to move one tool of a module at a time."""
        self._open()
        for spec in specs:
            if isinstance(spec, Tool):
                tool = dataclasses.replace(spec, legacy=True) if legacy else spec
            else:
                tool = Tool(**{"legacy": legacy, **spec})
            if tool.name in self._tools:
                raise ValueError(f"tool {tool.name} is registered twice")
            if tool.confirm and not tool.writes:
                raise ValueError(f"tool {tool.name}: confirm is only for write tools")
            if tool.confirm_label and not tool.confirm:
                raise ValueError(f"tool {tool.name}: confirm_label without confirm")
            self._tools[tool.name] = tool

    def rollback(self, kind: str, fn: Callable, only_action: str | None = None) -> None:
        """Who writes a kind of write-log row registers how to put it back (WriteLog.register_rollback)."""
        self._open()
        if kind in self._rollbacks:
            raise ValueError(f"rollback of kind {kind} is registered twice")
        self._rollbacks[kind] = (fn, only_action)

    def view(self, name: str, view: View) -> None:
        self._open()
        if name in self._views:
            raise ValueError(f"view {name} is registered twice")
        self._views[name] = view

    def freeze(self, *, policy: Policy, audit: AuditSink, writelog: WriteLog,
               toolset: Callable[[frozenset[str]], ToolsetConfig], call_context: Callable[[], ContextManager] | None = None,
               after_write: Callable[[int], None] | None = None,
               optional_defaults: Mapping[str, Any] | None = None) -> App:
        """toolset(confirm) -> the ToolsetConfig, with the confirm tools read off the registry; the other arguments are
        Executor's. A view naming a tool that does not exist fails here: a typo must not widen or narrow what a
        restricted caller gets without anybody noticing."""
        self._open()
        for name, view in self._views.items():
            unknown = sorted({*(view.tools or ()), *view.exclude} - set(self._tools))
            if unknown:
                raise ValueError(f"view {name}: no tool {', '.join(unknown)}")
        self._frozen = True
        for kind, (fn, only_action) in self._rollbacks.items():
            writelog.register_rollback(kind, fn, only_action)
        by_name = MappingProxyType(dict(self._tools))
        executor = Executor(by_name, policy=policy, audit=audit, writelog=writelog, messages=self.messages,
                            call_context=call_context, after_write=after_write, optional_defaults=optional_defaults)
        return App(tools=tuple(self._tools.values()), by_name=by_name, executor=executor,
                   confirm=frozenset(t.name for t in self._tools.values() if t.confirm),
                   views=MappingProxyType(dict(self._views)), toolset_factory=toolset)
