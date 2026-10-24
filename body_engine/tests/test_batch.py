"""hourly-batch (batch.py): users isolated from each other, unchanged inputs skipped, idempotent, worker processes.
Own test database body_test_batch (BODY_TEST_DB_NAME_BATCH), recreated on every run; skipped without Postgres. Never the real DB."""
import os
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine import batch
from body_engine.config import Settings
from body_engine.db import migrate
from test_scores import make_inputs
from test_scores_db import FakeOW

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_BATCH", "body_test_batch")


@pytest.fixture(scope="module")
def dsn():
    if not TEST_DB.startswith("body_test_"):
        pytest.exit("the test DB must be a body_test_* database")
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


def seed_user(conn, name: str, nights: int = 12, consent: bool = True) -> int:
    inp = make_inputs(nights)
    uid = conn.execute("""INSERT INTO users (display_name, role, birth_date, timezone)
                          VALUES (%s, 'owner', '2001-06-15', 'Australia/Sydney') RETURNING id""", (name,)).fetchone()["id"]
    for n in inp.nights:
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, core_min, deep_min,
                          rem_min, awake_min, is_main, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,'ow')""",
                     (uid, n.wake_date, n.start, n.end, n.asleep_min, n.asleep_min - n.deep_min - n.rem_min,
                      n.deep_min, n.rem_min, n.awake_min))
    for d, v in inp.daily_rhr.items():
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                        VALUES (%s, %s, 'resting_heart_rate', %s, 'count/min', 'avg', 'ow')""", (uid, d, v))
    return uid


@pytest.fixture
def world(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        conn.execute("TRUNCATE users CASCADE")
        ids = [seed_user(conn, f"用户{i}") for i in range(3)]
        yield conn, ids


def users(conn, ids=None):
    return batch.list_users(conn, ids)


def run(dsn, us, **kw):
    out: list[str] = []
    kw.setdefault("steps", ("compute-scores", "evaluate-rules"))
    res = batch.run(us, dsn=dsn, emit=out.append, **kw)
    return res, out


def test_all_users_processed_and_idempotent(dsn, world):
    conn, ids = world
    res, out = run(dsn, users(conn), skip=False)
    assert res["errors"] == [] and res["done"] == 3
    assert sum(l.startswith("✓ compute-scores user") for l in out) == 3
    assert sum(l.startswith("✓ evaluate-rules user") for l in out) == 3
    first = conn.execute("SELECT user_id, date, recovery, strain FROM daily_scores ORDER BY user_id, date").fetchall()
    assert first
    run(dsn, users(conn), skip=False)
    assert conn.execute("SELECT user_id, date, recovery, strain FROM daily_scores ORDER BY user_id, date").fetchall() == first


def test_failure_of_one_user_does_not_stop_the_others(dsn, world, monkeypatch):
    conn, ids = world
    real = batch._step_evaluate_rules

    def boom(c, client, u):
        if u["id"] == ids[1]:
            raise RuntimeError("规则坏了")
        return real(c, client, u)

    monkeypatch.setattr(batch, "_step_evaluate_rules", boom)
    res, out = run(dsn, users(conn), skip=False)
    assert len(res["errors"]) == 1 and f"user {ids[1]}" in res["errors"][0]
    assert any(l.startswith("✗ evaluate-rules user") for l in out)
    # the failed user still got scores, and the others got rules
    assert conn.execute("SELECT count(DISTINCT user_id) AS n FROM daily_scores").fetchone()["n"] == 3
    assert conn.execute("SELECT count(*) AS n FROM rule_runs").fetchone()["n"] == 2
    # a failed step is not remembered: the next run retries it
    monkeypatch.setattr(batch, "_step_evaluate_rules", real)
    res, out = run(dsn, users(conn))
    assert res["errors"] == [] and any(l.startswith("✓ evaluate-rules user %d" % ids[1]) for l in out)


def test_unchanged_users_are_skipped_until_data_changes(dsn, world):
    conn, ids = world
    run(dsn, users(conn))                        # first run computes everything
    res, out = run(dsn, users(conn))
    assert res["skipped_steps"] == 6 and not any(l.startswith("✓") for l in out)
    # new data for one user only -> only that user is recomputed
    conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                    VALUES (%s, '2026-10-20', 'step_count', 4000, 'count', 'sum', 'ow')""", (ids[0],))
    res, out = run(dsn, users(conn))
    assert res["skipped_steps"] == 4
    assert [l for l in out if l.startswith("✓ compute-scores")] and all(f"user {ids[0]}" in l for l in out if l.startswith("✓"))
    # a rewrite with the same content (import-ow bumps updated_at every hour) is not a change
    conn.execute("UPDATE daily_metrics SET updated_at = now() + interval '1 minute'")
    res, _ = run(dsn, users(conn))
    assert res["skipped_steps"] == 6
    # max age: nothing changed but the last run is too old -> recompute
    conn.execute("UPDATE batch_state SET ran_at = now() - interval '7 hours'")
    res, out = run(dsn, users(conn))
    assert res["skipped_steps"] == 0 and sum(l.startswith("✓ compute-scores") for l in out) == 3
    # --full / --no-skip never skip
    res, _ = run(dsn, users(conn), full=True, skip=False)
    assert res["skipped_steps"] == 0


def test_user_without_consent_or_data_is_skipped_quietly(dsn, world):
    conn, ids = world
    empty = conn.execute("INSERT INTO users (display_name, role) VALUES ('空', 'owner') RETURNING id").fetchone()["id"]
    res, out = run(dsn, users(conn, [empty]), skip=False)
    assert res["errors"] == [] and any("还没有任何数据" in l for l in out)


def test_worker_processes(dsn, world):
    conn, ids = world
    res, out = run(dsn, users(conn), workers=2, skip=False)
    assert res["errors"] == [] and res["workers"] == 2
    assert sum(l.startswith("✓ compute-scores user") for l in out) == 3


def test_fake_ow_client_used_for_import_free_scores(dsn, world):
    conn, ids = world
    inp = make_inputs(12)
    res, _ = run(dsn, users(conn, ids[:1]), skip=False, client=FakeOW(inp), steps=("compute-scores",))
    assert res["errors"] == []
    assert conn.execute("SELECT count(*) AS n FROM daily_scores WHERE user_id = %s", (ids[0],)).fetchone()["n"] >= 1


def test_own_log_change_reruns_rules_but_not_scores(dsn, world):
    conn, ids = world
    run(dsn, users(conn))
    conn.execute("""INSERT INTO food_entries (user_id, date, meal, food_name, calories) VALUES (%s, current_date, 'lunch', '示例', 500)""",
                 (ids[0],))
    res, out = run(dsn, users(conn))
    assert [l.split()[1] + " " + l.split()[2] for l in out if l.startswith("✓")] == [f"evaluate-rules user"]
    assert res["skipped_steps"] == 5


def test_refresh_is_staggered_by_user_and_hour(dsn, world, monkeypatch):
    conn, ids = world

    def due_in_hour(hour):
        monkeypatch.setattr(batch, "_now_hour", lambda: hour)
        conn.execute("UPDATE batch_state SET ran_at = now() - interval '5 hours'")      # unchanged for 5 hours
        _, out = run(dsn, users(conn))
        return {int(l.split("user ")[1].split("（")[0].split()[0]) for l in out if l.startswith("✓ compute-scores")}

    run(dsn, users(conn))
    seen = set()
    for hour in range(1000, 1004):
        due = due_in_hour(hour)
        assert due == {i for i in ids if (hour + i) % batch.REFRESH_TURNS == 0}
        seen |= due
    assert seen == set(ids)                       # every user gets its turn within REFRESH_TURNS hours
    conn.execute("UPDATE batch_state SET ran_at = now() - interval '2 hours'")           # young: nobody is due
    _, out = run(dsn, users(conn))
    assert not any(l.startswith("✓") for l in out)


def test_skip_counts_as_a_fresh_check(dsn, world):
    conn, ids = world
    run(dsn, users(conn))
    conn.execute("UPDATE batch_state SET checked_at = now() - interval '2 hours'")
    run(dsn, users(conn))
    assert conn.execute("SELECT count(*) AS n FROM batch_state WHERE checked_at < now() - interval '1 minute'").fetchone()["n"] == 0


class ProbeOW:
    def __init__(self, news: bool):
        self.news, self.calls = news, 0

    def get(self, path, **params):
        self.calls += 1
        assert path.endswith("/timeseries") and params["limit"] == 1
        return {"data": [{"timestamp": "2026-10-25T00:00:00+00:00"}] if self.news else []}


def test_import_ow_skipped_when_open_wearables_has_nothing_new(dsn, world, monkeypatch):
    conn, ids = world
    conn.execute("UPDATE users SET ow_user_id = 'ow-' || id")
    conn.execute("""INSERT INTO sync_state (user_id, source, last_synced_at, cursor)
                    SELECT id, 'ow', now() - interval '50 minutes', '2026-10-24T20:00:00+00:00' FROM users""")
    imports = []
    monkeypatch.setattr(batch, "_step_import_ow", lambda c, client, u: imports.append(u["id"]) or "ok")
    us = users(conn)
    kw = {"steps": ("import-ow",)}
    run(dsn, us, client=ProbeOW(news=True), **kw)
    assert sorted(imports) == sorted(ids)                               # first run: no earlier import recorded -> runs
    imports.clear()
    quiet = ProbeOW(news=False)
    res, out = run(dsn, us, client=quiet, **kw)
    assert imports == [] and quiet.calls == 3 and res["skipped_steps"] == 3
    # skipping still counts as a sync for the doctor
    assert conn.execute("SELECT count(*) AS n FROM sync_state WHERE last_synced_at > now() - interval '1 minute'").fetchone()["n"] == 3
    # news -> imports; no client / no cursor -> imports
    run(dsn, us, client=ProbeOW(news=True), **kw)
    assert sorted(imports) == sorted(ids)
    imports.clear()
    conn.execute("DELETE FROM sync_state")
    run(dsn, us, client=ProbeOW(news=False), **kw)
    assert sorted(imports) == sorted(ids)
