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
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.web import session

TEST_DB = "body_test_plugins"
STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"

DEMO = '''
from fastapi import APIRouter, Depends
from body_engine.plugins import Job
from body_engine.records.api import current_user, get_conn

from .helpers import TABLE   # a plugin may be several modules

router = APIRouter()


@router.get("/v1/plugin-demo/ping")
def ping(conn=Depends(get_conn), viewer=Depends(current_user)):
    return {"user": viewer["id"], "rows": conn.execute(f"SELECT count(*) AS n FROM {TABLE}").fetchone()["n"]}


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
