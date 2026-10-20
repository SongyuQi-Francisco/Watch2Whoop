"""HTTP endpoints of the AI integration layer (docs/ai-assistant-plan.md section 9).

Only for managing your own: the user in the path must be me or your own ID; family sharing (shares) does not include AI settings, AI tokens or conversations.
Changes need a token with write (Today page cookie writes also need X-Body-Web: 1, web/session.py). AI-only tokens (scope mcp) are always invalid here
(api.current_user does not accept them).

- GET/POST /v1/users/{user}/ai-providers, PATCH/DELETE /v1/users/{user}/ai-providers/{id}: provider config (the key is returned as its last 4 chars only)
- GET/POST /v1/users/{user}/ai-tokens, DELETE /v1/users/{user}/ai-tokens/{id}: tokens for AI clients to connect to the remote MCP (plaintext returned once)
- POST /v1/assistant/chat, POST /v1/assistant/chat/confirm: in-Today-page chat (SSE stream)
- GET /v1/assistant/conversations, GET/DELETE /v1/assistant/conversations/{id}
- GET /v1/users/{user}/ai-fallback, POST/DELETE /v1/users/{user}/ai-fallback/consent: the fallback AI (fallback.py): quota, consent
- GET/PUT /v1/users/{user}/ai-choice: which AI Jarvis uses (choice.py): own provider / fallback / butler; takes effect at once
- POST /v1/assistant/transcribe: voice -> text (asr.py, local recognition; body = 16 kHz mono WAV made by the page)
"""
import json
import logging
import os
import sys
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import asr, catalog, chat, choice, crypto, fallback, providers, tokens

log = logging.getLogger(__name__)
router = APIRouter(tags=["ai"])


def get_conn():
    from .. import api
    with api.pool.connection() as conn:
        yield conn


def current_user(request: Request, conn=Depends(get_conn)) -> dict:
    from .. import api
    return api.current_user(request, conn, request.headers.get("authorization"))


def _self_only(viewer: dict, user: str, write: bool = False) -> int:
    if user not in ("me", str(viewer["id"])):
        raise HTTPException(403, "AI 设置只能管自己的（家人授权不包括 AI 配置）")
    if write and "write" not in (viewer.get("token_scopes") or []):
        raise HTTPException(403, "这个登录没有 write 权限")
    return viewer["id"]


def public_url(request: Request) -> str:
    """Address for MCP clients: BODY_PUBLIC_URL (the https address inside the tailnet), falling back to the request's address."""
    return (os.environ.get("BODY_PUBLIC_URL") or str(request.base_url)).rstrip("/")


def mcp_setup(request: Request, token_env: str = "BODY_ENGINE_MCP_TOKEN") -> dict:
    """How to connect the clients. Tokens are read from environment variables, never written into config files."""
    url = public_url(request) + "/mcp"
    header = '{"Authorization": "Bearer ${' + token_env + '}"}'
    return {
        "url": url, "token_env": token_env,
        "keychain": (f"security add-generic-password -a \"$USER\" -s body-engine-mcp -w   # 粘贴 token\n"
                     f"echo 'export {token_env}=$(security find-generic-password -s body-engine-mcp -w)' >> ~/.zshrc"),
        "clients": {
            "claude-code": f"claude mcp add-json --scope user body-engine '{{\"type\": \"http\", \"url\": \"{url}\", \"headers\": {header}}}'",
            "codex": f"codex mcp add body-engine --url {url} --bearer-token-env-var {token_env}",
            "gemini-cli": (f"gemini mcp add --scope user --transport http -H 'Authorization: Bearer ${{{token_env}}}' body-engine {url}\n"
                           "# 或在 ~/.gemini/settings.json 的 mcpServers 里加（Antigravity 的 MCP 设置填同样的地址和请求头）：\n"
                           + json.dumps({"body-engine": {"type": "http", "url": url, "headers": {"Authorization": f"Bearer ${{{token_env}}}"}}},
                                        ensure_ascii=False, indent=2)),
            "other": json.dumps({"mcpServers": {"body-engine": {"type": "http", "url": url,
                                                                "headers": {"Authorization": f"Bearer ${{{token_env}}}"}}}},
                                ensure_ascii=False, indent=2),
        },
        "prompt": "连上以后让 AI 先读 MCP 里的 prompt「butler」（身体管家的角色设定），或者直接说「用 body-engine 看看我今天怎么样」。",
    }


def ai_status(conn, viewer: dict) -> dict:
    e = choice.effective(conn, viewer["id"])
    return {**_ai_mode(conn, viewer, e), "choice": choice.key_of(e), "asr": asr.status(conn, viewer["id"])}


def _ai_mode(conn, viewer: dict, e: dict) -> dict:
    """Used by /v1/web/me: what the Today page Jarvis button does, from choice.effective. mode=api -> in-page chat; mcp -> subscription,
    use it in the official app; fallback -> in-page chat with the admin's DeepSeek (consent first, CNY quota);
    butler -> open assistant_url (the deployer's body butler); none -> nothing usable (point to AI settings)."""
    if e["source"] == "fallback":
        return fallback.summary(conn, viewer["id"])
    if e["source"] == "butler":
        return {"mode": "butler", "display_name": "身体管家", "assistant_url": e["url"]}
    if e["source"] == "none":
        return {"mode": "none"}
    row = e["provider"]
    return {"mode": "mcp" if catalog.is_mcp(row["kind"]) else "api", "kind": row["kind"],
            "display_name": row["display_name"], "model": row["model"], "status": row["status"],
            "vision": catalog.KINDS[row["kind"]].get("vision")}


# ---------------------------------------------------------------- providers
class ProviderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[tuple(catalog.KINDS)]
    display_name: str | None = Field(None, max_length=60)
    model: str | None = Field(None, max_length=100)
    base_url: str | None = Field(None, max_length=300)
    api_key: str | None = Field(None, max_length=512, description="只写不读：之后只返回末 4 位")
    monthly_budget_usd: float | None = Field(None, ge=0, le=10000)
    price_in_per_mtok: float | None = Field(None, ge=0, le=1000)
    price_out_per_mtok: float | None = Field(None, ge=0, le=1000)
    is_default: bool = False


class ProviderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(None, max_length=60)
    model: str | None = Field(None, max_length=100)
    base_url: str | None = Field(None, max_length=300)
    api_key: str | None = Field(None, max_length=512)
    monthly_budget_usd: float | None = Field(None, ge=0, le=10000)
    price_in_per_mtok: float | None = Field(None, ge=0, le=1000)
    price_out_per_mtok: float | None = Field(None, ge=0, le=1000)
    status: Literal["active", "disabled"] | None = None
    is_default: bool | None = None


def _bad(fn, *a):
    try:
        return fn(*a)
    except crypto.MasterKeyMissing as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/v1/users/{user}/ai-providers")
def list_providers(user: str, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self_only(viewer, user)
    return {"providers": providers.list_providers(conn, uid), "month_cost_usd": round(providers.month_cost(conn, uid), 4),
            "kinds": catalog.public_catalog(uid), "mcp": mcp_setup(request), "fallback": _fallback_view(conn, viewer)}


@router.post("/v1/users/{user}/ai-providers", status_code=201)
def create_provider(user: str, body: ProviderIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self_only(viewer, user, write=True)
    data = body.model_dump(exclude_unset=True)
    row = _bad(providers.create_provider, conn, uid, {**data, "kind": body.kind})
    if body.is_default:  # "use it for Jarvis" was ticked: an explicit pick, wins over a fallback / butler pick
        choice.set_pref(conn, uid, "provider")
    return row


@router.patch("/v1/users/{user}/ai-providers/{provider_id}")
def patch_provider(user: str, provider_id: int, body: ProviderPatch, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self_only(viewer, user, write=True)
    row = _bad(providers.update_provider, conn, uid, provider_id, body.model_dump(exclude_unset=True))
    if row is None:
        raise HTTPException(404, "没有这个提供商（或不是你的）")
    if body.is_default:
        choice.set_pref(conn, uid, "provider")
    return row


@router.delete("/v1/users/{user}/ai-providers/{provider_id}", status_code=204)
def delete_provider(user: str, provider_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self_only(viewer, user, write=True)
    if not providers.delete_provider(conn, uid, provider_id):
        raise HTTPException(404, "没有这个提供商（或不是你的）")
    return Response(status_code=204)


# ---------------------------------------------------------------- which AI Jarvis uses
class ChoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=20, description="fallback / butler / p:<provider id>")


@router.get("/v1/users/{user}/ai-choice")
def get_choice(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self_only(viewer, user)
    return {"options": choice.options(conn, uid), "ai": ai_status(conn, viewer)}


@router.put("/v1/users/{user}/ai-choice")
def put_choice(user: str, body: ChoiceIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Pick what Jarvis uses; the answer carries the new /v1/web/me `ai` and `assistant_url` so the page switches without reloading."""
    uid = _self_only(viewer, user, write=True)
    try:
        choice.choose(conn, uid, body.key)
    except ValueError as e:
        raise HTTPException(422, str(e))
    ai = ai_status(conn, viewer)
    return {"options": choice.options(conn, uid), "ai": ai, "assistant_url": ai.get("assistant_url")}


# ---------------------------------------------------------------- fallback AI
def _fallback_view(conn, viewer: dict) -> dict | None:
    """The fallback row in "AI settings": None when it is not set up on this server. The owner (who pays) also sees the balance."""
    if fallback.read_key() is None:
        return None
    s = fallback.summary(conn, viewer["id"])
    s["used"] = choice.effective(conn, viewer["id"])["source"] == "fallback"
    s["points"] = fallback.consent_points(conn, viewer["id"])
    if viewer.get("role") == "owner":
        st = fallback.state(conn)
        s["balance_cny"] = float(st["balance_cny"]) if st.get("balance_cny") is not None else None
    return s


class FallbackConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=40)
    agree: Literal[True]


@router.get("/v1/users/{user}/ai-fallback")
def get_fallback(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    _self_only(viewer, user)
    view = _fallback_view(conn, viewer)
    if view is None:
        raise HTTPException(404, "这台服务器没有开通托底 AI")
    return view


@router.post("/v1/users/{user}/ai-fallback/consent")
def accept_fallback(user: str, body: FallbackConsentIn, request: Request, conn=Depends(get_conn),
                    viewer=Depends(current_user)):
    uid = _self_only(viewer, user, write=True)
    try:
        fallback.accept_consent(conn, uid, body.version, request.headers.get("user-agent"))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return _fallback_view(conn, viewer) or {}


@router.delete("/v1/users/{user}/ai-fallback/consent", status_code=204)
def revoke_fallback(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    fallback.revoke_consent(conn, _self_only(viewer, user, write=True))
    return Response(status_code=204)


# ---------------------------------------------------------------- AI client tokens (remote MCP)
class AiTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client: str = Field(min_length=1, max_length=31, description="claude-code / codex / gemini-cli / …")
    read_only: bool = False


@router.get("/v1/users/{user}/ai-tokens")
def list_ai_tokens(user: str, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    return {"tokens": tokens.list_tokens(conn, _self_only(viewer, user)), "mcp": mcp_setup(request)}


@router.post("/v1/users/{user}/ai-tokens", status_code=201)
def create_ai_token(user: str, body: AiTokenIn, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self_only(viewer, user, write=True)
    return {**_bad(tokens.create, conn, uid, body.client, body.read_only), "mcp": mcp_setup(request)}


@router.delete("/v1/users/{user}/ai-tokens/{token_id}", status_code=204)
def revoke_ai_token(user: str, token_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    if not tokens.revoke(conn, _self_only(viewer, user, write=True), token_id):
        raise HTTPException(404, "没有这个 AI token（或已吊销）")
    return Response(status_code=204)


# ---------------------------------------------------------------- chat
class ImageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_type: str = Field(max_length=40)
    data: str = Field(max_length=7_200_000, description="base64，不带 data: 前缀")


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field("", max_length=chat.MAX_TEXT)
    images: list[ImageIn] = Field(default_factory=list, max_length=chat.MAX_IMAGES)
    conversation_id: int | None = None


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: int
    approve: bool


def _sse(gen, cm):
    """Turn events into SSE; on error also send an error event; finally return the connection (committing the completed part)."""
    try:
        for e in gen:
            yield f"event: {e['event']}\ndata: {json.dumps(e['data'], ensure_ascii=False, default=str)}\n\n"
    except Exception as e:  # Do not send internal details (may contain paths etc.) to the frontend
        log.exception("chat stream failed")
        print(f"chat stream failed: {type(e).__name__}: {e}", file=sys.stderr)
        yield f"event: error\ndata: {json.dumps({'code': 'internal', 'message': '出错了，稍后再试'})}\n\n"
    finally:
        cm.__exit__(None, None, None)


def _stream(start):
    """start(conn) does the pre-checks and returns the event generator; a failed check returns an HTTP error directly. The connection follows the stream and is returned only when the stream ends."""
    from .. import api
    cm = api.pool.connection()
    conn = cm.__enter__()
    try:
        gen = start(conn)
    except chat.ChatError as e:
        cm.__exit__(type(e), e, e.__traceback__)
        raise HTTPException(e.status, {"code": e.code, "message": e.message, **e.extra})
    except BaseException as e:
        cm.__exit__(type(e), e, e.__traceback__)
        raise
    return StreamingResponse(_sse(gen, cm), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"})


@router.post("/v1/assistant/chat")
def post_chat(body: ChatIn, viewer=Depends(current_user)):
    """In-Today-page chat: uses the current user's own default API provider. Returns text/event-stream:
    conversation -> text (many) -> tool / usage -> confirm (a write awaits confirmation, the stream ends here) or done; error on failure.
    Not set up / subscription type / budget used up: 409 / 402, detail = {code, message, assistant_url?}."""
    # A read-only login can chat too (read tools work as usual), write tools are rejected (chat.py decides by token_scopes)
    return _stream(lambda conn: chat.chat_events(conn, viewer, body.message,
                                                 [i.model_dump() for i in body.images], body.conversation_id))


@router.post("/v1/assistant/chat/confirm")
def post_confirm(body: ConfirmIn, viewer=Depends(current_user)):
    """Confirm / reject a pending write, then continue the conversation (also SSE)."""
    def start(conn):
        row, conv, provider, adapter = chat.prepare_confirm(conn, viewer, body.call_id, body.approve)
        return chat.confirm_events(conn, viewer, row, conv, provider, adapter, body.approve)
    return _stream(start)


@router.post("/v1/assistant/transcribe")
async def transcribe(request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Voice -> text. Body: audio/wav (PCM16 mono, <= 60 s). Returns {text, seconds, used_today, per_day}; the page puts the text
    in the input box for the user to check before sending. The audio is never stored. Errors: detail = {code, message}."""
    if int(request.headers.get("content-length") or 0) > asr.MAX_BYTES:
        raise HTTPException(413, {"code": "too_large", "message": f"录音太大（最长 {asr.MAX_SECONDS} 秒）"})
    data = await request.body()
    try:
        return await run_in_threadpool(asr.transcribe, conn, viewer["id"], data)
    except asr.AsrError as e:
        raise HTTPException(e.status, {"code": e.code, "message": e.message})


@router.get("/v1/assistant/conversations")
def conversations(conn=Depends(get_conn), viewer=Depends(current_user)):
    return chat.list_conversations(conn, viewer["id"])


@router.get("/v1/assistant/conversations/{conv_id}")
def conversation(conv_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    try:
        return chat.conversation_view(conn, viewer["id"], conv_id)
    except chat.ChatError as e:
        raise HTTPException(e.status, e.message)


@router.delete("/v1/assistant/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    if not chat.delete_conversation(conn, viewer["id"], conv_id):
        raise HTTPException(404, "没有这个对话")
    return Response(status_code=204)

