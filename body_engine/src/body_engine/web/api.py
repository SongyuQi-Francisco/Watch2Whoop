"""Endpoints and static files for the Today page.

- POST /v1/web/login {"token"}: validate an API token and exchange it for an httpOnly cookie (session.py); POST /v1/web/logout signs out.
- POST /v1/web/activate {"code", "web"?}: reusable activation code → fresh watch2whoop://setup link (activation.py; page
  /app/activate.html#<code>). web=true (the page signing in on the web) mints only the login code, no OW invitation, so a
  link the app may still be opening keeps a valid OW code.
- POST /v1/web/ingest-token {"device"}: the Watch2Whoop app (in its WebView, with the page's cookie) gets an upload-only
  token for POST /v1/ingest/healthkit (rawdata/ingest.py); setup-link users have no account token of their own.
- GET /v1/web/me: current user + family members that can be switched to (shares), the "Ask the butler" (「问管家」) link, versions,
  feature flags on for this user and their A/B variants (rollout/); also extends the cookie's lifetime.
- GET /v1/users/{user}/today, /sleep/nights, /trends, /days: aggregate endpoints (views.py), shares checked per block.
- /app/: PWA static files (static/, no build step); / redirects to /app/.
- /app/media/: exercise demo images in the deployment's own media storage (BODY_MEDIA_DIR; training/media.py downloads them once).
"""
import json
import re
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .. import auth
from ..training import catalog as tr_catalog
from . import activation, login_codes, login_limit, session, views
from ..records.defaults import with_label
from ..rollout import api as rollout_api

STATIC = Path(__file__).parent / "static"
WEB_VERSION = "v2.99-2026-10-22"   # Today page frontend version (bump whenever the page changes)


router = APIRouter(tags=["web"])

# Inline style allowed: progress-bar widths use the style attribute; scripts only from this site.
# blob: for images / media: Jarvis reads a picked photo or video through an object URL to shrink it / take frames (ai-media.js)
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
       "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


# Dependencies: fetch api.py's connection pool and current_user at request time (same as records/api.py, avoids circular imports)
def get_conn():
    from .. import api
    with api.pool.connection() as conn:
        yield conn


def current_user(request: Request, conn=Depends(get_conn)) -> dict:
    from .. import api
    return api.current_user(request, conn, request.headers.get("authorization"))


def owner(conn, viewer: dict, user: str) -> int:
    try:
        owner_id = viewer["id"] if user == "me" else int(user)
    except ValueError:
        raise HTTPException(404, "用户写 me 或数字 ID")
    if not views.has_any_share(conn, viewer["id"], owner_id):
        raise HTTPException(403, f"没有查看用户 {owner_id} 的授权")
    return owner_id


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=8, max_length=200)


def _me(user: dict) -> dict:
    out = {k: user.get(k) for k in ("id", "display_name", "role", "timezone", "token_scopes", "status", "language")}
    out["intro_seen"] = user.get("intro_seen_at") is not None   # first-run intro (issue #60, profile/api.py PUT /intro)
    return out


@router.post("/v1/web/login")
def login(body: LoginIn, request: Request, response: Response, conn=Depends(get_conn)):
    """Log into the web page with your own API token: on success set an httpOnly cookie that later requests rely on.
    Too many failures from one IP return 429 first (login_limit) without hitting the database."""
    ip = request.client.host if request.client else "?"
    wait = login_limit.retry_after(ip)
    if wait:
        raise HTTPException(429, "登录失败次数太多，请稍后再试", headers={"Retry-After": str(wait)})
    res = session.login(conn, body.token, request.headers.get("user-agent"))
    if res is None:
        login_limit.record_failure(ip)
        raise HTTPException(401, "token 无效或已吊销")
    login_limit.record_success(ip)
    session_id, user = res
    session.set_cookie(response, session_id, request)
    return _me(user)


class LoginCodeIn(BaseModel):
    code: str = Field(min_length=8, max_length=40)


@router.post("/v1/web/login-code")
def login_with_code(body: LoginCodeIn, request: Request, response: Response, conn=Depends(get_conn)):
    """Log in with a one-time login code (from the Watch2Whoop app's setup info / QR code): issue a token specific to this device and set the same cookie."""
    res = login_codes.redeem(conn, body.code, request.headers.get("user-agent"))
    if res is None:
        raise HTTPException(401, "登录码无效、已用过或已过期")
    session_id, user = res
    session.set_cookie(response, session_id, request)
    return _me(user)


class ActivateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=8, max_length=40)
    web: bool = False   # activate.html signing in on the web: mint only a Today page login code (no OW invitation)


@router.post("/v1/web/activate")
def activate(body: ActivateIn, request: Request, conn=Depends(get_conn)):
    """Redeem a reusable activation code (activate.html#<code>) for a fresh watch2whoop://setup link (activation.py).
    No auth; failures are a generic 401 and count against the per-IP login failure limit (429 once exceeded)."""
    ip = request.client.host if request.client else "?"
    wait = login_limit.retry_after(ip)
    if wait:
        raise HTTPException(429, "失败次数太多，请稍后再试", headers={"Retry-After": str(wait)})
    res = activation.activate(conn, body.code, request.headers.get("user-agent"), str(request.base_url), mint_ow=not body.web)
    if res is None:
        login_limit.record_failure(ip)
        raise HTTPException(401, "激活码无效或已停用")
    return res


class IngestTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str = Field("iPhone", min_length=1, max_length=40)


@router.post("/v1/web/ingest-token")
def ingest_token(body: IngestTokenIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Upload-only token (scope ingest) for the app's raw data uploads. Cookie session with write permission only; a new
    token for the same device replaces the old one."""
    if viewer.get("auth") != "session":
        raise HTTPException(403, "只能在今天页登录后获取")
    if "write" not in (viewer.get("token_scopes") or []):
        raise HTTPException(403, "这个登录没有 write 权限")
    name = f"app:ingest:{body.device.strip()}"
    auth.revoke_tokens(conn, viewer["id"], name)
    token = auth.create_token(conn, viewer["id"], name, [auth.INGEST_SCOPE])
    conn.commit()
    return {"token": token, "scopes": [auth.INGEST_SCOPE]}


@router.post("/v1/web/logout", status_code=204)
def logout(request: Request, conn=Depends(get_conn)):
    session.logout(conn, request.cookies.get(session.COOKIE))
    response = Response(status_code=204)
    session.clear_cookie(response, request)
    return response


# ---- offline shell for the Watch2Whoop app (docs/offline-plan.md, section 5)
# The app's WKWebView has no service worker, so a cold start without network cannot open /app/. The app keeps this one
# file (index.html with every local stylesheet and script inlined) and opens it with loadHtmlString(baseUrl = the page's
# address): same origin, so the page reads the copies offline.js kept in IndexedDB. window.W2W_SHELL tells the page it
# runs from the copy: it sends nothing and asks the app to load the real page once the network is back. Public like the
# other static files; no user data in it.
_SCRIPT = re.compile(r'<script src="([\w.-]+\.js)"( defer)?></script>')
_STYLE = re.compile(r'<link rel="stylesheet" href="([\w.-]+\.css)">')
_shell_cache: dict[str, str] = {}


def offline_shell_html() -> str:
    if WEB_VERSION not in _shell_cache:
        def read(m: re.Match, tag: str) -> str:   # a closing tag inside the text would end the element early
            return (STATIC / m.group(1)).read_text(encoding="utf-8").replace(f"</{tag}", f"<\\/{tag}")
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        html = _SCRIPT.sub(lambda m: f"<script>{read(m, 'script')}\n</script>", html)
        html = _STYLE.sub(lambda m: f"<style>{read(m, 'style')}</style>", html)
        marker = f'<script>window.W2W_SHELL = {json.dumps(WEB_VERSION)};</script>\n'
        _shell_cache.clear()
        _shell_cache[WEB_VERSION] = html.replace("</head>", marker + "</head>", 1)
    return _shell_cache[WEB_VERSION]


@router.get("/app/offline-shell.html", include_in_schema=False)
def offline_shell():
    return Response(offline_shell_html(), media_type="text/html; charset=utf-8",
                    headers={"X-Web-Version": WEB_VERSION})


@router.get("/v1/web/me")
def web_me(request: Request, response: Response, conn=Depends(get_conn), viewer=Depends(current_user)):
    if viewer.get("auth") == "session":  # Renew: recompute the cookie Max-Age from now
        session.set_cookie(response, request.cookies[session.COOKIE], request)
    from .. import api
    from ..ai.api import ai_status  # AI integration layer: which AI Jarvis uses (ai/choice.py); the butler link only when that is the pick
    ai = ai_status(conn, viewer)
    return {"me": _me(viewer), "viewable": views.viewable(conn, viewer),
            "assistant_url": ai.get("assistant_url"), "ai": ai,
            "today": views.local_today(conn, viewer["id"]),
            **rollout_api.for_user(conn, viewer["id"]),   # feature flags on for me, my A/B variants (rollout/)
            "version": {"web": WEB_VERSION, "api": api.app.version}}


@router.get("/v1/users/{user}/today")
def today(user: str, day: Annotated[date | None, Query(alias="date")] = None,
          conn=Depends(get_conn), viewer=Depends(current_user)):
    """Everything the Today page needs in one call: scores (recovery, sleep, yesterday's strain, CTL/ATL/TSB interpretation, empty scores carry a reason), today's reminders,
    today's planned training (banned / caution exercises, load suggestions), today's nutrition vs targets. Each block is authorised separately by shares."""
    return views.today(conn, viewer, owner(conn, viewer, user), day)


@router.get("/v1/users/{user}/training/week")
def training_week(user: str, day: Annotated[date | None, Query(alias="date")] = None,
                  conn=Depends(get_conn), viewer=Depends(current_user)):
    """Record tab, training page: this week's plan (Monday–Sunday), load suggestions of the last 7 days, caution / banned exercises in the plan (scope training)."""
    owner_id = owner(conn, viewer, user)
    d = day or views.local_today(conn, owner_id)
    return views.section(conn, viewer["id"], owner_id, "training", lambda: views.training_week(conn, viewer["id"], owner_id, d))


@router.get("/v1/users/{user}/journal/frequent")
def journal_frequent(user: str, end: date | None = None, days: Annotated[int, Query(ge=7, le=365)] = 30,
                     limit: Annotated[int, Query(ge=1, le=30)] = 8, conn=Depends(get_conn), viewer=Depends(current_user)):
    """The "frequent" row on the log page: journal items logged most in the last `days` days (up to `limit`), padded in default order to at least 6 (scope journal)."""
    owner_id = owner(conn, viewer, user)
    if not views.auth.can_view(conn, viewer["id"], owner_id, "journal"):
        raise HTTPException(403, f"没有查看用户 {owner_id} 的 journal 的授权")
    return with_label(views.frequent_journal(conn, owner_id, end, days, limit, min(6, limit)))


@router.get("/v1/users/{user}/sleep/nights")
def sleep_nights(user: str, end: date | None = None, days: Annotated[int, Query(ge=1, le=60)] = 14,
                 conn=Depends(get_conn), viewer=Depends(current_user)):
    """Last N nights (by wake-up date): main sleep's bedtime / wake time (user timezone), minutes per stage, naps, sleep score and sleep need."""
    owner_id = owner(conn, viewer, user)
    if not views.auth.can_view(conn, viewer["id"], owner_id, "sleep"):
        raise HTTPException(403, f"没有查看用户 {owner_id} 的 sleep 的授权")
    return views.sleep_nights(conn, viewer, owner_id, end, days)


@router.get("/v1/users/{user}/trends")
def trends(user: str, end: date | None = None, days: Annotated[int, Query(ge=7, le=365)] = 90,
           conn=Depends(get_conn), viewer=Depends(current_user)):
    """Trends tab: every WHOOP metric as a daily series ending on `end` (recovery, HRV, resting HR, respiration, SpO2, wrist temperature,
    sleep need / debt / performance, restorative sleep, strain + target band, CTL / ATL / TSB, aerobic minutes, steps, energy, stress,
    resilience, VO2max, weight, body age …) with a 7-day rolling mean and the previous period's mean, plus status cards as of `end`
    (HRV status, training status, load focus, resilience, chronotype, illness risk, body battery, recovery time). See trends/series.py."""
    return views.trends(conn, viewer, owner(conn, viewer, user), end, days)


@router.get("/v1/users/{user}/days")
def month_days(user: str, month: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
         conn=Depends(get_conn), viewer=Depends(current_user)):
    """Today page look-back calendar: for each day of `month` (YYYY-MM, default this month) whether scores / sleep / training / daily metrics exist, the recovery score and colour,
    and the earliest day with data (first). Each category is authorised by shares, same auth as /today."""
    owner_id = owner(conn, viewer, user)
    return views.month_days(conn, viewer, owner_id, month or views.local_today(conn, owner_id).strftime("%Y-%m"))


class MediaFiles(StaticFiles):
    """Exercise demo images (BODY_MEDIA_DIR/…): the directory is looked up per request, so it may appear after start-up."""

    def __init__(self):
        super().__init__(directory=tr_catalog.media_dir(), check_dir=False)

    async def check_config(self) -> None:   # no media downloaded yet = every image is a 404, not a server error
        return None

    def lookup_path(self, path: str):
        self.all_directories = [str(tr_catalog.media_dir())]
        return super().lookup_path(path)


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/app/media/"):   # exercise demo images: pinned upstream commit, never change
        response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
    elif path.startswith("/app"):
        response.headers["Content-Security-Policy"] = CSP
        response.headers["Cache-Control"] = "no-cache"  # Revalidate with the server every time (offline is covered by the service worker)
    if path.startswith("/v1/"):
        response.headers.setdefault("Cache-Control", "private, no-store")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def install(app: FastAPI) -> None:
    """Call once from api.py: endpoints, static files, security headers."""
    app.include_router(router)
    # Exercise demo images downloaded once by `body-engine fetch-exercise-db --images` (training/media.py); mounted before /app
    app.mount("/app/media", MediaFiles(), name="media")
    app.mount("/app", StaticFiles(directory=STATIC, html=True), name="web")
    app.add_api_route("/", lambda: RedirectResponse("/app/"), include_in_schema=False)
    app.middleware("http")(security_headers)
