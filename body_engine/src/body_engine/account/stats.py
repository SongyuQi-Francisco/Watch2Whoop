"""Account and activity statistics for the admin: `body-engine user-stats --days N [--json]`. Read-only.

Everything comes from timestamps the server already keeps; nothing is written and no column was added for this.
Definitions (repeated in HELP, which is the command's --help epilogue):

- activated:
  - self-signup account (has an invite redemption): the moment it redeemed an invite / referral code
    (invite_redemptions.redeemed_at). A `pending` account is never activated.
  - admin-created account: the first time one of its one-time login codes was used (web_login_codes.used_at: the app
    setup link / QR code, including the codes an activation link mints); without one, the consent time
    (users.consent_at, in-app or recorded by the admin) as the best remaining signal. The first use of a reusable
    activation link is not stored (activation_codes keeps only last_used_at and a count), but every use mints a login
    code, so the first login code is that moment.
- active in the window: any selected signal has a timestamp within the last N days (`--signals`, default all):
    web    Today page / app web session seen (web_sessions.last_seen_at) or a sign-in (user_identities.last_login_at).
           Sessions are deleted on sign-out and when their token is revoked, so an old one can vanish.
    api    an API token (not an AI client token) used (api_tokens.last_used_at, also bumped by a web sign-in)
    mcp    an AI client (remote MCP) token used (api_tokens.last_used_at of scope mcp)
    sync   health data reached the server: a Shortcut upload imported (shortcut_uploads.imported_at), the newest sample
           pulled from Open Wearables (sync_state 'ow' cursor; that is the sample's own time, the import job's run time
           is not a sign of the user), or a health history upload started (history_imports.created_at)
    ai     an AI conversation turn on the Today page (ai_usage.at) or an AI tool call through MCP / the butler
           (assistant_audit.at)
    write  a record written by an AI (ai_write_log.created_at)
  Not tracked, so not used: Shortcut uploads that failed to import, page views of the public handbook, and anything
  done in SparkyFitness directly. `data_through` (latest daily_metrics date) is shown for reference only: importers
  re-write recent days every hour, so updated_at is no sign of the user.
- new in the window: signed up (users.created_at) or activated within the last N days.
- sign-up method: `invite` (admin invite code), `referral` (a user's referral code; inviter shown, once referral
  codes exist on this server), `signup` (signed in, no code redeemed yet), `activation` (admin-created, has an
  activation link), `admin` (admin-created, set up with plain login codes / tokens).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from .. import auth

SIGNALS = ("web", "api", "mcp", "sync", "ai", "write")
WINDOWS = (1, 7, 30, 90)   # the usual windows; any N >= 1 works

HELP = __doc__.split("Definitions (repeated in HELP, which is the command's --help epilogue):\n", 1)[1]


def mask_email(email: str | None) -> str | None:
    """a***@example.com: first character of the local part, the domain kept."""
    if not email or "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def _has_column(conn: psycopg.Connection, table: str, column: str) -> bool:
    return conn.execute("""SELECT 1 FROM information_schema.columns
                           WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s""",
                        (table, column)).fetchone() is not None


def _signal_sql() -> dict[str, str]:
    """signal -> SELECT user_id, at (one row per event candidate; the caller takes the max)."""
    return {
        "web": """SELECT user_id, last_seen_at AS at FROM web_sessions
                  UNION ALL SELECT user_id, last_login_at FROM user_identities""",
        "api": "SELECT user_id, last_used_at AS at FROM api_tokens WHERE NOT (%(mcp)s = ANY(scopes))",
        "mcp": "SELECT user_id, last_used_at AS at FROM api_tokens WHERE %(mcp)s = ANY(scopes)",
        "sync": r"""SELECT user_id, imported_at AS at FROM shortcut_uploads
                    UNION ALL SELECT user_id, CASE WHEN cursor ~ '^\d{4}-\d{2}-\d{2}' THEN cursor::timestamptz END
                              FROM sync_state WHERE source = 'ow'
                    UNION ALL SELECT user_id, created_at FROM history_imports""",
        "ai": "SELECT user_id, at FROM ai_usage UNION ALL SELECT user_id, at FROM assistant_audit",
        "write": "SELECT user_id, created_at AS at FROM ai_write_log",
    }


def collect(conn: psycopg.Connection, days: int = 7, signals: tuple[str, ...] = SIGNALS,
            now: datetime | None = None) -> dict:
    """{"window": …, "summary": …, "users": [...]}; users sorted newest activation first (never activated last)."""
    if days < 1:
        raise ValueError("days must be >= 1")
    bad = set(signals) - set(SIGNALS)
    if bad or not signals:
        raise ValueError(f"unknown signals {sorted(bad)}; choose from {', '.join(SIGNALS)}")
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    referrals = _has_column(conn, "invite_codes", "kind") and _has_column(conn, "invite_redemptions", "inviter_user_id")
    sig = _signal_sql()
    last = ",\n".join(f"(SELECT max(at) FROM ({sig[s]}) x WHERE x.user_id = u.id) AS last_{s}" for s in SIGNALS)
    rows = conn.execute(f"""
        SELECT u.id, u.display_name, u.role, u.status, u.created_at, u.consent_at,
               (SELECT email FROM user_identities i WHERE i.user_id = u.id AND i.email IS NOT NULL
                ORDER BY i.provider = 'email' DESC, i.id LIMIT 1) AS email,
               EXISTS (SELECT 1 FROM user_identities i WHERE i.user_id = u.id) AS has_identity,
               EXISTS (SELECT 1 FROM activation_codes a WHERE a.user_id = u.id) AS has_activation,
               r.redeemed_at,
               {"c.kind AS code_kind, r.inviter_user_id, inv.display_name AS inviter_name," if referrals else
                "NULL AS code_kind, NULL::int AS inviter_user_id, NULL AS inviter_name,"}
               (SELECT min(used_at) FROM web_login_codes l WHERE l.user_id = u.id) AS first_login_code,
               (SELECT max(date) FROM daily_metrics d WHERE d.user_id = u.id) AS data_through,
               {last}
        FROM users u
        LEFT JOIN invite_redemptions r ON r.user_id = u.id
        {"LEFT JOIN invite_codes c ON c.id = r.code_id LEFT JOIN users inv ON inv.id = r.inviter_user_id"
         if referrals else ""}
        ORDER BY u.id""", {"mcp": auth.MCP_SCOPE}).fetchall()

    users = []
    for r in rows:
        if r["redeemed_at"]:
            method = "referral" if r["code_kind"] == "referral" else "invite"
            activated, via = r["redeemed_at"], method
        elif r["has_identity"]:
            method, activated, via = "signup", None, None
        else:
            method = "activation" if r["has_activation"] else "admin"
            if r["first_login_code"]:
                activated, via = r["first_login_code"], "login_code"
            elif r["consent_at"]:
                activated, via = r["consent_at"], "consent"
            else:
                activated, via = None, None
        if r["status"] == "pending":
            activated, via = None, None
        last_seen = {s: r[f"last_{s}"] for s in SIGNALS}
        hits = [s for s in signals if last_seen[s] and last_seen[s] >= since]
        users.append({
            "id": r["id"], "name": r["display_name"], "role": r["role"], "status": r["status"],
            "email": r["email"], "method": method,
            "inviter": ({"id": r["inviter_user_id"], "name": r["inviter_name"]} if r["inviter_user_id"] else None),
            "created_at": r["created_at"], "activated_at": activated, "activated_via": via,
            "last": last_seen, "data_through": r["data_through"],
            "active": bool(hits), "active_signals": hits,
            "new": r["created_at"] >= since or bool(activated and activated >= since),
            "signed_up_in_window": r["created_at"] >= since,
            "activated_in_window": bool(activated and activated >= since),
        })
    users.sort(key=lambda u: (u["activated_at"] is not None, u["activated_at"] or u["created_at"], u["id"]), reverse=True)

    status = {"pending": 0, "active": 0, "disabled": 0}
    for u in users:
        status[u["status"]] = status.get(u["status"], 0) + 1
    summary = {
        "accounts": len(users), "status": status,
        "signed_up": sum(u["signed_up_in_window"] for u in users),
        "activated": sum(u["activated_in_window"] for u in users),
        "active": sum(u["active"] for u in users),
        "active_by_signal": {s: sum(s in u["active_signals"] for u in users) for s in signals},
    }
    return {"window": {"days": days, "since": since, "until": now, "signals": list(signals),
                       "referrals_tracked": referrals},
            "summary": summary, "users": users}


def filter_users(stats: dict, active_only: bool = False, new_only: bool = False) -> dict:
    users = [u for u in stats["users"] if (not active_only or u["active"]) and (not new_only or u["new"])]
    return {**stats, "users": users}


def masked(stats: dict) -> dict:
    return {**stats, "users": [{**u, "email": mask_email(u["email"])} for u in stats["users"]]}


def _age(ts: datetime | None, now: datetime) -> str:
    if ts is None:
        return "-"
    secs = (now - ts).total_seconds()
    if secs < 3600:
        return "<1h"
    if secs < 86400:
        return f"{int(secs // 3600)}h"
    return f"{int(secs // 86400)}d"


def render(stats: dict) -> str:
    """Plain-text report: summary lines, then one row per user (last activity per signal as an age)."""
    w, s, now = stats["window"], stats["summary"], stats["window"]["until"]
    st = s["status"]
    by = ", ".join(f"{k} {v}" for k, v in s["active_by_signal"].items())
    out = [f"accounts {s['accounts']} (active {st['active']}, pending {st['pending']}, disabled {st['disabled']})",
           f"last {w['days']} days: signed up {s['signed_up']}, activated {s['activated']}, "
           f"active {s['active']} ({by})", ""]
    head = f"{'id':>4}  {'name':<16}{'e-mail':<28}{'status':<9}{'method':<11}{'activated':<12}" + \
           "".join(f"{x:>6}" for x in SIGNALS) + f"  {'data':<11}active"
    out.append(head)
    for u in stats["users"]:
        method = u["method"] + (f"<{u['inviter']['id']}" if u["inviter"] else "")
        act = u["activated_at"].astimezone().date().isoformat() if u["activated_at"] else "-"
        out.append(f"{u['id']:>4}  {(u['name'] or '')[:15]:<16}{(u['email'] or '-')[:27]:<28}{u['status']:<9}"
                   f"{method[:10]:<11}{act:<12}" + "".join(f"{_age(u['last'][x], now):>6}" for x in SIGNALS) +
                   f"  {str(u['data_through'] or '-'):<11}{'yes ' + ','.join(u['active_signals']) if u['active'] else '-'}")
    out += ["", "ages = time since the last event of each signal; activated = local date; "
                "definitions: body-engine user-stats --help"]
    return "\n".join(out)
