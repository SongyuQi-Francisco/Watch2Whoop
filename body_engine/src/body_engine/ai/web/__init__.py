"""Server-side web tools for the in-page chat (issue #150, docs/ai-assistant-plan.md §9.20): web_search and web_fetch.

Who gets them (server_tools): users with the rollout flag ai_web whose chat AI has no web access of its own, i.e. the fallback
AI (the admin's DeepSeek) and openai_compatible providers. Anthropic / OpenAI / Gemini keys use the vendor's own web tools
instead (ai_providers.web_tools, "allow web access" in AI settings, billed on the user's key). web_search only when a search API
key is configured (search.configured); web_fetch needs no key. MCP clients (and the body butler, which connects through MCP)
get the same two tools behind their own flag mcp_web (ai/web/mcp.py): through find_tools in the grocery group, never in tools/list.
Behind the same flag the fallback AI also gets the grocery tools it needs to store what it found and build the list
(FALLBACK_GROCERY).

Rules (params.py): per person per local day SEARCHES_PER_DAY / FETCHES_PER_DAY; each search costs the admin params.PRICE_USD,
recorded in ai_usage (purpose web_search, cost_cny) and counted against the person's fallback quota (fallback.month_spent_cny
counts the model calls and these together); a monthly cap for all users together. Pages are read only when the user's request
needs them, robots.txt is obeyed, and a block of any kind stops the attempt (fetch.py). Only an excerpt goes back to the model;
what is kept is what the model writes with save_product / record_store_prices (name, size, price, store, URL, date).
Every excerpt (search snippets, page text) carries params.UNTRUSTED_NOTE: page content is data for the model, not instructions.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import psycopg

from ...assistant.tools import Tool
from ...toolspec import schema
from .. import catalog
from . import fetch as F
from . import params as P
from . import search as S

SEARCH, FETCH = "web_search", "web_fetch"
NAMES = (SEARCH, FETCH)
# Grocery tools the fallback AI gets with ai_web: look up -> store -> build the list (its base whitelist only reads grocery data)
# (= what playbooks/plan_shopping.md needs beyond the base whitelist; tests/test_playbooks.py checks it)
FALLBACK_GROCERY = ("get_shopping_context", "set_stores", "generate_shopping_list", "add_shopping_items", "remove_shopping_items",
                    "set_pantry", "save_product", "record_store_prices", "mark_bought")
TELL_USER = "这家超市的网页读不到，可以把商品页截图或小票发给我"


def _dispatched(conn, uid, a):  # never called: chat._exec sends these names to run()
    raise RuntimeError("web tools are run by ai/web.run")


SEARCH_TOOL = Tool(name=SEARCH, group="grocery", scope="", handler=_dispatched,
                   description="联网搜索（服务器代查，按次计费，每天有上限）：只放商品 / 店名关键词，不放健康信息。返回标题、链接、摘要。",
                   input_schema=schema({"query": {"type": "string"}}))
FETCH_TOOL = Tool(name=FETCH, group="grocery", scope="", handler=_dispatched,
                  description="读一个公开网页的商品信息和正文（遵守 robots.txt）。blocked=true 就停：照 tell_user 请用户发截图或小票，"
                              "不要换链接重试。",
                  input_schema=schema({"url": {"type": "string"}}))


def _wanted(conn: psycopg.Connection, user_id: int, provider: dict) -> bool:
    from ...rollout import flags
    from .. import fallback
    native = provider["kind"] in catalog.WEB_TOOL_KINDS and not fallback.is_fallback(provider)
    return not native and flags.enabled(conn, user_id, P.FLAG)


def server_tools(conn: psycopg.Connection, user_id: int, provider: dict) -> list[Tool]:
    """The web tools this user's chat gets now ([] with the flag off, or when the provider has its own web tools)."""
    if not _wanted(conn, user_id, provider):
        return []
    return ([SEARCH_TOOL] if S.configured() else []) + [FETCH_TOOL]


def fallback_extra(conn: psycopg.Connection, user_id: int) -> tuple[str, ...]:
    from ...rollout import flags
    return FALLBACK_GROCERY if flags.enabled(conn, user_id, P.FLAG) else ()


# ---------------------------------------------------------------- limits and usage
def _tz(conn, user_id: int) -> str:
    return (conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone() or {}).get("timezone") or "UTC"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def used_today(conn: psycopg.Connection, user_id: int, purpose: str) -> int:
    start = _now().astimezone(ZoneInfo(_tz(conn, user_id))).replace(hour=0, minute=0, second=0, microsecond=0)
    return conn.execute("SELECT count(*) AS n FROM ai_usage WHERE user_id = %s AND purpose = %s AND at >= %s",
                        (user_id, purpose, start)).fetchone()["n"]


def _month_searches(conn: psycopg.Connection) -> int:
    start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return conn.execute("SELECT count(*) AS n FROM ai_usage WHERE purpose = 'web_search' AND at >= %s", (start,)).fetchone()["n"]


def search_cost_cny(provider_name: str) -> float:
    return P.PRICE_USD[provider_name] * P.USD_CNY


def _record(conn, user_id: int, provider: dict, conv_id: int | None, purpose: str, model: str, cost_cny: float) -> None:
    conn.execute("""INSERT INTO ai_usage (user_id, provider_id, conversation_id, kind, model, cost_usd, cost_cny, purpose)
                    VALUES (%s,%s,%s,%s,%s,0,%s,%s)""",
                 (user_id, provider.get("id"), conv_id, provider["kind"], model, round(cost_cny, 6), purpose))


def _limit_error(conn, user_id: int, name: str) -> str | None:
    """Why this call may not run now (text for the model), or None."""
    from .. import fallback
    if name == SEARCH:
        if used_today(conn, user_id, "web_search") >= P.SEARCHES_PER_DAY:
            return f"今天的联网搜索次数用完了（{P.SEARCHES_PER_DAY} 次），明天再查；现在请用户发商品页截图或小票"
        if _month_searches(conn) >= P.SEARCHES_PER_MONTH_ALL:
            return "这个月服务器的联网搜索总额度用完了；请用户发商品页截图或小票"
        p = S.provider()
        a = fallback.allowance(conn, user_id)   # with the quota cards used this month (#182)
        if a["spent_cny"] + search_cost_cny(p.name) > a["quota_cny"]:
            return "本月 AI 额度不够再搜索了；请用户发商品页截图或小票"
    elif used_today(conn, user_id, "web_fetch") >= P.FETCHES_PER_DAY:
        return f"今天读网页的次数用完了（{P.FETCHES_PER_DAY} 次），明天再查；现在请用户发商品页截图或小票"
    return None


# ---------------------------------------------------------------- run
def run(conn: psycopg.Connection, user_id: int, provider: dict, conv_id: int | None, name: str, args: dict) -> dict:
    """Run web_search / web_fetch for the caller (always their own request). Limits refused -> PermissionError (the chat
    loop / the MCP server passes the text to the model); a blocked page is a normal result with blocked=true and tell_user.
    provider: the chat's provider row, or ai/web/mcp.PROVIDER for MCP (ai_usage.kind 'mcp', no provider_id). The limits
    count every row of this user whatever the channel, so the chat and MCP share one daily allowance and one quota."""
    if name not in NAMES:
        raise ValueError(f"没有这个工具：{name}")
    error = _limit_error(conn, user_id, name)
    if error:
        raise PermissionError(error)
    if name == SEARCH:
        query = " ".join(str(args.get("query") or "").split())[:P.QUERY_MAX_CHARS]
        if not query:
            raise ValueError("query 不能为空")
        p, results = S.search(query, S.country_for(_tz(conn, user_id)))   # SearchError: nothing billed, nothing counted
        _record(conn, user_id, provider, conv_id, "web_search", p.name, search_cost_cny(p.name))
        return {"query": query, "results": results, "note": P.UNTRUSTED_NOTE}
    _record(conn, user_id, provider, conv_id, "web_fetch", "fetch", 0.0)   # every attempt counts: no retry loops
    try:
        page = F.fetch(str(args.get("url") or ""))
    except F.Blocked as e:
        return {"url": args.get("url"), "blocked": True, "reason": e.reason, "tell_user": TELL_USER}
    return {**page, "note": P.UNTRUSTED_NOTE,
            "store_only": "存库只写商品名、规格、价格、门店、这个链接和日期（save_product / record_store_prices）"}
