"""Training history: parsing, dedup, TRIMP and active energy of OW / iOS Shortcut workouts, merging with SparkyFitness, intensity grading, cross-midnight, authorization of the calendar / single-day endpoints.

Pure-function tests need no database; database tests use a separate test database body_test_training (configurable via BODY_TEST_DB_NAME_TRAINING), recreated on every run,
skipped if it cannot connect. Both OW and SparkyFitness are fakes."""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import shortcut as sc_imp
from body_engine.training import history, importer
from body_engine.training import shortcut_workouts as sw
from body_engine.training import params as P

SYD = ZoneInfo("Australia/Sydney")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_TRAINING", "body_test_training")


def ow_workout(wid, start, end, type_="strength_training", source="My Apple Watch", kcal=300.0, avg=110, mx=150,
               dist=None, device_type="watch"):
    xml = source == "apple_health_xml"
    s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    return {"id": wid, "type": type_, "start_time": start, "end_time": end, "zone_offset": None if xml else "+11:00",
            "duration_seconds": (e - s).total_seconds(), "calories_kcal": kcal * (4.184 if xml else 1),
            "distance_meters": dist, "avg_heart_rate_bpm": avg, "max_heart_rate_bpm": mx,
            "source": {"provider": "apple", "source": source,
                       "device": "Watch" if xml else "Watch7,9", "device_type": None if xml else device_type,
                       "device_name": None if xml else "Apple Watch Series 10 46mm (GPS)"}}


# ---------------------------------------------------------------- parsing
def test_parse_ow_xml_energy_and_local_date():
    r = importer.parse_ow(ow_workout("x", "2026-09-04T18:19:00+10:00", "2026-09-04T19:06:00+10:00",
                                     source="apple_health_xml", kcal=302.9), SYD)
    assert r["is_xml"] and r["total_kcal"] == 302.9 and r["device"] == "Watch"     # kJ -> kcal
    assert r["local_date"] == date(2026, 9, 4) and r["duration_min"] == 47 and r["name_zh"] == "力量训练"
    live = importer.parse_ow(ow_workout("y", "2026-09-04T19:06:00+10:00", "2026-09-04T19:30:00+10:00", "walking",
                                        kcal=244.7, dist=1692.5), SYD)
    assert not live["is_xml"] and live["total_kcal"] == 244.7 and live["distance_km"] == 1.692
    assert live["name_zh"] == "步行 / 爬坡"      # A treadmill incline walk is walking in HealthKit, no guessing


def test_cross_midnight_counts_on_start_day():
    # Starts 23:30, ends 00:40 the next day: counts as the start day; the same with different UTC offset notations (the app syncs UTC)
    r = importer.parse_ow(ow_workout("m", "2026-10-15T12:30:00+00:00", "2026-10-15T13:40:00+00:00"), SYD)
    assert r["local_date"] == date(2026, 10, 15) and r["start_at"].astimezone(SYD).hour == 23
    assert r["duration_min"] == 70


def test_implausible_duration_excluded():
    r = importer.parse_ow(ow_workout("z", "2024-07-13T19:07:47+10:00", "2024-07-16T21:11:53+10:00", "walking"), SYD)
    assert r["excluded"] == "implausible_duration" and "implausible_duration" in r["flags"]


@pytest.mark.parametrize("text,minutes", [("3720", 62), ("3725 s", 3725 / 60), ("1:02:30", 62.5), ("45:30", 45.5),
                                          ("62.5 min", 62.5), ("1.5 hr", 90), ("", None), (None, None)])
def test_duration_formats(text, minutes):
    got = sw.duration_min(text)
    assert (got is None and minutes is None) or abs(got - minutes) < 1e-9


def test_parse_shortcut_line_spec_format():
    # Spec: Workout|start|end|type|duration seconds|active energy kcal|avg heart rate|max heart rate|source
    p = "Workout|2026-10-16T19:00:00+11:00|2026-10-16T20:05:00+11:00|Traditional Strength Training|3720|500|121|158|My Apple Watch".split("|")
    r = sw.parse(p, SYD)
    assert r["type"] == "strength_training" and r["source"] == "shortcut" and r["duration_min"] == 62
    assert (r["active_kcal"], r["avg_hr"], r["max_hr"]) == (500.0, 121, 158) and r["local_date"] == date(2026, 10, 16)
    assert r["origin"] == "My Apple Watch" and r["external_id"] == "2026-10-16T08:00:00+00:00|strength_training"
    # empty fields, unit suffixes, thousands separators, no time zone, Chinese type names
    r2 = sw.parse("Workout|2026-10-17 07:00:00|2026-10-17 07:40:00|步行||2,092 kJ|||iPhone".split("|"), SYD)
    assert r2["type"] == "walking" and r2["duration_min"] == 40 and r2["active_kcal"] == 500.0
    assert r2["avg_hr"] is None and r2["max_hr"] is None and r2["start_at"].tzinfo is not None
    assert sw.parse("Heart Rate|2026-10-16T19:00:00+11:00|x|80|count/min|w".split("|"), SYD) is None
    assert sw.parse("Workout|2026-10-16T20:00:00+11:00|2026-10-16T19:00:00+11:00|Walking".split("|"), SYD) is None
    assert sw.shortcut_type("HKWorkoutActivityTypeBasketball") == "basketball"
    assert sw.shortcut_type("FunctionalStrengthTraining") == "strength_training"
    assert sw.shortcut_type("Mixed Cardio") == "cardio_training"
    assert sw._number("1 234 kcal") == 1234 and sw._number("12,5") == 12.5


# ---------------------------------------------------------------- TRIMP
def test_session_trimp_zones_cap_and_window():
    t0 = datetime(2026, 9, 16, 20, 0, tzinfo=SYD)
    end = t0 + timedelta(minutes=30)
    hr = [(t0, 100.0), (t0 + timedelta(minutes=10), 140.0), (t0 + timedelta(minutes=12), 175.0),
          (end + timedelta(minutes=1), 180.0)]            # the sample after the workout ended doesn't count
    res = importer.session_trimp(hr, t0, end, 190.0)
    # 100 bpm = 52.6% -> zone 1, represents at most 5 minutes; 140 = 73.7% -> zone 3 for 2 minutes; 175 = 92% -> zone 5, at most 5 minutes until the end
    assert res["zones"] == [5.0, 0.0, 2.0, 0.0, 5.0]
    assert res["trimp"] == 5 * 1 + 2 * 3 + 5 * 5 and res["covered_min"] == 12 and res["samples"] == 3


# ---------------------------------------------------------------- intensity
def _sess(**kw):
    base = {"trimp": None, "avg_hr": None, "duration_min": 45, "working_sets": None, "volume_kg": None,
            "hr_max": 190.5, "_cat": "strength"}
    return {**base, **kw}


@pytest.mark.parametrize("trimp,level", [(10, "light"), (49.9, "light"), (50, "moderate"), (119, "moderate"),
                                         (120, "hard"), (199, "hard"), (200, "max")])
def test_intensity_from_trimp(trimp, level):
    s = _sess(trimp=trimp)
    history.classify(s, None)
    assert s["intensity"] == level and s["intensity_basis"] == "trimp"


def test_intensity_fallbacks():
    s = _sess(avg_hr=140, duration_min=60, _cat="cardio")          # 73% HRmax -> zone 3: 60 x 3 x 0.94 = 169 -> hard
    history.classify(s, None)
    assert (s["intensity"], s["intensity_basis"], s["intensity_value"]) == ("hard", "avg_hr", 169.2)
    s = _sess(trimp=30, working_sets=20, volume_kg=7020)            # Heart rate underestimates strength training: use the volume tier
    history.classify(s, None)
    assert (s["intensity"], s["intensity_basis"]) == ("hard", "volume")
    s = _sess(trimp=130, working_sets=8, volume_kg=1344)            # use the heart rate tier when it is higher
    history.classify(s, None)
    assert (s["intensity"], s["intensity_basis"]) == ("hard", "trimp")
    s = _sess(duration_min=80, _cat="sport", hr_max=None)           # Duration only: ball sports 80 minutes -> hard
    history.classify(s, None)
    assert (s["intensity"], s["intensity_basis"]) == ("hard", "duration")
    s = _sess(duration_min=3, trimp=300)                            # an accidental 3-minute record -> light
    history.classify(s, None)
    assert s["intensity"] == "light"


# ---------------------------------------------------------------- merging
def _w(i, start_h, minutes, type_, day=date(2026, 9, 16)):
    s = datetime.combine(day, datetime.min.time(), SYD) + timedelta(hours=start_h)
    return {"id": i, "source": "ow", "origin": "My Apple Watch", "device": "Apple Watch", "type": type_,
            "name_zh": importer.type_name(type_), "local_date": day, "start_at": s, "end_at": s + timedelta(minutes=minutes),
            "duration_min": minutes, "active_kcal": 200.0, "total_kcal": 260.0, "distance_km": None, "avg_hr": 120.0,
            "max_hr": 150.0, "hr_zone_min": None, "trimp": 60.0, "hr_max_used": 190.5, "flags": {}}


def _sp(name, exercises, notes=None, sid="s1"):
    return {"id": sid, "date": "2026-09-16", "name": name, "notes": notes, "total_duration_minutes": 0,
            "exercises": [{"exercise_id": n, "name": n, "category": cat, "duration_minutes": dur,
                           "sets": [{"set": k + 1, "type": "Working Set", "reps": r, "weight_kg": w, "rir": None,
                                     "duration_s": None} for k, (r, w) in enumerate(sets)]}
                          for n, cat, dur, sets in exercises]}


def test_merge_strength_and_cardio_into_watch_sessions():
    leg = _sp("腿", [("爬坡（跑步机）", "cardio", 40, [(None, None)]), ("弓步蹲", "strength", 0, [(12, 5)] * 4),
                    ("腿屈伸", "strength", 0, [(12, 32)] * 4)], notes="练前吃了香蕉")
    out = history.merge_day([_w(1, 20.2, 44, "walking"), _w(2, 20.95, 22, "strength_training")], [leg], SYD)
    assert [s["name"] for s in out] == ["爬坡（跑步机）", "力量训练 · 腿"]
    walk, lift = out
    assert walk["source"] == lift["source"] == "ow+sparkyfitness" and walk["duration_min"] == 44
    assert [e["name"] for e in lift["exercises"]] == ["弓步蹲", "腿屈伸"] and lift["volume_kg"] == 4 * 12 * 5 + 4 * 12 * 32
    assert lift["working_sets"] == 8 and walk["volume_kg"] is None and lift["notes"] == "练前吃了香蕉"


def test_merge_leftovers_and_watch_only():
    # The watch has only one basketball session: SparkyFitness's strength record can't be matched -> a separate session; basketball as usual
    chest = _sp("胸", [("龙门架夹胸", "strength", 0, [(12, 14)] * 4)])
    out = history.merge_day([_w(1, 19, 35, "basketball")], [chest], SYD)
    assert [(s["source"], s["name"]) for s in out] == [("ow", "篮球"), ("sparkyfitness", "胸")]
    assert out[1]["start"] is None and out[1]["working_sets"] == 4
    # Two watch strength sessions: SparkyFitness merges into the longest one
    out = history.merge_day([_w(1, 10, 20, "strength_training"), _w(2, 18, 70, "strength_training")], [chest], SYD)
    assert out[1]["exercises"] and not out[0]["exercises"]
    assert history.merge_day([], [], SYD) == []


# ---------------------------------------------------------------- database
class FakeOW:
    """paginate: /events/workouts returns workouts, /timeseries filters samples by time and type."""

    def __init__(self, workouts, samples=()):
        self.workouts, self.samples = list(workouts), list(samples)

    def paginate(self, path, **params):
        if path.endswith("/events/workouts"):
            lo, hi = datetime.fromisoformat(params["start_date"]), datetime.fromisoformat(params["end_date"])
            return iter([w for w in self.workouts if datetime.fromisoformat(w["start_time"]) >= lo
                         and datetime.fromisoformat(w["end_time"]) < hi])
        if path.endswith("/timeseries"):
            lo, hi = datetime.fromisoformat(params["start_time"]), datetime.fromisoformat(params["end_time"])
            types = params.get("types")
            return iter([s for s in self.samples if lo <= datetime.fromisoformat(s["timestamp"]) <= hi
                         and (not types or s["type"] in types)])
        return iter([])


def sample(type_, ts, value, source="My Apple Watch", unit=None):
    return {"type": type_, "timestamp": ts.isoformat(), "value": value, "unit": unit or "bpm",
            "source": {"source": source, "device": "Watch7,9", "device_type": "watch"}}


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


OWNER = [None]   # the user with records in the fake SparkyFitness (A)


@pytest.fixture(scope="module")
def world(dsn):
    """A has OW; B is shared training by A (no scores / metrics); C has no shares at all."""
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, ow_user_id, birth_date) VALUES (%s, %s, '2001-06-15')
                                  RETURNING id""", (n, f"ow-{n}")).fetchone()["id"] for n in ("A", "B", "C")}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["A"], ids["B"], ["training"]))
    OWNER[0] = ids["A"]
    return ids, tok


@pytest.fixture
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


T0 = datetime(2026, 9, 16, 20, 13, tzinfo=SYD)


def a_world():
    walk_live = ow_workout("live-1", "2026-09-16T20:13:00+10:00", "2026-09-16T20:57:00+10:00", "walking", kcal=592.4)
    walk_xml = ow_workout("xml-1", "2026-09-16T20:13:00+10:00", "2026-09-16T20:57:00+10:00", "walking",
                          source="apple_health_xml", kcal=592.4)
    lift_xml = ow_workout("xml-2", "2026-09-16T20:57:00+10:00", "2026-09-16T21:19:00+10:00", kcal=270.6,
                          source="apple_health_xml")
    late = ow_workout("live-3", "2026-09-17T23:30:00+10:00", "2026-09-18T00:40:00+10:00", "basketball", kcal=700)
    bogus = ow_workout("xml-9", "2024-07-21T20:21:15+10:00", "2024-07-31T18:08:16+10:00", "walking",
                       source="apple_health_xml", kcal=100)
    # Heart rate of 140 during the incline period (73% HRmax -> zone 3), one sample every 5 seconds, active / basal energy samples complete (520 + 72 ~ 592)
    samples = [sample("heart_rate", T0 + timedelta(seconds=5 * k), 140) for k in range(44 * 12)]
    samples += [sample("active_energy", T0 + timedelta(minutes=k), 520 / 44, unit="kcal") for k in range(44)]
    samples += [sample("basal_energy", T0 + timedelta(minutes=k), 72 / 44, unit="kcal") for k in range(44)]
    return [walk_live, walk_xml, lift_xml, late, bogus], samples


def test_import_ow_dedupes_enriches_and_is_idempotent(conn, world):
    ids, _ = world
    a = ids["A"]
    workouts, samples = a_world()
    r = importer.import_ow(conn, FakeOW(workouts, samples), a)
    assert (r["fetched"], r["inserted"], r["duplicates"], r["kept"], r["excluded"]) == (5, 5, 1, 3, 1)
    rows = {w["external_id"]: w for w in conn.execute("SELECT * FROM workouts WHERE user_id = %s", (a,))}
    assert rows["xml-1"]["dup_of"] == rows["live-1"]["id"]           # live sync takes priority over the export.xml copy
    assert rows["xml-2"]["dup_of"] is None and rows["xml-2"]["total_kcal"] == 270.6
    assert rows["xml-9"]["excluded"] == "implausible_duration"
    walk = rows["live-1"]
    assert walk["hr_zone_min"] == [0, 0, 44.0, 0, 0] and walk["trimp"] == 132.0 and walk["hr_samples"] == 528
    assert walk["active_kcal"] == 520.4 and walk["flags"]["basal_kcal"] == 72.0    # total energy 592.4 split by sample proportion
    lift = rows["xml-2"]
    assert lift["trimp"] is None and "no_raw_hr" in lift["flags"] and "active_kcal_estimated" in lift["flags"]
    assert rows["live-3"]["local_date"] == date(2026, 9, 17)        # cross-midnight counts as the start day
    again = importer.import_ow(conn, FakeOW(workouts, samples), a)
    assert (again["inserted"], again["updated"], again["unchanged"], again["deleted"]) == (0, 0, 5, 0)
    assert conn.execute("SELECT count(*) AS n FROM workouts WHERE user_id = %s", (a,)).fetchone()["n"] == 5
    # One session was deleted in OW (within the fetched range) -> deleted here too; the live one is gone, so the XML copy is promoted to the kept one
    importer.import_ow(conn, FakeOW([w for w in workouts if w["id"] != "live-1"], samples), a, date(2026, 9, 1))
    rows = {w["external_id"]: w for w in conn.execute("SELECT * FROM workouts WHERE user_id = %s", (a,))}
    assert "live-1" not in rows and rows["xml-1"]["dup_of"] is None and rows["xml-1"]["trimp"] == 132.0
    importer.import_ow(conn, FakeOW(workouts, samples), a)


def test_import_shortcut_dedupes_against_ow(conn, world, tmp_path):
    ids, _ = world
    a = ids["A"]
    (tmp_path / "health_20261017.txt").write_text(
        "Steps|2026-10-17T10:00:00+11:00|2026-10-17T11:00:00+11:00|500|count|My Apple Watch\n"
        # the same session as OW's basketball (start / end both within 2 minutes)
        "Workout|2026-09-17T23:30:30+10:00|2026-09-18T00:40:00+10:00|Basketball|4170|650|150|181|My Apple Watch\n"
        "Workout|2026-10-16T19:00:00+11:00|2026-10-16T20:00:00+11:00|Traditional Strength Training|3600|400|118|152|My Apple Watch\n",
        encoding="utf-8")
    res = sc_imp.run(conn, a, tmp_path)
    assert res["workouts"]["workouts"] == 2 and res["workouts"]["inserted"] == 2
    rows = {w["type"]: w for w in conn.execute("SELECT * FROM workouts WHERE user_id = %s AND source = 'shortcut'", (a,))}
    assert rows["basketball"]["dup_of"] is not None and rows["strength_training"]["dup_of"] is None
    st = rows["strength_training"]
    assert (st["active_kcal"], st["avg_hr"], st["max_hr"], st["duration_min"]) == (400, 118, 152, 60)
    again = sc_imp.run(conn, a, tmp_path)
    assert again["workouts"]["inserted"] == 0 and again["workouts"]["updated"] == 0


# ---------------------------------------------------------------- API
@pytest.fixture(scope="module")
def client(dsn, world):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def _seed_day_data(conn, a):
    for d, metric, v in ((date(2026, 9, 16), "active_energy_burned", 1051), (date(2026, 9, 16), "apple_exercise_time", 66),
                         (date(2026, 9, 16), "step_count", 8127), (date(2026, 9, 10), "active_energy_burned", 300),
                         (date(2026, 9, 17), "active_energy_burned", 100)):   # daily data was only partly synced
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                        VALUES (%s, %s, %s, %s, 'x', 'sum', 1, 'ow') ON CONFLICT DO NOTHING""", (a, d, metric, v))
    for d, strain, ml, rec in ((date(2026, 9, 16), 15.5, 1776, 47), (date(2026, 9, 17), 9.0, None, 57)):
        conn.execute("""INSERT INTO daily_scores (user_id, date, strain, muscle_load, recovery, hr_max, algo_version, flags)
                        VALUES (%s, %s, %s, %s, %s, 190.5, 't', %s) ON CONFLICT DO NOTHING""",
                     (a, d, strain, ml, rec, Jsonb({})))


def fake_sparky(monkeypatch):
    leg = _sp("腿", [("爬坡（跑步机）", "cardio", 40, [(None, None)]), ("upright", "strength", 0, [(12, 7)] * 4),
                    ("腿屈伸", "strength", 0, [(12, 32)] * 4)])
    leg["exercises"][1]["name"] = "杠铃提拉 / 直立划船（禁用）"
    monkeypatch.setattr(sparky_client, "client_for_user", lambda conn, uid: uid)   # the "client" is the user ID
    monkeypatch.setattr(sparky_client, "training_log",
                        lambda uid, s, e: [x for x in [leg] if uid == OWNER[0] and s <= date.fromisoformat(x["date"]) <= e])
    monkeypatch.setattr(sparky_client, "exercises", lambda c: [
        {"id": "upright", "name": "杠铃提拉 / 直立划船（禁用）", "description": "【禁用】永久不做", "category": "strength"}])


def test_calendar_month(client, conn, world, monkeypatch):
    ids, tok = world
    _seed_day_data(conn, ids["A"])
    fake_sparky(monkeypatch)
    r = client.get("/v1/users/me/training/calendar?month=2026-09", headers={"Authorization": f"Bearer {tok['A']}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["month"] == "2026-09" and len(body["days"]) == 30 and body["hidden"] == []
    days = {d["date"]: d for d in body["days"]}
    d16 = days["2026-09-16"]
    assert d16["sessions_count"] == 2 and d16["labels"] == ["爬坡（跑步机）", "力量训练 · 腿"]
    assert d16["types"] == ["walking", "strength_training"] and d16["intensity"] == "hard"
    assert (d16["active_kcal"], d16["exercise_min"], d16["strain"], d16["muscle_load"]) == (1051, 66, 15.5, 1776)
    assert d16["duration_min"] == 44 + 22 and d16["workout_kcal"] is not None
    assert days["2026-09-17"]["labels"] == ["篮球"]                     # basketball starting at 23:30 counts as the 17th
    assert "daily_incomplete" in days["2026-09-17"]["flags"] and d16["flags"] == {}
    d10 = days["2026-09-10"]
    assert d10["sessions_count"] == 0 and d10["intensity"] == "rest" and d10["active_kcal"] == 300
    s = body["summary"]
    assert s["training_days"] == 2 and s["longest_streak"] == 2 and s["rest_days"] == 28
    assert s["by_type"] == {"步行 / 爬坡": 1, "力量训练": 1, "篮球": 1}
    bad = client.get("/v1/users/me/training/calendar?month=2026-13", headers={"Authorization": f"Bearer {tok['A']}"})
    assert bad.status_code == 422
    rng = client.get("/v1/users/me/training/calendar?start=2026-09-15&end=2026-09-18",
                     headers={"Authorization": f"Bearer {tok['A']}"}).json()
    assert rng["month"] is None and [d["date"] for d in rng["days"]][0] == "2026-09-15" and len(rng["days"]) == 4


def test_day_detail_statuses(client, conn, world, monkeypatch):
    ids, tok = world
    _seed_day_data(conn, ids["A"])
    fake_sparky(monkeypatch)
    r = client.get("/v1/users/me/training/day/2026-09-16", headers={"Authorization": f"Bearer {tok['A']}"})
    assert r.status_code == 200, r.text
    body = r.json()
    walk, lift = body["sessions"]
    assert walk["name"] == "爬坡（跑步机）" and walk["start"] == "2026-09-16T20:13+10:00" and walk["trimp"] == 132.0
    assert walk["intensity"] == "hard" and walk["hr_zone_min"] == [0, 0, 44.0, 0, 0]
    statuses = {e["name"]: e["status"] for e in lift["exercises"]}
    assert statuses == {"杠铃提拉 / 直立划船（禁用）": "禁用", "腿屈伸": "正常"}
    assert lift["exercises"][1]["sets"][0] == {"set": 1, "type": "Working Set", "reps": 12, "weight_kg": 32, "rir": None,
                                               "rpe": None, "duration_s": None, "distance_km": None, "notes": None}
    assert lift["volume_kg"] == 4 * 12 * 7 + 4 * 12 * 32 and lift["intensity_basis"] in ("volume", "avg_hr")
    d = body["daily"]
    assert (d["active_kcal"], d["exercise_min"], d["steps"], d["strain"], d["recovery_next_day"]) == (1051, 66, 8127, 15.5, 57)


def test_auth_and_shares(client, world, monkeypatch):
    ids, tok = world
    fake_sparky(monkeypatch)
    a = ids["A"]
    assert client.get(f"/v1/users/{a}/training/calendar").status_code == 401
    assert client.get(f"/v1/users/{a}/training/calendar", headers={"Authorization": f"Bearer {tok['C']}"}).status_code == 403
    assert client.get(f"/v1/users/{a}/training/day/2026-09-16",
                      headers={"Authorization": f"Bearer {tok['C']}"}).status_code == 403
    r = client.get(f"/v1/users/{a}/training/calendar?month=2026-09", headers={"Authorization": f"Bearer {tok['B']}"})
    assert r.status_code == 200
    body = r.json()
    d16 = next(d for d in body["days"] if d["date"] == "2026-09-16")
    # B only has training: workouts as usual, scores and daily activity are null
    assert body["hidden"] == ["scores", "metrics"] and d16["sessions_count"] == 2
    assert d16["strain"] is None and d16["active_kcal"] is None
    # one's own calendar: B has no workouts
    mine = client.get("/v1/users/me/training/calendar?month=2026-09", headers={"Authorization": f"Bearer {tok['B']}"}).json()
    assert mine["summary"]["training_days"] == 0


def test_assistant_tools(conn, world, monkeypatch):
    ids, _ = world
    fake_sparky(monkeypatch)
    a, b, c = ids["A"], ids["B"], ids["C"]
    assert {"get_training_calendar", "get_training_day"} <= set(TOOLS_BY_NAME)
    cal = run_tool(conn, a, "test", "get_training_calendar", {"user": "me", "month": None, "start": "2026-09-15",
                                                              "end": "2026-09-18"})
    assert len(cal["days"]) == 4 and cal["summary"]["training_days"] == 2
    day = run_tool(conn, b, "test", "get_training_day", {"user": str(a), "date": "2026-09-16"})
    assert day["hidden"] == ["scores", "metrics"] and day["daily"]["strain"] is None and len(day["sessions"]) == 2
    with pytest.raises(PermissionError):
        run_tool(conn, c, "test", "get_training_day", {"user": str(a), "date": "2026-09-16"})
    audit = conn.execute("SELECT args FROM assistant_audit WHERE tool = 'get_training_day' ORDER BY id DESC LIMIT 1").fetchone()
    assert "_viewer_id" not in audit["args"]            # the audit records the raw arguments
