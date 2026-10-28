"""Storage back end on the own tables (issue #252 phase 4, flag own_food_only; docs/own-food-weight-storage.md §3, §4).

assistant/sparky_write.py keeps every check (dates, nutrition adding up, banned exercises, duplicates, weight conflicts) and
talks to its store only through the operations below; `sparky_write.backend()` picks this class when the user's writes are
own only (ownstore/source.only), else the SparkyFitness one. Records in and out are the shape rows.py builds (the same as
before), so the write log, undo by id, rollback (also of rows logged before the switch: ids are kept) and food merges work
on either store.

Every statement is scoped to the user; ids of new rows are uuids. A row changed here becomes origin 'own', so a later
`sparky-migrate` or sync never overwrites or deletes it.
"""
import uuid
from datetime import date

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from . import mapping as M
from . import rows as R

FOOD_NOTE = "身体管家记录饮食时建（营养是估算）"
EXERCISE_NOTE = "身体管家记录训练时新建（用户确认过）"


def _new_id() -> str:
    return str(uuid.uuid4())


def _insert(conn: psycopg.Connection, table: str, user_id: int, row: dict, own: bool = True) -> None:
    cols = ["user_id", *row]
    vals = [user_id, *(Jsonb(v) if k == "nutrients" else v for k, v in row.items())]
    if own:
        cols.append("origin")
        vals.append("own")
    conn.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder() * len(cols))), vals)


class OwnBackend:
    own = True

    def __init__(self, conn: psycopg.Connection, user_id: int):
        self.conn, self.user_id = conn, user_id

    def _one(self, q: str, *params):
        return self.conn.execute(q, (*params,)).fetchone()

    def after_write(self, **_dates) -> None:
        """Nothing to sync: the own tables are the store."""

    # ---------------------------------------------------------------- food
    def prepare_food(self) -> None:
        pass

    def food_day(self, d: date) -> list[dict]:
        return R.food_day(self.conn, self.user_id, d)

    def _food_record(self, entry_id: str) -> dict:
        r = self._one("SELECT * FROM food_entries WHERE id = %s AND user_id = %s", entry_id, self.user_id)
        return R.food_record(r)

    def _ensure_food(self, it: dict, ext_id: str) -> str:
        """The food library row for this portion (1 serving = this portion), reused by content like before."""
        hit = self._one("SELECT id FROM foods WHERE user_id = %s AND provider_external_id = %s ORDER BY created_at LIMIT 1",
                        self.user_id, ext_id)
        if hit:
            return hit["id"]
        fid = _new_id()
        _insert(self.conn, "foods", self.user_id, {
            "id": fid, "name": it["food"].strip(), "serving_size": 1, "serving_unit": it["amount"].strip(),
            "calories": it["calories"], "protein_g": it["protein_g"], "carbs_g": it["carbs_g"], "fat_g": it["fat_g"],
            "saturated_fat_g": it.get("saturated_fat_g"), "sugar_g": it.get("sugar_g"), "fiber_g": it.get("fiber_g"),
            "sodium_mg": it.get("sodium_mg"), "alcohol_g": it.get("alcohol_g"), "nutrients": {},
            "provider_type": "custom", "provider_external_id": ext_id, "is_custom": True, "notes": FOOD_NOTE})
        return fid

    def create_food(self, d: date, it: dict) -> dict:
        """One entry for a checked item (log_food / a merge): this portion's totals. A retry with the same source_id
        returns the entry already written instead of a second one."""
        from ..assistant.sparky_write import SOURCE, _food_ext_id, _food_source_id
        ext_id, source, source_id = _food_ext_id(it), SOURCE, _food_source_id(d, it)
        same = self._one("SELECT id FROM food_entries WHERE user_id = %s AND source_id = %s LIMIT 1", self.user_id, source_id)
        if same:
            return self._food_record(same["id"])
        eid = _new_id()
        _insert(self.conn, "food_entries", self.user_id, {
            "id": eid, "date": d, "time": it.get("time"), "meal": it["meal"], "food_id": self._ensure_food(it, ext_id),
            "food_name": it["food"].strip(), "quantity": 1, "unit": it["amount"].strip(), "serving_size": 1,
            "calories": it["calories"], "protein_g": it["protein_g"], "carbs_g": it["carbs_g"], "fat_g": it["fat_g"],
            "saturated_fat_g": it.get("saturated_fat_g"), "sugar_g": it.get("sugar_g"), "fiber_g": it.get("fiber_g"),
            "sodium_mg": it.get("sodium_mg"), "alcohol_g": it.get("alcohol_g"), "added_sugar_g": it.get("added_sugar_g"),
            "sugary_drink": it.get("sugary_drink"), "nutrients": {}, "notes": it.get("notes"), "source": source,
            "source_id": source_id})
        return self._food_record(eid)

    def delete_food(self, row: dict) -> None:
        self.conn.execute("DELETE FROM food_entries WHERE id = %s AND user_id = %s", (str(row["id"]), self.user_id))

    def update_food(self, row: dict, body: dict, meal_id: bool = True) -> dict:
        """In place (same id): quantity / date / meal / time / notes; the totals follow the quantity."""
        cur = self._one("SELECT * FROM food_entries WHERE id = %s AND user_id = %s", str(row["id"]), self.user_id)
        if cur is None:
            raise ValueError(f"没有这条饮食记录：{row['id']}")
        old_q = float(cur["quantity"] or 1) or 1
        new_q = float(body["quantity"]) if body.get("quantity") is not None else old_q
        f = new_q / old_q
        cols = [c for c in M.NUTRIENTS.values()] + ["added_sugar_g"]
        scaled = {c: None if cur[c] is None else cur[c] * f for c in cols}
        nutrients = {k: v * f for k, v in (cur["nutrients"] or {}).items() if isinstance(v, (int, float))}
        self.conn.execute(
            sql.SQL("UPDATE food_entries SET quantity = %s, unit = COALESCE(%s, unit), date = %s, meal = %s, time = COALESCE(%s, time), "
                    "notes = COALESCE(%s, notes), nutrients = %s, {}, origin = 'own', synced_at = NULL, updated_at = now() "
                    "WHERE id = %s AND user_id = %s").format(
                sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in cols)),
            (new_q, body.get("unit"), body.get("entry_date") or cur["date"], body.get("meal_type") or cur["meal"],
             body.get("entry_time"), body.get("notes"), Jsonb(nutrients), *scaled.values(),
             cur["id"], self.user_id))
        return self._food_record(cur["id"])

    def recreate_food(self, before: dict) -> dict:
        """Put back a deleted / merged entry (any record, also one logged before the switch) under a new id."""
        ex = self._one("SELECT * FROM food_entry_extras WHERE user_id = %s AND entry_id = %s", self.user_id, str(before["id"]))
        row = M.food_entry(before, ex)
        row.update(id=_new_id(), source_id=None)
        if row["food_id"] is not None and not self._one("SELECT 1 FROM foods WHERE user_id = %s AND id = %s",
                                                        self.user_id, row["food_id"]):
            row["food_id"] = None
        _insert(self.conn, "food_entries", self.user_id, row)
        return self._food_record(row["id"])

    # ---------------------------------------------------------------- exercise library
    def library(self) -> list[dict]:
        return R.exercises(self.conn, self.user_id)

    def create_exercise(self, name: str) -> dict:
        eid = _new_id()
        _insert(self.conn, "user_exercises", self.user_id, {
            "id": eid, "name": name, "category": "strength", "modality": "weight_reps", "description": EXERCISE_NOTE})
        return {"id": eid, "name": name}

    # ---------------------------------------------------------------- workouts
    def workout_day(self, d: date) -> list[dict]:
        return R.session_records(self.conn, self.user_id, d)

    def _snapshot(self, exercise_id) -> dict:
        r = self._one("SELECT name, category, primary_muscles FROM user_exercises WHERE user_id = %s AND id = %s",
                      self.user_id, str(exercise_id)) if exercise_id is not None else None
        return {"name": r["name"], "category": r["category"], "primary_muscles": list(r["primary_muscles"] or [])} if r else {}

    def _insert_session(self, rec: dict) -> dict:
        """A session record (with fresh ids) -> logged_workouts / _exercises / _sets."""
        w, exs, sets = M.workout(rec)
        _insert(self.conn, "logged_workouts", self.user_id, {k: w[k] for k in ("id", *M.WORKOUT_COLS)})
        for e in exs:
            _insert(self.conn, "logged_workout_exercises", self.user_id, {k: e[k] for k in ("id", *M.WORKOUT_EXERCISE_COLS)},
                    own=False)
        for s in sets:
            _insert(self.conn, "logged_workout_sets", self.user_id, {k: s[k] for k in ("id", *M.SET_COLS)}, own=False)
        return R.session_records(self.conn, self.user_id, w["date"], ids=[w["id"]])[0]

    def _fresh_sets(self, sets: list[dict]) -> list[dict]:
        return [{**s, "id": _new_id(), "set_number": s.get("set_number") or n + 1} for n, s in enumerate(sets)]

    def create_session(self, body: dict) -> dict:
        """log_workout's session: {entry_date, name, notes, source, exercises: [{exercise_id, sort_order, notes, sets}]}."""
        wid = _new_id()
        return self._insert_session({
            "id": wid, "type": "preset", "entry_date": body["entry_date"], "name": body.get("name"), "notes": body.get("notes"),
            "source": body.get("source"), "total_duration_minutes": None,
            "exercises": [{"id": _new_id(), "exercise_id": e["exercise_id"], "sort_order": e.get("sort_order", i),
                           "exercise_snapshot": self._snapshot(e["exercise_id"]), "notes": e.get("notes"),
                           "duration_minutes": e.get("duration_minutes"), "sets": self._fresh_sets(e.get("sets") or [])}
                          for i, e in enumerate(body["exercises"])]})

    def recreate_session(self, before: dict) -> dict:
        """Put back a deleted session (any record) under new ids."""
        rec = {**before, "id": _new_id()}
        if rec.get("type", "preset") == "preset":
            rec["exercises"] = [{**e, "id": _new_id(), "sets": self._fresh_sets(e.get("sets") or [])}
                                for e in before.get("exercises") or []]
        else:
            rec["sets"] = self._fresh_sets(before.get("sets") or [])
        return self._insert_session(rec)

    def delete_session(self, row: dict) -> None:
        self.delete_session_id(row["id"])

    def delete_session_id(self, session_id) -> None:
        self.conn.execute("DELETE FROM logged_workouts WHERE id = %s AND user_id = %s", (str(session_id), self.user_id))

    def replace_sets(self, entry_id, sets: list[dict]) -> None:
        """All sets of one exercise entry, replaced (new ids), like the SparkyFitness PUT."""
        e = self._one("SELECT id, workout_id FROM logged_workout_exercises WHERE id = %s AND user_id = %s",
                      str(entry_id), self.user_id)
        if e is None:
            raise ValueError("这个动作已经不在了")
        self.conn.execute("DELETE FROM logged_workout_sets WHERE exercise_entry_id = %s AND user_id = %s", (e["id"], self.user_id))
        for n, x in enumerate(sorted(self._fresh_sets(sets), key=lambda x: x["set_number"])):
            s = M.set_row(x, e["id"], n)
            _insert(self.conn, "logged_workout_sets", self.user_id, {k: s[k] for k in ("id", *M.SET_COLS)}, own=False)
        self.conn.execute("UPDATE logged_workouts SET origin = 'own', synced_at = NULL, updated_at = now() "
                          "WHERE id = %s AND user_id = %s", (e["workout_id"], self.user_id))

    # ---------------------------------------------------------------- weight / body measurements
    def checkin(self, d: date | str) -> dict:
        r = self._one("SELECT * FROM body_measurements WHERE user_id = %s AND date = %s", self.user_id, d)
        return R.checkin_record(r) if r else {}

    def save_checkin(self, d: date | str, vals: dict) -> None:
        """Set the given values of that day's check-in (record keys: weight, waist, …; others such as steps are not ours)."""
        cols = {M.MEASUREMENTS[k]: v for k, v in vals.items() if k in M.MEASUREMENTS}
        if not cols:
            return
        names = list(cols)
        self.conn.execute(
            sql.SQL("INSERT INTO body_measurements (user_id, date, id, origin, {cols}) VALUES (%s, %s, %s, 'own', {ph}) "
                    "ON CONFLICT (user_id, date) DO UPDATE SET {sets}, origin = 'own', synced_at = NULL, updated_at = now()").format(
                cols=sql.SQL(", ").join(map(sql.Identifier, names)),
                ph=sql.SQL(", ").join(sql.Placeholder() * len(names)),
                sets=sql.SQL(", ").join(sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in names)),
            (self.user_id, d, _new_id(), *cols.values()))

    def delete_checkin(self, cur: dict) -> None:
        self.conn.execute("DELETE FROM body_measurements WHERE user_id = %s AND date = %s",
                          (self.user_id, str(cur["entry_date"])[:10]))
