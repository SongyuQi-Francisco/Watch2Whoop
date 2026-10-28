"""Sync center, server side: the HealthKit types the app reads itself (rawdata/hk_types.py, quantities[] of
POST /v1/ingest/healthkit), the forced re-sync (sync/resync.py, /v1/ingest/resync) and the flags sync_center /
app_types_pause.

Synthetic example values only. Separate test database BODY_TEST_DB_NAME (default body_test_sync_center), recreated each
run; skipped without Postgres. Open Wearables and the score engine are fakes.
"""
import os
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import apple_zip, ow
from body_engine.importers.common import SOURCE_PRIORITY, snake, upsert_daily
from body_engine.rawdata import hk_types as T
from body_engine.rollout import flags
from body_engine.sync import data_status, params as SP, refresh, resync

TZ = ZoneInfo("Australia/Sydney")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_sync_center")
Q = "HKQuantityTypeIdentifier"
APP_TYPES = Path(__file__).resolve().parents[2] / "apps/watch2whoop/lib/src/hk_types.dart"


# ---------------------------------------------------------------- the registry (no database)
def test_registry_matches_the_export_import_and_ow():
    """Names, units and day aggregation are the ones the Apple Health export import and the OW importer use."""
    for t in T.HK_TYPES:
        assert t.metric == snake(t.hk), t.hk
        assert (t.agg == "sum") == (t.hk in apple_zip.SUM_TYPES), f"{t.hk}: same aggregation as the export import"
        spec = next((s for s in ow.SPECS.values() if s.metric == t.metric), None)
        if spec is not None:   # OW averages physical effort, the export import sums it: the export import is the reference
            assert spec.unit == t.unit and (spec.agg == t.agg or t.metric == "physical_effort"), t.hk
    assert T.SCORE_TYPES == {Q + "AppleSleepingWristTemperature": "temp"}
    assert SOURCE_PRIORITY["app"] > SOURCE_PRIORITY["ow"]
    assert {k.key for k in data_status.EXTRA_KINDS} | {"wrist_temperature"} == {t.kind for t in T.HK_TYPES}


def test_registry_same_as_the_app():
    """apps/watch2whoop/lib/src/hk_types.dart lists the same rows (hk, metric, unit, agg, kind)."""
    if not APP_TYPES.exists():
        pytest.skip("app registry not in this checkout yet")
    rows = re.findall(r"HkType\(\s*'([^']+)',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)'", APP_TYPES.read_text())
    assert rows == [(t.hk, t.metric, t.unit, t.agg, t.kind) for t in T.HK_TYPES]


# ---------------------------------------------------------------- database
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


@pytest.fixture(scope="module")
def users(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        def add(name, ow_id=None, consent=True):
            return conn.execute("""INSERT INTO users (display_name, role, ow_user_id, consent_at, consent_version)
                                   VALUES (%s, 'member', %s, %s, %s) RETURNING id""",
                                (name, ow_id, "2026-10-01" if consent else None, "2026-10-22.1" if consent else None)
                                ).fetchone()["id"]
        ids = {"a": add("a", "ow-a"), "b": add("b", "ow-b"), "c": add("c"), "noconsent": add("nc", consent=False)}
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in ids.items()}
        tok["a_ingest"] = auth.create_token(conn, ids["a"], "app:ingest:x", [auth.INGEST_SCOPE])
        conn.commit()
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token):
    return {"Authorization": f"Bearer {token}"}


def conn_of(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def sample(start, value, source="Apple Watch", minutes=5):
    return {"uuid": str(uuid.uuid4()), "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat(),
            "value": value, "source": source}


def group(hk, items, unit=None):
    return {"type": Q + hk, "unit": unit or T.BY_HK[Q + hk].unit, "items": items}


def batch(**kw):
    return {"batch_id": str(uuid.uuid4()), "device": "iPhone", "app_version": "1.0.7+7", **kw}


def daily(conn, uid, metric, d):
    return conn.execute("SELECT value, agg, n, min_value, max_value, source FROM daily_metrics WHERE user_id = %s "
                        "AND metric = %s AND date = %s", (uid, metric, d)).fetchone()


D1 = date(2026, 10, 18)
T1 = datetime(2026, 10, 18, 9, 0, tzinfo=TZ)


def test_quantities_daily_values_and_idempotent(client, users, dsn):
    ids, tok = users
    # Example values: stand minutes from the watch (3 + 4) and the phone (2) -> largest single source 7;
    # wrist temperature two samples -> mean / min / max; one sample out of range is dropped, not the batch
    stand = [sample(T1, 3), sample(T1 + timedelta(hours=1), 4), sample(T1 + timedelta(hours=2), 2, "iPhone")]
    temp = [sample(T1 - timedelta(hours=6), 35.5, minutes=400), sample(T1 - timedelta(hours=5), 35.9, minutes=300),
            sample(T1, 80.0)]
    body = batch(quantities=[group("AppleStandTime", stand), group("AppleSleepingWristTemperature", temp)])
    r = client.post("/v1/ingest/healthkit", json=body, headers=H(tok["a"]))
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["metrics"], j["dropped"], j["skipped"], j["metrics_enabled"]) == (5, 1, 0, True)
    assert j["status"]["metrics"] == 5 and Q + "AppleStandTime" in j["status"]["metric_types"]
    with conn_of(dsn) as conn:
        s = daily(conn, ids["a"], "apple_stand_time", D1)
        assert (s["value"], s["agg"], s["n"], s["source"]) == (7, "sum", 3, "app")
        w = daily(conn, ids["a"], "apple_sleeping_wrist_temperature", D1)
        assert (w["value"], w["min_value"], w["max_value"], w["agg"]) == (35.7, 35.5, 35.9, "avg")
    # the same batch again (answered from the batch log) and the same samples in a new batch (skipped by UUID)
    assert client.post("/v1/ingest/healthkit", json=body, headers=H(tok["a"])).json()["duplicate_batch"] is True
    again = client.post("/v1/ingest/healthkit", json=batch(quantities=body["quantities"]), headers=H(tok["a_ingest"])).json()
    assert (again["metrics"], again["skipped"]) == (0, 5)
    with conn_of(dsn) as conn:
        assert conn.execute("SELECT count(*) AS n FROM hk_samples WHERE user_id = %s", (ids["a"],)).fetchone()["n"] == 5
        assert daily(conn, ids["a"], "apple_stand_time", D1)["value"] == 7
        # the new data asked for a score recompute from its first day
        q = conn.execute("SELECT status, scores_from FROM sync_refresh WHERE user_id = %s", (ids["a"],)).fetchone()
        assert q["status"] == "scores_queued" and q["scores_from"] == D1


def test_deleted_sample_remakes_the_day(client, users, dsn):
    ids, tok = users
    one, two = sample(T1 + timedelta(days=1), 10), sample(T1 + timedelta(days=1, hours=1), 15)
    client.post("/v1/ingest/healthkit", json=batch(quantities=[group("AppleExerciseTime", [one, two])]),
                headers=H(tok["b"])).raise_for_status()
    d2 = D1 + timedelta(days=1)
    with conn_of(dsn) as conn:
        assert daily(conn, ids["b"], "apple_exercise_time", d2)["value"] == 25
    client.post("/v1/ingest/healthkit", json=batch(deleted=[one["uuid"]]), headers=H(tok["b"])).raise_for_status()
    with conn_of(dsn) as conn:
        assert daily(conn, ids["b"], "apple_exercise_time", d2)["value"] == 15
    client.post("/v1/ingest/healthkit", json=batch(deleted=[two["uuid"]]), headers=H(tok["b"])).raise_for_status()
    with conn_of(dsn) as conn:   # nothing left from any source: no daily value
        assert daily(conn, ids["b"], "apple_exercise_time", d2) is None
        assert conn.execute("SELECT count(*) AS n FROM daily_metric_sources WHERE user_id = %s AND metric = 'apple_exercise_time'",
                            (ids["b"],)).fetchone()["n"] == 0


def test_source_priority(client, users, dsn):
    """Non-cumulative: the app's value wins over OW's; cumulative: the largest clean source (here the export's)."""
    ids, tok = users
    d = D1 + timedelta(days=3)
    with conn_of(dsn) as conn:
        upsert_daily(conn, ids["c"], "ow", [
            {"date": d, "metric": "walking_heart_rate_average", "value": 100, "unit": "count/min", "agg": "avg", "n": 1,
             "min_value": 100, "max_value": 100}])
        upsert_daily(conn, ids["c"], "apple_export", [
            {"date": d, "metric": "time_in_daylight", "value": 50, "unit": "min", "agg": "sum", "n": 3,
             "min_value": None, "max_value": None}])
        conn.commit()
    t = datetime.combine(d, datetime.min.time(), TZ) + timedelta(hours=12)
    client.post("/v1/ingest/healthkit", json=batch(quantities=[
        group("WalkingHeartRateAverage", [sample(t, 98)]), group("TimeInDaylight", [sample(t, 20)])]),
        headers=H(tok["c"])).raise_for_status()
    with conn_of(dsn) as conn:
        w = daily(conn, ids["c"], "walking_heart_rate_average", d)
        assert (w["value"], w["source"]) == (98, "app")
        dl = daily(conn, ids["c"], "time_in_daylight", d)
        assert (dl["value"], dl["source"]) == (50, "apple_export")


def test_users_are_isolated_and_bad_batches_rejected(client, users, dsn):
    ids, tok = users
    s = sample(T1 + timedelta(days=5), 30)
    client.post("/v1/ingest/healthkit", json=batch(quantities=[group("AppleExerciseTime", [s])]),
                headers=H(tok["a"])).raise_for_status()
    # another user deleting the same UUID touches nothing of a's; the same UUID uploaded by b is b's own row
    client.post("/v1/ingest/healthkit", json=batch(deleted=[s["uuid"]]), headers=H(tok["b"])).raise_for_status()
    r = client.post("/v1/ingest/healthkit", json=batch(quantities=[group("AppleExerciseTime", [s])]), headers=H(tok["b"]))
    assert r.json()["metrics"] == 1
    with conn_of(dsn) as conn:
        assert {x["user_id"] for x in conn.execute("SELECT user_id FROM hk_samples WHERE hk_uuid = %s", (s["uuid"],))} \
            == {ids["a"], ids["b"]}
    # wrong unit or unknown type: the whole batch is refused (422), nothing stored
    bad_unit = client.post("/v1/ingest/healthkit", json=batch(quantities=[group("AppleExerciseTime", [sample(T1, 1)], "s")]),
                           headers=H(tok["a"]))
    unknown = client.post("/v1/ingest/healthkit", json=batch(quantities=[
        {"type": Q + "StepCount", "unit": "count", "items": [sample(T1, 1)]}]), headers=H(tok["a"]))
    assert bad_unit.status_code == 422 and unknown.status_code == 422
    # no consent: 409, nothing stored; no token: 401
    nc = client.post("/v1/ingest/healthkit", json=batch(quantities=[group("AppleStandTime", [sample(T1, 1)])]),
                     headers=H(tok["noconsent"]))
    assert nc.status_code == 409
    assert client.post("/v1/ingest/healthkit", json=batch()).status_code == 401


def test_pause_flag_stops_quantities(client, users, dsn):
    ids, tok = users
    with conn_of(dsn) as conn:
        flags.set_flag(conn, "app_types_pause", "all", actor="test")
        conn.commit()
    try:
        r = client.post("/v1/ingest/healthkit", json=batch(quantities=[group("AppleStandTime", [sample(T1 + timedelta(days=9), 1)])]),
                        headers=H(tok["c"])).json()
        assert r["metrics_enabled"] is False and r["metrics"] == 0
        st = client.get("/v1/ingest/healthkit", headers=H(tok["c"])).json()
        assert st["metrics_enabled"] is False and st["metric_types"] == []
    finally:
        with conn_of(dsn) as conn:
            flags.set_flag(conn, "app_types_pause", "off", actor="test")
            conn.commit()
    assert client.get("/v1/ingest/healthkit", headers=H(tok["c"])).json()["metrics_enabled"] is True


def test_data_status_extended_only_with_the_flag(users, dsn):
    ids, _ = users
    with conn_of(dsn) as conn:
        plain = data_status.collect(conn, ids["a"], today=D1)
        assert plain["extended"] is False and len(plain["types"]) == len(data_status.KINDS)
        ext = data_status.collect(conn, ids["a"], today=D1, extended=True)
        by = {t["key"]: t for t in ext["types"]}
        assert by["stand_time"]["status"] == "ok" and by["stand_time"]["last_date"] == D1.isoformat()
        assert by["stand_time"]["last_at"] is not None and by["wrist_temperature"]["channel"] == "app"
        assert by["daylight"]["status"] == "never"


# ---------------------------------------------------------------- forced re-sync
def flag_on(dsn, key="sync_center"):
    with conn_of(dsn) as conn:
        flags.set_flag(conn, key, "all", actor="test")
        conn.commit()


def test_resync_flag_off_is_404(client, users):
    _, tok = users
    assert client.get("/v1/ingest/resync", headers=H(tok["a"])).status_code == 404
    assert client.post("/v1/ingest/resync", json={"days": 7}, headers=H(tok["a"])).status_code == 404
    assert client.get("/v1/ingest/healthkit", headers=H(tok["a"])).json()["sync_center"] is False


def test_resync_range_limits_and_server_part(client, users, dsn):
    ids, tok = users
    flag_on(dsn)
    assert client.get("/v1/ingest/healthkit", headers=H(tok["a"])).json()["sync_center"] is True
    assert client.post("/v1/ingest/resync", json={"days": 0}, headers=H(tok["a"])).status_code == 422
    assert client.post("/v1/ingest/resync", json={"days": 9999}, headers=H(tok["a"])).status_code == 422
    r = client.post("/v1/ingest/resync", json={"days": 30}, headers=H(tok["a_ingest"]))
    assert r.status_code == 202, r.text
    j = r.json()
    assert j["accepted"] and j["status"] == "phone" and j["days"] == 30 and j["phase"] == "phone"
    today = datetime.now(TZ).date()
    assert j["range_from"] == (today - timedelta(days=30)).isoformat()
    # one job at a time
    busy = client.post("/v1/ingest/resync", json={"days": 7}, headers=H(tok["a"]))
    assert busy.status_code == 200 and busy.json()["reason"] == "busy"
    # phone progress, then done -> the server part is queued through sync_refresh
    p = client.put("/v1/ingest/resync", json={"phone_percent": 40, "counts": {"metrics": 12}}, headers=H(tok["a"])).json()
    assert p["phone_percent"] == 40 and p["percent"] == 24 and p["phone_counts"] == {"metrics": 12}
    p = client.put("/v1/ingest/resync", json={"phone_done": True}, headers=H(tok["a"])).json()
    assert p["status"] == "queued" and p["phase"] == "server"
    with conn_of(dsn) as conn:
        assert conn.execute("SELECT status FROM sync_refresh WHERE user_id = %s", (ids["a"],)).fetchone()["status"] == "scores_queued"
        imported, scored = [], []
        first = today - timedelta(days=400)
        kw = {"client": object(), "first_day": lambda c, u, cl: first,
              "importer": lambda c, cl, uid, s, e: imported.append((s, e)),
              "scorer": lambda c, uid, cl, start: scored.append(start)}
        out = refresh.run_queued(conn, ids["a"], resync_kw=kw, scorer=lambda *a: pytest.fail("the re-sync scores instead"))
        assert out[0]["resync"]["status"] == "done"
        assert imported[0][0] == today - timedelta(days=30) and imported[-1][1] == today
        assert all((e - s).days < SP.RESYNC_WINDOW_DAYS for s, e in imported)
        assert scored == [today - timedelta(days=30)]
        assert conn.execute("SELECT status FROM sync_refresh WHERE user_id = %s", (ids["a"],)).fetchone()["status"] == "idle"
    assert client.get("/v1/ingest/resync", headers=H(tok["a"])).json()["percent"] == 100
    # within the minimum gap: throttled with Retry-After
    t = client.post("/v1/ingest/resync", json={"days": 7}, headers=H(tok["a"]))
    assert t.status_code == 200 and t.json()["reason"] == "throttled" and int(t.headers["Retry-After"]) > 0
    # another user is not affected by a's job
    assert client.post("/v1/ingest/resync", json={"days": 7}, headers=H(tok["b"])).json()["accepted"] is True


def test_resync_all_history_budget_and_cancel(client, users, dsn):
    ids, tok = users
    flag_on(dsn)
    with conn_of(dsn) as conn:   # b's job from the previous test: cancel it
        resync.cancel(conn, ids["b"])
        conn.execute("UPDATE sync_resync SET requested_at = now() - interval '7 hours' WHERE user_id = %s", (ids["b"],))
        conn.commit()
    r = client.post("/v1/ingest/resync", json={"days": None}, headers=H(tok["b"])).json()
    assert r["accepted"] and r["range_from"] is None
    client.put("/v1/ingest/resync", json={"phone_done": True}, headers=H(tok["b"])).raise_for_status()
    today = datetime.now(TZ).date()
    first = today - timedelta(days=95)
    clock = iter(range(0, 10_000, 100))   # every call 100 s later: one window per 240 s budget
    imported, scored = [], []
    kw = {"client": object(), "first_day": lambda c, u, cl: first, "clock": lambda: next(clock),
          "importer": lambda c, cl, uid, s, e: imported.append(s), "scorer": lambda c, uid, cl, start: scored.append(start)}
    with conn_of(dsn) as conn:
        steps = 0
        while resync.pending(conn, ids["b"]) and steps < 10:
            resync.run_step(conn, ids["b"], **kw)
            steps += 1
            if resync.pending(conn, ids["b"]):   # unfinished: kept queued for the next per-minute run
                assert conn.execute("SELECT status FROM sync_refresh WHERE user_id = %s",
                                    (ids["b"],)).fetchone()["status"] == "scores_queued"
        assert steps > 1 and imported[0] == first and scored == [None]
    # cancel only before the server part: a phone job is cancelled, a new one may start after the gap
    with conn_of(dsn) as conn:
        conn.execute("UPDATE sync_resync SET requested_at = now() - interval '1 hour' WHERE user_id = %s", (ids["c"],))
        conn.commit()
    assert client.post("/v1/ingest/resync", json={"days": 7}, headers=H(tok["c"])).json()["accepted"]
    assert client.delete("/v1/ingest/resync", headers=H(tok["c"])).json()["status"] == "cancelled"
    # consent: refused
    assert client.post("/v1/ingest/resync", json={"days": 7}, headers=H(tok["noconsent"])).json()["reason"] == "consent"


def test_resync_stale_phone_job_frees_the_slot(client, users, dsn):
    ids, tok = users
    flag_on(dsn)
    with conn_of(dsn) as conn:
        conn.execute("""UPDATE sync_resync SET status = 'phone', requested_at = now() - interval '2 days',
                        updated_at = now() - interval '2 days' WHERE user_id = %s""", (ids["c"],))
        conn.commit()
    st = client.get("/v1/ingest/resync", headers=H(tok["c"])).json()
    assert st["stale"] is True and st["active"] is False
    assert client.post("/v1/ingest/resync", json={"days": 90}, headers=H(tok["c"])).json()["accepted"] is True


def test_resync_ow_client_failure_fails_the_job_not_the_run(client, users, dsn, monkeypatch):
    """OWClient() raising (no OW API key): the job ends in error with the reason, sync_refresh is idle again and
    sync-refresh-run returns normally, instead of raising out of it with the job stuck queued."""
    from body_engine import ow_client
    ids, tok = users
    flag_on(dsn)
    client.put("/v1/ingest/resync", json={"phone_done": True}, headers=H(tok["c"])).raise_for_status()   # c's 90-day job

    def no_key(*a, **kw):
        raise ow_client.OWError("no OW API key")
    monkeypatch.setattr(ow_client, "OWClient", no_key)
    with conn_of(dsn) as conn:
        out = refresh.run_queued(conn, ids["c"])
        assert out == [{"user_id": ids["c"], "resync": {"status": "error", "error": "no OW API key"}}]
        row = conn.execute("SELECT status, error FROM sync_resync WHERE user_id = %s", (ids["c"],)).fetchone()
        assert row["status"] == "error" and row["error"] == "no OW API key"
        assert conn.execute("SELECT status FROM sync_refresh WHERE user_id = %s", (ids["c"],)).fetchone()["status"] == "idle"
    st = client.get("/v1/ingest/resync", headers=H(tok["c"])).json()
    assert st["status"] == "error" and st["active"] is False


def test_resync_in_the_app_mode_waits_for_the_engine_not_ow(users, dsn, monkeypatch):
    """#219 S5b: in the sync mode app the server part imports nothing from OW: it waits while the app's range re-sync is
    open (no range_done) or the engine's days are not derived, at most RESYNC_ENGINE_WAIT after phone_done, then scores
    from the start of the range."""
    from body_engine.rawdata import engine as EN
    with conn_of(dsn) as conn:
        uid = conn.execute("""INSERT INTO users (display_name, role, ow_user_id, consent_at, consent_version)
                              VALUES ('e', 'member', 'ow-e', '2026-10-01', '2026-10-22.1') RETURNING id""").fetchone()["id"]
        conn.commit()
    monkeypatch.setattr(EN, "mode", lambda c, u: "app" if u == uid else "ow")
    monkeypatch.setattr(refresh, "derive_engine_days", lambda c, u: {})   # the derive queue does not move here
    scored = []
    kw = {"client": object(), "first_day": lambda *a: pytest.fail("OW is not asked in the app mode"),
          "importer": lambda *a: pytest.fail("nothing is imported from OW in the app mode"),
          "scorer": lambda c, u, cl, start: scored.append(start)}
    with conn_of(dsn) as conn:
        assert resync.request(conn, uid, 7)["accepted"]
        conn.execute("""INSERT INTO sync_ranges (user_id, client_id, types, start_at, end_at)
                        VALUES (%s, %s, ARRAY['HKQuantityTypeIdentifierHeartRate'], now() - interval '7 days', now())""",
                     (uid, uuid.uuid4()))
        conn.commit()
        resync.progress(conn, uid, phone_done=True)
        r = resync.run_step(conn, uid, **kw)
        assert (r["status"], r.get("waiting")) == ("queued", "range"), r
        assert conn.execute("SELECT status FROM sync_refresh WHERE user_id = %s", (uid,)).fetchone()["status"] == "scores_queued"
        conn.execute("UPDATE sync_ranges SET done_at = now() WHERE user_id = %s", (uid,))
        conn.execute("INSERT INTO hk_dirty_days (user_id, day) VALUES (%s, %s)", (uid, D1))
        conn.commit()
        assert resync.run_step(conn, uid, **kw)["waiting"] == "derive"
        # a day that never derives does not hold the job forever
        conn.execute("UPDATE sync_resync SET phone_done_at = now() - %s WHERE user_id = %s",
                     (SP.RESYNC_ENGINE_WAIT + timedelta(minutes=1), uid))
        conn.commit()
        assert resync.run_step(conn, uid, **kw)["status"] == "done"
        assert scored == [datetime.now(TZ).date() - timedelta(days=7)]
        assert conn.execute("SELECT status FROM sync_refresh WHERE user_id = %s", (uid,)).fetchone()["status"] == "idle"
