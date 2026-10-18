"""Google Gemini API adapter: official google-genai SDK, generate_content_stream + function calling.

- The SDK's automatic function calling is turned off (we run the tool loop ourselves: write tools must be confirmed by the user first).
- Tool schemas use parameters_json_schema (full JSON Schema, supports forms like ["string","null"]).
- The model's reply Content (including thought_signature) is stored as native and passed back when the same model continues the conversation (function calls of newer models require the signature).
"""
import base64

from google import genai
from google.genai import errors, types

from .base import MAX_OUTPUT_TOKENS, TIMEOUT_S, Adapter, AdapterError, TurnResult


class GeminiAdapter(Adapter):
    kind = "gemini_api"

    def _contents(self, history: list[dict]) -> list[types.Content]:
        out: list[types.Content] = []
        for m in history:
            if m["role"] == "assistant":
                if m.get("native_key") == self.key and m.get("native") is not None:
                    out.append(types.Content.model_validate(m["native"]))
                    continue
                parts = [types.Part(text=b["text"]) for b in m["content"] if b["type"] == "text" and b["text"]]
                parts += [types.Part(function_call=types.FunctionCall(name=b["name"], args=b["args"]))
                          for b in m["content"] if b["type"] == "tool_call"]
                out.append(types.Content(role="model", parts=parts or [types.Part(text="（空）")]))
            elif m["role"] == "tool":
                out.append(types.Content(role="user", parts=[
                    types.Part(function_response=types.FunctionResponse(
                        name=b["name"], response={"error": b["content"]} if b["is_error"] else {"result": b["content"]}))
                    for b in m["content"]]))
            else:
                parts = []
                for b in m["content"]:
                    if b["type"] == "image":
                        parts.append(types.Part.from_bytes(data=base64.b64decode(b["data"]), mime_type=b["media_type"]))
                    elif b["type"] == "text":
                        parts.append(types.Part(text=b["text"]))
                out.append(types.Content(role="user", parts=parts))
        return out

    def stream_turn(self, system, history, tools):
        client = genai.Client(api_key=self.api_key, http_options=types.HttpOptions(timeout=TIMEOUT_S * 1000))
        config = types.GenerateContentConfig(
            system_instruction=system, max_output_tokens=MAX_OUTPUT_TOKENS,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.input_schema)
                for t in tools])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        parts: list[types.Part] = []
        usage, finish = None, None
        try:
            for chunk in client.models.generate_content_stream(model=self.model, contents=self._contents(history),
                                                               config=config):
                if chunk.usage_metadata is not None:
                    usage = chunk.usage_metadata
                for cand in chunk.candidates or []:
                    if cand.finish_reason is not None:
                        finish = cand.finish_reason
                    for p in (cand.content.parts if cand.content and cand.content.parts else []):
                        parts.append(p)
                        if p.text and not p.thought:
                            yield "text", p.text
        except errors.APIError as e:
            code = getattr(e, "code", None)
            if code in (401, 403):
                raise AdapterError("Gemini API key 无效或没有权限，请在「AI 设置」里更新", "auth")
            if code == 404:
                raise AdapterError(f"Gemini 上找不到模型：{self.model}", "bad_request")
            if code == 429:
                raise AdapterError("Gemini 限流了（或额度用完），稍后再试", "rate_limit")
            if code == 400:
                raise AdapterError(f"请求被 Gemini 拒绝：{getattr(e, 'message', '')}", "bad_request")
            raise AdapterError(f"Gemini 服务出错（{code}），稍后再试", "unavailable")
        except Exception as e:  # network etc.
            raise AdapterError(f"连不上 Gemini：{type(e).__name__}", "network")

        calls = []
        for i, p in enumerate(parts):
            if p.function_call is not None:
                fc = p.function_call
                calls.append({"id": fc.id or f"gemini_{i}", "name": fc.name, "args": dict(fc.args or {})})
        text = "".join(p.text for p in parts if p.text and not p.thought)
        native = types.Content(role="model", parts=parts).model_dump(mode="json", exclude_none=True) if parts else None
        fr = str(getattr(finish, "value", finish) or "")
        stop = ("max_tokens" if fr == "MAX_TOKENS" else
                "refusal" if fr in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION") else
                "tool_use" if calls else "end")
        if stop in ("max_tokens", "refusal"):
            calls = []
        u = self._extract_usage(usage)
        yield "result", TurnResult(text=text, tool_calls=calls, native=native, usage=u, stop=stop)

    @staticmethod
    def _extract_usage(usage) -> dict:
        if not usage:
            return {}
        prompt_tok = getattr(usage, "prompt_token_count", 0) or 0
        cached_tok = getattr(usage, "cached_content_token_count", 0) or 0
        thoughts_tok = getattr(usage, "thoughts_token_count", 0) or 0
        candidates_tok = getattr(usage, "candidates_token_count", 0) or 0
        return {
            "input_tokens": max(prompt_tok - cached_tok, 0),
            "cache_read_tokens": cached_tok,
            "output_tokens": candidates_tok + thoughts_tok,
        }

