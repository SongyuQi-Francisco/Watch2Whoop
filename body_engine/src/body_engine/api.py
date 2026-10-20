"""HTTP API. All /v1 endpoints need a Bearer token (the Today page uses the cookie set at login, see web/session.py); the user in the path may be 'me'."""
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from starlette.routing import Route

from . import auth, errors_en, queries
from .i18n import LangMiddleware, is_en
from .account import api as account_api
from .ai.api import router as ai_router
from .ai.mcp_http import mcp_app
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
from .history.api import router as history_router
from .profile.api import router as profile_router
from .explain.api import router as explain_router
from .rawdata.api import router as rawdata_router
from .cardio.api import router as cardio_router
from .training.api import router as training_router
from .grocery.api import router as grocery_router
from .web import api as web_api, session as web_session

pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    # Streaming responses of in-page chat on the Today page hold a connection until done, so leave more than the original 5
    pool = ConnectionPool(settings.dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=10, open=True)
    async with mcp_app.lifespan():  # session manager of the remote MCP (/mcp)
        yield
    pool.close()


app = FastAPI(title="Body Engine", version="0.1.0", lifespan=lifespan)
app.add_middleware(LangMiddleware)   # X-Body-Lang from the Today page -> texts it shows in that language (i18n.py)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    """FastAPI's own handler, with the Chinese detail translated when the request asked for English (errors_en.py)."""
    if is_en() and isinstance(exc.detail, (str, dict)):   # dict = {"code", "message", …} (AI chat / voice)
        exc = HTTPException(exc.status_code, errors_en.translate_detail(exc.detail), headers=exc.headers)
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
    account_api.gate(user, request.url.path)
    return user


def resolve_owner(conn, viewer: dict, user: str, scope: str) -> int:
    owner_id = viewer["id"] if user == "me" else int(user)
    if not auth.can_view(conn, viewer["id"], owner_id, scope):
        raise HTTPException(403, f"没有查看用户 {owner_id} 的 {scope} 的授权")
    return owner_id


def date_range(start: date | None, end: date | None, default_days: int = 30) -> tuple[date, date]:
    end = end or date.today()
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
    return queries.daily_metrics(conn, owner_id, *date_range(start, end), metric)


@app.get("/v1/users/{user}/sleep")
def sleep(user: str, start: date | None = None, end: date | None = None, main_only: bool = False,
          conn=Depends(get_conn), viewer=Depends(current_user)):
    owner_id = resolve_owner(conn, viewer, user, "sleep")
    return queries.sleep_sessions(conn, owner_id, *date_range(start, end), main_only)


@app.get("/v1/users/{user}/scores")
def scores(user: str, start: date | None = None, end: date | None = None, details: bool = False,
           conn=Depends(get_conn), viewer=Depends(current_user)):
    owner_id = resolve_owner(conn, viewer, user, "scores")
    return scores_store.scores(conn, owner_id, *date_range(start, end), details)


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
app.include_router(ai_router)
app.include_router(history_router)
app.include_router(profile_router)
app.include_router(explain_router)
app.include_router(rawdata_router)
app.include_router(cardio_router)
app.include_router(account_api.router)
# Remote MCP (Streamable HTTP): its own Bearer auth (AI-only token), see ai/mcp_http.py
app.router.routes.append(Route("/mcp", endpoint=mcp_app, methods=["GET", "POST", "DELETE"], include_in_schema=False))
web_api.install(app)
