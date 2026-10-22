"""Automatic context management for the in-page chat (issue #53): a conversation is never "too long" to continue.

The user always sees the whole conversation (ai_messages is append-only; conversation_view returns all of it). The model sees:
  system prompt + the stored summary of the earlier turns (ai_conversations.summary) + the recent turns verbatim.
A turn starts at a user message and runs through the assistant / tool messages that answer it. Cutting only at turn starts
keeps "every tool call is followed by its result" and "the first message is the user's", which every vendor requires.

When the part after the summary grows past the budget (characters or messages; the fallback's budget is smaller because
its history is the bill), the older turns are folded into the summary by one tools-less call to the same provider and model
(chat._context): old summary + the turns being folded -> new summary, stored with the id of the last message it covers,
so every later call reuses it. The tail kept after a fold fits in TAIL_SHARE of the budget, so folds are rare.
That call is recorded in ai_usage (purpose 'compact') and counts against the same monthly budget / fallback quota.
If it fails, the call goes on with the old summary + the recent turns only (nothing stored; the next call tries again).
Images in folded turns are not resent (the summary says they were there); images in the kept turns are.
"""
import json

from . import fallback_params as fp

# The user's own providers (budget in USD set by the user; long contexts are fine, but resending everything costs)
MAX_CHARS = 120_000       # history after the summary, as JSON text of its blocks (~30–60k tokens)
MAX_MESSAGES = 160        # or this many messages (tool calls and results count)
KEEP_TURNS = 8            # recent turns kept verbatim at most (fewer when they do not fit TAIL_SHARE)
TAIL_SHARE = 0.5          # after a fold the kept tail is at most this share of the budget
IMAGE_CHARS = 2_000       # an image counts as this many characters when measuring (its base64 is not counted)

FOLD_INPUT_MAX_CHARS = 60_000   # transcript sent to the fold call; longer: the middle is cut (first fold of an old conversation)
TOOL_RESULT_CHARS = 600         # each tool result in the transcript (the model already used it; the summary keeps the outcome)
TOOL_ARGS_CHARS = 300
SUMMARY_MAX_CHARS = 3_000       # stored summary (the prompt asks for ~600 characters)

# AI-facing (Chinese, like the system prompt)
FOLD_SYSTEM = (
    "你在给一段身体管理助手的对话做摘要。摘要会替代这些早期消息，交给继续这段对话的模型，用户看不到摘要。\n"
    "只写事实，条目式，中文，不超过 600 字。保留：用户的目标、偏好、限制和明确的决定；已经记录 / 修改 / 删除了什么"
    "（日期、数值、记录 id）；答应了还没做的事；用户问过、后面可能接着问的问题和你的结论；关键数字。"
    "去掉：寒暄、重复内容、工具返回里后面用不到的明细。相对日期（今天、昨天）按消息里的「当前本地时间」换成绝对日期。\n"
    "如果给了「之前的摘要」，把它和新对话合成一份完整的新摘要（不是只写新增部分）。直接输出摘要，不要开场白。"
)
SUMMARY_HEADER = "## 早前对话摘要（自动压缩；用户那边仍能看到完整原文，需要时以工具查到的数据为准）"


def limits(fallback: bool) -> tuple[int, int, int]:
    """(max chars, max messages, turns kept) for this provider type."""
    if fallback:
        return fp.CONTEXT_MAX_CHARS, fp.CONTEXT_MAX_MESSAGES, fp.KEEP_TURNS
    return MAX_CHARS, MAX_MESSAGES, KEEP_TURNS


def size(messages: list[dict]) -> int:
    chars = 0
    for m in messages:
        blocks = [b for b in m["content"] if b.get("type") != "image"]
        chars += len(json.dumps(blocks, ensure_ascii=False, default=str))
        chars += IMAGE_CHARS * (len(m["content"]) - len(blocks))
    return chars


def turn_starts(messages: list[dict]) -> list[int]:
    return [i for i, m in enumerate(messages) if m["role"] == "user"]


def fold_point(messages: list[dict], fallback: bool) -> int | None:
    """Index of the first message to keep verbatim (messages[:i] get folded), or None when the history fits.
    Always a turn start; the latest turn (the one being answered) is always kept, however long."""
    max_chars, max_msgs, keep = limits(fallback)
    if size(messages) <= max_chars and len(messages) <= max_msgs:
        return None
    starts = turn_starts(messages)
    if len(starts) < 2:
        return None
    cut = starts[-1]
    for k in range(min(keep, len(starts) - 1), 1, -1):   # the most recent turns that fit in the tail share
        tail = messages[starts[-k]:]
        if size(tail) <= max_chars * TAIL_SHARE and len(tail) <= max_msgs * TAIL_SHARE:
            cut = starts[-k]
            break
    return cut


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + f"…（{len(text)} 字，截断）"


def transcript(messages: list[dict]) -> str:
    """Plain-text rendering of messages for the fold call."""
    lines = []
    for m in messages:
        for b in m["content"]:
            t = b.get("type")
            if t == "text" and b.get("text"):
                lines.append(("用户：" if m["role"] == "user" else "助手：") + b["text"])
            elif t == "image":
                lines.append("用户：[发了一张图片]")
            elif t == "tool_call":
                args = json.dumps(b.get("args") or {}, ensure_ascii=False, default=str)
                lines.append(f"助手调用工具 {b.get('name')}({_clip(args, TOOL_ARGS_CHARS)})")
            elif t == "tool_result":
                flag = "出错" if b.get("is_error") else "结果"
                lines.append(f"工具 {b.get('name')} {flag}：{_clip(str(b.get('content') or ''), TOOL_RESULT_CHARS)}")
    text = "\n".join(lines)
    if len(text) > FOLD_INPUT_MAX_CHARS:
        head = FOLD_INPUT_MAX_CHARS // 4
        text = text[:head] + "\n…（中间太长，省略）…\n" + text[-(FOLD_INPUT_MAX_CHARS - head):]
    return text


def fold_request(old_summary: str | None, messages: list[dict]) -> list[dict]:
    """History (neutral format) for the tools-less fold call."""
    parts = [f"之前的摘要：\n{old_summary}"] if old_summary else []
    parts.append(f"要并入摘要的对话：\n{transcript(messages)}")
    return [{"role": "user", "content": [{"type": "text", "text": "\n\n".join(parts)}], "native": None, "native_key": None}]


def clean_summary(text: str) -> str:
    return (text or "").strip()[:SUMMARY_MAX_CHARS]


def with_summary(system: str, summary: str | None) -> str:
    return f"{system}\n\n{SUMMARY_HEADER}\n{summary}" if summary else system
