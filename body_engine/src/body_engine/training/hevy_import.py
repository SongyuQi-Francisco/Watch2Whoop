"""Hevy CSV import (issue #161, rollout flag hevy_import, docs/hevy-benchmark.md §6).

Hevy (Profile → Settings → Export & Import Data → Export Workouts) exports one CSV row per set; one workout is the rows sharing
(title, start_time, end_time). Columns: see params.py (HEVY_*). Times are local wall-clock times without a time zone
("30 Jun 2025, 19:56"), read in the user's time zone.

- Each workout -> one `workouts` row (source 'hevy', external_id = the start time in UTC, type strength_training, or
  cardio_training when it holds only timed / distance exercises). The usual dedup (importer.refresh_duplicates) keeps a
  watch copy of the same workout (it has heart rate); the Hevy sets are then shown on that copy (history.merge_day).
- Its sets -> `training_session_logs` (source 'hevy', status 'imported', one row per local date, each exercise carrying
  `workout` = that external_id). The training page's own rows (source 'page') are never touched.
- Exercise names are matched to the catalog (the user's own answers first, then catalog names / aliases, then Hevy's
  "Name (Equipment)" turned into "Equipment Name"); an unmatched name keeps Hevy's name with slug null and unmatched true,
  and set_mapping() fixes it later for every imported row.
- Idempotent: workouts are upserted by external_id, a day's row is rebuilt from the workouts of the file (other imported
  workouts of that day are kept), so importing the same file twice changes nothing.
- Afterwards the muscle load (daily_scores.muscle_load) of the affected days is recomputed from SparkyFitness + imported
  sets (scores/engine.muscle_sessions, which the nightly scoring uses too), and the load / briefing caches are cleared.
Readers: imported_sessions() returns the same shape as sparky_client.training_log, used by history.build (calendar, day,
the AI's get_training_day / get_training_calendar), loads.load_history (last session, recommended weights) and the scores.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L
from . import catalog, importer, loads
from . import params as P

SOURCE = "hevy"
REQUIRED = ("title", "start_time", "exercise_title")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_HEVY_TIME = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$")
LB_KG = 0.45359237
MI_KM = 1.609344


# ---------------------------------------------------------------- reading the file
def decode(data: bytes) -> str:
    """The upload as text: gzip (the page compresses before sending) or plain, UTF-8 with or without BOM."""
    if data[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
                data = f.read(P.HEVY_MAX_CSV_BYTES + 1)
        except (OSError, EOFError):
            raise ValueError(L("文件解压失败", "The file could not be decompressed"))
    if len(data) > P.HEVY_MAX_CSV_BYTES:
        raise ValueError(L(f"文件太大（最多 {P.HEVY_MAX_CSV_BYTES // 1024 // 1024} MB）",
                           f"The file is too large (at most {P.HEVY_MAX_CSV_BYTES // 1024 // 1024} MB)"))
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(L("文件不是 UTF-8 文本，不像 Hevy 导出的 CSV", "Not UTF-8 text: this does not look like a Hevy CSV export"))


def parse_time(s: str | None, tz: ZoneInfo) -> datetime | None:
    """'30 Jun 2025, 19:56' (Hevy, local time) or ISO 8601 (local unless it has an offset) -> aware datetime."""
    s = (s or "").strip()
    if not s:
        return None
    m = _HEVY_TIME.match(s)
    if m:
        mon = _MONTHS.get(m.group(2).lower())
        if mon is None:
            return None
        try:
            return datetime(int(m.group(3)), mon, int(m.group(1)), int(m.group(4)), int(m.group(5)), int(m.group(6) or 0),
                            tzinfo=tz)
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def _num(row: dict, key: str, kind=float, scale: float = 1.0):
    v = (row.get(key) or "").strip()
    if not v:
        return None
    return kind(float(v)) if kind is int else float(v) * scale


def _set(row: dict) -> dict:
    """One CSV row -> a set in the shape of sparky_client.training_log (kg, km, seconds). ValueError on a bad number."""
    weight = _num(row, "weight_kg") if "weight_kg" in row else _num(row, "weight_lbs", scale=LB_KG)
    dist = _num(row, "distance_km") if "distance_km" in row else _num(row, "distance_miles", scale=MI_KM)
    s = {"set": (_num(row, "set_index", int) or 0) + 1,
         "type": P.HEVY_SET_TYPES.get((row.get("set_type") or "normal").strip().lower(), "normal"),
         "reps": _num(row, "reps", int), "weight_kg": None if weight is None else round(weight, 2),
         "distance_km": None if dist is None else round(dist, 3), "duration_s": _num(row, "duration_seconds"),
         "rpe": _num(row, "rpe")}
    for k, top in P.HEVY_LIMITS.items():
        if s[k] is not None and not 0 <= s[k] <= top:
            raise ValueError(k)
    return {k: v for k, v in s.items() if v is not None}


def parse(text: str, tz: ZoneInfo) -> dict:
    """CSV text -> {workouts: [{key, title, description, start, end, exercises: [{name, notes, superset, sets}]}], rows, skipped}.
    Rows with a bad time or number are skipped (counted); a file without Hevy's columns is refused."""
    reader = csv.DictReader(io.StringIO(text))
    cols = {c.strip().lower() for c in reader.fieldnames or []}
    if not all(c in cols for c in REQUIRED) or not ({"weight_kg", "weight_lbs"} & cols) or "reps" not in cols:
        raise ValueError(L("这不是 Hevy 导出的训练 CSV（缺少 title / start_time / exercise_title / weight_kg / reps 列）",
                           "This is not a Hevy workout export (missing the title / start_time / exercise_title / weight_kg / reps columns)"))
    workouts: dict[tuple, dict] = {}
    rows = skipped = 0
    for raw in reader:
        rows += 1
        if rows > P.HEVY_MAX_ROWS:
            raise ValueError(L(f"行数太多（最多 {P.HEVY_MAX_ROWS} 组）", f"Too many rows (at most {P.HEVY_MAX_ROWS} sets)"))
        row = {(k or "").strip().lower(): (v or "") for k, v in raw.items() if k}
        start = parse_time(row.get("start_time"), tz)
        name = row.get("exercise_title", "").strip()
        if start is None or not name:
            skipped += 1
            continue
        try:
            s = _set(row)
        except ValueError:
            skipped += 1
            continue
        end = parse_time(row.get("end_time"), tz) or start
        key = (row.get("title", "").strip(), start, end)
        w = workouts.get(key)
        if w is None:
            w = workouts[key] = {"key": key, "title": key[0][:120], "description": row.get("description", "").strip()[:1000],
                                 "start": start, "end": max(end, start), "exercises": []}
        exs = w["exercises"]
        last = exs[-1] if exs else None
        # a new exercise block when the name changes or the set numbering starts again
        if last is None or last["name"] != name or (last["sets"] and s["set"] <= last["sets"][-1]["set"]):
            last = {"name": name[:120], "notes": row.get("exercise_notes", "").strip()[:500] or None,
                    "superset": row.get("superset_id", "").strip() or None, "sets": []}
            exs.append(last)
        last["sets"].append(s)
    return {"workouts": sorted(workouts.values(), key=lambda w: w["start"]), "rows": rows, "skipped": skipped}


# ---------------------------------------------------------------- exercise names
def user_maps(conn: psycopg.Connection, user_id: int) -> dict[str, str]:
    return {r["name"]: r["slug"] for r in conn.execute(
        "SELECT name, slug FROM imported_exercise_maps WHERE user_id = %s AND source = %s", (user_id, SOURCE))}


def _equipment(name: str) -> str | None:
    m = re.search(r"\(([^()]+)\)\s*$", name)
    return P.HEVY_EQUIPMENT.get(m.group(1).strip().lower()) if m else None


def candidates(name: str) -> list[str]:
    """Hevy names are 'Bench Press (Barbell)', 'Seated Cable Row - V Grip (Cable)': try the name as 'Barbell Bench Press',
    then without the equipment, then without the grip variant."""
    base = catalog.strip_paren(name)
    m = re.search(r"\(([^()]+)\)\s*$", name)
    out = [f"{m.group(1)} {base}"] if m else []
    out.append(base)
    if " - " in base:
        head = base.split(" - ")[0].strip()
        out += ([f"{m.group(1)} {head}"] if m else []) + [head]
    return list(dict.fromkeys(x for x in out if x and x != name))


class Matcher:
    """Imported exercise name -> catalog slug: the user's own answer, a full-name match, the alias list, then the
    candidates whose catalog row has the bracketed equipment."""

    def __init__(self, conn: psycopg.Connection, user_id: int):
        self.index = loads.Index(conn, user_id)
        self.maps = user_maps(conn, user_id)

    def slug(self, name: str) -> str | None:
        for slug in (self.maps.get(name), P.HEVY_ALIASES.get(" ".join(name.lower().split()))):
            if slug in self.index.rows:
                return slug
        if catalog.norm(name) in self.index.by_name:   # the full name, brackets included, is a catalog name / alias
            return self.index.by_name[catalog.norm(name)]
        equip = _equipment(name)
        for cand in candidates(name):
            slug = self.index.slug_for(None, cand)
            if slug and (equip is None or equip in (self.index.rows[slug].get("equipment") or [equip])):
                return slug
        return None

    def row(self, slug: str | None) -> dict:
        return self.index.rows.get(slug or "") or {}


def _is_cardio(sets: list[dict]) -> bool:
    return not any(s.get("weight_kg") or s.get("reps") for s in sets)


def _exercise(ex: dict, m: Matcher, workout_id: str) -> dict:
    slug = m.slug(ex["name"])
    row = m.row(slug)
    category = row.get("category") or ("cardio" if _is_cardio(ex["sets"]) else "strength")
    out = {"workout": workout_id, "slug": slug, "name": ex["name"], "category": category, "sets": ex["sets"]}
    if slug is None:
        out["unmatched"] = True
    for k in ("notes", "superset"):
        if ex.get(k):
            out[k] = ex[k]
    return out


# ---------------------------------------------------------------- import
def external_id(start: datetime) -> str:
    return start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(conn: psycopg.Connection, user_id: int, data: bytes, sparky=None) -> dict:
    """Import one uploaded export (bytes, gzip or plain). Returns the summary shown on the page (also kept in hevy_imports)."""
    user = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    tz = ZoneInfo((user or {}).get("timezone") or "Australia/Sydney")
    parsed = parse(decode(data), tz)
    if not parsed["workouts"]:
        raise ValueError(L("文件里没有能导入的训练", "No workouts to import in this file"))
    m = Matcher(conn, user_id)
    rows, by_day = [], defaultdict(list)
    for w in parsed["workouts"]:
        ext = external_id(w["start"])
        exs = [_exercise(e, m, ext) for e in w["exercises"]]
        cardio_only = all(e["category"] == "cardio" for e in exs)
        t = "cardio_training" if cardio_only else "strength_training"
        row = importer._base_row(tz, w["start"], w["end"])
        row.update(source=SOURCE, external_id=ext, origin="Hevy", is_xml=False, device=None, type=t,
                   name_zh=w["title"] or importer.type_name(t), active_kcal=None, total_kcal=None, distance_km=None,
                   avg_hr=None, max_hr=None, zone_offset=None)
        if w["description"]:
            row["flags"]["note"] = w["description"]
        rows.append(row)
        by_day[row["local_date"]].append((ext, exs))
    with conn.transaction():
        res = importer.upsert(conn, user_id, rows)
        importer.refresh_duplicates(conn, user_id)
        for d, items in by_day.items():
            _write_day(conn, user_id, d, items)
    days = sorted(by_day)
    muscle = recompute_muscle(conn, user_id, days[0], days[-1], sparky)
    unmatched: dict[str, int] = defaultdict(int)
    for items in by_day.values():
        for _, exs in items:
            for e in exs:
                if e.get("unmatched"):
                    unmatched[e["name"]] += len(e["sets"])
    result = {"workouts": len(rows), "new_workouts": res["inserted"], "sets": sum(len(e["sets"]) for i in by_day.values()
                                                                               for _, exs in i for e in exs),
              "exercises": len({e["name"] for i in by_day.values() for _, exs in i for e in exs}),
              "unmatched": [{"name": n, "sets": c} for n, c in sorted(unmatched.items(), key=lambda kv: -kv[1])],
              "skipped_rows": parsed["skipped"], "start": days[0].isoformat(), "end": days[-1].isoformat(),
              "muscle_load": muscle}
    sha = hashlib.sha256(data).hexdigest()
    with conn.transaction():
        conn.execute("INSERT INTO hevy_imports (user_id, file_sha256, result) VALUES (%s, %s, %s::jsonb)",
                     (user_id, sha, json.dumps(result, ensure_ascii=False)))
        conn.execute("""DELETE FROM hevy_imports WHERE user_id = %s AND id NOT IN
                        (SELECT id FROM hevy_imports WHERE user_id = %s ORDER BY id DESC LIMIT %s)""",
                     (user_id, user_id, P.HEVY_IMPORTS_KEPT))
    _invalidate(user_id)
    return result


def _write_day(conn: psycopg.Connection, user_id: int, d: date, items: list[tuple[str, list[dict]]]) -> None:
    """Rebuild the day's imported row: the workouts in this file replace their own exercises, other workouts of that day stay."""
    old = conn.execute("""SELECT exercises FROM training_session_logs WHERE user_id = %s AND date = %s AND source = %s""",
                       (user_id, d, SOURCE)).fetchone()
    new_ids = {ext for ext, _ in items}
    kept = [e for e in (old["exercises"] if old else []) if e.get("workout") not in new_ids]
    exercises = kept + [e for _, exs in items for e in exs]
    exercises.sort(key=lambda e: e.get("workout") or "")   # stable: the file's order within a workout
    conn.execute(
        """INSERT INTO training_session_logs (user_id, date, source, exercises, status, submitted_at)
           VALUES (%s, %s, %s, %s::jsonb, 'imported', now())
           ON CONFLICT (user_id, date, source) DO UPDATE SET exercises = EXCLUDED.exercises, updated_at = now()
           WHERE training_session_logs.exercises IS DISTINCT FROM EXCLUDED.exercises""",
        (user_id, d, SOURCE, json.dumps(exercises, ensure_ascii=False)))


def _invalidate(user_id: int) -> None:
    from ..assistant import briefing
    loads.clear_history_cache(user_id)
    briefing.invalidate(user_id)


# ---------------------------------------------------------------- reading back
def imported_sessions(conn: psycopg.Connection, user_id: int, start: date, end: date) -> list[dict]:
    """Imported workouts in [start, end] in the shape of sparky_client.training_log (plus workout / source keys)."""
    logs = conn.execute("""SELECT date, exercises FROM training_session_logs WHERE user_id = %s AND source = %s
                           AND date BETWEEN %s AND %s ORDER BY date""", (user_id, SOURCE, start, end)).fetchall()
    if not logs:
        return []
    meta = {r["external_id"]: r for r in conn.execute(
        """SELECT external_id, name_zh, duration_min, flags, excluded FROM workouts WHERE user_id = %s AND source = %s
           AND local_date BETWEEN %s AND %s""", (user_id, SOURCE, start, end))}
    rows = {r["slug"]: r for r in catalog.catalog_rows(conn)}
    out = []
    for log in logs:
        by_w: dict[str, list] = {}
        for e in log["exercises"] or []:
            by_w.setdefault(e.get("workout") or "", []).append(e)
        for ext, exs in by_w.items():
            w = meta.get(ext) or {}
            if w.get("excluded") == "user_hidden":
                continue
            out.append({"id": f"hevy:{ext}", "date": log["date"].isoformat(), "source": SOURCE, "workout": ext,
                        "name": w.get("name_zh"), "total_duration_minutes": round(w["duration_min"]) if w.get("duration_min") else None,
                        "notes": (w.get("flags") or {}).get("note"),
                        "exercises": [{"exercise_id": None, "slug": e.get("slug"), "name": e["name"],
                                       "category": e.get("category") or "strength",
                                       "primary_muscles": (rows.get(e.get("slug") or "") or {}).get("primary_muscles") or [],
                                       "notes": e.get("notes"), "sets": e.get("sets") or [],
                                       **({"unmatched": True} if e.get("unmatched") else {})} for e in exs]})
    return out


def status(conn: psycopg.Connection, user_id: int) -> dict:
    """The page's view: the last import's summary and the names still unmatched (with set counts)."""
    last = conn.execute("SELECT result, created_at FROM hevy_imports WHERE user_id = %s ORDER BY id DESC LIMIT 1",
                        (user_id,)).fetchone()
    return {"last": last and {**last["result"], "imported_at": last["created_at"]}, "unmatched": unmatched(conn, user_id),
            "limits": {"upload_bytes": P.HEVY_MAX_UPLOAD_BYTES, "csv_bytes": P.HEVY_MAX_CSV_BYTES}}


def unmatched(conn: psycopg.Connection, user_id: int) -> list[dict]:
    counts: dict[str, int] = defaultdict(int)
    for r in conn.execute("""SELECT e->>'name' AS name, jsonb_array_length(coalesce(e->'sets', '[]')) AS n
                             FROM training_session_logs, jsonb_array_elements(exercises) e
                             WHERE user_id = %s AND source = %s AND (e->>'unmatched')::boolean""", (user_id, SOURCE)):
        counts[r["name"]] += r["n"]
    return [{"name": n, "sets": c} for n, c in sorted(counts.items(), key=lambda kv: -kv[1])]


def set_mapping(conn: psycopg.Connection, user_id: int, name: str, exercise: str | None) -> dict:
    """The user's answer for an imported name: a catalog exercise (slug or name), or empty to forget the answer.
    Every imported row with that name is updated; the affected days' muscle load is recomputed."""
    name = name.strip()
    if exercise:
        hit = catalog.resolve(conn, exercise, user_id=user_id)
        if not hit or not hit.get("slug"):
            raise LookupError(L(f"动作库里没有「{exercise}」", f"No exercise \"{exercise}\" in the library"))
        slug = hit["slug"]
        conn.execute("""INSERT INTO imported_exercise_maps (user_id, source, name, slug) VALUES (%s, %s, %s, %s)
                        ON CONFLICT (user_id, source, name) DO UPDATE SET slug = EXCLUDED.slug, updated_at = now()""",
                     (user_id, SOURCE, name, slug))
    else:
        conn.execute("DELETE FROM imported_exercise_maps WHERE user_id = %s AND source = %s AND name = %s",
                     (user_id, SOURCE, name))
    m = Matcher(conn, user_id)
    slug = m.slug(name)
    row = m.row(slug)
    days = []
    for log in conn.execute("""SELECT date, exercises FROM training_session_logs WHERE user_id = %s AND source = %s
                               AND exercises @> %s::jsonb""", (user_id, SOURCE, json.dumps([{"name": name}]))).fetchall():
        exs = []
        for e in log["exercises"]:
            if e.get("name") == name:
                e = {k: v for k, v in e.items() if k != "unmatched"}
                e["slug"] = slug
                e["category"] = row.get("category") or ("cardio" if _is_cardio(e.get("sets") or []) else "strength")
                if slug is None:
                    e["unmatched"] = True
            exs.append(e)
        conn.execute("""UPDATE training_session_logs SET exercises = %s::jsonb, updated_at = now()
                        WHERE user_id = %s AND date = %s AND source = %s""",
                     (json.dumps(exs, ensure_ascii=False), user_id, log["date"], SOURCE))
        days.append(log["date"])
    if days:
        recompute_muscle(conn, user_id, min(days), max(days))
    _invalidate(user_id)
    return {"name": name, "slug": slug, "name_zh": row.get("name_zh"), "rows": len(days), "unmatched": unmatched(conn, user_id)}


# ---------------------------------------------------------------- muscle load of the affected days
def recompute_muscle(conn: psycopg.Connection, user_id: int, start: date, end: date, sparky=None) -> dict:
    """daily_scores.muscle_load / details.muscle for [start, end] from SparkyFitness + imported sets (same as the nightly
    scoring). Days without a scores row are left to the nightly run. SparkyFitness linked but unreadable: nothing is changed."""
    from openbodyscore.muscle import daily_muscle_load
    from psycopg.types.json import Jsonb

    from ..scores import engine
    sessions, error, _ = engine.muscle_sessions(conn, user_id, start, end, sparky)
    if error:
        return {"updated_days": 0, "error": error}
    by_day = daily_muscle_load(sessions)
    n = 0
    with conn.transaction():
        for r in conn.execute("""SELECT date, muscle_load, details FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s""",
                              (user_id, start, end)).fetchall():
            m = by_day.get(r["date"])
            details = dict(r["details"] or {})
            details.pop("muscle", None)
            if m:
                details["muscle"] = m
            load = m["volume_kg"] if m else None
            if load != r["muscle_load"] or details != (r["details"] or {}):
                conn.execute("UPDATE daily_scores SET muscle_load = %s, details = %s WHERE user_id = %s AND date = %s",
                             (load, Jsonb(details), user_id, r["date"]))
                n += 1
    return {"updated_days": n}
