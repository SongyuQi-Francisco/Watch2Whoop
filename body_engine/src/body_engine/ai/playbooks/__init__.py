"""Playbooks: step-by-step instructions for one task, for the AI (Chinese, AI-facing), issue #100.

Each NAME.md here is served two ways with the very same text: as an MCP prompt NAME (mcp_server.py, next to `butler`)
and by the tool get_playbook(name) (clients that do not show prompts, the in-page chat). The first line `# 标题` is the
title, the `> …` line under it the one-line description; HTML comments are for developers and are stripped.
Every `tool_name` a playbook mentions must exist (tests/test_playbooks.py).
"""
import re
from functools import lru_cache
from importlib import resources

NAMES = ("plan_shopping", "plan_training_week", "weekly_review", "log_meal_from_photo")
# a backticked word that names a tool (verb_…); field names such as `needs_price` do not start with these verbs
TOOL_RE = re.compile(r"`((?:get|log|list|set|add|update|delete|remove|undo|save|propose|record|generate|mark|find|search|"
                     r"suggest|swap|reschedule|replan|analyze|lookup|rollback|report|create|end|dismiss)_[a-z_]+)`")


def _check(name: str) -> None:
    if name not in NAMES:
        raise ValueError(f"没有这个 playbook：{name}；有：{' / '.join(NAMES)}")


@lru_cache(maxsize=None)
def text(name: str) -> str:
    _check(name)
    raw = resources.files("body_engine.ai.playbooks").joinpath(f"{name}.md").read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->\s*", "", raw, flags=re.S).strip()


def title(name: str) -> str:
    return text(name).splitlines()[0].lstrip("# ").strip()


def description(name: str) -> str:
    m = re.search(r"^> (.+)$", text(name), flags=re.M)
    return m.group(1).strip() if m else title(name)


def tools_mentioned(name: str) -> set[str]:
    return set(TOOL_RE.findall(text(name)))


def get(name: str) -> dict:
    """What get_playbook returns: the same text as the MCP prompt of that name."""
    return {"name": name, "title": title(name), "text": text(name)}
