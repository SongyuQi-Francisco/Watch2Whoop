"""Sign-in identities of existing accounts: which e-mail / Apple ID logs in to which account.

Goal: once an account is activated, every later sign-in with Apple or an e-mail code goes straight in.
- Admin-created accounts (onboarding, activation codes) get their e-mail bound by the admin
  (`body-engine identity-link --user-id N --email x`, also `user-add --email`, `activation-code create --email`).
- A signed-in user binds more ways under Me → 「登录方式」: another e-mail (proved with a code) or their Apple ID.
- Someone who signed in with an unbound e-mail / Apple ID lands on the invite step of a new pending account; entering
  their existing account's activation code or login code there (claim_with_code) moves that identity to the existing
  account and drops the empty pending one.
- Sign in with Apple, unknown Apple ID (apple_account_for): joined to an existing account only when Apple says the e-mail
  is verified, it is not a private relay address, and it is exactly the bound e-mail of exactly one active account.
  Anything else (relay, no match, ambiguous) is a new pending account; nothing is ever merged by a guess.
One e-mail / Apple ID belongs to one account: binding one that is already elsewhere is refused (the admin can move it
with `--move`). Every change is written to identity_events (masked addresses only).
"""
from __future__ import annotations

import psycopg

from . import signin

APPLE_RELAY_DOMAIN = "privaterelay.appleid.com"


class IdentityConflict(Exception):
    """The identity already belongs to another account (its id is in .user_id)."""

    def __init__(self, user_id: int, status: str | None = None):
        super().__init__(f"already bound to user {user_id}")
        self.user_id, self.status = user_id, status


def mask(provider: str, subject: str, email: str | None) -> str:
    addr = subject if provider == "email" else email
    if addr and "@" in addr:
        local, domain = addr.rsplit("@", 1)
        host, _, tld = domain.rpartition(".")
        return f"{local[:1]}***@{(host or tld)[:1]}***" + (f".{tld}" if host else "")
    return f"Apple ID ···{subject[-4:]}"


def audit(conn: psycopg.Connection, kind: str, user_id: int | None, provider: str, subject: str, email: str | None,
          actor: str, from_user_id: int | None = None) -> None:
    conn.execute("""INSERT INTO identity_events (user_id, from_user_id, provider, hint, kind, actor)
                    VALUES (%s, %s, %s, %s, %s, %s)""", (user_id, from_user_id, provider, mask(provider, subject, email), kind, actor))


def _link(conn: psycopg.Connection, user_id: int, provider: str, subject: str, email: str | None, verified: bool,
          move: bool = False, actor: str = "user", kind: str | None = None) -> str:
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
        audit(conn, kind or "move", user_id, provider, subject, email, actor, row["user_id"])
        if provider == "apple":
            from . import apple_tokens
            apple_tokens.move(conn, subject, user_id)
        return "moved"
    conn.execute("""INSERT INTO user_identities (user_id, provider, subject, email, verified_at)
                    VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END)""", (user_id, provider, subject, email, verified))
    audit(conn, kind or "link", user_id, provider, subject, email, actor)
    return "linked"


def link_email(conn: psycopg.Connection, user_id: int, email: str, verified: bool = False, move: bool = False,
               actor: str = "admin-cli") -> str:
    """Bind an e-mail address to an account (admin: verified=False, the first code sign-in proves it)."""
    norm = signin.normalize_email(email)
    if norm is None:
        raise ValueError("邮箱格式不对 / invalid e-mail")
    return _link(conn, user_id, "email", norm, norm, verified, move, actor)


def link_apple(conn: psycopg.Connection, user_id: int, claims: dict, actor: str = "user") -> str:
    """Bind a verified Apple identity (token claims) to the signed-in account. Never moves one from another account."""
    email, verified = signin.apple_email(claims)
    return _link(conn, user_id, "apple", claims["sub"], email, verified, actor=actor)


def is_relay(email: str | None) -> bool:
    return bool(email) and email.rsplit("@", 1)[-1] == APPLE_RELAY_DOMAIN


def apple_account_for(conn: psycopg.Connection, email: str | None, verified: bool) -> int | None:
    """The existing account an unknown Apple ID may join: Apple-verified, not a private relay address, and exactly the
    bound e-mail of exactly one active account (counting every identity that carries that address). Else None."""
    if not email or not verified or is_relay(email):
        return None
    rows = conn.execute(
        """SELECT i.user_id, u.status, bool_or(i.provider = 'email' AND i.subject = %(e)s) AS has_email_identity
           FROM user_identities i JOIN users u ON u.id = i.user_id
           WHERE (i.provider = 'email' AND i.subject = %(e)s) OR lower(i.email) = %(e)s
           GROUP BY i.user_id, u.status""", {"e": email}).fetchall()
    if len(rows) != 1 or rows[0]["status"] != "active" or not rows[0]["has_email_identity"]:
        return None
    return rows[0]["user_id"]


def claim_with_code(conn: psycopg.Connection, pending_id: int, target_id: int) -> int:
    """A pending sign-up entered an existing account's activation / login code: move its identities to that account and
    delete the pending account (it has nothing yet). Returns how many identities moved."""
    pending = conn.execute("SELECT status FROM users WHERE id = %s FOR UPDATE", (pending_id,)).fetchone()
    target = conn.execute("SELECT status FROM users WHERE id = %s", (target_id,)).fetchone()
    if pending is None or pending["status"] != "pending" or target is None or target["status"] != "active" or pending_id == target_id:
        raise ValueError("not a pending → active claim")
    moved = 0
    for i in conn.execute("SELECT provider, subject, email, verified_at FROM user_identities WHERE user_id = %s ORDER BY id",
                          (pending_id,)).fetchall():
        _link(conn, target_id, i["provider"], i["subject"], i["email"], i["verified_at"] is not None,
              move=True, actor="sign-in", kind="claim_by_code")
        moved += 1
    from . import delete as account_delete
    account_delete._delete_rows(conn, pending_id)
    return moved


def unlink(conn: psycopg.Connection, identity_id: int | None = None, user_id: int | None = None,
           email: str | None = None, actor: str = "admin-cli") -> int:
    """Remove one identity (by id, or the user's e-mail identity). Returns rows removed."""
    if identity_id is not None:
        rows = conn.execute("DELETE FROM user_identities WHERE id = %s RETURNING user_id, provider, subject, email",
                            (identity_id,)).fetchall()
    else:
        norm = signin.normalize_email(email or "")
        if user_id is None or norm is None:
            raise ValueError("give --id, or --user-id with --email")
        rows = conn.execute("""DELETE FROM user_identities WHERE user_id = %s AND provider = 'email' AND subject = %s
                               RETURNING user_id, provider, subject, email""", (user_id, norm)).fetchall()
    for r in rows:
        audit(conn, "unlink", r["user_id"], r["provider"], r["subject"], r["email"], actor)
    return len(rows)


def list_identities(conn: psycopg.Connection, user_id: int | None = None) -> list[dict]:
    """Admin listing: every identity (or one user's), with the account's name and status."""
    return conn.execute(
        """SELECT i.id, i.user_id, u.display_name, u.status, i.provider, i.email, i.verified_at, i.last_login_at, i.created_at
           FROM user_identities i JOIN users u ON u.id = i.user_id
           WHERE %(u)s::int IS NULL OR i.user_id = %(u)s::int ORDER BY i.user_id, i.id""", {"u": user_id}).fetchall()
