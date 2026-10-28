"""One-time login codes: used by the Watch2Whoop app's "setup info" / QR code so a family member can scan once and log into the Today page.

- The long-lived API token never goes into the QR code: the code is single-use, expires after 72 hours by default, and only its sha256 is stored.
- On redemption (POST /v1/web/login-code) a new device-specific API token is created (named app:<label>, plaintext never leaves the server),
  then used to create a Today page session (exactly the same cookie as token login). Revoking that token = signing that device out.
- The code is 12 characters, alphabet without the easily confused 0 O 1 I (about 60 bits), so brute force is not realistic; any redemption failure is a 401 without saying why.
- Issue a code: `body-engine login-code --user-id N [--label 妈妈的iPhone] [--hours 72] [--scopes read,write]`,
  or deploy/open-wearables/invite.py --body-user-id N (generates the "setup info" together with the OW invite code).
"""
import hashlib
import secrets

import psycopg

from .. import auth
from . import session

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LENGTH = 12
DEFAULT_HOURS = 72
MAX_HOURS = 24 * 90   # 90 days: the App Review demo needs a code that lasts a whole TestFlight build; normal packs still default to 7 days
ALLOWED_SCOPES = {"read", "write"}


def normalize(code: str) -> str:
    return "".join(c for c in code.upper() if c not in " -\t\r\n")


def _sha(code: str) -> str:
    return hashlib.sha256(normalize(code).encode()).hexdigest()


def create(conn: psycopg.Connection, user_id: int, label: str | None = None, hours: int = DEFAULT_HOURS,
           scopes: list[str] | None = None) -> str:
    """Issue a one-time login code and return the plaintext (shown only this once)."""
    scopes = scopes or ["read", "write"]
    if not set(scopes) <= ALLOWED_SCOPES:
        raise ValueError(f"scopes 只能是 {sorted(ALLOWED_SCOPES)}")
    if not 1 <= hours <= MAX_HOURS:
        raise ValueError(f"有效期 1–{MAX_HOURS} 小时")
    if conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone() is None:
        raise ValueError(f"没有用户 {user_id}")
    code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    conn.execute("""INSERT INTO web_login_codes (user_id, code_sha256, scopes, label, expires_at)
                    VALUES (%s, %s, %s, %s, now() + make_interval(hours => %s))""",
                 (user_id, _sha(code), scopes, (label or "")[:60] or None, hours))
    return code


def redeem(conn: psycopg.Connection, code: str, user_agent: str | None = None) -> tuple[str, dict] | None:
    """If the code is valid, invalidate it, issue a device token, create a session, and return (plaintext session ID, user); otherwise None."""
    token = redeem_token(conn, code)
    return None if token is None else session.login(conn, token, user_agent)


def redeem_token(conn: psycopg.Connection, code: str) -> str | None:
    """If the code is valid, invalidate it and return a new device token (the native app keeps it in the Keychain and
    sends it as Bearer, #340 N8: it has no web view for the cookie); otherwise None."""
    if len(normalize(code)) != LENGTH:
        return None
    row = conn.execute(
        """UPDATE web_login_codes SET used_at = now()
           WHERE code_sha256 = %s AND used_at IS NULL AND expires_at > now()
           RETURNING id, user_id, scopes, label""", (_sha(code),)).fetchone()
    if row is None:
        return None
    token = auth.create_token(conn, row["user_id"], f"app:{row['label'] or 'login-code'}", list(row["scopes"]))
    conn.execute("""UPDATE web_login_codes SET token_id = (SELECT id FROM api_tokens WHERE token_sha256 = %s)
                    WHERE id = %s""", (auth.hash_token(token), row["id"]))
    return token
