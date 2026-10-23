"""Shared JSON-schema pieces for the AI tools (assistant/tools.py and the */tools.py spec modules).

The generic part (schema / nullable / relaxed / fill_defaults) lives in the datalayer framework (datalayer/spec.py,
issue #116); this module keeps Watch2Whoop's wording and defaults and the old import path.
Kept short on purpose: every tool definition is sent to the model on every call (MCP tools/list, in-page chat), docs/ai-assistant-plan.md §9.13.
Format follows Claude API strict tools: every property is in `required`, optional ones are nullable."""
from .datalayer import spec as _spec
from .datalayer.spec import _is_nullable, nullable, schema  # noqa: F401  (old import path)

USER = {"type": "string", "description": "me 或授权给你的家人 ID"}
DATE = {"type": "string", "description": "YYYY-MM-DD"}
START = {"type": "string", "description": "YYYY-MM-DD 含"}
END = {"type": "string", "description": "YYYY-MM-DD 含"}
OPT_START = nullable("string", "YYYY-MM-DD，null=默认")
OPT_END = nullable("string", "YYYY-MM-DD，null=今天")
OPT_DAY = nullable("string", "YYYY-MM-DD，null=今天")
DETAIL = {"type": "boolean", "description": "true=完整明细（大），默认 false 摘要"}


# ---------------------------------------------------------------- relaxed schema for MCP clients
# The strict form above (everything required, nullable = "not given") is what the Claude API's strict tools need (in-page chat).
# MCP clients get a relaxed copy: nullable properties and OPTIONAL_DEFAULTS are optional, so the model does not have to write
# `"x": null` for every unused field (fewer output tokens) and tools/list is shorter. fill_defaults puts the omitted values back
# before the handler runs (assistant/tools.run_tool), so handlers always see the full strict shape.
OPTIONAL_DEFAULTS = {"user": "me", "detail": False, "details": False}


def relaxed(s: dict) -> dict:
    return _spec.relaxed(s, OPTIONAL_DEFAULTS)


def fill_defaults(s: dict, value):
    """Fill what a relaxed caller left out: nullable -> None, OPTIONAL_DEFAULTS -> their default; recursing into objects / arrays."""
    return _spec.fill_defaults(s, value, OPTIONAL_DEFAULTS)
