"""The daily quotes as data, for clients that do not run the Today page's JavaScript (the native app, issues #126 / #340).

One source: web/static/slim-quotes.js. Only its `items` are served (`unverified` never is), each word for word, in the same
fixed mixed-up order as slim.js `ordered()`, so a client that takes (day number + taps) modulo the list length shows what the
page shows. Editing a quote in that file reaches the app with the next deploy; no app release is needed."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "web" / "static" / "slim-quotes.js"
FIELDS = ("id", "person", "text", "lang", "source_url")
_ROW = re.compile(r"^\s*(\{.*\}),?\s*(?://.*)?$")


def _hash(s: str) -> int:
    """slim.js hash(): djb2 with xor over UTF-16 code units, kept to 32 bits (the ids are ASCII)."""
    h = 5381
    for c in s:
        h = ((h * 33) ^ ord(c)) & 0xFFFFFFFF
    return h


def parse(source: str) -> dict:
    """{version, items} of the data file: one JSON object per line between `items: [` and `unverified: [`."""
    version = re.search(r"\bversion:\s*(\d+)", source)
    block = source[source.index("items: ["):source.index("unverified: [")]
    rows = [json.loads(m.group(1)) for line in block.splitlines() if (m := _ROW.match(line))]
    shown = [q for q in rows if q.get("verified") is True and q.get("text") and q.get("source_url")]
    shown.sort(key=lambda q: (_hash(q["id"]), q["id"]))
    return {"version": int(version.group(1)) if version else 1,
            "items": [{k: q[k] for k in FIELDS} for q in shown]}


@lru_cache(maxsize=1)
def load() -> dict:
    return parse(SOURCE.read_text(encoding="utf-8"))
