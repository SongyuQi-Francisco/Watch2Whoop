"""Grocery (docs/grocery-plan.md): product library, weekly list generation (merge, pantry, package rounding, health hints),
tick -> pantry, AI tools and API authorization. Needs Postgres: separate test DB BODY_TEST_DB_NAME (default body_test_grocery)."""
import os
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.ai.chat import ALWAYS_CONFIRM
from body_engine.assistant import meals, recipes
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.grocery import params as P, store
from body_engine.nutrition import constraints

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_grocery")
WEEK = date(2026, 10, 21)   # a Wednesday; week_start = 2026-10-19


# ---------------------------------------------------------------- no database needed
def test_tools_registered_and_marked():
    for n in ("save_product", "remove_product", "generate_shopping_list", "add_shopping_items", "mark_bought",
              "remove_shopping_items", "set_pantry", "set_stores"):
        assert TOOLS_BY_NAME[n].writes and TOOLS_BY_NAME[n].scope == "nutrition", n
    for n in ("find_products", "get_shopping_list"):
        assert not TOOLS_BY_NAME[n].writes, n
    assert {"remove_product", "remove_shopping_items"} <= ALWAYS_CONFIRM
    assert not {"save_product", "mark_bought", "generate_shopping_list"} & ALWAYS_CONFIRM


def test_guess_category_and_format():
    assert store.guess_category("牛油果") == "produce"
    assert store.guess_category("鸡胸肉") == "meat"
    assert store.guess_category("希腊酸奶") == "dairy_eggs"
    assert store.guess_category("某种东西") == "other"
    assert store.fmt_qty(1500, "g") == "1.5 kg" and store.fmt_qty(12, "pcs") == "12 个" and store.fmt_qty(500, "ml") == "500 ml"


def test_sized_rounds_to_packages():
    prod = {"package_size": 1000, "package_unit": "g", "piece_g": None, "price": 11.5, "currency": "AUD"}
    s = store._sized(prod, 1200, "g")
    assert s["packages"] == 2 and s["buy_qty"] == 2000 and s["est_cost"] == 23.0
    eggs = {"package_size": 12, "package_unit": "pcs", "piece_g": 50, "price": 7, "currency": "AUD"}
    assert store._sized(eggs, 700, "g")["packages"] == 2          # 700 g = 14 eggs -> 2 cartons
    s = store._sized(None, 120, "g")
    assert s["buy_qty"] == 150 and s["packages"] is None           # rounded up to 50 g


def test_product_flags_fsa():
    row = {"nutrition": {"sat_fat_g": 6, "sugar_g": 1, "sodium_mg": 700}, "package_unit": "g", "purine_level": "high",
           "red_meat": True, "name": "牛肋条"}
    assert store.product_flags(row) == ["饱和脂肪高", "钠高", "高嘌呤", "红肉"]   # no food is singled out by name (#76)
    drink = {"nutrition": {"sugar_g": 12}, "package_unit": "ml", "purine_level": None, "red_meat": False, "name": "果汁"}
    assert store.product_flags(drink) == ["糖高"]   # drinks use the halved per-100 ml line
    assert P.HIGH_PER_100ML["sugar_g"] < P.HIGH_PER_100G["sugar_g"]


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", ""):
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
    """A (owner of everything), B (A shares nutrition with B), C (no share)."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, birth_date, height_cm) VALUES (%s, '1990-01-01', 175) "
                               "RETURNING id", (n,)).fetchone()["id"] for n in ("A", "B", "C")}
        tokens = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        tokens["A_ro"] = auth.create_token(conn, ids["A"], "ro", ["read"])
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["nutrition"]))
        a = ids["A"]
        r = recipes.create_recipe(conn, a, {
            "name": "鸡胸西兰花", "slots": ["dinner"], "servings": 2, "ingredients": [
                {"food": "鸡胸肉", "grams": 400, "kcal": 480, "protein_g": 92, "carbs_g": 0, "fat_g": 10, "store": "costco"},
                {"food": "西兰花", "grams": 300, "kcal": 100, "protein_g": 8, "carbs_g": 14, "fat_g": 1}]}, sync=False)
        rib = recipes.create_recipe(conn, a, {
            "name": "盐葱牛肋条", "slots": ["dinner"], "servings": 1, "ingredients": [
                {"food": "牛肋条", "grams": 200, "kcal": 560, "protein_g": 36, "carbs_g": 0, "fat_g": 46, "sat_fat_g": 19,
                 "red_meat_g": 200, "purine_mg": 240}]}, sync=False)
        t = meals.create_template(conn, a, "早餐", "breakfast", [
            {"food": "鸡蛋", "grams": 100, "kcal": 143, "protein_g": 12.6, "carbs_g": 0.7, "fat_g": 9.5, "sat_fat_g": 3.1,
             "purine_level": "low", "red_meat_g": 0}], False)
    return ids, tokens, {"recipe": r["id"], "rib": rib["id"], "template": t["id"]}


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


def test_product_upsert_and_validation(conn, world):
    ids, _, _ = world
    a = ids["A"]
    p = store.save_product(conn, a, {"name": "鸡胸肉", "brand": "Farm", "store": "Costco", "package_size": 1000,
                                     "package_unit": "g", "price": 11.5, "currency": "aud", "matches": ["鸡胸"],
                                     "nutrition": {"kcal": 120, "protein_g": 23, "carbs_g": 0, "fat_g": 2.5, "sodium_mg": 60}})
    assert p["created"] and p["category"] == "meat" and p["currency"] == "AUD" and p["unit_price"] == 11.5
    assert p["price_on"] is not None and p["size_label"] == "1 kg"
    # same name + brand (case-insensitive) -> update, nutrition merged, unknown values (null) do not clear
    p2 = store.save_product(conn, a, {"name": "鸡胸肉", "brand": "farm", "price": 12.0,
                                      "nutrition": {"sat_fat_g": 0.7, "kcal": None}})
    assert not p2["created"] and p2["id"] == p["id"] and p2["price"] == 12.0
    assert p2["nutrition"]["kcal"] == 120 and p2["nutrition"]["sat_fat_g"] == 0.7
    with pytest.raises(ValueError, match="差太多"):
        store.save_product(conn, a, {"name": "错", "nutrition": {"kcal": 500, "protein_g": 1, "carbs_g": 1, "fat_g": 1}})
    with pytest.raises(ValueError, match="一起给"):
        store.save_product(conn, a, {"name": "只有规格", "package_size": 500})
    with pytest.raises(ValueError, match="条码"):
        store.save_product(conn, a, {"name": "坏条码", "barcode": "12ab"})
    eggs = store.save_product(conn, a, {"name": "鸡蛋 12 个", "package_size": 12, "package_unit": "pcs", "piece_g": 50,
                                        "price": 7.0, "currency": "AUD", "matches": ["鸡蛋"], "barcode": "9300000000011"})
    assert eggs["category"] == "dairy_eggs"
    same = store.save_product(conn, a, {"name": "Eggs", "barcode": "9300000000011", "price": 7.5})   # matched by barcode
    assert same["id"] == eggs["id"] and same["name"] == "Eggs" and same["price"] == 7.5
    found = store.list_products(conn, a, q="costco")
    assert [x["name"] for x in found] == ["鸡胸肉"]
    conn.commit()


def test_generate_list_with_pantry_and_rounding(conn, world):
    ids, _, ref = world
    a = ids["A"]
    store.set_pantry(conn, a, [{"name": "西兰花", "quantity": 250, "unit": "g"}])
    out = store.generate(conn, a, WEEK, [
        {"kind": "recipe", "ref_id": ref["recipe"], "amount": 6},      # 3x the pot: 1200 g chicken, 900 g broccoli
        {"kind": "template", "ref_id": ref["template"], "amount": 7},  # 700 g eggs
        {"kind": "food", "food": "燕麦", "amount": 400, "unit": "g"},
    ])
    assert out["week_start"] == "2026-10-19" and out["week_end"] == "2026-10-25"
    items = {i["name"]: i for i in out["items"]}
    chicken = items["鸡胸肉"]
    assert chicken["packages"] == 2 and chicken["buy_qty"] == 2000 and chicken["est_cost"] == 24.0
    assert chicken["store"] == "Costco" and chicken["used_by"] == ["鸡胸西兰花"] and chicken["qty_label"] == "2 包 · 2 kg"
    assert items["西兰花"]["need_qty"] == 650 and items["西兰花"]["buy_qty"] == 650   # 900 - 250 pantry, rounded to 50
    assert items["西兰花"]["pantry_qty"] == 250 and items["西兰花"]["pantry_label"] == "家里有 250 g"   # issue #102
    assert items["鸡胸肉"]["pantry_qty"] is None and items["鸡胸肉"]["pantry_label"] is None
    egg = items["鸡蛋"]
    assert egg["unit"] == "pcs" and egg["need_qty"] == 14 and egg["packages"] == 2   # Eggs product matched via matches
    assert items["燕麦"]["category"] == "grains" and items["燕麦"]["source"] == "plan"
    assert out["progress"] == {"bought": 0, "total": 4} and out["est_total"]["AUD"] == 24.0 + 15.0
    assert not [w for w in out["warnings"] if w["code"] in ("red_meat", "short_rib")]
    # pantry covers the whole need -> not on the list
    store.set_pantry(conn, a, [{"name": "燕麦", "quantity": 1000, "unit": "g"}])
    again = store.generate(conn, a, WEEK, out["plan"])
    assert "燕麦" in again["covered_by_pantry"] and "燕麦" not in {i["name"] for i in again["items"]}
    conn.commit()


def test_health_warnings(conn, world):
    """Weekly red meat / per-food warnings only against the user's own diet constraints (issue #76)."""
    ids, _, ref = world
    plan = [{"kind": "recipe", "ref_id": ref["rib"], "amount": 2}]
    out = store.generate(conn, ids["A"], date(2026, 11, 2), plan)
    assert not {w["code"] for w in out["warnings"]} & {"red_meat", "food_limit"}   # no constraints: no limits
    conn.rollback()
    constraints.set_constraint(conn, ids["A"], "red_meat_g_per_week", 300, basis="测试")
    constraints.set_constraint(conn, ids["A"], "food_times_per_week", 1, target="牛肋条")
    out = store.generate(conn, ids["A"], date(2026, 11, 2), plan)
    codes = {w["code"] for w in out["warnings"]}
    assert codes >= {"red_meat", "food_limit"}   # 400 g red meat > 300 g; 牛肋条 twice > once a week
    assert any("你设的" in w["detail"] for w in out["warnings"] if w["code"] == "red_meat")
    conn.rollback()


def test_tick_updates_pantry_and_price(conn, world):
    ids, _, _ = world
    a = ids["A"]
    lst = store.get_list(conn, a, WEEK)
    chicken = next(i for i in lst["items"] if i["name"] == "鸡胸肉")
    out = store.set_bought(conn, a, [chicken["id"]], True, {chicken["id"]: 26.0})
    assert out["progress"]["bought"] == 1
    pantry = {p["name"]: p for p in store.list_pantry(conn, a)}
    assert pantry["鸡胸肉"]["quantity"] == 2000
    prod = store.list_products(conn, a, q="鸡胸肉")[0]
    assert prod["price"] == 13.0   # 26 paid for 2 packages
    store.set_bought(conn, a, [chicken["id"]], False)
    assert {p["name"]: p for p in store.list_pantry(conn, a)}["鸡胸肉"]["quantity"] == 0
    # regenerating keeps manual and bought lines
    store.add_items(conn, a, WEEK, [{"name": "纸巾", "qty": None, "unit": None, "product_id": None, "note": None}])
    store.add_items(conn, a, WEEK, [{"name": "纸巾", "qty": 2, "unit": "pcs", "product_id": None, "note": None}])
    lst = store.get_list(conn, a, WEEK)
    paper = next(i for i in lst["items"] if i["name"] == "纸巾")
    assert paper["buy_qty"] == 3 and paper["source"] == "manual" and paper["category"] == "other"
    regen = store.generate(conn, a, WEEK, lst["plan"])
    assert "纸巾" in {i["name"] for i in regen["items"]}
    conn.commit()


def test_tools_via_run_tool(conn, world):
    ids, _, ref = world
    a, b, c = ids["A"], ids["B"], ids["C"]
    p = run_tool(conn, a, "butler", "save_product", {"name": "希腊酸奶", "brand": "X", "package_size": 1000, "package_unit": "g",
                                                     "price": 6.5, "currency": "AUD", "nutrition": {"kcal": 70, "protein_g": 9,
                                                     "carbs_g": 4, "fat_g": 2, "sat_fat_g": 1.3, "sugar_g": 4}})
    assert p["created"] and p["category"] == "dairy_eggs"
    lst = run_tool(conn, a, "butler", "add_shopping_items", {"week": "2026-10-21", "items": [{"product_id": p["id"]}]})
    item = next(i for i in lst["items"] if i["name"] == "希腊酸奶")
    assert item["packages"] == 1 and item["est_cost"] == 6.5
    run_tool(conn, a, "butler", "mark_bought", {"item_ids": [item["id"]], "bought": True})
    got = run_tool(conn, b, "butler", "get_shopping_list", {"user": str(a), "week": "2026-10-21"})   # B has the share
    assert any(i["bought"] for i in got["items"]) and "pantry" in got
    assert run_tool(conn, b, "butler", "find_products", {"user": str(a), "q": "酸奶"})[0]["name"] == "希腊酸奶"
    with pytest.raises(PermissionError):
        run_tool(conn, c, "butler", "get_shopping_list", {"user": str(a)})
    with pytest.raises(PermissionError):   # writes only for oneself, even with a share
        run_tool(conn, b, "butler", "set_pantry", {"user": str(a), "items": [{"name": "x", "quantity": 1, "unit": "g"}]})
    with pytest.raises(LookupError):       # C cannot touch A's items by id
        run_tool(conn, c, "butler", "mark_bought", {"item_ids": [item["id"]], "bought": False})
    with pytest.raises(LookupError):
        run_tool(conn, c, "butler", "remove_product", {"product_id": p["id"]})
    out = run_tool(conn, a, "butler", "remove_shopping_items", {"item_ids": [item["id"]]})
    assert out["removed"] == ["希腊酸奶"]
    run_tool(conn, a, "butler", "remove_product", {"product_id": p["id"]})
    assert not run_tool(conn, a, "butler", "find_products", {"q": "酸奶"})
    conn.commit()


def test_api_auth_and_flow(client, world):
    ids, tok, ref = world
    a = ids["A"]
    r = client.get("/v1/users/me/grocery/list?week=2026-10-21", headers=h(tok["A"]))
    assert r.status_code == 200 and r.json()["week_start"] == "2026-10-19"
    item = r.json()["items"][0]
    assert client.get(f"/v1/users/{a}/grocery/list", headers=h(tok["B"])).status_code == 200
    assert client.get(f"/v1/users/{a}/grocery/list", headers=h(tok["C"])).status_code == 403
    assert client.get(f"/v1/users/{a}/grocery/products", headers=h(tok["C"])).status_code == 403
    assert client.get(f"/v1/users/{a}/grocery/pantry", headers=h(tok["C"])).status_code == 403
    # writes: only oneself, needs write scope
    assert client.patch(f"/v1/users/{a}/grocery/list/items/{item['id']}", json={"bought": True},
                        headers=h(tok["B"])).status_code == 403
    assert client.patch(f"/v1/users/me/grocery/list/items/{item['id']}", json={"bought": True},
                        headers=h(tok["A_ro"])).status_code == 403
    assert client.patch(f"/v1/users/me/grocery/list/items/{item['id']}", json={"bought": True},
                        headers=h(tok["C"])).status_code == 404   # not C's item
    r = client.patch(f"/v1/users/me/grocery/list/items/{item['id']}", json={"bought": True}, headers=h(tok["A"]))
    assert r.status_code == 200 and next(i for i in r.json()["items"] if i["id"] == item["id"])["bought"]
    r = client.post("/v1/users/me/grocery/products", json={"name": "橄榄油", "package_size": 750, "package_unit": "ml",
                                                         "price": 12, "currency": "AUD"}, headers=h(tok["C"]))
    assert r.status_code == 201 and r.json()["category"] == "pantry" and r.json()["unit_price"] == 16.0
    assert client.get("/v1/users/me/grocery/products", headers=h(tok["A"])).json()[0]["name"] != "橄榄油"
    assert client.delete(f"/v1/users/me/grocery/products/{r.json()['id']}", headers=h(tok["A"])).status_code == 404
    assert client.delete(f"/v1/users/me/grocery/products/{r.json()['id']}", headers=h(tok["C"])).status_code == 204
    r = client.post("/v1/users/me/grocery/list/generate", json={"week": "2026-11-09", "plan": [
        {"kind": "recipe", "ref_id": ref["recipe"], "amount": 2}]}, headers=h(tok["C"]))
    assert r.status_code == 404   # A's recipe is not C's
    r = client.put("/v1/users/me/grocery/pantry", json={"items": [{"name": "米", "quantity": 2000, "unit": "g"}]},
                   headers=h(tok["C"]))
    assert r.status_code == 200 and r.json()[0]["label"] == "2 kg"
    assert client.post("/v1/users/me/grocery/list/generate", json={"plan": []}, headers=h(tok["C"])).status_code == 422


def test_store_links_on_products_and_items(conn, world):
    ids, _, _ = world
    a, c = ids["A"], ids["C"]
    url = "https://www.woolworths.com.au/shop/productdetails/36059/woolworths-chicken-breast?utm=share"
    p = store.save_product(conn, a, {"name": "Chicken Thigh", "store": "Costco", "product_url": url, "matches": ["鸡腿肉"]})
    assert p["created"] and p["store"] == "WWS" and p["store_label"] == "WWS" and p["store_sku"] == "36059"
    assert p["product_url"] == "https://www.woolworths.com.au/shop/productdetails/36059/woolworths-chicken-breast"
    assert p["links"] == [] and p["store_not_connected"] == "WWS"   # issue #44: no supermarket connected by default
    assert store.stores_view(conn, a)["connected"] == []
    out = run_tool(conn, a, "butler", "set_stores", {"stores": ["woolworths", "coles"]})
    assert out["connected"] == ["coles", "woolworths"] and [s["label"] for s in out["stores"]] == ["Coles", "WWS"]
    p = store.list_products(conn, a, q="Chicken Thigh")[0]
    assert p["links"] == [{"store": "woolworths", "label": "WWS", "kind": "product", "url": p["product_url"]}]
    again = store.save_product(conn, a, {"name": "鸡腿肉 1kg", "product_url": url, "price": 9})   # same page -> same product
    assert not again["created"] and again["id"] == p["id"] and again["name"] == "鸡腿肉 1kg"
    with pytest.raises(ValueError, match="只支持"):
        store.save_product(conn, a, {"name": "x", "product_url": "https://shop.example.com/product/1"})
    with pytest.raises(ValueError, match="商品页"):
        store.save_product(conn, a, {"product_url": "https://www.coles.com.au/search/products?q=milk"}, p["id"])
    conn.commit()
    with pytest.raises(psycopg.errors.CheckViolation):   # the table refuses other hosts even if the code is bypassed
        conn.execute("UPDATE grocery_products SET product_url = 'https://evil.com/x', store_sku = '1' WHERE id = %s", (p["id"],))
    conn.rollback()
    cleared = store.save_product(conn, a, {"product_url": ""}, p["id"])
    assert cleared["product_url"] is None and cleared["store_sku"] is None
    assert cleared["links"][0]["kind"] == "search" and [x["store"] for x in cleared["links"]] == ["woolworths"]
    store.save_product(conn, a, {"product_url": url}, p["id"])
    # list items: the product page when the product has one, else per-store search by the item name
    lst = store.add_items(conn, a, date(2026, 11, 16), [{"product_id": p["id"]}, {"name": "Paper towel"}, {"name": "牛油果"}])
    by = {i["name"]: i for i in lst["items"]}
    assert by["鸡腿肉 1kg"]["links"][0]["kind"] == "product"
    assert [x["label"] for x in by["Paper towel"]["links"]] == ["Coles", "WWS"]
    assert "Paper%20towel" in by["Paper towel"]["links"][0]["url"]
    assert lst["connected_stores"] == ["coles", "woolworths"]
    store.set_connected_stores(conn, a, ["coles"])   # only the connected chain's links stay
    by = {i["name"]: i for i in store.get_list(conn, a, date(2026, 11, 16))["items"]}
    assert by["鸡腿肉 1kg"]["links"] == [] and [x["label"] for x in by["Paper towel"]["links"]] == ["Coles"]
    with pytest.raises(ValueError):
        store.set_connected_stores(conn, a, ["aldi"])
    store.set_connected_stores(conn, a, ["coles", "woolworths"])
    # tool output leaves the page links out (tokens); product_url stays
    found = run_tool(conn, a, "butler", "find_products", {"q": "鸡腿"})
    assert found[0]["product_url"] and "links" not in found[0]
    got = run_tool(conn, a, "butler", "get_shopping_list", {"week": "2026-11-16"})
    assert got["items"] and all("links" not in i for i in got["items"])
    saved = run_tool(conn, a, "butler", "save_product", {"product_id": p["id"], "nutrition_source": "Open Food Facts (ODbL)"})
    assert saved["nutrition_source"] == "Open Food Facts (ODbL)" and "links" not in saved
    with pytest.raises(LookupError):   # C cannot set a link on A's product
        run_tool(conn, c, "butler", "save_product", {"product_id": p["id"], "product_url": url})
    conn.commit()


def test_api_product_url(client, world):
    _, tok, _ = world
    r = client.post("/v1/users/me/grocery/products", json={"name": "Milk 3L", "product_url":
                    "https://www.coles.com.au/product/coles-full-cream-milk-3l-8150288"}, headers=h(tok["C"]))
    assert r.status_code == 201 and r.json()["store"] == "Coles" and r.json()["links"] == []   # C has no store connected
    assert client.get("/v1/users/me/grocery/stores", headers=h(tok["C"])).json()["connected"] == []
    assert client.put("/v1/users/me/grocery/stores", json={"connected": ["coles"]}, headers=h(tok["A_ro"])).status_code == 403
    assert client.put("/v1/users/me/grocery/stores", json={"connected": ["aldi"]}, headers=h(tok["C"])).status_code == 422
    r = client.put("/v1/users/me/grocery/stores", json={"connected": ["coles"]}, headers=h(tok["C"]))
    assert r.status_code == 200 and r.json()["connected"] == ["coles"]
    prods = client.get("/v1/users/me/grocery/products", headers=h(tok["C"])).json()
    assert next(x for x in prods if x["name"] == "Milk 3L")["links"][0]["kind"] == "product"
    bad = client.post("/v1/users/me/grocery/products", json={"name": "Bad", "product_url": "https://example.com/p/1"},
                      headers=h(tok["C"]))
    assert bad.status_code in (400, 422)
    a_id = world[0]["A"]
    assert client.get(f"/v1/users/{a_id}/grocery/stores", headers=h(tok["C"])).status_code == 403
    assert client.get(f"/v1/users/{a_id}/grocery/stores", headers=h(tok["B"])).status_code == 200   # nutrition share
    assert client.put(f"/v1/users/{a_id}/grocery/stores", json={"connected": []}, headers=h(tok["B"])).status_code == 403
    lst = client.get("/v1/users/me/grocery/list?week=2026-10-21", headers=h(tok["A"])).json()
    assert all("links" in i for i in lst["items"])


def test_pantry_view_api(client, world):
    """Issue #102: the Today page pantry: category, days since the last change, 「用完了」 + undo through PUT …/pantry, read-only
    for viewers with a share, nothing for others; the list explains what the pantry covered (pantry_label, both languages)."""
    ids, tok, ref = world
    a = ids["A"]
    r = client.put("/v1/users/me/grocery/pantry", json={"items": [{"name": "鸡蛋", "quantity": 6, "unit": "pcs"},
                                                                   {"name": "大米", "quantity": 1500, "unit": "g"}]},
                   headers=h(tok["A"]))
    assert r.status_code == 200
    got = {p["name"]: p for p in r.json()}
    assert got["鸡蛋"]["category"] == "dairy_eggs" and got["鸡蛋"]["category_label"] == "蛋奶" and got["鸡蛋"]["days_ago"] == 0
    assert got["大米"]["category"] == "grains" and got["鸡蛋"]["label"] == "6 个"
    cats = [p["category"] for p in r.json()]
    assert cats.index("dairy_eggs") < cats.index("grains")   # sorted by category (UI order), then name
    en = client.get("/v1/users/me/grocery/pantry", headers={**h(tok["A"]), "X-Body-Lang": "en"}).json()
    assert {p["name"]: p for p in en}["鸡蛋"]["label"] == "6 pcs" and {p["name"]: p for p in en}["鸡蛋"]["category_label"] == "Dairy & eggs"
    # viewers: B (nutrition share) reads, cannot write; C neither
    assert {p["name"] for p in client.get(f"/v1/users/{a}/grocery/pantry", headers=h(tok["B"])).json()} >= {"鸡蛋", "大米"}
    assert client.put(f"/v1/users/{a}/grocery/pantry", json={"items": [{"name": "鸡蛋", "quantity": 0, "unit": "pcs"}]},
                      headers=h(tok["B"])).status_code == 403
    assert client.get(f"/v1/users/{a}/grocery/pantry", headers=h(tok["C"])).status_code == 403
    # 用完了 = quantity 0 (row removed); undo puts the old quantity back
    r = client.put("/v1/users/me/grocery/pantry", json={"items": [{"name": "鸡蛋", "quantity": 0, "unit": "pcs"}]}, headers=h(tok["A"]))
    assert "鸡蛋" not in {p["name"] for p in r.json()}
    r = client.put("/v1/users/me/grocery/pantry", json={"items": [{"name": "鸡蛋", "quantity": 6, "unit": "pcs", "product_id": None}]},
                   headers=h(tok["A"]))
    assert {p["name"]: p for p in r.json()}["鸡蛋"]["quantity"] == 6
    # C's pantry is separate: A's eggs are not C's
    assert "鸡蛋" not in {p["name"] for p in client.get("/v1/users/me/grocery/pantry", headers=h(tok["C"])).json()}
    # the list line says how much the pantry covered, in the page language
    r = client.post("/v1/users/me/grocery/list/generate", json={"week": "2026-11-16", "plan": [
        {"kind": "food", "food": "大米", "amount": 2000, "unit": "g"}]}, headers={**h(tok["A"]), "X-Body-Lang": "en"})
    rice = next(i for i in r.json()["items"] if i["name"] == "大米")
    assert rice["pantry_qty"] == 1500 and rice["pantry_label"] == "1.5 kg at home" and rice["need_qty"] == 500
    lst = client.get(f"/v1/users/{a}/grocery/list?week=2026-11-16", headers=h(tok["B"])).json()
    assert next(i for i in lst["items"] if i["name"] == "大米")["pantry_label"] == "家里有 1.5 kg"


def test_pantry_view_page_source():
    """grocery.js: the pantry section, 「用完了」 only on the own page, undo in the toast, the 「家里有」 chip on list lines."""
    from pathlib import Path
    js = (Path(__file__).parents[1] / "src/body_engine/web/static/grocery.js").read_text()
    assert 'id="g-pantry"' in js and "/pantry`" in js
    row = js[js.index("function pantryRow"):js.index("function pantryHtml")]
    assert "info.self ?" in row and "data-gpu" in row   # viewers see the pantry read-only
    used = js[js.index("async function usedUp"):js.index("function storesHtml")]
    assert "quantity: 0" in used and 'tr("撤销")' in used and "quantity: p.quantity" in used
    assert "i.pantry_label" in js
