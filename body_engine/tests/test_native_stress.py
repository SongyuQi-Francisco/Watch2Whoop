"""2.0.0 package M2 (docs/ios-native-2.0.md section 4): the phone's stress rows against the server's own.

The `local` case of packages/openbodyscore-js/test/cases_stress_cards.py (synthetic, made-up values) is stored as the app's
uploads (hk_samples: heart rate, HRV, raw steps of a watch and an iPhone, workouts; sleep_sessions; daily_scores
hrv_night_ln; a Shortcut-only day) for a user in the sync mode `app`. insights/stress.compute (the server's loader + merge +
score_range, its output unchanged by the load / compute split) must equal insights/stress.days_local on the same samples
(the reference the JS bundle is golden-tested against) on every day the app uploaded. Then the kind `stress`
(nativesync/derived_kinds/stress.py) through PUT /v1/users/me/derived/app: off by default, the spot check passes in
shadow, a changed hour level is caught (names only), primary stores the rows (origin 'app'), another user's rows are
untouched; the `records` pull stream carries record kind `stress`; /cardio still answers. Separate test database
BODY_TEST_DB_NAME_NATIVE_STRESS (default body_test_native_stress), recreated each run; skipped without Postgres.
"""
import importlib.util
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
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.algo import versions
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.insights import stress as S
from body_engine.nativesync import compute, derived_kinds, params as P
from body_engine.rawdata import engine as EN
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_NATIVE_STRESS", "body_test_native_stress")
REPO = Path(__file__).resolve().parents[2]
NEW_APP = {"X-App-Version": "2.0.0"}
HK = {"hr": "HKQuantityTypeIdentifierHeartRate", "hrv": "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
      "steps": "HKQuantityTypeIdentifierStepCount"}
DEVICE = {"Apple Watch": "watch", "iPhone": "phone"}


def _cases():
    path = REPO / "packages" / "openbodyscore-js" / "test" / "cases_stress_cards.py"
    spec = importlib.util.spec_from_file_location("openbodyscore_js_cases_stress_cards_db", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


CS = _cases()
LOCAL = CS.local_case()["src"]
TZ = ZoneInfo(LOCAL["tz"])
FIRST_APP = CS._day(CS.HISTORY_DAYS)
LAST = date.fromisoformat(LOCAL["end"])
# what the server has: the app's own samples only (no Open Wearables history for this user)
SRC = {**LOCAL, "start": FIRST_APP.isoformat(), "other": {}}


def _ts(t: float) -> datetime:
    return S.instant(t)


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


@pytest.fixture(scope="module")
def users(dsn):
    with conn_of(dsn) as conn:
        def add(name):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                                   VALUES (%s, 'member', %s, now(), '2026-10-22.2') RETURNING id""",
                                (name, LOCAL["tz"])).fetchone()["id"]
        ids = {"a": add("a"), "b": add("b")}
        flags.set_flag(conn, P.FLAG, "cohort", cohort="native", actor="test")
        for n in ids:
            flags.cohort_add(conn, ids[n], "native", actor="test")
        conn.commit()
        flags.invalidate()
        tok = {n: auth.create_token(conn, i, "app", ["read", "write"]) for n, i in ids.items()}
        conn.commit()
    return ids, tok


@pytest.fixture(scope="module")
def stored(dsn, users):
    """User a's samples, nights, workouts and night HRV as the app uploaded them; the sync mode is `app`."""
    ids, _ = users
    uid = ids["a"]
    mp = pytest.MonkeyPatch()
    mp.setattr(EN, "mode", lambda conn, user_id: "app")
    with conn_of(dsn) as c:
        src_id = {name: c.execute("""INSERT INTO hk_sources (user_id, name, device_type, device_name) VALUES (%s, %s, %s, %s)
                                     RETURNING id""", (uid, name, dt, name)).fetchone()["id"] for name, dt in DEVICE.items()}
        rows = []
        for group, xs in SRC["app"].items():
            for x in xs:
                t = _ts(x[0])
                rows.append((uid, uuid.uuid4(), HK[group], t, t + timedelta(seconds=60 if group == "steps" else 0),
                             t.astimezone(TZ).date(), x[1], x[2], src_id[x[2]]))
        for a, b in SRC["workouts"]:
            rows.append((uid, uuid.uuid4(), EN.WORKOUT, _ts(a), _ts(b), _ts(a).astimezone(TZ).date(), (b - a) / 60,
                         "Apple Watch", src_id["Apple Watch"]))
        with c.cursor() as cur:
            cur.executemany("""INSERT INTO hk_samples (user_id, hk_uuid, type, start_at, end_at, local_date, value, source,
                                                       source_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", rows)
            cur.executemany("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, is_main, source)
                               VALUES (%s, %s, %s, %s, %s, 'app')""",
                            [(uid, (_ts(b) - timedelta(hours=4)).astimezone(TZ).date(), _ts(a), _ts(b), (b - a) > 7200)
                             for a, b in SRC["sleep"]])
            cur.executemany("""INSERT INTO daily_scores (user_id, date, hrv_night_ln, algo_version) VALUES (%s, %s, %s, 'test')""",
                            [(uid, date.fromisoformat(d), v) for d, v in SRC["hrv_night"].items()])
            cur.executemany("""INSERT INTO daily_metrics (user_id, date, metric, value, agg, source)
                               VALUES (%s, %s, 'step_count', 1000, 'sum', 'shortcut')""",
                            [(uid, date.fromisoformat(d)) for d in SRC["shortcut"]])
        c.commit()
    yield uid
    mp.undo()


def test_server_stress_equals_the_phones(dsn, stored):
    """stress.compute on the uploaded samples == days_local (the JS bundle's reference) on the same rows, every app day."""
    want = S.days_local(SRC)
    with conn_of(dsn) as c:
        got = S.compute(c, None, stored, FIRST_APP, LAST)
    assert [r["date"] for r in got] == [r["date"] for r in want]
    assert got == want
    assert sum(r["score"] is not None for r in got) >= 40
    assert any("快捷指令" in r["flags"].get("no_raw_hr", "") for r in got)


def test_kind_registered_and_package_implemented():
    assert derived_kinds.get("stress").ALGO_KEY == "stress_cards" and derived_kinds.get("stress").PACKAGE == "stress"
    assert versions.current("stress_cards") == S.STRESS_CARDS_VERSION
    assert compute.package_implemented("stress")


def _upload(rows_by_day: dict, version: str = "0.3.0-test") -> dict:
    return {"kind": "stress", "algo_version": S.STRESS_CARDS_VERSION, "bundle_version": version,
            "days": [{"date": d, "rows": rows} for d, rows in sorted(rows_by_day.items(), reverse=True)]}


def _phone_rows() -> dict:
    """What the phone uploads: stressLocal's rows (dates as strings, stamped like the bundle)."""
    out = {}
    for r in S.days_local(SRC):
        row = {k: v for k, v in r.items() if k != "date"}
        out[r["date"].isoformat()] = [{**row, "date": r["date"].isoformat(), "algo_version": S.STRESS_CARDS_VERSION,
                                       "bundle_version": "0.3.0-test"}]
    return out


def test_upload_shadow_check_then_primary(client, users, dsn, stored, monkeypatch):
    ids, tok = users
    uid = stored
    monkeypatch.setattr(P, "CHECK_WITHIN_DAYS", (datetime.now(TZ).date() - LAST).days + 2)
    rows = _phone_rows()
    put = lambda body, t="a": client.put("/v1/users/me/derived/app", json=body, headers={  # noqa: E731
        "Authorization": f"Bearer {tok[t]}", **NEW_APP})
    assert put(_upload(rows)).status_code == 409, "native_stress is off by default"
    set_flag(dsn, "native_stress", "cohort", cohort="native")
    try:
        r = put(_upload(rows)).json()
        assert r["state"] == "shadow" and r["stored"] == 0
        assert r["check"]["compared"] and r["check"]["ok"], r["check"]
        assert r["check"]["date"] == LAST.isoformat()
        # an hour's level changed: caught as `hours` (names only); a small score difference is within 0.01
        day = LAST.isoformat()
        bad = {**rows, day: [dict(rows[day][0], score=rows[day][0]["score"] + 0.004,
                                  hours=[dict(h, level="high") for h in rows[day][0]["hours"]])]}
        r = put(_upload(bad, "0.3.1-test")).json()
        assert r["status"] == "mismatch" and r["check"]["fields_over"] == ["hours"], r["check"]
        # an hour's own numbers are not compared (only the levels)
        near = {**rows, day: [dict(rows[day][0], hours=[dict(h, hr=h["hr"] + 0.4) for h in rows[day][0]["hours"]])]}
        assert put(_upload(near, "0.3.2-test")).json()["check"]["ok"]
        # malformed rows are refused, another algorithm is stale
        assert put(_upload({day: [dict(rows[day][0], level="extreme")]})).status_code == 422
        assert put(_upload({day: rows[day] * 2})).status_code == 422
        assert put({**_upload(rows), "algo_version": "stress-cards-0"}).json()["status"] == "stale_algo"
        # primary: the rows are the user's (origin app), b's untouched
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "cohort", cohort="native")
        body = _upload(rows, "0.3.3-test")
        assert put(body).json()["state_after"] == "primary"
        got = put(body).json()
        assert got["state"] == "primary" and got["stored"] == len(rows), got
        with conn_of(dsn) as c:
            n = c.execute("""SELECT count(*) AS n, bool_and(bundle_version = '0.3.3-test') AS v FROM stress_daily
                             WHERE user_id = %s AND origin = 'app'""", (uid,)).fetchone()
            assert n["n"] == len(rows) and n["v"]
            one = c.execute("SELECT * FROM stress_daily WHERE user_id = %s AND date = %s", (uid, LAST)).fetchone()
            want = S.days_local(SRC)[-1]
            assert one["score"] == want["score"] and one["hours"] == want["hours"] and one["algo_version"] == S.STRESS_VERSION
            assert c.execute("SELECT count(*) AS n FROM stress_daily WHERE user_id = %s", (ids["b"],)).fetchone()["n"] == 0
    finally:
        set_flag(dsn, "native_stress", "off")
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "off")


def test_pull_records_carry_stress(client, users, dsn, stored):
    ids, tok = users
    with conn_of(dsn) as c:
        S.write(c, stored, S.compute(c, None, stored, LAST - timedelta(days=2), LAST))
        c.commit()
    r = client.post("/v1/sync/pull", json={"cursors": {"records": ""}, "limit": 2000},
                    headers={"Authorization": f"Bearer {tok['a']}", **NEW_APP})
    assert r.status_code == 200, r.text
    got = {x["date"]: x["body"] for x in r.json()["changes"] if x["stream"] == "records" and x["kind"] == "stress" and x["body"]}
    assert LAST.isoformat() in got
    item = got[LAST.isoformat()]["items"][0]
    assert {"score", "level", "hours", "baseline", "flags"} <= set(item) and "user_id" not in item
    rb = client.post("/v1/sync/pull", json={"cursors": {"records": ""}},
                     headers={"Authorization": f"Bearer {tok['b']}", **NEW_APP}).json()
    assert not [x for x in rb["changes"] if x["kind"] == "stress" and x["body"]]


def test_cardio_view_still_answers(client, users, stored):
    """GET /cardio = load + compute (no workouts here: empty HRR, recovery index from the score rows)."""
    ids, tok = users
    r = client.get(f"/v1/users/{ids['a']}/cardio", params={"end": LAST.isoformat()},
                   headers={"Authorization": f"Bearer {tok['a']}"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["hrr"]["workouts"] == [] and j["hrr"]["last30_n"] == 0 and j["recovery_index"]["median_pct"] is None
    assert client.get(f"/v1/users/{ids['a']}/cardio", headers={"Authorization": f"Bearer {tok['b']}"}).status_code in (403, 404)


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None
