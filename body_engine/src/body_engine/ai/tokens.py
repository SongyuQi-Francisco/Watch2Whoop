"""MCP tokens for AI clients (Claude Code, Codex CLI, Gemini CLI...).

- Same table as ordinary API tokens, api_tokens, with "mcp" in scopes: {mcp, read} is read-only, {mcp, read, write} can write.
- Only usable on the remote MCP endpoint /mcp; ordinary /v1 endpoints and Today page login reject it (auth.MCP_SCOPE), so even if leaked it can only do what MCP tools can,
  and there is one per client, revocable on its own (Today page "Me -> AI settings" or body-engine ai-token-revoke).
- Name = mcp:<client>, used directly as the audit channel (e.g. mcp:claude-code).
- OAuth access tokens (ai/oauth.py) are rows of the same kind with expires_at and oauth_grant_id: accepted here until they
  expire, but listed and capped as connections (oauth.list_grants), not as tokens.
"""
import re

import psycopg

from .. import auth, obs
from ..i18n import L

CLIENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
MAX_ACTIVE = 10


def create(conn: psycopg.Connection, user_id: int, client: str, read_only: bool = False) -> dict:
    client = (client or "").strip().lower()
    if not CLIENT_RE.match(client):
        raise ValueError(L("客户端名只能用小写字母、数字和 -，例 claude-code、codex、gemini-cli",
                           "Client names use lower-case letters, digits and - only, e.g. claude-code, codex, gemini-cli"))
    n = conn.execute("""SELECT count(*) AS n FROM api_tokens WHERE user_id = %s AND %s = ANY(scopes)
                        AND revoked_at IS NULL AND oauth_grant_id IS NULL""", (user_id, auth.MCP_SCOPE)).fetchone()["n"]
    if n >= MAX_ACTIVE:
        raise ValueError(L(f"每人最多 {MAX_ACTIVE} 个有效的 AI token，先吊销不用的",
                           f"At most {MAX_ACTIVE} active AI tokens per person; revoke the ones you no longer use"))
    scopes = [auth.MCP_SCOPE, "read"] + ([] if read_only else ["write"])
    token = auth.create_token(conn, user_id, f"mcp:{client}", scopes)
    row = conn.execute("SELECT id, name, scopes, created_at FROM api_tokens WHERE token_sha256 = %s",
                       (auth.hash_token(token),)).fetchone()
    return {**row, "token": token}


def list_tokens(conn: psycopg.Connection, user_id: int) -> list[dict]:
    return conn.execute(
        """SELECT id, name, scopes, created_at, last_used_at, revoked_at FROM api_tokens
           WHERE user_id = %s AND %s = ANY(scopes) AND oauth_grant_id IS NULL ORDER BY revoked_at IS NOT NULL, id DESC""",
        (user_id, auth.MCP_SCOPE)).fetchall()


def revoke(conn: psycopg.Connection, user_id: int, token_id: int) -> bool:
    return conn.execute(
        """UPDATE api_tokens SET revoked_at = now() WHERE id = %s AND user_id = %s AND %s = ANY(scopes)
           AND revoked_at IS NULL AND oauth_grant_id IS NULL RETURNING id""", (token_id, user_id, auth.MCP_SCOPE)).fetchone() is not None


def user_for_mcp_token(conn: psycopg.Connection, token: str) -> dict | None:
    """Identity for the remote MCP: only tokens with the mcp scope that are not revoked. Returns the user + token_id / token_name / token_scopes."""
    row = conn.execute(
        """UPDATE api_tokens SET last_used_at = now()
           WHERE token_sha256 = %s AND revoked_at IS NULL AND %s = ANY(scopes)
             AND (expires_at IS NULL OR expires_at > now())
           RETURNING id, user_id, name, scopes, oauth_grant_id""", (auth.hash_token(token), auth.MCP_SCOPE)).fetchone()
    if row is None:
        return None
    if row["oauth_grant_id"] is not None:   # an OAuth connection: its "last used" in AI settings
        conn.execute("UPDATE oauth_grants SET last_used_at = now() WHERE id = %s", (row["oauth_grant_id"],))
    user = conn.execute("SELECT * FROM users WHERE id = %s", (row["user_id"],)).fetchone()
    obs.note_user(row["user_id"])
    return {**user, "token_id": row["id"], "token_name": row["name"], "token_scopes": row["scopes"]}
