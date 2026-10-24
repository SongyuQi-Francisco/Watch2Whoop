"""Every workout type credits at least one muscle on the muscle load map, and `muscle-coverage-check` reports the ones that do not.

The table test walks every type the parsers can produce (Apple HKWorkoutActivityType through the Open Wearables mapping, the Shortcut's
labels, SparkyFitness / Hevy strength types, our own display-name list) and the unknown ones: none may come out empty."""
import os
from datetime import date, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.training import muscle_coverage as MC, muscle_load as ML, params as P, sport_muscles as SM

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_MUSCLE_COVERAGE", "body_test_muscle_coverage")


def test_every_known_type_credits_muscles():
    types = SM.all_known_types()
    assert len(types) >= 70
    assert set(P.HK_WORKOUT_ACTIVITIES) and {P.hk_workout_type(a) for a in P.HK_WORKOUT_ACTIVITIES} <= types   # all of HealthKit
    for t in sorted(types):
        load = ML.sport_load({"type": t, "min": 45.0})
        assert load["muscles"], t
        assert all(m in ML.CD.MUSCLE_ZH and sets > 0 for m, (sets, _) in load["muscles"].items()), t
        assert any(prim for _, prim in load["muscles"].values()), t   # at least one primary muscle
        assert MC.check_type(t) is None, t                            # and the check agrees


def test_every_hk_activity_name_and_shortcut_label_resolves():
    for _, sdk in P.HK_WORKOUT_ACTIVITIES.values():   # the OW SDK names, also when OW would call them something unknown
        assert SM.resolve(sdk)["primary"], sdk
    for label in P.SHORTCUT_TYPES:                    # "Traditional Strength Training", "跑步" ...
        assert SM.resolve(label)["primary"], label
    for t in (None, "", "  ", "unknown", "other", "generic", "Some Future Activity", "新运动"):
        r = SM.resolve(t)
        assert r["primary"] and r["secondary"] and not r["known"] and r["resistance"] == SM.FALLBACK[2], t


def test_fallback_is_whole_body_low_and_never_zero():
    load = ML.sport_load({"type": "never_heard_of_it", "min": 60.0, "trimp": 150})
    assert load["estimated"] and not load["known"]
    assert set(load["muscles"]) == set(SM.FALLBACK[0]) | set(SM.FALLBACK[1])
    cyc = ML.sport_load({"type": "cycling", "min": 60.0, "trimp": 150})
    assert max(v[0] for v in load["muscles"].values()) < max(v[0] for v in cyc["muscles"].values())   # low next to a real mapping
    tiny = ML.sport_load({"type": "never_heard_of_it", "min": 0.5})
    assert all(v[0] > 0 for v in tiny["muscles"].values())


def test_mapping_table_is_well_formed():
    for t, (prim, sec, res) in SM.SPORT_MUSCLES.items():
        assert prim and set(prim) | set(sec) <= set(ML.CD.MUSCLE_ZH), t
        assert 0 < res <= 1, t
    assert set(SM.FALLBACK[0]) | set(SM.FALLBACK[1]) <= set(ML.CD.MUSCLE_ZH)
    assert SM.STRENGTH_LIKE <= set(SM.SPORT_MUSCLES)
    # the cases that were reported: cycling, a watch-only strength session, swimming
    assert {"quadriceps", "glutes"} <= set(SM.resolve("cycling")["primary"]) and "calves" in SM.resolve("cycling")["secondary"]
    assert {"lats", "shoulders"} <= set(SM.resolve("swimming")["primary"])
    assert len(ML.sport_load({"type": "strength_training", "min": 45.0})["muscles"]) >= 12


def test_week_of_unlogged_workouts_reaches_every_muscle_group():
    today = date(2026, 10, 25)
    mon = ML.week_start(today)
    week = [{"date": mon + timedelta(days=i), "type": t, "min": m, "trimp": tr} for i, (t, m, tr) in enumerate(
        [("cycling", 25, 40), ("cycling", 20, 30), ("strength_training", 43, None), ("swimming", 50, 60), ("strength_training", 43, None)])]
    sp = ML.aggregate_sports(week, today)
    for m in ("quadriceps", "glutes", "calves", "lats", "shoulders", "chest", "abdominals"):
        assert sp["sets"][m]["week"] > 0, m


@pytest.fixture(scope="module")
def conn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    with psycopg.connect(Settings(db_name=TEST_DB).dsn, row_factory=dict_row) as c:
        migrate(c)
        yield c


def _add(conn, uid, ext, type_, minutes, days_ago=0, **kw):
    conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min, dup_of, excluded)
                    VALUES (%s, 'ow', %s, %s, %s, current_date - %s, now(), now(), %s, %s, %s)""",
                 (uid, ext, type_, type_, days_ago, minutes, kw.get("dup_of"), kw.get("excluded")))


def test_scan_reports_uncovered_types_only(conn):
    uid = conn.execute("INSERT INTO users (display_name, role) VALUES ('A', 'owner') RETURNING id").fetchone()["id"]
    for i, t in enumerate(["cycling", "swimming", "strength_training", "generic", "other", "yoga", "pilates"]):
        _add(conn, uid, f"ok{i}", t, 30, days_ago=i)
    ok = MC.scan(conn, 28)
    assert ok["ok"] and ok["uncovered"] == [] and ok["workouts"] == 7 and "都映射到了肌群" in MC.render(ok)

    _add(conn, uid, "x1", "quidditch", 40, days_ago=1)
    _add(conn, uid, "x2", "quidditch", 20, days_ago=2)
    _add(conn, uid, "old", "curling_on_mars", 20, days_ago=40)                      # outside the window
    _add(conn, uid, "dup", "hovercraft", 20, days_ago=1, excluded="user_hidden")    # excluded workouts are not counted
    bad = MC.scan(conn, 28)
    assert not bad["ok"] and [(b["type"], b["reason"], b["workouts"], b["minutes"], b["user_ids"]) for b in bad["uncovered"]] == [
        ("quidditch", "unmapped", 2, 60, [uid])]
    text = MC.render(bad)
    assert text.startswith("✗") and "quidditch" in text and "sport_muscles.py" in text
    conn.rollback()
