"""Public legal pages (legal/, docs/legal/): the package copies match docs/legal, both languages have the same sections,
placeholders are filled from settings (neutral defaults, nobody named), the Markdown renderer escapes everything, and the
pages are served without sign-in. No database needed."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from body_engine import api
from body_engine.legal import pages

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "legal"
PKG = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "legal" / "texts"
NAMES = ["privacy.md", "privacy.en.md", "terms.md", "terms.en.md"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for env in list(pages.ENV.values()) + ["BODY_LEGAL_MIN_AGE"]:
        monkeypatch.delenv(env, raising=False)
        monkeypatch.delenv(env + "_EN", raising=False)


@pytest.mark.parametrize("name", NAMES)
def test_package_copy_matches_docs(name):
    assert (PKG / name).read_bytes() == (DOCS / name).read_bytes(), f"copy docs/legal/{name} into legal/texts/"


@pytest.mark.parametrize("doc", pages.DOCS)
def test_languages_have_same_sections_and_version(doc):
    zh, en = pages.source(doc, "zh"), pages.source(doc, "en")
    assert len(re.findall(r"^## ", zh, re.M)) == len(re.findall(r"^## ", en, re.M))
    assert pages.version(doc, "zh") == pages.version(doc, "en")
    for lang, text in (("zh", zh), ("en", en)):
        unknown = set(re.findall(r"\{\{(\w+)\}\}", text)) - set(pages.values(lang))
        assert not unknown, unknown


def test_consent_draft_pairs_and_inactive():
    zh = (DOCS / "consent-public-draft.md").read_text(encoding="utf-8")
    en = (DOCS / "consent-public-draft.en.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^## ", zh, re.M)) == len(re.findall(r"^## ", en, re.M))
    vz = re.search(r"^版本：\s*(\S+)", zh, re.M).group(1)
    assert vz.endswith(".draft") and vz == re.search(r"^Version:\s*(\S+)", en, re.M).group(1)
    active = (ROOT / "body_engine" / "src" / "body_engine" / "profile" / "consent.md").read_text(encoding="utf-8")
    assert vz not in active   # the draft is not the consent in force


def test_defaults_are_neutral_and_settings_fill_in(monkeypatch):
    html_zh = pages.page("privacy", "zh")
    assert "{{" not in html_zh and "Watch2Whoop" in html_zh and "16" in html_zh
    assert "@" not in pages.values("en")["contact"]
    monkeypatch.setenv("BODY_LEGAL_ENTITY", "示例运营者")
    monkeypatch.setenv("BODY_LEGAL_ENTITY_EN", "Example Operator Ltd")
    monkeypatch.setenv("BODY_LEGAL_CONTACT", "privacy@example.com")
    monkeypatch.setenv("BODY_DATA_REGION", "Example Region")
    monkeypatch.setenv("BODY_LEGAL_MIN_AGE", "18")
    monkeypatch.setenv("BODY_LEGAL_APP_NAME", "Example App")
    zh, en = pages.page("privacy", "zh"), pages.page("terms", "en")
    assert "示例运营者" in zh and "privacy@example.com" in zh and "Example Region" in zh and "18 岁" in zh
    assert "Example Operator Ltd" in en and "at least <strong>18</strong>" in en and "<title>Example App Terms of Use" in en


def test_markdown_escapes_and_links():
    out = pages.markdown("# T <b>\n\nA **bold** `x<y>` [p](privacy) [w](https://example.com) [bad](javascript:alert(1))\n\n"
                         "- one\n  more\n- two\n\n> note\n\n---", "en")
    assert "<b>" not in out and "&lt;b&gt;" in out
    assert "<strong>bold</strong>" in out and "<code>x&lt;y&gt;</code>" in out
    assert '<a href="privacy?lang=en">p</a>' in out and '<a href="https://example.com">w</a>' in out
    assert "javascript" not in out and "bad" in out
    assert "<li>one more</li><li>two</li>" in out and "<blockquote>" in out and "<hr>" in out


def test_pages_are_public_and_pick_a_language():
    c = TestClient(api.app, base_url="https://testserver")
    r = c.get("/legal/privacy?lang=en")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "Privacy Policy" in r.text
    assert c.get("/legal/terms", headers={"Accept-Language": "zh-CN,zh;q=0.9"}).headers["content-language"] == "zh-Hans"
    assert c.get("/legal/terms", headers={"Accept-Language": "de-DE"}).headers["content-language"] == "en"
    assert c.get("/legal/other").status_code == 404
    idx = c.get("/v1/legal").json()
    assert idx["privacy"]["path"] == "/legal/privacy" and idx["terms"]["version"] and idx["min_age"] == 16
