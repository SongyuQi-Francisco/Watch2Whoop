#!/usr/bin/env python3
"""Match the exercise library against openly licensed demo-media sources and report coverage (issue #248). Offline, read-only.

    cd body_engine && uv run python ../tools/match_exercise_media.py --wger wger_exerciseinfo.json --everkinetic everkinetic.json

Inputs (metadata only, no image is downloaded):
- wger: the public API's /api/v2/exerciseinfo/ pages concatenated into one JSON list (per-image licence, author, AI flag).
  An image counts as usable when it is not AI-generated and names an author (CC-BY-SA needs one); others are listed apart.
- Everkinetic (github.com/everkinetic/data, CC-BY-SA 4.0): exercises.json at the commit given with --everkinetic-commit.
The library is rebuilt in a throwaway body_test_ database exactly as tools/translate_exercises.py does.

Matching: names are normalized (case, hyphens, plurals, synonyms such as db -> dumbbell, lever -> machine, flyes -> fly), the
equipment word of either side is folded into the token set, then
  score = name similarity (1.0 same tokens in order, 0.97 same token set, else token Jaccard)
          × 0.5 when the equipment contradicts × 0.7 when the primary muscle contradicts;
  high >= 0.95 (attached automatically by a later step), medium 0.80–0.95 (a person decides), low below (not listed).
Output: body_engine/src/body_engine/training/media_matches.json (best candidate per exercise and source, high and medium only)
and the coverage numbers on stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "body_engine" / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from body_engine.training.quality import tokens_with_equipment  # noqa: E402  (one normalization for matching and grading)

OUT = ROOT / "body_engine" / "src" / "body_engine" / "training" / "media_matches.json"
HIGH, MEDIUM = 0.95, 0.80

# source equipment -> library equipment key (catalog_data.EQUIPMENT_ZH keys); None = says nothing about the load
WGER_EQUIP = {"Barbell": "barbell", "Dumbbell": "dumbbell", "Kettlebell": "kettlebell", "SZ-Bar": "ez curl bar", "Cable machine": "cable",
              "Resistance band": "bands", "Swiss Ball": "exercise ball", "none (bodyweight exercise)": "body only", "Pull-up bar": "body only",
              "Bench": None, "Incline bench": None, "Gym mat": None}
EK_EQUIP = {"barbell": "barbell", "dumbbell": "dumbbell", "dumbbells": "dumbbell", "kettlebell": "kettlebell", "cable": "cable",
            "machine": "machine", "ez-bar": "ez curl bar", "ez bar": "ez curl bar", "bodyweight": "body only", "band": "bands",
            "smith machine": "smith machine", "bench": None, "mat": None, "pull-up bar": "body only"}
WGER_MUSCLE = {"Glutes": "glutes", "Shoulders": "shoulders", "Lats": "lats", "Quads": "quadriceps", "Chest": "chest", "Abs": "abdominals",
               "Biceps": "biceps", "Triceps": "triceps", "Trapezius": "traps", "Hamstrings": "hamstrings", "Calves": "calves",
               "Obliquus externus abdominis": "abdominals", "Brachialis": "biceps", "Serratus anterior": None, "Soleus": "calves"}
EK_MUSCLE = {"chest": "chest", "back": "lats", "lats": "lats", "shoulders": "shoulders", "biceps": "biceps", "triceps": "triceps",
             "abs": "abdominals", "abdominals": "abdominals", "quadriceps": "quadriceps", "quads": "quadriceps", "hamstrings": "hamstrings",
             "glutes": "glutes", "calves": "calves", "forearms": "forearms", "traps": "traps", "lower back": "lower back",
             "middle back": "middle back", "upper back": "middle back", "neck": "neck", "adductors": "adductors", "abductors": "abductors"}
# library equipment keys that are interchangeable for matching (a curl bar is a barbell; a "machine" includes the smith machine)
EQUIP_FAMILY = {"ez curl bar": "barbell", "smith machine": "machine", "body only": "body only"}


def name_sim(a: list[str], b: list[str]) -> float:
    if a == b:
        return 1.0
    sa, sb = set(a), set(b)
    if sa == sb:
        return 0.97
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def _fam(e: str) -> str:
    return EQUIP_FAMILY.get(e, e)


def equipment_conflict(ours: list[str], theirs: list[str]) -> bool:
    a = {_fam(e) for e in ours if e and e != "other"}
    b = {_fam(e) for e in theirs if e}
    return bool(a and b and not a & b)


def muscle_conflict(ours: list[str], theirs: list[str]) -> bool:
    return bool(ours and theirs and not set(ours) & set(theirs))


def score(row: dict, cand: dict) -> float:
    s = name_sim(tokens_with_equipment(row["name_en"], row.get("equipment") or []), cand["tokens"])
    if equipment_conflict(row.get("equipment") or [], cand["equipment"]):
        s *= 0.5
    if muscle_conflict(row.get("primary_muscles") or [], cand["muscles"]):
        s *= 0.7
    return round(s, 3)


# ---------------------------------------------------------------- sources
def wger_candidates(path: Path) -> list[dict]:
    out = []
    for x in json.loads(path.read_text(encoding="utf-8")):
        imgs = x.get("images") or []
        vids = x.get("videos") or []
        if not imgs and not vids:
            continue
        names = [t["name"] for t in x.get("translations") or [] if t.get("language") == 2]   # 2 = English
        names += [a["alias"] for t in x.get("translations") or [] if t.get("language") == 2 for a in t.get("aliases") or [] if a.get("alias")]
        if not names:
            continue
        eq = [WGER_EQUIP.get(e["name"]) for e in x.get("equipment") or []]
        eq = [e for e in eq if e]
        mus = [WGER_MUSCLE.get(m.get("name_en") or m.get("name")) for m in x.get("muscles") or []]
        usable = [i for i in imgs if not i.get("is_ai_generated") and (i.get("license_author") or i.get("author_history"))]
        unclear = [i for i in imgs if i not in usable and not i.get("is_ai_generated")]
        ai = [i for i in imgs if i.get("is_ai_generated")]
        lic = sorted({i["license"] for i in usable} | {v["license"] for v in vids})
        for n in dict.fromkeys(names):
            out.append({"source": "wger", "id": x["id"], "uuid": x["uuid"], "name": n, "equipment": eq, "muscles": [m for m in mus if m],
                        "tokens": tokens_with_equipment(n, eq), "images_usable": len(usable), "images_no_author": len(unclear),
                        "images_ai": len(ai), "videos": len(vids), "licenses": lic})
    return out


def everkinetic_candidates(path: Path) -> list[dict]:
    out = []
    for x in json.loads(path.read_text(encoding="utf-8")):
        if not x.get("img") and not x.get("images"):
            continue
        eq = [EK_EQUIP.get(e.lower()) for e in x.get("equipment") or []]
        eq = [e for e in eq if e] or ["body only"]
        mus = [EK_MUSCLE.get((x.get("primary") or "").lower())]
        out.append({"source": "everkinetic", "id": x["id_num"], "name": x["title"], "equipment": eq, "muscles": [m for m in mus if m],
                    "tokens": tokens_with_equipment(x["title"], eq), "images_usable": len(x.get("img") or x.get("images") or []),
                    "images_no_author": 0, "images_ai": 0, "videos": 0, "licenses": ["CC-BY-SA-4.0"]})
    return out


def best_matches(rows: list[dict], cands: list[dict]) -> list[dict]:
    by_tok: dict[str, list[dict]] = {}
    for c in cands:
        for t in set(c["tokens"]):
            by_tok.setdefault(t, []).append(c)
    out = []
    for r in rows:
        toks = set(tokens_with_equipment(r["name_en"], r.get("equipment") or []))
        pool = {id(c): c for t in toks for c in by_tok.get(t, [])}.values()
        best = None
        for c in pool:
            s = score(r, c)
            if s >= MEDIUM and (best is None or s > best[0] or (s == best[0] and c["images_usable"] > best[1]["images_usable"])):
                best = (s, c)
        if best:
            s, c = best
            out.append({"slug": r["slug"], "name_en": r["name_en"], "source": c["source"], "source_id": c["id"],
                        "source_name": c["name"], "score": s, "confidence": "high" if s >= HIGH else "medium",
                        "images_usable": c["images_usable"], "images_no_author": c["images_no_author"], "videos": c["videos"],
                        "licenses": c["licenses"]})
    return out


def main(argv: list[str] | None = None) -> int:
    from translate_exercises import build_rows, _db_name
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--wger", type=Path, required=True)
    ap.add_argument("--everkinetic", type=Path, required=True)
    ap.add_argument("--everkinetic-commit", required=True)
    ap.add_argument("--db", default="body_test_media_match", type=_db_name)
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "media")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)

    rows = build_rows(a.db, a.cache_dir)
    cands = {"wger": wger_candidates(a.wger), "everkinetic": everkinetic_candidates(a.everkinetic)}
    matches = {src: best_matches(rows, c) for src, c in cands.items()}
    have_img = {m["slug"] for src in matches for m in matches[src]
                if m["confidence"] == "high" and (m["images_usable"] or m["videos"])}
    have_img_med = {m["slug"] for src in matches for m in matches[src] if m["images_usable"] or m["videos"]}
    by_source_row = Counter(r["source"] for r in rows)
    cov = Counter(r["source"] for r in rows if r["slug"] in have_img)
    report = {
        "library": len(rows), "library_by_source": dict(by_source_row),
        "candidates": {s: len({c["id"] for c in cs}) for s, cs in cands.items()},
        "matches": {s: dict(Counter(m["confidence"] for m in ms)) for s, ms in matches.items()},
        "covered_high": len(have_img), "covered_high_by_source": dict(cov), "covered_high_or_medium": len(have_img_med),
        "missing_after_high": len(rows) - len(have_img),
        "source_items_used_high": {s: len({m["source_id"] for m in ms if m["confidence"] == "high"}) for s, ms in matches.items()},
    }
    doc = {"_about": "Proposed demo-media matches (tools/match_exercise_media.py, issue #248). high = attach automatically, "
                     "medium = a person decides. Metadata only: no image is in the repository.",
           "everkinetic_commit": a.everkinetic_commit, "report": report,
           "matches": sorted([m for ms in matches.values() for m in ms], key=lambda m: (m["slug"], m["source"]))}
    a.out.write_text(json.dumps(doc, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
