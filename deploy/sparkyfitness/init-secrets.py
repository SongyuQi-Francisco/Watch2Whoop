#!/usr/bin/env python3
"""Generates the local SparkyFitness config (generated once, left untouched if it exists).

- secrets/sparkyfitness.env: database password, encryption key, Better Auth secret, external address, admin email
- secrets/sparkyfitness_owner_password: login password of the owner account (bootstrap.py uses it to register)

The external address is the Tailscale HTTPS name of this Mac + port 8444 (what tailscale serve uses).
Note: the database password only takes effect when the database is first initialized; changing it here later does nothing (you would have to ALTER ROLE inside Postgres);
if BETTER_AUTH_SECRET changes everyone is logged out and two-factor auth breaks, so do not lose it.

Usage (repo root): python3 deploy/sparkyfitness/init-secrets.py [--email your-email]
The secrets directory defaults to secrets/ at the repo root; override with BODY_SECRETS_DIR.
"""

import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRETS = Path(os.environ.get("BODY_SECRETS_DIR", ROOT / "secrets"))
ENV_OUT = SECRETS / "sparkyfitness.env"
PASSWORD_OUT = SECRETS / "sparkyfitness_owner_password"
HTTPS_PORT = 8444


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _tailscale_name() -> str:
    exe = shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    status = json.loads(subprocess.run([exe, "status", "--json"], check=True, capture_output=True, text=True).stdout)
    return status["Self"]["DNSName"].rstrip(".")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--email", default="owner@example.com", help="email of the owner account (also the admin)")
    p.add_argument("--frontend-url", help="defaults to https://<Tailscale name>:8444")
    args = p.parse_args()

    SECRETS.mkdir(exist_ok=True)
    if ENV_OUT.exists():
        print(f"已存在，跳过：{ENV_OUT}")
    else:
        url = args.frontend_url or f"https://{_tailscale_name()}:{HTTPS_PORT}"
        values = {
            # Alphanumeric only, to avoid escaping in URLs / DSNs
            "SPARKY_FITNESS_DB_PASSWORD": secrets.token_hex(24),
            "SPARKY_FITNESS_APP_DB_PASSWORD": secrets.token_hex(24),
            "SPARKY_FITNESS_API_ENCRYPTION_KEY": secrets.token_hex(32),
            "BETTER_AUTH_SECRET": base64.b64encode(secrets.token_bytes(32)).decode(),
            "SPARKY_FITNESS_FRONTEND_URL": url,
            "SPARKY_FITNESS_ADMIN_EMAIL": args.email,
        }
        _write_private(ENV_OUT, "".join(f"{k}={v}\n" for k, v in values.items()))
        print(f"已生成：{ENV_OUT}（对外地址 {url}）")

    if PASSWORD_OUT.exists():
        print(f"已存在，跳过：{PASSWORD_OUT}")
    else:
        _write_private(PASSWORD_OUT, secrets.token_urlsafe(18) + "\n")
        print(f"已生成：{PASSWORD_OUT}")


if __name__ == "__main__":
    main()
