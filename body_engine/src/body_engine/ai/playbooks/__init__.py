"""Playbooks: step-by-step instructions for one task, for the AI (Chinese, AI-facing), issue #100.

Each NAME.md here is served two ways with the very same text: as an MCP prompt NAME (mcp_server.py, next to `butler`)
and by the tool get_playbook(name) (clients that do not show prompts, the in-page chat). The first line `# 标题` is the
title, the `> …` line under it the one-line description; HTML comments are for developers and are stripped.
Every `tool_name` a playbook mentions must exist (tests/test_playbooks.py). FIND_LINE is put under the description of every
playbook (tools not in the client's list are reached with find_tools); FALLBACK marks the ones the fallback AI can do.
"""
import re
from functools import lru_cache
from importlib import resources

NAMES = ("plan_shopping", "plan_training_week", "weekly_review", "log_meal_from_photo")
# Playbooks the fallback AI can carry out: every tool they name is in ai/fallback.TOOL_NAMES (tests/test_playbooks.py).
# The whitelist is not widened for them (owner's decision); the others answer the fallback with FALLBACK_NO.
# plan_shopping / log_meal_from_photo need several grocery / meal tools the fallback does not have.
FALLBACK = {"plan_shopping": False, "plan_training_week": True, "weekly_review": True, "log_meal_from_photo": False}
# Playbooks the fallback AI can carry out once the rollout flag ai_web gives it the web tools and ai/web.FALLBACK_GROCERY
# (issue #150; tests/test_playbooks.py checks the tools against that list too).
FALLBACK_WEB = {"plan_shopping"}
# Tools a playbook can do without: it says what to do when they are missing (weekly_review: suggest the numbers instead of
# changing the nutrition goals). Not counted when checking FALLBACK against the whitelist.
OPTIONAL_TOOLS = {"weekly_review": {"set_nutrition_goals"}}
FALLBACK_NO = "这件事需要用户自己的 AI（托底 AI 没有它要用的工具）：告诉用户可以在「我 → AI 设置」接入自己的 AI；能做的部分照常帮用户做。"
# One line at the top of every playbook, so the MCP prompt and get_playbook stay the same text (#109 review)
FIND_LINE = "提到的工具不在你的工具列表里时，用 find_tools 找，再用 run_read_tool / run_write_tool 调。"
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
    body = re.sub(r"<!--.*?-->\s*", "", raw, flags=re.S).strip()
    return re.sub(r"^(> .+)$", lambda m: f"{m.group(1)}\n\n{FIND_LINE}", body, count=1, flags=re.M)


def title(name: str) -> str:
    return text(name).splitlines()[0].lstrip("# ").strip()


def description(name: str) -> str:
    m = re.search(r"^> (.+)$", text(name), flags=re.M)
    return m.group(1).strip() if m else title(name)


def tools_mentioned(name: str) -> set[str]:
    return set(TOOL_RE.findall(text(name)))


def get(name: str, for_fallback: bool = False, web: bool = False) -> dict:
    """What get_playbook returns: the same text as the MCP prompt of that name; the fallback AI gets FALLBACK_NO for the
    playbooks it cannot carry out (web: ai_web is on for the caller, FALLBACK_WEB become possible)."""
    if for_fallback and not FALLBACK[name] and not (web and name in FALLBACK_WEB):
        return {"name": name, "title": title(name), "fallback": False, "text": FALLBACK_NO}
    return {"name": name, "title": title(name), "text": text(name)}
