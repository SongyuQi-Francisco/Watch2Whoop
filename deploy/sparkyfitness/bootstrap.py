#!/usr/bin/env python3
"""Creates a SparkyFitness account and issues API keys for one person (steps already done are skipped automatically).

1. Register the account (the password is read from secrets/sparkyfitness_<who>_password, generated if missing). Sign-up is off by default,
   so this step temporarily recreates the server container with SF_DISABLE_SIGNUP=false and turns it off again afterwards.
2. Log in and generate two non-expiring API keys:
   - secrets/sparkyfitness_api_key_<who>_rw: read-write, for the migration script
   - secrets/sparkyfitness_api_key_<who>_ro: read-only (the server enforces GET only), for Body Engine
3. Print the SparkyFitness user id (to record in Body Engine: body-engine sparky-link).

Usage (repo root): python3 deploy/sparkyfitness/bootstrap.py [--who owner --email ... --name ...]
The secrets directory defaults to secrets/ at the repo root; override with BODY_SECRETS_DIR.
"""

import argparse
import http.cookiejar
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SECRETS = Path(os.environ.get("BODY_SECRETS_DIR", ROOT / "secrets"))
BASE = os.environ.get("SPARKY_URL", "http://127.0.0.1:8300")
ORIGIN = "http://localhost:8300"  # listed in SPARKY_FITNESS_EXTRA_TRUSTED_ORIGINS


class Session:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method, headers={
            "Content-Type": "application/json", "Accept": "application/json", "Origin": ORIGIN})
        try:
            with self.opener.open(req, timeout=60) as resp:
                text = resp.read().decode()
                return resp.status, (json.loads(text) if text else None)
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(text)
            except ValueError:
                return e.code, text


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _compose_up(disable_signup: bool) -> None:
    env = {**os.environ, "SF_DISABLE_SIGNUP": "true" if disable_signup else "false",
           "BODY_SECRETS_DIR": str(SECRETS)}
    subprocess.run([str(HERE / "sf.sh"), "up", "-d", "sparkyfitness-server"], check=True, env=env)
    for _ in range(60):
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(3)
    raise SystemExit("SparkyFitness 没起来，看 sf.sh logs sparkyfitness-server")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--who", default="owner", help="identifier used in the secrets file names, e.g. owner, mum")
    p.add_argument("--email", help="defaults to SPARKY_FITNESS_ADMIN_EMAIL from secrets/sparkyfitness.env (owner only)")
    p.add_argument("--name", default="Alex")
    args = p.parse_args()

    env = dict(line.split("=", 1) for line in (SECRETS / "sparkyfitness.env").read_text().splitlines() if "=" in line)
    email = args.email or (env["SPARKY_FITNESS_ADMIN_EMAIL"] if args.who == "owner" else None)
    if not email:
        raise SystemExit("家人账号要给 --email")
    pw_file = SECRETS / f"sparkyfitness_{args.who}_password"
    if not pw_file.exists():
        _write_private(pw_file, secrets.token_urlsafe(18) + "\n")
    password = pw_file.read_text().strip()

    s = Session()
    status, body = s.call("POST", "/api/auth/sign-in/email", {"email": email, "password": password})
    if status != 200:
        print("账号还不存在，临时打开注册…")
        _compose_up(disable_signup=False)
        try:
            status, body = s.call("POST", "/api/auth/sign-up/email",
                                  {"email": email, "password": password, "name": args.name})
            if status != 200:
                raise SystemExit(f"注册失败：{status} {body}")
            print("已注册：", email)
        finally:
            _compose_up(disable_signup=True)
            print("注册已关闭")
        status, body = s.call("POST", "/api/auth/sign-in/email", {"email": email, "password": password})
        if status != 200:
            raise SystemExit(f"登录失败：{status} {body}")
    user_id = body["user"]["id"]

    for scope, suffix in (("full", "rw"), ("read", "ro")):
        key_file = SECRETS / f"sparkyfitness_api_key_{args.who}_{suffix}"
        if key_file.exists():
            print("已存在，跳过：", key_file)
            continue
        status, res = s.call("POST", "/api/identity/user/generate-api-key",
                             {"name": f"body-engine-{suffix}", "expiresIn": None, "scope": scope})
        if status != 201:
            raise SystemExit(f"生成 API key 失败：{status} {res}")
        _write_private(key_file, res["apiKey"]["key"] + "\n")
        print("已生成：", key_file)

    print("SparkyFitness user id:", user_id)


if __name__ == "__main__":
    main()
