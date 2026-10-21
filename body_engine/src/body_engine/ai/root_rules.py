"""Root rules every AI gets (root_rules.md, docs/ai-feedback-plan.md §1): one versioned text, injected into the in-page and
fallback system prompts (ai/prompt.py), the MCP instructions and the `butler` prompt (mcp_server.py) and get_briefing
(assistant/briefing.py). Not stored in the database: no tool can change it."""
import re
from functools import lru_cache
from importlib import resources


@lru_cache(maxsize=1)
def text() -> str:
    raw = resources.files("body_engine.ai").joinpath("root_rules.md").read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->\s*", "", raw, flags=re.S).strip()


@lru_cache(maxsize=1)
def version() -> str:
    m = re.search(r"根规则 (v\d+)", text())
    if not m:
        raise RuntimeError("root_rules.md 的标题里没有版本号「根规则 vN」")
    return m.group(1)
