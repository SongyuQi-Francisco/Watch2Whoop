"""Keyword filter for text other people see (App Store guideline 1.2): circle comments, circle names and display names are
checked when they are written; a text with a listed word is refused with HTTP 422 and the fixed message MESSAGE (the app
shows the server's message).

The list is a plain text file, one word or phrase per line (`#` starts a comment), at BODY_BLOCKED_WORDS_FILE, default
secrets/blocked_words.txt (not in git; the deployer fills it). No file = an empty list = nothing is refused. The file is
read again when it changes (mtime), so editing it needs no restart.

Matching: Unicode NFKC + case folding, then a plain substring test, also against the text with spaces and punctuation
removed ("b a d" and "b.a.d" match "bad"). Substrings are the only workable rule for Chinese; for short Latin words they can
over-match (a word inside a longer, harmless one), so list whole phrases there.
"""
from __future__ import annotations

import os
import threading
import unicodedata
from pathlib import Path

from .config import REPO_ROOT
from .i18n import L

ENV = "BODY_BLOCKED_WORDS_FILE"
DEFAULT_FILE = REPO_ROOT / "secrets" / "blocked_words.txt"
STATUS = 422
# Chinese stays the key of errors_en.py (the HTTP layer translates it for English requests)
MESSAGE_ZH = "内容里有不允许的词，请修改后再发"
MESSAGE_EN = "This contains words that aren't allowed. Please change it and try again"

_lock = threading.Lock()
_cache: dict[str, tuple[float, tuple[str, ...]]] = {}


class Blocked(ValueError):
    """A text with a listed word. str(e) is the readable message in the request's language."""
    status = STATUS
    code = "content_blocked"

    def __init__(self):
        super().__init__(L(MESSAGE_ZH, MESSAGE_EN))


def path() -> Path:
    return Path(os.environ.get(ENV) or DEFAULT_FILE)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _squeeze(text: str) -> str:
    """Letters, digits and CJK only (spaces, punctuation, symbols removed)."""
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in ("L", "N"))


def parse(lines) -> tuple[str, ...]:
    out = []
    for line in lines:
        w = normalize(line.split("#", 1)[0].strip())
        if w and w not in out:
            out.append(w)
    return tuple(out)


def words() -> tuple[str, ...]:
    p = path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return ()
    key = str(p)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            ws = parse(p.read_text(encoding="utf-8").splitlines())
        except OSError:
            return ()
        _cache[key] = (mtime, ws)
        return ws


def find(text: str | None, word_list: tuple[str, ...] | None = None) -> str | None:
    """The first listed word in `text`, else None."""
    ws = words() if word_list is None else word_list
    if not text or not ws:
        return None
    t = normalize(text)
    s = _squeeze(t)
    for w in ws:
        if w in t or (len(_squeeze(w)) > 1 and _squeeze(w) in s):
            return w
    return None


def check(text: str | None) -> None:
    """Raises Blocked when `text` contains a listed word."""
    if find(text) is not None:
        raise Blocked()
