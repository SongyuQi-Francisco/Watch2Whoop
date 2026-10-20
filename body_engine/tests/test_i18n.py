"""i18n coverage for the Today page (web/static/i18n.js, issue #13): every key exists in zh and en with the same placeholders,
every key the page scripts / HTML use exists, and i18n.js is loaded before the scripts that use it and cached by the service worker."""
import re
from pathlib import Path

import pytest

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
PH = re.compile(r"\{(\w+)\}")


def test_every_key_in_both_languages():
    assert set(ZH) == set(EN), {"only zh": sorted(set(ZH) - set(EN)), "only en": sorted(set(EN) - set(ZH))}
    for k in ZH:
        assert ZH[k].strip() and EN[k].strip(), k
        assert set(PH.findall(ZH[k])) == set(PH.findall(EN[k])), f"{k}: placeholders differ"
        assert not re.search(r"[一-鿿]", EN[k]), f"{k}: Chinese left in the English table"


USE_JS = re.compile(r"""\b(?:tr|I18N\.t)\(\s*["']([\w.]+)["']""")
USE_HTML = re.compile(r'data-i18n(?:-aria|-ph|-title)?="([\w.]+)"')


@pytest.mark.parametrize("name", ["app.js", "profile.js", "activate.js", "index.html", "activate.html"])
def test_used_keys_exist(name):
    text = (STATIC / name).read_text(encoding="utf-8")
    used = set(USE_HTML.findall(text)) if name.endswith(".html") else set(USE_JS.findall(text))
    # keys picked by a ternary inside tr(...): every quoted dotted literal that looks like a key
    if name.endswith(".js"):
        used |= set(re.findall(r"""["']((?:tab|hdr|common|login|err|banner|picker|who|me|jarvis|profile|activate)\.\w+)["']""", text))
    assert used, f"{name} uses no i18n keys"
    missing = sorted(k for k in used if k not in ZH)
    assert not missing, f"{name}: keys missing from i18n.js: {missing}"


def test_loaded_first_and_cached():
    for page, user in (("index.html", "ai-media.js"), ("activate.html", "activate.js")):
        html = (STATIC / page).read_text(encoding="utf-8")
        assert html.index('src="i18n.js"') < html.index(f'src="{user}"'), page
    assert '"i18n.js"' in (STATIC / "sw.js").read_text(encoding="utf-8")
