"""Each user's own AI provider config (table ai_providers).

Isolation rules (every function filters by user_id; the caller makes sure user_id = the logged-in person):
- You can only see / edit / delete your own; family sharing (shares) does not include AI config.
- The API key is stored encrypted (crypto.py, additional data bound to user_id) and only its last 4 chars are returned; only this user's own chat requests
  (chat.py -> decrypt_secret) decrypt it, and the row's user_id is checked once more at decryption.
"""
import ipaddress
import re
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L
from . import catalog, crypto

MODEL_RE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,100}$")
LOCAL_HTTP_HOSTS = {"localhost", "127.0.0.1", "host.docker.internal"}
MAX_PROVIDERS = 10
PUBLIC_COLS = ("id", "kind", "display_name", "model", "base_url", "secret_last4", "monthly_budget_usd", "web_tools",
               "price_in_per_mtok", "price_out_per_mtok", "status", "is_default", "created_at", "updated_at")


class _PrivateIp(ValueError):
    pass


def _check_base_url(url: str) -> str:
    """Address for openai_compatible: https to any host; http only to the local machine (Ollama etc.), so the key is never sent in plaintext over the network.
    Credentials and query strings are not allowed."""
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError(L("base_url 要写成 https://… （本机服务可以用 http://localhost:端口/v1）",
                           "base_url must look like https://… (a local service can use http://localhost:port/v1)"))
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(L("base_url 不能带账号密码、? 或 #", "base_url cannot contain a user name, password, ? or #"))
    if parts.scheme == "http" and parts.hostname not in LOCAL_HTTP_HOSTS:
        raise ValueError(L("http 只允许本机地址（localhost / 127.0.0.1 / host.docker.internal），其他请用 https",
                           "http is only allowed for this machine (localhost / 127.0.0.1 / host.docker.internal); use https otherwise"))
    try:  # Private-network IPs are never allowed (except the local allow-list), so the chat endpoint cannot be used to probe the internal network
        ip = ipaddress.ip_address(parts.hostname)
        if (ip.is_private or ip.is_link_local or ip.is_reserved) and parts.hostname not in LOCAL_HTTP_HOSTS:
            raise _PrivateIp(L("base_url 不能是内网 IP", "base_url cannot be a private network IP"))
    except _PrivateIp:
        raise
    except ValueError:  # not an IP address: a host name
        pass
    return url


def _check_key(key: str) -> str:
    key = (key or "").strip()
    if not 8 <= len(key) <= 512 or any(c.isspace() for c in key):
        raise ValueError(L("API key 格式不对（8–512 个字符，不能有空格）", "The API key looks wrong (8–512 characters, no spaces)"))
    return key


def _public(row: dict) -> dict:
    out = {k: row.get(k) for k in PUBLIC_COLS}
    for k in ("monthly_budget_usd", "price_in_per_mtok", "price_out_per_mtok"):
        if out[k] is not None:
            out[k] = float(out[k])
    out["has_secret"] = row.get("encrypted_secret") is not None
    out["category"] = catalog.kind_info(row["kind"]).get("category", "api")
    out["retired"] = row["kind"] in catalog.RETIRED_KINDS
    out["label"] = catalog.label(row["kind"])
    out["display_name"] = catalog.display_name(row)
    return out


def month_start(conn: psycopg.Connection, user_id: int) -> datetime:
    tz = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"]
    now = datetime.now(ZoneInfo(tz or "UTC"))
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_cost(conn: psycopg.Connection, user_id: int, provider_id: int | None = None) -> float:
    """This user's estimated spend this month (in their time zone); with provider_id only that provider is counted."""
    q = "SELECT coalesce(sum(cost_usd), 0) AS c FROM ai_usage WHERE user_id = %s AND at >= %s"
    args: list = [user_id, month_start(conn, user_id)]
    if provider_id is not None:
        q += " AND provider_id = %s"
        args.append(provider_id)
    return float(conn.execute(q, args).fetchone()["c"])


def list_providers(conn: psycopg.Connection, user_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM ai_providers WHERE user_id = %s ORDER BY is_default DESC, id",
                        (user_id,)).fetchall()
    return [{**_public(r), "month_cost_usd": round(month_cost(conn, user_id, r["id"]), 4)} for r in rows]


def get_provider(conn: psycopg.Connection, user_id: int, provider_id: int) -> dict | None:
    """The whole row including ciphertext, for this module and chat.py only; always queried by (id, user_id)."""
    return conn.execute("SELECT * FROM ai_providers WHERE id = %s AND user_id = %s",
                        (provider_id, user_id)).fetchone()


def default_provider(conn: psycopg.Connection, user_id: int) -> dict | None:
    return conn.execute("SELECT * FROM ai_providers WHERE user_id = %s AND is_default", (user_id,)).fetchone()


def decrypt_secret(row: dict, user_id: int) -> str | None:
    if row["user_id"] != user_id:  # Check once more: a key may only be used by its owner
        raise PermissionError(L("这个提供商不属于当前用户", "This provider belongs to someone else"))
    return crypto.decrypt(user_id, row["encrypted_secret"]) if row["encrypted_secret"] is not None else None


def _normalize(kind: str, data: dict, existing: dict | None) -> dict:
    """Validate and fill defaults; returns the columns to write into the database (without user_id)."""
    k = catalog.KINDS[kind]
    out: dict = {}
    if "display_name" in data or existing is None:
        name = (data.get("display_name") or "").strip() or k["label"]
        out["display_name"] = name[:60]
    if catalog.is_mcp(kind):
        for f in ("model", "base_url", "api_key"):
            if data.get(f):
                raise ValueError(L("订阅类不填模型、地址和 key：模型在你自己的客户端里选，后端不碰你的账号",
                                   "A subscription takes no model, address or key: pick the model in your own client; "
                                   "the server never touches your account"))
        return out
    if "model" in data or existing is None:
        model = (data.get("model") or k.get("default_model") or "").strip()
        if not MODEL_RE.match(model):
            ex = k.get("default_model") or "deepseek-chat"
            raise ValueError(L(f"要填模型名（例：{ex}）", f"Enter a model name (e.g. {ex})"))
        out["model"] = model
    if kind == "openai_compatible":
        if "base_url" in data or existing is None:
            if not data.get("base_url"):
                raise ValueError(L("OpenAI 兼容接口要填 base_url", "An OpenAI-compatible API needs a base_url"))
            out["base_url"] = _check_base_url(data["base_url"])
    elif data.get("base_url"):
        raise ValueError(L("只有 OpenAI 兼容接口才填 base_url", "Only an OpenAI-compatible API takes a base_url"))
    if data.get("api_key"):
        key = _check_key(data["api_key"])
        out["encrypted_secret"] = None  # Placeholder: encrypted after create / update gets the user_id
        out["_key"] = key
    elif existing is None:
        base = out.get("base_url") or ""
        local = urlsplit(base).hostname in LOCAL_HTTP_HOSTS if base else False
        if not local:
            raise ValueError(L("要填 API key", "Enter an API key"))
    if data.get("web_tools") is not None:
        if data["web_tools"] and kind not in catalog.WEB_TOOL_KINDS:
            raise ValueError(L("只有 Anthropic / OpenAI / Gemini API 能打开官方联网工具",
                               "Only the Anthropic, OpenAI and Gemini APIs can turn on the official web tools"))
        out["web_tools"] = bool(data["web_tools"])
    for f in ("monthly_budget_usd", "price_in_per_mtok", "price_out_per_mtok"):
        if f in data:
            v = data[f]
            if v is not None and not 0 <= float(v) <= 10000:
                raise ValueError(L(f"{f} 要在 0–10000 之间", f"{f} must be between 0 and 10000"))
            out[f] = v
    return out


def _apply_key(user_id: int, cols: dict) -> dict:
    key = cols.pop("_key", None)
    if key is not None:
        cols["encrypted_secret"] = crypto.encrypt(user_id, key)
        cols["secret_last4"] = crypto.last4(key)
    return cols


def create_provider(conn: psycopg.Connection, user_id: int, data: dict) -> dict:
    kind = data.get("kind")
    if kind not in catalog.KINDS:
        raise ValueError(L(f"不认识的提供商种类：{kind}", f"Unknown provider kind: {kind}"))
    n = conn.execute("SELECT count(*) AS n FROM ai_providers WHERE user_id = %s", (user_id,)).fetchone()["n"]
    if n >= MAX_PROVIDERS:
        raise ValueError(L(f"每人最多 {MAX_PROVIDERS} 个提供商", f"At most {MAX_PROVIDERS} providers per person"))
    cols = _apply_key(user_id, _normalize(kind, data, None))
    make_default = bool(data.get("is_default")) or n == 0  # The first one is set as default automatically
    with conn.transaction():
        if make_default:
            conn.execute("UPDATE ai_providers SET is_default = false WHERE user_id = %s AND is_default", (user_id,))
        names = ["user_id", "kind", "is_default", *cols]
        row = conn.execute(
            f"INSERT INTO ai_providers ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))}) RETURNING *",
            [user_id, kind, make_default, *cols.values()]).fetchone()
    return _public(row)


def update_provider(conn: psycopg.Connection, user_id: int, provider_id: int, data: dict) -> dict | None:
    row = get_provider(conn, user_id, provider_id)
    if row is None:
        return None
    if not catalog.kind_allowed(row["kind"]):
        raise ValueError(L("这个提供商已下线，不能再修改或启用，只能删除", "This provider was retired: it can't be changed or turned on, only deleted"))
    cols = _apply_key(user_id, _normalize(row["kind"], data, row))
    if "status" in data:
        if data["status"] not in ("active", "disabled"):
            raise ValueError(L("status 只能是 active / disabled", "status must be active or disabled"))
        cols["status"] = data["status"]
    with conn.transaction():
        if data.get("is_default"):
            conn.execute("UPDATE ai_providers SET is_default = false WHERE user_id = %s AND is_default", (user_id,))
            cols["is_default"] = True
        elif data.get("is_default") is False:
            cols["is_default"] = False
        if cols:
            sets = ", ".join(f"{c} = %s" for c in cols)
            row = conn.execute(f"UPDATE ai_providers SET {sets}, updated_at = now() WHERE id = %s AND user_id = %s RETURNING *",
                               [*cols.values(), provider_id, user_id]).fetchone()
    return _public(row)


def delete_provider(conn: psycopg.Connection, user_id: int, provider_id: int) -> bool:
    return conn.execute("DELETE FROM ai_providers WHERE id = %s AND user_id = %s RETURNING id",
                        (provider_id, user_id)).fetchone() is not None
