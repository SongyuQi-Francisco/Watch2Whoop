"""Beta invite codes: the admin issues them in batches, a signed-in `pending` account redeems one to become `active`.

- 10 characters from the login-code alphabet (no 0 O 1 I, ~50 bits); users may type lower case, spaces or dashes.
  Only the sha256 is stored plus the last 3 characters (`hint`) to tell codes apart in listings. The plaintext exists
  once, in the output of `body-engine invite-code batch` (CSV).
- Single use by default; `--max-uses M` lets one code open M accounts (e.g. a code posted in a group). A batch can expire
  (`--days`) and can be revoked as a whole; single codes can be revoked too.
- Redeeming is one atomic UPDATE (uses < max_uses), so two people racing for the last use cannot both get in.
"""
from __future__ import annotations

import hashlib
import secrets

import psycopg

from ..web import login_codes

ALPHABET = login_codes.ALPHABET
LENGTH = 10
MAX_COUNT = 1000
MAX_DAYS = 365


def normalize(code: str) -> str:
    return login_codes.normalize(code)


def _sha(code: str) -> str:
    return hashlib.sha256(normalize(code).encode()).hexdigest()


def pretty(code: str) -> str:
    return f"{code[:5]}-{code[5:]}"


def create_batch(conn: psycopg.Connection, count: int, label: str, days: int | None = None, max_uses: int = 1,
                 created_by: str | None = None) -> dict:
    """Create a batch; returns {batch_id, label, expires_at, max_uses, codes: [plaintext…]} (plaintext only this once)."""
    if not 1 <= count <= MAX_COUNT:
        raise ValueError(f"数量 1–{MAX_COUNT}")
    if days is not None and not 1 <= days <= MAX_DAYS:
        raise ValueError(f"有效期 1–{MAX_DAYS} 天，或不填（不过期）")
    if not 1 <= max_uses <= 10_000:
        raise ValueError("每码可用次数 ≥ 1")
    label = (label or "").strip()[:60]
    if not label:
        raise ValueError("要给一个标签（这批发给谁 / 哪个渠道）")
    batch = conn.execute(
        """INSERT INTO invite_batches (label, created_by, expires_at, max_uses)
           VALUES (%s, %s, CASE WHEN %s::int IS NULL THEN NULL ELSE now() + make_interval(days => %s::int) END, %s)
           RETURNING id, expires_at""", (label, (created_by or "")[:60] or None, days, days, max_uses)).fetchone()
    codes = []
    while len(codes) < count:
        code = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        row = conn.execute("""INSERT INTO invite_codes (batch_id, code_sha256, hint, max_uses) VALUES (%s, %s, %s, %s)
                              ON CONFLICT (code_sha256) DO NOTHING RETURNING id""",
                           (batch["id"], _sha(code), code[-3:], max_uses)).fetchone()
        if row:
            codes.append(code)
    return {"batch_id": batch["id"], "label": label, "expires_at": batch["expires_at"], "max_uses": max_uses, "codes": codes}


def list_batches(conn: psycopg.Connection) -> list[dict]:
    """Every batch with: codes, total uses / capacity, codes still usable, state."""
    return conn.execute(
        """SELECT b.id, b.label, b.created_by, b.created_at, b.expires_at, b.max_uses, b.revoked_at,
                  count(c.id) AS codes, coalesce(sum(c.uses), 0) AS uses, coalesce(sum(c.max_uses), 0) AS capacity,
                  count(c.id) FILTER (WHERE c.revoked_at IS NULL AND c.uses < c.max_uses
                                      AND b.revoked_at IS NULL AND (b.expires_at IS NULL OR b.expires_at > now())) AS free_codes,
                  CASE WHEN b.revoked_at IS NOT NULL THEN 'revoked'
                       WHEN b.expires_at IS NOT NULL AND b.expires_at <= now() THEN 'expired' ELSE 'active' END AS state
           FROM invite_batches b LEFT JOIN invite_codes c ON c.batch_id = b.id
           GROUP BY b.id ORDER BY b.id""").fetchall()


def list_codes(conn: psycopg.Connection, batch_id: int) -> list[dict]:
    """The codes of one batch (hint only, never the code) with who redeemed them and when."""
    return conn.execute(
        """SELECT c.id, c.hint, c.uses, c.max_uses, c.revoked_at,
                  coalesce(json_agg(json_build_object(
                      'user_id', r.user_id, 'name', u.display_name, 'redeemed_at', r.redeemed_at,
                      'email', (SELECT i.email FROM user_identities i WHERE i.user_id = r.user_id ORDER BY i.id LIMIT 1))
                    ORDER BY r.redeemed_at) FILTER (WHERE r.id IS NOT NULL), '[]') AS redeemed
           FROM invite_codes c LEFT JOIN invite_redemptions r ON r.code_id = c.id LEFT JOIN users u ON u.id = r.user_id
           WHERE c.batch_id = %s GROUP BY c.id ORDER BY c.id""", (batch_id,)).fetchall()


def revoke(conn: psycopg.Connection, batch_id: int | None = None, code_id: int | None = None) -> int:
    """Stop a whole batch or one code (accounts already opened stay). Returns how many rows changed."""
    if batch_id is not None:
        return len(conn.execute("UPDATE invite_batches SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL RETURNING id",
                                (batch_id,)).fetchall())
    if code_id is not None:
        return len(conn.execute("UPDATE invite_codes SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL RETURNING id",
                                (code_id,)).fetchall())
    raise ValueError("give batch_id or code_id")


def redeem(conn: psycopg.Connection, user_id: int, code: str) -> bool:
    """Use one invite code for a pending user: True → the user is active and queued for provisioning.
    An already active user is left alone (True, no code consumed). Any invalid / used up / expired / revoked code → False."""
    user = conn.execute("SELECT status FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
    if user is None or user["status"] == "disabled":
        return False
    if user["status"] == "active":
        return True
    if len(normalize(code)) != LENGTH:
        return False
    row = conn.execute(
        """UPDATE invite_codes c SET uses = c.uses + 1 FROM invite_batches b
           WHERE c.code_sha256 = %s AND c.batch_id = b.id AND c.revoked_at IS NULL AND b.revoked_at IS NULL
             AND (b.expires_at IS NULL OR b.expires_at > now()) AND c.uses < c.max_uses
           RETURNING c.id""", (_sha(code),)).fetchone()
    if row is None:
        return False
    conn.execute("INSERT INTO invite_redemptions (code_id, user_id) VALUES (%s, %s)", (row["id"], user_id))
    conn.execute("UPDATE users SET status = 'active' WHERE id = %s", (user_id,))
    conn.execute("INSERT INTO user_provisioning (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    return True
