"""New OW uploads -> scores within minutes (sync/ow_watch.py + sync/refresh.run_queued): who is queued when (quiet period,
minimum gap, already imported, busy, consent, not linked), that the queued run imports before it recomputes, and that the
hourly job then skips the user (fingerprints stored).

Uses its own test database (body_test_ow_watch, BODY_TEST_DB_NAME), reset on every test; skipped if Postgres is unreachable.
Open Wearables, the import and the score engine are fakes.
"""
import dataclasses
import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine import batch
from body_engine.config import settings
from body_engine.db import migrate
from body_engine.sync import ow_watch, params as P, refresh

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_ow_watch")
UTC = timezone.utc


@pytest.fixture
def conn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(dataclasses.replace(settings, db_name="postgres").dsn, autocommit=True)
    except (psycopg.OperationalError, RuntimeError) as e:
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    c = psycopg.connect(dataclasses.replace(settings, db_name=TEST_DB).dsn, row_factory=dict_row)
    migrate(c)
    c.commit()
    yield c
    c.close()


def add_user(conn, name, ow=None, consent=True) -> int:
    uid = conn.execute("""INSERT INTO users (display_name, role, ow_user_id, consent_at, consent_version)
                          VALUES (%s, 'member', %s, %s, %s) RETURNING id""",
                       (name, ow, datetime.now(UTC) if consent else None, "v" if consent else None)).fetchone()["id"]
    conn.commit()
    return uid


class UsersOW:
    """GET /users sorted by last_synced_at, newest first, 100 per page."""

    def __init__(self, synced: dict):
        self.synced = synced   # OW user id -> datetime | None
        self.calls = []

    def get(self, path, **params):
        assert path == "/users" and params["sort_by"] == "last_synced_at" and params["sort_order"] == "desc"
        self.calls.append(params)
        rows = sorted(self.synced.items(), key=lambda kv: (kv[1] is None, -(kv[1].timestamp() if kv[1] else 0)))
        page = rows[(params["page"] - 1) * params["limit"]:params["page"] * params["limit"]]
        return {"items": [{"id": k, "last_synced_at": v.isoformat() if v else None} for k, v in page],
                "total": len(rows), "page": params["page"], "limit": params["limit"]}


def row(conn, uid):
    return conn.execute("""SELECT r.status, r.import_queued_at, w.queued_through, w.ow_synced_at FROM users u
                           LEFT JOIN sync_refresh r ON r.user_id = u.id LEFT JOIN ow_watch w ON w.user_id = u.id
                           WHERE u.id = %s""", (uid,)).fetchone()


def test_quiet_then_queued_once(conn):
    now = datetime.now(UTC)
    a = add_user(conn, "a", "ow-a")
    t = now - timedelta(seconds=10)
    ow = UsersOW({"ow-a": t, "ow-x": now - timedelta(seconds=90), "ow-old": now - timedelta(hours=7), "ow-never": None})
    res = ow_watch.poll(conn, ow, now)
    assert res["queued"] == [] and res["waiting"] == [a] and res["uploads"] == 2   # still uploading; ow-x is no user here
    assert row(conn, a)["ow_synced_at"] == t and row(conn, a)["status"] is None
    later = t + P.OW_WATCH_QUIET + timedelta(seconds=1)
    assert ow_watch.poll(conn, ow, later)["queued"] == [a]
    r = row(conn, a)
    assert r["status"] == "scores_queued" and r["import_queued_at"] is not None and r["queued_through"] == t
    # nothing new: not again
    assert ow_watch.poll(conn, ow, later + timedelta(minutes=10))["queued"] == []
    # a new upload: waits for the minimum gap since the last queue, then goes
    ow.synced["ow-a"] = t2 = later + timedelta(seconds=5)
    conn.execute("UPDATE sync_refresh SET status = 'idle', import_queued_at = NULL")
    conn.commit()
    soon = t2 + P.OW_WATCH_QUIET + timedelta(seconds=1)
    if soon - later < P.OW_WATCH_MIN_GAP:
        res = ow_watch.poll(conn, ow, soon)
        assert res["queued"] == [] and res["waiting"] == [a]
    conn.execute("UPDATE ow_watch SET queued_at = now() - interval '1 hour'")
    conn.commit()
    assert ow_watch.poll(conn, ow, soon)["queued"] == [a] and row(conn, a)["queued_through"] == t2


def test_already_imported_busy_consent(conn):
    now = datetime.now(UTC)
    done = add_user(conn, "done", "ow-done")
    busy = add_user(conn, "busy", "ow-busy")
    nocons = add_user(conn, "nocons", "ow-nocons", consent=False)
    fresh = add_user(conn, "fresh", "ow-fresh")   # never imported: its first data comes in at once
    t = now - timedelta(minutes=5)
    # the hourly job (or the app's refresh) imported after this upload
    conn.execute("INSERT INTO sync_state (user_id, source, last_synced_at) VALUES (%s, 'ow', %s)", (done, t + timedelta(seconds=1)))
    conn.execute("INSERT INTO sync_refresh (user_id, status, updated_at) VALUES (%s, 'scoring', now())", (busy,))
    conn.commit()
    ow = UsersOW({f"ow-{n}": t for n in ("done", "busy", "nocons", "fresh")})
    res = ow_watch.poll(conn, ow, now)
    assert res["queued"] == [fresh] and res["waiting"] == [busy]
    assert row(conn, busy)["status"] == "scoring", "a running recompute is not touched"
    assert row(conn, nocons)["status"] is None and row(conn, done)["status"] is None


def test_pages_stop_at_the_lookback(conn):
    now = datetime.now(UTC)
    synced = {f"ow-{i}": now - timedelta(minutes=i) for i in range(250)}
    ow = UsersOW(synced)
    assert len(ow_watch.recent_uploads(ow, now - timedelta(minutes=150))) == 151
    assert [c["page"] for c in ow.calls] == [1, 2]


def test_run_once_lock_and_switch(conn, monkeypatch):
    other = psycopg.connect(dataclasses.replace(settings, db_name=TEST_DB).dsn, row_factory=dict_row)
    other.execute("SELECT pg_advisory_lock(%s)", (ow_watch.LOCK_KEY,))
    assert ow_watch.run_once(conn, UsersOW({})) is None, "another process is polling"
    other.close()
    assert ow_watch.run_once(conn, UsersOW({})) == {"uploads": 0, "queued": [], "waiting": []}
    monkeypatch.setenv("BODY_OW_WATCH", "off")
    assert ow_watch.enabled() is False


def test_queued_run_imports_first_and_the_hourly_job_skips(conn):
    now = datetime.now(UTC)
    a = add_user(conn, "a", "ow-a")
    conn.execute("UPDATE users SET timezone = 'Australia/Sydney' WHERE id = %s", (a,))
    conn.commit()
    ow_watch.poll(conn, UsersOW({"ow-a": now - timedelta(minutes=2)}), now)
    calls = []

    def importer(c, uid):
        calls.append(("import", uid))
        c.execute("UPDATE sync_refresh SET import_queued_at = NULL, status = 'scores_queued' WHERE user_id = %s", (uid,))
        c.commit()
        return {"start": None, "samples": 3}

    def scorer(c, uid, client, scores_from):
        calls.append(("scores", uid))
        return {"rows": 4}

    out = refresh.run_queued(conn, scorer=scorer, importer=importer)
    assert calls == [("import", a), ("scores", a)]
    assert out[0]["import"]["samples"] == 3 and out[0]["rows"] == 4
    assert row(conn, a)["status"] == "idle"
    # the hourly job finds the same inputs it would have scored: skipped
    steps = {r["step"]: r for r in conn.execute("SELECT * FROM batch_state WHERE user_id = %s", (a,))}
    assert set(steps) == {"compute-scores", "evaluate-rules"}
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Australia/Sydney")).date()
    assert steps["compute-scores"]["fingerprint"] == batch.fingerprint(conn, a, today)
    # nothing queued any more
    assert refresh.run_queued(conn, scorer=scorer, importer=importer) == []


def test_failed_import_does_not_score(conn):
    now = datetime.now(UTC)
    a = add_user(conn, "a", "ow-a")
    ow_watch.poll(conn, UsersOW({"ow-a": now - timedelta(minutes=2)}), now)
    scored = []

    class Broken:
        def get(self, *a, **k):
            raise RuntimeError("OW down")

        paginate = get

    out = [refresh.import_queued(conn, a, Broken())]
    assert "error" in out[0]
    r = conn.execute("SELECT status, error, import_queued_at FROM sync_refresh WHERE user_id = %s", (a,)).fetchone()
    assert r["status"] == "error" and "OW down" in r["error"] and r["import_queued_at"] is None
    assert refresh.run_queued(conn, scorer=lambda *a: scored.append(1)) == [] and scored == []
