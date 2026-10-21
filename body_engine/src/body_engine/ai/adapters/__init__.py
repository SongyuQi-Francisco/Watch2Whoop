"""Provider adapter registry: kind -> adapter class. SDKs are imported only when used (a vendor SDK not being installed does not affect the others).
Tests replace it with fake.FakeAdapter (monkeypatch ADAPTERS), calling no paid API."""
from ...i18n import L
from .base import Adapter, AdapterError, TurnResult  # noqa: F401


def _anthropic():
    from .anthropic_api import AnthropicAdapter
    return AnthropicAdapter


def _openai():
    from .openai_api import OpenAIResponsesAdapter
    return OpenAIResponsesAdapter


def _compat():
    from .openai_api import OpenAICompatAdapter
    return OpenAICompatAdapter


def _fallback():
    from .openai_api import DeepSeekFallbackAdapter
    return DeepSeekFallbackAdapter


def _gemini():
    from .gemini_api import GeminiAdapter
    return GeminiAdapter


def _gemini_cli():
    from .gemini_cli import GeminiCliAdapter
    return GeminiCliAdapter


ADAPTERS = {"anthropic_api": _anthropic, "openai_api": _openai, "openai_compatible": _compat, "gemini_api": _gemini,
            "gemini_cli": _gemini_cli, "system_fallback": _fallback}


def make(kind: str, model: str, api_key: str | None, base_url: str | None = None) -> Adapter:
    if kind not in ADAPTERS:
        raise AdapterError(L(f"{kind} 不是 API 类提供商，不能在今天页里对话", f"{kind} is not an API provider; it can't chat on the Today page"),
                           "bad_request")
    return ADAPTERS[kind]()(model, api_key, base_url)
