"""Start-up bundle POST /v1/web/boot (web/boot.py, flag boot_api, docs/ios-native-plan.md 3.2) and the page side in app.js.

Every item must equal what the same GET answers on its own (status and body), one failing item must leave the others,
only the signed-in user's own GET paths are answered. Separate test database BODY_TEST_DB_NAME (default body_test_boot);
skipped without Postgres.
"""
import json
import os
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.web import boot

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_boot")
STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- pure parts
def test_resolve_templates_and_allowlist():
    d = date(2026, 10, 25)
    assert boot.resolve("/v1/users/me/strain-coach?date={today}", d) == "/v1/users/me/strain-coach?date=2026-10-25"
    assert boot.resolve("/v1/users/me/events?start={d:-364}&end={today}", d) == "/v1/users/me/events?start=2025-10-26&end=2026-10-25"
    assert boot.resolve("/v1/web/me", d) == "/v1/web/me"
    assert boot.resolve("/v1/account", d) == "/v1/account"
    for bad in ("/v1/users/2/today", "/v1/users/me/../2/today", "/v1/web/boot", "/v1/web/logout", "/app/index.html",
                "https://x/v1/users/me/today", "/v1/users/me/today#x", "/v1/users/me/today?x=<b>", "/v1/admin/users"):
        assert boot.resolve(bad, d) is None, bad


# ---------------------------------------------------------------- endpoint
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "") or not TEST_DB.startswith("body_test_"):
        pytest.exit("the test DB must be a body_test_* database")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture(scope="module")
def users(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        def add(name):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                                   VALUES (%s, 'member', 'Australia/Sydney', now(), '2026-10-22.2') RETURNING id""",
                                (name,)).fetchone()["id"]
        ids = {"a": add("a"), "b": add("b")}
        conn.commit()
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in ids.items()}
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def set_flag(dsn, state):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, boot.FEATURE, state, actor="test")
        conn.commit()
    flags.invalidate()


def H(t):
    return {"Authorization": f"Bearer {t}"}


PATHS = ["/v1/auth/methods", "/v1/users/me/today", "/v1/users/me/goals/sleep", "/v1/users/me/profile",
         "/v1/users/me/training/day/{today}", "/v1/users/me/journal?start={d:-1}&end={today}", "/v1/users/me/health-history"]


def test_off_by_default_and_needs_login(client, users, dsn):
    _, tok = users
    set_flag(dsn, "off")
    assert client.post("/v1/web/boot", json={"paths": PATHS}, headers=H(tok["a"])).status_code == 404
    r = client.post("/v1/web/boot", json={"paths": PATHS})
    assert r.status_code == 401 and r.json()["detail"]
    assert client.post("/v1/web/boot", json={"paths": ["/x"] * 30}, headers=H(tok["a"])).status_code == 422


def test_items_equal_the_endpoints_on_their_own(client, users, dsn):
    ids, tok = users
    set_flag(dsn, "all")
    r = client.post("/v1/web/boot", json={"paths": PATHS + ["/v1/users/me/today"]}, headers=H(tok["a"]))
    assert r.status_code == 200, r.text
    assert r.headers["x-body-user"] == str(ids["a"])   # the bundle names its user although only its items authenticate
    out = r.json()
    today = out["today"]
    items = {it["path"]: it for it in out["items"]}
    assert list(items)[0] == "/v1/web/me"
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    want = ["/v1/web/me", "/v1/auth/methods", "/v1/users/me/today", "/v1/users/me/goals/sleep", "/v1/users/me/profile",
            "/v1/users/me/health-history", f"/v1/users/me/training/day/{today}",
            f"/v1/users/me/journal?start={yesterday}&end={today}"]
    assert sorted(items) == sorted(want)
    volatile = {"generated_at"}
    for path, it in items.items():
        alone = client.get(path, headers=H(tok["a"]))
        assert it["status"] == alone.status_code, path
        a, b = it["body"], alone.json()
        if isinstance(a, dict):
            a, b = {k: v for k, v in a.items() if k not in volatile}, {k: v for k, v in b.items() if k not in volatile}
        assert a == b, path
    assert items["/v1/web/me"]["body"]["me"]["id"] == ids["a"]


def test_only_own_paths_are_answered(client, users, dsn):
    ids, tok = users
    set_flag(dsn, "all")
    r = client.post("/v1/web/boot", headers=H(tok["b"]), json={"paths": [
        f"/v1/users/{ids['a']}/today", "/v1/users/me/../1/today", "/v1/web/logout", "/v1/users/me/no-such-thing",
        "/v1/users/me/today"]})
    assert r.status_code == 200
    items = {it["path"]: it for it in r.json()["items"]}
    assert set(items) == {"/v1/web/me", "/v1/users/me/no-such-thing", "/v1/users/me/today"}   # a's data never asked for
    assert items["/v1/users/me/no-such-thing"]["status"] == 404
    assert items["/v1/users/me/today"]["status"] == 200
    assert items["/v1/web/me"]["body"]["me"]["id"] == ids["b"]


def test_item_error_is_isolated(client, users, dsn, monkeypatch):
    _, tok = users
    set_flag(dsn, "all")
    from body_engine.web import views

    def boom(*a, **k):
        raise RuntimeError("today broke")
    monkeypatch.setattr(views, "today", boom)
    r = TestClient(api.app, base_url="https://testserver", raise_server_exceptions=False).post(
        "/v1/web/boot", headers=H(tok["a"]), json={"paths": ["/v1/users/me/today", "/v1/users/me/profile"]})
    assert r.status_code == 200
    items = {it["path"]: it for it in r.json()["items"]}
    assert items["/v1/users/me/today"]["status"] == 500
    assert items["/v1/users/me/profile"]["status"] == 200


def test_cookie_login_renews_cookie(client, users, dsn):
    _, tok = users
    set_flag(dsn, "all")
    c = TestClient(api.app, base_url="https://testserver")
    assert c.post("/v1/web/login", json={"token": tok["a"]}).status_code == 200
    r = c.post("/v1/web/boot", json={"paths": ["/v1/users/me/profile"]}, headers={"X-Body-Web": "1"})
    assert r.status_code == 200
    assert "set-cookie" in r.headers   # /v1/web/me renews the session cookie, also through the bundle
    assert {it["path"] for it in r.json()["items"]} == {"/v1/web/me", "/v1/users/me/profile"}


# ---------------------------------------------------------------- optimistic writes: the answer carries the nutrition block
def test_meal_write_answer_carries_today_nutrition(users, dsn, monkeypatch):
    from body_engine.assistant import memory_api
    from body_engine.rollout import flags as fl
    from body_engine.web import views
    ids, _ = users
    monkeypatch.setattr(views, "nutrition_today", lambda conn, owner, day: {"date": day, "logged": True, "intake": {"calories": 330}})
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for state, has in (("off", False), ("all", True)):
            fl.set_flag(conn, "optimistic_writes", state, actor="test")
            conn.commit()
            fl.invalidate()
            out = memory_api._with_today(conn, ids["a"], {"log_id": 1, "date": "2026-10-25"})
            assert ("today" in out) is has
            if has:
                assert out["today"]["nutrition"]["intake"]["calories"] == 330
        assert "today" not in memory_api._with_today(conn, ids["a"], {"deleted": 0})   # no date: nothing to add


# ---------------------------------------------------------------- page side (app.js / offline.js / water.js)
WATER = (STATIC / "water.js").read_text(encoding="utf-8")


def run_pre(body: str):
    """Run app.js's prefetch / start-up bundle block in node with stubs (skipped without node)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    start = APP.index("  // ---- prefetched GETs")
    src = APP[start:APP.index("  // ---- tab switches", start)]
    js = """
      const S = { todayDate: "2026-10-25", me: { id: 1 }, owner: 1, flags: ["boot_api"] };
      const I18N = { headers: () => ({ "X-Body-Lang": "zh" }) };
      const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
      const isoDay = (dt) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
      const addDays = (s, n) => { const d = parseDay(s); d.setDate(d.getDate() + n); return isoDay(d); };
      const isPast = () => false;
      const window = {}; let tabRec = null;
      const store = {}, localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; }, removeItem: (k) => { delete store[k]; } };
      const calls = []; let answer = null;
      const fetch = async (url, init) => { calls.push({ url, body: init?.body ? JSON.parse(init.body) : null }); return answer(url, init); };
      const json = (status, data) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
    """ + src + "\n(async () => {\n" + body + "\n})().then((v) => console.log(JSON.stringify(v)), (e) => { console.error(e); process.exit(1); });"
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_bundle_answers_are_used_once_and_failed_items_are_asked_again():
    got = run_pre("""
      answer = () => json(200, { today: "2026-10-25", items: [
        { path: "/v1/web/me", status: 200, body: { me: { id: 1 } } },
        { path: "/v1/users/me/today", status: 200, body: { date: "2026-10-25" } },
        { path: "/v1/users/me/stress", status: 403, body: { detail: "no share" } },
        { path: "/v1/users/me/sleep-coach", status: 500, body: null } ] });
      await fetchBoot(["/v1/users/me/today", "/v1/users/me/stress", "/v1/users/me/sleep-coach"]);
      const r = {};
      r.sent = calls[0].body.paths;
      const a = await preTake("/v1/users/me/today");
      r.first = [a.status, await a.json()];
      r.again = preTake("/v1/users/me/today");          // used once
      r.forbidden = (await preTake("/v1/users/me/stress")).status;   // a 4xx is the answer, as the endpoint gives it
      r.failed = preTake("/v1/users/me/sleep-coach");     // a 5xx item is not kept: the page asks for it on its own
      PRE.set("/x", { at: Date.now() - 61000, ttl: BOOT_TTL, res: Promise.resolve(json(200, {})) });
      r.expired = preTake("/x");
      return r;""")
    assert got == {"sent": ["/v1/users/me/today", "/v1/users/me/stress", "/v1/users/me/sleep-coach"],
                   "first": [200, {"date": "2026-10-25"}], "again": None, "forbidden": 403, "failed": None, "expired": None}


def test_bundle_signed_out_offline_and_flag_off():
    got = run_pre("""
      const r = {};
      answer = () => json(401, { detail: "登录已过期" });
      await fetchBoot(["/v1/users/me/today"]);
      const me = await preTake("/v1/web/me");
      r.signedOut = [me.status, (await me.json()).detail, PRE.size];   // the login screen without a second 401
      answer = () => json(404, { detail: "Not Found" });
      await fetchBoot(["/v1/users/me/today"]);
      r.flagOff = PRE.size;
      answer = () => { throw new TypeError("Load failed"); };
      await fetchBoot(["/v1/users/me/today"]);
      r.offline = PRE.size;
      return r;""")
    assert got == {"signedOut": [401, "登录已过期", 0], "flagOff": 0, "offline": 0}


def test_start_up_paths_are_recorded_relative_to_today():
    got = run_pre("""
      rec = { tab: "today", paths: new Set() };
      for (const p of ["/v1/web/me", "/v1/users/me/stress?start=2026-10-25&end=2026-10-25", "/v1/users/me/events?start=2025-10-26&end=2026-10-25",
                       "/v1/users/7/today", "/v1/assistant/chat", "/v1/friends", "/v1/users/me/old?d=2024-01-01"]) noteGet(p);
      endRec();
      const saved = JSON.parse(store["be.fast"]).boot.today;
      S.todayDate = "2026-10-26";   // the next day: the same templates ask for that day
      return { saved, next: saved.map(fromTemplate) };""")
    assert got["saved"] == ["/v1/web/me", "/v1/users/me/stress?start={today}&end={today}",
                            "/v1/users/me/events?start={d:-364}&end={today}", "/v1/friends"]   # others' data never; > 400 days dropped
    assert got["next"][1] == "/v1/users/me/stress?start=2026-10-26&end=2026-10-26"
    assert got["next"][2] == "/v1/users/me/events?start=2025-10-27&end=2026-10-26"


def test_page_wiring():
    # the flags of the last start decide before /v1/web/me answers; a flag turned off takes effect one start later
    assert 'flagWas("boot_api") && paths?.length ? fetchBoot(paths)' in APP and 'flagWas("cached_first_paint")' in APP
    assert "await (S.stale ? prePeek(path) : preTake(path))" in APP   # api() GETs use the bundle / prefetched answers
    assert "PRE.clear();   // an A/B exposure" in APP                 # a write drops every prefetched answer
    # logout and a 401 forget the start-up paths and the prefetched answers; logout also wipes the offline copies
    logout = APP[APP.index("async function logout()"):APP.index("// ------------------------------------------------------------------ page switching")]
    assert "localStorage.removeItem(FAST)" in logout and "PRE.clear()" in logout and "BodyOffline?.clear()" in logout
    assert "if (r.status === 401) { PRE.clear(); try { localStorage.removeItem(FAST)" in APP


def test_cached_first_paint_only_own_fresh_copies():
    paint = APP[APP.index("async function paintCached("):APP.index("// Jarvis' card buttons")]
    for cond in ("off.user() !== fast.user", "me.me.id !== fast.user", "STALE_MAX", "zoneToday(me.me.timezone)",
                 'includes("offline_mode")', "login="):
        assert cond in paint, cond
    assert "S.data = {}" in paint   # nothing from the copies stays in memory
    assert 'tr("更新中 · 数据更新于 {when}"' in APP


def test_tab_prefetch_is_capped_and_respects_the_network():
    assert "PREFETCH_MAX = 30" in APP
    net = APP[APP.index("function prefetchNetOk()"):APP.index("function idlePrefetch()")]
    for cond in ('"cellular"', '"constrained"', "saveData", "2g"):
        assert cond in net, cond


def test_optimistic_writes_roll_back():
    meal = APP[APP.index("async function logMealNow("):APP.index("async function logMeal(t, items, again)")]
    assert "delete S.mealLogs[t.id]" in meal and "day.nutrition = before" in meal   # the failure puts the old numbers back
    assert "res?.today?.nutrition" in meal
    assert "sending -= body.ml" in WATER and 'optimistic_writes' in WATER
