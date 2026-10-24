#!/usr/bin/env python3
"""How many exercises pass the quality grade (training/quality.py) under different assumptions (issue #248). Offline, read-only.

    cd body_engine && uv run python ../tools/exercise_quality_report.py

The library is rebuilt in a throwaway body_test_ database (as tools/translate_exercises.py does). Scenarios:
- media: "free-exercise-db images" counts the images every deployment downloads today, "licensed only" counts only the
  high-confidence wger / Everkinetic matches in training/media_matches.json;
- text: "today" uses the Chinese the rows have (human + the committed machine lines), "translated" assumes every row with
  English has machine Chinese (what the full batch gives).
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

from body_engine.training import quality as Q  # noqa: E402

MATCHES = ROOT / "body_engine" / "src" / "body_engine" / "training" / "media_matches.json"


def pretend_translated(r: dict) -> dict:
    if Q.CJK.search(r["name_zh"] or ""):
        return r
    steps = r.get("instructions_en") or r.get("instructions_zh") or []
    return {**r, "name_zh": "译" + r["name_en"], "instructions_zh": ["译" + s for s in steps], "instructions_en": steps,
            "cues_zh": "译" if r.get("description_en") else r.get("cues_zh"), "zh_source": "machine"}


def run(rows: list[dict], licensed: set[str], free_db_ok: bool, translated: bool) -> dict:
    rows = [pretend_translated(r) for r in rows] if translated else rows

    def media(r):
        return r["slug"] in licensed or (free_db_ok and bool(r.get("images")))
    dups = Q.duplicates(rows, media)
    grades = {r["slug"]: Q.grade(r, media(r), dups.get(r["slug"])) for r in rows}
    passed = Counter(r["source"] for r in rows if grades[r["slug"]]["passed"])
    miss = Counter(m for g in grades.values() if not g["passed"] for m in g["missing"] if m in Q.MUST)
    buckets = Counter(min(g["score"] // 10 * 10, 90) for g in grades.values())
    return {"passed": sum(passed.values()), "passed_by_source": dict(passed), "failed": len(rows) - sum(passed.values()),
            "failed_missing_must": dict(miss.most_common()), "duplicates": len(dups),
            "variants": sum(1 for g in grades.values() if g["variant"]), "score_buckets": dict(sorted(buckets.items()))}


def main(argv: list[str] | None = None) -> int:
    from translate_exercises import build_rows, _db_name
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default="body_test_quality_report", type=_db_name)
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "media")
    a = ap.parse_args(argv)
    rows = build_rows(a.db, a.cache_dir)
    doc = json.loads(MATCHES.read_text(encoding="utf-8"))
    licensed = {m["slug"] for m in doc["matches"] if m["confidence"] == "high" and (m["images_usable"] or m["videos"])}
    out = {"library": len(rows)}
    for media_name, fdb in (("free-exercise-db images", True), ("licensed only", False)):
        for text in ("today", "translated"):
            out[f"{media_name} / {text}"] = run(rows, licensed, fdb, text == "translated")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
