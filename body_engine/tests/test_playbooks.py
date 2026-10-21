"""Playbooks (ai/playbooks/*.md, issue #100): the same text as MCP prompt and get_playbook, every tool they name exists,
each states its guardrails, and the fallback AI gets the scene tools but nothing that reads ECGs. Pure: no database.
(MCP prompts/list and prompts/get over /mcp: tests/test_ai.py::test_mcp_playbook_prompts.)"""
from importlib import resources

import pytest

from body_engine.ai import fallback, playbooks
from body_engine.assistant.tools import TOOLS_BY_NAME

KEY_TOOLS = {
    "plan_shopping": {"get_shopping_context", "record_store_prices"},
    "plan_training_week": {"get_training_context", "propose_plan", "save_plan"},
    "weekly_review": {"get_week_review_context"},
    "log_meal_from_photo": {"log_food"},
}


def test_files_match_names():
    files = {p.name[:-3] for p in resources.files("body_engine.ai.playbooks").iterdir() if p.name.endswith(".md")}
    assert files == set(playbooks.NAMES) == set(KEY_TOOLS)


@pytest.mark.parametrize("name", playbooks.NAMES)
def test_every_mentioned_tool_exists(name):
    named = playbooks.tools_mentioned(name)
    assert KEY_TOOLS[name] <= named
    assert named <= set(TOOLS_BY_NAME), named - set(TOOLS_BY_NAME)


@pytest.mark.parametrize("name", playbooks.NAMES)
def test_shape_and_guardrails(name):
    text = playbooks.text(name)
    assert text.startswith("# ") and playbooks.title(name) and playbooks.description(name) != playbooks.title(name)
    assert "<!--" not in text                                  # developer comments are stripped
    for heading in ("## 步骤", "## 什么时候先问用户", "## 护栏"):
        assert heading in text, heading
    guard = text.split("## 护栏", 1)[1]
    assert "只" in guard and "自己" in guard                    # only the user's own matters
    assert "联网" in guard                                      # web: only the list, or not at all
    assert len(text) < 3000, len(text)                          # read at the start of a task: keep it short


def test_shopping_guardrails_spelled_out():
    guard = playbooks.text("plan_shopping").split("## 护栏", 1)[1]
    assert "不绕过" in guard and "needs_price" in guard


def test_get_playbook_is_the_prompt_text():
    tool = TOOLS_BY_NAME["get_playbook"]
    assert not tool.writes and tool.input_schema["properties"]["name"]["enum"] == list(playbooks.NAMES)
    for n in playbooks.NAMES:
        got = tool.handler(None, 1, {"name": n})
        assert got == {"name": n, "title": playbooks.title(n), "text": playbooks.text(n)}
    with pytest.raises(ValueError):
        playbooks.text("nope")


def test_fallback_gets_the_scene_tools_but_no_ecg():
    names = set(fallback.TOOL_NAMES)
    assert {"get_playbook", "get_training_context", "get_week_review_context"} <= names
    assert not {"get_ecg_list", "get_cardio"} & names
