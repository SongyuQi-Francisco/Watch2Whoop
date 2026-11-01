"""datalayer: the framework that lets any AI read and write one user's personal data through MCP or an API key
(working name; issue #116, design: docs/datalayer-design.md).

It never imports Watch2Whoop's business modules (tests/test_datalayer_boundary.py): the application hands it its tools,
configuration and texts. Phase 2a moved the pure parts: tool definitions and schemas (spec), compact results (compact),
tool groups, meta tools and their size budgets (toolsets), playbook loading (playbooks) and the framework's texts (messages). Phase 2b
moved running a tool (execute: Executor; policy: Caller, Policy), the audit (audit: AuditSink, PgAudit), the AI write
log and its rollback dispatch by kind (writelog: WriteLog) and the MCP transports (mcp.server, mcp.stdio, mcp.http). Phase 3
added the registration interface (registry: Registry, App), restricted callers (views: View) and the (ctx, args) handler
signature (execute: ToolContext; older handlers keep working with legacy=True). Phase 4 made it usable on its own: English
default texts (messages: ENGLISH), a default policy (policy: SelfOnly), the write log's storage behind a protocol
(writelog: WriteLogStore), optional MCP resources (mcp.server: ResourceSpec), an example without health data
(examples/reading-notes) and its documentation (README.md and REFERENCE.md next to this file)."""
from . import compact, playbooks, spec, toolsets
from .audit import AuditSink, PgAudit
from .execute import Executor, ToolContext
from .messages import ENGLISH, Messages
from .playbooks import PlaybookSource
from .policy import Caller, Policy, SelfOnly
from .registry import App, Registry
from .spec import Tool, fill_defaults, nullable, relaxed, schema
from .toolsets import Access, Budgets, ToolsetConfig
from .views import View
from .writelog import PgWriteLogStore, WriteLog, WriteLogStore

__all__ = ["compact", "playbooks", "spec", "toolsets", "AuditSink", "PgAudit", "Executor", "ToolContext", "ENGLISH", "Messages",
           "PlaybookSource", "Caller", "Policy", "SelfOnly", "App", "Registry", "Tool", "fill_defaults", "nullable", "relaxed",
           "schema", "Access", "Budgets", "ToolsetConfig", "View", "PgWriteLogStore", "WriteLog", "WriteLogStore"]
