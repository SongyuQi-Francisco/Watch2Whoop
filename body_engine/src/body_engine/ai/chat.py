"""In-Today-page chat (API-type providers): tool-call loop, pending confirmation for writes, monthly budget, usage and audit.

One turn: user message -> model (streamed text) -> does it want a tool?
  - read tool: run directly (run_tool, same authorization as MCP: reading another user's data needs shares, writing only for the caller), result goes back to the model, continue;
  - write tool: runs directly, except deleting / undoing (ALWAYS_CONFIRM): not run, recorded as pending, a confirm event is sent so the frontend stops; user confirms -> run, user declines -> tell the model "not written", then keep looping.
Before each model call the budget is checked (this user, this provider, this month); over budget stops with a notice. Each model call records ai_usage (tokens and estimated cost).
A message never ends half way through its tool calls (issue #184): the last call allowed (MAX_ROUNDS, or for the fallback
also MAX_TURN_CNY spent on this message) gets the same tools with calls switched off plus WRAP_UP, so the model answers from
what it has found, and a notice says why and that "继续" goes on.
No conversation is ever too long to continue (issue #53): past a budget the older turns are folded into a stored summary
(context.py, _context); the model gets the summary + the recent turns, the user still sees every message.
No default provider (and no body butler link): the fallback AI (fallback.py, the admin's DeepSeek) with its own CNY quota, rate limits,
short prompt and tool subset that can only read the caller's own data.
All queries carry user_id = the logged-in person: conversations, pending writes, providers and keys never leak to anyone else.

Tools sent with each call: the resident core + the groups of the pages the conversation has been on (ai_conversations.pages,
most recent last; beyond PAGE_BUDGET_CHARS the oldest are dropped, the current page always stays) + the meta tools
(assistant/toolsets.py, issue #99). run_read_tool / run_write_tool are resolved to the real tool before anything runs, so
the read-only check, ALWAYS_CONFIRM, the fallback whitelist and the audit all go by the real tool name.
Behind the rollout flag ai_web, chats whose AI cannot go online itself (the fallback, openai_compatible) also get the server's
web_search / web_fetch (ai/web, issue #150), sent after the meta tools and run by web.run instead of run_tool.

Cards (cards.py, rollout flag jarvis_rich): the model may call show_card, and the write tools in cards.AUTO get one by
themselves; each becomes a `card` event and is kept on its ai_tool_calls row for conversation_view.

Events (produced by chat_events / start_chat, run to the end in the background and numbered by runs.py, streamed as SSE by api.py):
conversation, text, tool, card, confirm, usage, notice, error, done.
"""
import base64
import binascii
import copy
import json
from typing import Iterator

import psycopg
from psycopg.types.json import Jsonb

from .. import sparky_client
from ..assistant import compact, toolsets
from ..assistant.tools import TOOLS, TOOLS_BY_NAME, run_tool
from ..assistant.tools import _audit as run_audit
from ..i18n import L, is_en
from . import adapters, cards, catalog, choice, context, crypto, fallback, prompt, providers, web
from .choice import owner_assistant_url  # noqa: F401  (web/api.py and older callers import it from here)
from . import fallback_params as fp
from . import params as ai_params
from .params import ALWAYS_CONFIRM  # only deleting / undoing asks first (tests patch it here)

MAX_ROUNDS = 8                 # Max model calls chained for one user message (stops the tool loop from running forever); the last is a tools-off answer
# Appended (not stored) to the history of that last call: AI-facing, so Chinese
WRAP_UP = ("（系统提示，不是用户说的）这条消息查资料 / 调工具的次数到上限了，这一轮不能再调工具。"
           "用已经查到的信息直接给出尽量完整的回答，不要说「稍等」或「我再查一下」；"
           "还没查到或没做完的部分，最后用一句话说清楚，并告诉用户回「继续」就接着做。")
MAX_TOOL_RESULT_CHARS = 30000  # Truncate overly long tool results (same order of magnitude as Claude Code's MAX_MCP_OUTPUT_TOKENS)
MAX_IMAGES = 8                 # photos, or the frames the page takes from one video
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_TEXT = 8000
# What the confirm card says for these tools in English (the Chinese card uses the tool description's first sentence)
CONFIRM_EN = {"undo_sparky_entry": "Delete a food entry / strength workout / set / weigh-in",
              "undo_meal_log": "Undo a quick meal log", "undo_day_changes": "Undo day changes to the training plan", "delete_journal_entry": "Clear a journal answer for a day",
              "delete_lab_results": "Delete lab results for a sample date", "delete_observation": "Delete an observation note",
              "delete_supplement": "Delete a supplement and its journal item", "remove_product": "Remove a product",
              "remove_shopping_items": "Remove shopping list items", "remove_staple": "Stop treating a product as a usual item",
              "delete_workout": "Hide a duplicate / wrong workout",
              "rollback_write": "Roll back an AI change"}


class ChatError(Exception):
    """Failures detectable before starting (not configured, subscription type, budget, parameters): api.py turns them into HTTP errors."""

    def __init__(self, code: str, message: str, status: int = 409, **extra):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra


def ev(_event: str, **data) -> dict:
    return {"event": _event, "data": data}


# ---------------------------------------------------------------- providers and budget
def resolve_provider(conn: psycopg.Connection, user_id: int, need_quota: bool = True, allow_owner_fallback: bool = False) -> dict:
    """What this user's Jarvis uses now (choice.effective). allow_owner_fallback: `ai-fallback try` forces the fallback."""
    e = {"source": "fallback"} if allow_owner_fallback else choice.effective(conn, user_id)
    if e["source"] == "fallback":
        fallback.require_available(conn, user_id, need_quota=need_quota)  # raises with the reason (consent, quota, paused)
        return fallback.provider()
    if e["source"] == "butler":
        raise ChatError("subscription", L("请在 Claude app 中使用身体管家", "Use the body butler in the Claude app"), assistant_url=e["url"])
    row = e["provider"]
    if row is None:
        raise ChatError("no_provider", L("还没有设置 AI：在「我 → AI 设置」里选一个提供商",
                                         "No AI set up yet: pick a provider in Me → AI settings"))
    if catalog.is_mcp(row["kind"]):
        client = {"claude_plan_mcp": "Claude app / Claude Code", "codex_plan_mcp": "Codex",
                  "gemini_plan_mcp": "Antigravity"}.get(row["kind"], L("你的 AI 客户端", "your AI client"))
        name = catalog.display_name(row)
        raise ChatError("subscription", L(f"你用的是订阅（{name}）：请在 {client} 里和身体管家对话（已连上 Body Engine 的 MCP）",
                                          f"You use a subscription ({name}): talk to Jarvis in {client} "
                                          "(connected to Body Engine's MCP)"),
                        assistant_url=owner_assistant_url(user_id), kind=row["kind"])
    if not catalog.kind_allowed(row["kind"]):
        raise ChatError("disabled", L("这个 AI 提供商已下线：在「AI 设置」里换一个",
                                      "This AI provider was retired: pick another in AI settings"))
    if row["status"] != "active":
        raise ChatError("disabled", L("默认的 AI 提供商已停用，在「AI 设置」里启用或换一个",
                                      "Your default AI provider is turned off; turn it on or pick another in AI settings"))
    return row


def check_budget(conn: psycopg.Connection, user_id: int, provider: dict) -> tuple[float, float]:
    if fallback.is_fallback(provider):
        s = fallback.summary(conn, user_id)
        if s["spent_cny"] >= s["quota_cny"]:
            raise ChatError("quota", s["message"] or L("本月额度已用完", "This month's quota is used up"), status=402,
                            spent_cny=s["spent_cny"],
                            quota_cny=s["quota_cny"], remaining_cny=0, reset_on=s["reset_on"])
        return s["spent_cny"], s["quota_cny"]
    spent = providers.month_cost(conn, user_id, provider["id"])
    budget = float(provider["monthly_budget_usd"])
    free = ((provider.get("price_in_per_mtok") is not None and provider.get("price_out_per_mtok") is not None
                and float(provider["price_in_per_mtok"]) == 0 and float(provider["price_out_per_mtok"]) == 0))
    if not free and spent >= budget:
        raise ChatError("budget", L(f"本月 AI 预算已用完（${spent:.2f} / ${budget:.2f}）：在「AI 设置」里调高预算，或下个月再用",
                                    f"This month's AI budget is used up (${spent:.2f} / ${budget:.2f}): raise it in AI settings, "
                                    "or wait for next month"),
                        status=402, spent_usd=round(spent, 4), budget_usd=budget)
    return spent, budget


def _record_usage(conn, user_id, provider, conv_id, usage, purpose: str = "chat") -> float:
    """Returns the cost in the provider's budget currency: USD for the user's own providers, CNY for the fallback
    (cost_usd stays 0 there: it is not the user's money and must not count against their own budgets)."""
    if fallback.is_fallback(provider):
        cost, cost_usd, cost_cny = fallback.cost_cny(usage), 0.0, fallback.cost_cny(usage)
    else:
        cost = cost_usd = catalog.cost_usd(provider["model"], usage, provider["price_in_per_mtok"], provider["price_out_per_mtok"])
        cost_cny = None
    conn.execute("""INSERT INTO ai_usage (user_id, provider_id, conversation_id, kind, model, input_tokens, output_tokens,
                      cache_read_tokens, cache_write_tokens, cost_usd, cost_cny, purpose) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (user_id, provider["id"], conv_id, provider["kind"], provider["model"], usage.get("input_tokens", 0),
                  usage.get("output_tokens", 0), usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0),
                  round(cost_usd, 6), round(cost_cny, 6) if cost_cny is not None else None, purpose))
    return cost


# ---------------------------------------------------------------- input validation
def clean_images(images: list[dict] | None) -> list[dict]:
    out = []
    for img in images or []:
        mt, data = img.get("media_type"), img.get("data") or ""
        if mt not in IMAGE_TYPES:
            raise ChatError("bad_image", L("图片只支持 JPEG / PNG / WebP / GIF", "Images must be JPEG / PNG / WebP / GIF"), status=422)
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise ChatError("bad_image", L("图片数据不是有效的 base64", "The image data is not valid base64"), status=422)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ChatError("bad_image", L("图片太大（每张不超过 5MB）", "Image too big (5 MB at most each)"), status=422)
        out.append({"type": "image", "media_type": mt, "data": data})
    if len(out) > MAX_IMAGES:
        raise ChatError("bad_image", L(f"一次最多 {MAX_IMAGES} 张图片", f"At most {MAX_IMAGES} images at a time"), status=422)
    return out


# ---------------------------------------------------------------- storage
def _conversation(conn, user_id: int, conv_id: int) -> dict:
    row = conn.execute("SELECT * FROM ai_conversations WHERE id = %s AND user_id = %s", (conv_id, user_id)).fetchone()
    if row is None:
        raise ChatError("not_found", L("没有这个对话", "No such conversation"), status=404)
    return row


def _add_message(conn, conv_id: int, user_id: int, role: str, content: list, native=None, native_key=None) -> int:
    mid = conn.execute("""INSERT INTO ai_messages (conversation_id, user_id, role, content, native, native_key)
                          VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                       (conv_id, user_id, role, Jsonb(content), Jsonb(native) if native is not None else None,
                        native_key)).fetchone()["id"]
    conn.execute("UPDATE ai_conversations SET updated_at = now() WHERE id = %s", (conv_id,))
    return mid


def _history(conn, conv_id: int, user_id: int, after: int | None = None) -> list[dict]:
    """Messages after `after` (the summary's last message id), oldest first."""
    return conn.execute("""SELECT id, role, content, native, native_key FROM ai_messages
                           WHERE conversation_id = %s AND user_id = %s AND id > %s ORDER BY id""",
                        (conv_id, user_id, after or 0)).fetchall()


def _context(conn, viewer: dict, provider: dict, conv: dict, adapter: adapters.Adapter):
    """What the next model call sees (context.py): the system prompt with the stored summary, and the messages after it.
    Over the budget, the older turns are folded into the summary first (one tools-less call, billed like any other: usage
    event + ai_usage purpose 'compact'). Generator: yields that usage event, returns (system, history)."""
    fb = fallback.is_fallback(provider)
    state = conn.execute("SELECT summary, summary_upto FROM ai_conversations WHERE id = %s AND user_id = %s",
                         (conv["id"], viewer["id"])).fetchone()
    summary = state["summary"]
    history = _history(conn, conv["id"], viewer["id"], state["summary_upto"])
    cut = context.fold_point(history, fb)
    if cut is not None:
        request = context.fold_request(summary, history[:cut])
        try:
            if fb:
                spent, budget = fallback.precheck(conn, viewer["id"], context.FOLD_SYSTEM, request, [])
            else:
                spent, budget = check_budget(conn, viewer["id"], provider)
            folder, result = copy.copy(adapter), None
            folder.web = False   # no tools at all, not even the vendor's web search
            for kind, val in folder.stream_turn(context.FOLD_SYSTEM, request, []):
                if kind == "result":
                    result = val
        except (ChatError, adapters.AdapterError):
            result = None   # go on with the old summary + the recent turns; the budget check of the real call reports quota
        if result is not None:
            cost = _record_usage(conn, viewer["id"], provider, conv["id"], result.usage, purpose="compact")
            yield _usage_event(provider, result.usage, cost, spent, budget)
            new = context.clean_summary(result.text)
            if new:
                conn.execute("""UPDATE ai_conversations SET summary = %s, summary_upto = %s, summary_at = now()
                                WHERE id = %s AND user_id = %s""", (new, history[cut - 1]["id"], conv["id"], viewer["id"]))
                summary = new
            conn.commit()
        history = history[cut:]
    return context.with_summary(conv["system_prompt"], summary), history


def _close_open_calls(conn, conv: dict, reason: str) -> None:
    """When the last message is an assistant message with tool calls and no results yet, add results (pending ones are treated as "not run"),
    so history keeps "every tool call is followed by a result", which every vendor's API requires."""
    last = conn.execute("""SELECT id, role, content FROM ai_messages WHERE conversation_id = %s AND user_id = %s
                           ORDER BY id DESC LIMIT 1""", (conv["id"], conv["user_id"])).fetchone()
    if last is None or last["role"] != "assistant":
        return
    calls = [b for b in last["content"] if b["type"] == "tool_call"]
    if not calls:
        return
    conn.execute("""UPDATE ai_tool_calls SET status = 'rejected', result = %s, decided_at = now()
                    WHERE message_id = %s AND user_id = %s AND status = 'pending'""", (reason, last["id"], conv["user_id"]))
    _finish_tool_message(conn, conv, last["id"], calls)


def _finish_tool_message(conn, conv: dict, message_id: int, calls: list[dict]) -> None:
    rows = {r["call_id"]: r for r in conn.execute(
        "SELECT call_id, status, result FROM ai_tool_calls WHERE message_id = %s AND user_id = %s",
        (message_id, conv["user_id"]))}
    results = []
    for c in calls:
        r = rows.get(c["id"])
        results.append({"type": "tool_result", "id": c["id"], "name": c["name"],
                        "content": (r["result"] if r else None) or "没有执行（中断了）",
                        "is_error": r is None or r["status"] in ("error", "rejected")})
    _add_message(conn, conv["id"], conv["user_id"], "tool", results)
    conn.execute("UPDATE ai_conversations SET state = 'idle' WHERE id = %s", (conv["id"],))


def _result_text(result) -> str:
    text = compact.dumps(result)  # same token-lean text as the MCP channel (assistant/compact.py)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + f"…（结果太长，截断了；原长 {len(text)} 字，缩小日期范围再查）"
    return text


def _exec(conn, viewer: dict, provider: dict, call_row_id: int, name: str, args: dict) -> tuple[str, str]:
    """Run one tool (read tools automatically, write tools after confirmation); returns (status, result text)."""
    if name in web.NAMES:
        return _exec_web(conn, viewer, provider, call_row_id, name, args)
    if fallback.is_fallback(provider):  # the fallback reads / writes only the caller's own data, never through shares
        args = {**args, "user": "me"} if "user" in TOOLS_BY_NAME[name].input_schema.get("properties", {}) else args
    try:
        res = run_tool(conn, viewer["id"], f"api:{provider['kind']}", name, args,
                       provider_id=provider["id"], model=provider["model"])
        status, text = "done", _result_text(res)
    except Exception as e:  # Permission, parameter and database errors are passed to the model as they are
        status, text = "error", f"{type(e).__name__}: {e}"
    conn.execute("UPDATE ai_tool_calls SET status = %s, result = %s, decided_at = now() WHERE id = %s AND user_id = %s",
                 (status, text, call_row_id, viewer["id"]))
    return status, text


def _exec_web(conn, viewer: dict, provider: dict, call_row_id: int, name: str, args: dict) -> tuple[str, str]:
    """The server's web_search / web_fetch (ai/web): usage and limits per user, audited like any other tool."""
    conv_id = conn.execute("SELECT conversation_id FROM ai_tool_calls WHERE id = %s AND user_id = %s",
                           (call_row_id, viewer["id"])).fetchone()["conversation_id"]
    channel = f"api:{provider['kind']}"
    try:   # no savepoint: a page read that fails after its ai_usage row still counts (no retry loops)
        res = web.run(conn, viewer["id"], provider, conv_id, name, args)
        status, text = "done", _result_text(res)
        run_audit(conn, viewer["id"], channel, name, args, True, None, provider["id"], provider["model"])
    except Exception as e:  # limits, search API errors, bad URLs: passed to the model as they are
        status, text = "error", f"{type(e).__name__}: {e}"
        run_audit(conn, viewer["id"], channel, name, args, False, str(e), provider["id"], provider["model"])
    conn.execute("UPDATE ai_tool_calls SET status = %s, result = %s, decided_at = now() WHERE id = %s AND user_id = %s",
                 (status, text, call_row_id, viewer["id"]))
    return status, text


def _summary(name: str, args: dict) -> str:
    tool = TOOLS_BY_NAME.get(name)
    shown = {k: v for k, v in args.items() if k != "user" and v not in (None, "", [])}
    if is_en():
        return f"{CONFIRM_EN.get(name, name)}: " + json.dumps(shown, ensure_ascii=False)[:600]
    return f"{tool.description.split('。')[0] if tool else name}：" + json.dumps(shown, ensure_ascii=False)[:600]


def _pending_payload(conn, user_id: int, message_id: int) -> list[dict]:
    rows = conn.execute("""SELECT id, tool, args FROM ai_tool_calls WHERE message_id = %s AND user_id = %s
                           AND status = 'pending' ORDER BY id""", (message_id, user_id)).fetchall()
    return [{"call_id": r["id"], "tool": r["tool"], "args": r["args"], "summary": _summary(r["tool"], r["args"])}
            for r in rows]


# ---------------------------------------------------------------- loop
def make_adapter(provider: dict, user_id: int) -> adapters.Adapter:
    """Decrypt this user's own key and build the adapter. Done before the conversation starts, failures go straight to the user (not into the stream)."""
    if fallback.is_fallback(provider):
        key = fallback.read_key()
        if key is None:
            raise ChatError("fallback_off", L("Jarvis 还没开通", "Jarvis is not set up yet"), status=409)
        return adapters.make(provider["kind"], provider["model"], key, provider["base_url"])
    try:
        key = providers.decrypt_secret(provider, user_id)
    except crypto.MasterKeyMissing as e:
        raise ChatError("server_key", str(e), status=503)
    except PermissionError:
        raise ChatError("forbidden", L("这个提供商不属于你", "This provider is not yours"), status=403)
    except Exception:  # Bad authentication tag: the master key was changed or the data is corrupted
        raise ChatError("bad_secret", L("保存的 API key 解不开了，请在「AI 设置」里重新填一次",
                                        "The saved API key can no longer be read; enter it again in AI settings"))
    web = bool(provider.get("web_tools")) and provider["kind"] in catalog.WEB_TOOL_KINDS   # never the fallback (returned above)
    return adapters.make(provider["kind"], provider["model"], key, provider["base_url"], web=web)


def _usage_event(provider: dict, usage: dict, cost: float, spent: float, budget: float) -> dict:
    base = {"input_tokens": usage.get("input_tokens", 0) + usage.get("cache_read_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0), "provider": provider["kind"], "model": provider["model"]}
    if fallback.is_fallback(provider):
        return ev("usage", **base, currency="CNY", cost_cny=round(cost, 4), month_cost_cny=round(spent + cost, 4),
                  quota_cny=budget, remaining_cny=round(max(budget - spent - cost, 0), 4))
    return ev("usage", **base, cost_usd=round(cost, 4), month_cost_usd=round(spent + cost, 4), budget_usd=budget)


def _resolve_call(access: toolsets.Access, name: str, args: dict):
    """A tool call from the model -> (real name, real args, Tool or None, error text or None).
    run_read_tool / run_write_tool become the tool inside them; find_tools is answered by the caller."""
    if name in toolsets.RUN_NAMES:
        try:
            tool, args = toolsets.resolve(access, name, args)
        except (PermissionError, ValueError) as e:
            return name, args, None, f"{type(e).__name__}: {e}"
        return tool.name, args, tool, None
    tool = access.tools.get(name)
    return name, args, tool, None if tool else f"没有这个工具：{name}"


def _tool_sets(conn, user_id: int, provider: dict) -> tuple[tuple, list]:
    """(the tools this chat may reach, the server web tools): the fallback only ever reaches its own whitelist (+ the
    grocery tools behind ai_web), also through the meta tools."""
    allowed = fallback.tools(web.fallback_extra(conn, user_id)) if fallback.is_fallback(provider) else TOOLS
    return tuple(allowed), web.server_tools(conn, user_id, provider)


def _card(conn, user_id: int, args: dict) -> tuple[dict | None, str, str]:
    """(card, status, text for the model) for show_card. A failure only costs the card: the savepoint keeps the turn's transaction."""
    try:
        with conn.transaction():
            card = cards.build(conn, user_id, args)
        return card, "done", cards.summary(card)
    except (ValueError, PermissionError, sparky_client.SparkyError) as e:
        return None, "error", f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001  a card never breaks the answer
        return None, "error", f"卡片没显示出来（{type(e).__name__}）"


def _auto_cards(conn, user_id: int, done_writes: list[tuple]) -> Iterator[dict]:
    """One card per distinct cards.AUTO spec among this round's successful writes (three log_food calls -> one meal card),
    built after all of them ran so it shows the final totals; kept on the first matching call's row."""
    seen = set()
    for row_id, name, args in done_writes:
        spec = cards.auto_spec(name, args)
        key = spec and (spec["type"], spec["date"])
        if not spec or key in seen:
            continue
        seen.add(key)
        card, status, _ = _card(conn, user_id, spec)
        if card:
            conn.execute("UPDATE ai_tool_calls SET card = %s WHERE id = %s AND user_id = %s", (Jsonb(card), row_id, user_id))
            yield ev("card", card=card, call_id=row_id)


def _page_budget(provider: dict) -> int:
    return ai_params.FALLBACK_PAGE_BUDGET_CHARS if fallback.is_fallback(provider) else ai_params.PAGE_BUDGET_CHARS


def _loop(conn, viewer: dict, provider: dict, conv: dict, adapter: adapters.Adapter) -> Iterator[dict]:
    can_write = "write" in (viewer.get("token_scopes") or [])
    fb = fallback.is_fallback(provider)
    allowed, web_tools = _tool_sets(conn, viewer["id"], provider)
    access = toolsets.Access({t.name: t for t in (*allowed, *web_tools)}, can_write)
    tools = toolsets.for_page(allowed, toolsets.fit_pages(allowed, conv.get("pages") or [], _page_budget(provider))) + web_tools
    rich = cards.enabled(conn, viewer["id"])
    if rich:
        tools = [*tools, cards.TOOL]
    rounds, turn_cost = (fp.MAX_ROUNDS if fb else MAX_ROUNDS), 0.0
    for rnd in range(rounds):
        system, history = yield from _context(conn, viewer, provider, conv, adapter)
        if rich:
            system = f"{system}\n\n{cards.PROMPT}"
        # The last call allowed: same tools (history and prompt cache stay valid), calls switched off, an answer now
        last = rnd > 0 and (rnd == rounds - 1 or (fb and turn_cost >= fp.MAX_TURN_CNY))
        caller = adapter
        if last:
            history = [*history, {"role": "user", "content": [{"type": "text", "text": WRAP_UP}]}]
            caller = copy.copy(adapter)
            caller.no_tool_calls = True
        try:
            if fb:
                spent, budget = fallback.precheck(conn, viewer["id"], system, history, tools)
            else:
                spent, budget = check_budget(conn, viewer["id"], provider)
        except ChatError as e:
            yield ev("error", code=e.code, message=e.message, **e.extra)
            return
        result = None
        try:
            for kind, val in caller.stream_turn(system, history, tools):
                if kind == "text":
                    yield ev("text", delta=val)
                else:
                    result = val
        except adapters.AdapterError as e:
            yield ev("error", code=e.code, message=str(e))
            return
        cost = _record_usage(conn, viewer["id"], provider, conv["id"], result.usage)
        turn_cost += cost
        yield _usage_event(provider, result.usage, cost, spent, budget)
        if last and result.tool_calls:   # a model that ignored tool_choice: the calls are dropped, never run, and so is the
            result.tool_calls, result.native = [], None   # native reply holding them (replaying it would need their results)
        content = ([{"type": "text", "text": result.text}] if result.text else []) + [
            {"type": "tool_call", "id": c["id"], "name": c["name"], "args": c["args"]} for c in result.tool_calls]
        if not content:
            content = [{"type": "text", "text": ""}]
        mid = _add_message(conn, conv["id"], viewer["id"], "assistant", content, result.native, adapter.key)
        conn.commit()
        if result.stop == "refusal":
            yield ev("notice", message=L("模型拒绝回答这个问题", "The model declined to answer this"))
        if result.stop == "max_tokens":   # the adapters drop a tool call cut off half way: no text usually means that
            yield ev("notice", message=L("回答太长被截断了；回「继续」让它接着说", "The answer was too long and got cut off; "
                                         "reply \u201ccontinue\u201d for the rest") if result.text.strip() else
                     L("这一步要写的内容太长，超过了单次输出的上限，没有写成；可以让它分几次写",
                       "What it tried to write in one go was too long and was not saved; ask it to do it in smaller steps"))
        if last:
            yield ev("notice", code="round_limit", message=L(
                f"这条消息查了 {rnd + 1} 轮资料，先按已经查到的回答；没答全的部分回「继续」接着查",
                f"It looked things up {rnd + 1} times for this message, so it answered with what it found; "
                "reply \u201ccontinue\u201d to look further"))
        if not result.tool_calls:
            conn.execute("UPDATE ai_conversations SET state = 'idle' WHERE id = %s", (conv["id"],))
            conn.commit()
            yield ev("done", conversation_id=conv["id"])
            return

        pending, done_writes = False, []
        for c in result.tool_calls:
            args = c["args"] if isinstance(c["args"], dict) else {}
            if rich and c["name"] == cards.NAME:   # not a data tool: builds a card from the user's own data (no audit)
                card, status, text = _card(conn, viewer["id"], args)
                row_id = conn.execute(
                    """INSERT INTO ai_tool_calls (conversation_id, message_id, user_id, call_id, tool, args, writes, status, result, card)
                       VALUES (%s,%s,%s,%s,%s,%s,false,%s,%s,%s) RETURNING id""",
                    (conv["id"], mid, viewer["id"], c["id"], c["name"], Jsonb(args), status, text,
                     Jsonb(card) if card else None)).fetchone()["id"]
                yield ev("card", card=card, call_id=row_id) if card else ev("tool", name=c["name"], status=status)
                continue
            if c["name"] == toolsets.FIND:   # only lists tool definitions: no user data, no audit
                try:
                    status, text = "done", _result_text(toolsets.find(access, args.get("group"), args.get("query")))
                except ValueError as e:
                    status, text = "error", f"ValueError: {e}"
                conn.execute("""INSERT INTO ai_tool_calls (conversation_id, message_id, user_id, call_id, tool, args, writes, status, result)
                                VALUES (%s,%s,%s,%s,%s,%s,false,%s,%s)""",
                             (conv["id"], mid, viewer["id"], c["id"], c["name"], Jsonb(args), status, text))
                yield ev("tool", name=c["name"], status=status)
                continue
            # The row holds the real tool and its args: confirming, the confirm card and the audit all use them
            name, args, tool, error = _resolve_call(access, c["name"], args)
            writes = bool(tool and tool.writes)
            if error is None and writes and not can_write:
                error = "这次登录是只读的，不能写"
            confirm = error is None and writes and name in ALWAYS_CONFIRM
            row_id = conn.execute(
                """INSERT INTO ai_tool_calls (conversation_id, message_id, user_id, call_id, tool, args, writes, status, result)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (conv["id"], mid, viewer["id"], c["id"], name, Jsonb(args), writes,
                 "error" if error else ("pending" if confirm else "done"), error)).fetchone()["id"]
            if error:
                yield ev("tool", name=name, status="error")
            elif confirm:
                pending = True
            else:
                yield ev("tool", name=name, status="running")
                status, _ = _exec(conn, viewer, provider, row_id, name, args)
                yield ev("tool", name=name, status=status)
                if status == "done":
                    done_writes.append((row_id, name, args))
        if rich:
            yield from _auto_cards(conn, viewer["id"], done_writes)
        conn.commit()
        if pending:
            conn.execute("UPDATE ai_conversations SET state = 'awaiting_confirmation' WHERE id = %s", (conv["id"],))
            conn.commit()
            yield ev("confirm", conversation_id=conv["id"], calls=_pending_payload(conn, viewer["id"], mid))
            return
        _finish_tool_message(conn, conv, mid, [b for b in content if b["type"] == "tool_call"])
        conn.commit()


def chat_events(conn: psycopg.Connection, viewer: dict, message: str, images: list[dict] | None,
                conversation_id: int | None, allow_owner_fallback: bool = False, page: str | None = None) -> Iterator[dict]:
    """One user message. Pre-checks (provider, budget, parameters) are done by prepare_chat before the first next().
    allow_owner_fallback: only `body-engine ai-fallback try` (lets the deployer, who has the butler link, test the fallback).
    page: the Today page id the message is sent from (ai/params.PAGE_GROUPS); a page new to the conversation adds its groups
    (unknown ids are ignored; over the budget the oldest pages' groups are dropped)."""
    provider, adapter, conv, content = prepare_chat(conn, viewer, message, images, conversation_id, allow_owner_fallback, page)
    return _run(conn, viewer, provider, adapter, conv, content)


def prepare_chat(conn, viewer, message, images, conversation_id, allow_owner_fallback=False, page=None):
    message = (message or "").strip()
    imgs = clean_images(images)
    if not message and not imgs:
        raise ChatError("empty", L("说点什么，或者发一张图", "Say something, or send a picture"), status=422)
    if len(message) > MAX_TEXT:
        raise ChatError("too_long", L(f"一条消息不超过 {MAX_TEXT} 字", f"At most {MAX_TEXT} characters per message"), status=422)
    provider = resolve_provider(conn, viewer["id"], allow_owner_fallback=allow_owner_fallback)
    fb = fallback.is_fallback(provider)
    vision = fp.VISION if fb else catalog.kind_info(provider["kind"]).get("vision")
    if imgs and vision is False:
        raise ChatError("no_vision", L("Jarvis 暂不支持图片，用文字描述一下", "Jarvis can't read images yet; describe it in words")
                        if fb else L("这个提供商的模型不支持图片", "This provider's model can't read images"), status=422)
    check_budget(conn, viewer["id"], provider)
    adapter = make_adapter(provider, viewer["id"])
    if conversation_id is None:
        conv = conn.execute("""INSERT INTO ai_conversations (user_id, provider_id, title, system_prompt, pages)
                               VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                            (viewer["id"], provider["id"], (message or "图片")[:40],
                             prompt.build_system_prompt(conn, viewer["id"], lite=fb),
                             [page] if toolsets.known_page(page) else [])).fetchone()
    else:
        conv = _conversation(conn, viewer["id"], conversation_id)
        conn.execute("UPDATE ai_conversations SET provider_id = %s WHERE id = %s", (provider["id"], conv["id"]))
        allowed, _ = _tool_sets(conn, viewer["id"], provider)
        pages = toolsets.visit_page(allowed, conv["pages"], page, _page_budget(provider))
        if pages != conv["pages"]:   # a new page adds its groups; over the cap the oldest pages' groups are dropped
            conv = conn.execute("UPDATE ai_conversations SET pages = %s WHERE id = %s RETURNING *",
                                (pages, conv["id"])).fetchone()
    content = [{"type": "text", "text": f"{prompt.time_prefix(conn, viewer['id'])}\n{message}"}] + imgs
    return provider, adapter, conv, content


def start_chat(conn: psycopg.Connection, viewer: dict, message: str, images: list[dict] | None,
               conversation_id: int | None, page: str | None = None) -> tuple[int, Iterator[dict]]:
    """chat_events for the Today page: also returns the conversation id, which keys the background turn (runs.py, #114)."""
    provider, adapter, conv, content = prepare_chat(conn, viewer, message, images, conversation_id, page=page)
    return conv["id"], _run(conn, viewer, provider, adapter, conv, content)


def _run(conn, viewer, provider, adapter, conv, content) -> Iterator[dict]:
    yield ev("conversation", id=conv["id"], provider=provider["kind"], model=provider["model"],
             display_name=catalog.display_name(provider),
             **({"quota": {k: v for k, v in fallback.summary(conn, viewer["id"]).items()
                           if k in ("spent_cny", "quota_cny", "remaining_cny", "reset_on")}} if fallback.is_fallback(provider) else {}))
    _close_open_calls(conn, conv, "用户没有确认就发了新消息，这次没有写入")
    _add_message(conn, conv["id"], viewer["id"], "user", content)
    conn.commit()
    yield from _loop(conn, viewer, provider, conv, adapter)


def prepare_confirm(conn, viewer: dict, call_id: int, approve: bool):
    row = conn.execute("SELECT * FROM ai_tool_calls WHERE id = %s AND user_id = %s FOR UPDATE",
                       (call_id, viewer["id"])).fetchone()
    if row is None:
        raise ChatError("not_found", L("没有这个待确认的操作", "No such pending action"), status=404)
    if row["status"] != "pending":
        raise ChatError("decided", L("这个操作已经处理过了", "This action was already handled"))
    if approve and "write" not in (viewer.get("token_scopes") or []):
        raise ChatError("read_only", L("这次登录是只读的，不能写", "This login is read-only; it can't write"), status=403)
    conv = _conversation(conn, viewer["id"], row["conversation_id"])
    # An approved write still runs when the fallback quota ran out meanwhile (the loop then stops before the next model call)
    provider = resolve_provider(conn, viewer["id"], need_quota=False)
    return row, conv, provider, make_adapter(provider, viewer["id"])


def confirm_events(conn, viewer, row, conv, provider, adapter, approve: bool) -> Iterator[dict]:
    if approve:
        yield ev("tool", name=row["tool"], status="running")
        status, _ = _exec(conn, viewer, provider, row["id"], row["tool"], row["args"])
        yield ev("tool", name=row["tool"], status=status, call_id=row["id"])
    else:
        conn.execute("""UPDATE ai_tool_calls SET status = 'rejected', result = '用户拒绝了这次写入，没有执行。',
                        decided_at = now() WHERE id = %s AND user_id = %s""", (row["id"], viewer["id"]))
        yield ev("tool", name=row["tool"], status="rejected", call_id=row["id"])
    conn.commit()
    left = _pending_payload(conn, viewer["id"], row["message_id"])
    if left:  # Other write operations in the same reply are still waiting for confirmation
        yield ev("confirm", conversation_id=conv["id"], calls=left)
        return
    msg = conn.execute("SELECT content FROM ai_messages WHERE id = %s AND user_id = %s",
                       (row["message_id"], viewer["id"])).fetchone()
    _finish_tool_message(conn, conv, row["message_id"], [b for b in msg["content"] if b["type"] == "tool_call"])
    conn.commit()
    yield from _loop(conn, viewer, provider, conv, adapter)


# ---------------------------------------------------------------- list / read back
def list_conversations(conn, user_id: int, limit: int = 30) -> list[dict]:
    return conn.execute("""SELECT id, title, state, created_at, updated_at FROM ai_conversations
                           WHERE user_id = %s ORDER BY updated_at DESC LIMIT %s""", (user_id, limit)).fetchall()


def conversation_view(conn, user_id: int, conv_id: int) -> dict:
    """For the frontend to reopen a conversation: text, tool names, images only as a count (image data is not returned), and writes still awaiting confirmation."""
    conv = _conversation(conn, user_id, conv_id)
    cards_of: dict[int, list] = {}
    for r in conn.execute("""SELECT message_id, card FROM ai_tool_calls WHERE conversation_id = %s AND user_id = %s
                             AND card IS NOT NULL ORDER BY id""", (conv_id, user_id)):
        cards_of.setdefault(r["message_id"], []).append(r["card"])
    msgs = []
    for m in conn.execute("SELECT id, role, content, created_at FROM ai_messages WHERE conversation_id = %s AND user_id = %s"
                          " ORDER BY id", (conv_id, user_id)):
        if m["role"] == "tool":
            continue
        text = "".join(b.get("text", "") for b in m["content"] if b["type"] == "text")
        if m["role"] == "user" and text.startswith("[当前本地时间"):
            text = text.split("\n", 1)[1] if "\n" in text else ""
        msgs.append({"id": m["id"], "role": m["role"], "text": text, "created_at": m["created_at"],
                     "images": sum(1 for b in m["content"] if b["type"] == "image"),
                     "tools": [b["name"] for b in m["content"] if b["type"] == "tool_call"],
                     "cards": cards_of.get(m["id"], [])})
    last_asst = conn.execute("""SELECT id FROM ai_messages WHERE conversation_id = %s AND user_id = %s AND role = 'assistant'
                                ORDER BY id DESC LIMIT 1""", (conv_id, user_id)).fetchone()
    pending = _pending_payload(conn, user_id, last_asst["id"]) if last_asst else []
    return {"id": conv["id"], "title": conv["title"], "state": conv["state"], "messages": msgs, "pending": pending}


def delete_conversation(conn, user_id: int, conv_id: int) -> bool:
    return conn.execute("DELETE FROM ai_conversations WHERE id = %s AND user_id = %s RETURNING id",
                        (conv_id, user_id)).fetchone() is not None
