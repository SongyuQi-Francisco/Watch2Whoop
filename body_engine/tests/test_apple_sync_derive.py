"""Apple sync engine, server step S2 (docs/apple-sync-engine.md §3.2.3–3.2.5, rawdata/derive.py): daily values, sleep
and workouts derived from the engine's uploads, the dirty-day queue (hk-derive-run) and the sync modes (D4).

One synthetic data set (every number is a made-up example value, 示例) is fed to three users:
- O: only Open Wearables (the OW import, ow.run, through a fake OW client) - the reference, sync mode ow;
- S: the same OW data plus the same samples uploaded by the app's engine, sync mode shadow;
- A: the same as S, but in the app mode (reserved for step S5; rawdata/engine.mode patched).
Checks: A's derived values equal O's OW values by the comparison rules of the design (§4: non-cumulative ≤ 0.1 %,
cumulative ≤ 1 %, sleep times / stage minutes ≤ 1 minute, workouts by type / duration / energy / heart rate); S sees
exactly what O sees (daily values, sleep, workouts, scores: nothing changes outside the app mode); the queue is cleared,
idempotent, locked per user and consent-gated. Separate test database BODY_TEST_DB_NAME (default
body_test_apple_sync_s2), recreated each run; skipped without Postgres.
"""
import json
import os
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import common, ow
from body_engine.rawdata import derive as DV, engine as EN, hk_types as T, params as RP
from body_engine.rollout import flags
from body_engine.scores import engine as scores_engine
from body_engine.sync import data_status, refresh
from body_engine.training import importer as TI, params as TP

TZ = ZoneInfo("Australia/Sydney")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_apple_sync_s2")
Q, C = "HKQuantityTypeIdentifier", "HKCategoryTypeIdentifier"
SLEEP, WORKOUT = C + "SleepAnalysis", "HKWorkoutTypeIdentifier"
D1, D2 = date(2026, 10, 20), date(2026, 10, 21)
DAYS = (D1, D2)


def at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), TZ)


# ---------------------------------------------------------------- the synthetic data (example values)
SOURCES = [
    {"name": "Example Watch", "bundle": "com.example.watch", "product": "Watch0,0", "device_type": "watch",
     "device_name": "Apple Watch", "device_model": "Watch", "manufacturer": "Apple Inc.", "os_version": "27.0"},
    {"name": "Example Phone", "bundle": "com.example.phone", "product": "iPhone0,0", "device_type": "phone",
     "device_name": "iPhone", "device_model": "iPhone", "manufacturer": "Apple Inc.", "os_version": "27.0"},
]
OW_SRC = [{"provider": "apple", "source": s["name"], "device": s["device_model"], "device_type": s["device_type"],
           "device_name": s["device_name"]} for s in SOURCES]
WATCH, PHONE = 0, 1


def quantity_samples() -> list[dict]:
    """{hk, start, end, value (the app's unit), ow: (OW code, value in OW's unit) or None, src}"""
    out = []

    def add(hk, start, value, ow_code=None, ow_value=None, src=WATCH, minutes=1):
        out.append({"hk": Q + hk, "start": start, "end": start + timedelta(minutes=minutes), "value": value,
                    "ow": (ow_code, value if ow_value is None else ow_value) if ow_code else None, "src": src})

    for d in DAYS:
        for i in range(72):                                     # heart rate every 20 minutes
            add("HeartRate", at(d, 0) + timedelta(minutes=20 * i), 60.0 + (i * 7) % 35, "heart_rate")
        for i, h in enumerate((2, 3, 4)):
            add("HeartRateVariabilitySDNN", at(d, h), 40.0 + 3 * i + d.day % 3, "heart_rate_variability_sdnn")
        add("RestingHeartRate", at(d, 8), 54.0 + d.day % 2, "resting_heart_rate")
        for h, v in ((2, 14.5), (4, 15.0)):
            add("RespiratoryRate", at(d, h, 30), v, "respiratory_rate")
        for h, v in ((1, 0.95), (3, 0.97), (5, 0.98)):        # 0–1 in HealthKit, 0–100 stored
            add("OxygenSaturation", at(d, h, 10), v, "oxygen_saturation")
        for h in (9, 17):                                       # km/h in the app, m/s in OW
            add("WalkingSpeed", at(d, h, 5), 4.5, "walking_speed", 1.25)
        add("WalkingStepLength", at(d, 9, 6), 70.0 + d.day % 2)   # only the app reads it here
        for h in range(7, 22):                                  # cumulative: one sample per 15-minute bucket
            add("StepCount", at(d, h, 2), 100.0 + (h * 13) % 50, "steps")
            add("DistanceWalkingRunning", at(d, h, 3), 0.08, "distance_walking_running", 80.0)
        add("StepCount", at(d, 22, 30), 40.0, "steps", src=PHONE)   # a walk only the phone recorded: added
        for h in range(24):
            add("BasalEnergyBurned", at(d, h), 70.0, "basal_energy")
        for i in range(25):
            add("ActiveEnergyBurned", at(d, 8, 5) + timedelta(minutes=30 * i), 20.0, "active_energy")
    add("BodyMass", at(D1, 7), 80.5, "weight")
    return out


# sleep: the night D1 -> D2 from the watch (stages), the phone's "in bed" around it, a nap on D2 (HealthKit values)
SLEEP_STAGES = [(PHONE, 0, at(D1, 22, 50), at(D2, 7, 10))] + [(WATCH, v, a, b) for v, a, b in (
    (3, at(D1, 23, 10), at(D2, 0, 30)), (4, at(D2, 0, 30), at(D2, 1, 20)), (3, at(D2, 1, 20), at(D2, 2, 40)),
    (5, at(D2, 2, 40), at(D2, 3, 30)), (2, at(D2, 3, 30), at(D2, 3, 36)), (3, at(D2, 3, 36), at(D2, 5, 0)),
    (4, at(D2, 5, 0), at(D2, 5, 30)), (5, at(D2, 5, 30), at(D2, 6, 20)), (3, at(D2, 6, 20), at(D2, 6, 58)),
    (2, at(D2, 6, 58), at(D2, 7, 5)), (3, at(D2, 14, 0), at(D2, 14, 35)))]

# workouts: (activity, name, start, end, duration s, active, basal, distance m, avg hr, max hr, OW type)
WORKOUTS = [
    (50, "traditionalStrengthTraining", at(D1, 18, 0), at(D1, 18, 47), 2700, 210.0, 60.0, None, 118.0, 150.0, "strength_training"),
    (52, "walking", at(D2, 7, 30), at(D2, 8, 10), 2400, 150.0, 50.0, 3200.0, 105.0, 128.0, "walking"),
    (20, "functionalStrengthTraining", at(D2, 18, 0), at(D2, 18, 30), 1800, 120.0, 40.0, None, 112.0, 140.0, "strength_training"),
]
CUMULATIVE = {Q + "StepCount", Q + "DistanceWalkingRunning", Q + "BasalEnergyBurned", Q + "ActiveEnergyBurned"}


def ow_sleep_sessions() -> list[dict]:
    """What OW's sleep state machine makes of the same samples: one session per night for the user (the phone's in-bed
    sample opens it, so it is the session's source), stages cleaned (with core / deep / REM, in bed is dropped)."""
    night = [s for s in SLEEP_STAGES if s[0] == WATCH and s[2] < at(D2, 12)]
    nap = [s for s in SLEEP_STAGES if s[2] >= at(D2, 12)]
    name = {2: "awake", 3: "light", 4: "deep", 5: "rem"}
    return [{"source": OW_SRC[src], "sleep_stage_intervals": [
        {"stage": name[v], "start_time": a.astimezone(timezone.utc).isoformat(), "end_time": b.astimezone(timezone.utc).isoformat()}
        for _, v, a, b in stages]} for src, stages in ((PHONE, night), (WATCH, nap))]


class FakeOW:
    """The synthetic data as Open Wearables' API returns it for SDK-synced samples."""

    def __init__(self):
        self.raw = [{"timestamp": s["start"].astimezone(timezone.utc).isoformat(), "type": s["ow"][0], "value": s["ow"][1],
                     "unit": "x", "source": OW_SRC[s["src"]]} for s in quantity_samples() if s["ow"]]
        self.sleep = ow_sleep_sessions()
        self.workouts = [{"id": f"ow-{i}", "type": w[10], "name": w[1], "start_time": w[2].isoformat(),
                          "end_time": w[3].isoformat(), "duration_seconds": w[4], "calories_kcal": w[5] + w[6],
                          "distance_meters": w[7], "avg_heart_rate_bpm": w[8], "max_heart_rate_bpm": w[9],
                          "zone_offset": "+11:00", "source": OW_SRC[WATCH]} for i, w in enumerate(WORKOUTS)]

    def get(self, path, **params):
        return {"data": self.raw[:1]}

    def paginate(self, path, **params):
        if path.endswith("/timeseries"):
            lo, hi = datetime.fromisoformat(params["start_time"]), datetime.fromisoformat(params["end_time"])
            types = params.get("types")
            return iter([r for r in self.raw if lo <= datetime.fromisoformat(r["timestamp"]) < hi
                         and (types is None or r["type"] in types)])
        if path.endswith("/events/sleep"):
            return iter(self.sleep)
        if path.endswith("/events/workouts"):
            return iter(self.workouts)
        return iter([])


def app_batches() -> list[dict]:
    """The same data as the app's sync engine uploads it (rawdata/engine.py fields)."""
    groups: dict[tuple, dict] = {}
    buckets: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for s in quantity_samples():
        t = T.ENGINE_BY_HK[s["hk"]]
        if t.raw:
            g = groups.setdefault((s["hk"], s["src"]), {"type": s["hk"], "unit": t.unit, "uuid": [], "start": [], "end": [],
                                                         "value": [], "src": []})
            for k, v in (("uuid", str(uuid.uuid4())), ("start", s["start"].timestamp()), ("end", s["end"].timestamp()),
                         ("value", s["value"]), ("src", s["src"])):
                g[k].append(v)
        if t.stats:   # Apple's statistics buckets of the same samples (no overlap here: every sample counts)
            ts = int(s["start"].timestamp())
            buckets[s["hk"]][ts - ts % EN.BUCKET_S] += s["value"]
    lo, hi = int(at(D1, 0).timestamp()), int(at(D2 + timedelta(days=1), 0).timestamp())
    stats = [{"type": hk, "unit": T.ENGINE_BY_HK[hk].unit, "start": lo,
              "values": [b.get(x, 0.0) for x in range(lo, hi, EN.BUCKET_S)]} for hk, b in buckets.items()]
    cats = {"type": SLEEP, "unit": "", "uuid": [str(uuid.uuid4()) for _ in SLEEP_STAGES],
            "start": [a.timestamp() for _, _, a, _ in SLEEP_STAGES], "end": [b.timestamp() for _, _, _, b in SLEEP_STAGES],
            "value": [v for _, v, _, _ in SLEEP_STAGES], "src": [s for s, _, _, _ in SLEEP_STAGES],
            "tz": ["Australia/Sydney"] * len(SLEEP_STAGES)}
    workouts = [{"uuid": str(uuid.uuid4()), "activity": w[0], "activity_name": w[1], "start": w[2].timestamp(),
                 "end": w[3].timestamp(), "duration_s": w[4],
                 "stats": {k: v for k, v in (("active_kcal", w[5]), ("basal_kcal", w[6]), ("distance_m", w[7]),
                                             ("avg_hr", w[8]), ("max_hr", w[9])) if v is not None},
                 "events": [], "meta": {"tz": "Australia/Sydney"}, "src": WATCH} for w in WORKOUTS]

    def batch(**kw):
        return {"batch_id": str(uuid.uuid4()), "device": "iPhone", "app_version": "1.0.8", "phase": "backfill",
                "tz": "Australia/Sydney", "sources": SOURCES, **kw}
    return [batch(samples=list(groups.values())), batch(stats=stats), batch(categories=[cats], workouts=workouts)]


# ---------------------------------------------------------------- pure functions
def test_workout_types_are_ows():
    assert all(sdk in TP.OW_SDK_WORKOUT_TYPES for _, sdk in TP.HK_WORKOUT_ACTIVITIES.values())
    assert len({case for case, _ in TP.HK_WORKOUT_ACTIVITIES.values()}) == len(TP.HK_WORKOUT_ACTIVITIES)
    cases = {50: "strength_training", 20: "strength_training", 59: "strength_training", 63: "cardio_training",
             37: "running", 52: "walking", 6: "basketball", 46: "swimming", 13: "cycling", 24: "hiking",
             44: "stair_climbing", 57: "yoga", 3000: "generic", 999: "generic"}
    for activity, want in cases.items():
        assert TP.hk_workout_type(activity) == want, activity
    assert TP.hk_workout_type(None, "yoga") == "yoga" and TP.hk_workout_type(None, "madeUp") == "generic"


def test_sleep_stage_clean_up_and_sessions():
    t = at(D1, 22)
    m = lambda n: t + timedelta(minutes=n)   # noqa: E731
    # detailed stages: the in-bed / asleep wrappers go, an overlap is cut
    assert DV.clean_stages([("in_bed", m(0), m(90)), ("sleeping", m(0), m(90)), ("light", m(10), m(40)),
                            ("deep", m(30), m(60))]) == [("light", m(10), m(40)), ("deep", m(40), m(60))]
    # only in bed (an iPhone's bedtime): counts as asleep
    assert DV.clean_stages([("in_bed", m(0), m(60)), ("awake", m(60), m(65))]) == \
        [("sleeping", m(0), m(60)), ("awake", m(60), m(65))]
    src = {"source_id": 1, "source": "Example Watch", "name": "Example Watch", "device_type": "watch", "device_name": "W"}
    rows = [{**src, "start_at": a, "end_at": b, "value": v} for v, a, b in (
        (2, m(0), m(5)),                              # awake cannot open a session (OW's start states)
        (3, m(10), m(70)), (3, m(10), m(70)),         # the same sample twice counts once
        (4, m(70), m(100)),
        (3, m(100 + 121), m(100 + 150)))]             # more than 2 hours later: a new session
    sessions = DV.ow_sessions(rows)
    assert [[(s["stage"], s["start_time"]) for s in x["sleep_stage_intervals"]] for x in sessions] == [
        [("light", m(10).isoformat()), ("deep", m(70).isoformat())], [("light", m(221).isoformat())]]
    assert sessions[0]["source"] == {"source": "Example Watch", "device_type": "watch", "device_name": "W"}


def test_app_sleep_same_as_ow_without_database():
    """Same stage samples: OW-shaped sessions -> the OW import's functions give the same nights as the app path."""
    rows = [{"source_id": src + 1, "source": SOURCES[src]["name"], "name": SOURCES[src]["name"],
             "device_type": SOURCES[src]["device_type"], "device_name": SOURCES[src]["device_name"],
             "start_at": a, "end_at": b, "value": v} for src, v, a, b in SLEEP_STAGES]
    app = common.sessionize(ow.sleep_segments(DV.ow_sessions(rows), TZ))
    want = common.sessionize(ow.sleep_segments(ow_sleep_sessions(), TZ))
    assert [(n.wake_date, n.start, n.end, dict(n.minutes)) for n in app] == \
           [(n.wake_date, n.start, n.end, dict(n.minutes)) for n in want]
    assert [(n.wake_date, n.asleep_min) for n in app] == [(D2, 462), (D2, 35)]
    assert app[0].device == "Apple Watch", "the phone's overlapping in-bed session is dropped (watch first)"


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


def conn_of(dsn):
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


@pytest.fixture(scope="module")
def world(dsn):
    """O: OW only; S: OW + the app engine (shadow); A: OW + the app engine in the app mode; N: no consent."""
    fake = FakeOW()
    with conn_of(dsn) as conn:
        def add(name, consent=True):
            return conn.execute("""INSERT INTO users (display_name, role, consent_at, consent_version, timezone, birth_date,
                                                      sex, height_cm, ow_user_id)
                                   VALUES (%s, 'member', %s, %s, 'Australia/Sydney', '1990-01-01', 'male', 180, %s)
                                   RETURNING id""", (name, "2026-10-01" if consent else None,
                                                     "2026-10-22.1" if consent else None, f"ow-{name}")).fetchone()["id"]
        ids = {n: add(n, n != "N") for n in ("O", "S", "A", "N")}
        for n in ("S", "A"):
            flags.cohort_add(conn, ids[n], "beta", actor="test")
        flags.set_flag(conn, EN.FLAG, "cohort", cohort="beta", actor="test")
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ("S", "A")}
    orig = EN.mode
    mp = pytest.MonkeyPatch()
    mp.setattr(EN, "mode", lambda conn, uid: "app" if uid == ids["A"] else orig(conn, uid))
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    client = TestClient(api.app, base_url="https://testserver")
    with conn_of(dsn) as conn:
        for n in ("O", "S", "A"):
            ow.run(conn, fake, ids[n], D1, D2)
    for n in ("S", "A"):
        for b in app_batches():
            r = client.post("/v1/ingest/healthkit", json=b, headers={"Authorization": f"Bearer {tok[n]}"})
            assert r.status_code == 200, r.text
            assert r.json()["dropped"] == 0
    with conn_of(dsn) as conn:
        queued = {r["user_id"] for r in conn.execute("SELECT DISTINCT user_id FROM hk_dirty_days")}
        assert queued == {ids["S"], ids["A"]}
        res = {r["user_id"]: r for r in DV.run(conn)}
    yield {"ids": ids, "tok": tok, "client": client, "fake": fake, "derived": res}
    api.pool.close()
    mp.undo()


def rows(dsn, sql, *params):
    with conn_of(dsn) as c:
        return c.execute(sql, params).fetchall()


DAILY = "SELECT date, metric, value, unit, agg, n, min_value, max_value, source, flags FROM {} WHERE user_id = %s {} ORDER BY date, metric, source"


def daily(dsn, uid, table="daily_metrics", source=None):
    return rows(dsn, DAILY.format(table, "AND source = %s" if source else ""), uid, *([source] if source else []))


def sleep(dsn, uid, source=None):
    return rows(dsn, """SELECT wake_date, start_at, end_at, asleep_min, core_min, deep_min, rem_min, awake_min,
                               unspecified_min, is_main, source FROM sleep_sessions WHERE user_id = %s
                        AND (%s::text IS NULL OR source = %s) ORDER BY wake_date, start_at""", uid, source, source)


def workouts(dsn, uid, where="TRUE"):
    return rows(dsn, f"""SELECT source, type, local_date, start_at, end_at, duration_min, total_kcal, active_kcal, distance_km,
                                avg_hr, max_hr, excluded, dup_of IS NULL AS kept, trimp, hr_zone_min
                         FROM workouts WHERE user_id = %s AND {where} ORDER BY start_at, source""", uid)


def test_queue_is_cleared(world, dsn):
    ids, res = world["ids"], world["derived"]
    assert res[ids["S"]]["mode"] == "shadow" and res[ids["A"]]["mode"] == "app"
    for n in ("S", "A"):
        assert res[ids[n]]["days"] == 2 and res[ids[n]]["workouts"] == 3, res[ids[n]]
    assert rows(dsn, "SELECT * FROM hk_dirty_days") == []


def test_app_values_equal_ow_values(world, dsn):
    """§4 of the design: the app path's daily values are the OW import's (A's 'app' rows vs O's 'ow' rows)."""
    ids = world["ids"]
    want = {(r["date"], r["metric"]): r for r in daily(dsn, ids["O"], "daily_metric_sources", "ow")}
    got = {(r["date"], r["metric"]): r for r in daily(dsn, ids["A"], "daily_metric_sources", "app")}
    assert set(want) == set(got) - {(d, "walking_step_length") for d in DAYS}
    assert len(want) == 2 * 11 - 1   # 11 OW types a day, body mass only on D1
    for k, w in want.items():
        g = got[k]
        assert (g["unit"], g["agg"]) == (w["unit"], w["agg"]), k
        if w["agg"] == "sum":   # cumulative: within 1 %
            assert abs(g["value"] - w["value"]) <= 0.01 * w["value"], (k, g["value"], w["value"])
        else:                   # non-cumulative: within 0.1 %, same count, min, max
            assert abs(g["value"] - w["value"]) <= 0.001 * abs(w["value"]), (k, g["value"], w["value"])
            assert g["n"] == w["n"] and g["min_value"] == pytest.approx(w["min_value"]), k
            assert g["max_value"] == pytest.approx(w["max_value"]), k
    steps = got[(D1, "step_count")]["value"]
    assert steps == sum(100.0 + (h * 13) % 50 for h in range(7, 22)) + 40.0, "the phone-only walk is added"


def test_app_sleep_equals_ow_sleep(world, dsn):
    ids = world["ids"]
    want, got = sleep(dsn, ids["O"], "ow"), sleep(dsn, ids["A"], "app")
    assert len(want) == len(got) == 2 and sleep(dsn, ids["A"], "ow") == [], "app above ow: one source per day"
    for w, g in zip(want, got):
        assert g["wake_date"] == w["wake_date"] and g["is_main"] == w["is_main"]
        assert abs((g["start_at"] - w["start_at"]).total_seconds()) <= 60 and abs((g["end_at"] - w["end_at"]).total_seconds()) <= 60
        for k in ("asleep_min", "core_min", "deep_min", "rem_min", "awake_min", "unspecified_min"):
            assert abs(g[k] - w[k]) <= 1, k


def test_app_workouts_equal_ow_workouts(world, dsn):
    ids = world["ids"]
    want, got = workouts(dsn, ids["O"], "source = 'ow'"), workouts(dsn, ids["A"], "source = 'app'")
    assert len(want) == len(got) == 3
    for w, g in zip(want, got):
        assert (g["type"], g["local_date"], g["start_at"], g["end_at"], g["distance_km"]) == \
               (w["type"], w["local_date"], w["start_at"], w["end_at"], w["distance_km"])
        assert abs(g["duration_min"] - w["duration_min"]) <= 1
        assert abs(g["total_kcal"] - w["total_kcal"]) <= 0.02 * w["total_kcal"]
        assert abs(g["avg_hr"] - w["avg_hr"]) <= 1 and abs(g["max_hr"] - w["max_hr"]) <= 1
        assert g["excluded"] is None and g["kept"], "app mode: the app copy is the one kept"
    assert [g["active_kcal"] for g in got] == [w[5] for w in WORKOUTS], "active energy straight from the statistics"
    assert all(not w["kept"] for w in workouts(dsn, ids["A"], "source = 'ow'")), "A's OW copies are its duplicates"


def test_app_mode_values_take_part(world, dsn):
    ids = world["ids"]
    chosen = {(r["date"], r["metric"]): r["source"] for r in daily(dsn, ids["A"])}
    assert chosen[(D1, "heart_rate")] == "app" and chosen[(D1, "walking_step_length")] == "app"
    assert chosen[(D1, "step_count")] == "app", "the same cumulative value: the higher priority wins the tie"


def test_shadow_changes_nothing(world, dsn):
    """Default behaviour: outside the app mode the engine's values are stored but nothing anyone sees changes."""
    ids = world["ids"]
    o, s = ids["O"], ids["S"]
    assert daily(dsn, s) == daily(dsn, o)
    assert sleep(dsn, s) == sleep(dsn, o)
    assert workouts(dsn, s, "excluded IS NULL") == workouts(dsn, o)
    assert {w["excluded"] for w in workouts(dsn, s, "source = 'app'")} == {"shadow"}
    assert len(daily(dsn, s, "daily_metric_sources", "app")) == 2 * 11 + 1, "stored for the comparison (S4)"
    # the scores read the same inputs; the data status lists the same sources
    with conn_of(dsn) as conn:
        assert data_status._metric_rows(conn, s, D1, D1) == data_status._metric_rows(conn, o, D1, D1)
        assert "app" in data_status._metric_rows(conn, ids["A"], D1, D1)["heart_rate"]["sources"]
        def scores(uid):
            origin = scores_engine.scoring_origin(conn, uid)
            inp = scores_engine.load(conn, world["fake"], uid, origin - timedelta(days=400), D2, None, hrv_kind="sdnn")
            return json.dumps(scores_engine.score_days(inp, origin, D2, D2), default=str, sort_keys=True)
        assert scores(s) == scores(o)


def test_idempotent(world, dsn):
    ids = world["ids"]
    snap = lambda uid: (daily(dsn, uid, "daily_metric_sources"), daily(dsn, uid), sleep(dsn, uid), workouts(dsn, uid))  # noqa: E731
    before = {n: snap(ids[n]) for n in ("S", "A")}
    with conn_of(dsn) as conn:
        for n in ("S", "A"):
            EN.mark_dirty(conn, ids[n], {(hk, d) for d in DAYS for hk in (SLEEP, WORKOUT, Q + "HeartRate", Q + "StepCount")})
        assert {r["user_id"]: r["days"] for r in DV.run(conn)} == {ids["S"]: 2, ids["A"]: 2}
    after = {n: snap(ids[n]) for n in ("S", "A")}
    for n in ("S", "A"):
        for i, name in enumerate(("sources", "daily", "sleep", "workouts")):
            b, a = before[n][i], after[n][i]
            assert len(a) == len(b), (n, name)
            for x, y in zip(b, a):
                assert x == y, (n, name, {k: (x[k], y[k]) for k in x if x[k] != y[k]})


def test_queue_lock_consent_and_refresh(world, dsn):
    ids = world["ids"]
    with conn_of(dsn) as conn, conn_of(dsn) as other:
        EN.mark_dirty(conn, ids["S"], {(Q + "HeartRate", D1)})
        other.execute("SELECT pg_advisory_lock(%s, %s)", (DV.LOCK_NS, ids["S"]))
        assert DV.run_user(conn, ids["S"])["busy"] is True
        assert len(rows(dsn, "SELECT * FROM hk_dirty_days WHERE user_id = %s", ids["S"])) == 1
        other.execute("SELECT pg_advisory_unlock(%s, %s)", (DV.LOCK_NS, ids["S"]))
        # sync-refresh-run derives the queued days before the recompute
        refresh.queue_scores(conn, ids["S"], D2)
        out = refresh.run_queued(conn, ids["S"], scorer=lambda *a: {"rows": 0})
        assert [o["user_id"] for o in out] == [ids["S"]]
        assert rows(dsn, "SELECT * FROM hk_dirty_days WHERE user_id = %s", ids["S"]) == []
        # no consent: left queued; nothing queued: nothing done
        EN.mark_dirty(conn, ids["N"], {(Q + "HeartRate", D1)})
        assert DV.run(conn) == [{"user_id": ids["N"], "skipped": "consent"}]
        assert DV.run(conn, ids["O"]) == []


def test_deleted_samples_and_back_to_shadow(world, dsn):
    """A deleted workout / a day's samples gone (A, app mode), then A back in the shadow mode: the chosen values are
    the OW ones again and the app-only metric has no chosen value."""
    ids, client, tok = world["ids"], world["client"], world["tok"]
    a = ids["A"]
    gone = rows(dsn, "SELECT hk_uuid::text AS u FROM hk_samples WHERE user_id = %s AND type = %s AND local_date = %s",
                a, WORKOUT, D2)
    hr = rows(dsn, "SELECT hk_uuid::text AS u FROM hk_samples WHERE user_id = %s AND type = %s AND local_date = %s",
              a, Q + "WalkingStepLength", D2)
    r = client.post("/v1/ingest/healthkit", json={"batch_id": str(uuid.uuid4()), "deleted": [x["u"] for x in gone + hr]},
                    headers={"Authorization": f"Bearer {tok['A']}"})
    assert r.status_code == 200 and r.json()["deleted"] == 3
    with conn_of(dsn) as conn:
        DV.run(conn, a)
    left = workouts(dsn, a)
    assert [(w["source"], w["kept"]) for w in left if w["local_date"] == D2] == [("ow", True), ("ow", True)]
    assert not any(r["metric"] == "walking_step_length" and r["date"] == D2 for r in daily(dsn, a))
    with conn_of(dsn) as conn:
        common.resolve_daily(conn, a, DAYS, mode="shadow")
    o = {(r["date"], r["metric"]): (r["value"], r["source"]) for r in daily(dsn, ids["O"])}
    got = {(r["date"], r["metric"]): (r["value"], r["source"]) for r in daily(dsn, a)}
    assert got == o
