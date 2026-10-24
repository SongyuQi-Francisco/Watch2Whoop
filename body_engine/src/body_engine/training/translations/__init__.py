"""Machine Chinese for the exercises that only have English text (issue #248): imported free-exercise-db and openGym rows.

- exercises_zh.jsonl: one line per exercise, made offline by tools/translate_exercises.py (never at run time):
  {"slug", "src_sha", "name_zh", "description_zh", "instructions_zh", "zh_source": "machine", "model", "glossary_misses"}.
  src_sha is the hash of the English the line was translated from (src_text): when upstream text changes the line is stale and
  is not applied (the row stays English until the batch is re-run), so a translation never describes a different text.
- glossary.json: the one Chinese rendering of each term (equipment, muscles, positions, movements); the batch gives the model
  the terms of each exercise and reports a name that misses one (glossary_misses), the tests check the committed data.
- apply(row): used by the two importers (catalog.convert_free_exercise_db_entry, opengym.importer.convert), so re-seeding writes
  the same data. A row that already has human Chinese (zh_source 'human': the curated library, a free-exercise-db row merged
  into it) is never touched.
- The page shows machine Chinese only while the rollout flag `exercise_zh_machine` is on for the viewer (catalog.rows_for gives
  english_view(row) otherwise); the data stays in the table either way.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "exercises_zh.jsonl"
CURATED_STEPS = HERE / "curated_steps.json"   # {slug: {"zh": [...], "en": [...]}}: AI-written steps for the curated library
STEPS_FLAG = "exercise_curated_steps"
GLOSSARY = HERE / "glossary.json"
FLAG = "exercise_zh_machine"
CJK = re.compile(r"[一-鿿]")


def has_chinese(s: str | None) -> bool:
    return bool(s and CJK.search(s))


def src_text(row: dict) -> dict:
    """The English an exercise is translated from: name, description, steps (free-exercise-db rows only have steps)."""
    steps = list(row.get("instructions_en") or [])
    if not steps and not any(has_chinese(s) for s in row.get("instructions_zh") or []):
        steps = list(row.get("instructions_zh") or [])
    return {"name": row["name_en"], "description": row.get("description_en") or "", "steps": steps}


def src_sha(row: dict) -> str:
    blob = json.dumps(src_text(row), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def load(path: Path | None = None) -> dict[str, dict]:
    p = path or DATA
    if not p.is_file():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            e = json.loads(line)
            out[e["slug"]] = e
    return out


@functools.cache
def entries() -> dict[str, dict]:
    return load()


def needs_translation(row: dict) -> bool:
    return row.get("zh_source") != "human" and not has_chinese(row.get("name_zh"))


def apply(row: dict, table: dict[str, dict] | None = None) -> str | None:
    """Fill the row's Chinese from the data file, in place. Returns 'applied', 'stale' (text changed since), or None (no line)."""
    if row.get("zh_source") == "human" or has_chinese(row.get("name_zh")) and row.get("zh_source") != "machine":
        return None
    e = (table if table is not None else entries()).get(row["slug"])
    if e is None:
        return None
    if e["src_sha"] != src_sha(row):
        return "stale"
    steps = [s for s in e.get("instructions_zh") or [] if s]
    row.update(name_zh=e["name_zh"], instructions_zh=steps, zh_source="machine",
               cues_zh=e.get("description_zh") or (steps[0] if steps else None))
    return "applied"


def english_view(row: dict) -> dict:
    """The row as it was before machine translation (flag off): English name, no Chinese cue or steps, and `_plain_labels`
    (no Chinese for the equipment names that only got it with #248, catalog_data.EQUIPMENT_ZH_EXTRA)."""
    if row.get("zh_source") != "machine":
        return {**row, "_plain_labels": True}
    return {**row, "name_zh": row["name_en"], "cues_zh": None, "instructions_zh": [], "zh_source": None, "_plain_labels": True}


@functools.cache
def curated_steps() -> dict[str, dict]:
    return json.loads(CURATED_STEPS.read_text(encoding="utf-8")) if CURATED_STEPS.is_file() else {}


def with_curated_steps(row: dict) -> dict:
    """A curated row that only has a one-line cue gets the committed step-by-step instructions in both languages (written by AI
    from the human cue, reviewed; `steps_source` 'ai'). The human name, cue and restriction tags are untouched."""
    st = curated_steps().get(row["slug"])
    if not st or row.get("source") != "curated" or len(row.get("instructions_zh") or []) >= 2:
        return row
    return {**row, "instructions_zh": list(st["zh"]), "instructions_en": list(st["en"]), "steps_source": "ai"}


# ---------------------------------------------------------------- glossary
@functools.cache
def glossary() -> dict[str, tuple[str, ...]]:
    """English term (lower case) -> accepted Chinese renderings (first = preferred), all sections in one namespace."""
    doc = json.loads(GLOSSARY.read_text(encoding="utf-8"))
    out: dict[str, tuple[str, ...]] = {}
    for sec, terms in doc.items():
        if sec.startswith("_"):
            continue
        for en, zh in terms.items():
            out[en.lower()] = tuple(x.strip() for x in zh.split("|") if x.strip())
    return out


@functools.cache
def _term_rx() -> re.Pattern:
    terms = sorted(glossary(), key=len, reverse=True)   # longest first: "romanian deadlift" before "deadlift"
    return re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in terms) + r")(?![a-z0-9])")


def terms_in(text: str) -> list[str]:
    """Glossary terms in an English text, longest match first, each once (overlapping shorter terms are not reported)."""
    return list(dict.fromkeys(m.group(1) for m in _term_rx().finditer((text or "").lower())))


def glossary_misses(name_en: str, name_zh: str) -> list[str]:
    """Terms of the English name whose Chinese (any accepted rendering) is not in the Chinese name."""
    g = glossary()
    return [t for t in terms_in(name_en) if not any(zh in name_zh for zh in g[t])]
