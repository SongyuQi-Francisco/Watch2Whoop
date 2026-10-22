"""#183: the server restarts for a few seconds on a deploy; the Today page must not show a dead "request failed".

- app.js fetchRetrying (run in node with stubs, skipped without node): a read (GET) waits and tries again at most twice on
  502 / 503 / 504 or a lost connection; any other request is sent once (a write must not run twice); the service worker's
  503 "no copy" (X-Offline) and a device that knows it is offline are not a restart.
- the page wiring, checked on the source: a request that is still failing says the server is updating (and a retry button),
  Jarvis resends a new message by itself and offers a retry button, a rejected tool value is shown apart from a network failure.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
AI = (STATIC / "ai.js").read_text(encoding="utf-8")
EN = (STATIC / "i18n-en.js").read_text(encoding="utf-8")


def run_retry(body: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    start = APP.index("const UPDATING_WAITS")
    src = APP[start:APP.index("// queue: {kind", start)]
    js = """
      const tr = (s) => s, toasts = [];
      const toast = (...a) => toasts.push(a[0]);
      let off = null; const OFF = () => off;
      globalThis.navigator = { onLine: true };
      const setTimeout = (f) => f();               // no real waiting
      const res = (status, headers = {}) => ({ status, ok: status < 400, headers: { get: (k) => headers[k] ?? null } });
      let script = [], calls = 0;
      const fetch = async () => { const x = script[calls++]; if (x instanceof Error) throw x; return x; };
    """ + src + "\n(async () => {\n" + body + "\n})().then((v) => console.log(JSON.stringify(v)), (e) => { console.error(e); process.exit(1); });"
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_get_retries_twice_then_gives_the_answer():
    got = run_retry("""
      script = [res(502), res(504), res(200)];
      const a = await fetchRetrying("/x", {}, true);
      const first = { status: a.status, calls, toasts: toasts.length };
      calls = 0; script = [res(502), res(502), res(502), res(200)];
      const b = await fetchRetrying("/x", {}, true);       // still down after two retries: the failing answer is returned
      return { first, second: { status: b.status, calls } };""")
    assert got == {"first": {"status": 200, "calls": 3, "toasts": 2}, "second": {"status": 502, "calls": 3}}


def test_get_retries_a_lost_connection_but_not_when_offline():
    got = run_retry("""
      script = [new TypeError("Load failed"), res(200)];
      const a = await fetchRetrying("/x", {}, true);
      const r = { retried: a.status, calls };
      navigator.onLine = false; calls = 0; script = [new TypeError("offline"), res(200)];
      try { await fetchRetrying("/x", {}, true); r.offline = "returned"; } catch (e) { r.offline = e.message; r.offlineCalls = calls; }
      navigator.onLine = true; off = {}; calls = 0; script = [new TypeError("x"), res(200)];     // offline_mode: its own copies
      try { await fetchRetrying("/x", {}, true); } catch (e) { r.cached = calls; }
      return r;""")
    assert got == {"retried": 200, "calls": 2, "offline": "offline", "offlineCalls": 1, "cached": 1}


def test_writes_and_service_worker_503_are_sent_once():
    got = run_retry("""
      script = [res(502), res(200)];
      const w = await fetchRetrying("/x", { method: "POST" }, false);
      const r = { write: w.status, writeCalls: calls };
      calls = 0; script = [res(503, { "X-Offline": "1" }), res(200)];
      const s = await fetchRetrying("/x", {}, true);
      r.sw = s.status; r.swCalls = calls;
      calls = 0; script = [res(400), res(200)];
      r.bad = (await fetchRetrying("/x", {}, true)).status; r.badCalls = calls;
      return r;""")
    assert got == {"write": 502, "writeCalls": 1, "sw": 503, "swCalls": 1, "bad": 400, "badCalls": 1}


def test_page_wiring():
    assert "fetchRetrying(path, { method, headers" in APP and 'method === "GET")' in APP     # only reads retry
    assert "e.updating = true" in APP and "data-retry" in APP.split("const errorHtml")[1][:400]
    assert "RESTART_WAITS" in AI and "retryButton(el, path, body)" in AI
    assert "if (resume || ctrl.signal.aborted" in AI                  # a picked-up turn has its own retry loop
    assert 'd.status === "error" && d.message' in AI                  # a rejected value is told apart from "连不上"
    for zh in ("服务器正在更新，几秒后自动重试…", "服务器正在更新，请过一会儿再试", "{name} 校验失败：{msg}"):
        assert re.search(rf'"{re.escape(zh)}":', EN), zh
