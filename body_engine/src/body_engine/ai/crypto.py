"""Encrypted storage for API keys: AES-256-GCM, master key in secrets/ai_master_key (32 bytes, base64, file mode 600).

- Ciphertext format: b"v1" + 12-byte nonce + ciphertext and authentication tag. Additional data (AAD) = "ai_providers:user:<user_id>":
  the ciphertext can only be opened under the user it belongs to; copying A's ciphertext into B's row fails to decrypt (second layer of isolation).
- The master key is read from the file only when needed; never in environment variables, git or logs. Rotation: add a v2 prefix later and re-encrypt the old ones on read.
- In the container the master key is mounted as a docker secret at /run/secrets/ai_master_key (compose.yaml), and BODY_AI_MASTER_KEY_FILE points there.
"""
import base64
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import REPO_ROOT

VERSION = b"v1"


class MasterKeyMissing(RuntimeError):
    pass


def key_path() -> Path:
    return Path(os.environ.get("BODY_AI_MASTER_KEY_FILE", REPO_ROOT / "secrets" / "ai_master_key"))


def init_master_key(path: Path | None = None) -> tuple[Path, bool]:
    """Generate a master key if there is none (mode 600); an existing one is untouched. Returns (path, newly created?)."""
    path = path or key_path()
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(base64.b64encode(secrets.token_bytes(32)).decode() + "\n")
    return path, True


def _key() -> bytes:
    path = key_path()
    if not path.exists():
        raise MasterKeyMissing(f"服务器还没有 AI 主密钥（{path.name}）：管理员先运行 body-engine ai-key-init")
    key = base64.b64decode(path.read_text().strip())
    if len(key) != 32:
        raise MasterKeyMissing("AI 主密钥格式不对（应为 32 字节 base64）")
    return key


def _aad(user_id: int) -> bytes:
    return f"ai_providers:user:{int(user_id)}".encode()


def encrypt(user_id: int, plaintext: str) -> bytes:
    nonce = secrets.token_bytes(12)
    return VERSION + nonce + AESGCM(_key()).encrypt(nonce, plaintext.encode(), _aad(user_id))


def decrypt(user_id: int, blob: bytes) -> str:
    blob = bytes(blob)
    if blob[:2] != VERSION:
        raise ValueError("不认识的密文版本")
    return AESGCM(_key()).decrypt(blob[2:14], blob[14:], _aad(user_id)).decode()


def last4(secret: str) -> str:
    s = secret.strip()
    return s[-4:] if len(s) >= 8 else "****"
