"""2.0.0 S9 regression (docs/ios-native-2.0.md section 4 S9): the server stops computing for app-mode users.

One synthetic user's phone uploads every kind (daily, sleep, workouts, stress, healthspan, reminders) and its scores;
every package is flipped to primary with `app-compute primary` (refused first: too few spot checks; --force audited).
Then the scheduled jobs (hk-derive-run, hourly-batch compute-scores + evaluate-rules, sync-refresh-run) write nothing for
that user, every app-serving endpoint returns the uploaded values, the AI / MCP tools still answer, the endpoints and jobs
fall back to computing (recorded) where nothing was uploaded, and a user with nothing on is unaffected.
The spot checks' Python twins are replaced by "the server agrees" (their equality is the per-kind tests' and the goldens'
job); everything else is the real code. Fictional data only. Separate test database BODY_TEST_DB_NAME_NATIVE_S9 (default
body_test_native_s9), recreated each run; skipped without Postgres.
"""
import argparse
import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, batch, cli
from body_engine.algo import versions
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.cardio import hrr
from body_engine.config import Settings
from body_engine.insights import jobs as insights_jobs
from body_engine.training import importer as training_importer
from body_engine.db import migrate
from body_engine.nativesync import appscores, derived_kinds, params as P, primary
from body_engine.rawdata import derive
from body_engine.rollout import flags
from body_engine.rules import engine as rules_engine
from body_engine.scores import params as SP
from body_engine.sync import refresh
from body_engine.toolspec import fill_defaults

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_NATIVE_S9", "body_test_native_s9")
TZ = "Australia/Sydney"
HR = "HKQuantityTypeIdentifierHeartRate"
COHORT = "inner_test"
# tables the server computes into: none may change for the primary user while the jobs run
COMPUTED = ("daily_metric_sources", "daily_metrics", "sleep_sessions", "workouts", "workout_hrr", "stress_daily",
            "healthspan_weekly", "reminders", "rule_runs", "daily_scores")


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
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None


@pytest.fixture()
def server_agrees(monkeypatch):
    """Every kind's spot check answers the phone's own rows (agreement), the scores' check has nothing to compare."""
    for mod in derived_kinds.kinds().values():
        monkeypatch.setattr(mod, "recompute", lambda conn, uid, day, uploaded: uploaded[day])
    monkeypatch.setattr(appscores, "server_rows", lambda conn, uid, days, end=None: {})


def H(tok):
    return {"Authorization": f"Bearer {tok}", "X-App-Version": "2.0.0"}


def snapshot(c, uid: int) -> dict:
    return {t: c.execute(f"""SELECT count(*) AS n, md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) AS h
                             FROM {t} x WHERE user_id = %s""", (uid,)).fetchone() for t in COMPUTED}


# ---------------------------------------------------------------- the phone's rows (fictional)
def daily_rows(steps: float) -> list[dict]:
    row = {"metric": "step_count", "source": "app", "value": steps, "unit": "count", "agg": "sum", "n": 40,
           "min_value": None, "max_value": None, "flags": {}}
    return [{"role": "source", **row}, {"role": "chosen", **row}]


def sleep_rows(d: date, asleep: int) -> list[dict]:
    end = datetime.combine(d, datetime.min.time(), ZoneInfo(TZ)) + timedelta(hours=7)
    start = end - timedelta(minutes=asleep + 20)
    return [{"start": start.timestamp(), "end": end.timestamp(), "asleep_min": asleep, "core_min": asleep - 120,
             "deep_min": 60, "rem_min": 60, "awake_min": 20, "unspecified_min": 0, "is_main": True, "device": "Watch",
             "segments": []}]


def workout_row(d: date, ext: str, trimp: float) -> dict:
    start = datetime.combine(d, datetime.min.time(), ZoneInfo(TZ)) + timedelta(hours=18)
    return {"external_id": ext, "type": "running", "name_zh": "跑步", "local_date": d.isoformat(),
            "start": start.timestamp(), "end": (start + timedelta(minutes=40)).timestamp(), "duration_min": 40.0,
            "active_kcal": 300.0, "total_kcal": 360.0, "distance_km": 6.0, "avg_hr": 150.0, "max_hr": 172.0,
            "zone_offset": None, "device": "Watch", "origin": "Watch", "excluded": None, "dup_of": None, "trimp": trimp,
            "hr_samples": 400, "hr_max_used": 190.0, "zone1_min": 2.0, "zone2_min": 8.0, "zone3_min": 20.0,
            "zone4_min": 8.0, "zone5_min": 2.0, "hr_recovery_bpm": 31.0, "intensity": "hard", "hrr60": 31.0,
            "hrr_peak": 168.0, "hrr_60s": 137.0, "hrr_valid": True, "hrr_reason": None, "flags": {}}


def stress_rows(score: float) -> list[dict]:
    return [{"score": score, "level": "medium", "low_h": 5, "medium_h": 6, "high_h": 1, "peak_hour": 15,
             "hours": [{"hour": 15, "score": 2.1, "level": "high", "hr": 88.0, "samples": 30, "hrv_z": None}],
             "baseline": None, "flags": {}}]


def healthspan_rows(week: date, today: date, body_age: float) -> list[dict]:
    return [{"as_of": min(week + timedelta(days=6), today).isoformat(), "chrono_age": 25.2, "body_age": body_age,
             "delta_years": body_age - 25.2, "pace_of_aging": 0.9, "pace_raw": 0.88, "pace_window_weeks": 8,
             "contributors": [{"key": "steps", "years": -0.4}], "insufficient": []}]


def reminder_rows(d: date, title: str) -> list[dict]:
    key = rules_engine.RULE_KEYS[0]
    return [{"role": "run", "now": f"{d.isoformat()}T23:59:00+11:00",
             "rules": [{"rule_key": k, "status": "triggered" if k == key else "not_triggered"} for k in rules_engine.RULE_KEYS]},
            {"role": "reminder", "rule_key": key, "subject": "s9", "level": "info", "title": title, "message": "m",
             "title_en": "", "message_en": "", "evidence": {}, "sticky": False, "status": None}]


# ---------------------------------------------------------------- the test
def test_primary_user_server_computes_nothing_and_serves_the_uploads(client, dsn, server_agrees):
    today = datetime.now(ZoneInfo(TZ)).date()
    days = [today - timedelta(days=i) for i in (3, 2, 1)]
    monday = today - timedelta(days=today.weekday())
    weeks = [monday - timedelta(days=14), monday - timedelta(days=7), monday]
    with conn_of(dsn) as c:
        def add(name):
            return c.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                                VALUES (%s, 'member', %s, now(), '2026-10-22.2') RETURNING id""", (name, TZ)).fetchone()["id"]
        uid, off = add("phone"), add("off")
        for key in [P.FLAG, P.COMPUTE_PRIMARY_FLAG, *(s["flag"] for s in P.COMPUTE_PACKAGES.values())]:
            flags.set_flag(c, key, "cohort", cohort=COHORT, actor="test")
        flags.cohort_add(c, uid, COHORT, actor="test")
        tok = auth.create_token(c, uid, "app", ["read", "write"])
        tok_off = auth.create_token(c, off, "app", ["read", "write"])
        # the server's own import of the phone's workouts (rawdata/derive): the phone's upload enriches these rows
        for i, d in enumerate(days):
            w = workout_row(d, f"w-{i}", 0)
            c.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at,
                                               duration_min, active_kcal, total_kcal)
                         VALUES (%s, 'app', %s, 'running', '跑步', %s, to_timestamp(%s), to_timestamp(%s), 40, 300, 360)""",
                      (uid, w["external_id"], d, w["start"], w["end"]))
        # the other user: a day of data and a queued day, nothing turned on
        c.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                     VALUES (%s, %s, 'step_count', 4321, 'count', 'sum', 'ow')""", (off, days[-1]))
        c.execute("INSERT INTO hk_dirty_days (user_id, day, types) VALUES (%s, %s, %s)", (off, days[-1], [HR]))
        c.commit()
    flags.invalidate()

    def upload(kind, items, bundle):
        mod = derived_kinds.get(kind)
        r = client.put("/v1/users/me/derived/app", headers=H(tok),
                       json={"kind": kind, "algo_version": versions.current(mod.ALGO_KEY), "bundle_version": bundle,
                             "days": [{"date": d.isoformat(), "rows": rows} for d, rows in items]})
        assert r.status_code == 200, (kind, r.text)
        return r.json()

    phone = {
        "daily": [(d, daily_rows(12000 + i)) for i, d in enumerate(days)],
        "sleep": [(d, sleep_rows(d, 431 + i)) for i, d in enumerate(days)],
        "workouts": [(d, [workout_row(d, f"w-{i}", 42.5 + i)]) for i, d in enumerate(days)],
        "stress": [(d, stress_rows(1.11 + i / 100)) for i, d in enumerate(days)],
        "healthspan": [(w, healthspan_rows(w, today, 23.1 + i / 10)) for i, w in enumerate(weeks)],
        "reminders": [(d, reminder_rows(d, f"S9 提醒 {d.isoformat()}")) for d in [*days[1:], today]],
    }
    # shadow: the phone uploads, the server only checks (one check per bundle version)
    for kind, items in phone.items():
        assert upload(kind, items, "9.0.0")["state"] == "shadow"

    # the flip: refused while the pass-rate gate has too few checks (nothing changes), --force is audited
    ns = lambda **kw: argparse.Namespace(**{"action": "primary", "package": "derive", "kind": None, "user_id": None,  # noqa: E731
                                            "cohort": COHORT, "all": False, "force": False, "days": None, "by": "test", **kw})
    with conn_of(dsn) as c:
        assert cli._app_compute(c, ns()) == 1
        assert primary.grants(c) == []
        assert cli._app_compute(c, ns(package="workouts", user_id=uid, cohort=None, force=True)) == 0
        g = primary.grants(c, "workouts")
        assert len(g) == 1 and g[0]["forced"] and g[0]["scope"] == "user"
        a = c.execute("SELECT * FROM rollout_audit WHERE action LIKE 'compute.%%' ORDER BY id").fetchall()
        assert [(x["action"], x["target"]) for x in a] == [("compute.primary.force", f"workouts:user:{uid}")]
        assert a[0]["after"]["gate"]["ok"] is False and "value" not in json.dumps(a[0]["after"])
        assert cli._app_compute(c, ns(action="shadow", package="workouts", user_id=uid, cohort=None)) == 0
        assert primary.grants(c) == []
    # two more bundle versions = three passing checks per kind: the gate passes, every package goes primary
    for bundle in ("9.0.1", "9.0.2"):
        for kind, items in phone.items():
            assert upload(kind, items, bundle)["check"]["ok"]
    with conn_of(dsn) as c:
        assert cli._app_compute(c, ns(package="all")) == 0
        g = primary.grants(c)
        assert {x["package"] for x in g} == set(P.COMPUTE_PACKAGES) and not any(x["forced"] for x in g)
        assert all(x["gate"]["ok"] for x in g)
        assert {x["action"] for x in c.execute("SELECT action FROM rollout_audit WHERE action LIKE 'compute.%%'")} == \
            {"compute.primary.force", "compute.shadow", "compute.primary"}
    j = client.get("/v1/sync/compute", headers=H(tok)).json()["compute"]["packages"]
    assert all(p["state"] == "primary" for p in j.values()), {k: p["state"] for k, p in j.items()}
    # primary: the uploads are stored as the user's rows (and the scores, #340 N2)
    for kind, items in phone.items():
        got = upload(kind, items, "9.0.2")
        assert got["state"] == "primary" and got["stored"] > 0, kind
    r = client.put("/v1/users/me/scores/app", headers=H(tok), json={
        "algo_version": SP.ALGO_VERSION, "bundle_version": "9.0.2",
        "days": [{"date": d.isoformat(), "row": {"recovery": 61 + i, "sleep_score": 80, "strain": 10.5}}
                 for i, d in enumerate(days)]})
    assert r.status_code == 200 and r.json()["app_scoring"], r.text
    with conn_of(dsn) as c:
        assert primary.primary_packages(c, uid) == set(P.COMPUTE_PACKAGES)
        assert primary.primary_packages(c, off) == set()
        # queued days: one the phone uploaded (queued before its upload), one it may still upload (today, just queued)
        up_at = c.execute("SELECT min(uploaded_at) AS t FROM app_compute_days WHERE user_id = %s", (uid,)).fetchone()["t"]
        c.execute("INSERT INTO hk_dirty_days (user_id, day, types, queued_at) VALUES (%s, %s, %s, %s), (%s, %s, %s, now())",
                  (uid, days[1], [HR, derive.SLEEP], up_at - timedelta(seconds=1), uid, today, [HR]))
        c.commit()
        before = snapshot(c, uid)

        # ---- the scheduled jobs
        res = derive.run(c)
        mine = next(r for r in res if r["user_id"] == uid)
        assert mine["phone_primary"] and mine["phone_days"] == 1 and mine["waiting"] == 1 and mine["fallback_days"] == 0
        assert [r["day"] for r in c.execute("SELECT day FROM hk_dirty_days WHERE user_id = %s", (uid,))] == [today]
        assert c.execute("SELECT count(*) AS n FROM hk_dirty_days WHERE user_id = %s", (off,)).fetchone()["n"] == 0
    lines: list[str] = []
    out = batch.run(batch.list_users(conn_of(dsn), [uid, off]), steps=("compute-scores", "evaluate-rules"), skip=False,
                    dsn=dsn, emit=lines.append, client=None)
    assert out["errors"] == [], lines
    assert any("rules 已转正" in x and f"user {uid}" in x for x in lines), lines
    with conn_of(dsn) as c:
        refresh.queue_scores(c, uid, days[0])
        assert "error" not in refresh.run_queued(c, uid)[0]
        # the jobs compute-scores runs whenever the server scores a user (here the app scores, so call them directly):
        # body age + stress, the workouts' heart rate and heart-rate recovery
        assert insights_jobs.run(c, None, uid, ow_cache="use") == {"healthspan": "app", "stress": "app"}
        assert training_importer.enrich_pending(c, None, uid) == 0
        hrr.compute(c, uid, [], {}, days[0], days[-1], skip_app=primary.server_primary(c, uid, "workouts"))
        c.commit()
        assert snapshot(c, uid) == before, "a scheduled job wrote computed rows for the primary user"
        # the other user is computed as always
        assert c.execute("SELECT count(*) AS n FROM rule_runs WHERE user_id = %s", (off,)).fetchone()["n"] == 1
        assert c.execute("SELECT count(*) AS n FROM app_compute_fallbacks").fetchone()["n"] == 0

    # ---- the app-serving endpoints return the uploaded values
    def get(path, t=tok, **params):
        r = client.get(path, headers=H(t), params=params)
        assert r.status_code == 200, (path, r.text)
        return r.json()
    s = get("/v1/users/me/stress", start=days[0].isoformat(), end=days[-1].isoformat())
    assert [x["score"] for x in s["days"]] == pytest.approx([1.11, 1.12, 1.13])
    assert get("/v1/users/me/healthspan")["body_age"] == pytest.approx(23.3)
    rem = get("/v1/users/me/reminders", start=days[1].isoformat(), end=today.isoformat())
    assert {x["title"] for x in rem} == {f"S9 提醒 {d.isoformat()}" for d in [*days[1:], today]}
    assert all(x["id"] is not None for x in rem)
    assert f"S9 提醒 {today.isoformat()}" in json.dumps(get("/v1/users/me/today"), ensure_ascii=False)
    assert "42.5" in json.dumps(get(f"/v1/users/me/training/day/{days[0].isoformat()}"))
    assert "433" in json.dumps(get("/v1/users/me/sleep/nights", end=days[-1].isoformat(), days=3))
    assert "12002" in json.dumps(get("/v1/users/me/trends", days=7))
    # ---- the AI / MCP tools still answer (on demand, decision 4)
    with conn_of(dsn) as c:
        rng = {"start": days[0].isoformat(), "end": days[-1].isoformat()}
        for name, args in [("get_stress", {**rng, "hours": False}), ("get_healthspan", {"weeks": 12}),
                           ("get_reminders", {}), ("get_training_day", {"date": days[0].isoformat()}),
                           ("get_sleep", {**rng, "main_only": False}),
                           ("get_daily_metrics", {**rng, "metrics": ["step_count"]}), ("get_scores", rng)]:
            got = run_tool(c, uid, "mcp:test", name, fill_defaults(TOOLS_BY_NAME[name].input_schema, args))
            assert got is not None, name
        c.commit()
        assert snapshot(c, uid) == before

    # ---- fallbacks: nothing uploaded -> computed on demand, not stored, recorded (ids, package, path, day only)
    old = today - timedelta(days=6)
    assert get("/v1/users/me/stress", start=old.isoformat(), end=old.isoformat())["days"][0]["date"] == old.isoformat()
    with conn_of(dsn) as c:
        c.execute("DELETE FROM rule_runs WHERE user_id = %s AND date = %s", (uid, today))
        c.execute("INSERT INTO hk_dirty_days (user_id, day, types, queued_at) VALUES (%s, %s, %s, now() - interval '3 hours')",
                  (uid, today - timedelta(days=5), [HR]))
        c.commit()
    get("/v1/users/me/reminders")
    with conn_of(dsn) as c:
        res = next(r for r in derive.run(c) if r["user_id"] == uid)
        assert res["fallback_days"] == 1 and res["waiting"] == 1
        fb = {(r["package"], r["path"], r["day"]) for r in c.execute("SELECT * FROM app_compute_fallbacks WHERE user_id = %s",
                                                                     (uid,))}
        assert fb == {("stress", "stress", old), ("rules", "reminders", today),
                      ("derive", "hk-derive-run", today - timedelta(days=5))}
        assert c.execute("SELECT count(*) AS n FROM stress_daily WHERE user_id = %s AND date = %s", (uid, old)).fetchone()["n"] == 0
        st = primary.status(c, uid)
        assert st["user"]["packages"]["rules"]["server_primary"] and len(st["user"]["fallbacks"]) == 3

    # ---- the other user: nothing primary, its endpoints as before
    j = client.get("/v1/sync/compute", headers=H(tok_off))
    assert j.status_code == 404   # native_sync is off for them
    assert get("/v1/users/me/stress", t=tok_off)["days"] is not None

    # ---- the kill switch: native_compute_primary off -> the server computes the packages again
    with conn_of(dsn) as c:
        flags.set_flag(c, P.COMPUTE_PRIMARY_FLAG, "off", actor="test")
        c.commit()
        flags.invalidate()
        assert primary.primary_packages(c, uid) == set()
