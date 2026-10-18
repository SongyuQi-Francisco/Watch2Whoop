"""System prompt of the chat: a provider-independent persona (system_prompt.md) + this user's own basic profile and long-term memory.

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
from ..assistant import memory, status

MAX_MEMORY_CHARS = 12000


LITE_MEMORY_CHARS = 4000  # the fallback AI (fallback.py): shorter memory snapshot, fewer tokens per call


def base_prompt(lite: bool = False) -> str:
    name = "system_prompt_lite.md" if lite else "system_prompt.md"
    text = resources.files("body_engine.ai").joinpath(name).read_text()
    return re.sub(r"<!--.*?-->\s*", "", text, flags=re.S).strip()


def _memory_block(conn: psycopg.Connection, user_id: int, limit: int = MAX_MEMORY_CHARS) -> str:
    try:
        status.refresh_if_stale(conn, user_id)
    except Exception:  # If the auto status cannot be computed, the conversation is unaffected
        pass
    m = memory.get_memory(conn, user_id)
    lines = []
    st = status.list_status(conn, user_id)
    if st:
        lines.append("### 状态卡")
        lines += [f"- {s['label']}：{s['value']}{s.get('unit') or ''}" + (f"（{s['note']}）" if s.get("note") else "")
                  for s in st]
    if m["profile_card"]:
        lines.append("### 身体档案卡")
        lines += [f"**{c['label']}**（{c['updated_at']:%Y-%m-%d}）：{c['value']}" for c in m["profile_card"]]
    if m["observations"]:
        lines.append("### 观察笔记")
        lines += [f"- [{o['id']}] {o['created_at']:%Y-%m-%d} {o['text']}" for o in m["observations"][-60:]]
    text = "\n".join(lines) or "（还没有长期记忆：多问几句、学到长期有用的事实就用 add_observation 记下来。）"
    if len(text) > limit:
        text = text[:limit] + "\n…（太长截断了，需要时调 get_memory）"
    return text


def build_system_prompt(conn: psycopg.Connection, user_id: int, lite: bool = False) -> str:
    p = queries.profile(conn, user_id) or {}
    basics = [f"称呼：{p.get('display_name') or '—'}", f"时区：{p.get('timezone') or 'UTC'}"]
    for k, label in (("age", "年龄"), ("sex", "性别"), ("height_cm", "身高 cm")):
        if p.get(k) is not None:
            basics.append(f"{label}：{p[k]}")
    memory_block = _memory_block(conn, user_id, LITE_MEMORY_CHARS if lite else MAX_MEMORY_CHARS)
    return (f"{base_prompt(lite)}\n\n## 用户\n" + "；".join(basics)
            + f"\n\n## 长期记忆（对话开始时的快照）\n{memory_block}")


def time_prefix(conn: psycopg.Connection, user_id: int) -> str:
    tz = (conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone() or {}).get("timezone") or "UTC"
    now = datetime.now(ZoneInfo(tz))
    return f"[当前本地时间 {now:%Y-%m-%d %H:%M} 周{'一二三四五六日'[now.weekday()]}，{tz}]"
