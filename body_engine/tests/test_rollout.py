"""Gradual rollout and A/B tests (rollout/): stable buckets and accurate percentages, cohorts, the state order, the consent
version that follows the flags, gated endpoints (404 when off), the cache, experiments (stable variants, weights, cohort,
window, exposures, results), and the admin commands with their audit log.

Uses its own test database (body_test_rollout), dropped and recreated on every run; skipped if Postgres is unreachable.
No flag is declared in the shipped registry yet, so the tests declare their own keys.
"""
import json
import sys
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, cli
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.profile import consent
from body_engine.rollout import experiments, flags, registry

TEST_DB = "body_test_rollout"
KEY, OTHER = "demo_feature", "other_feature"
OLD = "2000-01-01"


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


@pytest.fixture(autouse=True)
def declared(monkeypatch):
    monkeypatch.setitem(registry.FLAGS, KEY, "测试功能")
    monkeypatch.setitem(registry.FLAGS, OTHER, "另一个测试功能")
    flags.invalidate()
    yield
    flags.invalidate()


@pytest.fixture(scope="module")
def users(dsn):
    """owner, two beta testers (b1, b2) and two others (m1, m2); members agreed to an old consent version."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids, tok = {}, {}
        for name, role in (("owner", "owner"), ("b1", "member"), ("b2", "member"), ("m1", "member"), ("m2", "member")):
            ids[name] = conn.execute("""INSERT INTO users (display_name, role, consent_at, consent_version)
                                        VALUES (%s, %s, now(), %s) RETURNING id""",
                                     (name, role, None if role == "owner" else OLD)).fetchone()["id"]
            tok[name] = auth.create_token(conn, ids[name], "web", ["read", "write"])
    return ids, tok


@pytest.fixture
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


def reset(conn, ids):
    conn.execute("DELETE FROM user_cohorts")
    conn.execute("DELETE FROM feature_flags")
    conn.execute("DELETE FROM rollout_settings")
    conn.execute("DELETE FROM experiments")
    conn.execute("UPDATE users SET consent_version = %s WHERE role = 'member'", (OLD,))
    for u in ("b1", "b2"):
        flags.cohort_add(conn, ids[u], "beta", actor="test")
    conn.commit()
    flags.invalidate()


# ---------------------------------------------------------------- buckets
def test_buckets_are_stable_uniform_and_independent():
    assert flags.bucket("flag", KEY, 42) == flags.bucket("flag", KEY, 42)
    xs = [flags.bucket("flag", KEY, u) for u in range(20000)]
    assert all(0 <= x < 100 for x in xs)
    for p in (1, 10, 30, 50, 90):
        share = sum(x < p for x in xs) / len(xs)
        assert abs(share - p / 100) < 0.012, (p, share)
    ys = [flags.bucket("flag", OTHER, u) for u in range(20000)]
    both = sum(x < 50 and y < 50 for x, y in zip(xs, ys)) / len(xs)
    assert abs(both - 0.25) < 0.015   # another key buckets independently
    assert flags.bucket("exp", KEY, 7) != flags.bucket("flag", KEY, 7)


# ---------------------------------------------------------------- states, cohorts, cache
def test_state_order_and_cohorts(conn, users):
    ids, _ = users
    reset(conn, ids)
    on = lambda: {n for n in ids if flags.enabled(conn, ids[n], KEY)}  # noqa: E731
    assert on() == set()                                              # no row yet = off
    flags.set_flag(conn, KEY, "cohort", actor="test")
    assert on() == {"b1", "b2"}
    flags.set_flag(conn, KEY, "percent", percent=0, actor="test")
    assert on() == {"b1", "b2"}                                       # the cohort stays in during a percentage ramp
    flags.set_flag(conn, KEY, "percent", percent=100, actor="test")
    assert on() == set(ids)
    flags.set_flag(conn, KEY, "all", actor="test")
    assert on() == set(ids)
    flags.set_flag(conn, KEY, "off", actor="test")
    assert on() == set()
    flags.set_flag(conn, KEY, "cohort", cohort="family", actor="test")
    assert on() == set()
    flags.cohort_add(conn, ids["m1"], "family", actor="test")
    assert on() == {"m1"}
    assert flags.cohort_remove(conn, ids["m1"], "family", actor="test") and on() == set()
    assert flags.enabled_keys(conn, ids["b1"]) == []
    with pytest.raises(KeyError):
        flags.enabled(conn, ids["b1"], "typo_feature")
    with pytest.raises(ValueError):
        flags.set_flag(conn, KEY, "percent", actor="test")             # percent needs --percent


def test_cache_expires_and_set_clears_it(conn, users, monkeypatch):
    ids, _ = users
    reset(conn, ids)
    now = [1000.0]
    monkeypatch.setattr(flags, "_clock", lambda: now[0])
    flags.set_flag(conn, KEY, "all", actor="test")
    assert flags.enabled(conn, ids["m1"], KEY)
    conn.execute("UPDATE feature_flags SET state = 'off' WHERE key = %s", (KEY,))   # another process changed it
    assert flags.enabled(conn, ids["m1"], KEY)                                       # still cached
    now[0] += flags.CACHE_TTL_S + 1
    assert not flags.enabled(conn, ids["m1"], KEY)                                   # expired -> re-read
    flags.set_flag(conn, KEY, "cohort", actor="test")
    assert flags.enabled(conn, ids["b1"], KEY)                                       # set_flag clears at once
    conn.execute("DELETE FROM user_cohorts WHERE user_id = %s", (ids["b1"],))
    assert flags.enabled(conn, ids["b1"], KEY)                                       # membership cached too
    now[0] += flags.CACHE_TTL_S + 1
    assert not flags.enabled(conn, ids["b1"], KEY)


# ---------------------------------------------------------------- consent follows the flags
def test_consent_version_follows_flags(conn, users):
    ids, _ = users
    reset(conn, ids)
    text = consent.version()
    # no pinned base, no flag requirements: the behaviour before flags (agreed to an old version -> reconfirm)
    assert consent.required_version(conn, ids["m1"]) == text
    assert consent.status(conn, ids["m1"])["reconfirm"] is True
    # a new text goes to beta first: base pinned to the old version, the flag needs the new one
    flags.set_setting(conn, consent.BASE_SETTING, OLD, actor="test")
    flags.set_flag(conn, KEY, "cohort", min_consent=text, actor="test")
    assert consent.required_version(conn, ids["b1"]) == text and consent.status(conn, ids["b1"])["reconfirm"] is True
    assert consent.required_version(conn, ids["m1"]) == OLD and consent.status(conn, ids["m1"])["reconfirm"] is False
    assert not flags.enabled(conn, ids["b1"], KEY)          # targeted, but not on until the new text is agreed
    consent.accept(conn, ids["b1"], text, "app")
    assert flags.enabled(conn, ids["b1"], KEY) and consent.status(conn, ids["b1"])["reconfirm"] is False
    # full rollout: now everyone is asked
    flags.set_flag(conn, KEY, "all", actor="test")
    assert consent.status(conn, ids["m1"])["reconfirm"] is True and not flags.enabled(conn, ids["m1"], KEY)
    assert flags.enabled(conn, ids["owner"], KEY) and consent.status(conn, ids["owner"])["reconfirm"] is False
    # never newer than the text itself; clearing the requirement / the base restores the default
    flags.set_setting(conn, consent.BASE_SETTING, "2999-01-01", actor="test")
    assert consent.base_version(conn) == text
    with pytest.raises(ValueError):
        flags.set_flag(conn, KEY, "all", min_consent="2999-01-01", actor="test")
    flags.set_flag(conn, KEY, "all", clear_min_consent=True, actor="test")
    flags.set_setting(conn, consent.BASE_SETTING, None, actor="test")
    assert consent.required_version(conn, ids["m1"]) == text and flags.enabled(conn, ids["m1"], KEY)


def test_version_order():
    assert consent.vkey("2026-10-18.10") > consent.vkey("2026-10-18.9") > consent.vkey("2026-10-18")
    assert consent.vkey("2026-10-20") > consent.vkey("2026-10-18.3")
    assert consent.newest("2026-10-18.2", "2026-10-18.10", "2026-01-01") == "2026-10-18.10"


# ---------------------------------------------------------------- endpoints
@pytest.fixture(scope="module")
def client(dsn):
    registry.FLAGS[KEY] = "测试功能"   # require() checks the key when the route is built (module fixtures come first)
    gated = APIRouter()

    @gated.get("/v1/test-rollout/gated", dependencies=[Depends(flags.require(KEY))])
    def gated_endpoint():
        return {"ok": True}
    api.app.include_router(gated)
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    registry.FLAGS.pop(KEY, None)


def H(t):
    return {"Authorization": f"Bearer {t}"}


def test_gated_endpoint_and_me(client, conn, users):
    ids, tok = users
    reset(conn, ids)
    flags.set_flag(conn, KEY, "cohort", actor="test")
    experiments.start(conn, "exp_demo", experiments.parse_variants("a:50,b:50"), cohort="beta", actor="test")
    conn.commit()
    flags.invalidate()
    assert client.get("/v1/test-rollout/gated", headers=H(tok["b1"])).json() == {"ok": True}
    r = client.get("/v1/test-rollout/gated", headers=H(tok["m1"]))
    assert r.status_code == 404                                      # off for m1: the endpoint itself is gone
    me = client.get("/v1/web/me", headers=H(tok["b1"])).json()
    assert me["flags"] == [KEY] and set(me["experiments"]) == {"exp_demo"}
    other = client.get("/v1/users/me/flags", headers=H(tok["m1"])).json()
    assert other == {"flags": [], "experiments": {}}
    assert client.get(f"/v1/users/{ids['b1']}/flags", headers=H(tok["m1"])).status_code == 403
    v = client.post("/v1/users/me/experiments/exp_demo/exposure", headers=H(tok["b1"])).json()["variant"]
    assert v == me["experiments"]["exp_demo"]
    assert client.post("/v1/users/me/experiments/exp_demo/exposure", headers=H(tok["m1"])).json() == {"variant": None}
    flags.set_flag(conn, KEY, "off", actor="test")
    conn.commit()
    flags.invalidate()
    assert client.get("/v1/test-rollout/gated", headers=H(tok["b1"])).status_code == 404   # rolled back: gone again


# ---------------------------------------------------------------- experiments
def test_experiment_variants_window_and_exposures(conn, users, monkeypatch):
    ids, _ = users
    reset(conn, ids)
    vs = experiments.parse_variants("control:70,new:30")
    experiments.start(conn, "exp_w", vs, actor="test")
    picks = [experiments._pick({"key": "exp_w", "variants": vs}, u) for u in range(20000)]
    assert abs(picks.count("new") / len(picks) - 0.30) < 0.012
    assert [experiments._pick({"key": "exp_w", "variants": vs}, u) for u in range(50)] == picks[:50]   # stable
    v = experiments.variant(conn, ids["m1"], "exp_w")
    assert v in ("control", "new") and experiments.variant(conn, ids["m1"], "exp_w") == v
    for _ in range(2):
        experiments.variant(conn, ids["m1"], "exp_w", expose=True)
    experiments.variant(conn, ids["m2"], "exp_w", expose=True)
    res = experiments.results(conn, "exp_w")
    by = {x["name"]: x for x in res["variants"]}
    assert sum(x["users"] for x in by.values()) == 2 and sum(x["exposures"] for x in by.values()) == 3
    assert by[v]["exposures"] >= 2
    rows = conn.execute("SELECT * FROM experiment_exposures WHERE experiment_key = 'exp_w' AND user_id = %s",
                        (ids["m1"],)).fetchall()
    assert len(rows) == 1 and rows[0]["exposures"] == 2 and set(rows[0]) == {
        "experiment_key", "user_id", "day", "variant", "exposures", "first_at", "last_at"}   # no content stored
    with pytest.raises(ValueError):
        experiments.start(conn, "exp_w", experiments.parse_variants("control:50,new:50"), actor="test")
    # cohort restriction, window, stop
    experiments.start(conn, "exp_beta", experiments.parse_variants("a,b"), cohort="beta", days=7, actor="test")
    assert experiments.variant(conn, ids["m1"], "exp_beta") is None and experiments.variant(conn, ids["b1"], "exp_beta")
    monkeypatch.setattr(experiments, "_now", lambda: datetime.now(timezone.utc) + timedelta(days=8))
    assert experiments.variant(conn, ids["b1"], "exp_beta") is None                   # ended
    monkeypatch.undo()
    assert experiments.stop(conn, "exp_w", actor="test") and experiments.variant(conn, ids["m1"], "exp_w") is None
    assert experiments.assignments(conn, ids["b1"]) == {"exp_beta": experiments.variant(conn, ids["b1"], "exp_beta")}
    # metric comparisons plug in later
    monkeypatch.setitem(experiments.METRICS, "users_seen", lambda c, k, by_variant: {n: len(u) for n, u in by_variant.items()})
    assert sum(experiments.results(conn, "exp_w")["metrics"]["users_seen"].values()) == 2
    with pytest.raises(ValueError):
        experiments.parse_variants("only_one:100")


# ---------------------------------------------------------------- admin commands and audit
def test_cli_and_audit(dsn, users, monkeypatch, capsys):
    ids, _ = users
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        reset(c, ids)
        c.execute("DELETE FROM rollout_audit")
    monkeypatch.setattr(cli, "connect", lambda: psycopg.connect(dsn, row_factory=dict_row))

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["body-engine", *argv])
        cli.main()
        return capsys.readouterr().out

    assert "done" in run("cohort", "add", "--user-id", str(ids["m1"]), "--user-id", str(ids["m2"]), "--by", "admin")
    assert {r["user_id"] for r in map(json.loads, run("cohort", "list", "--cohort", "beta").splitlines())} == \
        {ids[u] for u in ("b1", "b2", "m1", "m2")}
    assert json.loads(run("flags", "set", KEY, "--state", "percent", "--percent", "20", "--by", "admin"))["percent"] == 20
    listed = run("flags", "list")
    assert KEY in listed and "percent" in listed and "consent base version" in listed
    run("experiments", "start", "exp_cli", "--variants", "a:1,b:1", "--cohort", "beta", "--by", "admin")
    assert "stopped" in run("experiments", "stop", "exp_cli", "--by", "admin")
    with pytest.raises(SystemExit):
        run("flags", "set", "typo_feature", "--state", "all")
    audit = [json.loads(l) for l in run("flags", "audit").splitlines()]
    assert [a["action"] for a in audit][:4] == ["experiment.stop", "experiment.start", "flag.set", "cohort.add"]
    assert all(a["actor"] == "admin" for a in audit[:5])
    flag_set = next(a for a in audit if a["action"] == "flag.set")
    assert flag_set["before"]["state"] == "off" and flag_set["after"]["state"] == "percent"
