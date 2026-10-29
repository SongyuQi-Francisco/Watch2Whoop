#!/usr/bin/env python3
"""Receiver for iPhone Shortcut health data (standard library only). For the format and addresses see docs/shortcut-sync-spec.md.

POST /upload?name=health   body = text assembled by the Shortcut; header Authorization: Bearer <upload token>
  - Saved to health_data/<name>_latest.txt and health_data/archive/<name>_timestamp.txt (user 1);
    other users go under archive/u<user_id>/. Replies 200 once saved, then runs `body-engine import-shortcut`, `compute-scores` for the last
    4 days and `evaluate-rules` in the background (idempotent; uploads arriving during an import are merged into one re-run).
  - The token identifies the user via secrets/health_upload_tokens.json (only sha256 is stored); old Shortcuts without a token are still accepted during the transition (counted as user 1),
    with one line in server.log; a wrong token gets 401. With HEALTH_SYNC_REQUIRE_TOKEN=1, requests without a token are rejected too.
GET /ping                   health-sync ok
GET /legacy-status          {"cutoff": ..., "active": ...}: the retirement switch below, for the doctor

Retirement (2.0.0 R1, docs/ios-native-2.0.md §4 R1): once BODY_LEGACY_SOURCES_CUTOFF (ISO date = 00:00 UTC of that day, or
an ISO date-time) is reached, POST /upload answers 410 Gone with a Chinese-first message pointing to the Watch2Whoop app and
stores nothing; files already received stay as they are. Unset (default) or invalid = uploads are accepted as before.

Listens on two ports: 0.0.0.0:8765 (legacy LAN address, kept for compatibility) and 127.0.0.1:8767 (for `tailscale serve --https=8446`;
127.0.0.1:8765 on this machine is taken by another project, so it cannot be used).

Managing upload tokens:
  health_server.py token-create --user-id 1 --name iphone-owner   # plaintext is written to secrets/health_upload_token_<name>
  health_server.py token-list
  health_server.py token-revoke --name iphone-owner
"""
import argparse, datetime, hashlib, hmac, json, os, re, secrets, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("HEALTH_SYNC_BASE") or os.path.expanduser("~/Watch2Whoop/health_data")
ARCHIVE = os.path.join(BASE, "archive")
SECRETS = os.environ.get("HEALTH_SYNC_SECRETS") or os.path.join(os.path.dirname(BASE), "secrets")
TOKENS = os.path.join(SECRETS, "health_upload_tokens.json")
PORT = int(os.environ.get("HEALTH_SYNC_PORT", "8765"))              # 0.0.0.0, LAN
LOCAL_PORT = int(os.environ.get("HEALTH_SYNC_LOCAL_PORT", "8767"))  # 127.0.0.1, tailscale serve; 0 = off
REQUIRE_TOKEN = os.environ.get("HEALTH_SYNC_REQUIRE_TOKEN") == "1"
RUN_IMPORT = os.environ.get("HEALTH_SYNC_IMPORT", "1") == "1"
MAX_BYTES = 50 * 1024 * 1024
OWNER_USER_ID = 1             # old Shortcuts without a token count as the owner
UV = os.path.expanduser("~/.local/bin/uv")
ENGINE_DIR = os.environ.get("BODY_ENGINE_DIR") or os.path.join(os.path.dirname(BASE), "body_engine")
IMPORT_LOG = os.path.join(BASE, "import.log")
SERVER_LOG = os.path.join(BASE, "server.log")

CUTOFF_ENV = "BODY_LEGACY_SOURCES_CUTOFF"
GONE_MESSAGE = ("快捷指令同步已停用：请打开 Watch2Whoop app 同步健康数据（「我」→「手机同步」）。\n"
                "The Shortcut upload has been retired: please sync your health data with the Watch2Whoop app.\n")

_import_users: set[int] = set()
_import_cv = threading.Condition()
_save_lock = threading.Lock()


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def log(msg: str) -> None:
    with open(SERVER_LOG, "a") as f:
        f.write(f"{now()} {msg}\n")


def user_archive(user_id: int) -> str:
    return ARCHIVE if user_id == OWNER_USER_ID else os.path.join(ARCHIVE, f"u{user_id}")


# ---------- retirement switch (same rules as body_engine/rawdata/retire.parse_cutoff) ----------

def parse_cutoff(raw: str | None) -> datetime.datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if len(raw) == 10:
        return datetime.datetime.combine(datetime.date.fromisoformat(raw), datetime.time.min, datetime.timezone.utc)
    t = datetime.datetime.fromisoformat(raw)
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def cutoff_state(now: datetime.datetime | None = None) -> dict:
    raw = os.environ.get(CUTOFF_ENV, "")
    try:
        at = parse_cutoff(raw)
    except ValueError:
        return {"cutoff": None, "active": False, "error": "invalid"}
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return {"cutoff": at.isoformat() if at else None, "active": at is not None and now >= at, "error": None}


# ---------- token ----------

def load_tokens() -> dict:
    try:
        with open(TOKENS) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_tokens(tokens: dict) -> None:
    os.makedirs(SECRETS, exist_ok=True)
    tmp = TOKENS + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TOKENS)


def check_token(headers) -> tuple[int | None, str]:
    """→ (user_id, reason). user_id None = reject."""
    auth = headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("X-Upload-Token", "").strip()
    if not token:
        return (None, "没带 token（已要求必须带）") if REQUIRE_TOKEN else (OWNER_USER_ID, "没带 token（旧快捷指令，过渡期按 user 1 收）")
    digest = hashlib.sha256(token.encode()).hexdigest()
    for h, info in load_tokens().items():
        if hmac.compare_digest(h, digest) and not info.get("revoked_at"):
            return int(info["user_id"]), f"token {info.get('name', '?')}"
    return None, "token 不对或已吊销"


# ---------- import ----------

def _import_worker() -> None:
    while True:
        with _import_cv:
            while not _import_users:
                _import_cv.wait()
            users = sorted(_import_users)
            _import_users.clear()
        for uid in users:
            with open(IMPORT_LOG, "a") as out:
                out.write(f"{now()} import-shortcut user {uid}\n"); out.flush()
                # Scores of the last few days are recomputed right away, so a night that just synced shows on the Today page
                # without waiting for the hourly job (4 days back covers every time zone; the engine recomputes from its origin
                # and only writes this window). evaluate-rules refreshes the reminders that depend on those scores.
                start = (datetime.date.today() - datetime.timedelta(days=4)).isoformat()
                steps = [["import-shortcut", "--user-id", str(uid), "--archive-dir", user_archive(uid)],
                         ["compute-scores", "--user-id", str(uid), "--start", start],
                         ["evaluate-rules", "--user-id", str(uid)]]
                for step in steps:
                    try:
                        r = subprocess.run([UV, "--directory", ENGINE_DIR, "run", "--quiet", "body-engine", *step],
                                           stdout=out, stderr=subprocess.STDOUT, timeout=600)
                    except Exception as e:  # a failure does not affect receiving; the next upload or the hourly job retries
                        out.write(f"{step[0]} failed: {e}\n"); break
                    if r.returncode != 0:
                        out.write(f"{step[0]} exited {r.returncode}\n"); break


def want_import(user_id: int) -> None:
    with _import_cv:
        _import_users.add(user_id)
        _import_cv.notify()


# ---------- HTTP ----------

class H(BaseHTTPRequestHandler):
    def _reply(self, code: int, msg: str, ctype: str = "text/plain; charset=utf-8") -> None:
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/ping":
            return self._reply(200, "health-sync ok\n")
        if path == "/legacy-status":
            return self._reply(200, json.dumps(cutoff_state()) + "\n", "application/json")
        self._reply(404, "not found\n")

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/upload":
            return self._reply(404, "not found\n")
        name = parse_qs(u.query).get("name", ["data"])[0]
        name = re.sub(r"[^A-Za-z0-9_-]", "", name)[:40] or "data"
        user_id, why = check_token(self.headers)
        via = "tailscale" if self.server.server_address[0] == "127.0.0.1" else "lan"
        if cutoff_state()["active"]:   # retired (2.0.0 R1): nothing is stored any more
            log(f"已停用，拒收 {name}（user {user_id if user_id is not None else '?'}，{via}）")
            return self._reply(410, GONE_MESSAGE)
        if user_id is None:
            log(f"拒收 {name}（{via}，{self.client_address[0]}）：{why}")
            return self._reply(401, "unauthorized\n")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BYTES:
            return self._reply(400, "bad size\n")
        body = self.rfile.read(n)
        folder = user_archive(user_id)
        with _save_lock:
            os.makedirs(folder, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            path, k = os.path.join(folder, f"{name}_{ts}.txt"), 2
            while os.path.exists(path):          # two uploads in the same second: add a suffix, sort order stays after
                path, k = os.path.join(folder, f"{name}_{ts}_{k}.txt"), k + 1
            with open(path, "wb") as f:
                f.write(body)
            latest = os.path.join(BASE if user_id == OWNER_USER_ID else folder, f"{name}_latest.txt")
            with open(latest, "wb") as f:
                f.write(body)
        log(f"收到 {name} {n} 字节 → {os.path.relpath(path, BASE)}（user {user_id}，{via}，{why}）")
        self._reply(200, f"saved {name} {n} bytes\n")
        if name == "health" and RUN_IMPORT:
            want_import(user_id)

    def log_message(self, fmt, *a):
        log(fmt % a)


def serve() -> None:
    os.makedirs(ARCHIVE, exist_ok=True)
    threading.Thread(target=_import_worker, daemon=True).start()
    servers = [ThreadingHTTPServer(("0.0.0.0", PORT), H)]
    if LOCAL_PORT:
        servers.append(ThreadingHTTPServer(("127.0.0.1", LOCAL_PORT), H))
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[0].serve_forever()


# ---------- token management commands ----------

def main() -> None:
    p = argparse.ArgumentParser(description="iPhone Shortcut health data receiver")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("token-create", help="issue an upload token (plaintext is written to secrets/health_upload_token_<name>)")
    c.add_argument("--user-id", type=int, required=True)
    c.add_argument("--name", required=True, help="purpose, e.g. iphone-owner")
    sub.add_parser("token-list")
    r = sub.add_parser("token-revoke")
    r.add_argument("--name", required=True)
    args = p.parse_args()
    if args.cmd is None:
        return serve()
    tokens = load_tokens()
    if args.cmd == "token-create":
        if not re.fullmatch(r"[a-z0-9-]{1,40}", args.name):
            sys.exit("名字只能用小写字母、数字和 -")
        if any(i["name"] == args.name and not i.get("revoked_at") for i in tokens.values()):
            sys.exit(f"已经有叫 {args.name} 的 token（先 token-revoke）")
        token = secrets.token_urlsafe(24)
        tokens[hashlib.sha256(token.encode()).hexdigest()] = {"user_id": args.user_id, "name": args.name, "created_at": now()}
        save_tokens(tokens)
        out = os.path.join(SECRETS, f"health_upload_token_{args.name}")
        with open(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            f.write(token + "\n")
        print(f"已创建 {args.name}（user {args.user_id}），明文在 {out}")
    elif args.cmd == "token-list":
        for info in tokens.values():
            print(json.dumps(info, ensure_ascii=False))
    elif args.cmd == "token-revoke":
        hit = [i for i in tokens.values() if i["name"] == args.name and not i.get("revoked_at")]
        for i in hit:
            i["revoked_at"] = now()
        save_tokens(tokens)
        print(f"吊销了 {len(hit)} 个")


if __name__ == "__main__":
    main()
