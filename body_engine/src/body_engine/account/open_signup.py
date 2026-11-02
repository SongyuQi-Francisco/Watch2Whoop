"""Open sign-up: accounts without an invite code while the server is below its account cap (flag `open_signup`), or
always while the App Store switch is on (flag `store_signup`: no cap, every other limit the same; params.py).

Limits and what counts are in params.py (OPEN_SIGNUP_*). The flag is global: on = state `all` (`body-engine flags set
open_signup --state all`); off, cohort or percent = off.

- try_activate() is called for a `pending` account at sign-in (account/api.py email_verify / apple: `new` = the account was
  just created there, with the request's IP) and on every later authenticated request (account/api.py gate()). It does
  exactly what redeeming an invite code does to the account (invites.redeem): records the sign-in identities so the same
  Apple ID / e-mail cannot open a second free account, sets status 'active' and queues provisioning (user_provisioning);
  the caller commits and starts provisioning (account/api.py activate_open). Nothing about consent changes: the consent
  sheet still comes first in the app, and no health data is processed before it (profile/consent.py).
- It refuses (the account stays `pending`, the invite step shows as before) when: the flag is off; the active accounts
  reached the cap; one of the identities already opened an account (with a code or here); or, for a new account, its IP
  opened OPEN_SIGNUP_PER_IP_DAY accounts in the last 24 hours (that account is marked and never opened here later).
- Concurrency: one transaction-level advisory lock around the count and the activation, so two sign-ups at 199 cannot
  both pass the cap of 200.
- Codes keep counting: code_window() lets an account opened here redeem one invite / referral code within
  OPEN_SIGNUP_CODE_DAYS (invites.redeem), so the inviter, the circle invite and the reward are recorded as at sign-up.
"""
from __future__ import annotations

import hashlib

import psycopg

from ..rollout import flags
from . import invites, params

LOCK_KEY = 760_001   # pg_advisory_xact_lock key: one open-signup decision at a time
REFUSALS = ("off", "not_pending", "ip_limit", "full", "identity_used")


def flag_on(conn: psycopg.Connection) -> bool:
    row = flags.rows(conn).get(params.OPEN_SIGNUP_FLAG)
    return bool(row and row["state"] == "all")


def store_on(conn: psycopg.Connection) -> bool:
    """App Store sign-up (flag store_signup, state `all` only): new accounts open with no cap."""
    row = flags.rows(conn).get(params.STORE_SIGNUP_FLAG)
    return bool(row and row["state"] == "all")


def mode(conn: psycopg.Connection) -> str | None:
    """'store' (no cap), 'open' (below the cap) or None (sign-up needs a code)."""
    return "store" if store_on(conn) else "open" if flag_on(conn) else None


def active_count(conn: psycopg.Connection) -> int:
    """Accounts that count against the cap (params.py: active, not in an excluded cohort)."""
    return conn.execute(
        """SELECT count(*) AS n FROM users u WHERE u.status = 'active'
           AND NOT EXISTS (SELECT 1 FROM user_cohorts c WHERE c.user_id = u.id AND c.cohort = ANY(%s))""",
        (list(params.OPEN_SIGNUP_EXCLUDE_COHORTS),)).fetchone()["n"]


def is_open(conn: psycopg.Connection) -> bool:
    """New accounts are opened without a code right now (store_signup on, or open_signup on and room under the cap)."""
    return store_on(conn) or (flag_on(conn) and active_count(conn) < params.OPEN_SIGNUP_MAX_ACTIVE)


def ip_hash(ip: str | None) -> str | None:
    if not ip or ip == "?":
        return None
    return hashlib.sha256(f"open-signup:{ip}".encode()).hexdigest()


def _identities(conn: psycopg.Connection, user_id: int) -> list[tuple[str, str, str]]:
    return [(i["provider"], invites._identity_sha(i["provider"], i["subject"]),
             invites.identity_hint(i["provider"], i["subject"], i["email"]))
            for i in conn.execute("SELECT provider, subject, email FROM user_identities WHERE user_id = %s ORDER BY id",
                                  (user_id,)).fetchall()]


def identity_used(conn: psycopg.Connection, shas: list[str], open_signup_too: bool = True) -> bool:
    """One of these identity hashes already opened an account (invite code, or open sign-up when open_signup_too)."""
    if not shas:
        return False
    if conn.execute("SELECT 1 FROM invite_redemption_identities WHERE identity_sha256 = ANY(%s)", (shas,)).fetchone():
        return True
    return open_signup_too and conn.execute(
        "SELECT 1 FROM open_signup_identities WHERE identity_sha256 = ANY(%s)", (shas,)).fetchone() is not None


def try_activate(conn: psycopg.Connection, user_id: int, ip: str | None = None, new: bool = False) -> str:
    """'activated', or why not (REFUSALS). Does not commit; the caller commits and starts provisioning."""
    how = mode(conn)
    if how is None:
        return "off"
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
    user = conn.execute("SELECT status FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
    if user is None or user["status"] != "pending":
        return "not_pending"
    held = conn.execute("SELECT decision FROM open_signups WHERE user_id = %s", (user_id,)).fetchone()
    if held is not None and held["decision"] == "ip_limit":
        return "ip_limit"
    if how == "open" and active_count(conn) >= params.OPEN_SIGNUP_MAX_ACTIVE:   # the store switch has no cap
        return "full"
    idents = _identities(conn, user_id)
    if identity_used(conn, [sha for _, sha, _ in idents]):
        return "identity_used"
    ip_sha = ip_hash(ip) if new else None
    conn.execute("UPDATE open_signups SET ip_sha256 = NULL WHERE ip_sha256 IS NOT NULL "
                 "AND created_at < now() - make_interval(hours => %s)", (params.OPEN_SIGNUP_IP_KEEP_HOURS,))
    if ip_sha is not None and conn.execute(
            """SELECT count(*) AS n FROM open_signups WHERE ip_sha256 = %s AND decision = 'activated' AND via = 'signup'
               AND created_at > now() - interval '1 day'""", (ip_sha,)).fetchone()["n"] >= params.OPEN_SIGNUP_PER_IP_DAY:
        conn.execute("""INSERT INTO open_signups (user_id, decision, via, ip_sha256) VALUES (%s, 'ip_limit', 'signup', %s)
                        ON CONFLICT (user_id) DO NOTHING""", (user_id, ip_sha))
        return "ip_limit"
    conn.execute("""INSERT INTO open_signups (user_id, decision, via, ip_sha256, activated_at, mode)
                    VALUES (%s, 'activated', %s, %s, now(), %s)
                    ON CONFLICT (user_id) DO UPDATE SET decision = 'activated', activated_at = now(), mode = EXCLUDED.mode""",
                 (user_id, "signup" if new else "pending", ip_sha, how))
    for provider, sha, hint in idents:
        conn.execute("""INSERT INTO open_signup_identities (identity_sha256, provider, hint) VALUES (%s, %s, %s)
                        ON CONFLICT (identity_sha256) DO NOTHING""", (sha, provider, hint))
    # from here on exactly what invites.redeem does to the account
    conn.execute("UPDATE users SET status = 'active' WHERE id = %s", (user_id,))
    conn.execute("INSERT INTO user_provisioning (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    return "activated"


def code_window(conn: psycopg.Connection, user_id: int) -> bool:
    """An account opened by open sign-up that may still redeem one invite / referral code (params.OPEN_SIGNUP_CODE_DAYS)."""
    return conn.execute(
        """SELECT 1 FROM open_signups s WHERE s.user_id = %s AND s.decision = 'activated'
           AND s.activated_at > now() - make_interval(days => %s)
           AND NOT EXISTS (SELECT 1 FROM invite_redemptions r WHERE r.user_id = s.user_id)""",
        (user_id, params.OPEN_SIGNUP_CODE_DAYS)).fetchone() is not None


# ---------------------------------------------------------------------------------------------------- admin

def status(conn: psycopg.Connection) -> dict:
    """For `body-engine open-signup status`: switch, cap, counts, limits, fallback-AI cost at the cap."""
    row = flags.rows(conn).get(params.OPEN_SIGNUP_FLAG)
    n = active_count(conn)
    counts = conn.execute(
        """SELECT count(*) FILTER (WHERE status = 'pending') AS pending, count(*) FILTER (WHERE status = 'disabled') AS disabled,
                  count(*) FILTER (WHERE status = 'active') AS active_all FROM users""").fetchone()
    opened = conn.execute(
        """SELECT count(*) FILTER (WHERE decision = 'activated') AS opened,
                  count(*) FILTER (WHERE decision = 'activated' AND mode = 'store') AS opened_store,
                  count(*) FILTER (WHERE decision = 'activated' AND activated_at > now() - interval '1 day') AS opened_24h,
                  count(*) FILTER (WHERE decision = 'ip_limit') AS held_ip FROM open_signups""").fetchone()
    on = bool(row and row["state"] == "all")
    store = store_on(conn)
    return {"flag_state": row["state"] if row else "off", "on": on, "store": store,
            "open": store or (on and n < params.OPEN_SIGNUP_MAX_ACTIVE),
            "active": n, "cap": params.OPEN_SIGNUP_MAX_ACTIVE, "room": max(params.OPEN_SIGNUP_MAX_ACTIVE - n, 0),
            "active_all": counts["active_all"], "excluded_cohorts": list(params.OPEN_SIGNUP_EXCLUDE_COHORTS),
            "pending": counts["pending"], "disabled": counts["disabled"],
            "opened": opened["opened"], "opened_store": opened["opened_store"], "opened_24h": opened["opened_24h"],
            "held_ip_limit": opened["held_ip"],
            "per_ip_day": params.OPEN_SIGNUP_PER_IP_DAY, "code_days": params.OPEN_SIGNUP_CODE_DAYS,
            "fallback": fallback_cost(conn, n)}


def fallback_cost(conn: psycopg.Connection, active: int | None = None, refresh: bool = False) -> dict:
    """Fallback-AI (托底 AI) cost ceiling: every counted account may spend its monthly quota (ai/fallback_params.py
    MONTHLY_QUOTA_CNY; per-person overrides in ai_fallback_quota are used for existing accounts, quota cards not included,
    the deployer (role owner, who uses the butler) not counted).
    month_max_now = the accounts counted now; month_max_at_cap = the same plus the rest of the cap at the default quota.
    runway_months_* = DeepSeek balance / that ceiling (None when the balance is unknown)."""
    from ..ai import fallback, fallback_params as fp
    active = active_count(conn) if active is None else active
    q = conn.execute(
        """SELECT coalesce(sum(coalesce(f.monthly_cny, %s)), 0) AS now FROM users u
           LEFT JOIN ai_fallback_quota f ON f.user_id = u.id
           WHERE u.status = 'active' AND u.role <> 'owner'
             AND NOT EXISTS (SELECT 1 FROM user_cohorts c WHERE c.user_id = u.id AND c.cohort = ANY(%s))""",
        (fp.MONTHLY_QUOTA_CNY, list(params.OPEN_SIGNUP_EXCLUDE_COHORTS))).fetchone()
    now = float(q["now"])
    at_cap = now + max(params.OPEN_SIGNUP_MAX_ACTIVE - active, 0) * fp.MONTHLY_QUOTA_CNY
    st = fallback.refresh_balance(conn, force=True) if refresh and fallback.read_key() else fallback.state(conn)
    bal = st.get("balance_cny")
    bal = float(bal) if bal is not None else None
    usable = max(bal - fp.BALANCE_STOP_CNY, 0) if bal is not None else None   # below the stop line the fallback pauses
    run = lambda m: round(usable / m, 1) if usable is not None and m > 0 else None  # noqa: E731
    return {"quota_cny": fp.MONTHLY_QUOTA_CNY, "month_max_now_cny": round(now, 2), "month_max_at_cap_cny": round(at_cap, 2),
            "balance_cny": bal, "balance_checked_at": st.get("balance_checked_at"), "stop_cny": fp.BALANCE_STOP_CNY,
            "runway_months_now": run(now), "runway_months_at_cap": run(at_cap)}


def list_opened(conn: psycopg.Connection, days: int | None = None, all_rows: bool = False) -> list[dict]:
    """Accounts opened by open sign-up (newest first), with their identities masked; all_rows adds the ones held back
    by the IP limit. days: only the last N days."""
    rows = conn.execute(
        """SELECT s.user_id, u.display_name AS name, u.status, s.decision, s.via, s.mode, s.created_at, s.activated_at,
                  u.created_at AS signed_up_at, u.consent_version,
                  EXISTS (SELECT 1 FROM invite_redemptions r WHERE r.user_id = s.user_id) AS code_redeemed,
                  coalesce((SELECT json_agg(json_build_object('provider', i.provider, 'email', i.email, 'subject', i.subject)
                                            ORDER BY i.id)
                            FROM user_identities i WHERE i.user_id = s.user_id), '[]') AS identities
           FROM open_signups s JOIN users u ON u.id = s.user_id
           WHERE (%(all)s OR s.decision = 'activated')
             AND (%(days)s::int IS NULL OR coalesce(s.activated_at, s.created_at) > now() - make_interval(days => %(days)s::int))
           ORDER BY coalesce(s.activated_at, s.created_at) DESC, s.user_id DESC""",
        {"all": all_rows, "days": days}).fetchall()
    for r in rows:
        r["identities"] = [{"provider": i["provider"], "hint": invites.identity_hint(i["provider"], i["subject"], i["email"])}
                           for i in r["identities"]]
    return rows
