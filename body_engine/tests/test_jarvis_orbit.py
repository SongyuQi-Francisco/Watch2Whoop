"""Jarvis entry (issue #53, rollout flags jarvis_orbit and jarvis_voice_direct): nothing changes with the flag off, the page
files are wired and cached, the voice text is only sent without a check when jarvis_voice_direct is on (the consent says the
text is confirmed first), only Jarvis (the fallback AI) gets the quick entry, reduced motion is handled in JS, and the icons
are the published Lucide paths with their notice."""
import re
from pathlib import Path

from body_engine.rollout import registry

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "body_engine/src/body_engine/web/static"
JS = (STATIC / "jarvis-entry.js").read_text(encoding="utf-8")
CSS = (STATIC / "jarvis-entry.css").read_text(encoding="utf-8")
AI = (STATIC / "ai.js").read_text(encoding="utf-8")


def test_flags_declared_and_checked():
    assert "jarvis_orbit" in registry.FLAGS and "jarvis_voice_direct" in registry.FLAGS
    assert 'const FLAG = "jarvis_orbit";' in JS and "if (!window.BodyFlags?.on(FLAG)) return;" in JS
    assert 'window.BodyFlags?.on("jarvis_voice_direct")' in JS


def test_wired_after_ai_and_cached():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="ai.js"') < html.index('src="jarvis-entry.js"') < html.index('src="app.js"')
    assert html.index('href="ai.css"') < html.index('href="jarvis-entry.css"')
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert '"jarvis-entry.js"' in sw and '"jarvis-entry.css"' in sw
    # the dock keeps the old pill in the HTML: the plugin replaces it only with the flag on
    assert '<a class="ask" id="ask"' in html


def test_css_only_touches_its_own_elements():
    """With the flag off no rule may apply: every selector names a jv-* element, body.jv-orbit, the half mode of the chat
    panel (.jv-half, set only by BodyAI.openHalf) or the half sheet's own parts."""
    body = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    body = re.sub(r"@media[^{]*\{", "", body)
    rules = re.findall(r"([^{}]+)\{[^{}]*\}", body)
    assert len(rules) > 40
    own = re.compile(r"\.jv\b|jv-|ai-half|ai-quicklog|ai-more|ai-voice-tag|#ai-mic\.rec|:root")
    for sel in rules:
        for one in re.split(r",(?![^()]*\))", sel):   # commas inside :is(...) belong to that selector
            one = one.strip()
            if one in ("to", "from") or re.fullmatch(r"\d+%", one):
                continue
            assert own.search(one), one
    assert not re.search(r"\.ai-chat\.half\b|\.half\b", CSS), "app.css has a generic .half (grid) class: use .jv-half"


def test_voice_is_checked_before_sending_unless_direct():
    # review state: the recognised text is editable and only 发送 sends it
    assert 'if (directSend()) { endVoice(); deliver(out); return; }' in JS
    assert 'setState("review")' in JS and "function sendReviewed()" in JS
    # the message to the model carries the voice marker the system prompts explain
    assert 'message: `${tr("［语音］")}${text}`' in AI


def test_only_jarvis_gets_the_quick_entry():
    assert 'isJarvis() { return !!C && C.ai?.mode === "fallback"; }' in AI
    assert "if (!isJarvis()) { C.openAssistant?.(); return; }" in JS   # own AI / butler / none: the old tap
    assert "const voiceOn = () => !!(jv && isJarvis() && engineFor(voiceLang()));" in JS
    # the chat's own mic keeps its behaviour for everyone else
    assert "if (window.BodyJarvis?.ownsMic()) return;" in AI


def test_reduced_motion_in_js():
    assert 'matchMedia?.("(prefers-reduced-motion: reduce)")' in JS
    for fn in ("function openMenu", "function closeMenu"):
        start = JS.index(fn)
        assert "reduced()" in JS[start:JS.index("\n  }\n", start)], fn
    assert "@media (prefers-reduced-motion: reduce)" in CSS


def test_native_voice_needs_the_apps_flag_and_finalises_chinese_on_the_server():
    assert "window.BodyMedia?.nativeVoice()" in JS
    assert 'const audioFor = (l) => l === "zh";' in JS
    assert 'fetch("/v1/assistant/transcribe?fix=1"' in JS and '"/v1/assistant/voice-fix"' in JS
    for verb in ("start", "stop", "cancel", "lang"):
        assert f'"{verb}"' in JS, verb


def test_lucide_icons_and_notice():
    orbit = '<path d="M20.341 6.484A10 10 0 0 1 10.266 21.85"/><path d="M3.659 17.516A10 10 0 0 1 13.74 2.152"/><circle cx="12" cy="12" r="3"/><circle cx="19" cy="5" r="2"/><circle cx="5" cy="19" r="2"/>'
    assert orbit in JS
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for name in ("orbit", "bot-message-square", "audio-waveform", "scan-eye", "zap", "chevron-down", "mic", "x", "lock", "arrow-up", "audio-lines", "chevron-right"):
        assert f"lucide/blob/main/icons/{name}.svg" in notices, name


def test_menu_motion_is_quick_transform_and_opacity_only_and_one_ink():
    """The menu (#53, speed + mono pass): about 200 ms, no stagger, no bounce; Web Animations only move transform and opacity
    (the compositor runs them in the app's WKWebView); nothing loops; the open key shows chevron-down with a clear label; and
    the whole Jarvis look is one ink (the page's --text): no colour, gradient or glow."""
    assert "const OPEN_MS = 200, CLOSE_MS = 150" in JS
    for fn in ("function openMenu", "function closeMenu", "function arcFrames"):
        body = JS[JS.index(fn):JS.index("\n  }\n", JS.index(fn))]
        for props in re.findall(r"\{ ((?:offset|opacity|transform)[^{}]*)\}", body):
            for prop in re.findall(r"(\w+):", props):
                assert prop in ("offset", "opacity", "transform"), (fn, prop)
        assert "strokeDashoffset" not in body, fn
        assert "delay: order" not in body and "overshoot" not in body, fn   # no stagger, no bounce
    # the press answers on pointerdown in CSS (no JS between the touch and the first frame)
    assert ".jv.pressed { transform: scale(.92); }" in CSS and "transition: transform .08s" in CSS
    assert 'const EXIT_ICON = "chevron-down";' in JS and "${lu(EXIT_ICON)}" in JS
    assert 'tr("收起 Jarvis 菜单")' in JS
    # nothing runs forever in the menu (only the voice panel's recording dot blinks)
    for m in re.finditer(r"([^{}]+)\{[^{}]*infinite[^{}]*\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):
        assert "jv-vp-dot" in m.group(1), m.group(1)
    # one ink: no colour, gradient, glow or accent token anywhere in the Jarvis files
    code = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    for bad in ("gradient", "--sleep", "--bad", "--accent", "hud", "glow", "#fff;", "hsl", "--bad-soft"):
        assert bad not in code, bad
    for colour in re.findall(r"#[0-9a-fA-F]{3,8}\b", code):
        assert colour.lower() in ("#ffffff", "#151517"), colour   # the key's face: white / near-black
    assert "linearGradient" not in JS and "jv-grad" not in JS
    # one stroke width for the Lucide icons
    assert "const lu = (name, sw = 1.75)" in JS and not re.search(r"lu\([^)]*,\s*[\d.]+\)", JS)
    assert ".jv, .jv-ic, .jv-kbase" in CSS   # reduced motion drops the transitions too
