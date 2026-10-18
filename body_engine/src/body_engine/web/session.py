"""Today page login: exchange an API token for an httpOnly cookie.

- The first time, the user enters their own API token on the page (same kind as used for Bearer, one per person) -> POST /v1/web/login validates it and creates a session;
  the cookie only holds a random session ID (sha256 stored in the DB), the token itself is not kept in the browser.
- Cookie: HttpOnly (page JS cannot read it), Secure (HTTPS only; Chrome exempts localhost / 127.0.0.1 so local debugging works),
  SameSite=Strict (requests started from other sites don't carry it), Path=/.
  WKWebView (the Watch2Whoop app in the iOS simulator) does not store Secure cookies over http://localhost: for local development set
  BODY_WEB_INSECURE_LOCAL_COOKIE=1, and Secure is dropped only when the request Host is localhost / 127.0.0.1;
  production (tailnet HTTPS, Host is *.ts.net) is unaffected.
- A session hangs off the token that created it, permissions (read / write) follow the token; revoking the token invalidates the session immediately.
  Lifetime is SESSION_DAYS days (the browser maximum), extended on every use, so testers stay logged in for as long as the service runs; logout = delete the session.
- CSRF protection: besides SameSite=Strict, cookie-authenticated write requests (POST / PUT / PATCH / DELETE) must also carry the custom header X-Body-Web: 1
  (cross-site requests cannot add custom headers without a CORS preflight, and this service does not enable CORS).
"""
import hashlib
import os
import secrets

import psycopg
from fastapi import HTTPException, Request, Response

from .. import auth

COOKIE = "be_session"
CSRF_HEADER = "x-body-web"
SESSION_DAYS = 400   # browsers cap cookie Max-Age at 400 days; renewed on every visit, so an account that is used at all stays logged in
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _sha(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


def login(conn: psycopg.Connection, token: str, user_agent: str | None = None) -> tuple[str, dict] | None:
    """If the token is valid create a session and return (plaintext session ID, user); otherwise None."""
    row = conn.execute("""SELECT id, user_id FROM api_tokens WHERE token_sha256 = %s AND revoked_at IS NULL
                          AND NOT (%s = ANY(scopes))""",  # AI clients' MCP tokens cannot log into the web page
                       (auth.hash_token(token.strip()), auth.MCP_SCOPE)).fetchone()
    if row is None:
        return None
    session_id = secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO web_sessions (user_id, token_id, session_sha256, user_agent, expires_at)
           VALUES (%s, %s, %s, %s, now() + make_interval(days => %s))""",
        (row["user_id"], row["id"], _sha(session_id), (user_agent or "")[:300] or None, SESSION_DAYS))
    conn.execute("UPDATE api_tokens SET last_used_at = now() WHERE id = %s", (row["id"],))
    return session_id, user_for_session(conn, session_id)


def user_for_session(conn: psycopg.Connection, session_id: str) -> dict | None:
    """If the session is valid (not expired, token not revoked) extend its lifetime and return the same user dict as auth.user_for_token."""
    row = conn.execute(
        """UPDATE web_sessions s SET last_seen_at = now(), expires_at = now() + make_interval(days => %s)
           FROM api_tokens t
           WHERE s.session_sha256 = %s AND s.expires_at > now() AND t.id = s.token_id AND t.revoked_at IS NULL
           RETURNING s.user_id, t.scopes""", (SESSION_DAYS, _sha(session_id))).fetchone()
    if row is None:
        return None
    user = conn.execute("SELECT * FROM users WHERE id = %s", (row["user_id"],)).fetchone()
    return {**user, "token_scopes": row["scopes"], "auth": "session"}


def logout(conn: psycopg.Connection, session_id: str | None) -> None:
    if session_id:
        conn.execute("DELETE FROM web_sessions WHERE session_sha256 = %s", (_sha(session_id),))


def user_for_request(conn: psycopg.Connection, request: Request) -> dict:
    """Authenticate by cookie when there is no Bearer header (called from api.current_user)."""
    session_id = request.cookies.get(COOKIE)
    if not session_id:
        raise HTTPException(401, "缺少 Bearer token（或没有登录）")
    user = user_for_session(conn, session_id)
    if user is None:
        raise HTTPException(401, "登录已过期或已退出，请重新输入 token")
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, f"用 cookie 登录时写操作要带 {CSRF_HEADER}: 1 头")
    return user


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def cookie_secure(request: Request | None) -> bool:
    """Whether the cookie carries Secure: yes by default; no only when the local-dev switch is on and the request goes to localhost (see module docstring)."""
    if request is None or os.environ.get("BODY_WEB_INSECURE_LOCAL_COOKIE") != "1":
        return True
    return (request.url.hostname or "").lower() not in LOCAL_HOSTS


def set_cookie(response: Response, session_id: str, request: Request | None = None) -> None:
    response.set_cookie(COOKIE, session_id, max_age=SESSION_DAYS * 86400, path="/",
                        httponly=True, secure=cookie_secure(request), samesite="strict")


def clear_cookie(response: Response, request: Request | None = None) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=cookie_secure(request), samesite="strict")
