"""Native views of the iOS app (#247 phase 2, app 1.0.9+; flags native_jarvis / native_today): native-bridge.js and its hooks.

- the flags are declared and default off; the bridge is an on-demand script, loaded only inside an app that announces
  Watch2WhoopFeatures.nativeUi (browsers, Android and older apps never fetch it), so flags off = the page is unchanged;
- the bridge tells the app the flags, where the Jarvis key / the 今天 content are and whether anything covers them, and the data
  the 今天 tab drew (a fixed subset); it hides the page's own key only once the app says it draws it;
- the app's actions go back to the page's own code (jarvis-entry.js BodyJarvis.native, the cards' data-card buttons).
JS behaviour runs in node with a small fake DOM (skipped without node); the rest reads the files."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from body_engine.rollout.registry import FLAGS

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
JV = (STATIC / "jarvis-entry.js").read_text(encoding="utf-8")
BRIDGE = STATIC / "native-bridge.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def test_flags_declared():
    assert "native_jarvis" in FLAGS and "native_today" in FLAGS
    assert "jarvis_orbit" in FLAGS["native_jarvis"]   # the native key replaces the orbit key only


def test_loaded_on_demand_only_in_an_app_that_has_native_views():
    assert re.search(r'const nativeHello = \(\) => \{ if \(IN_APP && S\.me && window\.Watch2WhoopFeatures\?\.nativeUi\) lazyScript\("native-bridge\.js"\); \};', APP)
    assert 'window.addEventListener("w2w-features", nativeHello);' in APP
    assert 'tellApp("logged-in"); nativeHello();' in APP
    assert "native-bridge.js" not in re.findall(r'<script src="([\w.-]+)"', (STATIC / "index.html").read_text(encoding="utf-8"))


def test_today_hook_and_plugin_context():
    assert 'window.BodyHooks.emit("today", lastToday = { day, past, self: isSelf(), scores: d.scores, daily: tday?.daily || null, reminders: d.reminders, coach, monitor });' in APP
    assert "page: () => lastPage, todayDrawn: () => lastToday" in APP


def test_jarvis_entry_native_hooks():
    # the app's camera only when the app says it has one; otherwise the page's own file input, as before
    assert 'if (window.Watch2WhoopFeatures?.nativeJarvis?.camera) tell("jv-camera");' in JV
    assert "native: () => ({ jv, V, C, isJarvis, offline, runKey, finishVoice, cancelVoice, startVoice, holdMove, holdUp, voiceOn, keys: KEYS.map((k) => k.id) })," in JV
    # the hold gesture of either key goes through the same two functions
    assert "holdMove(e.clientX, e.clientY)" in JV and "e.pointerId === V.pid) holdUp()" in JV
    # a tap from the app never opens the page's own (invisible) menu: Jarvis opens the app's menu, the rest goes where the key went
    src = BRIDGE.read_text(encoding="utf-8")
    tap = src[src.index("  function jvTap("):src.index("  function jvRun(")]
    assert "openMenu" not in tap and "openAssistant" in tap and "finishVoice()" in tap


HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const out = [];
function run(scenario) {
  const msgs = [], timers = [], listeners = {}, hooks = {}, clicks = [];
  const el = (name, rect, extra = {}) => ({ name, hidden: false, kids: [], rect,
    getBoundingClientRect() { return this.rect; }, contains(o) { return o === this || this.kids.includes(o); }, click() { clicks.push(name); }, ...extra });
  const ask = el("ask", { left: 167, top: 760, width: 56, height: 56 });
  const view = el("view", { left: 0, top: 0, width: 390, height: 900 });
  const inside = el("inside", null); view.kids.push(inside);
  const sheet = el("sheet", null);
  const top = el("top", { left: 0, top: 0, width: 390, height: 110, bottom: 110 });
  const dock = el("dock", { left: 0, top: 740, width: 390, height: 104 });
  const recovery = el("recovery-card", null);
  const app = el("app", null);
  const sel = { "#ask": ask, "#view": view, ".top": top, ".dock": dock, "#app": app, '#view [data-card="recovery"]': recovery };
  const cls = new Set();
  let cover = null;
  const document = {
    visibilityState: "visible",
    head: { append() {} },
    body: { classList: { toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)), contains: (c) => cls.has(c) } },
    documentElement: {},
    createElement: () => ({}),
    querySelector: (s) => sel[s] || null,
    elementFromPoint: (x, y) => cover || (Math.abs(y - 788) < 1 ? ask : inside),
    addEventListener() {},
  };
  const features = {};
  const jarvis = { calls: [] }, V = { state: "off", pid: null };
  const window = {
    Watch2Whoop: { postMessage: (m) => msgs.push(m) },
    get Watch2WhoopFeatures() { return features; },
    BodyFlags: { on: (k) => scenario.flags.includes(k) },
    I18N: { t: (k) => k },
    BodyHooks: { on: (n, f) => (hooks[n] = hooks[n] || []).push(f), emit: (n, d) => (hooks[n] || []).forEach((f) => f(d)) },
    BodyJarvis: { native: () => ({ jv: ask, V, keys: ["chat", "voice", "photo", "quick"], voiceOn: () => !scenario.noVoice,
      startVoice: (o) => { jarvis.calls.push(`start:${o.mode}:${o.pointerId}:${o.x},${o.y}`); V.state = "hold"; V.pid = o.pointerId; },
      holdMove: (x, y) => jarvis.calls.push(`move:${x},${y}`), holdUp: () => { jarvis.calls.push("up"); V.state = "locked"; },
      cancelVoice: (m, why) => { jarvis.calls.push(`cancel:${why}`); V.state = "off"; },
      C: { openAssistant: () => jarvis.calls.push("assistant"), needsNet: () => jarvis.calls.push("needsNet") },
      isJarvis: () => !scenario.ownAi, offline: () => false, runKey: (id) => jarvis.calls.push(id), finishVoice: () => jarvis.calls.push("finish") }) },
    BodyAI: { openHalf: () => true, sendPhoto: (f) => jarvis.calls.push(`photo:${f.name}:${f.type}:${f.size}`), ensureJarvis: () => true,
      phase: () => scenario.phase || null },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    addEventListener: (n, f) => (listeners[n] = listeners[n] || []).push(f),
    innerWidth: 390, scrollY: 0,
  };
  const sandbox = { window, document, console, JSON, Math, Object, Array, String, Uint8Array, atob: (s) => Buffer.from(s, "base64").toString("binary"),
    File: class { constructor(parts, name, o) { this.name = name; this.type = o.type; this.size = parts[0].length; } },
    getComputedStyle: () => ({ getPropertyValue: (k) => (k === "--good" ? " #4e9a6b" : "#000") }),
    MutationObserver: class { observe() {} },
    setTimeout: (f) => { timers.push(f); return timers.length; }, clearTimeout() {}, requestAnimationFrame: (f) => timers.push(f) };
  sandbox.window.window = sandbox.window;
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  const flush = () => { for (let i = 0; i < 50 && timers.length; i++) timers.splice(0).forEach((f) => f()); };
  const ctx = { reload: () => clicks.push("reload"), page: () => "today", toast() {},
    todayDrawn: () => ({ day: "2026-10-27", past: false, self: true, reminders: [{ status: "new" }, { status: "dismissed" }],
      daily: { steps: 8000, active_kcal: 500, exercise_min: null, secret: 1 },
      coach: { target_low: 8, target_high: 12, current_strain: 6.2, status: "below", note: "x" },
      monitor: { metrics: [{ key: "rhr", label: "静息心率", value: 52, unit: "bpm", status: "normal", low: 48, missing_reason: null }] },
      scores: { today: { recovery: 66, hrv_night_ms: 52, sleep_score: 81, private_note: "x" }, yesterday: { strain: 12.3, date: "2026-10-26" },
        recovery_zone: "yellow", load_status: { label: "适中", text: "long" } } }) };
  window.BodyPlugins[0].init(ctx, { me: { id: 7 } });
  flush();
  const r = { name: scenario.name, first: [...msgs] };
  if (scenario.features) {
    Object.assign(features, scenario.features);
    (listeners["w2w-features"] || []).forEach((f) => f());
    flush();
    r.after = msgs.slice(r.first.length);
    r.hidden = cls.has("jv-native");
    if (scenario.cover) { cover = sheet; (hooks.sheet || []).forEach((f) => f({ open: true })); flush(); r.covered = msgs.slice(r.first.length + r.after.length); cover = null; }
    window.BodyNative.action({ kind: "jarvis", id: "tap" });
    window.BodyNative.action({ kind: "jarvis", id: "photo" });
    window.BodyNative.action({ kind: "jarvis", id: "hold" });
    window.BodyNative.action({ kind: "jarvis", id: "nope" });
    if (scenario.hold) {   // hold to talk on the app's key: start, slide, lift; the state goes back to the app
      window.BodyNative.action({ kind: "jarvis", id: "hold-move", x: 1, y: 1 });   // nothing is holding yet: ignored
      window.BodyNative.action({ kind: "jarvis", id: "hold-start", x: 195, y: 788 });
      flush(); r.holding = JSON.parse(msgs.filter((m) => m.startsWith("jvn:")).pop().slice(4));
      window.BodyNative.action({ kind: "jarvis", id: "hold-move", x: 260, y: 650 });
      window.BodyNative.action({ kind: "jarvis", id: "hold-end" });
      window.BodyNative.action({ kind: "jarvis", id: "hold-cancel" });   // not holding any more (locked): ignored
      flush(); r.locked = JSON.parse(msgs.filter((m) => m.startsWith("jvn:")).pop().slice(4));
    }
    window.BodyNative.action({ kind: "today", card: "recovery" });
    window.BodyNative.action({ kind: "today", card: "bad\"]" });
    window.BodyNative.action({ kind: "today", card: "refresh" });
    window.BodyNative.photo(Buffer.from("abc").toString("base64"), "image/png");
    flush();
    const n = msgs.length;
    window.BodyNative.action({ kind: "today", card: "web" });
    flush();
    r.web = msgs.slice(n);
    r.jarvis = jarvis.calls; r.clicks = clicks;
  }
  out.push(r);
}
run({ name: "off", flags: [] });
run({ name: "on", flags: ["native_jarvis", "jarvis_orbit", "native_today"], features: { nativeUi: { v: 1 }, nativeJarvis: { v: 1, camera: true }, nativeToday: { v: 1 } }, cover: true });
run({ name: "hold", hold: true, flags: ["native_jarvis", "jarvis_orbit"], features: { nativeUi: { v: 1 }, nativeJarvis: { v: 1 } } });
run({ name: "hold-novoice", hold: true, noVoice: true, flags: ["native_jarvis", "jarvis_orbit"], features: { nativeUi: { v: 1 }, nativeJarvis: { v: 1 } } });
run({ name: "answering", phase: "answering", flags: ["native_jarvis", "jarvis_orbit"], features: { nativeUi: { v: 1 }, nativeJarvis: { v: 1 } } });
run({ name: "own-ai", ownAi: true, flags: ["native_jarvis", "jarvis_orbit"], features: { nativeUi: { v: 1 }, nativeJarvis: { v: 1 } } });
run({ name: "today-only", flags: ["native_jarvis", "native_today"], features: { nativeUi: { v: 1 }, nativeToday: { v: 1 } } });
console.log(JSON.stringify(out));
"""


def _run(tmp_path):
    h = tmp_path / "harness.js"
    h.write_text(HARNESS, encoding="utf-8")
    res = subprocess.run([NODE, str(h), str(BRIDGE)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return {r["name"]: r for r in json.loads(res.stdout)}


def _msg(msgs, prefix):
    hits = [json.loads(m[len(prefix):]) for m in msgs if m.startswith(prefix)]
    return hits[-1] if hits else None


@needs_node
def test_bridge_tells_the_flags_and_nothing_else_while_the_app_draws_nothing(tmp_path):
    r = _run(tmp_path)
    assert r["off"]["first"] == ['native:{"v":1,"jarvis":false,"today":false}']
    assert _msg(r["on"]["first"], "native:") == {"v": 1, "jarvis": True, "today": True}
    assert _msg(r["today-only"]["first"], "native:") == {"v": 1, "jarvis": False, "today": True}   # needs jarvis_orbit
    assert not any(m.startswith(("jvn:", "tdn:", "tdn-data:")) for m in r["on"]["first"])


@needs_node
def test_bridge_reports_key_and_today_area_and_hides_the_page_key(tmp_path):
    r = _run(tmp_path)["on"]
    assert r["hidden"] is True
    jv = _msg(r["after"], "jvn:")
    assert jv["show"] is True and jv["rect"] == [167, 760, 56, 56] and jv["menu"] is True and jv["voice"] == "off"
    assert jv["theme"]["good"] == "#4e9a6b" and jv["labels"]["photo"] == "jarvis.photo"
    td = _msg(r["after"], "tdn:")
    assert td["show"] is True and td["rect"] == [0, 110, 390, 630]
    data = _msg(r["after"], "tdn-data:")
    assert data["uid"] == 7 and data["day"] == "2026-10-27" and data["reminders"] == 1
    assert data["scores"]["today"] == {"recovery": 66, "hrv_night_ms": 52, "sleep_score": 81}   # a fixed subset only
    assert data["daily"] == {"steps": 8000, "active_kcal": 500}
    assert data["coach"] == {"target_low": 8, "target_high": 12, "current_strain": 6.2, "status": "below"}
    assert data["monitor"] == [{"key": "rhr", "label": "静息心率", "value": 52, "unit": "bpm", "status": "normal"}]
    assert data["scores"]["load_status"] == {"label": "适中"} and data["scores"]["yesterday"] == {"strain": 12.3, "date": "2026-10-26"}
    # a sheet over the page: both native views step aside
    assert _msg(r["covered"], "jvn:")["show"] is False and _msg(r["covered"], "tdn:")["show"] is False
    # 「完整今天页」: the web page until the user leaves 今天
    assert _msg(r["web"], "tdn:")["show"] is False


@needs_node
def test_actions_go_to_the_pages_own_code(tmp_path):
    r = _run(tmp_path)["on"]
    # Jarvis: a tap opened the app's own menu (nothing here); a long press = hands-free talk; unknown keys are ignored
    assert r["jarvis"] == ["photo", "voice", "photo:photo.png:image/png:3"]
    assert r["clicks"] == ["recovery-card", "reload"]   # an odd card key is ignored
    # own AI: the key does what it always did, the sub-keys too
    own = _run(tmp_path)["own-ai"]
    assert own["jarvis"][:4] == ["assistant", "assistant", "assistant", "assistant"]
    assert _msg(own["after"], "jvn:")["menu"] is False


@needs_node
def test_page_key_stays_when_the_app_does_not_draw_it(tmp_path):
    r = _run(tmp_path)["today-only"]
    assert r["hidden"] is False and _msg(r["after"], "jvn:") is None and _msg(r["after"], "tdn:")["show"] is True


@needs_node
def test_hold_to_talk_on_the_apps_key_runs_the_pages_voice_and_reports_its_state(tmp_path):
    r = _run(tmp_path)
    h = r["hold"]
    i = h["jarvis"].index("start:hold:app:195,788")
    assert h["jarvis"][i:i + 3] == ["start:hold:app:195,788", "move:260,650", "up"]
    assert "move:1,1" not in h["jarvis"] and not any(c.startswith("cancel") for c in h["jarvis"])
    assert h["holding"]["phase"] == "recording" and h["holding"]["voice"] == "hold" and h["holding"]["menu"] is False
    assert h["locked"]["voice"] == "locked" and h["locked"]["phase"] == "recording"
    # no way to talk (no recogniser): holding is a tap, as on the page's key; Jarvis then has nothing more to do
    assert not any(c.startswith("start:") for c in r["hold-novoice"]["jarvis"])
    assert _msg(r["hold-novoice"]["after"], "jvn:")["talk"] is False and _msg(h["after"], "jvn:")["talk"] is True
    assert _msg(r["answering"]["after"], "jvn:")["phase"] == "answering" and _msg(h["after"], "jvn:")["phase"] == "idle"
