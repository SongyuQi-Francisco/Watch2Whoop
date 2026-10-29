"""2.0.0 package M1 (docs/ios-native-2.0.md section 4): the phone's derivation against the server's own.

The `new_york` case of packages/openbodyscore-js/test/cases_derive.py (synthetic, made-up values) is uploaded through the
sync engine's write path (rawdata/engine.write: samples, sleep stages, 15-minute buckets, the phase-1 rebuild) for a user
in the sync mode `app`; the server derives every day with rawdata/derive.derive_day + common.resolve_daily. Its rows must
equal derive.derive_days on the same samples (the reference the JS bundle is golden-tested against, so the phone's rows
equal the server's). Then the kinds `daily` / `sleep` (nativesync/derived_kinds) through PUT /v1/users/me/derived/app:
spot check passes in shadow, primary stores with origin 'app', another user's rows are untouched; the pull carries
`daily.sources`; raw_history carries 15-minute steps. Separate test database BODY_TEST_DB_NAME_NATIVE_DERIVE (default
body_test_native_derive), recreated each run; skipped without Postgres.
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
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.algo import versions
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import common
from body_engine.nativesync import compute, derived_kinds, history, params as P
from body_engine.rawdata import derive as DV, engine as EN, hk_types as T
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_NATIVE_DERIVE", "body_test_native_derive")
REPO = Path(__file__).resolve().parents[2]
NEW_APP = {"X-App-Version": "2.0.0"}


def _cases():
    path = REPO / "packages" / "openbodyscore-js" / "test" / "cases_derive.py"
    spec = importlib.util.spec_from_file_location("openbodyscore_js_cases_derive_db", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


CASE = next(c for c in _cases().all_cases()["cases"] if c["name"] == "new_york")
SRC = CASE["src"]
TZ = ZoneInfo(SRC["tz"])
DAYS = [date.fromisoformat(d) for d in SRC["days"]]


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
    """The case's rows as one engine batch (the app's units; sources by index)."""
    names = []
    for s in SRC["samples"]:
        if s["source"] not in names:
            names.append(s["source"])
    by_name = {s["source"]: s for s in SRC["samples"]}
    sources = [{"name": n, "bundle": f"org.example.{i}", "product": "", "device_type": by_name[n]["device_type"],
                "device_name": by_name[n]["device_name"], "device_model": "", "manufacturer": "", "os_version": ""}
               for i, n in enumerate(names)]
    groups: dict = {}
    for s in SRC["samples"]:
        t = T.ENGINE_BY_HK[s["type"]]
        field = "categories" if t.kind == "category" else "samples"
        g = groups.setdefault((field, t.hk), {"type": t.hk, "unit": t.unit, "uuid": [], "start": [], "end": [], "value": [],
                                              "src": [], "tz": []})
        if field == "categories":
            g.pop("unit", None)
        for k in ("uuid", "start", "end", "value"):
            g[k].append(s[k])
        g["src"].append(names.index(s["source"]))
        g["tz"].append(SRC["tz"])
    stats = [{"type": b["type"], "unit": T.ENGINE_BY_HK[b["type"]].unit, "bucket_s": 900, "start": int(b["start"]),
              "values": [b["value"]]} for b in SRC["buckets"]]
    return {"batch_id": str(uuid.uuid4()), "sources": sources,
            "samples": [g for (f, _), g in groups.items() if f == "samples"],
            "categories": [g for (f, _), g in groups.items() if f == "categories"], "stats": stats}


@pytest.fixture(scope="module")
def users(dsn):
    with conn_of(dsn) as conn:
        def add(name):
            return conn.execute("""INSERT INTO users (display_name, role, timezone, consent_at, consent_version)
                                   VALUES (%s, 'member', %s, now(), '2026-10-22.2') RETURNING id""",
                                (name, SRC["tz"])).fetchone()["id"]
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
def derived(dsn, users):
    """User a's samples through the engine, every day derived by the server in the sync mode `app`."""
    ids, _ = users
    uid = ids["a"]
    mp = pytest.MonkeyPatch()
    mp.setattr(EN, "mode", lambda conn, user_id: "app")
    today = date.fromisoformat(SRC["today"])
    with conn_of(dsn) as c:
        for r in SRC["other"]:
            common.upsert_daily(c, uid, r["source"], [{**r, "date": date.fromisoformat(r["date"])}], today)
        EN.write(c, uid, _batch(), TZ, [])
        for d in DAYS:
            DV.derive_day(c, uid, d, {t.hk for t in T.ENGINE_TYPES}, TZ, "app", today)
        c.commit()
    yield uid
    mp.undo()


def _close(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def _same_rows(got: list[dict], want: list[dict]) -> list[str]:
    bad = []
    g, w = {r["metric"]: r for r in got}, {r["metric"]: r for r in want}
    if set(g) != set(w):
        return [f"metrics {sorted(set(g) ^ set(w))}"]
    for m, r in w.items():
        for k, v in r.items():
            if not _close(g[m].get(k), v):
                bad.append(f"{m}.{k}: {g[m].get(k)} != {v}")
    return bad


def test_server_derivation_equals_the_phones(dsn, derived):
    """derive_day + resolve_daily on the uploaded samples == derive_days (the JS bundle's reference) on the same rows."""
    want = DV.derive_days(SRC)
    uid = derived
    cols = "metric, source, value, unit, agg, n, min_value, max_value, flags"
    with conn_of(dsn) as c:
        for d in DAYS:
            w = want["daily"][d.isoformat()]
            srcs = c.execute(f"SELECT {cols} FROM daily_metric_sources WHERE user_id = %s AND date = %s AND source = 'app'",
                             (uid, d)).fetchall()
            assert _same_rows(srcs, w["sources"]) == [], d
            chosen = c.execute(f"SELECT {cols} FROM daily_metrics WHERE user_id = %s AND date = %s", (uid, d)).fetchall()
            assert _same_rows(chosen, w["chosen"]) == [], d
        for w_day, sessions in want["sleep"].items():
            got = c.execute("""SELECT start_at, end_at, asleep_min, core_min, deep_min, rem_min, awake_min, unspecified_min,
                                      is_main, device FROM sleep_sessions WHERE user_id = %s AND wake_date = %s
                               ORDER BY start_at""", (uid, date.fromisoformat(w_day))).fetchall()
            assert len(got) == len(sessions), w_day
            for g, s in zip(got, sessions):
                assert g["start_at"].timestamp() == s["start"] and g["end_at"].timestamp() == s["end"]
                assert {k: g[k] for k in ("asleep_min", "core_min", "deep_min", "rem_min", "awake_min", "unspecified_min",
                                          "is_main", "device")} == {k: s[k] for k in g if k not in ("start_at", "end_at")}
    assert sum(len(v) for v in want["sleep"].values()) >= 25


def test_kinds_are_registered_and_the_package_implemented():
    assert set(derived_kinds.kinds()) >= {"daily", "sleep"}
    assert versions.current("derive") == DV.DERIVE_VERSION
    assert compute.package_implemented("derive")
    assert derived_kinds.get("daily").ALGO_KEY == "derive" and derived_kinds.get("sleep").PACKAGE == "derive"


def _upload(rows_by_day: dict, kind: str) -> dict:
    return {"kind": kind, "algo_version": DV.DERIVE_VERSION, "bundle_version": "0.3.0-test",
            "days": [{"date": d, "rows": rows} for d, rows in sorted(rows_by_day.items(), reverse=True)]}


def _phone_rows(today: date) -> tuple[dict, dict]:
    """What the phone uploads (derive_days on its rows), shifted so the last days are within the spot-check window."""
    want = DV.derive_days(SRC)
    daily = {d: [{"role": "source", **r} for r in v["sources"]] + [{"role": "chosen", **r} for r in v["chosen"]]
             for d, v in want["daily"].items()}
    return daily, want["sleep"]


def test_upload_shadow_check_then_primary(client, users, dsn, derived, monkeypatch):
    ids, tok = users
    uid = derived
    # the case's days are in the past: the spot check looks at the newest uploaded day within 14 days of "today"
    last = DAYS[-1]
    monkeypatch.setattr(P, "CHECK_WITHIN_DAYS", (datetime.now(TZ).date() - last).days + 2)
    daily, sleep = _phone_rows(last)
    put = lambda body, t="a": client.put("/v1/users/me/derived/app", json=body, headers={  # noqa: E731
        "Authorization": f"Bearer {tok[t]}", **NEW_APP})
    assert put(_upload(daily, "daily")).status_code == 409, "native_derive is off by default"
    set_flag(dsn, "native_derive", "cohort", cohort="native")
    try:
        r = put(_upload(daily, "daily")).json()
        assert r["state"] == "shadow" and r["stored"] == 0
        assert r["check"]["compared"] and r["check"]["ok"], r["check"]
        r = put(_upload(sleep, "sleep")).json()
        assert r["state"] == "shadow" and r["check"]["compared"] and r["check"]["ok"], r["check"]
        # a changed value is caught (names only)
        bad = {d: [dict(x, value=x["value"] * 1.01) if x["metric"] == "heart_rate" else x for x in rows]
               for d, rows in daily.items()}
        r = client.put("/v1/users/me/derived/app", json={**_upload(bad, "daily"), "bundle_version": "0.3.1-test"},
                       headers={"Authorization": f"Bearer {tok['a']}", **NEW_APP}).json()
        assert r["status"] == "mismatch" and r["check"]["fields_over"] == ["value"]
        # malformed rows are refused
        assert put({**_upload({DAYS[0].isoformat(): [{"role": "source", "metric": "x", "source": "ow", "agg": "sum",
                                                      "value": 1}]}, "daily")}).status_code == 422
        # primary: a passing check, then the rows are the user's (origin app), b's untouched
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "cohort", cohort="native")
        for kind, rows in (("daily", daily), ("sleep", sleep)):
            body = {**_upload(rows, kind), "bundle_version": "0.3.2-test"}
            assert put(body).json()["state_after"] == "primary"
            got = put(body).json()
            assert got["state"] == "primary" and got["stored"] > 0, got
        with conn_of(dsn) as c:
            n = c.execute("SELECT count(*) AS n FROM daily_metrics WHERE user_id = %s AND origin = 'app'", (uid,)).fetchone()
            assert n["n"] > 0
            s = c.execute("""SELECT count(*) AS n, bool_and(segments IS NOT NULL) AS seg FROM sleep_sessions
                             WHERE user_id = %s AND origin = 'app'""", (uid,)).fetchone()
            assert s["n"] > 0 and s["seg"]
            assert c.execute("SELECT count(*) AS n FROM daily_metrics WHERE user_id = %s", (ids["b"],)).fetchone()["n"] == 0
            mains = c.execute("""SELECT wake_date, count(*) FILTER (WHERE is_main) AS m FROM sleep_sessions WHERE user_id = %s
                                 GROUP BY wake_date""", (uid,)).fetchall()
            assert all(r["m"] == 1 for r in mains)
    finally:
        set_flag(dsn, "native_derive", "off")
        set_flag(dsn, P.COMPUTE_PRIMARY_FLAG, "off")


def test_pull_daily_carries_every_source(client, users, dsn, derived):
    ids, tok = users
    r = client.post("/v1/sync/pull", json={"cursors": {"daily": ""}, "limit": 2000},
                    headers={"Authorization": f"Bearer {tok['a']}", **NEW_APP})
    assert r.status_code == 200, r.text
    bodies = {x["date"]: x["body"] for x in r.json()["changes"] if x["stream"] == "daily" and x["body"]}
    d = DAYS[3].isoformat()
    basal = bodies[d]["sources"]["basal_energy_burned"]
    assert {x["source"] for x in basal} == {"app", "apple_export"}
    assert set(basal[0]) == {"source", "value", "unit", "agg", "n", "min", "max", "flags"}
    # b sees nothing of a's
    rb = client.post("/v1/sync/pull", json={"cursors": {"daily": ""}},
                     headers={"Authorization": f"Bearer {tok['b']}", **NEW_APP}).json()
    assert not [x for x in rb["changes"] if x["stream"] == "daily" and x["body"]]


def test_raw_history_has_15_minute_steps(dsn, derived):
    uid = derived
    d = DAYS[4]
    with conn_of(dsn) as c:
        got = history.bodies(c, uid, [d], TZ)
    steps = got[d]["inputs"]["steps"]
    t0 = got[d]["t0"]
    lo, hi = datetime.combine(d, datetime.min.time(), TZ).timestamp(), datetime.combine(
        d + timedelta(days=1), datetime.min.time(), TZ).timestamp()
    want = sorted((b["start"], b["value"]) for b in SRC["buckets"]
                  if b["type"] == history.STEP_TYPE and lo <= b["start"] < hi and b["value"])
    assert [(round(t0 + dt, 3), v) for dt, v in steps] == [(float(a), float(v)) for a, v in want]
    assert all(dt % 900 == 0 or (t0 + dt) % 900 == 0 for dt, _ in steps)
    assert history.size(got[d]) >= len(steps)


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None
