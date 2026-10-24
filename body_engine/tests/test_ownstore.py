"""Own storage for food / weight / workout logs (issue #252 phase 2): the mapping from SparkyFitness rows, the dual write
behind own_food_store (off = not a single extra request), and `sparky-migrate` (dry run writes nothing, a real run copies and
checks every table, reruns are no-ops, deletions / edits in SparkyFitness follow, a failed check rolls back, ids of another
user are never overwritten). SparkyFitness is a stateful fake; no real SparkyFitness is contacted.
Needs Postgres: separate test database BODY_TEST_DB_NAME_OWNSTORE (default body_test_ownstore), dropped and recreated."""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from body_engine.assistant import sparky_write, writelog
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.ownstore import mapping as M
from body_engine.ownstore import migrate as om
from body_engine.ownstore import mirror, store
from body_engine.rollout import flags
from psycopg.rows import dict_row

from body_engine import sparky_client

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_OWNSTORE", "body_test_ownstore")
TZ = "Australia/Sydney"


def _today() -> date:
    return datetime.now(ZoneInfo(TZ)).date()


class FakeSparky:
    """One user's SparkyFitness, stateful enough for log_food / log_workout / log_weight / undo and the migration reads.
    `calls` counts every request, so "flag off = no extra request" can be checked."""

    def __init__(self, prefix="u"):
        self.p = prefix
        self.food: dict[str, dict] = {}       # id -> entry (per-serving nutrition, like SparkyFitness)
        self.foods: list[dict] = []
        self.sessions: dict[str, dict] = {}
        self.checkins: dict[str, dict] = {}   # date -> check-in
        self.library = [{"id": f"{prefix}-ex-bench", "name": "卧推", "category": "strength", "primary_muscles": '["chest"]',
                         "description": None},
                        {"id": f"{prefix}-ex-row", "name": "划船", "category": "strength", "primary_muscles": ["lats"]}]
        self.calls: list[tuple] = []
        self.n = 0
        self.fail_reads = False   # by-date reads fail once something was written (the sync after a write, not its own checks)
        self.wrote = False

    def _id(self, kind):
        self.n += 1
        return f"{self.p}-{kind}-{self.n}"

    # seeding helpers
    def add_food(self, day, name, meal="lunch", qty=2, serving=1, cal=150, p=10, c=12, f=6, **kw):
        rid = self._id("fe")
        self.food[rid] = {"id": rid, "entry_date": day.isoformat(), "entry_time": "12:30:00", "meal_type": meal,
                          "food_name": name, "quantity": qty, "serving_size": serving, "unit": "份", "calories": cal,
                          "protein": p, "carbs": c, "fat": f, "saturated_fat": kw.get("sat"), "sugars": kw.get("sugar"),
                          "dietary_fiber": None, "sodium": None, "cholesterol": kw.get("chol"),
                          "food_id": kw.get("food_id", f"{self.p}-food-{name}"), "variant_id": "v", "notes": None,
                          "source": "app", "source_id": f"src-{rid}"}
        return rid

    def add_session(self, day, name="上肢", sets=((10, 40, 2), (8, 42.5, 1))):
        sid = self._id("ws")
        self.sessions[sid] = {"id": sid, "type": "preset", "entry_date": day.isoformat(), "name": name, "notes": None,
                              "source": "app", "total_duration_minutes": 50, "exercises": [
                                  {"id": self._id("xe"), "exercise_id": f"{self.p}-ex-bench",
                                   "exercise_snapshot": {"name": "卧推", "category": "strength",
                                                         "primary_muscles": ["chest"]},
                                   "sets": [{"id": self._id("set"), "set_number": i + 1, "set_type": "Working Set",
                                             "reps": r, "weight": w, "rir": rir} for i, (r, w, rir) in enumerate(sets)]}]}
        return sid

    def add_single(self, day):
        sid = self._id("xe")
        self.sessions[sid] = {"id": sid, "type": "individual", "entry_date": day.isoformat(), "exercise_id": "public-ex-run",
                              "exercise_snapshot": {"name": "爬坡走", "category": "cardio"}, "duration_minutes": 30,
                              "calories_burned": 250, "source": "app", "sets": []}
        return sid

    # API
    def get(self, path, **params):
        self.calls.append(("GET", path))
        if self.fail_reads and self.wrote and "by-date" in path:
            raise sparky_client.SparkyError("down")
        if path.startswith("/food-entries/by-date/"):
            d = path.rsplit("/", 1)[1]
            return [dict(r) for r in self.food.values() if r["entry_date"] == d]
        if path.startswith("/food-entries/range/"):
            s, e = path.split("/")[-2:]
            return [dict(r) for r in self.food.values() if s <= r["entry_date"] <= e]
        if path == "/foods/foods-paginated":
            return {"foods": self.foods, "totalCount": len(self.foods)}
        if path == "/meal-types":
            return [{"id": f"mt-{m}", "name": m} for m in sparky_write.MEALS]
        if path == "/v2/exercise-entries/by-date":
            return [s for s in self.sessions.values() if s["entry_date"] == params["selectedDate"]]
        if path == "/v2/exercise-entries/history":
            rows = sorted(self.sessions.values(), key=lambda s: s["entry_date"], reverse=True)
            return {"sessions": rows, "pagination": {"hasMore": False}}
        if path == "/exercises":
            return {"exercises": self.library, "totalCount": len(self.library)}
        if path.startswith("/measurements/check-in-measurements-range/"):
            s, e = path.split("/")[-2:]
            return [c for d, c in self.checkins.items() if s <= d <= e]
        if path.startswith("/measurements/check-in/"):
            d = path.rsplit("/", 1)[1]
            return self.checkins.get(d) or {}
        raise AssertionError(path)

    def request(self, method, path, body=None, params=None, multipart_field=None):
        self.calls.append((method, path))
        self.wrote = True
        if method == "POST" and path == "/foods":
            f = {"id": self._id("food"), "default_variant": {"id": "v", "serving_size": body["serving_size"],
                                                             "serving_unit": body["serving_unit"],
                                                             **{k: body.get(k) for k in M.NUTRIENTS}}, **body}
            self.foods.append(f)
            return f
        if method == "POST" and path == "/food-entries":
            food = next(f for f in self.foods if f["id"] == body["food_id"])
            rid = self._id("fe")
            self.food[rid] = {"id": rid, "entry_date": body["entry_date"], "entry_time": body.get("entry_time"),
                              "meal_type": body["meal_type"], "food_name": food["name"], "quantity": body["quantity"],
                              "serving_size": 1, "unit": body["unit"], "food_id": food["id"], "variant_id": "v",
                              "source": body.get("source"), "source_id": body.get("source_id"), "notes": body.get("notes"),
                              **{k: food.get(k) for k in M.NUTRIENTS}}
            return dict(self.food[rid])
        if method == "DELETE" and path.startswith("/food-entries/"):
            self.food.pop(path.rsplit("/", 1)[1], None)
            return None
        if method == "POST" and path == "/exercise-preset-entries":
            sid = self._id("ws")
            lib = {e["id"]: e for e in self.library}
            self.sessions[sid] = {"id": sid, "type": "preset", "entry_date": body["entry_date"], "name": body["name"],
                                  "source": body.get("source"), "exercises": [
                                      {"id": self._id("xe"), "exercise_id": e["exercise_id"],
                                       "exercise_snapshot": {"name": lib[e["exercise_id"]]["name"]},
                                       "sets": [{"id": self._id("set"), **s} for s in e["sets"]]} for e in body["exercises"]]}
            return self.sessions[sid]
        if method == "DELETE" and path.startswith("/exercise-preset-entries/"):
            self.sessions.pop(path.rsplit("/", 1)[1], None)
            return None
        if method == "POST" and path == "/measurements/check-in":
            d = body["entry_date"]
            cur = self.checkins.get(d) or {"id": self._id("ci"), "entry_date": d}
            self.checkins[d] = {**cur, **body}
            return self.checkins[d]
        if method == "DELETE" and path.startswith("/measurements/check-in/"):
            cid = path.rsplit("/", 1)[1]
            self.checkins = {d: c for d, c in self.checkins.items() if c["id"] != cid}
            return None
        raise AssertionError((method, path))


# ---------------------------------------------------------------- mapping (no database)
def test_food_entry_totals_match_todays_reader():
    fake = FakeSparky()
    rid = fake.add_food(date(2026, 10, 1), "燕麦", qty=3, serving=2, cal=150, p=5, c=27, f=3, sat=0.5, sugar=1, chol=4)
    raw = fake.food[rid]
    row, view = M.food_entry(raw, {"added_sugar_g": 0.5, "sugar_g": 9, "sugary_drink": False}), sparky_client._food_entry(raw)
    for col, k in (("calories", "calories"), ("protein_g", "protein_g"), ("carbs_g", "carbs_g"), ("fat_g", "fat_g"),
                   ("saturated_fat_g", "saturated_fat_g"), ("sugar_g", "sugar_g")):
        assert round(row[col], 1) == view[k]
    assert row["calories"] == 225 and row["nutrients"] == {"cholesterol": 6.0}
    assert row["sugar_g"] == 1.5           # SparkyFitness has a value: it wins over the extras
    assert row["added_sugar_g"] == 0.5 and row["sugary_drink"] is False   # only in the extras
    assert row["date"] == date(2026, 10, 1) and row["time"] == "12:30" and row["food_id"] == "u-food-燕麦"


def test_workout_and_measurement_mapping():
    fake = FakeSparky()
    sid = fake.add_session(date(2026, 10, 2))
    w, exs, sets = M.workout(fake.sessions[sid])
    assert w["kind"] == "preset" and w["name"] == "上肢" and w["total_duration_min"] == 50
    assert [e["exercise_name"] for e in exs] == ["卧推"] and exs[0]["primary_muscles"] == ["chest"]
    assert [(s["set_number"], s["reps"], s["weight_kg"], s["rir"]) for s in sets] == [(1, 10, 40, 2), (2, 8, 42.5, 1)]
    single = M.workout(fake.sessions[fake.add_single(date(2026, 10, 2))])
    assert single[0]["kind"] == "single" and single[0]["name"] == "爬坡走" and single[1][0]["id"] == single[0]["id"]
    assert M.measurement({"id": 1, "entry_date": "2026-10-02", "steps": 9000}) is None
    m = M.measurement({"id": 7, "entry_date": "2026-10-02T00:00:00", "weight": "88.4", "waist": 90})
    assert m == {"date": date(2026, 10, 2), "id": "7", **{c: None for c in M.MEASUREMENTS.values()},
                 "weight_kg": 88.4, "waist_cm": 90.0}
    assert M.exercise(fake.library[0])["primary_muscles"] == ["chest"]


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # no password file, or the database is not running
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


def _user(conn, name):
    return conn.execute("INSERT INTO users (display_name, birth_date, height_cm, timezone) VALUES (%s, '1990-01-01', 175, %s) "
                        "RETURNING id", (name, TZ)).fetchone()["id"]


def _count(conn, table, uid):
    return conn.execute(f"SELECT count(*) AS n FROM {table} WHERE user_id = %s", (uid,)).fetchone()["n"]


def _seed(fake):
    d1, d2 = _today() - timedelta(days=3), _today() - timedelta(days=1)
    fake.foods.append({"id": "u-food-燕麦", "name": "燕麦", "is_custom": True, "provider_type": "custom",
                       "default_variant": {"serving_size": 40, "serving_unit": "g", "calories": 150, "protein": 5}})
    fake.add_food(d1, "燕麦", meal="breakfast")
    fake.add_food(d1, "鸡胸", meal="dinner", food_id="public-food-1")   # a public food: not in "my foods"
    fake.add_food(d2, "酸奶", meal="snacks")
    fake.add_session(d1)
    fake.add_single(d2)
    fake.checkins[d1.isoformat()] = {"id": "ci-1", "entry_date": d1.isoformat(), "weight": 88.2, "body_fat_percentage": 22}
    fake.checkins[d2.isoformat()] = {"id": "ci-2", "entry_date": d2.isoformat(), "steps": 8000}   # steps only: not ours
    return d1, d2


def test_migrate_dry_run_then_confirm_then_rerun(conn):
    uid = _user(conn, "mig")
    fake = FakeSparky()
    d1, _ = _seed(fake)
    conn.execute("INSERT INTO food_entry_extras (user_id, entry_id, date, added_sugar_g, sugary_drink, source) "
                 "VALUES (%s, %s, %s, 3, TRUE, 'log_food')", (uid, next(iter(fake.food)), d1))

    dry = om.run(conn, uid, dry_run=True, client=fake)
    assert dry["ok"] and not dry["committed"] and dry["tables"]["food_entries"]["inserted"] == 3
    assert all(_count(conn, t, uid) == 0 for t in om.TABLES) and _count(conn, "own_store_migrations", uid) == 0

    rep = om.run(conn, uid, dry_run=False, client=fake, actor="test")
    t = rep["tables"]
    assert rep["ok"] and rep["committed"], om.format_report(rep)
    assert (t["food_entries"]["sparky"], t["food_entries"]["after"]) == (3, 3)
    assert t["foods"]["after"] == 3 and t["foods"]["ok"]            # 燕麦 (library) + 鸡胸 / 酸奶 known from entries
    assert t["body_measurements"]["after"] == 1                     # the steps-only check-in is not ours
    assert (t["logged_workouts"]["after"], t["logged_workout_exercises"]["after"], t["logged_workout_sets"]["after"]) == (2, 2, 2)
    assert t["user_exercises"]["after"] == 3                        # 2 of mine + the public one a session used
    oats = conn.execute("SELECT * FROM food_entries WHERE user_id = %s AND food_name = '燕麦'", (uid,)).fetchone()
    assert oats["calories"] == 300 and oats["added_sugar_g"] == 3 and oats["sugary_drink"] and oats["origin"] == "sparky"
    lib = conn.execute("SELECT * FROM foods WHERE user_id = %s AND id = 'u-food-燕麦'", (uid,)).fetchone()
    assert (lib["serving_size"], lib["serving_unit"], lib["calories"]) == (40, "g", 150)   # the full library row won
    log = conn.execute("SELECT * FROM own_store_migrations WHERE user_id = %s", (uid,)).fetchall()
    assert len(log) == 1 and log[0]["ok"] and log[0]["actor"] == "test"
    assert "✓" in om.format_report(rep) and "88" not in om.format_report(rep)   # counts only, no health values

    again = om.run(conn, uid, dry_run=False, client=fake)
    assert again["ok"] and all(v["inserted"] == v["updated"] == v["deleted"] == 0 for v in again["tables"].values())

    # Changes made in SparkyFitness follow: an edit, a deletion, a new set
    fid = next(k for k, v in fake.food.items() if v["food_name"] == "酸奶")
    fake.food[fid]["quantity"] = 3
    fake.food.pop(next(k for k, v in fake.food.items() if v["food_name"] == "鸡胸"))
    sess = next(s for s in fake.sessions.values() if s["type"] == "preset")
    sess["exercises"][0]["sets"].append({"id": "u-set-new", "set_number": 3, "reps": 6, "weight": 45, "rir": 0})
    rep = om.run(conn, uid, dry_run=False, client=fake)
    t = rep["tables"]
    assert rep["ok"] and t["food_entries"]["updated"] == 1 and t["food_entries"]["deleted"] == 1
    assert t["logged_workout_sets"]["inserted"] == 1 and t["logged_workout_sets"]["after"] == 3
    assert t["foods"]["deleted"] == 0                               # libraries are upsert only


def test_failed_check_rolls_back_and_is_logged(conn, monkeypatch):
    uid = _user(conn, "bad")
    fake = FakeSparky("b")
    _seed(fake)
    real = store.apply_food
    monkeypatch.setattr(store, "apply_food", lambda c, u, s, e, rows: real(c, u, s, e, rows[1:]))   # "loses" one entry
    rep = om.run(conn, uid, dry_run=False, client=fake)
    assert not rep["ok"] and not rep["committed"]
    assert rep["tables"]["food_entries"]["n_missing"] == 1 and "✗" in om.format_report(rep)
    assert all(_count(conn, t, uid) == 0 for t in om.TABLES)
    assert conn.execute("SELECT ok FROM own_store_migrations WHERE user_id = %s", (uid,)).fetchone()["ok"] is False


def test_ids_of_another_user_are_never_overwritten(conn):
    a, b = _user(conn, "own-a"), _user(conn, "own-b")
    fake = FakeSparky("same")
    _seed(fake)
    assert om.run(conn, a, dry_run=False, client=fake)["ok"]
    with pytest.raises(ValueError, match="another user"):
        om.run(conn, b, dry_run=False, client=fake)   # the same entry ids under someone else
    assert _count(conn, "food_entries", b) == 0 and _count(conn, "food_entries", a) == 3


def test_not_linked_user_has_nothing_to_migrate(conn):
    uid = _user(conn, "nolink")
    rep = om.run(conn, uid, dry_run=True)
    assert rep["ok"] and rep["linked"] is False and "没有要迁的数据" in om.format_report(rep)


def test_cli_needs_exactly_one_mode(capsys):
    class A:
        dry_run = confirm = False
    assert om.cli(A()) == 2
    A.dry_run = A.confirm = True
    assert om.cli(A()) == 2


# ---------------------------------------------------------------- dual write
def _food_item(d, **kw):
    return {"date": d.isoformat(), "meal": "lunch", "time": "12:00", "food": "牛肉饭", "amount": "1 碗", "calories": 650,
            "protein_g": 35, "carbs_g": 80, "fat_g": 20, "saturated_fat_g": 7, "alcohol_g": None, "notes": None,
            "added_sugar_g": 2, "sugar_g": 4, **kw}


def test_flag_off_changes_nothing(conn):
    uid = _user(conn, "off")
    fake = FakeSparky("off")
    d = _today()
    sparky_write.log_food(conn, uid, [_food_item(d)], False, client=fake)
    sparky_write.log_weight(conn, uid, {"date": d.isoformat(), "weight_kg": 88.0, "overwrite": False}, client=fake)
    assert fake.calls == [   # exactly the requests of before the dual write existed
        ("GET", "/meal-types"), ("GET", f"/food-entries/by-date/{d}"), ("GET", "/foods/foods-paginated"), ("POST", "/foods"),
        ("POST", "/food-entries"), ("GET", f"/measurements/check-in/{d}"), ("POST", "/measurements/check-in")]
    assert all(_count(conn, t, uid) == 0 for t in om.TABLES)


def test_flag_on_mirrors_every_write(conn):
    uid = _user(conn, "on")
    fake = FakeSparky("on")
    d = _today() - timedelta(days=1)
    flags.set_flag(conn, mirror.FLAG, "all", actor="test")
    try:
        res = sparky_write.log_food(conn, uid, [_food_item(d)], False, client=fake)
        row = conn.execute("SELECT * FROM food_entries WHERE user_id = %s", (uid,)).fetchone()
        assert row["id"] == res["created"][0]["id"] and row["calories"] == 650 and row["added_sugar_g"] == 2
        assert row["food_id"] and _count(conn, "foods", uid) == 1

        sparky_write.log_weight(conn, uid, {"date": d.isoformat(), "weight_kg": 88.0, "overwrite": False}, client=fake)
        assert conn.execute("SELECT weight_kg FROM body_measurements WHERE user_id = %s", (uid,)).fetchone()["weight_kg"] == 88

        w = sparky_write.log_workout(conn, uid, {"date": d.isoformat(), "name": "推", "notes": None, "allow_duplicate": False,
                                                 "create_missing_exercises": False, "exercises": [
                                                     {"exercise": "卧推", "exercise_id": None, "notes": None, "sets": [
                                                         {"reps": 8, "weight_kg": 60, "rir": 2, "duration_s": None,
                                                          "set_type": None, "notes": None}]}]}, client=fake)
        assert conn.execute("SELECT id FROM logged_workouts WHERE user_id = %s", (uid,)).fetchone()["id"] == w["created"]["id"]
        assert _count(conn, "logged_workout_sets", uid) == 1

        # Deletes follow; so does a rollback of the delete (it comes back under a new id)
        sparky_write.undo_entry(conn, uid, "food", d.isoformat(), res["created"][0]["id"], client=fake)
        assert _count(conn, "food_entries", uid) == 0
        sparky_write.undo_entry(conn, uid, "workout", d.isoformat(), w["created"]["id"], client=fake)
        assert _count(conn, "logged_workouts", uid) == 0 and _count(conn, "logged_workout_sets", uid) == 0
        sparky_write.undo_entry(conn, uid, "weight", d.isoformat(), client=fake)
        assert _count(conn, "body_measurements", uid) == 0
        before = {"id": "gone", "entry_date": d.isoformat(), "meal_type": "dinner", "quantity": 1, "unit": "份",
                  "food_id": fake.foods[0]["id"], "variant_id": "v", "source": "app"}
        sparky_write.rollback(conn, uid, "food", "delete", before, None, client=fake)
        assert conn.execute("SELECT meal FROM food_entries WHERE user_id = %s", (uid,)).fetchone()["meal"] == "dinner"

        # A failing sync never fails the write it follows; the migration catches up later
        fake.fail_reads, fake.wrote = True, False
        assert sparky_write.log_food(conn, uid, [_food_item(d, food="鸡蛋", calories=140, protein_g=12, carbs_g=1,
                                                            fat_g=10, saturated_fat_g=3, added_sugar_g=None, sugar_g=None)],
                                     False, client=fake)["created"]
        assert _count(conn, "food_entries", uid) == 1
        conn.execute("SELECT 1")   # the connection is still usable (the sync ran in a savepoint)
        fake.fail_reads = False
        assert om.run(conn, uid, dry_run=False, client=fake)["ok"]
        assert _count(conn, "food_entries", uid) == 2
    finally:
        flags.set_flag(conn, mirror.FLAG, "off", actor="test")


def test_rollback_dates_cover_every_kind():
    d = "2026-10-20"
    assert sparky_write._rollback_dates("food", {"entry_date": d}, {"entry_date": "2026-10-21"}) == \
        {"food": [d, "2026-10-21"]}
    assert sparky_write._rollback_dates("session", None, {"id": "x", "date": d}) == {"workouts": [d]}
    assert sparky_write._rollback_dates("sets", {"date": d}, {"date": d}) == {"workouts": [d, d]}
    assert sparky_write._rollback_dates("weight", {"date": d}, None) == {"weight": [d]}
    assert sparky_write._rollback_dates("lab", {}, {}) == {}
    assert writelog   # rollback_write goes through sparky_write.rollback (assistant/writelog._sparky)
