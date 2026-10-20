"""「我的饮食档案」(issue #32): sections on the profile card, version log, energy averages and deficit, goals adapter,
AI tools and API authorization. Needs Postgres: separate test DB BODY_TEST_DB_NAME (default body_test_diet_profile)."""
import os
import types
from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.ai.chat import ALWAYS_CONFIRM
from body_engine.assistant import diet_goals, diet_profile as DP, diet_profile_params as P, memory
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_diet_profile")

CONSTRAINTS = [{"rule": "中低嘌呤，少内脏和浓汤", "basis": "尿酸 480 μmol/L（09-01）"},
               {"rule": "饱和脂肪 ≤ 20 g/天", "basis": "LDL 3.8 mmol/L"}]
DAY_PLAN = {"meals": [{"meal": "早餐", "foods": "鸡蛋 2 个 + 燕麦 60g + 牛奶 250ml", "kcal": 520, "protein_g": 32},
                      {"meal": "晚餐", "foods": "鸡胸 200g + 米饭 150g + 蔬菜", "kcal": None, "protein_g": None}],
            "staples": ["鸡蛋", "希腊酸奶", "鸡胸"]}


# ---------------------------------------------------------------- no database needed
def test_tools_registered_and_not_confirmed():
    w, r = TOOLS_BY_NAME["update_diet_profile"], TOOLS_BY_NAME["get_diet_profile"]
    assert w.writes and w.scope == "memory" and not r.writes and r.scope == "memory"
    assert "update_diet_profile" not in ALWAYS_CONFIRM   # not a delete: runs without confirmation
    assert "user" not in w.input_schema["properties"]      # writes only for the caller


def test_bump_render_and_validation():
    assert DP._bump(None) == "V1.0" and DP._bump("V1.9") == "V1.10" and DP._bump("V2.3") == "V2.4"
    c = DP._clean("constraints", CONSTRAINTS)
    assert DP.render("constraints", c).startswith("中低嘌呤，少内脏和浓汤（依据：尿酸 480")
    d = DP._clean("day_plan", DAY_PLAN)
    assert DP.render("day_plan", d).splitlines() == ["早餐：鸡蛋 2 个 + 燕麦 60g + 牛奶 250ml（约 520 kcal）",
                                                     "晚餐：鸡胸 200g + 米饭 150g + 蔬菜", "常备：鸡蛋、希腊酸奶、鸡胸"]
    r = DP._clean("rationale", {"goal_weight_kg": 80, "checkpoints": [{"date": "2026-12-31", "weight_kg": 84},
                                                                       {"date": "2026-11-30", "weight_kg": 86.5}]})
    assert [x["date"] for x in r["checkpoints"]] == ["2026-11-30", "2026-12-31"]   # sorted
    assert DP.render("rationale", r) == "目标体重 80 kg；检查点 2026-11-30 86.5 kg、2026-12-31 84 kg"
    with pytest.raises(ValueError, match="basis"):
        DP._clean("constraints", [{"rule": "少糖", "basis": ""}])
    with pytest.raises(ValueError, match="超出范围"):
        DP._clean("rationale", {"goal_weight_kg": 5})
    with pytest.raises(ValueError, match="空"):
        DP._clean("targets", {"steps": None, "water_ml": None, "note": None})
    with pytest.raises(ValueError, match="最多"):
        DP._clean("adjust_rules", [{"when": "a", "action": "b"}] * (P.MAX_ITEMS + 1))


def test_plan_math():
    en = {"total_kcal": 2800}
    p = DP.plan_math(en, {"avg_kcal": 2300}, {"kg": 90.0})
    assert p["deficit_kcal"] == 500 and p["loss_kg_per_week"] == round(500 * 7 / P.KCAL_PER_KG, 2) and not p["fast"]
    assert DP.plan_math(en, {"avg_kcal": 1500}, {"kg": 60.0})["fast"]       # 1.18 kg/week > 1 % of 60 kg
    assert DP.plan_math({"total_kcal": None}, {"avg_kcal": 2300}, None) is None
    assert DP.plan_math(en, {"avg_kcal": None}, None) is None


def test_goals_adapter_sparky_fallback_and_hook(monkeypatch):
    class FakeSparky:
        def get(self, path):
            d = date.fromisoformat(path.rsplit("/", 1)[1])
            train = d.weekday() < 5
            return {"preset_name": "训练日" if train else "休息日", "calories": 2400 if train else 2100,
                    "protein": 180, "carbs": 250 if train else 180, "fat": 65, "water_goal_ml": 3000}
    monkeypatch.setattr(diet_goals, "_goals_module", lambda: None)
    g = diet_goals.current(None, 1, date(2026, 10, 19), client=FakeSparky())
    assert g["source"] == "sparkyfitness" and {s["name"]: s["days"] for s in g["sets"]} == {"训练日": 5, "休息日": 2}
    assert g["avg_kcal"] == pytest.approx((5 * 2400 + 2 * 2100) / 7)

    # issue #31's versioned goals, when installed, are used instead of SparkyFitness
    mod = types.SimpleNamespace(current_goals=lambda conn, uid, day: {
        "training_day": {"kcal": 2500, "protein_g": 180, "added_sugar_max_g": 25}, "rest_day": {"calories": 2100},
        "effective_from": "2026-10-21", "version": 3})

    class Conn:   # the adapter wraps the hook in a savepoint
        def transaction(self):
            import contextlib
            return contextlib.nullcontext()
    monkeypatch.setattr(diet_goals, "_goals_module", lambda: mod)
    g = diet_goals.current(Conn(), 1, date(2026, 10, 19))
    assert g["source"] == "nutrition_goals" and g["avg_kcal"] == 2300 and g["version"] == 3
    assert g["sets"][0] == {"name": "训练日", "calories": 2500, "protein_g": 180, "carbs_g": None, "fat_g": None,
                            "sat_fat_max_g": None, "added_sugar_max_g": 25, "fiber_g": None, "water_ml": None}


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", "body_accept", ""):
        pytest.exit("the test DB must not be a real DB")
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
    """A owns the data; B has a memory share only; C has memory + nutrition + metrics; D has nothing."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, birth_date, height_cm) VALUES (%s, '1990-01-01', 180) "
                               "RETURNING id", (n,)).fetchone()["id"] for n in ("A", "B", "C", "D")}
        tokens = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s), (%s, %s, %s)",
                     (ids["A"], ids["B"], ["memory"], ids["A"], ids["C"], ["memory", "nutrition", "metrics"]))
        today = DP._local_today(conn, ids["A"])
        for k in range(1, 29):   # 28 complete days; one partial-sync day (basal < 800) is skipped
            d = today - timedelta(days=k)
            basal = 500 if k == 3 else 2000
            conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source) VALUES
                            (%s, %s, 'basal_energy_burned', %s, 'kcal', 'sum', 'ow'),
                            (%s, %s, 'active_energy_burned', 600, 'kcal', 'sum', 'ow')""",
                         (ids["A"], d, basal, ids["A"], d))
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                        VALUES (%s, %s, 'body_mass', 90.24, 'kg', 'avg', 'ow')""", (ids["A"], today - timedelta(days=2)))
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


def test_energy_average(conn, world):
    ids, _ = world
    en = DP.energy(conn, ids["A"], DP._local_today(conn, ids["A"]))
    assert en["days"] == 27 and en["basal_kcal"] == 2000 and en["active_kcal"] == 600 and en["total_kcal"] == 2600
    assert DP.energy(conn, ids["D"], date.today())["total_kcal"] is None   # too few days -> no average


def test_tool_update_versions_and_card(conn, world):
    ids, _ = world
    a = ids["A"]
    out = run_tool(conn, a, "test", "update_diet_profile", {
        "constraints": CONSTRAINTS, "day_plan": DAY_PLAN, "rationale": {"goal_weight_kg": 80, "checkpoints": []},
        "changes": "建立饮食档案", "reason": "第一次整理"})
    assert out["updated"] == ["rationale", "constraints", "day_plan"] and out["version"]["version"] == "V1.0"
    # the sections are profile card items: get_memory shows their text, the briefing lists them
    card = {c["key"]: c for c in memory.get_memory(conn, a)["profile_card"]}
    assert card["diet_constraints"]["label"] == "饮食约束" and "依据：尿酸 480" in card["diet_constraints"]["value"]
    # a later change: only the given section is replaced, version auto +0.1
    out = run_tool(conn, a, "test", "update_diet_profile", {
        "adjust_rules": [{"when": "周均体重 2 周不降", "action": "每天 −150 kcal（碳水）"}],
        "changes": "加调整规则", "reason": "平台期"})
    assert out["version"]["version"] == "V1.1"
    # a section fix without a version note does not add a version
    assert run_tool(conn, a, "test", "update_diet_profile", {"targets": {"steps": 9000, "water_ml": 3000}})["version"] is None
    # explicit major version; duplicate refused; reason without changes refused; nothing given refused
    assert DP.update(conn, a, {}, "热量下调", "体重 2 周没动", "V2.0")["version"]["version"] == "V2.0"
    for kw in (dict(changes="x", version="V2.0"), dict(reason="r"), {}, dict(changes="x", version="2.1")):
        with pytest.raises(ValueError):
            DP.update(conn, a, {}, **kw)
    with pytest.raises(ValueError, match="没有这些块"):
        DP.update(conn, a, {"lunch": {}})
    s = DP.summary(conn, a)
    assert s["version"] == "V2.0" and s["sections"] == 5 and s["updated_on"] == DP._local_today(conn, a)


def test_text_edit_clears_structured_data(conn, world):
    ids, _ = world
    c = ids["C"]
    DP.update(conn, c, {"constraints": CONSTRAINTS[:1]})
    assert conn.execute("SELECT data FROM assistant_profile WHERE user_id = %s AND key = 'diet_constraints'",
                        (c,)).fetchone()["data"] is not None
    memory.update_profile(conn, c, "diet_constraints", "改成手写的一段", source="user")
    sec = DP.view(conn, c, c)["sections"][0]
    assert sec["data"] is None and sec["text"] == "改成手写的一段" and sec["label"] == "饮食约束"


def test_write_tool_only_for_self(conn, world):
    ids, _ = world
    with pytest.raises(PermissionError):
        run_tool(conn, ids["B"], "test", "update_diet_profile", {"user": str(ids["A"]), "targets": {"steps": 1}})
    # the read tool honours the viewer's other scopes: B (memory only) gets no goals / energy
    v = run_tool(conn, ids["B"], "test", "get_diet_profile", {"user": str(ids["A"])})
    assert v["goals"] is None and v["energy"] is None and set(v["hidden"]) == {"goals", "energy"} and not v["self"]
    with pytest.raises(PermissionError):
        run_tool(conn, ids["D"], "test", "get_diet_profile", {"user": str(ids["A"])})


def test_api_view_and_auth(client, world, monkeypatch):
    ids, tokens = world
    monkeypatch.setattr(diet_goals, "current", lambda conn, uid, day, client=None: {
        "source": "sparkyfitness", "sets": [{"name": "训练日", "calories": 2200}], "avg_kcal": 2200, "error": None})
    r = client.get("/v1/users/me/diet-profile", headers=h(tokens["A"]))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["self"] and b["version"] == "V2.0" and [v["version"] for v in b["versions"]][:2] == ["V2.0", "V1.1"]
    assert [s["section"] for s in b["sections"]] == ["targets", "rationale", "constraints", "day_plan", "adjust_rules"]
    assert b["energy"]["total_kcal"] == 2600 and b["weight"]["kg"] == pytest.approx(90.24)
    assert b["plan"]["deficit_kcal"] == 400 and b["plan"]["loss_kg_per_week"] == round(400 * 7 / P.KCAL_PER_KG, 2)
    assert client.get("/v1/users/me/diet-profile?brief=true", headers=h(tokens["A"])).json()["version"] == "V2.0"
    # C has every scope: same blocks; B only memory: sections + versions, no goals / energy; D: 403
    c = client.get(f"/v1/users/{ids['A']}/diet-profile", headers=h(tokens["C"])).json()
    assert c["plan"]["deficit_kcal"] == 400 and not c["self"]
    bb = client.get(f"/v1/users/{ids['A']}/diet-profile", headers=h(tokens["B"])).json()
    assert bb["goals"] is None and bb["energy"] is None and bb["plan"] is None and len(bb["sections"]) == 5
    assert client.get(f"/v1/users/{ids['A']}/diet-profile", headers=h(tokens["D"])).status_code == 403
    assert client.get(f"/v1/users/{ids['A']}/diet-profile?brief=true", headers=h(tokens["D"])).status_code == 403


def test_goals_unavailable_still_shows_profile(client, world):
    """No SparkyFitness linked: goals carry the error, the rest of the profile still renders."""
    _, tokens = world
    b = client.get("/v1/users/me/diet-profile", headers=h(tokens["A"])).json()
    assert b["goals"]["error"] and b["goals"]["sets"] == [] and b["plan"] is None and b["energy"]["total_kcal"] == 2600
