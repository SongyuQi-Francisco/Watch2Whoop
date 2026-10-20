"""Issue #31: nutrition goal versions (resolve by date / weekday, carry-over, history, SparkyFitness backfill), sugar tracking
(extras, coverage, notes parsing, sugary drinks), the sugar rule, status cards, reports and the API / tools
(test DB body_test_nutrition_goals). Numbers are synthetic."""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.assistant import meals, recipes, sparky_write, status
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.goals import nutrition as G
from body_engine.insights import reports
from body_engine.nutrition import daily, extras
from body_engine.rules import nutrition as rules_nutrition, params as RP
from body_engine.rules.context import Ctx

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_NUTRITION", "body_test_nutrition_goals")
SYD = ZoneInfo("Australia/Sydney")
TODAY = datetime.now(SYD).date()
TRAIN = {"calories": 2100, "protein_g": 140, "carbs_g": 220, "fat_g": 60}
REST = {"calories": 1900, "protein_g": 140, "carbs_g": 170, "fat_g": 60}
WEEKDAYS = [1, 3, 5, 6]
REST_DAYS = ("tuesday", "thursday", "sunday")


# ================================================================ pure functions
def test_check_optional_and_parse_notes():
    assert extras.check_optional("x", {"sugar_g": 10, "added_sugar_g": None}) == {
        "sugar_g": 10.0, "added_sugar_g": None, "fiber_g": None, "sodium_mg": None}
    with pytest.raises(ValueError, match="添加糖不能多于总糖"):
        extras.check_optional("x", {"sugar_g": 5, "added_sugar_g": 9})
    with pytest.raises(ValueError, match="不能多于碳水"):
        extras.check_optional("x", {"sugar_g": 30}, carbs_g=20)
    with pytest.raises(ValueError, match="≥ 0"):
        extras.check_optional("x", {"fiber_g": -1})
    assert extras.parse_notes("估算；添加糖 12g，纤维 3.5 g") == {"added_sugar_g": 12.0, "fiber_g": 3.5}
    assert extras.parse_notes("含糖约 30克") == {"sugar_g": 30.0}
    assert extras.parse_notes("总糖 20g、添加糖 8g") == {"added_sugar_g": 8.0, "sugar_g": 20.0}
    assert extras.parse_notes("糖尿病饮食") == {} and extras.parse_notes(None) == {}


def test_sugary_drink_detection():
    d = extras.is_sugary_drink
    assert d({"food": "可口可乐 330ml"}) and d({"food": "珍珠奶茶（大杯）"}) and d({"food": "Orange juice 250ml"})
    assert not d({"food": "零度可乐"}) and not d({"food": "Coke Zero"}) and not d({"food": "无糖奶茶"})
    assert not d({"food": "可乐 (diet)", "sugar_g": 0})
    assert not d({"food": "柠檬茶", "sugar_g": 2})               # keyword, but known sugar below the threshold
    assert d({"food": "自制饮品", "sugary_drink_flag": True}) and not d({"food": "可乐", "sugary_drink_flag": False})


def test_day_totals_coverage_unknown_is_not_zero():
    rows = [{"sugar_g": 10.0, "added_sugar_g": 8.0}, {"sugar_g": None}, {"sugar_g": 2.0, "sugary_drink": True}]
    t = extras.day_totals(rows)
    assert t["sugar_g"] == 12.0 and t["added_sugar_g"] == 8.0 and t["fiber_g"] is None
    assert t["known"] == {"of": 3, "sugar_g": 2, "added_sugar_g": 1, "fiber_g": 0, "sodium_mg": 0}
    assert t["sugary_drinks"] == 1


def test_adherence():
    goal = {"calories": 2000, "protein_g": 150}
    assert daily.adherence({"logged": True, "calories": 2150, "protein_g": 140}, goal) == {"calories": True, "protein": True}
    assert daily.adherence({"logged": True, "calories": 2300, "protein_g": 120}, goal) == {"calories": False, "protein": False}
    assert daily.adherence({"logged": False, "calories": 0, "protein_g": 0}, goal) == {"calories": None, "protein": None}


def test_plan_to_version_two_presets_and_weekdays():
    presets = {"t": {"calories": "2500.00", "protein": "150", "carbs": "270", "fat": "65", "saturated_fat": "20"},
               "r": {"calories": "2300", "protein": "150", "carbs": "210", "fat": "65"}}
    plan = {"id": "p1", "plan_name": "Plan A", "start_date": "2026-10-14T00:00:00.000Z",
            **{f"{d}_preset_id": "r" if d in REST_DAYS else "t" for d in G.WEEKDAYS}}
    v, why = G.plan_to_version(plan, presets)
    assert why is None and v["effective_from"] == date(2026, 10, 14) and v["training_weekdays"] == WEEKDAYS
    assert v["training"]["calories"] == 2500 and v["training"]["sat_fat_max_g"] == 20 and v["rest"]["carbs_g"] == 210
    assert v["import_key"] == "sparky-plan:p1" and v["name"] == "Plan A"
    assert G.plan_to_version({**plan, "tuesday_preset_id": "x"}, presets)[1] == "找不到计划用的目标预设"
    one, _ = G.plan_to_version({"id": "p2", "start_date": "2026-01-01", "monday_preset_id": "r"}, presets)
    assert one["training"] == one["rest"] and one["training_weekdays"] == []


def _ctx(food, goal_cap=None, day=TODAY):
    goal = {"added_sugar_max_g": goal_cap or RP.ADDED_SUGAR_CAP_G, "added_sugar_cap_default": goal_cap is None}
    return Ctx(user_id=1, day=day, food=food, goals=[goal] * 7)


def test_sugar_rule():
    iso = TODAY.isoformat()
    r = rules_nutrition.sugar_caps(_ctx([{"date": iso, "food": "蛋糕", "added_sugar_g": 30.0, "sugar_g": 35.0}]))
    assert r.status == "triggered" and [(x.subject, x.level) for x in r.reminders] == [("added_sugar", "info")]
    r = rules_nutrition.sugar_caps(_ctx([{"date": iso, "food": "蛋糕", "added_sugar_g": 60.0}]))
    assert r.reminders[0].level == "warning"
    r = rules_nutrition.sugar_caps(_ctx([{"date": iso, "food": "蛋糕", "added_sugar_g": 30.0}], goal_cap=40))
    assert r.status == "not_triggered"                          # the goal's own cap wins over the default 25 g
    r = rules_nutrition.sugar_caps(_ctx([{"date": iso, "food": "面包", "added_sugar_g": None}]))
    assert r.status == "not_triggered" and r.details["added_sugar_g"] is None   # unknown is not counted as 0 or as over
    drinks = [{"date": (TODAY - timedelta(days=k)).isoformat(), "food": "可乐", "sugary_drink": True} for k in (0, 2, 6)]
    r = rules_nutrition.sugar_caps(_ctx(drinks))
    assert [(x.subject, x.level) for x in r.reminders] == [("sugary_drinks", "warning")]
    old = [dict(x, date=(TODAY - timedelta(days=7)).isoformat()) for x in drinks]   # outside the 7-day window
    assert rules_nutrition.sugar_caps(_ctx(old)).status == "insufficient_data"
    assert rules_nutrition.sugar_caps(Ctx(user_id=1, day=TODAY, errors={"food": "x"})).status == "error"


# ================================================================ database
@pytest.fixture(scope="module")
def dsn():
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
def world(dsn):
    """A shares nutrition with B; C shares nothing."""
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"]
               for n in ("A", "B", "C", "D")}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        tok["A_ro"] = auth.create_token(conn, ids["A"], "t", ["read"])
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["A"], ids["B"], ["nutrition"]))
    return ids, tok


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def h(t):
    return {"Authorization": f"Bearer {t}"}


class FakeSparky:
    """Goal fallback, weekly goal plans, food entries; records writes."""

    def __init__(self, food=None):
        self.food = food or []      # raw SparkyFitness entries
        self.writes = []
        self.goal_calls = 0

    def get(self, path, **params):
        if path.startswith("/goals/by-date/"):
            self.goal_calls += 1
            return {"preset_name": "默认", "calories": 2500, "protein": 140, "carbs": 280, "fat": 70}
        if path == "/goal-presets":
            return [{"id": "t1", "calories": "2500", "protein": "150", "carbs": "270", "fat": "65"},
                    {"id": "r1", "calories": "2300", "protein": "150", "carbs": "210", "fat": "65"},
                    {"id": "t2", "calories": "2100", "protein": "140", "carbs": "220", "fat": "60"},
                    {"id": "r2", "calories": "1900", "protein": "140", "carbs": "170", "fat": "60"}]
        if path == "/weekly-goal-plans":
            days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
            mk = lambda pid, name, start, t, r, active: {
                "id": pid, "plan_name": name, "start_date": start, "is_active": active,
                **{f"{d}_preset_id": (r if d in REST_DAYS else t) for d in days}}
            return [mk("p2", "Plan 2", (TODAY - timedelta(days=1)).isoformat(), "t2", "r2", True),
                    mk("p1", "Plan 1", (TODAY - timedelta(days=8)).isoformat(), "t1", "r1", False)]
        if path.startswith("/food-entries/range/"):
            return self.food
        if path == "/foods/foods-paginated":
            return {"foods": []}
        if path == "/meal-types":
            return [{"id": f"mt-{n}", "name": n} for n in ("breakfast", "lunch", "snacks", "dinner")]
        if path.startswith("/food-entries/by-date/"):
            return []
        raise AssertionError(path)

    def request(self, method, path, body=None, params=None, multipart_field=None):
        self.writes.append((method, path, body))
        if path == "/foods":
            return {"id": f"food-{len(self.writes)}", "default_variant": {"id": "v"}, **body}
        if path == "/food-entries":
            return {"id": f"fe-{len(self.writes)}", **body}
        return {"id": "x"}


def test_first_version_needs_macros_then_carries_over(conn, world):
    ids, _ = world
    c = ids["C"]
    with pytest.raises(ValueError, match="缺"):
        G.set_goals(conn, c, training={"calories": 2000}, reason="开始", set_by="test", actor_user_id=c)
    with pytest.raises(ValueError, match="reason"):
        G.set_goals(conn, c, training=TRAIN, reason=" ", set_by="test", actor_user_id=c)
    with pytest.raises(ValueError, match="饱和脂肪上限"):
        G.set_goals(conn, c, training={**TRAIN, "sat_fat_max_g": 80}, reason="x", set_by="test", actor_user_id=c)
    with pytest.raises(ValueError, match="之间"):
        G.set_goals(conn, c, training={**TRAIN, "calories": 20000}, reason="x", set_by="test", actor_user_id=c)
    start = TODAY - timedelta(days=14)
    v1 = G.set_goals(conn, c, training=TRAIN, rest=REST, training_weekdays=WEEKDAYS, effective_from=start,
                     reason="第一版", set_by="test", actor_user_id=c, name="V1")
    assert v1["warnings"] == [] and v1["version"]["training_weekdays"] == WEEKDAYS
    # Only the rest-day calories and an added sugar cap change; everything else carries over (name too)
    v2 = G.set_goals(conn, c, rest={"calories": 1700, "added_sugar_max_g": 20}, effective_from=TODAY - timedelta(days=3),
                     reason="减一点", set_by="test", actor_user_id=c)["version"]
    assert v2["rest"] == {**REST, "calories": 1700, "sat_fat_max_g": None, "added_sugar_max_g": 20, "fiber_min_g": None}
    assert v2["training"]["calories"] == 2100 and v2["training_weekdays"] == WEEKDAYS and v2["name"] == "V1"
    # A correction with the same effective date: the newest row wins, the older one stays as superseded history
    v3 = G.set_goals(conn, c, rest={"calories": 1750}, effective_from=TODAY - timedelta(days=3), reason="改正",
                     set_by="test", actor_user_id=c)
    assert v3["warnings"] == []           # 4*140 + 4*170 + 9*60 = 1780 kcal vs 1750: within the tolerance
    hist = G.history(conn, c)
    assert [(x["reason"], x["superseded"]) for x in hist] == [("改正", False), ("减一点", True), ("第一版", False)]
    resolve = G.resolver(conn, c, client=FakeSparky())
    for k in range(14):   # versions by date, training / rest by weekday
        d = TODAY - timedelta(days=k)
        g = resolve(d)
        want = (1750 if d.isoweekday() not in WEEKDAYS else 2100) if k <= 3 else \
            (REST["calories"] if d.isoweekday() not in WEEKDAYS else 2100)
        assert g["calories"] == want and g["source"] == "body_engine", d
        assert g["day_type"] == ("training" if d.isoweekday() in WEEKDAYS else "rest")
    assert resolve(TODAY)["added_sugar_max_g"] in (20, RP.ADDED_SUGAR_CAP_G)
    fake = FakeSparky()
    before = G.resolver(conn, c, client=fake)(start - timedelta(days=1))
    assert before["source"] == "sparkyfitness" and before["calories"] == 2500 and before["added_sugar_cap_default"]
    assert fake.goal_calls == 1


def test_macro_warning(conn, world):
    ids, _ = world
    d = ids["D"]
    res = G.set_goals(conn, d, training={"calories": 3000, "protein_g": 100, "carbs_g": 100, "fat_g": 50},
                      reason="x", set_by="test", actor_user_id=d)
    assert [w for w in res["warnings"] if "差得多" in w] and not [w for w in res["warnings"] if "没有训练日" in w]
    assert res["version"]["training"] == res["version"]["rest"]   # the missing set copies the given one


def test_import_sparky_plans_dry_run_then_apply_idempotent(conn, world):
    ids, _ = world
    b = ids["B"]
    fake = FakeSparky()
    dry = G.import_sparky_plans(conn, b, apply=False, client=fake)
    assert [x["plan"] for x in dry["created"]] == ["Plan 1", "Plan 2"] and G.history(conn, b) == []
    res = G.import_sparky_plans(conn, b, apply=True, client=fake)
    assert all(x["version_id"] for x in res["created"])
    again = G.import_sparky_plans(conn, b, apply=True, client=fake)
    assert again["created"] == [] and len(again["existing"]) == 2 and len(G.history(conn, b)) == 2
    resolve = G.resolver(conn, b, client=fake)
    for k in range(8):
        d = TODAY - timedelta(days=k)
        train = d.isoweekday() in WEEKDAYS
        want = (2100 if train else 1900) if k <= 1 else (2500 if train else 2300)
        assert resolve(d)["calories"] == want, d
    assert G.history(conn, b)[0]["set_by"] == "import" and G.history(conn, b)[0]["actor_user_id"] is None


def test_api_get_put_and_auth(client, world):
    ids, tok = world
    a = ids["A"]
    r = client.get("/v1/users/me/goals/nutrition", headers=h(tok["A"]))
    assert r.status_code == 200 and r.json()["version"] is None and r.json()["history"] == []
    body = {"training": TRAIN, "rest": REST, "training_weekdays": WEEKDAYS, "reason": "新计划", "name": "Plan B"}
    r = client.put("/v1/users/me/goals/nutrition", json=body, headers=h(tok["A"]))
    assert r.status_code == 200, r.text
    assert r.json()["version"]["set_by"] == "api" and r.json()["version"]["actor_user_id"] == a
    r = client.put("/v1/users/me/goals/nutrition", json={"rest": {"fat_g": 50}, "reason": "少油",
                                                          "effective_from": (TODAY + timedelta(days=7)).isoformat()},
                   headers=h(tok["A"]))
    assert r.status_code == 200, r.text
    v = client.get(f"/v1/users/{a}/goals/nutrition", headers=h(tok["B"])).json()   # shared with B
    assert v["version"]["name"] == "Plan B" and len(v["upcoming"]) == 1 and v["upcoming"][0]["rest"]["fat_g"] == 50
    assert [x["reason"] for x in v["history"]] == ["少油", "新计划"]
    assert v["goal"]["calories"] == (2100 if TODAY.isoweekday() in WEEKDAYS else 1900)
    day = (TODAY + timedelta(days=8)).isoformat()
    assert client.get(f"/v1/users/me/goals/nutrition?date={day}", headers=h(tok["A"])).json()["version"]["rest"]["fat_g"] == 50
    assert client.get(f"/v1/users/{a}/goals/nutrition", headers=h(tok["C"])).status_code == 403
    assert client.put(f"/v1/users/{a}/goals/nutrition", json=body, headers=h(tok["B"])).status_code == 403
    assert client.put("/v1/users/me/goals/nutrition", json=body, headers=h(tok["A_ro"])).status_code == 403
    for bad in ({"training": TRAIN}, {**body, "extra": 1}, {**body, "training_weekdays": [8]},
                {**body, "effective_from": (TODAY + timedelta(days=400)).isoformat()}):
        assert client.put("/v1/users/me/goals/nutrition", json=bad, headers=h(tok["A"])).status_code == 422, bad


def test_tools(conn, world):
    ids, _ = world
    assert TOOLS_BY_NAME["set_nutrition_goals"].writes and not TOOLS_BY_NAME["get_nutrition_goals"].writes
    empty = dict.fromkeys(G.FIELDS)
    res = run_tool(conn, ids["A"], "mcp:test", "set_nutrition_goals", {
        "training": {**empty, "protein_g": 145}, "rest": empty, "training_weekdays": None, "effective_from": None,
        "name": None, "reason": "多点蛋白"})
    assert res["version"]["set_by"] == "mcp:test" and res["version"]["training"]["protein_g"] == 145
    assert res["version"]["rest"]["protein_g"] == 140
    got = run_tool(conn, ids["B"], "mcp", "get_nutrition_goals", {"user": str(ids["A"]), "date": None})
    assert [x["reason"] for x in got["history"]] == ["少油", "多点蛋白", "新计划"]   # newest effective date first
    assert got["version"]["reason"] == "多点蛋白" and got["upcoming"][0]["reason"] == "少油"
    with pytest.raises(PermissionError):
        run_tool(conn, ids["C"], "mcp", "get_nutrition_goals", {"user": str(ids["A"]), "date": None})


def test_log_food_sugar_fields_and_daily(conn, world, monkeypatch):
    ids, _ = world
    a = ids["A"]
    iso = TODAY.isoformat()
    fake = FakeSparky()
    item = {"date": iso, "meal": "snacks", "time": "15:00", "food": "可乐 330ml", "amount": "1 罐", "calories": 139,
            "protein_g": 0, "carbs_g": 35, "fat_g": 0, "saturated_fat_g": None, "alcohol_g": None, "sugar_g": 35,
            "added_sugar_g": 35, "fiber_g": None, "sodium_mg": 10, "sugary_drink": None, "notes": None}
    with pytest.raises(ValueError, match="添加糖不能多于总糖"):
        sparky_write.log_food(conn, a, [dict(item, added_sugar_g=40)], False, client=fake)
    res = sparky_write.log_food(conn, a, [item], False, client=fake)
    entry_id = res["created"][0]["id"]
    (_, _, food), (_, _, _) = fake.writes
    assert food["sugars"] == 35 and food["sodium"] == 10 and "dietary_fiber" not in food   # unknown not sent
    row = conn.execute("SELECT * FROM food_entry_extras WHERE user_id = %s AND entry_id = %s", (a, entry_id)).fetchone()
    assert row["added_sugar_g"] == 35 and row["source"] == "log_food" and row["fiber_g"] is None
    # Reading back: SparkyFitness has sugars (35) but no added sugar; extras fill it in
    fake.food = [{"id": entry_id, "entry_date": iso, "entry_time": "15:00:00", "meal_type": "snacks",
                  "food_name": "可乐 330ml", "quantity": 1, "serving_size": 1, "calories": 139, "protein": 0, "carbs": 35,
                  "fat": 0, "sugars": 35, "sodium": 10},
                 {"id": "old-1", "entry_date": iso, "meal_type": "lunch", "food_name": "米饭", "quantity": 1,
                  "serving_size": 1, "calories": 300, "protein": 6, "carbs": 66, "fat": 1, "notes": "添加糖 0g"}]
    n = daily.nutrition(conn, a, TODAY, TODAY, client=fake)
    d = n["days"][0]
    assert d["calories"] == 439 and d["sugar_g"] == 35 and d["added_sugar_g"] == 35 and d["fiber_g"] is None
    assert d["known"] == {"of": 2, "sugar_g": 1, "added_sugar_g": 1, "fiber_g": 0, "sodium_mg": 1}
    assert d["sugary_drinks"] == 1 and d["goal"]["source"] == "body_engine"
    assert n["entries"][1]["sugary_drink"] is True
    # Notes backfill: dry run writes nothing, apply fills old-1's added sugar, a second run finds nothing new
    dry = extras.backfill_from_notes(conn, a, TODAY, TODAY, apply=False, client=fake)
    assert [u["id"] for u in dry["updates"]] == ["old-1"]
    assert conn.execute("SELECT 1 FROM food_entry_extras WHERE entry_id = 'old-1'").fetchone() is None
    extras.backfill_from_notes(conn, a, TODAY, TODAY, apply=True, client=fake)
    assert extras.backfill_from_notes(conn, a, TODAY, TODAY, apply=True, client=fake)["updates"] == []
    d = daily.nutrition(conn, a, TODAY, TODAY, client=fake)["days"][0]
    assert d["added_sugar_g"] == 35 and d["known"]["added_sugar_g"] == 2
    # Status cards: today's added sugar against the cap in effect, 7-day average over days with numbers
    monkeypatch.setattr(sparky_client, "client_for_user", lambda conn, uid: fake)
    items = {i["key"]: i for i, _ in status.calc_sugar(conn, a, TODAY)}
    assert (items["added_sugar_today"]["value"], items["added_sugar_today"]["tone"]) == ("35", "warn")
    assert items["added_sugar_7d"]["value"] == "35" and items["added_sugar_7d"]["spark"][-1] == 35
    assert items["added_sugar_today"]["label"] == "今日添加糖" and items["added_sugar_7d"]["label"] == "7 天平均"
    # Weekly report: nutrition block judged against the goal in effect that day
    rep = reports._nutrition_block(conn, a, TODAY - timedelta(days=6), TODAY, fake)
    assert rep["days_logged"] == 1 and rep["added_sugar_avg_g"] == 35 and rep["sugary_drinks"] == 1
    assert rep["calories_judged_days"] == 1 and rep["calories_on_target_days"] == 0   # 439 kcal vs 1900 / 2100


def test_templates_and_recipes_carry_sugar(conn, world):
    ids, _ = world
    d = ids["D"]
    items = [{"food": "酸奶", "grams": 200, "kcal": 180, "protein_g": 10, "carbs_g": 20, "fat_g": 6.5, "sat_fat_g": 4,
              "purine_level": "low", "red_meat_g": 0, "sugar_g": 18, "added_sugar_g": 8, "fiber_g": None,
              "sodium_mg": None, "note": None},
             {"food": "燕麦", "grams": 40, "kcal": 150, "protein_g": 5, "carbs_g": 27, "fat_g": 2.8, "sat_fat_g": 0.5,
              "purine_level": "low", "red_meat_g": 0, "note": None}]
    tpl = meals.create_template(conn, d, "早餐", "breakfast", items, False)
    assert tpl["totals"]["sugar_g"] == 18 and tpl["totals"]["fiber_g"] is None
    scaled = meals.scale_items(tpl["items"], [{"food": "酸奶", "grams": 100}])
    assert scaled[0]["added_sugar_g"] == 4 and scaled[1].get("added_sugar_g") is None
    ings = recipes.check_ingredients([{**i, "purine_mg": 0, "purchase_unit": None, "store": None} for i in items])
    ps = recipes.per_serving(ings, 2)
    assert ps["sugar_g"] == 9 and ps["added_sugar_g"] == 4 and ps["sodium_mg"] is None
