"""Provider kinds, default models and price table (docs/ai-assistant-plan.md section 9).

- Subscription type (category=mcp): Claude Pro / Max, ChatGPT (Codex) and similar subscriptions may, under each vendor's terms, only be used in the official client,
  so the backend neither calls them nor stores keys; users connect Body Engine's remote MCP from their own client (Claude Code, Codex CLI, Gemini CLI...).
- API type (category=api): the user's own API key, the backend calls on this user's behalf, billed per token and limited by a monthly budget.
- Prices are for budget estimation (USD per million tokens); the vendors' official price pages are authoritative: copied on 2026-10-17 from
  platform.claude.com, developers.openai.com/api/docs/pricing, ai.google.dev/gemini-api/docs/pricing.
  Announced price rises (Gemini 2026-12 / 2027-01) use the raised price, erring on the high side; models not in the table use FALLBACK_PRICE (the most expensive tier),
  and users can enter a custom unit price on the provider (e.g. 0 for local Ollama).
"""
import os

from ..i18n import is_en

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
        # Google AI Pro / Ultra (formerly Google One AI Premium). Since 2026-06-18 Gemini CLI no longer serves these plans;
        # Antigravity (desktop / IDE / CLI `agy`) is the subscription client. The Gemini app's custom MCP apps need OAuth
        # and are US-only (docs/ai-assistant-plan.md 9.15), so they are not offered here.
        "label": "Google AI Pro / Ultra 订阅（Antigravity）", "category": "mcp", "client": "antigravity",
        "note": "在你自己登录的 Antigravity（桌面版、IDE 或 agy 命令行）里连 Body Engine 的 MCP；后端不碰你的 Google 账号。"
                "Gemini CLI 从 2026-06-18 起不再支持订阅登录。"},
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
        "vision": True, "key_hint": "AI Studio 生成的 API key"},
    "gemini_cli": {
        "label": "Google AI 订阅（服务端 Gemini CLI）", "category": "api", "default_model": "gemini-4-argon",
        "models": ["gemini-4-argon", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite",
                   "gemini-2.5-pro", "gemini-2.5-flash"],
        # Server-side subprocess: off unless the admin sets BODY_GEMINI_CLI_ENABLED=1, and only for BODY_ASSISTANT_USER_ID
        "vision": True, "needs_key": False, "owner_only": True,
        "note": "仅部署者：用服务器上登录的 Gemini CLI（Google 2026-06-18 起不再给 AI Pro / Ultra 订阅提供 Gemini CLI，"
                "只剩企业版 Code Assist 账号能登录）"},
    "openai_compatible": {
        "label": "OpenAI 兼容接口（DeepSeek、通义千问、Ollama…）", "category": "api", "default_model": None,
        "models": [], "vision": None, "needs_base_url": True,
        "key_hint": "对应服务的 API key（本机 Ollama 随便填）",
        "base_url_examples": ["https://api.deepseek.com/v1", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                              "http://host.docker.internal:11434/v1"]},
}

# English for the texts above (AI settings on the Today page, X-Body-Lang: en); applied at read time by public_catalog / label
KINDS_EN: dict[str, dict] = {
    "claude_plan_mcp": {
        "label": "Claude subscription (Pro / Max)",
        "note": "A subscription only works in Claude's own apps: connect Body Engine's MCP in Claude Code or Claude Desktop."},
    "codex_plan_mcp": {
        "label": "ChatGPT subscription (Codex)",
        "note": "Connect Body Engine's MCP in the Codex CLI you signed in to; the server never touches your ChatGPT account."},
    "gemini_plan_mcp": {
        "label": "Google AI Pro / Ultra subscription (Antigravity)",
        "note": "Connect Body Engine's MCP in the Antigravity you signed in to (desktop app, IDE or the agy CLI); the server never "
                "touches your Google account. Gemini CLI no longer accepts subscription sign-in since 2026-06-18."},
    "generic_mcp_client": {
        "label": "Other MCP client",
        "note": "Any client that supports remote MCP (Streamable HTTP + Bearer header) can connect."},
    "anthropic_api": {"label": "Anthropic API (Claude)", "key_hint": "sk-ant-… (create one at console.anthropic.com)"},
    "openai_api": {"label": "OpenAI API", "key_hint": "sk-… (create one at platform.openai.com)"},
    "gemini_api": {"label": "Google Gemini API", "key_hint": "API key from AI Studio"},
    "gemini_cli": {"label": "Google AI subscription (Gemini CLI on the server)",
                   "note": "Deployer only: uses the Gemini CLI signed in on the server (since 2026-06-18 Google no longer serves "
                           "Gemini CLI to AI Pro / Ultra plans; only enterprise Code Assist accounts can sign in)"},
    "openai_compatible": {"label": "OpenAI-compatible API (DeepSeek, Qwen, Ollama…)",
                          "key_hint": "That service's API key (anything for a local Ollama)"},
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
# Claude prompt caching: read 0.1x, write (5 minutes) 1.25x of the input price. OpenAI cache discounts are not counted
# (full price, erring on the high side); Gemini reports cached tokens separately and is charged 0.25x
# (Gemini 2.5 context-cache price; newer models are cheaper, so this errs on the high side).
CACHE_READ_FACTOR = 0.1
GEMINI_CACHE_READ_FACTOR = 0.25
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
    read_factor = GEMINI_CACHE_READ_FACTOR if model.startswith("gemini") else CACHE_READ_FACTOR
    return (usage.get("input_tokens", 0) * p_in
            + usage.get("cache_read_tokens", 0) * p_in * read_factor
            + usage.get("cache_write_tokens", 0) * p_in * CACHE_WRITE_FACTOR
            + usage.get("output_tokens", 0) * p_out) / 1_000_000


def is_owner(user_id: int) -> bool:
    return str(user_id) == os.environ.get("BODY_ASSISTANT_USER_ID", "1")


def gemini_cli_enabled() -> bool:
    """The server-side Gemini CLI runs a subprocess as the server: the admin has to opt in (default off)."""
    return os.environ.get("BODY_GEMINI_CLI_ENABLED", "").strip().lower() in ("1", "true", "yes")


def kind_allowed(kind: str, user_id: int) -> bool:
    """owner_only kinds (gemini_cli) exist only when enabled, and only for the deployer."""
    if not KINDS.get(kind, {}).get("owner_only"):
        return True
    return gemini_cli_enabled() and is_owner(user_id)


def texts(kind: str) -> dict:
    """label / note / key_hint of a kind in the request's language."""
    k = KINDS.get(kind, {})
    out = {f: k[f] for f in ("label", "note", "key_hint") if f in k}
    return {**out, **KINDS_EN.get(kind, {})} if is_en() else out


def label(kind: str) -> str:
    return texts(kind).get("label") or kind


def display_name(row: dict) -> str:
    """A provider's name for the page: the stored name, except the default (the Chinese catalog label) follows the language."""
    name = row.get("display_name") or ""
    return label(row["kind"]) if name == KINDS.get(row.get("kind"), {}).get("label") else name


def public_catalog(user_id: int | None = None) -> list[dict]:
    out = []
    for kind, k in KINDS.items():
        if k.get("owner_only") and (user_id is None or not kind_allowed(kind, user_id)):
            continue
        out.append({"kind": kind, **{f: v for f, v in k.items()}, **texts(kind),
                    "prices": {m: PRICES.get(m) for m in k.get("models", [])}})
    return out
