"""The tester handbook in two languages (issue #50): docs/tester-handbook.md (Chinese) and docs/tester-handbook.en.md (English),
rendered by the deployer's handbook.py to static/handbook/index.html and static/handbook/en/index.html (served at /app/handbook/ and
/app/handbook/en/). The renderer is not part of the public repository, so these tests check what can drift without it: the two
Markdown files match section by section, every table-of-contents link lands on a heading, the rendered pages still carry the same
headings (a Markdown edit without a re-render shows here), they link to each other, and the Today page / activation page open the
edition in the page language."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
EDITIONS = {"zh": (DOCS / "tester-handbook.md", STATIC / "handbook" / "index.html"),
            "en": (DOCS / "tester-handbook.en.md", STATIC / "handbook" / "en" / "index.html")}
HEADING = re.compile(r"^(#{1,4})\s+(.*)$", re.M)


def slug(text: str, seen: dict) -> str:
    """The renderer's (and GitHub's) heading anchor: lower case, punctuation dropped, spaces to -, repeats get -1, -2."""
    text = re.sub(r"`([^`]*)`|\*\*(.+?)\*\*|\[([^\]]+)\]\([^)]*\)", lambda m: next(g for g in m.groups() if g is not None), text)
    base = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")
    n = seen.get(base, 0)
    seen[base] = n + 1
    return base if n == 0 else f"{base}-{n}"


def headings(md: str) -> list[tuple[int, str, str]]:
    seen: dict = {}
    return [(len(m.group(1)), m.group(2).strip(), slug(m.group(2), seen)) for m in HEADING.finditer(md) if len(m.group(1)) > 1]


def read(lang: str) -> str:
    return EDITIONS[lang][0].read_text(encoding="utf-8")


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_toc_links_land_on_headings(lang):
    md = read(lang)
    slugs = {s for _, _, s in headings(md)}
    links = re.findall(r"\]\(#([^)]+)\)", md)
    assert len(links) >= 8, "the table of contents is missing"
    assert [x for x in links if x not in slugs] == []


def test_editions_match_section_by_section():
    zh, en = read("zh"), read("en")
    assert [(lvl) for lvl, _, _ in headings(zh)] == [(lvl) for lvl, _, _ in headings(en)], "## / ### headings differ between the languages"
    # the table of contents lists as many sections in both
    toc = lambda md: re.findall(r"^- \[[^\]]+\]\(#([^)]+)\)$", md, re.M)   # noqa: E731
    assert len(toc(zh)) == len(toc(en))
    # same screenshots, same personal-edition markers, same example chat and table rows
    # (the English edition has its own English screenshots under images/tester-handbook/en/, same names)
    imgs = lambda md: [i.replace("/en/", "/") for i in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", md)]   # noqa: E731
    assert imgs(zh) == imgs(en)
    for marker in ("W2W:HELLO", "W2W:SETUP", "W2W:FOOTER"):
        assert f"<!-- {marker} -->" in zh and f"<!-- {marker} -->" in en
    assert len(re.findall(r"^> (?:你|AI)[：:]", zh, re.M)) == len(re.findall(r"^> (?:You|AI)[：:]", en, re.M))
    assert zh.count("\n| ") == en.count("\n| ")
    # the "in beta" tags line up (a feature is tagged in one language, so it is in the other)
    assert zh.count("内测中") == en.count("in beta"), (zh.count("内测中"), en.count("in beta"))


def test_required_sections():
    zh = [t for _, t, _ in headings(read("zh"))]
    en = [t for _, t, _ in headings(read("en"))]
    for needed in ("怎么用：一切都和 AI 说", "遇到问题", "同步中心", "朋友和圈子", "页面一览", "目录"):
        assert needed in zh, needed
    for needed in ("How to use: just talk to your AI", "When something goes wrong", "Sync center", "Friends and circles",
                   "Pages at a glance", "Contents"):
        assert needed in en, needed


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_no_retired_content_or_server_address(lang):
    md = read(lang)
    # the iPhone app is the only client: no computer-browser sign-in steps, no server address in the generic edition
    assert "example.com" not in md and "https://" not in md
    assert "电脑上看" not in md and "On a computer" not in md
    # not a medical product
    assert "医疗级" not in md and "medical-grade" not in md.lower()
    assert ("不是医疗器械" in md) if lang == "zh" else ("not a medical device" in md)


def test_language_switch_links_in_markdown():
    zh, en = read("zh"), read("en")
    assert "[English](tester-handbook.en.md)" in zh.split("\n", 4)[2]
    assert "[中文](tester-handbook.md)" in en.split("\n", 4)[2]


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_rendered_page_is_up_to_date(lang):
    """handbook.py was re-run after the last Markdown edit: the rendered ## headings (with their anchors) are the Markdown's."""
    md_path, html_path = EDITIONS[lang]
    html = html_path.read_text(encoding="utf-8")
    want = [(s, t) for lvl, t, s in headings(md_path.read_text(encoding="utf-8")) if lvl == 2]
    got = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', html)
    assert [s for s, _ in got] == [s for s, _ in want], "re-render: python3 deploy/ali/handbook.py docs/tester-handbook*.md …"
    assert f'<html lang="{"en" if lang == "en" else "zh-CN"}"' in html
    # the images the Markdown uses are all copied next to the page
    for name in re.findall(r"!\[[^\]]*\]\(images/tester-handbook/([^)]+)\)", md_path.read_text(encoding="utf-8")):
        assert (html_path.parent / "img" / name.removeprefix("en/")).is_file(), name
        assert (DOCS / "images" / "tester-handbook" / name).is_file(), name
    assert "viewport" in html and "width=device-width" in html


def test_english_edition_uses_its_own_screenshots():
    """#13: the English handbook shows the English interface. Its pictures are taken from the Quick Start demo (synthetic data) by
    tools/handbook_shots_en.mjs + tools/handbook_images.py --lang en, and the rendered page carries the same files. The one
    exception is the iOS permission sheet (a system screen), which says so under the picture."""
    md = read("en")
    names = re.findall(r"!\[[^\]]*\]\(images/tester-handbook/(en/[^)]+)\)", md)
    assert len(names) == len(re.findall(r"!\[[^\]]*\]\(", md)) >= 11, "every picture of the English edition is under images/tester-handbook/en/"
    assert "still show the Chinese interface" not in md
    assert "captured on an iPhone set to Chinese" in md
    shots = DOCS / "images" / "tester-handbook"
    same = {n for n in names if (shots / n).read_bytes() == (shots / n.removeprefix("en/")).read_bytes()}
    assert same == {"en/health-permission.jpg"}, f"these English screenshots are still the Chinese ones: {sorted(same)}"
    for n in names:   # the rendered page carries the very same files
        assert (STATIC / "handbook" / "en" / "img" / n.removeprefix("en/")).read_bytes() == (shots / n).read_bytes(), n


def test_rendered_pages_link_to_each_other():
    zh = EDITIONS["zh"][1].read_text(encoding="utf-8")
    en = EDITIONS["en"][1].read_text(encoding="utf-8")
    assert '<a href="en/">English</a>' in zh
    assert '<a href="../">中文</a>' in en


def test_today_page_opens_the_edition_in_the_page_language():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'handbookStart = () => (I18N.lang === "en" ? new URL("en/", HANDBOOK_URL).href : HANDBOOK_URL)' in app
    assert "loadHandbook(handbookStart())" in app and "loadHandbook(HANDBOOK_URL);" not in app
    # the language link inside the panel stays inside the panel (same handbook directory)
    assert "url.pathname.startsWith(new URL(HANDBOOK_URL).pathname)" in app


@pytest.mark.parametrize("page", ["activate.html", "install.html"])
def test_activation_pages_link_to_the_handbook_in_the_page_language(page):
    html = (STATIC / page).read_text(encoding="utf-8")
    assert 'href="handbook/"' in html and 'data-i18n-href="activate.handbookUrl"' in html
    i18n = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert '"activate.handbookUrl": "handbook/"' in i18n and '"activate.handbookUrl": "handbook/en/"' in i18n
    assert "[data-i18n-href]" in i18n
