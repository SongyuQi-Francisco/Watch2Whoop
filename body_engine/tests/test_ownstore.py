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
from body_engine.ownstore import mirror, source, store
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


# ---------------------------------------------------------------- phase 3: read switch (own_food_read)
@pytest.fixture()
def flag_state(conn):
    """Set rollout flags for one test; everything back to off afterwards."""
    touched = []

    def set_(key, state="all", **kw):
        touched.append(key)
        flags.set_flag(conn, key, state, actor="test", **kw)
    yield set_
    for key in touched:
        flags.set_flag(conn, key, "off", actor="test")


def _linked(conn, uid):
    conn.execute("UPDATE users SET sparky_user_id = %s WHERE id = %s", (f"sp-{uid}", uid))


def test_reader_returns_the_same_shapes_as_sparkyfitness(conn, flag_state, monkeypatch):
    uid = _user(conn, "read")
    _linked(conn, uid)
    fake = FakeSparky("rd")
    d1, d2 = _seed(fake)
    monkeypatch.setattr(sparky_client, "client_for_user", lambda c, u: fake)
    flag_state(source.READ_FLAG)
    assert not getattr(sparky_client.reader_for_user(conn, uid), "own", False)   # no ok migration yet: SparkyFitness
    assert om.run(conn, uid, dry_run=False, client=fake)["ok"]
    reader = sparky_client.reader_for_user(conn, uid)
    assert reader.own and not reader.only
    s, e = d1 - timedelta(days=1), d2
    fake.calls.clear()
    assert sparky_client.training_log(reader, s, e) == sparky_client.training_log(fake, s, e)
    key = lambda r: r["id"]   # noqa: E731
    assert sorted(sparky_client.food_entries(reader, s, e), key=key) == sorted(sparky_client.food_entries(fake, s, e), key=key)
    assert sparky_client.weight(reader, s, e) == sparky_client.weight(fake, s, e)
    mine = {x["id"]: x for x in sparky_client.exercises(reader)}
    assert all(mine[x["id"]] == x for x in sparky_client.exercises(fake))   # + the public one a session used
    assert len(fake.calls) == 4                                               # only the fake's own four reads above
    assert sparky_client.link_state(conn, uid)["state"] == "connected"
    # A failed later migration switches the user back to SparkyFitness
    conn.execute("INSERT INTO own_store_migrations (user_id, since, until, ok, report) VALUES (%s, %s, %s, FALSE, '{}')",
                 (uid, s, e))
    assert not getattr(sparky_client.reader_for_user(conn, uid), "own", False)


def test_read_flag_keeps_the_tables_in_step_with_writes(conn, flag_state):
    uid = _user(conn, "readwrite")
    _linked(conn, uid)
    fake = FakeSparky("rw")
    assert om.run(conn, uid, dry_run=False, client=fake)["ok"]
    flag_state(source.READ_FLAG)                       # own_food_store stays off: reads own -> the dual write runs anyway
    d = _today()
    res = sparky_write.log_food(conn, uid, [_food_item(d)], False, client=fake)
    reader = sparky_client.reader_for_user(conn, uid)
    assert [r["id"] for r in sparky_client.food_entries(reader, d, d)] == [res["created"][0]["id"]]


# ---------------------------------------------------------------- phase 4: own only (own_food_only)
def _no_sparky(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("SparkyFitness must not be used")
    monkeypatch.setattr(sparky_client, "write_client_for_user", boom)
    monkeypatch.setattr(sparky_client, "client_for_user", boom)
    monkeypatch.setattr(sparky_client.SparkyClient, "request", boom)


def _workout(d, *exercises, create=True):
    return {"date": d.isoformat(), "name": "推", "notes": None, "allow_duplicate": False, "create_missing_exercises": create,
            "exercises": [{"exercise": name, "exercise_id": None, "notes": None, "sets": [
                {"reps": r, "weight_kg": w, "rir": 2, "duration_s": None, "set_type": None, "notes": None} for r, w in sets]}
                for name, sets in exercises]}


def test_only_mode_food_without_sparkyfitness(conn, flag_state, monkeypatch):
    uid = _user(conn, "only")                          # no SparkyFitness account at all: a new sign-up
    _no_sparky(monkeypatch)
    flag_state(source.ONLY_FLAG)
    assert source.only(conn, uid) and source.only_for_everyone(conn)
    assert sparky_client.link_state(conn, uid)["state"] == "connected"
    d = _today() - timedelta(days=1)
    reader = sparky_client.reader_for_user(conn, uid)

    res = sparky_write.log_food(conn, uid, [_food_item(d)], False)
    eid = res["created"][0]["id"]
    row = conn.execute("SELECT * FROM food_entries WHERE id = %s", (eid,)).fetchone()
    assert row["origin"] == "own" and row["calories"] == 650 and row["added_sugar_g"] == 2 and row["user_id"] == uid
    assert conn.execute("SELECT provider_external_id FROM foods WHERE user_id = %s", (uid,)).fetchone()[
        "provider_external_id"].startswith("ba:")
    assert sparky_write.log_food(conn, uid, [_food_item(d)], False)["skipped_duplicates"]
    assert [(r["id"], r["calories"], r["time"]) for r in sparky_client.food_entries(reader, d, d)] == [(eid, 650, "12:00")]
    half = sparky_write.update_food_entry(conn, uid, {"date": d.isoformat(), "id": eid, "portion": 0.5})
    assert half["updated"]["id"] == eid and half["updated"]["calories"] == 325
    renamed = sparky_write.update_food_entry(conn, uid, {"date": d.isoformat(), "id": eid, "food": "牛肉面", "calories": 500,
                                                         "protein_g": 30, "carbs_g": 60, "fat_g": 15})
    new_id = renamed["updated"]["id"]
    assert new_id != eid and [r["food"] for r in sparky_client.food_entries(reader, d, d)] == ["牛肉面"]
    record = sparky_write.backend(conn, uid).food_day(d)[0]
    gone = sparky_write.undo_entry(conn, uid, "food", d.isoformat(), new_id)
    assert gone["deleted"]["id"] == new_id and _count(conn, "food_entries", uid) == 0
    back = sparky_write.rollback(conn, uid, "food", "delete", record, None)
    assert [r["calories"] for r in sparky_client.food_entries(reader, d, d)] == [500] and back["restored"] != new_id
    assert sparky_write.delete_food_entry(conn, uid, d, back["restored"])["deleted"]["food"] == "牛肉面"
    with pytest.raises(ValueError, match="没有这条"):
        sparky_write.undo_entry(conn, uid, "food", d.isoformat(), "someone-elses-id")


def test_only_mode_weight_and_workouts_without_sparkyfitness(conn, flag_state, monkeypatch):
    uid = _user(conn, "only2")
    _no_sparky(monkeypatch)
    flag_state(source.ONLY_FLAG)
    d = _today() - timedelta(days=1)
    reader = sparky_client.reader_for_user(conn, uid)

    w = {"date": d.isoformat(), "weight_kg": 88.0, "overwrite": False}
    assert sparky_write.log_weight(conn, uid, w)["written"] == {"date": d.isoformat(), "weight": 88.0}
    assert sparky_write.log_weight(conn, uid, w)["written"] is None
    assert sparky_write.log_weight(conn, uid, {**w, "weight_kg": 89.0})["conflicts"]
    sparky_write.log_weight(conn, uid, {**w, "weight_kg": 89.0, "overwrite": True})
    assert sparky_client.weight(reader, d, d) == [{"date": d.isoformat(), "weight_kg": 89}]
    sparky_write.undo_entry(conn, uid, "weight", d.isoformat())
    assert sparky_client.weight(reader, d, d) == []
    sparky_write.rollback(conn, uid, "weight", "delete", {"date": d.isoformat(), "weight": 89.0, "steps": 5000}, None)
    assert sparky_client.weight(reader, d, d) == [{"date": d.isoformat(), "weight_kg": 89}]

    with pytest.raises(ValueError, match="对不上"):
        sparky_write.log_workout(conn, uid, _workout(d, ("哑铃卧推", [(10, 20)]), create=False))
    wk = sparky_write.log_workout(conn, uid, _workout(d, ("哑铃卧推", [(10, 20), (8, 22.5)])))
    assert wk["new_exercises_in_library"] == ["哑铃卧推"] and wk["created"]["exercises"] == [{"name": "哑铃卧推", "sets": 2}]
    assert sparky_write.log_workout(conn, uid, _workout(d, ("哑铃卧推", [(10, 20)])))["skipped_duplicate"]
    log = sparky_client.training_log(reader, d, d)
    assert [(s["id"], [x["name"] for x in s["exercises"]]) for s in log] == [(wk["created"]["id"], ["哑铃卧推"])]
    sets = log[0]["exercises"][0]["sets"]
    assert [(x["set"], x["reps"], x["weight_kg"]) for x in sets] == [(1, 10, 20), (2, 8, 22.5)]
    assert sparky_write.update_set(conn, uid, d.isoformat(), sets[1]["id"], 9, None, None)["updated"]["now"]["reps"] == 9
    sets = sparky_client.training_log(reader, d, d)[0]["exercises"][0]["sets"]
    sparky_write.undo_entry(conn, uid, "set", d.isoformat(), sets[0]["id"])
    assert [(x["set"], x["reps"]) for x in sparky_client.training_log(reader, d, d)[0]["exercises"][0]["sets"]] == [(1, 9)]
    session = sparky_write.backend(conn, uid).workout_day(d)[0]
    sparky_write.undo_entry(conn, uid, "workout", d.isoformat(), wk["created"]["id"])
    assert _count(conn, "logged_workouts", uid) == 0 and _count(conn, "logged_workout_sets", uid) == 0
    restored = sparky_write.rollback(conn, uid, "session", "delete", session, None)["restored"]
    again = sparky_client.training_log(reader, d, d)
    assert [s["id"] for s in again] == [restored] and again[0]["exercises"][0]["name"] == "哑铃卧推"
    assert [x["reps"] for x in again[0]["exercises"][0]["sets"]] == [9]


def test_only_mode_merge_and_split(conn, flag_state, monkeypatch):
    from body_engine.assistant import food_combine
    uid = _user(conn, "merge")
    _no_sparky(monkeypatch)
    flag_state(source.ONLY_FLAG)
    flag_state("meal_merge")
    d = _today()
    ids = [c["id"] for c in sparky_write.log_food(conn, uid, [
        _food_item(d, food="米饭", amount="150 g", calories=200, protein_g=4, carbs_g=44, fat_g=0.5, saturated_fat_g=0.1),
        _food_item(d, food="鸡胸", amount="100 g", calories=165, protein_g=31, carbs_g=0, fat_g=3.6, saturated_fat_g=1,
                   added_sugar_g=None, sugar_g=None)], False)["created"]]
    out = food_combine.combine(conn, uid, d, ids, "merge")
    reader = sparky_client.reader_for_user(conn, uid)
    rows = sparky_client.food_entries(reader, d, d)
    assert [r["id"] for r in rows] == [out["merge"]["entry_id"]] and rows[0]["calories"] == 365
    food_combine.split(conn, uid, out["merge"]["id"])
    assert sorted(r["calories"] for r in sparky_client.food_entries(reader, d, d)) == [165, 200]


def test_only_mode_rolls_back_rows_logged_before_the_switch(conn, flag_state, monkeypatch):
    uid = _user(conn, "switch")
    _linked(conn, uid)
    fake = FakeSparky("sw")
    d1, _ = _seed(fake)
    assert om.run(conn, uid, dry_run=False, client=fake)["ok"]
    raw = next(r for r in fake.food.values() if r["food_name"] == "燕麦")
    _no_sparky(monkeypatch)
    flag_state(source.ONLY_FLAG)
    assert source.only(conn, uid) and not mirror.enabled(conn, uid)
    # An entry the AI logged on SparkyFitness (write log: after = its SparkyFitness record) is removed by id from the own table
    assert sparky_write.rollback(conn, uid, "food", "create", None, raw)["deleted"] == raw["id"]
    assert conn.execute("SELECT 1 FROM food_entries WHERE id = %s", (raw["id"],)).fetchone() is None
    # ... and a SparkyFitness record from a delete log comes back with the same totals
    back = sparky_write.rollback(conn, uid, "food", "delete", raw, None)["restored"]
    got = conn.execute("SELECT * FROM food_entries WHERE id = %s", (back,)).fetchone()
    assert (got["food_name"], got["calories"], got["date"], got["origin"]) == ("燕麦", 300, d1, "own")


def test_only_mode_needs_a_good_migration_for_users_with_an_account(conn, flag_state):
    uid = _user(conn, "pending")
    _linked(conn, uid)
    flag_state(source.ONLY_FLAG)
    assert not source.only(conn, uid) and not source.reads_own(conn, uid)   # stays on SparkyFitness until migrated
    flag_state(source.ONLY_FLAG, "cohort")
    assert not source.only_for_everyone(conn)
