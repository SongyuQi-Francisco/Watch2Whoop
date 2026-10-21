"""Which AI Jarvis uses for this user (docs/ai-assistant-plan.md §9.12). One place decides it; /v1/web/me, the chat and the settings all ask here.

Sources: one of the user's own providers (ai_providers, the one marked is_default), the fallback AI (the admin's DeepSeek, fallback.py),
or the body butler link (only the deployer, BODY_ASSISTANT_USER_ID). The user's pick is stored in ai_choice; no row = automatic.

A provider counts only while it can actually be used: status active, and for a subscription (MCP) provider, not "every AI token revoked".
When the pick cannot be used (disabled, deleted, tokens revoked, fallback switched off) the next one applies, so disabling / deleting
your own AI drops Jarvis back to the fallback at once:
  pick fallback -> fallback (if this server has it)       pick butler -> butler (if this is the deployer)
  otherwise: an API provider -> in-page chat; the deployer -> butler; a subscription provider -> its own app; fallback; nothing.
"""
import os

import psycopg

from ..i18n import L
from . import catalog, fallback, providers

SOURCES = ("provider", "fallback", "butler")


def owner_assistant_url(user_id: int) -> str | None:
    """The body butler (Claude Code Remote Control) belongs only to the person who deploys it (BODY_ASSISTANT_USER_ID, default 1)."""
    url = os.environ.get("BODY_ASSISTANT_URL", "").strip()
    if str(user_id) != os.environ.get("BODY_ASSISTANT_USER_ID", "1") or not url.startswith("https://"):
        return None
    return url


def pref(conn: psycopg.Connection, user_id: int) -> str | None:
    row = conn.execute("SELECT source FROM ai_choice WHERE user_id = %s", (user_id,)).fetchone()
    return row["source"] if row else None


def set_pref(conn: psycopg.Connection, user_id: int, source: str) -> None:
    conn.execute("""INSERT INTO ai_choice (user_id, source) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET source = EXCLUDED.source, updated_at = now()""", (user_id, source))


def _mcp_tokens_all_revoked(conn, user_id: int) -> bool:
    """The user had MCP access and none is left. Live = a static AI token not revoked, or an OAuth connection not revoked
    (its short-lived access token may have expired while the refresh token still works); an OAuth access token row by
    itself never counts, expired or not (ai/oauth.py)."""
    r = conn.execute("""SELECT (SELECT count(*) FROM api_tokens WHERE user_id = %(u)s AND 'mcp' = ANY(scopes)) AS n,
                               (SELECT count(*) FROM api_tokens WHERE user_id = %(u)s AND 'mcp' = ANY(scopes)
                                  AND oauth_grant_id IS NULL AND revoked_at IS NULL)
                             + (SELECT count(*) FROM oauth_grants WHERE user_id = %(u)s AND revoked_at IS NULL) AS live""",
                     {"u": user_id}).fetchone()
    return r["n"] > 0 and r["live"] == 0


def usable(conn: psycopg.Connection, row: dict | None) -> bool:
    if row is None or row["status"] != "active":
        return False
    if not catalog.kind_allowed(row["kind"]):
        return False
    return not (catalog.is_mcp(row["kind"]) and _mcp_tokens_all_revoked(conn, row["user_id"]))


def effective(conn: psycopg.Connection, user_id: int) -> dict:
    """{"source": provider | fallback | butler | none, "provider": row or None, "url": butler link or None, "pref": stored pick}"""
    p = pref(conn, user_id)
    url = owner_assistant_url(user_id)
    fb = fallback.read_key() is not None
    row = providers.default_provider(conn, user_id)
    prov = row if usable(conn, row) else None
    out = {"pref": p, "provider": None, "url": None}
    if p == "fallback" and fb:
        return {**out, "source": "fallback"}
    if p == "butler" and url:
        return {**out, "source": "butler", "url": url}
    if prov is not None and (p == "provider" or not catalog.is_mcp(prov["kind"]) or not url):
        return {**out, "source": "provider", "provider": prov}
    if url:
        return {**out, "source": "butler", "url": url}
    if fb:
        return {**out, "source": "fallback"}
    return {**out, "source": "none"}


def key_of(e: dict) -> str:
    return f"p:{e['provider']['id']}" if e["source"] == "provider" else e["source"]


def options(conn: psycopg.Connection, user_id: int) -> list[dict]:
    """Everything this user can pick, in display order; `current` marks what Jarvis uses now."""
    cur = key_of(effective(conn, user_id))
    out = []
    if fallback.read_key() is not None:
        s = fallback.summary(conn, user_id)
        out.append({"key": "fallback", "label": s["display_name"], "kind": "fallback", "available": s["available"] or s["reason"] == "fallback_consent",  # consent is asked when picked
                    "note": f"¥{s['spent_cny']:.2f} / ¥{s['quota_cny']:g}", "consent": s["consent"]})
    for r in conn.execute("SELECT * FROM ai_providers WHERE user_id = %s ORDER BY is_default DESC, id", (user_id,)):
        mcp = catalog.is_mcp(r["kind"])
        out.append({"key": f"p:{r['id']}", "label": catalog.display_name(r), "kind": "mcp" if mcp else "api",
                    "available": usable(conn, r), "note": L("订阅", "Subscription") if mcp else (r["model"] or ""),
                    "status": r["status"]})
    if owner_assistant_url(user_id):
        out.append({"key": "butler", "label": L("身体管家", "Body butler"), "kind": "butler", "available": True, "note": "Claude"})
    for o in out:
        o["current"] = o["key"] == cur
    return out


def choose(conn: psycopg.Connection, user_id: int, key: str) -> None:
    """The user's pick from AI settings or the chat panel's switcher. Raises ValueError with a short message (request language)."""
    if key == "fallback":
        if fallback.read_key() is None:
            raise ValueError(L("这台服务器没有开通托底 AI", "This server has no backup AI"))
        set_pref(conn, user_id, "fallback")
    elif key == "butler":
        if not owner_assistant_url(user_id):
            raise ValueError(L("身体管家只给部署者本人", "The body butler is only for the deployer"))
        set_pref(conn, user_id, "butler")
    elif key.startswith("p:") and key[2:].isdigit():
        row = providers.get_provider(conn, user_id, int(key[2:]))
        if row is None:
            raise ValueError(L("没有这个 AI（或不是你的）", "No such AI (or it is not yours)"))
        if row["status"] != "active":
            raise ValueError(L("这个 AI 停用了，先在 AI 设置里启用", "This AI is turned off; turn it on in AI settings first"))
        if not row["is_default"]:
            providers.update_provider(conn, user_id, row["id"], {"is_default": True})
        set_pref(conn, user_id, "provider")
    else:
        raise ValueError(L("不认识的选项", "Unknown option"))
