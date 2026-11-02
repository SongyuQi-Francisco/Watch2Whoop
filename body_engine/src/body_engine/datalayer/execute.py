"""Running one tool (moved from assistant/tools.run_tool, issue #116, docs/datalayer-design.md section 2.3).

Executor.run: fill the defaults the relaxed schema left out -> whose data (policy.resolve_owner) -> writes only for
oneself, viewing another user needs Policy.can_view -> the handler in a savepoint (write tools inside a write-log
capture) -> audit -> after_write. Confirming writes is the caller's job (the chat loop, the MCP client).
On an error only what the tool half-wrote is undone; the audit row stays, so the caller must commit after an error.
A connection is whatever the application's tools use, plus transaction(): a context manager that is a savepoint, rolling
back to its start when the block raises. psycopg connections have it as they are.

Handlers take (ctx, args), with ctx a ToolContext. Tools with legacy=True keep the application's older signature
handler(conn, owner_id, args), with the viewer and the channel in args as _viewer_id / _channel (invoke adapts them)."""
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Mapping

from .audit import AuditSink
from .messages import ENGLISH, Messages
from .policy import Policy, SelfOnly, resolve_owner
from .spec import Tool, fill_defaults, relaxed
from .writelog import WriteLog


@dataclass(frozen=True)
class ToolContext:
    """What a handler gets besides its arguments: whose data (owner_id), who is asking (viewer_id), through which channel."""
    conn: Any
    owner_id: int
    viewer_id: int
    channel: str
    writelog: WriteLog

    def log(self, kind: str, action: str, target, before=None, after=None) -> int | None:
        """One row of the write log (what a write tool changed, with before / after values); does nothing in a read tool."""
        return self.writelog.record(self.conn, kind, action, target, before, after)


def invoke(tool: Tool, ctx: ToolContext, args: dict) -> Any:
    """Call a handler with the signature it has: (ctx, args), or the legacy adapter's (conn, owner_id, args)."""
    if tool.legacy:
        return tool.handler(ctx.conn, ctx.owner_id, {**args, "_viewer_id": ctx.viewer_id, "_channel": ctx.channel})
    return tool.handler(ctx, dict(args))


class Executor:
    """tools: name -> Tool (read at call time, so the application's mapping stays the single copy);
    policy: None = SelfOnly (nobody sees anybody else's data);
    call_context: a context manager around every call (Watch2Whoop: AI-facing texts in Chinese);
    after_write(viewer_id): runs after a successful write tool (Watch2Whoop: drop the cached briefing);
    optional_defaults: see spec.relaxed (what a relaxed caller may leave out)."""

    def __init__(self, tools: Mapping[str, Tool], *, audit: AuditSink, writelog: WriteLog, policy: Policy | None = None,
                 messages: Messages = ENGLISH, call_context: Callable[[], ContextManager] | None = None,
                 after_write: Callable[[int], None] | None = None, optional_defaults: Mapping[str, Any] | None = None):
        self.tools = tools
        self.policy = SelfOnly() if policy is None else policy
        self.audit = audit
        self.writelog = writelog
        self.messages = messages
        self.call_context = call_context or nullcontext
        self.after_write = after_write
        self.optional_defaults = optional_defaults

    def run(self, conn, viewer_id: int, channel: str, name: str, args: dict, **extra) -> Any:
        """extra: more audit columns (the in-page chat's provider_id / model)."""
        with self.call_context():
            return self._run(conn, viewer_id, channel, name, args, extra)

    def _missing_params(self, tool: Tool, full: dict, e: Exception) -> list[str]:
        """The handler read args["x"] and x was left out: a KeyError naming one of the tool's own properties. Returns every required
        argument still missing (so one message names them all), or [] for any other error. Only this case is turned into a message:
        a tool that copes with a missing argument keeps working and any other KeyError stays visible."""
        key = e.args[0] if isinstance(e, KeyError) and e.args else None
        if not (isinstance(key, str) and key in tool.input_schema.get("properties", {}) and full.get(key) is None):
            return []
        required = relaxed(tool.input_schema, self.optional_defaults).get("required", [])
        missing = [k for k in required if full.get(k) is None]
        return missing if key in missing else [*missing, key]

    def _run(self, conn, viewer_id, channel, name, args, extra) -> Any:
        tool = self.tools[name]
        full = fill_defaults(tool.input_schema, args, self.optional_defaults)  # MCP clients may leave out nullable / defaulted fields (spec.relaxed)
        owner_id = resolve_owner(viewer_id, full)
        m = self.messages
        try:
            if tool.writes and owner_id != viewer_id:
                raise PermissionError(m.write_only_self)
            if not self.policy.can_view(conn, viewer_id, owner_id, tool.scope):
                raise PermissionError(m.no_grant.format(owner=owner_id, scope=tool.scope))
            with conn.transaction():
                ctx = ToolContext(conn, owner_id, viewer_id, channel, self.writelog)
                if tool.writes:  # every AI write is logged with before / after values (writelog.py)
                    with self.writelog.capture(viewer_id, channel, name) as cap:
                        result = invoke(tool, ctx, full)
                        if not cap["ids"]:
                            self.writelog.record_generic(conn, full, result)
                else:
                    result = invoke(tool, ctx, full)
        except Exception as e:
            if missing := self._missing_params(tool, full, e):   # the model can fix this: say which arguments, not KeyError
                err = ValueError(m.missing_param.format(param=m.missing_sep.join(missing)))
                self.audit.record(conn, viewer_id, channel, name, args, False, str(err), **extra)
                raise err from None
            self.audit.record(conn, viewer_id, channel, name, args, False, str(e), **extra)
            raise
        self.audit.record(conn, viewer_id, channel, name, args, True, None, **extra)
        if tool.writes and self.after_write is not None:
            self.after_write(viewer_id)
        return result
