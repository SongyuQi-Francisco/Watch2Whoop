"""Server-side web tools of the in-page chat (ai/web/, issue #150): web_search / web_fetch for the fallback AI and
openai_compatible providers behind the rollout flag ai_web.

Pure part: page reading obeys robots.txt and stops at bot challenges, refusing statuses, login walls, known bot-protected
stores and private addresses (no request is even made for those); search providers build the right requests; the tool
definitions stay short. Database part (BODY_TEST_DB_NAME_WEB, default body_test_ai_web; skipped without Postgres): flag off
-> no web tools; flag on -> the fallback and openai_compatible get them, Anthropic keys do not (they use the vendor's own);
searches are billed in ai_usage against the fallback quota, per-day limits and the quota stop them, a blocked page tells the
model to ask for a screenshot. No network, no paid API: FakeAdapter, fake transport.
"""
import json
import os
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth
from body_engine.ai import adapters, crypto, fallback, providers, web
from body_engine.ai import fallback_params as fp
from body_engine.ai.adapters.fake import FakeAdapter
from body_engine.ai.web import fetch as F
from body_engine.ai.web import params as WP
from body_engine.ai.web import search as S
from body_engine.assistant import toolsets
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_WEB", "body_test_ai_web")
PUBLIC_IP = "203.0.113.10"   # documentation range, counted as public by the fixtures (_global)
ROBOTS_OK = (200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /private/\n")
PRODUCT = b"""<html><head><title>Greek Yoghurt 1kg | Shop</title>
<meta property="product:price:amount" content="6.50"><meta property="product:price:currency" content="AUD">
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Product", "name": "Greek Yoghurt",
 "brand": {"@type": "Brand", "name": "Farm"}, "size": "1kg", "gtin13": "9300000000001",
 "offers": {"@type": "Offer", "price": "6.50", "priceCurrency": "AUD", "availability": "https://schema.org/InStock"}}</script>
<script>var tracking = 1;</script></head>
<body><nav>Menu</nav><h1>Greek Yoghurt 1kg</h1><p>Thick and creamy. $6.50 ($0.65 per 100g)</p></body></html>"""


# ---------------------------------------------------------------- page reading (no database, no network)
class Net:
    """Fake transport: url -> (status, headers, body); records every request."""

    def __init__(self, pages: dict):
        self.pages, self.calls = pages, []

    def __call__(self, scheme, host, ip, path, limit):
        url = f"{scheme}://{host}{path}"
        self.calls.append(url)
        return self.pages.get(url, (404, {}, b""))


@pytest.fixture(autouse=True)
def doc_ip_is_public(monkeypatch):
    real = F._global
    monkeypatch.setattr(F, "_global", lambda ip: ip == PUBLIC_IP or real(ip))


@pytest.fixture()
def net(monkeypatch):
    F._robots.clear()
    F._blocked.clear()
    monkeypatch.setattr(F, "_resolve", lambda host: ["10.1.2.3"] if host.startswith("intranet.") else [PUBLIC_IP])

    def install(pages):
        n = Net(pages)
        monkeypatch.setattr(F, "_request", n)
        return n
    yield install
    F._robots.clear()
    F._blocked.clear()


def test_fetch_reads_product_and_obeys_robots(net):
    n = net({"https://shop.example/robots.txt": ROBOTS_OK,
             "https://shop.example/p/yoghurt": (200, {"content-type": "text/html; charset=utf-8"}, PRODUCT)})
    page = F.fetch("https://shop.example/p/yoghurt")
    assert n.calls == ["https://shop.example/robots.txt", "https://shop.example/p/yoghurt"]   # robots.txt first
    assert page["title"] == "Greek Yoghurt 1kg | Shop" and page["url"] == "https://shop.example/p/yoghurt"
    assert page["products"] == [{"name": "Greek Yoghurt", "brand": "Farm", "size": "1kg", "gtin": "9300000000001",
                                 "price": "6.50", "currency": "AUD", "availability": "InStock"}]
    assert page["meta"]["product:price:amount"] == "6.50"
    assert "$6.50" in page["text"] and "tracking" not in page["text"]       # scripts never reach the model
    assert len(page["text"]) <= WP.TEXT_CHARS
    with pytest.raises(F.Blocked) as e:                                     # robots.txt says no: the page is not requested
        F.fetch("https://shop.example/private/basket")
    assert e.value.reason == "robots" and n.calls.count("https://shop.example/robots.txt") == 1   # cached
    assert "https://shop.example/private/basket" not in n.calls


@pytest.mark.parametrize("robots, allowed", [
    ((200, {}, b"User-agent: Watch2WhoopBot\nDisallow: /\n"), False),     # our agent named
    ((200, {}, b"User-agent: *\nDisallow: /\n"), False),
    ((404, {}, b""), True),                                                # no robots.txt: no rules (RFC 9309)
    ((403, {}, b""), False),                                               # closed to us
    ((503, {}, b""), False),                                               # unreachable = complete disallow
])
def test_robots_answers(net, robots, allowed):
    n = net({"https://a.example/robots.txt": robots, "https://a.example/x": (200, {"content-type": "text/html"}, b"<p>hi</p>")})
    if allowed:
        assert F.fetch("https://a.example/x")["text"] == "hi"
    else:
        with pytest.raises(F.Blocked) as e:
            F.fetch("https://a.example/x")
        assert e.value.reason == "robots" and "https://a.example/x" not in n.calls


@pytest.mark.parametrize("status, body, reason", [
    (403, b"Forbidden", "status"),
    (429, b"slow down", "status"),
    (200, b"<html><title>Just a moment...</title><div id='cf-chl-widget'></div></html>", "bot"),
    (200, b"<html><body>Please complete the CAPTCHA to continue</body></html>", "bot"),
    (200, b"<html><title>Pardon Our Interruption</title></html>", "bot"),
    (200, b"<html><body><form><input name=u><input type=password name=p></form>Sign in</body></html>", "login"),
])
def test_blocked_pages_stop_and_are_not_retried(net, status, body, reason):
    n = net({"https://b.example/robots.txt": ROBOTS_OK, "https://b.example/item": (status, {"content-type": "text/html"}, body)})
    with pytest.raises(F.Blocked) as e:
        F.fetch("https://b.example/item")
    assert e.value.reason == reason
    with pytest.raises(F.Blocked):                       # the host is not asked again (no retry loop)
        F.fetch("https://b.example/other")
    assert n.calls == ["https://b.example/robots.txt", "https://b.example/item"]


def test_never_fetched(net):
    n = net({})
    for url in ("https://www.coles.com.au/product/x-1234567", "https://www.woolworths.com.au/shop/productdetails/1/x"):
        with pytest.raises(F.Blocked) as e:              # known bot-protected stores: not even robots.txt is requested
            F.fetch(url)
        assert e.value.reason == "no_fetch_host"
    for url in ("http://intranet.example/", "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data",
                "file:///etc/passwd", "https://u:p@shop.example/", "https://shop.example:8443/", "javascript:alert(1)"):
        with pytest.raises(F.FetchError):
            F.fetch(url)
    assert n.calls == []


def test_redirects_are_checked_again(net):
    n = net({"https://c.example/robots.txt": ROBOTS_OK,
             "https://c.example/go": (302, {"location": "http://intranet.example/admin"}, b""),
             "https://c.example/coles": (301, {"location": "https://www.coles.com.au/product/x-1"}, b""),
             "https://c.example/pdf": (200, {"content-type": "application/pdf"}, b"%PDF")})
    with pytest.raises(F.FetchError):                   # a redirect into the private network
        F.fetch("https://c.example/go")
    with pytest.raises(F.Blocked):                      # a redirect to a bot-protected store
        F.fetch("https://c.example/coles")
    with pytest.raises(F.FetchError):                   # not a web page
        F.fetch("https://c.example/pdf")
    assert not any("//intranet.example" in c or "//www.coles.com.au" in c for c in n.calls)


# ---------------------------------------------------------------- search providers
def test_search_providers(monkeypatch, tmp_path):
    monkeypatch.setenv("BODY_WEB_SEARCH_API_KEY_FILE", str(tmp_path / "missing"))
    assert not S.configured()                            # no key: web_search is not offered
    (tmp_path / "k").write_text("sk-search-test\n")
    monkeypatch.setenv("BODY_WEB_SEARCH_API_KEY_FILE", str(tmp_path / "k"))
    seen = []
    replies = {"brave": {"web": {"results": [{"title": "Yoghurt", "url": "https://x.example/y", "description": "  $6.50\n 1kg"}]}},
               "tavily": {"results": [{"title": "Yoghurt", "url": "https://x.example/y", "content": "$6.50 1kg"}]},
               "serper": {"organic": [{"title": "Yoghurt", "link": "https://x.example/y", "snippet": "$6.50 1kg"}]}}
    for name in ("brave", "tavily", "serper"):
        monkeypatch.setenv(WP.PROVIDER_ENV, name)
        monkeypatch.setattr(S, "_http", lambda m, u, h, b, name=name: seen.append((m, u, h, b)) or replies[name])
        p, results = S.search("greek yoghurt 1kg", "AU")
        assert p.name == name and results == [{"title": "Yoghurt", "url": "https://x.example/y", "snippet": "$6.50 1kg"}]
    (bm, bu, bh, _), (tm, tu, th, tb), (sm, su, sh, sb) = seen
    assert bm == "GET" and bu.startswith("https://api.search.brave.com/res/v1/web/search?") and "country=AU" in bu
    assert bh["X-Subscription-Token"] == "sk-search-test"
    assert tm == "POST" and tu == "https://api.tavily.com/search" and th["Authorization"] == "Bearer sk-search-test"
    assert tb == {"query": "greek yoghurt 1kg", "max_results": WP.SEARCH_RESULTS, "search_depth": "basic"}
    assert sm == "POST" and su == "https://google.serper.dev/search" and sh["X-API-KEY"] == "sk-search-test" and sb["gl"] == "au"
    monkeypatch.setenv(WP.PROVIDER_ENV, "nope")
    assert not S.configured()
    assert S.country_for("Australia/Sydney") == "AU" and S.country_for("Asia/Shanghai") == "CN" and S.country_for(None) is None


def test_user_agent_is_honest(monkeypatch):
    monkeypatch.delenv("BODY_PUBLIC_URL", raising=False)
    assert WP.user_agent().startswith("Watch2WhoopBot/1.0 (") and "Mozilla" not in WP.user_agent()
    monkeypatch.setenv("BODY_PUBLIC_URL", "https://body.example/")
    assert "+https://body.example;" in WP.user_agent()


def test_tool_definitions_are_short():
    assert toolsets.definition_chars([web.SEARCH_TOOL, web.FETCH_TOOL]) <= 700
    assert {web.SEARCH_TOOL.group, web.FETCH_TOOL.group} == {"grocery"}
    assert not web.SEARCH_TOOL.writes and not web.FETCH_TOOL.writes
    from body_engine.assistant.tools import TOOLS_BY_NAME
    assert not set(web.NAMES) & set(TOOLS_BY_NAME)       # never in the MCP tools/list: clients there browse themselves


# ---------------------------------------------------------------- chat (database)
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


@pytest.fixture(scope="module")
def world(dsn, tmp_path_factory):
    d = tmp_path_factory.mktemp("web")
    mp = pytest.MonkeyPatch()
    mp.setenv("BODY_AI_MASTER_KEY_FILE", str(d / "ai_master_key"))
    crypto.init_master_key()
    (d / "deepseek_api_key").write_text("test-fallback-key-not-real\n")
    mp.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(d / "deepseek_api_key"))
    (d / "web_search_api_key").write_text("test-search-key-not-real\n")
    mp.setenv("BODY_WEB_SEARCH_API_KEY_FILE", str(d / "web_search_api_key"))
    mp.setenv(WP.PROVIDER_ENV, "brave")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {}
        for n in ("F", "C", "A"):   # fallback / openai_compatible / anthropic_api
            ids[n] = conn.execute("""INSERT INTO users (display_name, role, consent_at, timezone)
                                     VALUES (%s, 'member', now(), 'Australia/Sydney') RETURNING id""", (n,)).fetchone()["id"]
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        providers.create_provider(conn, ids["C"], {"kind": "openai_compatible", "api_key": "sk-compat-test-1234",
                                                   "model": "qwen-x", "base_url": "https://api.example.com/v1"})
        providers.create_provider(conn, ids["A"], {"kind": "anthropic_api", "api_key": "sk-ant-test-123456"})
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    with TestClient(api.app, base_url="https://testserver") as c:
        v = c.get("/v1/users/me/ai-fallback", headers=H(tok["F"])).json()["consent_version"]
        assert c.post("/v1/users/me/ai-fallback/consent", headers=H(tok["F"]), json={"version": v, "agree": True}).status_code == 200
        yield c, ids, tok
    mp.undo()


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    for kind in ("anthropic_api", "openai_api", "gemini_api", "openai_compatible", "system_fallback"):
        monkeypatch.setitem(adapters.ADAPTERS, kind, lambda: FakeAdapter)
    FakeAdapter.script, FakeAdapter.calls = [], []
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 50.0)
    F._robots.clear()
    F._blocked.clear()


def H(token):
    return {"Authorization": f"Bearer {token}"}


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def set_web(dsn, state):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, WP.FLAG, state, actor="test")


def chat(client, tok, msg="帮我查一下酸奶多少钱", page="food.shopping"):
    r = client.post("/v1/assistant/chat", headers=H(tok), json={"message": msg, "page": page})
    assert r.status_code == 200, r.text
    return sse(r.text)


def results_of(call):
    return {b["id"]: b for b in call["history"][-1]["content"]}


def clean(dsn, uid):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        c.execute("DELETE FROM ai_usage WHERE user_id = %s", (uid,))
        c.execute("DELETE FROM ai_fallback_quota WHERE user_id = %s", (uid,))


def test_flag_off_means_no_web_tools(world, dsn):
    client, ids, tok = world
    set_web(dsn, "off")
    for who in ("F", "C"):
        chat(client, tok[who])
        sent = FakeAdapter.calls[-1]["tools"]
        assert not set(web.NAMES) & set(sent)
    assert "save_product" not in FakeAdapter.calls[0]["tools"]          # nor the fallback's extra grocery tools


def test_flag_on_gives_the_right_users_web_tools(world, dsn, monkeypatch):
    client, ids, tok = world
    set_web(dsn, "all")
    try:
        for who, expect in (("F", ["web_search", "web_fetch"]), ("C", ["web_search", "web_fetch"]), ("A", [])):
            chat(client, tok[who])
            sent = FakeAdapter.calls[-1]["tools"]
            assert [t for t in sent if t in web.NAMES] == expect, who
        f_sent = FakeAdapter.calls[0]["tools"]
        assert f_sent[-2:] == ["web_search", "web_fetch"] and "save_product" in f_sent and "record_store_prices" in f_sent
        monkeypatch.setenv("BODY_WEB_SEARCH_API_KEY_FILE", "/nonexistent/key")   # no search key: only web_fetch, no error
        chat(client, tok["F"])
        assert [t for t in FakeAdapter.calls[-1]["tools"] if t in web.NAMES] == ["web_fetch"]
    finally:
        set_web(dsn, "off")


def test_search_is_billed_against_the_fallback_quota(world, dsn, monkeypatch):
    client, ids, tok = world
    clean(dsn, ids["F"])
    set_web(dsn, "all")
    asked = []
    monkeypatch.setattr(S, "_http", lambda m, u, h, b: asked.append(u) or
                        {"web": {"results": [{"title": "Greek Yoghurt 1kg", "url": "https://x.example/y", "description": "$6.50"}]}})
    try:
        FakeAdapter.script = [{"tool_calls": [{"id": "s1", "name": "web_search", "args": {"query": "greek yoghurt 1kg price"}}]},
                              {"text": "6.5 块"}]
        evs = chat(client, tok["F"])
        assert ("tool", {"name": "web_search", "status": "done"}) in evs
        res = results_of(FakeAdapter.calls[1])["s1"]
        assert not res["is_error"] and "https://x.example/y" in res["content"] and len(asked) == 1
        assert "country=AU" in asked[0]
        with psycopg.connect(dsn, row_factory=dict_row) as c:
            row = c.execute("SELECT * FROM ai_usage WHERE user_id = %s AND purpose = 'web_search'", (ids["F"],)).fetchone()
            assert row["model"] == "brave" and float(row["cost_usd"]) == 0
            assert float(row["cost_cny"]) == pytest.approx(WP.PRICE_USD["brave"] * WP.USD_CNY)
            assert row["conversation_id"] is not None
            model_cny = float(c.execute("""SELECT sum(cost_cny) AS s FROM ai_usage WHERE user_id = %s AND purpose = 'chat'""",
                                        (ids["F"],)).fetchone()["s"])
            assert fallback.month_spent_cny(c, ids["F"]) == pytest.approx(model_cny + float(row["cost_cny"]))
            assert fallback._calls_since(c, ids["F"], datetime(2000, 1, 1, tzinfo=timezone.utc)) == 2   # model calls only
            audit = c.execute("SELECT tool, ok FROM assistant_audit WHERE user_id = %s AND tool = 'web_search'",
                              (ids["F"],)).fetchall()
            assert audit == [{"tool": "web_search", "ok": True}]

        # quota: what is left cannot pay for one more search -> refused, nothing asked, nothing billed
        with psycopg.connect(dsn, row_factory=dict_row) as c:
            spent = fallback.month_spent_cny(c, ids["F"])
            fallback.set_quota(c, ids["F"], round(spent + web.search_cost_cny("brave") / 2, 4))
            with pytest.raises(PermissionError, match="额度不够"):
                web.run(c, ids["F"], fallback.provider(), None, "web_search", {"query": "milk 2L"})
            assert len(asked) == 1 and fallback.month_spent_cny(c, ids["F"]) == pytest.approx(spent)
    finally:
        set_web(dsn, "off")


def test_daily_limits(world, dsn, monkeypatch):
    client, ids, tok = world
    clean(dsn, ids["C"])
    set_web(dsn, "all")
    monkeypatch.setattr(S, "_http", lambda *a: pytest.fail("the search API must not be called over the limit"))
    monkeypatch.setattr(F, "_request", lambda *a: pytest.fail("no page must be read over the limit"))
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as c:
            for purpose, n in (("web_search", WP.SEARCHES_PER_DAY), ("web_fetch", WP.FETCHES_PER_DAY)):
                c.execute("""INSERT INTO ai_usage (user_id, kind, model, cost_cny, purpose)
                             SELECT %s, 'openai_compatible', 'x', 0, %s FROM generate_series(1, %s)""", (ids["C"], purpose, n))
        FakeAdapter.script = [{"tool_calls": [{"id": "s1", "name": "web_search", "args": {"query": "eggs"}},
                                              {"id": "f1", "name": "web_fetch", "args": {"url": "https://shop.example/eggs"}}]},
                              {"text": "好"}]
        chat(client, tok["C"])
        res = results_of(FakeAdapter.calls[-1])
        assert res["s1"]["is_error"] and "搜索次数用完" in res["s1"]["content"]
        assert res["f1"]["is_error"] and "读网页的次数用完" in res["f1"]["content"]
        with psycopg.connect(dsn, row_factory=dict_row) as c:   # the compat user's own USD budget is untouched
            assert providers.month_cost(c, ids["C"]) == pytest.approx(
                sum(float(r["cost_usd"]) for r in c.execute("SELECT cost_usd FROM ai_usage WHERE user_id = %s AND purpose = 'chat'",
                                                            (ids["C"],))))
    finally:
        set_web(dsn, "off")


def test_blocked_page_tells_the_model_to_ask_for_a_screenshot(world, dsn, monkeypatch):
    client, ids, tok = world
    clean(dsn, ids["F"])
    set_web(dsn, "all")
    monkeypatch.setattr(F, "_resolve", lambda host: [PUBLIC_IP])
    pages = {"https://shop.example/robots.txt": ROBOTS_OK,
             "https://shop.example/yoghurt": (200, {"content-type": "text/html"}, b"<title>Attention Required! | Cloudflare</title>"),
             "https://ok.example/robots.txt": ROBOTS_OK,
             "https://ok.example/yoghurt": (200, {"content-type": "text/html"}, PRODUCT)}
    monkeypatch.setattr(F, "_request", lambda s, h, ip, p, limit: pages.get(f"{s}://{h}{p}", (404, {}, b"")))
    try:
        FakeAdapter.script = [{"tool_calls": [{"id": "f1", "name": "web_fetch", "args": {"url": "https://shop.example/yoghurt"}},
                                              {"id": "f2", "name": "web_fetch", "args": {"url": "https://www.coles.com.au/product/y-1"}},
                                              {"id": "f3", "name": "web_fetch", "args": {"url": "https://ok.example/yoghurt"}},
                                              {"id": "f4", "name": "web_fetch", "args": {"url": "http://127.0.0.1/admin"}}]},
                              {"text": "好"}]
        chat(client, tok["F"])
        res = results_of(FakeAdapter.calls[-1])
        for i in ("f1", "f2"):
            assert not res[i]["is_error"] and web.TELL_USER in res[i]["content"] and "blocked" in res[i]["content"], res[i]
        assert "Greek Yoghurt" in res["f3"]["content"] and "store_only" in res["f3"]["content"]
        assert res["f4"]["is_error"] and "内网" in res["f4"]["content"]
        with psycopg.connect(dsn, row_factory=dict_row) as c:   # page reads are counted (no retry loops) but cost nothing
            rows = c.execute("SELECT cost_cny FROM ai_usage WHERE user_id = %s AND purpose = 'web_fetch'", (ids["F"],)).fetchall()
            assert len(rows) == 4 and all(float(r["cost_cny"]) == 0 for r in rows)   # a failed read counts too
    finally:
        set_web(dsn, "off")


def test_fallback_can_plan_shopping_only_with_the_flag(world, dsn):
    client, ids, tok = world
    clean(dsn, ids["F"])
    for state, can in (("off", False), ("all", True)):
        set_web(dsn, state)
        FakeAdapter.script = [{"tool_calls": [{"id": "p1", "name": "get_playbook", "args": {"name": "plan_shopping"}}]},
                              {"text": "好"}]
        chat(client, tok["F"])
        content = results_of(FakeAdapter.calls[-1])["p1"]["content"]
        assert ("get_shopping_context" in content) is can and ("需要用户自己的 AI" in content) is not can
    set_web(dsn, "off")
    assert fp.MODEL   # the fallback provider stays the same DeepSeek model
