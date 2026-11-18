"""Apple Watch device tokens for hold-to-talk (docs/watch-app-plan.md 2.2, 2.3, 4.5; migration 1190).

The watch has no sign-in screen. The signed-in iPhone app asks for a one-time code for its watch
(POST /v1/account/watch-code), hands it over WatchConnectivity (sendMessage / transferUserInfo, never the application
context), and the watch exchanges it for its own device token (POST /v1/web/login-code {code, client: "watch"}), which
it keeps in its own Keychain. Records (quick meals, water, sets, undo) still go through the phone; only the AI calls
(voice -> text, Jarvis) go from the watch straight to the server.

Why the watch token never reaches more than the phone that asked for it:
- Its scope `watch` is restricted (auth.RESTRICTED_SCOPES): api.current_user, the Today page login, the upload endpoints
  and the remote MCP all refuse it. Only the voice endpoints accept it (ai/api.py: POST /v1/assistant/transcribe,
  POST /v1/assistant/chat, POST /v1/assistant/chat/confirm, GET /v1/assistant/chat/{id}/events), and there only for the
  conversations its watch started (ai_conversations.client = 'watch').
- Its read / write scopes are the asking phone token's, never more: a read-only phone gets a read-only watch.
- Only an API token (Bearer) of an active account may ask: not a cookie session, not a restricted token (AI client,
  upload-only, another watch). A watch cannot mint another watch.
- It hangs off that phone token (parent_token_id) and is checked against it on every request
  (auth.user_for_watch_token): signing out on the phone, revoking that token by any path, or deleting the account ends
  the watch at once. A new code from the same phone replaces that phone's earlier watch token (one watch per sign-in).
- The user sees and revokes it: GET /v1/account/watch-tokens, DELETE /v1/account/watch-tokens/{id}.
"""
import re

import psycopg

from .. import auth
from ..web import login_codes
from . import params as P

NAME_PREFIX = "watch:"


class Refused(Exception):
    """The caller may not ask for a watch code; `code` is the language-neutral reason the app shows its own text for."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def device_label(device: str | None) -> str:
    label = re.sub(r"[^\w .\-]", "", device or "")[:P.WATCH_DEVICE_MAX_LEN].strip()
    return label or P.WATCH_DEVICE_DEFAULT


def mint_code(conn: psycopg.Connection, viewer: dict, device: str | None = None) -> dict:
    """A one-time code (P.WATCH_CODE_MINUTES) the phone hands to its watch: the asking token's read / write, nothing more.
    The phone's earlier unused watch codes are void: only the newest one works."""
    if (viewer.get("status") or "active") != "active":
        raise Refused("account_pending")
    held = set(viewer.get("token_scopes") or [])
    if viewer.get("token_id") is None or viewer.get("auth") in ("session", "watch") or held & set(auth.RESTRICTED_SCOPES):
        raise Refused("bearer_only")
    scopes = [s for s in ("read", "write") if s in held]
    if not scopes:
        raise Refused("no_scope")
    conn.execute("""UPDATE web_login_codes SET expires_at = now()
                    WHERE user_id = %s AND parent_token_id = %s AND purpose = %s AND used_at IS NULL AND expires_at > now()""",
                 (viewer["id"], viewer["token_id"], login_codes.PURPOSE_WATCH))
    code = login_codes.create(conn, viewer["id"], device_label(device), scopes=scopes, minutes=P.WATCH_CODE_MINUTES,
                              purpose=login_codes.PURPOSE_WATCH, parent_token_id=viewer["token_id"])
    return {"code": code, "expires_in": P.WATCH_CODE_MINUTES * 60, "scopes": scopes}


def redeem(conn: psycopg.Connection, code: str) -> tuple[str, dict] | None:
    """A valid watch code -> (the watch's new device token, its identity as auth.user_for_watch_token gives it); else None.
    The code is used up; the phone token must still be live; that phone's earlier watch token ends."""
    if len(login_codes.normalize(code)) != login_codes.LENGTH:
        return None
    row = conn.execute(
        """UPDATE web_login_codes c SET used_at = now()
           WHERE c.code_sha256 = %s AND c.used_at IS NULL AND c.expires_at > now() AND c.purpose = %s
             AND EXISTS (SELECT 1 FROM api_tokens p WHERE p.id = c.parent_token_id AND p.user_id = c.user_id
                         AND p.revoked_at IS NULL AND (p.expires_at IS NULL OR p.expires_at > now())
                         AND NOT (p.scopes && %s::text[]))
           RETURNING c.id, c.user_id, c.scopes, c.label, c.parent_token_id""",
        (login_codes._sha(code), login_codes.PURPOSE_WATCH, auth.RESTRICTED_SCOPES)).fetchone()
    if row is None:
        return None
    conn.execute("""UPDATE api_tokens SET revoked_at = now()
                    WHERE parent_token_id = %s AND user_id = %s AND revoked_at IS NULL""",
                 (row["parent_token_id"], row["user_id"]))
    scopes = [auth.WATCH_SCOPE, *(s for s in ("read", "write") if s in row["scopes"])]
    token = auth.create_token(conn, row["user_id"], NAME_PREFIX + device_label(row["label"]), scopes,
                              parent_token_id=row["parent_token_id"])
    conn.execute("""UPDATE web_login_codes SET token_id = (SELECT id FROM api_tokens WHERE token_sha256 = %s)
                    WHERE id = %s""", (auth.hash_token(token), row["id"]))
    user = auth.user_for_watch_token(conn, token)
    return None if user is None else (token, user)


def list_watches(conn: psycopg.Connection, user_id: int, phone_token_id: int | None = None) -> list[dict]:
    """The user's live watch tokens (their phone token live too), oldest first; this_phone = made from phone_token_id."""
    rows = conn.execute(
        """SELECT t.id, t.name, t.scopes, t.created_at, t.last_used_at, t.parent_token_id FROM api_tokens t
           JOIN api_tokens p ON p.id = t.parent_token_id AND p.user_id = t.user_id
           WHERE t.user_id = %s AND %s = ANY(t.scopes) AND t.revoked_at IS NULL
             AND p.revoked_at IS NULL AND (p.expires_at IS NULL OR p.expires_at > now())
           ORDER BY t.id""", (user_id, auth.WATCH_SCOPE)).fetchall()
    return [{"id": r["id"], "device": r["name"].removeprefix(NAME_PREFIX),
             "scopes": [s for s in r["scopes"] if s != auth.WATCH_SCOPE], "created_at": r["created_at"],
             "last_used_at": r["last_used_at"], "this_phone": phone_token_id is not None and r["parent_token_id"] == phone_token_id}
            for r in rows]


def revoke(conn: psycopg.Connection, user_id: int, token_id: int) -> bool:
    """End one of the user's own watch tokens; False when there is no such live watch token of theirs."""
    return conn.execute("""UPDATE api_tokens SET revoked_at = now()
                           WHERE id = %s AND user_id = %s AND %s = ANY(scopes) AND revoked_at IS NULL RETURNING id""",
                        (token_id, user_id, auth.WATCH_SCOPE)).fetchone() is not None


def end_for_phone(conn: psycopg.Connection, user_id: int, phone_token_id: int) -> None:
    """The phone signed out: its watch token and unused watch codes end with it (auth.user_for_watch_token would refuse
    them anyway once the phone token is revoked; this keeps the rows saying so)."""
    conn.execute("""UPDATE api_tokens SET revoked_at = now()
                    WHERE parent_token_id = %s AND user_id = %s AND revoked_at IS NULL""", (phone_token_id, user_id))
    conn.execute("""UPDATE web_login_codes SET expires_at = now()
                    WHERE parent_token_id = %s AND user_id = %s AND used_at IS NULL AND expires_at > now()""",
                 (phone_token_id, user_id))
