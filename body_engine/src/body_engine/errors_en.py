"""English for the Chinese `detail` of HTTP errors (issue #42). Applied once, in the exception handler in api.py, when the
request asked for English (X-Body-Lang: en); everyone else keeps the Chinese detail, so older clients and scripts are unchanged.
EXACT: whole detail -> English. PATTERNS: (regex on the Chinese, English with \\1 … groups) for details with numbers / names."""
import re

EXACT: dict[str, str] = {
}

PATTERNS: list[tuple[re.Pattern, str]] = [
]


def translate(detail: str) -> str:
    if detail in EXACT:
        return EXACT[detail]
    for rx, en in PATTERNS:
        m = rx.fullmatch(detail)
        if m:
            return m.expand(en)
    return detail
