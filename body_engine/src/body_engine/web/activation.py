"""Reusable activation codes: one long-lived link per person for the Watch2Whoop app (new phone, reinstall) until it is revoked.

The setup link the app understands (`watch2whoop://setup?today=…&ow=…&code=…&login=…`, see deploy/open-wearables/invite.py)
carries two single-use codes, so it cannot be handed out once and reused. An activation code sits in front of it:

- The admin gives the person `<BODY_PUBLIC_URL>/app/activate.html#<CODE>` (`body-engine activation-code create --user-id N`).
  The code lives in the URL fragment, so it never reaches the server in a URL or in access logs; the page POSTs it to
  `POST /v1/web/activate`, which answers with a freshly minted setup link, and the page opens it (the app takes over).
- Each use mints a new one-time Today page login code (login_codes.create, 1 hour, label `activation:<label>`) and, when
  configured, a new Open Wearables invitation code for users.ow_user_id (OW voids that user's older unused codes itself).
  If the user has no ow_user_id, or OW signing is not configured, or OW cannot be reached, the link has no `code`:
  the app accepts that and asks for the invitation code on its sync step.
- 16 characters from the login-code alphabet (no 0 O 1 I, about 80 bits); only the sha256 is stored. A code is valid until
  revoked, or until expires_at when created with --days. Every redemption counts `uses` and `last_used_at`.
- Any failure is a generic 401 and counts against the per-IP failure limit of web/login_limit.py (shared with token login).
  A successful activation does not clear that IP's failures: a code is reusable, so a holder of one valid code could
  otherwise reset the counter between guesses at other codes.

Minting OW invitation codes needs a developer JWT. OW signs those itself with its SECRET_KEY
(vendor/open-wearables/backend/app/utils/security.py create_access_token: {"exp", "sub": developer_id}, HS256).
Body Engine signs an identical short-lived (5 minute) token from the JSON file named by env BODY_OW_INVITE_SIGNING_FILE:

    {"secret_key": "<OW SECRET_KEY>", "algorithm": "HS256", "developer_id": "<UUID of an OW developer>"}

**That file holds Open Wearables' master secret**: anyone who has it can sign tokens as any OW developer (read every user's
data, create users, mint tokens). Keep it mode 600 under secrets/, mount it read-only into the body-engine container only
(never into the one-off job containers or the butler), and do not set the variable anywhere else. Without it activation
still works, just without the sync invitation code. `deploy/open-wearables/invite.py --export-signing-file` writes it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg

from ..config import settings
from . import login_codes

log = logging.getLogger(__name__)

ALPHABET = login_codes.ALPHABET
LENGTH = 16
MAX_DAYS = 3650
LOGIN_CODE_HOURS = 1
OW_JWT_TTL_SECONDS = 300
OW_TIMEOUT_SECONDS = 10
_HMAC = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}


def normalize(code: str) -> str:
    return login_codes.normalize(code)


def _sha(code: str) -> str:
    return hashlib.sha256(normalize(code).encode()).hexdigest()


def create(conn: psycopg.Connection, user_id: int, label: str | None = None, days: int | None = None) -> dict:
    """Issue an activation code; returns {id, code} (the plaintext is shown only this once). days=None: valid until revoked."""
    if days is not None and not 1 <= days <= MAX_DAYS:
        raise ValueError(f"有效期 1–{MAX_DAYS} 天，或不填（一直有效直到停用）")
    if conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone() is None:
        raise ValueError(f"没有用户 {user_id}")
    code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    row = conn.execute("""INSERT INTO activation_codes (user_id, code_sha256, label, expires_at)
                          VALUES (%s, %s, %s, CASE WHEN %s::int IS NULL THEN NULL ELSE now() + make_interval(days => %s::int) END)
                          RETURNING id""",
                       (user_id, _sha(code), (label or "")[:60] or None, days, days)).fetchone()
    return {"id": row["id"], "code": code}


def list_codes(conn: psycopg.Connection, user_id: int | None = None) -> list[dict]:
    """All activation codes (no plaintext, no hash), optionally for one user; `active` = usable right now."""
    return conn.execute(
        """SELECT id, user_id, label, created_at, expires_at, revoked_at, last_used_at, uses,
                  (revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())) AS active
           FROM activation_codes WHERE %s::int IS NULL OR user_id = %s::int ORDER BY id""",
        (user_id, user_id)).fetchall()


def revoke(conn: psycopg.Connection, code_id: int) -> bool:
    """Stop an activation code. Devices already set up stay signed in (revoke their app:activation:* token for that).
    `token-revoke --all` also revokes all of a user's activation codes (auth.revoke_tokens)."""
    return conn.execute("UPDATE activation_codes SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL RETURNING id",
                        (code_id,)).fetchone() is not None


# ---------------------------------------------------------------------------- Open Wearables invitation codes

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_jwt(payload: dict, secret_key: str, algorithm: str = "HS256") -> str:
    """Compact JWS with HMAC (the subset OW uses through python-jose), standard library only."""
    digest = _HMAC.get(algorithm)
    if digest is None:
        raise ValueError(f"unsupported OW JWT algorithm {algorithm!r} (HS256 / HS384 / HS512)")
    head = _b64url(json.dumps({"alg": algorithm, "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret_key.encode(), f"{head}.{body}".encode(), digest).digest()
    return f"{head}.{body}.{_b64url(sig)}"


def ow_signing() -> dict | None:
    """The OW signing config from BODY_OW_INVITE_SIGNING_FILE, or None when not configured / unreadable."""
    path = os.environ.get("BODY_OW_INVITE_SIGNING_FILE")
    if not path or not Path(path).is_file():
        return None
    try:
        cfg = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        log.warning("activation: BODY_OW_INVITE_SIGNING_FILE is not readable JSON")
        return None
    if not (isinstance(cfg, dict) and cfg.get("secret_key") and cfg.get("developer_id")):
        log.warning("activation: BODY_OW_INVITE_SIGNING_FILE needs secret_key and developer_id")
        return None
    return {"secret_key": str(cfg["secret_key"]), "algorithm": str(cfg.get("algorithm") or "HS256"),
            "developer_id": str(cfg["developer_id"])}


def _post_json(url: str, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=b"", method="POST", headers={"Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def mint_ow_invitation(ow_user_id: str, signing: dict) -> str:
    """POST {ow_url}/api/v1/users/{ow_user_id}/invitation-code with a 5-minute developer JWT; returns the 8-character code."""
    token = sign_jwt({"exp": int(time.time()) + OW_JWT_TTL_SECONDS, "sub": signing["developer_id"]},
                     signing["secret_key"], signing["algorithm"])
    url = f"{settings.ow_url.rstrip('/')}/api/v1/users/{urllib.parse.quote(ow_user_id, safe='')}/invitation-code"
    res = _post_json(url, {"Authorization": f"Bearer {token}"}, OW_TIMEOUT_SECONDS)
    code = res.get("code") if isinstance(res, dict) else None
    if not code:
        raise RuntimeError("OW response has no invitation code")
    return code


# ---------------------------------------------------------------------------- redeem

def setup_link(today: str | None, ow: str | None, code: str | None, login: str | None) -> str:
    """Same format as deploy/open-wearables/invite.py and the app's buildSetupLink."""
    q = {k: v for k, v in (("today", today), ("ow", ow), ("code", code), ("login", login)) if v}
    return "watch2whoop://setup?" + urllib.parse.urlencode(q)


def activate(conn: psycopg.Connection, code: str, user_agent: str | None = None,
             today_url: str | None = None) -> dict | None:
    """Redeem an activation code. Valid → {setup_link, sync, note}; anything else → None (the caller answers a generic 401).
    sync is False when the link carries no OW invitation code (note says why, for the admin; the app asks for the code later).
    user_agent is accepted for symmetry with login codes; the device token is only created when the app redeems the login code."""
    if len(normalize(code)) != LENGTH:
        return None
    row = conn.execute(
        """UPDATE activation_codes SET uses = uses + 1, last_used_at = now()
           WHERE code_sha256 = %s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())
           RETURNING id, user_id, label""", (_sha(code),)).fetchone()
    if row is None:
        return None
    login = login_codes.create(conn, row["user_id"], f"activation:{row['label'] or row['id']}", hours=LOGIN_CODE_HOURS)

    ow_code, note = None, None
    ow_user_id = conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (row["user_id"],)).fetchone()["ow_user_id"]
    signing = ow_signing()
    if not ow_user_id:
        note = "no_ow_user"
    elif signing is None:
        note = "ow_signing_not_configured"
    else:
        try:
            ow_code = mint_ow_invitation(ow_user_id, signing)
        except (urllib.error.URLError, OSError, ValueError, RuntimeError) as e:
            log.warning("activation %s: OW invitation code failed: %s", row["id"], e)
            note = "ow_unavailable"
    today = (os.environ.get("BODY_PUBLIC_URL") or today_url or "").rstrip("/") or None
    ow = (os.environ.get("BODY_OW_PUBLIC_URL") or "").rstrip("/") or None
    return {"setup_link": setup_link(today, ow, ow_code, login), "sync": ow_code is not None, "note": note}
