"""Personal hotword correction for recognised speech (Jarvis voice input, #53, rollout flag jarvis_orbit).

Speech recognisers (the server's SenseVoice, the iPhone's own on-device recogniser) get the user's own words wrong most often:
exercise names ("lat pulldown", 罗马尼亚硬拉), foods, supplements, brands. After recognition the text is compared with the
user's own term list and near misses are replaced:
- English terms: fuzzy spelling + sound (Levenshtein similarity * 0.7 + Metaphone similarity * 0.3 >= EN_MIN_SCORE, span
  length 0.6-1.6x the term, no span starting / ending with a function word); short terms need EN_MIN_SCORE_SHORT;
- Chinese terms: same pinyin (two-character terms also need the same tones, so 不及 never becomes 补剂).

Only text goes in and out; the terms are the user's own records (training log and plan, custom exercises, meal templates,
supplements, recipes, grocery products) and are read for that user only. Nothing is stored.
Thresholds come from the Jarvis voice-input study (2026-10-23, 108 synthetic clips in Chinese, English and mixed speech):
with this correction SenseVoice's mixed-speech error rate went from 14.2 % to 8.8 % and Apple's on-device one from 19.5 % to
8.0 %, with one wrong replacement (the stricter rules for short terms were added here because a personal list is longer
than the study's 27 terms). Real recordings may differ; the result keeps the list of replacements so a page can show them.
"""
import re
import time
from collections.abc import Iterable

import psycopg

# jellyfish and pypinyin are imported on first use: pypinyin's dictionaries are about 50 MB of Python objects, and this
# module is imported by every API process although voice input is rare (the API stays under its memory limit, see
# docs/api-memory.md).

MAX_TERMS = 300            # per user, most recently used first
MAX_TEXT = 2000            # characters accepted by POST /v1/assistant/voice-fix
EN_MIN_SCORE = 0.72        # combined spelling / sound similarity for a replacement (study: no misses above it on 27 terms)
EN_MIN_SCORE_SHORT = 0.78  # terms of at most SHORT_TERM letters: "calls" (0.72) must not become the store "Coles"
SHORT_TERM = 6
EN_MIN_LETTERS = 4         # shorter English terms are never used
ZH_MIN_CHARS, ZH_MAX_CHARS = 2, 10
CACHE_S = 300              # the term list is read again after this many seconds
TRAINING_DAYS = 180        # exercises logged in this many days count as the user's own

STOP = {"a", "an", "of", "my", "and", "the", "on", "for", "at", "to", "in", "i", "is", "was", "with"}
CJK = re.compile(r"[一-鿿]")
EN_TERM = re.compile(r"^[A-Za-z][A-Za-z' -]*[A-Za-z]$")
ZH_TERM = re.compile(r"^[一-鿿]+$")
PAREN = re.compile(r"\s*[（(][^）)]*[）)]\s*")

_CACHE: dict[tuple[str, int], tuple[float, list[str]]] = {}


# ---------------------------------------------------------------- the user's terms
def _clean(name: str | None) -> list[str]:
    """A name as one or two candidate terms: as written and without a bracketed part (杠铃卧推（平板） -> 杠铃卧推)."""
    if not name:
        return []
    s = " ".join(str(name).split())
    out = [s]
    bare = PAREN.sub(" ", s).strip()
    if bare and bare != s:
        out.append(bare)
    return out


def usable(term: str) -> bool:
    if ZH_TERM.match(term):
        return ZH_MIN_CHARS <= len(term) <= ZH_MAX_CHARS
    return bool(EN_TERM.match(term)) and len(re.sub(r"[^A-Za-z]", "", term)) >= EN_MIN_LETTERS


def _exercise_names(conn: psycopg.Connection, user_id: int) -> list[str]:
    rows = conn.execute(
        """SELECT x->>'slug' AS slug, x->>'name' AS name, max(l.date) AS last
             FROM training_session_logs l, jsonb_array_elements(l.exercises) x
            WHERE l.user_id = %s AND l.date >= current_date - %s AND jsonb_typeof(l.exercises) = 'array'
            GROUP BY 1, 2 ORDER BY last DESC LIMIT 200""", (user_id, TRAINING_DAYS)).fetchall()
    slugs = [r["slug"] for r in rows if r["slug"]]
    names = [r["name"] for r in rows if r["name"]]
    plan = conn.execute("SELECT days FROM training_plans WHERE user_id = %s AND active ORDER BY id DESC LIMIT 1",
                        (user_id,)).fetchone()
    for day in (plan["days"] if plan and isinstance(plan["days"], list) else []):
        for ex in (day.get("exercises") or []) if isinstance(day, dict) else []:
            if isinstance(ex, dict):
                slugs.append(ex.get("slug") or "")
                names.append(ex.get("name") or ex.get("name_zh") or "")
    slugs = [s for s in dict.fromkeys(slugs) if s]
    out: list[str] = []
    if slugs:
        cat = {r["slug"]: r for r in conn.execute(
            "SELECT slug, name_zh, name_en FROM exercise_catalog WHERE slug = ANY(%s)", (slugs,)).fetchall()}
        for s in slugs:
            r = cat.get(s)
            if r:
                out += [r["name_zh"], r["name_en"]]
    out += names
    out += [n for r in conn.execute(
        "SELECT name_zh, name_en FROM custom_exercises WHERE user_id = %s ORDER BY updated_at DESC LIMIT 100",
        (user_id,)).fetchall() for n in (r["name_zh"], r["name_en"])]
    return out


def _other_names(conn: psycopg.Connection, user_id: int) -> list[str]:
    q = [
        ("SELECT name FROM meal_templates WHERE user_id = %s AND active ORDER BY updated_at DESC LIMIT 60", ("name",)),
        ("SELECT name FROM supplements WHERE user_id = %s AND status = 'active' ORDER BY id DESC LIMIT 40", ("name",)),
        ("SELECT name FROM recipe_meta WHERE user_id = %s ORDER BY id DESC LIMIT 80", ("name",)),
        ("SELECT name, brand, store FROM grocery_products WHERE user_id = %s ORDER BY id DESC LIMIT 120",
         ("name", "brand", "store")),
    ]
    out: list[str] = []
    for sql, cols in q:
        for r in conn.execute(sql, (user_id,)).fetchall():
            out += [r[c] for c in cols]
    return out


def collect(names: Iterable[str | None], limit: int = MAX_TERMS) -> list[str]:
    """Usable, de-duplicated terms in the order given (most recent first), at most `limit`."""
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        for t in _clean(n):
            key = t.lower()
            if key in seen or not usable(t):
                continue
            seen.add(key)
            out.append(t)
            if len(out) >= limit:
                return out
    return out


def terms(conn: psycopg.Connection, user_id: int) -> list[str]:
    """The user's own term list (cached for CACHE_S per database and user)."""
    key = (conn.info.dbname, user_id)
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    out = collect([*_exercise_names(conn, user_id), *_other_names(conn, user_id)])
    _CACHE[key] = (time.monotonic(), out)
    return out


def forget(user_id: int | None = None) -> None:
    """Drop cached term lists (tests; all users when user_id is None)."""
    for k in [k for k in _CACHE if user_id is None or k[1] == user_id]:
        _CACHE.pop(k, None)


# ---------------------------------------------------------------- correction
def _sim(a: str, b: str) -> float:
    import jellyfish
    return 1 - jellyfish.levenshtein_distance(a, b) / max(len(a), len(b), 1)


def _metaphone(s: str) -> str:
    import jellyfish
    return jellyfish.metaphone(s)


def _fix_en(text: str, en_terms: list[str]) -> tuple[str, list[dict]]:
    words = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"[A-Za-z][A-Za-z']*", text)]
    if not words:
        return text, []
    flat = re.sub(r"[^a-z]", "", text.lower().replace("yoghurt", "yogurt"))
    reps: list[tuple[int, int, str]] = []
    used: set[int] = set()
    for t in sorted(en_terms, key=len, reverse=True):
        tl = re.sub(r"[^a-z]", "", t.lower())
        n = len(t.split())
        if not tl or tl in flat:
            continue
        need = EN_MIN_SCORE_SHORT if len(tl) <= SHORT_TERM else EN_MIN_SCORE
        best = None
        for k in range(max(1, n - 1), n + 2):
            for i in range(0, len(words) - k + 1):
                span = words[i:i + k]
                if any(text[span[j][1]:span[j + 1][0]].strip() for j in range(k - 1)):
                    continue   # the words must be next to each other
                if any(x in used for x in range(i, i + k)):
                    continue
                if k > 1 and (span[0][2].lower() in STOP or span[-1][2].lower() in STOP):
                    continue
                s = "".join(w[2] for w in span).lower().replace("'", "")
                if s == tl or s.rstrip("s") == tl:
                    continue
                if not (0.6 <= len(s) / len(tl) <= 1.6):
                    continue
                sc = 0.7 * _sim(s, tl) + 0.3 * _sim(_metaphone(s), _metaphone(tl))
                if sc >= need and (best is None or sc > best[0]):
                    best = (sc, i, k)
        if best:
            _, i, k = best
            used.update(range(i, i + k))
            said = "".join(w[2] for w in words[i:i + k])
            plural = "s" if said.lower().endswith("s") and not t.lower().endswith("s") else ""
            reps.append((words[i][0], words[i + k - 1][1], t + plural))
    changes = []
    for a, b, r in sorted(reps, reverse=True):
        changes.append({"from": text[a:b], "to": r})
        text = text[:a] + r + text[b:]
    return text, changes[::-1]


def _char_py(c: str, tones: bool) -> set[str]:
    from pypinyin import Style, pinyin
    return set(pinyin(c, heteronym=True, style=Style.TONE3 if tones else Style.NORMAL)[0])


def _fix_zh(text: str, zh_terms: list[str]) -> tuple[str, list[dict]]:
    if not CJK.search(text):
        return text, []
    from pypinyin import Style, lazy_pinyin
    changes = []
    for t in sorted(zh_terms, key=len, reverse=True):
        if t in text:
            continue
        n, tones = len(t), len(t) <= 2
        style = Style.TONE3 if tones else Style.NORMAL
        want = lazy_pinyin(t, style=style)
        chars = [(m.start(), m.group(0)) for m in CJK.finditer(text)]
        cands = [{p for p in _char_py(c, tones)} for _, c in chars]
        i = 0
        while i <= len(chars) - n:
            if chars[i + n - 1][0] - chars[i][0] != n - 1 or not all(want[j] in cands[i + j] for j in range(n)):
                i += 1
                continue
            seg = "".join(c for _, c in chars[i:i + n])
            if seg != t and lazy_pinyin(seg, style=style) == want:
                a = chars[i][0]
                changes.append({"from": seg, "to": t})
                text = text[:a] + t + text[a + n:]
                chars = [(m.start(), m.group(0)) for m in CJK.finditer(text)]
                cands = [{p for p in _char_py(c, tones)} for _, c in chars]
            i += n
    return text, changes


def fix(text: str, term_list: list[str]) -> tuple[str, list[dict]]:
    """Recognised text -> (corrected text, [{from, to}] in the order applied)."""
    text = text or ""
    if not text.strip() or not term_list:
        return text, []
    en = [t for t in term_list if not ZH_TERM.match(t)]
    zh = [t for t in term_list if ZH_TERM.match(t)]
    text, a = _fix_en(text, en)
    text, b = _fix_zh(text, zh)
    return text, a + b


def fix_for(conn: psycopg.Connection, user_id: int, text: str) -> dict:
    """{text, fixed: [{from, to}]} with the user's own terms; `raw` only when something changed."""
    out, changes = fix(text, terms(conn, user_id))
    return {"text": out, "fixed": changes, **({"raw": text} if changes else {})}
