"""Test database speed-up (issue: Body Engine tests took ~7 minutes, much of it migrations).

Every test module creates its own throw-away database (`CREATE DATABASE "<name>"`) and then runs all migrations in it,
about 140 times per run. Here the migrations run ONCE per test run into a template database, and every
`CREATE DATABASE "<name>"` a test issues is rewritten to clone that template. The tests' own `migrate(conn)` call then
finds everything already applied and does nothing, so no test file needs to change.

The template's name carries a hash of the migration files, so a changed migration never meets a stale template, and a
postgres advisory lock makes parallel pytest-xdist workers build it only once. If the template cannot be built
(Postgres unreachable, ...) nothing is patched: the tests behave exactly as before and skip / fail on their own.
"""
import hashlib
import re
import time

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine import db as bdb
from body_engine.config import Settings

_CREATE = re.compile(r'^\s*CREATE DATABASE\s+("[^"]+")\s*;?\s*$', re.IGNORECASE)
_LOCK_KEY = 0x62747465   # advisory lock: "only one builder of the template"


def _template_name() -> str:
    h = hashlib.sha256()
    for f in bdb.migration_files():
        h.update(f.name.encode())
        h.update(f.read_bytes())
    # the DATA_STEPS code is part of the template too; a change there should rebuild it as well
    h.update(repr([(n, needs) for n, needs, _ in bdb.DATA_STEPS]).encode())
    return f"body_tpl_{h.hexdigest()[:12]}"


def _ensure_template(name: str) -> None:
    with psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True) as admin:
        admin.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
            if exists:
                return
            # build under a temporary name, rename when complete: a half-built template is never visible
            tmp = f"{name}_build"
            admin.execute(f'DROP DATABASE IF EXISTS "{tmp}" WITH (FORCE)')
            admin.execute(f'CREATE DATABASE "{tmp}"')
            with psycopg.connect(Settings(db_name=tmp).dsn, row_factory=dict_row) as conn:
                bdb.migrate(conn)
            admin.execute(f'ALTER DATABASE "{tmp}" RENAME TO "{name}"')
        finally:
            admin.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))


def _clone_sql(sql: str, template: str) -> str | None:
    m = _CREATE.match(sql)
    return f'CREATE DATABASE {m.group(1)} TEMPLATE "{template}"' if m else None


def pytest_configure(config):
    try:
        template = _template_name()
        _ensure_template(template)
    except Exception:   # Postgres unreachable etc.: leave the tests alone, they handle it themselves
        return
    original = psycopg.Connection.execute

    def execute(self, query, *args, **kwargs):
        if isinstance(query, str):
            cloned = _clone_sql(query, template)
            if cloned:
                # concurrent clones of one template can collide on "source database is being accessed"; retry briefly
                for attempt in range(20):
                    try:
                        return original(self, cloned, *args, **kwargs)
                    except psycopg.errors.ObjectInUse:
                        time.sleep(0.2 * (attempt + 1))
                return original(self, cloned, *args, **kwargs)
        return original(self, query, *args, **kwargs)

    psycopg.Connection.execute = execute


# --- tests that need a quiet cluster -----------------------------------------------------------------------------
# The sync pull cursors (nativesync/pull.py) only hand out changes older than the cluster-wide oldest running transaction
# (pg_snapshot_xmin), by design: a late commit can never slip behind a cursor. A transaction another xdist worker has open
# in ITS database holds that horizon back, so an incremental pull right after a write can legitimately return nothing.
# Those test files run in a second, serial pytest run (`-m quiet_cluster`, see .github/workflows/ci.yml) after the parallel
# one (`-m "not quiet_cluster"`). They are found by what they touch, so a new test of the same kind is covered
# automatically; a plain `pytest` (no -m) runs everything, in order, as before.
_NEEDS_QUIET = re.compile(r"sync/pull|nativesync|sync/history|pg_current_snapshot|pg_snapshot")
_quiet_cache: dict[str, bool] = {}


def pytest_collection_modifyitems(items):
    for item in items:
        path = str(item.fspath)
        if path not in _quiet_cache:
            _quiet_cache[path] = bool(_NEEDS_QUIET.search(open(path, encoding="utf-8").read()))
        if _quiet_cache[path]:
            item.add_marker(pytest.mark.quiet_cluster)


# --- process-wide state that outlives a test module ---------------------------------------------------------------------
# Every module has its own database, but the process is shared, and ids (user 1, conversation 1) repeat across databases.
# With one process in a fixed order the leftovers were old enough to have expired; with xdist, which modules share a worker
# changes from run to run. Clear them before each module (a module's own tests still build on each other's state).
@pytest.fixture(autouse=True, scope="module")
def _fresh_process_state():
    from body_engine.ai import runs
    with runs._lock:
        runs._runs.clear()   # chat turns kept for resume: GET /v1/assistant/chat/1/events answered with another module's turn
    yield
