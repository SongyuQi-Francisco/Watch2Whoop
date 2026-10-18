"""Provider kinds, default models and price table (docs/ai-assistant-plan.md section 9).

- Subscription type (category=mcp): Claude Pro / Max, ChatGPT (Codex) and similar subscriptions may, under each vendor's terms, only be used in the official client,
  so the backend neither calls them nor stores keys; users connect Body Engine's remote MCP from their own client (Claude Code, Codex CLI, Gemini CLI...).
- API type (category=api): the user's own API key, the backend calls on this user's behalf, billed per token and limited by a monthly budget.
- Prices are for budget estimation (USD per million tokens); the vendors' official price pages are authoritative: copied on 2026-10-17 from
  platform.claude.com, developers.openai.com/api/docs/pricing, ai.google.dev/gemini-api/docs/pricing.
  Announced price rises (Gemini 2026-12 / 2027-01) use the raised price, erring on the high side; models not in the table use FALLBACK_PRICE (the most expensive tier),
  and users can enter a custom unit price on the provider (e.g. 0 for local Ollama).
"""

MCP_KINDS = ("claude_plan_mcp", "codex_plan_mcp", "gemini_plan_mcp", "generic_mcp_client")
API_KINDS = ("anthropic_api", "openai_api", "gemini_api", "gemini_cli", "openai_compatible")

KINDS: dict[str, dict] = {
    "claude_plan_mcp": {
        "label": "Claude 订阅（Pro / Max）", "category": "mcp", "client": "claude-code",
        "note": "订阅只能在 Claude 官方客户端里用：在 Claude Code / Claude 桌面版里连 Body Engine 的 MCP。"},
    "codex_plan_mcp": {
        "label": "ChatGPT 订阅（Codex）", "category": "mcp", "client": "codex",
        "note": "在你自己登录的 Codex CLI 里连 Body Engine 的 MCP；后端不碰你的 ChatGPT 账号。"},
    "gemini_plan_mcp": {
        "label": "Google AI 订阅（Gemini）", "category": "mcp", "client": "gemini-cli",
        "note": "在你自己登录 Google 账号的 Gemini CLI / Antigravity 里连 Body Engine 的 MCP；后端不碰你的 Google 账号。"},
    "generic_mcp_client": {
        "label": "其他 MCP 客户端", "category": "mcp", "client": "other",
        "note": "任何支持远程 MCP（Streamable HTTP + Bearer 头）的客户端都可以连。"},
    "anthropic_api": {
        "label": "Anthropic API（Claude）", "category": "api", "default_model": "claude-opus-5-5",
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"],
        "vision": True, "key_hint": "sk-ant-…（console.anthropic.com 生成）"},
    "openai_api": {
        "label": "OpenAI API", "category": "api", "default_model": "gpt-6.1-sol",
        "models": ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.4-mini"],
        "vision": True, "key_hint": "sk-…（platform.openai.com 生成）"},
    "gemini_api": {
        "label": "Google Gemini API", "category": "api", "default_model": "gemini-4-argon",
        "models": ["gemini-4-argon", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite",
                   "gemini-2.5-pro", "gemini-2.5-flash"],
        "vision": True, "key_hint": "AIza…（AI Studio 生成；免费额度可勾选下方「免费额度」）"},
    "gemini_cli": {
        "label": "Google AI 订阅（服务端 Gemini CLI）", "category": "api", "default_model": "gemini-4-argon",
        "models": ["gemini-4-argon", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite",
                   "gemini-2.5-pro", "gemini-2.5-flash"],
        "vision": True, "needs_key": False, "owner_only": True,
        "note": "仅限部署者本人：调用服务器上已登录 Google AI Plan 的 gemini CLI，在今天页内对话，不走 API key 计费。"},
    "openai_compatible": {
        "label": "OpenAI 兼容接口（DeepSeek、通义千问、Ollama…）", "category": "api", "default_model": None,
        "models": [], "vision": None, "needs_base_url": True,
        "key_hint": "对应服务的 API key（本机 Ollama 随便填）",
        "base_url_examples": ["https://api.deepseek.com/v1", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                              "http://host.docker.internal:11434/v1"]},
}

# USD per million tokens: (input, output)
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0), "claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0),
    "gpt-6-astra": (10.0, 50.0), "gpt-6.1-sol": (2.0, 10.0), "gpt-6-sol": (2.0, 10.0), "gpt-6-luna": (0.10, 0.50),
    "gpt-5.6-sol": (4.0, 20.0), "gpt-5.6-terra": (2.0, 12.0), "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.5": (5.0, 30.0), "gpt-5.4": (2.5, 15.0), "gpt-5.4-mini": (0.75, 4.50),
    "gemini-4-argon": (4.0, 20.0), "gemini-3.8-flash": (1.5, 7.5), "gemini-3.6-flash": (1.5, 7.5),
    "gemini-3.5-flash-lite": (0.30, 2.50), "gemini-2.5-pro": (1.25, 10.0), "gemini-2.5-flash": (0.30, 2.50),
}
FALLBACK_PRICE = (10.0, 50.0)
# Claude prompt caching: read 0.1x, write (5 minutes) 1.25x of the input price. OpenAI / Gemini cache discounts are not counted (full price, erring on the high side).
CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25


def is_mcp(kind: str) -> bool:
    return kind in MCP_KINDS


def price(model: str, custom_in=None, custom_out=None) -> tuple[float, float]:
    base = PRICES.get(model, FALLBACK_PRICE)
    return (float(custom_in) if custom_in is not None else base[0],
            float(custom_out) if custom_out is not None else base[1])


def cost_usd(model: str, usage: dict, custom_in=None, custom_out=None) -> float:
    """usage: input_tokens (excluding the cached part), output_tokens, cache_read_tokens, cache_write_tokens."""
    p_in, p_out = price(model, custom_in, custom_out)
    return (usage.get("input_tokens", 0) * p_in
            + usage.get("cache_read_tokens", 0) * p_in * CACHE_READ_FACTOR
            + usage.get("cache_write_tokens", 0) * p_in * CACHE_WRITE_FACTOR
            + usage.get("output_tokens", 0) * p_out) / 1_000_000


def is_owner(user_id: int) -> bool:
    import os
    return str(user_id) == os.environ.get("BODY_ASSISTANT_USER_ID", "1")


def public_catalog(user_id: int | None = None) -> list[dict]:
    out = []
    for kind, k in KINDS.items():
        if user_id is not None and k.get("owner_only") and not is_owner(user_id):
            continue
        out.append({"kind": kind, **{f: v for f, v in k.items()},
                    "prices": {m: PRICES.get(m) for m in k.get("models", [])}})
    return out
