"""One API token per user; viewing someone else's data requires a shares check first."""
import hashlib
import secrets

import psycopg

TOKEN_PREFIX = "be_"
# A token with this scope is an AI-client-only token (ai/tokens.py): usable only for the remote MCP (/mcp); normal /v1 endpoints and the Today page login reject it.
MCP_SCOPE = "mcp"
# Upload-only token of the Watch2Whoop app (rawdata/ingest.py): usable only for POST /v1/ingest/*; it cannot read anything,
# and normal /v1 endpoints and the Today page login reject it. Minted by the logged-in Today page (POST /v1/web/ingest-token).
INGEST_SCOPE = "ingest"
# Tokens with one of these scopes only work on their own endpoint
RESTRICTED_SCOPES = [MCP_SCOPE, INGEST_SCOPE]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(conn: psycopg.Connection, user_id: int, name: str, scopes: list[str]) -> str:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    conn.execute("INSERT INTO api_tokens (user_id, name, token_sha256, scopes) VALUES (%s, %s, %s, %s)",
                 (user_id, name, hash_token(token), scopes))
    return token


def list_tokens(conn: psycopg.Connection, user_id: int) -> list[dict]:
    return conn.execute("""SELECT id, name, scopes, created_at, last_used_at, revoked_at FROM api_tokens
                           WHERE user_id = %s ORDER BY id""", (user_id,)).fetchall()


def revoke_tokens(conn: psycopg.Connection, user_id: int, name: str | None = None, everything: bool = False) -> dict:
    """Revoke a user's live API tokens: by exact name, or all of them (everything=True, which also voids the user's unused
    one-time login codes, revokes their reusable activation codes and ends their OAuth connections with all refresh tokens).
    Today page sessions made from a revoked token stop working at once (web/session.py checks it).
    AI-client tokens (scope mcp) are left alone unless everything=True."""
    if not name and not everything:
        raise ValueError("give a token name or everything=True")
    if everything:
        n = conn.execute("""UPDATE api_tokens SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL
                            RETURNING id""", (user_id,)).fetchall()
        codes = conn.execute("""UPDATE web_login_codes SET expires_at = now() WHERE user_id = %s AND used_at IS NULL
                                AND expires_at > now() RETURNING id""", (user_id,)).fetchall()
        activation = conn.execute("""UPDATE activation_codes SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL
                                     RETURNING id""", (user_id,)).fetchall()
        # OAuth connections (ai/oauth.py): revoking the access tokens alone is not enough, a refresh token would mint a new one
        from .ai import oauth
        grants = [r["id"] for r in conn.execute("SELECT id FROM oauth_grants WHERE user_id = %s AND revoked_at IS NULL",
                                                 (user_id,))]
        for g in grants:
            oauth.revoke_grant(conn, g, "admin")
        return {"tokens": len(n), "login_codes": len(codes), "activation_codes": len(activation), "oauth_connections": len(grants)}
    n = conn.execute("""UPDATE api_tokens SET revoked_at = now() WHERE user_id = %s AND name = %s AND revoked_at IS NULL
                        RETURNING id""", (user_id, name)).fetchall()
    return {"tokens": len(n), "login_codes": 0}


def user_for_token(conn: psycopg.Connection, token: str) -> dict | None:
    row = conn.execute(
        """UPDATE api_tokens SET last_used_at = now()
           WHERE token_sha256 = %s AND revoked_at IS NULL AND NOT (scopes && %s::text[])
           RETURNING user_id, scopes""", (hash_token(token), RESTRICTED_SCOPES)).fetchone()
    if row is None:
        return None
    user = conn.execute("SELECT * FROM users WHERE id = %s", (row["user_id"],)).fetchone()
    return {**user, "token_scopes": row["scopes"]}


def user_for_ingest_token(conn: psycopg.Connection, token: str) -> dict | None:
    """Identity for POST /v1/ingest/*: the app's account token (scope write) or its upload-only token (scope ingest)."""
    row = conn.execute(
        """UPDATE api_tokens SET last_used_at = now()
           WHERE token_sha256 = %s AND revoked_at IS NULL AND NOT (%s = ANY(scopes))
             AND ('write' = ANY(scopes) OR %s = ANY(scopes))
           RETURNING id, user_id, scopes""", (hash_token(token), MCP_SCOPE, INGEST_SCOPE)).fetchone()
    if row is None:
        return None
    user = conn.execute("SELECT * FROM users WHERE id = %s", (row["user_id"],)).fetchone()
    return {**user, "token_id": row["id"], "token_scopes": row["scopes"]}


# What a shares row grants right now: the hand-made grant while it has not expired, plus what common circles add
# (friends/store.py keeps circle_scopes). Use as `{EFFECTIVE_SCOPES.format(s="s")}` with the shares alias.
EFFECTIVE_SCOPES = ("((CASE WHEN {s}.expires_at IS NULL OR {s}.expires_at > now() THEN {s}.scopes ELSE '{{}}'::text[] END)"
                    " || {s}.circle_scopes)")
# A scope that a wider scope also grants: whoever may see someone's food may see whether they met their goals
IMPLIED_BY = {"nutrition_summary": ("nutrition",)}


def can_view(conn: psycopg.Connection, viewer_id: int, owner_id: int, scope: str) -> bool:
    if viewer_id == owner_id:
        return True
    return conn.execute(
        f"""SELECT 1 FROM shares s WHERE s.owner_id = %s AND s.viewer_id = %s
            AND {EFFECTIVE_SCOPES.format(s="s")} && %s::text[]""",
        (owner_id, viewer_id, [scope, *IMPLIED_BY.get(scope, ())])).fetchone() is not None
