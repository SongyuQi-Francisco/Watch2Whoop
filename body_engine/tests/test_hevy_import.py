"""Hevy CSV import (issue #161, rollout flag hevy_import): parsing, exercise matching, idempotent writes to workouts +
training_session_logs, the merge into the training history the AI reads, muscle load, the user's mappings and the HTTP
endpoints. Every CSV here is synthetic (made-up workouts in Hevy's 14-column export format)."""
import gzip
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.training import hevy_import, history, importer, loads, session_log
from body_engine.training import params as P

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_HEVY", "body_test_hevy_import")
TZ = ZoneInfo("Australia/Sydney")
CSRF = {"X-Body-Web": "1"}
FLAG = "hevy_import"
HEAD = ("title,start_time,end_time,description,exercise_title,superset_id,exercise_notes,set_index,set_type,"
        "weight_kg,reps,distance_km,duration_seconds,rpe")


def csv_text(rows: list[tuple], head: str = HEAD) -> str:
    return "\n".join([head] + [",".join(f'"{x}"' if isinstance(x, str) else ("" if x is None else str(x)) for x in r)
                               for r in rows]) + "\n"


PUSH = ("Push A", "2 Mar 2026, 18:00", "2 Mar 2026, 19:05", "test push")
PULL = ("Pull A", "4 Mar 2026, 07:30", "4 Mar 2026, 08:20", "")
ROWS = [
    (*PUSH, "Bench Press (Barbell)", None, "", 0, "warmup", 40, 10, None, None, None),
    (*PUSH, "Bench Press (Barbell)", None, "", 1, "normal", 60, 8, None, None, 8),
    (*PUSH, "Bench Press (Barbell)", None, "", 2, "normal", 60, 8, None, None, 8.5),
    (*PUSH, "Bench Press (Barbell)", None, "", 3, "dropset", 50, 10, None, None, None),
    (*PUSH, "Incline Bench Press (Dumbbell)", 0, "slow", 0, "normal", 22, 10, None, None, None),
    (*PUSH, "Imaginary Press (Gizmo)", 0, "", 0, "failure", 30, 12, None, None, None),
    (*PULL, "Lat Pulldown (Cable)", None, "", 0, "normal", 50, 10, None, None, None),
    (*PULL, "Lat Pulldown (Cable)", None, "", 1, "normal", 50, 9, None, None, None),
    (*PULL, "Treadmill", None, "", 0, "normal", None, None, 2.5, 900, None),
    (*PULL, "Lat Pulldown (Cable)", None, "", 2, "nonsense-type", "abc", 9, None, None, None),   # bad number: skipped
]


# ---------------------------------------------------------------- parsing (no database)
def test_parse_hevy_export():
    p = hevy_import.parse(csv_text(ROWS), TZ)
    assert p["rows"] == 10 and p["skipped"] == 1 and len(p["workouts"]) == 2
    push, pull = p["workouts"]
    assert push["title"] == "Push A" and push["start"] == datetime(2026, 3, 2, 18, 0, tzinfo=TZ)
    assert push["end"] - push["start"] == timedelta(minutes=65) and push["description"] == "test push"
    bench = push["exercises"][0]
    assert [s["type"] for s in bench["sets"]] == ["warmup", "normal", "normal", "dropset"]
    assert bench["sets"][1] == {"set": 2, "type": "normal", "reps": 8, "weight_kg": 60.0, "rpe": 8.0}
    assert push["exercises"][1]["superset"] == "0" and push["exercises"][1]["notes"] == "slow"
    assert pull["exercises"][1]["sets"][0] == {"set": 1, "type": "normal", "distance_km": 2.5, "duration_s": 900.0}


def test_parse_imperial_iso_and_blocks():
    head = HEAD.replace("weight_kg", "weight_lbs").replace("distance_km", "distance_miles")
    rows = [("Legs", "2026-03-05T06:00:00", "2026-03-05T07:00:00", "", "Squat (Barbell)", None, "", 0, "normal", 225, 5, None, None, None),
            ("Legs", "2026-03-05T06:00:00", "2026-03-05T07:00:00", "", "Leg Press (Machine)", None, "", 0, "normal", 300, 10, None, None, None),
            ("Legs", "2026-03-05T06:00:00", "2026-03-05T07:00:00", "", "Squat (Barbell)", None, "", 0, "normal", 135, 10, None, None, None),
            ("Legs", "2026-03-05T06:00:00", "2026-03-05T07:00:00", "", "Walk", None, "", 0, "normal", None, None, 1, 600, None)]
    w = hevy_import.parse(csv_text(rows, head), TZ)["workouts"][0]
    assert [e["name"] for e in w["exercises"]] == ["Squat (Barbell)", "Leg Press (Machine)", "Squat (Barbell)", "Walk"]
    assert w["exercises"][0]["sets"][0]["weight_kg"] == pytest.approx(102.06, abs=0.01)
    assert w["exercises"][3]["sets"][0]["distance_km"] == pytest.approx(1.609, abs=0.001)
    assert w["start"] == datetime(2026, 3, 5, 6, 0, tzinfo=TZ)


def test_parse_refuses_other_files_and_limits():
    with pytest.raises(ValueError):
        hevy_import.parse("Date,Workout Name,Exercise Name\n2026-01-01,A,B\n", TZ)
    with pytest.raises(ValueError):
        hevy_import.decode(b"\xff\xfe\x00broken")
    big = gzip.compress(b"x" * (P.HEVY_MAX_CSV_BYTES + 10))
    with pytest.raises(ValueError):
        hevy_import.decode(big)
    assert hevy_import.decode(gzip.compress(("﻿" + HEAD).encode())) == HEAD
    assert hevy_import.parse_time("31 Feb 2026, 10:00", TZ) is None
    assert hevy_import.parse_time("1 Sept 2026, 9:05", TZ) == datetime(2026, 9, 1, 9, 5, tzinfo=TZ)


def test_candidates():
    assert hevy_import.candidates("Seated Cable Row - V Grip (Cable)") == [
        "Cable Seated Cable Row - V Grip", "Seated Cable Row - V Grip", "Cable Seated Cable Row", "Seated Cable Row"]


# ---------------------------------------------------------------- database
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


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def world(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, role, timezone) VALUES (%s, %s, 'Australia/Sydney') RETURNING id",
                               (n, "owner" if n == "A" else "member")).fetchone()["id"] for n in ("A", "B")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["training"]))
        # a watch workout overlapping the push session (it has heart rate: it is kept, the Hevy copy points to it)
        start = datetime(2026, 3, 2, 18, 2, tzinfo=TZ)
        importer.upsert(conn, ids["A"], [{**importer._base_row(TZ, start, start + timedelta(minutes=62)),
                                          "source": "ow", "external_id": "ow-push", "origin": "watch", "is_xml": False,
                                          "device": "Apple Watch", "type": "strength_training", "name_zh": "力量训练",
                                          "active_kcal": 300.0, "total_kcal": 380.0, "distance_km": None, "avg_hr": 118.0,
                                          "max_hr": 160.0, "zone_offset": None}])
        # the training page's own log on the pull day: an import must not touch it
        conn.execute("""INSERT INTO training_session_logs (user_id, date, exercises, status)
                        VALUES (%s, '2026-03-04', %s, 'draft')""",
                     (ids["A"], Jsonb([{"slug": "lat_pulldown", "sets": [{"reps": 10, "weight_kg": 45, "done": True}]}])))
        for d in ("2026-03-02", "2026-03-03", "2026-03-04"):
            conn.execute("INSERT INTO daily_scores (user_id, date, muscle_load, details, algo_version) VALUES (%s, %s, %s, %s, 'test')",
                         (ids["A"], d, 999.0 if d == "2026-03-03" else None, Jsonb({"keep": 1})))
    return ids, tok


def test_import_writes_and_is_idempotent(conn, world):
    ids, _ = world
    a = ids["A"]
    res = hevy_import.run(conn, a, gzip.compress(csv_text(ROWS).encode()))
    assert res["workouts"] == 2 and res["new_workouts"] == 2 and res["sets"] == 9 and res["skipped_rows"] == 1
    assert res["unmatched"] == [{"name": "Imaginary Press (Gizmo)", "sets": 1}, {"name": "Treadmill", "sets": 1}]
    assert (res["start"], res["end"]) == ("2026-03-02", "2026-03-04")

    ws = conn.execute("SELECT * FROM workouts WHERE user_id = %s AND source = 'hevy' ORDER BY start_at", (a,)).fetchall()
    assert [w["name_zh"] for w in ws] == ["Push A", "Pull A"] and ws[0]["external_id"] == "2026-03-02T07:00:00Z"
    watch = conn.execute("SELECT id FROM workouts WHERE user_id = %s AND source = 'ow'", (a,)).fetchone()["id"]
    assert ws[0]["dup_of"] == watch and ws[1]["dup_of"] is None and ws[0]["flags"]["note"] == "test push"

    logs = {r["date"]: r for r in conn.execute(
        "SELECT * FROM training_session_logs WHERE user_id = %s AND source = 'hevy'", (a,))}
    assert set(logs) == {date(2026, 3, 2), date(2026, 3, 4)} and logs[date(2026, 3, 2)]["status"] == "imported"
    push = logs[date(2026, 3, 2)]["exercises"]
    assert [e["slug"] for e in push] == ["barbell_bench_press", "incline_dumbbell_press", None]
    assert push[2]["unmatched"] and push[2]["name"] == "Imaginary Press (Gizmo)"
    # the page's own row of the pull day is untouched
    page = session_log._row(conn, a, date(2026, 3, 4))
    assert page["status"] == "draft" and page["exercises"][0]["sets"][0]["weight_kg"] == 45

    snapshot = conn.execute("SELECT date, exercises, updated_at FROM training_session_logs WHERE user_id = %s ORDER BY date, source",
                            (a,)).fetchall()
    again = hevy_import.run(conn, a, csv_text(ROWS).encode())   # same file, not compressed this time
    assert again["new_workouts"] == 0 and again["sets"] == 9
    assert conn.execute("SELECT count(*) AS n FROM workouts WHERE user_id = %s AND source = 'hevy'", (a,)).fetchone()["n"] == 2
    assert conn.execute("SELECT date, exercises, updated_at FROM training_session_logs WHERE user_id = %s ORDER BY date, source",
                        (a,)).fetchall() == snapshot
    assert hevy_import.status(conn, a)["last"]["workouts"] == 2


def test_muscle_load_of_affected_days(conn, world):
    a = world[0]["A"]
    rows = {r["date"]: r for r in conn.execute("SELECT date, muscle_load, details FROM daily_scores WHERE user_id = %s", (a,))}
    # push: working sets 60×8 + 60×8 + 50×10 (drop set counts) + 22×10 + 30×12; the warm-up 40×10 does not
    assert rows[date(2026, 3, 2)]["muscle_load"] == 60 * 8 * 2 + 500 + 220 + 360
    assert rows[date(2026, 3, 2)]["details"]["keep"] == 1 and rows[date(2026, 3, 2)]["details"]["muscle"]["working_sets"] == 5
    assert rows[date(2026, 3, 3)]["muscle_load"] is None and "muscle" not in rows[date(2026, 3, 3)]["details"]   # no record that day
    assert rows[date(2026, 3, 4)]["muscle_load"] == 50 * 10 + 50 * 9   # the treadmill is cardio


def test_history_and_ai_see_imported_sets(conn, world):
    a = world[0]["A"]
    day = history.day(conn, a, a, date(2026, 3, 2))
    [s] = day["sessions"]   # the watch workout, carrying the Hevy sets
    assert s["source"] == "ow+hevy" and s["source_label"].endswith("+ Hevy") and s["avg_hr"] == 118
    assert [e["name"] for e in s["exercises"]] == ["Bench Press (Barbell)", "Incline Bench Press (Dumbbell)", "Imaginary Press (Gizmo)"]
    assert s["exercises"][0]["sets"][0]["type"] == "warmup" and s["exercises"][2]["unmatched"]
    assert s["volume_kg"] == 60 * 8 * 2 + 500 + 220 + 360 and s["notes"] == "test push"
    pull = history.day(conn, a, a, date(2026, 3, 4))["sessions"]
    assert [x["source"] for x in pull] == ["hevy"] and pull[0]["name"] == "Pull A" and pull[0]["duration_min"] == 50
    cal = history.calendar(conn, a, a, month="2026-03")
    assert {d["date"]: d["sessions_count"] for d in cal["days"]}[date(2026, 3, 4)] == 1
    out = run_tool(conn, a, "butler", "get_training_day", {"user": "me", "date": "2026-03-04"})
    assert "Lat Pulldown (Cable)" in str(out)
    # last session / recommended weight history (no SparkyFitness linked: the imported sets alone)
    loads.clear_history_cache(a)
    sessions, err, linked = loads.load_history(conn, a, date(2026, 3, 10))
    assert err is None and linked and {s["workout"] for s in sessions} == {"2026-03-02T07:00:00Z", "2026-03-03T20:30:00Z"}
    hist = loads.history_by_slug(loads.Index(conn, a), sessions, date(2026, 3, 10))
    assert hist["barbell_bench_press"][0]["sets"][0] == {"set": 2, "type": "normal", "reps": 8, "weight_kg": 60.0, "rpe": 8.0}
    # B has the training share and sees the imported sets too
    assert history.day(conn, world[0]["B"], a, date(2026, 3, 4))["sessions"][0]["exercises"]


def test_mapping_an_unmatched_name(conn, world):
    a = world[0]["A"]
    with pytest.raises(LookupError):
        hevy_import.set_mapping(conn, a, "Imaginary Press (Gizmo)", "no such exercise anywhere")
    res = hevy_import.set_mapping(conn, a, "Imaginary Press (Gizmo)", "machine_chest_press")
    assert res["slug"] == "machine_chest_press" and res["rows"] == 1
    assert [u["name"] for u in res["unmatched"]] == ["Treadmill"]
    # a later import keeps the user's answer
    hevy_import.run(conn, a, csv_text(ROWS).encode())
    push = conn.execute("SELECT exercises FROM training_session_logs WHERE user_id = %s AND date = '2026-03-02' AND source = 'hevy'",
                        (a,)).fetchone()["exercises"]
    assert push[2]["slug"] == "machine_chest_press" and "unmatched" not in push[2]
    # forgetting the answer makes it unmatched again
    assert [u["name"] for u in hevy_import.set_mapping(conn, a, "Imaginary Press (Gizmo)", None)["unmatched"]] == [
        "Imaginary Press (Gizmo)", "Treadmill"]


def test_page_log_still_works_next_to_an_import(conn, world):
    a = world[0]["A"]
    today = session_log._today(conn, a)
    conn.execute("""INSERT INTO training_session_logs (user_id, date, source, exercises, status)
                    VALUES (%s, %s, 'hevy', '[]', 'imported') ON CONFLICT DO NOTHING""", (a, today))
    out = session_log.save_draft(conn, a, today, [{"slug": "lat_pulldown", "sets": [{"reps": 8, "weight_kg": 40, "done": True}]}])
    assert out["log"]["status"] == "draft"
    assert conn.execute("SELECT count(*) AS n FROM training_session_logs WHERE user_id = %s AND date = %s", (a, today)).fetchone()["n"] == 2


# ---------------------------------------------------------------- HTTP + flag
@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token: str, **extra) -> dict:
    return {"Authorization": f"Bearer {token}", **CSRF, **extra}


def _flag(dsn, state: str) -> None:
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        flags.set_flag(c, FLAG, state, actor="test")
    flags.invalidate()


def test_http_behind_flag(client, world, dsn):
    ids, tok = world
    url = "/v1/users/me/training/import/hevy"
    body = gzip.compress(csv_text(ROWS[:4]).encode())
    _flag(dsn, "off")
    assert client.get(url, headers=H(tok["B"])).status_code == 404
    assert client.post(url, headers=H(tok["B"], **{"Content-Type": "application/gzip"}), content=body).status_code == 404
    _flag(dsn, "all")
    try:
        r = client.post(url, headers=H(tok["B"], **{"Content-Type": "application/gzip"}), content=body)
        assert r.status_code == 200 and r.json()["workouts"] == 1 and r.json()["sets"] == 4
        assert client.get(url, headers=H(tok["B"])).json()["last"]["sets"] == 4
        # only for oneself
        assert client.post(f"/v1/users/{ids['A']}/training/import/hevy", headers=H(tok["B"]), content=body).status_code == 403
        assert client.get(f"/v1/users/{ids['A']}/training/import/hevy", headers=H(tok["B"])).status_code == 403
        # size limit and a non-Hevy file, the error in English when asked
        big = os.urandom(P.HEVY_MAX_UPLOAD_BYTES + 1)
        assert client.post(url, headers=H(tok["B"]), content=big).status_code == 413
        r = client.post(url, headers=H(tok["B"], **{"X-Body-Lang": "en"}), content=b"a,b\n1,2\n")
        assert r.status_code == 422 and "Hevy" in r.json()["detail"]
        r = client.put(f"{url}/map", headers=H(tok["B"]), json={"name": "Bench Press (Barbell)", "exercise": "barbell_bench_press"})
        assert r.status_code == 200 and r.json()["slug"] == "barbell_bench_press"
    finally:
        _flag(dsn, "off")
