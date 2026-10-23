"""Naming filter (rollout flag ai_parity, ai/parity.py): the AI never names the back end that keeps the food, training and
weight logs. It is "底层数据库" in Chinese and "our database" in English, in what the AI reads and in what the user sees.

- scrub(text): texts the AI reads (system prompt, tool descriptions, tool results and errors). Identifiers stay as they
  are, because the AI must call `undo_sparky_entry` by its name and read fields such as `sparky_error`; admin hints
  (CLI commands, key files) are dropped.
- scrub(text, output=True): texts the user sees (answers, stored messages, errors). Identifiers lose their back end part
  too (`undo_sparky_entry` -> `undo_entry`).
- StreamScrubber: the same as scrub(output=True) over a streamed answer, where a name can be split across two chunks.
  It holds back only a tail that could still become a name (at most HOLD characters), so the answer keeps flowing.
Code, logs, developer docs and THIRD_PARTY_NOTICES.md keep the real name; only these boundaries change.
"""
import re

from ..i18n import is_en

ZH, EN = "底层数据库", "our database"
HOLD = 48                                   # longer than any name ("the sparky   fitness   database")
CONTEXT = 64                                # raw characters kept before a streamed tail (look-behinds, which language)
_WS = r"[\s​-‍﻿]"           # spaces, also zero-width ones
_ID = "A-Za-z0-9_\\-"
_CJK = re.compile(r"[⺀-鿿　-〿＀-￯]")
_NAME = (rf"(?<![{_ID}/.])(?P<the>the{_WS}{{1,3}})?sparky(?:(?:{_WS}|[\-_]){{0,3}}fitness)?"
         rf"(?P<db>{_WS}{{0,3}}(?:database|db|数据库))?(?![{_ID}])")
_ADMIN = (r"(?P<admin>[：:]\s*body-engine\s+sparky-link[^\n）)，。]{0,80}"   # 「还没连 SparkyFitness：body-engine sparky-link …」
          r"|（deploy/sparkyfitness/[^）\n]{0,80}）?"                       # 「（deploy/sparkyfitness/bootstrap.py 生成）」
          r"|sparkyfitness_api_key\w*"                                     # key file names
          r"|(?<![A-Za-z0-9_])sparkyerror(?![A-Za-z0-9_]))")              # the client's exception class in error texts
_IDENT = (r"(?P<ident>(?<=_)sparky(?:_?fitness)?_?"                         # undo_sparky_entry -> undo_entry
          rf"|(?<![{_ID}/.])sparky(?:_?fitness)?[\-_](?=[A-Za-z0-9]))")     # sparky_error -> error, sparky-link -> link
_AI = re.compile(f"{_ADMIN}|{_NAME}", re.I)
_OUT = re.compile(f"{_ADMIN}|{_IDENT}|{_NAME}", re.I)
# Lower-case forms a name can take (whitespace runs as one space): a streamed tail that is the start of one is held back
_CANON = tuple(f"{the}{core}{db}" for the in ("", "the ")
               for core in ("sparky", "sparky fitness", "sparkyfitness", "sparky-fitness", "sparky_fitness")
               for db in ("", " database", " db", "数据库", " 数据库"))
_ADMIN_START = (*(f"{c}body-engine sparky-link" for c in ("：", ":", "： ", ": ")), "（deploy/sparkyfitness/", "sparkyfitness_api_key",
                "sparkyerror")


def _side(text: str, i: int, step: int) -> str:
    while 0 <= i < len(text) and text[i].isspace():
        i += step
    return text[i] if 0 <= i < len(text) else ""


def _replacement(m: re.Match, text: str) -> str:
    if m.group("admin") or ("ident" in m.re.groupindex and m.group("ident")):
        low = m.group(0).lower()
        return "key" if low.startswith("sparkyfitness_api_key") else "DatabaseError" if low == "sparkyerror" else ""
    db = (m.group("db") or "").strip().lower()
    if m.group("the") or db in ("database", "db"):
        return "Our database" if m.group(0)[0] == "T" else EN
    before, after = _side(text, m.start() - 1, -1), _side(text, m.end(), 1)
    if _CJK.match(before) or _CJK.match(after) or db == "数据库":
        return ZH
    if re.match(r"[A-Za-z]", before) or re.match(r"[A-Za-z]", after):
        return EN
    # no Chinese and no letters next to it: an English request still gets the English name ("SparkyFitness: …" at the start of a text)
    return (EN if before else EN[0].upper() + EN[1:]) if is_en() else ZH


def scrub(text, output: bool = False):
    """Replace the back end's name in one text (non-strings are returned as they are)."""
    if not isinstance(text, str) or "sparky" not in text.lower():
        return text
    return (_OUT if output else _AI).sub(lambda m: _replacement(m, text), text)


def scrub_obj(obj, output: bool = False):
    """scrub every string inside a JSON-like value; dict keys are left alone."""
    if isinstance(obj, str):
        return scrub(obj, output)
    if isinstance(obj, list):
        return [scrub_obj(v, output) for v in obj]
    if isinstance(obj, dict):
        return {k: scrub_obj(v, output) for k, v in obj.items()}
    return obj


def scrub_schema(schema):
    """Only the `description` texts of a JSON schema (property names and enum values are what the tool accepts)."""
    if isinstance(schema, list):
        return [scrub_schema(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    return {k: scrub(v) if k == "description" else scrub_schema(v) for k, v in schema.items()}


def _could_start(text: str, j: int) -> bool:
    """Whether a name, an identifier holding it or an admin hint might start at text[j] and is not finished yet."""
    tail = re.sub(rf"{_WS}+", " ", text[j:].lower())
    if any(a.startswith(tail) for a in _ADMIN_START):
        return True
    prev = text[j - 1] if j else ""
    if prev and (prev.isalnum() and prev.isascii() or prev in "-/."):
        return False
    return any(c.startswith(tail) for c in _CANON) or (prev == "_" and "sparky_".startswith(tail))


class StreamScrubber:
    """feed() each streamed delta, then flush() once at the end; the pieces joined = scrub(whole text, output=True)."""

    def __init__(self):
        self.pending = ""
        self.context = ""   # the last raw characters already emitted (look-behinds, which language)

    def feed(self, delta: str) -> str:
        self.pending += delta or ""
        return self._emit(final=False)

    def flush(self) -> str:
        return self._emit(final=True)

    def _emit(self, final: bool) -> str:
        work, off = self.context + self.pending, len(self.context)
        cut = len(work)
        if not final:   # the earliest place a name may be starting and is not finished
            cut = next((j for j in range(max(off, len(work) - HOLD), len(work)) if _could_start(work, j)), len(work))
        out, pos = [], off
        for m in _OUT.finditer(work):
            if m.start() < off:
                continue
            if m.start() >= cut:
                break
            if m.end() > cut or (not final and m.end() == len(work)):   # may still grow: keep it for the next delta
                cut = m.start()
                break
            out += [work[pos:m.start()], _replacement(m, work)]
            pos = m.end()
        out.append(work[pos:cut])
        self.context = work[:cut][-CONTEXT:]
        self.pending = work[cut:]
        return "".join(out)
