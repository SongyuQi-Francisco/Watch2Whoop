"""Jarvis on the Apple Watch (docs/watch-app-plan.md 2.2, 2.3, 4.3–4.5): hold to talk -> POST /v1/assistant/transcribe ->
POST /v1/assistant/chat with page "watch", from the watch's own device token (account/watch.py).

- Short answers: a request is a watch request when its page is "watch" or it comes with the watch token (then also its
  confirmations, which carry no page). Every model call of that request gets PROMPT as a per-call extra of the system
  prompt (chat.compose_system), so it works the same for every provider and for the fallback AI, and a conversation
  continued on the phone gets its usual answers again. Nothing else changes: the same tools, writes still run directly,
  deleting / undoing still waits for the confirmation, the same budgets, the fallback's allowance and tool set.
- Which device: from the token, never from what the request says about itself. The watch token marks its conversations
  (ai_conversations.client), the usage rows of its calls (ai_usage.client, asr_usage.client) and the audit channel of
  its tool calls (`api:<kind>:watch`, assistant_audit / the write log).
"""
from . import params as P

PAGE = "watch"      # ChatIn.page of the watch app
CLIENT = "watch"    # viewer["client"] of a watch device token (auth.user_for_watch_token)

PROMPT = (
    "## 这条消息来自用户的 Apple Watch（系统规则）\n"
    "手表屏幕很小，用户抬着手腕看。这一轮回答：\n"
    f"- 第一行就是结论或那个数（最好 {P.WATCH_HEADLINE_CHARS_ZH} 个字以内，例「还能吃 680 kcal」「可以练」），不要开场白、不要复述问题。\n"
    f"- 一共最多 {P.WATCH_MAX_SENTENCES} 句，中文不超过 {P.WATCH_MAX_CHARS_ZH} 个字（英文不超过 {P.WATCH_MAX_WORDS_EN} 个词）；"
    "不要列表、表格、标题、markdown、emoji。\n"
    "- 用户说的是吃了什么 / 练了什么 / 体重这类记录：照常直接调记录工具写进去（删除 / 撤销照常先确认），"
    "写完只回一句，带上记了什么和热量或关键数字，例「记到午餐：牛肉面 + 蛋，620 kcal」。\n"
    "- 信息不够只问一个问题，一句话。\n"
    "- 有卡片工具时一个数配一张卡，文字不再复述卡片里的数。\n"
    "- 要展开讲的（原因、计划、对比、长建议）：只给一句结论，最后加「详细的在手机上看」（英文 \"More on your phone\"）。"
)


def is_watch(viewer: dict) -> bool:
    """The request came with an Apple Watch device token."""
    return viewer.get("client") == CLIENT


def for_request(viewer: dict, page: str | None) -> dict:
    """The viewer of one chat request: marked for short answers when it is a watch request."""
    return {**viewer, "short_answer": True} if page == PAGE or is_watch(viewer) else viewer


def short(viewer: dict) -> bool:
    return bool(viewer.get("short_answer")) or is_watch(viewer)


def client_of(viewer: dict) -> str | None:
    """ai_usage.client / asr_usage.client of a call made for this viewer."""
    return CLIENT if is_watch(viewer) else None


def channel(base: str, viewer: dict) -> str:
    """The audit channel of a tool call: `api:<kind>` from the phone / the Today page, `api:<kind>:watch` from the watch."""
    return f"{base}:{CLIENT}" if is_watch(viewer) else base
