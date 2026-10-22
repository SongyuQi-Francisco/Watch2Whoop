"""Strength training in body age (issue #210): imported (Hevy) sessions are counted next to the watch and SparkyFitness
records (per day the largest), incomplete records are labelled instead of read as "did not train", the data status page
counts the records by source, and the AI briefing asks the user to fill them in. Every number here is a made-up example.

Uses its own test database BODY_TEST_DB_NAME_HS_STRENGTH (default body_test_healthspan_strength), dropped and recreated on
every run; skipped if it cannot connect.
"""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from body_engine.assistant import briefing
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.insights import healthspan_params as H, healthspan_store as HSS
from body_engine.records import defaults
from body_engine.sync import data_status
from body_engine.training import hevy_import, importer

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_HS_STRENGTH", "body_test_healthspan_strength")
TZ = ZoneInfo("Australia/Sydney")
TODAY = date(2026, 10, 20)
ORIGIN = TODAY - timedelta(days=59)   # started wearing the watch 60 days ago
HEAD = ("title,start_time,end_time,description,exercise_title,superset_id,exercise_notes,set_index,set_type,"
        "weight_kg,reps,distance_km,duration_seconds,rpe")


def _hevy_csv(workouts: list[tuple[str, date, int, int]]) -> bytes:
    """(title, day, start hour, minutes) -> a Hevy export with two working sets of a squat per workout."""
    rows = [HEAD]
    for title, d, hour, minutes in workouts:
        start = datetime(d.year, d.month, d.day, hour, tzinfo=TZ)
        end = start + timedelta(minutes=minutes)
        fmt = lambda t: f"{t.day} {t:%b %Y}, {t:%H:%M}"
        for i in range(2):
            rows.append(f'"{title}","{fmt(start)}","{fmt(end)}","","Squat (Barbell)",,"",{i},"normal",60,8,,,')
    return ("\n".join(rows) + "\n").encode()


def _watch(conn, uid: int, ext: str, d: date, hour: int, minutes: int, kind: str) -> None:
    start = datetime(d.year, d.month, d.day, hour, tzinfo=TZ)
    importer.upsert(conn, uid, [{**importer._base_row(TZ, start, start + timedelta(minutes=minutes)),
                                 "source": "ow", "external_id": ext, "origin": "watch", "is_xml": False,
                                 "device": "Apple Watch", "type": kind, "name_zh": "示例", "active_kcal": None,
                                 "total_kcal": None, "distance_km": None, "avg_hr": 110.0, "max_hr": 150.0,
                                 "zone_offset": None}])
    importer.refresh_duplicates(conn, uid)


class FakeSparky:
    """SparkyFitness with the given strength sessions [(day, minutes or None, sets)] and no weigh-ins."""

    def __init__(self, sessions=()):
        self.sessions = [{"type": "preset", "id": f"s{i}", "entry_date": d.isoformat(), "name": "示例",
                          "total_duration_minutes": m,
                          "exercises": [{"exercise_snapshot": {"name": "Squat", "category": "strength"},
                                         "sets": [{"set_number": k + 1, "reps": 8, "weight": 60} for k in range(n)]}]}
                         for i, (d, m, n) in enumerate(sessions)]

    def get(self, path, **params):
        if path == "/v2/exercise-entries/history":
            return {"sessions": sorted(self.sessions, key=lambda s: s["entry_date"], reverse=True),
                    "pagination": {"hasMore": False}}
        return []


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


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def people(dsn):
    """hevy: only Hevy imports (one of them a duplicate of a watch 'fitness_equipment' copy, which body age did not count);
    dup: the same day in all three sources; none: no strength record at all; journal / plan: evidence the records miss
    sessions."""
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        ids = {}
        for name in ("hevy", "dup", "none", "journal", "plan"):
            uid = conn.execute("""INSERT INTO users (display_name, role, timezone, birth_date, sex, height_cm)
                                  VALUES (%s, 'member', 'Australia/Sydney', '1990-01-01', 'male', 180) RETURNING id""",
                               (name,)).fetchone()["id"]
            conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                            VALUES (%s, %s, 'resting_heart_rate', 60, 'count/min', 'avg', 1, 'ow')""", (uid, ORIGIN))
            ids[name] = uid
        d1, d2 = TODAY - timedelta(days=10), TODAY - timedelta(days=3)
        _watch(conn, ids["hevy"], "ow-eq", d2, 7, 50, "fitness_equipment")
        hevy_import.run(conn, ids["hevy"], _hevy_csv([("Push", d1, 18, 60), ("Legs", d2, 7, 52)]))
        dd = TODAY - timedelta(days=5)
        _watch(conn, ids["dup"], "ow-st", dd, 7, 50, "strength_training")
        hevy_import.run(conn, ids["dup"], _hevy_csv([("Legs", dd, 7, 52)]))
        defaults.init_journal_items(conn, ids["journal"])
        for d in (TODAY - timedelta(days=2), TODAY - timedelta(days=9)):
            conn.execute("INSERT INTO journal_entries (user_id, date, key, value, source) VALUES (%s, %s, 'training_legs', 1, 'ai')",
                         (ids["journal"], d))
        conn.execute("""INSERT INTO training_plans (user_id, name, split_type, days_per_week, start_date, days)
                        VALUES (%s, '示例计划', '4_day_upper_lower', 4, %s, %s)""",
                     (ids["plan"], TODAY - timedelta(days=50), Jsonb([])))
        _watch(conn, ids["plan"], "ow-p1", TODAY - timedelta(days=6), 7, 45, "strength_training")
    return ids


def _strength(conn, uid: int, sparky=None) -> tuple[HSS.Data, dict | None]:
    data = HSS.Data(conn, uid, ORIGIN - timedelta(days=1), TODAY, sparky or FakeSparky())
    snap = HSS.snapshot(data, TODAY)
    return data, next((c for c in snap["contributors"] if c["key"] == "strength"), None)


def test_hevy_only_user_counts_every_imported_session(conn, people):
    uid = people["hevy"]
    d1, d2 = TODAY - timedelta(days=10), TODAY - timedelta(days=3)
    # the second Hevy workout is a duplicate of the watch's fitness_equipment copy, a type body age does not count
    dup = conn.execute("SELECT dup_of FROM workouts WHERE user_id = %s AND source = 'hevy' AND local_date = %s",
                       (uid, d2)).fetchone()
    assert dup["dup_of"] is not None
    data, item = _strength(conn, uid)
    assert data.strength[d1] == 60 and data.strength[d2] == 52   # before #210: d2 was 0
    assert item is not None and item["value"] > 0 and "coverage" not in item


def test_same_day_in_three_sources_counts_once(conn, people):
    uid = people["dup"]
    dd = TODAY - timedelta(days=5)
    data, _ = _strength(conn, uid, FakeSparky([(dd, 40, 6)]))
    assert data.strength[dd] == 52   # max(watch 50, SparkyFitness 40, Hevy 52), not their sum
    # SparkyFitness without a duration: the set estimate, still the larger value only
    data, _ = _strength(conn, uid, FakeSparky([(dd, None, 30)]))
    assert data.strength[dd] == H.STRENGTH_MIN_PER_SET * 30


def test_no_records_behaves_as_before(conn, people):
    data, item = _strength(conn, people["none"])
    assert dict(data.strength) == {} and data.strength_from == ORIGIN
    assert item["value"] == 0 and item["years"] == 0 and "coverage" not in item
    assert item["tip"] == H.TIPS["strength"][0]


def test_users_are_isolated(conn, people):
    for name in ("none", "journal", "plan"):
        data, _ = _strength(conn, people[name])
        assert not any(m for d, m in data.strength.items() if d != TODAY - timedelta(days=6))
    assert HSS.imported_strength_days(conn, people["none"], ORIGIN, TODAY) == {}
    src = HSS.strength_sources(conn, people["none"], TODAY - timedelta(days=27), TODAY, FakeSparky())
    assert src == {"days": 0, "sessions": {"watch": 0, "sparky": 0, "hevy": 0}, "sparky": "connected"}


def test_journal_evidence_labels_the_item(conn, people):
    _, item = _strength(conn, people["journal"])
    assert item["coverage"] == "possibly_incomplete" and "日记" in item["coverage_reason"]
    assert item["tip"] == H.TIPS["strength_incomplete"]
    assert item["value"] == 0 and item["years"] == 0   # the value is not changed, only labelled
    # a record on one of the two days leaves one unexplained day: below the minimum, no label
    _, item = _strength(conn, people["journal"], FakeSparky([(TODAY - timedelta(days=2), 45, 5)]))
    assert "coverage" not in item


def test_plan_evidence_labels_the_item(conn, people):
    _, item = _strength(conn, people["plan"])
    assert item["coverage"] == "possibly_incomplete" and "训练计划" in item["coverage_reason"]
    # enough recorded days for the plan (half of 4 days x 4 weeks): no label
    days = [(TODAY - timedelta(days=k), 45, 5) for k in range(0, 24, 3)]
    _, item = _strength(conn, people["plan"], FakeSparky(days))
    assert "coverage" not in item
    # a plan that started inside the window says nothing about the weeks before it
    data = HSS.Data(conn, people["plan"], ORIGIN - timedelta(days=1), TODAY, FakeSparky())
    assert HSS.strength_coverage(data, TODAY - timedelta(days=40)) is None


def test_data_status_counts_sources(conn, people):
    src = HSS.strength_sources(conn, people["hevy"], TODAY - timedelta(days=27), TODAY,
                               FakeSparky([(TODAY - timedelta(days=1), 30, 4), (TODAY - timedelta(days=40), 30, 4)]))
    assert src == {"days": 3, "sessions": {"watch": 0, "sparky": 1, "hevy": 2}, "sparky": "connected"}
    src = HSS.strength_sources(conn, people["dup"], TODAY - timedelta(days=27), TODAY, FakeSparky())
    assert src["days"] == 1 and src["sessions"] == {"watch": 1, "sparky": 0, "hevy": 1}
    # not linked to SparkyFitness: said so, the other sources still counted
    res = data_status.collect(conn, people["hevy"], TODAY)
    assert res["strength"] == {"weeks": H.STRENGTH_COVERAGE_WEEKS, "days": 2,
                               "sessions": {"watch": 0, "sparky": None, "hevy": 2}, "sparky": "not_linked"}


def test_briefing_asks_to_fill_in_records(conn, people):
    for name in ("journal", "none"):
        uid = people[name]
        HSS.write(conn, uid, HSS.compute(conn, uid, today=TODAY, sparky=FakeSparky()))
    briefing.clear()
    got = briefing.build(conn, people["journal"], people["journal"])["training"]
    assert got["strength_records"]["state"] == "possibly_incomplete" and "补记" in got["strength_records"]["tell_user"]
    assert "strength_records" not in briefing.build(conn, people["none"], people["none"])["training"]

