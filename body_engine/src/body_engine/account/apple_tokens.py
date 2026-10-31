"""Sign in with Apple refresh tokens: kept so that deleting an account can revoke the app's access to the person's Apple ID
(App Store Review Guideline 5.1.1(v); Apple's "Revoke tokens" REST API).

Flow
- Sign-in / binding: the app sends the `authorization_code` Apple gave it next to the identity token. After the identity
  token is verified, exchange_and_store() trades the code for tokens at APPLE_AUTH_BASE/auth/token and keeps only the
  refresh token, encrypted (ai/crypto.py AES-256-GCM, same master key; additional data = the Apple subject, so a row
  cannot be opened under another Apple ID). One row per Apple ID (`subject`); a later sign-in replaces it.
- Account deletion (account/delete.py) and the pending purge: revoke_for_user() posts each stored refresh token to
  APPLE_AUTH_BASE/auth/revoke, then deletes the rows. Never raises: anything that could not be revoked comes back as
  `leftovers` entries (system "apple") and is logged; the person can still remove the app under their Apple ID settings
  (Settings → Apple ID → Sign in with Apple).
- When an identity moves to another account (identities._link with move), its token row moves with it.

Configuration (all optional; without them nothing is exchanged and deletion reports `not_configured`):
  BODY_APPLE_SIGNIN_KEY_FILE   the Sign in with Apple private key (.p8), default secrets/apple_signin_key.p8
  BODY_APPLE_SIGNIN_KEY_ID     its Key ID (10 characters, from the Apple Developer portal)
  BODY_APPLE_TEAM_ID           the developer team ID
  BODY_APPLE_CLIENT_ID         the client_id for /auth/token and /auth/revoke; default = the identity token's audience
                               (the app's bundle ID, one of BODY_APPLE_AUDIENCES)
The client secret is an ES256 JWT {iss: team, iat, exp: +5 min, aud: https://appleid.apple.com, sub: client_id}
signed with the key. The code exchange happens once per sign-in; nothing else is sent to Apple.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import psycopg

from ..ai import crypto
from ..config import REPO_ROOT

log = logging.getLogger(__name__)

APPLE_AUTH_BASE = "https://appleid.apple.com"
CLIENT_SECRET_SECONDS = 300
HTTP_TIMEOUT = 10


@dataclass(frozen=True)
class Config:
    key_file: Path
    key_id: str
    team_id: str
    client_id: str | None


def load_config() -> Config | None:
    """The Sign in with Apple key settings, or None when any part is missing (then nothing is exchanged or revoked)."""
    key_file = Path(os.environ.get("BODY_APPLE_SIGNIN_KEY_FILE") or REPO_ROOT / "secrets" / "apple_signin_key.p8")
    key_id = (os.environ.get("BODY_APPLE_SIGNIN_KEY_ID") or "").strip()
    team_id = (os.environ.get("BODY_APPLE_TEAM_ID") or "").strip()
    if not (key_id and team_id and key_file.is_file()):
        return None
    return Config(key_file, key_id, team_id, (os.environ.get("BODY_APPLE_CLIENT_ID") or "").strip() or None)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def client_secret(cfg: Config, client_id: str, now: float | None = None) -> str:
    """ES256 JWT for Apple's token endpoints (raw r||s signature, as JWS wants)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    iat = int(now if now is not None else time.time())
    head = _b64(json.dumps({"alg": "ES256", "kid": cfg.key_id}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"iss": cfg.team_id, "iat": iat, "exp": iat + CLIENT_SECRET_SECONDS,
                              "aud": APPLE_AUTH_BASE, "sub": client_id}, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(cfg.key_file.read_bytes(), password=None)
    r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{head}.{claims}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


class AppleError(Exception):
    pass


def _post(path: str, form: dict) -> dict:
    """POST a form to Apple; the JSON answer ({} for an empty body). Raises AppleError on HTTP / network errors."""
    req = urllib.request.Request(APPLE_AUTH_BASE + path, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("error", "")
        except ValueError:
            detail = ""
        raise AppleError(f"{path} → {e.code} {detail}".strip()) from e
    except (urllib.error.URLError, OSError) as e:
        raise AppleError(f"{path}: {e}") from e
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError as e:
        raise AppleError(f"{path}: not JSON") from e


def _aad(subject: str) -> str:
    return f"apple_signin_tokens:subject:{subject}"


def _client_id(cfg: Config, claims_aud) -> str | None:
    if cfg.client_id:
        return cfg.client_id
    if isinstance(claims_aud, str):
        return claims_aud
    return claims_aud[0] if claims_aud else None


def exchange_and_store(conn: psycopg.Connection, user_id: int, claims: dict, code: str | None) -> str:
    """Trade a fresh authorization code for a refresh token and keep it (encrypted). Never raises: returns
    'stored' / 'no_code' / 'not_configured' / 'no_refresh_token' / 'failed' (the sign-in goes on either way)."""
    if not code:
        return "no_code"
    cfg = load_config()
    if cfg is None:
        return "not_configured"
    subject = claims["sub"]
    client_id = _client_id(cfg, claims.get("aud"))
    try:
        answer = _post("/auth/token", {"client_id": client_id, "client_secret": client_secret(cfg, client_id),
                                       "code": code, "grant_type": "authorization_code"})
        refresh = answer.get("refresh_token")
        if not refresh:
            return "no_refresh_token"
        blob = crypto.encrypt_with(_aad(subject), refresh)
    except (AppleError, crypto.MasterKeyMissing, OSError, ValueError) as e:
        log.warning("apple: exchanging the authorization code of user %s failed: %s", user_id, e)
        return "failed"
    conn.execute("""INSERT INTO apple_signin_tokens (subject, user_id, client_id, refresh_token_enc)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (subject) DO UPDATE SET user_id = EXCLUDED.user_id, client_id = EXCLUDED.client_id,
                      refresh_token_enc = EXCLUDED.refresh_token_enc, updated_at = now()""",
                 (subject, user_id, client_id, blob))
    return "stored"


def move(conn: psycopg.Connection, subject: str, user_id: int) -> None:
    """An Apple identity moved to another account: its stored token follows it."""
    conn.execute("UPDATE apple_signin_tokens SET user_id = %s, updated_at = now() WHERE subject = %s", (user_id, subject))


def revoke_for_user(conn: psycopg.Connection, user_id: int) -> list[dict]:
    """Revoke every Apple token kept for this account (its Apple identities), then drop the rows. Never raises; returns
    leftovers [{system: 'apple', id: '···1234', error}] for Apple IDs whose access could not be revoked."""
    subjects = {r["subject"] for r in conn.execute(
        "SELECT subject FROM user_identities WHERE user_id = %s AND provider = 'apple'", (user_id,)).fetchall()}
    rows = conn.execute("SELECT subject, client_id, refresh_token_enc FROM apple_signin_tokens WHERE user_id = %s",
                        (user_id,)).fetchall()
    subjects |= {r["subject"] for r in rows}
    if not subjects:
        return []
    by_subject = {r["subject"]: r for r in rows}
    cfg = load_config()
    leftovers = []
    for subject in sorted(subjects):
        hint = f"···{subject[-4:]}"
        row = by_subject.get(subject)
        if cfg is None:
            leftovers.append({"system": "apple", "id": hint, "error": "not_configured"})
            continue
        if row is None:
            leftovers.append({"system": "apple", "id": hint, "error": "no_refresh_token"})
            continue
        client_id = cfg.client_id or row["client_id"]
        try:
            token = crypto.decrypt_with(_aad(subject), row["refresh_token_enc"])
            _post("/auth/revoke", {"client_id": client_id, "client_secret": client_secret(cfg, client_id),
                                   "token": token, "token_type_hint": "refresh_token"})
        except Exception as e:  # noqa: BLE001  never stop the deletion; the admin sees it in the log
            leftovers.append({"system": "apple", "id": hint, "error": str(e)[:200] or type(e).__name__})
    for item in leftovers:   # not configured / never stored: a warning; Apple refused or unreachable: an error
        level = logging.WARNING if item["error"] in ("not_configured", "no_refresh_token") else logging.ERROR
        log.log(level, "account deletion of user %s: Apple ID %s not revoked: %s", user_id, item["id"], item["error"])
    with conn.transaction():
        conn.execute("DELETE FROM apple_signin_tokens WHERE user_id = %s OR subject = ANY(%s)", (user_id, sorted(subjects)))
    return leftovers
