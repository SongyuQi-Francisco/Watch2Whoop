"""Content and versions of the static data bundle.

Modules (each is replaced or updated as a unit):
- exercises: the shared exercise_catalog rows (row-level delta by `rev`; custom exercises and per-user exercise_meta are NOT in it);
- taxonomy: muscle / equipment / body-group / movement-pattern / joint-stress labels (zh + en) and the default restriction slugs;
- glossary: the explanation texts (explain/glossary*.py, both languages) for metrics, labs, status items and conditions;
- split_templates: the built-in 3 / 4 / 5-day training splits (training/params.SPLIT_TEMPLATES).
`version` = the highest rev of any change (migration 730): it only goes up, so "give me what changed since N" is exact.
Exercise rows are compact: a key that is missing means empty / null. Texts that exist in both languages are separate fields
(`name_zh` / `name_en`, `cues_zh` / `cues_en`, `instructions_zh` / `instructions_en`); the app picks by interface language.
The `_zh` fields hold Chinese text only: a row that has no Chinese yet (imported ones: the library keeps the English in the Chinese
columns until a translation exists) has just `name_en` / `cues_en` / `instructions_en`, and the app falls back to them.
"""
from __future__ import annotations

import functools
import gzip
import hashlib
import json
import re

import psycopg

from ..explain import glossary as G, glossary_en as GE
from ..training import catalog, catalog_data as CD, catalog_en as CE, custom, muscle_load, params as P
from ..training.opengym import COMMIT as OPENGYM_COMMIT, REPO as OPENGYM_REPO, mapping as OGM
from . import FORMAT

CODE_MODULES = ("taxonomy", "glossary", "split_templates")
MODULES = ("exercises", *CODE_MODULES)

ATTRIBUTION = [
    {"name": "free-exercise-db", "source": "free-exercise-db", "license": "Unlicense", "url": "https://github.com/yuhonas/free-exercise-db"},
    {"name": "openGym (exercise text only)", "source": "opengym", "license": "AGPL-3.0", "url": OPENGYM_REPO, "commit": OPENGYM_COMMIT},
    {"name": "ExerciseDB v1 via hasaneyldrm/exercises-dataset, as carried by openGym (exercise text only)", "source": "exercisedb",
     "license": "MIT", "url": "https://github.com/hasaneyldrm/exercises-dataset", "commit": OPENGYM_COMMIT},
    {"name": "Watch2Whoop curated exercises, texts and templates", "source": "curated", "license": "AGPL-3.0"},
]
NOTICES = "THIRD_PARTY_NOTICES.md"      # in the repository: full attribution and the MIT text

_EXERCISE_COLUMNS = (
    "slug", "name_zh", "name_en", "aliases", "category", "modality", "force", "mechanic", "level", "movement_pattern", "equipment",
    "primary_muscles", "secondary_muscles", "joint_stress", "per_side", "default_increment_kg", "cues_zh", "instructions_zh",
    "description_en", "instructions_en", "images", "source", "source_ref", "family_slug", "license")


_CJK = re.compile(r"[\u3400-\u9fff]")


def _chinese(v) -> bool:
    return bool(_CJK.search(" ".join(v) if isinstance(v, list) else str(v)))


def exercise_row(r: dict) -> dict:
    """One exercise_catalog row as it goes into the bundle: no rev / timestamps, empty values left out, English cue and steps of
    the curated exercises filled in (catalog_en), English text out of the Chinese columns, provenance reduced to [{source, license, ref}]."""
    out = {}
    for k in _EXERCISE_COLUMNS:
        v = r.get(k)
        if v in (None, [], "") or (k == "per_side" and v is False):
            continue
        out[k] = v
    slug = r["slug"]
    if slug in CE.CUES_EN:
        out["cues_en"] = CE.CUES_EN[slug]
    elif r.get("description_en"):
        out["cues_en"] = r["description_en"]
    if slug in CE.INSTRUCTIONS_EN:
        out["instructions_en"] = CE.INSTRUCTIONS_EN[slug]
    out.pop("description_en", None)
    for zh, en in (("name_zh", "name_en"), ("cues_zh", "cues_en"), ("instructions_zh", "instructions_en")):
        if zh in out and not _chinese(out[zh]):           # English kept in the Chinese column: move it, or drop the duplicate
            if en not in out:
                out[en] = out[zh]
            del out[zh]
    if r.get("text_sources"):
        out["text_sources"] = [{"source": s["source"], "license": s["license"], "ref": s["ref"]} for s in r["text_sources"]]
    return out


def exercises_all(conn: psycopg.Connection) -> list[dict]:
    return [{**exercise_row(r), "rev": r["rev"]} for r in conn.execute("SELECT * FROM exercise_catalog ORDER BY slug").fetchall()]


def exercises_since(conn: psycopg.Connection, since: int) -> tuple[list[dict], list[str]]:
    rows = conn.execute("SELECT * FROM exercise_catalog WHERE rev > %s ORDER BY rev, slug", (since,)).fetchall()
    gone = conn.execute("SELECT key FROM static_tombstones WHERE module = 'exercises' AND rev > %s ORDER BY rev, key", (since,)).fetchall()
    return [{**exercise_row(r), "rev": r["rev"]} for r in rows], [g["key"] for g in gone]


# ---------------------------------------------------------------- code-derived modules (the same for everyone, fixed per deploy)
def _plain(x):
    """tuples -> lists, sets -> sorted lists, so the module is JSON and its hash is stable."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted(_plain(v) for v in x)
    return x


@functools.lru_cache(maxsize=None)
def code_module(name: str) -> dict:
    if name == "taxonomy":
        return _plain({
            "muscles": {k: {"zh": zh, "en": muscle_load.MUSCLE_EN.get(k, k), "group": CD.MUSCLE_GROUP.get(k)} for k, zh in CD.MUSCLE_ZH.items()},
            "groups": {"zh": CD.GROUP_ZH},
            "equipment": {k: {"zh": zh, "en": custom.EQUIPMENT_EN.get(k, k)} for k, zh in CD.EQUIPMENT_ZH.items()},
            "movement_patterns": {"zh": CD.PATTERN_ZH},
            "joint_stress": {"zh": CD.JOINT_STRESS_ZH},
            "categories": ["strength", "stretching", "cardio", "mobility"],
            "modalities": ["weight_reps", "bodyweight_reps", "reps_only", "duration"],
            "default_caution_slugs": sorted(catalog.DEFAULT_BANNED_SLUGS),   # plus every row whose family_slug is one of them
            "imported_sources": list(OGM.IMPORTED_SOURCES),
        })
    if name == "glossary":
        return _plain({"zh": {"metrics": G.METRICS, "labs": G.LABS, "status": G.STATUS, "conditions": G.CONDITIONS},
                       "en": {"metrics": GE.METRICS_EN, "labs": GE.LABS_EN, "status": GE.STATUS_EN, "conditions": GE.CONDITIONS_EN}})
    if name == "split_templates":
        return _plain(P.SPLIT_TEMPLATES)
    raise KeyError(name)


def code_hash(name: str) -> str:
    return hashlib.sha256(json.dumps(code_module(name), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


_refreshed: set[str] = set()      # databases whose code modules this process has already compared


def refresh_code_modules(conn: psycopg.Connection, *, force: bool = False) -> None:
    """Give a code module a new rev when its content differs from the one recorded (after a deploy that changed it).
    Compared once per process and database (the code cannot change under a running process)."""
    if not force and conn.info.dbname in _refreshed:
        return
    _refreshed.add(conn.info.dbname)
    have = {r["name"]: r["hash"] for r in conn.execute("SELECT name, hash FROM static_modules")}
    for name in CODE_MODULES:
        h = code_hash(name)
        if have.get(name) != h:
            conn.execute("""INSERT INTO static_modules (name, hash, rev) VALUES (%s, %s, nextval('static_data_rev_seq'))
                            ON CONFLICT (name) DO UPDATE SET hash = EXCLUDED.hash, rev = EXCLUDED.rev, updated_at = now()""", (name, h))


# ---------------------------------------------------------------- versions and documents
def current_version(conn: psycopg.Connection) -> int:
    refresh_code_modules(conn)
    row = conn.execute("""SELECT GREATEST((SELECT COALESCE(max(rev), 0) FROM exercise_catalog),
                                          (SELECT COALESCE(max(rev), 0) FROM static_tombstones),
                                          (SELECT COALESCE(max(rev), 0) FROM static_modules)) AS v""").fetchone()
    return row["v"]


def manifest(conn: psycopg.Connection, *, flags: dict | None = None) -> dict:
    """What the bundle is now: version, modules with their revs, licences. `flags` = switches the app must respect locally
    (exercise_opengym false: hide the exercises whose source is in taxonomy.imported_sources)."""
    version = current_version(conn)
    mods = {r["name"]: {"rev": r["rev"], "hash": r["hash"]} for r in conn.execute("SELECT name, hash, rev FROM static_modules")}
    n = conn.execute("SELECT count(*) AS n, COALESCE(max(rev), 0) AS rev FROM exercise_catalog").fetchone()
    mods["exercises"] = {"rev": n["rev"], "count": n["n"]}
    return {"format": FORMAT, "version": version, "modules": {k: mods[k] for k in MODULES}, "attribution": ATTRIBUTION,
            "notices": NOTICES, "flags": flags or {}}


def document(conn: psycopg.Connection, since: int | None = None, *, flags: dict | None = None) -> dict:
    """The bundle (since None / 0 / ahead of the server: everything, `full` true) or what changed after `since` (exercise rows
    with rev > since, deleted slugs, and the code modules whose rev > since, each whole)."""
    man = manifest(conn, flags=flags)
    version = man["version"]
    full = not since or since <= 0 or since > version
    mods: dict = {}
    if full:
        mods["exercises"] = {"upsert": exercises_all(conn), "delete": []}
    else:
        up, gone = exercises_since(conn, since)
        if up or gone:
            mods["exercises"] = {"upsert": up, "delete": gone}
    for name in CODE_MODULES:
        if full or man["modules"][name]["rev"] > since:
            mods[name] = code_module(name)
    return {**{k: man[k] for k in ("format", "version", "attribution", "notices", "flags")}, "since": None if full else since, "full": full,
            "modules": mods,
            "manifest": {"modules": man["modules"]}}


def encode(doc: dict) -> bytes:
    """Compact JSON, then gzip (mtime 0: the same document gives the same bytes)."""
    raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return gzip.compress(raw, compresslevel=9, mtime=0)
