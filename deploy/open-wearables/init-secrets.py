#!/usr/bin/env python3
"""Generates the local Open Wearables config (generated once, left untouched if it exists).

- secrets/open_wearables.env: rendered from deploy/open-wearables/.env.example, with __GENERATE__ replaced by random values
- secrets/open_wearables_s3.json: SeaweedFS S3 account (matches the AWS_* values above)

Usage (repo root): python3 deploy/open-wearables/init-secrets.py
"""

import json
import os
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy/open-wearables/.env.example"
ENV_OUT = ROOT / "secrets/open_wearables.env"
S3_OUT = ROOT / "secrets/open_wearables_s3.json"


def _generate(key: str) -> str:
    if key == "AWS_ACCESS_KEY_ID":
        return "ow" + secrets.token_hex(9)
    if key == "SECRET_KEY":
        return secrets.token_urlsafe(64)
    # Alphanumeric only, to avoid escaping in URLs / DSNs
    return secrets.token_hex(24)


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m:
            values[m.group(1)] = m.group(2)
    return values


def main() -> None:
    ENV_OUT.parent.mkdir(exist_ok=True)
    if ENV_OUT.exists():
        print(f"已存在，跳过：{ENV_OUT.relative_to(ROOT)}")
    else:
        lines = []
        for line in TEMPLATE.read_text().splitlines():
            m = re.match(r"^([A-Z0-9_]+)=__GENERATE__$", line)
            lines.append(f"{m.group(1)}={_generate(m.group(1))}" if m else line)
        _write_private(ENV_OUT, "\n".join(lines) + "\n")
        print(f"已生成：{ENV_OUT.relative_to(ROOT)}")

    if S3_OUT.exists():
        print(f"已存在，跳过：{S3_OUT.relative_to(ROOT)}")
    else:
        env = _read_env(ENV_OUT)
        config = {
            "identities": [
                {
                    "name": "open-wearables",
                    "credentials": [
                        {"accessKey": env["AWS_ACCESS_KEY_ID"], "secretKey": env["AWS_SECRET_ACCESS_KEY"]}
                    ],
                    "actions": ["Admin", "Read", "List", "Tagging", "Write"],
                }
            ]
        }
        _write_private(S3_OUT, json.dumps(config, indent=2) + "\n")
        print(f"已生成：{S3_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
