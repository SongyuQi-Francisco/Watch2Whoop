#!/usr/bin/env python3
"""Checks an exported .shortcut for private information; must be run before committing (standard library + the macOS built-in aea / aa / openssl / plutil).

    python3 shortcuts/check_shortcut.py "shortcuts/Watch2Whoop Health Sync.shortcut" [--deny extra-keyword ...]

A signed .shortcut is an Apple Encrypted Archive (AEA1, signed but not encrypted): take the leaf certificate public key from SigningCertificateChain in the header →
`aea decrypt -sign-pub` → `aa extract` yields Shortcut.wflow (binary plist). The decoded plist is searched for:
- generic deny words (LAN IPs, .local, email suffixes, ...), personal deny words from secrets/shortcut_deny.txt (hostname, tailnet name, names,
    watch name, ..., one per line, not tracked by git) and those added with --deny;
- the plaintext of every token in secrets/health_upload_token_* (when secrets/ can be found in the repo);
- any http(s) address (only the placeholder YOUR-… and the <mac>.<tailnet> example in the import questions are allowed).
It also prints the import questions and the signing certificate subject (should be the anonymous certificate signed by Apple servers, not a personal Apple ID).
Exit code 1 on problems.
"""
import argparse
import plistlib
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Generic deny words; personal ones (hostname, tailnet name, names, device names, email) go in secrets/shortcut_deny.txt, one per line, not tracked by git
DENY = ["192.168.", "10.0.", "172.16.", ".local:", ".local/", "@gmail", "@icloud", "@qq.com", "@outlook", "Bearer "]
ALLOWED_URL = re.compile(r"https?://(YOUR-|&lt;mac&gt;|<mac>|www\.apple\.com/DTDs/)")


def extract(path: Path, tmp: Path) -> tuple[dict, str]:
    data = path.read_bytes()
    if data[:4] != b"AEA1":
        sys.exit(f"{path} 不是签名的快捷指令（开头不是 AEA1）")
    n = struct.unpack("<I", data[8:12])[0]
    auth = plistlib.loads(data[12:12 + n])
    (tmp / "leaf.der").write_bytes(auth["SigningCertificateChain"][0])
    subject = subprocess.run(["openssl", "x509", "-inform", "der", "-in", tmp / "leaf.der", "-noout", "-subject"],
                             capture_output=True, text=True, check=True).stdout.strip()
    pub = subprocess.run(["openssl", "x509", "-inform", "der", "-in", tmp / "leaf.der", "-pubkey", "-noout"],
                         capture_output=True, check=True).stdout
    (tmp / "pub.pem").write_bytes(pub)
    subprocess.run(["aea", "decrypt", "-i", path, "-o", tmp / "w.aar", "-sign-pub", tmp / "pub.pem"], check=True)
    (tmp / "out").mkdir()
    subprocess.run(["aa", "extract", "-i", tmp / "w.aar", "-d", tmp / "out"], check=True)
    wflow = next((tmp / "out").rglob("*.wflow"))
    return plistlib.loads(wflow.read_bytes()), subject


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shortcut", type=Path)
    ap.add_argument("--deny", nargs="*", default=[])
    ap.add_argument("--secrets-dir", default=ROOT / "secrets", help="point to the secrets/ of the main repo when running in a git worktree")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as t:
        wf, subject = extract(args.shortcut, Path(t))
    text = plistlib.dumps(wf, fmt=plistlib.FMT_XML).decode()
    deny = DENY + args.deny
    secrets_dir = Path(args.secrets_dir)
    if (secrets_dir / "shortcut_deny.txt").exists():
        deny += [x.strip() for x in (secrets_dir / "shortcut_deny.txt").read_text().splitlines() if x.strip()]
    for f in sorted(secrets_dir.glob("health_upload_token_*")) if secrets_dir.is_dir() else []:
        deny.append(f.read_text().strip())
    if not (secrets_dir / "shortcut_deny.txt").exists():
        print(f"⚠ 没有 {secrets_dir}/shortcut_deny.txt：只查了通用禁用词和地址")
    problems = [f"含有禁用词 {d[:6]}…" for d in deny if d and d.lower() in text.lower()]
    problems += [f"含有地址 {u}" for u in re.findall(r"https?://[^\s<\"']+", text) if not ALLOWED_URL.match(u)]
    if "O=Apple Inc." not in subject:
        problems.append(f"签名证书不是 Apple 服务器的：{subject}（导出时「对象」要选「任何人」）")
    print(f"签名：{subject}")
    print(f"动作数：{len(wf.get('WFWorkflowActions', []))}")
    for q in wf.get("WFWorkflowImportQuestions", []):
        print(f"导入问题：{q.get('ParameterKey')} — {q.get('Text')}")
    if not wf.get("WFWorkflowImportQuestions"):
        problems.append("没有导入问题：上传地址和 token 要设成导入问题")
    if problems:
        print("✗ " + "\n✗ ".join(problems))
        sys.exit(1)
    print("✓ 没有发现私人信息")


if __name__ == "__main__":
    main()
