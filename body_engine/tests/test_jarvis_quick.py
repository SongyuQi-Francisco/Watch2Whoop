"""Jarvis quick tasks per page (issue #101): behind the rollout flag jarvis_quick, only on one's own page, every quick question
bilingual, and the chat request carries the page id the server's tool groups use (ai/params.PAGE_GROUPS, #99)."""
import re
from pathlib import Path

from body_engine.ai import params as P
from body_engine.assistant import toolsets
from body_engine.rollout import registry

STATIC = Path(__file__).parents[1] / "src/body_engine/web/static"
AI = (STATIC / "ai.js").read_text()


def test_flag_declared_and_checked_first():
    assert "jarvis_quick" in registry.FLAGS
    body = AI[AI.index("function quickList"):AI.index("function drawQuick")]
    assert body.index('BodyFlags?.on("jarvis_quick")') < body.index("C.isSelf")   # off -> nothing, before anything else


def test_quick_pages_are_server_pages_and_bilingual():
    body = AI[AI.index("function quickList"):AI.index("function drawQuick")]
    pages = re.findall(r'^\s+"?([a-z.]+)"?: \[', body, flags=re.M)
    assert pages and all(toolsets.known_page(p) for p in pages), pages
    en = (STATIC / "i18n-en.js").read_text()
    for zh in re.findall(r'tr\("([^"]+)"\)', body):
        assert f'"{zh}":' in en, zh


def test_chat_request_sends_the_page():
    send = AI[AI.index("async function send()"):AI.index("async function stream(")]
    assert 'stream("/v1/assistant/chat", {' in send and "page: C.page?.()" in send
    app = (STATIC / "app.js").read_text()
    assert "page: pageId, isSelf" in app
    assert P.PAGE_MAX_LEN >= max(len(p) for p in P.PAGE_GROUPS)
