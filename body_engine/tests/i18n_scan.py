"""A small JavaScript scanner for the i18n tests (tests/test_i18n.py): finds string / template / regex literals and comments,
which tr(...) first arguments they are, and the Chinese left outside tr(...). No dependency on a JS parser; good enough for
the hand-written page scripts (no build step)."""
import re
from dataclasses import dataclass

CJK = re.compile(r"[　-〿一-鿿＀-￯]")
TR_CALL = re.compile(r"(?:\btr|\bI18N\.t)\(\s*$")
REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
REGEX_KW = ("return", "typeof", "case", "do", "else", "in", "of", "void", "yield", "await")


@dataclass
class Lit:
    kind: str          # "str" | "tpl" (static template part) | "regex"
    text: str          # raw text without quotes
    line: int
    is_tr_key: bool    # the first argument of tr(...) / I18N.t(...)
    line_text: str


def scan(src: str) -> list[Lit]:
    out: list[Lit] = []
    lines = src.split("\n")
    line_at = lambda i: src.count("\n", 0, i) + 1
    i, n = 0, len(src)
    stack: list[int] = []   # template nesting: brace depth inside each ${ }
    last_sig = ""           # last significant code character / word, to tell a regex from a division

    def push(kind, start, end, text):
        q = start - 1 if kind in ("str", "regex") or (start and src[start - 1] == "`") else None
        before = src[max(0, q - 40):q] if q is not None else ""
        ln = line_at(start)
        out.append(Lit(kind, text, ln, bool(TR_CALL.search(before)) and kind != "regex", lines[ln - 1]))

    def read_template(i):
        # at a backtick (or right after a closing } of ${...}); returns index after the closing backtick or after "${"
        start = i
        while i < n:
            c = src[i]
            if c == "\\":
                i += 2
                continue
            if c == "`":
                push("tpl", start, i, src[start:i])
                return i + 1, False
            if c == "$" and i + 1 < n and src[i + 1] == "{":
                push("tpl", start, i, src[start:i])
                return i + 2, True
            i += 1
        return i, False

    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            push("str", i + 1, j, src[i + 1:j])
            i = j + 1
            last_sig = "a"
            continue
        if c == "`":
            i, opened = read_template(i + 1)
            if opened:
                stack.append(0)
                last_sig = "("
            else:
                last_sig = "a"
            continue
        if c == "{" and stack:
            stack[-1] += 1
        if c == "}" and stack:
            if stack[-1] == 0:
                stack.pop()
                i, opened = read_template(i + 1)
                if opened:
                    stack.append(0)
                    last_sig = "("
                else:
                    last_sig = "a"
                continue
            stack[-1] -= 1
        if c == "/" and (last_sig == "" or last_sig in REGEX_PREV or last_sig in REGEX_KW):
            j, in_cls = i + 1, False
            while j < n and (src[j] != "/" or in_cls) and src[j] != "\n":
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    in_cls = True
                elif src[j] == "]":
                    in_cls = False
                j += 1
            push("regex", i + 1, j, src[i + 1:j])
            i = j + 1
            while i < n and src[i].isalpha():
                i += 1
            last_sig = "a"
            continue
        m = re.match(r"[A-Za-z_$][\w$]*", src[i:])
        if m:
            word = m.group(0)
            last_sig = word if word in REGEX_KW else "a"
            i += len(word)
            continue
        last_sig = c if c in REGEX_PREV else ("a" if c in ")]" else c)
        i += 1
    return out


def tr_keys(src: str) -> list[Lit]:
    return [x for x in scan(src) if x.is_tr_key]


def stray_chinese(src: str) -> list[Lit]:
    """Literals with Chinese that are not a tr() key, except on lines marked i18n-ok."""
    return [x for x in scan(src) if not x.is_tr_key and CJK.search(x.text) and "i18n-ok" not in x.line_text]
