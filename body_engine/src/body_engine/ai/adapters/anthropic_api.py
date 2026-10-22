"""Anthropic API (Claude) adapter: official anthropic SDK, Messages API + tool calls, streaming.

- Default model claude-opus-5-5 (the user can change it). Thinking uses the model default adaptive (cannot be turned off on Opus 5.5), effort set to medium.
- Prompt caching: top-level cache_control caches automatically (tools -> system -> messages prefix; system does not change within a conversation).
- Refusal fallback: Opus 5.5 / Sonnet 5.5 / Fable 5.1 / Opus 5 enable server-side fallbacks="default" (beta server-side-fallback-2026-07-01),
  when rejected by the safety classifier the API retries with another model; if it still ends as a refusal the user is told.
- Tools use strict=True (api_definition in tools.py); eager_input_streaming is off: tool arguments are short, strict validation matters more than streaming them while generating.
- The assistant's raw content (including thinking blocks) is stored as native and passed back as-is when the same model continues the conversation (preserved thinking requires history to be append-only and unchanged).
- web (ai_providers.web_tools, off by default): Anthropic's own server tools web_search + web_fetch (the _20260209 variants with
  dynamic filtering on the models that support them, the basic web_search_20250305 + web_fetch_20250910 otherwise; checked
  against platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool and web-fetch-tool on 2026-10-22), billed on the
  user's key ($10 / 1,000 searches + tokens; fetch has no extra charge, page text capped by WEB_FETCH_MAX_TOKENS). Server tool blocks
  (server_tool_use, web_search_tool_result, web_fetch_tool_result) stay in native as returned. A turn that stops with pause_turn
  (the server-side loop hit its limit) is continued by re-sending the partial assistant content, up to MAX_CONTINUE times.
"""
import anthropic

from ...i18n import L
from .base import MAX_OUTPUT_TOKENS, TIMEOUT_S, Adapter, AdapterError, TurnResult

FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-opus-5"}
WEB_NEW_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-5-5",
                  "claude-sonnet-5", "claude-sonnet-4-6"}   # web_search_20260209 / web_fetch_20260209
WEB_MAX_USES = 8      # searches + page reads per request (a dozen items from one list)
WEB_FETCH_MAX_TOKENS = 20000   # max_content_tokens per fetched page: a product page fits, a huge one doesn't burn the user's key
MAX_CONTINUE = 3      # pause_turn continuations per turn
EFFORT_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1", "claude-opus-5",
                 "claude-sonnet-5"}


# When passing back to the API keep only the fields the input format accepts for each block type (read-only fields that appear in output may be rejected if sent back); thinking signature and content are not changed by a single character
_KEEP = {"text": ("type", "text", "citations"), "tool_use": ("type", "id", "name", "input"),
         "thinking": ("type", "thinking", "signature"), "redacted_thinking": ("type", "data")}


def _replayable(block: dict) -> dict:
    keep = _KEEP.get(block.get("type"))
    return {k: block[k] for k in keep if k in block} if keep else block


class AnthropicAdapter(Adapter):
    kind = "anthropic_api"

    def _messages(self, history: list[dict]) -> list[dict]:
        out = []
        for m in history:
            if m["role"] == "assistant":
                if m.get("native_key") == self.key and m.get("native") is not None:
                    out.append({"role": "assistant", "content": m["native"]})
                    continue
                blocks = []
                for b in m["content"]:
                    if b["type"] == "text" and b["text"]:
                        blocks.append({"type": "text", "text": b["text"]})
                    elif b["type"] == "tool_call":
                        blocks.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["args"]})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "（空）"}]})
            elif m["role"] == "tool":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b["id"], "content": b["content"], "is_error": b["is_error"]}
                    for b in m["content"]]})
            else:
                blocks = []
                for b in m["content"]:
                    if b["type"] == "image":
                        blocks.append({"type": "image", "source": {"type": "base64", "media_type": b["media_type"],
                                                                   "data": b["data"]}})
                    elif b["type"] == "text":
                        blocks.append({"type": "text", "text": b["text"]})
                out.append({"role": "user", "content": blocks})
        return out

    def _web_tools(self) -> list[dict]:
        if not self.web:
            return []
        if self.model in WEB_NEW_MODELS:
            return [{"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_MAX_USES},
                    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": WEB_MAX_USES,
                     "max_content_tokens": WEB_FETCH_MAX_TOKENS}]
        return [{"type": "web_search_20250305", "name": "web_search", "max_uses": WEB_MAX_USES},
                {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": WEB_MAX_USES,
                 "max_content_tokens": WEB_FETCH_MAX_TOKENS}]

    def stream_turn(self, system, history, tools):
        client = anthropic.Anthropic(api_key=self.api_key, timeout=TIMEOUT_S, max_retries=2)
        messages = self._messages(history)
        kwargs = dict(model=self.model, max_tokens=MAX_OUTPUT_TOKENS, system=system, cache_control={"type": "ephemeral"})
        tool_defs = [t.api_definition() for t in tools] + self._web_tools()
        if tool_defs:   # none for the history fold call (ai/context.py)
            kwargs["tools"] = tool_defs
        if self.model in EFFORT_MODELS:
            kwargs["output_config"] = {"effort": "medium"}
        if self.model in FALLBACK_MODELS:
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        content: list[dict] = []   # this turn's blocks, across pause_turn continuations
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
        try:
            for _ in range(MAX_CONTINUE + 1):
                kwargs["messages"] = messages + ([{"role": "assistant", "content": content}] if content else [])
                with client.beta.messages.stream(**kwargs) as stream:
                    for event in stream:
                        if event.type == "text":
                            yield "text", event.text
                    final = stream.get_final_message()
                content += [_replayable(b.model_dump(mode="json", exclude_none=True)) for b in final.content]
                u = final.usage
                usage["input_tokens"] += u.input_tokens or 0
                usage["output_tokens"] += u.output_tokens or 0
                usage["cache_read_tokens"] += u.cache_read_input_tokens or 0
                usage["cache_write_tokens"] += u.cache_creation_input_tokens or 0
                if final.stop_reason != "pause_turn":
                    break
        except anthropic.AuthenticationError:
            raise AdapterError(L("Anthropic API key 无效或已停用，请在「AI 设置」里更新",
                                 "The Anthropic API key is invalid or disabled; update it in AI settings"), "auth")
        except anthropic.PermissionDeniedError:
            raise AdapterError(L("这个 API key 没有权限用这个模型", "This API key may not use this model"), "auth")
        except anthropic.NotFoundError:
            raise AdapterError(L(f"模型不存在：{self.model}", f"No such model: {self.model}"), "bad_request")
        except anthropic.RateLimitError:
            raise AdapterError(L("Anthropic 限流了，稍后再试", "Anthropic is rate limiting; try again later"), "rate_limit")
        except anthropic.BadRequestError as e:
            raise AdapterError(L(f"请求被 Anthropic 拒绝：{e.message}", f"Anthropic rejected the request: {e.message}"), "bad_request")
        except anthropic.APIStatusError as e:
            raise AdapterError(L(f"Anthropic 服务出错（{e.status_code}），稍后再试", f"Anthropic service error ({e.status_code}); try again later"),
                               "unavailable")
        except anthropic.APIConnectionError:
            raise AdapterError(L("连不上 Anthropic（网络问题）", "Can't reach Anthropic (network problem)"), "network")

        native = content
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        calls = [{"id": b["id"], "name": b["name"], "args": b["input"]} for b in content if b.get("type") == "tool_use"]
        stop = {"tool_use": "tool_use", "max_tokens": "max_tokens", "refusal": "refusal"}.get(final.stop_reason, "end")
        if stop == "refusal":
            calls = []  # A refusal may cut a tool call in half
        yield "result", TurnResult(text=text, tool_calls=calls, native=native, usage=usage, stop=stop)
