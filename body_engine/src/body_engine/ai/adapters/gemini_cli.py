"""Server-side Gemini CLI adapter (kind gemini_cli): the deployer's own Google AI plan login, used for in-page chat.

Off by default. It runs a subprocess as the server, so the admin opts in with BODY_GEMINI_CLI_ENABLED=1, and only
BODY_ASSISTANT_USER_ID may use it (catalog.kind_allowed; checked again in chat.resolve_provider and choice.usable).

The CLI is only a text generator here; Body Engine's chat loop (chat.py) runs the tools as the signed-in user, so
the model sees only this user's conversation and tool results, and deletes / undos still need the in-page confirmation.
How it is contained:
- every CLI tool (shell, files, web, memory, sub-agents) is denied by an admin-tier policy (highest priority) and
  excluded in the workspace settings; extensions (`-e none`) and MCP servers from the server's own settings are off;
- it runs in a fresh 0700 temp directory that holds only this turn's photos (`--skip-trust` for that directory only);
- the environment is an allowlist (no database DSN, no secrets, no API keys: GEMINI_API_KEY / GOOGLE_API_KEY are never
  passed, so the CLI always uses its Google sign-in and never bills a key);
- the prompt goes through stdin (not argv, so other processes cannot read it in `ps` and long chats fit), stderr goes
  to a file, and the whole turn is killed after TIMEOUT_S;
- "@" in user / tool text is neutralised so only the photos we wrote can be attached with `@photo_N.ext`.
Set BODY_GEMINI_HOME to a directory that holds only the CLI's login (passed as GEMINI_CLI_HOME), so the server
user's other Gemini settings, hooks and MCP servers are not loaded at all.
"""
import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from ...i18n import L
from ...toolspec import relaxed
from .base import TIMEOUT_S, Adapter, AdapterError, TurnResult

BLOCKED_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_APPLICATION_CREDENTIALS")
ENV_ALLOW = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "TMPDIR", "GEMINI_CLI_HOME", "GOOGLE_CLOUD_PROJECT",
             "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
             "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE")
BUILTIN_TOOLS = ["run_shell_command", "write_file", "replace", "read_file", "read_many_files", "list_directory",
                 "glob", "grep_search", "google_web_search", "web_fetch", "save_memory", "write_todos"]
# Admin tier beats user / workspace policies: no CLI tool may run, whatever the server's own settings say.
DENY_ALL_POLICY = ('[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 999\n'
                   'denyMessage = "No CLI tools here: ask for Body Engine tools with <tool_call>."\n')
NO_MCP = "__body_engine_none__"   # --allowed-mcp-server-names with a name that matches nothing = no MCP servers
EXT_BY_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
OPEN_TAG = "<tool_call>"
PROMPT_TAIL = "请按上面的说明继续回复（需要工具时只输出 <tool_call>{...}</tool_call>）。"


def find_gemini() -> str:
    b = os.environ.get("BODY_GEMINI_BIN")
    if b:
        if Path(b).is_file() and os.access(b, os.X_OK):
            return b
        raise AdapterError(L("找不到 BODY_GEMINI_BIN 指定的 gemini", "The gemini set in BODY_GEMINI_BIN was not found"), "unavailable")
    dirs = [d for d in os.environ.get("PATH", "").split(":") if d] + [
        str(Path.home() / ".npm-global/bin"), str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    for d in dict.fromkeys(dirs):
        p = Path(d) / "gemini"
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    raise AdapterError(L("服务器上找不到 gemini CLI（npm i -g @google/gemini-cli，再用 Google 账号登录）",
                         "No gemini CLI on the server (npm i -g @google/gemini-cli, then sign in with Google)"), "unavailable")


def clean_env() -> dict[str, str]:
    """Allowlist, not a blocklist: the server's database DSN, secrets and API keys never reach the CLI."""
    env = {k: os.environ[k] for k in ENV_ALLOW if k in os.environ and k not in BLOCKED_ENV}
    home = os.environ.get("BODY_GEMINI_HOME")
    if home:
        env["GEMINI_CLI_HOME"] = home
    env.setdefault("LANG", "en_US.UTF-8")
    env["NO_COLOR"] = "1"
    return env


def _defuse(text: str) -> str:
    """'@path' would make the CLI attach a file: only the photos we write may be attached."""
    return str(text).replace("@", "＠")


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
    for m in TOOL_CALL_RE.finditer(raw):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("name"), str):
            args = obj.get("args") if isinstance(obj.get("args"), dict) else (
                obj.get("arguments") if isinstance(obj.get("arguments"), dict) else {})
            # Our own id: the model's text cannot pick or reuse call ids
            calls.append({"id": f"gcli_{uuid.uuid4().hex[:12]}", "name": obj["name"], "args": args})
    clean = TOOL_CALL_RE.sub("", raw).strip()
    return clean, calls


def _classify(detail: str, rc: int, model: str) -> AdapterError:
    low = detail.lower()
    if any(w in low for w in ("auth", "login", "oauth", "unauthenticated", "credentials")):
        return AdapterError(L("服务器上的 Gemini CLI 没登录或登录过期：在服务器上运行 gemini 重新登录",
                              "The Gemini CLI on the server is not signed in (or expired): run gemini on the server to sign in"), "auth")
    if "429" in low or "quota" in low or "rate limit" in low or "resource_exhausted" in low:
        return AdapterError(L("Google AI 订阅的额度用完或被限流，稍后再试", "The Google AI subscription is out of quota or rate limited; try again later"),
                            "rate_limit")
    if "not found" in low or "404" in low:
        return AdapterError(L(f"Gemini CLI 找不到模型：{model}", f"The Gemini CLI has no model {model}"), "bad_request")
    if "trusted" in low:
        return AdapterError(L("Gemini CLI 拒绝在临时目录运行（版本太旧？需要支持 --skip-trust）",
                              "The Gemini CLI refused to run in a temporary folder (too old? it needs --skip-trust)"), "unavailable")
    return AdapterError(L(f"Gemini CLI 调用失败（退出码 {rc}）", f"The Gemini CLI failed (exit code {rc})"), "unavailable")


class GeminiCliAdapter(Adapter):
    kind = "gemini_cli"

    def _build_prompt(self, system: str, history: list[dict], tools: list, workdir: Path) -> str:
        img_idx = 0
        tool_specs = [{"name": t.name, "description": t.description, "parameters": relaxed(t.input_schema)} for t in tools]
        lines = [
            "# 系统指令", system, "",
            "# 工具调用协议",
            "你只能通过下面这些工具读写用户的身体数据（CLI 自带的工具全部不可用）。需要调用时只输出如下格式的块（可以多个），"
            "然后停下，等工具结果在下一轮给你：",
            '<tool_call>{"name": "工具名", "args": {"参数名": "值"}}</tool_call>',
            "不需要工具时直接用中文回答，不要输出 <tool_call> 标签。",
            "可用工具（JSON）：",
            json.dumps(tool_specs, ensure_ascii=False, separators=(",", ":")),
            "", "# 对话记录",
        ]
        for m in history:
            role = m["role"]
            if role == "user":
                parts = []
                for b in m["content"]:
                    if b["type"] == "image":
                        img_idx += 1
                        fname = f"photo_{img_idx}.{EXT_BY_MIME.get(b['media_type'], 'jpg')}"
                        (workdir / fname).write_bytes(base64.b64decode(b["data"]))
                        parts.append(f"@{fname}")
                    elif b["type"] == "text" and b.get("text"):
                        parts.append(_defuse(b["text"]))
                lines.append("用户：" + "\n".join(parts))
            elif role == "assistant":
                parts = []
                for b in m["content"]:
                    if b["type"] == "text" and b.get("text"):
                        parts.append(_defuse(b["text"]))
                    elif b["type"] == "tool_call":
                        parts.append("<tool_call>" + _defuse(json.dumps({"name": b["name"], "args": b["args"]},
                                                                       ensure_ascii=False)) + "</tool_call>")
                lines.append("管家：" + "\n".join(parts))
            elif role == "tool":
                for b in m["content"]:
                    kind = "错误" if b.get("is_error") else "结果"
                    lines.append(f'工具返回（{b["name"]} · {kind}）：{_defuse(b["content"])}')
        return "\n\n".join(lines)

    def _command(self, binary: str, workdir: Path, policy: Path) -> list[str]:
        cmd = [binary, "--skip-trust", "-o", "stream-json", "-e", "none", "--approval-mode", "default",
               "--allowed-mcp-server-names", NO_MCP, "--admin-policy", str(policy), "-p", PROMPT_TAIL]
        if self.model:
            cmd += ["-m", self.model]
        return cmd

    def stream_turn(self, system, history, tools):
        binary = find_gemini()
        with tempfile.TemporaryDirectory(prefix="body-gemini-") as tmp, \
                tempfile.TemporaryDirectory(prefix="body-gemini-ctl-") as ctl:
            workdir, ctldir = Path(tmp), Path(ctl)
            gdir = workdir / ".gemini"
            gdir.mkdir()
            (gdir / "settings.json").write_text(json.dumps({"mcpServers": {}, "tools": {"exclude": BUILTIN_TOOLS}}))
            policy = ctldir / "deny-all.toml"
            policy.write_text(DENY_ALL_POLICY)
            prompt_file = ctldir / "prompt.txt"
            prompt_file.write_text(self._build_prompt(system, history, tools, workdir), encoding="utf-8")
            stderr_file = ctldir / "stderr.txt"
            try:
                with open(prompt_file, "rb") as stdin, open(stderr_file, "wb") as stderr:
                    proc = subprocess.Popen(self._command(binary, workdir, policy), cwd=workdir, env=clean_env(),
                                            stdin=stdin, stdout=subprocess.PIPE, stderr=stderr,
                                            text=True, encoding="utf-8", errors="replace", bufsize=1)
            except OSError:
                raise AdapterError(L("无法启动 gemini CLI", "Could not start the gemini CLI"), "unavailable")
            timed_out = threading.Event()

            def _kill():
                timed_out.set()
                proc.kill()

            timer = threading.Timer(TIMEOUT_S, _kill)
            timer.start()
            full_text = pending_emit = ""
            in_tool_tag = False
            stats: dict = {}
            err_event: str | None = None
            try:
                assert proc.stdout is not None
                for raw_line in proc.stdout:
                    line = raw_line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    etype = ev.get("type")
                    if etype == "message" and ev.get("role") == "assistant":
                        chunk = ev.get("content") or ""
                        if not isinstance(chunk, str) or not chunk:
                            continue
                        full_text += chunk
                        if not in_tool_tag:
                            safe, pending_emit = _hold_back_tag_prefix(pending_emit + chunk)
                            if safe:
                                yield "text", safe
                            in_tool_tag = pending_emit.startswith(OPEN_TAG)
                    elif etype == "result":
                        if isinstance(ev.get("stats"), dict):
                            stats = ev["stats"]
                        if ev.get("status") == "error":
                            err_event = json.dumps(ev.get("error") or ev, ensure_ascii=False)
                    elif etype == "error" and ev.get("severity") != "warning":
                        err_event = str(ev.get("message") or ev)
                rc = proc.wait()
            finally:
                timer.cancel()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            if timed_out.is_set():
                raise AdapterError(L("Gemini CLI 响应超时，稍后再试", "The Gemini CLI timed out; try again later"), "unavailable")
            if rc != 0 or err_event:
                detail = err_event or stderr_file.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise _classify(detail, rc, self.model)

        if not in_tool_tag and pending_emit:
            yield "text", pending_emit
        clean_text, calls = _parse_tool_calls(full_text)
        prompt_tok = int(stats.get("input_tokens") or 0)
        cached_tok = int(stats.get("cached") or 0)
        usage = {"input_tokens": max(prompt_tok - cached_tok, 0), "cache_read_tokens": cached_tok,
                 "output_tokens": int(stats.get("output_tokens") or 0)}
        yield "result", TurnResult(text=clean_text, tool_calls=calls, native=None, usage=usage,
                                   stop="tool_use" if calls else "end")
