"""Circle activation codes (#199): one readable code (e.g. a club's name) that opens an account and puts the person in one
circle. The admin makes them (`body-engine circle-code create`); docs/friends-social-plan.md §10.

- Matching ignores case, spaces and dashes (normalize()): "Thin Muscle Club" = "thin-muscle-club" = "THINMUSCLECLUB".
  The code is a promo code, not a secret: it is stored as typed (listings show it); wrong guesses still count against
  the per-IP failure limits of the endpoints that take it.
- A code has max_uses, an optional expiry and can be disabled. Every use is one row in circle_code_uses (who, when,
  new account or not, when they joined). The code row is locked (SELECT … FOR UPDATE) for the whole redemption and the
  use is counted with `uses < max_uses`, so people signing up at the same moment can never exceed max_uses.
- A new person (account `pending`) redeems it in the sign-up invite step (POST /v1/account/redeem-invite): the account
  becomes active and a use is recorded with joined_at NULL. Nothing about them reaches the circle yet: they join
  (complete_pending) only after they agreed to the consent version with the circle clause (params.CONSENT_VERSION),
  from POST /v1/users/{user}/consent or the next Friends page load. They then share the circle's default scopes
  (circles.default_scopes, else params.DEFAULT_SCOPES), which the consent sheet listed beforehand; they can change
  that or leave at any time like any member, and leaving ends the sharing at once (store.leave).
- Someone who already has an account types the code on the Friends page (preview / join in store.py); they pick what to
  share in the join sheet, as with any invite. A member who uses it again consumes nothing.
- A circle's member cap counts members plus uses still waiting for consent, so the 50th person cannot be turned away
  after they signed up.
Errors are FriendsError(status, Chinese message); errors_en.py has the English.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime

import psycopg

from .. import auth
from ..profile import consent
from . import params as P, store

DASHES = "-‐‑‒–—―"
NORM_MIN, NORM_MAX, CODE_MAX = 4, 40, 60
MAX_USES = 10_000


def normalize(code: str | None) -> str:
    """Case-folded, without white space or dashes (NFKC first, so full-width letters match too)."""
    s = unicodedata.normalize("NFKC", code or "").casefold()
    return "".join(ch for ch in s if not ch.isspace() and ch not in DASHES)


def _clean_code(code: str) -> tuple[str, str]:
    shown = " ".join((code or "").split())
    norm = normalize(shown)
    if not NORM_MIN <= len(norm) <= NORM_MAX or len(shown) > CODE_MAX:
        raise ValueError(f"圈子激活码去掉空格和连字符后要 {NORM_MIN}–{NORM_MAX} 个字符")
    return shown, norm


def state(row: dict) -> str:
    """ok / disabled / expired / used_up (in that order of precedence)."""
    if row["disabled_at"] is not None:
        return "disabled"
    if row["expires_at"] is not None and row["expires_at"] <= datetime.now().astimezone():
        return "expired"
    if row["uses"] >= row["max_uses"]:
        return "used_up"
    return "ok"


_STATE_ERRORS = {"disabled": "这个圈子激活码已停用", "expired": "这个圈子激活码已过期", "used_up": "这个圈子激活码已经用完了"}


def _check(row: dict) -> None:
    s = state(row)
    if s != "ok":
        raise store.FriendsError(410, _STATE_ERRORS[s])


def find(conn: psycopg.Connection, code: str | None, lock: bool = False) -> dict | None:
    """The code with this normalised text: the live one, else the most recent disabled one (to say it was disabled)."""
    norm = normalize(code)
    if not NORM_MIN <= len(norm) <= NORM_MAX:
        return None
    return conn.execute(
        "SELECT * FROM circle_codes WHERE code_norm = %s ORDER BY (disabled_at IS NULL) DESC, id DESC LIMIT 1"
        + (" FOR UPDATE" if lock else ""), (norm,)).fetchone()


# ---------------------------------------------------------------- admin (CLI)
def create(conn: psycopg.Connection, circle_id: int, code: str, max_uses: int, expires_at: datetime | None = None,
           label: str | None = None, created_by: str | None = None) -> dict:
    shown, norm = _clean_code(code)
    if isinstance(max_uses, bool) or not 1 <= max_uses <= MAX_USES:
        raise ValueError(f"次数 1–{MAX_USES}")
    if expires_at is not None and expires_at <= datetime.now().astimezone():
        raise ValueError("有效期已经过去了")
    circle = conn.execute("SELECT id, owner_id FROM circles WHERE id = %s", (circle_id,)).fetchone()
    if circle is None:
        raise ValueError(f"没有圈子 {circle_id}")
    if conn.execute("SELECT 1 FROM circle_codes WHERE code_norm = %s AND disabled_at IS NULL", (norm,)).fetchone():
        raise ValueError("已经有一个同样的圈子激活码（不分大小写、空格和连字符）：先停用它")
    return conn.execute(
        """INSERT INTO circle_codes (circle_id, user_id, code, code_norm, label, max_uses, expires_at, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (circle_id, circle["owner_id"], shown, norm, (label or "").strip()[:60] or None, max_uses, expires_at,
         (created_by or "").strip()[:60] or None)).fetchone()


def list_codes(conn: psycopg.Connection, circle_id: int | None = None) -> list[dict]:
    rows = conn.execute(
        """SELECT k.id, k.code, k.label, k.circle_id, c.name AS circle, k.uses, k.max_uses, k.expires_at, k.disabled_at,
                  k.created_at, k.created_by,
                  (SELECT count(*) FROM circle_code_uses u WHERE u.code_id = k.id AND u.new_account) AS new_accounts,
                  (SELECT count(*) FROM circle_code_uses u WHERE u.code_id = k.id AND u.joined_at IS NULL
                     AND u.user_id IS NOT NULL) AS waiting_consent
           FROM circle_codes k JOIN circles c ON c.id = k.circle_id
           WHERE %(c)s::int IS NULL OR k.circle_id = %(c)s::int ORDER BY k.id""", {"c": circle_id}).fetchall()
    return [{**r, "state": state(r), "remaining": max(0, r["max_uses"] - r["uses"])} for r in rows]


def uses(conn: psycopg.Connection, code_id: int) -> list[dict]:
    """Every use of one code: who (id + name; NULL = account deleted), when, new account or not, joined when, still in."""
    return conn.execute(
        """SELECT u.id, u.user_id, us.display_name AS name, u.used_at, u.new_account, u.joined_at,
                  EXISTS (SELECT 1 FROM circle_members m JOIN circle_codes k ON k.circle_id = m.circle_id
                          WHERE k.id = u.code_id AND m.user_id = u.user_id) AS member
           FROM circle_code_uses u LEFT JOIN users us ON us.id = u.user_id WHERE u.code_id = %s ORDER BY u.used_at, u.id""",
        (code_id,)).fetchall()


def disable(conn: psycopg.Connection, code_id: int) -> bool:
    """Stop a code (people who already joined stay; uses still waiting for consent still join)."""
    return conn.execute("UPDATE circle_codes SET disabled_at = now() WHERE id = %s AND disabled_at IS NULL RETURNING id",
                        (code_id,)).fetchone() is not None


# ---------------------------------------------------------------- people
def _circle_info(conn, circle_id: int) -> dict:
    return conn.execute(
        """SELECT c.id, c.name, c.personal, c.avatar_image_id, u.display_name AS owner_name,
                  coalesce(c.default_scopes, %s) AS default_scopes,
                  (SELECT count(*) FROM circle_members m WHERE m.circle_id = c.id) AS members
           FROM circles c LEFT JOIN users u ON u.id = c.owner_id WHERE c.id = %s""",
        (list(P.DEFAULT_SCOPES), circle_id)).fetchone()


def preview(conn: psycopg.Connection, code: str) -> dict | None:
    """What a circle code opens, for the Friends page join sheet; None = not a circle code. Raises 410 when it cannot be used."""
    row = find(conn, code)
    if row is None:
        return None
    _check(row)
    c = _circle_info(conn, row["circle_id"])
    return {"id": c["id"], "name": c["name"], "owner_name": c["owner_name"], "members": c["members"],
            "default_scopes": [s for s in P.CIRCLE_SCOPES if s in set(c["default_scopes"])], "circle_code": True}


def _pending_count(conn, *, circle_id: int | None = None, user_id: int | None = None) -> int:
    return conn.execute(
        """SELECT count(*) AS n FROM circle_code_uses u JOIN circle_codes k ON k.id = u.code_id
           WHERE u.joined_at IS NULL AND u.user_id IS NOT NULL
             AND (%(c)s::int IS NULL OR k.circle_id = %(c)s::int) AND (%(u)s::int IS NULL OR u.user_id = %(u)s::int)""",
        {"c": circle_id, "u": user_id}).fetchone()["n"]


def _ready(conn, uid: int) -> bool:
    """May this person be put in a circle now: active, circles on for them, and the circle consent given."""
    u = conn.execute("SELECT status FROM users WHERE id = %s", (uid,)).fetchone()
    return u is not None and u["status"] == "active" and auth.circles_on(conn, uid) \
        and consent.agreed_at_least(conn, uid, P.CONSENT_VERSION)


def redeem(conn: psycopg.Connection, uid: int, code: str, scopes=None) -> dict | None:
    """Use a circle code for `uid`. None = no circle code has this text (the caller tries its other kinds of codes).
    Returns {circle_id, name, new_account, joined, already}: joined False = waiting for consent (complete_pending).
    `scopes`: what to share (Friends page join sheet); None = the circle's defaults. A pending account becomes active."""
    row = find(conn, code, lock=True)   # the row lock serialises everyone redeeming this code
    if row is None:
        return None
    _check(row)
    if scopes is not None:
        scopes = store.clean_scopes(scopes)
    user = conn.execute("SELECT id, status FROM users WHERE id = %s FOR UPDATE", (uid,)).fetchone()
    if user is None or user["status"] == "disabled":
        raise store.FriendsError(403, "这个账号已停用")
    cid = row["circle_id"]
    c = conn.execute("SELECT id, name, max_members FROM circles WHERE id = %s FOR UPDATE", (cid,)).fetchone()
    out = {"circle_id": cid, "name": c["name"], "new_account": False, "joined": False, "already": False}
    if conn.execute("SELECT 1 FROM circle_members WHERE circle_id = %s AND user_id = %s", (cid, uid)).fetchone():
        return {**out, "joined": True, "already": True}
    prior = conn.execute("SELECT id, new_account FROM circle_code_uses WHERE code_id = %s AND user_id = %s",
                         (row["id"], uid)).fetchone()
    if prior is None:
        if store.members_and_waiting(conn, cid) >= (c["max_members"] or P.MAX_MEMBERS):
            raise store.FriendsError(409, f"这个圈子已满（最多 {c['max_members'] or P.MAX_MEMBERS} 人）")
        if store._count_circles(conn, uid) + _pending_count(conn, user_id=uid) >= P.MAX_CIRCLES_PER_USER:
            raise store.FriendsError(409, f"最多加入 {P.MAX_CIRCLES_PER_USER} 个圈子")
        if conn.execute("UPDATE circle_codes SET uses = uses + 1 WHERE id = %s AND uses < max_uses RETURNING id",
                        (row["id"],)).fetchone() is None:
            raise store.FriendsError(410, _STATE_ERRORS["used_up"])
        new_account = user["status"] == "pending"
        conn.execute("INSERT INTO circle_code_uses (code_id, user_id, new_account) VALUES (%s, %s, %s)",
                     (row["id"], uid, new_account))
        if new_account:
            conn.execute("UPDATE users SET status = 'active' WHERE id = %s", (uid,))
            conn.execute("INSERT INTO user_provisioning (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (uid,))
        out["new_account"] = new_account
    else:
        out["new_account"] = prior["new_account"]
    out["joined"] = bool(complete_pending(conn, uid, scopes=scopes, only_circle=cid))
    return out


def complete_pending(conn: psycopg.Connection, uid: int, scopes=None, only_circle: int | None = None) -> list[dict]:
    """Put `uid` in every circle whose code they used and have not joined yet, once _ready(); returns [{circle_id, name}]
    of the circles joined now. Idempotent; called after the consent and on every Friends page load."""
    if not _ready(conn, uid):
        return []
    rows = conn.execute(
        """SELECT u.id, k.circle_id FROM circle_code_uses u JOIN circle_codes k ON k.id = u.code_id
           WHERE u.user_id = %s AND u.joined_at IS NULL AND (%s::int IS NULL OR k.circle_id = %s::int)
           ORDER BY u.used_at, u.id FOR UPDATE OF u""", (uid, only_circle, only_circle)).fetchall()
    joined = []
    for r in rows:
        c = conn.execute("SELECT id, name, default_scopes FROM circles WHERE id = %s FOR UPDATE", (r["circle_id"],)).fetchone()
        if not conn.execute("SELECT 1 FROM circle_members WHERE circle_id = %s AND user_id = %s", (c["id"], uid)).fetchone():
            chosen = scopes if scopes is not None else store.clean_scopes(c["default_scopes"] or P.DEFAULT_SCOPES)
            conn.execute("INSERT INTO circle_members (circle_id, user_id, scopes) VALUES (%s, %s, %s)", (c["id"], uid, chosen))
            joined.append({"circle_id": c["id"], "name": c["name"]})
        conn.execute("UPDATE circle_code_uses SET joined_at = now() WHERE id = %s", (r["id"],))
    if joined:
        store.sync_user(conn, uid)
    return joined


def pending_for(conn: psycopg.Connection, uid: int) -> list[dict]:
    """Circles this person will join after the consent, with what they will share there (shown on the consent sheet)."""
    rows = conn.execute(
        """SELECT DISTINCT ON (c.id) c.id AS circle_id, c.name, coalesce(c.default_scopes, %s) AS scopes
           FROM circle_code_uses u JOIN circle_codes k ON k.id = u.code_id JOIN circles c ON c.id = k.circle_id
           WHERE u.user_id = %s AND u.joined_at IS NULL ORDER BY c.id""", (list(P.DEFAULT_SCOPES), uid)).fetchall()
    return [{**r, "scopes": [s for s in P.CIRCLE_SCOPES if s in set(r["scopes"])]} for r in rows]
