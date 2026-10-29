"""2.0.0 P0 seams (docs/ios-native-2.0.md sections 2.2, 3 and 5): the derived-kind registry and its upload
(PUT /v1/users/me/derived/app, spot check, state), GET /v1/sync/compute, the ext_records pull, the registry in the pull
streams, the rollout flags. No kind ships in P0: the tests register a fictional one. Fictional data only. Separate test
database BODY_TEST_DB_NAME_NATIVE_COMPUTE (default body_test_native_compute), recreated each run; skipped without Postgres.
"""
import os
import types
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.algo import versions
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.nativesync import compute, derived, derived_kinds, ext_records, params as P
from body_engine.rollout import flags, registry

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_NATIVE_COMPUTE", "body_test_native_compute")
TZ = "Australia/Sydney"
NEW_APP = {"X-App-Version": "2.0.0"}


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
        # the fictional kind's own table (a real package adds its table + sync trigger in its migration range)
        conn.execute("CREATE TABLE probe_rows (user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE, day DATE NOT NULL, value REAL)")
        conn.execute("SELECT sync_attach('probe_rows', 'records', 'probe', 'day')")
        conn.commit()
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
        for n in ("a", "b"):
            flags.cohort_add(conn, ids[n], "native", actor="test")
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
    return {"Authorization": f"Bearer {t}", **NEW_APP, **extra}


def set_flag(dsn, key, state, cohort=None):
    with conn_of(dsn) as c:
        flags.set_flag(c, key, state, cohort=cohort, actor="test")
        c.commit()
    flags.invalidate()


# ---------------------------------------------------------------- a fictional kind (never shipped)
def probe_kind(server_value=1.0):
    """Kind "probe" of package derive, algorithm `scores` (in the bundle), rows {"value": float}; recompute answers
    server_value for every day; write goes to probe_rows of the uploading user only."""
    written: list = []

    def validate(day, rows):
        out = []
        for r in rows:
            if set(r) != {"value"} or not isinstance(r["value"], (int, float)):
                raise ValueError("probe rows are {value: number}")
            out.append({"value": float(r["value"])})
        return out

    def write(conn, uid, days, bundle_version):
        written.append((uid, sorted(days)))
        for d, rows in days.items():
            conn.execute("DELETE FROM probe_rows WHERE user_id = %s AND day = %s", (uid, d))
            for r in rows:
                conn.execute("INSERT INTO probe_rows (user_id, day, value) VALUES (%s, %s, %s)", (uid, d, r["value"]))
        return sum(len(r) for r in days.values())

    def record_body(conn, uid, days):
        rows = conn.execute("SELECT day, value FROM probe_rows WHERE user_id = %s AND day = ANY(%s) ORDER BY day, value",
                            (uid, days)).fetchall()
        out: dict = {}
        for r in rows:
            out.setdefault(r["day"], {"items": []})["items"].append({"value": r["value"]})
        return out

    def extend_pull(stream, kind, conn, uid, found):
        if stream == "daily":
            for body in found.values():
                body["probe"] = True

    mod = types.SimpleNamespace(
        KIND="probe", PACKAGE="derive", ALGO_KEY="scores", TEST_ONLY=True, TOLERANCE={"value": 0.5},
        validate=validate, write=write, recompute=lambda conn, uid, day, uploaded: [{"value": server_value}],
        RECORD_KIND="probe", RECORD_TABLE="probe_rows", RECORD_DAYCOL="day", record_body=record_body, extend_pull=extend_pull,
        written=written)
    return mod


@pytest.fixture()
def probe():
    mod = probe_kind()
    derived_kinds.register(mod)
    yield mod
    derived_kinds.unregister("probe")


# ---------------------------------------------------------------- flags, registry, versions
def test_flags_are_registered_and_off(dsn, users):
    keys = {"native_derive", "native_workouts", "native_stress", "native_series", "native_coach", "native_reports",
            "native_rules", "native_compute_primary", "force_upgrade"}
    assert keys <= set(registry.FLAGS)
    assert {s["flag"] for s in P.COMPUTE_PACKAGES.values()} | {P.COMPUTE_PRIMARY_FLAG} <= keys
    ids, _ = users
    with conn_of(dsn) as c:
        for k in keys:
            assert not flags.enabled(c, ids["a"], k)


def test_registry_ships_no_kind_and_checks_modules():
    assert derived_kinds.kinds() == {}
    assert derived_kinds.record_kinds() == {}
    with pytest.raises(derived_kinds.BadKind):
        derived_kinds.register(types.SimpleNamespace(KIND="x", PACKAGE="derive"))
    bad = probe_kind()
    bad.TEST_ONLY = False   # "probe" is not one of derive's kinds
    with pytest.raises(derived_kinds.BadKind):
        derived_kinds.register(bad)
    assert derived_kinds.get("probe") is None


def test_app_version_gate():
    assert compute.app_meets("2.0.0", None, "2.0.0") and compute.app_meets("2.1", None, "2.0.0")
    assert not compute.app_meets("1.0.9", "99", "2.0.0") and not compute.app_meets(None, None, "2.0.0")
    assert compute.app_meets("2.0.0", "57", "2.0.0+57") and not compute.app_meets("2.0.0", "56", "2.0.0+57")
    assert not compute.app_meets("2.0.0", None, "2.0.0+57") and compute.app_meets("2.0.1", None, "2.0.0+57")


def test_fields_over_names_only():
    tol = {"a": 0.5, "r": ("rel", 1e-4)}
    assert derived.fields_over([{"a": 1.0, "r": 1000.0, "s": "x"}], [{"a": 1.4, "r": 1000.05, "s": "x"}], tol) == []
    assert derived.fields_over([{"a": 1.0, "r": 1000.0, "s": "x"}], [{"a": 2.0, "r": 1001.0, "s": "y"}], tol) == ["a", "r", "s"]
    assert derived.fields_over([{"a": 1}], [], tol) == ["rows"]
    key = lambda r: r["k"]   # noqa: E731
    assert derived.fields_over([{"k": 1, "a": 1}, {"k": 2, "a": 2}], [{"k": 2, "a": 2}, {"k": 1, "a": 1}], tol, key) == []


# ---------------------------------------------------------------- GET /v1/sync/compute
def test_compute_is_off_for_everyone_by_default(client, users, dsn):
    ids, tok = users
    r = client.get("/v1/sync/compute", headers=H(tok["a"]))
    assert r.status_code == 200, r.text
    j = r.json()
    # a superset of /v1/sync/scoring
    scoring = client.get("/v1/sync/scoring", headers=H(tok["a"])).json()
    assert set(scoring) <= set(j) and j["algo_version"] == scoring["algo_version"]
    c = j["compute"]
    assert set(c["packages"]) == set(P.COMPUTE_PACKAGES)
    assert all(p["state"] == "off" for p in c["packages"].values())
    assert all(k["state"] == "off" and not k["registered"] for p in c["packages"].values() for k in p["kinds"].values())
    assert c["server_algo_versions"]["scores"] == versions.current("scores") and c["primary_flag"] is False
    # every package flag on for everyone: a package this server does not implement stays off; a views-only package
    # (M3 series: nothing uploaded, nothing to spot-check) is shadow, then primary once native_compute_primary is on
    for spec in P.COMPUTE_PACKAGES.values():
        set_flag(dsn, spec["flag"], "all")
    try:
        for primary in (False, True):
            if primary:
                set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "all")
            j = client.get("/v1/sync/compute", headers=H(tok["b"])).json()
            for name, p in j["compute"]["packages"].items():
                assert p["flag"] and p["implemented"] == compute.package_implemented(name), name
                if not p["implemented"]:
                    assert p["state"] == "off", name
                elif not P.COMPUTE_PACKAGES[name]["kinds"]:
                    assert p["state"] == ("primary" if primary else "shadow"), name
            assert j["compute"]["packages"]["series"]["implemented"]   # M3 (trends/series.SERIES_VERSION + src/33_series.js)
        old = client.get("/v1/sync/compute", headers=H(tok["b"], **{"X-App-Version": "1.0.9"})).json()
        assert old["compute"]["packages"]["series"]["state"] == "off", "older apps never compute a package"
    finally:
        for spec in P.COMPUTE_PACKAGES.values():
            set_flag(dsn, spec["flag"], "off")
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "off")
    assert client.get("/v1/sync/compute", headers=H(tok["off"])).status_code == 404   # native_sync off
    assert client.get("/v1/sync/compute", params={"hist_from": "2024-01-01", "hist_to": "2026-02-09"},
                      headers=H(tok["a"])).status_code == 422


# ---------------------------------------------------------------- PUT /v1/users/me/derived/app
def _body(days, value=1.0, algo=None, bundle="0.3.0"):
    return {"kind": "probe", "algo_version": algo or versions.current("scores"), "bundle_version": bundle,
            "days": [{"date": d.isoformat(), "rows": [{"value": value}]} for d in days]}


def test_derived_upload_auth_flags_check_and_primary(client, users, dsn, probe):
    ids, tok = users
    uid = ids["a"]
    today = datetime.now(ZoneInfo(TZ)).date()
    days = [today - timedelta(days=i) for i in (3, 2, 1)]
    up = lambda body, t="a", user="me", **h: client.put(f"/v1/users/{user}/derived/app", json=body, headers=H(tok[t], **h))   # noqa: E731
    # unknown kind / flag native_sync off / someone else / read-only token
    assert up({**_body(days), "kind": "nope"}).status_code == 422
    assert up(_body(days), "off").status_code == 404
    assert up(_body(days), user=str(ids["b"])).status_code == 403, "a user cannot write another's rows"
    assert up(_body(days), "a_ro").status_code == 403
    # the package flag is off: kind_off, nothing kept
    r = up(_body(days))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "kind_off"
    set_flag(dsn, "native_derive", "cohort", cohort="native")
    try:
        assert up(_body(days), **{"X-App-Version": "1.0.9"}).status_code == 409, "older apps never compute a package"
        # malformed rows / a future day
        assert up({**_body(days), "days": [{"date": days[0].isoformat(), "rows": [{"nope": 1}]}]}).status_code == 422
        assert up(_body([today + timedelta(days=1)])).status_code == 422
        # another algorithm version: stale_algo, nothing kept
        stale = up(_body(days, algo="v0-old")).json()
        assert stale["status"] == "stale_algo" and stale["stored"] == 0
        # shadow: checked (newest day before today), equal -> ok, but not stored
        ok = up(_body(days)).json()
        assert ok["status"] == "ok" and ok["state"] == "shadow" and ok["stored"] == 0
        assert ok["check"] == {"date": days[-1].isoformat(), "ok": True, "compared": True, "fields_over": []}
        assert probe.written == []
        # a second upload within CHECK_EVERY_HOURS of the same bundle is not checked
        assert up(_body(days[:1])).json()["check"] is None
        # a different answer: mismatch, names only in the check table, the kind is untrusted
        bad = up(_body(days, value=9.0, bundle="0.3.1")).json()
        assert bad["status"] == "mismatch" and bad["check"]["fields_over"] == ["value"]
        with conn_of(dsn) as c:
            chk = c.execute("SELECT * FROM app_compute_checks WHERE user_id = %s AND NOT ok", (uid,)).fetchone()
            assert chk["kind"] == "probe" and chk["fields_over"] == ["value"] and "app" not in str(chk)
            st = compute.kind_row(c, uid, "probe")
            assert st["trusted"] is False and st["uploads"] == 3 and st["days_uploaded"] == 3 + 1 + 3
        # primary on: an untrusted kind stays shadow; a passing check makes it primary and the next upload is stored
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "cohort", cohort="native")
        assert up(_body(days, bundle="0.3.2")).json()["state_after"] == "primary"
        got = up(_body(days, bundle="0.3.2")).json()
        assert got["state"] == "primary" and got["stored"] == 3 and probe.written == [(uid, days)]
        with conn_of(dsn) as c:
            assert c.execute("SELECT count(*) AS n FROM probe_rows WHERE user_id = %s", (uid,)).fetchone()["n"] == 3
            assert c.execute("SELECT count(*) AS n FROM probe_rows WHERE user_id <> %s", (uid,)).fetchone()["n"] == 0
            derived.set_server_forced(c, uid, "probe", True)
            assert compute.kind_state(c, uid, "probe", "2.0.0", None) == "shadow"
            derived.set_server_forced(c, uid, "probe", False)
        # b's state is b's own
        with conn_of(dsn) as c:
            assert compute.kind_row(c, ids["b"], "probe") is None
            assert compute.kind_state(c, ids["b"], "probe", "2.0.0", None) == "shadow"
    finally:
        set_flag(dsn, "native_derive", "off")
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "off")


def test_registry_kinds_reach_the_pull_streams(client, users, dsn, probe):
    ids, tok = users
    uid = ids["b"]
    d = date(2026, 1, 12)
    with conn_of(dsn) as c:
        c.execute("INSERT INTO probe_rows (user_id, day, value) VALUES (%s, %s, 4.5), (%s, %s, 7.0)", (uid, d, ids["a"], d))
        c.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                     VALUES (%s, %s, 'step_count', 1000, 'count', 'sum', 'test')""", (uid, d))
        c.commit()
    r = client.post("/v1/sync/pull", json={"cursors": {"records": "", "daily": ""}}, headers=H(tok["b"]))
    assert r.status_code == 200, r.text
    ch = r.json()["changes"]
    probes = [x for x in ch if x["stream"] == "records" and x["kind"] == "probe"]
    assert probes == [{**probes[0], "date": d.isoformat(), "body": {"items": [{"value": 4.5}]}}], "only b's own rows"
    daily = [x for x in ch if x["stream"] == "daily" and x["date"] == d.isoformat()]
    assert daily and daily[0]["body"]["probe"] is True
    # incremental: a change of the kind's table comes as a records change of that kind
    cur = r.json()["cursors"]
    with conn_of(dsn) as c:
        c.execute("UPDATE probe_rows SET value = 5.5 WHERE user_id = %s", (uid,))
        c.commit()
    j = client.post("/v1/sync/pull", json={"cursors": {"records": cur["records"]}}, headers=H(tok["b"])).json()
    assert [(x["kind"], x["body"]) for x in j["changes"]] == [("probe", {"items": [{"value": 5.5}]})]


# ---------------------------------------------------------------- GET /v1/users/me/ext-records
class FakeLog:
    """A food / training log in the shapes sparky_client returns (own = True: its module functions ask it directly)."""
    own = True

    def __init__(self, sessions, weights, foods):
        self.sessions, self.weights, self.foods = sessions, weights, foods

    def training_log(self, start, end):
        return [s for s in self.sessions if start.isoformat() <= s["date"] <= end.isoformat()]

    def weight(self, start, end):
        return [w for w in self.weights if start.isoformat() <= w["date"] <= end.isoformat()]

    def food_entries(self, start, end):
        return [f for f in self.foods if start.isoformat() <= f["date"] <= end.isoformat()]


def _log(uid_tag: str, kg=80.0):
    sets = [{"id": 1, "set": 1, "type": "normal", "reps": 8, "weight_kg": 60, "rir": None, "rpe": None, "duration_s": None,
             "distance_km": None, "notes": None}]
    session = {"id": f"{uid_tag}-s1", "date": "2026-01-10", "name": "Push", "notes": None, "source": "app",
               "total_duration_minutes": 45,
               "exercises": [{"entry_id": "e1", "exercise_id": "x1", "name": "Bench press", "category": "strength",
                              "primary_muscles": ["chest"], "notes": None, "superset_group": None, "duration_minutes": None,
                              "calories_burned": None, "avg_heart_rate": None, "sets": sets}]}
    weights = [{"date": "2026-01-10", "weight_kg": kg}, {"date": "2026-01-11", "weight_kg": kg - 0.2}]
    foods = [{"id": "f1", "date": "2026-01-10", "calories": 500.0, "protein_g": 30.0, "carbs_g": 50.0, "fat_g": 10.0},
             {"id": "f2", "date": "2026-01-10", "calories": 250.5, "protein_g": 20.0, "carbs_g": None, "fat_g": 5.0}]
    return FakeLog([session], weights, foods)


def test_ext_records_raw_rows_fingerprints_and_auth(client, users, dsn, monkeypatch):
    ids, tok = users
    logs = {ids["a"]: _log("a"), ids["b"]: _log("b", kg=70.0)}
    monkeypatch.setattr(ext_records, "reader", lambda conn, uid: logs[uid])
    q = {"from": "2026-01-01", "to": "2026-01-31"}
    r = client.get("/v1/users/me/ext-records", params=q, headers=H(tok["a_ro"]))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["available"] is True and j["error"] is None and [d["date"] for d in j["days"]] == ["2026-01-10", "2026-01-11"]
    d10, d11 = j["days"]
    assert d10["strength_sets"] == logs[ids["a"]].sessions, "raw rows, as the log has them"
    assert d10["weights"] == [{"date": "2026-01-10", "weight_kg": 80.0}]
    assert d10["nutrition_totals"] == {"entries": 2, "calories": 750.5, "protein_g": 50.0, "carbs_g": 50.0, "fat_g": 15.0,
                                       "saturated_fat_g": None, "fiber_g": None, "sugar_g": None, "sodium_mg": None}
    assert d11["strength_sets"] == [] and d11["nutrition_totals"] is None
    assert d10["fingerprint"] == ext_records.fingerprint(d10) and len(d10["fingerprint"]) == 64
    # fingerprints only, stable across calls
    fp = client.get("/v1/users/me/ext-records", params={**q, "fingerprints": 1}, headers=H(tok["a"])).json()
    assert "days" not in fp and fp["fingerprints"] == {"2026-01-10": d10["fingerprint"], "2026-01-11": d11["fingerprint"]}
    assert client.get("/v1/users/me/ext-records", params={**q, "fingerprints": 1}, headers=H(tok["a"])).json() == fp
    # a changed weigh-in changes that day's fingerprint only
    logs[ids["a"]].weights[1]["weight_kg"] = 79.0
    fp2 = client.get("/v1/users/me/ext-records", params={**q, "fingerprints": 1}, headers=H(tok["a"])).json()["fingerprints"]
    assert fp2["2026-01-10"] == fp["fingerprints"]["2026-01-10"] and fp2["2026-01-11"] != fp["fingerprints"]["2026-01-11"]
    # per user: b sees b's log, never a's; nobody reads someone else's
    jb = client.get("/v1/users/me/ext-records", params=q, headers=H(tok["b"])).json()
    assert jb["days"][0]["weights"] == [{"date": "2026-01-10", "weight_kg": 70.0}]
    assert jb["days"][0]["strength_sets"][0]["id"] == "b-s1"
    assert client.get(f"/v1/users/{ids['a']}/ext-records", params=q, headers=H(tok["b"])).status_code == 403
    assert client.get("/v1/users/me/ext-records", params=q, headers=H(tok["off"])).status_code == 404
    # range limits; a log that cannot be read is "not available", not an empty log
    assert client.get("/v1/users/me/ext-records", params={"from": "2024-01-01", "to": "2026-01-31"},
                      headers=H(tok["a"])).status_code == 422
    assert client.get("/v1/users/me/ext-records", params={"from": "2026-02-01", "to": "2026-01-31"},
                      headers=H(tok["a"])).status_code == 422

    def unreadable(conn, uid):
        raise sparky_client.SparkyError("not linked")
    monkeypatch.setattr(ext_records, "reader", unreadable)
    na = client.get("/v1/users/me/ext-records", params=q, headers=H(tok["a"])).json()
    assert na["available"] is False and na["error"] == "not_linked" and na["days"] == []
