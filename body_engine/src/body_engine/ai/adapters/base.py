"""Common parts of the provider adapters.

The chat loop (chat.py) only knows the "neutral format" here, and each adapter converts to / from its own vendor's SDK:
- Message: {"role": "user" | "assistant" | "tool", "content": [blocks...], "native": ..., "native_key": "kind:model"}
- Block: {"type": "text", "text"} / {"type": "image", "media_type", "data"(base64)}
      / {"type": "tool_call", "id", "name", "args"} / {"type": "tool_result", "id", "name", "content", "is_error"}
- native: the vendor SDK's assistant reply as-is (Claude thinking blocks, OpenAI reasoning items, Gemini thought_signature).
  Passed back as-is when the same kind:model continues the conversation (these models require it); when the provider / model changes, the neutral format is converted instead.

stream_turn is a generator: it yields ("text", fragment) while generating, and finally yields ("result", TurnResult).
The API key lives only in the adapter object and is discarded after use; error messages never contain the key.
"""
from dataclasses import dataclass, field
from typing import Iterator

from ...toolspec import relaxed

MAX_OUTPUT_TOKENS = 16000
TIMEOUT_S = 180


class AdapterError(Exception):
    """Calling the provider failed. code: auth / rate_limit / bad_request / unavailable / network / refusal"""

    def __init__(self, message: str, code: str = "unavailable"):
        super().__init__(message)
        self.code = code


@dataclass
class TurnResult:
    text: str
    tool_calls: list[dict]                 # [{"id", "name", "args"}]
    native: object                         # JSON serializable
    usage: dict = field(default_factory=dict)  # input_tokens / output_tokens / cache_read_tokens / cache_write_tokens
    stop: str = "end"                      # end / tool_use / max_tokens / refusal


class Adapter:
    kind = ""
    web = False   # the vendor's official web search / fetch tools (set by adapters.make; only WEB_TOOL_KINDS act on it)
    shorten_history = False   # older turns' tool results may be sent shortened (context.shorten_old_results). Off for every
                              # vendor that replays reasoning bound to the exact earlier turns (Claude's preserved thinking,
                              # OpenAI reasoning items, Gemini thought signatures, DeepSeek thinking mode): only the fallback,
                              # whose thinking is off (DeepSeekFallbackAdapter), turns it on.
    no_tool_calls = False   # the tools are still sent (history and prompt cache stay valid) but the model may not call any:
                            # the tools-off answer at the round / cost limit (chat._loop sets it on a copy; issue #184)

    def __init__(self, model: str, api_key: str | None, base_url: str | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.model}"

    def parameters(self, tool) -> dict:
        """The JSON schema this vendor gets for a tool's arguments: the relaxed form MCP clients get too (nullable and
        defaulted properties optional, toolspec.relaxed), about 11% shorter than the strict one and the model writes no
        `"x": null` for unused fields (issue #78). The executor puts the omitted values back (fill_defaults) before any
        handler runs. Only the Anthropic adapter sends the strict form (strict tools need it)."""
        return relaxed(tool.input_schema)

    def tool_defs(self, tools) -> list[dict]:
        """The function-tool definitions exactly as this vendor's request carries them (the vendor's own server tools,
        such as web search, are added by stream_turn). Also what tests measure (tests/test_token_economy.py)."""
        return [tool.api_definition() for tool in tools]

    def stream_turn(self, system: str, history: list[dict], tools: list[dict]) -> Iterator[tuple[str, object]]:
        raise NotImplementedError


def text_of(blocks: list[dict]) -> str:
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
