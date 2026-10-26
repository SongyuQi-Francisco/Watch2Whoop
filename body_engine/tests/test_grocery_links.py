"""Grocery store links and barcode lookup (docs/grocery-plan.md §7): product-URL validation / parsing, per-store link generation,
Open Food Facts parsing, opt-in and limits. No database, no network (OFF calls are stubbed)."""
import pytest

from body_engine.ai.chat import ALWAYS_CONFIRM
from body_engine.assistant.tools import TOOLS_BY_NAME
from body_engine.grocery import links as L, off, params as P


@pytest.mark.parametrize("url,store,sku,canon", [
    ("https://www.coles.com.au/product/coles-full-cream-milk-3l-8150288", "coles", "8150288",
     "https://www.coles.com.au/product/coles-full-cream-milk-3l-8150288"),
    ("https://coles.com.au/product/coles-free-range-eggs-12-pack-700g-2809562/?utm_source=app#x", "coles", "2809562",
     "https://www.coles.com.au/product/coles-free-range-eggs-12-pack-700g-2809562"),
    ("https://www.coles.com.au/product/8150288", "coles", "8150288", "https://www.coles.com.au/product/8150288"),
    ("https://www.woolworths.com.au/shop/productdetails/36059/woolworths-chicken-breast-fillets", "woolworths", "36059",
     "https://www.woolworths.com.au/shop/productdetails/36059/woolworths-chicken-breast-fillets"),
    ("  https://woolworths.com.au/shop/productdetails/36059?cid=share ", "woolworths", "36059",
     "https://www.woolworths.com.au/shop/productdetails/36059"),
])
def test_parse_product_url(url, store, sku, canon):
    assert L.parse_product_url(url) == {"store": store, "sku": sku, "url": canon}


@pytest.mark.parametrize("url", [
    "http://www.coles.com.au/product/x-8150288",                    # not https
    "https://www.coles.com.au/search/products?q=milk",              # search, not a product page
    "https://www.coles.com.au/product/milk",                        # no id
    "https://www.woolworths.com.au/shop/productdetails/abc/x",      # stockcode not numeric
    "https://www.woolworths.com.au/shop/browse/dairy",
    "https://www.coles.com.au.evil.com/product/x-1",                # other hosts
    "https://evil.com/www.coles.com.au/product/x-1",
    "https://user" + "@" + "www.coles.com.au/product/x-1",       # credentials (split so it does not look like an e-mail)
    "https://www.coles.com.au:8443/product/x-1",
    "https://www.aldi.com.au/product/x-1",
    "javascript:alert(1)", "", "https://www.coles.com.au/product/" + "a" * 300 + "-1",
])
def test_parse_product_url_rejects(url):
    with pytest.raises(ValueError):
        L.parse_product_url(url)


def test_store_key_and_search_term():
    assert L.store_key("Coles Local") == "coles" and L.store_key("woolies") == "woolworths"
    assert L.store_key("Woolworths Metro") == "woolworths"
    assert L.store_key(None) is None and L.store_key("  ") is None
    assert L.store_key("Aldi") == "" and L.store_key("Costco") == ""
    assert L.search_term("Greek Yoghurt", "Chobani") == "Chobani Greek Yoghurt"
    assert L.search_term("Chobani Greek Yoghurt", "Chobani") == "Chobani Greek Yoghurt"
    assert L.search_term("鸡胸肉", "某牌", ["鸡胸", "chicken breast"]) == "chicken breast"
    assert L.search_term("鸡胸肉") == "鸡胸肉"


def test_links():
    ALL = ("coles", "woolworths")
    saved = L.links("鸡胸肉", "Costco", "https://www.coles.com.au/product/chicken-breast-123", connected=ALL)
    assert saved == [{"store": "coles", "label": "Coles", "kind": "product", "url": "https://www.coles.com.au/product/chicken-breast-123"}]
    both = L.links("Greek Yoghurt", None, connected=ALL)
    assert [x["store"] for x in both] == ["coles", "woolworths"] and all(x["kind"] == "search" for x in both)
    assert both[0]["url"] == "https://www.coles.com.au/search/products?q=Greek%20Yoghurt"
    assert both[1]["url"] == "https://www.woolworths.com.au/shop/search/products?searchTerm=Greek%20Yoghurt"
    assert [x["label"] for x in L.links("milk", "Woolies", connected=ALL)] == ["WWS"]   # issue #44: shown as WWS
    assert L.links("milk", "Aldi", connected=ALL) == []
    q = L.links("鸡蛋 & <b>", None, connected=ALL)[0]["url"]   # always URL-encoded: nothing to break out of the href
    assert "<" not in q and "&" not in q.split("?q=")[1] and " " not in q


def test_links_only_for_connected_stores():
    """Issue #44: no store is connected by default -> no links; otherwise only the connected chains."""
    url = "https://www.coles.com.au/product/chicken-breast-123"
    assert L.links("Greek Yoghurt", None) == [] and L.links("x", None, url) == []
    assert [x["store"] for x in L.links("Greek Yoghurt", None, connected=["woolworths"])] == ["woolworths"]
    assert L.links("x", None, url, connected=["woolworths"]) == []          # saved page of a chain that is not connected
    assert L.links("milk", "Coles", connected=["woolworths"]) == []
    assert L.store_key("WWS Metro") == "woolworths" and L.store_label("Woolworths") == "WWS" and L.store_label("Aldi") == "Aldi"
    assert L.check_stores(["woolworths", "coles", "coles"]) == ["coles", "woolworths"]
    with pytest.raises(ValueError):
        L.check_stores(["aldi"])


def test_tools_registered():
    t = TOOLS_BY_NAME["lookup_barcode"]
    assert not t.writes and t.scope == "nutrition" and "lookup_barcode" not in ALWAYS_CONFIRM
    props = TOOLS_BY_NAME["save_product"].input_schema["properties"]
    assert "product_url" in props and "nutrition_source" in props


OFF_JSON = {"status": 1, "product": {
    "product_name": "Greek Style Yoghurt", "brands": "Chobani, Chobani Inc", "quantity": "907 g",
    "nutriments": {"energy-kcal_100g": 70, "proteins_100g": 9.2, "carbohydrates_100g": 4, "fat_100g": 2.1,
                   "saturated-fat_100g": 1.3, "sugars_100g": 4, "sodium_100g": 0.045, "salt_100g": 0.11}}}


def test_off_parse():
    r = off.parse("9300633603137", OFF_JSON)
    assert r["found"] and r["name"] == "Greek Style Yoghurt" and r["brand"] == "Chobani" and r["quantity"] == "907 g"
    assert r["nutrition"] == {"kcal": 70, "protein_g": 9.2, "carbs_g": 4, "fat_g": 2.1, "sat_fat_g": 1.3, "sugar_g": 4, "sodium_mg": 45}
    assert r["source"] == P.OFF_ATTRIBUTION and r["url"].endswith("/9300633603137")
    assert off.parse("1234567", {"status": 0})["found"] is False


@pytest.fixture()
def fresh_off(monkeypatch):
    monkeypatch.setattr(off, "_recent", __import__("collections").deque())
    monkeypatch.setattr(off, "_per_user", {})
    monkeypatch.setattr(off, "_cache", {})
    calls = []
    monkeypatch.setattr(off, "_fetch", lambda code: calls.append(code) or OFF_JSON)
    return calls


def test_off_disabled_without_contact(monkeypatch, fresh_off):
    monkeypatch.delenv("BODY_OFF_CONTACT", raising=False)
    r = off.lookup(1, "9300633603137")
    assert r["enabled"] is False and not fresh_off
    with pytest.raises(ValueError):
        off.lookup(1, "12ab")


def test_off_lookup_cache_and_limits(monkeypatch, fresh_off):
    monkeypatch.setenv("BODY_OFF_CONTACT", "ops@example.org")
    assert off.user_agent().startswith("Watch2Whoop/") and off.user_agent().endswith("(ops@example.org)")
    assert off.lookup(1, "9300 6336 03137")["found"]
    assert off.lookup(2, "9300633603137")["found"] and fresh_off == ["9300633603137"]   # cached: one OFF call
    monkeypatch.setattr(P, "OFF_PER_USER_DAY", 2)
    off.lookup(1, "1111111")
    with pytest.raises(RuntimeError, match="用完"):
        off.lookup(1, "2222222")
    monkeypatch.setattr(P, "OFF_PER_MINUTE", 3)
    off.lookup(3, "3333333")
    with pytest.raises(RuntimeError, match="频繁"):
        off.lookup(4, "4444444")


def test_off_parse_archive_fields():
    """#291: the fuller nutrition table (minerals and cholesterol are grams in OFF -> mg), ingredients, allergens and the picture link."""
    data = {"status": 1, "product": {
        "product_name": "Milk", "ingredients_text": "Milk", "ingredients_text_en": "Milk (100%)",
        "allergens_tags": ["en:milk", "fr:lait", "en:tree-nuts"], "traces_tags": ["en:soybeans"],
        "image_front_url": "https://images.openfoodfacts.org/images/products/930/front.400.jpg",
        "nutriments": {"energy-kcal_100g": 64, "proteins_100g": 3.4, "carbohydrates_100g": 4.8, "fat_100g": 3.6,
                       "trans-fat_100g": 0.1, "added-sugars_100g": 0, "fiber_100g": 0, "potassium_100g": 0.15,
                       "calcium_100g": 0.12, "iron_100g": 0.0001, "cholesterol_100g": 0.012}}}
    r = off.parse("9300633603137", data)
    assert r["nutrition"] == {"kcal": 64, "protein_g": 3.4, "carbs_g": 4.8, "fat_g": 3.6, "trans_fat_g": 0.1, "added_sugar_g": 0,
                              "fiber_g": 0, "potassium_mg": 150, "calcium_mg": 120, "iron_mg": 0.1, "cholesterol_mg": 12}
    assert r["ingredients"] == "Milk (100%)" and r["allergens"] == ["milk", "tree nuts"] and r["allergen_traces"] == ["soybeans"]
    assert r["image_url"].startswith("https://images.openfoodfacts.org/")
    # nothing extra when OFF has none of it; a non-https picture is dropped
    bare = off.parse("9300633603137", OFF_JSON)
    assert not {"ingredients", "allergens", "allergen_traces", "image_url"} & set(bare)
    data["product"]["image_front_url"] = "http://images.openfoodfacts.org/x.jpg"
    assert "image_url" not in off.parse("9300633603137", data)
