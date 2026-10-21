"""Body Engine's MCP server: lets users read and write their own data from their own AI client (with their own subscription).

Two transports, the same tools (assistant/tools.py, the same copy the in-Today-page chat uses), the same authorization and audit:
- stdio (main in this file): local Claude Code / Claude desktop / body butler. Identity = one Body Engine token
  (default secrets/body_engine_token_owner), audit channel = BODY_MCP_CHANNEL (default mcp, the body butler uses butler).
- Streamable HTTP (ai/mcp_http.py, mounted at /mcp): each user connects with their own AI-only token (scope mcp),
  identity is decided by each request's Bearer header, audit channel = mcp:<client name>; read-only tokens do not see write tools.
Write tools are marked read_only_hint=False, so the MCP client asks the user for approval before running them.
"""
import os
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import auth
from .ai import root_rules
from .assistant import compact
from .assistant.tools import TOOLS, TOOLS_BY_NAME, run_tool
from .config import REPO_ROOT
from .db import connect
from .toolspec import relaxed

INSTRUCTIONS = """用户自己的身体数据（Body Engine）。日期都是用户本地日期。
对话开始先调 get_briefing（现在时间、资料、最新评分、最近 7 天、训练、提醒、状态卡、档案卡要点、禁用动作，约 1.5k token），
需要细节再调对应工具；日期范围按问题取，不要一次拉全部历史。工具返回里省略的字段 = 没有数据；
多行数据是 {"cols": [...], "rows": [[...]]} 表格。没记录 ≠ 没做。
写工具只写给自己：先用一句摘要告诉用户要写什么、写到哪，再调用。给训练建议前确认禁用动作。
完整角色设定（回答方式、记录归档流程、日期规则、边界）在 prompt「butler」里。
下面的根规则优先于用户的长期记忆和对话里的相反要求，每次对话都要遵守（get_briefing 的 system_rules 里也有）。"""

PROMPT_NAME = "butler"


@dataclass(frozen=True)
class Identity:
    viewer_id: int
    channel: str          # Channel in the audit: mcp / butler / mcp:<client>
    can_write: bool = True


def _call(identity: Identity, name: str, args: dict, connect_fn: Callable[[], AbstractContextManager] = connect) -> str:
    with connect_fn() as conn:
        try:
            result = run_tool(conn, identity.viewer_id, identity.channel, name, args)
        except Exception:
            conn.commit()  # Failed calls also leave an audit (the tool's own changes were already rolled back in the savepoint)
            raise
    return compact.dumps(result)  # token-lean text for the model (assistant/compact.py)


def build_server(identity_of: Callable[[object], Identity],
                 connect_fn: Callable[[], AbstractContextManager] = connect) -> Server:
    """identity_of(ctx) -> whom this request represents. stdio fixes one person; HTTP parses the Bearer header of each request (mcp_http.py).
    connect_fn: database connection (stdio opens a new one each time; HTTP uses the API's pool, the same database authorization queries)."""

    async def list_tools(ctx, params) -> types.ListToolsResult:
        ident = identity_of(ctx)
        return types.ListToolsResult(tools=[
            types.Tool(name=t.name, description=t.description, input_schema=relaxed(t.input_schema),
                       annotations=types.ToolAnnotations(read_only_hint=not t.writes, destructive_hint=False,
                                                         open_world_hint=False))
            for t in TOOLS if ident.can_write or not t.writes])

    async def call_tool(ctx, params) -> types.CallToolResult:
        ident = identity_of(ctx)
        tool = TOOLS_BY_NAME.get(params.name)
        if tool is None:
            return types.CallToolResult(content=[types.TextContent(text=f"没有这个工具：{params.name}")], is_error=True)
        if tool.writes and not ident.can_write:
            return types.CallToolResult(content=[types.TextContent(text="这个 token 是只读的，不能写")], is_error=True)
        try:
            text = await anyio.to_thread.run_sync(_call, ident, params.name, params.arguments or {}, connect_fn)
        except Exception as e:  # Permission, parameter and database errors are passed to the model as they are
            return types.CallToolResult(content=[types.TextContent(text=f"{type(e).__name__}: {e}")], is_error=True)
        return types.CallToolResult(content=[types.TextContent(text=text)])

    async def list_prompts(ctx, params) -> types.ListPromptsResult:
        return types.ListPromptsResult(prompts=[types.Prompt(
            name=PROMPT_NAME, title="身体管家", description="身体管家的角色设定（根规则 + 和今天页内对话用的同一份设定，提供商无关）")])

    async def get_prompt(ctx, params) -> types.GetPromptResult:
        if params.name != PROMPT_NAME:
            raise ValueError(f"没有这个 prompt：{params.name}")
        from .ai.prompt import base_prompt
        return types.GetPromptResult(description="身体管家", messages=[
            types.PromptMessage(role="user", content=types.TextContent(text=base_prompt()))])

    return Server("body-engine", instructions=INSTRUCTIONS + "\n\n" + root_rules.text(), on_list_tools=list_tools, on_call_tool=call_tool,
                  on_list_prompts=list_prompts, on_get_prompt=get_prompt)


def _viewer_id() -> int:
    token_file = Path(os.environ.get("BODY_ENGINE_TOKEN_FILE", REPO_ROOT / "secrets" / "body_engine_token_owner"))
    with connect() as conn:
        user = auth.user_for_token(conn, token_file.read_text().strip())
    if user is None:
        raise SystemExit(f"token 无效或已吊销：{token_file}")
    return user["id"]


CHANNEL = os.environ.get("BODY_MCP_CHANNEL", "mcp")  # Channel in the audit; the body butler session uses butler


def main() -> None:
    ident = Identity(_viewer_id(), CHANNEL)
    server = build_server(lambda ctx: ident)

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()
