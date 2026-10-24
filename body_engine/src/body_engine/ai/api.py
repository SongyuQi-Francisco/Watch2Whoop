"""HTTP endpoints of the AI integration layer (docs/ai-assistant-plan.md section 9).

Only for managing your own: the user in the path must be me or your own ID; family sharing (shares) does not include AI settings, AI tokens or conversations.
Changes need a token with write (Today page cookie writes also need X-Body-Web: 1, web/session.py). AI-only tokens (scope mcp) are always invalid here
(api.current_user does not accept them).

- GET/POST /v1/users/{user}/ai-providers, PATCH/DELETE /v1/users/{user}/ai-providers/{id}: provider config (the key is returned as its last 4 chars only)
- GET/POST /v1/users/{user}/ai-tokens, DELETE /v1/users/{user}/ai-tokens/{id}: tokens for AI clients to connect to the remote MCP (plaintext returned once)
- POST /v1/assistant/chat, POST /v1/assistant/chat/confirm: in-Today-page chat (SSE stream; the turn runs on without the connection, runs.py)
- GET /v1/assistant/chat/{conversation_id}/events?after=N: resume a turn's stream after the page was in the background
- GET /v1/assistant/conversations, GET/DELETE /v1/assistant/conversations/{id}
- GET /v1/users/{user}/ai-fallback, POST/DELETE /v1/users/{user}/ai-fallback/consent: the fallback AI (fallback.py): quota, consent
- GET /v1/users/{user}/ai-cards, POST /v1/users/{user}/ai-cards/{id}/use: Jarvis quota cards (quota_cards.py, flag ai_cards, #182)
- GET/PUT /v1/users/{user}/ai-choice: which AI Jarvis uses (choice.py): own provider / fallback / butler; takes effect at once
- POST /v1/assistant/transcribe: voice -> text (asr.py, local recognition; body = 16 kHz mono WAV made by the page);
  ?fix=1 and POST /v1/assistant/voice-fix: correction with the user's own terms (voice_fix.py, flag jarvis_orbit)
"""
import json
import logging
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..i18n import L, lang
from ..rollout import flags
from . import asr, catalog, chat, choice, crypto, fallback, oauth, providers, quota_cards, runs, tokens, voice_fix
from . import quota_cards_params as qcp
from .web import mcp as web_mcp
from . import params as ai_params

log = logging.getLogger(__name__)
router = APIRouter(tags=["ai"])


def get_conn():
    from .. import api
    with api.pool.connection() as conn:
        yield conn


def current_user(request: Request, conn=Depends(get_conn)) -> dict:
    from .. import api
    return api.current_user(request, conn, request.headers.get("authorization"))


def stream_user(request: Request) -> dict:
    """current_user on a connection of its own, committed and returned at once, for the chat streams. A stream stays open
    for minutes; holding the login's transaction that long keeps the row lock of its session / token (last_seen_at) and
    blocks every other request of the same login, the resume of the same turn included (#114)."""
    from .. import api
    with api.pool.connection() as conn:
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
        "keychain": (f"security add-generic-password -a \"$USER\" -s body-engine-mcp -w   # {L('粘贴 token', 'paste the token')}\n"
                     f"echo 'export {token_env}=$(security find-generic-password -s body-engine-mcp -w)' >> ~/.zshrc"),
        "clients": {
            "claude-code": f"claude mcp add-json --scope user body-engine '{{\"type\": \"http\", \"url\": \"{url}\", \"headers\": {header}}}'",
            "codex": f"codex mcp add body-engine --url {url} --bearer-token-env-var {token_env}",
            "antigravity": (f"agy mcp add --header \"Authorization: Bearer ${{{token_env}}}\" body-engine {url}\n"
                            "chmod 600 ~/.gemini/config/mcp_config.json\n"
                            + L("# Antigravity 不展开配置文件里的 ${…}：上面这条命令由 shell 把 token 填进去。\n"
                                "# 也可以手动写（桌面版 / IDE 的 MCP Servers → Manage MCP Servers → View raw config 是同一个文件）\n"
                                "# ~/.gemini/config/mcp_config.json，把 <token> 换成上面那串 token：\n",
                                "# Antigravity does not expand ${…} in its config file: the command above lets the shell fill in the token.\n"
                                "# Or write it by hand (MCP Servers → Manage MCP Servers → View raw config in the desktop app / IDE is the\n"
                                "# same file) in ~/.gemini/config/mcp_config.json, with <token> replaced by the token above:\n")
                            + json.dumps({"mcpServers": {"body-engine": {"serverUrl": url, "headers": {"Authorization": "Bearer <token>"}}}},
                                         ensure_ascii=False, indent=2)),
            "gemini-cli": (L("# Gemini CLI 从 2026-06-18 起只支持付费 Gemini API key（AI Pro / Ultra 订阅请选 Antigravity）\n",
                             "# Since 2026-06-18 Gemini CLI only works with a paid Gemini API key (AI Pro / Ultra: choose Antigravity)\n")
                           + f"gemini mcp add --scope user --transport http -H 'Authorization: Bearer ${{{token_env}}}' body-engine {url}\n"
                           + L("# 或在 ~/.gemini/settings.json 的 mcpServers 里加：\n", "# or add it to mcpServers in ~/.gemini/settings.json:\n")
                           + json.dumps({"body-engine": {"type": "http", "url": url, "headers": {"Authorization": f"Bearer ${{{token_env}}}"}}},
                                        ensure_ascii=False, indent=2)),
            "other": json.dumps({"mcpServers": {"body-engine": {"type": "http", "url": url,
                                                                "headers": {"Authorization": f"Bearer ${{{token_env}}}"}}}},
                                ensure_ascii=False, indent=2),
        },
        "oauth": None if not oauth.enabled() else L("Claude 网页 / 手机 app、ChatGPT、Gemini app：在它们的「连接器 / 自定义应用」里添加这个地址，按提示登录今天页并同意，不用 token。",
                   "Claude web / mobile, ChatGPT, the Gemini app: add this address as a connector / custom app, sign in to the Today "
                   "page when asked and approve; no token needed."),
        "prompt": L("连上以后让 AI 先读 MCP 里的 prompt「butler」（身体管家的角色设定），或者直接说「用 body-engine 看看我今天怎么样」。",
                    "Once connected, ask the AI to read the MCP prompt \"butler\" (the coach's role) first, "
                    "or just say \"use body-engine to check how I'm doing today\"."),
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
        return {"mode": "butler", "display_name": L("身体管家", "Body butler"), "assistant_url": e["url"]}
    if e["source"] == "none":
        return {"mode": "none"}
    row = e["provider"]
    return {"mode": "mcp" if catalog.is_mcp(row["kind"]) else "api", "kind": row["kind"],
            "display_name": catalog.display_name(row), "model": row["model"], "status": row["status"],
            "vision": catalog.kind_info(row["kind"]).get("vision")}


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
    web_tools: bool | None = Field(None, description="官方联网搜索 / 读网页（只 Anthropic / OpenAI / Gemini API），默认关，费用算你的 key")
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
    web_tools: bool | None = None
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
    s["points_lang"] = lang()                # "en" = a translation; the Chinese points (consent_version) are what is agreed to
    s["points_translated"] = lang() != "zh"
    if viewer.get("role") == "owner":
        st = fallback.state(conn)
        s["balance_cny"] = float(st["balance_cny"]) if st.get("balance_cny") is not None else None
    return s


class FallbackConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=40)
    agree: Literal[True]
    method: Literal["app", "one_tap"] = "app"   # one_tap: the one-tap Jarvis sheet (flag consent_one_tap)


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
    from ..profile import consent as main_consent
    if body.method == "one_tap" and not main_consent.one_tap_on(conn, uid):
        raise HTTPException(404, L("这个功能还没有对你开放", "This feature is not available to you yet"))
    try:
        fallback.accept_consent(conn, uid, body.version, request.headers.get("user-agent"), method=body.method)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return _fallback_view(conn, viewer) or {}


@router.delete("/v1/users/{user}/ai-fallback/consent", status_code=204)
def revoke_fallback(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    fallback.revoke_consent(conn, _self_only(viewer, user, write=True))
    return Response(status_code=204)


# ---------------------------------------------------------------- Jarvis quota cards (quota_cards.py, #182)
def _quota_view(conn, uid: int) -> dict | None:
    """The numbers the page redraws after a card is used (percent only on screen)."""
    if fallback.read_key() is None:
        return None
    s = fallback.summary(conn, uid)
    return {k: s[k] for k in ("spent_cny", "quota_cny", "remaining_cny", "remaining_pct", "reset_on", "available",
                              "reason", "message", "cards") if k in s}


def _cards_user(conn, viewer: dict, user: str, write: bool = False) -> int:
    """Own cards only, 404 while the flag is off (flags.require would open a second login connection in this router)."""
    uid = _self_only(viewer, user, write)
    if not flags.enabled(conn, uid, qcp.FLAG):
        raise HTTPException(404, L("这个功能还没有对你开放", "This feature is not available to you yet"))
    return uid


@router.get("/v1/users/{user}/ai-cards")
def list_ai_cards(user: str, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _cards_user(conn, viewer, user)
    return {**quota_cards.user_view(conn, uid), "fallback": _quota_view(conn, uid)}


@router.post("/v1/users/{user}/ai-cards/{card_id}/use")
def use_ai_card(user: str, card_id: int, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Only your own cards; a second tap on the same card returns already=true and changes nothing."""
    uid = _cards_user(conn, viewer, user, write=True)
    try:
        r = quota_cards.use(conn, uid, card_id)
    except quota_cards.CardError as e:
        raise HTTPException(e.status, {"code": e.code, "message": e.message})
    return {"card_id": card_id, "kind": r["card"]["kind"], "already": r["already"], "fallback": _quota_view(conn, uid),
            "counts": quota_cards.counts(conn, uid)}


# ---------------------------------------------------------------- AI client tokens (remote MCP)
class AiTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client: str = Field(min_length=1, max_length=31, description="claude-code / codex / gemini-cli / …")
    read_only: bool = False


@router.get("/v1/users/{user}/ai-tokens")
def list_ai_tokens(user: str, request: Request, conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self_only(viewer, user)
    return {"tokens": tokens.list_tokens(conn, uid), "connections": oauth.list_grants(conn, uid), "mcp": mcp_setup(request),
            "web": web_mcp.status(conn, uid)}   # the server's web tools for external AIs (rollout flag mcp_web), None = off


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
    page: str | None = Field(None, max_length=ai_params.PAGE_MAX_LEN)   # Today page id: picks extra tool groups (ai/params.PAGE_GROUPS)


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: int
    approve: bool


def _start_run(viewer: dict, start):
    """start(conn) does the pre-checks and returns (conversation id, event generator); a failed check returns an HTTP error
    directly. The turn then runs to the end in the background (runs.py, #114) and this response only streams its events:
    a dropped connection no longer stops it. The connection belongs to the turn and is returned when the turn ends."""
    from .. import api
    cm = api.pool.connection()
    conn = cm.__enter__()
    try:
        conv_id, gen = start(conn)
        run = runs.start(viewer["id"], conv_id, gen, cm)
    except chat.ChatError as e:
        cm.__exit__(type(e), e, e.__traceback__)
        raise HTTPException(e.status, {"code": e.code, "message": e.message, **e.extra})
    except BaseException as e:
        cm.__exit__(type(e), e, e.__traceback__)
        raise
    return _sse_response(runs.sse(run))


def _sse_response(body) -> StreamingResponse:
    return StreamingResponse(body, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"})


@router.post("/v1/assistant/chat")
def post_chat(body: ChatIn, viewer=Depends(stream_user)):
    """In-Today-page chat: uses the current user's own default API provider. Returns text/event-stream:
    conversation -> text (many) -> tool / usage -> confirm (a write awaits confirmation, the stream ends here) or done; error on failure.
    Not set up / subscription type / budget used up: 409 / 402, detail = {code, message, assistant_url?}."""
    # A read-only login can chat too (read tools work as usual), write tools are rejected (chat.py decides by token_scopes)
    return _start_run(viewer, lambda conn: chat.start_chat(conn, viewer, body.message,
                                                           [i.model_dump() for i in body.images], body.conversation_id,
                                                           page=body.page))


@router.post("/v1/assistant/chat/confirm")
def post_confirm(body: ConfirmIn, viewer=Depends(stream_user)):
    """Confirm / reject a pending write, then continue the conversation (also SSE)."""
    def start(conn):
        row, conv, provider, adapter = chat.prepare_confirm(conn, viewer, body.call_id, body.approve)
        return conv["id"], chat.confirm_events(conn, viewer, row, conv, provider, adapter, body.approve)
    return _start_run(viewer, start)


@router.get("/v1/assistant/chat/{conv_id}/events")
def chat_events_after(conv_id: int, after: int = Query(0, ge=0), viewer=Depends(stream_user)):
    """Resume a turn (#114): the events numbered above `after` (the SSE `id:` of the last one received), live while the turn
    still runs, then to its end (done / confirm / error). Only your own conversations (404 otherwise). When the turn is no
    longer in memory (ended over run_params.KEEP_SECONDS ago, or the server restarted) a single `resync` event: reload the
    conversation from GET /v1/assistant/conversations/{id}."""
    run = runs.get(viewer["id"], conv_id)
    if run is not None:
        return _sse_response(runs.sse(run, after))
    from .. import api
    with api.pool.connection() as conn:
        try:
            chat.conversation_view(conn, viewer["id"], conv_id)   # 404 for a conversation that is not yours
        except chat.ChatError as e:
            raise HTTPException(e.status, e.message)
    return _sse_response(runs.resync(conv_id))


@router.post("/v1/assistant/transcribe")
async def transcribe(request: Request, fix: bool = Query(False), conn=Depends(get_conn), viewer=Depends(current_user)):
    """Voice -> text. Body: audio/wav (PCM16 mono, <= 60 s). Returns {text, seconds, used_today, per_day}; the page puts the text
    in the input box for the user to check before sending. The audio is never stored. Errors: detail = {code, message}.
    fix=1 (Jarvis voice input, flag jarvis_orbit): the text is corrected with the user's own terms (voice_fix.py) and the
    answer adds `fixed` (the replacements) and `raw` (the text before, when something changed)."""
    if int(request.headers.get("content-length") or 0) > asr.MAX_BYTES:
        raise HTTPException(413, {"code": "too_large", "message": asr.too_large_message()})
    data = await request.body()
    try:
        out = await run_in_threadpool(asr.transcribe, conn, viewer["id"], data)
    except asr.AsrError as e:
        raise HTTPException(e.status, {"code": e.code, "message": e.message})
    if fix and out.get("text") and flags.enabled(conn, viewer["id"], "jarvis_orbit"):
        out.update(await run_in_threadpool(voice_fix.fix_for, conn, viewer["id"], out["text"]))
    return out


class VoiceFixIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., max_length=voice_fix.MAX_TEXT)


@router.post("/v1/assistant/voice-fix")
def voice_fix_text(body: VoiceFixIn, conn=Depends(get_conn), viewer=Depends(current_user)):
    """Text recognised on the phone itself (the app's on-device recogniser, app 1.0.8+) -> the same correction with the user's
    own terms as transcribe?fix=1: {text, fixed, raw?}. Only text comes in; nothing is stored. 404 while jarvis_orbit is off."""
    if not flags.enabled(conn, viewer["id"], "jarvis_orbit"):
        raise HTTPException(404, L("没有这个功能", "Not available"))
    return voice_fix.fix_for(conn, viewer["id"], body.text)


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

