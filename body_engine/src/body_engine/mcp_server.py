"""Body Engine's MCP server: lets users read and write their own data from their own AI client (with their own subscription).

Two transports, the same tools (assistant/tools.py, the same copy the in-Today-page chat uses), the same authorization and audit:
- stdio (main in this file): local Claude Code / Claude desktop / body butler. Identity = one Body Engine token
  (default secrets/body_engine_token_owner), audit channel = BODY_MCP_CHANNEL (default mcp, the body butler uses butler).
- Streamable HTTP (ai/mcp_http.py, mounted at /mcp): each user connects with their own AI-only token (scope mcp),
  identity is decided by each request's Bearer header, audit channel = mcp:<client name>; read-only tokens do not see write tools.
Write tools are marked read_only_hint=False, so the MCP client asks the user for approval before running them.
tools/list sends only the resident core, the delete / undo tools and the meta tools (assistant/toolsets.py, issue #99);
every other tool is found with find_tools and runs through run_read_tool / run_write_tool, which call run_tool with the real name.
Rollout flag ai_parity (ai/parity.py), per signed-in user: tool descriptions, results, errors and the butler prompt go
through ai/naming.scrub, so the back end's name never reaches the AI (the server instructions never contain it).
The server itself is the datalayer framework's (datalayer/mcp/, issue #116); this module passes it Watch2Whoop's
instructions, prompts, configuration and the ai_parity filter, and keeps the old names (Identity, build_server, main).
"""
import os
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Callable

from mcp.server.lowlevel import Server

from . import auth
from .ai import naming, parity, root_rules
from .ai import playbooks
from .assistant import toolsets
from .assistant.tools import EXECUTOR, TOOLS
from .config import REPO_ROOT
from .datalayer.mcp import server as dl_server
from .datalayer.mcp import stdio as dl_stdio
from .datalayer.policy import Caller
from .db import connect

INSTRUCTIONS = """用户自己的身体数据（Body Engine）。日期都是用户本地日期。
对话开始先调 get_briefing（现在时间、资料、最新评分、最近 7 天、训练、提醒、状态卡、档案卡要点、禁用动作，约 1.5k token），
需要细节再调对应工具；日期范围按问题取，不要一次拉全部历史。
这里只列了常用工具；其余的（训练计划、食谱、采购、化验、补剂、长期记忆…）先用 find_tools 按组或关键词查到完整参数，
再用 run_read_tool（读）/ run_write_tool（写）调用，规矩和直接调用一样；删除 / 撤销类直接按名字调用。
工具返回里省略的字段 = 没有数据；
多行数据是 {"cols": [...], "rows": [[...]]} 表格。没记录 ≠ 没做。
写工具只写给自己：先用一句摘要告诉用户要写什么、写到哪，再调用。给训练建议前确认禁用动作。
完整角色设定（回答方式、记录归档流程、日期规则、边界）在 prompt「butler」里。
规划采购、排一周训练、复盘一周、照片记餐各有一份操作说明：prompt plan_shopping / plan_training_week / weekly_review /
log_meal_from_photo，或者调 get_playbook(name)，两边是同一份。
下面的根规则优先于用户的长期记忆和对话里的相反要求，每次对话都要遵守（get_briefing 的 system_rules 里也有）。"""

PROMPT_NAME = "butler"
Identity = Caller   # viewer_id, channel (in the audit: mcp / butler / mcp:<client>), can_write (old name)


def _call(identity: Identity, name: str, args: dict, connect_fn: Callable[[], AbstractContextManager] = connect) -> str:
    """Failed calls also leave an audit; token-lean text for the model (datalayer/mcp/server.call)."""
    return dl_server.call(EXECUTOR, connect_fn, identity, name, args)


def _parity_on(identity: Identity, connect_fn: Callable[[], AbstractContextManager] = connect) -> bool:
    with connect_fn() as conn:
        return parity.on(conn, identity.viewer_id)


class _Scrub:
    """ai_parity on: tool descriptions, schemas, results and errors go through ai/naming.scrub."""
    text = staticmethod(naming.scrub)
    schema = staticmethod(naming.scrub_schema)


def _butler_prompt(identity: Identity, connect_fn: Callable[[], AbstractContextManager]) -> str:
    from .ai.prompt import base_prompt
    text = base_prompt()
    if _parity_on(identity, connect_fn):
        text = f"{naming.scrub(text)}\n\n{parity.prompt(False)}"
    return text


def _prompts(connect_fn: Callable[[], AbstractContextManager]) -> list[dl_server.PromptSpec]:
    return [dl_server.PromptSpec(
        name=PROMPT_NAME, title="身体管家", description="身体管家的角色设定（根规则 + 和今天页内对话用的同一份设定，提供商无关）",
        get_description="身体管家", text=lambda identity: _butler_prompt(identity, connect_fn))] + [
        # the same text get_playbook returns (ai/playbooks/, issue #100)
        dl_server.PromptSpec(name=n, title=playbooks.title(n), description=playbooks.description(n),
                             get_description=playbooks.title(n), text=lambda identity, n=n: playbooks.text(n))
        for n in playbooks.NAMES]


def build_server(identity_of: Callable[[object], Identity],
                 connect_fn: Callable[[], AbstractContextManager] = connect) -> Server:
    """identity_of(ctx) -> whom this request represents. stdio fixes one person; HTTP parses the Bearer header of each request (mcp_http.py).
    connect_fn: database connection (stdio opens a new one each time; HTTP uses the API's pool, the same database authorization queries)."""
    return dl_server.build_server(
        "body-engine", INSTRUCTIONS + "\n\n" + root_rules.text(), identity_of, executor=EXECUTOR, tools=TOOLS,
        toolset=toolsets._cfg, connect_fn=connect_fn, prompts=_prompts(connect_fn),
        text_filter=lambda identity: _Scrub if _parity_on(identity, connect_fn) else None)


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
    dl_stdio.run(build_server(lambda ctx: ident))


if __name__ == "__main__":
    main()
