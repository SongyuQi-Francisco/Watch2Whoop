"""Usual items / 常买 (issue #103, docs/grocery-plan.md §3): suggestions from the user's own purchases (never marked on their own),
due usual items added to the weekly list (source staple), merged with the plan, left off when the pantry covers them or when not
due yet; per-user isolation (B with a nutrition share reads only). Needs Postgres: separate test DB (default
body_test_grocery_staples)."""
import os
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.grocery import params as P, prices, staples, store

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_STAPLES", "body_test_grocery_staples")


def test_typical_gap():
    from datetime import date
    d = date(2026, 10, 1)
    assert staples.typical_gap([d, d + timedelta(days=6), d + timedelta(days=13), d + timedelta(days=21)]) == 7
    assert staples.typical_gap([d]) is None and staples.typical_gap([]) is None
    assert staples.typical_gap([d, d + timedelta(days=400)]) == P.STAPLE_MAX_DAYS


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


def _buy(conn, uid, name, pid, days_ago):
    day = store.today(conn, uid) - timedelta(days=days_ago)
    prices.add_purchase(conn, uid, name=name, store=None, store_product_id=None, bought_on=day, packages=1, paid_total=None,
                        on_special=False, was_price=None, source="manual", product_id=pid)


@pytest.fixture(scope="module")
def world(dsn):
    """A (owner), B (A shares nutrition with B), C (no share, no usual items)."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, birth_date, height_cm) VALUES (%s, '1990-01-01', 175) "
                               "RETURNING id", (n,)).fetchone()["id"] for n in ("A", "B", "C")}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["nutrition"]))
        a, b = ids["A"], ids["B"]
        pr = {}
        for d in (dict(name="鸡蛋", package_size=12, package_unit="pcs", piece_g=50, price=7.5, currency="AUD", matches=["egg"]),
                  dict(name="牛奶", package_size=2000, package_unit="ml", price=3.6, currency="AUD"),
                  dict(name="香蕉", package_size=1000, package_unit="g", price=3.9, currency="AUD"),
                  dict(name="面包", package_size=650, package_unit="g", price=4.0, currency="AUD")):
            pr[d["name"]] = store.save_product(conn, a, d)["id"]
        for days in (21, 14, 7):
            _buy(conn, a, "鸡蛋", pr["鸡蛋"], days)
        for days in (17, 3):
            _buy(conn, a, "牛奶", pr["牛奶"], days)
        bp = store.save_product(conn, b, dict(name="面包", package_size=650, package_unit="g"))["id"]
        for days in (20, 13, 6, 1):   # B's purchases never count for A
            _buy(conn, b, "面包", bp, days)
    return ids, tok, pr


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def h(token, **extra):
    return {"Authorization": f"Bearer {token}", **extra}


def test_suggestions_only_own_history_and_never_marked(conn, world):
    ids, _, pr = world
    a, b = ids["A"], ids["B"]
    sug = store.staple_suggestions(conn, a)
    assert [s["name"] for s in sug] == ["鸡蛋"]   # 3 times in 8 weeks; milk twice; bread only by B
    assert sug[0]["times"] == 3 and sug[0]["every_days"] == 7 and sug[0]["product_id"] == pr["鸡蛋"]
    assert not any(p["staple"] for p in store.list_products(conn, a))   # suggesting marks nothing
    assert [s["name"] for s in store.staple_suggestions(conn, b)] == ["面包"]
    assert "staple_suggestions" in prices.shopping_context(conn, a)


def test_due_staples_on_the_list(conn, world):
    ids, _, pr = world
    a = ids["A"]
    eggs = store.set_staple(conn, a, pr["鸡蛋"])
    assert eggs["staple"] and eggs["staple_info"]["every_days"] == 7 and eggs["staple_info"]["unit"] == "pack"
    today = store.today(conn, a)
    assert eggs["staple_info"]["next_due"] == today.isoformat() and eggs["staple_info"]["due"]   # last bought 7 days ago
    milk = store.set_staple(conn, a, pr["牛奶"], {"every_days": 14, "qty": 1, "unit": "pack"})
    assert milk["staple_info"]["next_due"] == (today + timedelta(days=11)).isoformat() and not milk["staple_info"]["due"]
    store.set_staple(conn, a, pr["香蕉"], {"qty": 1000, "unit": "g"})   # never bought -> due now
    store.set_pantry(conn, a, [{"name": "香蕉", "quantity": 1500, "unit": "g"}])   # ... but the pantry covers it
    assert [s["name"] for s in store.staple_suggestions(conn, a)] == []           # marked ones are not suggested again
    # only usual items, no plan
    out = store.generate(conn, a, None, [])
    by = {i["name"]: i for i in out["items"]}
    assert set(by) == {"鸡蛋"} and by["鸡蛋"]["source"] == "staple" and by["鸡蛋"]["staple"] and by["鸡蛋"]["packages"] == 1
    assert "香蕉" in out["covered_by_pantry"]
    # the pantry runs out -> bananas come back; regenerating replaces staple lines, no duplicates
    store.set_pantry(conn, a, [{"name": "香蕉", "quantity": 0, "unit": "g"}])
    out = store.generate(conn, a, None, [])
    assert sorted(i["name"] for i in out["items"]) == ["香蕉", "鸡蛋"]
    # merged with the plan: one egg line, the larger amount, source plan, still tagged staple
    out = store.generate(conn, a, None, [{"kind": "food", "food": "egg", "amount": 4, "unit": "pcs"},
                                         {"kind": "food", "food": "面包", "amount": 600, "unit": "g"}])
    eggs = [i for i in out["items"] if i["product_id"] == pr["鸡蛋"]]
    assert len(eggs) == 1 and eggs[0]["source"] == "plan" and eggs[0]["staple"] and eggs[0]["need_qty"] == 12
    assert {i["name"]: i["source"] for i in out["items"]}["面包"] == "plan"
    assert not {i["name"]: i for i in out["items"]}["面包"]["staple"]
    out = store.generate(conn, a, None, [{"kind": "food", "food": "egg", "amount": 30, "unit": "pcs"}])
    assert next(i for i in out["items"] if i["product_id"] == pr["鸡蛋"])["need_qty"] == 30   # the plan needs more
    # manual and bought lines stay
    store.add_items(conn, a, None, [{"name": "纸巾", "qty": 1, "unit": "pcs", "product_id": None, "note": None}])
    out = store.generate(conn, a, None, [])
    assert {i["name"]: i["source"] for i in out["items"]} == {"鸡蛋": "staple", "香蕉": "staple", "纸巾": "manual"}
    # removing the usual item: no longer added
    store.remove_staple(conn, a, pr["香蕉"])
    assert "香蕉" not in {i["name"] for i in store.generate(conn, a, None, [])["items"]}
    conn.commit()


def test_users_without_staples_unchanged(conn, world):
    ids, _, _ = world
    c = ids["C"]
    with pytest.raises(ValueError):
        store.generate(conn, c, None, [])   # empty plan still an error without usual items
    out = store.generate(conn, c, None, [{"kind": "food", "food": "鸡蛋", "amount": 6, "unit": "pcs"}])
    assert [(i["name"], i["source"], i["staple"]) for i in out["items"]] == [("鸡蛋", "plan", False)]
    conn.rollback()


def test_tools(conn, world):
    ids, _, pr = world
    a, b = ids["A"], ids["B"]
    out = run_tool(conn, a, "butler", "set_staple", {"product_id": pr["面包"], "every_days": 10, "qty": None, "unit": None})
    assert out["staple"] and out["staple_info"]["every_days"] == 10
    with pytest.raises(ValueError):
        run_tool(conn, a, "butler", "set_staple", {"product_id": pr["面包"], "every_days": 0, "qty": None, "unit": None})
    with pytest.raises(LookupError):   # B cannot mark A's product (writes only for oneself; ids are per user)
        run_tool(conn, b, "butler", "set_staple", {"product_id": pr["面包"], "every_days": None, "qty": None, "unit": None})
    with pytest.raises(LookupError):
        run_tool(conn, b, "butler", "remove_staple", {"product_id": pr["面包"]})
    assert not run_tool(conn, a, "butler", "remove_staple", {"product_id": pr["面包"]})["staple"]
    log = conn.execute("SELECT tool FROM ai_write_log WHERE user_id = %s AND tool IN ('set_staple', 'remove_staple')", (a,)).fetchall()
    assert len(log) == 2
    assert "staple_suggestions" in run_tool(conn, a, "butler", "get_shopping_context", {"user": str(a), "week": None})
    conn.commit()


def test_api_isolation(client, world):
    ids, tok, pr = world
    a = ids["A"]
    prods = client.get(f"/v1/users/{a}/grocery/products", headers=h(tok["B"])).json()   # B reads with the share
    eggs = next(p for p in prods if p["name"] == "鸡蛋")
    assert eggs["staple"] and eggs["staple_info"]["next_due"]
    assert client.get(f"/v1/users/{a}/grocery/products", headers=h(tok["C"])).status_code == 403
    assert client.put(f"/v1/users/{a}/grocery/products/{pr['鸡蛋']}/staple", json={}, headers=h(tok["B"])).status_code == 403
    assert client.delete(f"/v1/users/{a}/grocery/products/{pr['鸡蛋']}/staple", headers=h(tok["B"])).status_code == 403
    assert client.put(f"/v1/users/me/grocery/products/{pr['鸡蛋']}/staple", json={}, headers=h(tok["B"])).status_code == 404
    assert client.delete(f"/v1/users/me/grocery/products/{pr['鸡蛋']}/staple", headers=h(tok["C"])).status_code == 404
    r = client.put(f"/v1/users/me/grocery/products/{pr['面包']}/staple", json={"every_days": 0}, headers=h(tok["A"]))
    assert r.status_code == 422
    r = client.put(f"/v1/users/me/grocery/products/{pr['面包']}/staple", json={"every_days": 14, "qty": 2, "unit": "pack"},
                   headers=h(tok["A"]))
    assert r.status_code == 200 and r.json()["staple_info"]["qty"] == 2
    assert client.delete(f"/v1/users/me/grocery/products/{pr['面包']}/staple", headers=h(tok["A"])).json()["staple"] is False
    r = client.put(f"/v1/users/me/grocery/products/{pr['面包']}/staple", json={"unit": "box"}, headers=h(tok["A"], **{"X-Body-Lang": "en"}))
    assert r.status_code == 422
    r = client.post("/v1/users/me/grocery/list/generate", json={"plan": []}, headers=h(tok["A"]))
    assert r.status_code == 200 and any(i["source"] == "staple" for i in r.json()["items"])
    assert client.post("/v1/users/me/grocery/list/generate", json={"plan": []}, headers=h(tok["C"])).status_code == 422


def test_page_source():
    js = (Path(__file__).parents[1] / "src/body_engine/web/static/grocery.js").read_text()
    assert 'tr("常买")' in js and 'tr("下次该买 {d}"' in js
