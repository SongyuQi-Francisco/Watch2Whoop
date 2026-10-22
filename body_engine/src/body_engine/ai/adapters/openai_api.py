"""Two OpenAI adapters (official openai SDK):

- OpenAIResponsesAdapter (openai_api): the Responses API, OpenAI's recommended new interface, supported by all the latest models (gpt-6.x).
  store=False (the conversation is not kept on OpenAI's side), include reasoning.encrypted_content, reasoning items are passed back as-is (native).
- OpenAICompatAdapter (openai_compatible): Chat Completions; DeepSeek, Qwen (DashScope compatible mode), Ollama and other
  "OpenAI-compatible" services generally implement only this interface; base_url is entered by the user (validated by providers._check_base_url).
  Thinking models (DeepSeek's default thinking mode) stream reasoning_content: it is kept in native and passed back on the same
  kind:model (DeepSeek returns 400 when a request with tools does not pass it back). DeepSeek's prompt_cache_hit / miss tokens are
  split into cache_read_tokens / input_tokens.
- DeepSeekFallbackAdapter (system_fallback): the admin's DeepSeek key for users without their own AI (ai/fallback.py):
  thinking off (cheaper, no reasoning tokens), max_tokens from fallback_params.

Both only hand this user's own key to the SDK client; the client is created per turn and discarded after use.
"""
import json

import openai

from ...i18n import L
from .base import MAX_OUTPUT_TOKENS, TIMEOUT_S, Adapter, AdapterError, TurnResult


def _errors(e: Exception, who: str) -> AdapterError:
    en = {"兼容接口": "The API"}.get(who, who)  # OpenAICompatAdapter.who
    if isinstance(e, openai.AuthenticationError):
        return AdapterError(L(f"{who} 的 API key 无效或已停用，请在「AI 设置」里更新",
                              f"{en}: the API key is invalid or disabled; update it in AI settings"), "auth")
    if isinstance(e, openai.PermissionDeniedError):
        return AdapterError(L(f"这个 {who} key 没有权限用这个模型", f"{en}: this key may not use this model"), "auth")
    if isinstance(e, openai.NotFoundError):
        return AdapterError(L(f"{who} 上找不到这个模型（或地址不对）", f"{en}: model not found (or wrong address)"), "bad_request")
    if isinstance(e, openai.RateLimitError):
        return AdapterError(L(f"{who} 限流了（或额度用完），稍后再试", f"{en}: rate limited (or out of quota); try again later"),
                            "rate_limit")
    if isinstance(e, openai.APIStatusError) and e.status_code == 402:  # DeepSeek: Insufficient Balance
        return AdapterError(L(f"{who} 账户余额不足，充值后再用", f"{en}: insufficient balance; top up first"), "balance")
    if isinstance(e, openai.BadRequestError):
        return AdapterError(L(f"请求被 {who} 拒绝：{e.message}", f"{en} rejected the request: {e.message}"), "bad_request")
    if isinstance(e, openai.APIStatusError):
        return AdapterError(L(f"{who} 服务出错（{e.status_code}），稍后再试", f"{en}: service error ({e.status_code}); try again later"),
                            "unavailable")
    if isinstance(e, openai.APIConnectionError):
        return AdapterError(L(f"连不上 {who}（网络或地址问题）", f"Can't reach {en} (network or address problem)"), "network")
    return AdapterError(L(f"{who} 调用失败：{type(e).__name__}", f"{en} call failed: {type(e).__name__}"), "unavailable")


def _data_url(b: dict) -> str:
    return f"data:{b['media_type']};base64,{b['data']}"


def _args(raw: str | None) -> dict:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {"_invalid_json": raw}


class OpenAIResponsesAdapter(Adapter):
    kind = "openai_api"

    def _input(self, history: list[dict]) -> list[dict]:
        items: list[dict] = []
        for m in history:
            if m["role"] == "assistant":
                if m.get("native_key") == self.key and m.get("native") is not None:
                    items += m["native"]
                    continue
                text = "".join(b["text"] for b in m["content"] if b["type"] == "text")
                if text:
                    items.append({"role": "assistant", "content": text})
                items += [{"type": "function_call", "call_id": b["id"], "name": b["name"],
                           "arguments": json.dumps(b["args"], ensure_ascii=False)}
                          for b in m["content"] if b["type"] == "tool_call"]
            elif m["role"] == "tool":
                items += [{"type": "function_call_output", "call_id": b["id"], "output": b["content"]}
                          for b in m["content"]]
            else:
                parts = []
                for b in m["content"]:
                    if b["type"] == "image":
                        parts.append({"type": "input_image", "image_url": _data_url(b), "detail": "auto"})
                    elif b["type"] == "text":
                        parts.append({"type": "input_text", "text": b["text"]})
                items.append({"role": "user", "content": parts})
        return items

    def stream_turn(self, system, history, tools):
        client = openai.OpenAI(api_key=self.api_key, timeout=TIMEOUT_S, max_retries=2)
        final = None
        try:
            tool_defs = [{"type": "function", "name": t.name, "description": t.description,
                          "parameters": t.input_schema, "strict": False} for t in tools] \
                + ([{"type": "web_search"}] if self.web else [])   # OpenAI's own web search (ai_providers.web_tools)
            stream = client.responses.create(
                model=self.model, instructions=system, input=self._input(history),
                **({"tools": tool_defs} if tool_defs else {}),     # none for the history fold call (ai/context.py)
                store=False, include=["reasoning.encrypted_content"], max_output_tokens=MAX_OUTPUT_TOKENS,
                stream=True)
            for event in stream:
                if event.type == "response.output_text.delta":
                    yield "text", event.delta
                elif event.type in ("response.completed", "response.incomplete"):
                    final = event.response
                elif event.type == "response.failed":
                    msg = getattr(getattr(event.response, "error", None), "message", None) or L("生成失败", "generation failed")
                    raise AdapterError(f"OpenAI：{msg}", "unavailable")
        except AdapterError:
            raise
        except openai.OpenAIError as e:
            raise _errors(e, "OpenAI")
        if final is None:
            raise AdapterError(L("OpenAI 没有返回完整结果", "OpenAI did not return a complete result"), "unavailable")

        native, text, calls = [], [], []
        for item in final.output:
            d = item.model_dump(mode="json", exclude_none=True)
            if item.type == "message":
                text += [c.text for c in item.content if getattr(c, "type", "") == "output_text"]
            elif item.type == "function_call":
                calls.append({"id": item.call_id, "name": item.name, "args": _args(item.arguments)})
            if item.type in ("message", "function_call", "reasoning", "web_search_call"):
                native.append(d)
        u = final.usage
        # input_tokens already includes the cache-hit part, counted at full price (erring on the high side)
        usage = {"input_tokens": (u.input_tokens if u else 0), "output_tokens": (u.output_tokens if u else 0)}
        stop = "max_tokens" if final.status == "incomplete" else ("tool_use" if calls else "end")
        if stop == "max_tokens":
            calls = []
        yield "result", TurnResult(text="".join(text), tool_calls=calls, native=native, usage=usage, stop=stop)


def _extra(obj, name: str):
    """A provider-specific field the SDK does not declare (DeepSeek's reasoning_content, prompt_cache_hit_tokens)."""
    v = getattr(obj, name, None)
    if v is None:
        v = (getattr(obj, "model_extra", None) or {}).get(name)
    return v


class OpenAICompatAdapter(Adapter):
    kind = "openai_compatible"
    who = "兼容接口"
    max_tokens = MAX_OUTPUT_TOKENS
    extra_body: dict | None = None

    def _messages(self, system: str, history: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in history:
            if m["role"] == "assistant":
                text = "".join(b["text"] for b in m["content"] if b["type"] == "text")
                calls = [{"id": b["id"], "type": "function",
                          "function": {"name": b["name"], "arguments": json.dumps(b["args"], ensure_ascii=False)}}
                         for b in m["content"] if b["type"] == "tool_call"]
                msg: dict = {"role": "assistant", "content": text or None}
                native = m.get("native") if m.get("native_key") == self.key else None
                if isinstance(native, dict) and native.get("reasoning_content"):
                    msg["reasoning_content"] = native["reasoning_content"]
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            elif m["role"] == "tool":
                out += [{"role": "tool", "tool_call_id": b["id"], "content": b["content"]} for b in m["content"]]
            else:
                images = [b for b in m["content"] if b["type"] == "image"]
                text = "".join(b["text"] for b in m["content"] if b["type"] == "text")
                if images:
                    out.append({"role": "user", "content": [{"type": "text", "text": text}] + [
                        {"type": "image_url", "image_url": {"url": _data_url(b)}} for b in images]})
                else:
                    out.append({"role": "user", "content": text})
        return out

    def stream_turn(self, system, history, tools):
        client = openai.OpenAI(api_key=self.api_key or "none", base_url=self.base_url, timeout=TIMEOUT_S, max_retries=2)
        text, reasoning, calls, usage, finish = [], [], {}, None, None
        try:
            stream = client.chat.completions.create(
                model=self.model, messages=self._messages(system, history),
                **({"tools": [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                "parameters": t.input_schema}} for t in tools]}
                   if tools else {}),   # an empty list is rejected; none for the history fold call (ai/context.py)
                max_tokens=self.max_tokens, stream=True, stream_options={"include_usage": True},
                **({"extra_body": self.extra_body} if self.extra_body else {}))
            for chunk in stream:
                if chunk.usage is not None:
                    usage = chunk.usage
                for choice in chunk.choices or []:
                    d = choice.delta
                    rc = _extra(d, "reasoning_content") if d is not None else None
                    if rc:
                        reasoning.append(rc)
                    if d is not None and d.content:
                        text.append(d.content)
                        yield "text", d.content
                    for tc in (d.tool_calls or []) if d is not None else []:
                        c = calls.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
                        if tc.id:
                            c["id"] = tc.id
                        if tc.function is not None:
                            c["name"] += tc.function.name or ""
                            c["arguments"] += tc.function.arguments or ""
                    if choice.finish_reason:
                        finish = choice.finish_reason
        except openai.OpenAIError as e:
            raise _errors(e, self.who)
        tool_calls = [{"id": c["id"] or f"call_{i}", "name": c["name"], "args": _args(c["arguments"])}
                      for i, c in sorted(calls.items())]
        stop = "max_tokens" if finish == "length" else ("tool_use" if tool_calls else "end")
        if stop == "max_tokens":
            tool_calls = []
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        u = {"input_tokens": prompt_tokens, "output_tokens": getattr(usage, "completion_tokens", 0) or 0}
        hit = _extra(usage, "prompt_cache_hit_tokens") if usage is not None else None
        if isinstance(hit, int):  # DeepSeek: prompt = hit + miss
            u["cache_read_tokens"], u["input_tokens"] = hit, max(prompt_tokens - hit, 0)
        native = {"reasoning_content": "".join(reasoning)} if reasoning else None
        yield "result", TurnResult(text="".join(text), tool_calls=tool_calls, native=native, usage=u, stop=stop)


class DeepSeekFallbackAdapter(OpenAICompatAdapter):
    kind = "system_fallback"
    extra_body = {"thinking": {"type": "disabled"}}

    def __init__(self, model, api_key, base_url=None):
        from .. import fallback_params as fp
        super().__init__(model, api_key, base_url or fp.BASE_URL)
        self.max_tokens = fp.MAX_OUTPUT_TOKENS

    def stream_turn(self, system, history, tools):
        """The user cannot fix the admin's key or balance: say so, and point to their own AI."""
        try:
            yield from super().stream_turn(system, history, tools)
        except AdapterError as e:
            if e.code in ("auth", "balance"):
                raise AdapterError(L("托底 AI 暂停了（管理员的 DeepSeek 账户有问题，已经提醒他）；也可以在「我 → AI 设置」接入自己的 AI",
                                     "The backup AI is paused (a problem with the admin's DeepSeek account; they have been told). "
                                     "You can also connect your own AI in Me → AI settings"), "fallback_unavailable")
            raise
