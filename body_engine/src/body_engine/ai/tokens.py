"""MCP tokens for AI clients (Claude Code, Codex CLI, Gemini CLI...).

- Same table as ordinary API tokens, api_tokens, with "mcp" in scopes: {mcp, read} is read-only, {mcp, read, write} can write.
- Only usable on the remote MCP endpoint /mcp; ordinary /v1 endpoints and Today page login reject it (auth.MCP_SCOPE), so even if leaked it can only do what MCP tools can,
  and there is one per client, revocable on its own (Today page "Me -> AI settings" or body-engine ai-token-revoke).
- Name = mcp:<client>, used directly as the audit channel (e.g. mcp:claude-code).
"""
import re

import psycopg

from .. import auth

CLIENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
MAX_ACTIVE = 10


def create(conn: psycopg.Connection, user_id: int, client: str, read_only: bool = False) -> dict:
    client = (client or "").strip().lower()
    if not CLIENT_RE.match(client):
        raise ValueError("客户端名只能用小写字母、数字和 -，例 claude-code、codex、gemini-cli")
    n = conn.execute("""SELECT count(*) AS n FROM api_tokens WHERE user_id = %s AND %s = ANY(scopes)
                        AND revoked_at IS NULL""", (user_id, auth.MCP_SCOPE)).fetchone()["n"]
    if n >= MAX_ACTIVE:
        raise ValueError(f"每人最多 {MAX_ACTIVE} 个有效的 AI token，先吊销不用的")
    scopes = [auth.MCP_SCOPE, "read"] + ([] if read_only else ["write"])
    token = auth.create_token(conn, user_id, f"mcp:{client}", scopes)
    row = conn.execute("SELECT id, name, scopes, created_at FROM api_tokens WHERE token_sha256 = %s",
                       (auth.hash_token(token),)).fetchone()
    return {**row, "token": token}


def list_tokens(conn: psycopg.Connection, user_id: int) -> list[dict]:
    return conn.execute(
        """SELECT id, name, scopes, created_at, last_used_at, revoked_at FROM api_tokens
           WHERE user_id = %s AND %s = ANY(scopes) ORDER BY revoked_at IS NOT NULL, id DESC""",
        (user_id, auth.MCP_SCOPE)).fetchall()


def revoke(conn: psycopg.Connection, user_id: int, token_id: int) -> bool:
    return conn.execute(
        """UPDATE api_tokens SET revoked_at = now() WHERE id = %s AND user_id = %s AND %s = ANY(scopes)
           AND revoked_at IS NULL RETURNING id""", (token_id, user_id, auth.MCP_SCOPE)).fetchone() is not None


def user_for_mcp_token(conn: psycopg.Connection, token: str) -> dict | None:
    """Identity for the remote MCP: only tokens with the mcp scope that are not revoked. Returns the user + token_id / token_name / token_scopes."""
    row = conn.execute(
        """UPDATE api_tokens SET last_used_at = now()
           WHERE token_sha256 = %s AND revoked_at IS NULL AND %s = ANY(scopes)
           RETURNING id, user_id, name, scopes""", (auth.hash_token(token), auth.MCP_SCOPE)).fetchone()
    if row is None:
        return None
    user = conn.execute("SELECT * FROM users WHERE id = %s", (row["user_id"],)).fetchone()
    return {**user, "token_id": row["id"], "token_name": row["name"], "token_scopes": row["scopes"]}
