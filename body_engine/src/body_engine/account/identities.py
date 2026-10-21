"""Sign-in identities of existing accounts: which e-mail / Apple ID logs in to which account.

Accounts the admin created (onboarding, activation codes) had no identity, so signing in with e-mail or Apple made a new
pending account. Now:
- the admin binds an e-mail to an account (`body-engine identity-link --user-id N --email x`, also `user-add --email`,
  `activation-code create --email`); signing in with a code sent to that address then opens that account;
- a signed-in user binds more ways themselves (Me → 「登录方式」): another e-mail (proved with a code) or their Apple ID
  (a fresh Apple identity token from the app).
One e-mail / Apple ID belongs to one account: binding one that is already elsewhere is refused (the admin can move it
with `--move`). Accounts are never merged by matching e-mail addresses (Apple may give a private relay address).
"""
from __future__ import annotations

import psycopg

from . import signin


class IdentityConflict(Exception):
    """The identity already belongs to another account (its id is in .user_id)."""

    def __init__(self, user_id: int, status: str | None = None):
        super().__init__(f"already bound to user {user_id}")
        self.user_id, self.status = user_id, status


def _link(conn: psycopg.Connection, user_id: int, provider: str, subject: str, email: str | None, verified: bool,
          move: bool = False) -> str:
    """'linked' / 'already' / 'moved'. Raises IdentityConflict, ValueError (no such user)."""
    user = conn.execute("SELECT id, status FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None:
        raise ValueError(f"no user {user_id}")
    row = conn.execute("""SELECT i.id, i.user_id, u.status FROM user_identities i JOIN users u ON u.id = i.user_id
                          WHERE i.provider = %s AND i.subject = %s FOR UPDATE OF i""", (provider, subject)).fetchone()
    if row is not None and row["user_id"] == user_id:
        if verified:
            conn.execute("UPDATE user_identities SET verified_at = COALESCE(verified_at, now()) WHERE id = %s", (row["id"],))
        return "already"
    if row is not None:
        if not move:
            raise IdentityConflict(row["user_id"], row["status"])
        conn.execute("""UPDATE user_identities SET user_id = %s, email = COALESCE(%s, email),
                        verified_at = CASE WHEN %s THEN now() ELSE verified_at END WHERE id = %s""",
                     (user_id, email, verified, row["id"]))
        return "moved"
    conn.execute("""INSERT INTO user_identities (user_id, provider, subject, email, verified_at)
                    VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END)""", (user_id, provider, subject, email, verified))
    return "linked"


def link_email(conn: psycopg.Connection, user_id: int, email: str, verified: bool = False, move: bool = False) -> str:
    """Bind an e-mail address to an account (admin: verified=False, the first code sign-in proves it)."""
    norm = signin.normalize_email(email)
    if norm is None:
        raise ValueError("邮箱格式不对 / invalid e-mail")
    return _link(conn, user_id, "email", norm, norm, verified, move)


def link_apple(conn: psycopg.Connection, user_id: int, claims: dict) -> str:
    """Bind a verified Apple identity (token claims) to the signed-in account. Never moves one from another account."""
    email, verified = signin.apple_email(claims)
    return _link(conn, user_id, "apple", claims["sub"], email, verified)


def unlink(conn: psycopg.Connection, identity_id: int | None = None, user_id: int | None = None,
           email: str | None = None) -> int:
    """Remove one identity (by id, or the user's e-mail identity). Returns rows removed."""
    if identity_id is not None:
        return len(conn.execute("DELETE FROM user_identities WHERE id = %s RETURNING id", (identity_id,)).fetchall())
    norm = signin.normalize_email(email or "")
    if user_id is None or norm is None:
        raise ValueError("give --id, or --user-id with --email")
    return len(conn.execute("DELETE FROM user_identities WHERE user_id = %s AND provider = 'email' AND subject = %s RETURNING id",
                            (user_id, norm)).fetchall())


def list_identities(conn: psycopg.Connection, user_id: int | None = None) -> list[dict]:
    """Admin listing: every identity (or one user's), with the account's name and status."""
    return conn.execute(
        """SELECT i.id, i.user_id, u.display_name, u.status, i.provider, i.email, i.verified_at, i.last_login_at, i.created_at
           FROM user_identities i JOIN users u ON u.id = i.user_id
           WHERE %(u)s::int IS NULL OR i.user_id = %(u)s::int ORDER BY i.user_id, i.id""", {"u": user_id}).fetchall()


def methods_of(conn: psycopg.Connection, user_id: int) -> list[dict]:
    """What the user sees under Me → 「登录方式」: provider + address (Apple: the address Apple shares, may be a relay)."""
    return conn.execute("""SELECT id, provider, email, last_login_at FROM user_identities WHERE user_id = %s ORDER BY id""",
                        (user_id,)).fetchall()
