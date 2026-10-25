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


# ---------------------------------------------------------------- flag jarvis_dial: the start-up sequence from HTML layers in one ink
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
    for fn in ("function openDial", "function closeDial", "function dialLayer", "const spring = "):
        assert fn in JS, fn
    # the old stylesheet part has no dial rule and every dial rule starts at body.jv-dial
    assert "jv-dial" not in CSS_OLD
    body = re.sub(r"/\*.*?\*/", "", CSS_DIAL, flags=re.S)
    body = re.sub(r"@media[^{]*\{", "", body)
    for sel in re.findall(r"([^{}]+)\{[^{}]*\}", body):
        for one in re.split(r",(?![^()]*\))", sel):
            assert "jv-dial" in one, one.strip()


def test_flag_on_is_html_layers_moving_only_transform_and_opacity_in_one_ink():
    """jarvis_dial: the start-up sequence from plain HTML layers made at mount (no SVG, no stroke / filter / shadow / border animation),
    only transform and opacity animated, at most 8 things at once, no loop, will-change only while it runs, about 320 ms open and
    160 ms close, the press answered by a CSS class at once, neutral blacks / whites only, the final state at once with reduced motion."""
    dial_js = JS[JS.index("// ---- jarvis_dial"):JS.index("function bindMenu")]
    opening = dial_js[dial_js.index("function openDial"):dial_js.index("function closeDial")]
    for props in re.findall(r"\{ ((?:offset|opacity|transform)[^{}]*)\}", dial_js):
        for prop in re.findall(r"(\w+):", props):
            assert prop in ("offset", "opacity", "transform"), prop
    for gone in ("strokeDashoffset", "iterations", "Infinity", "createElementNS", "<svg", "stroke-dash", "getTotalLength", "filter", "boxShadow"):
        assert gone not in dial_js, gone
    assert "DIAL_CLOSE_MS = 160" in JS and "DIAL_HIT = [105, 128, 151, 174]" in JS
    # the schedule: every delay + duration ends by 320 ms and no more than 8 animations / transitions overlap
    iv = [(0, 70, "pulse"), (0, 70, "flash"), (30, 120, "r1"), (45, 135, "r2"), (60, 150, "r3"), (70, 230, "scan"), (230, 320, "halo"), (0, 200, "jv release"), (0, 120, "scrim")]
    for h in (105, 128, 151, 174):
        iv += [(h - 10, h + 80, "key"), (h + 40, h + 90, "outline")]
    assert max(e for _, e, _ in iv) <= 320
    assert max(sum(1 for b, e, _ in iv if b <= t < e) for t in range(0, 320)) <= 8
    for needle in ("delay: DIAL_HIT[i] - 10, duration: 90", "delay: DIAL_HIT[i] + 40, duration: 50", "{ delay, duration: 90 }", "delay: 230, duration: 90", "delay: 70, duration: 160"):
        assert needle in opening, needle   # the schedule above is the code's
    assert opening.count("an(") == 7   # pulse, flash, rings (loop of 3), scan, keys and outlines (loops of 4), halo: 15 animations, never more than 8 at once
    # built once at mount, in the laid-out fan; the press arms the layers; will-change only while armed / running; nothing is created when opening
    assert "createElement" not in dial_js and "dialLayer()" in JS and "if (dial) dialArm(true);" in JS
    assert "${dial ? \"\" : \"hidden\"}" in JS and "fan.classList.remove(\"arm\"); fan.classList.add(\"on\", \"anim\");" in opening
    assert "will-change: transform, opacity" in CSS_DIAL and ":is(.arm, .anim)" in CSS_DIAL
    # springs: CSS linear() generated from a damped spring, a plain curve where it is not supported
    assert "linear(${pts.join" in dial_js and 'CSS?.supports?.("animation-timing-function", "linear(0, 1)")' in dial_js
    assert "@supports (transition-timing-function: linear(0, 1))" in CSS_DIAL
    # the scrim ends above the dock, so the dock's own backdrop blur never sees a changing picture; its fade is a plain opacity transition
    assert "scrim.style.bottom" in opening and "backdrop-filter" not in CSS_DIAL and "filter:" not in CSS_DIAL
    code = re.sub(r"/\*.*?\*/", "", CSS_DIAL, flags=re.S)
    assert "@keyframes" not in code and "animation" not in code
    for line in re.findall(r"transition[^;]*;", code):
        assert "filter" not in line and "shadow" not in line and "background" not in line and "border" not in line, line
    # the press: a class on pointerdown and a 50 ms transition
    assert "body.jv-dial .jv.pressed { transform: scale(.92); transition: transform .05s ease-out;" in code
    assert "width: 46px; height: 46px" in code
    # one neutral ink: black / white / grey only
    for r, g, b, _ in re.findall(r"rgba\((\d+), (\d+), (\d+), ([\d.]+)\)", code):
        assert max(int(r), int(g), int(b)) - min(int(r), int(g), int(b)) <= 4, (r, g, b)
    for bad in ("--sleep", "--bad", "--jv-hud", "hsl", "url(", "indigo"):
        assert bad not in code, bad
    for colour in re.findall(r"#[0-9a-fA-F]{3,8}\b", code):
        assert colour.lower() in ("#ffffff", "#151517"), colour
    # reduced motion: nothing is animated (the layers sit in their resting style = the end state), transitions off
    assert "prefers-reduced-motion: reduce" in CSS_DIAL and "transition: none" in CSS_DIAL
    assert "if (!rm) {" in opening
