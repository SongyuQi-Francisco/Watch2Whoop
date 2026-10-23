"""Apple sync engine, server step S1 (docs/apple-sync-engine.md §3.2, flag apple_sync_engine): the registry
(rawdata/hk_types.ENGINE_TYPES), the engine fields of POST /v1/ingest/healthkit (rawdata/engine.py), gzip bodies, the
`engine` block of GET, rate limits, range re-sync and sync runs.

Synthetic example values only. Separate test database BODY_TEST_DB_NAME (default body_test_apple_sync), recreated each run;
skipped without Postgres.
"""
import gzip
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
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
from body_engine.importers.common import snake
from body_engine.rawdata import api as RA, engine as EN, hk_types as T, ingest as I
from body_engine.rollout import flags
from body_engine.sync import data_status

TZ = ZoneInfo("Australia/Sydney")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_apple_sync")
Q = "HKQuantityTypeIdentifier"
HR, SPO2, STEPS, STAND = Q + "HeartRate", Q + "OxygenSaturation", Q + "StepCount", Q + "AppleStandTime"
SLEEP, WORKOUT = "HKCategoryTypeIdentifierSleepAnalysis", "HKWorkoutTypeIdentifier"
APP_JSON = Path(__file__).resolve().parents[2] / "apps/watch2whoop/assets/healthkit_types.json"
T0 = datetime(2026, 10, 20, 8, 0, tzinfo=TZ)          # 2026-10-19 21:00 UTC, on a 15-minute boundary
SOURCES = [{"name": "Example Watch", "bundle": "com.apple.health.example", "product": "Watch0,0", "device_type": "watch",
            "device_model": "Watch", "manufacturer": "Apple Inc.", "os_version": "27.0"}]


# ---------------------------------------------------------------- the registry (no database)
def test_engine_registry_matches_the_importers():
    """The direct-upload types keep their row; names, units and aggregation are the other importers'."""
    for p in T.HK_TYPES:
        e = T.ENGINE_BY_HK[p.hk]
        assert (e.key, e.kind, e.unit, e.store_unit, e.agg, e.lo, e.hi, e.factor, e.status) == \
               (p.metric, "quantity", p.unit, p.unit, p.agg, p.lo, p.hi, 1.0, p.kind), p.hk
    by_metric = {s.metric: s for s in ow.SPECS.values()}
    for t in T.ENGINE_TYPES:
        if t.kind in ("quantity", "category", "water"):
            assert t.key == snake(t.hk), t.hk
        if t.kind == "quantity" and t.key in by_metric:
            s = by_metric[t.key]
            assert s.unit == t.store_unit, t.hk
            assert s.agg == t.agg or t.key == "physical_effort", t.hk     # OW averages physical effort; export sums
        if t.stats:
            assert t.hk in apple_zip.SUM_TYPES, f"{t.hk}: statistics buckets only for cumulative types"
        if t.kind == "quantity" and t.hk in apple_zip.SUM_TYPES:
            assert t.agg == "sum", t.hk
        assert t.kind != "quantity" or t.unit, t.hk
    assert {t.consent for t in T.ENGINE_TYPES if t.consent} == {I.RAW_CONSENT_VERSION}
    statuses = {k.key for k in data_status.KINDS + data_status.EXTRA_KINDS}
    assert {t.status for t in T.ENGINE_TYPES if t.status} <= statuses
    assert [t.hk for t in T.ENGINE_TYPES][-1] == HR, "heart rate (the largest) goes last"
    assert set(EN.FIELDS) <= set(RA.IngestIn.model_fields)


def test_engine_registry_same_as_the_app():
    """apps/watch2whoop/assets/healthkit_types.json is engine_json() (python -m body_engine.rawdata.hk_types)."""
    json.dumps(T.engine_json())
    if not APP_JSON.exists():
        pytest.skip("the app's registry JSON is not in this checkout yet (app step A1)")
    assert json.loads(APP_JSON.read_text()) == json.loads(json.dumps(T.engine_json()))


def test_gunzip_limits():
    assert EN.gunzip(gzip.compress(b'{"a": 1}')) == b'{"a": 1}'
    with pytest.raises(EN.TooLarge):
        EN.gunzip(gzip.compress(b" " * (EN.MAX_INFLATED_BYTES + 1)))
    with pytest.raises(EN.BadBatch):
        EN.gunzip(b"not gzip")
    with pytest.raises(EN.BadBatch):
        EN.gunzip(gzip.compress(b"{}")[:-4])
    with pytest.raises(EN.BadBatch):
        EN.gunzip(gzip.compress(b"{}") + gzip.compress(b"{}"))


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
    """a, b, r: in the beta cohort, which has the engine; c: not in it; nc: no consent."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        def add(name, consent=True):
            return conn.execute("""INSERT INTO users (display_name, role, consent_at, consent_version, timezone)
                                   VALUES (%s, 'member', %s, %s, 'Australia/Sydney') RETURNING id""",
                                (name, "2026-10-01" if consent else None, "2026-10-22.1" if consent else None)
                                ).fetchone()["id"]
        ids = {n: add(n, n != "nc") for n in ("a", "b", "c", "r", "nc")}
        for n in ("a", "b", "r", "nc"):
            flags.cohort_add(conn, ids[n], "beta", actor="test")
        flags.set_flag(conn, EN.FLAG, "cohort", cohort="beta", actor="test")
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in ids.items()}
        tok["a_ingest"] = auth.create_token(conn, ids["a"], "app:ingest:x", [auth.INGEST_SCOPE])
        conn.commit()
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token, **extra):
    return {"Authorization": f"Bearer {token}", **extra}


def conn_of(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def group(hk, values, start=T0, step=timedelta(minutes=5), unit=None, src=0, uuids=None):
    starts = [start + i * step for i in range(len(values))]
    return {"type": hk, "unit": T.ENGINE_BY_HK[hk].unit if unit is None else unit,
            "uuid": uuids or [str(uuid.uuid4()) for _ in values], "start": [s.timestamp() for s in starts],
            "end": [(s + timedelta(minutes=1)).timestamp() for s in starts], "value": values, "src": [src] * len(values)}


def workout(start=T0, minutes=45, **kw):
    return {"uuid": str(uuid.uuid4()), "activity": 50, "activity_name": "traditionalStrengthTraining",
            "start": start.timestamp(), "end": (start + timedelta(minutes=minutes)).timestamp(), "duration_s": minutes * 60,
            "stats": {"active_kcal": 210.0, "avg_hr": 118.0}, "events": [{"type": "pause", "start": start.timestamp(),
            "end": start.timestamp() + 30}], "meta": {"indoor": True, "tz": "Australia/Sydney"}, "src": 0, **kw}


def batch(**kw):
    return {"batch_id": str(uuid.uuid4()), "device": "iPhone", "app_version": "1.0.8", "phase": "incremental",
            "tz": "Australia/Sydney", "sources": SOURCES, **kw}


def count(dsn, uid, table="hk_samples"):
    with conn_of(dsn) as c:
        return c.execute(f"SELECT count(*) AS n FROM {table} WHERE user_id = %s", (uid,)).fetchone()["n"]


def dirty(dsn, uid):
    with conn_of(dsn) as c:
        return {r["day"]: set(r["types"]) for r in c.execute("SELECT day, types FROM hk_dirty_days WHERE user_id = %s", (uid,))}


def set_flag(dsn, key, state):
    with conn_of(dsn) as c:
        flags.set_flag(c, key, state, actor="test")
        c.commit()


def post(client, token, body, **headers):
    return client.post("/v1/ingest/healthkit", json=body, headers=H(token, **headers))


# ---------------------------------------------------------------- flag off / on
def test_flag_off_refuses_engine_fields(client, users, dsn):
    ids, tok = users
    st = client.get("/v1/ingest/healthkit", headers=H(tok["c"])).json()["engine"]
    assert st["enabled"] is False and st["mode"] == "ow" and st["types"] == [] and st["encodings"] == []
    assert "samples" not in st["accepts"] and "heartbeats" in st["accepts"]
    r = post(client, tok["c"], batch(samples=[group(HR, [60.0])]))
    assert r.status_code == 409 and r.json()["detail"] == "engine_disabled"
    assert count(dsn, ids["c"]) == 0 and count(dsn, ids["c"], "rawdata_uploads") == 0
    # an older app's batch (no engine fields) is not affected
    assert post(client, tok["c"], {"batch_id": str(uuid.uuid4()), "deleted": [str(uuid.uuid4())]}).status_code == 200


def test_status_with_the_flag(client, users):
    _, tok = users
    st = client.get("/v1/ingest/healthkit", headers=H(tok["a_ingest"])).json()["engine"]
    assert st["enabled"] is True and st["mode"] == "shadow" and st["encodings"] == ["gzip"]
    assert {"samples", "categories", "workouts", "stats", "range", "range_done", "runs", "quantities"} <= set(st["accepts"])
    assert HR in st["types"] and SLEEP in st["types"] and WORKOUT in st["types"]
    assert Q + "DietaryWater" not in st["types"], "water flag off: the app does not read water"
    assert st["limits"]["body_bytes"] == I.MAX_BODY_BYTES


def test_samples_categories_workouts(client, users, dsn):
    ids, tok = users
    uid = ids["a"]
    w = workout()
    body = batch(samples=[group(HR, [60.0, 61.0, 300.0]), group(SPO2, [0.97])],
                 categories=[group(SLEEP, [3, 4], step=timedelta(minutes=30))], workouts=[w])
    r = post(client, tok["a"], body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["samples"], j["workouts"], j["dropped"], j["skipped"]) == (5, 1, 1, 0)   # 300 bpm is implausible
    assert j["status"]["engine"]["mode"] == "shadow"
    with conn_of(dsn) as c:
        spo2 = c.execute("SELECT value, source, source_id, origin, batch_id::text AS b FROM hk_samples WHERE user_id = %s AND type = %s",
                         (uid, SPO2)).fetchone()
        assert spo2["value"] == pytest.approx(97.0) and spo2["source"] == "Example Watch" and spo2["origin"] == "app"
        assert spo2["b"] == body["batch_id"]
        src = c.execute("SELECT * FROM hk_sources WHERE id = %s", (spo2["source_id"],)).fetchone()
        assert (src["user_id"], src["product"], src["device_type"]) == (uid, "Watch0,0", "watch")
        wr = c.execute("SELECT value, value_text, tz, meta FROM hk_samples WHERE user_id = %s AND hk_uuid = %s",
                       (uid, w["uuid"])).fetchone()
        assert wr["value"] == 45 and wr["value_text"] == "traditionalStrengthTraining" and wr["tz"] == "Australia/Sydney"
        assert wr["meta"]["activity"] == 50 and wr["meta"]["stats"] == {"active_kcal": 210.0, "avg_hr": 118.0}
        up = c.execute("SELECT phase, tz FROM rawdata_uploads WHERE user_id = %s AND batch_id = %s",
                       (uid, body["batch_id"])).fetchone()
        assert (up["phase"], up["tz"]) == ("incremental", "Australia/Sydney")
    assert dirty(dsn, uid)[T0.date()] >= {HR, SPO2, SLEEP, WORKOUT}
    # the same batch again: answered from rawdata_uploads; the same samples in a new batch: skipped
    assert post(client, tok["a"], body).json()["duplicate_batch"] is True
    again = post(client, tok["a"], {**body, "batch_id": str(uuid.uuid4())}).json()
    assert (again["samples"], again["workouts"], again["skipped"]) == (0, 0, 6)
    assert count(dsn, uid, "hk_sources") == 1


def test_gzip_body(client, users, dsn):
    ids, tok = users
    n = count(dsn, ids["a"])
    raw = json.dumps(batch(samples=[group(HR, [70.0, 71.0])])).encode()
    r = client.post("/v1/ingest/healthkit", content=gzip.compress(raw),
                    headers=H(tok["a"], **{"Content-Encoding": "gzip", "Content-Type": "application/json"}))
    assert r.status_code == 200 and r.json()["samples"] == 2 and count(dsn, ids["a"]) == n + 2
    bad = client.post("/v1/ingest/healthkit", content=b"\x1f\x8bnot", headers=H(tok["a"], **{"Content-Encoding": "gzip"}))
    assert bad.status_code == 400
    bomb = client.post("/v1/ingest/healthkit", content=gzip.compress(b" " * (EN.MAX_INFLATED_BYTES + 10)),
                       headers=H(tok["a"], **{"Content-Encoding": "gzip"}))
    assert bomb.status_code == 413
    other = client.post("/v1/ingest/healthkit", content=raw, headers=H(tok["a"], **{"Content-Encoding": "br"}))
    assert other.status_code == 415
    assert count(dsn, ids["a"]) == n + 2


def test_bad_batches_store_nothing(client, users, dsn):
    ids, tok = users
    n = count(dsn, ids["a"])
    g = group(HR, [65.0, 66.0])
    bad = [
        batch(samples=[{**g, "unit": "count/s"}]),                        # wrong unit
        batch(samples=[{**g, "type": Q + "MadeUpType"}]),                 # unknown type
        batch(samples=[{**g, "src": [0, 5]}]),                            # no such source
        batch(samples=[{**g, "value": [65.0]}]),                          # lists of different length
        batch(samples=[group(Q + "DietaryWater", [250.0])]),              # water has its own field
        batch(samples=[group(SLEEP, [3])]),                               # a category in samples[]
        batch(categories=[group(HR, [60.0])]),                            # a quantity in categories[]
        batch(stats=[{"type": HR, "unit": "count/min", "start": 1760994000, "values": [1.0]}]),   # not cumulative
        batch(stats=[{"type": STEPS, "unit": "count", "start": 1760994001, "values": [1.0]}]),    # not aligned
        batch(samples=[g], tz="Mars/Olympus"),
        batch(samples=[g], range={"id": str(uuid.uuid4()), "start": T0.timestamp(), "end": T0.timestamp(), "types": [HR]}),
        batch(samples=[g], range_done=str(uuid.uuid4())),                 # unknown range: found while writing
        batch(runs=[{"id": str(uuid.uuid4()), "trigger": "observer", "result": "ok", "started": T0.timestamp(),
                     "duration_ms": 10, "error": "free text with a value: 61 bpm!"}]),
    ]
    for b in bad:
        r = post(client, tok["a"], b)
        assert r.status_code == 422, (r.status_code, r.text)
        assert "65.0" not in r.text, "input values are not echoed"
    assert count(dsn, ids["a"]) == n


def test_stats_replace_their_span(client, users, dsn):
    ids, tok = users
    uid = ids["a"]
    start = int(T0.timestamp())

    def send(values):
        return post(client, tok["a"], batch(stats=[{"type": STEPS, "unit": "count", "start": start, "values": values}])).json()

    def stored():
        with conn_of(dsn) as c:
            return {int(r["bucket_start"].timestamp()) - start: r["value"] for r in
                    c.execute("SELECT bucket_start, value FROM hk_stats WHERE user_id = %s AND type = %s", (uid, STEPS))}

    assert send([0.0, 100.0, 50.0])["buckets"] == 2 and stored() == {900: 100.0, 1800: 50.0}
    with conn_of(dsn) as c:
        c.execute("DELETE FROM hk_dirty_days WHERE user_id = %s", (uid,))
        c.commit()
    assert send([0.0, 100.0, 50.0])["buckets"] == 0 and dirty(dsn, uid) == {}, "an unchanged span marks nothing"
    assert send([0.0, 100.0, 0.0, 30.0])["buckets"] == 2 and stored() == {900: 100.0, 2700: 30.0}
    assert dirty(dsn, uid) == {T0.date(): {STEPS}}


def test_direct_upload_types_and_pause(client, users, dsn):
    """Stand time through samples[] is remade into its daily value at once, like quantities[]; app_types_pause stops it."""
    ids, tok = users
    uid = ids["b"]
    r = post(client, tok["b"], batch(samples=[group(STAND, [5.0, 7.0])]))
    assert r.status_code == 200 and r.json()["samples"] == 2
    with conn_of(dsn) as c:
        d = c.execute("SELECT value, source FROM daily_metrics WHERE user_id = %s AND metric = 'apple_stand_time' AND date = %s",
                      (uid, T0.date())).fetchone()
        assert d["value"] == 12.0 and d["source"] == "app"
        assert c.execute("SELECT scores_from FROM sync_refresh WHERE user_id = %s", (uid,)).fetchone()["scores_from"] == T0.date()
    set_flag(dsn, I.PAUSE_FLAG, "all")
    try:
        st = client.get("/v1/ingest/healthkit", headers=H(tok["b"])).json()["engine"]
        assert STAND not in st["types"] and HR in st["types"] and "quantities" not in st["accepts"]
        r = post(client, tok["b"], batch(samples=[group(STAND, [3.0])]))
        assert r.status_code == 409 and r.json()["detail"] == "type_paused"
        assert post(client, tok["b"], batch(samples=[group(HR, [58.0])])).status_code == 200
    finally:
        set_flag(dsn, I.PAUSE_FLAG, "off")


def test_deleted_removes_engine_samples(client, users, dsn):
    ids, tok = users
    uid = ids["b"]
    g = group(Q + "RespiratoryRate", [14.0, 15.0], start=T0 + timedelta(days=1))
    assert post(client, tok["b"], batch(samples=[g])).status_code == 200
    r = post(client, tok["b"], batch(deleted=[g["uuid"][0]]))
    assert r.status_code == 200 and r.json()["deleted"] == 1
    with conn_of(dsn) as c:
        left = [r["u"] for r in c.execute("SELECT hk_uuid::text AS u FROM hk_samples WHERE user_id = %s AND type = %s",
                                          (uid, Q + "RespiratoryRate"))]
    assert left == [g["uuid"][1]]
    assert Q + "RespiratoryRate" in dirty(dsn, uid)[(T0 + timedelta(days=1)).date()]


def test_range_resync_removes_what_is_gone(client, users, dsn):
    ids, tok = users
    uid = ids["b"]
    day = T0 + timedelta(days=3)
    old = group(HR, [60.0, 61.0, 62.0], start=day)
    assert post(client, tok["b"], batch(samples=[old])).status_code == 200
    rid = str(uuid.uuid4())
    rng = {"id": rid, "start": day.timestamp() - 3600, "end": day.timestamp() + 86400, "types": [HR]}
    page = group(HR, [60.0, 61.0, 63.0], start=day, uuids=[old["uuid"][0], old["uuid"][1], str(uuid.uuid4())])
    r = post(client, tok["b"], batch(phase="range", range=rng, samples=[page]))
    assert r.status_code == 200 and (r.json()["samples"], r.json()["skipped"]) == (1, 2)
    later = group(HR, [64.0], start=day + timedelta(hours=1))           # incremental upload while the range runs
    assert post(client, tok["b"], batch(samples=[later])).status_code == 200
    done = post(client, tok["b"], batch(phase="range", range=rng, range_done=rid))
    assert done.status_code == 200 and done.json()["removed"] == 1
    with conn_of(dsn) as c:
        left = {r["u"] for r in c.execute("""SELECT hk_uuid::text AS u FROM hk_samples WHERE user_id = %s AND type = %s
                                             AND start_at >= %s""", (uid, HR, day))}
        assert c.execute("SELECT removed FROM sync_ranges WHERE client_id = %s", (rid,)).fetchone()["removed"] == 1
    assert left == {old["uuid"][0], old["uuid"][1], page["uuid"][2], later["uuid"][0]}
    assert post(client, tok["b"], batch(phase="range", range_done=rid)).json()["removed"] == 0


def test_rate_limits(client, users, dsn, monkeypatch):
    ids, tok = users
    monkeypatch.setattr(EN, "BATCHES_PER_MINUTE", 2)
    monkeypatch.setattr(EN, "BIG_BATCHES_PER_MINUTE", 3)
    for _ in range(2):
        assert post(client, tok["r"], batch(samples=[group(HR, [60.0])])).status_code == 200
    r = post(client, tok["r"], batch(samples=[group(HR, [60.0])]))
    assert r.status_code == 429 and 1 <= int(r.headers["Retry-After"]) <= 60 and r.json()["detail"] == "rate_limited"
    assert post(client, tok["r"], batch(phase="backfill", samples=[group(HR, [60.0])])).status_code == 200
    # an older app's batch has no limits
    assert post(client, tok["r"], {"batch_id": str(uuid.uuid4()), "deleted": [str(uuid.uuid4())]}).status_code == 200
    monkeypatch.setattr(EN, "BIG_BATCHES_PER_MINUTE", 100)
    monkeypatch.setattr(EN, "BIG_DAY_BYTES", 10)
    r = post(client, tok["r"], batch(phase="backfill", samples=[group(HR, [60.0])]))
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert count(dsn, ids["r"]) == 3


def test_runs(client, users, dsn):
    ids, tok = users
    run = {"id": str(uuid.uuid4()), "trigger": "observer", "result": "ok", "started": T0.timestamp(), "duration_ms": 1800,
           "batches": 2, "bytes": 51200, "samples": 340, "error": None}
    for _ in range(2):
        r = post(client, tok["a"], batch(runs=[run]))
        assert r.status_code == 200
    with conn_of(dsn) as c:
        rows = c.execute("SELECT trigger, result, batches, samples, app_version FROM sync_runs WHERE user_id = %s",
                         (ids["a"],)).fetchall()
    assert rows == [{"trigger": "observer", "result": "ok", "batches": 2, "samples": 340, "app_version": "1.0.8"}]


def test_users_are_separate_and_consent(client, users, dsn):
    ids, tok = users
    g = group(Q + "RestingHeartRate", [52.0], start=T0 - timedelta(days=5))
    for n in ("a", "b"):
        assert post(client, tok[n], batch(samples=[g])).json()["samples"] == 1, "the same UUID is per user"
    with conn_of(dsn) as c:
        owners = {r["user_id"] for r in c.execute("SELECT user_id FROM hk_samples WHERE hk_uuid = %s", (g["uuid"][0],))}
        sources = {r["user_id"] for r in c.execute("SELECT user_id FROM hk_sources")}
    assert owners == {ids["a"], ids["b"]} and sources <= {ids["a"], ids["b"], ids["r"]}
    r = post(client, tok["nc"], batch(samples=[group(HR, [60.0])]))
    assert r.status_code == 409 and r.json()["detail"] == "consent_required" and count(dsn, ids["nc"]) == 0
