"""Exercise quality grade (issue #248): which library rows are good enough to be offered. Rules: docs/exercise-library-quality.md.

grade(row, ...) -> {"score": 0–100, "passed": bool, "missing": [...], "variant": bool, "duplicate_of": slug | None}
- score: bilingual names 20, steps in both languages 20 (only a description / one-line cue in both: 10), demo media with a
  licence we may use 25,
  primary muscle 15, secondary muscles 5, equipment we have a label for 5, not a variant / duplicate 10.
- passed: score >= PASS_SCORE and every MUST item present. Rows that do not pass stay in the library (found by exact name,
  logged workouts still resolve) but are not offered in search results, recommendations or alternatives.
Pure functions: the caller says which media counts (media_ok) and which rows are duplicates (duplicates()).
Names are compared with norm_tokens / tokens_with_equipment, the same normalization tools/match_exercise_media.py uses.
"""
from __future__ import annotations

import re

from . import catalog_data as CD

PASS_SCORE = 70
MUST = ("names", "text", "media", "primary_muscle", "not_duplicate")   # all required for passed
POINTS = {"names": 20, "steps": 20, "media": 25, "primary_muscle": 15, "secondary_muscles": 5, "equipment": 5, "not_variant": 10}
# Upstream naming of camera angles, model versions and checks of one movement (ExerciseDB / openGym): a variant of another row.
VARIANT_RX = re.compile(r"\bv\.?\s*\d\b|\((?:fe)?male\)|\b(?:fe)?male\b|form check|\bpov\b", re.I)
SOURCE_PRIORITY = {"curated": 0, "free-exercise-db": 1, "exercisedb": 2, "opengym": 3}
CJK = re.compile(r"[一-鿿]")
NO_MUSCLE_CATEGORIES = {"cardio"}      # whole-body work: no primary muscle needed

SYN = [  # applied in order on the lower-cased, hyphen-free name
    (r"\bdb\b", "dumbbell"), (r"\bbb\b", "barbell"), (r"\bkb\b", "kettlebell"), (r"\bsz bar\b", "ez bar"),
    (r"\bez (?:curl )?bar(?:bell)?\b", "ez bar"), (r"\be z\b", "ez"), (r"\bleverage\b|\blever\b", "machine"),
    (r"\bpush ?ups?\b|\bpress ?ups?\b", "pushup"), (r"\bpull ?ups?\b", "pullup"), (r"\bchin ?ups?\b", "chinup"),
    (r"\bsit ?ups?\b", "situp"), (r"\bstep ?ups?\b", "stepup"), (r"\bpull ?downs?\b", "pulldown"), (r"\bpush ?downs?\b", "pushdown"),
    (r"\bflyes\b|\bflys\b|\bflye\b|\bflies\b", "fly"), (r"\bbiceps?\b", "biceps"), (r"\btriceps?\b", "triceps"),
    (r"\b(?:one|1|single) arm\b", "singlearm"), (r"\b(?:one|1|single) leg(?:ged)?\b", "singleleg"),
    (r"\balternat(?:e|ing)\b", "alternate"), (r"\bresistance band\b|\bbanded\b", "band"),
    (r"\bstability ball\b|\bswiss ball\b|\bexercise ball\b", "ball"),
    (r"\bromanian deadlift\b|\brdl\b", "rdl"), (r"\bstiff(?: leg(?:ged)?)? deadlift\b", "stiffdeadlift"),
    (r"\bshoulder press\b|\bmilitary press\b|\boverhead press\b", "ohpress"), (r"\bbent over\b", "bentover"),
    (r"\bclose grip\b|\bnarrow grip\b", "closegrip"), (r"\bwide grip\b", "widegrip"), (r"\breverse grip\b|\bunderhand\b", "reversegrip"),
    (r"\bdumbbells\b", "dumbbell"), (r"\bkettlebells\b", "kettlebell"), (r"\bbands\b", "band"), (r"\bcable machine\b", "cable"),
    (r"\bbody ?weight\b", ""),
]
STOP = {"the", "a", "an", "on", "with", "to", "of", "and", "in", "for", "at", "exercise", "movement", "version", "v"}
_PLURAL = re.compile(r"(?<=[a-z]{3})s\b")
EQUIP_WORDS = {"barbell", "dumbbell", "kettlebell", "cable", "machine", "smith", "band", "ball", "ez", "trx", "sled", "landmine"}


def norm_tokens(name: str) -> list[str]:
    s = re.sub(r"[()\[\],./'’]", " ", (name or "").lower().replace("-", " ").replace("°", " deg "))
    for rx, rep in SYN:
        s = re.sub(rx, rep, s)
    s = _PLURAL.sub("", s)
    return [t for t in s.split() if t and t not in STOP]


def tokens_with_equipment(name: str, equipment: list[str]) -> list[str]:
    """Name tokens plus the equipment word when the name leaves it out ("Bench Press" + barbell = "Barbell Bench Press")."""
    toks = norm_tokens(name)
    for e in equipment:
        for t in norm_tokens(e.replace("ez curl bar", "ez bar").replace("body only", "")):
            if t in EQUIP_WORDS and t not in toks:
                toks.append(t)
    return toks


def is_variant(row: dict) -> bool:
    return bool(VARIANT_RX.search(row.get("name_en") or ""))


def _key(row: dict) -> tuple:
    return tuple(sorted(set(tokens_with_equipment(re.sub(VARIANT_RX, " ", row.get("name_en") or ""), row.get("equipment") or []))))


def duplicates(rows: list[dict], media_ok=None) -> dict[str, str]:
    """slug -> the slug it duplicates: same normalized name (variant markers ignored) and equipment. The row that stays is the
    best of the group: human Chinese, then with usable media, then source priority, then without a variant marker, then slug."""
    media_ok = media_ok or (lambda r: False)
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get("source") == "user_custom":
            continue
        groups.setdefault(_key(r), []).append(r)
    out = {}
    for g in groups.values():
        if len(g) < 2:
            continue
        g = sorted(g, key=lambda r: (r.get("zh_source") != "human", not media_ok(r), SOURCE_PRIORITY.get(r.get("source"), 9),
                                     is_variant(r), r["slug"]))
        for r in g[1:]:
            out[r["slug"]] = g[0]["slug"]
    return out


def _steps_ok(row: dict) -> tuple[bool, bool]:
    """(both languages have >= 2 steps, at least a description / cue in both languages)."""
    zh = [s for s in row.get("instructions_zh") or [] if CJK.search(s or "")]
    en = list(row.get("instructions_en") or [])
    if not en and row.get("source") == "curated":   # curated English steps live in catalog_en (or the one-line cue)
        from . import catalog_en as CE
        en = CE.INSTRUCTIONS_EN.get(row["slug"]) or ([CE.CUES_EN[row["slug"]]] if row["slug"] in CE.CUES_EN else [])
    full = len(zh) >= 2 and len(en) >= 2
    some = bool((zh or CJK.search(row.get("cues_zh") or "")) and (en or row.get("description_en")))
    return full, some


def grade(row: dict, media_ok: bool, duplicate_of: str | None = None) -> dict:
    have = {
        "names": bool(CJK.search(row.get("name_zh") or "")) and bool(row.get("name_en")),
        "media": media_ok,
        "primary_muscle": bool(row.get("primary_muscles")) or row.get("category") in NO_MUSCLE_CATEGORIES,
        "secondary_muscles": bool(row.get("secondary_muscles")) or row.get("category") in NO_MUSCLE_CATEGORIES,
        "equipment": bool(row.get("equipment")) and all(e in CD.EQUIPMENT_ZH or e in CD.EQUIPMENT_ZH_EXTRA for e in row["equipment"]),
        "not_duplicate": duplicate_of is None,
    }
    full, some = _steps_ok(row)
    have["steps"], have["text"] = full, full or some
    variant = is_variant(row)
    have["not_variant"] = not variant
    score = sum(p for k, p in POINTS.items() if have.get(k)) + (POINTS["steps"] // 2 if some and not full else 0)
    missing = [k for k in (*MUST, "steps", "secondary_muscles", "equipment", "not_variant") if not have[k]]
    return {"score": score, "passed": score >= PASS_SCORE and all(have[k] for k in MUST), "missing": missing,
            "variant": variant, "duplicate_of": duplicate_of}
