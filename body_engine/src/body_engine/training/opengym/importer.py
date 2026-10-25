"""`body-engine exercises-import-opengym`: put the openGym exercise text (snapshot.json.gz) into exercise_catalog. Idempotent.

For every snapshot entry (name, body part, equipment, muscles, category, description, steps; never media):
- the same exercise twice inside openGym (same name, equipment and target: ExerciseDB has a few) -> one row, both ids kept;
- an exercise the library already has (normalized name equal to a name / alias / slug of a row that is not an openGym import, with
  compatible equipment and primary muscle) -> merged: the row keeps its Chinese name, restriction tags, images and source; only gaps
  are filled (English description and steps, secondary muscles) and openGym's name is added as an alias when it differs;
- otherwise -> a new row `og_<id>` (source opengym / exercisedb, licence AGPL-3.0 / MIT), Chinese name = English name until translated.
Muscles, equipment and category are mapped by mapping.py; names it has no counterpart for are listed in the report, never guessed.
Variants of the curated restricted exercises (upright row, behind-the-neck press / pulldown, dips) get family_slug and joint-stress tags
like every imported exercise (catalog.family_of), so the user's restriction applies to them as well.
"""
from __future__ import annotations

import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .. import catalog, catalog_data as CD, translations as TR
from . import COMMIT, REPO, mapping as M

SNAPSHOT = Path(__file__).with_name("snapshot.json.gz")
IMPORTED_SOURCES = M.IMPORTED_SOURCES
_PATTERN_MUSCLES = {"chest", "lats", "middle back", "shoulders", "quadriceps", "hamstrings", "glutes", "lower back", "calves",
                    "biceps", "triceps", "abdominals"}      # muscles whose movement pattern catalog._infer_pattern can tell


def load_snapshot(path: Path | None = None) -> tuple[dict, list[dict]]:
    """(meta, entries) of the snapshot; refuses a file that was not built from the pinned commit."""
    path = path or SNAPSHOT
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        doc = json.load(f)
    if doc["meta"]["commit"] != COMMIT:
        raise ValueError(f"{path} is from openGym {doc['meta']['commit']}, the pinned commit is {COMMIT}")
    return doc["meta"], doc["exercises"]


def title(name: str) -> str:
    """openGym names are lower case: capitalize each word (small words stay lower case except the first)."""
    words = name.strip().split()
    return " ".join(w if (i and w in M.SMALL_WORDS) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def _family_stress(fam: str | None) -> list[str]:
    return list(next((x.get("joint_stress") or [] for x in CD.CURATED_EXERCISES if x["slug"] == fam), [])) if fam else []


def _dedup(xs) -> list:
    return list(dict.fromkeys(xs))


def convert(e: dict, unmapped: Counter | None = None) -> dict:
    """One snapshot entry -> exercise_catalog row (plus `_ref`: the text_sources element). Unmapped names are counted in `unmapped`."""
    um = unmapped if unmapped is not None else Counter()
    name = title(e["name"])
    target, cat_raw, eq_raw = e["target"], e["category"], e["equipment"]
    cardio = target == M.CARDIO_TARGET
    if cat_raw not in M.CATEGORY:
        um[("category", cat_raw)] += 1
    category = "cardio" if cardio else M.CATEGORY.get(cat_raw, "strength")
    prim = [M.TARGET[target]] if target in M.TARGET else []
    if target not in M.TARGET and not cardio:
        um[("muscle (target)", target)] += 1
    sec = []
    for m in e.get("secondaryMuscles") or []:
        if m in M.SECONDARY:
            sec.append(M.SECONDARY[m])
        else:
            um[("muscle (secondary)", m)] += 1
    sec = [m for m in _dedup(sec) if m not in prim]
    eq = M.EQUIPMENT.get(eq_raw)
    if eq is None:
        um[("equipment", eq_raw)] += 1
        eq = eq_raw
    duration = cardio or cat_raw in M.DURATION_CATEGORIES
    modality = "duration" if duration else "bodyweight_reps" if eq == "body only" else "reps_only" if eq in ("bands", "foam roll") else "weight_reps"
    if category == "cardio":
        pattern = "cardio"
    elif category in ("stretching", "mobility"):
        pattern = "mobility"
    elif prim and prim[0] in _PATTERN_MUSCLES:
        pattern = catalog._infer_pattern({"category": "strength", "primaryMuscles": prim, "name": name})
    else:
        pattern = None
    source, licence = M.LICENSES[e.get("textSource") or "opengym"]
    fam = catalog.family_of(name)
    steps = [str(s).strip() for s in e.get("instructions") or [] if str(s).strip()]
    ref = {"source": source, "license": licence, "ref": e["id"], "commit": COMMIT, "body_part": e.get("bodyPart"),
           "target": target, "category": cat_raw, "equipment": eq_raw}
    row = {
        "slug": f"og_{e['id']}", "name_zh": name, "name_en": name, "aliases": [], "category": category, "modality": modality,
        "force": None, "mechanic": None, "level": "intermediate", "movement_pattern": pattern, "equipment": [eq],
        "primary_muscles": prim, "secondary_muscles": sec, "joint_stress": _family_stress(fam), "per_side": False,
        "default_increment_kg": 2.5 if modality == "weight_reps" else None, "cues_zh": None, "instructions_zh": [], "images": [],
        "source": source, "source_ref": e["id"], "family_slug": fam, "license": licence,
        "description_en": (e.get("description") or "").strip() or None, "instructions_en": steps, "text_sources": [ref],
        "zh_source": None,
    }
    TR.apply(row)   # the committed machine Chinese (training/translations/), when it was made from this very English
    return row


def _group(entries: list[dict]) -> list[list[dict]]:
    """Entries that are the same exercise (name, equipment, target) -> one group each, ordered by id."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for e in sorted(entries, key=lambda x: x["id"]):
        groups[(catalog.norm(e["name"]), e["equipment"], e["target"])].append(e)
    return list(groups.values())


def _compatible(row: dict, new: dict, *, strict_equipment: bool = False) -> bool:
    """Equipment and primary muscle do not contradict (a side that says nothing, or the row's "other", does not contradict).
    strict_equipment: the row must name the same equipment (for matches made on a name with the equipment word stripped)."""
    r_eq, r_prim = set(row.get("equipment") or []), set(row.get("primary_muscles") or [])
    eq_ok = bool(r_eq & set(new["equipment"])) if strict_equipment else (not r_eq or "other" in r_eq or bool(r_eq & set(new["equipment"])))
    mu_ok = not r_prim or not new["primary_muscles"] or bool(r_prim & set(new["primary_muscles"]))
    return eq_ok and mu_ok


def _stripped_name(e: dict) -> str | None:
    """The openGym name without its leading equipment word ("cable lat pulldown" -> "lat pulldown"), None when it has none."""
    name = e["name"].lower().strip()
    for pre in M.NAME_PREFIX.get(e["equipment"], ()):
        if name.startswith(pre + " "):
            return name[len(pre) + 1:]
    return None


def _merge_sources(old: list[dict], extra: list[dict]) -> list[dict]:
    have = {(s.get("source"), s.get("ref")) for s in old}
    return [*old, *(s for s in extra if (s["source"], s["ref"]) not in have)]


_PRIORITY = {"curated": 0, "free-exercise-db": 1}

_INSERT = """INSERT INTO exercise_catalog (
      slug, name_zh, name_en, aliases, category, modality, force, mechanic, level, movement_pattern, equipment, primary_muscles,
      secondary_muscles, joint_stress, per_side, default_increment_kg, cues_zh, instructions_zh, images, source, source_ref,
      family_slug, license, description_en, instructions_en, text_sources, zh_source, updated_at)
    VALUES (%(slug)s, %(name_zh)s, %(name_en)s, %(aliases)s, %(category)s, %(modality)s, %(force)s, %(mechanic)s, %(level)s,
      %(movement_pattern)s, %(equipment)s, %(primary_muscles)s, %(secondary_muscles)s, %(joint_stress)s, %(per_side)s,
      %(default_increment_kg)s, %(cues_zh)s, %(instructions_zh)s, %(images)s, %(source)s, %(source_ref)s, %(family_slug)s,
      %(license)s, %(description_en)s, %(instructions_en)s, %(text_sources)s, %(zh_source)s, now())
    ON CONFLICT (slug) DO UPDATE SET
      name_zh = CASE WHEN exercise_catalog.zh_source IS DISTINCT FROM 'human' THEN EXCLUDED.name_zh ELSE exercise_catalog.name_zh END,
      cues_zh = CASE WHEN exercise_catalog.zh_source IS DISTINCT FROM 'human' THEN EXCLUDED.cues_zh ELSE exercise_catalog.cues_zh END,
      instructions_zh = CASE WHEN exercise_catalog.zh_source IS DISTINCT FROM 'human' THEN EXCLUDED.instructions_zh
                             ELSE exercise_catalog.instructions_zh END,
      zh_source = CASE WHEN exercise_catalog.zh_source IS DISTINCT FROM 'human' THEN EXCLUDED.zh_source ELSE 'human' END,
      name_en = EXCLUDED.name_en, category = EXCLUDED.category, modality = EXCLUDED.modality,
      movement_pattern = EXCLUDED.movement_pattern, equipment = EXCLUDED.equipment, primary_muscles = EXCLUDED.primary_muscles,
      secondary_muscles = EXCLUDED.secondary_muscles, joint_stress = EXCLUDED.joint_stress, family_slug = EXCLUDED.family_slug,
      source = EXCLUDED.source, source_ref = EXCLUDED.source_ref, license = EXCLUDED.license,
      description_en = EXCLUDED.description_en, instructions_en = EXCLUDED.instructions_en, text_sources = EXCLUDED.text_sources,
      updated_at = now()
    WHERE (exercise_catalog.name_en, exercise_catalog.category, exercise_catalog.modality, exercise_catalog.movement_pattern,
           exercise_catalog.equipment, exercise_catalog.primary_muscles, exercise_catalog.secondary_muscles,
           exercise_catalog.joint_stress, exercise_catalog.family_slug, exercise_catalog.source, exercise_catalog.license,
           exercise_catalog.description_en, exercise_catalog.instructions_en, exercise_catalog.text_sources,
           CASE WHEN exercise_catalog.zh_source = 'human' THEN NULL ELSE
             (exercise_catalog.name_zh, exercise_catalog.cues_zh, exercise_catalog.instructions_zh, exercise_catalog.zh_source) END)
      IS DISTINCT FROM (EXCLUDED.name_en, EXCLUDED.category, EXCLUDED.modality, EXCLUDED.movement_pattern, EXCLUDED.equipment,
           EXCLUDED.primary_muscles, EXCLUDED.secondary_muscles, EXCLUDED.joint_stress, EXCLUDED.family_slug, EXCLUDED.source,
           EXCLUDED.license, EXCLUDED.description_en, EXCLUDED.instructions_en, EXCLUDED.text_sources,
           CASE WHEN exercise_catalog.zh_source = 'human' THEN NULL ELSE
             (EXCLUDED.name_zh, EXCLUDED.cues_zh, EXCLUDED.instructions_zh, EXCLUDED.zh_source) END)"""


def run(conn: psycopg.Connection, snapshot: tuple[dict, list[dict]] | None = None, *, dry_run: bool = False) -> dict:
    """Import (or, with dry_run, only plan) the snapshot. Returns the report; nothing is written when dry_run."""
    meta, entries = snapshot or load_snapshot()
    unmapped: Counter = Counter()
    existing = [r for r in catalog.catalog_rows(conn) if r["source"] not in IMPORTED_SOURCES and r.get("source") != "user_custom"]
    by_name: dict[str, list[dict]] = defaultdict(list)
    for r in existing:
        for n in r["_names"]:
            by_name[n].append(r)
    og_slugs = {r["slug"] for r in conn.execute("SELECT slug FROM exercise_catalog WHERE slug LIKE 'og\\_%'")}
    total_before = conn.execute("SELECT count(*) AS n FROM exercise_catalog").fetchone()["n"]

    new_rows: list[dict] = []
    merges: dict[str, dict] = {}     # existing slug -> {"row": row, "refs": [...], "new": converted}
    merged_pairs, merged_how = Counter(), Counter()
    internal_dups = 0
    for group in _group(entries):
        conv = convert(group[0], unmapped)
        for extra in group[1:]:
            c2 = convert(extra, unmapped)       # counted, but the text of the first entry stands
            conv["text_sources"] = _merge_sources(conv["text_sources"], c2["text_sources"])
            internal_dups += 1
        cands = [r for r in by_name.get(catalog.norm(group[0]["name"]), []) if _compatible(r, conv)]
        how = "name"
        stripped = _stripped_name(group[0])
        if not cands and stripped:
            cands = [r for r in by_name.get(catalog.norm(stripped), []) if _compatible(r, conv, strict_equipment=True)]
            how = "name without equipment word"
        if conv["slug"] not in og_slugs and cands:
            target = sorted(cands, key=lambda r: (_PRIORITY.get(r["source"], 9), r["slug"]))[0]
            m = merges.setdefault(target["slug"], {"row": target, "new": conv, "refs": []})
            m["refs"] = _merge_sources(m["refs"], conv["text_sources"])
            merged_pairs[target["source"]] += 1
            merged_how[how] += 1
        else:
            new_rows.append(conv)

    updated_existing = 0
    if not dry_run:
        with conn.transaction():
            with conn.cursor() as cur:
                for conv in new_rows:
                    cur.execute(_INSERT, {**conv, "text_sources": Jsonb(conv["text_sources"])})
                for slug, m in merges.items():
                    row, conv = m["row"], m["new"]
                    cur_row = conn.execute("SELECT slug, name_zh, name_en, cues_zh, instructions_zh, zh_source, aliases, secondary_muscles, "
                                           "description_en, instructions_en, text_sources FROM exercise_catalog WHERE slug = %s",
                                           (slug,)).fetchone()
                    aliases = list(cur_row["aliases"])
                    nm = catalog.norm(conv["name_en"])
                    if nm not in row["_names"] and nm not in {catalog.norm(a) for a in aliases}:
                        aliases.append(conv["name_en"])
                    want = {
                        "aliases": aliases,
                        "secondary_muscles": list(cur_row["secondary_muscles"]) or conv["secondary_muscles"],
                        "description_en": cur_row["description_en"] or conv["description_en"],
                        "instructions_en": list(cur_row["instructions_en"]) or conv["instructions_en"],
                        "text_sources": _merge_sources(cur_row["text_sources"], m["refs"]),
                    }
                    # the merge can add English (a description) to a row without human Chinese: its machine Chinese is the line
                    # made from that final English (translations/), so apply it here too
                    zh = {k: cur_row[k] for k in ("name_zh", "cues_zh", "instructions_zh", "zh_source")}
                    if cur_row["zh_source"] != "human":
                        cand = {**cur_row, **want, "instructions_zh": list(cur_row["instructions_zh"])}
                        if cur_row["zh_source"] == "machine":   # judge by the English, as if no Chinese had been applied yet
                            cand.update(name_zh=cand["name_en"], zh_source=None)
                        if TR.apply(cand) == "applied":
                            zh = {k: cand[k] for k in zh}
                    want.update(zh)
                    if all(want[k] == cur_row[k] for k in want):
                        continue
                    cur.execute("""UPDATE exercise_catalog SET aliases = %s, secondary_muscles = %s, description_en = %s,
                                   instructions_en = %s, text_sources = %s, name_zh = %s, cues_zh = %s, instructions_zh = %s,
                                   zh_source = %s, updated_at = now() WHERE slug = %s""",
                                (want["aliases"], want["secondary_muscles"], want["description_en"], want["instructions_en"],
                                 Jsonb(want["text_sources"]), want["name_zh"], want["cues_zh"], want["instructions_zh"],
                                 want["zh_source"], slug))
                    updated_existing += 1
    by_src = Counter(c["source"] for c in new_rows)
    skipped_stubs = meta.get("variant_stubs_skipped")
    return {
        "dry_run": dry_run, "source": REPO, "commit": COMMIT, "entries": len(entries), "variant_stubs_skipped": skipped_stubs,
        "internal_duplicates_merged": internal_dups, "new_rows": len(new_rows), "new_by_source": dict(by_src),
        "machine_chinese": sum(1 for c in new_rows if c.get("zh_source") == "machine"),
        "merged_into_existing": sum(merged_pairs.values()), "merged_by_existing_source": dict(merged_pairs), "merged_by_match": dict(merged_how),
        "existing_rows_updated": len(merges) if dry_run else updated_existing,   # dry run: rows an import would look at
        "unmapped": [{"kind": k, "name": n, "entries": c} for (k, n), c in sorted(unmapped.items(), key=lambda kv: (kv[0][0], -kv[1]))],
        "unmapped_entries": sum(unmapped.values()),
        "catalog_total": total_before + sum(1 for c in new_rows if c["slug"] not in og_slugs),
    }


def format_report(rep: dict) -> str:
    lines = [f"openGym {rep['commit'][:10]}: {rep['entries']} entries (+{rep['variant_stubs_skipped']} variant stubs skipped)"
             + (" [dry run]" if rep["dry_run"] else ""),
             f"  new rows {rep['new_rows']} {rep['new_by_source']}, merged into existing {rep['merged_into_existing']} "
             f"{rep['merged_by_existing_source']}, duplicates inside openGym {rep['internal_duplicates_merged']}, "
             f"existing rows touched {rep['existing_rows_updated']}, catalog total {rep['catalog_total']}, "
             f"with machine Chinese {rep['machine_chinese']}"]
    if rep["unmapped"]:
        lines.append(f"  unmapped ({len(rep['unmapped'])} names, {rep['unmapped_entries']} uses; kept as raw / left out):")
        lines += [f"    {u['kind']}: {u['name']} ×{u['entries']}" for u in rep["unmapped"]]
    return "\n".join(lines)
