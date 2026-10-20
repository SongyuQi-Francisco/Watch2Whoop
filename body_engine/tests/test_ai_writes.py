"""Issue #30: the AI edits / deletes the user's own data (food, sets, workouts, weight, labs), every AI write is logged
with before / after values and one logged change can be rolled back; watch workout corrections survive re-imports.

Needs Postgres: separate test database BODY_TEST_DB_NAME_WRITES (default body_test_ai_writes), dropped and recreated each run.
SparkyFitness is replaced by an in-memory fake; nothing real is contacted.
"""
import copy
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.assistant import sparky_write, writelog
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.training import history, importer, overrides, shortcut_workouts as sw

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_WRITES", "body_test_ai_writes")
TZ = ZoneInfo("Australia/Sydney")


# ---------------------------------------------------------------- no database
def test_type_mapping():
    assert sw.shortcut_type("HKWorkoutActivityTypeBasketball") == "basketball"
    assert sw.shortcut_type("HKWorkoutActivityTypeTraditionalStrengthTraining") == "strength_training"
    assert sw.shortcut_type("HKWorkoutActivityTypeSocialDance") == "dance"
    assert sw.shortcut_type("HKWorkoutActivityTypeSomethingNew") == "something_new"   # unknown: readable, not glued
    assert sw.shortcut_type("Other") == "generic"
    raw = {"id": "ow-1", "type": "other", "name": "HKWorkoutActivityTypeBasketball", "source": {"source": "apple"},
           "start_time": "2026-10-20T19:00:00+11:00", "end_time": "2026-10-20T20:00:00+11:00"}
    assert importer.parse_ow(raw, TZ)["type"] == "basketball"          # OW only knew "other": its label has the HK type
    assert importer.parse_ow({**raw, "name": "Evening"}, TZ)["type"] == "other"


def test_confirm_lists_cover_new_destructive_tools():
    from body_engine.ai.chat import ALWAYS_CONFIRM
    assert {"undo_sparky_entry", "delete_workout", "delete_lab_results", "rollback_write"} <= ALWAYS_CONFIRM
    assert not {"update_food_entry", "update_training_set", "update_workout", "get_write_log"} & ALWAYS_CONFIRM
    for n in ("update_food_entry", "update_training_set", "update_workout", "delete_workout", "rollback_write"):
        assert TOOLS_BY_NAME[n].writes and "user" not in TOOLS_BY_NAME[n].input_schema["properties"], n
    assert not TOOLS_BY_NAME["get_write_log"].writes


# ---------------------------------------------------------------- fake SparkyFitness
class Fake:
    """Keeps food entries, workout sessions and check-ins in memory and applies writes like SparkyFitness does."""

    def __init__(self):
        self.food: dict[str, list] = {}
        self.sessions: dict[str, list] = {}
        self.checkin: dict[str, dict] = {}
        self.foods: list = []
        self.writes: list = []
        self.n = 0

    def _id(self, p):
        self.n += 1
        return f"{p}-{self.n}"

    def get(self, path, **params):
        if path == "/meal-types":
            return [{"id": f"mt-{n}", "name": n} for n in ("breakfast", "lunch", "snacks", "dinner")]
        if path == "/foods/foods-paginated":
            return {"foods": self.foods}
        if path.startswith("/food-entries/by-date/"):
            return copy.deepcopy(self.food.get(path.rsplit("/", 1)[1], []))
        if path == "/v2/exercise-entries/by-date":
            return copy.deepcopy(self.sessions.get(params["selectedDate"], []))
        if path.startswith("/measurements/check-in/"):
            return copy.deepcopy(self.checkin.get(path.rsplit("/", 1)[1], {}))
        raise AssertionError(path)

    def _food_by_id(self, i):
        for d, rows in self.food.items():
            for r in rows:
                if r["id"] == i:
                    return d, r
        raise AssertionError(i)

    def request(self, method, path, body=None, params=None, multipart_field=None):
        self.writes.append((method, path))
        if method == "POST" and path == "/foods":
            f = {"id": self._id("food"), "default_variant": {"id": self._id("var")}, **body}
            self.foods.append(f)
            return f
        if method == "POST" and path == "/food-entries":
            f = next((x for x in self.foods if x["id"] == body["food_id"]), {})
            row = {"id": self._id("fe"), "food_name": f.get("name", body.get("food_id")),
                   "serving_size": 1, "calories": f.get("calories", 100), "protein": f.get("protein", 10),
                   "carbs": f.get("carbs", 10), "fat": f.get("fat", 2), **body}
            self.food.setdefault(body["entry_date"], []).append(row)
            return row
        if method == "PUT" and path.startswith("/food-entries/"):
            d, row = self._food_by_id(path.rsplit("/", 1)[1])
            self.food[d].remove(row)
            row = {**row, **body}
            self.food.setdefault(row["entry_date"], []).append(row)
            return row
        if method == "DELETE" and path.startswith("/food-entries/"):
            d, row = self._food_by_id(path.rsplit("/", 1)[1])
            self.food[d].remove(row)
            return None
        if method == "PUT" and path.startswith("/exercise-entries/"):
            eid = path.rsplit("/", 1)[1]
            for rows in self.sessions.values():
                for s in rows:
                    for e in s.get("exercises") or [s]:
                        if e["id"] == eid:
                            e["sets"] = [{**x, "id": 1000 + self.n * 10 + i} for i, x in enumerate(body["sets"])]
                            self.n += 1
                            return e
            raise AssertionError(eid)
        if method == "DELETE" and path.startswith("/exercise-preset-entries/"):
            for rows in self.sessions.values():
                rows[:] = [s for s in rows if s["id"] != path.rsplit("/", 1)[1]]
            return None
        if method == "POST" and path == "/exercise-preset-entries":
            s = {"id": self._id("ws"), "type": "preset", **body,
                 "exercises": [{**e, "id": self._id("ee")} for e in body["exercises"]]}
            self.sessions.setdefault(body["entry_date"], []).append(s)
            return s
        if method == "POST" and path == "/measurements/check-in":
            d = body["entry_date"]
            cur = self.checkin.setdefault(d, {"id": self._id("ci"), "entry_date": d})
            cur.update({k: v for k, v in body.items() if k != "entry_date"})
            return cur
        if method == "DELETE" and path.startswith("/measurements/check-in/"):
            self.checkin = {d: c for d, c in self.checkin.items() if c["id"] != path.rsplit("/", 1)[1]}
            return None
        raise AssertionError((method, path))


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture(scope="module")
def ids(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return {n: conn.execute("INSERT INTO users (display_name, birth_date, height_cm) VALUES (%s, '2001-06-15', 184) "
                                "RETURNING id", (n,)).fetchone()["id"] for n in ("A", "B")}


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture()
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(sparky_write.sparky_client, "write_client_for_user", lambda conn, uid: f)
    return f


def _today(conn, uid):
    return sparky_write.user_today(conn, uid)


def _log(conn, uid, tool):
    return conn.execute("SELECT * FROM ai_write_log WHERE user_id = %s AND tool = %s ORDER BY id DESC LIMIT 1",
                        (uid, tool)).fetchone()


def _food_item(d, **kw):
    return {"date": d, "meal": "dinner", "time": "19:00", "food": "蘑菇烧鸡", "amount": "1 份", "calories": 400,
            "protein_g": 42, "carbs_g": 10, "fat_g": 20, "saturated_fat_g": 5, "alcohol_g": None, "notes": None, **kw}


# ---------- food ----------
def test_update_food_in_place_and_rollback(conn, ids, fake):
    a = ids["A"]
    d = (_today(conn, a) - timedelta(days=1)).isoformat()
    fake.food[d] = [{"id": "app-1", "source": None, "food_name": "米饭", "meal_type": "lunch", "entry_date": d,
                     "entry_time": "12:00:00", "quantity": 2, "unit": "碗", "serving_size": 1, "calories": 200,
                     "protein": 4, "carbs": 44, "fat": 1, "variant_id": "v-rice", "food_id": "f-rice"}]
    res = run_tool(conn, a, "butler", "update_food_entry", {"date": d, "id": "app-1", "portion": 0.5, "meal": "dinner",
                                                            "time": "18:30"})
    assert res["updated"]["id"] == "app-1" and res["updated"]["calories"] == 200 and res["updated"]["meal_type"] == "dinner"
    row = fake.food[d][0]
    assert row["quantity"] == 1 and row["entry_time"] == "18:30" and row["meal_type"] == "dinner"
    log = _log(conn, a, "update_food_entry")
    assert log["kind"] == "food" and log["before"]["quantity"] == 2 and log["after"]["quantity"] == 1
    # rollback puts portion / meal / time back (rollback itself is logged, can't be rolled back twice)
    run_tool(conn, a, "butler", "rollback_write", {"log_id": log["id"]})
    row = fake.food[d][0]
    assert row["quantity"] == 2 and row["meal_type"] == "lunch" and row["entry_time"] == "12:00"
    with pytest.raises(ValueError, match="回滚过"):
        run_tool(conn, a, "butler", "rollback_write", {"log_id": log["id"]})
    assert _log(conn, a, "rollback_write")["action"] == "rollback"


def test_update_food_nutrition_replaces_entry(conn, ids, fake):
    a = ids["A"]
    d = (_today(conn, a) - timedelta(days=1)).isoformat()
    fake.food[d] = [{"id": "app-2", "source": "sparky_app", "food_name": "牛排", "meal_type": "dinner", "entry_date": d,
                     "entry_time": None, "quantity": 1, "unit": "份", "serving_size": 1, "calories": 600,
                     "protein": 50, "carbs": 0, "fat": 44, "variant_id": "v-st", "food_id": "f-st"}]
    with pytest.raises(ValueError, match="差太多"):          # nutrition must still add up
        run_tool(conn, a, "butler", "update_food_entry", {"date": d, "id": "app-2", "calories": 900})
    res = run_tool(conn, a, "butler", "update_food_entry", {"date": d, "id": "app-2", "food": "牛排（瘦）",
                                                            "amount": "200 g", "calories": 400, "protein_g": 50,
                                                            "carbs_g": 0, "fat_g": 22})
    assert res["previous_id"] == "app-2" and res["updated"]["id"] != "app-2" and "新 id" in res["note"]
    assert [r["food_name"] for r in fake.food[d]] == ["牛排（瘦）"]
    log = _log(conn, a, "update_food_entry")
    out = writelog.rollback(conn, a, log["id"], client=fake)        # removes the new one, restores the old one
    assert out["result"]["deleted"] == res["updated"]["id"]
    assert [(r["food_id"], r["source"]) for r in fake.food[d]] == [("f-st", "sparky_app")]


def test_delete_food_any_source_logged_and_restored(conn, ids, fake):
    a = ids["A"]
    d = (_today(conn, a) - timedelta(days=2)).isoformat()
    fake.food[d] = [{"id": "m-1", "source": "migrated", "food_name": "燕麦", "meal_type": "breakfast", "entry_date": d,
                     "entry_time": "08:00:00", "quantity": 1, "unit": "碗", "food_id": "f-oat", "variant_id": "v-oat"}]
    run_tool(conn, a, "butler", "undo_sparky_entry", {"kind": "food", "date": d, "id": "m-1"})
    assert fake.food[d] == []
    log = _log(conn, a, "undo_sparky_entry")
    assert log["action"] == "delete" and log["before"]["food_name"] == "燕麦" and log["after"] is None
    run_tool(conn, a, "butler", "rollback_write", {"log_id": log["id"]})
    assert [(r["food_id"], r["meal_type"], r["source"]) for r in fake.food[d]] == [("f-oat", "breakfast", "migrated")]


def test_log_food_logged_and_rolled_back(conn, ids, fake):
    a = ids["A"]
    d = _today(conn, a).isoformat()
    res = run_tool(conn, a, "butler", "log_food", {"items": [_food_item(d)], "allow_duplicates": False})
    log = _log(conn, a, "log_food")
    assert log["action"] == "create" and log["after"]["id"] == res["created"][0]["id"]
    writelog.rollback(conn, a, log["id"], client=fake)
    assert fake.food[d] == []


# ---------- strength sets / sessions ----------
def _session(d):
    return {"id": "ws-a", "type": "preset", "entry_date": d, "name": "胸", "source": None, "notes": None,
            "exercises": [{"id": "ee-1", "exercise_id": "ex-bench", "name": "卧推", "duration_minutes": 0, "sets": [
                {"id": 11, "set_number": 1, "set_type": "Working Set", "reps": 8, "weight": 60, "rir": 2,
                 "completed_at": None, "is_pr": False, "progression_mode": None},
                {"id": 12, "set_number": 2, "set_type": "Working Set", "reps": 8, "weight": 60, "rir": 1,
                 "completed_at": None, "is_pr": False}]}]}


def test_update_and_delete_set_with_rollback(conn, ids, fake):
    a = ids["A"]
    d = "2026-10-15"
    fake.sessions[d] = [_session(d)]
    res = run_tool(conn, a, "butler", "update_training_set", {"date": d, "set_id": 12, "reps": 6, "weight_kg": 62.5,
                                                              "rir": None})
    assert res["updated"]["was"]["reps"] == 8 and res["updated"]["now"] == {
        "set_number": 2, "set_type": "Working Set", "reps": 6, "weight": 62.5, "rir": 1, "is_pr": False}
    sets = fake.sessions[d][0]["exercises"][0]["sets"]
    assert [(s["reps"], s["weight"]) for s in sets] == [(8, 60), (6, 62.5)]
    with pytest.raises(ValueError, match="RIR"):
        run_tool(conn, a, "butler", "update_training_set", {"date": d, "set_id": sets[0]["id"], "rir": 12})
    up = _log(conn, a, "update_training_set")
    # delete the first set (any source), then roll back both changes in reverse order
    run_tool(conn, a, "butler", "undo_sparky_entry", {"kind": "set", "date": d, "id": str(sets[0]["id"])})
    sets = fake.sessions[d][0]["exercises"][0]["sets"]
    assert [(s["set_number"], s["reps"]) for s in sets] == [(1, 6)]
    with pytest.raises(ValueError, match="唯一的一组"):
        run_tool(conn, a, "butler", "undo_sparky_entry", {"kind": "set", "date": d, "id": str(sets[0]["id"])})
    writelog.rollback(conn, a, _log(conn, a, "undo_sparky_entry")["id"], client=fake)
    writelog.rollback(conn, a, up["id"], client=fake)
    sets = fake.sessions[d][0]["exercises"][0]["sets"]
    assert [(s["reps"], s["weight"], s["rir"]) for s in sets] == [(8, 60, 2), (8, 60, 1)]


def test_delete_session_any_source_and_restore(conn, ids, fake):
    a = ids["A"]
    d = "2026-10-14"
    fake.sessions[d] = [_session(d)]
    run_tool(conn, a, "butler", "undo_sparky_entry", {"kind": "workout", "date": d, "id": "ws-a"})
    assert fake.sessions[d] == []
    writelog.rollback(conn, a, _log(conn, a, "undo_sparky_entry")["id"], client=fake)
    s = fake.sessions[d][0]
    assert s["name"] == "胸" and s["exercises"][0]["exercise_id"] == "ex-bench"
    assert [x["reps"] for x in s["exercises"][0]["sets"]] == [8, 8]
    assert "id" not in s["exercises"][0]["sets"][0] or s["exercises"][0]["sets"][0]["id"] != 11


def test_training_log_returns_set_ids():
    from body_engine import sparky_client
    from body_engine.assistant.sparky_tools import summarize_sessions
    s = sparky_client._session(_session("2026-10-15"))
    assert s["exercises"][0]["entry_id"] == "ee-1" and s["exercises"][0]["sets"][0]["id"] == 11
    assert summarize_sessions([s])[0]["exercises"][0]["set_ids"] == [11, 12]


# ---------- weight ----------
def test_weight_delete_and_log_rollback(conn, ids, fake):
    a = ids["A"]
    d = (_today(conn, a) - timedelta(days=3)).isoformat()
    fake.checkin[d] = {"id": "ci-x", "entry_date": d, "weight": 98.0, "waist": 90}
    run_tool(conn, a, "butler", "undo_sparky_entry", {"kind": "weight", "date": d})
    assert d not in fake.checkin
    with pytest.raises(ValueError, match="没有称重"):
        run_tool(conn, a, "butler", "undo_sparky_entry", {"kind": "weight", "date": d})
    writelog.rollback(conn, a, _log(conn, a, "undo_sparky_entry")["id"], client=fake)
    assert fake.checkin[d]["weight"] == 98.0 and fake.checkin[d]["waist"] == 90
    # log_weight overwrite -> rollback restores the old weight
    run_tool(conn, a, "butler", "log_weight", {"date": d, "weight_kg": 89.0, "overwrite": True})
    assert fake.checkin[d]["weight"] == 89.0
    writelog.rollback(conn, a, _log(conn, a, "log_weight")["id"], client=fake)
    assert fake.checkin[d]["weight"] == 98.0


# ---------- labs ----------
def test_delete_migrated_lab_and_rollback(conn, ids):
    a = ids["A"]
    conn.execute("""INSERT INTO lab_results (user_id, collected_on, test_key, test_name, category, value, unit, ref_high,
                    flag, import_key) VALUES (%s, '2026-08-30', 'uric_acid', '尿酸', 'blood', 550, 'μmol/L', 420, 'H',
                    'md:lab:2026-08:uric_acid')""", (a,))
    run_tool(conn, a, "butler", "delete_lab_results", {"collected_on": "2026-08-30", "test_keys": ["uric_acid"]})
    n = lambda: conn.execute("SELECT count(*) n FROM lab_results WHERE user_id = %s AND test_key = 'uric_acid'",
                             (a,)).fetchone()["n"]
    assert n() == 0
    log = _log(conn, a, "delete_lab_results")
    assert log["before"][0]["value"] == 550
    run_tool(conn, a, "butler", "rollback_write", {"log_id": log["id"]})
    r = conn.execute("SELECT value, import_key FROM lab_results WHERE user_id = %s AND test_key = 'uric_acid'",
                     (a,)).fetchone()
    assert r == {"value": 550, "import_key": "md:lab:2026-08:uric_acid"}


# ---------- audit ----------
def test_every_write_logged_and_own_data_only(conn, ids):
    a, b = ids["A"], ids["B"]
    run_tool(conn, a, "butler", "add_event", {"occurred_at": "2026-10-16T20:00", "title": "测试：膝盖", "category": None,
                                             "body_part": None, "severity": None, "note": None})
    log = _log(conn, a, "add_event")
    assert log["kind"] == "other" and log["after"]["args"]["title"] == "测试：膝盖"
    with pytest.raises(ValueError, match="没有可自动回滚"):
        writelog.rollback(conn, a, log["id"])
    # reads are not logged; B can neither see nor roll back A's log
    run_tool(conn, a, "butler", "get_profile", {"user": "me"})
    assert _log(conn, a, "get_profile") is None
    assert all(r["id"] != log["id"] for r in run_tool(conn, b, "butler", "get_write_log", {"days": None}))
    with pytest.raises(ValueError, match="不是你的"):
        run_tool(conn, b, "butler", "rollback_write", {"log_id": log["id"]})
    mine = run_tool(conn, a, "butler", "get_write_log", {"days": 30})
    assert mine[0]["tool"] in ("add_event",) and mine[0]["rollback"] is None
    # write tools never write for someone else
    with pytest.raises(PermissionError, match="只能写给自己"):
        run_tool(conn, b, "butler", "update_workout", {"user": str(a), "workout_id": 1, "type": "basketball"})


# ---------- watch workouts ----------
def _sc_row(start: str, end: str, t="Other", avg="140"):
    return sw.parse(["Workout", start, end, t, "", "500", avg, "170", "iPhone"], TZ)


def _ow_row(wid, start, end, t="basketball"):
    return importer.parse_ow({"id": wid, "type": t, "start_time": start, "end_time": end,
                              "source": {"source": "apple", "device_name": "Apple Watch"}}, TZ)


def _w(conn, uid, external_id):
    return conn.execute("SELECT * FROM workouts WHERE user_id = %s AND external_id = %s", (uid, external_id)).fetchone()


def test_dedup_when_real_type_arrives_after_inferred(conn, ids):
    """Shortcut-inferred generic workout first, the OW basketball workout of the same session later (4 min apart):
    the later OW row is kept, the shortcut row becomes its duplicate. A hand-set dup_of (flags.manual_dup) is never changed."""
    a = ids["A"]
    sc = _sc_row("2026-10-20T18:58:00+11:00", "2026-10-20T20:17:00+11:00")
    importer.upsert(conn, a, [sc])
    importer.refresh_duplicates(conn, a)
    assert _w(conn, a, sc["external_id"])["dup_of"] is None
    ow = _ow_row("ow-759", "2026-10-20T19:02:00+11:00", "2026-10-20T20:16:00+11:00")
    importer.upsert(conn, a, [ow])
    importer.refresh_duplicates(conn, a)
    kept = _w(conn, a, "ow-759")
    assert kept["dup_of"] is None and _w(conn, a, sc["external_id"])["dup_of"] == kept["id"]
    # manual decision: a different pair marked by hand stays as it is through re-imports
    x = _sc_row("2026-10-18T07:00:00+11:00", "2026-10-18T07:40:00+11:00", t="Walking")
    y = _ow_row("ow-760", "2026-10-18T09:00:00+11:00", "2026-10-18T09:30:00+11:00", t="walking")
    importer.upsert(conn, a, [x, y])
    yid = _w(conn, a, "ow-760")["id"]
    conn.execute("""UPDATE workouts SET dup_of = %s, flags = flags || '{"manual_dup": "by hand"}' WHERE user_id = %s
                    AND external_id = %s""", (yid, a, x["external_id"]))
    importer.upsert(conn, a, [x])
    importer.refresh_duplicates(conn, a)
    assert _w(conn, a, x["external_id"])["dup_of"] == yid


def test_update_workout_override_survives_reimport(conn, ids):
    a, b = ids["A"], ids["B"]
    row = _sc_row("2026-10-08T18:00:00+11:00", "2026-10-08T19:30:00+11:00")   # a Thursday, generic
    importer.upsert(conn, a, [row])
    wid = _w(conn, a, row["external_id"])["id"]
    with pytest.raises(ValueError, match="没有这次训练"):                         # someone else's workout
        run_tool(conn, b, "butler", "update_workout", {"workout_id": wid, "type": "basketball"})
    with pytest.raises(ValueError, match="type 不认识"):
        run_tool(conn, a, "butler", "update_workout", {"workout_id": wid, "type": "quidditch"})
    res = run_tool(conn, a, "butler", "update_workout", {"workout_id": wid, "type": "basketball", "name": "周四篮球",
                                                         "apply_to_similar": True})
    assert res["type"] == "basketball" and res["rule"]["weekday"] == "四"
    importer.upsert(conn, a, [_sc_row("2026-10-08T18:00:00+11:00", "2026-10-08T19:30:00+11:00")])  # re-import
    w = _w(conn, a, row["external_id"])
    assert (w["type"], w["name_zh"]) == ("basketball", "周四篮球")
    # deleted and imported again (new id): the override still applies
    conn.execute("DELETE FROM workouts WHERE id = %s", (wid,))
    importer.upsert(conn, a, [_sc_row("2026-10-08T18:00:00+11:00", "2026-10-08T19:30:00+11:00")])
    assert _w(conn, a, row["external_id"])["type"] == "basketball"
    # the rule: next Thursday's generic workout around the same time is basketball too, a Friday one is not
    nxt = _sc_row("2026-10-15T18:30:00+11:00", "2026-10-15T20:00:00+11:00")
    fri = _sc_row("2026-10-16T18:30:00+11:00", "2026-10-16T20:00:00+11:00")
    importer.upsert(conn, a, [nxt, fri])
    assert _w(conn, a, nxt["external_id"])["type"] == "basketball" and _w(conn, a, fri["external_id"])["type"] == "generic"
    assert _w(conn, b, nxt["external_id"]) is None


def test_calendar_counts_corrected_type_and_hide(conn, ids):
    a = ids["A"]
    r1 = _sc_row("2026-09-02T18:00:00+10:00", "2026-09-02T19:00:00+10:00", t="Swimming")
    r2 = _sc_row("2026-09-03T07:00:00+10:00", "2026-09-03T07:30:00+10:00")
    importer.upsert(conn, a, [r1, r2])
    importer.refresh_duplicates(conn, a)
    w2 = _w(conn, a, r2["external_id"])
    cal = lambda: history.calendar(conn, a, a, "2026-09")["summary"]["by_type"]
    assert cal() == {"游泳": 1, "其他运动": 1}
    run_tool(conn, a, "butler", "update_workout", {"workout_id": w2["id"], "type": "running"})
    assert cal() == {"游泳": 1, "跑步": 1}
    up = _log(conn, a, "update_workout")
    run_tool(conn, a, "butler", "delete_workout", {"workout_id": w2["id"]})
    assert cal() == {"游泳": 1}
    importer.upsert(conn, a, [_sc_row("2026-09-03T07:00:00+10:00", "2026-09-03T07:30:00+10:00")])   # stays hidden
    importer.refresh_duplicates(conn, a)
    assert cal() == {"游泳": 1}
    with pytest.raises(ValueError, match="已经删掉"):
        run_tool(conn, a, "butler", "delete_workout", {"workout_id": w2["id"]})
    # rollback the hide, then the type change
    run_tool(conn, a, "butler", "rollback_write", {"log_id": _log(conn, a, "delete_workout")["id"]})
    assert cal() == {"游泳": 1, "跑步": 1}
    run_tool(conn, a, "butler", "rollback_write", {"log_id": up["id"]})
    assert cal() == {"游泳": 1, "其他运动": 1}
    importer.upsert(conn, a, [_sc_row("2026-09-03T07:00:00+10:00", "2026-09-03T07:30:00+10:00")])
    assert _w(conn, a, r2["external_id"])["type"] == "generic"


def test_hidden_workout_keeps_its_copy_hidden(conn, ids):
    a = ids["A"]
    ow = _ow_row("ow-900", "2026-09-10T18:00:00+10:00", "2026-09-10T19:00:00+10:00", t="walking")
    sc = _sc_row("2026-09-10T18:01:00+10:00", "2026-09-10T19:00:00+10:00", t="Walking")
    importer.upsert(conn, a, [ow, sc])
    importer.refresh_duplicates(conn, a)
    wid = _w(conn, a, "ow-900")["id"]
    run_tool(conn, a, "butler", "delete_workout", {"workout_id": wid})
    days = history.calendar(conn, a, a, start=date(2026, 9, 10), end=date(2026, 9, 10))["days"]
    assert days[0]["sessions_count"] == 0
