"""After the consent form is confirmed the Today page must show the 「开启数据同步」 card at once (1.0.8 pre-release check).

Bug: a new user confirmed the consent form, but the page kept the sync status it had read while the form was open
(state "consent", setup_needed false): no card, no sheet, until something forced a refresh. The server was right
(GET /sync/overview is computed per request, nothing cached); the page was stale.

No database: the page scripts run in node with a fake window (skipped without node).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"

HARNESS = r"""
const fs = require('fs');
const fake = () => new Proxy(function () {}, { get: (t, k) => (k === 'style' || k === 'dataset' ? {} : k === Symbol.toPrimitive ? () => '' : fake()), apply: () => fake() });
globalThis.document = { querySelector: () => null, querySelectorAll: () => [], getElementById: () => null, createElement: () => fake(),
  head: fake(), body: fake(), addEventListener() {}, visibilityState: 'visible' };
globalThis.location = {};
const pending = [];                       // one entry per GET /sync/overview: its resolve function
const calls = [];
globalThis.window = {
  I18N: { t: (k, p) => k.replace(/\{(\w+)\}/g, (_, n) => (p || {})[n]) },
  BodyPlugins: [],
};
const C = { esc: (s) => String(s), isSelf: () => true, reload() {}, toast() {},
  api: (path) => new Promise((resolve) => { calls.push(path); pending.push(resolve); }) };
const src = fs.readFileSync(process.argv[1], 'utf8');
new Function('window', 'document', 'location', 'module', src)(window, document, location, undefined);
const plugin = window.BodyPlugins[0];
const tone = () => { const e = { classList: { add() {} }, setAttribute() {}, dataset: {} }; window.BodySyncStatus.decorate(e); return e.dataset.tone; };
const tick = () => new Promise((r) => setTimeout(r, 0));
const CONSENT = { state: 'consent', setup_needed: false, issue: { code: 'consent', actions: ['consent'], label: 'c' } };
const SETUP = { state: 'off', setup_needed: true, issue: { code: 'not_connected', actions: ['connect'], label: 'n' } };
(async () => { __BODY__ })().then((r) => console.log(JSON.stringify(r)));
"""


def run(body: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", HARNESS.replace("__BODY__", body), str(STATIC / "sync-status.js")],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_reuse_overview_rule():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = (f"globalThis.window = undefined; const m = {{ exports: {{}} }}; "
             f"new Function('module', require('fs').readFileSync({json.dumps(str(STATIC / 'sync-status.js'))}, 'utf8'))(m); const P = m.exports;"
             "console.log(JSON.stringify([P.reuseOverview({state:'ok'}, 1000, 1500, false), P.reuseOverview({state:'ok'}, 1000, 1500, true),"
             "P.reuseOverview({state:'ok'}, 1000, 200000, false), P.reuseOverview(null, 1000, 1500, false),"
             "P.reuseOverview({state:'consent'}, 1000, 1500, false)]))")
    out = json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)
    # a recent answer is reused; a forced request, an old answer, no answer and a "consent" answer are not
    assert out == [True, False, False, False, False]


def test_card_comes_right_after_the_consent():
    r = run("""
      plugin.init(C, { me: { id: 7 } });                 // the page starts while the consent form is open
      pending.shift()(CONSENT); await tick();
      const before = tone();
      window.BodySyncStatus.refresh();                   // profile.js right after "同意并继续"
      pending.shift()(SETUP); await tick();
      return { before, after: tone(), requests: calls.length };
    """)
    assert r == {"before": "mid", "after": "plain", "requests": 2}


def test_a_tab_switch_does_not_reuse_the_consent_answer():
    r = run("""
      plugin.init(C, { me: { id: 7 } });
      pending.shift()(CONSENT); await tick();
      plugin.onTab('today');                             // inside the one-minute throttle, but the old answer says "consent"
      pending.shift()(SETUP); await tick();
      return { after: tone(), requests: calls.length };
    """)
    assert r == {"after": "plain", "requests": 2}


def test_a_slow_old_answer_does_not_overwrite_the_new_one():
    r = run("""
      plugin.init(C, { me: { id: 7 } });                 // request 1, still running
      const slow = pending.shift();
      window.BodySyncStatus.refresh();                   // request 2, after the consent
      pending.shift()(SETUP); await tick();
      slow(CONSENT); await tick();                       // request 1 arrives last, with the stale "consent"
      return { after: tone() };
    """)
    assert r == {"after": "plain"}


def test_profile_refreshes_the_sync_status_after_the_consent():
    js = (STATIC / "profile.js").read_text(encoding="utf-8")
    submit = js[js.index("async function submit("):js.index("// One-tap consent")]
    assert submit.index("el.remove()") < submit.index("window.BodySyncStatus?.refresh?.()")
    # both consent sheets (full text and one tap) go through submit()
    assert js.count("submit(el, {") == 2
