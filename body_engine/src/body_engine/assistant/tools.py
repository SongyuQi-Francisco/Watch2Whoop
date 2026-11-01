"""Old import path of the assistant tool registry. The tools are registered in dl_app.py and run by the datalayer
framework's Executor (datalayer/execute.py, issue #116), the same copy for MCP and the in-page chat. The format is the
Claude API tool definition (name / description / input_schema)."""
from typing import Any

import psycopg

from ..datalayer.spec import Tool  # noqa: F401  (moved to the datalayer framework, issue #116; old import path)
from ..dl_app import APP, AUDIT, EXECUTOR
from ..toolspec import range_schema as _range_schema  # noqa: F401  (old name)
from ..toolspec import schema as _schema  # noqa: F401  (old name)
from .health_tools import SCORE_DETAIL_ONLY  # noqa: F401  (old import path)

TOOLS = APP.tools
TOOLS_BY_NAME = APP.by_name


def run_tool(conn: psycopg.Connection, viewer_id: int, channel: str, name: str, args: dict, *,
             provider_id: int | None = None, model: str | None = None) -> Any:
    """Run one tool: authorize -> call -> write audit (datalayer/execute.py). Confirmation of writes is the caller's job (chat loop / MCP client).
    The tool runs in a savepoint: on error only what it half-wrote is undone and the audit stays (the caller must also commit after an error, see mcp_server).
    In-Today-page chat (ai/chat.py) additionally records provider_id / model; for the MCP channel these two columns are empty."""
    return EXECUTOR.run(conn, viewer_id, channel, name, args, provider_id=provider_id, model=model)


def _audit(conn, user_id, channel, tool, args, ok, error, provider_id=None, model=None):
    AUDIT.record(conn, user_id, channel, tool, args, ok, error, provider_id=provider_id, model=model)
