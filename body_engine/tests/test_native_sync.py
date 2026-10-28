"""Native app sync (#340 N2, nativesync/): the change log, pull (full restore + incremental + raw), push, client_id on
the record endpoints, app-computed scores (spot check, the hourly job's plan), silent pushes, the algorithm bundle
signature. Fictional data only. Separate test database BODY_TEST_DB_NAME (default body_test_native_sync), recreated
each run; skipped without Postgres.
"""
import base64
import json
import os
import re
import shutil
import secrets as pysecrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import algo, api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.nativesync import appscores, params as P, pull as PL, store
from body_engine.notify import apns, core, silent
from body_engine.rollout import flags
from body_engine.scores import params as SP

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_native_sync")
TZ = "Australia/Sydney"
D0 = date(2026, 1, 10)   # fictional days, well in the past


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
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


def conn_of(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


@pytest.fixture(scope="module")
def users(dsn):
    with conn_of(dsn) as conn:
        def add(name):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                                   VALUES (%s, 'member', %s, now(), '2026-10-22.2') RETURNING id""", (name, TZ)).fetchone()["id"]
        ids = {"a": add("a"), "b": add("b"), "off": add("off")}
        flags.set_flag(conn, P.FLAG, "cohort", cohort="native", actor="test")
        flags.set_flag(conn, "water", "all", actor="test")
        flags.cohort_add(conn, ids["a"], "native", actor="test")
        flags.cohort_add(conn, ids["b"], "native", actor="test")
        conn.commit()
        flags.invalidate()
        tok = {n: auth.create_token(conn, i, "app", ["read", "write"]) for n, i in ids.items()}
        tok["a_ro"] = auth.create_token(conn, ids["a"], "ro", ["read"])
        conn.commit()
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None


def H(t, **extra):
    return {"Authorization": f"Bearer {t}", **extra}


def seed_day(conn, uid, d, steps=1000.0):
    conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source) VALUES (%s, %s, 'step_count', %s, 'count', 'sum', 'test')
                    ON CONFLICT (user_id, date, metric) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (uid, d, steps))
    conn.execute("""INSERT INTO daily_scores (user_id, date, sleep_score, recovery, strain, algo_version)
                    VALUES (%s, %s, 70, 60, 8.5, %s) ON CONFLICT (user_id, date) DO NOTHING""", (uid, d, SP.ALGO_VERSION))
    start = datetime(d.year, d.month, d.day, 13, 0, tzinfo=timezone.utc) - timedelta(days=1)
    conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, source)
                    VALUES (%s, %s, %s, %s, 420, 'test') ON CONFLICT DO NOTHING""", (uid, d, start, start + timedelta(hours=7.5)))


def pull_all(client, token, cursors, limit=500, **kw):
    """Pages until more = false; returns (changes, cursors, pages)."""
    out, pages = [], 0
    while True:
        r = client.post("/v1/sync/pull", json={"cursors": cursors, "limit": limit, **kw}, headers=H(token))
        assert r.status_code == 200, r.text
        j = r.json()
        out += j["changes"]
        cursors = j["cursors"]
        pages += 1
        assert pages < 100
        if not j["more"]:
            return out, cursors, pages


# ---------------------------------------------------------------- change log
def test_triggers_log_changes_not_rewrites(dsn, users):
    ids, _ = users
    uid = ids["b"]
    with conn_of(dsn) as c:
        v0 = store.data_version(c, uid)
        seed_day(c, uid, D0)
        c.commit()
        v1 = store.data_version(c, uid)
        assert v1 > v0
        rows = c.execute("SELECT stream, kind, key FROM sync_changes WHERE user_id = %s AND seq > %s", (uid, v0)).fetchall()
        assert {(r["stream"], r["key"]) for r in rows} == {("daily", "2026-01-10"), ("scores", "2026-01-10"), ("sleep", "2026-01-10")}
        # a rewrite with the same values (only updated_at moves) is not a change
        c.execute("UPDATE daily_metrics SET updated_at = now() + interval '1 minute' WHERE user_id = %s", (uid,))
        c.commit()
        assert store.data_version(c, uid) == v1
        # undated kinds use the key "all"
        c.execute("INSERT INTO supplements (user_id, name, status) VALUES (%s, 'Example', 'active')", (uid,))
        assert store.days_changed_here(c, uid) == []
        c.execute("INSERT INTO journal_items (user_id, key, label_zh) VALUES (%s, 'stretch', '拉伸')", (uid,))
        c.execute("INSERT INTO journal_entries (user_id, date, key, value) VALUES (%s, %s, 'stretch', 1)", (uid, D0))
        assert store.days_changed_here(c, uid) == ["2026-01-10"], "the open transaction's own days"
        c.commit()
        assert store.version(c, uid, "records", "supplements", "all") > 0
        # compaction keeps the newest row per key
        seed_day(c, uid, D0, steps=2000.0)
        c.commit()
        before = store.data_version(c, uid)
        assert store.compact(c, uid) >= 1
        assert store.data_version(c, uid) == before
        n = c.execute("""SELECT count(*) AS n FROM sync_changes WHERE user_id = %s AND stream = 'daily' AND key = '2026-01-10'""",
                      (uid,)).fetchone()["n"]
        assert n == 1


def test_deleting_a_user_is_not_blocked(dsn):
    with conn_of(dsn) as c:
        uid = c.execute("INSERT INTO users (display_name, role, timezone) VALUES ('tmp', 'member', %s) RETURNING id", (TZ,)).fetchone()["id"]
        seed_day(c, uid, D0)
        c.commit()
        c.execute("DELETE FROM users WHERE id = %s", (uid,))
        c.commit()
        assert c.execute("SELECT count(*) AS n FROM sync_changes WHERE user_id = %s", (uid,)).fetchone()["n"] == 0


# ---------------------------------------------------------------- pull
def test_pull_needs_the_flag_and_only_shows_own_data(client, users, dsn):
    ids, tok = users
    with conn_of(dsn) as c:
        seed_day(c, ids["b"], D0 + timedelta(days=1))
        c.commit()
    assert client.post("/v1/sync/pull", json={"cursors": {"scores": ""}}, headers=H(tok["off"])).status_code == 404
    got, _, _ = pull_all(client, tok["a"], {"scores": "", "daily": ""})
    assert got == [], "user b's days are not user a's"
    assert client.post("/v1/sync/pull", json={"cursors": {"nope": ""}}, headers=H(tok["a"])).status_code == 422
    r = client.post("/v1/sync/pull", json={"cursors": {"scores": "garbage"}}, headers=H(tok["a"]))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_cursor"
    me = client.get("/v1/web/me", headers=H(tok["a"])).json()
    assert P.FLAG in me["flags"] and isinstance(me["data_version"], int)
    assert "data_version" not in client.get("/v1/web/me", headers=H(tok["off"])).json()


def test_full_restore_then_incremental(client, users, dsn):
    ids, tok = users
    uid = ids["a"]
    days = [D0 - timedelta(days=i) for i in range(20)]
    with conn_of(dsn) as c:
        for d in days:
            seed_day(c, uid, d, steps=1000.0 + d.day)
        c.execute("INSERT INTO supplements (user_id, name, status) VALUES (%s, 'Example', 'active')", (uid,))
        c.execute("""INSERT INTO water_intake (user_id, at, local_date, ml, source) VALUES (%s, %s, %s, 250, 'button')""",
                  (uid, datetime(2026, 1, 9, 23, tzinfo=timezone.utc), date(2026, 1, 10)))
        c.commit()
    streams = {"scores": "", "daily": "", "sleep": "", "records": "", "docs": ""}
    got, cursors, pages = pull_all(client, tok["a"], streams, limit=7)
    assert pages > 3, "small pages"
    by = lambda s: [ch for ch in got if ch["stream"] == s]   # noqa: E731
    assert [ch["date"] for ch in by("scores")] == [d.isoformat() for d in days], "newest day first"
    assert len(by("daily")) == 20 and len(by("sleep")) == 20 and by("docs") == []
    rec = {(ch["kind"], ch["key"]) for ch in by("records")}
    assert rec == {("supplements", "all"), ("water", "2026-01-10")}
    sc = by("scores")[0]
    assert sc["op"] == "upsert" and sc["body"]["recovery"] == 60 and sc["body"]["origin"] == "server" and sc["version"] > 0
    assert by("daily")[0]["body"]["metrics"]["step_count"]["value"] == 1010.0
    assert by("sleep")[0]["body"]["sessions"][0]["asleep_min"] == 420
    assert all(c.startswith("c.") for c in cursors.values())
    # nothing new: an empty page with the same cursors
    again, cur2, _ = pull_all(client, tok["a"], cursors)
    assert again == [] and cur2 == cursors
    # one change, one deletion
    with conn_of(dsn) as c:
        c.execute("UPDATE daily_metrics SET value = 5 WHERE user_id = %s AND date = %s", (uid, D0))
        c.execute("DELETE FROM daily_scores WHERE user_id = %s AND date = %s", (uid, D0 - timedelta(days=1)))
        c.commit()
    inc, cur3, _ = pull_all(client, tok["a"], cursors)
    kinds = {(ch["stream"], ch["op"], ch["key"]) for ch in inc}
    assert kinds == {("daily", "upsert", "2026-01-10"), ("scores", "delete", "2026-01-09"),
                     ("docs", "stale", "today:2026-01-10"), ("docs", "stale", "today:2026-01-09")}
    assert [ch for ch in inc if ch["stream"] == "daily"][0]["body"]["metrics"]["step_count"]["value"] == 5
    assert pull_all(client, tok["a"], cur3)[0] == []


def test_raw_restore_and_raw_days(client, users, dsn):
    ids, tok = users
    uid = ids["a"]
    t = datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc)
    with conn_of(dsn) as c:
        for i in range(3):
            c.execute("""INSERT INTO hk_samples (user_id, hk_uuid, type, start_at, end_at, local_date, value, source)
                         VALUES (%s, gen_random_uuid(), 'HKQuantityTypeIdentifierHeartRate', %s, %s, %s, 61, 'Watch')""",
                      (uid, t + timedelta(minutes=i), t + timedelta(minutes=i), date(2026, 1, 10)))
        c.execute("""INSERT INTO hk_stats (user_id, type, bucket_start, value) VALUES (%s, 'HKQuantityTypeIdentifierStepCount', %s, 120)""",
                  (uid, t))
        c.execute("INSERT INTO hk_dirty_days (user_id, day, types) VALUES (%s, %s, '{x}')", (uid, date(2026, 1, 10)))
        c.commit()
    got, cursors, _ = pull_all(client, tok["a"], {"raw": ""})
    assert [ch["key"] for ch in got] == ["2026-01-10"]
    body = got[0]["body"]
    assert body["samples"]["cols"][:3] == ["uuid", "type", "start"] and len(body["samples"]["rows"]) == 3
    assert body["stats"]["rows"] == [["HKQuantityTypeIdentifierStepCount", t.timestamp(), 120.0]]
    # the restore covered it: no change afterwards; explicit raw_days always answer
    assert pull_all(client, tok["a"], cursors)[0] == []
    r = client.post("/v1/sync/pull", json={"cursors": {"docs": ""}, "raw_days": ["2026-01-10", "2026-01-02"]},
                    headers=H(tok["a"]))
    raw = [ch for ch in r.json()["changes"] if ch["stream"] == "raw"]
    assert [(ch["key"], ch["op"]) for ch in raw] == [("2026-01-02", "delete"), ("2026-01-10", "upsert")]


def test_late_commit_is_not_skipped(dsn, users):
    """A transaction that started earlier but commits later is never behind a cursor already handed out."""
    ids, _ = users
    uid = ids["b"]
    with conn_of(dsn) as base:
        cur = PL.pull(base, uid, {"daily": ""})["cursors"]["daily"]
        base.commit()
        while True:   # finish the restore
            res = PL.pull(base, uid, {"daily": cur})
            cur = res["cursors"]["daily"]
            base.commit()
            if not res["more"]:
                break
    slow, fast, reader = conn_of(dsn), conn_of(dsn), conn_of(dsn)
    try:
        slow.execute("INSERT INTO daily_metrics (user_id, date, metric, value, agg, source) VALUES (%s, '2025-12-01', 'x', 1, 'sum', 't')", (uid,))
        fast.execute("INSERT INTO daily_metrics (user_id, date, metric, value, agg, source) VALUES (%s, '2025-12-02', 'x', 1, 'sum', 't')", (uid,))
        fast.commit()
        first = PL.pull(reader, uid, {"daily": cur})
        reader.commit()
        assert first["changes"] == [], "the slow transaction is still open: nothing after it is handed out yet"
        slow.commit()
        second = PL.pull(reader, uid, {"daily": first["cursors"]["daily"]})
        assert {ch["key"] for ch in second["changes"]} == {"2025-12-01", "2025-12-02"}
    finally:
        for c in (slow, fast, reader):
            c.close()


# ---------------------------------------------------------------- push and client_id
def test_push_applies_dedups_and_reports_conflicts(client, users, dsn):
    ids, tok = users
    uid = ids["a"]
    today = datetime.now().date()
    with conn_of(dsn) as c:
        c.execute("INSERT INTO journal_items (user_id, key, label_zh) VALUES (%s, 'sauna', '桑拿') ON CONFLICT DO NOTHING", (uid,))
        c.commit()
    day = (today - timedelta(days=1)).isoformat()
    muts = [{"client_id": "w-1", "kind": "water", "body": {"ml": 300}},
            {"client_id": "j-1", "kind": "journal", "key": f"{day}/sauna", "body": {"value": 1}},
            {"client_id": "bad-1", "kind": "journal", "key": "not-a-key", "body": {"value": 1}},
            {"client_id": "bad-2", "kind": "journal", "key": f"{day}/nope", "body": {"value": 1}}]
    r = client.post("/v1/sync/push", json={"mutations": muts}, headers=H(tok["a"]))
    assert r.status_code == 200, r.text
    res = {x["client_id"]: x for x in r.json()["results"]}
    assert res["w-1"]["status"] == "applied" and res["w-1"]["body"]["entry"]["ml"] == 300 and res["w-1"]["version"] > 0
    assert res["j-1"]["status"] == "applied" and res["j-1"]["date"] == day
    assert res["bad-1"]["status"] == "rejected" and res["bad-2"]["status"] == "rejected"
    # resend: the first answers, nothing written twice
    r2 = client.post("/v1/sync/push", json={"mutations": muts[:2]}, headers=H(tok["a"])).json()["results"]
    assert [x["status"] for x in r2] == ["duplicate", "duplicate"] and r2[0]["body"] == res["w-1"]["body"]
    with conn_of(dsn) as c:
        assert c.execute("SELECT count(*) AS n FROM water_intake WHERE user_id = %s AND ml = 300", (uid,)).fetchone()["n"] == 1
    # deletion wins over a stale edit
    v = res["j-1"]["version"]
    gone = client.post("/v1/sync/push", json={"mutations": [
        {"client_id": "j-del", "kind": "journal", "op": "delete", "key": f"{day}/sauna"},
        {"client_id": "j-2", "kind": "journal", "key": f"{day}/sauna", "base_version": v, "body": {"value": 0}}]},
        headers=H(tok["a"])).json()["results"]
    assert [x["status"] for x in gone] == ["applied", "conflict"] and gone[1]["detail"]["conflict"] == "deleted"
    # read-only token, someone else's flag state, malformed body
    assert client.post("/v1/sync/push", json={"mutations": muts[:1]}, headers=H(tok["a_ro"])).status_code == 403
    assert client.post("/v1/sync/push", json={"mutations": [{"client_id": "x", "kind": "nope"}]},
                       headers=H(tok["a"])).status_code == 422


def test_training_day_conflicts_on_a_stale_version(client, users, dsn):
    ids, tok = users
    day = (datetime.now().date() - timedelta(days=1)).isoformat()
    first = client.post("/v1/sync/push", json={"mutations": [
        {"client_id": "t-1", "kind": "training_session", "key": day, "body": {"exercises": [], "notes": "a"}}]},
        headers=H(tok["a"])).json()["results"][0]
    assert first["status"] == "applied", first
    v = first["version"]
    second = client.post("/v1/sync/push", json={"mutations": [
        {"client_id": "t-2", "kind": "training_session", "key": day, "base_version": v, "body": {"exercises": [], "notes": "b"}}]},
        headers=H(tok["a"])).json()["results"][0]
    assert second["status"] == "applied" and second["version"] > v
    stale = client.post("/v1/sync/push", json={"mutations": [
        {"client_id": "t-3", "kind": "training_session", "key": day, "base_version": v, "body": {"exercises": [], "notes": "c"}}]},
        headers=H(tok["a"])).json()["results"][0]
    assert stale["status"] == "conflict" and stale["detail"]["version"] == second["version"]


def test_record_endpoints_take_a_client_id(client, users, dsn):
    ids, tok = users
    uid = ids["b"]
    with conn_of(dsn) as c:
        c.execute("INSERT INTO journal_items (user_id, key, label_zh) VALUES (%s, 'walk', '散步') ON CONFLICT DO NOTHING", (uid,))
        c.commit()
    day = (datetime.now().date() - timedelta(days=2)).isoformat()
    a = client.put(f"/v1/users/me/journal/{day}/walk", json={"value": 1, "client_id": "jc-1"}, headers=H(tok["b"]))
    assert a.status_code == 200 and "x-duplicate" not in a.headers
    with conn_of(dsn) as c:   # changed elsewhere meanwhile: the resend still gets its first answer, writes nothing
        c.execute("UPDATE journal_entries SET value = 0 WHERE user_id = %s AND key = 'walk'", (uid,))
        c.commit()
    b = client.put(f"/v1/users/me/journal/{day}/walk", json={"value": 1}, headers=H(tok["b"], **{"X-Client-Id": "jc-1"}))
    assert b.status_code == 200 and b.headers["x-duplicate"] == "1" and b.json() == a.json()
    with conn_of(dsn) as c:
        assert c.execute("SELECT value FROM journal_entries WHERE user_id = %s AND key = 'walk'", (uid,)).fetchone()["value"] == 0
    d1 = client.delete(f"/v1/users/me/journal/{day}/walk", headers=H(tok["b"], **{"X-Client-Id": "jc-del"}))
    d2 = client.delete(f"/v1/users/me/journal/{day}/walk", headers=H(tok["b"], **{"X-Client-Id": "jc-del"}))
    assert d1.status_code == 204 and d2.status_code == 204 and d2.headers["x-duplicate"] == "1"
    assert client.delete(f"/v1/users/me/journal/{day}/walk", headers=H(tok["b"])).status_code == 404
    # the same client_id for another write is refused
    assert client.put(f"/v1/users/me/journal/{day}/walk", json={"value": 1, "client_id": "jc-del"},
                      headers=H(tok["b"])).status_code == 422


# ---------------------------------------------------------------- app scores
def app_row(**kw):
    return {"sleep_score": 70, "recovery": 60, "strain": 8.5, "sleep_need_min": 480, "hrv_kind": "rmssd", **kw}


def test_app_scores_check_store_and_plan(client, users, dsn, monkeypatch):
    ids, tok = users
    uid = ids["b"]
    today = datetime.now().date()
    d1, d2 = today - timedelta(days=2), today - timedelta(days=1)
    calls = []

    def fake_server(conn, u, days):
        calls.append(days)
        return {d: {**{c: None for c in appscores.E.COLUMNS}, "date": d, **app_row(), "algo_version": SP.ALGO_VERSION}
                for d in days}
    monkeypatch.setattr(appscores, "server_rows", fake_server)
    up = lambda body, t="b": client.put("/v1/users/me/scores/app", json=body, headers=H(tok[t]))   # noqa: E731
    body = {"algo_version": SP.ALGO_VERSION, "bundle_version": "0.1.0",
            "days": [{"date": d1.isoformat(), "row": app_row()}, {"date": d2.isoformat(), "row": app_row()}]}
    # flag off / someone else / bad rows / other algorithm version
    assert up(body, "off").status_code == 404
    assert client.put(f"/v1/users/{ids['a']}/scores/app", json=body, headers=H(tok["b"])).status_code == 403
    assert up({**body, "days": [{"date": d1.isoformat(), "row": {"nope": 1}}]}).status_code == 422
    assert up({**body, "days": [{"date": d1.isoformat(), "row": {"recovery": "high"}}]}).status_code == 422
    stale = up({**body, "algo_version": "v0-old"}).json()
    assert stale["status"] == "stale_algo" and stale["stored"] == 0
    # first upload of a bundle: checked (the newest day), equal -> stored as the app's
    ok = up(body).json()
    assert ok["status"] == "ok" and ok["stored"] == 2 and ok["check"]["date"] == d2.isoformat() and ok["check"]["ok"]
    assert ok["app_scoring"] is True and calls == [[d2]]
    with conn_of(dsn) as c:
        rows = c.execute("SELECT date, origin, bundle_version, recovery FROM daily_scores WHERE user_id = %s AND date >= %s ORDER BY date",
                         (uid, d1)).fetchall()
        assert [(r["origin"], r["bundle_version"], r["recovery"]) for r in rows] == [("app", "0.1.0", 60)] * 2
        # the hourly job: nothing to do while nothing changed after the upload
        assert appscores.plan(c, uid) == {"mode": "app", "days": []}
        # inputs of d1 changed long ago (beyond the grace) and the app did not upload since: the server fills in d1 only
        c.execute("UPDATE daily_scores SET computed_at = now() - interval '5 hours' WHERE user_id = %s AND date = %s", (uid, d1))
        seed_day(c, uid, d1, steps=4321.0)
        c.execute("UPDATE sync_changes SET changed_at = now() - interval '3 hours' WHERE user_id = %s AND day = %s", (uid, d1))
        c.commit()
        assert appscores.plan(c, uid) == {"mode": "app", "days": [d1]}
    # a second upload within CHECK_EVERY_HOURS of the same bundle is not checked
    again = up({**body, "days": [{"date": d1.isoformat(), "row": app_row(recovery=61)}]}).json()
    assert again["check"] is None and again["stored"] == 1 and len(calls) == 1
    with conn_of(dsn) as c:
        assert appscores.plan(c, uid) == {"mode": "app", "days": []}, "the app's upload is newer than the change"
    # a different answer than Python: the server's row wins, the user is scored by the server again
    bad = up({**body, "bundle_version": "0.1.1", "days": [{"date": d2.isoformat(), "row": app_row(recovery=90)}]}).json()
    assert bad["status"] == "mismatch" and bad["stored"] == 0 and bad["check"]["diffs"] == {"recovery": {"app": 90, "server": 60}}
    assert bad["app_scoring"] is False
    with conn_of(dsn) as c:
        r = c.execute("SELECT origin, recovery FROM daily_scores WHERE user_id = %s AND date = %s", (uid, d2)).fetchone()
        assert (r["origin"], r["recovery"]) == ("server", 60)
        assert appscores.plan(c, uid) == {"mode": "server"}
        assert c.execute("SELECT count(*) AS n FROM app_score_checks WHERE user_id = %s AND NOT ok", (uid,)).fetchone()["n"] == 1
    # a passing check trusts the app again; the admin can force the server
    assert up({**body, "bundle_version": "0.1.2"}).json()["app_scoring"] is True
    with conn_of(dsn) as c:
        assert appscores.set_server_forced(c, uid, True)["active"] is False
        assert appscores.plan(c, uid) == {"mode": "server"}
        assert appscores.set_server_forced(c, uid, False)["active"] is True
    # the AI / MCP read what is stored
    with conn_of(dsn) as c:
        from body_engine.scores import store as scores_store
        got = scores_store.scores(c, uid, d2, d2)
        assert got and (got[0]["recovery"] if isinstance(got, list) else got["days"][0]["recovery"]) == 60


def test_hourly_job_follows_the_plan(dsn, users, monkeypatch):
    from body_engine import batch
    from body_engine.insights import jobs as insights_jobs
    from body_engine.scores import engine as scores_engine
    ids, _ = users
    runs = []
    monkeypatch.setattr(scores_engine, "run", lambda conn, client, uid, start, end, archive, ow_cache=None, only=None:
                        runs.append((uid, start, only)) or {"rows": 0})
    monkeypatch.setattr(insights_jobs, "run", lambda *a, **k: {})
    u = {"id": ids["b"], "tz": TZ}
    with conn_of(dsn) as c:
        monkeypatch.setattr(appscores, "plan", lambda conn, uid: {"mode": "app", "days": []})
        assert json.loads(batch._step_compute_scores(c, None, u, None))["app_scoring"] is True and runs == []
        monkeypatch.setattr(appscores, "plan", lambda conn, uid: {"mode": "app", "days": [D0]})
        batch._step_compute_scores(c, None, u, None)
        assert runs == [(ids["b"], D0, {D0})]
        monkeypatch.setattr(appscores, "plan", lambda conn, uid: {"mode": "server"})
        batch._step_compute_scores(c, None, u, D0)
        assert runs[-1] == (ids["b"], D0, None)


# ---------------------------------------------------------------- the phone's own scoring (#340 N2b)
def test_raw_carries_beats_and_raw_since_bounds_the_restore(client, users, dsn):
    ids, tok = users
    uid = ids["b"]
    t = datetime(2026, 2, 3, 15, 0, tzinfo=timezone.utc)   # 2026-02-04 02:00 in Sydney: the local day counts
    old = datetime(2026, 1, 20, 1, 0, tzinfo=timezone.utc)
    with conn_of(dsn) as c:
        c.execute("""INSERT INTO heartbeat_series (user_id, start_at, end_at, source, origin, hk_uuid, beats_ms, n_beats,
                                                   n_valid, algo, rmssd, sdnn)
                     VALUES (%s, %s, %s, 'Watch', 'app', gen_random_uuid(), '{0,800,1600}', 3, 2, 'rr-v1', 42.5, 40.1)""",
                  (uid, t, t + timedelta(minutes=1)))
        c.execute("""INSERT INTO hk_samples (user_id, hk_uuid, type, start_at, end_at, local_date, value, source)
                     VALUES (%s, gen_random_uuid(), 'HKQuantityTypeIdentifierHeartRate', %s, %s, '2026-01-20', 58, 'Watch')""",
                  (uid, old, old))
        c.commit()
        logged = c.execute("SELECT key FROM sync_changes WHERE user_id = %s AND stream = 'raw'", (uid,)).fetchall()
        assert "2026-02-04" in {r["key"] for r in logged}   # the trigger of migration 980, by the user's local day
    got, cursors, _ = pull_all(client, tok["b"], {"raw": ""}, raw_since="2026-02-01")
    assert [ch["key"] for ch in got] == ["2026-02-04"]
    beats = got[0]["body"]["beats"]
    assert beats["cols"] == ["uuid", "start", "end", "rmssd", "sdnn", "origin"]
    assert beats["rows"][0][1] == t.timestamp() and beats["rows"][0][3:] == [42.5, 40.1, "app"]
    assert cursors["raw"].startswith("c.")   # the bounded restore is complete; changes follow incrementally
    everything, _, _ = pull_all(client, tok["b"], {"raw": ""})
    assert {"2026-01-20", "2026-02-04"} <= {ch["key"] for ch in everything}


def test_sleep_carries_the_scores_stage_segments(client, users, dsn, monkeypatch):
    """Recent nights come with the stage segments the server's scoring uses (scores/inputs.night_segments)."""
    from zoneinfo import ZoneInfo
    from body_engine.rawdata import samples as rx_samples
    ids, tok = users
    uid = ids["b"]
    tz = ZoneInfo(TZ)
    wake = datetime.now(tz).date() - timedelta(days=2)
    bed = datetime.combine(wake, datetime.min.time(), tz).replace(hour=2)   # wakes 05:20: that day (end − 4 h)
    with conn_of(dsn) as c:
        src = c.execute("""INSERT INTO hk_sources (user_id, name, device_type, device_name) VALUES (%s, 'Watch', 'watch', 'Apple Watch')
                           RETURNING id""", (uid,)).fetchone()["id"]
        for i, value in enumerate((3, 4, 5, 2, 3)):   # core, deep, rem, awake, core: 40 minutes each
            a = bed + timedelta(minutes=40 * i)
            c.execute("""INSERT INTO hk_samples (user_id, hk_uuid, type, start_at, end_at, local_date, value, source, source_id)
                         VALUES (%s, gen_random_uuid(), 'HKCategoryTypeIdentifierSleepAnalysis', %s, %s, %s, %s, 'Watch', %s)""",
                      (uid, a, a + timedelta(minutes=40), a.date(), value, src))
        c.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, core_min, deep_min, rem_min,
                                                 awake_min, source, device)
                     VALUES (%s, %s, %s, %s, 160, 80, 40, 40, 40, 'app', 'Apple Watch')""",
                  (uid, wake, bed, bed + timedelta(minutes=200)))
        c.commit()
    monkeypatch.setattr(rx_samples, "reads_app", lambda conn, user_id: True)
    got, _, _ = pull_all(client, tok["b"], {"sleep": ""})
    night = next(ch for ch in got if ch["key"] == wake.isoformat())
    segs = night["body"]["segments"]
    assert [s[2] for s in segs] == ["core", "deep", "rem", "awake", "core"]
    assert segs[0][0] == bed.timestamp() and segs[-1][1] == (bed + timedelta(minutes=200)).timestamp()
    old = next(ch for ch in got if ch["key"] == D0.isoformat())   # outside SEGMENT_DAYS: sessions only
    assert "segments" not in old["body"]


def test_scoring_context(client, users, dsn, monkeypatch):
    from body_engine.rawdata import samples as rx_samples
    ids, tok = users
    uid = ids["b"]
    t = datetime(2026, 2, 9, 1, 0, tzinfo=timezone.utc)   # 2026-02-09 12:00 in Sydney
    with conn_of(dsn) as c:
        for i, v in enumerate((60, 61, 61, 60.5, 140)):    # 60.5 rounds to 60, like Python's round()
            c.execute("""INSERT INTO hk_samples (user_id, hk_uuid, type, start_at, end_at, local_date, value, source)
                         VALUES (%s, gen_random_uuid(), 'HKQuantityTypeIdentifierHeartRate', %s, %s, '2026-02-09', %s, 'Watch')""",
                      (uid, t + timedelta(minutes=i), t + timedelta(minutes=i), v))
        c.commit()
    monkeypatch.setattr(rx_samples, "reads_app", lambda conn, user_id: True)
    r = client.get("/v1/sync/scoring", params={"from": "2026-02-08", "to": "2026-02-09", "hist_from": "2026-02-08",
                                               "hist_to": "2026-02-09"}, headers=H(tok["b"]))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["tz"] == TZ and j["hrv_kind"] in ("sdnn", "rmssd") and j["algo_version"] == SP.ALGO_VERSION
    assert j["sync_mode"] == "ow" and j["raw_days"] == P.APP_RAW_DAYS
    assert set(j["app_scoring"]) >= {"active", "trusted", "server_forced", "last_check"}
    assert j["hr_hist"]["days"] == {"2026-02-09": {"60": 2, "61": 2, "140": 1}}
    assert j["muscle"] == {"from": "2026-02-08", "to": "2026-02-09", "days": {}, "error": None}
    plain = client.get("/v1/sync/scoring", headers=H(tok["b"])).json()
    assert "hr_hist" not in plain and "muscle" not in plain
    too_long = {"hist_from": "2024-01-01", "hist_to": "2026-02-09"}
    assert client.get("/v1/sync/scoring", params=too_long, headers=H(tok["b"])).status_code == 422
    assert client.get("/v1/sync/scoring", headers=H(tok["off"])).status_code == 404


# ---------------------------------------------------------------- silent push
class FakeApns:
    def __init__(self):
        self.sent = []

    def send(self, token, environment, payload, headers):
        self.sent.append({"token": token, "payload": payload, "headers": headers})
        return apns.Result(200)


def test_silent_push_on_change(client, users, dsn):
    ids, tok = users
    uid = ids["a"]
    token = pysecrets.token_hex(32)
    r = client.post("/v1/users/me/push-devices", json={"token": token, "environment": "sandbox", "silent": True},
                    headers=H(tok["a"]))
    assert r.status_code == 200, r.text
    core.set_client(None)
    with conn_of(dsn) as c:
        assert silent.tick(c) == [], "APNs not configured: nothing"
    fake = FakeApns()
    core.set_client(fake)
    try:
        with conn_of(dsn) as c:
            seed_day(c, uid, date.today() - timedelta(days=3), steps=777.0)
            c.commit()
            sent = silent.tick(c)
            assert [s["user_id"] for s in sent] == [uid] and fake.sent[0]["token"] == token
            assert fake.sent[0]["payload"]["aps"] == {"content-available": 1}
            assert fake.sent[0]["headers"]["apns-push-type"] == "background"
            assert fake.sent[0]["payload"]["w2w"]["data_version"] == store.data_version(c, uid)
            assert silent.tick(c) == [], "nothing new"
    finally:
        core.set_client(None)


# ---------------------------------------------------------------- algorithm bundle signature
def test_sign_and_verify_the_bundle(tmp_path, monkeypatch):
    from body_engine.algo import signing
    from body_engine.algo.api import router
    from fastapi import FastAPI
    dist = tmp_path / "dist"
    shutil.copytree(algo.DEFAULT_DIR, dist)
    key = tmp_path / "secrets" / "algo_signing_key"
    pub = signing.keygen(key)
    assert oct(key.stat().st_mode & 0o777) == "0o600" and len(base64.b64decode(pub["public_key"])) == 32
    with pytest.raises(FileExistsError):
        signing.keygen(key)
    monkeypatch.setenv("BODY_ALGO_JS_DIR", str(dist))
    m = signing.sign_dist(key, dist)
    assert m["key_id"] == pub["key_id"]
    body = (dist / "openbodyscore.js").read_bytes()
    assert signing.verify(m, body, [pub["public_key"]])
    other = signing.keygen(tmp_path / "other")
    assert not signing.verify(m, body, [other["public_key"]])
    assert signing.verify(m, body, [other["public_key"], pub["public_key"]]), "current or next key"
    assert not signing.verify(m, body + b"\n", [pub["public_key"]])
    # the manifest stays what build.js writes, plus the two fields; the endpoint hands them out
    written = json.loads((dist / "manifest.json").read_text())
    assert set(written) - set(json.loads((algo.DEFAULT_DIR / "manifest.json").read_text())) <= {"signature", "key_id"}
    app = FastAPI()
    app.include_router(router)
    got = TestClient(app).get("/v1/algo/manifest").json()
    assert got["signature"] == m["signature"] and got["key_id"] == m["key_id"]


def test_repository_keys_match_the_app():
    """signing-keys.json and the keys built into the iOS app are the same; the committed dist/ is unsigned or signed
    by one of them."""
    from body_engine.algo import signing
    keys = signing.known_keys()
    assert set(keys) == {"current", "next"}
    swift = (Path(__file__).resolve().parents[2] / "apps" / "ios" / "Watch2Whoop" / "Algorithms" / "AlgoSigningKeys.swift").read_text()
    for slot in ("current", "next"):
        m = re.search(rf"static let {slot}: String\? = (nil|\"([A-Za-z0-9+/=]+)\")", swift)
        assert m, slot
        assert (m.group(2) if m.group(1) != "nil" else None) == (keys[slot] or {}).get("public_key")
    assert signing.check_repository() is None
    assert client_manifest_fields()


def client_manifest_fields():
    from body_engine.algo.api import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    m = TestClient(app).get("/v1/algo/manifest").json()
    return "signature" in m and "key_id" in m
