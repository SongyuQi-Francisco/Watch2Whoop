"""Server-side Google AI Plan (Gemini CLI) adapter for the deployer (owner_only).

Runs the local `gemini` CLI in headless streaming mode (`gemini -p ... -o stream-json`) inside an isolated temp workspace:
- Strips GEMINI_API_KEY / GOOGLE_API_KEY so the CLI always uses the server's Google account OAuth login (Google AI Pro / Ultra plan), never billing an API key.
- Disables built-in shell, file-write and web tools; Body Engine's own chat loop (chat.py) runs the tools and enforces write confirmations (ALWAYS_CONFIRM).
- Supports multimodal images by writing them into the temp workspace and referencing `@photo_N.<ext>` in the prompt.
"""
import base64
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from ...toolspec import relaxed
from .base import TIMEOUT_S, Adapter, AdapterError, TurnResult

BLOCKED_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI")
EXCLUDED_TOOLS = ["run_shell_command", "write_file", "replace", "read_many_files", "list_directory",
                  "glob", "grep_search", "google_web_search", "web_fetch", "save_memory"]
EXT_BY_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
OPEN_TAG = "<tool_call>"


def find_gemini() -> str:
    b = os.environ.get("BODY_GEMINI_BIN")
    if b:
        if Path(b).exists():
            return b
        raise AdapterError(f"找不到指定的 BODY_GEMINI_BIN：{b}", "unavailable")
    dirs = [d for d in os.environ.get("PATH", "").split(":") if d] + [
        str(Path.home() / ".npm-global/bin"),
        str(Path.home() / ".local/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    for d in dict.fromkeys(dirs):
        p = Path(d) / "gemini"
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    raise AdapterError("服务器上找不到 gemini CLI（安装：npm i -g @google/gemini-cli，并用 Google 账号登录）", "unavailable")


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in BLOCKED_ENV}
    env.setdefault("LANG", "en_US.UTF-8")
    env["NO_COLOR"] = "1"
    return env


def _hold_back_tag_prefix(text: str) -> tuple[str, str]:
    """Split `text` into (safe_to_emit, held_back) so `<tool_call>...` is never streamed as user-visible chat text."""
    idx = text.find(OPEN_TAG)
    if idx >= 0:
        return text[:idx], text[idx:]
    for k in range(len(OPEN_TAG) - 1, 0, -1):
        if text.endswith(OPEN_TAG[:k]):
            return text[:-k], text[-k:]
    return text, ""


def _parse_tool_calls(raw: str) -> tuple[str, list[dict]]:
    calls: list[dict] = []
    for i, m in enumerate(TOOL_CALL_RE.finditer(raw)):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("name"), str):
            args = obj.get("args") if isinstance(obj.get("args"), dict) else (
                obj.get("arguments") if isinstance(obj.get("arguments"), dict) else {})
            calls.append({"id": obj.get("id") or f"gcli_{i}", "name": obj["name"], "args": args})
    clean = TOOL_CALL_RE.sub("", raw).strip()
    return clean, calls


class GeminiCliAdapter(Adapter):
    kind = "gemini_cli"

    def _build_prompt(self, system: str, history: list[dict], tools: list, workdir: Path) -> tuple[str, bool]:
        has_images = False
        img_idx = 0
        tool_specs = [{"name": t.name, "description": t.description, "parameters": relaxed(t.input_schema)} for t in tools]
        lines = [
            "# 系统指令",
            system,
            "",
            "# 工具调用协议",
            "你可以使用以下工具读写用户的身体管理数据库。当需要调用一个或多个工具时，请只输出如下格式的块（可以输出多个），等待工具返回结果后再继续回答：",
            '<tool_call>{"name": "工具名", "args": {"参数名": "值"}}</tool_call>',
            "如果不需要调用工具，直接用中文回答用户，不要输出 <tool_call> 标签。",
            "可用工具列表（JSON）：",
            json.dumps(tool_specs, ensure_ascii=False, separators=(",", ":")),
            "",
            "# 对话记录",
        ]
        for m in history:
            role = m["role"]
            if role == "user":
                parts = []
                for b in m["content"]:
                    if b["type"] == "image":
                        img_idx += 1
                        has_images = True
                        ext = EXT_BY_MIME.get(b["media_type"], "jpg")
                        fname = f"photo_{img_idx}.{ext}"
                        (workdir / fname).write_bytes(base64.b64decode(b["data"]))
                        parts.append(f"@{fname}")
                    elif b["type"] == "text" and b.get("text"):
                        parts.append(b["text"])
                lines.append("用户：" + "\n".join(parts))
            elif role == "assistant":
                parts = []
                for b in m["content"]:
                    if b["type"] == "text" and b.get("text"):
                        parts.append(b["text"])
                    elif b["type"] == "tool_call":
                        parts.append(f'<tool_call>{json.dumps({"name": b["name"], "args": b["args"]}, ensure_ascii=False)}</tool_call>')
                lines.append("管家：" + "\n".join(parts))
            elif role == "tool":
                for b in m["content"]:
                    kind = "错误" if b.get("is_error") else "结果"
                    lines.append(f'工具返回（{b["name"]} · {kind}）：{b["content"]}')
        lines.append("\n请继续回复（如需调工具请输出 <tool_call>{...}</tool_call>）：")
        return "\n\n".join(lines), has_images

    def stream_turn(self, system, history, tools):
        binary = find_gemini()
        with tempfile.TemporaryDirectory(prefix="body-gemini-") as tmp:
            workdir = Path(tmp)
            prompt, has_images = self._build_prompt(system, history, tools, workdir)
            excluded = EXCLUDED_TOOLS if has_images else EXCLUDED_TOOLS + ["read_file"]
            gdir = workdir / ".gemini"
            gdir.mkdir(parents=True, exist_ok=True)
            (gdir / "settings.json").write_text(json.dumps({
                "mcpServers": {},
                "tools": {"exclude": excluded},
            }, ensure_ascii=False))
            cmd = [binary, "-p", prompt, "-o", "stream-json", "--extensions", ""]
            if self.model:
                cmd += ["-m", self.model]
            try:
                proc = subprocess.Popen(
                    cmd, cwd=workdir, env=clean_env(),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1,
                )
            except OSError as e:
                raise AdapterError(f"无法启动 gemini CLI：{e}", "unavailable")

            full_text = ""
            pending_emit = ""
            in_tool_tag = False
            stats: dict = {}
            err_event: str | None = None
            try:
                assert proc.stdout is not None
                for raw_line in proc.stdout:
                    line = raw_line.strip()
                    if not line or not line.startswith("{"):
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    etype = ev.get("type")
                    if etype == "message" and ev.get("role") == "assistant":
                        chunk = ev.get("content") or ""
                        if not chunk:
                            continue
                        full_text += chunk
                        if not in_tool_tag:
                            pending_emit += chunk
                            safe, held = _hold_back_tag_prefix(pending_emit)
                            if safe:
                                yield "text", safe
                            pending_emit = held
                            if pending_emit.startswith(OPEN_TAG):
                                in_tool_tag = True
                    elif etype == "result":
                        if isinstance(ev.get("stats"), dict):
                            stats = ev["stats"]
                    elif etype == "error":
                        err_event = ev.get("message") or str(ev)
                stderr = (proc.stderr.read() if proc.stderr else "") or ""
                rc = proc.wait(timeout=TIMEOUT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise AdapterError("Gemini CLI 响应超时，稍后再试", "unavailable")

            if rc != 0 or err_event:
                detail = (err_event or stderr).strip()
                low = detail.lower()
                if any(w in low for w in ("auth", "login", "oauth", "unauthenticated", "credentials")):
                    raise AdapterError("服务器上的 Gemini CLI 还没登录 Google 账号（或登录已过期）：请在服务器上运行 gemini 登录", "auth")
                if "429" in low or "quota" in low or "rate limit" in low or "resource_exhausted" in low:
                    raise AdapterError("Google AI 订阅额度已达上限或限流，稍后再试", "rate_limit")
                if "not found" in low or "404" in low:
                    raise AdapterError(f"Gemini CLI 找不到模型：{self.model}", "bad_request")
                raise AdapterError(f"Gemini CLI 调用失败：{detail[:200] or f'退出码 {rc}'}", "unavailable")

            if not in_tool_tag and pending_emit:
                yield "text", pending_emit

            clean_text, calls = _parse_tool_calls(full_text)
            prompt_tok = int(stats.get("input_tokens") or 0)
            cached_tok = int(stats.get("cached") or 0)
            out_tok = int(stats.get("output_tokens") or 0)
            usage = {
                "input_tokens": max(prompt_tok - cached_tok, 0),
                "cache_read_tokens": cached_tok,
                "output_tokens": out_tok,
            }
            stop = "tool_use" if calls else "end"
            yield "result", TurnResult(text=clean_text, tool_calls=calls, native=None, usage=usage, stop=stop)
