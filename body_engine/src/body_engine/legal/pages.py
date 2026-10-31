"""Public legal pages: the privacy policy and the terms of use, in Chinese and English (docs/legal/).

- Sources: docs/legal/{privacy,terms}{,.en}.md. The package carries byte-identical copies in legal/texts/ (the Docker image
  is built from body_engine/ alone); tests/test_legal.py checks they match.
- Operator details are placeholders filled from settings at request time, so the public repository names nobody:
    {{app_name}}       BODY_LEGAL_APP_NAME        default "Watch2Whoop" (the store name, once decided)
    {{entity}}         BODY_LEGAL_ENTITY          who operates the service (person or company)
    {{contact}}        BODY_LEGAL_CONTACT         how to reach the operator (e-mail address or web page)
    {{region}}         BODY_DATA_REGION           where the server and its backups are
    {{governing_law}}  BODY_LEGAL_GOVERNING_LAW   which law governs the terms
    {{min_age}}        BODY_LEGAL_MIN_AGE         minimum age (default 16; set the age your regions require)
  Each text setting may have an English variant `<NAME>_EN`; without it the same value is used in both languages. Unset
  settings fall back to neutral wording ("the operator of this server", "not set").
- Rendered by a small Markdown subset (headings, paragraphs, lists, block quotes, rules, bold, code, links) after
  HTML-escaping everything, so a text can never inject markup. Relative links (`[Privacy Policy](privacy)`) stay in the
  same language.
"""
from __future__ import annotations

import html
import os
import re
from functools import lru_cache
from importlib import resources

DOCS = ("privacy", "terms")
LANGS = ("zh", "en")

# Neutral defaults: nothing here names a real person, company, domain or address.
DEFAULTS = {
    "app_name": ("Watch2Whoop", "Watch2Whoop"),
    "entity": ("本服务器的运营者", "the operator of this server"),
    "contact": ("（运营者还没有填写联系方式）", "(the operator has not set a contact yet)"),
    "region": ("运营者选择的服务器所在地区", "the region where the operator runs the server"),
    "governing_law": ("运营者所在地的法律", "the law of the place where the operator is established"),
}
ENV = {"app_name": "BODY_LEGAL_APP_NAME", "entity": "BODY_LEGAL_ENTITY", "contact": "BODY_LEGAL_CONTACT",
       "region": "BODY_DATA_REGION", "governing_law": "BODY_LEGAL_GOVERNING_LAW"}
MIN_AGE_DEFAULT = 16
TITLES = {"privacy": ("隐私政策", "Privacy Policy"), "terms": ("使用条款", "Terms of Use")}


def min_age() -> int:
    try:
        return max(1, int(os.environ.get("BODY_LEGAL_MIN_AGE") or MIN_AGE_DEFAULT))
    except ValueError:
        return MIN_AGE_DEFAULT


def values(lang: str) -> dict[str, str]:
    """The placeholder values for one language, from settings (English variant `<NAME>_EN` first) or the defaults."""
    en = lang == "en"
    out = {}
    for key, env in ENV.items():
        v = (os.environ.get(env + "_EN") if en else None) or os.environ.get(env)
        out[key] = (v or "").strip() or DEFAULTS[key][1 if en else 0]
    out["min_age"] = str(min_age())
    return out


@lru_cache(maxsize=None)
def source(doc: str, lang: str) -> str:
    name = f"{doc}.en.md" if lang == "en" else f"{doc}.md"
    return resources.files("body_engine.legal").joinpath("texts", name).read_text(encoding="utf-8")


def version(doc: str, lang: str = "zh") -> str | None:
    m = re.search(r"^(?:版本：|Version:)\s*(\S+)", source(doc, lang), re.M)
    return m.group(1) if m else None


def fill(text: str, vals: dict[str, str]) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: vals.get(m.group(1), m.group(0)), text)


_INLINE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)")


def _inline(text: str, lang: str) -> str:
    """Escape, then bold / code / links. Links: https:, mailto: or relative (kept in the same language)."""
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        if m.group(1) is not None:
            out.append(f"<strong>{_inline(m.group(1), lang)}</strong>")
        elif m.group(2) is not None:
            out.append(f"<code>{html.escape(m.group(2))}</code>")
        else:
            label, url = m.group(3), m.group(4)
            if re.match(r"^(https://|mailto:)", url):
                href = url
            elif re.match(r"^[a-z][a-z0-9-]*$", url):
                href = f"{url}?lang={lang}"
            else:
                out.append(html.escape(label))
                pos = m.end()
                continue
            out.append(f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a>')
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def markdown(md: str, lang: str = "zh") -> str:
    """The Markdown subset the legal texts use → HTML."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    quote: list[str] = []

    def flush():
        if para:
            out.append(f"<p>{_inline(' '.join(para), lang)}</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(i, lang)}</li>" for i in items) + "</ul>")
            items.clear()
        if quote:
            out.append(f"<blockquote><p>{_inline(' '.join(quote), lang)}</p></blockquote>")
            quote.clear()

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if m := re.match(r"^(#{1,3})\s+(.*)$", line):
            flush()
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2), lang)}</h{n}>")
        elif re.match(r"^-{3,}$", line):
            flush()
            out.append("<hr>")
        elif line.startswith("- "):
            if para or quote:
                flush()
            items.append(line[2:].strip())
        elif line.startswith(">"):
            if para or items:
                flush()
            quote.append(line.lstrip(">").strip())
        elif items and raw.startswith("  "):
            items[-1] += " " + line.strip()
        else:
            if items or quote:
                flush()
            para.append(line.strip())
    flush()
    return "\n".join(out)


STYLE = """
:root{--bg:#f7f7f5;--text:#1c1c1e;--text2:#5f5f66;--line:#e2e2df;--link:#1c1c1e}
@media (prefers-color-scheme: dark){:root{--bg:#111113;--text:#f2f2f2;--text2:#a1a1a8;--line:#2a2a2e;--link:#f2f2f2}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;line-height:1.3;margin:8px 0 4px}h2{font-size:19px;margin:28px 0 8px}h3{font-size:16px;margin:20px 0 6px}
p,li{color:var(--text)}ul{padding-left:22px}li{margin:4px 0}
blockquote{margin:12px 0;padding:8px 14px;border-left:3px solid var(--line);color:var(--text2)}
code{font:14px ui-monospace,Menlo,monospace}a{color:var(--link)}hr{border:0;border-top:1px solid var(--line);margin:28px 0}
nav{display:flex;gap:16px;font-size:14px;color:var(--text2)}nav a{color:var(--text2)}
"""


def page(doc: str, lang: str) -> str:
    """The full HTML page of one document in one language."""
    vals = values(lang)
    body = markdown(fill(source(doc, lang), vals), lang)
    other = "en" if lang == "zh" else "zh"
    sibling = "terms" if doc == "privacy" else "privacy"
    title = f"{vals['app_name']} {TITLES[doc][0]}" if lang == "zh" else f"{vals['app_name']} {TITLES[doc][1]}"
    nav = (f'<nav><a href="{sibling}?lang={lang}">{html.escape(TITLES[sibling][0 if lang == "zh" else 1])}</a>'
           f'<a href="{doc}?lang={other}" hreflang="{other}">{"English" if other == "en" else "中文"}</a></nav>')
    return (f'<!doctype html><html lang="{"zh-Hans" if lang == "zh" else "en"}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>'
            f'<style>{STYLE}</style></head><body><main>{nav}\n{body}</main></body></html>')


def pick_lang(query: str | None, accept_language: str | None) -> str:
    """?lang= first, then the browser's first language: Chinese → zh, anything else → en."""
    if query in LANGS:
        return query
    first = (accept_language or "").split(",")[0].strip().lower()
    return "zh" if first.startswith("zh") else "en"
