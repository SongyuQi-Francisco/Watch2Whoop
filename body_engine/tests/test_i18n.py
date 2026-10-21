"""i18n coverage for the Today page (web/static/i18n.js + i18n-en.js, issues #13 / #42): every key exists in zh and en with the
same placeholders, every key the page scripts / HTML use exists, no Chinese literal outside tr(...) in the page scripts, every
Chinese text in the HTML is marked data-i18n*, and the i18n files are loaded before the scripts that use them and cached by sw.js."""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from i18n_scan import CJK, scan, stray_chinese, tr_keys

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
SRC = (STATIC / "i18n.js").read_text(encoding="utf-8")
ENTRY = re.compile(r'^\s*"([\w.]+)":\s*"((?:[^"\\]|\\.)*)",\s*$', re.M)


def table(name: str) -> dict[str, str]:
    m = re.search(rf"const {name} = \{{\n(.*?)\n  \}};", SRC, re.S)
    assert m, f"table {name} not found in i18n.js"
    body = m.group(1)
    entries = ENTRY.findall(body)
    lines = [ln for ln in body.splitlines() if ln.strip()]
    assert len(entries) == len(lines), f"{name}: every line must be one simple \"key\": \"text\", entry"
    keys = [k for k, _ in entries]
    assert len(keys) == len(set(keys)), f"{name}: duplicate keys"
    return dict(entries)


ZH, EN = table("ZH"), table("EN")
SCRIPTS = ["app.js", "ai.js", "ai-media.js", "account.js", "grocery.js", "profile.js", "diet-profile.js", "muscle-map.js", "feedback.js", "identities.js", "intro.js", "sync.js", "activate.js", "ios-web.js", "oauth.js", "sw.js"]
PAGES = ["index.html", "activate.html", "install.html", "oauth.html"]


def en_text() -> dict[str, str]:
    src = (STATIC / "i18n-en.js").read_text(encoding="utf-8")
    m = re.search(r"I18N\.addEn\(\{\n(.*?)\n?\}\);", src, re.S)
    assert m, "I18N.addEn({ … }) not found in i18n-en.js"
    out: dict[str, str] = {}
    for ln in m.group(1).splitlines():
        if not ln.strip() or ln.strip().startswith("//"):
            continue
        k, v = json.loads("{" + ln.strip().rstrip(",") + "}").popitem()
        assert k not in out, f"i18n-en.js: duplicate entry {k!r}"
        out[k] = v
    return out


EN_TEXT = en_text()
PH = re.compile(r"\{(\w+)\}")


def test_every_key_in_both_languages():
    assert set(ZH) == set(EN), {"only zh": sorted(set(ZH) - set(EN)), "only en": sorted(set(EN) - set(ZH))}
    for k in ZH:
        assert ZH[k].strip() and EN[k].strip(), k
        assert set(PH.findall(ZH[k])) == set(PH.findall(EN[k])), f"{k}: placeholders differ"
        assert not re.search(r"[一-鿿]", EN[k]), f"{k}: Chinese left in the English table"


def test_english_text_table():
    assert EN_TEXT, "i18n-en.js is empty"
    for k, v in EN_TEXT.items():
        assert CJK.search(k), f"i18n-en.js: key without Chinese {k!r} (use a dotted key in i18n.js instead)"
        assert v.strip(), f"i18n-en.js: empty English for {k!r}"
        assert not re.search(r"[一-鿿]", v), f"i18n-en.js: Chinese left in the English for {k!r}"
        assert set(PH.findall(k)) == set(PH.findall(v)), f"i18n-en.js: placeholders differ for {k!r}"


USE_HTML = re.compile(r'data-i18n(?:-aria|-ph|-title)?="([^"]+)"')
DOTTED = re.compile(r"""["']((?:tab|hdr|common|login|err|banner|picker|who|me|jarvis|profile|activate)\.(?!js\b)\w+)["']""")


def known(key: str) -> bool:
    return key in ZH if not CJK.search(key) else key in EN_TEXT


@pytest.mark.parametrize("name", SCRIPTS + PAGES)
def test_used_keys_exist(name):
    text = (STATIC / name).read_text(encoding="utf-8")
    if name.endswith(".html"):
        used = set(USE_HTML.findall(text))
    else:
        lits = tr_keys(text)
        dyn = [f"{x.line}: {x.text}" for x in lits if x.kind == "tpl"]
        assert not dyn, f"{name}: tr() keys must be plain strings (use {{name}} placeholders, not ${{}}): {dyn}"
        used = {x.text.encode().decode("unicode_escape") if "\\u" in x.text else x.text for x in lits}
        used |= set(DOTTED.findall(text))   # keys picked by a ternary inside tr(...)
    missing = sorted(k for k in used if not known(k))
    assert not missing, f"{name}: keys missing from i18n.js / i18n-en.js: {missing}"


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_chinese_outside_tr(name):
    """Every Chinese text in the page scripts goes through tr(); data matched against server texts is marked i18n-ok."""
    stray = stray_chinese((STATIC / name).read_text(encoding="utf-8"))
    assert not stray, f"{name}: {len(stray)} Chinese literals outside tr(): " + "; ".join(f"{x.line}: {x.text[:40]!r}" for x in stray[:25])


class _HtmlCheck(HTMLParser):
    ATTRS = {"placeholder": "data-i18n-ph", "aria-label": "data-i18n-aria", "title": "data-i18n-title"}

    def __init__(self):
        super().__init__()
        self.stack: list[tuple[str, dict]] = []
        self.bad: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for attr, marker in self.ATTRS.items():
            if CJK.search(a.get(attr) or "") and marker not in a:
                self.bad.append(f"<{tag} {attr}={a[attr]!r}>")
        if tag == "meta" and CJK.search(a.get("content") or "") and "i18n-ok" not in (a.get("data-note") or ""):
            self.bad.append(f"<meta content={a['content']!r}>")
        if tag not in ("meta", "link", "input", "img", "br", "source"):
            self.stack.append((tag, a))

    def handle_endtag(self, tag):
        while self.stack and self.stack.pop()[0] != tag:
            pass

    def handle_data(self, data):
        if CJK.search(data) and self.stack and self.stack[-1][0] not in ("script", "style", "title"):
            tag, a = self.stack[-1]
            if "data-i18n" not in a:
                self.bad.append(f"<{tag}> {data.strip()[:30]!r}")


@pytest.mark.parametrize("name", PAGES)
def test_html_marked(name):
    p = _HtmlCheck()
    p.feed((STATIC / name).read_text(encoding="utf-8"))
    assert not p.bad, f"{name}: Chinese without data-i18n*: {p.bad}"


def test_loaded_first_and_cached():
    for page, user in (("index.html", "ai-media.js"), ("activate.html", "activate.js")):
        html = (STATIC / page).read_text(encoding="utf-8")
        assert html.index('src="i18n.js"') < html.index('src="i18n-en.js"') < html.index(f'src="{user}"'), page
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert '"i18n.js"' in sw and '"i18n-en.js"' in sw


NODE_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const [file, hash, device] = process.argv.slice(1);
const store = {};
let replaced = null;
Object.assign(globalThis, {
  window: globalThis,
  localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  history: { replaceState: (a, b, u) => { replaced = u; } },
  location: { hash, pathname: "/app/", search: "" },
  document: { documentElement: { dataset: {}, lang: "" }, querySelectorAll: () => [], title: "" },
});
Object.defineProperty(globalThis, "navigator", { value: { language: device, languages: [device] }, configurable: true });   // Node 21+ has a read-only one
vm.runInThisContext(fs.readFileSync(file, "utf8"));
console.log(JSON.stringify({ lang: window.I18N.lang, replaced, unsaved: window.I18N.unsaved() }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("hash_, device, lang, replaced, unsaved", [
    ("#login=ABCDEFGH2345&lang=en", "zh-CN", "en", "/app/#login=ABCDEFGH2345", "en"),   # app sign-in: the login code stays for app.js
    ("#lang=zh", "en-AU", "zh", "/app/", "zh"),
    ("", "en-AU", "en", None, None),                                                  # no choice from the app: device language
    ("#lang=fr", "zh-CN", "zh", None, None),                                          # unsupported: ignored
])
def test_app_sign_in_language_in_fragment(hash_, device, lang, replaced, unsaved):
    """The Watch2Whoop app's native 中文 / English switch reaches the page as #…&lang=xx: used at once, saved to the profile
    after login like the page's own login switch (unsaved), and removed from the address bar."""
    out = subprocess.run(["node", "-e", NODE_HARNESS, str(STATIC / "i18n.js"), hash_, device],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == {"lang": lang, "replaced": replaced, "unsaved": unsaved}


@pytest.mark.parametrize("name", [s for s in SCRIPTS if s != "sw.js"])
def test_upper_case_lookups_are_declared(name):
    """Every `${NAME[...]}` in a page script refers to a constant declared in that script (the training week strip once
    used WEEK_MON after it was renamed away, and 记录 → 训练 failed with "WEEK_MON is not defined")."""
    js = (STATIC / name).read_text()
    used = set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)\[", js))
    declared = set(re.findall(r"\b(?:const|let|var)\s+([A-Z][A-Z0-9_]+)\b", js))
    assert used <= declared, sorted(used - declared)
