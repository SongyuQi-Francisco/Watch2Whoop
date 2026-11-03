"""Optional server plugins (plugins.py): nothing happens without BODY_PLUGIN_DIR; with it a plugin's migrations, router,
page files (only the ones listed for the signed-in user) and jobs (advisory lock, every-N-seconds and daily schedules).

Uses its own test database (body_test_plugins), dropped and recreated on every run; skipped if Postgres is unreachable.
The plugin is written into a temporary directory by the test.
"""
import re
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, plugins
from body_engine.account import api as account_api
from body_engine.client import params as upgrade_params, policy as upgrade_policy
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.i18n import LangMiddleware
from body_engine.rollout import flags
from body_engine.web import api as web_api, session

TEST_DB = "body_test_plugins"
STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"

DEMO = '''
from fastapi import APIRouter, Depends
from body_engine.plugins import Job
from body_engine.records.api import current_user, get_conn

from .helpers import TABLE   # a plugin may be several modules

router = APIRouter()
LINKS = []       # rows for me_links; a test sets them on the loaded module (an Exception here = the hook raises)


@router.get("/v1/plugin-demo/ping")
def ping(conn=Depends(get_conn), viewer=Depends(current_user)):
    return {"user": viewer["id"], "rows": conn.execute(f"SELECT count(*) AS n FROM {TABLE}").fetchone()["n"]}


@router.get("/v1/web/plugins/demo/ping")
def ping_page(viewer=Depends(current_user)):
    return {"user": viewer["id"]}


def me_links(conn, user):
    if isinstance(LINKS, Exception):
        conn.execute("SELECT * FROM no_such_table")   # a failed query inside the hook
    return LINKS


def tick(conn):
    conn.execute("INSERT INTO plugin_demo_runs DEFAULT VALUES")
    return "ticked"


JOBS = [Job("tick", tick, every_s=300), Job("morning", tick, daily_at="08:00", tz="Australia/Sydney")]


def page_scripts(conn, user):
    return ["demo.js"] + (["owner.js"] if user and user["role"] == "owner" else [])


def page_styles(conn, user):
    return ["demo.css"] if user else []
'''


@pytest.fixture(scope="module")
def dsn():
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # no password file, or the database is not running
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    d = tmp_path / "plugins" / "demo"
    (d / "migrations").mkdir(parents=True)
    (d / "static").mkdir()
    (d / "__init__.py").write_text(textwrap.dedent(DEMO))
    (d / "helpers.py").write_text('TABLE = "plugin_demo_runs"\n')
    (d / "migrations" / "001_demo.sql").write_text("CREATE TABLE IF NOT EXISTS plugin_demo_runs (id SERIAL PRIMARY KEY);")
    for f in ("demo.js", "owner.js", "secret.js", "demo.css"):
        (d / "static" / f).write_text(f"/* {f} */")
    (tmp_path / "plugins" / "Bad-Name").mkdir()
    (tmp_path / "plugins" / "Bad-Name" / "__init__.py").write_text("raise RuntimeError('must not load')")
    monkeypatch.setenv("BODY_PLUGIN_DIR", str(tmp_path / "plugins"))
    plugins.reset()
    yield d
    plugins.reset()


def test_nothing_without_plugin_dir(dsn, monkeypatch):
    monkeypatch.delenv("BODY_PLUGIN_DIR", raising=False)
    plugins.reset()
    assert plugins.load() == ()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert plugins.migrate(conn) == []
    app = FastAPI()
    plugins.install(app)
    c = TestClient(app)
    assert c.get("/app/plugins/manifest.json").json() == {"scripts": [], "styles": []}
    assert c.get("/app/plugins/demo/demo.js").status_code == 404


def test_migrations_router_files(dsn, plugin_dir):
    assert [p.name for p in plugins.load()] == ["demo"]                      # Bad-Name skipped, never imported
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert plugins.migrate(conn) == ["demo/001_demo.sql"]
        assert plugins.migrate(conn) == []                                   # once
        owner = conn.execute("INSERT INTO users (display_name, role) VALUES ('O', 'owner') RETURNING id").fetchone()["id"]
        member = conn.execute("INSERT INTO users (display_name, role) VALUES ('M', 'member') RETURNING id").fetchone()["id"]
        tok = {u: auth.create_token(conn, u, "web", ["read", "write"]) for u in (owner, member)}
        sid = {u: session.login(conn, tok[u])[0] for u in (owner, member)}
    app = FastAPI()
    plugins.install(app)
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=2, open=True)
    try:
        c = TestClient(app, base_url="https://testserver")
        assert c.get("/v1/plugin-demo/ping", headers={"Authorization": f"Bearer {tok[member]}"}).json() == {"user": member, "rows": 0}
        assert c.get("/app/plugins/manifest.json").json() == {"scripts": ["/app/plugins/demo/demo.js"], "styles": []}
        c.cookies.set(session.COOKIE, sid[owner])
        assert c.get("/app/plugins/manifest.json").json() == {
            "scripts": ["/app/plugins/demo/demo.js", "/app/plugins/demo/owner.js"], "styles": ["/app/plugins/demo/demo.css"]}
        r = c.get("/app/plugins/demo/owner.js")
        assert r.status_code == 200 and r.text == "/* owner.js */" and r.headers["content-type"].startswith("text/javascript")
        for path in ("/app/plugins/demo/secret.js", "/app/plugins/demo/../__init__.py", "/app/plugins/other/demo.js"):
            assert c.get(path).status_code == 404, path                     # only listed files of a loaded plugin
        c.cookies.set(session.COOKIE, sid[member])
        assert c.get("/app/plugins/demo/owner.js").status_code == 404        # listed for the owner only
    finally:
        api.pool.close()


def test_jobs_run_with_lock_and_schedule(dsn, plugin_dir):
    p = plugins.load()[0]
    tick = next(j for j in p.jobs if j.name == "tick")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        plugins.migrate(conn)
        assert plugins.run_job(p, tick, conn) == "ticked"
        with psycopg.connect(dsn, row_factory=dict_row) as other:            # another process holds the lock
            other.execute("SELECT pg_advisory_lock(%s)", (plugins._lock_key("demo", "tick"),))
            assert plugins.run_job(p, tick, conn) is None
            other.execute("SELECT pg_advisory_unlock(%s)", (plugins._lock_key("demo", "tick"),))
        assert conn.execute("SELECT count(*) AS n FROM plugin_demo_runs").fetchone()["n"] == 1
    now = datetime(2026, 10, 22, 21, 30, tzinfo=timezone.utc)                # Sydney 23-10 08:30
    assert plugins.due(tick, now, None) and not plugins.due(tick, now, now - timedelta(seconds=299))
    assert plugins.due(tick, now, now - timedelta(seconds=300))
    morning = next(j for j in p.jobs if j.name == "morning")
    assert plugins.due(morning, now, None)
    assert not plugins.due(morning, now, now - timedelta(minutes=20))        # already ran this local morning
    assert plugins.due(morning, now, now - timedelta(hours=23))              # last run was yesterday (Sydney)
    assert not plugins.due(morning, now - timedelta(hours=1), None)          # 07:30 local: not yet


def test_page_hooks_are_inert_without_plugins():
    js = (STATIC / "app.js").read_text()
    assert 'fetch("/app/plugins/manifest.json"' in js and "window.BodyHooks" in js
    assert re.search(r'emit\("page"', js) and re.search(r'emit\("action"', js)
    assert 'if (!HOOKS.has("action")) return;' in js                         # no listener -> nothing computed
    assert 'emit("sheet", { open: true' in js and 'emit("sheet", { open: false }' in js
    assert 'a !== "data-card"' in js                                         # data-card never becomes a control name
    cards = set(re.findall(r'data-card="([a-z_]+)"', js + "".join(f.read_text() for f in STATIC.glob("*.js") if f.name != "app.js")))
    assert {"recovery", "sleep", "nutrition", "training", "healthspan", "reminders", "hr_zones", "sleep_regularity"} <= cards
    assert all(re.fullmatch(r"[a-z][a-z0-9_.:-]{0,59}", c) for c in cards)


# ---------------------------------------------------------------- rows of the native Me page (me_links), web/me_links.py
@pytest.fixture
def links_app(dsn, plugin_dir):
    app = FastAPI()
    app.add_middleware(LangMiddleware)
    plugins.install(app)
    app.include_router(web_api.router)
    app.include_router(account_api.router)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        plugins.migrate(conn)
        uid = conn.execute("""INSERT INTO users (display_name, role, consent_at, timezone)
                              VALUES ('L', 'member', now(), 'Australia/Sydney') RETURNING id""").fetchone()["id"]
        tok = {"rw": auth.create_token(conn, uid, "app", ["read", "write"]), "ro": auth.create_token(conn, uid, "ro", ["read"])}
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=2, open=True)
    yield TestClient(app, base_url="https://testserver"), plugins.load()[0].module, uid, tok
    api.pool.close()


def _bearer(t, **extra):
    return {"Authorization": f"Bearer {t}", **extra}


def _opened(c, tok, key, **headers):
    return c.post(f"/v1/web/me-links/{key}/open", headers=_bearer(tok, **headers))


ROW = {"key": "demo-row", "title": "Row", "url": "/app/plugins/demo/", "login": True}


def test_me_links_absent_without_a_plugin(dsn, monkeypatch):
    monkeypatch.delenv("BODY_PLUGIN_DIR", raising=False)
    plugins.reset()
    app = FastAPI()
    app.include_router(web_api.router)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = conn.execute("INSERT INTO users (display_name, role, consent_at) VALUES ('N', 'member', now()) RETURNING id").fetchone()["id"]
        tok = auth.create_token(conn, uid, "app", ["read", "write"])
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=2, open=True)
    try:
        c = TestClient(app, base_url="https://testserver")
        assert "me_links" not in c.get("/v1/web/me", headers=_bearer(tok)).json()
        assert _opened(c, tok, "demo-row").status_code == 404
    finally:
        api.pool.close()


def test_me_links_listed_and_cleaned(links_app):
    c, mod, _, tok = links_app
    assert "me_links" not in c.get("/v1/web/me", headers=_bearer(tok["rw"])).json()       # the plugin has none: no key at all
    mod.LINKS = [ROW, {**ROW, "title": "A second row with the same key"},
                 {"key": "out", "title": "Out", "url": "https://example.com/page", "login": True},
                 {"key": "Bad Key", "title": "x", "url": "/app/"}, {"key": "no-title", "title": " ", "url": "/app/"},
                 {"key": "long", "title": "x" * 41, "url": "/app/"}, {"key": "js", "title": "x", "url": "javascript:alert(1)"},
                 {"key": "http", "title": "x", "url": "http://example.com/"}, {"key": "proto", "title": "x", "url": "//example.com/"},
                 {"key": "frag", "title": "x", "url": "/app/#login=x"}, "not a row"]
    assert c.get("/v1/web/me", headers=_bearer(tok["rw"])).json()["me_links"] == [
        {"key": "demo-row", "title": "Row", "url": "/app/plugins/demo/"},
        {"key": "out", "title": "Out", "url": "https://example.com/page"}]                # never `login`, and only valid rows
    assert _opened(c, tok["rw"], "out").json() == {"url": "https://example.com/page"}     # no code to another origin
    assert _opened(c, tok["rw"], "js").status_code == 404


def test_me_links_a_failing_plugin_does_not_break_the_page(links_app):
    c, mod, _, tok = links_app
    mod.LINKS = RuntimeError("broken")
    r = c.get("/v1/web/me", headers=_bearer(tok["rw"]))
    assert r.status_code == 200 and "me_links" not in r.json() and r.json()["me"]["display_name"] == "L"


def test_me_links_only_for_active_accounts(links_app, dsn):
    c, mod, uid, tok = links_app
    mod.LINKS = [ROW]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert plugins.me_links(conn, {"id": uid}) and web_api.me_links.for_me(conn, {"id": uid, "status": "pending"}) == []


def test_me_link_open_signs_the_browser_in_once(links_app, dsn):
    c, mod, uid, tok = links_app
    mod.LINKS = [ROW]
    assert _opened(c, tok["rw"], "nope").status_code == 404
    url = _opened(c, tok["rw"], "demo-row", **{"X-Body-Lang": "en"}).json()["url"]
    assert url.startswith("/app/plugins/demo/#login=") and url.endswith("&lang=en")      # in the fragment: never reaches a log
    code = url.split("#login=")[1].split("&")[0]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("""SELECT scopes, label, expires_at - created_at <= interval '10 minutes' AS short
                              FROM web_login_codes WHERE user_id = %s""", (uid,)).fetchone()
    assert row == {"scopes": ["read", "write"], "label": "link:demo-row", "short": True}
    browser = TestClient(c.app, base_url="https://testserver")
    assert browser.post("/v1/web/login-code", json={"code": code}).status_code == 200    # one time only
    assert browser.get("/v1/web/me").json()["me"]["id"] == uid
    assert TestClient(c.app, base_url="https://testserver").post("/v1/web/login-code", json={"code": code}).status_code == 401
    # opening again: the unused code and the previous login are gone, one browser login per row
    second = _opened(c, tok["rw"], "demo-row").json()["url"].split("#login=")[1].split("&")[0]
    assert browser.get("/v1/web/me").status_code == 401
    third = _opened(c, tok["rw"], "demo-row").json()["url"].split("#login=")[1].split("&")[0]
    assert TestClient(c.app, base_url="https://testserver").post("/v1/web/login-code", json={"code": second}).status_code == 401
    assert browser.post("/v1/web/login-code", json={"code": third}).status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("""SELECT count(*) AS n FROM api_tokens WHERE user_id = %s AND name = 'app:link:demo-row'
                               AND revoked_at IS NULL""", (uid,)).fetchone()["n"] == 1
    # the app signs out: the browser login it opened ends too
    assert c.post("/v1/account/logout", headers=_bearer(tok["rw"])).status_code == 204
    assert browser.get("/v1/web/me").status_code == 401


def test_me_link_open_never_escalates(links_app, dsn):
    c, mod, uid, tok = links_app
    mod.LINKS = [ROW]
    assert _opened(c, tok["ro"], "demo-row").json() == {"url": "/app/plugins/demo/"}     # read-only token: plain address
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        sid = session.login(conn, tok["rw"])[0]
    browser = TestClient(c.app, base_url="https://testserver", cookies={session.COOKIE: sid})
    assert browser.post("/v1/web/me-links/demo-row/open").status_code == 403             # a cookie write needs X-Body-Web
    r = browser.post("/v1/web/me-links/demo-row/open", headers={"X-Body-Web": "1"})
    assert r.json() == {"url": "/app/plugins/demo/"}                                     # a browser session never mints a code
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM web_login_codes WHERE user_id = %s", (uid,)).fetchone()["n"] == 0


def test_plugin_page_endpoints_stay_open_after_the_upgrade_block(links_app, dsn):
    c, mod, uid, tok = links_app
    mod.LINKS = [ROW]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        sid = session.login(conn, tok["rw"])[0]
        upgrade_policy.update(conn, block_from="2020-01-01", actor="pytest")
        flags.set_flag(conn, upgrade_params.FLAG, "all", actor="pytest")
    upgrade_policy.invalidate()
    browser = TestClient(c.app, base_url="https://testserver", cookies={session.COOKIE: sid})
    try:
        assert browser.get("/v1/plugin-demo/ping").status_code == 426                    # a plugin's ordinary endpoint: blocked
        assert browser.get("/v1/web/plugins/demo/ping").json() == {"user": uid}          # its own web page's endpoint: not
        assert browser.get("/v1/web/me").json()["upgrade"]["state"] == "block"
        assert _opened(c, tok["rw"], "demo-row", **{"X-App-Client": "ios-native", "X-App-Version": "1.0.0"}).status_code == 200
    finally:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            flags.set_flag(conn, upgrade_params.FLAG, "off", actor="pytest")
            upgrade_policy.update(conn, block_from="none", actor="pytest")
        upgrade_policy.invalidate()
