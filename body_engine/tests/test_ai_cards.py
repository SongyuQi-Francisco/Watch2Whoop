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


# ---------------------------------------------------------------- v2 (flag jarvis_cards_v2, ai/cards_food.py)
def test_v2_tool_fits_and_is_in_page_only():
    assert cards.FLAG_V2 in FLAGS
    assert toolsets.definition_chars([cards.TOOL_V2]) <= 1100, toolsets.definition_chars([cards.TOOL_V2])
    for page in P.PAGE_GROUPS:
        assert toolsets.definition_chars([*toolsets.for_page(TOOLS, page), cards.TOOL_V2]) <= P.PAGE_BUDGET_CHARS, page
    assert cards.tool(False) is cards.TOOL and cards.tool(True) is cards.TOOL_V2
    assert cards.prompt(False) == cards.PROMPT and cards.prompt(True).startswith(cards.PROMPT)
    assert set(cards.TOOL_V2.input_schema["properties"]["type"]["enum"]) == {*cards.TYPES, *cards.TYPES_V2}


def test_v2_types_are_refused_without_the_flag():
    for t in cards.TYPES_V2:
        with pytest.raises(ValueError):
            cards.build(None, 1, {"type": t})


def test_v2_auto_maps():
    writes = {t.name for t in TOOLS if t.writes}
    reads = {t.name for t in TOOLS if not t.writes}
    assert set(cards.AUTO_V2) <= writes and set(cards.READ_AUTO) <= reads
    assert cards.auto_spec("set_nutrition_goals", {}) is None   # v2 off: exactly the old map
    assert cards.auto_spec("set_nutrition_goals", {}, v2=True) == {"type": "goals", "date": None}
    assert cards.auto_spec("undo_sparky_entry", {"kind": "set", "date": "2026-10-20"}, v2=True) is None
    assert cards.auto_spec("get_nutrition_goals", {"date": "2026-10-20"}, v2=True) is None   # reads: fallback only
    assert cards.auto_spec("get_nutrition_goals", {"date": "2026-10-20"}, v2=True, reads=True) == {"type": "goals", "date": "2026-10-20"}
    assert cards.auto_spec("get_nutrition", {"start": "2026-10-14", "end": "2026-10-20"}, v2=True, reads=True) is None
    assert cards.auto_spec("get_recipe", {"recipe_id": 7}, v2=True, reads=True) == {"type": "recipe", "date": None, "id": 7}
    assert cards.auto_spec("log_food", {"items": []}, v2=True, reads=True) == {"type": "meal", "date": None}


def test_v2_summaries_are_short():
    cs = [{"type": "goals", "date": "2026-10-22", "name": "训练日", "totals": {"calories": 1500, "protein_g": 90},
           "goal": {"calories": 2000, "protein_g": 150}},
          {"type": "suggest", "slots": [{"slot": "lunch", "items": [{"id": 3, "name": "鸡胸饭"}]}]},
          {"type": "recipe", "id": 3, "name": "鸡胸饭", "kcal": 600, "protein_g": 50, "ingredients": [{}] * 3, "steps": 4},
          {"type": "recipes", "total": 12, "items": [{"id": 3, "name": "鸡胸饭"}]},
          {"type": "templates", "items": [{"id": 1, "name": "早餐", "logged": True}]},
          {"type": "shopping", "week_start": "2026-10-19", "progress": {"bought": 2, "total": 9}}]
    for c in cs:
        s = cards.summary(c)
        assert s.startswith("已显示") and len(s) < 160, s


def test_every_card_action_is_drawn_by_the_page():
    """Each open page / send key a card can carry has its label in ai.js and (pages) a route in app.js goPage."""
    from pathlib import Path
    static = Path(cards.__file__).parent.parent / "web" / "static"
    ai_js, app_js = (static / "ai.js").read_text(encoding="utf-8"), (static / "app.js").read_text(encoding="utf-8")
    for key in cards.SEND_KEYS:
        assert f"    {key}: " in ai_js, key
    for page in cards.OPEN_PAGES:
        assert f'"{page}": tr(' in ai_js or f"{page}: tr(" in ai_js, page
        tab = page.split(".")[0]
        assert f'page === "{page}"' in app_js or f'"{tab}"' in app_js, page


def test_v2_read_auto_covers_the_common_reads_and_no_ecg():
    for tool in ("get_sleep", "get_scores", "get_healthspan", "get_weight", "get_lab_history", "get_report",
                 "get_health_monitor", "get_vo2max", "get_hr_zones", "get_stress", "list_supplements", "analyze_journal"):
        assert tool in cards.READ_AUTO, tool
    assert not {t for t in cards.READ_AUTO if "ecg" in t or "cardio" in t or "heartbeat" in t}
    assert cards.auto_spec("get_lab_history", {"test": "尿酸"}, v2=True, reads=True) == {"type": "lab", "date": None, "metric": "尿酸"}
    assert cards.auto_spec("get_report", {"period": "month", "end": None}, v2=True, reads=True)["range"] == "month"
    assert cards.auto_spec("add_lab_results", {"results": [{"test": "尿酸", "test_key": "uric_acid"}]}, v2=True) == \
        {"type": "lab", "date": None, "metric": "uric_acid"}
    assert "ecg" not in cards.TYPES_V2 and "cardio" not in cards.TYPES_V2
