"""Product card of the in-page chat (ai/cards_product.py, FB-12, flag product_card on top of jarvis_cards_v2).

What is pinned: the card holds only the user's own data (library nutrition, prices they recorded, their purchases, their pantry),
a block without data is left out (never invented), nothing comes from the public catalogue sync or from another user, and with the
flag off nothing changes (tool definition, prompt, build). Needs Postgres (BODY_TEST_DB_NAME_PRODUCT_CARD, default
body_test_product_card). No network."""
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.ai import cards, cards_product
from body_engine.ai import params as P
from body_engine.assistant import toolsets
from body_engine.assistant.tools import TOOLS
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.grocery import prices, store
from body_engine.rollout import flags
from body_engine.rollout.registry import FLAGS

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_PRODUCT_CARD", "body_test_product_card")
# The test users keep the default time zone (users.timezone), and the product code counts "days ago" in the user's local days. So every
# timestamp below is anchored to the user's local calendar (noon of the day, or its first instant for "today"), never to "now minus
# N x 24 h": that drifts a day whenever the user's date and the UTC date differ (UTC 13:00-24:00 for Sydney) and can land in the future.
USER_TZ = ZoneInfo("Australia/Sydney")
TODAY = datetime.now(USER_TZ).date()


def at(days_ago: int = 0) -> str:
    """ISO time on the user's local day `days_ago` days back: noon, or 00:00 for today (so it is never in the future)."""
    return datetime.combine(TODAY - timedelta(days=days_ago), time(12) if days_ago else time(0), USER_TZ).isoformat()
YOGURT = {"name": "Greek Yoghurt Natural", "brand": "Chobani", "store": "Coles", "category": "dairy_eggs",
          "package_size": 1000, "package_unit": "g", "price": 7.5, "currency": "AUD",
          "nutrition": {"kcal": 97, "protein_g": 9, "carbs_g": 4.0, "fat_g": 5, "sat_fat_g": 3.4, "sugar_g": 4, "sodium_mg": 40},
          "barcode": "9300000000123", "matches": ["酸奶", "greek yoghurt"], "nutrition_source": "Open Food Facts (ODbL)"}
EGGS = {"name": "Free Range Eggs 12 Pack", "category": "dairy_eggs", "package_size": 12, "package_unit": "pcs", "piece_g": 60,
        "nutrition": {"kcal": 143, "protein_g": 12.6, "carbs_g": 0.7, "fat_g": 9.5}, "matches": ["鸡蛋"]}


def _seen(store_key, days_ago, price, **kw):
    return {"store": store_key, "name": "Chobani Greek Yoghurt Natural", "size": "1kg", "price": price,
            "observed_at": at(days_ago), "source": "screenshot",
            "for_item": "greek yoghurt", **kw}


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


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def world(dsn):
    """A: connected to Coles + WWS, a yoghurt (library) with prices from both stores over four days, purchases, eggs in the pantry.
    B: the same stores, their own yoghurt prices. C: nothing recorded at all."""
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        ids = {n: c.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"] for n in "ABC"}
        for n in "AB":
            store.set_connected_stores(c, ids[n], ["coles", "woolworths"])
        a = ids["A"]
        yoghurt = store.save_product(c, a, YOGURT)
        store.save_product(c, a, EGGS)
        store.set_pantry(c, a, [{"name": "鸡蛋", "quantity": 6, "unit": "pcs"},
                                {"name": "Greek Yoghurt Natural", "quantity": 400, "unit": "g", "product_id": yoghurt["id"],
                                 "best_before": (store.today(c, a) + timedelta(days=2)).isoformat()}])
        for days_ago, price in ((40, 7.0), (25, 6.5), (12, 5.0), (3, 6.8)):
            prices.record(c, a, [_seen("coles", days_ago, price, special="half_price" if price == 5.0 else None,
                                       was_price=10.0 if price == 5.0 else None)])
        prices.record(c, a, [_seen("woolworths", 1, 6.2, name="Woolworths Greek Style Yoghurt Natural", size="1kg",
                                   url="https://www.woolworths.com.au/shop/productdetails/12345/greek-yoghurt")])
        prices.record(c, a, [_seen("coles", 0, 6.8, name="Chobani Greek Yoghurt Natural", bought=True, paid_total=6.8,
                                   observed_at=at(0))])
        prices.record(c, ids["B"], [_seen("coles", 2, 99.0)])
        c.commit()
    return ids, yoghurt["id"]


def _flag(conn, on=True):
    flags.set_flag(conn, cards.FLAG_PRODUCT, "all" if on else "off", actor="test")
    conn.commit()


# ---------------------------------------------------------------- no database
def test_flag_is_declared_and_off_changes_nothing():
    assert cards.FLAG_PRODUCT == "product_card" and cards.FLAG_PRODUCT in FLAGS
    assert cards.tool(True) is cards.TOOL_V2 and cards.tool(False, True) is cards.TOOL   # needs the v2 flag too
    assert cards.tool(True, True) is cards.TOOL_PRODUCT and cards.TOOL_PRODUCT.name == cards.NAME
    assert cards.prompt(True) == f"{cards.PROMPT}\n{cards.PROMPT_V2}" and cards.prompt(False, True) == cards.PROMPT
    assert cards.prompt(True, True).endswith(cards.PROMPT_PRODUCT)
    enum = cards.TOOL_PRODUCT.input_schema["properties"]["type"]["enum"]
    assert enum == [*cards.TYPES, *cards.TYPES_V2, "product"] and "product" not in cards.TOOL_V2.input_schema["properties"]["type"]["enum"]
    assert "product" not in cards.TYPES_V2   # the v2 list (and its golden definition) stay as they were


def test_definition_budget_and_prompt_guides_the_user():
    assert toolsets.definition_chars([cards.TOOL_PRODUCT]) <= 1150, toolsets.definition_chars([cards.TOOL_PRODUCT])
    for page in P.PAGE_GROUPS:
        assert toolsets.definition_chars([*toolsets.for_page(TOOLS, page), cards.TOOL_PRODUCT]) <= P.PAGE_BUDGET_CHARS, page
    text = cards.PROMPT_PRODUCT
    assert "拍价签或小票发给我，我帮你记价格" in text and "不是实时价" in text and "不要为它去 Coles / Woolworths 网站查价" in text and "不要尝试绕过" in text


def test_auto_specs():
    writes = {t.name for t in TOOLS if t.writes}
    reads = {t.name for t in TOOLS if not t.writes}
    assert {"save_product", "record_store_prices"} <= set(cards.AUTO_V2) and {"save_product", "record_store_prices"} <= writes
    assert {"find_products", "get_price_history"} <= set(cards.READ_AUTO) and {"find_products", "get_price_history"} <= reads
    assert cards.auto_spec("save_product", {"name": "x"}) is None                          # v2 off: as before
    spec = cards.auto_spec("save_product", {"product_id": 7, "name": "Greek Yoghurt", "barcode": None}, v2=True)
    assert spec == {"type": "product", "date": None, "id": 7, "metric": "Greek Yoghurt"}
    assert cards.auto_spec("save_product", {"product_id": None, "name": "Greek Yoghurt", "barcode": "9300000000123"},
                           v2=True)["metric"] == "9300000000123"                                # a barcode is exact
    assert cards.auto_spec("save_product", {"product_id": None, "name": None, "barcode": None}, v2=True) is None
    one = {"items": [{"name": "Chobani Greek Yoghurt Natural"}]}
    assert cards.auto_spec("record_store_prices", one, v2=True)["metric"] == "Chobani Greek Yoghurt Natural"
    assert cards.auto_spec("record_store_prices", {"items": [{"name": "a"}, {"name": "b"}]}, v2=True) is None   # a receipt: no card
    assert cards.auto_spec("find_products", {"q": "yoghurt"}, v2=True) is None                  # reads: the fallback AI only
    assert cards.auto_spec("find_products", {"q": "yoghurt"}, v2=True, reads=True)["metric"] == "yoghurt"
    assert cards.auto_spec("find_products", {"q": None, "category": "snacks"}, v2=True, reads=True) is None
    assert cards.auto_spec("get_price_history", {"q": "eggs", "store_product_id": None}, v2=True, reads=True)["metric"] == "eggs"
    assert cards.spec_key({"type": "product", "id": 7, "metric": "x"}) != cards.spec_key({"type": "product", "id": 8, "metric": "x"})


def test_energy_split_adds_up_and_needs_all_three():
    for p in ({"protein_g": 9, "carbs_g": 4, "fat_g": 5}, {"protein_g": 33.3, "carbs_g": 33.3, "fat_g": 33.4},
              {"protein_g": 0.5, "carbs_g": 80, "fat_g": 0.3}, {"protein_g": 1, "carbs_g": 1, "fat_g": 0}):
        assert sum(cards_product._split(p).values()) == 100
    assert cards_product._split({"protein_g": 9, "carbs_g": 4}) is None and cards_product._split({"protein_g": 0, "carbs_g": 0, "fat_g": 0}) is None


def test_summary_is_short_and_guides_when_no_prices():
    card = {"type": "product", "name": "酸奶", "nutrition": None, "stores": [], "history": None, "pantry": []}
    s = cards.summary(card)
    assert s.startswith("已显示商品卡「酸奶」") and "没有价格记录" in s and "拍价签或小票" in s and "record_store_prices" in s
    full = {"type": "product", "name": "Yoghurt", "nutrition": {"unit": "g", "per100": {"kcal": 97, "protein_g": 9}},
            "stores": [{"label": "Coles", "price": 5.0, "age_days": 3, "lowest": True}], "history": {"count": 4, "min": 5.0, "avg": 6.4},
            "pantry": [{"name": "酸奶", "label": "400 g"}]}
    s = cards.summary(full)
    assert "最低 Coles 5" in s and "3 天前" in s and "家里有 酸奶 400 g" in s and "不是实时价" in s and "拍价签" not in s


# ---------------------------------------------------------------- database
def test_build_needs_the_flag(conn, world):
    ids, pid = world
    _flag(conn, False)
    with pytest.raises(ValueError, match="还没有对这个用户开放"):
        cards.build(conn, ids["A"], {"type": "product", "id": pid}, v2=True)
    with pytest.raises(ValueError):   # without v2 the type does not exist at all
        cards.build(conn, ids["A"], {"type": "product", "id": pid})
    assert cards.product_enabled(conn, ids["A"]) is False
    _flag(conn, True)
    assert cards.product_enabled(conn, ids["A"]) is False   # jarvis_cards_v2 / jarvis_rich are still off
    flags.set_flag(conn, cards.FLAG, "all", actor="test")
    flags.set_flag(conn, cards.FLAG_V2, "all", actor="test")
    conn.commit()
    assert cards.product_enabled(conn, ids["A"]) is True


def test_full_card(conn, world):
    ids, pid = world
    _flag(conn)
    card = cards.build(conn, ids["A"], {"type": "product", "id": pid}, v2=True)
    assert card["v"] == 1 and card["type"] == "product" and card["id"] == pid
    assert card["name"] == "Greek Yoghurt Natural" and card["brand"] == "Chobani" and card["size"] == "1 kg"
    n = card["nutrition"]
    assert n["unit"] == "g" and n["per100"]["kcal"] == 97 and n["per100"]["protein_g"] == 9 and n["per100"]["sodium_mg"] == 40
    assert n["split"] == {"protein_g": 37, "carbs_g": 16, "fat_g": 47}   # 36 / 16 / 45 kcal: 37.1 / 16.5 / 46.4 %, the rounding gap goes to the biggest
    assert n["serving"] == {"kind": "pack", "amount": 1000.0, "unit": "g", "kcal": 970.0, "protein_g": 90.0, "carbs_g": 40.0, "fat_g": 50.0}
    assert n["source"] == "Open Food Facts (ODbL)" and n["flags"] == []
    # stores: only prices this user recorded (B's 99.0 and the catalogue never show); WWS is a different product page
    by = {r["store"]: r for r in card["stores"]}
    assert set(by) == {"coles", "woolworths"} and by["coles"]["price"] == 6.8 and by["woolworths"]["price"] == 6.2
    assert by["woolworths"]["age_days"] == 1 and by["coles"]["age_days"] == 0 and by["coles"]["source"] == "screenshot"
    assert by["coles"]["unit_price"] == 6.8 and by["coles"]["unit_price_unit"] == "kg" and by["coles"]["currency"] == "AUD"
    assert [r["store"] for r in card["stores"]] == ["woolworths", "coles"]   # cheapest unit price first
    assert by["woolworths"].get("lowest") is True and "lowest" not in by["coles"]
    assert 99.0 not in [r["price"] for r in card["stores"]]
    # history: Coles has the most days (4 seen days + today), so it draws a line; the half-price day is marked
    h = card["history"]
    assert h["store"] == "Coles" and h["count"] == 5 and len(h["points"]) == 5 and h["min"] == 5.0 and h["latest"] == 6.8
    assert [p["price"] for p in h["points"]] == [7.0, 6.5, 5.0, 6.8, 6.8]
    assert [p["special"] for p in h["points"]] == [False, False, True, False, False] and h["points"][-1]["paid"] is True
    assert h["specials"]["count"] == 1 and h["last_purchase"]["unit_paid"] == 6.8
    # pantry: linked by product id; the eggs are another product
    assert [p["name"] for p in card["pantry"]] == ["Greek Yoghurt Natural"] and card["pantry"][0]["days_left"] == 2
    assert card["pantry"][0]["label"] == "400 g" and card["pantry"][0]["expiring"] is True
    assert card["actions"] == [{"kind": "send", "key": "log_product"}, {"kind": "send", "key": "record_price"}]
    assert all(a["key"] in cards.SEND_KEYS for a in card["actions"])
    assert cards.summary(card).startswith("已显示商品卡「Greek Yoghurt Natural」")


def test_found_by_name_barcode_and_matches(conn, world):
    ids, pid = world
    _flag(conn)
    a = ids["A"]
    for q in ("greek yoghurt", "9300000000123", "酸奶", "Chobani"):
        assert cards.build(conn, a, {"type": "product", "metric": q}, v2=True)["id"] == pid, q
    eggs = cards.build(conn, a, {"type": "product", "metric": "鸡蛋"}, v2=True)
    assert eggs["name"] == "Free Range Eggs 12 Pack" and eggs["size"] == "12 个"
    assert eggs["nutrition"]["serving"] == {"kind": "piece", "amount": 60.0, "unit": "g", "kcal": 85.8, "protein_g": 7.6,
                                            "carbs_g": 0.4, "fat_g": 5.7}
    assert eggs["stores"] == [] and eggs["history"] is None                     # nothing recorded: no price block, no trend
    assert [p["label"] for p in eggs["pantry"]] == ["6 个"]                      # the pantry row named like one of its `matches`


def test_blocks_without_data_are_left_out(conn, world):
    ids, _ = world
    _flag(conn)
    a = ids["A"]
    bare = store.save_product(conn, a, {"name": "Mystery Sauce"})
    conn.commit()
    card = cards.build(conn, a, {"type": "product", "id": bare["id"]}, v2=True)
    assert card["nutrition"] is None and card["stores"] == [] and card["history"] is None and card["pantry"] == []
    assert card["size"] is None and card["brand"] is None
    assert card["actions"] == [{"kind": "send", "key": "record_price"}]       # nothing to log, but the user is led to add a price
    assert "没有价格记录" in cards.summary(card)


def test_two_days_show_numbers_not_a_line(conn, world):
    ids, _ = world
    _flag(conn)
    a = ids["A"]
    p = store.save_product(conn, a, {"name": "Oat Milk Barista", "package_size": 1000, "package_unit": "ml", "matches": ["oat milk"]})
    prices.record(conn, a, [{"store": "coles", "name": "Oat Milk Barista", "size": "1L", "price": 2.5, "for_item": "oat milk",
                             "observed_at": at(9)}])
    prices.record(conn, a, [{"store": "coles", "name": "Oat Milk Barista", "size": "1L", "price": 2.9, "for_item": "oat milk",
                             "observed_at": at(2)}])
    conn.commit()
    h = cards.build(conn, a, {"type": "product", "id": p["id"]}, v2=True)["history"]
    assert h["count"] == 2 and h["points"] == [] and h["min"] == 2.5 and h["latest"] == 2.9
    assert cards_product.TREND_MIN_POINTS == 3


def test_stale_price_is_never_the_lowest(conn, world):
    ids, _ = world
    _flag(conn)
    a = ids["A"]
    p = store.save_product(conn, a, {"name": "Rolled Oats", "package_size": 1000, "package_unit": "g", "matches": ["oats"]})
    prices.record(conn, a, [{"store": "coles", "name": "Rolled Oats", "size": "1kg", "price": 2.0, "for_item": "oats",
                             "observed_at": at(30)},
                            {"store": "woolworths", "name": "Rolled Oats", "size": "1kg", "price": 3.0, "for_item": "oats",
                             "observed_at": at(1)}])
    conn.commit()
    rows = cards.build(conn, a, {"type": "product", "id": p["id"]}, v2=True)["stores"]
    assert {r["store"]: r["stale"] for r in rows} == {"coles": True, "woolworths": False}
    assert not any(r.get("lowest") for r in rows)       # only one fresh price: nothing to compare


def test_library_price_and_purchases_without_a_recorded_price(conn, world):
    ids, _ = world
    _flag(conn)
    a = ids["A"]
    p = store.save_product(conn, a, {"name": "Tinned Tuna", "store": "Aldi", "price": 1.5, "package_size": 95, "package_unit": "g"})
    for d, price in ((30, 1.4), (14, 1.5), (2, 1.9)):
        prices.add_purchase(conn, a, name="Tinned Tuna", store="Aldi", store_product_id=None, bought_on=TODAY - timedelta(days=d),
                            packages=1, paid_total=price, on_special=False, was_price=None, source="manual", product_id=p["id"])
    conn.commit()
    card = cards.build(conn, a, {"type": "product", "id": p["id"]}, v2=True)
    assert [(r["label"], r["price"], r["source"]) for r in card["stores"]] == [("Aldi", 1.5, "library")]
    assert card["stores"][0]["unit_price"] == 15.79 and card["stores"][0]["unit_price_unit"] == "kg"
    h = card["history"]
    assert h["store"] is None and h["count"] == 3 and [x["price"] for x in h["points"]] == [1.4, 1.5, 1.9] and h["min"] == 1.4


def test_only_the_users_own_prices_never_the_catalogue(conn, world):
    ids, _ = world
    _flag(conn)
    a = ids["A"]
    row = conn.execute("""INSERT INTO store_products (store, sku, name, url, price, special_type, first_seen, last_seen, origin)
                          VALUES ('coles', '9999991', 'Catalogue Only Biscuits', 'https://www.coles.com.au/product/x-9999991',
                                  1.0, 'half_price', current_date, current_date, 'sync') RETURNING id""").fetchone()
    conn.execute("""INSERT INTO price_observations (store_product_id, observed_on, observed_at, price, source)
                    VALUES (%s, current_date, now(), 1.0, 'sync')""", (row["id"],))
    conn.commit()
    with pytest.raises(ValueError, match="都没有这个商品"):
        cards.build(conn, a, {"type": "product", "metric": "Catalogue Only Biscuits"}, v2=True)
    # even when the user has the catalogue row linked (their own sighting of it), only their observation counts
    prices.record(conn, a, [{"store": "coles", "name": "Catalogue Only Biscuits", "size": "200g", "price": 3.3,
                             "url": "https://www.coles.com.au/product/x-9999991", "observed_at": at(0)}])
    conn.commit()
    card = cards.build(conn, a, {"type": "product", "metric": "Catalogue Only Biscuits"}, v2=True)
    assert card["id"] is None and card["name"] == "Catalogue Only Biscuits" and card["nutrition"] is None
    assert [r["price"] for r in card["stores"]] == [3.3] and card["actions"] == [{"kind": "send", "key": "record_price"}]


def test_isolation_between_users(conn, world):
    ids, pid = world
    _flag(conn)
    with pytest.raises(ValueError, match="没有这个商品"):   # A's library id from B (or anyone): the same answer as for a missing one
        cards.build(conn, ids["B"], {"type": "product", "id": pid}, v2=True)
    with pytest.raises(ValueError, match="没有这个商品"):
        cards.build(conn, ids["B"], {"type": "product", "id": 99999999}, v2=True)
    with pytest.raises(ValueError, match="都没有这个商品"):   # B has not saved the yoghurt: A's library is invisible by name too
        cards.build(conn, ids["B"], {"type": "product", "metric": "9300000000123"}, v2=True)
    b = cards.build(conn, ids["B"], {"type": "product", "metric": "Greek Yoghurt"}, v2=True)   # B's own recorded price only
    assert b["id"] is None and [r["price"] for r in b["stores"]] == [99.0] and b["pantry"] == []
    with pytest.raises(ValueError, match="都没有这个商品"):
        cards.build(conn, ids["C"], {"type": "product", "metric": "Greek Yoghurt"}, v2=True)
    with pytest.raises(ValueError, match="要 id"):
        cards.build(conn, ids["A"], {"type": "product"}, v2=True)


def test_stores_not_connected_show_no_prices(conn, world):
    ids, pid = world
    _flag(conn)
    store.set_connected_stores(conn, ids["A"], [])
    conn.commit()
    try:
        card = cards.build(conn, ids["A"], {"type": "product", "id": pid}, v2=True)
        assert [r["source"] for r in card["stores"]] == ["library"]             # only the library price (its own, not a store page)
    finally:
        store.set_connected_stores(conn, ids["A"], ["coles", "woolworths"])
        conn.commit()


def test_card_is_plain_json(conn, world):
    import json
    ids, pid = world
    _flag(conn)
    card = cards.build(conn, ids["A"], {"type": "product", "id": pid}, v2=True)
    assert json.loads(json.dumps(card)) == card


def test_card_shows_archive_fields(conn, world):
    """#291: the fuller nutrition table, the label's own per-serving column and the allergens (short labels) are on the card;
    the ingredient text and the picture are not (the library keeps them)."""
    ids, _ = world
    _flag(conn)
    p = store.save_product(conn, ids["A"], {
        "name": "Full Cream Milk", "package_size": 2000, "package_unit": "ml", "piece_g": None,
        "nutrition": {"kcal": 64, "protein_g": 3.4, "carbs_g": 4.8, "fat_g": 3.6, "calcium_mg": 120, "fiber_g": 0, "potassium_mg": 150},
        "serving_size": 250, "serving_unit": "ml", "nutrition_serving": {"kcal": 160, "protein_g": 8.5, "calcium_mg": 300},
        "ingredients": "Milk (100%)", "allergens": ["Milk"], "allergen_traces": ["Soy"], "image_url": "https://img.example.org/m.jpg"})
    card = cards.build(conn, ids["A"], {"type": "product", "id": p["id"]}, v2=True)
    n = card["nutrition"]
    assert n["per100"]["calcium_mg"] == 120 and n["per100"]["fiber_g"] == 0 and n["per100"]["potassium_mg"] == 150
    assert n["serving"] == {"kind": "serving", "amount": 250.0, "unit": "ml", "kcal": 160.0, "protein_g": 8.5}
    assert card["allergens"] == ["Milk"] and card["traces"] == ["Soy"] and card["image_url"] == "https://img.example.org/m.jpg"
    assert "ingredients" not in card and "含过敏原：Milk" in cards_product.summary(card)
    # a product without any of it: same card as before, empty lists
    plain = cards.build(conn, ids["A"], {"type": "product", "id": world[1]}, v2=True)
    assert plain["allergens"] == [] and plain["traces"] == [] and plain["image_url"] is None
    assert plain["nutrition"]["serving"]["kind"] == "pack"
