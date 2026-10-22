"""Each user's own exercises (issue #189, behind the rollout flag muscle_load): what the shared catalog does not know.

- Stored in custom_exercises (migration 650) with user_id; added by the user's AI (add_custom_exercise, source 'ai') or on the
  Today page (动作库 → 我的动作, source 'manual'), changed and deleted there (delete_custom_exercise asks first, ai/params.ALWAYS_CONFIRM).
- rows() turns them into catalog-shaped rows (slug "my_<id>", source "user_custom") that catalog.rows_for() appends to the shared
  catalog for that user only: logged sets match them (loads.Index -> the muscle load map counts them), search and the exercise
  detail show them. Nobody else ever sees another user's rows.
- Names that are already in the shared catalog (or in the user's own list) are refused, so a custom row never shadows a real one.
  A variant of a restricted exercise (params.HAZARD_FAMILIES, e.g. "upright cable row") carries that family, so the user's ban /
  caution applies to it as to every other variant ("禁用动作照常拦截").
- admin_names(): names and how many users added them, never who (body-engine exercises custom-list), so common ones can be
  folded into the shared catalog later.
"""
from __future__ import annotations

import psycopg

from ..i18n import L
from . import catalog, catalog_data as CD

PREFIX = "my_"
SOURCES = ("ai", "manual")
MAX_PER_USER = 300
MAX_ALIASES = 10
MAX_PRIMARY = 4
MAX_NAME = 80
EQUIPMENT_EN = {"barbell": "Barbell", "dumbbell": "Dumbbell", "cable": "Cable", "machine": "Machine", "smith machine": "Smith machine",
                "body only": "Bodyweight", "bands": "Bands", "kettlebell": "Kettlebell", "ez curl bar": "EZ bar", "foam roll": "Foam roller",
                "other": "Other"}
_COLS = "id, user_id, name_zh, name_en, aliases, primary_muscles, secondary_muscles, equipment, source, created_at, updated_at"


def slug_of(cid: int) -> str:
    return f"{PREFIX}{cid}"


def id_of(slug: str | None) -> int | None:
    s = str(slug or "")
    return int(s[len(PREFIX):]) if s.startswith(PREFIX) and s[len(PREFIX):].isdigit() else None


def _names(name_zh: str | None, name_en: str | None, aliases: list[str] | None) -> list[str]:
    return [x for x in [name_zh, name_en, *(aliases or [])] if x]


def _norms(names: list[str]) -> set[str]:
    return ({catalog.norm(x) for x in names} | {catalog.norm(catalog.strip_paren(x)) for x in names}) - {""}


def catalog_row(r: dict) -> dict:
    """A custom_exercises row in the shape of an exercise_catalog row (catalog.format_exercise / loads.Index read it as such)."""
    names = _names(r["name_zh"], r["name_en"], r["aliases"])
    eq = list(r["equipment"] or [])
    modality = "bodyweight_reps" if eq == ["body only"] else "reps_only" if eq and set(eq) <= {"bands", "foam roll"} else "weight_reps"
    fam = next((f for f in (catalog.family_of(n) for n in names) if f), None)
    pattern = catalog._infer_pattern({"name": " ".join(names), "primaryMuscles": r["primary_muscles"], "category": "strength"})
    return {
        "slug": slug_of(r["id"]), "name_zh": r["name_zh"], "name_en": r["name_en"] or r["name_zh"], "aliases": list(r["aliases"] or []),
        "category": "strength", "modality": modality, "force": None, "mechanic": None, "level": "intermediate",
        "movement_pattern": pattern, "equipment": eq, "primary_muscles": list(r["primary_muscles"]),
        "secondary_muscles": list(r["secondary_muscles"] or []), "joint_stress": _family_stress(fam), "per_side": False,
        "default_increment_kg": 2.5 if modality == "weight_reps" else None, "cues_zh": None, "instructions_zh": [], "images": [],
        "source": "user_custom", "source_ref": None, "family_slug": fam, "custom_id": r["id"], "_names": _norms(names),
    }


def _family_stress(fam: str | None) -> list[str]:
    if not fam:
        return []
    return list(next((x.get("joint_stress") or [] for x in CD.CURATED_EXERCISES if x["slug"] == fam), []))


def _rows(conn: psycopg.Connection, uid: int) -> list[dict]:
    return conn.execute(f"SELECT {_COLS} FROM custom_exercises WHERE user_id = %s ORDER BY id", (uid,)).fetchall()


def rows(conn: psycopg.Connection, uid: int) -> list[dict]:
    """The owner's custom exercises as catalog rows (a small per-user query; the shared catalog stays cached in catalog.py)."""
    return [catalog_row(r) for r in _rows(conn, uid)]


def view(r: dict) -> dict:
    """What the page and the AI get for one custom exercise."""
    muscle = lambda ks: [L(CD.MUSCLE_ZH.get(k, k), _muscle_en(k)) for k in ks or []]
    return {"id": r["id"], "slug": slug_of(r["id"]), "name_zh": r["name_zh"], "name_en": r["name_en"], "aliases": list(r["aliases"] or []),
            "primary_muscles": list(r["primary_muscles"]), "secondary_muscles": list(r["secondary_muscles"] or []),
            "equipment": list(r["equipment"] or []), "primary_names": muscle(r["primary_muscles"]),
            "secondary_names": muscle(r["secondary_muscles"]),
            "equipment_names": [L(CD.EQUIPMENT_ZH.get(k, k), EQUIPMENT_EN.get(k, k)) for k in r["equipment"] or []],
            "source": r["source"], "created_at": r["created_at"].isoformat() if r.get("created_at") else None}


def _muscle_en(k: str) -> str:
    from .muscle_load import MUSCLE_EN
    return MUSCLE_EN.get(k, k)


def options() -> dict:
    """The muscle and equipment keys the form (and the AI) may use, with their names in the page language."""
    return {"muscles": [{"key": k, "name": L(v, _muscle_en(k))} for k, v in CD.MUSCLE_ZH.items()],
            "equipment": [{"key": k, "name": L(v, EQUIPMENT_EN.get(k, k))} for k, v in CD.EQUIPMENT_ZH.items()]}


def list_mine(conn: psycopg.Connection, uid: int) -> list[dict]:
    return [view(r) for r in _rows(conn, uid)]


# ---------------------------------------------------------------- validation
def _text(v, field: str, required: bool = False) -> str | None:
    s = str(v or "").strip()
    if not s:
        if required:
            raise ValueError(L(f"{field} 不能为空", f"{field} is required"))
        return None
    if len(s) > MAX_NAME:
        raise ValueError(L(f"{field} 最多 {MAX_NAME} 个字", f"{field}: at most {MAX_NAME} characters"))
    return s


def _muscles(values, field: str, required: bool = False) -> list[str]:
    out = []
    for v in values or []:
        k = catalog._normalize_filter_muscle(str(v))
        if k not in CD.MUSCLE_ZH:
            raise ValueError(L(f"{field} 只能用动作库的肌群：{v} 不认识（可用：{'、'.join(CD.MUSCLE_ZH)}）",
                               f"{field}: unknown muscle {v} (use one of: {', '.join(CD.MUSCLE_ZH)})"))
        if k not in out:
            out.append(k)
    if required and not out:
        raise ValueError(L("至少要一个主要肌群", "At least one primary muscle is needed"))
    if len(out) > MAX_PRIMARY * 2:
        raise ValueError(L("肌群太多了", "Too many muscles"))
    return out


def _equipment(values) -> list[str]:
    out = []
    for v in values or []:
        k = catalog._normalize_filter_equip(str(v))
        if k not in CD.EQUIPMENT_ZH:
            raise ValueError(L(f"器械不认识：{v}（可用：{'、'.join(CD.EQUIPMENT_ZH)}）", f"Unknown equipment {v} (use one of: {', '.join(CD.EQUIPMENT_ZH)})"))
        if k not in out:
            out.append(k)
    return out


def _clean(d: dict) -> dict:
    """Normalized fields of an add / a full edit (ValueError, in the page language, when something is wrong)."""
    name_zh = _text(d.get("name_zh"), L("中文名", "Chinese name"), required=True)
    name_en = _text(d.get("name_en"), L("英文名", "English name"))
    aliases = list(dict.fromkeys(a for a in (_text(x, L("别名", "Alias")) for x in d.get("aliases") or []) if a))
    if len(aliases) > MAX_ALIASES:
        raise ValueError(L(f"别名最多 {MAX_ALIASES} 个", f"At most {MAX_ALIASES} aliases"))
    prim = _muscles(d.get("primary_muscles"), L("主要肌群", "Primary muscles"), required=True)
    if len(prim) > MAX_PRIMARY:
        raise ValueError(L(f"主要肌群最多 {MAX_PRIMARY} 个", f"At most {MAX_PRIMARY} primary muscles"))
    sec = [m for m in _muscles(d.get("secondary_muscles"), L("次要肌群", "Secondary muscles")) if m not in prim]
    return {"name_zh": name_zh, "name_en": name_en, "aliases": aliases, "primary_muscles": prim, "secondary_muscles": sec,
            "equipment": _equipment(d.get("equipment"))}


def _check_names(conn: psycopg.Connection, uid: int, f: dict, exclude_id: int | None = None) -> None:
    """Refuse names the shared catalog or another of the user's own exercises already has."""
    mine = _norms(_names(f["name_zh"], f["name_en"], f["aliases"]))
    for r in catalog.catalog_rows(conn):
        if r["_names"] & mine:
            raise ValueError(L(f"动作库里已经有「{r['name_zh']}」（{r['slug']}），不用再加；记录时用这个名字就能对上",
                               f"The catalog already has \"{r['name_en']}\" ({r['slug']}); log it under that name"))
    for r in _rows(conn, uid):
        if r["id"] != exclude_id and _norms(_names(r["name_zh"], r["name_en"], r["aliases"])) & mine:
            raise ValueError(L(f"你的动作里已经有「{r['name_zh']}」", f"You already have \"{r['name_zh']}\""))


def _get(conn: psycopg.Connection, uid: int, cid: int) -> dict:
    r = conn.execute(f"SELECT {_COLS} FROM custom_exercises WHERE id = %s AND user_id = %s", (cid, uid)).fetchone()
    if r is None:   # someone else's id looks the same as a missing one
        raise LookupError(L("没有这个自定义动作", "No such custom exercise"))
    return r


def _with_status(conn: psycopg.Connection, uid: int, r: dict) -> dict:
    """view() plus the restriction the user's rules give it (a variant of a banned exercise is banned too)."""
    out = view(r)
    ex = catalog.resolve(conn, slug_of(r["id"]), uid)
    if ex:
        out["status"] = ex["status"]
        if ex.get("restriction_from"):
            out["restriction_from"] = ex["restriction_from"]
    return out


# ---------------------------------------------------------------- writes
def add(conn: psycopg.Connection, uid: int, data: dict, source: str = "manual") -> dict:
    if source not in SOURCES:
        raise ValueError(f"source: {source}")
    f = _clean(data)
    n = conn.execute("SELECT count(*) AS n FROM custom_exercises WHERE user_id = %s", (uid,)).fetchone()["n"]
    if n >= MAX_PER_USER:
        raise ValueError(L(f"自定义动作最多 {MAX_PER_USER} 个", f"At most {MAX_PER_USER} custom exercises"))
    _check_names(conn, uid, f)
    r = conn.execute(f"""INSERT INTO custom_exercises (user_id, name_zh, name_en, aliases, primary_muscles, secondary_muscles, equipment, source)
                         VALUES (%(uid)s, %(name_zh)s, %(name_en)s, %(aliases)s, %(primary_muscles)s, %(secondary_muscles)s, %(equipment)s,
                                 %(source)s) RETURNING {_COLS}""", {**f, "uid": uid, "source": source}).fetchone()
    return _with_status(conn, uid, r)


def update(conn: psycopg.Connection, uid: int, cid: int, data: dict) -> dict:
    """Change fields of one of the user's own exercises (fields not given keep their value)."""
    cur = _get(conn, uid, cid)
    f = _clean({k: data[k] if k in data and data[k] is not None else cur[k]
                for k in ("name_zh", "name_en", "aliases", "primary_muscles", "secondary_muscles", "equipment")})
    _check_names(conn, uid, f, exclude_id=cid)
    r = conn.execute(f"""UPDATE custom_exercises SET name_zh = %(name_zh)s, name_en = %(name_en)s, aliases = %(aliases)s,
                           primary_muscles = %(primary_muscles)s, secondary_muscles = %(secondary_muscles)s, equipment = %(equipment)s,
                           updated_at = now()
                         WHERE id = %(id)s AND user_id = %(uid)s RETURNING {_COLS}""", {**f, "id": cid, "uid": uid}).fetchone()
    return _with_status(conn, uid, r)


def delete(conn: psycopg.Connection, uid: int, cid: int) -> dict:
    """Delete one of the user's own exercises; sets logged under its name are unmatched again (nothing else is touched)."""
    r = _get(conn, uid, cid)
    conn.execute("DELETE FROM custom_exercises WHERE id = %s AND user_id = %s", (cid, uid))
    return {"deleted": view(r)}


# ---------------------------------------------------------------- admin
def admin_names(conn: psycopg.Connection) -> list[dict]:
    """Every custom exercise name with how many users added it (most first). Never says who."""
    rows = conn.execute("""SELECT lower(name_zh) AS k, min(name_zh) AS name_zh, min(name_en) AS name_en,
                                  count(DISTINCT user_id) AS users, max(created_at) AS last_added
                           FROM custom_exercises GROUP BY lower(name_zh) ORDER BY users DESC, k""").fetchall()
    return [{"name_zh": r["name_zh"], "name_en": r["name_en"], "users": r["users"], "last_added": r["last_added"].date().isoformat()}
            for r in rows]
