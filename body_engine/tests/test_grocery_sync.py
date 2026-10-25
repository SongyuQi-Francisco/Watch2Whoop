"""grocery-sync (docs/grocery-store-data-plan.md §8): robots.txt matching, the polite client, the Coles parser (synthetic fixtures),
WWS blocked, run / status / purge, and the read side (offers on the list, weekly half price, meal suggestions).
Never reaches the live sites: every request goes through a fake opener. DB tests need Postgres (BODY_TEST_DB_NAME_SYNC,
default body_test_grocery_sync)."""
import io
import os
import urllib.error
from datetime import date
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant import recipes
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.grocery import store
from body_engine.grocery.sync import coles, common as C, http, offers, params as P, runner, wws
from body_engine.grocery.sync.robots import Robots

FIX = Path(__file__).parent / "fixtures" / "grocery_sync"
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_SYNC", "body_test_grocery_sync")
DAY = date(2026, 10, 21)   # a Wednesday: the specials week starts today


def fx(name: str) -> str:
    return (FIX / name).read_text()


# ---------------------------------------------------------------- fake network
class Resp:
    def __init__(self, status: int, body: str):
        self.status, self._body, self.headers = status, body.encode(), {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    """url -> body (200) or int status (raised as HTTPError like urllib does); records every request."""

    def __init__(self, routes: dict):
        self.routes, self.seen = routes, []

    def __call__(self, req, timeout):
        url = req.full_url
        self.seen.append((url, req.get_header("User-agent")))
        r = self.routes.get(url, 404)
        if isinstance(r, list):   # a sequence of answers for retries
            r = r.pop(0)
        if isinstance(r, int):
            raise urllib.error.HTTPError(url, r, "x", {}, io.BytesIO(b""))
        return Resp(200, r)


def coles_routes(**extra) -> dict:
    b = P.COLES_BASE
    return {f"{b}/robots.txt": fx("coles_robots.txt"),
            f"{b}/browse/meat-seafood": fx("coles_meat_p1.html"), f"{b}/browse/meat-seafood?page=2": fx("coles_meat_p2.html"),
            f"{b}/browse/fruit-vegetables": fx("coles_fruit_p1.html"), **extra}


def fetcher(routes, base=P.COLES_BASE, **kw):
    sleeps = []
    f = http.Fetcher(base, "ops@example.org", delay_s=0, opener=Opener(routes), sleep=sleeps.append, **kw)
    f.sleeps = sleeps
    return f


# ---------------------------------------------------------------- no database
def test_robots_rfc9309_longest_match():
    r = Robots(fx("coles_robots.txt"), "Watch2Whoop-grocery-sync/0.1")
    b = P.COLES_BASE
    assert r.allowed(f"{b}/browse/meat-seafood?page=2") and r.allowed(f"{b}/product/testfarm-chicken-9000001")
    assert r.allowed(f"{b}/on-special?filter_Special=halfprice")              # the explicit Allow beats *?filter_Special=*
    assert r.allowed(f"{b}/on-special?filter_Special=halfprice&page=2")
    assert not r.allowed(f"{b}/on-special?filter_Special=multibuy")
    assert not r.allowed(f"{b}/search/products?q=milk") and not r.allowed(f"{b}/product/9000001")   # /product/*$ without a slug
    assert not r.allowed(f"{b}/browse/pantry?utm_source=x") and not r.allowed(f"{b}/a/b/c/on-special")
    assert Robots("User-agent: *\nDisallow: /\n", "x").allowed(f"{b}/browse/x") is False
    assert Robots("User-agent: watch2whoop\nDisallow: /x\n\nUser-agent: *\nDisallow: /\n", "Watch2Whoop-grocery-sync/1").allowed(f"{b}/y")
    assert Robots("", "x").allowed(f"{b}/anything")


def test_common_parsers():
    assert C.parse_size("500g") == (500, "g") and C.parse_size("1.5kg") == (1500, "g") and C.parse_size("2L") == (2000, "ml")
    assert C.parse_size("6 x 330mL") == (1980, "ml") and C.parse_size("12 Pack") == (12, "pcs")
    assert C.parse_size("approx. 1.6kg each") == (1600, "g") and C.parse_size("each") == (1, "pcs") and C.parse_size("bunch") == (None, None)
    assert C.parse_comparable("$19.60/ 1kg") == (19.6, "kg") and C.parse_comparable("$3.85/ 100mL") == (38.5, "L")
    assert C.parse_comparable("$1.59/ 100g") == (15.9, "kg") and C.parse_comparable("$2.63/ 1ea") == (2.63, "ea")
    assert C.parse_comparable(None) == (None, None)
    assert C.classify_special(6.5, 13, False, False, True) == "half_price"
    assert C.classify_special(6.5, 13, True, False, True) == "half_price"
    assert C.classify_special(9, None, False, True, True) == "multibuy"
    assert C.classify_special(8, 9, False, False, True) == "price_drop"
    assert C.classify_special(9.8, None, False, False, True) == "special" and C.classify_special(9, None, False, False, False) is None


def test_coles_parse_page_fixture():
    out = coles.parse_page(fx("coles_meat_p1.html"))
    assert out["total"] == 5 and out["page_size"] == 3
    skus = [p["sku"] for p in out["products"]]
    assert skus == ["9000001", "9000002", "9000003"]   # ad tile skipped, featured duplicate once
    half = out["products"][0]
    assert half["special_type"] == "half_price" and half["price"] == 6.5 and half["was_price"] == 13 and half["save_amount"] == 6.5
    assert half["name"] == "Testfarm Free Range Chicken Breast Fillets" and half["package_size"] == 500 and half["package_unit"] == "g"
    assert half["unit_price"] == 13 and half["unit_price_unit"] == "kg"
    assert half["url"] == "https://www.coles.com.au/product/testfarm-free-range-chicken-breast-fillets-9000001"
    assert set(half) >= {"store", "sku", "name", "url"} and not {"description", "imageUris", "image"} & set(half)  # no images / text
    assert out["products"][1]["special_type"] == "price_drop" and out["products"][2]["special_type"] is None
    mb = coles.parse_page(fx("coles_meat_p2.html"))["products"][0]
    assert mb["special_type"] == "multibuy" and mb["special_label"] == "Buy 2 for $15" and mb["multibuy_qty"] == 2
    assert mb["multibuy_unit_price"] == 7.5 and mb["was_price"] is None
    with pytest.raises(http.Unclear, match="Pardon Our Interruption"):   # the title goes into the run's error for a person to judge
        coles.parse_page(fx("coles_challenge.html"))
    with pytest.raises(http.Unclear):
        coles.parse_page('<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>')


def test_fetcher_politeness():
    f = fetcher(coles_routes())
    pages = list(coles.crawl(f, categories=("meat-seafood",)))
    assert [(c, p, len(x)) for c, p, x in pages] == [("meat-seafood", 1, 3), ("meat-seafood", 2, 2)]
    urls = [u for u, _ in f.opener.seen]
    assert urls[0].endswith("/robots.txt") and len(urls) == 3 and f.requests == 3
    assert all(ua.startswith("Watch2Whoop-grocery-sync/") and "ops@example.org" in ua for _, ua in f.opener.seen)
    # a URL robots.txt disallows is never requested
    f.robots = Robots(fx("coles_robots.txt"), f.ua)
    with pytest.raises(http.Blocked, match="robots"):
        f.get(f"{P.COLES_BASE}/search/products?q=milk")
    assert len(f.opener.seen) == 3
    # 503 is retried with backoff; 403 / 429 stop at once
    f = fetcher({f"{P.COLES_BASE}/robots.txt": [503, fx("coles_robots.txt")]})
    f.load_robots()
    assert f.sleeps == [P.BACKOFF_S[0]] and f.requests == 2
    f = fetcher(coles_routes(**{f"{P.COLES_BASE}/browse/meat-seafood": 429}))
    with pytest.raises(http.Blocked, match="429"):
        list(coles.crawl(f, categories=("meat-seafood",)))
    assert f.requests == 2
    f = fetcher(coles_routes(**{f"{P.COLES_BASE}/browse/meat-seafood": fx("coles_challenge.html")}))
    with pytest.raises(http.Unclear):
        list(coles.crawl(f, categories=("meat-seafood",)))
    f = fetcher(coles_routes(), max_requests=2)
    with pytest.raises(http.Blocked, match="上限"):
        list(coles.crawl(f, categories=("meat-seafood",)))


def test_wws_blocked_stops_at_robots():
    f = fetcher({f"{P.WWS_BASE}/robots.txt": 403}, base=P.WWS_BASE)
    with pytest.raises(http.Blocked, match="403"):
        list(wws.crawl(f))
    assert f.requests == 1


def test_switch_and_terms(monkeypatch):
    monkeypatch.delenv(P.ENV_STORES, raising=False)
    assert P.enabled_stores() == []
    monkeypatch.setenv(P.ENV_STORES, " Coles , aldi")
    assert P.enabled_stores() == ["coles"]
    monkeypatch.setenv(P.ENV_DELAY, "0.1")
    assert P.delay_s() == P.DELAY_MIN_S
    assert offers.terms_for("鸡胸肉") == [["chicken", "breast"]]
    assert offers.terms_for("鸡蛋 12 个", {"name": "Free Range Eggs", "matches": ["鸡蛋"]})[0] == ["free", "range", "egg"]
    assert offers.terms_for("Tomatoes")[0] == ["tomato"] and offers.terms_for("某种东西") == []
    assert runner.specials_week(date(2026, 10, 27)) == date(2026, 10, 21) and runner.specials_week(DAY) == DAY
    for n in ("get_weekly_specials",):
        assert not TOOLS_BY_NAME[n].writes and TOOLS_BY_NAME[n].scope == "nutrition"


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", "body_test_accept", ""):
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


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture()
def on(monkeypatch):
    monkeypatch.setenv(P.ENV_STORES, "coles,woolworths")
    monkeypatch.setenv(P.ENV_CONTACT, "ops@example.org")
    monkeypatch.setattr(runner, "sydney_today", lambda: DAY)
    monkeypatch.setattr(offers, "sydney_today", lambda: DAY)
    monkeypatch.setattr(P, "COLES_CATEGORIES", ("meat-seafood", "fruit-vegetables"))   # the categories the fixtures have


@pytest.fixture(scope="module")
def users(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, birth_date, height_cm) VALUES (%s, '1990-01-01', 175) "
                               "RETURNING id", (n,)).fetchone()["id"] for n in ("A", "B")}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
    return ids, tok


def _run(conn, routes=None, **kw):
    f = fetcher(routes or coles_routes())
    return runner.run_store(conn, "coles", fetcher=f, day=DAY, log=lambda *_: None, **kw), f


def test_run_disabled_without_switch(conn, monkeypatch):
    monkeypatch.delenv(P.ENV_STORES, raising=False)
    assert runner.run(conn)[0]["status"] == "disabled"
    monkeypatch.setenv(P.ENV_STORES, "coles")
    monkeypatch.delenv(P.ENV_CONTACT, raising=False)
    out = runner.run_store(conn, "coles", fetcher=fetcher({}), day=DAY)
    assert out["status"] == "disabled" and "CONTACT" in out["note"]


def test_run_status_rerun_purge(conn, on):
    out, f = _run(conn)
    assert out["status"] == "ok" and out["products"] == 8 and out["pages"] == 3 and out["requests"] == 4
    assert out["half_price"] == 5 and out["specials"] == 7 and out["week_start"] == "2026-10-21"
    rows = {r["sku"]: r for r in conn.execute("SELECT * FROM store_products")}
    assert rows["9000001"]["category"] == "meat-seafood" and rows["9000006"]["category"] == "fruit-vegetables"
    assert conn.execute("SELECT count(*) AS n FROM weekly_specials WHERE special_type = 'half_price'").fetchone()["n"] == 5
    # the timer firing again the same week does nothing
    again, f2 = _run(conn)
    assert again["status"] == "skipped" and f2.requests == 0
    # --force: idempotent (one observation per product per day); a product gone from the site goes inactive
    routes = coles_routes(**{f"{P.COLES_BASE}/browse/fruit-vegetables": fx("coles_fruit_p1.html").replace("9000008", "9000009")})
    forced, _ = _run(conn, routes, force=True)
    assert forced["status"] == "ok"
    assert conn.execute("SELECT count(*) AS n FROM price_observations WHERE observed_on = %s", (DAY,)).fetchone()["n"] == 9
    assert not conn.execute("SELECT active FROM store_products WHERE sku = '9000008'").fetchone()["active"]
    assert conn.execute("SELECT count(*) AS n FROM weekly_specials w JOIN store_products s ON s.id = w.store_product_id "
                        "WHERE s.sku = '9000008'").fetchone()["n"] == 0
    # blocked: recorded, nothing changes, no retries
    blocked, fb = _run(conn, {f"{P.COLES_BASE}/robots.txt": 403}, force=True)
    assert blocked["status"] == "blocked" and fb.requests == 1
    st = runner.status(conn)
    c = next(s for s in st["stores"] if s["store"] == "coles")
    assert c["last_run"]["status"] == "blocked" and c["products"] == 8 and c["half_price"] == 5 and c["last_ok"] and not c["stale"]
    conn.commit()


def test_offers_on_list_and_specials(conn, users, on):
    ids, tok = users
    a, b = ids["A"], ids["B"]
    _run(conn, force=True)   # the default pages again: 9000008 back, 9000009 gone
    chicken = store.save_product(conn, a, {"name": "鸡胸肉", "matches": ["chicken breast"], "package_size": 1000, "package_unit": "g",
                                          "price": 11.0, "currency": "AUD"})
    store.add_items(conn, a, DAY, [{"name": "鸡胸肉", "qty": 1000, "unit": "g"}, {"name": "西兰花", "qty": 2, "unit": "pcs"},
                                  {"name": "希腊酸奶", "qty": 1000, "unit": "g"}, {"name": "纸巾", "qty": 1, "unit": "pcs"}])
    lst = store.get_list(conn, a, DAY)
    assert "half_price" not in lst and all("offer" not in i for i in lst["items"])   # no store connected yet (issue #44)
    store.set_connected_stores(conn, a, ["coles"])
    lst = store.get_list(conn, a, DAY)
    by = {i["name"]: i for i in lst["items"]}
    o = by["鸡胸肉"]["offer"]
    assert o["sku"] == "9000001" and o["half_price"] and o["packages"] == 2 and o["save_total"] == 13.0   # plain fillets, not schnitzel
    assert by["鸡胸肉"]["est_cost"] == 11.0 and "est_from" not in by["鸡胸肉"]     # the user's own price stays the estimate
    assert by["希腊酸奶"]["offer"]["sku"] == "9000007" and by["希腊酸奶"]["est_cost"] == 4.0 and by["希腊酸奶"]["est_from"] == "coles"
    assert by["西兰花"]["offer"]["half_price"] and "offer" not in by["纸巾"]
    assert lst["half_price"]["items"] == 3 and lst["half_price"]["save"]["AUD"] == 13.0 + 4.0 + 2 * 1.5   # broccoli "each" x 2
    # the user's saved store page wins over fuzzy matching
    store.save_product(conn, a, {"product_url": "https://www.coles.com.au/product/testfarm-chicken-breast-fillets-large-pack-9000003"},
                       chicken["id"])
    lst = store.get_list(conn, a, DAY)
    assert next(i for i in lst["items"] if i["name"] == "鸡胸肉")["offer"]["sku"] == "9000003"
    prods = store.list_products(conn, a, q="鸡胸肉")
    assert prods[0]["offer"]["sku"] == "9000003"
    # weekly half price: health-filtered (no ice cream), things the user buys first, high purine flagged
    sp = offers.weekly_specials(conn, a)
    names = [s["name"] for s in sp["items"]]
    assert not any("Ice Cream" in n for n in names) and sp["excluded"] >= 1
    assert sp["items"][0]["for"] and sp["valid_to"] == "2026-10-27"
    sardine = next(s for s in sp["items"] if "Sardines" in s["name"])
    assert "高嘌呤" in sardine["flags"] and names.index(sardine["name"]) == len(names) - 1
    # AI tools: slim offers, specials tool
    got = run_tool(conn, a, "butler", "get_shopping_list", {"week": DAY.isoformat()})
    assert "url" not in next(i for i in got["items"] if i["name"] == "希腊酸奶")["offer"]
    tool = run_tool(conn, a, "butler", "get_weekly_specials", {"q": "yoghurt"})
    assert [s["name"] for s in tool["items"]] == ["Testdairy Greek Style Yoghurt"]
    # the component off -> back to the user's own data only
    os.environ.pop(P.ENV_STORES)
    assert "half_price" not in store.get_list(conn, a, DAY) and offers.weekly_specials(conn, a)["items"] == []
    os.environ[P.ENV_STORES] = "coles"
    # B has not connected Coles: nothing
    assert offers.weekly_specials(conn, b)["items"] == [] and offers.visible_stores(conn, b) == []
    conn.commit()


def test_generate_and_meal_suggestion_bonus(conn, users, on, monkeypatch):
    ids, _ = users
    a = ids["A"]
    out = store.generate(conn, a, DAY, [{"kind": "food", "food": "鸡胸肉", "amount": 600, "unit": "g"}])
    assert "specials" in out and out["specials"] and out["half_price"]["items"] >= 1
    # meal suggestions: a recipe whose ingredient is half price this week gets the bonus and the reason
    r = recipes.create_recipe(conn, a, {"name": "鸡胸西兰花", "slots": ["dinner"], "servings": 1, "ingredients": [
        {"food": "鸡胸肉", "grams": 200, "kcal": 240, "protein_g": 46, "carbs_g": 0, "fat_g": 5},
        {"food": "西兰花", "grams": 150, "kcal": 50, "protein_g": 4, "carbs_g": 7, "fat_g": 1}]}, sync=False)
    monkeypatch.setattr(recipes.sparky_client, "goal_for_date",
                        lambda c, d: {"name": "x", "calories": 2400, "protein_g": 160, "carbs_g": 250, "fat_g": 60})
    monkeypatch.setattr(recipes.sparky_client, "food_entries", lambda c, s, e: [])
    monkeypatch.setattr(recipes, "red_meat_week", lambda c, u, d: 0)
    res = recipes.suggest(conn, a, DAY, "dinner", client=object())
    s = next(x for x in res["suggestions"] if x["id"] == r["id"])
    assert {h["food"] for h in s["half_price"]} == {"鸡胸肉", "西兰花"} and "本周半价" in s["reason"]
    os.environ.pop(P.ENV_STORES)
    plain = next(x for x in recipes.suggest(conn, a, DAY, "dinner", client=object())["suggestions"] if x["id"] == r["id"])
    assert "half_price" not in plain and plain["score"] > s["score"]
    os.environ[P.ENV_STORES] = "coles"
    conn.commit()


def test_api_specials_and_purge(dsn, conn, users, on):
    ids, tok = users
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=2, open=True)
    try:
        c = TestClient(api.app)
        h = {"Authorization": f"Bearer {tok['A']}"}
        r = c.get("/v1/users/me/grocery/specials?limit=2", headers=h)
        assert r.status_code == 200 and len(r.json()["items"]) == 2
        assert c.get(f"/v1/users/{ids['A']}/grocery/specials", headers={"Authorization": f"Bearer {tok['B']}"}).status_code == 403
        assert c.get(f"/v1/users/me/grocery/list?week={DAY}", headers=h).json().get("half_price") is not None   # ?week=: the seeded data's week, not the real "this week"
    finally:
        api.pool.close()
    out = runner.purge(conn, "coles")
    assert out["products_deleted"] == 9 and conn.execute("SELECT count(*) AS n FROM price_observations").fetchone()["n"] == 0
    assert offers.weekly_specials(conn, ids["A"])["items"] == [] and "offer" not in str(store.get_list(conn, ids["A"], DAY)["items"])
