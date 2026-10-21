"""The chat answer renderer (web/static/md.js, issue #92): safe Markdown subset, XSS, links, streaming with half-written
markers. Runs md.js in node (skipped only where node is missing; CI has it)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"
MD = STATIC / "md.js"


def render_many(cases: list[tuple[str, bool]]) -> list[str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = (f"const md = require({json.dumps(str(MD))});\n"
             f"const cases = {json.dumps(cases, ensure_ascii=False)};\n"
             "console.log(JSON.stringify(cases.map(([t, p]) => md.render(t, { partial: p }))));")
    out = subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def r(text: str, partial: bool = False) -> str:
    return render_many([(text, partial)])[0]


def text_of(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def test_markup_subset():
    html = r("**粗** 和 *斜* 和 _also_ 和 ~~删~~ 和 `code`\n\n### 标题\n\n- a\n- b\n  - 嵌套\n\n1. 一\n2. 二\n\n> 引用\n\n"
             "| 项目 | 数值 |\n|---|---:|\n| 蛋白 | 120 g |\n\n```\nx < 1\n```\n\n---\n结尾")
    for frag in ("<strong>粗</strong>", "<em>斜</em>", "<em>also</em>", "<del>删</del>", "<code>code</code>",
                 '<p class="md-h">标题</p>', "<ul><li>a</li><li>b<ul><li>嵌套</li></ul></li></ul>", "<ol><li>一</li><li>二</li></ol>",
                 "<blockquote><p>引用</p></blockquote>", "<th>项目</th>", "<td>120 g</td>", "<pre><code>x &lt; 1</code></pre>", "<hr>"):
        assert frag in html, frag
    assert r("第一行\n第二行") == "<p>第一行<br>第二行</p>"                         # chat line breaks stay
    assert r("snake_case_name 和 2*3*4") == "<p>snake_case_name 和 2*3*4</p>"         # no false emphasis
    assert r("3. 从三开始\n4. 四").startswith('<ol start="3">')


def test_html_is_escaped_and_links_are_safe():
    cases = ["<script>alert(1)</script>", '<img src=x onerror="alert(1)">', "[点我](javascript:alert(1))",
             "[x](data:text/html,<b>)", '[a](https://x.com" onmouseover="alert(1))', "https://a.com\"><script>",
             "**<b>粗</b>**", "`<i>`", "```\n</code><script>\n```", "> <iframe src=x>", "| <a> | b |\n|---|---|\n| c | d |"]
    for html in render_many([(c, False) for c in cases]):
        assert "<script" not in html and "<img" not in html and "<iframe" not in html and "<i>" not in html
        assert "javascript:" not in re.sub(r"&[a-z#0-9]+;", "", "".join(re.findall(r'href="([^"]*)"', html)))
        assert not re.search(r"<a (?![^>]*href=\"https?://)", html)                 # every link is http(s)
        assert not re.search(r"<[a-z][^>]*\son\w+\s*=", html)                       # no event handler in a real tag
    link = r("详见[手册](https://example.com/h?a=1&b=2)。")
    assert '<a href="https://example.com/h?a=1&amp;b=2" target="_blank" rel="noopener noreferrer nofollow">手册</a>' in link
    bare = r("看这里 https://example.com/x，然后")
    assert '<a href="https://example.com/x"' in bare and "，然后" in bare


def test_streaming_half_markers_never_leak():
    """Every prefix of a streamed answer, rendered partial: no literal ** / ~~ shows, nothing after an unclosed ** turns
    bold, an unclosed fence is a growing code block; the final render is the plain full render."""
    answer = ("**恢复 61（黄）**，比上周低一点。\n\n- 睡眠 **7.2 小时**，睡眠债 1 小时\n- HRV 46 ms，*略低*\n\n"
              "看 [手册](https://example.com/handbook)，命令 `get_briefing`。\n\n```\nlet a = 1\n```\n~~旧的~~ 新的")
    prefixes = [answer[:i] for i in range(1, len(answer) + 1)]
    htmls = render_many([(p, True) for p in prefixes] + [(answer, False)])
    final = htmls.pop()
    for p, html in zip(prefixes, htmls):
        t = text_of(html)
        assert "**" not in t and "~~" not in t, (p, html)
        for bold in re.findall(r"<strong>(.*?)</strong>", html):
            assert bold in ("恢复 61（黄）", "7.2 小时"), (p, html)                # only the real bold spans
        assert "](" not in t, (p, html)                                              # half-written link hidden
    assert htmls[-1] == final


def test_unclosed_markers_after_streaming_stay_text():
    assert r("今天 **恢复很好") == "<p>今天 **恢复很好</p>"                          # final: literal, not all bold
    assert r("今天 **恢复很好", True) == "<p>今天 恢复很好</p>"                       # streaming: marker hidden
    assert r("```js\nlet a", True) == "<pre><code>let a</code></pre>"


def test_ai_js_uses_the_renderer():
    ai = (STATIC / "ai.js").read_text(encoding="utf-8")
    assert "window.BodyMarkdown.render(el._raw" in ai and "cur.textContent += d.delta" not in ai
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('src="md.js"') < html.index('src="ai.js"')
    assert '"md.js"' in (STATIC / "sw.js").read_text(encoding="utf-8")
