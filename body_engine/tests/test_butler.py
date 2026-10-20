"""Body butler (P1): tools that write SparkyFitness (duplicate detection, banned-exercise blocking), lab result writes, screenshot metrics, long-term memory and its API authorization.

Needs Postgres: uses a separate test database BODY_TEST_DB_NAME (default body_test_butler), dropped and recreated on every run, never touching the production DB.
SparkyFitness is replaced by a fake client (FakeSparky); no real service is contacted.
"""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant import meals, memory, memory_seed, recipe_seed, recipes, snapshots, sparky_write, status
from body_engine.assistant.tools import TOOLS, TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.records import lab_catalog

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_butler")


# ---------------------------------------------------------------- no database needed
def _walk(schema, path):
    if schema.get("type") == "object" or "properties" in schema:
        assert schema.get("additionalProperties") is False, path
        assert set(schema["required"]) == set(schema["properties"]), path
        for k, v in schema["properties"].items():
            _walk(v, f"{path}.{k}")
    if "items" in schema:
        _walk(schema["items"], path + "[]")


def test_nested_schemas_are_strict():
    for t in TOOLS:
        _walk(t.input_schema, t.name)


def test_new_write_tools_are_marked():
    for name in ("delete_journal_entry", "log_food", "log_workout", "log_weight", "undo_sparky_entry", "add_lab_results",
                 "log_metric_snapshot", "update_profile_card", "add_observation", "delete_observation"):
        assert TOOLS_BY_NAME[name].writes, name
    for name in ("get_memory", "get_metric_snapshots", "search_exercises"):
        assert not TOOLS_BY_NAME[name].writes, name


def test_lab_catalog_units_and_aliases():
    assert lab_catalog.resolve_key("Urate", None, set()) == "uric_acid"
    assert lab_catalog.resolve_key("谷丙转氨酶", None, set()) == "alt"
    assert lab_catalog.resolve_key("LDL-C (calc)", None, set()) == "ldl"
    assert lab_catalog.resolve_key("某个新指标", None, set()) is None
    v, u, lo, hi, note = lab_catalog.normalize_unit("uric_acid", 9.25, "mg/dL", None, 7.0)
    assert u == "μmol/L" and round(v) == 550 and round(hi) == 416 and "mg/dL" in note
    v, u, *_ = lab_catalog.normalize_unit("uric_acid", 0.55, "mmol/L", None, None)
    assert round(v) == 550
    with pytest.raises(ValueError, match="单位"):
        lab_catalog.normalize_unit("uric_acid", 550, "g/L", None, None)
    with pytest.raises(ValueError, match="单位或小数点"):
        lab_catalog.check_plausible("triglycerides", 225)  # 2.25 mistaken for 225


def test_food_macro_consistency():
    ok = dict(food="啤酒", calories=300, protein_g=3, carbs_g=26, fat_g=0, alcohol_g=26)
    assert sparky_write._check_food(ok) == []
    with pytest.raises(ValueError, match="差太多"):
        sparky_write._check_food(dict(food="鸡胸", calories=900, protein_g=40, carbs_g=0, fat_g=5))
    with pytest.raises(ValueError, match="饱和脂肪不能多于"):
        sparky_write._check_food(dict(food="x", calories=90, protein_g=0, carbs_g=0, fat_g=10, saturated_fat_g=12))


def test_seed_keys_are_standard():
    assert set(memory_seed.MD_PROFILE) <= set(memory.STANDARD_KEYS)


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


@pytest.fixture(scope="module")
def world(dsn):
    """Three users A, B, C: A shares memory with B; C shares nothing. A has a banned exercise and one lab result."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, birth_date, height_cm) VALUES (%s, '2001-06-15', 184) "
                               "RETURNING id", (n,)).fetchone()["id"] for n in ("A", "B", "C")}
        tokens = {
            "A": auth.create_token(conn, ids["A"], "test", ["read", "write"]),
            "A_ro": auth.create_token(conn, ids["A"], "test-ro", ["read"]),
            "B": auth.create_token(conn, ids["B"], "test", ["read", "write"]),
            "C": auth.create_token(conn, ids["C"], "test", ["read", "write"]),
        }
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["A"], ids["B"], ["memory"]))
        conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, aliases, reason, app_exercise_id)
                        VALUES (%s, 'upright_row', '杠铃提拉 / 直立划船', 'banned', '{直立划船,杠铃提拉}', '练上斜方、肩峰下撞击', 'ex-upright'),
                               (%s, 'lat_pulldown', '高位下拉', 'active', '{高位下拉,Lat Pulldown}', NULL, 'ex-lat')""",
                     (ids["A"], ids["A"]))
        conn.execute("""INSERT INTO lab_results (user_id, collected_on, test_key, test_name, category, value, unit, ref_high, flag)
                        VALUES (%s, '2026-08-30', 'uric_acid', '尿酸', 'blood', 550, 'μmol/L', 420, 'H')""", (ids["A"],))
    return ids, tokens


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def h(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- long-term memory ----------
def test_memory_profile_and_observations(conn, world):
    ids, _ = world
    a = ids["A"]
    row = memory.update_profile(conn, a, "goals", "3 个月到 80kg")
    assert row["label"] == "目标和进度" and row["source"] == "assistant"
    with pytest.raises(ValueError, match="label"):
        memory.update_profile(conn, a, "sleep_notes", "x")
    assert memory.update_profile(conn, a, "sleep_notes", "周末晚睡", label="睡眠备注")["label"] == "睡眠备注"
    with pytest.raises(ValueError, match="小写"):
        memory.update_profile(conn, a, "Bad-Key", "x")
    o1 = memory.add_observation(conn, a, "周三晚上固定打篮球", "用户原话")
    o2 = memory.add_observation(conn, a, " 周三晚上固定打篮球 ")
    assert o2["duplicate"] and o2["id"] == o1["id"]
    m = memory.get_memory(conn, a)
    assert [p["key"] for p in m["profile_card"]][:1] == ["goals"] and len(m["observations"]) == 1
    assert memory.delete_observation(conn, ids["B"], o1["id"]) is None  # someone else's cannot be deleted
    assert memory.delete_observation(conn, a, o1["id"])["id"] == o1["id"]


def test_memory_init_from_database_is_idempotent(conn, world):
    ids, _ = world
    c = ids["C"]
    res = memory.init_profile(conn, c, {"schedule": "常凌晨 2 点睡"})
    assert {"basics", "schedule"} <= set(res["created"])
    memory.update_profile(conn, c, "schedule", "改成 12 点睡", source="user")
    res2 = memory.init_profile(conn, c, {"schedule": "常凌晨 2 点睡"})
    assert res2["created"] == [] and "schedule" in res2["kept"]
    card = {p["key"]: p for p in memory.get_memory(conn, c)["profile_card"]}
    assert card["schedule"]["value"] == "改成 12 点睡" and card["schedule"]["source"] == "user"
    # A has abnormal labs and a banned exercise -> health constraints and injuries come from the database
    a_db = memory.from_database(conn, ids["A"])
    assert "尿酸" in a_db["health_constraints"] and "直立划船" in a_db["injuries"]


def test_memory_api_auth(client, world):
    ids, tok = world
    a, b = ids["A"], ids["B"]
    assert client.get("/v1/users/me/memory").status_code == 401
    r = client.put("/v1/users/me/memory/profile/what_works", headers=h(tok["A"]), json={"value": "练前吃香蕉"})
    assert r.status_code == 200 and r.json()["source"] == "user" and r.json()["label"] == "对你有效的做法"
    assert client.put("/v1/users/me/memory/profile/what_works", headers=h(tok["A_ro"]),
                      json={"value": "x"}).status_code == 403  # read-only token
    assert client.put("/v1/users/me/memory/profile/Bad", headers=h(tok["A"]), json={"value": "x"}).status_code == 422
    assert client.put("/v1/users/me/memory/profile/new_key", headers=h(tok["A"]), json={"value": "x"}).status_code == 422
    # B has the memory share: can read, cannot modify or delete
    r = client.get(f"/v1/users/{a}/memory", headers=h(tok["B"]))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "profile_card", "observations"}
    assert {"key", "label", "value", "updated_at", "source"} == set(body["profile_card"][0])
    assert client.put(f"/v1/users/{a}/memory/profile/goals", headers=h(tok["B"]), json={"value": "x"}).status_code == 403
    # C has no share
    assert client.get(f"/v1/users/{a}/memory", headers=h(tok["C"])).status_code == 403
    # Deleting an observation: own -> 204; B deleting A's -> 403; nonexistent -> 404; deleting someone else's id via one's own path -> 404
    with psycopg.connect(api.pool.conninfo, row_factory=dict_row) as conn:
        oid = memory.add_observation(conn, a, "乳糖不耐")["id"]
    assert client.delete(f"/v1/users/{a}/memory/observations/{oid}", headers=h(tok["B"])).status_code == 403
    assert client.delete(f"/v1/users/me/memory/observations/{oid}", headers=h(tok["B"])).status_code == 404
    obs = client.get("/v1/users/me/memory", headers=h(tok["A"])).json()["observations"]
    assert [o["id"] for o in obs] == [oid] and set(obs[0]) == {"id", "text", "created_at", "source"}
    assert client.delete(f"/v1/users/me/memory/observations/{oid}", headers=h(tok["A"])).status_code == 204
    assert client.delete(f"/v1/users/me/memory/observations/{oid}", headers=h(tok["A"])).status_code == 404


def test_run_tool_memory_scope_and_audit(conn, world):
    ids, _ = world
    out = run_tool(conn, ids["B"], "butler", "get_memory", {"user": str(ids["A"])})
    assert "profile_card" in out
    with pytest.raises(PermissionError):
        run_tool(conn, ids["C"], "butler", "get_memory", {"user": str(ids["A"])})
    audit = conn.execute("SELECT channel, ok FROM assistant_audit WHERE tool = 'get_memory' ORDER BY id").fetchall()
    assert [(r["channel"], r["ok"]) for r in audit][-2:] == [("butler", True), ("butler", False)]


# ---------- lab results ----------
def test_add_lab_results(conn, world):
    ids, _ = world
    a = ids["A"]
    day = date(2026, 11, 10)
    res = lab_catalog.add_results(conn, a, day, "Laverty", [
        dict(test="Urate", test_key=None, value=0.48, value_text=None, unit="mmol/L", ref_low=0.2, ref_high=0.42,
             flag=None, note=None),
        dict(test="ALT", test_key=None, value=38, value_text=None, unit="U/L", ref_low=None, ref_high=40, flag=None, note=None),
    ], overwrite=False)
    got = {r["test_key"]: r for r in res["created"]}
    assert got["uric_acid"]["value"] == 480 and got["uric_acid"]["unit"] == "μmol/L" and got["uric_acid"]["flag"] == "H"
    assert got["alt"]["flag"] == "normal" and res["unit_conversions"]
    # Writing the same report again -> unchanged; changed numbers -> conflict, not overwritten; overwrite -> updated
    again = lab_catalog.add_results(conn, a, day, None, [dict(test="尿酸", test_key=None, value=480, value_text=None,
                                    unit="μmol/L", ref_low=None, ref_high=None, flag=None, note=None)], overwrite=False)
    assert [r["test_key"] for r in again["unchanged"]] == ["uric_acid"]
    diff = [dict(test="尿酸", test_key=None, value=470, value_text=None, unit="μmol/L", ref_low=None, ref_high=420,
                 flag=None, note=None)]
    assert lab_catalog.add_results(conn, a, day, None, diff, overwrite=False)["conflicts"][0]["existing"] == 480
    assert lab_catalog.add_results(conn, a, day, None, diff, overwrite=True)["updated"][0]["previous"] == 480
    trend = run_tool(conn, a, "butler", "get_lab_results", {"user": "me", "tests": ["uric_acid"]})[0]
    assert [h["value"] for h in trend["history"]] == [550, 470]
    # Undo: only deletes what the butler wrote; the migrated August results are kept
    with pytest.raises(PermissionError, match="不是身体管家写的"):
        lab_catalog.delete_results(conn, a, date(2026, 8, 30), ["uric_acid"])
    assert lab_catalog.delete_results(conn, a, day, ["uric_acid", "alt"])["deleted"]["test_keys"]
    assert [h["value"] for h in run_tool(conn, a, "butler", "get_lab_results",
                                         {"user": "me", "tests": ["uric_acid"]})[0]["history"]] == [550]
    with pytest.raises(ValueError, match="test_key"):
        lab_catalog.add_results(conn, a, day, None, [dict(test="某新指标", test_key=None, value=1, value_text=None,
                                unit=None, ref_low=None, ref_high=None, flag=None, note=None)], overwrite=False)


# ---------- screenshot metrics ----------
def test_metric_snapshot_does_not_touch_sensor_data(conn, world):
    ids, _ = world
    a, d = ids["A"], date(2026, 10, 16)
    conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                    VALUES (%s, %s, 'body_fat_pct', 25.0, '%%', 'avg', 'ow')""", (a, d))
    res = snapshots.log_snapshot(conn, a, d, "inbody", [{"metric": "body_fat_pct", "value": 24.1, "unit": None},
                                                        {"metric": "visceral_fat_level", "value": 9, "unit": None}], None)
    assert len(res["created"]) == 2 and res["sensor_data_same_day"][0]["source"] == "ow"
    assert conn.execute("SELECT value, source FROM daily_metrics WHERE user_id = %s AND date = %s",
                        (a, d)).fetchone() == {"value": 25.0, "source": "ow"}
    res = snapshots.log_snapshot(conn, a, d, "inbody", [{"metric": "body_fat_pct", "value": 24.1, "unit": "%"}], None)
    assert res["unchanged"]
    with pytest.raises(ValueError, match="合理范围"):
        snapshots.log_snapshot(conn, a, d, "apple_health", [{"metric": "apple_readiness", "value": 140, "unit": None}], None)
    assert [r["metric"] for r in snapshots.list_snapshots(conn, a, d, d)] == ["body_fat_pct", "visceral_fat_level"]


# ---------- SparkyFitness writes ----------
class FakeSparky:
    """Records write requests; by-date / exercise library / check-in return pre-arranged data."""

    def __init__(self, food=None, sessions=None, checkin=None):
        self.food = food or {}            # date -> [entry]
        self.sessions = sessions or {}    # date -> [session]
        self.checkin = checkin or {}      # date -> {...}
        self.writes = []
        self.foods = []
        self.library = [
            {"id": "ex-lat", "name": "高位下拉（正手，略宽于肩）", "category": "strength", "description": "要点"},
            {"id": "ex-upright", "name": "杠铃提拉 / 直立划船（禁用）", "category": "strength", "description": "【禁用】练上斜方"},
            {"id": "ex-dips", "name": "双杠臂屈伸（禁用）", "category": "strength", "description": "【禁用】肩前压力大"},
            {"id": "ex-incline", "name": "上斜哑铃卧推（30°）", "category": "strength", "description": None},
        ]

    def get(self, path, **params):
        if path == "/foods/foods-paginated":
            return {"foods": self.foods}
        if path.startswith("/goals/by-date/"):
            return {"preset_name": "训练日", "calories": 2400, "protein": 160, "carbs": 255, "fat": 62}
        if path.startswith("/food-entries/range/"):
            return [dict(e, entry_date=d) for d, rows in self.food.items() for e in rows]
        if path == "/meals":
            return [{"id": f"meal-{r['key']}", "name": r["name"]} for r in recipe_seed.RECIPES[:-1]]  # one fewer: tests the "no match" case
        if path == "/meal-types":
            return [{"id": f"mt-{n}", "name": n} for n in ("breakfast", "lunch", "snacks", "dinner")]
        if path.startswith("/food-entries/by-date/"):
            return self.food.get(path.rsplit("/", 1)[1], [])
        if path == "/v2/exercise-entries/by-date":
            return self.sessions.get(params["selectedDate"], [])
        if path == "/exercises":
            return {"exercises": self.library, "totalCount": len(self.library)}
        if path.startswith("/measurements/check-in/"):
            return self.checkin.get(path.rsplit("/", 1)[1], {})
        raise AssertionError(path)

    def request(self, method, path, body=None, params=None, multipart_field=None):
        self.writes.append((method, path, body))
        if method == "POST" and path == "/foods":
            food = {"id": f"food-{len(self.foods)}", "default_variant": {"id": f"var-{len(self.foods)}"}, **body}
            self.foods.append(food)
            return food
        if method == "POST" and path == "/food-entries":
            row = {"id": f"fe-{len(self.writes)}", **body, "food_name": next(
                (f["name"] for f in self.foods if f["id"] == body.get("food_id")), None)}
            self.food.setdefault(body["entry_date"], []).append(row)
            return row
        if method == "POST" and path == "/exercise-preset-entries":
            return {"id": "ws-1", "name": body["name"], "exercises": [
                {"exercise_snapshot": {"name": e["exercise_id"]}, "sets": e["sets"]} for e in body["exercises"]]}
        if method == "POST" and path == "/exercises":
            return {"id": "ex-new", "name": body["name"]}
        return None


def _food(d, **kw):
    return {"date": d, "meal": "dinner", "time": "23:50", "food": "蘑菇烧鸡（去皮鸡腿 170g）", "amount": "1 份",
            "calories": 400, "protein_g": 42, "carbs_g": 10, "fat_g": 20, "saturated_fat_g": 5, "alcohol_g": None,
            "notes": None, **kw}


def test_log_food_writes_snapshot_and_skips_duplicates(conn, world):
    ids, _ = world
    a = ids["A"]
    today = sparky_write.user_today(conn, a)
    d = (today - timedelta(days=1)).isoformat()
    fake = FakeSparky(food={d: [{"id": "old", "meal_type": "dinner", "food_name": "蘑菇烧鸡（去皮鸡腿 170g）", "calories": 400}]})
    res = sparky_write.log_food(conn, a, [_food(d), _food(d, meal="snacks", food="啤酒", amount="2 瓶（330ml/瓶）",
                                                           calories=290, protein_g=2, carbs_g=24, fat_g=0,
                                                           saturated_fat_g=None, alcohol_g=26)],
                                allow_duplicates=False, client=fake)
    assert [s["food"] for s in res["skipped_duplicates"]] == ["蘑菇烧鸡（去皮鸡腿 170g）"]
    assert [c["food"] for c in res["created"]] == ["啤酒"]
    (_, p1, food), (_, p2, body) = fake.writes
    assert p1 == "/foods" and food["serving_size"] == 1 and food["serving_unit"] == "2 瓶（330ml/瓶）"
    assert food["calories"] == 290 and food["alcohol_g"] == 26 and food["provider_external_id"].startswith("ba:")
    assert p2 == "/food-entries" and body["food_id"] == "food-0" and body["variant_id"] == "var-0"
    assert body["source"] == "body_assistant" and body["source_id"]
    assert body["quantity"] == 1 and body["meal_type_id"] == "mt-snacks" and body["entry_time"] == "23:50"
    # Writing the same entry again: same source_id (SparkyFitness is idempotent on it)
    fake2 = FakeSparky()
    sparky_write.log_food(conn, a, [_food(d, meal="snacks", food="啤酒", amount="2 瓶（330ml/瓶）", calories=290,
                                          protein_g=2, carbs_g=24, fat_g=0, saturated_fat_g=None, alcohol_g=26)],
                          allow_duplicates=False, client=fake2)
    assert fake2.writes[1][2]["source_id"] == body["source_id"]
    # The same food was already created -> reuse it, don't create again
    fake4 = FakeSparky()
    fake4.foods = fake.foods
    sparky_write.log_food(conn, a, [_food(d, meal="snacks", food="啤酒", amount="2 瓶（330ml/瓶）", calories=290,
                                          protein_g=2, carbs_g=24, fat_g=0, saturated_fat_g=None, alcohol_g=26)],
                          allow_duplicates=False, client=fake4)
    assert [w[1] for w in fake4.writes] == ["/food-entries"]
    # allow_duplicates -> write
    fake3 = FakeSparky(food=fake.food)
    assert len(sparky_write.log_food(conn, a, [_food(d)], allow_duplicates=True, client=fake3)["created"]) == 1
    with pytest.raises(ValueError, match="未来"):
        sparky_write.log_food(conn, a, [_food((today + timedelta(days=1)).isoformat())], False, client=FakeSparky())
    with pytest.raises(ValueError, match="meal"):
        sparky_write.log_food(conn, a, [_food(d, meal="supper")], False, client=FakeSparky())


def _workout(d, *names, **kw):
    return {"date": d, "name": "上肢 A", "notes": None, "allow_duplicate": False, "create_missing_exercises": False,
            "exercises": [{"exercise": n, "exercise_id": None, "notes": None,
                           "sets": [{"reps": 10, "weight_kg": 39, "rir": 2, "duration_s": None, "set_type": None,
                                     "notes": None}] * 3} for n in names], **kw}


def test_log_workout_blocks_banned_exercises(conn, world):
    ids, _ = world
    a = ids["A"]
    d = sparky_write.user_today(conn, a).isoformat()
    for name in ("直立划船", "杠铃提拉 / 直立划船", "双杠臂屈伸"):  # exercise_meta alias / name in the library / banned only in SparkyFitness
        fake = FakeSparky()
        with pytest.raises(PermissionError, match="拒绝写入"):
            sparky_write.log_workout(conn, a, _workout(d, "高位下拉", name), client=fake)
        assert fake.writes == []  # nothing was written at all
    # A banned exercise given by exercise_id is blocked too
    fake = FakeSparky()
    w = _workout(d, "随便叫")
    w["exercises"][0]["exercise_id"] = "ex-upright"
    with pytest.raises(PermissionError):
        sparky_write.log_workout(conn, a, w, client=fake)
    # Same via run_tool: blocked by exercise_meta first, no SparkyFitness needed; the audit records the failure
    with pytest.raises(PermissionError, match="禁用"):
        run_tool(conn, a, "butler", "log_workout", _workout(d, "直立划船"))
    last = conn.execute("SELECT tool, ok, error FROM assistant_audit ORDER BY id DESC LIMIT 1").fetchone()
    assert last["tool"] == "log_workout" and not last["ok"] and "禁用" in last["error"]


def test_log_workout_matching_duplicates_and_write(conn, world):
    ids, _ = world
    a = ids["A"]
    d = sparky_write.user_today(conn, a).isoformat()
    with pytest.raises(ValueError, match="对不上") as e:
        sparky_write.log_workout(conn, a, _workout(d, "卧推"), client=FakeSparky())
    assert "上斜哑铃卧推（30°）" in str(e.value)  # offers candidates
    fake = FakeSparky(sessions={d: [{"type": "preset", "id": "s0", "exercises": [{"exercise_id": "ex-lat"}]}]})
    res = sparky_write.log_workout(conn, a, _workout(d, "Lat Pulldown"), client=fake)
    assert res["skipped_duplicate"] and fake.writes == []
    fake = FakeSparky()
    res = sparky_write.log_workout(conn, a, _workout(d, "高位下拉", "上斜哑铃卧推"), client=fake)
    (method, path, body), = fake.writes
    assert path == "/exercise-preset-entries" and body["source"] == "body_assistant"
    assert [e["exercise_id"] for e in body["exercises"]] == ["ex-lat", "ex-incline"]
    assert body["exercises"][0]["sets"][0] == {"set_number": 1, "set_type": "Working Set", "reps": 10, "weight": 39, "rir": 2}
    # A new exercise is only created after user confirmation (create_missing_exercises)
    fake = FakeSparky()
    res = sparky_write.log_workout(conn, a, _workout(d, "壶铃摇摆", create_missing_exercises=True), client=fake)
    assert res["new_exercises_in_library"] == ["壶铃摇摆"] and fake.writes[0][1] == "/exercises"
    with pytest.raises(ValueError, match="RIR"):
        w = _workout(d, "高位下拉")
        w["exercises"][0]["sets"] = [{"reps": 10, "weight_kg": 39, "rir": 12, "duration_s": None, "set_type": None, "notes": None}]
        sparky_write.log_workout(conn, a, w, client=FakeSparky())


def _weight(d, **kw):
    return {"date": d, "weight_kg": None, "body_fat_pct": None, "waist_cm": None, "muscle_mass_kg": None,
            "body_water_pct": None, "bone_mass_kg": None, "bmr_kcal": None, "overwrite": False, **kw}


def test_log_weight_conflicts_and_unchanged(conn, world):
    ids, _ = world
    a = ids["A"]
    d = sparky_write.user_today(conn, a).isoformat()
    fake = FakeSparky(checkin={d: {"id": "c1", "weight": "88.4"}})
    res = sparky_write.log_weight(conn, a, _weight(d, weight_kg=88.0, waist_cm=96), client=fake)
    assert res["conflicts"]["weight"] == {"existing": 88.4, "new": 88.0} and fake.writes == []
    res = sparky_write.log_weight(conn, a, _weight(d, weight_kg=88.4), client=fake)
    assert res["unchanged"] and fake.writes == []
    res = sparky_write.log_weight(conn, a, _weight(d, weight_kg=88.4, waist_cm=96), client=fake)
    assert fake.writes[-1][2] == {"entry_date": d, "waist": 96}  # only the new one is written
    sparky_write.log_weight(conn, a, _weight(d, weight_kg=88.0, overwrite=True), client=fake)
    assert fake.writes[-1][2] == {"entry_date": d, "weight": 88.0}
    with pytest.raises(ValueError, match="合理范围"):
        sparky_write.log_weight(conn, a, _weight(d, weight_kg=420), client=fake)  # pounds mistaken for kg


def test_log_weight_ignores_earlier_checkin(conn, world):
    """GET /measurements/check-in/{date} falls back to the latest earlier check-in: that is not a same-day conflict."""
    ids, _ = world
    a = ids["A"]
    d = sparky_write.user_today(conn, a)
    earlier = (d - timedelta(days=3)).isoformat()
    fake = FakeSparky(checkin={d.isoformat(): {"id": "c0", "entry_date": earlier, "weight": "88.4"}})
    res = sparky_write.log_weight(conn, a, _weight(d.isoformat(), weight_kg=88.0), client=fake)
    assert res["written"] == {"date": d.isoformat(), "weight": 88.0} and not res["overwrote"]


def test_undo_only_deletes_assistant_entries(conn, world):
    ids, _ = world
    a = ids["A"]
    d = "2026-10-16"
    fake = FakeSparky(food={d: [{"id": "mine", "source": "body_assistant", "food_name": "啤酒"},
                                {"id": "app", "source": None, "food_name": "早餐"}]})
    assert sparky_write.undo_entry(conn, a, "food", d, "mine", client=fake)["deleted"]["name"] == "啤酒"
    assert fake.writes == [("DELETE", "/food-entries/mine", None)]
    with pytest.raises(PermissionError, match="不是身体管家写的"):
        sparky_write.undo_entry(conn, a, "food", d, "app", client=fake)


def test_undo_by_content_match(conn, world):
    """Issue #25: without an id, food is matched exactly by date + meal + name (+ time); 0 or several matches refuse."""
    ids, _ = world
    a = ids["A"]
    d = "2026-10-16"
    rows = [{"id": "p1", "source": "body_assistant", "meal_type": "dinner", "food_name": "蘑菇烧鸡（去皮鸡腿 170g）",
             "entry_time": "19:00:00"},
            {"id": "p2", "source": "body_assistant", "meal_type": "dinner", "food_name": "蘑菇烧鸡（去皮鸡腿 170g）",
             "entry_time": "21:30:00"},
            {"id": "b1", "source": "body_assistant", "meal_type": "snacks", "food_name": "啤酒", "entry_time": None},
            {"id": "y1", "source": None, "meal_type": "breakfast", "food_name": "酸奶"}]
    fake = FakeSparky(food={d: rows})
    undo = lambda **kw: sparky_write.undo_entry(conn, a, "food", d, None, client=fake, **{"meal": None, "food": None,
                                                                                        "time": None, **kw})
    with pytest.raises(ValueError, match="没有「啤酒」"):          # 0 matches: wrong meal
        undo(meal="dinner", food="啤酒")
    with pytest.raises(ValueError, match="有 2 条.*p1.*p2"):     # several: refuse, list the ids
        undo(meal="dinner", food="蘑菇烧鸡(去皮鸡腿170g)")
    with pytest.raises(ValueError, match="meal"):
        undo(meal="supper", food="啤酒")
    assert fake.writes == []
    assert undo(meal="dinner", food="蘑菇烧鸡（去皮鸡腿 170g）", time="21:30")["deleted"]["id"] == "p2"
    assert undo(meal="snacks", food=" 啤酒 ")["deleted"]["id"] == "b1"
    with pytest.raises(PermissionError, match="今天页"):          # logged elsewhere: the user deletes it on the Today page
        undo(meal="breakfast", food="酸奶")
    assert fake.writes == [("DELETE", "/food-entries/p2", None), ("DELETE", "/food-entries/b1", None)]
    # Workouts need an id; SparkyFitness ids may be numbers while the AI passes a string
    fake = FakeSparky(sessions={d: [{"id": 7, "type": "preset", "source": "body_assistant", "name": "胸"}]})
    with pytest.raises(ValueError, match="训练要给 id"):
        sparky_write.undo_entry(conn, a, "workout", d, None, client=fake)
    assert sparky_write.undo_entry(conn, a, "workout", d, "7", client=fake)["deleted"]["name"] == "胸"
    assert fake.writes == [("DELETE", "/exercise-preset-entries/7", None)]
    # Through the tool (relaxed MCP call without the optional fields)
    assert TOOLS_BY_NAME["undo_sparky_entry"].input_schema["required"] == ["kind", "date", "id", "meal", "food", "time"]


def test_delete_food_entry_api(client, world, monkeypatch):
    """Today page: DELETE /v1/users/me/nutrition/entries/{id}?date= deletes any of your own entries (write scope, only for yourself)."""
    ids, tok = world
    d = "2026-10-16"
    fakes = {ids["A"]: FakeSparky(food={d: [{"id": "app-1", "source": None, "meal_type": "breakfast", "food_name": "酸奶"}]}),
             ids["B"]: FakeSparky()}
    monkeypatch.setattr(sparky_write.sparky_client, "write_client_for_user", lambda conn, uid: fakes[uid])
    url = f"/v1/users/me/nutrition/entries/app-1?date={d}"
    assert client.delete(url, headers=h(tok["A_ro"])).status_code == 403                      # read-only token
    assert client.delete(f"/v1/users/{ids['A']}/nutrition/entries/app-1?date={d}", headers=h(tok["B"])).status_code == 403
    assert client.delete(url, headers=h(tok["B"])).status_code == 404                          # B's own account has no such entry
    assert client.delete(f"/v1/users/me/nutrition/entries/app-1", headers=h(tok["A"])).status_code == 422  # date required
    assert client.delete(f"/v1/users/me/nutrition/entries/a%2Fb?date={d}", headers=h(tok["A"])).status_code in (404, 422)
    assert fakes[ids["A"]].writes == [] and fakes[ids["B"]].writes == []
    web = TestClient(api.app, base_url="https://testserver")
    assert web.post("/v1/web/login", json={"token": tok["A"]}).status_code == 200
    assert web.delete(url).status_code == 403                                                    # cookie write without X-Body-Web
    r = web.delete(url, headers={"X-Body-Web": "1"})
    assert r.status_code == 200 and r.json()["deleted"]["food"] == "酸奶"
    assert fakes[ids["A"]].writes == [("DELETE", "/food-entries/app-1", None)]


def test_write_tools_only_for_self(conn, world):
    ids, _ = world
    # Write tools have no user parameter; run_tool runs as the viewer itself; a manually injected user is blocked too
    with pytest.raises(PermissionError, match="只能写给自己"):
        run_tool(conn, ids["B"], "butler", "add_observation", {"user": str(ids["A"]), "text": "x", "evidence": None})


def test_every_write_tool_asks_in_butler_settings():
    """The butler's .claude/settings.json (owner's rule, 2026-10-18): every body-engine tool is allowed; only deleting / undoing
    asks (same list as the in-page chat's ALWAYS_CONFIRM), and every delete / undo tool is in that list."""
    import json
    from pathlib import Path
    from body_engine.ai.chat import ALWAYS_CONFIRM
    cfg = json.loads((Path(__file__).resolve().parents[2] / "assistant/.claude/settings.json").read_text())
    ask = {r.removeprefix("mcp__body-engine__") for r in cfg["permissions"]["ask"]}
    assert cfg["permissions"]["allow"] == ["mcp__body-engine__*"]
    destructive = {t.name for t in TOOLS if t.writes and t.name.startswith(("delete_", "undo_", "remove_"))}
    assert ask == ALWAYS_CONFIRM and destructive <= ask and ask <= {t.name for t in TOOLS if t.writes}
    assert "Bash" in cfg["permissions"]["deny"] and cfg["permissions"]["defaultMode"] == "default"


def test_gemini_butler_settings_and_policies():
    """Gemini CLI butler configuration: settings.json and policies/butler.toml mirror the Claude Code setup."""
    import tomllib
    import json
    from pathlib import Path
    from body_engine.ai.chat import ALWAYS_CONFIRM
    assistant = Path(__file__).resolve().parents[2] / "assistant"
    g_cfg = json.loads((assistant / ".gemini/settings.json").read_text())
    assert g_cfg["mcpServers"]["body-engine"]["headers"]["Authorization"] == "Bearer ${BODY_AI_TOKEN}"
    assert "run_shell_command" in g_cfg["tools"]["exclude"] and "write_file" in g_cfg["tools"]["exclude"]
    # Persona = assistant/CLAUDE.md via GEMINI.md (a CLAUDE.md file name would also pull in the repo's developer notes)
    assert g_cfg["context"]["fileName"] == "GEMINI.md"
    assert "@./CLAUDE.md" in (assistant / "GEMINI.md").read_text()
    hook = g_cfg["hooks"]["BeforeAgent"][0]["hooks"][0]["command"]
    assert "additionalContext" in hook   # plain stdout would only be shown to the user, not given to the model

    policy_file = assistant / ".gemini/policies/butler.toml"
    assert policy_file.exists()
    policy = tomllib.loads(policy_file.read_text())
    rules = policy.get("rule", [])
    ask_rule = next((r for r in rules if r.get("decision") == "ask_user" and r.get("mcpName") == "body-engine"), None)
    assert ask_rule is not None
    assert set(ask_rule["toolName"]) == ALWAYS_CONFIRM
    deny_tools = next((r for r in rules if r.get("decision") == "deny" and not r.get("mcpName")), None)
    assert deny_tools is not None and "run_shell_command" in deny_tools["toolName"]


def test_failed_call_keeps_audit_but_undoes_partial_writes(dsn, world):
    """Same as mcp_server._call: commit after an error. Half-written lab results are undone by the savepoint, the failure audit stays."""
    ids, _ = world
    bad = {"collected_on": "2026-11-20", "lab": None, "overwrite": False, "results": [
        dict(test="尿酸", test_key=None, value=480, value_text=None, unit="μmol/L", ref_low=None, ref_high=None,
             flag=None, note=None),
        dict(test="某新指标", test_key=None, value=1, value_text=None, unit=None, ref_low=None, ref_high=None,
             flag=None, note=None)]}
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        with pytest.raises(ValueError):
            try:
                run_tool(c, ids["A"], "butler", "add_lab_results", bad)
            except Exception:
                c.commit()
                raise
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        assert c.execute("SELECT count(*) AS n FROM lab_results WHERE collected_on = '2026-11-20'").fetchone()["n"] == 0
        last = c.execute("SELECT tool, ok FROM assistant_audit ORDER BY id DESC LIMIT 1").fetchone()
        assert last == {"tool": "add_lab_results", "ok": False}



# ================================================================ status card
def _item(**kw):
    return {"key": "phase", "label": "当前阶段", "value": "减脂增肌", "unit": None, "tone": "neutral", "trend": None,
            "good_direction": None, "group": "训练", "spark": None, "note": None, **kw}


def test_status_item_validation(conn, world):
    ids, _ = world
    c = ids["C"]
    for bad, msg in [(dict(label="当前训练阶段名"), "超过 6"), (dict(value="一二三四五六七八九十一二三"), "超过 12"),
                     (dict(note="x" * 21), "超过 20"), (dict(tone="great"), "tone"), (dict(group="心情"), "group"),
                     (dict(trend="sideways"), "trend"), (dict(spark=[1] * 31), "spark"), (dict(key="Bad"), "key")]:
        with pytest.raises(ValueError, match=msg):
            status.set_items(conn, c, [_item(**bad)], [])
    with pytest.raises(ValueError, match="自动算"):
        status.set_items(conn, c, [_item(key="weight")], [])
    with pytest.raises(ValueError, match="自动算"):
        status.set_items(conn, c, [], ["recovery"])
    res = status.set_items(conn, c, [_item(), _item(key="right_shoulder", label="肩", value="内旋受限", tone="warn",
                                                    group="健康", note="无痛弹响")], [])
    assert res["written"] == ["phase", "right_shoulder"]
    got = status.list_status(conn, c)
    assert [g["group"] for g in got] == ["健康", "训练"]  # in GROUPS order
    assert set(got[0]) == set(status.FIELDS)
    assert status.set_items(conn, c, [], ["phase"])["removed"] == ["phase"]


def test_clock_and_red_meat_marker():
    from body_engine.rules.nutrition import red_meat_estimate
    assert status._clock(2 + 40 / 60) == "凌晨 2:40"
    assert status._clock(-0.5) == "23:30"
    assert status._clock(6.5) == "早上 6:30"
    assert red_meat_estimate("培根 2 片（40g，红肉 40g）", 1, "40 g") == ("标注", 40)
    assert red_meat_estimate("培根 2 片", 1, "份") == ("培根", 30)       # unannotated ones are still estimated by keyword as before
    assert red_meat_estimate("牛油果（45g）") is None


def test_refresh_auto_computes_and_keeps_old_on_errors(conn, world, monkeypatch):
    ids, _ = world
    b = ids["B"]
    today = date(2026, 10, 17)
    for k in range(14):
        d = today - timedelta(days=13 - k)
        conn.execute("""INSERT INTO daily_scores (user_id, date, recovery, sleep_debt_min, sleep_actual_min, algo_version)
                        VALUES (%s, %s, %s, %s, %s, 't')""", (b, d, 40 if k < 7 else 70, 90, 420 if k >= 7 else 360))
        start = datetime(d.year, d.month, d.day, 2, 30, tzinfo=ZoneInfo("Australia/Sydney"))
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, is_main, source)
                        VALUES (%s, %s, %s, %s, 400, true, 'ow')""", (b, d, start, start + timedelta(hours=7)))
    conn.execute("""INSERT INTO lab_results (user_id, collected_on, test_key, test_name, category, value, unit, ref_high)
                    VALUES (%s, '2026-08-30', 'uric_acid', '尿酸', 'blood', 550, 'μmol/L', 420),
                           (%s, '2026-10-10', 'uric_acid', '尿酸', 'blood', 470, 'μmol/L', 420)""", (b, b))
    status.set_items(conn, b, [_item(key="goal_weight", label="目标体重", value="80", unit="kg", group="目标")], [])
    weights = {today - timedelta(days=k): 89.0 + k * 0.1 for k in range(14)}  # drops 0.1 per day over two weeks
    monkeypatch.setattr(status, "_weights", lambda conn, uid, s, e: weights)
    monkeypatch.setattr(status.sparky_client, "client_for_user", lambda conn, uid: None)
    monkeypatch.setattr(status.sparky_client, "training_log", lambda c, s, e: [
        {"date": (today - timedelta(days=k)).isoformat()} for k in (0, 2, 4, 9)])
    monkeypatch.setattr(status.sparky_client, "food_entries", lambda c, s, e: [
        {"date": today.isoformat(), "food": "培根 2 片（40g，红肉 40g）", "quantity": 1, "unit": "40 g"},
        {"date": today.isoformat(), "food": "牛油果（45g）", "quantity": 1, "unit": "45 g"}])
    res = status.refresh_auto(conn, b, today)
    assert res["errors"] == {}
    got = {i["key"]: i for i in status.list_status(conn, b)}
    w = got["weight"]
    assert (w["value"], w["unit"], w["trend"], w["good_direction"], w["tone"]) == ("89.0", "kg", "down", "down", "good")
    assert w["note"] == "周均 -0.7 kg" and w["spark"][-1] == 89.0
    assert got["to_goal"]["value"] == "9.0" and got["to_goal"]["note"] == "按现速约 13 周"
    assert (got["recovery"]["value"], got["recovery"]["trend"], got["recovery"]["tone"]) == ("70", "up", "good")
    assert (got["sleep_debt"]["value"], got["sleep_debt"]["tone"]) == ("1.5", "warn")
    assert (got["bedtime"]["value"], got["bedtime"]["tone"]) == ("凌晨 2:30", "bad")
    assert (got["sleep_hours"]["value"], got["sleep_hours"]["trend"]) == ("7.0", "up")
    u = got["uric_acid"]
    assert (u["value"], u["unit"], u["trend"], u["tone"], u["spark"]) == ("偏高 470", "μmol/L", "down", "warn", [550, 470])
    assert (got["training_week"]["value"], got["training_week"]["tone"]) == ("3", "warn")
    assert (got["red_meat_week"]["value"], got["red_meat_week"]["tone"]) == ("40", "good")
    assert all(i["source"] == "auto" for k, i in got.items() if k in status.AUTO_KEYS)
    # SparkyFitness errors: training and red meat keep old values; weight has no data any more -> removed
    def boom(conn, uid):
        raise status.sparky_client.SparkyError("连不上")
    monkeypatch.setattr(status.sparky_client, "client_for_user", boom)
    monkeypatch.setattr(status, "_weights", lambda conn, uid, s, e: {})
    res = status.refresh_auto(conn, b, today)
    assert set(res["errors"]) == {"calc_training", "calc_red_meat"} and {"weight", "to_goal"} <= set(res["removed"])
    got = {i["key"]: i for i in status.list_status(conn, b)}
    assert got["training_week"]["value"] == "3" and "weight" not in got and "goal_weight" in got


def test_memory_api_status_refreshes_when_stale(client, world, monkeypatch):
    ids, tok = world
    calls = []
    monkeypatch.setattr(status, "refresh_auto", lambda conn, uid, today=None: calls.append(uid))
    with psycopg.connect(api.pool.conninfo, row_factory=dict_row) as c:
        c.execute("DELETE FROM sync_state WHERE user_id = %s AND source = 'status_auto'", (ids["A"],))
    assert client.get("/v1/users/me/memory", headers=h(tok["A"])).status_code == 200
    assert calls == [ids["A"]]
    with psycopg.connect(api.pool.conninfo, row_factory=dict_row) as c:
        c.execute("INSERT INTO sync_state (user_id, source, last_synced_at) VALUES (%s, 'status_auto', now())", (ids["A"],))
    client.get("/v1/users/me/memory", headers=h(tok["A"]))
    assert calls == [ids["A"]]  # just refreshed, not recomputed


# ================================================================ meal templates
def test_meal_template_validation_and_scaling(conn, world):
    ids, _ = world
    c = ids["C"]
    tpl = meals.create_template(conn, c, "固定早餐", "breakfast", meals.FIXED_BREAKFAST["items"], True)
    assert tpl["totals"] == {"kcal": 547.0, "protein_g": 30.2, "carbs_g": 35.6, "fat_g": 31.4, "sat_fat_g": 8.2,
                             "red_meat_g": 40.0}
    with pytest.raises(ValueError, match="已经有"):
        meals.create_template(conn, c, "固定早餐", "breakfast", meals.FIXED_BREAKFAST["items"], True)
    bad = [dict(meals.FIXED_BREAKFAST["items"][0], kcal=900)]
    with pytest.raises(ValueError, match="差太多"):
        meals.create_template(conn, c, "x", "breakfast", bad, True)
    with pytest.raises(ValueError, match="红肉克数"):
        meals.create_template(conn, c, "x", "breakfast", [dict(meals.FIXED_BREAKFAST["items"][2], red_meat_g=60)], True)
    scaled = meals.scale_items(tpl["items"], [{"food": "培根 2 片", "grams": 60}, {"food": "咖啡 1 杯", "grams": 0}])
    bacon = next(i for i in scaled if i["food"] == "培根 2 片")
    assert (bacon["grams"], bacon["kcal"], bacon["red_meat_g"]) == (60, 180, 60) and len(scaled) == 4
    with pytest.raises(ValueError, match="没有"):
        meals.scale_items(tpl["items"], [{"food": "香肠", "grams": 50}])
    upd = meals.update_template(conn, c, tpl["id"], {"items": tpl["items"], "needs_confirmation": False, "name": None})
    assert upd["needs_confirmation"] is False


def test_meal_log_and_undo(conn, world):
    ids, _ = world
    a = ids["A"]
    tpl = meals.create_template(conn, a, "固定早餐", "breakfast", meals.FIXED_BREAKFAST["items"], True)
    fake = FakeSparky()
    res = meals.log_template(conn, a, tpl["id"], client=fake, channel="test")
    assert len(res["entries"]) == 5 and res["totals"]["kcal"] == 547
    assert any("红肉" in w and "280g" in w and "会超上限" in w for w in res["warnings"])
    names = [w[2]["food_id"] for w in fake.writes if w[1] == "/food-entries"]
    foods = {f["id"]: f["name"] for f in fake.foods}
    assert "培根 2 片（40g，红肉 40g）" in {foods[n] for n in names}
    entry = next(w[2] for w in fake.writes if w[1] == "/food-entries")
    assert entry["meal_type"] == "breakfast" and entry["source"] == "body_assistant" and "克数待确认" in entry["notes"]
    assert meals.list_templates(conn, a)[0]["today_log"]["log_id"] == res["log_id"]
    assert meals.list_templates(conn, a)[0]["uses_30d"] == 1   # the Today page puts the most used templates first
    # Tapping again on the same day -> duplicate, rejected
    fake.food[res["date"]] = [{"id": e["id"], "meal_type": "breakfast", "food_name": e["food"], "calories": 1,
                               "source": "body_assistant"} for e in res["entries"]]
    with pytest.raises(meals.DuplicateMealError):
        meals.log_template(conn, a, tpl["id"], client=fake)
    # Undo: deletes 5 entries; undo again -> already undone
    out = meals.undo_log(conn, a, res["log_id"], client=fake)
    assert len(out["deleted"]) == 5 and all(w[0] == "DELETE" for w in fake.writes[-5:])
    with pytest.raises(LookupError, match="撤销过"):
        meals.undo_log(conn, a, res["log_id"], client=fake)
    assert meals.list_templates(conn, a)[0]["today_log"] is None
    assert meals.list_templates(conn, a)[0]["uses_30d"] == 0   # undone logs don't count
    with pytest.raises(LookupError):
        meals.undo_log(conn, ids["B"], res["log_id"], client=fake)  # someone else's


def test_meal_api_auth(client, world, monkeypatch):
    ids, tok = world
    fake = FakeSparky()
    monkeypatch.setattr(sparky_write.sparky_client, "write_client_for_user", lambda conn, uid: fake)
    tpl = client.get("/v1/users/me/meal-templates", headers=h(tok["A"])).json()
    tid = next(t["id"] for t in tpl if t["name"] == "固定早餐")
    assert client.get(f"/v1/users/{ids['A']}/meal-templates", headers=h(tok["B"])).status_code == 403  # no nutrition share
    assert client.post(f"/v1/users/{ids['A']}/meal-templates/{tid}/log", headers=h(tok["B"])).status_code == 403
    assert client.post(f"/v1/users/me/meal-templates/{tid}/log", headers=h(tok["A_ro"])).status_code == 403
    assert client.post(f"/v1/users/me/meal-templates/{tid}/log", headers=h(tok["B"])).status_code == 404  # B does not have this template
    r = client.post(f"/v1/users/me/meal-templates/{tid}/log", headers=h(tok["A"]),
                    json={"date": "2026-10-16", "time": "08:30", "items": [{"food": "培根 2 片", "grams": 30}]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) >= {"log_id", "entries", "totals", "warnings", "date", "meal", "skipped_duplicates"}
    assert body["totals"]["red_meat_g"] == 30 and body["date"] == "2026-10-16"
    assert client.post(f"/v1/users/me/meal-templates/{tid}/log", headers=h(tok["A"]),
                       json={"items": [{"food": "香肠", "grams": 1}]}).status_code == 422
    # cookie login: a write request without X-Body-Web -> 403; with it -> undo works
    web = TestClient(api.app, base_url="https://testserver")
    assert web.post("/v1/web/login", json={"token": tok["A"]}).status_code == 200
    assert web.delete(f"/v1/users/me/meal-logs/{body['log_id']}").status_code == 403
    assert web.delete(f"/v1/users/{ids['B']}/meal-logs/{body['log_id']}", headers={"X-Body-Web": "1"}).status_code == 403
    r = web.delete(f"/v1/users/me/meal-logs/{body['log_id']}", headers={"X-Body-Web": "1"})
    assert r.status_code == 200 and len(r.json()["deleted"]) == 5
    assert web.delete(f"/v1/users/me/meal-logs/{body['log_id']}", headers={"X-Body-Web": "1"}).status_code == 404



# ================================================================ recipes
def test_recipe_seed_data_is_consistent():
    by_name = {}
    for r in recipe_seed.RECIPES:
        ings = recipes.check_ingredients([recipe_seed.ingredient(*x) for x in r["ingredients"]])  # every item passes validation
        by_name[r["name"]] = (recipes.per_serving(ings, r.get("servings", 1)), recipes.constraint_flags(ings, r.get("servings", 1)))
        assert set(r["slots"]) <= set(recipes.SLOTS) and r["steps"], r["name"]
    ps, f = by_name["蘑菇烧鸡"]
    assert 330 <= ps["kcal"] <= 420 and ps["protein_g"] >= 38 and f["purine_level"] == "medium" and f["red_meat_g"] == 0
    ps, f = by_name["姜葱鸡油饭（减脂版）"]  # body-management.md: about 700 kcal per serving, protein 45g, carbs 75g, fat 20g
    assert 650 <= ps["kcal"] <= 780 and 40 <= ps["protein_g"] <= 50 and 70 <= ps["carbs_g"] <= 90
    ps, f = by_name["盐葱酱牛肋条"]
    assert f["short_rib"] and f["red_meat_g"] == 150 and f["sat_fat_level"] == "high"
    assert by_name["希腊酸奶碗"][1]["purine_level"] == "low"


def _new_recipe(**kw):
    return {"name": "测试鸡胸沙拉", "cuisine": "西式", "slots": ["lunch"], "tags": ["鸡肉"], "prep_min": 10, "cook_min": 5,
            "servings": 2, "serving_label": "1 份（不含主食）", "includes_staple": False, "steps": ["鸡胸煎熟切片", "拌菜"],
            "note": None, "can_make": True, "favorite": None,
            "ingredients": [recipe_seed.ingredient("鸡胸肉", 300), recipe_seed.ingredient("青菜（菜心 / 芥兰）", 300),
                            dict(recipe_seed.ingredient("食用油", 10), store="costco")], **kw}


def test_recipe_create_list_update(conn, world):
    ids, _ = world
    c = ids["C"]
    r = recipes.create_recipe(conn, c, _new_recipe(), sync=False)
    assert r["kcal"] == round((330 + 90 + 88.4) / 2, 1) and r["servings"] == 2 and r["can_make"]
    assert {"高蛋白", "低饱和脂肪", "快手", "鸡肉"} <= set(r["tags"]) and r["manual_tags"] == ["鸡肉"]
    assert r["constraint_flags"]["purine_level"] == "medium" and "低嘌呤" not in r["tags"]
    assert r["ingredients"][2]["store"] == "costco" and r["ingredients"][0]["purchase_unit"] == "整盒（约 1kg）"
    assert set(recipes.list_recipes(conn, c)[0]) == {"id", "name", "slots", "tags", "kcal", "protein_g", "carbs_g", "fat_g",
                                                     "per_serving", "prep_min", "cook_min", "favorite", "can_make",
                                                     "constraint_flags", "last_cooked_on", "image"}
    assert [x["name"] for x in recipes.list_recipes(conn, c, slot="lunch", tag="高蛋白", q="鸡胸")] == ["测试鸡胸沙拉"]
    assert recipes.list_recipes(conn, c, slot="breakfast") == [] and recipes.list_recipes(conn, c, q="三文鱼") == []
    with pytest.raises(ValueError, match="已经有"):
        recipes.create_recipe(conn, c, _new_recipe(), sync=False)
    with pytest.raises(ValueError, match="slots"):
        recipes.create_recipe(conn, c, _new_recipe(name="x", slots=["supper"]), sync=False)
    with pytest.raises(ValueError, match="store"):
        recipes.create_recipe(conn, c, _new_recipe(name="y", ingredients=[dict(recipe_seed.ingredient("米（生）", 90), store="aldi")]), sync=False)
    beef = [recipe_seed.ingredient("牛肋条", 300), recipe_seed.ingredient("食用油", 10)]
    u = recipes.update_recipe(conn, c, r["id"], {"ingredients": beef, "favorite": True, "name": None}, sync=False)
    assert u["constraint_flags"]["red_meat_g"] == 150 and u["constraint_flags"]["short_rib"] and u["favorite"]
    assert conn.execute("SELECT constraint_flags->>'red_meat_g' AS g FROM recipe_meta WHERE id = %s", (r["id"],)).fetchone()["g"] == "150.0"
    with pytest.raises(LookupError):
        recipes.get_recipe(conn, ids["B"], r["id"])  # someone else's


def test_recipe_log_and_undo(conn, world):
    ids, _ = world
    a = ids["A"]
    r = recipes.create_recipe(conn, a, _new_recipe(name="测试黑椒牛柳", slots=["dinner"], ingredients=[
        recipe_seed.ingredient("牛肉 rump", 300), recipe_seed.ingredient("洋葱彩椒", 300)]), sync=False)
    fake = FakeSparky()
    res = recipes.log_recipe(conn, a, r["id"], date(2026, 10, 16), None, 1.5, "19:00", client=fake)
    entry = [w[2] for w in fake.writes if w[1] == "/food-entries"][0]
    food = next(f for f in fake.foods if f["id"] == entry["food_id"])
    assert food["name"] == "测试黑椒牛柳（1.5 份，红肉 225g）" and entry["meal_type"] == "dinner"  # defaults to its only slot
    assert res["totals"]["red_meat_g"] == 225 and res["servings"] == 1.5
    assert recipes.get_recipe(conn, a, r["id"])["last_cooked_on"] == date(2026, 10, 16)
    with pytest.raises(meals.DuplicateMealError):
        recipes.log_recipe(conn, a, r["id"], date(2026, 10, 16), "dinner", 1.5, None, client=fake)
    meals.undo_log(conn, a, res["log_id"], client=fake)  # shares undo with meal templates
    assert recipes.get_recipe(conn, a, r["id"])["last_cooked_on"] is None
    with pytest.raises(ValueError, match="servings"):
        recipes.log_recipe(conn, a, r["id"], None, None, 20, None, client=fake)


def test_recipe_suggest_respects_constraints(conn, world, monkeypatch):
    ids, _ = world
    b = ids["B"]
    seeded = recipes.seed(conn, b, client=FakeSparky())
    assert len(seeded["created"]) == len(recipe_seed.RECIPES) and seeded["not_in_sparkyfitness"] == [recipe_seed.RECIPES[-1]["name"]]
    assert recipes.seed(conn, b, client=FakeSparky())["created"] == []  # idempotent
    day = date(2026, 10, 17)
    fake = FakeSparky(food={
        "2026-10-14": [{"food_name": "盐葱酱牛肋条（1 份，红肉 150g）", "meal_type": "dinner", "quantity": 1, "serving_size": 1,
                        "calories": 490, "protein": 26, "carbs": 2, "fat": 37}],
        "2026-10-17": [{"food_name": "希腊酸奶碗", "meal_type": "breakfast", "quantity": 1, "serving_size": 1,
                        "calories": 300, "protein": 25, "carbs": 30, "fat": 3}]})
    monkeypatch.setattr(status.sparky_client, "client_for_user", lambda conn, uid: fake)
    res = recipes.suggest(conn, b, day, "dinner")
    assert res["remaining"] == {"kcal": 2100, "protein_g": 135, "carbs_g": 225, "fat_g": 59}
    # Lunch not logged: treated as eaten at the usual 35% (840 kcal / 56g), the rest goes to dinner
    assert res["meal_target"] == {"kcal": 1260, "protein_g": 79} and res["assumed_eaten"] == ["lunch"]
    assert res["red_meat_week_g"] == 150
    names = {e["name"]: e["reason"] for e in res["excluded"]}
    assert "牛肋条" in names["盐葱酱牛肋条"]                       # eaten in the last 7 days
    assert "本周红肉只剩 100g" in names["黑椒牛柳（rump）"]         # 150 + 150 > 250
    assert len(res["suggestions"]) == 3 and all(s["reason"] and "dinner" in s["slots"] for s in res["suggestions"])
    lunch = recipes.suggest(conn, b, day, "lunch")
    assert lunch["meal_target"]["kcal"] == 1050  # dinner still follows lunch: the remainder is split 35 : 35
    snack = recipes.suggest(conn, b, day, "snack")
    assert snack["meal_target"]["kcal"] == 240 and all("snack" in x["slots"] for x in snack["suggestions"])
    assert all(s["with_staple"] == ("含主食" not in s["tags"]) for s in lunch["suggestions"])
    with pytest.raises(ValueError, match="slot"):
        recipes.suggest(conn, b, day, "supper", client=fake)


def test_recipe_api_auth(client, world, monkeypatch):
    ids, tok = world
    fake = FakeSparky()
    monkeypatch.setattr(sparky_write.sparky_client, "write_client_for_user", lambda conn, uid: fake)
    rid = client.get("/v1/users/me/recipes?q=黑椒", headers=h(tok["A"])).json()[0]["id"]
    assert client.get(f"/v1/users/{ids['A']}/recipes", headers=h(tok["B"])).status_code == 403  # no nutrition share
    assert client.get(f"/v1/users/{ids['A']}/recipes/{rid}", headers=h(tok["C"])).status_code == 403
    d = client.get(f"/v1/users/me/recipes/{rid}", headers=h(tok["A"])).json()
    assert {"ingredients", "steps", "servings"} <= set(d) and {"purchase_unit", "store"} <= set(d["ingredients"][0])
    assert client.get("/v1/users/me/recipes?slot=supper", headers=h(tok["A"])).status_code == 422
    assert client.patch(f"/v1/users/me/recipes/{rid}", headers=h(tok["A_ro"]), json={"favorite": True}).status_code == 403
    assert client.patch(f"/v1/users/{ids['A']}/recipes/{rid}", headers=h(tok["B"]), json={"favorite": True}).status_code == 403
    r = client.patch(f"/v1/users/me/recipes/{rid}", headers=h(tok["A"]), json={"favorite": True, "slots": ["lunch", "dinner"]})
    assert r.status_code == 200 and r.json()["favorite"] and r.json()["slots"] == ["lunch", "dinner"]
    assert client.patch(f"/v1/users/me/recipes/{rid}", headers=h(tok["A"]), json={"name": "x"}).status_code == 422
    web = TestClient(api.app, base_url="https://testserver")
    assert web.post("/v1/web/login", json={"token": tok["A"]}).status_code == 200
    assert web.post(f"/v1/users/me/recipes/{rid}/log", json={"date": "2026-10-15"}).status_code == 403  # no X-Body-Web
    r = web.post(f"/v1/users/me/recipes/{rid}/log", headers={"X-Body-Web": "1"},
                 json={"date": "2026-10-15", "meal": "lunch", "servings": 1})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"log_id", "recipe_id", "name", "date", "time", "meal", "servings", "entries", "totals", "warnings"}
    assert web.post(f"/v1/users/me/recipes/{rid}/log", headers={"X-Body-Web": "1"},
                    json={"date": "2026-10-15", "meal": "lunch"}).status_code == 409
    assert web.delete(f"/v1/users/me/meal-logs/{body['log_id']}", headers={"X-Body-Web": "1"}).status_code == 200
