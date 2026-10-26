"""HTTP API. All /v1 endpoints need a Bearer token (the Today page uses the cookie set at login, see web/session.py); the user in the path may be 'me'."""
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.routing import Route

from . import auth, errors_en, obs, plugins, queries
from .i18n import LangMiddleware, is_en
from .account import api as account_api
from .ai import naming, parity
from .ai.api import router as ai_router
from .ai.mcp_http import mcp_app
from .ai.oauth_api import router as oauth_router, well_known as oauth_well_known
from .ai.oauth_http import oauth_app
from .rollout.api import router as rollout_router
from .static_bundle.api import router as static_bundle_router
from .assistant.memory_api import router as memory_router
from .assistant.diet_profile_api import router as diet_profile_router
from .assistant.recipes_api import router as recipes_router
from .assistant.router import router as assistant_router
from .config import settings
from .scores import store as scores_store
from .insights.api import router as insights_router
from .records.api import router as records_router
from .rules.api import router as rules_router
from .goals.api import router as goals_router
from .sync import ow_watch
from .sync.api import router as sync_router
from .history.api import router as history_router
from .profile.api import router as profile_router
from .explain.api import router as explain_router
from .rawdata.api import ingest_router as rawdata_ingest_router, router as rawdata_router
from .cardio.api import router as cardio_router
from .water.api import router as water_router
from .appearance.api import router as appearance_router
from .offline.api import router as offline_router
from .sleep_night.api import router as sleep_night_router
from .ui.api import router as ui_router
from .training.api import router as training_router
from .grocery.api import router as grocery_router
from .feedback.api import router as feedback_router
from .avatars.api import router as avatars_router
from .friends.api import router as friends_router
from .friends.feed_api import router as friends_feed_router
from .notify.api import router as notify_router
from .notices.api import router as notices_router
from .web import api as web_api, session as web_session

pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    obs.install_file_log()   # BODY_LOG_DIR: the app log survives container rebuilds (obs.py)
    # Streaming responses of in-page chat on the Today page hold a connection until done, so leave more than the original 5
    pool = ConnectionPool(settings.dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=10, open=True)
    # remote MCP session manager (/mcp); jobs of BODY_PLUGIN_DIR plugins; new OW uploads -> scores within minutes (sync/ow_watch.py)
    async with mcp_app.lifespan(), plugins.scheduler(), ow_watch.watcher():
        yield
    pool.close()


app = FastAPI(title="Body Engine", version="0.1.0", lifespan=lifespan)
app.add_middleware(LangMiddleware)   # X-Body-Lang from the Today page -> texts it shows in that language (i18n.py)
app.add_middleware(obs.UserHeaderMiddleware)   # X-Body-User: the authenticated user id, for the proxy's access log (obs.py)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    """FastAPI's own handler, with the Chinese detail translated when the request asked for English (errors_en.py).
    With ai_parity on for the signed-in user, the back end's name is taken out of the text (ai/naming.py)."""
    if is_en() and isinstance(exc.detail, (str, dict)):   # dict = {"code", "message", …} (AI chat / voice)
        exc = HTTPException(exc.status_code, errors_en.translate_detail(exc.detail), headers=exc.headers)
    if getattr(request.state, "ai_parity", False):
        d = exc.detail
        d = (naming.scrub(d, output=True) if isinstance(d, str) else
             {k: naming.scrub(v, output=True) if k in ("message", "detail") else v for k, v in d.items()} if isinstance(d, dict)
             else d)
        exc = HTTPException(exc.status_code, d, headers=exc.headers)
    return await http_exception_handler(request, exc)


def get_conn():
    with pool.connection() as conn:
        yield conn


def current_user(request: Request, conn=Depends(get_conn), authorization: Annotated[str | None, Header()] = None) -> dict:
    """Bearer token or Today page cookie. The AI-client-only MCP token (scope mcp) is invalid here; it only works on /mcp."""
    if authorization is None:  # no Bearer header: Today page cookie login
        user = web_session.user_for_request(conn, request)
    elif not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少 Bearer token")
    else:
        user = auth.user_for_token(conn, authorization.removeprefix("Bearer ").strip())
        if user is None:
            raise HTTPException(401, "token 无效或已吊销")
    # Self-signed-up accounts waiting for an invite code only reach /v1/account*; disabled ones nothing (account/api.py)
    account_api.gate(user, request.url.path, conn)
    request.state.ai_parity = parity.on(conn, user["id"])   # error texts without the back end's name (http_error)
    return user


def resolve_owner(conn, viewer: dict, user: str, scope: str) -> int:
    owner_id = viewer["id"] if user == "me" else int(user)
    if not auth.can_view(conn, viewer["id"], owner_id, scope):
        raise HTTPException(403, f"没有查看用户 {owner_id} 的 {scope} 的授权")
    return owner_id


def date_range(conn, owner_id: int, start: date | None, end: date | None, default_days: int = 30) -> tuple[date, date]:
    """Default end = the owner's own today (their time zone)."""
    end = end or queries.local_today(conn, owner_id)
    return start or end - timedelta(days=default_days - 1), end


@app.get("/healthz")
def healthz(conn=Depends(get_conn)):
    conn.execute("SELECT 1")
    return {"ok": True}


@app.get("/v1/me")
def me(viewer=Depends(current_user)):
    return {k: viewer.get(k) for k in ("id", "display_name", "role", "timezone", "token_scopes", "status")}


@app.get("/v1/users/{user}/metrics")
def list_metrics(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    return queries.metric_names(conn, resolve_owner(conn, viewer, user, "metrics"))


@app.get("/v1/users/{user}/daily-metrics")
def daily_metrics(user: str, start: date | None = None, end: date | None = None,
                  metric: Annotated[list[str] | None, Query()] = None,
                  conn=Depends(get_conn), viewer=Depends(current_user)):
    owner_id = resolve_owner(conn, viewer, user, "metrics")
    return queries.daily_metrics(conn, owner_id, *date_range(conn, owner_id, start, end), metric)


@app.get("/v1/users/{user}/sleep")
def sleep(user: str, start: date | None = None, end: date | None = None, main_only: bool = False,
          conn=Depends(get_conn), viewer=Depends(current_user)):
    owner_id = resolve_owner(conn, viewer, user, "sleep")
    return queries.sleep_sessions(conn, owner_id, *date_range(conn, owner_id, start, end), main_only)


@app.get("/v1/users/{user}/scores")
def scores(user: str, start: date | None = None, end: date | None = None, details: bool = False,
           conn=Depends(get_conn), viewer=Depends(current_user)):
    owner_id = resolve_owner(conn, viewer, user, "scores")
    return scores_store.scores(conn, owner_id, *date_range(conn, owner_id, start, end), details,
                               not details or auth.can_view(conn, viewer["id"], owner_id, "sleep"))


app.include_router(assistant_router)
app.include_router(records_router)
app.include_router(insights_router)
app.include_router(rules_router)
app.include_router(goals_router)
app.include_router(memory_router)
app.include_router(diet_profile_router)
app.include_router(recipes_router)
app.include_router(training_router)
app.include_router(grocery_router)
app.include_router(feedback_router)
app.include_router(friends_router)
app.include_router(avatars_router)
app.include_router(friends_feed_router)
app.include_router(notify_router)   # in-app notifications, push devices, settings (notify/, #175)
app.include_router(notices_router)   # one-time messages from the admin to one user (notices/)
app.include_router(ai_router)
app.include_router(history_router)
app.include_router(profile_router)
app.include_router(explain_router)
app.include_router(rawdata_router)
app.include_router(rawdata_ingest_router)   # uploads from the app (rawdata/ingest.py)
app.include_router(cardio_router)
app.include_router(water_router)   # daily water intake (water/, issue #125)
app.include_router(appearance_router)   # theme + daily quote of the Today page (appearance/, issue #126, flag slim_mode)
app.include_router(offline_router)   # weight from the Today page, queued while offline (offline/, #77)
app.include_router(sleep_night_router)   # sleep detail page (sleep_night/, flag sleep_detail)
app.include_router(ui_router)   # the AI-first page: my Today, focus, first-screen overview (ui/, flag ui_ai_first, #279)
app.include_router(account_api.router)
app.include_router(sync_router)   # on-demand refresh: app opened / pull to refresh (sync/)
# Remote MCP (Streamable HTTP): its own Bearer auth (AI-only token), see ai/mcp_http.py
app.router.routes.append(Route("/mcp", endpoint=mcp_app, methods=["GET", "POST", "DELETE"], include_in_schema=False))
app.include_router(static_bundle_router)   # versioned static data for the app: exercise library etc. (static_bundle/, flag static_bundle)
app.include_router(rollout_router)       # feature flags / A/B variants of the signed-in user (rollout/api.py)
app.include_router(oauth_router)        # MCP OAuth: consent step and connections (ai/oauth_api.py)
app.include_router(oauth_well_known)    # RFC 9728 / RFC 8414 discovery documents
app.mount("/oauth", oauth_app)          # /oauth/authorize, /token, /register, /revoke (mcp.server.auth, ai/oauth_http.py)
plugins.install(app)   # optional BODY_PLUGIN_DIR plugins: their routers and /app/plugins/… (before the /app mount)
web_api.install(app)
