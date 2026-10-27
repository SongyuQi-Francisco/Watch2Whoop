"""Flag ui_ai_first (#279, docs/ui-ai-first-redesign.md v0.4): the AI-first page in ui-ai-first.js.

With the flag off the page must be exactly as before: the flag is declared (off by default, like every flag), the file is declared
on demand (nobody else downloads it), and every place app.js hands over to it is behind the flag. The pure parts of the file (the
feature search, the question a long press asks, the example questions made from the user's numbers) run in node with stubs."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from body_engine.rollout import registry

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def test_flag_is_declared():
    assert "ui_ai_first" in registry.FLAGS


def test_the_file_loads_only_for_people_with_the_flag():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<script type="w2w/lazy" src="ui-ai-first.js"></script>' in index
    assert '<script src="ui-ai-first.js"' not in index
    loads = re.findall(r'.*lazyScript\("ui-ai-first\.js"\).*', APP)
    assert loads == ['    if (S.flags.includes("ui_ai_first")) await lazyScript("ui-ai-first.js");']


def test_every_hand_over_is_behind_the_flag():
    """A1() = the flag is on and the file has run; every use of BodyAIFirst in app.js goes through it, and the intro is skipped
    only for these people."""
    assert 'const A1 = () => (S.flags || []).includes("ui_ai_first") && !!window.BodyAIFirst;' in APP
    uses = [ln for ln in APP.splitlines() if "BodyAIFirst" in ln and "const A1 = " not in ln]
    assert len(uses) == 2 and all("A1()" in ln for ln in uses), uses
    assert 'me.me?.intro_seen === false && !A1()) lazyScript("intro.js")' in APP


HARNESS = r"""
globalThis.window = globalThis;
globalThis.document = { querySelector: () => null, querySelectorAll: () => [] };
globalThis.I18N = { t: (k, p) => k.replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m)) };
window.BodyPage = { S: { flags: [] }, friendsOn: () => true };
require(process.argv[1]);
for (const p of window.BodyPlugins) p.init();   // the flag is off here: init only picks up BodyPage
const T = window.BodyAIFirst._test;
console.log(JSON.stringify({
  recipes: T.matches("食谱"), weight: T.matches("体重"), area: T.matches("睡眠"), none: T.matches(""), nothing: T.matches("天气"),
  recovery: T.question({ key: "recovery", label: "恢复", value: "79" }),
  labKey: T.question({ key: "lab:alt", label: "ALT", value: "52" }),
  generic: T.question({ key: "food_day", label: "今天吃了", value: "1,240" }),
  plain: T.question({ key: "", label: "日记", value: "" }),
  qs: T.todayQuestions({ scores: { today: { recovery: 72, sleep_actual_min: 380, sleep_need_min: 470 } } }, true).map((q) => q.label),
  calm: T.todayQuestions({ scores: { today: { recovery: 80, sleep_actual_min: 470, sleep_need_min: 480 } } }, true).map((q) => q.label),
  other: T.todayQuestions({ scores: { today: { recovery: 72 } } }, false),
}));
"""


@needs_node
def test_pure_parts():
    out = json.loads(subprocess.run([NODE, "-e", HARNESS, str(STATIC / "ui-ai-first.js")], capture_output=True, text=True, check=True).stdout)
    assert out["recipes"] == ["食谱"]
    assert out["weight"] == ["体重"]
    assert out["area"][:3] == ["昨晚的睡眠", "睡眠趋势", "入睡目标"]   # an area name lists that area
    assert out["none"] == [] and out["nothing"] == []                 # nothing found: Enter asks Jarvis instead
    assert out["recovery"] == "为什么我今天恢复 79？"
    assert out["labKey"] == "「ALT」52，这说明什么？"
    assert out["generic"] == "「今天吃了」1,240，这说明什么？"
    assert out["plain"] == "「日记」是什么意思？"
    # concrete questions from the user's own numbers, at most three; nothing about someone else's page
    assert out["qs"] == ["为什么恢复 72？", "为什么睡得比需要的少？", "记一顿饭"]
    assert out["calm"] == ["为什么恢复 80？", "记一顿饭"]
    assert out["other"] == []
