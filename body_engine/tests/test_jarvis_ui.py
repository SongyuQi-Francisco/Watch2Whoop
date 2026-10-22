"""Jarvis chat panel UI v2 (issue #53, rollout flag jarvis_ui_v2): with the flag off the panel keeps its look, so every v2 rule
is scoped under .ai-chat.v2 and only ai.js decides (once, when it builds the panel) whether the panel gets the class."""
import re
from pathlib import Path

from body_engine.rollout import registry

STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"


def test_flag_is_declared():
    assert "jarvis_ui_v2" in registry.FLAGS


def test_v2_css_is_scoped_and_every_selector_starts_with_the_panel_class():
    css = (STATIC / "ai.css").read_text(encoding="utf-8")
    block = css[css.index("/* ---------- Jarvis UI v2"):]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    block = re.sub(r"@media[^{]*\{", "", block)   # the media blocks' rules are checked like the others
    rules = re.findall(r"([^{}]+)\{[^{}]*\}", block)
    assert len(rules) > 15
    for sel in rules:
        for one in sel.split(","):
            assert one.strip().startswith(".ai-chat.v2"), one.strip()


def test_flag_gates_the_class_and_the_old_greeting_stays():
    js = (STATIC / "ai.js").read_text(encoding="utf-8")
    assert 'window.BodyFlags?.on("jarvis_ui_v2") ? " v2" : ""' in js
    # flag off: the greeting is still one assistant bubble with the same text as before
    assert 'bubble("assistant", [tr("你好，我是你的身体管家。问我今天怎么样' in js
    # the welcome block belongs to v2 only and goes away with the first user message
    assert 'classList.contains("v2")' in js and 'if (role === "user") $(".ai-welcome")?.remove();' in js
