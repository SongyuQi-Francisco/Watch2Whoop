"""Rows the server adds to the native app's Me page, and opening one in the system browser.

A plugin decides who sees which row and what it says (plugins.py, `me_links`); the app draws what GET /v1/web/me lists under
`me_links`. No plugin, or nothing for this person: the key is absent and the app draws nothing.

POST /v1/web/me-links/{key}/open answers with the address to open. A row marked login=True (same-origin only) gets a one-time
login code in the URL fragment: browsers never send a fragment to the server, so the code stays out of access logs, and the
page redeems it with POST /v1/web/login-code. The app's long-lived token never goes into a URL.
- Only the app's Bearer token with write scope mints a code, with that token's scopes. A browser session or a read-only token
  gets the plain address, and the page asks that person to sign in.
- One browser login per row and person: opening a row again voids the unused code and ends the login made from the previous
  one, so tapping a row does not pile up tokens. Signing out of the app ends them too (end_logins).
"""
import psycopg

from .. import auth, plugins
from ..i18n import lang
from . import login_codes

CODE_MINUTES = 10           # the browser opens right after the tap; a code nobody used is gone soon after
LABEL_PREFIX = "link:"      # web_login_codes.label; the token from redeeming it is named app:<label> (login_codes.py)


def _listed(conn: psycopg.Connection, viewer: dict) -> list[dict]:
    return plugins.me_links(conn, viewer) if (viewer.get("status") or "active") == "active" else []


def for_me(conn: psycopg.Connection, viewer: dict) -> list[dict]:
    """What /v1/web/me shows: key, title, url of each row for this person."""
    return [{k: link[k] for k in ("key", "title", "url")} for link in _listed(conn, viewer)]


def open_url(conn: psycopg.Connection, viewer: dict, key: str) -> str | None:
    """The address to open for this person, or None when the row is not there for them."""
    link = next((x for x in _listed(conn, viewer) if x["key"] == key), None)
    if link is None:
        return None
    scopes = [s for s in (viewer.get("token_scopes") or []) if s in login_codes.ALLOWED_SCOPES]
    if not link["login"] or viewer.get("auth") == "session" or "write" not in scopes:
        return link["url"]
    label = LABEL_PREFIX + key
    auth.revoke_tokens(conn, viewer["id"], name=f"app:{label}")
    conn.execute("""UPDATE web_login_codes SET expires_at = now()
                    WHERE user_id = %s AND label = %s AND used_at IS NULL AND expires_at > now()""", (viewer["id"], label))
    code = login_codes.create(conn, viewer["id"], label, scopes=scopes, minutes=CODE_MINUTES)
    return f"{link['url']}#login={code}&lang={lang()}"


def end_logins(conn: psycopg.Connection, user_id: int) -> None:
    """The app signed out: the browser logins it opened end with it, and unused codes are void."""
    conn.execute("""UPDATE api_tokens SET revoked_at = now()
                    WHERE user_id = %s AND name LIKE %s AND revoked_at IS NULL""", (user_id, f"app:{LABEL_PREFIX}%"))
    conn.execute("""UPDATE web_login_codes SET expires_at = now()
                    WHERE user_id = %s AND label LIKE %s AND used_at IS NULL AND expires_at > now()""",
                 (user_id, f"{LABEL_PREFIX}%"))
