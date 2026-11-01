"""Apple sync engine, server steps S4–S6 and the upload status (docs/apple-sync-engine.md §4–5, issue #219):
- S4 rawdata/shadow.py: verdicts per item (pure judges), the comparison of the synthetic data set (OW vs the app's
  uploads of the same samples: nothing inconsistent), an injected difference, the summary and its readiness;
- S5 rawdata/cutover.py: the app-version gate, switch (mode app, workouts shown, nights replaced, values re-chosen,
  scores queued), rollback (back to shadow at once, OW's nights back), the kill switch, pin / unpin, apply, post check;
- S6 rawdata/ow_archive.py: an archived app-mode user's scoring inputs, stress and stage chart without any OW client are
  what they were with OW;
- rawdata/sync_status.py: refused uploads recorded by reason (auth / format / refused / limit), the status endpoints,
  the light status of engine batches, an engine batch in the app mode queues the recompute, and a large batch is
  stored within the 2-second budget.
The synthetic data of tests/test_apple_sync_derive.py (every number a made-up example value, 示例). Separate test database
BODY_TEST_DB_NAME_S4 (default body_test_apple_sync_s4), recreated each run; skipped without Postgres.
"""
import gzip
import json
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import test_apple_sync_derive as S2
from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import ow
from body_engine.insights import stress
from body_engine.rawdata import cutover as CO, derive as DV, engine as EN, ow_archive as OA, params as RP
from body_engine.rawdata import readiness as RD, shadow as SH
from body_engine.rollout import flags
from body_engine.scores import engine as scores_engine, inputs as SI
from body_engine.web import views

TZ, D1, D2, at = S2.TZ, S2.D1, S2.D2, S2.at
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_S4", "body_test_apple_sync_s4")
NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
HR = S2.Q + "HeartRate"


# ---------------------------------------------------------------- pure judges
def _side(value, n=None, lo=None, hi=None):
    return {"value": value, "n": n, "min": lo, "max": hi}


def test_judge_daily():
    j = SH.judge_daily
    assert j({"metric": "made_up", "agg": "avg", "unit": "", "ow": _side(1), "app": _side(1)}) is None
    ok = j({"metric": "heart_rate", "agg": "avg", "unit": "count/min", "ow": _side(70, 10, 50, 90), "app": _side(70, 10, 50, 90)})
    assert (ok["verdict"], ok["reason"]) == ("ok", None)
    same_ts = j({"metric": "heart_rate", "agg": "avg", "unit": "", "ow": _side(70, 99, 50, 90), "app": _side(70.02, 100, 50, 90)})
    assert (same_ts["verdict"], same_ts["reason"]) == ("explained", "ow_same_ts")
    bad = j({"metric": "heart_rate", "agg": "avg", "unit": "", "ow": _side(70, 10, 50, 90), "app": _side(75, 10, 50, 90)})
    assert bad["verdict"] == "mismatch"
    assert j({"metric": "step_count", "agg": "sum", "unit": "", "ow": _side(1000), "app": _side(1005)})["verdict"] == "ok"
    assert j({"metric": "step_count", "agg": "sum", "unit": "", "ow": _side(1000), "app": _side(1040)})["reason"] == "method"
    assert j({"metric": "step_count", "agg": "sum", "unit": "", "ow": _side(1000), "app": _side(1300)})["verdict"] == "mismatch"
    assert j({"metric": "step_count", "agg": "sum", "unit": "", "ow": _side(1000), "app": None})["verdict"] == "missing_app"
    only_app = j({"metric": "step_count", "agg": "sum", "unit": "", "ow": None, "app": _side(5)})
    assert (only_app["verdict"], only_app["reason"]) == ("new", "missing_ow")
    assert j({"metric": "cycling_power", "agg": "avg", "unit": "", "ow": None, "app": _side(5)})["reason"] is None


def _night(a, b, asleep, device="Watch", main=True, **stages):
    return {"start_at": a, "end_at": b, "asleep_min": asleep, "device": device, "is_main": main,
            **{k: stages.get(k, 0) for k in SH.SLEEP_FIELDS if k != "asleep_min"}}


def test_judge_sleep_and_workouts():
    a, b = at(D1, 23), at(D2, 7)
    assert SH.judge_sleep(D2, {"ow": [_night(a, b, 400)], "app": [_night(a, b, 400)]})["verdict"] == "ok"
    late = SH.judge_sleep(D2, {"ow": [_night(a, b, 400, device="iPhone")], "app": [_night(a + timedelta(minutes=10), b, 390)]})
    assert (late["verdict"], late["reason"]) == ("explained", "source_priority")
    assert SH.judge_sleep(D2, {"ow": [_night(a, b, 400)], "app": [_night(a, b, 300)]})["verdict"] == "mismatch"
    assert SH.judge_sleep(D2, {"ow": [_night(a, b, 400)], "app": []})["verdict"] == "missing_app"
    assert SH.judge_sleep(D2, {"ow": [], "app": []}) is None
    w = {"start_at": at(D1, 18), "end_at": at(D1, 18, 47), "type": "strength_training", "duration_min": 45.0,
         "total_kcal": 270.0, "active_kcal": 200.0, "avg_hr": 118.0, "max_hr": 150.0, "dup_of": None, "excluded": None}
    app = {**w, "active_kcal": 210.0, "excluded": "shadow"}
    got = SH.judge_workouts({"ow": [w], "app": [app]})
    assert [(x["verdict"], x["reason"]) for x in got] == [("explained", "active_kcal_method")]
    assert SH.judge_workouts({"ow": [w], "app": []})[0]["verdict"] == "missing_app"
    assert SH.judge_workouts({"ow": [], "app": [app]})[0]["verdict"] == "new"
    assert SH.judge_workouts({"ow": [w], "app": [{**app, "total_kcal": 300.0}]})[0]["verdict"] == "mismatch"
    assert [x["verdict"] for x in SH.judge_scores({"recovery": 60, "strain": 10.0}, {"recovery": 60.5, "strain": 10.05}, False)] \
        == ["ok", "ok"]
    assert SH.judge_scores({"recovery": 60}, {"recovery": 63}, True)[0]["reason"] == "input_diff"
    assert SH.judge_scores({"recovery": 60}, {"recovery": 63}, False)[0]["verdict"] == "mismatch"


def test_version_compare():
    assert CO._version("1.0.10") > CO._version("1.0.8") > CO._version("1.0.7+42") > CO._version(None)


def _assessed(**kw):
    a = {"mode": "shadow", "engine": True, "consent": False, "pin": None, "engine_only": False,
         "app_version": {"ok": True, "app_version": "1.0.9", "min": "1.0.8"},
         "shadow": {"blockers": [], "clean_streak": 7, "coverage": 1.0}, "scores": {"ok": True}}
    return {**a, **kw}


def test_readiness_missing_is_what_the_gates_check():
    """#219 S5b: what blocks a switch, in the order to fix it (pure)."""
    m = RD.missing
    assert m(_assessed()) == []
    assert m(_assessed(mode="app", consent=True)) == [] and m(_assessed(engine=False)) == []
    old = {"ok": False, "app_version": "1.0.7", "min": "1.0.8"}
    assert m(_assessed(app_version=old, shadow={"blockers": ["streak", "coverage"]}, scores={"ok": False})) \
        == ["app_version", "streak", "coverage", "scores"]
    assert m(_assessed(app_version={"ok": False, "app_version": None, "min": "1.0.8"}, shadow=None, consent=True,
                       pin="ow")) == ["consent", "pinned", "no_engine_upload", "no_shadow"]
    assert m(_assessed(scores={"ok": False, "recovery": {"lost": 2}})) == ["score_missing"]
    assert m(_assessed(scores={"ok": None})) == []                  # no score items yet: the gate recomputes anyway
    # an engine-only account (no OW link): only the app version, as cutover.gates
    assert m(_assessed(engine_only=True, shadow=None, scores={"ok": False}, app_version=old)) == ["app_version"]
    assert m(_assessed(engine_only=True, shadow=None)) == []


# ---------------------------------------------------------------- database
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


def conn_of(dsn):
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


def rows(dsn, sql, *params):
    with conn_of(dsn) as c:
        return c.execute(sql, params).fetchall()


ENGINE_USERS = ("S", "M", "W", "V", "H", "F")


@pytest.fixture(scope="module")
def world(dsn):
    """S: shadow (the comparison); M: shadow with an injected difference; W: switched / rolled back; V: an old app;
    H: OW only, put in the app mode and archived; F: failures and the status panel; X: engine off."""
    fake = S2.FakeOW()
    mp = pytest.MonkeyPatch()
    mp.setattr(RP, "CUTOVER_CLEAN_DAYS", 2)       # the data set has two days
    mp.setattr(RP, "CUTOVER_APP_SEEN", timedelta(days=36500))
    with conn_of(dsn) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, role, consent_at, consent_version, timezone, birth_date,
                                                     sex, height_cm, ow_user_id)
                                  VALUES (%s, 'member', '2026-10-01', '2026-10-22.1', 'Australia/Sydney', '1990-01-01',
                                          'male', 180, %s) RETURNING id""", (n, f"ow-{n}")).fetchone()["id"]
               for n in (*ENGINE_USERS, "X")}
        for n in ENGINE_USERS:
            flags.cohort_add(conn, ids[n], "beta", actor="test")
        flags.set_flag(conn, EN.FLAG, "cohort", cohort="beta", actor="test")
        flags.set_flag(conn, EN.PRIMARY_FLAG, "cohort", cohort=CO.PRIMARY_COHORT, actor="test")
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    client = TestClient(api.app, base_url="https://testserver")
    with conn_of(dsn) as conn:
        for n in (*ENGINE_USERS, "X"):
            ow.run(conn, fake, ids[n], D1, D2)
    for n in ("S", "M", "W", "V"):
        for b in S2.app_batches():
            if n == "V":
                b["app_version"] = "1.0.7"
            r = client.post("/v1/ingest/healthkit", json=b, headers={"Authorization": f"Bearer {tok[n]}"})
            assert r.status_code == 200, r.text
    with conn_of(dsn) as conn:
        # M: the app lost its heart rate of D1 (an injected difference)
        conn.execute("DELETE FROM hk_samples WHERE user_id = %s AND type = %s AND local_date = %s", (ids["M"], HR, D1))
        EN.mark_dirty(conn, ids["M"], {(HR, D1)})
        DV.run(conn)
        for n in ("S", "M", "W", "V"):
            scores_engine.run(conn, fake, ids[n])
    yield {"ids": ids, "tok": tok, "client": client, "fake": fake}
    api.pool.close()
    mp.undo()


def H(world, n):
    return {"Authorization": f"Bearer {world['tok'][n]}"}


def test_shadow_comparison_is_clean(world, dsn):
    ids, fake = world["ids"], world["fake"]
    with conn_of(dsn) as conn:
        res = SH.compare_user(conn, ids["S"], D1, D2, fake, with_scores=True, now=NOW)
    assert res["counts"].get("mismatch", 0) == 0 and res["counts"].get("missing_app", 0) == 0, \
        rows(dsn, "SELECT * FROM sync_shadow_diffs WHERE user_id = %s AND verdict <> 'ok'", ids["S"])
    assert res["counts"]["ok"] > 20
    kinds = {r["kind"] for r in rows(dsn, "SELECT DISTINCT kind FROM sync_shadow_diffs WHERE user_id = %s", ids["S"])}
    assert {"daily", "sleep", "workout", "score"} <= kinds
    s = res["summary"]
    assert (s["clean_streak"], s["ready"], s["blockers"]) == (2, True, [])
    assert s["coverage"] == 1.0


def test_shadow_finds_the_injected_difference(world, dsn):
    ids = world["ids"]
    with conn_of(dsn) as conn:
        res = SH.compare_user(conn, ids["M"], D1, D2, None, with_scores=False, now=NOW)
    bad = rows(dsn, "SELECT date, kind, key, verdict FROM sync_shadow_diffs WHERE user_id = %s AND verdict IN ('mismatch', 'missing_app')",
               ids["M"])
    assert {(r["date"], r["key"], r["verdict"]) for r in bad} == {(D1, "heart_rate", "missing_app")}
    assert res["summary"]["ready"] is False and "streak" in res["summary"]["blockers"]
    assert res["summary"]["clean_streak"] == 1   # D2 clean, D1 not
    with conn_of(dsn) as conn:
        text = SH.markdown(conn, [ids["M"]], D1)
    assert "heart_rate" in text and "app 缺" in text


def test_shadow_endpoint_own_data(world):
    c, ids = world["client"], world["ids"]
    r = c.get(f"/v1/users/me/sync/engine/shadow?date={D1}&end={D2}", headers=H(world, "M"))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["key"] for i in items if i["verdict"] in SH.BAD] == ["heart_rate"] and r.json()["summary"]["ready"] is False
    assert all(i["verdict"] != "ok" for i in items)
    assert c.get(f"/v1/users/{ids['S']}/sync/engine/shadow", headers=H(world, "M")).status_code == 403
    assert c.get("/v1/users/me/sync/engine/shadow", headers=H(world, "X")).status_code == 404


def test_old_app_is_never_switched(world, dsn):
    ids, fake = world["ids"], world["fake"]
    with conn_of(dsn) as conn:
        SH.compare_user(conn, ids["V"], D1, D2, None, now=NOW)
        res = CO.switch(conn, ids["V"], fake, force=True)
        assert res["switched"] is False and res["gates"]["app_version"]["app_version"] == "1.0.7"
        assert EN.mode(conn, ids["V"]) == "shadow"


def test_new_account_without_ow_needs_only_the_app_version(world, dsn):
    ids, fake = world["ids"], world["fake"]
    uid = ids["V"]
    with conn_of(dsn) as conn:
        conn.execute("UPDATE users SET ow_user_id = NULL WHERE id = %s", (uid,))
        g = CO.gates(conn, fake, uid)
        assert g["shadow"] == {"ok": True, "reason": "no_ow"} and g["ok"] is False   # still the 1.0.7 app
        conn.execute("UPDATE rawdata_uploads SET app_version = '1.0.8' WHERE user_id = %s", (uid,))
        assert CO.gates(conn, fake, uid)["ok"] is True
        conn.execute("UPDATE rawdata_uploads SET app_version = '1.0.7' WHERE user_id = %s", (uid,))
        conn.execute("UPDATE users SET ow_user_id = %s WHERE id = %s", ("ow-V", uid))


def test_switch_rollback_and_back(world, dsn):
    ids, fake, uid = world["ids"], world["fake"], world["ids"]["W"]
    with conn_of(dsn) as conn:
        SH.compare_user(conn, uid, D1, D2, fake, with_scores=False, now=NOW)
        assert CO.switch(conn, uid, fake, dry_run=True)["would_switch"] is True
        assert EN.mode(conn, uid) == "shadow"
        res = CO.switch(conn, uid, fake)
        assert res["switched"] is True, res
        assert res["gates"]["scores"]["ok"] is True
        assert EN.mode(conn, uid) == "app" and CO.desired(conn, uid) == "app"
    assert {r["excluded"] for r in rows(dsn, "SELECT excluded FROM workouts WHERE user_id = %s AND source = 'app'", uid)} == {None}
    assert {r["source"] for r in rows(dsn, "SELECT source FROM sleep_sessions WHERE user_id = %s AND wake_date = %s", uid, D2)} \
        == {"app"}
    assert rows(dsn, "SELECT source FROM daily_metrics WHERE user_id = %s AND date = %s AND metric = 'heart_rate'", uid, D1) \
        == [{"source": "app"}]
    assert rows(dsn, "SELECT status FROM sync_refresh WHERE user_id = %s", uid)[0]["status"] == "scores_queued"
    st = rows(dsn, "SELECT pre_scores, switched_at FROM sync_mode_state WHERE user_id = %s", uid)[0]
    assert st["pre_scores"] and st["switched_at"]

    # the first recompute in the app mode, then the post-switch check
    with conn_of(dsn) as conn:
        scores_engine.run(conn, fake, uid)
        pc = CO.post_check(conn, uid)
    assert pc["ok"] is True, pc

    # rollback: shadow at once, OW's nights and values back
    with conn_of(dsn) as conn:
        rb = CO.rollback(conn, uid, fake)
        assert rb["mode"] == "shadow" and rb["nights_removed"] >= 1
        assert CO.desired(conn, uid) == "shadow"
    assert {r["excluded"] for r in rows(dsn, "SELECT excluded FROM workouts WHERE user_id = %s AND source = 'app'", uid)} \
        == {"shadow"}
    assert {r["source"] for r in rows(dsn, "SELECT source FROM sleep_sessions WHERE user_id = %s AND wake_date = %s", uid, D2)} \
        == {"ow"}
    assert rows(dsn, "SELECT source FROM daily_metrics WHERE user_id = %s AND date = %s AND metric = 'heart_rate'", uid, D1) \
        == [{"source": "ow"}]

    # pinned: apply leaves the user alone; unpin + apply switches again (the flag still wants app)
    with conn_of(dsn) as conn:
        flags.cohort_add(conn, uid, CO.PRIMARY_COHORT, actor="test")
        assert [r["action"] for r in CO.apply(conn, uid, fake)] == ["none"]
        CO.unpin(conn, uid)
        assert [r["action"] for r in CO.apply(conn, uid, fake)] == ["switch"]
        assert EN.mode(conn, uid) == "app"
        # the kill switch: everyone back at once, apply cleans up
        flags.set_flag(conn, EN.PRIMARY_FLAG, "off", actor="test")
        assert EN.mode(conn, uid) == "shadow"
        assert [r["action"] for r in CO.apply(conn, uid, fake)] == ["rollback"]
        flags.set_flag(conn, EN.PRIMARY_FLAG, "cohort", cohort=CO.PRIMARY_COHORT, actor="test")
    assert {r["source"] for r in rows(dsn, "SELECT source FROM sleep_sessions WHERE user_id = %s AND wake_date = %s", uid, D2)} \
        == {"ow"}


def _put_in_app_mode(conn, uid):
    flags.cohort_add(conn, uid, CO.PRIMARY_COHORT, actor="test")
    CO._set_state(conn, uid, mode="app", pin=None)
    conn.commit() if not conn.autocommit else None


def test_archive_replaces_ow(world, dsn):
    """H has only OW data and is in the app mode: after ow-archive, no OW client is needed for the same inputs."""
    ids, fake, uid = world["ids"], world["fake"], world["ids"]["H"]
    lo, hi = at(D1, 0), at(D2 + timedelta(days=1), 0)
    with conn_of(dsn) as conn:
        _put_in_app_mode(conn, uid)
        assert EN.mode(conn, uid) == "app"
        live = SI.load(conn, fake, uid, D1, D2, None)
        stress_live = stress.compute(conn, fake, uid, D1, D2)
        res = OA.archive(conn, fake, uid, now=NOW)
        assert res["segments"] > 0 and res["samples"]["heart_rate"] > 0
        assert OA.check(conn, uid)["complete"] is True
        arch = SI.load(conn, None, uid, D1, D2, None)
        for key in ("hr", "hrv", "resp", "spo2", "temp"):
            assert getattr(arch, key) == getattr(live, key), key
        assert arch.segments == live.segments
        assert stress.compute(conn, None, uid, D1, D2) == stress_live
        assert all("no_raw_hr" not in r["flags"] for r in stress_live), "the archive must carry the raw heart rate"
        stages, src, _ = views.night_stages(conn, uid, TZ, at(D1, 22), at(D2, 8))
        assert src == "ow" and stages
    assert OA.reads_archive.__name__   # the module is wired


def test_archive_is_ignored_outside_the_app_mode(world, dsn):
    ids = world["ids"]
    with conn_of(dsn) as conn:
        assert OA.reads_archive(conn, ids["S"]) is False


# ---------------------------------------------------------------- uploads: failures, status, app-mode queue, latency
def test_failures_are_recorded_by_reason(world, dsn):
    c, ids, uid = world["client"], world["ids"], world["ids"]["F"]
    url = "/v1/ingest/healthkit"
    # format: not JSON, bad gzip
    assert c.post(url, content=b"{nope", headers={**H(world, "F"), "Content-Type": "application/json"}).status_code == 422
    assert c.post(url, content=b"xx", headers={**H(world, "F"), "Content-Encoding": "gzip"}).status_code == 400
    # refused: the engine is off for X
    b = S2.app_batches()[0]
    assert c.post(url, json=b, headers=H(world, "X")).status_code == 409
    # limit
    mp = pytest.MonkeyPatch()
    mp.setattr(EN, "BATCHES_PER_MINUTE", 0)
    mp.setattr(EN, "BIG_BATCHES_PER_MINUTE", 0)
    for _ in range(3):
        assert c.post(url, json={**b, "batch_id": str(uuid.uuid4())}, headers=H(world, "F")).status_code == 429
    mp.undo()
    # auth: a revoked token is still attributed to its owner
    with conn_of(dsn) as conn:
        tok = auth.create_token(conn, uid, "old", ["read", "write"])
        conn.execute("UPDATE api_tokens SET revoked_at = now() WHERE token_sha256 = %s", (auth.hash_token(tok),))
    assert c.post(url, json=b, headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    got = {(r["reason"], r["code"]): r["n"] for r in rows(dsn, "SELECT reason, code, n FROM ingest_failures WHERE user_id = %s", uid)}
    assert got == {("format", "validation"): 1, ("format", "bad_gzip"): 1, ("limit", "rate_limited"): 3,
                   ("auth", "token_revoked"): 1}
    assert rows(dsn, "SELECT reason, code FROM ingest_failures WHERE user_id = %s", ids["X"]) == \
        [{"reason": "refused", "code": "engine_disabled"}]

    # then a good upload: the panel shows it, the failures as recovered, the newest sample per type
    r = c.post(url, json={**b, "batch_id": str(uuid.uuid4())}, headers=H(world, "F"))
    assert r.status_code == 200 and r.json()["status"]["counts"] is False and "metrics" not in r.json()["status"]
    for path, hdr in (("/v1/users/me/sync/engine/status", H(world, "F")), ("/v1/ingest/sync-status", H(world, "F"))):
        st = c.get(path, headers=hdr).json()
        assert st["mode"] == "shadow" and st["engine_enabled"] is True
        assert st["last_upload"]["app_version"] == "1.0.8" and st["last_upload"]["phase"] == "backfill"
        assert st["failures"]["counts_24h"] == {"auth": 1, "format": 2, "refused": 0, "limit": 3, "server": 0}
        assert st["failures"]["last"]["reason"] == "auth" and st["failures"]["recovered"] is True
        hr = next(t for t in st["types"] if t["type"] == HR)
        assert hr["latest_sample_at"].startswith(str(D2)) and hr["status"] == "heart_rate"
        assert st["pipeline"]["pending_days"] == 2
    assert c.get(f"/v1/users/{ids['S']}/sync/engine/status", headers=H(world, "F")).status_code == 403


def test_app_mode_upload_queues_the_recompute(world, dsn):
    c, uid = world["client"], world["ids"]["H"]   # H is in the app mode (test_archive_replaces_ow)
    with conn_of(dsn) as conn:
        _put_in_app_mode(conn, uid)
        conn.execute("DELETE FROM sync_refresh WHERE user_id = %s", (uid,))
    b = S2.app_batches()[0]
    r = c.post("/v1/ingest/healthkit", json=b, headers=H(world, "H"))
    assert r.status_code == 200, r.text
    q = rows(dsn, "SELECT status, scores_from FROM sync_refresh WHERE user_id = %s", uid)
    assert q and q[0]["status"] == "scores_queued" and q[0]["scores_from"] == D1
    # an on-demand refresh in the app mode imports nothing from OW: it only queues
    with conn_of(dsn) as conn:
        conn.execute("UPDATE sync_refresh SET requested_at = NULL, status = 'idle' WHERE user_id = %s", (uid,))
    r = c.post("/v1/users/me/sync/refresh", headers=H(world, "H"))
    assert r.json()["accepted"] is True
    assert rows(dsn, "SELECT import_queued_at FROM sync_refresh WHERE user_id = %s", uid)[0]["import_queued_at"] is None


def test_large_batch_within_two_seconds(world):
    """A foreground page of the engine (5000 heart-rate samples + 30 days of 15-minute buckets, gzip) is stored and
    answered within the 2-second budget (generous for a CI machine; production measured separately)."""
    c = world["client"]
    t0 = int(at(D1 - timedelta(days=40), 0).timestamp())
    samples = {"type": HR, "unit": "count/min", "uuid": [str(uuid.uuid4()) for _ in range(5000)],
               "start": [t0 + 60 * i for i in range(5000)], "end": [t0 + 60 * i + 1 for i in range(5000)],
               "value": [60.0 + i % 40 for i in range(5000)], "src": [0] * 5000}
    stats = {"type": S2.Q + "StepCount", "unit": "count", "start": t0 - t0 % 900, "values": [float(i % 50) for i in range(2880)]}
    body = {"batch_id": str(uuid.uuid4()), "app_version": "1.0.8", "phase": "backfill", "tz": "Australia/Sydney",
            "sources": S2.SOURCES[:1], "samples": [samples], "stats": [stats]}
    raw = gzip.compress(json.dumps(body).encode())
    hdr = {**H(world, "F"), "Content-Encoding": "gzip", "Content-Type": "application/json"}
    t = time.perf_counter()
    r = c.post("/v1/ingest/healthkit", content=raw, headers=hdr)
    took = time.perf_counter() - t
    assert r.status_code == 200, r.text
    assert r.json()["samples"] == 5000 and took < 2.0, took
    print(f"engine batch of 5000 samples + 2880 buckets: {took:.2f} s")
    # the same batch again (a lost answer on a weak network): answered from the log, nothing stored twice
    r2 = c.post("/v1/ingest/healthkit", content=raw, headers=hdr)
    assert r2.status_code == 200 and r2.json()["duplicate_batch"] is True


def test_readiness_report(world, dsn):
    """#219 S5b `sync-mode readiness`: S (clean comparison, app 1.0.8) can be switched, M (an injected difference) not
    yet, V (the 1.0.7 app) not; the switches on top; nothing written but the declared flag rows."""
    ids = world["ids"]
    with conn_of(dsn) as conn:
        SH.compare_user(conn, ids["S"], D1, D2, world["fake"], with_scores=True, now=NOW)
        rep = RD.report(conn, [ids["S"], ids["M"], ids["V"]])
        assert set(RD.users(conn)) >= {ids["S"], ids["M"], ids["V"], ids["X"]}   # OW link or engine data
    by = {a["user_id"]: a for a in rep["users"]}
    s, m, v = by[ids["S"]], by[ids["M"]], by[ids["V"]]
    assert (s["status"], s["missing"]) == ("ready", []), s
    assert s["scores"]["ok"] is True and s["scores"]["days"] == 2 and s["shadow"]["clean_streak"] == 2
    assert m["status"] == "blocked" and "streak" in m["missing"]
    assert v["status"] == "blocked" and v["missing"][0] == "app_version" and v["app_version"]["app_version"] == "1.0.7"
    assert rep["counts"]["ready"] == 1 and rep["counts"]["blocked"] == 2
    sw = rep["switches"]
    assert sw["engine"]["state"] == "cohort" and sw["primary"]["cohort"] == CO.PRIMARY_COHORT
    assert sw["new_accounts"] == {"on": False, "flag": "off", "engine": "cohort", "reason": "flag_off"}
    text = RD.markdown(rep)
    assert "可以切换" in text and "app 1.0.7 < 1.0.8" in text and "连续一致 1/2 天" in text
    assert EN.NEW_ACCOUNTS_FLAG in text and "| user | 名字 |" in text
