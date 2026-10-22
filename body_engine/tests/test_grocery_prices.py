"""Prices from the user's own AI (docs/grocery-plan.md §9, migration 420): de-duplicated product pool, per-user visibility (the main
thing tested here: nobody sees, finds or reaches another user's products or prices, also not by guessing ids), observations that
never expire, list pricing (which store, totals, savings, missing / stale), purchase history with dear / cheap verdicts, the
shopping context for the AI, sync purge keeping users' data, and the in-page chat's official web tools switch.
Needs Postgres (BODY_TEST_DB_NAME_PRICES, default body_test_grocery_prices). No network: vendor SDK clients are faked."""
import os
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.ai import adapters, catalog, chat, providers
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.grocery import params as P, prices, store
from body_engine.grocery.sync import offers as SO, params as SP, runner

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_PRICES", "body_test_grocery_prices")
CHICKEN = "https://www.coles.com.au/product/coles-rspca-chicken-breast-fillets-500g-1234567"
CHICKEN_WWS = "https://www.woolworths.com.au/shop/productdetails/765432/woolworths-chicken-breast-fillets"
NOW = datetime.now(timezone.utc)


def _price(**kw):
    return {"store": "coles", "name": "RSPCA Chicken Breast Fillets", "size": "500g", "price": 8.0, "url": CHICKEN,
            "for_item": "鸡胸肉", **kw}


# ---------------------------------------------------------------- no database
def test_tools_and_name_key():
    for n in ("get_shopping_context", "get_price_history"):
        assert not TOOLS_BY_NAME[n].writes and TOOLS_BY_NAME[n].scope == "nutrition"
    assert TOOLS_BY_NAME["record_store_prices"].writes
    assert prices.name_key("Greek Yoghurt  Natural!", "1kg") == "greek yoghurt natural|1000g"
    assert prices.name_key("希腊酸奶", "1 kg") == "希腊酸奶|1000g" and prices.name_key("Eggs", None) == "eggs"


def test_anthropic_web_tools_and_pause_turn(monkeypatch):
    from body_engine.ai.adapters import anthropic_api as A
    a = adapters.make("anthropic_api", "claude-opus-5-5", "k")
    assert a._web_tools() == []                                   # off by default
    a = adapters.make("anthropic_api", "claude-opus-5-5", "k", web=True)
    assert [t["type"] for t in a._web_tools()] == ["web_search_20260209", "web_fetch_20260209"]
    old = adapters.make("anthropic_api", "claude-haiku-4-5", "k", web=True)
    assert [t["type"] for t in old._web_tools()] == ["web_search_20250305", "web_fetch_20250910"]   # basic fetch too (#150)
    assert all(t["max_content_tokens"] == A.WEB_FETCH_MAX_TOKENS for t in old._web_tools() + a._web_tools()
               if t["name"] == "web_fetch")

    class Block:
        def __init__(self, **d):
            self.d = d

        def model_dump(self, **_):
            return self.d

    class Usage:
        input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 10, 5, 0, 0

    finals = [
        type("M", (), {"content": [Block(type="server_tool_use", id="s1", name="web_search", input={"query": "coles chicken"})],
                       "usage": Usage(), "stop_reason": "pause_turn"}),
        type("M", (), {"content": [Block(type="web_search_tool_result", tool_use_id="s1", content=[]),
                                   Block(type="text", text="Coles $8"),
                                   Block(type="tool_use", id="t1", name="record_store_prices", input={"items": []})],
                       "usage": Usage(), "stop_reason": "tool_use"}),
    ]
    sent = []

    class Stream:
        def __init__(self, **kw):
            sent.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter([])

        def get_final_message(self):
            return finals[len(sent) - 1]

    class Client:
        def __init__(self, **kw):
            self.beta = type("B", (), {"messages": type("Ms", (), {"stream": staticmethod(lambda **kw: Stream(**kw))})()})()

    monkeypatch.setattr(A.anthropic, "Anthropic", Client)
    out = list(a.stream_turn("sys", [{"role": "user", "content": [{"type": "text", "text": "查价"}]}], []))
    result = out[-1][1]
    assert len(sent) == 2 and sent[1]["messages"][-1]["role"] == "assistant"              # pause_turn continued
    assert sent[1]["messages"][-1]["content"][0]["type"] == "server_tool_use"
    assert any(t.get("type") == "web_search_20260209" for t in sent[0]["tools"])
    assert result.text == "Coles $8" and result.tool_calls == [{"id": "t1", "name": "record_store_prices", "args": {"items": []}}]
    assert [b["type"] for b in result.native] == ["server_tool_use", "web_search_tool_result", "text", "tool_use"]
    assert result.usage["input_tokens"] == 20 and result.stop == "tool_use"


def test_openai_and_gemini_web_tools(monkeypatch):
    from body_engine.ai.adapters import gemini_api, openai_api
    seen = {}

    class Stop(Exception):
        pass

    class OAI:
        def __init__(self, **kw):
            self.responses = self

        def create(self, **kw):
            seen["openai"] = kw
            raise Stop

    monkeypatch.setattr(openai_api.openai, "OpenAI", OAI)
    for web in (False, True):
        with pytest.raises(Stop):
            list(adapters.make("openai_api", "gpt-x", "k", web=web).stream_turn("s", [], []))
        assert ({"type": "web_search"} in seen["openai"].get("tools", [])) is web   # no tools at all: no "tools" key

    class Models:
        def generate_content_stream(self, model, contents, config):
            seen["gemini"] = config
            raise Stop

    class GClient:
        def __init__(self, **kw):
            self.models = Models()

    monkeypatch.setattr(gemini_api.genai, "Client", GClient)
    with pytest.raises(adapters.AdapterError):   # the adapter wraps any SDK failure
        list(adapters.make("gemini_api", "gemini-x", "k", web=True).stream_turn("s", [], []))
    assert any(t.google_search is not None for t in seen["gemini"].tools)
    assert any(t.url_context is not None for t in seen["gemini"].tools)


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


@pytest.fixture(scope="module")
def world(dsn):
    """A and B both connected to Coles + WWS; C connected but never links anything; B has A's nutrition share; D shares nothing."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, birth_date, height_cm) VALUES (%s, '1990-01-01', 175) RETURNING id",
                               (n,)).fetchone()["id"] for n in "ABCD"}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        tok["A_ro"] = auth.create_token(conn, ids["A"], "ro", ["read"])
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["nutrition"]))
        for n in "ABC":
            store.set_connected_stores(conn, ids[n], ["coles", "woolworths"])
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def h(token):
    return {"Authorization": f"Bearer {token}"}


def test_pool_dedupe(conn, world):
    ids, _ = world
    a, b = ids["A"], ids["B"]
    r1 = prices.record(conn, a, [_price()])
    r2 = prices.record(conn, b, [_price(name="Chicken fillets (B's name)", price=7.5, url=CHICKEN + "?utm_source=share")])
    pid = r1["items"][0]["store_product_id"]
    assert r2["items"][0]["store_product_id"] == pid                              # same store + product id -> one row
    rows = conn.execute("SELECT * FROM store_products WHERE store = 'coles' AND sku = '1234567'").fetchall()
    assert len(rows) == 1 and rows[0]["name"] == "RSPCA Chicken Breast Fillets" and rows[0]["origin"] == "user"
    assert rows[0]["price"] is None                                                # no per-user price on the shared pool row
    # barcode, then name + size, when there is no product page
    y1 = prices.record(conn, a, [{"store": "coles", "name": "Greek Yoghurt", "size": "1kg", "price": 6, "barcode": "9300000000123"}])
    y2 = prices.record(conn, b, [{"store": "coles", "name": "greek yoghurt", "size": "1 kg", "price": 5, "barcode": "9300000000123"}])
    assert y1["items"][0]["store_product_id"] == y2["items"][0]["store_product_id"]
    n1 = prices.record(conn, a, [{"store": "coles", "name": "Rolled Oats", "size": "1kg", "price": 3}])
    n2 = prices.record(conn, b, [{"store": "coles", "name": "rolled  oats!", "size": "1000g", "price": 3.2}])
    n3 = prices.record(conn, b, [{"store": "coles", "name": "Rolled Oats", "size": "750g", "price": 2.6}])
    assert n1["items"][0]["store_product_id"] == n2["items"][0]["store_product_id"] != n3["items"][0]["store_product_id"]
    # a later upload with the product page upgrades the name-only row instead of making a second one
    up = prices.record(conn, a, [{"store": "coles", "name": "Rolled Oats", "size": "1kg", "price": 3,
                                  "url": "https://www.coles.com.au/product/coles-rolled-oats-1kg-555"}])
    assert up["items"][0]["store_product_id"] == n1["items"][0]["store_product_id"]
    assert conn.execute("SELECT sku FROM store_products WHERE id = %s", (n1["items"][0]["store_product_id"],)).fetchone()["sku"] == "555"
    # the same reading sent twice is stored once
    at = (NOW - timedelta(hours=1)).isoformat()
    assert prices.record(conn, a, [_price(observed_at=at)])["recorded"] == 1
    assert prices.record(conn, a, [_price(observed_at=at)])["recorded"] == 0
    conn.commit()


def test_record_validation(conn, world):
    ids, _ = world
    d = ids["D"]
    with pytest.raises(ValueError, match="没连接|not connected"):
        prices.record(conn, d, [_price()])
    a = ids["A"]
    with pytest.raises(ValueError, match="30"):
        prices.record(conn, a, [_price()] * 31)
    with pytest.raises(ValueError, match="WWS|Coles"):
        prices.record(conn, a, [_price(store="woolworths")])                      # a Coles page filed under WWS
    with pytest.raises(ValueError, match="未来|future"):
        prices.record(conn, a, [_price(observed_at=(NOW + timedelta(days=2)).isoformat())])
    with pytest.raises(ValueError):
        prices.record(conn, a, [_price(url="https://evil.example/product/1")])
    with pytest.raises(ValueError):
        prices.record(conn, a, [_price(price=-1)])
    conn.rollback()


def test_isolation_everywhere(conn, world, client):
    """C never linked anything: A's and B's products and prices are invisible to C by list, search, id, list matching, specials,
    and the sync read side; B sees the shared product only with B's own prices."""
    ids, tok = world
    a, b, c = ids["A"], ids["B"], ids["C"]
    prices.record(conn, a, [_price(price=4.0, was_price=8.0, special="half_price")])   # now: A's latest
    conn.commit()
    pid = conn.execute("SELECT id FROM store_products WHERE sku = '1234567'").fetchone()["id"]
    assert prices.list_products(conn, c) == [] and prices.list_products(conn, c, q="chicken") == []
    with pytest.raises(LookupError):
        prices.stats(conn, c, pid)
    with pytest.raises(LookupError) as e1:
        prices.stats(conn, c, 999999)                                              # same answer as a product that exists
    assert str(e1.value) == str(pytest.raises(LookupError, prices.stats, conn, c, pid).value)
    # API: 404 for both, same detail; another user's id never 200
    r_real = client.get(f"/v1/users/me/grocery/store-products/{pid}", headers=h(tok["C"]))
    r_none = client.get("/v1/users/me/grocery/store-products/999999", headers=h(tok["C"]))
    assert r_real.status_code == r_none.status_code == 404 and r_real.json()["detail"] == r_none.json()["detail"]
    assert client.get("/v1/users/me/grocery/store-products?q=chicken", headers=h(tok["C"])).json() == []
    # C's list line with the same name gets no price; C's specials and the meal hits stay empty
    store.add_items(conn, c, None, [{"name": "鸡胸肉", "qty": 500, "unit": "g"}])
    lst = store.get_list(conn, c)
    assert all("offer" not in i for i in lst["items"]) and lst["prices"]["missing"] == ["鸡胸肉"]
    assert SO.weekly_specials(conn, c)["items"] == [] and SO.half_price_hits(conn, c, ["鸡胸肉"]) == {}
    # the sync read side never returns user-added pool rows, even with sync on and the exact sku
    os.environ[SP.ENV_STORES] = "coles"
    try:
        assert SO.find_offer(conn, ["coles"], "Chicken Breast Fillets", {"product_url": CHICKEN, "store_sku": "1234567"}) is None
        assert SO.find_offer(conn, ["coles"], "Chicken Breast Fillets") is None
    finally:
        os.environ.pop(SP.ENV_STORES)
    # B linked the same product (test_pool_dedupe) but sees only B's own prices
    bst = prices.stats(conn, b, pid)
    ast = prices.stats(conn, a, pid)
    assert {p["price"] for p in bst["points"]} == {7.5} and 4.0 in {p["price"] for p in ast["points"]}
    # tools take the caller's own user only; writes to someone else are refused
    with pytest.raises(LookupError):
        run_tool(conn, c, "butler", "get_price_history", {"store_product_id": pid})
    with pytest.raises(PermissionError):
        run_tool(conn, ids["D"], "butler", "get_price_history", {"user": str(a), "q": "chicken"})
    assert client.post(f"/v1/users/{a}/grocery/store-prices", json={"items": [_price()]}, headers=h(tok["B"])).status_code == 403
    assert client.post("/v1/users/me/grocery/store-prices", json={"items": [_price()]}, headers=h(tok["A_ro"])).status_code == 403
    assert client.get(f"/v1/users/{a}/grocery/store-products", headers=h(tok["D"])).status_code == 403
    assert client.get(f"/v1/users/{a}/grocery/context", headers=h(tok["D"])).status_code == 403
    # a nutrition share shows the owner's own view (existing share rule), not the viewer's
    shared = client.get(f"/v1/users/{a}/grocery/store-products?q=chicken", headers=h(tok["B"])).json()
    assert shared and shared[0]["offer"]["price"] == 4.0
    conn.commit()


def test_list_pricing_and_purchases(conn, world):
    ids, _ = world
    a = ids["A"]
    week = date.today()
    old = (NOW - timedelta(days=12)).isoformat()
    prices.record(conn, a, [
        _price(store="woolworths", url=CHICKEN_WWS, name="WW Chicken Breast Fillets", price=9.0, observed_at=old),
        {"store": "coles", "name": "Broccoli", "size": "each", "price": 1.5, "was_price": 3.0, "special": "half_price",
         "for_item": "西兰花"},
    ])
    store.add_items(conn, a, week, [{"name": "鸡胸肉", "qty": 1000, "unit": "g"}, {"name": "西兰花", "qty": 2, "unit": "pcs"},
                                    {"name": "纸巾", "qty": 1, "unit": "pcs"}])
    lst = store.get_list(conn, a, week)
    by = {i["name"]: i for i in lst["items"]}
    ch = by["鸡胸肉"]["offer"]
    assert ch["store"] == "coles" and ch["price"] == 4.0 and ch["packages"] == 2 and ch["cost"] == 8.0    # cheapest store wins
    assert by["鸡胸肉"]["alt_offer"]["store"] == "woolworths" and by["鸡胸肉"]["est_from"] == "coles"
    assert ch["half_price"] and ch["save_total"] == 8.0 and ch["age_days"] == 0
    assert by["西兰花"]["offer"]["half_price"] and "offer" not in by["纸巾"]
    pr = lst["prices"]
    assert pr["by_store"]["coles"]["items"] == 2 and pr["missing"] == ["纸巾"] and pr["savings"]["AUD"] == 8.0 + 3.0
    assert lst["half_price"]["items"] == 2
    assert by["鸡胸肉"]["links"][0] == {"store": "coles", "label": "Coles", "kind": "product", "url": CHICKEN}
    # stale: an old price is shown with its age and listed again
    wws_only = prices.best_offer(prices._with_latest(conn, a, stores=["woolworths"]), {"name": "鸡胸肉", "buy_qty": 500, "unit": "g"},
                                 None, date.today(), prices._tz(conn, a))[0]
    assert wws_only["age_days"] >= 12 and wws_only["stale"]
    # ticking with the price paid records the purchase and its verdict (history: 8.0, 7.5? no - A's own: 8.0, 4.0 special)
    pid = ch["store_product_id"]
    for p in (8.0, 8.2, 7.9):
        prices.record(conn, a, [_price(price=p, observed_at=(NOW - timedelta(days=40 + p)).isoformat())])
    item = by["鸡胸肉"]
    out = store.set_bought(conn, a, [item["id"]], True, {item["id"]: 2 * 10.5})        # 10.50 per pack: dear
    bought = next(i for i in out["items"] if i["id"] == item["id"])
    assert bought["purchase"]["verdict"] == "dear" and bought["purchase"]["unit_paid"] == 10.5
    pur = conn.execute("SELECT * FROM grocery_purchases WHERE shopping_item_id = %s", (item["id"],)).fetchone()
    assert pur["store"] == "coles" and pur["store_product_id"] == pid and not pur["paid_estimated"]
    store.set_bought(conn, a, [item["id"]], False)
    assert conn.execute("SELECT count(*) AS n FROM grocery_purchases WHERE shopping_item_id = %s", (item["id"],)).fetchone()["n"] == 0
    # ticking without a price records the store price, marked estimated (no verdict on a guess)
    store.set_bought(conn, a, [by["西兰花"]["id"]], True)
    est = conn.execute("SELECT * FROM grocery_purchases WHERE shopping_item_id = %s", (by["西兰花"]["id"],)).fetchone()
    assert est["paid_estimated"] and float(est["paid_total"]) == 3.0 and est["on_special"]
    # the AI records a purchase outside the list: cheap against the history
    r = prices.record(conn, a, [_price(price=6.0, bought=True, packages=1)])
    assert r["items"][0]["purchase"]["verdict"] == "cheap"
    st = prices.stats(conn, a, pid)
    assert st["min"] == 4.0 and st["latest"] is not None and st["specials"]["count"] >= 1 and st["last_purchase"]["verdict"] == "cheap"
    assert any(p["kind"] == "paid" for p in st["points"])
    conn.commit()


def test_shopping_context_and_tools(conn, world):
    ids, _ = world
    a = ids["A"]
    ctx = run_tool(conn, a, "butler", "get_shopping_context", {})
    assert {s["store"] for s in ctx["connected_stores"]} == {"coles", "woolworths"}
    assert "纸巾" in ctx["needs_price"] and len(ctx["needs_price"]) <= P.CONTEXT_NEEDS_MAX
    assert any("绕过" in r for r in ctx["rules"]) and ctx["health"]["diet_constraints"] == []   # no limits set (#76)
    out = run_tool(conn, a, "butler", "record_store_prices", {"items": [
        {"store": "coles", "name": "Kleenex Tissues", "price": 4.5, "for_item": "纸巾", "source": "screenshot"}]})
    assert out["recorded"] == 1
    assert "纸巾" not in run_tool(conn, a, "butler", "get_shopping_context", {})["needs_price"]
    hist = run_tool(conn, a, "butler", "get_price_history", {"q": "chicken"})
    assert hist and all("image_url" not in x for x in hist)
    one = run_tool(conn, a, "butler", "get_price_history", {"store_product_id": hist[0]["id"]})
    assert "points" in one and len(one["points"]) <= 20
    got = run_tool(conn, a, "butler", "get_shopping_list", {})
    assert "prices" in got and all("url" not in (i.get("offer") or {}) for i in got["items"])
    conn.commit()


def test_sync_purge_keeps_users_data(conn, world):
    ids, _ = world
    a = ids["A"]
    sync = conn.execute("""INSERT INTO store_products (store, sku, name, url, price, special_type, first_seen, last_seen)
                           VALUES ('coles', '8888', 'Sync Milk 2L', 'https://www.coles.com.au/product/sync-milk-8888', 3, NULL,
                                   current_date, current_date) RETURNING id""").fetchone()["id"]
    conn.execute("INSERT INTO store_products (store, sku, name, url, first_seen, last_seen) VALUES ('coles', '9999', 'Sync Only', "
                 "'https://www.coles.com.au/product/sync-only-9999', current_date, current_date)")
    conn.execute("INSERT INTO price_observations (store_product_id, observed_on, observed_at, source, price) VALUES (%s, current_date, now(), 'sync', 3)",
                 (sync,))
    prices.record(conn, a, [{"store": "coles", "name": "Milk 2L", "price": 3.1, "url": "https://www.coles.com.au/product/sync-milk-8888"}])
    conn.commit()
    out = runner.purge(conn, "coles")
    assert out["products_deleted"] == 1 and out["kept_linked_by_users"] == 1
    row = conn.execute("SELECT * FROM store_products WHERE id = %s", (sync,)).fetchone()
    assert row["origin"] == "user" and row["price"] is None
    obs = conn.execute("SELECT user_id, price FROM price_observations WHERE store_product_id = %s", (sync,)).fetchall()
    assert [(o["user_id"], float(o["price"])) for o in obs] == [(a, 3.1)]
    assert prices.list_products(conn, a, q="milk")                                  # still the user's
    conn.commit()


def test_web_tools_setting(conn, world, monkeypatch):
    ids, _ = world
    a = ids["A"]
    monkeypatch.setattr(providers.crypto, "encrypt", lambda uid, key: b"x")
    p = providers.create_provider(conn, a, {"kind": "anthropic_api", "api_key": "sk-ant-test-123456", "web_tools": True})
    assert p["web_tools"] is True
    off = providers.create_provider(conn, a, {"kind": "openai_api", "api_key": "sk-test-1234567"})
    assert off["web_tools"] is False                                               # off by default
    with pytest.raises(ValueError):
        providers.create_provider(conn, a, {"kind": "openai_compatible", "base_url": "https://api.deepseek.com/v1",
                                            "api_key": "sk-test-1234567", "web_tools": True})
    assert "openai_compatible" not in catalog.WEB_TOOL_KINDS and "system_fallback" not in catalog.WEB_TOOL_KINDS
    monkeypatch.setattr(providers, "decrypt_secret", lambda row, uid: "k")
    row = providers.get_provider(conn, a, p["id"])
    assert chat.make_adapter(row, a).web is True
    assert chat.make_adapter(providers.get_provider(conn, a, off["id"]), a).web is False
    conn.rollback()
