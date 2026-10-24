"""Exercise catalog management, bilingual search, and biomechanical alternative recommendation (Training V2).

Maintains `exercise_catalog` (shared read-only dictionary) and joins it with each user's `exercise_meta`
(banned / caution / active, personal cues, reason, app_exercise_id). For a known user the user's own custom exercises
(custom.py, issue #189) are appended to the shared rows (rows_for); nobody else sees them.
"""
import functools
import json
import os
import re
import unicodedata
from pathlib import Path

import psycopg

from .. import sparky_client
from ..config import REPO_ROOT
from ..i18n import L, is_en
from ..records import store as records_store
from ..rollout import flags
from . import catalog_data as CD, catalog_en as CE, params as P
from .opengym import mapping as OGM

DEFAULT_BANNED_SLUGS = {"upright_row", "behind_neck_press", "behind_neck_pulldown", "dips"}
_PAREN_TAIL = re.compile(r"[（(][^（()）]*[)）]\s*$")

# Reverse lookups so Chinese filter strings ("胸", "背阔肌", "哑铃") map to canonical keys
_MUSCLE_BY_ZH = {v: k for k, v in CD.MUSCLE_ZH.items()} | {
    "胸": "chest", "胸肌": "chest", "背": "lats", "背阔": "lats", "中背": "middle back", "下背": "lower back",
    "腰": "lower back", "斜方": "traps", "肩": "shoulders", "三角肌": "shoulders", "腿": "quadriceps",
    "股四头": "quadriceps", "大腿前侧": "quadriceps", "腘绳": "hamstrings", "大腿后侧": "hamstrings",
    "臀": "glutes", "臀大肌": "glutes", "臀中肌": "abductors", "小腿": "calves", "二头": "biceps",
    "三头": "triceps", "手臂": "biceps", "核心": "abdominals", "腹": "abdominals", "腹肌": "abdominals",
}
_EQUIP_BY_ZH = {v: k for k, v in CD.EQUIPMENT_ZH.items()} | {
    "龙门架": "cable", "绳索": "cable", "器械": "machine", "徒手": "body only", "曲杆": "ez curl bar",
    "史密斯": "smith machine", "body weight": "body only", "bodyweight": "body only",
}
_GROUP_BY_ZH = {v: k for k, v in CD.GROUP_ZH.items()} | {
    "腿": "legs", "臀": "legs", "有氧": "cardio", "拉伸": "mobility", "灵活": "mobility",
}


LOCAL_IMAGE_BASE = "/app/media/exercises"   # served from media_dir()/exercises by web/api.py (never hotlinked from GitHub)


def media_dir() -> Path:
    """The deployment's own storage for downloaded exercise media (`body-engine fetch-exercise-db --images`)."""
    return Path(os.environ.get("BODY_MEDIA_DIR", REPO_ROOT / "media"))


def image_base_url() -> str:
    return os.environ.get("BODY_EXERCISE_IMAGE_BASE_URL", LOCAL_IMAGE_BASE).rstrip("/")


def image_urls(images: list[str]) -> list[str]:
    """Public URLs of an exercise's demo images. With the default local storage only images that were downloaded are listed."""
    base = image_base_url()
    if base != LOCAL_IMAGE_BASE:
        return [img if img.startswith("http") else f"{base}/{img}" for img in images]
    root = media_dir() / "exercises"
    return [f"{base}/{img}" for img in images if not img.startswith(("http", "/")) and ".." not in img
            and (root / img).is_file()]


@functools.lru_cache(maxsize=200000)
def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("（禁用）", "").replace("(禁用)", "")
    return re.sub(r"[\s/+·・,，、.。:：\-_'\"“”‘’()（）\[\]]", "", s)


def strip_paren(s: str | None) -> str:
    s = (s or "").replace("（禁用）", "").replace("(禁用)", "").strip()
    return _PAREN_TAIL.sub("", s).strip()


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _infer_pattern(raw: dict) -> str:
    cat = (raw.get("category") or "strength").lower()
    if cat == "cardio":
        return "cardio"
    if cat == "stretching":
        return "mobility"
    primary = {m.lower() for m in raw.get("primaryMuscles") or []}
    force = (raw.get("force") or "").lower()
    name = (raw.get("name") or "").lower()
    if "chest" in primary:
        return "incline_push" if "incline" in name else "horizontal_push"
    if primary & {"lats", "middle back"}:
        return "vertical_pull" if any(w in name for w in ("pulldown", "pull-up", "pullup", "chin-up")) else "horizontal_pull"
    if "shoulders" in primary:
        if any(w in name for w in ("face pull", "rear", "external rotation", "reverse")):
            return "face_pull_er"
        if any(w in name for w in ("raise", "lateral")):
            return "lateral_raise"
        return "vertical_push"
    if "quadriceps" in primary:
        if "extension" in name:
            return "knee_extension"
        if any(w in name for w in ("lunge", "split", "step")):
            return "lunge_split"
        return "squat"
    if primary & {"hamstrings", "glutes", "lower back"}:
        return "knee_flexion" if "curl" in name else "hip_hinge"
    if "calves" in primary:
        return "calf"
    if "biceps" in primary:
        return "elbow_flexion"
    if "triceps" in primary:
        return "elbow_extension"
    if "abdominals" in primary:
        return "core"
    return "horizontal_push" if force == "push" else "horizontal_pull"


_EQUIP_ALIASES = {"body weight": "body only", "none": "body only", "": "body only", "e-z curl bar": "ez curl bar",
                  "kettlebells": "kettlebell", "band": "bands"}


def family_of(name: str | None) -> str | None:
    """Curated restricted exercise an imported variant belongs to (params.HAZARD_FAMILIES), e.g. "Upright Cable Row" -> upright_row."""
    n = (name or "").lower()
    return next((slug for rx, slug in P.HAZARD_FAMILIES if re.search(rx, n)), None)


def convert_free_exercise_db_entry(raw: dict) -> dict:
    """Convert an upstream yuhonas/free-exercise-db JSON entry into our exercise_catalog row format."""
    slug = _slugify(raw.get("id") or raw["name"])
    cat_raw = (raw.get("category") or "strength").lower()
    category = cat_raw if cat_raw in ("strength", "stretching", "cardio", "mobility") else "strength"
    eq_raw = (raw.get("equipment") or "body only").lower()
    eq = _EQUIP_ALIASES.get(eq_raw, eq_raw)
    modality = "duration" if category in ("cardio", "stretching") else (
        "bodyweight_reps" if eq == "body only" else "reps_only" if eq in ("bands", "foam roll") else "weight_reps"
    )
    force = (raw.get("force") or "").lower()
    mechanic = (raw.get("mechanic") or "").lower()
    level = (raw.get("level") or "intermediate").lower()
    instructions = [str(x).strip() for x in (raw.get("instructions") or []) if str(x).strip()]
    return {
        "slug": slug,
        "name_zh": raw.get("name_zh") or raw["name"],
        "name_en": raw["name"],
        "aliases": list(raw.get("aliases") or ([raw["id"]] if raw.get("id") else [])),
        "category": category,
        "modality": modality,
        "force": force if force in ("push", "pull", "static") else None,
        "mechanic": mechanic if mechanic in ("compound", "isolation") else None,
        "level": level if level in ("beginner", "intermediate", "expert") else "intermediate",
        "movement_pattern": raw.get("movement_pattern") or _infer_pattern(raw),
        "equipment": [eq] if isinstance(eq, str) else list(eq),
        "primary_muscles": [m.lower() for m in (raw.get("primaryMuscles") or raw.get("primary_muscles") or [])],
        "secondary_muscles": [m.lower() for m in (raw.get("secondaryMuscles") or raw.get("secondary_muscles") or [])],
        "joint_stress": list(raw.get("joint_stress") or []),
        "per_side": bool(raw.get("per_side", False)),
        "default_increment_kg": raw.get("default_increment_kg", 2.5 if modality == "weight_reps" else None),
        "cues_zh": raw.get("cues_zh") or (instructions[0] if instructions else None),
        "instructions_zh": instructions,
        "images": list(raw.get("images") or []),
        "source": "free-exercise-db",
        "license": "Unlicense",
        "source_ref": raw.get("id"),
        "family_slug": family_of(raw.get("name")),
    }


def _curated_rows() -> dict[str, dict]:
    out = {}
    for item in CD.CURATED_EXERCISES:
        imgs = item.get("images") or []
        out[item["slug"]] = {**item, "aliases": list(item.get("aliases") or []),
                             "source_ref": imgs[0].split("/", 1)[0] if imgs else None, "family_slug": None,
                             "license": "AGPL-3.0"}
    return out


def _merge_upstream(row: dict, raw: dict, conv: dict) -> None:
    """Fold a free-exercise-db entry into the curated row it duplicates: the curated Chinese overlay wins, upstream fills gaps."""
    seen = {norm(x) for x in [row["name_zh"], row["name_en"], row["slug"], *row["aliases"]]}
    for extra in (raw.get("name"), raw.get("id")):
        if extra and norm(extra) not in seen:
            row["aliases"].append(extra)
            seen.add(norm(extra))
    if not row.get("images"):
        row["images"] = conv["images"]
    if not row.get("secondary_muscles"):
        row["secondary_muscles"] = conv["secondary_muscles"]
    row["source_ref"] = row.get("source_ref") or conv["source_ref"]


def seed_catalog(conn: psycopg.Connection, extra_json: Path | None = None) -> dict:
    """Idempotently populate `exercise_catalog` from CURATED_EXERCISES + optional upstream free-exercise-db JSON.
    Upstream entries that duplicate a curated exercise (same image folder, English name, alias or slug) are merged into it."""
    curated_by_slug = _curated_rows()
    by_slug: dict[str, dict] = dict(curated_by_slug)
    by_key: dict[str, str] = {}
    for slug, row in curated_by_slug.items():
        for x in [row["slug"], row["name_en"], *row["aliases"]]:
            by_key.setdefault(norm(x), slug)
        for img in row.get("images") or []:
            by_key.setdefault(norm(img.split("/", 1)[0]), slug)
    imported_free_db = merged_free_db = 0
    if extra_json is not None and extra_json.exists():
        raw_list = json.loads(extra_json.read_text(encoding="utf-8"))
        for item in raw_list:
            conv = convert_free_exercise_db_entry(item)
            target = (by_key.get(norm(item.get("id"))) or by_key.get(norm(item.get("name")))
                      or (conv["slug"] if conv["slug"] in curated_by_slug else None))
            if target:
                _merge_upstream(by_slug[target], item, conv)
                merged_free_db += 1
                continue
            fam = curated_by_slug.get(conv["family_slug"] or "")
            if fam:   # a variant of a restricted exercise carries the same joint-stress tags
                conv["joint_stress"] = list(dict.fromkeys([*conv["joint_stress"], *fam["joint_stress"]]))
            by_slug[conv["slug"]] = conv
            imported_free_db += 1

    rows = list(by_slug.values())
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO exercise_catalog (
                 slug, name_zh, name_en, aliases, category, modality, force, mechanic, level,
                 movement_pattern, equipment, primary_muscles, secondary_muscles, joint_stress,
                 per_side, default_increment_kg, cues_zh, instructions_zh, images, source, source_ref, family_slug, license, updated_at
               ) VALUES (
                 %(slug)s, %(name_zh)s, %(name_en)s, %(aliases)s, %(category)s, %(modality)s,
                 %(force)s, %(mechanic)s, %(level)s, %(movement_pattern)s, %(equipment)s,
                 %(primary_muscles)s, %(secondary_muscles)s, %(joint_stress)s, %(per_side)s,
                 %(default_increment_kg)s, %(cues_zh)s, %(instructions_zh)s, %(images)s, %(source)s,
                 %(source_ref)s, %(family_slug)s, %(license)s, now()
               )
               ON CONFLICT (slug) DO UPDATE SET
                 name_zh = EXCLUDED.name_zh,
                 name_en = EXCLUDED.name_en,
                 aliases = EXCLUDED.aliases,
                 category = EXCLUDED.category,
                 modality = EXCLUDED.modality,
                 force = EXCLUDED.force,
                 mechanic = EXCLUDED.mechanic,
                 level = EXCLUDED.level,
                 movement_pattern = EXCLUDED.movement_pattern,
                 equipment = EXCLUDED.equipment,
                 primary_muscles = EXCLUDED.primary_muscles,
                 secondary_muscles = EXCLUDED.secondary_muscles,
                 joint_stress = EXCLUDED.joint_stress,
                 per_side = EXCLUDED.per_side,
                 default_increment_kg = EXCLUDED.default_increment_kg,
                 cues_zh = EXCLUDED.cues_zh,
                 instructions_zh = EXCLUDED.instructions_zh,
                 images = EXCLUDED.images,
                 source = EXCLUDED.source,
                 source_ref = EXCLUDED.source_ref,
                 family_slug = EXCLUDED.family_slug,
                 license = EXCLUDED.license,
                 updated_at = now()""",
            rows,
        )
    return {
        "seeded": len(rows),
        "curated": len(CD.CURATED_EXERCISES),
        "imported_free_db": imported_free_db,
        "merged_free_db": merged_free_db,
    }


def ensure_seeded(conn: psycopg.Connection) -> None:
    """Seed `exercise_catalog` automatically if empty (e.g. fresh test database)."""
    row = conn.execute("SELECT 1 FROM exercise_catalog LIMIT 1").fetchone()
    if row is None:
        seed_catalog(conn)


def body_group(row: dict) -> str:
    cat = row.get("category")
    if cat == "cardio" or row.get("movement_pattern") == "cardio":
        return "cardio"
    if cat in ("stretching", "mobility") or row.get("movement_pattern") == "mobility":
        return "mobility"
    for m in row.get("primary_muscles") or []:
        if m in CD.MUSCLE_GROUP:
            return CD.MUSCLE_GROUP[m]
    return "other"


def format_exercise(row: dict, meta: dict | None = None, *, detail: bool = False) -> dict:
    """Merge a catalog row with a user's exercise_meta row."""
    slug = row["slug"]
    if meta is not None:
        status = meta["status"]
    else:
        status = "caution" if slug in DEFAULT_BANNED_SLUGS or row.get("family_slug") in DEFAULT_BANNED_SLUGS else "active"
    cues = (meta or {}).get("cues") or row.get("cues_zh") or row.get("description_en")   # imported rows only have English text
    if is_en() and not (meta or {}).get("cues"):   # the user's own cue stays as written; the curated ones have English
        cues = CE.CUES_EN.get(slug) or row.get("description_en") or cues
    reason = (meta or {}).get("reason")
    grp = body_group(row)
    prim = row.get("primary_muscles") or []
    sec = row.get("secondary_muscles") or []
    eq = row.get("equipment") or []
    pat = row.get("movement_pattern")
    pat_zh = CD.PATTERN_ZH.get(pat or "", pat)
    j_stress = row.get("joint_stress") or []
    joint_flags: dict[str, str] = {}
    for j in j_stress:
        joint_flags[j] = "high"
        if j == "shoulder_ir":
            joint_flags["shoulder_internal_rotation"] = "high"
    joint_flags_zh = {CD.JOINT_STRESS_ZH.get(j, j): "high" for j in j_stress}
    muscle_groups = list(dict.fromkeys(CD.MUSCLE_GROUP[m] for m in prim if m in CD.MUSCLE_GROUP))
    if not muscle_groups and grp in CD.GROUP_ZH:
        muscle_groups = [grp]
    out = {
        "slug": slug,
        "exercise_id": (meta or {}).get("app_exercise_id") or slug,
        "app_exercise_id": (meta or {}).get("app_exercise_id"),
        "name": row["name_zh"],
        "name_zh": row["name_zh"],
        "name_en": row["name_en"],
        "category": row["category"],
        "modality": row["modality"],
        "group": grp,
        "group_zh": CD.GROUP_ZH.get(grp, "其他"),
        "muscle_groups": muscle_groups,
        "movement_pattern": pat,
        "pattern_zh": pat_zh,
        "movement_pattern_zh": pat_zh,
        "equipment": eq,
        "equipment_zh": [CD.EQUIPMENT_ZH.get(x, x) for x in eq],
        "primary_muscles": prim,
        "primary_muscles_zh": [CD.MUSCLE_ZH.get(x, x) for x in prim],
        "secondary_muscles": sec,
        "secondary_muscles_zh": [CD.MUSCLE_ZH.get(x, x) for x in sec],
        "joint_stress": j_stress,
        "joint_stress_zh": [CD.JOINT_STRESS_ZH.get(j, j) for j in j_stress],
        "joint_flags": joint_flags,
        "joint_flags_zh": joint_flags_zh,
        "default_increment_kg": row.get("default_increment_kg"),
        "per_side": bool((meta or {}).get("per_side") if (meta or {}).get("per_side") is not None else row.get("per_side")),
        "status": status,
        "cues": cues,
        "reason": reason,
        "source": row.get("source"),
    }
    if row.get("family_slug"):
        out["family_slug"] = row["family_slug"]
    if (meta or {}).get("inherited_from"):
        out["restriction_from"] = meta["inherited_from"]
    if detail:
        imgs = row.get("images") or []
        steps = row.get("instructions_zh") or row.get("instructions_en") or ([cues] if cues else [])
        if is_en():   # the curated steps, or the one-line cue the steps were derived from (cues is already English here)
            steps = (CE.INSTRUCTIONS_EN.get(slug) or row.get("instructions_en")
                     or ([cues] if cues and steps == [row.get("cues_zh")] else steps))
        out.update(
            aliases=list(dict.fromkeys([*(row.get("aliases") or []), *((meta or {}).get("aliases") or [])])),
            force=row.get("force"),
            mechanic=row.get("mechanic"),
            level=row.get("level"),
            instructions_zh=steps,
            license=row.get("license"),
            images=imgs,
            image_urls=image_urls(imgs),
        )
    return out


_ROWS_CACHE: dict[str, tuple] = {}


def catalog_rows(conn: psycopg.Connection) -> list[dict]:
    """All exercise_catalog rows (about 950 with free-exercise-db), cached per database until the table changes.
    Each row carries `_names`: normalized names / aliases for matching. Rows are shared: treat them as read-only."""
    ensure_seeded(conn)
    fp = conn.execute("SELECT count(*) AS n, max(updated_at) AS t FROM exercise_catalog").fetchone()
    key, stamp = conn.info.dbname, (fp["n"], fp["t"])
    hit = _ROWS_CACHE.get(key)
    if hit is None or hit[0] != stamp:
        rows = conn.execute("SELECT * FROM exercise_catalog ORDER BY slug").fetchall()
        for r in rows:
            names = [r["slug"], r["name_zh"], r["name_en"], *(r.get("aliases") or [])]
            r["_names"] = {norm(x) for x in names if x} | {norm(strip_paren(x)) for x in names if x} - {""}
        hit = _ROWS_CACHE[key] = (stamp, rows)
    return hit[1]


OPENGYM_FLAG = "exercise_opengym"


def imported_visible(conn: psycopg.Connection, user_id: int | None) -> bool:
    """Whether the exercises imported from openGym (#248) show up for this user; with no user: only when the flag is on for everyone.
    The flag is the emergency switch: off hides the imported rows from search / lookup, the data stays (logged workouts still resolve)."""
    if user_id is not None:
        return flags.enabled(conn, user_id, OPENGYM_FLAG)
    row = flags.rows(conn).get(OPENGYM_FLAG)
    return bool(row and row["state"] == "all")


def rows_for(conn: psycopg.Connection, user_id: int | None) -> list[dict]:
    """The shared catalog plus the user's own custom exercises (only the shared rows for None); the openGym import only while its
    flag is on for the user."""
    rows = catalog_rows(conn)
    if not imported_visible(conn, user_id):
        rows = [r for r in rows if r["source"] not in OGM.IMPORTED_SOURCES]
    if user_id is None:
        return rows
    from . import custom
    return rows + custom.rows(conn, user_id)


def _load_catalog_and_meta(conn: psycopg.Connection, user_id: int | None, with_meta: bool = True) -> tuple[list[dict], dict[str, dict]]:
    cat_rows = rows_for(conn, user_id if with_meta else None)
    meta_by_ref: dict[str, dict] = {}
    if user_id is not None and with_meta:
        for m in records_store.list_exercises(conn, user_id):
            meta_by_ref[m["exercise_ref"]] = m
    return cat_rows, meta_by_ref


def _match_meta_to_slug(cat_rows: list[dict], meta_by_ref: dict[str, dict]) -> dict[str, dict]:
    """Map catalog slug -> user's exercise_meta row (matching by slug == exercise_ref or normalized name/alias)."""
    by_slug: dict[str, dict] = {}
    unmatched_meta = []
    cat_slugs = {r["slug"] for r in cat_rows}
    for ref, m in meta_by_ref.items():
        if ref in cat_slugs:
            by_slug[ref] = m
        else:
            unmatched_meta.append(m)
    if unmatched_meta:
        m_names = [(m, {norm(x) for x in [m["exercise_ref"], m.get("name_zh"), m.get("name_en"), *(m.get("aliases") or [])] if x})
                   for m in unmatched_meta]
        for r in cat_rows:
            if r["slug"] in by_slug:
                continue
            r_names = r.get("_names") or {norm(x) for x in [r["slug"], r["name_zh"], r["name_en"], *(r.get("aliases") or [])] if x}
            for m, names in m_names:
                if r_names & names:
                    by_slug[r["slug"]] = m
                    break
    # Imported variants of a restricted exercise inherit the user's restriction (not the SparkyFitness id)
    for r in cat_rows:
        fam = r.get("family_slug")
        if fam and r["slug"] not in by_slug and fam in by_slug and by_slug[fam]["status"] != "active":
            by_slug[r["slug"]] = {**by_slug[fam], "app_exercise_id": None, "inherited_from": fam}
    return by_slug


def resolve_with_candidates(
    conn: psycopg.Connection,
    user_id: int | None,
    query_or_slug: str,
    with_meta: bool = True,
) -> tuple[dict | None, dict | None, list[str]]:
    """Resolve an exercise slug, Chinese/English name, or alias to (catalog_row, meta_row, candidate_names)."""
    cat_rows, meta_by_ref = _load_catalog_and_meta(conn, user_id, with_meta)
    meta_by_slug = _match_meta_to_slug(cat_rows, meta_by_ref)
    q_raw = (query_or_slug or "").strip()
    q = norm(q_raw)
    q_short = norm(strip_paren(q_raw))
    if not q:
        return None, None, []

    # 1. Exact slug or app_exercise_id match
    for r in cat_rows:
        m = meta_by_slug.get(r["slug"])
        if r["slug"] == q_raw or r["slug"] == q or (m and m.get("app_exercise_id") == q_raw):
            return r, m, []

    # 2. Exact name_zh / name_en / alias match
    exact_hits = []
    for r in cat_rows:
        m = meta_by_slug.get(r["slug"])
        names = [r["name_zh"], r["name_en"], r["slug"], *(r.get("aliases") or [])]
        if m:
            names += [m.get("name_zh"), m.get("name_en"), m.get("exercise_ref"), *(m.get("aliases") or [])]
        if any(norm(x) == q for x in names if x):
            exact_hits.append((r, m))
    if len(exact_hits) == 1:
        return exact_hits[0][0], exact_hits[0][1], []

    # 3. Stripped parenthetical match
    if not exact_hits and q_short:
        for r in cat_rows:
            m = meta_by_slug.get(r["slug"])
            names = [r["name_zh"], r["name_en"], *(r.get("aliases") or [])]
            if any(norm(strip_paren(x)) == q_short for x in names if x):
                exact_hits.append((r, m))
        if len(exact_hits) == 1:
            return exact_hits[0][0], exact_hits[0][1], []

    if exact_hits:
        return exact_hits[0][0], exact_hits[0][1], [r["name_zh"] for r, _ in exact_hits[:8]]

    # 4. Substring candidate suggestions
    cands = []
    for r in cat_rows:
        m = meta_by_slug.get(r["slug"])
        names = [r["name_zh"], r["name_en"], *(r.get("aliases") or [])]
        if m:
            names += [m.get("name_zh"), m.get("name_en"), *(m.get("aliases") or [])]
        if any(q in norm(x) or (norm(strip_paren(x)) and norm(strip_paren(x)) in q) for x in names if x):
            cands.append(r["name_zh"])
    return None, None, cands[:8]


def resolve(
    conn: psycopg.Connection,
    query_or_slug: str,
    user_id: int | None = None,
    *,
    with_meta: bool = True,
) -> dict | None:
    """Resolve an exercise slug, Chinese/English name, or alias into a formatted exercise dict (or None)."""
    row, meta, _cands = resolve_with_candidates(conn, user_id, query_or_slug, with_meta=with_meta)
    if row is None:
        return None
    return format_exercise(row, meta, detail=True)


def _normalize_filter_muscle(m: str | None) -> str | None:
    if not m:
        return None
    s = m.strip().lower()
    return _MUSCLE_BY_ZH.get(s) or _MUSCLE_BY_ZH.get(m.strip()) or s


def _normalize_filter_equip(e: str | None) -> str | None:
    if not e:
        return None
    s = e.strip().lower()
    return _EQUIP_BY_ZH.get(s) or _EQUIP_BY_ZH.get(e.strip()) or s


def _normalize_filter_group(g: str | None) -> str | None:
    if not g:
        return None
    s = g.strip().lower()
    return _GROUP_BY_ZH.get(s) or _GROUP_BY_ZH.get(g.strip()) or s


def search(
    conn: psycopg.Connection,
    user_id: int | None = None,
    query: str | None = None,
    *,
    muscle: str | None = None,
    equipment: str | None = None,
    pattern: str | None = None,
    group: str | None = None,
    category: str | None = None,
    include_banned: bool = True,
    limit: int = 30,
    with_meta: bool = True,
    client: sparky_client.SparkyClient | None = None,
) -> list[dict]:
    """Search the standard exercise catalog + user's exercise_meta (+ SparkyFitness custom exercises if connected)."""
    cat_rows, meta_by_ref = _load_catalog_and_meta(conn, user_id, with_meta)
    meta_by_slug = _match_meta_to_slug(cat_rows, meta_by_ref)

    # Best-effort fetch of SparkyFitness library to attach app_exercise_id or include custom exercises
    sparky_lib: list[dict] = []
    if client is None and user_id is not None:
        try:
            client = sparky_client.client_for_user(conn, user_id)
        except Exception:
            client = None
    if client is not None:
        try:
            sparky_lib = sparky_client.exercises(client)
        except Exception:
            sparky_lib = []

    sparky_by_norm: dict[str, dict] = {}
    for s in sparky_lib:
        sparky_by_norm[norm(s["name"])] = s
        sparky_by_norm[norm(strip_paren(s["name"]))] = s
        if (s.get("source_id") or "").startswith(("bm:", "w2w:")):
            sparky_by_norm[s["source_id"].split(":", 1)[1]] = s

    q = norm(query) if query else ""
    f_muscle = _normalize_filter_muscle(muscle)
    if f_muscle in CD.GROUP_ZH and f_muscle not in CD.MUSCLE_ZH and not group:
        f_group = f_muscle
        f_muscle = None
    else:
        f_group = _normalize_filter_group(group)
    f_equip = _normalize_filter_equip(equipment)
    f_pattern = (pattern or "").strip().lower() or None
    f_cat = (category or "").strip().lower() or None

    scored: list[tuple[int, str, dict]] = []
    seen_sparky_ids: set[str] = set()

    for r in cat_rows:
        m = meta_by_slug.get(r["slug"])
        sp = None
        if m and m.get("app_exercise_id"):
            sp = next((x for x in sparky_lib if x["id"] == m["app_exercise_id"]), None)
        if sp is None:
            sp = (sparky_by_norm.get(r["slug"])
                  or sparky_by_norm.get(norm(r["name_zh"]))
                  or sparky_by_norm.get(norm(strip_paren(r["name_zh"])))
                  or sparky_by_norm.get(norm(r["name_en"])))
        if sp is not None:
            seen_sparky_ids.add(sp["id"])
            if m is None:
                m = {"status": "banned" if ("（禁用）" in sp["name"] or (sp.get("description") or "").startswith("【禁用】")) else "active",
                     "app_exercise_id": sp["id"], "cues": None, "reason": None, "per_side": r.get("per_side")}
            elif not m.get("app_exercise_id"):
                m = {**m, "app_exercise_id": sp["id"]}

        item = format_exercise(r, m, detail=False)
        if not include_banned and item["status"] == "banned":
            continue
        if f_cat and item["category"] != f_cat and item["group"] != f_cat:
            continue
        if f_group and item["group"] != f_group and f_group not in item["muscle_groups"]:
            continue
        if f_pattern and item["movement_pattern"] != f_pattern:
            continue
        if f_muscle and f_muscle not in item["primary_muscles"] and f_muscle not in item["secondary_muscles"]:
            continue
        if f_equip and f_equip not in item["equipment"]:
            continue

        rank = 0
        if q:
            names = [r["name_zh"], r["name_en"], r["slug"], *(r.get("aliases") or [])]
            if m:
                names += [m.get("name_zh"), m.get("name_en"), *(m.get("aliases") or [])]
            norms = [norm(x) for x in names if x]
            if any(x == q for x in norms):
                rank = 100
            elif any(x.startswith(q) for x in norms):
                rank = 75
            elif any(q in x for x in norms):
                rank = 50
            elif any(q in norm(x) for x in [*item["primary_muscles_zh"], *item["equipment_zh"], item["pattern_zh"] or ""]):
                rank = 25
            else:
                continue
        else:
            # Prefer user's configured / linked exercises first, then the curated overlay, then common compound exercises
            rank = (30 if m else 0) + (20 if r.get("source") == "curated" else 0) + (10 if r.get("mechanic") == "compound" else 0)
        if q and r.get("source") == "curated":
            rank += 5
        if f_muscle and f_muscle in item["primary_muscles"]:
            rank += 15
        scored.append((-rank, r["slug"], item))

    # Also include any custom SparkyFitness exercises not in exercise_catalog
    for sp in sparky_lib:
        if sp["id"] in seen_sparky_ids:
            continue
        if f_group or f_pattern or f_equip or f_cat:
            continue
        prim = [x.lower() for x in (sp.get("primary_muscles") or [])]
        if f_muscle and f_muscle not in prim:
            continue
        if q and q not in norm(sp["name"]):
            continue
        desc = sp.get("description") or ""
        st = "banned" if ("（禁用）" in sp["name"] or desc.startswith("【禁用】")) else (
            "caution" if desc.startswith("【注意】") else "active"
        )
        if not include_banned and st == "banned":
            continue
        scored.append((-40, sp["id"], {
            "slug": f"custom:{sp['id']}",
            "exercise_id": sp["id"],
            "app_exercise_id": sp["id"],
            "name": sp["name"],
            "name_zh": sp["name"],
            "name_en": sp["name"],
            "category": sp.get("category") or "strength",
            "modality": sp.get("modality") or "weight_reps",
            "group": "other",
            "group_zh": "自定义",
            "muscle_groups": [],
            "movement_pattern": None,
            "pattern_zh": None,
            "movement_pattern_zh": None,
            "equipment": [],
            "equipment_zh": [],
            "primary_muscles": prim,
            "primary_muscles_zh": [CD.MUSCLE_ZH.get(x, x) for x in prim],
            "secondary_muscles": [],
            "secondary_muscles_zh": [],
            "joint_stress": [],
            "joint_stress_zh": [],
            "joint_flags": {},
            "joint_flags_zh": {},
            "default_increment_kg": None,
            "per_side": False,
            "status": st,
            "cues": desc[:120] if desc else None,
            "reason": None,
            "source": "sparky_custom",
        }))

    scored.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in scored[: max(1, min(limit, 150))]]


def recommend_alternatives(
    conn: psycopg.Connection,
    target: dict | str,
    user_id: int | None = None,
    *,
    equipment: str | None = None,
    limit: int = 5,
    with_meta: bool = True,
) -> list[dict]:
    """Recommend safe biomechanical alternatives for `target` (catalog row dict or slug/name), excluding banned exercises."""
    cat_rows, meta_by_ref = _load_catalog_and_meta(conn, user_id, with_meta)
    meta_by_slug = _match_meta_to_slug(cat_rows, meta_by_ref)
    if isinstance(target, str):
        target_row, _, _ = resolve_with_candidates(conn, user_id, target, with_meta=with_meta)
        if target_row is None:
            return []
    else:
        target_row = target

    f_equip = _normalize_filter_equip(equipment)
    t_slug = target_row["slug"]
    t_pat = target_row.get("movement_pattern")
    t_prim = set(target_row.get("primary_muscles") or [])
    t_sec = set(target_row.get("secondary_muscles") or [])
    t_stress = set(target_row.get("joint_stress") or [])

    cands: list[tuple[int, str, dict]] = []
    for r in cat_rows:
        slug = r["slug"]
        # Only curated rows carry checked movement patterns and joint-stress tags (imported rows would all look "safer")
        if slug == t_slug or slug in DEFAULT_BANNED_SLUGS or r.get("source") != "curated" or r.get("family_slug"):
            continue
        m = meta_by_slug.get(slug)
        status = (m or {}).get("status") or "active"
        if status == "banned":
            continue
        if f_equip and f_equip not in (r.get("equipment") or []):
            continue

        r_pat = r.get("movement_pattern")
        r_prim = set(r.get("primary_muscles") or [])
        r_sec = set(r.get("secondary_muscles") or [])
        r_stress = set(r.get("joint_stress") or [])

        same_pat = bool(t_pat and r_pat == t_pat)
        prim_overlap = t_prim & r_prim
        if not same_pat and not prim_overlap:
            continue

        score = 0
        reasons = []
        if same_pat:
            score += 40
            reasons.append(f"同模式（{CD.PATTERN_ZH.get(r_pat, r_pat)}）")
        if prim_overlap:
            score += 30 * len(prim_overlap) // max(1, len(t_prim))
            reasons.append("练" + "、".join(CD.MUSCLE_ZH.get(x, x) for x in sorted(prim_overlap)))
        if t_sec & r_sec:
            score += 10
        avoided = t_stress - r_stress
        if avoided:
            score += 15
            reasons.append("规避" + "、".join(CD.JOINT_STRESS_ZH.get(x, x) for x in sorted(avoided)))
        if m and status == "active":
            score += 10
        if status == "caution":
            score -= 15
            reasons.append("需注意要点")

        view = format_exercise(r, m, detail=False)
        view["match_score"] = score
        view["why"] = " · ".join(reasons)
        cands.append((-score, slug, view))

    cands.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in cands[:limit]]


def get_exercise(conn: psycopg.Connection, user_id: int | None, exercise: str,
                 *, with_meta: bool = True) -> dict:
    """Full exercise card + user restrictions + top 5 biomechanical alternatives."""
    row, meta, cands = resolve_with_candidates(conn, user_id, exercise, with_meta=with_meta)
    if row is None:
        hint = f"（相近动作：{'、'.join(cands)}）" if cands else ""
        raise ValueError(L(f"动作库里找不到「{exercise}」{hint}；可先用 search_exercises 搜索",
                           f"The exercise library has no “{exercise}”" + (f" (similar: {', '.join(cands)})" if cands else "")))
    out = format_exercise(row, meta, detail=True)
    out["alternatives"] = recommend_alternatives(conn, row, user_id=user_id, limit=5, with_meta=with_meta)
    return out
