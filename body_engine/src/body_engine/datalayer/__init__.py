"""datalayer: the framework that lets any AI read and write one user's personal data through MCP or an API key
(working name; issue #116, design: docs/datalayer-design.md).

It never imports Watch2Whoop's business modules (tests/test_datalayer_boundary.py): the application hands it its tools,
configuration and texts. Phase 2a holds the pure parts: tool definitions and schemas (spec), compact results (compact),
tool groups and meta tools (toolsets), playbook loading (playbooks) and the framework's texts (messages)."""
from . import compact, playbooks, spec, toolsets
from .messages import Messages
from .playbooks import PlaybookSource
from .spec import Tool, fill_defaults, nullable, relaxed, schema
from .toolsets import Access, ToolsetConfig

__all__ = ["compact", "playbooks", "spec", "toolsets", "Messages", "PlaybookSource", "Tool", "fill_defaults", "nullable",
           "relaxed", "schema", "Access", "ToolsetConfig"]
