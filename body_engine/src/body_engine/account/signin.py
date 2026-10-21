"""Self-service sign-in: e-mail + one-time 6-digit code, and Sign in with Apple (native iOS identity token).

- E-mail codes: 6 digits, valid CODE_MINUTES (10) minutes, MAX_ATTEMPTS (5) wrong guesses, then dead; a new code voids
  the older ones for that address. Only sha256(salt + code) is stored. Rate limits live in account/api.py.
- Apple: the app sends the identity token Apple gave it. We verify the RS256 signature against Apple's published keys
  (APPLE_JWKS_URL, cached, refetched on an unknown `kid`), iss = https://appleid.apple.com, aud ∈ BODY_APPLE_AUDIENCES
  (the app bundle ID(s), comma separated), exp, and — when the app sends its raw nonce — sha256(nonce) = the `nonce`
  claim. No Apple secret / key file is needed for this (that is only for web sign-in with a Services ID, later).
- Identities: (provider, subject) → user. An unknown identity makes a new `pending` user. Existing accounts get their
  identities bound explicitly (account/identities.py: the admin's `identity-link`, Me → 「登录方式」, or an existing
  account's activation / login code entered on the invite step). The one automatic case is in the Apple endpoint
  (identities.apple_account_for): Apple-verified, non-relay e-mail equal to exactly one active account's bound e-mail.
  Disabled users cannot sign in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.request

import psycopg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .. import auth

log = logging.getLogger(__name__)

CODE_MINUTES = 10
MAX_ATTEMPTS = 5
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
JWKS_TTL_SECONDS = 3600
JWKS_MIN_REFETCH_SECONDS = 60
CLOCK_SKEW_SECONDS = 120


class SignInError(Exception):
    """Invalid code / token. The message is for logs only; clients get a generic answer."""


# ---------------------------------------------------------------------------------------------------- e-mail codes

def normalize_email(email: str) -> str | None:
    e = (email or "").strip().lower()
    return e if len(e) <= 254 and EMAIL_RE.match(e) else None


def _hash(salt: str, code: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def new_email_code(conn: psycopg.Connection, email: str) -> str:
    """Create a code for a normalised address (older unused codes stop working) and return the plaintext."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    conn.execute("UPDATE email_login_codes SET expires_at = now() WHERE email = %s AND used_at IS NULL AND expires_at > now()",
                 (email,))
    conn.execute("""INSERT INTO email_login_codes (email, code_sha256, salt, expires_at)
                    VALUES (%s, %s, %s, now() + make_interval(mins => %s))""", (email, _hash(salt, code), salt, CODE_MINUTES))
    return code


def check_email_code(conn: psycopg.Connection, email: str, code: str) -> bool:
    """True once for the right code. A wrong guess counts against the newest live code; after MAX_ATTEMPTS it is dead."""
    code = re.sub(r"\s", "", code or "")
    row = conn.execute("""SELECT id, code_sha256, salt, attempts FROM email_login_codes
                          WHERE email = %s AND used_at IS NULL AND expires_at > now()
                          ORDER BY created_at DESC LIMIT 1 FOR UPDATE""", (email,)).fetchone()
    if row is None or row["attempts"] >= MAX_ATTEMPTS:
        return False
    if not (re.fullmatch(r"\d{6}", code) and hmac.compare_digest(_hash(row["salt"], code), row["code_sha256"])):
        conn.execute("UPDATE email_login_codes SET attempts = attempts + 1 WHERE id = %s", (row["id"],))
        return False
    conn.execute("UPDATE email_login_codes SET used_at = now() WHERE id = %s", (row["id"],))
    return True


def purge_old_codes(conn: psycopg.Connection) -> int:
    return len(conn.execute("DELETE FROM email_login_codes WHERE created_at < now() - interval '1 day' RETURNING id").fetchall())


# ---------------------------------------------------------------------------------------------------- Apple

def apple_audiences() -> list[str]:
    return [a.strip() for a in (os.environ.get("BODY_APPLE_AUDIENCES") or "").split(",") if a.strip()]


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _fetch_jwks() -> dict:
    with urllib.request.urlopen(APPLE_JWKS_URL, timeout=10) as r:
        return json.load(r)


_jwks_lock = threading.Lock()
_jwks: dict = {"keys": {}, "at": 0.0}


def reset_jwks_cache() -> None:
    with _jwks_lock:
        _jwks.update(keys={}, at=0.0)


def _apple_key(kid: str) -> rsa.RSAPublicKey:
    now = time.monotonic()
    with _jwks_lock:
        stale = now - _jwks["at"] > JWKS_TTL_SECONDS
        unknown = kid not in _jwks["keys"] and now - _jwks["at"] > JWKS_MIN_REFETCH_SECONDS
        if stale or unknown or not _jwks["keys"]:
            try:
                data = _fetch_jwks()
                keys = {}
                for k in data.get("keys", []):
                    if k.get("kty") == "RSA" and k.get("kid"):
                        keys[k["kid"]] = rsa.RSAPublicNumbers(int.from_bytes(_b64d(k["e"]), "big"),
                                                              int.from_bytes(_b64d(k["n"]), "big")).public_key()
                _jwks.update(keys=keys, at=now)
            except Exception as e:  # noqa: BLE001  keep the old keys if Apple cannot be reached
                log.warning("apple: fetching JWKS failed: %s", e)
                if not _jwks["keys"]:
                    raise SignInError("cannot fetch Apple keys") from e
        key = _jwks["keys"].get(kid)
    if key is None:
        raise SignInError("unknown Apple key id")
    return key


def verify_apple_token(token: str, audiences: list[str], raw_nonce: str | None = None, now: float | None = None) -> dict:
    """Verify a Sign in with Apple identity token; returns its claims or raises SignInError."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        head, claims, sig = json.loads(_b64d(head_b64)), json.loads(_b64d(body_b64)), _b64d(sig_b64)
    except (ValueError, AttributeError) as e:
        raise SignInError("malformed token") from e
    if head.get("alg") != "RS256":
        raise SignInError("unexpected alg")
    try:
        _apple_key(str(head.get("kid"))).verify(sig, f"{head_b64}.{body_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as e:
        raise SignInError("bad signature") from e
    now = time.time() if now is None else now
    if claims.get("iss") != APPLE_ISSUER:
        raise SignInError("wrong issuer")
    aud = claims.get("aud")
    if not audiences or (aud not in audiences if isinstance(aud, str) else not set(aud or []) & set(audiences)):
        raise SignInError("wrong audience")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - CLOCK_SKEW_SECONDS:
        raise SignInError("expired")
    if isinstance(claims.get("iat"), (int, float)) and claims["iat"] > now + CLOCK_SKEW_SECONDS:
        raise SignInError("issued in the future")
    if not claims.get("sub"):
        raise SignInError("no subject")
    if raw_nonce is not None or claims.get("nonce") is not None:
        expected = hashlib.sha256((raw_nonce or "").encode()).hexdigest()
        if raw_nonce is None or not hmac.compare_digest(str(claims.get("nonce", "")), expected):
            raise SignInError("nonce mismatch")
    return claims


def apple_email(claims: dict) -> tuple[str | None, bool]:
    email = normalize_email(claims.get("email") or "")
    verified = str(claims.get("email_verified", "")).lower() == "true"
    return email, verified


# ---------------------------------------------------------------------------------------------------- identities → users

def find_or_create_user(conn: psycopg.Connection, provider: str, subject: str, email: str | None,
                        email_verified: bool, name: str | None = None) -> tuple[dict, bool]:
    """(user row, newly created). Raises SignInError for a disabled account."""
    ident = conn.execute("SELECT id, user_id FROM user_identities WHERE provider = %s AND subject = %s",
                         (provider, subject)).fetchone()
    new_user = False
    if ident is not None:
        user_id = ident["user_id"]
        conn.execute("""UPDATE user_identities SET last_login_at = now(), email = COALESCE(%s, email),
                        verified_at = COALESCE(verified_at, CASE WHEN %s THEN now() END) WHERE id = %s""",
                     (email, email_verified, ident["id"]))
    else:
        display = (name or "").strip()[:40] or (email.split("@")[0][:30] if email else "") or "新用户"
        user_id = conn.execute("INSERT INTO users (display_name, role, status) VALUES (%s, 'member', 'pending') RETURNING id",
                               (display,)).fetchone()["id"]
        new_user = True
        conn.execute("""INSERT INTO user_identities (user_id, provider, subject, email, verified_at, last_login_at)
                        VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END, now())""",
                     (user_id, provider, subject, email, email_verified))
    user = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    if user["status"] == "disabled":
        raise SignInError("account disabled")
    return user, new_user


def device_token(conn: psycopg.Connection, user_id: int, client: str, device: str | None) -> str:
    """A fresh API token for this sign-in (one per device; revoking it signs that device out)."""
    label = re.sub(r"[^\w .\-]", "", device or "")[:40].strip() or client
    return auth.create_token(conn, user_id, f"{client}:signin:{label}", ["read", "write"])
