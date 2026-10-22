"""System prompt of the chat: the root rules (root_rules.md, above everything else) + a provider-independent persona
(system_prompt.md) + this user's own basic profile and long-term memory.

- Assembled once at the start of a conversation and stored in ai_conversations.system_prompt, never changed afterwards: history is append-only so the prompt cache hits,
  and Claude's preserved thinking also requires system to stay the same. So "what time is it now" is not put here but prepended to each user message (time_prefix).
- Memory is taken only from this user (memory.get_memory / status.list_status filter by user_id) and never mixes in anyone else's.
"""
import re
from datetime import datetime
from importlib import resources
from zoneinfo import ZoneInfo

import psycopg

from .. import queries
from ..feedback import store as feedback_store
from . import root_rules
from ..assistant import memory, status
from ..assistant import memory_params as mp

MAX_MEMORY_CHARS = 12000


LITE_MEMORY_CHARS = 4000  # the fallback AI (fallback.py): shorter memory snapshot, fewer tokens per call


def base_prompt(lite: bool = False) -> str:
    """Root rules first (they take precedence over the persona and the memory below), then the persona."""
    name = "system_prompt_lite.md" if lite else "system_prompt.md"
    text = resources.files("body_engine.ai").joinpath(name).read_text()
    return root_rules.text() + "\n\n" + re.sub(r"<!--.*?-->\s*", "", text, flags=re.S).strip()


TRUNCATED = "\n…（记忆太长，后面的省略了，需要时调 get_memory）"
EMPTY = "（还没有长期记忆：多问几句、学到长期有用的事实就用 add_observation 记下来。）"


def _memory_sections(conn: psycopg.Connection, user_id: int) -> list[tuple[str, list[str], bool]]:
    """(heading, lines, must_keep) in priority order (issue #112): communication -> health constraints -> injuries ->
    recent notes in effect -> status card -> other profile card items -> lasting observation notes, newest first.
    must_keep lines are cut to fit rather than dropped."""
    try:
        status.refresh_if_stale(conn, user_id)
    except Exception:  # If the auto status cannot be computed, the conversation is unaffected
        pass
    m = memory.get_memory(conn, user_id)
    card = {c["key"]: c for c in m["profile_card"]}
    line = lambda c: f"**{c['label']}**（{c['updated_at']:%Y-%m-%d}）：{c['value']}"
    out = [("### 身体档案卡（优先）", [line(card[k]) for k in mp.PRIORITY_KEYS if k in card], True)]
    recent = [o for o in m["observations"] if o["kind"] == "recent"]
    out.append(("### 近况（到期后自动不再出现）",
                [f"- [{o['id']}] 到 {o['valid_until']:%Y-%m-%d}：{o['text']}" for o in recent], False))
    out.append(("### 状态卡", [f"- {s['label']}：{s['value']}{s.get('unit') or ''}" + (f"（{s['note']}）" if s.get("note") else "")
                              for s in status.list_status(conn, user_id)], False))
    out.append(("### 身体档案卡", [line(c) for c in m["profile_card"] if c["key"] not in mp.PRIORITY_KEYS], False))
    facts = [o for o in m["observations"] if o["kind"] != "recent"][::-1][:mp.PROMPT_FACTS_MAX]
    out.append(("### 观察笔记（新的在前）", [f"- [{o['id']}] {o['created_at']:%Y-%m-%d} {o['text']}" for o in facts], False))
    return out


def _memory_block(conn: psycopg.Connection, user_id: int, limit: int = MAX_MEMORY_CHARS) -> str:
    """Fills the memory up to `limit` characters in priority order instead of cutting the tail: a line that does not
    fit is skipped (a must_keep one is cut to the room left), lower-priority lines still get the room that is left."""
    sections = _memory_sections(conn, user_id)
    if not any(lines for _, lines, _ in sections):
        return EMPTY
    room = limit - len(TRUNCATED)
    parts, cut = [], False
    for head, lines, must in sections:
        kept = []
        for ln in lines:
            hdr = 0 if kept else len(head) + 1
            if hdr + len(ln) + 1 <= room:
                kept.append(ln)
                room -= hdr + len(ln) + 1
            else:
                cut = True
                if must and room - hdr - 2 > 40:
                    kept.append(ln[:room - hdr - 2] + "…")
                    room = 0
        if kept:
            parts += [head, *kept]
    return "\n".join(parts) + (TRUNCATED if cut else "")


def build_system_prompt(conn: psycopg.Connection, user_id: int, lite: bool = False) -> str:
    p = queries.profile(conn, user_id) or {}
    basics = [f"称呼：{p.get('display_name') or '—'}", f"时区：{p.get('timezone') or 'UTC'}"]
    basics.append(f"反馈模式：{feedback_store.mode(conn, user_id)}")  # root rule 4
    for k, label in (("age", "年龄"), ("sex", "性别"), ("height_cm", "身高 cm"), ("language", "语言")):
        if p.get(k) is not None:
            basics.append(f"{label}：{p[k]}")
    memory_block = _memory_block(conn, user_id, LITE_MEMORY_CHARS if lite else MAX_MEMORY_CHARS)
    return (f"{base_prompt(lite)}\n\n## 用户\n" + "；".join(basics)
            + f"\n\n## 长期记忆（对话开始时的快照）\n{memory_block}")


def time_prefix(conn: psycopg.Connection, user_id: int) -> str:
    tz = (conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone() or {}).get("timezone") or "UTC"
    now = datetime.now(ZoneInfo(tz))
    return f"[当前本地时间 {now:%Y-%m-%d %H:%M} 周{'一二三四五六日'[now.weekday()]}，{tz}]"
