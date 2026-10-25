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


def test_hud_motion_is_transform_and_opacity_only_and_rests_when_hidden():
    """The HUD open / close (#53): Web Animations only move transform and opacity (the compositor runs them in the app's
    WKWebView); the idle breathing and turning run only while the menu is open and pause while the page is hidden; the
    open key shows chevron-down with a clear label, never the x."""
    for fn in ("function openMenu", "function closeMenu"):
        body = JS[JS.index(fn):JS.index("\n  }\n", JS.index(fn))]
        for props in re.findall(r"\{ ((?:offset|opacity|transform)[^{}]*)\}", body):
            for prop in re.findall(r"(\w+):", props):
                assert prop in ("offset", "opacity", "transform"), (fn, prop)
        assert "strokeDashoffset" not in body, fn
    assert 'const EXIT_ICON = "chevron-down";' in JS and '${lu(EXIT_ICON, 2.2)}' in JS
    assert 'tr("收起 Jarvis 菜单")' in JS
    assert 'document.addEventListener("visibilitychange", pause)' in JS
    assert "body.jv-paused :is(.jv-kglow, .jv-kring, .jv-ticks .spin) { animation-play-state: paused; }" in CSS
    # infinite animations only inside the fan (display:none while closed) or the open key, and off with reduced motion
    for m in re.finditer(r"([^{}]+)\{[^{}]*infinite[^{}]*\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):
        sel = m.group(1).strip()
        assert sel.startswith((".jv-fan", ".jv.open")) or "jv-vp-dot" in sel, sel
    assert ":is(.jv-kglow, .jv-kring, .jv-ticks .spin) { animation: none !important; }" in CSS


# ---------------------------------------------------------------- flag jarvis_dial: Liquid Glass in one ink
DIAL_MARK = "/* ---------- flag jarvis_dial"
CSS_OLD, CSS_DIAL = CSS.split(DIAL_MARK, 1)
CSS_DIAL = DIAL_MARK + CSS_DIAL


def test_dial_flag_declared_and_checked_once_at_mount():
    assert "jarvis_dial" in registry.FLAGS
    assert 'DIAL_FLAG = "jarvis_dial"' in JS
    assert JS.count("window.BodyFlags?.on(DIAL_FLAG)") == 1   # read once, when the key is mounted
    assert "let dial = false;" in JS
    assert 'if (dial) document.body.classList.add("jv-dial");' in JS


def test_flag_off_keeps_the_ring_menu_unchanged():
    """The existing look and animation are all still there and run when `dial` is false; the dial code is only reached
    through `if (dial)`, and its styles only apply under body.jv-dial."""
    for old in ("const R_KEY = 98, R_RING = 35, START = 225, SWEEP_MS = 560", "const FLY_MS = 440, BACK_MS = 260;",
                'lu("orbit", 1.7)', "lu(EXIT_ICON, 2.2)", 'id="jv-comet"', 'id="jv-ticks"', 'class="jv-shock"', 'class="jv-kglow"',
                "function orbitFrames(th, overshoot = 1.25)", 'id="jv-grad"', "th: 290,", "th: 336.67,", "th: 23.33,", "th: 70,"):
        assert old in JS, old
    assert "if (dial) { openDial(byKeyboard); return; }" in JS and "if (dial) { closeDial(focusBack); return; }" in JS
    assert "const seg = CIRC / SEGS, segs = dial ? [] : Array.from" in JS
    assert 'stroke-width="${dial ? 1.75 : sw}"' in JS
    for fn in ("function openDial", "function closeDial", "const spring = "):
        assert fn in JS, fn
    # the old stylesheet part has no dial rule and every dial rule starts at body.jv-dial
    assert "jv-dial" not in CSS_OLD
    body = re.sub(r"/\*.*?\*/", "", CSS_DIAL, flags=re.S)
    body = re.sub(r"@media[^{]*\{", "", body)
    for sel in re.findall(r"([^{}]+)\{[^{}]*\}", body):
        for one in re.split(r",(?![^()]*\))", sel):
            assert "jv-dial" in one, one.strip()


def test_flag_on_is_glass_on_a_spring_with_only_composited_properties():
    """jarvis_dial (Liquid Glass): glass discs with a static backdrop blur, one element and one animation per key on a CSS linear()
    spring, only transform and opacity (no SVG, no stroke-dashoffset, nothing looping), at most 6 things moving at once,
    will-change only while moving, neutral whites / blacks only, the final state at once with reduced motion."""
    dial_js = JS[JS.index("// ---- jarvis_dial"):JS.index("function bindMenu")]
    opening = dial_js[dial_js.index("function openDial"):dial_js.index("function closeDial")]
    # what moves: keys (transform + opacity keyframes) and the scrim (opacity); the Jarvis key's squeeze is a CSS transition
    assert opening.count("D.A.push(") == 2 and "el.animate([" in opening and "scrim.animate([" in opening
    for props in re.findall(r"\{ ((?:offset|opacity|transform)[^{}]*)\}", dial_js):
        for prop in re.findall(r"(\w+):", props):
            assert prop in ("offset", "opacity", "transform"), prop
    for gone in ("strokeDashoffset", "iterations", "Infinity", "createElementNS", "<svg", "dialLayer", "getTotalLength"):
        assert gone not in dial_js, gone
    assert 'GLASS_OPEN_MS = 380, GLASS_CLOSE_MS = 200' in JS
    # springs: CSS linear() generated from a damped spring, a plain curve where it is not supported
    assert "linear(${pts.join" in dial_js and 'CSS?.supports?.("animation-timing-function", "linear(0, 1)")' in dial_js
    assert "@supports (transition-timing-function: linear(0, 1))" in CSS_DIAL and "cubic-bezier(.2, .9, .3, 1.2)" in CSS_DIAL
    # will-change only while moving; the click after the touch cannot land on a key that is still under the Jarvis key; the scrim stops above the dock
    assert 'el.style.willChange = on ? "transform, opacity" : ""' in dial_js and "will(keys, false)" in dial_js
    assert 'el.style.pointerEvents = "none"' in dial_js and "scrim.style.bottom" in dial_js
    # the glass: a static backdrop blur (never animated), edge, highlight; the press squeezes to .94 and the sheen moves
    code = re.sub(r"/\*.*?\*/", "", CSS_DIAL, flags=re.S)
    assert "backdrop-filter: blur(16px) saturate(180%)" in code and "@keyframes" not in code and "animation" not in code
    for line in re.findall(r"transition[^;]*;", code):
        assert "filter" not in line and "box-shadow" not in line and "background" not in line, line
    assert "body.jv-dial .jv.pressed { transform: scale(.94);" in code and "translateY(2.5px)" in code
    assert "width: 46px; height: 46px" in code
    # one neutral ink: white / black / grey only
    for r, g, b, _ in re.findall(r"rgba\((\d+), (\d+), (\d+), ([\d.]+)\)", code):
        assert max(int(r), int(g), int(b)) - min(int(r), int(g), int(b)) <= 4, (r, g, b)
    for bad in ("--sleep", "--bad", "--jv-hud", "hsl", "url(", "indigo"):
        assert bad not in code, bad
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", code)
    # reduced motion: no transitions in CSS, no animations in JS (the keys are just there)
    assert "prefers-reduced-motion: reduce" in CSS_DIAL and "transition: none" in CSS_DIAL
    assert "if (rm) return;" in opening and "if (!rm) {" in opening
