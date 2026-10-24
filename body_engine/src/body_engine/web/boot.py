"""Start-up bundle of the Today page (docs/ios-native-plan.md, section 3.2; rollout flag boot_api).

POST /v1/web/boot {"paths": [...]} answers several of the page's own GET requests in one round trip:
{"today": "YYYY-MM-DD", "items": [{"path", "status", "body"}, ...]}. The page sends the GET paths it asked for during its
last start-up (app.js, "boot bundle"); dates in them are templates resolved against the signed-in user's own today:
{today}, {d:-6} (= today - 6 days).

Each path is answered by running that very endpoint inside this request (same cookie / Bearer header, same language,
same share checks, same body and status), so nothing here re-implements authorisation and an old endpoint keeps working
on its own. /v1/web/me runs first (its `today` resolves the templates, its flags decide whether this endpoint is on), the
rest in parallel; one path failing or timing out only leaves that item with its error status, never the whole answer.
Only GET paths of the signed-in user's own data are accepted (PATHS_RE), at most MAX_PATHS.
"""
import asyncio
import json
import re
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

FEATURE = "boot_api"
ME = "/v1/web/me"
MAX_PATHS = 24
TIMEOUT_S = 10.0   # one item; the page asks for that item again on its own when it is missing
# Own data only ("me"; friends = my own circles), plus the account-level reads the page makes at start-up
PATHS_RE = re.compile(r"^/v1/(web/me|auth/methods|account|friends(/[A-Za-z0-9_\-/]+)?|users/me/[A-Za-z0-9_\-/.]+)"
                      r"(\?[A-Za-z0-9_\-=&%.:,+]*)?$")
TEMPLATE_RE = re.compile(r"\{(today|d:(-?\d{1,3}))\}")
SKIP_HEADERS = {b"content-length", b"content-type", b"transfer-encoding", b"accept-encoding", b"x-body-web"}

router = APIRouter(tags=["web"])


class BootIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] = Field(default_factory=list, max_length=MAX_PATHS)


def resolve(template: str, today: date) -> str | None:
    """A path template -> the path for this user's today, or None when it is not one the bundle may answer."""
    if len(template) > 400 or ".." in template:
        return None
    path = TEMPLATE_RE.sub(lambda m: (today if m.group(1) == "today" else today + timedelta(days=int(m.group(2)))).isoformat(),
                           template)
    return path if PATHS_RE.match(path) else None


async def _get(request: Request, path_qs: str) -> tuple[int, list, bytes]:
    """Run GET path_qs through the whole app (middleware, auth, the endpoint) with this request's headers."""
    path, _, qs = path_qs.partition("?")
    headers = [(k, v) for k, v in request.scope["headers"] if k not in SKIP_HEADERS]
    scope = {**{k: v for k, v in request.scope.items() if k in ("type", "asgi", "http_version", "scheme", "server", "client",
                                                                 "root_path", "extensions")},
             "method": "GET", "path": path, "raw_path": path.encode(), "query_string": qs.encode(),
             "headers": headers, "state": dict(request.scope.get("state") or {})}
    sent = False
    out = {"status": 500, "headers": [], "body": b""}

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()   # never a disconnect: the caller cancels this when the response is done
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"], out["headers"] = msg["status"], msg.get("headers", [])
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    await request.app(scope, receive, send)
    return out["status"], out["headers"], out["body"]


async def _item(request: Request, path: str) -> dict:
    try:
        status, headers, body = await asyncio.wait_for(_get(request, path), TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"path": path, "status": 504, "body": None, "cookies": []}
    except Exception:   # noqa: BLE001  one block failing never fails the bundle
        return {"path": path, "status": 500, "body": None, "cookies": []}
    try:
        data = json.loads(body) if body else None
    except ValueError:
        data = None
    return {"path": path, "status": status, "body": data,
            "cookies": [v.decode("latin-1") for k, v in headers if k == b"set-cookie"]}


@router.post("/v1/web/boot")
async def boot(request: Request, body: BootIn):
    """The page's start-up reads in one round trip (rollout flag boot_api; 404 while off: the page then asks one by one)."""
    templates = list(dict.fromkeys(p for p in body.paths if p != ME))
    first = [t for t in templates if not TEMPLATE_RE.search(t)]
    later = [t for t in templates if TEMPLATE_RE.search(t)]
    plain = [p for p in (resolve(t, date.today()) for t in first) if p]   # no date in them: start with /v1/web/me
    me, *rest = await asyncio.gather(_item(request, ME), *(_item(request, p) for p in plain))
    if me["status"] != 200 or not isinstance(me["body"], dict):
        return JSONResponse(me["body"] or {"detail": "boot"}, status_code=me["status"] if me["status"] >= 400 else 500)
    if FEATURE not in (me["body"].get("flags") or []):
        raise HTTPException(404, "Not Found")
    today = date.fromisoformat(str(me["body"]["today"]))
    dated = list(dict.fromkeys(p for p in (resolve(t, today) for t in later) if p and p not in plain))
    rest += await asyncio.gather(*(_item(request, p) for p in dated))
    items = [me, *rest]
    resp = JSONResponse({"today": today.isoformat(),
                         "items": [{k: it[k] for k in ("path", "status", "body")} for it in items]})
    for it in items:   # /v1/web/me renews the login cookie
        for c in it["cookies"]:
            resp.headers.append("set-cookie", c)
    return resp
