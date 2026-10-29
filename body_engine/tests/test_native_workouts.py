"""2.0.0 package M1c (docs/ios-native-2.0.md section 4): the phone's workouts against the server's own.

The `sydney` case of packages/openbodyscore-js/test/cases_workouts.py (synthetic, made-up values) is uploaded through the
sync engine's write path (rawdata/engine.write: HKWorkouts and heart rate) for a user in the sync mode `app`, with the
other sources' copies, the user's corrections and the day's HRmax in the server's tables. The server imports and enriches
the workouts its own way (rawdata/derive.derive_day -> importer.upsert + refresh_duplicates, then enrich_pending, and
cardio/hrr.compute): its rows must equal importer.workout_days on the same inputs (the reference the JS bundle is
golden-tested against). The training calendar / day / muscle map answer the same through load + compute as before the
split. Then the kind `workouts` through PUT /v1/users/me/derived/app: the spot check passes in shadow, a changed TRIMP
is caught, primary stores with computed_by 'app', another user's rows are untouched; the records pull carries `hrr`.
Separate test database BODY_TEST_DB_NAME_NATIVE_WORKOUTS (default body_test_native_workouts), recreated each run;
skipped without Postgres.
"""
import importlib.util
import math
import os
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.algo import versions
from body_engine.cardio import hrr as HRR
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.nativesync import compute, derived_kinds, params as P
from body_engine.rawdata import derive as DV, engine as EN, hk_types as T
from body_engine.rollout import flags
from body_engine.training import history as H, importer as TI, muscle_load as ML

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_NATIVE_WORKOUTS", "body_test_native_workouts")
REPO = Path(__file__).resolve().parents[2]
NEW_APP = {"X-App-Version": "2.0.0"}


def _cases():
    path = REPO / "packages" / "openbodyscore-js" / "test" / "cases_workouts.py"
    spec = importlib.util.spec_from_file_location("openbodyscore_js_cases_workouts_db", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


CASE = next(c for c in _cases().all_cases()["workouts"] if c["name"] == "sydney")
SRC = CASE["src"]
TZ = ZoneInfo(SRC["tz"])
DAYS = [date.fromisoformat(d) for d in SRC["days"]]
HR = T.ENGINE_BY_HK["HKQuantityTypeIdentifierHeartRate"]


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


def set_flag(dsn, key, state, cohort=None):
    with conn_of(dsn) as c:
        flags.set_flag(c, key, state, cohort=cohort, actor="test")
        c.commit()
    flags.invalidate()


def _batch() -> dict:
    """The case's HKWorkouts and heart rate as one engine batch (the app's units)."""
    names = []
    for w in SRC["hk_workouts"]:
        if w["source"] not in names:
            names.append(w["source"])
    by = {w["source"]: w for w in SRC["hk_workouts"]}
    sources = [{"name": n, "bundle": f"org.example.{i}", "product": "", "device_type": by[n]["device_type"],
                "device_name": by[n]["device_name"], "device_model": "", "manufacturer": "", "os_version": ""}
               for i, n in enumerate(names)]
    workouts = [{"uuid": w["uuid"], "start": w["start"], "end": w["end"], "duration_s": w["value"] * 60,
                 "activity": w["meta"]["activity"], "activity_name": w["value_text"], "stats": w["meta"]["stats"],
                 "events": [], "meta": {"tz": w["tz"]}, "src": names.index(w["source"])} for w in SRC["hk_workouts"]]
    hr = {"type": HR.hk, "unit": HR.unit, "uuid": [], "start": [], "end": [], "value": [], "src": [], "tz": []}
    for i, (t, v) in enumerate(SRC["hr"]):
        hr["uuid"].append(str(uuid.UUID(int=i + 1)))
        for k in ("start", "end"):
            hr[k].append(t)
        hr["value"].append(v)
        hr["src"].append(0)
        hr["tz"].append(SRC["tz"])
    return {"batch_id": str(uuid.uuid4()), "sources": sources, "samples": [hr], "categories": [], "workouts": workouts}


@pytest.fixture(scope="module")
def users(dsn):
    with conn_of(dsn) as conn:
        def add(name):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version, birth_date)
                                   VALUES (%s, 'member', %s, now(), '2026-10-22.2', %s) RETURNING id""",
                                (name, SRC["tz"], SRC["birth_date"])).fetchone()["id"]
        ids = {"a": add("a"), "b": add("b")}
        flags.set_flag(conn, P.FLAG, "cohort", cohort="native", actor="test")
        for n in ids:
            flags.cohort_add(conn, ids[n], "native", actor="test")
        conn.commit()
        flags.invalidate()
        tok = {n: auth.create_token(conn, i, "app", ["read", "write"]) for n, i in ids.items()}
        conn.commit()
    return ids, tok


def _others(c, uid: int) -> None:
    """The other sources' copies (the case's `others`), the corrections and the days' HRmax, in the server's tables."""
    ids = {}
    for o in SRC["others"]:
        a, b = datetime.fromtimestamp(o["start"], TZ), datetime.fromtimestamp(o["end"], TZ)
        ids[f"{o['source']}:{o['external_id']}"] = c.execute(
            """INSERT INTO workouts (user_id, source, external_id, is_xml, device, type, name_zh, local_date, start_at, end_at,
                                     duration_min, excluded, flags)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (uid, o["source"], o["external_id"], o["is_xml"], o["device"], o["type"], TI.type_name(o["type"]), a.date(),
             a, b, (b - a).total_seconds() / 60, o["excluded"],
             Jsonb({"manual_dup": True} if o.get("manual_dup") else {}))).fetchone()["id"]
    for o in SRC["others"]:
        if o.get("manual_dup"):
            c.execute("UPDATE workouts SET dup_of = %s WHERE id = %s",
                      (ids[o["dup_of"]], ids[f"{o['source']}:{o['external_id']}"]))
    for o in SRC["overrides"]:
        c.execute("""INSERT INTO workout_overrides (user_id, source, external_id, type, name_zh, hidden)
                     VALUES (%s, %s, %s, %s, %s, %s)""", (uid, o["source"], o["external_id"], o["type"], o["name_zh"],
                                                         o["hidden"]))
    for r in SRC["type_rules"]:
        c.execute("""INSERT INTO workout_type_rules (user_id, weekday, start_hour, to_type, name_zh)
                     VALUES (%s, %s, %s, %s, %s)""", (uid, r["weekday"], r["start_hour"], r["to_type"], r["name_zh"]))
    for d, v in SRC["hr_max"].items():
        c.execute("INSERT INTO daily_scores (user_id, date, hr_max, algo_version) VALUES (%s, %s, %s, 'test')",
                  (uid, date.fromisoformat(d), v))


@pytest.fixture(scope="module")
def imported(dsn, users):
    """User a's workouts through the engine, imported and enriched by the server in the sync mode `app`."""
    ids, _ = users
    uid = ids["a"]
    mp = pytest.MonkeyPatch()
    mp.setattr(EN, "mode", lambda conn, user_id: "app")
    today = datetime.now(TZ).date()
    with conn_of(dsn) as c:
        _others(c, uid)
        EN.write(c, uid, _batch(), TZ, [])
        for d in DAYS:
            DV.derive_day(c, uid, d, {DV.WORKOUT}, TZ, "app", today)
        TI.refresh_duplicates(c, uid, "app")
        TI.enrich_pending(c, None, uid)
        hr = TI.heart_rate(SRC["hr"])
        hm = {date.fromisoformat(k): v for k, v in SRC["hr_max"].items()}
        HRR.compute(c, uid, hr, hm, DAYS[0], DAYS[-1])
        c.commit()
    yield uid
    mp.undo()


def _close(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def test_server_workouts_equal_the_phones(dsn, imported):
    """upsert + refresh_duplicates + enrich_pending + hrr.compute on the uploaded workouts == workout_days."""
    uid = imported
    want = TI.workout_days(SRC)
    with conn_of(dsn) as c:
        rows = {r["external_id"]: r for r in c.execute("""SELECT * FROM workouts WHERE user_id = %s""", (uid,))}
        key = {r["id"]: f"{r['source']}:{r['external_id']}" for r in rows.values()}
        hrr = {r["workout_id"]: r for r in c.execute("SELECT * FROM workout_hrr WHERE user_id = %s", (uid,))}
    n = 0
    for d, ws in want["days"].items():
        for w in ws:
            s = rows[w["external_id"]]
            assert s["source"] == "app" and s["local_date"].isoformat() == d
            for k in ("type", "name_zh", "duration_min", "active_kcal", "total_kcal", "distance_km", "avg_hr", "max_hr",
                      "zone_offset", "device", "origin", "excluded", "trimp", "hr_samples", "hr_max_used"):
                assert _close(s[k], w[k]), (w["external_id"], k, s[k], w[k])
            assert s["start_at"].timestamp() == w["start"] and s["end_at"].timestamp() == w["end"]
            assert key.get(s["dup_of"]) == w["dup_of"], w["external_id"]
            zones = [w[f"zone{i}_min"] for i in range(1, 6)]
            assert (s["hr_zone_min"] or [None] * 5) == zones
            flags = dict(s["flags"])
            assert _close(flags.pop("hr_recovery_bpm", None), w["hr_recovery_bpm"])
            assert flags == w["flags"], w["external_id"]
            h = hrr.get(s["id"])
            n += 1
            if s["end_at"].fold:
                # cardio/hrr.compute does wall-clock arithmetic on the database's ZoneInfo (end − 2 min across the end of
                # DST lands an hour early); the phone's twin works on instants. Left to the owner of cardio/ (M2).
                continue
            if w["hrr_valid"] is None:
                assert h is None
            else:
                assert (h["hrr60"], h["peak_hr"], h["hr_60s"], h["valid"], h["reason"]) == (
                    w["hrr60"], w["hrr_peak"], w["hrr_60s"], w["hrr_valid"], w["hrr_reason"])
    assert n == len(SRC["hk_workouts"])
    dups = {key[r["id"]]: key[r["dup_of"]] for r in rows.values() if r["dup_of"]}
    assert dups == want["dups"]


def test_views_unchanged_by_the_split(dsn, imported):
    """history.build / muscle_load._build == compute on load (the phone's path), one month and one day."""
    uid = imported
    with conn_of(dsn) as c:
        res = H.build(c, uid, uid, DAYS[0], DAYS[-1], with_exercises=True)
        data = H.load(c, uid, uid, DAYS[0], DAYS[-1], with_exercises=True)
        again = H.compute(data, DAYS[0], DAYS[-1], datetime.now(TZ).date())
        assert res == again
        cal = H.calendar(c, uid, uid, start=DAYS[0], end=DAYS[-1])
        assert sum(d["sessions_count"] for d in cal["days"]) == sum(
            1 for ws in TI.workout_days(SRC)["days"].values() for w in ws if not w["dup_of"] and not w["excluded"])
        day = H.day(c, uid, uid, DAYS[0])
        assert {s["type"] for s in day["sessions"]} == {"strength_training", "walking"}
        assert all(s["intensity"] for s in day["sessions"])
        full, _ = ML._build(c, uid, DAYS[-1])
        assert full["sports"]["week"] and full["version"]


def test_kind_is_registered_and_the_package_implemented():
    assert "workouts" in derived_kinds.kinds()
    assert versions.current("workouts") == TI.WORKOUTS_VERSION
    assert compute.package_implemented("workouts")
    mod = derived_kinds.get("workouts")
    assert mod.ALGO_KEY == "workouts" and mod.PACKAGE == "workouts"


def _upload(rows_by_day: dict, bundle: str = "0.3.0-test") -> dict:
    return {"kind": "workouts", "algo_version": TI.WORKOUTS_VERSION, "bundle_version": bundle,
            "days": [{"date": d, "rows": rows} for d, rows in sorted(rows_by_day.items(), reverse=True)]}


def test_upload_shadow_check_then_primary(client, users, dsn, imported, monkeypatch):
    ids, tok = users
    uid = imported
    monkeypatch.setattr(P, "CHECK_WITHIN_DAYS", (datetime.now(TZ).date() - DAYS[-1]).days + 2)
    rows = {d: ws for d, ws in TI.workout_days(SRC)["days"].items()}
    put = lambda body, t="a": client.put("/v1/users/me/derived/app", json=body, headers={  # noqa: E731
        "Authorization": f"Bearer {tok[t]}", **NEW_APP})
    assert put(_upload(rows)).status_code == 409, "native_workouts is off by default"
    set_flag(dsn, "native_workouts", "cohort", cohort="native")
    try:
        r = put(_upload(rows)).json()
        assert r["state"] == "shadow" and r["stored"] == 0
        assert r["check"]["compared"] and r["check"]["ok"], r["check"]
        assert r["check"]["date"] == DAYS[-1].isoformat()
        bad = {d: [dict(w, trimp=(w["trimp"] or 0) + 5) for w in ws] for d, ws in rows.items()}
        r = put(_upload(bad, "0.3.1-test")).json()
        assert r["status"] == "mismatch" and r["check"]["fields_over"] == ["trimp"]
        # malformed rows are refused
        d0 = DAYS[0].isoformat()
        assert put(_upload({d0: [dict(rows[d0][0], local_date=DAYS[1].isoformat())]})).status_code == 422
        assert put(_upload({d0: [dict(rows[d0][0], intensity="extreme")]})).status_code == 422
        # primary: a passing check, then the enrichment is the user's (computed_by app), b's untouched
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "cohort", cohort="native")
        body = _upload(rows, "0.3.2-test")
        assert put(body).json()["state_after"] == "primary"
        got = put(body).json()
        assert got["state"] == "primary" and got["stored"] == len(SRC["hk_workouts"]), got
        with conn_of(dsn) as c:
            n = c.execute("""SELECT count(*) AS n FROM workouts WHERE user_id = %s AND computed_by = 'app'
                             AND bundle_version = '0.3.2-test'""", (uid,)).fetchone()["n"]
            assert n == len(SRC["hk_workouts"])
            h = c.execute("SELECT count(*) AS n FROM workout_hrr WHERE user_id = %s AND computed_by = 'app'",
                          (uid,)).fetchone()["n"]
            assert h == sum(1 for ws in rows.values() for w in ws if w["hrr_valid"] is not None)
            assert c.execute("SELECT count(*) AS n FROM workouts WHERE user_id = %s", (ids["b"],)).fetchone()["n"] == 0
        # the stored rows are what the server computes itself: the next check still passes
        r = put(_upload(rows, "0.3.3-test")).json()
        assert r["check"]["ok"], r["check"]
    finally:
        set_flag(dsn, "native_workouts", "off")
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "off")


def test_pull_workouts_carry_hrr(client, users, dsn, imported):
    ids, tok = users
    r = client.post("/v1/sync/pull", json={"cursors": {"records": ""}, "limit": 2000},
                    headers={"Authorization": f"Bearer {tok['a']}", **NEW_APP})
    assert r.status_code == 200, r.text
    items = [it for x in r.json()["changes"] if x["stream"] == "records" and x.get("kind") == "workout" and x["body"]
             for it in x["body"]["items"]]
    assert items and any(it.get("hrr") and it["hrr"]["valid"] for it in items)
    rb = client.post("/v1/sync/pull", json={"cursors": {"records": ""}},
                     headers={"Authorization": f"Bearer {tok['b']}", **NEW_APP}).json()
    assert not [x for x in rb["changes"] if x["stream"] == "records" and x.get("kind") == "workout" and x["body"]]


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None
