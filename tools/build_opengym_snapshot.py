#!/usr/bin/env python3
"""Build the openGym text snapshot (body_engine/src/body_engine/training/opengym/snapshot.json.gz) from a clone of openGym.

    git clone https://github.com/DuarteSantos8/openGym && git -C openGym checkout <commit>
    python3 tools/build_opengym_snapshot.py openGym

Text only: name, body part, equipment, target / secondary muscles, category, description, steps, textSource. Nothing from
`catalogue/media/` (licensed to openGym only) is read, copied or referenced. Variant stubs (`variantOf`, e.g. the female
versions that only point at the main entry) carry no text and are skipped. The clone must be at the pinned commit
(`body_engine/src/body_engine/training/opengym/__init__.py` COMMIT), so a snapshot always says which commit it came from.
"""
import gzip
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "body_engine" / "src" / "body_engine" / "training" / "opengym"
FIELDS = ("id", "name", "bodyPart", "equipment", "target", "secondaryMuscles", "category", "description", "instructions",
          "textSource")


def pinned_commit() -> str:
    for line in (PKG / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("COMMIT"):
            return line.split('"')[1]
    raise SystemExit("COMMIT not found in training/opengym/__init__.py")


def main(clone: Path) -> None:
    head = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if head != pinned_commit():
        raise SystemExit(f"clone is at {head}, the pinned commit is {pinned_commit()}")
    out, stubs, files = [], 0, 0
    for f in sorted((clone / "catalogue" / "exercises").glob("*.json")):
        files += 1
        d = json.loads(f.read_text(encoding="utf-8"))
        if "name" not in d:       # variantOf stub: no text of its own
            stubs += 1
            continue
        out.append({k: d[k] for k in FIELDS if k in d})
    by_source: dict[str, int] = {}
    for d in out:
        by_source[d.get("textSource", "?")] = by_source.get(d.get("textSource", "?"), 0) + 1
    doc = {"meta": {"repo": "https://github.com/DuarteSantos8/openGym", "commit": head, "files": files, "variant_stubs_skipped": stubs,
                    "entries": len(out), "by_text_source": by_source},
           "exercises": out}
    data = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    dest = PKG / "snapshot.json.gz"
    with gzip.GzipFile(filename="", fileobj=open(dest, "wb"), mode="wb", mtime=0, compresslevel=9) as g:   # mtime 0: reproducible bytes
        g.write(data)
    print(f"{dest.relative_to(ROOT)}: {len(out)} entries ({stubs} stubs skipped), {dest.stat().st_size} bytes, {by_source}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
