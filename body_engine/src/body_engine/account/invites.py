"""Beta invite codes: the admin issues them in batches, a signed-in `pending` account redeems one to become `active`.

- 10 characters from the login-code alphabet (no 0 O 1 I, ~50 bits); users may type lower case, spaces or dashes.
  Only the sha256 is stored plus the last 3 characters (`hint`) to tell codes apart in listings. The plaintext exists
  once, in the output of `body-engine invite-code batch` (CSV).
- Single use by default; `--max-uses M` lets one code open M accounts (e.g. a code posted in a group). A batch can expire
  (`--days`) and can be revoked as a whole; single codes can be revoked too.
- Redeeming is one atomic UPDATE (uses < max_uses), so two people racing for the last use cannot both get in.
- One code, many people: `invite-code create --label X --uses N` is a batch of one code with max_uses N. Every
  redemption records the sign-in identities (Apple / e-mail) of the account as a hash plus a masked hint; an identity
  can redeem only once, ever (the record outlives the account), so one person cannot open several accounts with it.
- Referral codes: every active user can have one code of kind `referral` (owner = the user; shown on the Me page). It
  opens up to params.REFERRAL_MAX_USES accounts, at most params.REFERRAL_DAILY_REDEEMS in any 24 hours (soft cap: two
  redemptions racing for the last slot of a day may both pass; the total stays strict), never expires, and stops working
  when the admin revokes it, the inviter is disabled or deleted, or referrals are switched off (BODY_REFERRALS_ENABLED). Redeeming one records the inviter and writes a pending
  reward (params.REFERRAL_REWARD). The new user is invited to the circles the inviter owns (friends/store.py referral_invites; invite_redemptions.circle_id = the first one).
"""
from __future__ import annotations

import hashlib
import secrets

import psycopg

from ..web import login_codes
from . import params

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


def create_code(conn: psycopg.Connection, label: str, uses: int, days: int | None = None,
                created_by: str | None = None) -> dict:
    """One code that opens up to `uses` accounts (a batch of one): {batch_id, label, expires_at, max_uses, code}."""
    res = create_batch(conn, 1, label, days, uses, created_by)
    return {**{k: v for k, v in res.items() if k != "codes"}, "code": res["codes"][0]}


def mask_email(email: str | None) -> str:
    """j***@e***.com: enough for the admin to recognise a person, not enough to read the address."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return "***"
    local, domain = email.rsplit("@", 1)
    host, _, tld = domain.rpartition(".")
    return f"{local[:1]}***@{(host or tld)[:1]}***" + (f".{tld}" if host else "")


def identity_hint(provider: str, subject: str, email: str | None) -> str:
    if provider == "email":
        return mask_email(subject)
    return mask_email(email) if email else f"Apple ID ···{subject[-4:]}"


def _identity_sha(provider: str, subject: str) -> str:
    return hashlib.sha256(f"{provider}:{subject}".encode()).hexdigest()


# Per redemption: when, which account, who invited, the bound identities (masked). Used by both listings.
_REDEMPTIONS = """coalesce((SELECT json_agg(json_build_object(
        'user_id', r.user_id, 'name', u.display_name, 'redeemed_at', r.redeemed_at, 'inviter_user_id', r.inviter_user_id,
        'identities', coalesce((SELECT json_agg(json_build_object('provider', ri.provider, 'hint', ri.hint) ORDER BY ri.id)
                                FROM invite_redemption_identities ri WHERE ri.redemption_id = r.id), '[]'))
      ORDER BY r.redeemed_at, r.id)
    FROM invite_redemptions r LEFT JOIN users u ON u.id = r.user_id WHERE r.code_id = {code}), '[]')"""


def list_batches(conn: psycopg.Connection, kind: str | None = None) -> list[dict]:
    """Every batch with: codes, total uses / capacity / remaining, codes still usable, state, and every redemption with
    its bound identity (masked) and time. kind='referral': only users' referral codes (owner = who invited)."""
    return conn.execute(
        """SELECT b.id, b.label, b.created_by, b.created_at, b.expires_at, b.max_uses, b.revoked_at,
                  min(c.kind) AS kind, min(c.owner_user_id) AS owner_user_id,
                  (SELECT o.display_name FROM users o WHERE o.id = min(c.owner_user_id)) AS owner_name,
                  count(c.id) AS codes, coalesce(sum(c.uses), 0) AS uses, coalesce(sum(c.max_uses), 0) AS capacity,
                  CASE WHEN b.revoked_at IS NOT NULL OR (b.expires_at IS NOT NULL AND b.expires_at <= now()) THEN 0
                       ELSE coalesce(sum(c.max_uses - c.uses) FILTER (WHERE c.revoked_at IS NULL), 0) END AS remaining,
                  count(c.id) FILTER (WHERE c.revoked_at IS NULL AND c.uses < c.max_uses
                                      AND b.revoked_at IS NULL AND (b.expires_at IS NULL OR b.expires_at > now())) AS free_codes,
                  CASE WHEN b.revoked_at IS NOT NULL THEN 'revoked'
                       WHEN b.expires_at IS NOT NULL AND b.expires_at <= now() THEN 'expired' ELSE 'active' END AS state,
                  coalesce((SELECT json_agg(x ORDER BY x->>'redeemed_at') FROM invite_codes c2,
                            json_array_elements(""" + _REDEMPTIONS.format(code="c2.id") + """) x
                            WHERE c2.batch_id = b.id), '[]') AS redemptions
           FROM invite_batches b LEFT JOIN invite_codes c ON c.batch_id = b.id
           GROUP BY b.id HAVING %(kind)s::text IS NULL OR min(c.kind) = %(kind)s::text ORDER BY b.id""", {"kind": kind}).fetchall()


def list_codes(conn: psycopg.Connection, batch_id: int) -> list[dict]:
    """The codes of one batch (hint only, never the code) with who redeemed them, with which identity, and when."""
    return conn.execute(
        """SELECT c.id, c.hint, c.kind, c.owner_user_id, c.uses, c.max_uses, c.revoked_at,
                  """ + _REDEMPTIONS.format(code="c.id") + """ AS redeemed
           FROM invite_codes c WHERE c.batch_id = %s ORDER BY c.id""", (batch_id,)).fetchall()


def referral_code(conn: psycopg.Connection, user_id: int) -> dict:
    """The user's own referral code, created on first use: {code, uses, max_uses, remaining, joined, expires_at}.
    Only active users. A code the admin revoked is replaced by a new one only when the admin allows it: here it is not
    (revoked = this user's inviting is stopped), so a user whose code was revoked gets ValueError("revoked")."""
    user = conn.execute("SELECT status FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
    if user is None or user["status"] != "active":
        raise ValueError("pending")
    q = """SELECT c.id, c.display_code AS code, c.uses, c.max_uses, b.expires_at,
                  (SELECT count(*) FROM invite_redemptions r WHERE r.code_id = c.id AND r.user_id IS NOT NULL) AS joined
           FROM invite_codes c JOIN invite_batches b ON b.id = c.batch_id
           WHERE c.owner_user_id = %s AND c.kind = 'referral' AND c.revoked_at IS NULL AND b.revoked_at IS NULL"""
    row = conn.execute(q, (user_id,)).fetchone()
    if row is None:
        if conn.execute("SELECT 1 FROM invite_codes WHERE owner_user_id = %s AND kind = 'referral'", (user_id,)).fetchone():
            raise ValueError("revoked")
        res = create_batch(conn, 1, f"referral:u{user_id}", params.REFERRAL_DAYS, params.REFERRAL_MAX_USES, f"user {user_id}")
        conn.execute("UPDATE invite_codes SET kind = 'referral', owner_user_id = %s, display_code = %s WHERE batch_id = %s",
                     (user_id, pretty(res["codes"][0]), res["batch_id"]))
        row = conn.execute(q, (user_id,)).fetchone()
    elif row["max_uses"] != params.REFERRAL_MAX_USES:   # the limit was changed in params.py
        conn.execute("UPDATE invite_codes SET max_uses = %s WHERE id = %s", (params.REFERRAL_MAX_USES, row["id"]))
        row = conn.execute(q, (user_id,)).fetchone()
    out = {k: row[k] for k in ("code", "uses", "max_uses", "joined", "expires_at")}
    out["remaining"] = max(0, row["max_uses"] - row["uses"])
    return out


def revoke_owned(conn: psycopg.Connection, user_id: int) -> int:
    """Account deletion: stop the user's referral codes (so they never turn into owner-less admin codes)."""
    return len(conn.execute("""UPDATE invite_codes SET revoked_at = coalesce(revoked_at, now())
                               WHERE owner_user_id = %s RETURNING id""", (user_id,)).fetchall())


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
    idents = [(i["provider"], _identity_sha(i["provider"], i["subject"]), identity_hint(i["provider"], i["subject"], i["email"]))
              for i in conn.execute("SELECT provider, subject, email FROM user_identities WHERE user_id = %s ORDER BY id",
                                    (user_id,)).fetchall()]
    if idents and conn.execute("SELECT 1 FROM invite_redemption_identities WHERE identity_sha256 = ANY(%s)",
                               ([sha for _, sha, _ in idents],)).fetchone():
        return False   # this Apple ID / e-mail already opened an account with an invite code
    row = conn.execute(
        """UPDATE invite_codes c SET uses = c.uses + 1 FROM invite_batches b
           WHERE c.code_sha256 = %(sha)s AND c.batch_id = b.id AND c.revoked_at IS NULL AND b.revoked_at IS NULL
             AND (b.expires_at IS NULL OR b.expires_at > now()) AND c.uses < c.max_uses
             AND (c.kind = 'admin'
                  OR (%(referrals)s AND c.owner_user_id IS DISTINCT FROM %(user)s
                      AND EXISTS (SELECT 1 FROM users o WHERE o.id = c.owner_user_id AND o.status = 'active')
                      AND (SELECT count(*) FROM invite_redemptions r
                           WHERE r.code_id = c.id AND r.redeemed_at > now() - interval '1 day') < %(daily)s))
           RETURNING c.id, c.kind, c.owner_user_id""",
        {"sha": _sha(code), "user": user_id, "daily": params.REFERRAL_DAILY_REDEEMS,
         "referrals": params.referrals_enabled()}).fetchone()
    if row is None:
        return False
    inviter = row["owner_user_id"] if row["kind"] == "referral" else None
    red = conn.execute("INSERT INTO invite_redemptions (code_id, user_id, inviter_user_id) VALUES (%s, %s, %s) RETURNING id",
                       (row["id"], user_id, inviter)).fetchone()
    for provider, sha, hint in idents:
        conn.execute("INSERT INTO invite_redemption_identities (redemption_id, provider, identity_sha256, hint) VALUES (%s, %s, %s, %s)",
                     (red["id"], provider, sha, hint))
    if inviter is not None:
        # invites to the inviter's circles; joining (and what to share) stays the new user's own choice
        from ..friends import store as friends
        circle = friends.referral_invites(conn, inviter, user_id)
        if circle is not None:
            conn.execute("UPDATE invite_redemptions SET circle_id = %s WHERE id = %s", (circle, red["id"]))
        r = params.REFERRAL_REWARD
        conn.execute("""INSERT INTO invite_rewards (user_id, redemption_id, invitee_user_id, reward_type, amount, unit)
                        VALUES (%s, %s, %s, %s, %s, %s)""", (inviter, red["id"], user_id, r["type"], r["amount"], r.get("unit")))
    conn.execute("UPDATE users SET status = 'active' WHERE id = %s", (user_id,))
    conn.execute("INSERT INTO user_provisioning (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    return True
