"""Apple sync engine, server step S3 (docs/apple-sync-engine.md §5.4, §6): the read layer over the engine's uploads
(rawdata/samples.py) used by the scores, stress, workout heart rate and the Today page's stage chart in the sync mode
`app`, and the two sync paths side by side per day (rawdata/side_by_side.py, `sync-shadow-day`, GET
/v1/users/{user}/sync/engine/days).

The synthetic data set of tests/test_apple_sync_derive.py (every number a made-up example value, 示例) goes to three users:
- O: only Open Wearables (the reference, sync mode ow; the flag is off for O);
- S: the same OW data + the same samples uploaded by the app's engine, sync mode shadow;
- A: the same as S in the app mode (reserved for step S5; rawdata/engine.mode patched).
Checks: A without any OW client reads from the engine what O reads from OW (raw samples, sleep stages, workout windows,
workout heart rate and energy, the stage chart); O and S never touch the engine's samples (the ow and shadow modes read
exactly what they read before); the side-by-side view and its endpoint (flag-gated, own data only). Separate test database
BODY_TEST_DB_NAME_S3 (default body_test_apple_sync_s3), recreated each run; skipped without Postgres.
"""
import os
from datetime import datetime, timedelta, timezone

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
from body_engine.rawdata import derive as DV, engine as EN, samples as HS, side_by_side as SBS
from body_engine.rollout import flags
from body_engine.scores import inputs as SI
from body_engine.training import importer as TI
from body_engine.web import views

TZ, D1, D2, at = S2.TZ, S2.D1, S2.D2, S2.at
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_S3", "body_test_apple_sync_s3")


def test_ow_codes_cover_what_the_readers_ask():
    for code in (*SI.ow_types("sdnn"), *stress.STRESS_TYPES, "active_energy", "basal_energy"):
        assert code in HS.HK_BY_OW, code
    assert HS.HK_BY_OW["body_temperature"].hk.endswith("AppleSleepingWristTemperature")
    assert HS.HK_BY_OW["oxygen_saturation"].factor == 100.0, "stored 0–100 like the OW import"
    assert not HS.HK_BY_OW["active_energy"].raw, "energy only as statistics buckets"


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


@pytest.fixture(scope="module")
def world(dsn):
    fake = S2.FakeOW()
    with conn_of(dsn) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, role, consent_at, consent_version, timezone, birth_date,
                                                     sex, height_cm, ow_user_id)
                                  VALUES (%s, 'member', '2026-10-01', '2026-10-22.1', 'Australia/Sydney', '1990-01-01',
                                          'male', 180, %s) RETURNING id""", (n, f"ow-{n}")).fetchone()["id"]
               for n in ("O", "S", "A")}
        for n in ("S", "A"):
            flags.cohort_add(conn, ids[n], "beta", actor="test")
        flags.set_flag(conn, EN.FLAG, "cohort", cohort="beta", actor="test")
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
    orig = EN.mode
    mp = pytest.MonkeyPatch()
    mp.setattr(EN, "mode", lambda conn, uid: "app" if uid == ids["A"] else orig(conn, uid))
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    client = TestClient(api.app, base_url="https://testserver")
    with conn_of(dsn) as conn:
        for n in ids:
            ow.run(conn, fake, ids[n], D1, D2)
    for n in ("S", "A"):
        for b in S2.app_batches():
            r = client.post("/v1/ingest/healthkit", json=b, headers={"Authorization": f"Bearer {tok[n]}"})
            assert r.status_code == 200, r.text
    with conn_of(dsn) as conn:
        DV.run(conn)
    yield {"ids": ids, "tok": tok, "client": client, "fake": fake}
    api.pool.close()
    mp.undo()


def window():
    return at(D1, 0), at(D2 + timedelta(days=1), 0)


def test_only_the_app_mode_reads_the_engine(world, dsn):
    ids = world["ids"]
    with conn_of(dsn) as conn:
        assert [HS.reads_app(conn, ids[n]) for n in ("O", "S", "A")] == [False, False, True]


def test_samples_equal_ows(world, dsn):
    """The engine's heart rate / HRV / steps of A are OW's of O (same times, values, priorities)."""
    ids, fake = world["ids"], world["fake"]
    lo, hi = window()
    with conn_of(dsn) as conn:
        got = HS.samples(conn, ids["A"], ["heart_rate", "heart_rate_variability_sdnn", "steps", "oxygen_saturation"], lo, hi)
        assert HS.samples(conn, ids["O"], ["heart_rate"], lo, hi) == {}, "O uploaded nothing"
    for code, xs in got.items():
        want = sorted((ow.parse_sample(r)[2] for r in fake.raw if r["type"] == code), key=lambda s: s.ts)
        assert [(s.ts, s.value, s.priority) for s in xs] == [(s.ts, pytest.approx(s.value), s.priority) for s in want], code
    assert {s.priority for s in got["steps"]} == {0, 2}, "watch and phone steps keep their priorities"


def test_energy_buckets_prorated(world, dsn):
    ids = world["ids"]
    with conn_of(dsn) as conn:
        whole = HS.samples(conn, ids["A"], ["basal_energy"], at(D1, 10), at(D1, 11))["basal_energy"]
        half = HS.samples(conn, ids["A"], ["basal_energy"], at(D1, 10, 7), at(D1, 10, 22))["basal_energy"]
    assert sum(s.value for s in whole) == pytest.approx(70.0), "one example sample an hour, in its bucket"
    assert [(s.ts, s.value) for s in half] == [(at(D1, 10, 7), pytest.approx(70.0 * 8 / 15))]


def test_scores_read_the_engine_in_the_app_mode(world, dsn):
    """A with no OW at all gets O's inputs; S (shadow) reads only OW, as before."""
    ids, fake = world["ids"], world["fake"]
    with conn_of(dsn) as conn:
        o = SI.load(conn, fake, ids["O"], D1, D2, None, "sdnn")
        a = SI.load(conn, None, ids["A"], D1, D2, None, "sdnn")
        s = SI.load(conn, None, ids["S"], D1, D2, None, "sdnn")
    for k in ("hr", "hrv", "resp", "spo2"):
        assert [(t, pytest.approx(v)) for t, v in getattr(o, k)] == getattr(a, k), k
        assert getattr(o, k) and not getattr(s, k), k
    assert a.temp == o.temp == []
    assert a.segments == o.segments and len(a.segments) == 11, "same stages (the phone's in-bed session dropped)"
    assert s.segments == []


def test_scores_app_first_ow_fills(world, dsn):
    """With OW too, A's samples are the engine's (no doubles) and an OW-only night comes in."""
    ids, fake = world["ids"], world["fake"]
    extra = {"source": S2.OW_SRC[S2.WATCH], "sleep_stage_intervals": [
        {"stage": "light", "start_time": at(D1, 1).astimezone(timezone.utc).isoformat(),
         "end_time": at(D1, 2).astimezone(timezone.utc).isoformat()}]}
    fake.sleep.append(extra)
    try:
        with conn_of(dsn) as conn:
            a = SI.load(conn, fake, ids["A"], D1, D2, None, "sdnn")
            o = SI.load(conn, fake, ids["O"], D1, D2, None, "sdnn")
    finally:
        fake.sleep.remove(extra)
    assert len(a.hr) == len(o.hr) == 144
    assert a.segments == o.segments and (at(D1, 1), at(D1, 2), "core") in [(x, y, z) for x, y, z in a.segments]


def test_stress_reads_the_engine_in_the_app_mode(world, dsn):
    ids, fake = world["ids"], world["fake"]
    lo, hi = at(D1, 0) - timedelta(days=stress.BASELINE_DAYS), at(D2 + timedelta(days=1), 0)
    ow_samples = stress._ow_samples(fake, "ow-O", stress.STRESS_TYPES, lo, hi)
    ow_workouts = stress._workouts(fake, "ow-O", D1 - timedelta(days=stress.BASELINE_DAYS), D2, TZ)
    with conn_of(dsn) as conn:
        samples, workouts = stress._with_app(conn, ids["A"], TZ, lo, hi, {}, [])
        assert samples == ow_samples and workouts == ow_workouts
        # OW only fills a day the app has none of
        early = (at(D1, 12) - timedelta(days=3), 77.0)
        both, _ = stress._with_app(conn, ids["A"], TZ, lo, hi, {"heart_rate": [*ow_samples["heart_rate"], early]}, ow_workouts)
        assert both["heart_rate"] == sorted([*ow_samples["heart_rate"], early])
        assert stress.compute(conn, None, ids["A"], D1, D2) == stress.compute(conn, fake, ids["O"], D1, D2)


def _hr_fields(dsn, uid, source):
    return [(r["start_at"], r["trimp"], r["hr_zone_min"], r["hr_samples"], (r["flags"] or {}).get("hr_recovery_bpm"),
             (r["flags"] or {}).get("no_raw_hr")) for r in S2.rows(
        dsn, "SELECT * FROM workouts WHERE user_id = %s AND source = %s ORDER BY start_at", uid, source)]


def test_workout_heart_rate_from_the_engine(world, dsn):
    ids, fake = world["ids"], world["fake"]
    s_ow = {r["id"] for r in S2.rows(dsn, "SELECT id FROM workouts WHERE user_id = %s AND source = 'ow'", ids["S"])}
    with conn_of(dsn) as conn:
        TI.enrich_pending(conn, None, ids["A"])
        TI.enrich_pending(conn, None, ids["S"], s_ow)
        shadow = _hr_fields(dsn, ids["S"], "ow")
        TI.enrich_pending(conn, fake, ids["S"], s_ow)   # back to what OW gives
    want, got = _hr_fields(dsn, ids["O"], "ow"), _hr_fields(dsn, ids["A"], "app")
    assert len(got) == 3 and got == want and all(g[3] for g in got)
    assert [r["active_kcal"] for r in S2.rows(dsn, "SELECT active_kcal FROM workouts WHERE user_id = %s AND source = 'app' "
                                                    "ORDER BY start_at", ids["A"])] == [w[5] for w in S2.WORKOUTS]
    assert all(s[5] for s in shadow), "shadow: no OW client, so no heart rate (the engine is not read)"


def test_ow_workout_energy_in_the_app_mode(world, dsn):
    """An OW workout re-enriched in the app mode splits its energy from the engine's buckets; with no energy anywhere it
    keeps the active energy computed before."""
    ids = world["ids"]
    o_ow = S2.rows(dsn, "SELECT * FROM workouts WHERE user_id = %s AND source = 'ow' ORDER BY start_at", ids["O"])
    a_ow = S2.rows(dsn, "SELECT * FROM workouts WHERE user_id = %s AND source = 'ow' ORDER BY start_at", ids["A"])
    with conn_of(dsn) as conn:
        for w in a_ow:
            TI.enrich(conn, None, ids["A"], w, None, TZ, app=True)
        conn.execute("UPDATE workouts SET active_kcal = 123.0, flags = flags || '{\"basal_kcal\": 1.0}' WHERE id = %s",
                     (a_ow[0]["id"],))
        conn.execute("DELETE FROM hk_stats WHERE user_id = %s", (ids["A"],))
        try:
            w = conn.execute("SELECT * FROM workouts WHERE id = %s", (a_ow[0]["id"],)).fetchone()
            flags_ = TI.enrich(conn, None, ids["A"], w, None, TZ, app=True)
            kept = conn.execute("SELECT active_kcal FROM workouts WHERE id = %s", (a_ow[0]["id"],)).fetchone()["active_kcal"]
        finally:
            for b in S2.app_batches()[1:2]:   # put the statistics buckets back for the other tests
                r = world["client"].post("/v1/ingest/healthkit", json=b,
                                         headers={"Authorization": f"Bearer {world['tok']['A']}"})
                assert r.status_code == 200, r.text
            DV.run(conn, ids["A"])
    assert kept == 123.0 and flags_["basal_kcal"] == 1.0
    assert flags_.get("active_kcal_estimated") == (w["flags"] or {}).get("active_kcal_estimated"), "the old flags stay"
    got = S2.rows(dsn, "SELECT active_kcal FROM workouts WHERE user_id = %s AND source = 'ow' ORDER BY start_at", ids["A"])
    for g, w, o in zip(got[1:], a_ow[1:], o_ow[1:]):
        assert g["active_kcal"] == pytest.approx(o["active_kcal"], rel=0.05), (w["start_at"], g, o["active_kcal"])


def test_stage_chart(world, dsn, monkeypatch):
    ids, fake = world["ids"], world["fake"]
    start, end = at(D1, 23, 10), at(S2.D2, 7, 5)
    want = views.hypnogram(ow.sleep_segments(fake.sleep, TZ), start, end, TZ)
    monkeypatch.setattr(views, "ow_segments", lambda conn, uid, tz, a, b: ow.sleep_segments(fake.sleep, tz))
    with conn_of(dsn) as conn:
        a = views.night_stages(conn, ids["A"], TZ, start, end)
        s = views.night_stages(conn, ids["S"], TZ, start, end)
    assert a[1] == "app" and a[0] == want and len(want) > 5
    assert s[1] == "ow" and s[0] == want, "shadow: OW first, as before"


def test_side_by_side(world, dsn):
    ids = world["ids"]
    with conn_of(dsn) as conn:
        res = SBS.days(conn, ids["S"], D1, D2)
        with pytest.raises(ValueError):
            SBS.days(conn, ids["S"], D1, D1 + timedelta(days=SBS.MAX_DAYS))
    assert res["mode"] == "shadow" and [d["date"] for d in res["days"]] == [D1, D2]
    d1 = {r["metric"]: r for r in res["days"][0]["daily"]}
    hr = d1["heart_rate"]
    assert hr["ow"]["n"] == hr["app"]["n"] == 72 and abs(hr["rel"]) <= 0.001 and hr["chosen"] == "ow"
    assert d1["walking_step_length"]["ow"] is None and d1["walking_step_length"]["diff"] is None
    assert abs(d1["step_count"]["rel"]) <= 0.01
    sleep = res["days"][1]["sleep"]
    assert len(sleep["ow"]) == len(sleep["app"]) == 2
    for o, a in zip(sleep["ow"], sleep["app"]):
        assert abs((o["start_at"] - a["start_at"]).total_seconds()) <= 60 and o["is_main"] == a["is_main"]
        assert all(abs(o[k] - a[k]) <= 1 for k in SBS.SLEEP_KEYS)
    w = res["days"][0]["workouts"]
    assert len(w["ow"]) == len(w["app"]) == 1 and w["app"][0]["excluded"] == "shadow" and w["ow"][0]["excluded"] is None
    text = SBS.markdown(res)
    assert f"## {D1}" in text and "heart_rate" in text and "影子" in text


def test_side_by_side_endpoint(world):
    ids, tok, client = world["ids"], world["tok"], world["client"]
    url = "/v1/users/{}/sync/engine/days"

    def get(who, user, **params):
        return client.get(url.format(user), params={"date": D1.isoformat(), **params},
                          headers={"Authorization": f"Bearer {tok[who]}"})
    r = get("S", "me", end=D2.isoformat())
    assert r.status_code == 200 and len(r.json()["days"]) == 2, r.text
    assert get("O", "me").status_code == 404, "flag off for O: not there"
    assert get("S", ids["O"]).status_code == 403, "own data only"
    assert get("S", "me", end=(D1 + timedelta(days=40)).isoformat()).status_code == 422
    assert get("S", "me", end=(D1 - timedelta(days=1)).isoformat()).status_code == 422


def test_derive_nights_unchanged_by_the_shared_reader(world, dsn):
    """rawdata/derive.nights now reads through samples.stage_rows: the app's nights are still OW's."""
    ids = world["ids"]
    with conn_of(dsn) as conn:
        app = DV.nights(conn, ids["S"], {D2}, TZ)
    want = S2.sleep(dsn, ids["O"], "ow")
    assert [(n.wake_date, n.asleep_min) for n in app] == [(w["wake_date"], w["asleep_min"]) for w in want]
    assert all(isinstance(n.start, datetime) for n in app)
