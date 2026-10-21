#!/usr/bin/env python3
"""CI guard for the app's example build config (apps/*/config/defaults.example.json).

The real server addresses live only in the git-ignored config/defaults.json that the builder gets privately; the
example in the repo must point at example.com and leave the server lock off (self-hosters need the editable field).
Fails when an address is not example.com (or a subdomain of it), when W2W_LOCK_SERVER is not "false", or when a real
config/defaults.json is tracked."""
import json
import subprocess
import sys
from urllib.parse import urlsplit


def tracked(pattern: str) -> list[str]:
    return subprocess.run(["git", "ls-files", pattern], capture_output=True, text=True, check=True).stdout.split()


def main() -> int:
    errors = [f"{p}: real build config must not be tracked" for p in tracked("*config/defaults.json")]
    examples = tracked("*config/defaults.example.json")
    if not examples:
        errors.append("no config/defaults.example.json found")
    for path in examples:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for key, value in data.items():
            if not isinstance(value, str):
                errors.append(f"{path}: {key} must be a string")
            elif "://" in value:
                host = (urlsplit(value).hostname or "").lower()
                if host != "example.com" and not host.endswith(".example.com"):
                    errors.append(f"{path}: {key} must point at example.com")
        if data.get("W2W_LOCK_SERVER") != "false":
            errors.append(f'{path}: W2W_LOCK_SERVER must be "false" in the example')
    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
