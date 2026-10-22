"""Cards in the in-page chat (ai/cards.py, issue #53, flag jarvis_rich): pure parts, no database.
The chat-level tests (events, storage, flag on / off, automatic cards) are in test_ai.py."""
import pytest

from body_engine.ai import cards
from body_engine.ai import params as P
from body_engine.assistant import toolsets
from body_engine.assistant.tools import TOOLS, TOOLS_BY_NAME
from body_engine.rollout.registry import FLAGS


def test_tool_is_small_and_in_page_only():
    assert cards.FLAG in FLAGS
    assert toolsets.definition_chars([cards.TOOL]) <= 900, toolsets.definition_chars([cards.TOOL])
    for page in P.PAGE_GROUPS:   # still fits the in-page budget with the card tool added
        assert toolsets.definition_chars([*toolsets.for_page(TOOLS, page), cards.TOOL]) <= P.PAGE_BUDGET_CHARS, page
    # Remote MCP clients never get it: not a registered tool, not in tools/list
    assert cards.NAME not in TOOLS_BY_NAME
    assert cards.NAME not in {t.name for t in toolsets.for_mcp(True)}


def test_auto_cards_only_for_known_write_tools():
    assert set(cards.AUTO) <= {t.name for t in TOOLS if t.writes}
    assert cards.auto_spec("log_food", {"items": [{"date": "2026-10-20", "name": "米饭"}]}) == {"type": "meal", "date": "2026-10-20"}
    assert cards.auto_spec("log_food", {"items": []}) == {"type": "meal", "date": None}
    assert cards.auto_spec("log_weight", {"date": None}) == {"type": "metric", "date": None, "metric": "weight", "days": 30}
    assert cards.auto_spec("get_scores", {}) is None


def test_actions_are_sanitized():
    card = cards.build(None, 1, {"type": "actions", "buttons": [
        {"label": "x" * 50, "text": "y" * 500}, {"label": "", "text": "空的不要"}, {"label": "b", "text": "c"},
        {"label": "d", "text": "e"}, {"label": "f", "text": "g"}, {"label": "h", "text": "i"}]})
    acts = card["actions"]
    assert card["v"] == 1 and len(acts) == 3   # at most 4 given, the empty one dropped
    assert all(a["kind"] == "send" and set(a) == {"kind", "label", "text"} for a in acts)
    assert len(acts[0]["label"]) == cards.MAX_LABEL and len(acts[0]["text"]) == cards.MAX_TEXT
    with pytest.raises(ValueError):
        cards.build(None, 1, {"type": "actions", "buttons": None})
    with pytest.raises(ValueError):
        cards.build(None, 1, {"type": "html"})


def test_fixed_actions_are_known_keys_and_pages():
    for a in [{"kind": "open", "page": p} for p in cards.OPEN_PAGES] + [{"kind": "send", "key": k} for k in cards.SEND_KEYS]:
        assert set(a) <= {"kind", "page", "key"}


def test_summary_is_short_and_does_not_repeat_the_data():
    meal = {"type": "meal", "date": "2026-10-22", "totals": {"calories": 1850.0, "protein_g": 120.5},
            "goal": {"calories": 2000}, "entries": [{}] * 5}
    s = cards.summary(meal)
    assert "1850" in s and "目标 2000" in s and "不要用文字复述" in s and len(s) < 120
    metric = {"type": "metric", "metric": "hrv", "unit": "ms", "dates": ["d"] * 14, "latest": 52, "avg": 48}
    assert "近 14 天" in cards.summary(metric)
