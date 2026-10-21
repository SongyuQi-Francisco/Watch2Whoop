"""Raw data uploads from the Watch2Whoop app (rawdata/ingest.py, POST /v1/ingest/healthkit, POST /v1/web/ingest-token).

Synthetic data only. Separate test database BODY_TEST_DB_NAME (default body_test_rawdata_ingest), recreated each run;
skipped without Postgres.
"""
import base64
import math
import os
import struct
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rawdata import ingest as I

TZ = ZoneInfo("Australia/Sydney")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_rawdata_ingest")
CSRF = {"X-Body-Web": "1"}
ALT = [1000 + (20 if i % 2 else -20) for i in range(50)]   # RR in ms: RMSSD 40


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
        def add(name, role="member", consent=True):
            return conn.execute("""INSERT INTO users (display_name, role, consent_at, consent_version)
                                   VALUES (%s, %s, %s, %s) RETURNING id""",
                                (name, role, "2026-10-01" if consent else None, "v" if consent else None)).fetchone()["id"]
        ids = {"a": add("a", "owner"), "b": add("b"), "noconsent": add("noconsent", consent=False)}
        tok = {n: auth.create_token(conn, i, "t", ["read", "write"]) for n, i in ids.items()}
        tok["a_ro"] = auth.create_token(conn, ids["a"], "ro", ["read"])
        tok["a_ingest"] = auth.create_token(conn, ids["a"], "app:ingest:x", [auth.INGEST_SCOPE])
        tok["a_mcp"] = auth.create_token(conn, ids["a"], "mcp:x", [auth.MCP_SCOPE, "read", "write"])
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token):
    return {"Authorization": f"Bearer {token}"}


def beats(rr_ms):
    out, t = [0.0], 0.0
    for r in rr_ms:
        t += r / 1000
        out.append(round(t, 4))
    return out


def ecg_b64(hz=512, seconds=30, rr_s=1.0):
    n = int(hz * seconds)
    v = [int(1000 * math.exp(-(((i / hz) % rr_s) - 0.3) ** 2 / 0.0002)) for i in range(n)]
    return base64.b64encode(struct.pack(f"<{n}h", *v)).decode()


def reading(start, rr=ALT, gaps=False):
    b = beats(rr)
    return {"uuid": str(uuid.uuid4()), "start": start.isoformat(), "end": (start + timedelta(seconds=b[-1])).isoformat(),
            "source": "Apple Watch", "beats": b, **({"gaps": [False] * len(b)} if gaps else {})}


def ecg(start, cls="sinusRhythm"):
    return {"uuid": str(uuid.uuid4()), "start": start.isoformat(), "sample_hz": 512, "voltage_b64": ecg_b64(),
            "classification": cls, "symptoms_status": "none", "avg_hr": 60, "device": "Apple Watch",
            "software_version": "11.0"}


def batch(**kw):
    return {"batch_id": str(uuid.uuid4()), "device": "iPhone", "app_version": "1.0.0+2", **kw}


T0 = datetime(2026, 10, 20, 3, 0, tzinfo=TZ)


def test_upload_computes_and_dedups(client, users, dsn):
    ids, tok = users
    hb = [reading(T0, gaps=True), reading(T0 + timedelta(hours=1))]
    e = ecg(T0 + timedelta(hours=5))
    b = batch(heartbeats=hb, ecgs=[e])
    r = client.post("/v1/ingest/healthkit", json=b, headers=H(tok["a"]))
    assert r.status_code == 200, r.text
    assert r.json()["heartbeats"] == 2 and r.json()["ecgs"] == 1 and r.json()["skipped"] == 0
    assert r.json()["status"]["heartbeats"] == 2 and r.json()["status"]["ecgs"] == 1
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        rows = conn.execute("SELECT rmssd, origin, hk_uuid::text AS u FROM heartbeat_series WHERE user_id = %s ORDER BY start_at",
                            (ids["a"],)).fetchall()
        assert [round(x["rmssd"]) for x in rows] == [40, 40] and {x["origin"] for x in rows} == {"app"}
        assert rows[0]["u"] == hb[0]["uuid"]
        d = conn.execute("SELECT derived, apple_avg_hr, classification FROM ecg_recordings WHERE user_id = %s",
                         (ids["a"],)).fetchone()
        assert d["classification"] == "sinusRhythm" and d["apple_avg_hr"] == 60 and abs(d["derived"]["hr"] - 60) < 2
    # Same batch again: answered from the batch log; same samples in a new batch: skipped by UUID
    again = client.post("/v1/ingest/healthkit", json=b, headers=H(tok["a"])).json()
    assert again["duplicate_batch"] is True
    r2 = client.post("/v1/ingest/healthkit", json=batch(heartbeats=hb, ecgs=[e]), headers=H(tok["a"])).json()
    assert r2["heartbeats"] == 0 and r2["ecgs"] == 0 and r2["skipped"] == 3
    # The owner sees the ECG through the existing endpoint; the AI-facing summary is unchanged
    lst = client.get("/v1/users/me/ecg", headers=H(tok["a"])).json()["recordings"]
    assert len(lst) == 1 and lst[0]["origin"] == "app"


def test_deleted_in_health_is_removed(client, users, dsn):
    ids, tok = users
    hb = reading(T0 + timedelta(days=1))
    e = ecg(T0 + timedelta(days=1, hours=2))
    client.post("/v1/ingest/healthkit", json=batch(heartbeats=[hb], ecgs=[e]), headers=H(tok["b"])).raise_for_status()
    r = client.post("/v1/ingest/healthkit", json=batch(deleted=[hb["uuid"], e["uuid"], str(uuid.uuid4())]),
                    headers=H(tok["b"]))
    assert r.json()["deleted"] == 2
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM ecg_recordings WHERE user_id = %s", (ids["b"],)).fetchone()["n"] == 0


def test_tokens_and_scopes(client, users):
    ids, tok = users
    ok = batch(heartbeats=[reading(T0 + timedelta(days=2))])
    assert client.post("/v1/ingest/healthkit", json=ok).status_code == 401
    assert client.post("/v1/ingest/healthkit", json=ok, headers=H(tok["a_ro"])).status_code == 401
    assert client.post("/v1/ingest/healthkit", json=ok, headers=H(tok["a_mcp"])).status_code == 401
    assert client.post("/v1/ingest/healthkit", json=ok, headers=H(tok["a_ingest"])).status_code == 200
    # The upload-only token cannot read anything nor log in to the Today page
    assert client.get("/v1/users/me/ecg", headers=H(tok["a_ingest"])).status_code == 401
    assert client.get("/v1/web/me", headers=H(tok["a_ingest"])).status_code == 401
    assert client.post("/v1/web/login", json={"token": tok["a_ingest"]}).status_code == 401
    assert client.get("/v1/ingest/healthkit", headers=H(tok["a_ingest"])).json()["heartbeats"] >= 1


def test_ingest_token_from_web_session(client, users):
    ids, tok = users
    # Only a cookie session of the Today page can mint it (not a Bearer token)
    assert client.post("/v1/web/ingest-token", json={"device": "iPhone"}, headers=H(tok["b"])).status_code == 403
    c = TestClient(api.app, base_url="https://testserver")
    c.post("/v1/web/login", json={"token": tok["b"]}).raise_for_status()
    assert c.post("/v1/web/ingest-token", json={"device": "iPhone"}).status_code == 403   # CSRF header missing
    t1 = c.post("/v1/web/ingest-token", json={"device": "iPhone"}, headers=CSRF).json()["token"]
    t2 = c.post("/v1/web/ingest-token", json={"device": "iPhone"}, headers=CSRF).json()["token"]
    assert client.get("/v1/ingest/healthkit", headers=H(t1)).status_code == 401   # replaced by the new one
    assert client.get("/v1/ingest/healthkit", headers=H(t2)).status_code == 200


def test_consent_and_validation(client, users, dsn):
    ids, tok = users
    r = client.post("/v1/ingest/healthkit", json=batch(heartbeats=[reading(T0)]), headers=H(tok["noconsent"]))
    assert r.status_code == 409 and r.json()["detail"] == "consent_required"
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT count(*) AS n FROM heartbeat_series WHERE user_id = %s",
                            (ids["noconsent"],)).fetchone()["n"] == 0
    bad_gaps = reading(T0 + timedelta(days=3))
    bad_gaps["gaps"] = [False]
    assert client.post("/v1/ingest/healthkit", json=batch(heartbeats=[bad_gaps]), headers=H(tok["a"])).status_code == 422
    bad_ecg = ecg(T0 + timedelta(days=3))
    bad_ecg["voltage_b64"] = "not base64!"
    assert client.post("/v1/ingest/healthkit", json=batch(ecgs=[bad_ecg]), headers=H(tok["a"])).status_code == 422
    naive = reading(T0)
    naive["start"] = "2026-10-20T03:00:00"
    assert client.post("/v1/ingest/healthkit", json=batch(heartbeats=[naive]), headers=H(tok["a"])).status_code == 422
    too_many = batch(ecgs=[ecg(T0)] * (I.MAX_ECGS + 1))
    assert client.post("/v1/ingest/healthkit", json=too_many, headers=H(tok["a"])).status_code == 422
    unknown = ecg(T0 + timedelta(days=4), cls="somethingNew")
    client.post("/v1/ingest/healthkit", json=batch(ecgs=[unknown]), headers=H(tok["a"])).raise_for_status()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert conn.execute("SELECT classification FROM ecg_recordings WHERE hk_uuid = %s",
                            (unknown["uuid"],)).fetchone()["classification"] == "unrecognized"


def test_app_copy_replaces_export_copy(users, dsn):
    ids, _ = users
    from body_engine.rawdata import store
    start = T0 + timedelta(days=6)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        store.save_heartbeats(conn, ids["b"], [{"start_at": start, "end_at": start + timedelta(minutes=1), "source": "Apple Watch",
                                                "origin": "apple_export", "beats_s": beats(ALT)}])
        conn.commit()
        hb = reading(start)
        I.ingest(conn, ids["b"], {"batch_id": uuid.uuid4(), "heartbeats": [
            {**hb, "start": start, "end": start + timedelta(minutes=1)}]})
        r = conn.execute("SELECT origin, hk_uuid::text AS u FROM heartbeat_series WHERE user_id = %s AND start_at = %s",
                         (ids["b"], start)).fetchone()
        assert r["origin"] == "app" and r["u"] == hb["uuid"]


def test_decode_voltage_limits():
    assert I.decode_voltage(base64.b64encode(struct.pack("<3h", -5, 0, 1200)).decode()) == [-5, 0, 1200]
    with pytest.raises(I.IngestError):
        I.decode_voltage(base64.b64encode(b"\x01\x02\x03").decode())
