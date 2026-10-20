#!/usr/bin/python3
"""Body butler daemon: runs Claude Code in Remote Control server mode (claude remote-control) in the assistant/ directory.

Since 2026-10-18 it runs on Alibaba Cloud Ali as the systemd service body-butler (deploy/ali/butler/, user bodybutler,
Restart=always). When falling back to the Mac it is started by the LaunchAgent org.watch2whoop.body.butler
(deploy/assistant/install.sh, KeepAlive).
Standard library only, compatible with Python 3.6 (Ali's system Python) up to macOS /usr/bin/python3. What it does:
1. Strips environment variables that make Remote Control refuse or bill through the API (ANTHROPIC_API_KEY etc.),
   so everything goes through the owner's Claude subscription login;
2. Picks a claude that is new enough (Remote Control session resume needs v2.1.200+): BODY_CLAUDE_BIN > claude on PATH >
   ~/.local/bin/claude > the claude bundled with the Claude desktop app (newest version);
3. Runs `claude remote-control --name 身体管家 ...` inside a pseudo-terminal (it needs a TTY) and logs the output
   (the UI redraws every second, so the same line is logged at most once per 10 minutes); the session URL is taken from
   claude's bridge-pointer.json and written to state.json (BODY_BUTLER_STATE_DIR, default health_data/assistant/;
   /var/lib/body-butler/ on Ali; used by doctor and by deploy/ali/butler/butler.sh activate, which points the
   Today page "Ask the butler" link at it).
After a restart, claude remote-control reattaches the session this directory served before (if down for under 4 hours),
so the URL normally stays the same.

Usage: butler.py run (for systemd / the LaunchAgent) | butler.py status | butler.py check | butler.py gemini-check
"""
import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODE_ROOT = HERE.parent.parent                       # repo (or worktree) root: assistant/ lives here
ROOT = Path(os.environ.get("BODY_ROOT", CODE_ROOT))  # main repo that holds data and secrets
HEALTH = Path(os.environ.get("BODY_HEALTH_DATA_DIR", ROOT / "health_data"))
STATE_DIR = Path(os.environ.get("BODY_BUTLER_STATE_DIR", HEALTH / "assistant"))
STATE = STATE_DIR / "state.json"
ASSISTANT_DIR = Path(os.environ.get("BODY_ASSISTANT_DIR", CODE_ROOT / "assistant"))
NAME = os.environ.get("BODY_BUTLER_NAME", "身体管家")
CAPACITY = os.environ.get("BODY_BUTLER_CAPACITY", "4")  # concurrent sessions (one claude process each; 2 on Ali)
IS_MAC = sys.platform == "darwin"
MIN_VERSION = (2, 1, 200)
LOG_KEEP_DAYS = 14
# These variables make Remote Control refuse (API key / token login, third-party API URL, feature flags turned off)
BLOCKED_ENV = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
               "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
               "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_GROWTHBOOK", "ANTHROPIC_MODEL"]
# If these show up in the output, the owner has to act once (log in, trust the directory, consent to Remote Control); restarting automatically will not help
NEEDS_USER = [
    ("needs_login", re.compile(r"must be logged in|only available with claude\.ai subscriptions|revoked|"
                               r"Authentication failed|Please log in|full-scope login|Not logged in", re.I),
     "Claude Code 没登录或登录失效：运行 claude auth login（选 claude.ai 账号；Ali 上见 deploy/assistant/README.md）"),
    ("needs_trust", re.compile(r"Trust .*\?\s*\[y/N\]|Workspace not trusted", re.I),
     "管家目录还没信任：终端 cd 到 assistant/ 运行一次 claude，选信任，再 /exit"),
    ("needs_consent", re.compile(r"Enable Remote Control\?", re.I),
     "第一次用 Remote Control 要确认：终端 cd 到 assistant/ 运行一次 claude remote-control，答 y，再 Ctrl+C"),
    ("not_eligible", re.compile(r"isn't enabled for this account|disabled by your organization|"
                                r"only available when using Claude via api\.anthropic\.com|requires claude\.ai subscription auth",
                                re.I), "Remote Control 不可用（看日志里的原因）"),
]
NEEDS_USER_BACKOFF = 300  # seconds: while waiting for the owner to act, retry every 5 minutes
STOP_GRACE = 8  # seconds claude gets after Ctrl+C before SIGTERM (SIGKILL after twice that); systemd waits 30
URL_RE = re.compile(r"https://claude\.ai/code/[A-Za-z0-9_\-?=&./]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)|\x1b[()][A-Z0-9]|[\x00-\x08\x0b-\x1f]")


def log_path() -> Path:
    return STATE_DIR / f"butler-{datetime.now():%Y-%m-%d}.log"


def log(msg: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(log_path(), "a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def rotate_logs() -> None:
    cutoff = datetime.now() - timedelta(days=LOG_KEEP_DAYS)
    for p in STATE_DIR.glob("butler-*.log"):
        try:
            if datetime.strptime(p.stem[7:], "%Y-%m-%d") < cutoff:
                p.unlink()
        except ValueError:
            pass


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(**updates) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    s = load_state()
    s.update(updates)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2))
    tmp.replace(STATE)


def bridge_pointer():
    """claude remote-control records the session it currently serves for this directory in
    ~/.claude/projects/<directory name>/bridge-pointer.json (sessionId, environmentId, pid).
    The output only prints the environment URL; the session URL comes from here."""
    name = re.sub(r"[^A-Za-z0-9]", "-", str(ASSISTANT_DIR))
    try:
        return json.loads((Path.home() / ".claude/projects" / name / "bridge-pointer.json").read_text())
    except (OSError, ValueError):
        return {}


SECRETS = Path(os.environ.get("BODY_SECRETS_DIR", ROOT / "secrets"))
# The butler connects to the remote MCP on the primary server (Alibaba Cloud Ali since 2026-10-18): in assistant/.mcp.json
# url = ${BODY_MCP_URL:-https://body.example.com/mcp}, Authorization = Bearer ${BODY_AI_TOKEN}.
# The token is AI-only (scope mcp, usable only on /mcp; audit channel = mcp:butler); the plaintext lives only in secrets/
# and is put into claude's environment at startup, never written to the plist or .mcp.json.
# On Ali the bodybutler user cannot read secrets/ (700 root): systemd reads the EnvironmentFile (secrets/body_butler.env)
# as root and passes BODY_AI_TOKEN directly, so no file is read here; the service also sets the MCP URL to the local
# http://127.0.0.1:8100/mcp.
AI_TOKEN_FILE = Path(os.environ.get("BODY_AI_TOKEN_FILE", SECRETS / "body_ai_token_butler"))


def ai_token_file_readable():
    try:
        return AI_TOKEN_FILE.is_file() and os.access(str(AI_TOKEN_FILE), os.R_OK)
    except OSError:  # no permission on a parent directory
        return False


def clean_env():
    env = {k: v for k, v in os.environ.items() if k not in BLOCKED_ENV}
    if ai_token_file_readable():
        env["BODY_AI_TOKEN"] = AI_TOKEN_FILE.read_text().strip()
    env.setdefault("LANG", "en_US.UTF-8")
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = "200"
    return env


def version_of(binary):
    try:
        out = subprocess.run([binary, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, timeout=30, env=clean_env()).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(map(int, m.groups())) if m else None


def find_claude():
    """A claude that is new enough: BODY_CLAUDE_BIN > PATH > ~/.local/bin > the desktop app's bundled one (highest
    version). Returns (path, version tuple)."""
    b = os.environ.get("BODY_CLAUDE_BIN")
    if b:
        return b, version_of(b) or (0, 0, 0)
    cands = []
    for d in os.environ.get("PATH", "").split(":") + [str(Path.home() / ".local/bin")]:
        p = Path(d) / "claude"
        if p.exists():
            cands.append(str(p))
    desktop = Path.home() / "Library/Application Support/Claude/claude-code"
    cands += [str(p) for p in desktop.glob("*/*/claude.app/Contents/MacOS/claude")]
    found = [(c, v) for c, v in ((c, version_of(c)) for c in dict.fromkeys(cands)) if v]
    if not found:
        raise SystemExit("找不到 claude（装 Claude Code：https://code.claude.com）")
    if found[0][1] >= MIN_VERSION:
        return found[0]  # if the first claude on PATH is new enough, use it (the one the owner installed and updates)
    return max(found, key=lambda x: x[1])


def command(binary):
    return [binary, "remote-control", "--name", NAME, "--spawn", "same-dir", "--permission-mode", "default",
            "--capacity", CAPACITY, "--no-chrome"]


def notify(title, message):
    """macOS notification on the Mac; Linux (Ali) has no desktop, so doctor (hourly) reports it instead."""
    if IS_MAC:
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(message)} with title {json.dumps(title)}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)


def exit_code(status):
    """Compatible version of os.waitstatus_to_exitcode (3.9+)."""
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return os.WEXITSTATUS(status)


def check():
    """Pre-start check: returns a list of problems (empty = OK to start)."""
    problems = []
    if not (ASSISTANT_DIR / "CLAUDE.md").exists():
        problems.append(f"找不到管家目录 {ASSISTANT_DIR}")
    binary, v = find_claude()
    if v < MIN_VERSION:
        problems.append(f"claude 版本 {'.'.join(map(str, v))} 太旧（要 ≥ {'.'.join(map(str, MIN_VERSION))}）：运行 claude update")
    for k in BLOCKED_ENV[:4]:
        if k in os.environ:
            problems.append(f"环境里有 {k}（启动时会清掉）")
    if not os.environ.get("BODY_AI_TOKEN") and not ai_token_file_readable():
        problems.append(f"没有 {AI_TOKEN_FILE}：在主服务器上 body-engine ai-token-create --user-id 1 --client butler，"
                        "明文写进这个文件（600）")
    return problems


GEMINI_BLOCKED_ENV = ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"]


def find_gemini():
    """Locate the gemini CLI binary: BODY_GEMINI_BIN > PATH > common npm/Homebrew paths. Returns (path, version)."""
    b = os.environ.get("BODY_GEMINI_BIN")
    if b:
        return (b, version_of(b) or (0, 0, 0)) if Path(b).exists() else (None, None)
    dirs = os.environ.get("PATH", "").split(":") + [
        str(Path.home() / ".npm-global/bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
        str(Path.home() / ".local/bin"),
    ]
    for d in dict.fromkeys(dirs):
        if not d:
            continue
        p = Path(d) / "gemini"
        if p.exists():
            return str(p), version_of(str(p)) or (0, 0, 0)
    return None, None


def gemini_check():
    """Check whether Gemini CLI is installed, logged in via Google AI Plan OAuth, and configured in assistant/."""
    problems = []
    if not (ASSISTANT_DIR / "CLAUDE.md").exists():
        problems.append(f"找不到管家目录 {ASSISTANT_DIR}")
    if not (ASSISTANT_DIR / ".gemini/settings.json").exists():
        problems.append(f"找不到 {ASSISTANT_DIR / '.gemini/settings.json'}")
    binary, v = find_gemini()
    if not binary:
        problems.append("找不到 gemini（装 Gemini CLI：npm install -g @google/gemini-cli）")
    oauth = Path(os.environ.get("GEMINI_CLI_HOME", str(Path.home()))) / ".gemini/oauth_creds.json"
    if not oauth.is_file():
        problems.append(f"找不到 {oauth}：运行 gemini 选 Login with Google 登录 Google AI Plan 账号")
    for k in GEMINI_BLOCKED_ENV:
        if k in os.environ:
            problems.append(f"环境里有 {k}（页面内调用时会自动清掉，终端直接运行 gemini 前请先 unset）")
    if not os.environ.get("BODY_AI_TOKEN") and not ai_token_file_readable():
        problems.append(f"没有 {AI_TOKEN_FILE}（终端 cd assistant/ 直接跑 gemini 连 MCP 时需要；页面内 gemini_cli 不需要）")
    return binary, v, problems


def run():
    rotate_logs()
    binary, v = find_claude()
    cmd = command(binary)
    log(f"启动：{' '.join(cmd)}（claude {'.'.join(map(str, v))}，目录 {ASSISTANT_DIR}）")
    removed = [k for k in BLOCKED_ENV if k in os.environ]
    if removed:
        log(f"已从环境里去掉：{', '.join(removed)}")
    save_state(pid=os.getpid(), started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
               claude=binary, claude_version=".".join(map(str, v)), assistant_dir=str(ASSISTANT_DIR),
               status="starting", api_key_in_env=False)
    pid, fd = pty.fork()
    if pid == 0:  # child process: run claude in the pseudo-terminal
        os.chdir(ASSISTANT_DIR)
        os.execve(binary, cmd, clean_env())

    # Stopping (systemd stop / launchctl bootout / Ctrl+C): send claude Ctrl+C (SIGINT, the session is kept and can be
    # reattached later), escalate to SIGTERM after STOP_GRACE and SIGKILL after 2 x STOP_GRACE, then exit right away.
    # The handler itself never sleeps, and once claude is gone it raises SystemExit so the backoff sleeps below are cut
    # short (time.sleep resumes after a signal handler that returns normally, PEP 475).
    stopping = {"at": None, "child_done": False}

    def signal_child(sig):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass

    def stop(signum, frame):
        if stopping["child_done"]:
            raise SystemExit(0)
        if stopping["at"] is None:
            stopping["at"] = time.time()
            log(f"收到信号 {signum}，停止 claude remote-control")
            signal_child(signal.SIGINT)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    buf, seen, blocked = "", set(), None
    logged = {}  # line -> time it was last logged; the UI redraws every second, so the same line is logged at most once per 10 minutes
    while True:
        if stopping["at"] is not None:
            waited = time.time() - stopping["at"]
            if waited > 2 * STOP_GRACE:
                signal_child(signal.SIGKILL)
            elif waited > STOP_GRACE:
                signal_child(signal.SIGTERM)
        try:
            r, _, _ = select.select([fd], [], [], 1 if stopping["at"] is not None else 15)
        except InterruptedError:
            continue
        if fd in r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            text = ANSI_RE.sub("", data.decode(errors="replace"))
            buf += text
            *lines, buf = buf.split("\n")
            for line in lines:
                line = line.strip()
                if line and time.time() - logged.get(line, 0) > 600:
                    log("claude: " + line[:500])
                    logged[line] = time.time()
                for key, pattern, hint in NEEDS_USER:
                    if not blocked and pattern.search(line):
                        blocked = key
                        prev = load_state().get("status")
                        save_state(status=key, detail=line[:300], hint=hint)
                        log(f"需要用户处理：{hint}")
                        if prev != key:
                            notify("身体管家没启动", hint)
                        try:
                            os.kill(pid, signal.SIGTERM)  # wait for the owner to act; do not hang here
                        except ProcessLookupError:
                            pass
                if "Connected" in line and load_state().get("status") == "starting":
                    save_state(status="running", detail=None, hint=None)
                for url in URL_RE.findall(line):
                    if "environment=" in url and url not in seen:
                        seen.add(url)
                        save_state(environment_url=url.rstrip(".,)"))
        # session URL: the session recorded in bridge-pointer.json that belongs to this very claude process
        bp = bridge_pointer()
        if bp.get("pid") == pid and bp.get("sessionId") and bp["sessionId"] != load_state().get("session_id"):
            url = f"https://claude.ai/code/{bp['sessionId']}"
            save_state(session_url=url, session_id=bp["sessionId"], environment_id=bp.get("environmentId"),
                       url_seen_at=datetime.now().astimezone().isoformat(timespec="seconds"))
            log(f"会话地址：{url}")
    _, status = os.waitpid(pid, 0)
    stopping["child_done"] = True
    code = exit_code(status)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    if stopping["at"] is not None:
        save_state(status="stopped", exited_at=stamp, exit_code=code)
        log(f"claude remote-control 已停止，退出码 {code}")
        return 0
    if blocked:
        save_state(exited_at=stamp, exit_code=code)
        log(f"claude remote-control 退出（{blocked}），{NEEDS_USER_BACKOFF} 秒后重试")
        time.sleep(NEEDS_USER_BACKOFF)
        return code
    save_state(status="exited", exited_at=stamp, exit_code=code)
    log(f"claude remote-control 退出，退出码 {code}（systemd / launchd 会重启）")
    time.sleep(10)  # avoid being respawned in a tight loop when crashing repeatedly
    return code


def status():
    s = load_state()
    print(json.dumps(s, ensure_ascii=False, indent=2) if s else "还没运行过")
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "run":
        return run()
    if cmd == "check":
        problems = check()
        binary, v = find_claude()
        print(f"claude：{binary}（{'.'.join(map(str, v))}）\n管家目录：{ASSISTANT_DIR}\n状态文件：{STATE}")
        print("\n".join("⚠ " + p for p in problems) or "✓ 可以启动")
        return 1 if any("太旧" in p or "找不到" in p for p in problems) else 0
    if cmd == "gemini-check":
        binary, v, problems = gemini_check()
        ver = ".".join(map(str, v)) if v else "未安装"
        print(f"gemini：{binary or '未找到'}（{ver}）\n管家目录：{ASSISTANT_DIR}")
        print("\n".join("⚠ " + p for p in problems) or "✓ Gemini CLI（Google AI Plan）可用")
        return 1 if any("找不到" in p for p in problems) else 0
    if cmd == "status":
        return status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
