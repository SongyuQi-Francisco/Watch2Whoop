#!/usr/bin/env python3
"""Generates the "setup info" for the Watch2Whoop app: server address + one-time OW invitation code + one-time Today page login code,
output as a setup link / QR code (a family member scans it with the iPhone camera and the app is set up and logged in automatically).

    # First use by a family member: create the user in OW, issue an OW invitation code and a Today page login code, print the QR code
    python3 deploy/open-wearables/invite.py --create-user "Mom" --body-user-id 3 --label "Mom iPhone" \\
        --today-url https://body.example.com --ow-url https://sync.example.com --qr /tmp/mom-setup.png
    # Existing OW user (Body Engine users.ow_user_id)
    python3 deploy/open-wearables/invite.py --ow-user-id <UUID> --body-user-id 1 --today-url … --ow-url … --copy

- No long-lived token: the OW invitation code is single-use (expiry days are OW USER_INVITATION_CODE_EXPIRE_DAYS; generating again voids the old code),
  the Today page login code is single-use and valid for 72 hours by default (on redeem the server issues this device its own token; revoking it logs this device out).
  Treat the QR code / link like a password: send it only to the person it is for; it stops working once used.
- The OW part calls OW own service inside the OW backend container (no portal password); the login code uses the Body Engine `login-code` command:
  default `docker exec -i body-body-engine-1 body-engine`; on Ali `--body-cmd deploy/ali/engine.sh`;
  Mac development `--body-cmd "uv run --directory body_engine body-engine"`.
- The QR code needs `qrencode` (Mac: brew install qrencode; Ali: apt install qrencode); without it only the link is printed, use --copy to put it on the clipboard,
  then tap 「粘贴设置信息」 ("Paste setup info") in the iPhone app.
- Standard library only, runs with the system python3 (3.6 compatible: the Ali system Python is 3.6).

Reusable alternative: activation links. The link above works once (both codes are single-use). For a link a person can open
again on a new phone or after a reinstall, until you revoke it, use Body Engine activation codes (web/activation.py):

    body-engine activation-code create --user-id 3 --label Mom      # prints the code and <BODY_PUBLIC_URL>/app/activate.html#<code>
    body-engine activation-code list [--user-id 3] / revoke --id N

Each use mints fresh one-time codes server-side. For the sync invitation code Body Engine needs OW's signing secret:
`invite.py --export-signing-file secrets/ow_invite_signing.json` writes it (mode 600) from the OW container; mount it read-only
into the body-engine container only and point BODY_OW_INVITE_SIGNING_FILE at it. That file is OW's master secret.
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
from subprocess import PIPE
import sys
import urllib.parse
import uuid
from typing import Dict, Optional

OW_CONTAINER = "backend__open-wearables"
BODY_CMD = "docker exec -i body-body-engine-1 body-engine"

# Runs inside the OW container (/root_project, OW 0.9.0). Parameters are passed via environment variables; prints one line of JSON as the result.
_IN_CONTAINER = r"""
import json, os
from uuid import UUID
from app.database import SessionLocal
from app.models import Developer
from app.schemas.model_crud.user_management import UserCreate
from app.services.user_invitation_code_service import user_invitation_code_service
from app.services.user_service import user_service

db = SessionLocal()
try:
    out = {}
    name = os.environ.get("W2W_CREATE_USER")
    if name:
        u = user_service.create(db, UserCreate(first_name=name))
        out["user_id"] = str(u.id)
    else:
        out["user_id"] = os.environ["W2W_OW_USER_ID"]
    dev = db.query(Developer).order_by(Developer.created_at).first()
    if dev is None:
        raise SystemExit("OW 里还没有开发者账号（先登录一次门户）")
    code = user_invitation_code_service.generate(db, UUID(out["user_id"]), dev.id)
    db.commit()
    out["code"] = code.code
    out["expires_at"] = code.expires_at.isoformat()
    print(json.dumps(out))
finally:
    db.close()
"""


def ow_invite(container: str, env: Dict[str, str]) -> dict:
    args = ["docker", "exec", "-i"]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    args += [container, "python", "-"]
    p = subprocess.run(args, input=_IN_CONTAINER, stdout=PIPE, stderr=PIPE, universal_newlines=True)
    if p.returncode != 0:
        sys.exit(f"OW 邀请码生成失败：{p.stderr.strip()[-800:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


# Prints OW's JWT signing settings + the first developer's id (the same developer ow_invite uses), for activation links.
_SIGNING_IN_CONTAINER = r"""
import json
from app.config import settings
from app.database import SessionLocal
from app.models import Developer

db = SessionLocal()
try:
    dev = db.query(Developer).order_by(Developer.created_at).first()
    if dev is None:
        raise SystemExit("OW 里还没有开发者账号（先登录一次门户）")
    print(json.dumps({"secret_key": settings.secret_key, "algorithm": settings.algorithm, "developer_id": str(dev.id)}))
finally:
    db.close()
"""


def export_signing_file(container: str, path: str) -> None:
    """Write OW's signing secret for Body Engine activation links (BODY_OW_INVITE_SIGNING_FILE), mode 600."""
    p = subprocess.run(["docker", "exec", "-i", container, "python", "-"], input=_SIGNING_IN_CONTAINER,
                       stdout=PIPE, stderr=PIPE, universal_newlines=True)
    if p.returncode != 0:
        sys.exit(f"读取 OW 签名配置失败：{p.stderr.strip()[-800:]}")
    data = json.loads(p.stdout.strip().splitlines()[-1])
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
        f.write("\n")
    os.chmod(path, 0o600)
    print(f"已写入 {path}（OW 的主密钥：权限 600，只读挂进 body-engine 容器，设 BODY_OW_INVITE_SIGNING_FILE）")


def body_login_code(body_cmd: str, user_id: int, label: Optional[str], hours: int) -> str:
    args = shlex.split(body_cmd) + ["login-code", "--user-id", str(user_id), "--hours", str(hours)]
    if label:
        args += ["--label", label]
    p = subprocess.run(args, stdout=PIPE, stderr=PIPE, universal_newlines=True)
    if p.returncode != 0:
        sys.exit(f"今天页登录码生成失败：{p.stderr.strip()[-800:]}")
    return p.stdout.strip().splitlines()[-1].strip()


def setup_link(today: Optional[str], ow: Optional[str], code: Optional[str], login: Optional[str]) -> str:
    q = {k: v for k, v in (("today", today), ("ow", ow), ("code", code), ("login", login)) if v}
    return "watch2whoop://setup?" + urllib.parse.urlencode(q)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    who = ap.add_mutually_exclusive_group()
    who.add_argument("--ow-user-id", help="UUID of an existing user in OW (issues an OW invitation code)")
    who.add_argument("--create-user", metavar="NAME", help="create a new user (name) in OW first, then issue it an invitation code")
    ap.add_argument("--body-user-id", type=int, help="Body Engine user ID: also issues a one-time Today page login code")
    ap.add_argument("--label", help="who / which device the login code is for, e.g. Mom's iPhone")
    ap.add_argument("--hours", type=int, default=72, help="login code validity (hours, default 72)")
    ap.add_argument("--today-url", help="Today page address, e.g. https://body.example.com")
    ap.add_argument("--ow-url", help="sync server (OW API) address, e.g. https://sync.example.com")
    ap.add_argument("--ow-container", default=OW_CONTAINER)
    ap.add_argument("--body-cmd", default=BODY_CMD, help=f"how to run Body Engine commands (default: {BODY_CMD})")
    ap.add_argument("--qr", metavar="PNG", help="also save the QR code as a PNG (needs qrencode)")
    ap.add_argument("--copy", action="store_true", help="put the setup link on the Mac clipboard (pbcopy)")
    ap.add_argument("--export-signing-file", metavar="PATH",
                    help="only write OW's signing secret (for Body Engine activation links) to PATH, mode 600, and exit")
    a = ap.parse_args()
    if a.export_signing_file:
        export_signing_file(a.ow_container, a.export_signing_file)
        return
    if not (a.ow_user_id or a.create_user or a.body_user_id):
        ap.error("至少要 --ow-user-id / --create-user（同步）或 --body-user-id（今天页登录）之一")

    code = None
    if a.ow_user_id or a.create_user:
        if a.ow_user_id:
            uuid.UUID(a.ow_user_id)  # format check
            res = ow_invite(a.ow_container, {"W2W_OW_USER_ID": a.ow_user_id})
        else:
            res = ow_invite(a.ow_container, {"W2W_CREATE_USER": a.create_user})
            print(f"已在 OW 建 user：{res['user_id']}（记进 Body Engine：users.ow_user_id）")
        code = res["code"]
        print(f"OW 邀请码 {code}（{res['expires_at'][:16]} 前有效，用一次就失效）")
    login = None
    if a.body_user_id:
        login = body_login_code(a.body_cmd, a.body_user_id, a.label, a.hours)
        print(f"今天页登录码 {login}（{a.hours} 小时内有效，用一次就失效）")

    link = setup_link(a.today_url, a.ow_url, code, login)
    print("\n设置链接（家人 iPhone 相机扫下面的二维码；或把链接发给本人，在 app 里「粘贴设置信息」）：\n" + link + "\n")
    if shutil.which("qrencode"):
        subprocess.run(["qrencode", "-t", "ANSIUTF8", "-m", "2", link], check=False)
        if a.qr:
            subprocess.run(["qrencode", "-o", a.qr, "-s", "8", "-m", "2", link], check=True)
            print(f"二维码已存：{a.qr}")
    else:
        print("（没装 qrencode，不打印二维码：brew install qrencode / apt install qrencode）")
    if a.copy:
        subprocess.run(["pbcopy"], input=link, universal_newlines=True, check=True)
        print("已放进剪贴板。")


if __name__ == "__main__":
    main()
