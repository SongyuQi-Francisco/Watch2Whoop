"""Hold-to-talk path on the Today page (#171): where 🎤 goes in a browser, in the Watch2Whoop app with and without the
app's microphone flag (window.Watch2WhoopFeatures.mic, app 1.0.7+), and when the app has the microphone declined.
Runs ai-media.js in node against fake windows (skipped only where node is missing; CI has it)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"
MEDIA = STATIC / "ai-media.js"

PROBE = """
globalThis.window = {};
require(%s);
const M = window.BodyMedia;
const posted = [];
const browser = () => ({ navigator: { mediaDevices: { getUserMedia() {} } }, AudioContext: function () {} });
const app = (features) => Object.assign(browser(), { Watch2Whoop: { postMessage: (m) => posted.push(m) } },
  features === undefined ? {} : { Watch2WhoopFeatures: features });
const denied = Object.assign(new Error("x"), { name: "NotAllowedError" });
const busy = Object.assign(new Error("x"), { name: "NotReadableError" });
const out = {
  browser: M.voicePath(true, browser()),
  browserAsrOff: M.voicePath(false, browser()),
  oldBrowser: M.voicePath(true, { navigator: {} }),
  noAudio: M.voicePath(true, { navigator: { mediaDevices: { getUserMedia() {} } } }),
  oldApp: M.voicePath(true, app()),
  appOtherFlags: M.voicePath(true, app({ appleSignIn: true })),
  appMic: M.voicePath(true, app({ mic: true })),
  appMicAsrOff: M.voicePath(false, app({ mic: true })),
  appMicNoWebAudio: M.voicePath(true, { navigator: {}, Watch2Whoop: {}, Watch2WhoopFeatures: { mic: true } }),
  blockedApp: M.micBlockedInApp(denied, app({ mic: true })),
  blockedBusy: M.micBlockedInApp(busy, app({ mic: true })),
  blockedBrowser: M.micBlockedInApp(denied, browser()),
  blockedOldApp: M.micBlockedInApp(denied, app()),
  nativeNone: M.nativeVoice(browser()),
  nativeOldApp: M.nativeVoice(app({ mic: true, syncCenter: true })),
  nativeApp: M.nativeVoice(app({ mic: true, voice: { langs: ["zh", "en"], onDevice: true } })),
  nativeEmpty: M.nativeVoice(app({ voice: { langs: [] } })),
  nativeNoBridge: M.nativeVoice(Object.assign(browser(), { Watch2WhoopFeatures: { voice: { langs: ["en"] } } })),
};
M.openMicSettings(app({ mic: true }));
M.openMicSettings(browser());
out.posted = posted;
console.log(JSON.stringify(out));
"""


def run_probe() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", PROBE % json.dumps(str(MEDIA))], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_browser_records_when_recognition_is_on():
    r = run_probe()
    assert r["browser"] == "record"
    assert r["browserAsrOff"] == "off"
    assert r["oldBrowser"] == "dictation" and r["noAudio"] == "dictation"


def test_app_without_the_mic_flag_points_to_dictation():
    """1.0.6 and older never let the WebView use the microphone: the page must not even try."""
    r = run_probe()
    assert r["oldApp"] == "dictation"
    assert r["appOtherFlags"] == "dictation"


def test_app_with_the_mic_flag_records():
    r = run_probe()
    assert r["appMic"] == "record"
    assert r["appMicAsrOff"] == "off"
    assert r["appMicNoWebAudio"] == "dictation"


def test_declined_microphone_offers_the_apps_settings_page():
    r = run_probe()
    assert r["blockedApp"] is True
    assert r["blockedBusy"] is False and r["blockedBrowser"] is False and r["blockedOldApp"] is False
    assert r["posted"] == ["mic-settings"]   # the app opens its iOS Settings page (apps/watch2whoop/lib/src/web_mic.dart)


def test_chat_uses_the_shared_decision():
    js = (STATIC / "ai.js").read_text(encoding="utf-8")
    assert 'window.BodyMedia?.voicePath(asrOn()) === "record"' in js
    assert "window.BodyMedia.micBlockedInApp(err)" in js and "window.BodyMedia.openMicSettings()" in js


def test_on_device_recognition_only_from_an_app_that_says_so():
    """Jarvis' quick voice entry (flag jarvis_orbit) uses the app's on-device recogniser only when the app (1.0.8+) lists its
    languages in Watch2WhoopFeatures.voice; 1.0.7 and older, browsers and a page without the app's bridge record as before."""
    r = run_probe()
    assert r["nativeNone"] is None and r["nativeOldApp"] is None and r["nativeEmpty"] is None and r["nativeNoBridge"] is None
    assert r["nativeApp"] == {"langs": ["zh", "en"], "onDevice": True}
