"""`body-engine sparky-migrate --user-id N [--since D] [--until D] (--dry-run | --confirm)` (issue #252 phase 2).

Copies one user's food library, food entries, weight / body check-ins, exercise library and workouts from SparkyFitness
into the own tables (migration 730), then reads them back and checks them against SparkyFitness table by table.

- Reads with the user's own read-only key (sparky_client.client_for_user): only that user's data is reachable.
- Idempotent: upsert by id, rows SparkyFitness no longer has (origin 'sparky', inside the range) are deleted; a second run
  reports everything unchanged.
- The check compares against what today's readers return (sparky_client._food_entry / _session for entries and workouts,
  the mapping for the rest): dates, names, portions, nutrition and sets rounded to one decimal. Only ids and field names
  are printed, never values.
- --dry-run does all of it inside a transaction that is rolled back. --confirm commits, but only when every table checks
  out; a failing check is rolled back as well. Real runs (ok or not) are logged in own_store_migrations.
  Production runs need the owner's go-ahead on a dry run first (docs/own-food-weight-storage.md §5).
"""
import json
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from .. import sparky_client
from . import mapping as M
from . import store

DEFAULT_SINCE = date(2000, 1, 1)
PAGE = 100
MAX_PAGES = 2000
SAMPLE = 5   # mismatching ids shown per table
TABLES = ("foods", "food_entries", "body_measurements", "user_exercises", "logged_workouts", "logged_workout_exercises",
          "logged_workout_sets")
LIBRARIES = {"foods", "user_exercises"}   # upsert only: rows SparkyFitness no longer lists stay (history refers to them)


class _DryRun(Exception):
    pass


# ---------------------------------------------------------------- reading SparkyFitness
def fetch(client, since: date, until: date) -> dict:
    """Everything of this user in SparkyFitness that the own tables hold (raw API rows)."""
    foods, page = [], 1
    while page <= MAX_PAGES:
        res = client.get("/foods/foods-paginated", searchTerm="", foodFilter="mine", currentPage=page, itemsPerPage=PAGE,
                         sortBy="name:asc", providerType="") or {}
        got = res.get("foods") or []
        foods += got
        total = res.get("totalCount")
        if len(got) < PAGE or (total is not None and len(foods) >= total):
            break
        page += 1
    sessions, page = [], 1
    while page <= MAX_PAGES:
        res = client.get("/v2/exercise-entries/history", page=page, pageSize=PAGE)
        got = res.get("sessions") or []
        sessions += [s for s in got if since <= date.fromisoformat(str(s["entry_date"])[:10]) <= until]
        if not res.get("pagination", {}).get("hasMore") or not got \
                or date.fromisoformat(str(got[-1]["entry_date"])[:10]) < since:
            break
        page += 1
    return {"foods": foods, "exercises": sparky_client.exercises(client),
            "food_entries": client.get(f"/food-entries/range/{since.isoformat()}/{until.isoformat()}") or [],
            "checkins": client.get(f"/measurements/check-in-measurements-range/{since.isoformat()}/{until.isoformat()}") or [],
            "sessions": sessions}


# ---------------------------------------------------------------- what the tables should hold, and what they hold
def _r(v):
    return None if v is None else round(float(v), 1)


def expected(raw: dict, since: date, until: date) -> dict[str, dict]:
    """table -> {key: comparable tuple}, computed from SparkyFitness the way today's readers see it."""
    out = {t: {} for t in TABLES}
    for f in raw["foods"]:
        r = M.food(f)
        out["foods"][r["id"]] = (r["name"], _r(r["calories"]), _r(r["protein_g"]))
    for e in raw["food_entries"]:
        if (f := M.food_from_entry(e)) and f["id"] not in out["foods"]:
            out["foods"][f["id"]] = None   # known only from an entry: existence is checked, not the values
        r = sparky_client._food_entry(e)
        out["food_entries"][str(e["id"])] = (r["date"], r["meal_type"], r["food"] or "", _r(r["quantity"]), r["unit"],
                                             *(_r(r[k]) for k in ("calories", "protein_g", "carbs_g", "fat_g")))
    for c in raw["checkins"]:
        if (m := M.measurement(c)) and since <= m["date"] <= until:
            out["body_measurements"][m["date"].isoformat()] = tuple(_r(m[k]) for k in M.MEASUREMENTS.values())
    for e in raw["exercises"]:
        out["user_exercises"][str(e["id"])] = (e.get("name") or "", e.get("category"))
    for s in raw["sessions"]:
        for x in M._exercise_entries(s):
            if (row := M.exercise_from_snapshot(x)) and row["id"] not in out["user_exercises"]:
                out["user_exercises"][row["id"]] = None
        view = sparky_client._session(s)
        _, exs, sets = M.workout(s)
        out["logged_workouts"][str(s["id"])] = (str(view["date"])[:10], view["name"], len(view["exercises"]))
        for x, v in zip(exs, view["exercises"]):
            out["logged_workout_exercises"][x["id"]] = (x["workout_id"], v["name"], len(v["sets"]))
        for st in sets:
            out["logged_workout_sets"][st["id"]] = (st["exercise_entry_id"], st["set_number"], _r(st["reps"]),
                                                    _r(st["weight_kg"]), _r(st["rir"]), _r(st["duration_s"]))
    return out


def actual(conn: psycopg.Connection, user_id: int, since: date, until: date) -> dict[str, dict]:
    """The same view read back from the own tables (origin 'sparky' rows inside the range; libraries: all of the user's)."""
    q = lambda sql_, *p: conn.execute(sql_, (user_id, *p)).fetchall()
    out = {t: {} for t in TABLES}
    for r in q("SELECT * FROM foods WHERE user_id = %s AND origin = 'sparky'"):
        out["foods"][r["id"]] = (r["name"], _r(r["calories"]), _r(r["protein_g"]))
    for r in q("SELECT * FROM food_entries WHERE user_id = %s AND origin = 'sparky' AND date BETWEEN %s AND %s", since, until):
        out["food_entries"][r["id"]] = (r["date"].isoformat(), r["meal"], r["food_name"], _r(r["quantity"]), r["unit"],
                                        *(_r(r[k]) for k in ("calories", "protein_g", "carbs_g", "fat_g")))
    for r in q("SELECT * FROM body_measurements WHERE user_id = %s AND origin = 'sparky' AND date BETWEEN %s AND %s",
               since, until):
        out["body_measurements"][r["date"].isoformat()] = tuple(_r(r[k]) for k in M.MEASUREMENTS.values())
    for r in q("SELECT * FROM user_exercises WHERE user_id = %s AND origin = 'sparky'"):
        out["user_exercises"][r["id"]] = (r["name"], r["category"])
    for r in q("""SELECT w.*, (SELECT count(*) FROM logged_workout_exercises e WHERE e.workout_id = w.id) AS n
                  FROM logged_workouts w WHERE w.user_id = %s AND w.origin = 'sparky' AND w.date BETWEEN %s AND %s""",
               since, until):
        out["logged_workouts"][r["id"]] = (r["date"].isoformat(), r["name"], r["n"])
    for r in q("""SELECT e.*, (SELECT count(*) FROM logged_workout_sets s WHERE s.exercise_entry_id = e.id) AS n
                  FROM logged_workout_exercises e JOIN logged_workouts w ON w.id = e.workout_id
                  WHERE w.user_id = %s AND w.origin = 'sparky' AND w.date BETWEEN %s AND %s""", since, until):
        out["logged_workout_exercises"][r["id"]] = (r["workout_id"], r["exercise_name"], r["n"])
    for r in q("""SELECT s.* FROM logged_workout_sets s JOIN logged_workout_exercises e ON e.id = s.exercise_entry_id
                  JOIN logged_workouts w ON w.id = e.workout_id
                  WHERE w.user_id = %s AND w.origin = 'sparky' AND w.date BETWEEN %s AND %s""", since, until):
        out["logged_workout_sets"][r["id"]] = (r["exercise_entry_id"], r["set_number"], _r(r["reps"]), _r(r["weight_kg"]),
                                               _r(r["rir"]), _r(r["duration_s"]))
    return out


FIELDS = {
    "foods": ("name", "calories", "protein_g"),
    "food_entries": ("date", "meal", "food", "quantity", "unit", "calories", "protein_g", "carbs_g", "fat_g"),
    "body_measurements": tuple(M.MEASUREMENTS.values()),
    "user_exercises": ("name", "category"),
    "logged_workouts": ("date", "name", "exercises"),
    "logged_workout_exercises": ("workout", "name", "sets"),
    "logged_workout_sets": ("exercise", "set_number", "reps", "weight_kg", "rir", "duration_s"),
}


def compare(table: str, want: dict, have: dict) -> dict:
    missing = [k for k in want if k not in have]
    extra = [k for k in have if k not in want] if table not in LIBRARIES else []
    diff = []
    for k, v in want.items():
        if v is not None and k in have and have[k] != v:
            diff.append({"id": k, "fields": [f for f, a, b in zip(FIELDS[table], v, have[k]) if a != b]})
    return {"ok": not (missing or extra or diff), "missing": missing[:SAMPLE], "extra": extra[:SAMPLE],
            "different": diff[:SAMPLE], "n_missing": len(missing), "n_extra": len(extra), "n_different": len(diff)}


# ---------------------------------------------------------------- run
def user_today(conn: psycopg.Connection, user_id: int) -> date:
    tz = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"]
    return datetime.now(ZoneInfo(tz)).date()


def run(conn: psycopg.Connection, user_id: int, *, since: date | None = None, until: date | None = None,
        dry_run: bool = True, client=None, actor: str | None = None) -> dict:
    if conn.execute("SELECT 1 FROM users WHERE id = %s", (user_id,)).fetchone() is None:
        raise ValueError(f"没有用户 {user_id}")
    since, until = since or DEFAULT_SINCE, until or user_today(conn, user_id)
    if since > until:
        raise ValueError("--since 晚于 --until")
    report = {"user_id": user_id, "since": since.isoformat(), "until": until.isoformat(), "dry_run": dry_run}
    if client is None:
        link = sparky_client.link_state(conn, user_id)
        if link["state"] == "not_linked":
            return {**report, "ok": True, "linked": False, "tables": {}}
        client = sparky_client.client_for_user(conn, user_id)
    raw = fetch(client, since, until)
    want = expected(raw, since, until)
    try:
        with conn.transaction():
            before = actual(conn, user_id, since, until)
            counts: dict[str, dict] = {t: {"inserted": 0, "updated": 0} for t in TABLES}
            for part in (store.apply_foods(conn, user_id, raw["foods"]),
                         store.apply_exercises(conn, user_id, raw["exercises"]),
                         store.apply_food(conn, user_id, since, until, raw["food_entries"]),
                         store.apply_measurements(conn, user_id, since, until, raw["checkins"]),
                         store.apply_workouts(conn, user_id, since, until, raw["sessions"])):
                for t, c in part.items():
                    counts[t]["inserted"] += c["inserted"]
                    counts[t]["updated"] += c["updated"]
            after = actual(conn, user_id, since, until)
            tables = {}
            for t in TABLES:
                n_before, n_after = len(before[t]), len(after[t])
                ins, upd = counts[t]["inserted"], counts[t]["updated"]
                tables[t] = {"sparky": len(want[t]), "before": n_before, "inserted": ins, "updated": upd,
                             "unchanged": max(len(want[t]) - ins - upd, 0), "deleted": n_before + ins - n_after,
                             "after": n_after, **compare(t, want[t], after[t])}
            report.update(linked=True, tables=tables, ok=all(v["ok"] for v in tables.values()))
            if dry_run or not report["ok"]:
                raise _DryRun
    except _DryRun:
        pass
    report["committed"] = not dry_run and report["ok"]
    if not dry_run:
        conn.execute("INSERT INTO own_store_migrations (user_id, since, until, ok, report, actor) VALUES (%s, %s, %s, %s, %s, %s)",
                     (user_id, since, until, report["ok"], Jsonb(report), actor))
    return report


def format_report(rep: dict) -> str:
    head = f"用户 {rep['user_id']}  {rep['since']} → {rep['until']}  " + ("预演（不写入）" if rep["dry_run"] else "正式")
    if not rep.get("linked", True):
        return head + "\n没有连 SparkyFitness，没有要迁的数据"
    cols = ("sparky", "before", "inserted", "updated", "unchanged", "deleted", "after")
    lines = [head, f"{'表':<26}" + "".join(f"{c:>10}" for c in cols) + "  核对"]
    for t, v in rep["tables"].items():
        lines.append(f"{t:<26}" + "".join(f"{v[c]:>10}" for c in cols) + ("  ✓" if v["ok"] else "  ✗"))
    for t, v in rep["tables"].items():
        if not v["ok"]:
            lines.append(f"✗ {t}: 缺 {v['n_missing']} {v['missing']}  多 {v['n_extra']} {v['extra']}  "
                         f"不一致 {v['n_different']} {json.dumps(v['different'], ensure_ascii=False)}")
    if rep["dry_run"]:
        lines.append("预演结束，什么都没写。" + ("全部一致，负责人确认后用 --confirm 正式执行。" if rep["ok"] else "有不一致，先查原因。"))
    else:
        lines.append("已写入并记录。" if rep["committed"] else "核对不通过，已全部回滚（记录在 own_store_migrations）。")
    return "\n".join(lines)


def cli(args) -> int:
    if args.dry_run == args.confirm:
        print("要么 --dry-run（预演），要么 --confirm（负责人确认过预演结果后正式写入），二选一", file=sys.stderr)
        return 2
    from ..db import connect
    from ..rollout import audit
    with connect() as conn:
        rep = run(conn, args.user_id, since=args.since, until=args.until, dry_run=args.dry_run, actor=audit.actor())
    print(json.dumps(rep, ensure_ascii=False, default=str, indent=1) if args.json else format_report(rep))
    return 0 if rep["ok"] else 1
