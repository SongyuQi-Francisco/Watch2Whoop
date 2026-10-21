"""Pantry follows logged meals + best before (issue #104, docs/grocery-plan.md §3): log_food / quick meals / recipes take what was
eaten from the pantry when the user's switch is on (matched by name or the product's matches, converted like the list, never
below 0, soonest best before first), unmatched foods are skipped; undo / delete / rollback_write put it back; the AI write log
has each deduction; expiring items reach get_shopping_context and get_briefing; per-user isolation.
Needs Postgres: separate test DB (default body_test_pantry_auto). SparkyFitness is the in-memory fake from test_ai_writes."""
import os
from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant import briefing, meals, recipes, sparky_write
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.grocery import pantry_auto, params as P, prices, store
from test_ai_writes import Fake

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_PANTRY_AUTO", "body_test_pantry_auto")


# ---------------------------------------------------------------- no database
def test_parse_amount():
    assert pantry_auto.parse_amount("2 个") == (2, "pcs")
    assert pantry_auto.parse_amount("200 g") == (200, "g") and pantry_auto.parse_amount("200g 熟重") == (200, "g")
    assert pantry_auto.parse_amount("1 碗（约 250g 熟饭）") == (250, "g")
    assert pantry_auto.parse_amount("0.5 kg") == (500, "g") and pantry_auto.parse_amount("1.5 L") == (1500, "ml")
    assert pantry_auto.parse_amount("250ml") == (250, "ml")
    assert pantry_auto.parse_amount("1 份") is None and pantry_auto.parse_amount("1 large") is None
    assert pantry_auto.parse_amount("") is None and pantry_auto.parse_amount(None) is None


def test_tool_registered():
    t = TOOLS_BY_NAME["set_pantry_auto"]
    assert t.writes and t.scope == "nutrition" and "user" not in t.input_schema["properties"]
    assert "best_before" in TOOLS_BY_NAME["set_pantry"].input_schema["properties"]["items"]["items"]["properties"]
    assert P.PANTRY_AUTO_DEFAULT in (True, False) and P.PANTRY_EXPIRY_DAYS >= 0


# ---------------------------------------------------------------- database
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
    """A (owner), B (A shares nutrition with B), C (no share)."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, birth_date, height_cm) VALUES (%s, '1990-01-01', 175) "
                               "RETURNING id", (n,)).fetchone()["id"] for n in ("A", "B", "C")}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["nutrition"]))
        pr = {u: store.save_product(conn, ids[u], dict(name="鸡蛋", package_size=12, package_unit="pcs", piece_g=50,
                                                         matches=["egg", "eggs"]))["id"] for u in ("A", "B")}
    return ids, tok, pr


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture()
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(sparky_write.sparky_client, "write_client_for_user", lambda conn, uid: f)
    return f


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def _pantry(conn, uid):
    return {p["name"]: p for p in store.list_pantry(conn, uid)}


def _food(conn, uid, food, amount, meal="breakfast"):
    return {"date": sparky_write.user_today(conn, uid).isoformat(), "meal": meal, "time": "08:00", "food": food,
            "amount": amount, "calories": 143, "protein_g": 12.6, "carbs_g": 0.7, "fat_g": 9.5, "saturated_fat_g": 3,
            "alcohol_g": None, "notes": None}


def test_switch_off_by_default_then_log_and_undo(conn, world, fake):
    ids, _, pr = world
    a = ids["A"]
    store.set_pantry(conn, a, [{"name": "鸡蛋", "quantity": 6, "unit": "pcs", "product_id": pr["A"]}])
    assert pantry_auto.enabled(conn, a) is P.PANTRY_AUTO_DEFAULT
    pantry_auto.set_enabled(conn, a, False)
    run_tool(conn, a, "butler", "log_food", {"items": [_food(conn, a, "鸡蛋", "2 个")], "allow_duplicates": True})
    assert _pantry(conn, a)["鸡蛋"]["quantity"] == 6   # switched off: unchanged
    assert run_tool(conn, a, "butler", "set_pantry_auto", {"enabled": True})["enabled"]
    res = run_tool(conn, a, "butler", "log_food", {"items": [_food(conn, a, "鸡蛋", "2 个", "lunch")], "allow_duplicates": True})
    assert res["pantry_used"][0]["qty"] == 2 and _pantry(conn, a)["鸡蛋"]["quantity"] == 4
    log = conn.execute("SELECT * FROM ai_write_log WHERE user_id = %s AND kind = 'pantry' ORDER BY id DESC LIMIT 1", (a,)).fetchone()
    assert log["action"] == "update" and log["target"] == res["created"][0]["id"] and log["before"][0]["quantity"] == 6
    out = run_tool(conn, a, "butler", "undo_sparky_entry", {"kind": "food", "date": res["created"][0]["date"],
                                                            "id": res["created"][0]["id"], "meal": None, "food": None, "time": None})
    assert out["pantry_restored"][0]["qty"] == 2 and _pantry(conn, a)["鸡蛋"]["quantity"] == 6
    # undoing twice does not add twice
    assert pantry_auto.restore(conn, a, res["created"][0]["id"]) == []
    conn.commit()


def test_rollback_write_and_delete_restore(conn, world, fake):
    ids, _, _ = world
    a = ids["A"]
    res = run_tool(conn, a, "butler", "log_food", {"items": [_food(conn, a, "egg", "3 pcs", "dinner")], "allow_duplicates": True})
    assert _pantry(conn, a)["鸡蛋"]["quantity"] == 3   # "egg" matches the product linked to the pantry row
    log = conn.execute("SELECT id FROM ai_write_log WHERE user_id = %s AND kind = 'pantry' ORDER BY id DESC LIMIT 1", (a,)).fetchone()
    run_tool(conn, a, "butler", "rollback_write", {"log_id": log["id"]})   # the pantry goes back, the entry stays
    assert _pantry(conn, a)["鸡蛋"]["quantity"] == 6 and fake.food[res["created"][0]["date"]]
    # Today page delete of an entry
    res = run_tool(conn, a, "butler", "log_food", {"items": [_food(conn, a, "鸡蛋", "1 个", "snacks")], "allow_duplicates": True})
    assert _pantry(conn, a)["鸡蛋"]["quantity"] == 5
    sparky_write.delete_food_entry(conn, a, sparky_write.user_today(conn, a), res["created"][0]["id"])
    assert _pantry(conn, a)["鸡蛋"]["quantity"] == 6
    # rollback_write of the food create: entry deleted, pantry back
    res = run_tool(conn, a, "butler", "log_food", {"items": [_food(conn, a, "鸡蛋", "2 个", "snacks")], "allow_duplicates": True})
    flog = conn.execute("SELECT id FROM ai_write_log WHERE user_id = %s AND kind = 'food' AND action = 'create' ORDER BY id DESC LIMIT 1",
                        (a,)).fetchone()
    run_tool(conn, a, "butler", "rollback_write", {"log_id": flog["id"]})
    assert _pantry(conn, a)["鸡蛋"]["quantity"] == 6
    conn.commit()


def test_unmatched_and_floor_and_best_before_order(conn, world, fake):
    ids, _, pr = world
    a = ids["A"]
    before = {k: v["quantity"] for k, v in _pantry(conn, a).items()}
    res = run_tool(conn, a, "butler", "log_food", {"items": [_food(conn, a, "蘑菇烧鸡", "1 份", "lunch"),
                                                             _food(conn, a, "鸡蛋饼", "2 个", "lunch")], "allow_duplicates": True})
    assert "pantry_used" not in res and {k: v["quantity"] for k, v in _pantry(conn, a).items()} == before   # no match: no change
    soon = sparky_write.user_today(conn, a) + timedelta(days=1)
    store.set_pantry(conn, a, [{"name": "土鸡蛋", "quantity": 2, "unit": "pcs", "product_id": pr["A"], "best_before": soon.isoformat()}])
    res = run_tool(conn, a, "butler", "log_food", {"items": [_food(conn, a, "鸡蛋", "3 个", "dinner")], "allow_duplicates": True})
    got = _pantry(conn, a)
    assert "土鸡蛋" not in got and got["鸡蛋"]["quantity"] == 5          # the one expiring first went first (2), then 1
    assert [u["pantry"] for u in res["pantry_used"]] == ["土鸡蛋", "鸡蛋"]
    run_tool(conn, a, "butler", "undo_sparky_entry", {"kind": "food", "date": res["created"][0]["date"],
                                                      "id": res["created"][0]["id"], "meal": None, "food": None, "time": None})
    got = _pantry(conn, a)
    assert got["土鸡蛋"]["quantity"] == 2 and got["土鸡蛋"]["best_before"] == soon.isoformat() and got["鸡蛋"]["quantity"] == 6
    # never below 0
    res = run_tool(conn, a, "butler", "log_food", {"items": [_food(conn, a, "鸡蛋", "20 个", "dinner")], "allow_duplicates": True})
    assert sum(u["qty"] for u in res["pantry_used"]) == 8 and not {"鸡蛋", "土鸡蛋"} & set(_pantry(conn, a))
    run_tool(conn, a, "butler", "undo_sparky_entry", {"kind": "food", "date": res["created"][0]["date"],
                                                      "id": res["created"][0]["id"], "meal": None, "food": None, "time": None})
    assert _pantry(conn, a)["鸡蛋"]["quantity"] == 6 and _pantry(conn, a)["土鸡蛋"]["quantity"] == 2
    conn.commit()


def test_template_and_recipe(conn, world, fake):
    ids, _, _ = world
    a = ids["A"]
    store.set_pantry(conn, a, [{"name": "西兰花", "quantity": 500, "unit": "g"}, {"name": "土鸡蛋", "quantity": 0, "unit": "pcs"}])
    t = meals.create_template(conn, a, "早餐", "breakfast", [
        {"food": "鸡蛋", "grams": 100, "kcal": 143, "protein_g": 12.6, "carbs_g": 0.7, "fat_g": 9.5, "sat_fat_g": 3.1,
         "purine_level": "low", "red_meat_g": 0}], False)
    lg = meals.log_template(conn, a, t["id"], allow_duplicates=True)
    assert lg["pantry_used"][0]["qty"] == 2 and _pantry(conn, a)["鸡蛋"]["quantity"] == 4   # 100 g / 50 g per egg
    meals.undo_log(conn, a, lg["log_id"])
    assert _pantry(conn, a)["鸡蛋"]["quantity"] == 6
    r = recipes.create_recipe(conn, a, {"name": "鸡蛋西兰花", "slots": ["dinner"], "servings": 2, "ingredients": [
        {"food": "西兰花", "grams": 300, "kcal": 100, "protein_g": 8, "carbs_g": 14, "fat_g": 1},
        {"food": "鸡蛋", "grams": 200, "kcal": 286, "protein_g": 25, "carbs_g": 1.4, "fat_g": 19},
        {"food": "酱油", "grams": 10, "kcal": 5, "protein_g": 1, "carbs_g": 0, "fat_g": 0}]}, sync=False)
    lr = recipes.log_recipe(conn, a, r["id"], servings=1, allow_duplicates=True)
    got = _pantry(conn, a)
    assert got["西兰花"]["quantity"] == 350 and got["鸡蛋"]["quantity"] == 4   # half the pot
    meals.undo_log(conn, a, lr["log_id"])
    got = _pantry(conn, a)
    assert got["西兰花"]["quantity"] == 500 and got["鸡蛋"]["quantity"] == 6
    conn.commit()


def test_other_users_untouched(conn, world, fake):
    ids, _, pr = world
    a, b, c = ids["A"], ids["B"], ids["C"]
    store.set_pantry(conn, b, [{"name": "鸡蛋", "quantity": 10, "unit": "pcs", "product_id": pr["B"]}])
    pantry_auto.set_enabled(conn, b, True)
    run_tool(conn, b, "butler", "log_food", {"items": [_food(conn, b, "鸡蛋", "2 个")], "allow_duplicates": True})
    assert _pantry(conn, b)["鸡蛋"]["quantity"] == 8 and _pantry(conn, a)["鸡蛋"]["quantity"] == 6   # B's meal never touches A
    store.set_pantry(conn, c, [{"name": "鸡蛋", "quantity": 4, "unit": "pcs"}])   # C: switch at the default (off)
    run_tool(conn, c, "butler", "log_food", {"items": [_food(conn, c, "鸡蛋", "2 个")], "allow_duplicates": True})
    assert _pantry(conn, c)["鸡蛋"]["quantity"] == (2 if P.PANTRY_AUTO_DEFAULT else 4)
    with pytest.raises(PermissionError):   # writes only for oneself
        run_tool(conn, b, "butler", "set_pantry_auto", {"user": str(a), "enabled": False})
    conn.commit()


def test_best_before_reminders(conn, world):
    ids, _, _ = world
    a = ids["A"]
    today = store.today(conn, a)
    store.set_pantry(conn, a, [{"name": "牛奶", "quantity": 1000, "unit": "ml", "best_before": (today + timedelta(days=2)).isoformat()},
                               {"name": "酸奶", "quantity": 500, "unit": "g", "best_before": (today - timedelta(days=1)).isoformat()},
                               {"name": "大米", "quantity": 2000, "unit": "g", "best_before": (today + timedelta(days=60)).isoformat()}])
    got = _pantry(conn, a)
    assert got["牛奶"]["days_left"] == 2 and got["牛奶"]["expiring"] and got["酸奶"]["days_left"] == -1
    assert not got["大米"]["expiring"]
    store.set_pantry(conn, a, [{"name": "牛奶", "quantity": 800, "unit": "ml"}])   # best_before not given: kept
    assert _pantry(conn, a)["牛奶"]["best_before"] == (today + timedelta(days=2)).isoformat()
    conn.commit()
    with pytest.raises(ValueError):
        store.set_pantry(conn, a, [{"name": "牛奶", "quantity": 800, "unit": "ml", "best_before": "10/25"}])
    conn.rollback()
    exp = prices.shopping_context(conn, a)["expiring"]
    assert [e["name"] for e in exp] == ["酸奶", "牛奶"] and exp[1]["days_left"] == 2
    lines = briefing.build(conn, a, a)["pantry_expiring"]
    assert lines[0].startswith("酸奶") and "已过期" in lines[0] and "2 天后到期" in lines[1]
    assert "pantry_expiring" not in briefing.build(conn, ids["C"], ids["C"])   # nothing expiring: no section
    conn.commit()


def test_api(client, world):
    ids, tok, _ = world
    a = ids["A"]
    h = lambda t, **kw: {"Authorization": f"Bearer {tok[t]}", **kw}
    r = client.get(f"/v1/users/{a}/grocery/pantry-auto", headers=h("B", **{"X-Body-Lang": "en"}))
    assert r.status_code == 200 and r.json()["enabled"] and r.json()["label"].endswith(": on")
    assert client.get(f"/v1/users/{a}/grocery/pantry-auto", headers=h("C")).status_code == 403
    assert client.put(f"/v1/users/{a}/grocery/pantry-auto", json={"enabled": False}, headers=h("B")).status_code == 403
    r = client.put("/v1/users/me/grocery/pantry-auto", json={"enabled": False}, headers=h("A"))
    assert r.status_code == 200 and r.json()["label"] == "记餐自动扣存货：关"
    r = client.put("/v1/users/me/grocery/pantry", json={"items": [{"name": "牛奶", "quantity": 900, "unit": "ml",
                                                                  "best_before": "2030-01-02"}]}, headers=h("A"))
    assert {p["name"]: p for p in r.json()}["牛奶"]["best_before"] == "2030-01-02"
    assert client.put("/v1/users/me/grocery/pantry", json={"items": [{"name": "牛奶", "quantity": 900, "unit": "ml",
                                                                     "best_before": "soon"}]}, headers=h("A")).status_code == 422
