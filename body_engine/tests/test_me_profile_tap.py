"""Me page: the avatar + name row opens the profile sheet (#213). Static checks on the page scripts (no browser): my own row
is a real <button> with an accessible name through tr(), it opens the same sheet as 我 → 个人资料, the row of someone
I am viewing read-only stays plain text, and the English exists."""
import re
from pathlib import Path

from body_engine import api

STATIC = Path(api.__file__).parent / "web" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
EN = (STATIC / "i18n-en.js").read_text(encoding="utf-8")
CSS = (STATIC / "app.css").read_text(encoding="utf-8")


def _me_head() -> str:
    m = re.search(r"\n  function meHead\(who\) \{\n(.*?)\n  \}\n", APP, re.S)
    assert m, "meHead() not found in app.js"
    return m.group(1)


def test_both_me_layouts_use_the_shared_head():
    # the old Me page and the me_settings one both draw the head through meHead(), nowhere else
    assert APP.count("${meHead(who)}") == 2
    assert '<div class="me"><span class="avatar">' not in APP.replace("`<div class=\"me\">${inner}</div>`", "")


def test_own_row_is_a_button_that_opens_the_profile():
    body = _me_head()
    assert '<button type="button" class="me me-open" id="me-open" aria-label="${esc(tr("打开个人资料"))}">' in body
    assert "who.self && window.BodyProfile" in body          # only on my own page (and only when profile.js is there)
    assert '`<div class="me">${inner}</div>`' in body        # viewing someone else: no link (#213 default)
    assert "<div" not in body.split("const inner =")[1].split(";")[0]   # nothing but phrasing content inside the button
    assert re.search(r'const prof = \$\("#me-open", view\);.*\n\s*if \(prof\) prof\.onclick = \(\) => window\.BodyProfile\?\.open\(\);', APP)


def test_profile_sheet_is_the_same_as_the_me_row():
    profile = (STATIC / "profile.js").read_text(encoding="utf-8")
    assert "window.BodyProfile = { open: () => openProfile(false)" in profile
    assert 'b.onclick = () => openProfile(false)' in profile   # 我 → 个人资料 opens the same function


def test_english_and_styles():
    assert '"打开个人资料": "Open profile",' in EN
    assert ".me-open {" in CSS and "cursor: pointer" in CSS.split(".me-open {", 1)[1].split("}", 1)[0]
