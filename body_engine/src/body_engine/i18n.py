"""Language of the texts the Today page shows (issue #42).

The page sends `X-Body-Lang: zh | en` with every API call (web/static/i18n.js `I18N.headers()`); `LangMiddleware` puts it in a
context variable for the request. Without the header (older clients, Shortcut uploads, MCP / AI clients, CLI) it is zh, so
everything the AI reads stays as it was. AI tool runs (`assistant.tools.run_tool`) always run in zh, also inside the in-page chat.

Code that builds a UI text calls `L("中文", "English")` (both evaluated, the request's language picks one) or looks a fixed
label up with `tl("中文", TABLE)`. HTTP error details are translated once, in the exception handler (`errors_en.translate`).
"""
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

LANGS = ("zh", "en")
_lang: ContextVar[str] = ContextVar("body_lang", default="zh")


def norm(value: str | None) -> str:
    v = (value or "").strip().lower()
    return "en" if v.startswith("en") else "zh"


def lang() -> str:
    return _lang.get()


def is_en() -> bool:
    return _lang.get() == "en"


@contextmanager
def use_lang(value: str) -> Iterator[None]:
    token = _lang.set(norm(value))
    try:
        yield
    finally:
        _lang.reset(token)


def L(zh: str, en: str) -> str:
    """The text in the current language."""
    return en if _lang.get() == "en" else zh


def tl(zh: str, table: Mapping[str, str]) -> str:
    """A fixed Chinese label in the current language: `table` maps Chinese -> English; unknown labels are returned as is."""
    if _lang.get() != "en" or not zh:
        return zh
    return table.get(zh, zh)


class LangMiddleware:
    """Pure ASGI (works for streaming responses too): X-Body-Lang -> the request's language."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        raw = dict(scope.get("headers") or []).get(b"x-body-lang", b"").decode("latin-1")
        token = _lang.set(norm(raw))
        try:
            await self.app(scope, receive, send)
        finally:
            _lang.reset(token)
