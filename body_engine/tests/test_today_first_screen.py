"""Today page audit fixes (docs/audits 2026-10-26, W1 / W3 / W4 / W5 and the first-screen diet).

- first screen: what the page loads at start (file count and size budget), the on-demand scripts (type w2w/lazy), the English
  table only for an English page (lang-gate.js decides like i18n.js), the offline copy keeps everything inline;
- W1: links that open a new tab are opened from code inside the app's WebView; W3: year in long chart ranges;
  W4: a JSON 502 is not a restarting server; W5: the viewport allows zooming and no field can trigger iOS' focus zoom.
JS behaviour runs in node with stubs (skipped without node); the rest reads the files / the served page."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def served_page() -> str:
    from body_engine import api as root_api
    r = TestClient(root_api.app, base_url="https://testserver").get("/app/")
    assert r.status_code == 200
    return r.text


# ---------------------------------------------------------------- first screen
def test_first_screen_budget():
    """What the page fetches at start (Chinese page): the count and size of its scripts / styles. Before the audit: 40 files,
    1,242,015 bytes (gzip about 414 KB). Raise the budget only on purpose; new big things load on demand (lazyScript)."""
    html = served_page()
    names = re.findall(r'<(?:script (?:data-w2w="gate" )?src|link rel="stylesheet" href)="([\w.-]+\.(?:js|css))\?v=[0-9a-f]{12}"', html)
    assert len(names) == len(set(names))
    total = sum((STATIC / n).stat().st_size for n in names)
    assert len(names) <= 35, len(names)
    assert total <= 990_000, total   # 980_000 + 10_000 for the jarvis_dial code that sits beside the old ring menu until the flag is on for everyone (#294)
    for heavy in ("i18n-en.js", "muscle-body-data.js", "intro.js", "grocery.js", "diet-profile.js", "water.js"):
        assert heavy not in names, f"{heavy} is loaded on demand, not at start"


def test_lazy_scripts_are_declared_and_fingerprinted():
    html = served_page()
    lazy = re.findall(r'<script type="w2w/lazy"(?: data-lang="(\w+)")? src="([\w.-]+\.js)\?v=([0-9a-f]{12})"></script>', html)
    assert [(lang, name) for lang, name, _ in lazy] == [("en", "i18n-en.js"), ("", "grocery.js"), ("", "diet-profile.js"), ("", "water.js"),
                                                        ("", "muscle-body-data.js"), ("", "intro.js"), ("", "sync-status.js")]
    assert re.search(r'<script data-w2w="gate" src="lang-gate\.js\?v=[0-9a-f]{12}"></script>', html)
    # the gate comes after i18n.js (the table adds itself to it) and before the scripts that call tr() when they run
    assert html.index('src="i18n.js?') < html.index('src="i18n-en.js?') < html.index('src="lang-gate.js?') < html.index('src="ios-web.js?')
    for _, name, v in lazy:   # a request for the current fingerprint is cached for a year, like the other files
        from body_engine import api as root_api
        r = TestClient(root_api.app, base_url="https://testserver").get(f"/app/{name}?v={v}")
        assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    # an older page that still has the tags keeps working: nothing else in the page names these files as plain scripts
    assert not re.search(r'<script src="(?:i18n-en|muscle-body-data|intro|grocery|diet-profile|water|sync-status)\.js', INDEX)


def test_offline_copy_has_the_lazy_scripts_inline_and_no_gate():
    from body_engine.web import api as web_api
    html = web_api.offline_shell_html()
    assert not re.search(r'<script (?:data-w2w="gate"|type="w2w/lazy")[^>]*src=', html)
    for needle in ("I18N.addEn({", "window.BodyMuscleBodies =", "window.BodyIntro = { open }"):
        assert needle in html, needle


GATE_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const [gate, i18n, hash, stored, device] = process.argv.slice(1);
function run(file, withDocument) {
  const store = stored === "-" ? {} : { "be.lang": stored };
  const written = [];
  const sandbox = {
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    history: { replaceState: () => {} },
    location: { hash, pathname: "/app/", search: "" },
    navigator: { language: device, languages: [device] },
    document: {
      documentElement: { dataset: {}, lang: "" }, title: "", querySelectorAll: () => [], write: (t) => written.push(t),
      querySelector: (q) => (/w2w\/lazy/.test(q) ? { getAttribute: () => "i18n-en.js?v=abc123abc123" } : null),
    },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(file, "utf8"), sandbox);
  return { written, lang: sandbox.I18N && sandbox.I18N.lang };
}
const g = run(gate), i = run(i18n);
console.log(JSON.stringify({ gate: g.written, lang: i.lang }));
"""


@needs_node
@pytest.mark.parametrize("hash_, stored, device", [
    ("", "-", "zh-CN"), ("", "-", "zh-Hant-TW"), ("", "-", "en-AU"), ("", "-", ""),
    ("", "zh", "en-AU"), ("", "en", "zh-CN"), ("", "fr", "zh-CN"), ("", "fr", "en-US"),
    ("#lang=en", "zh", "zh-CN"), ("#lang=zh", "en", "en-AU"), ("#login=ABCDEFGH2345&lang=en", "-", "zh-CN"), ("#lang=fr", "-", "zh-CN"),
])
def test_gate_loads_the_english_table_exactly_when_the_page_is_english(hash_, stored, device):
    """lang-gate.js and i18n.js decide the page language independently (the gate runs first, before the page is parsed further):
    the table is written into the page when, and only when, i18n.js ends up English."""
    out = json.loads(subprocess.run([NODE, "-e", GATE_HARNESS, str(STATIC / "lang-gate.js"), str(STATIC / "i18n.js"), hash_, stored, device],
                                    capture_output=True, text=True, check=True).stdout)
    if out["lang"] == "en":
        assert out["gate"] == ['<script src="i18n-en.js?v=abc123abc123" defer><\\/script>'.replace("<\\/", "</")], out
    else:
        assert out["gate"] == [], out


def test_english_switch_without_reload_asks_for_the_table():
    i18n = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert "ensureEn" in i18n and 'script[type="w2w/lazy"][data-lang="en"]' in i18n
    assert 'if (l === "en") await window.I18N.ensureEn();' in (STATIC / "account.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- start-up requests
def test_decorating_reads_wait_for_the_first_page():
    """The plugin files' start-up reads (profile, account, sync state, notices, history import) wait until the first page is drawn."""
    m = re.search(r"const DEFERRED_GETS = (/.*/);", APP)
    assert m
    rx = re.compile(m.group(1)[1:-1].replace("\\/", "/"))
    for path in ("/v1/users/me/profile", "/v1/account", "/v1/users/me/sync/connection", "/v1/users/me/sync/status",
                 "/v1/users/me/notices", "/v1/users/me/health-history"):
        assert rx.match(path), path
    for path in ("/v1/users/me/today", "/v1/web/me", "/v1/users/me/goals/sleep", "/v1/users/me/strain-coach?date=2026-10-26",
                 "/v1/account/ow-invitation", "/v1/users/me/profile-pic", "/v1/users/me/notices/3/seen"):
        assert not rx.match(path), path
    assert 'if (!pre && method === "GET" && DEFERRED_GETS.test(path)) await firstPaint;' in APP
    assert APP.count("paintDone()") >= 3 and "setTimeout(() => paintDone(), 4000)" in APP   # first page drawn, a pending account, never stuck


def test_on_demand_wiring():
    assert "window.BodyLazy = { script: lazyScript }" in APP
    assert 'if (!stale && me.me?.intro_seen === false) lazyScript("intro.js")' in APP
    food = APP.split("const RENDER_food = async () => {")[1][:900]   # the food page's plugin files load when the tab first opens
    assert 'lazyScript("diet-profile.js")' in food and 'lazyScript("water.js")' in food and '"shopping" ? lazyScript("grocery.js")' in food
    assert food.index("lazyScript(") < food.index("foodTop")
    assert "if (!window.BodyIntro) window.BodyIntro = introStub" in APP
    assert "if (mdata) await window.BodyMuscleLoad.ready();" in APP
    ml = (STATIC / "muscle-load.js").read_text(encoding="utf-8")
    assert 'BodyLazy.script("muscle-body-data.js")' in ml and "const drawings = ready();" in ml and "await drawings;" in ml
    assert "!!window.BodyMuscleBodies;\n" not in ml.split("const on = ")[1].split("\n")[0] + "\n"   # on() no longer waits for the drawings


LAZY_HARNESS = r"""
const src = require("fs").readFileSync(process.argv[1], "utf8");   // a path: the whole file does not fit in one argument on Linux
const m = /function lazyScript\(name\) \{[\s\S]*?\n  \}\n/.exec(src);
const head = /const LAZY = new Map\(\);[\s\S]*?function initLate\(\) \{[\s\S]*?\n  \}\n/.exec(src);
const appended = [];
const decls = [{ getAttribute: () => "muscle-body-data.js?v=abc" }, { getAttribute: () => "intro.js?v=def" }];
const document = {
  querySelectorAll: () => decls,
  createElement: () => ({ remove() {} }),
  head: { append: (el) => appended.push(el) },
};
const window = { BodyPlugins: [] };
const PLUGINS = () => window.BodyPlugins;
eval(head[0] + m[0] + "\n(async () => {" + `
  const out = {};
  out.none = await lazyScript("not-declared.js");                        // no declaration (the offline copy): nothing to fetch
  out.declared = appended.length;
  const p1 = lazyScript("intro.js"), p2 = lazyScript("intro.js");        // asked twice: one script
  out.one = appended.length === 1 && appended[0].src === "intro.js?v=def";
  window.BodyPlugins.push({ init: (ctx, me) => { out.init = [ctx, me]; } });
  lateInit.ctx = "ctx"; lateInit.me = "me";
  appended[0].onload();
  out.ok = [await p1, await p2];
  const p3 = lazyScript("muscle-body-data.js"); appended[1].onerror();
  out.failed = await p3;
  const p4 = lazyScript("muscle-body-data.js");                          // a failed load is tried again
  out.retried = appended.length === 3;
  console.log(JSON.stringify(out));
` + "})()");
"""


@needs_node
def test_lazy_script_loader():
    out = json.loads(subprocess.run([NODE, "-e", LAZY_HARNESS, str(STATIC / "app.js")], capture_output=True, text=True, check=True).stdout)
    assert out == {"none": True, "declared": 0, "one": True, "init": ["ctx", "me"], "ok": [True, True], "failed": False, "retried": True}


# ---------------------------------------------------------------- W1 links
LINK_HARNESS = r"""
const src = require("fs").readFileSync(process.argv[1], "utf8");   // a path: the whole file does not fit in one argument on Linux
const block = /\/\/ <in-app-links>[\s\S]*?\/\/ <\/in-app-links>/.exec(src)[0];
let handler = null, opened = [];
const document = { addEventListener: (ev, fn) => { if (ev === "click") handler = fn; } };
const window = { open: (...a) => opened.push(a) };
const location = { href: "https://body.example/app/" };
function run(IN_APP, ev) {
  opened = [];
  const IN = IN_APP;
  new Function("IN_APP", "document", "window", "location", "URL", block)(IN, document, window, location, URL);
  handler(ev);
  return { opened, prevented: ev.defaultPrevented };
}
const link = (href, target = "_blank") => ({ getAttribute: () => href });
const ev = (a, extra = {}) => ({ defaultPrevented: false, button: 0, target: { closest: (sel) => (a && /target=_blank/.test(sel) ? a : null) }, preventDefault() { this.defaultPrevented = true; }, ...extra });
const out = {};
out.app = run(true, ev(link("https://example.org/doc?a=1")));
out.browser = run(false, ev(link("https://example.org/doc")));
out.notALink = run(true, ev(null));
out.mailto = run(true, ev(link("mailto:a@example.org")));
out.javascript = run(true, ev(link("javascript:alert(1)")));
out.handled = run(true, ev(link("https://example.org/x"), { defaultPrevented: true }));
out.middle = run(true, ev(link("https://example.org/x"), { button: 1 }));
out.relative = run(true, ev(link("/app/handbook/")));
console.log(JSON.stringify(out));
"""


@needs_node
def test_new_tab_links_open_from_code_inside_the_app():
    """W1: Jarvis answers (md.js), the feedback link and the Jarvis 「open」 button are target=_blank anchors, which do nothing in the
    app's WebView; window.open reaches the app's navigation delegate (hands the address to the system)."""
    out = json.loads(subprocess.run([NODE, "-e", LINK_HARNESS, str(STATIC / "app.js")], capture_output=True, text=True, check=True).stdout)
    assert out["app"] == {"opened": [["https://example.org/doc?a=1", "_blank", "noopener"]], "prevented": True}
    assert out["relative"]["opened"] == [["https://body.example/app/handbook/", "_blank", "noopener"]]
    for case in ("browser", "notALink", "mailto", "javascript", "handled", "middle"):   # the browser keeps its own behaviour
        assert out[case] == {"opened": [], "prevented": out[case]["prevented"] and case == "handled"}, case
    md = (STATIC / "md.js").read_text(encoding="utf-8")
    assert 'target="_blank" rel="noopener noreferrer nofollow"' in md   # browsers keep opening a new tab


# ---------------------------------------------------------------- W3 chart range labels
YEAR_HARNESS = r"""
const src = require("fs").readFileSync(process.argv[1], "utf8");   // a path: the whole file does not fit in one argument on Linux
const day = (name) => new RegExp("  const " + name + " = [^\\n]*\\n").exec(src)[0];
const pieces = ["parseDay", "shortDay", "yearDay", "spansYear"].map(day).join("");
function make(lang, locale) {
  const I18N = { lang, locale: () => locale };
  return new Function("I18N", pieces + "return { shortDay, yearDay, spansYear };")(I18N);
}
const zh = make("zh", "zh-CN"), en = make("en", "en-AU");
console.log(JSON.stringify({
  zh: [zh.yearDay("2025-10-27"), zh.shortDay("2025-10-27")],
  en: [en.yearDay("2025-10-27"), en.shortDay("2025-10-27")],
  year: zh.spansYear("2025-10-27", "2026-10-26"), halfYear: zh.spansYear("2026-04-30", "2026-10-26"), month: zh.spansYear("2026-09-27", "2026-10-26"),
  across: zh.spansYear("2025-12-20", "2026-01-10"), sameYearShort: zh.spansYear("2026-01-01", "2026-03-01"),
}));
"""


@needs_node
def test_long_chart_ranges_show_the_year():
    """W3: a one-year chart read 「10/27 … 10/26」 (backwards). Ranges of half a year or more, or across New Year, label the ends with the year."""
    out = json.loads(subprocess.run([NODE, "-e", YEAR_HARNESS, str(STATIC / "app.js")], capture_output=True, text=True, check=True).stdout)
    assert out["zh"] == ["2025/10/27", "10/27"]
    assert out["en"][1] == "27/10" and out["en"][0].endswith("/25") and out["en"][0].startswith("27/10")
    assert out["year"] and out["halfYear"] and out["across"]
    assert not out["month"] and not out["sameYearShort"]
    assert "const axisDay = spansYear(dates[0], dates[n - 1]) ? yearDay : shortDay;" in APP
    assert "const day = (x) => (withYear ? yearDay(String(x)) : shortDay(String(x)));" in APP


# ---------------------------------------------------------------- W5 zoom
def test_viewport_allows_zoom_and_fields_do_not_trigger_focus_zoom():
    """W5: no maximum-scale / user-scalable=no (WCAG 1.4.4) and no script that cancels pinch gestures. iOS zooms into a field on focus
    only when its font is smaller than 16px: every input / select / textarea is at least 16px."""
    viewport = re.search(r'<meta name="viewport" content="([^"]+)"', INDEX).group(1)
    assert "maximum-scale" not in viewport and "user-scalable" not in viewport and "initial-scale=1" in viewport
    assert "gesturestart" not in APP and "noPinch" not in APP
    css = "".join((STATIC / n).read_text(encoding="utf-8") for n in ("app.css", "ai.css", "grocery.css", "diet-profile.css", "jarvis-entry.css"))
    assert re.search(r"input, select, textarea \{ font-size: max\(16px, 1em\); \}", css)
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"(?:^|[\s,>])(?:input|select|textarea|\.input|\.search)(?:[\s,.:\[{]|$)", sel.strip() + " ") and "font-size" in body:
            size = re.search(r"font-size:\s*([^;]+)", body).group(1).strip()
            px = re.fullmatch(r"(\d+(?:\.\d+)?)px", size)
            assert not px or float(px.group(1)) >= 16, f"{sel.strip()[:60]}: font-size {size}"
    for page in ("activate.html", "install.html", "oauth.html"):
        assert "user-scalable" not in (STATIC / page).read_text(encoding="utf-8")
