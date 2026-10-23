"""Jarvis scope guard (issue #170): the fallback AI (the admin pays for it) only works on the person's own body and health.

Behind the rollout flag jarvis_scope_guard, and only for the fallback AI. A person's own AI (own API key, MCP subscription, the
body butler) is never judged. Two layers, neither calls a model (parameters, word lists and the reasoning: scope_guard_params.py):

- check(): rules on the user's message, before the model is called. A clearly off-topic and costly request (code, homework,
  long writing, long translation, a plainly unrelated subject) gets the fixed polite text (decline_text()), no model call, no
  tools, no quota used; chat.py stores it in the conversation like any answer and counts it (record(), tool `scope_guard` in
  assistant_audit: the category and nothing else), `body-engine ai-fallback status` shows the count per person.
- PROMPT: added to the system prompt of the fallback AI for everything the rules let through, so the model itself declines in
  a sentence or two (no tools) and keeps the gray areas.

With the flag off nothing here is called and the system prompt is exactly what it was.
"""
import re
import unicodedata
from datetime import datetime
from functools import lru_cache

import psycopg
from psycopg.types.json import Jsonb

from ..i18n import L
from . import scope_guard_params as sp

FLAG = sp.FLAG
PROMPT = sp.PROMPT
AUDIT_TOOL = "scope_guard"   # assistant_audit.tool of a blocked message; args = {"category": ...}
CATEGORIES = tuple(sp.OFF_TOPIC)


def on(conn: psycopg.Connection, user_id: int) -> bool:
    from ..rollout import flags
    return flags.enabled(conn, user_id, FLAG)


def applies(conn: psycopg.Connection, user_id: int, provider: dict) -> bool:
    """The guard judges this chat: the flag is on for the person and the chat runs on the fallback AI."""
    from . import fallback
    return fallback.is_fallback(provider) and on(conn, user_id)


def decline_text() -> str:
    return L(sp.DECLINE_ZH, sp.DECLINE_EN)


# ---------------------------------------------------------------- the rules
def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower().strip()


@lru_cache(maxsize=1)
def _on_topic_en() -> re.Pattern:
    parts = []
    for w in sp.ON_TOPIC_EN:
        if w.endswith("*"):
            parts.append(re.escape(w[:-1]) + r"[a-z]*")
        else:
            parts.append(r"\s+".join(re.escape(x) for x in w.split(" ")) + r"(?:s|es|ed|ing)?")
    return re.compile(r"(?<![a-z])(?:" + "|".join(parts) + r")(?![a-z])")


@lru_cache(maxsize=1)
def _patterns() -> dict[str, tuple[re.Pattern, ...]]:
    return {c: tuple(re.compile(p) for p in ps) for c, ps in sp.OFF_TOPIC.items()}


@lru_cache(maxsize=1)
def _whole_document() -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p) for p in sp.WHOLE_DOCUMENT)


@lru_cache(maxsize=1)
def _code_line() -> re.Pattern:
    return re.compile(sp.CODE_LINE)


def _on_topic(text: str) -> bool:
    return any(w in text for w in sp.ON_TOPIC_ZH) or _on_topic_en().search(text) is not None


def _points_back(text: str) -> bool:
    return len(text) <= sp.ANAPHORA_MAX_CHARS and any(w in text for w in sp.ANAPHORA)


def _pasted_code(text: str) -> bool:
    return sp.CODE_FENCE in text and len(_code_line().findall(text)) >= sp.CODE_HITS


def check(message: str, images: int = 0) -> str | None:
    """The category of a clearly off-topic request, or None (let it through). Never judges a short message, a message that
    names anything from the health / app vocabulary (except pasted code), or one that points back at the conversation."""
    text = _norm(message)
    if _pasted_code(text):
        return "code"
    if len(text) < sp.MIN_CHARS or _on_topic(text) or _points_back(text):
        return None
    for category, pats in _patterns().items():
        if not any(p.search(text) for p in pats):
            continue
        if category == "general":
            if len(text) >= sp.GENERAL_MIN_CHARS:
                return category
        elif category in ("translation", "rewrite"):
            if images:   # a picture may be a lab report or a training plan: leave it to the model
                continue
            minimum = sp.TRANSLATE_MIN_CHARS if category == "translation" else sp.REWRITE_MIN_CHARS
            if len(text) >= minimum or any(p.search(text) for p in _whole_document()):
                return category
        else:
            return category
    return None


# ---------------------------------------------------------------- audit, admin count
def record(conn: psycopg.Connection, user_id: int, category: str) -> None:
    """One assistant_audit row (channel api:system_fallback): the category only, never the message."""
    from . import fallback
    conn.execute("INSERT INTO assistant_audit (user_id, channel, tool, args, ok, error) VALUES (%s,%s,%s,%s,true,NULL)",
                 (user_id, f"api:{fallback.KIND}", AUDIT_TOOL, Jsonb({"category": category})))


def blocked_since(conn: psycopg.Connection, user_id: int, since: datetime) -> int:
    return conn.execute("SELECT count(*) AS n FROM assistant_audit WHERE user_id = %s AND tool = %s AND at >= %s",
                        (user_id, AUDIT_TOOL, since)).fetchone()["n"]
