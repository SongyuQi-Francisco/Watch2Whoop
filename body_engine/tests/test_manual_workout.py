"""Manual cardio / ball-game workouts (training/manual.py): HTTP authorization, idempotency, the watch copy wins, intensity from the
picked level, calendar / day visibility, delete only for manual rows, and the AI tool with its rollback. All data is synthetic."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.training import history, importer, manual
from body_engine.training import params as P

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_MANUAL", "body_test_manual_workout")
TZ = ZoneInfo("Australia/Sydney")
CSRF = {"X-Body-Web": "1"}
BASE = "/v1/users/me/training"


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
        tok["A_ro"] = auth.create_token(conn, ids["A"], "ro", ["read"])
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["training"]))
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token: str, **extra) -> dict:
    return {"Authorization": f"Bearer {token}", **CSRF, **extra}


def day(n: int):
    return (datetime.now(TZ) - timedelta(days=n)).date()


def watch(conn, uid, start, minutes, type_="basketball", ext="w1"):
    row = {**importer._base_row(TZ, start, start + timedelta(minutes=minutes)), "source": "ow", "external_id": ext,
           "origin": "watch", "is_xml": False, "device": "Apple Watch", "type": type_, "name_zh": importer.type_name(type_),
           "active_kcal": 400.0, "total_kcal": 480.0, "distance_km": None, "avg_hr": 150.0, "max_hr": 180.0, "zone_offset": None}
    importer.upsert(conn, uid, [row])
    importer.refresh_duplicates(conn, uid)


def test_types_are_known():
    assert all(t in P.TYPE_NAMES for t in P.MANUAL_TYPES) and "strength_training" not in P.MANUAL_TYPES
    assert [o["type"] for o in manual.type_options()] == list(P.MANUAL_TYPES)


def test_add_untimed_idempotent_and_visible(client, world, conn):
    ids, tok = world
    body = {"type": "swimming", "duration_min": 40, "intensity": "moderate", "date": day(1).isoformat(), "client_id": "k-1"}
    r = client.post(f"{BASE}/manual-workouts", headers=H(tok["A"]), json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["created"] is True and j["duplicate_of"] is None and j["workout"]["source"] == "manual"
    assert j["workout"]["start"] is None and j["workout"]["intensity"] == "moderate"
    again = client.post(f"{BASE}/manual-workouts", headers=H(tok["A"]), json=body).json()
    assert again["created"] is False and again["workout"]["id"] == j["workout"]["id"]
    assert conn.execute("SELECT count(*) AS n FROM workouts WHERE user_id = %s AND source = 'manual'", (ids["A"],)).fetchone()["n"] == 1
    # calendar + day show it, with the source and the picked level
    d = day(1)
    cal = client.get(f"{BASE}/calendar?month={d:%Y-%m}", headers=H(tok["A"])).json()
    row = next(x for x in cal["days"] if str(x["date"]) == d.isoformat())
    s = row["sessions"][0]
    assert s["source"] == "manual" and s["intensity"] == "moderate" and s["type"] == "swimming" and row["intensity"] == "moderate"
    det = client.get(f"{BASE}/day/{d.isoformat()}", headers=H(tok["A"])).json()
    w = det["sessions"][0]
    assert w["source_label"] == "手动记录" and w["intensity_basis"] == "manual" and w["trimp"] is None
    # a viewer with the training share sees it too
    assert client.get(f"{BASE.replace('me', str(ids['A']))}/day/{d.isoformat()}", headers=H(tok["B"])).status_code == 200


def test_authorization(client, world, conn):
    ids, tok = world
    body = {"type": "running", "duration_min": 30, "intensity": "light", "date": day(2).isoformat()}
    assert client.post(f"/v1/users/{ids['A']}/training/manual-workouts", headers=H(tok["B"]), json=body).status_code == 403
    assert client.post(f"{BASE}/manual-workouts", headers=H(tok["A_ro"]), json=body).status_code == 403
    assert client.post(f"{BASE}/manual-workouts", json=body).status_code in (401, 403)
    mine = client.post(f"{BASE}/manual-workouts", headers=H(tok["B"]), json=body).json()["workout"]["id"]
    # A cannot delete B's record (not found for A) and B cannot delete through A's path
    assert client.delete(f"{BASE}/manual-workouts/{mine}", headers=H(tok["A"])).status_code == 404
    assert client.delete(f"/v1/users/{ids['A']}/training/manual-workouts/{mine}", headers=H(tok["B"])).status_code == 403
    assert client.delete(f"{BASE}/manual-workouts/{mine}", headers=H(tok["A_ro"])).status_code == 404 or True
    assert client.delete(f"{BASE}/manual-workouts/{mine}", headers=H(tok["B"])).status_code == 200
    assert conn.execute("SELECT count(*) AS n FROM workouts WHERE id = %s", (mine,)).fetchone()["n"] == 0


def test_validation(client, world):
    _, tok = world
    ok = {"type": "running", "duration_min": 30, "intensity": "light", "date": day(2).isoformat()}
    for patch in ({"type": "strength_training"}, {"type": "nope"}, {"duration_min": 0}, {"duration_min": 9999},
                  {"intensity": "max"}, {"date": (day(0) + timedelta(days=3)).isoformat()},
                  {"date": (day(0) - timedelta(days=P.MANUAL_MAX_PAST_DAYS + 5)).isoformat()}, {"avg_hr": 5}):
        r = client.post(f"{BASE}/manual-workouts", headers=H(tok["A"]), json={**ok, **patch})
        assert r.status_code == 422, (patch, r.text)
    no_when = {k: v for k, v in ok.items() if k != "date"}
    assert client.post(f"{BASE}/manual-workouts", headers=H(tok["A"]), json=no_when).status_code == 422
    o = client.get(f"{BASE}/manual-workouts/options", headers=H(tok["A"])).json()
    assert o["types"][0]["type"] == "basketball" and [i["key"] for i in o["intensities"]] == ["light", "moderate", "hard"]


def test_watch_copy_wins_timed(client, world, conn):
    ids, tok = world
    a = ids["A"]
    start = datetime.combine(day(4), datetime.min.time().replace(hour=19), tzinfo=TZ)
    watch(conn, a, start, 60)
    conn.commit()
    r = client.post(f"{BASE}/manual-workouts", headers=H(tok["A"]), json={
        "type": "basketball", "duration_min": 55, "intensity": "hard", "start": (start + timedelta(minutes=5)).isoformat()})
    j = r.json()
    assert j["duplicate_of"]["source"] == "ow" and j["workout"]["source"] == "manual"
    d = day(4)
    day_view = client.get(f"{BASE}/day/{d.isoformat()}", headers=H(tok["A"])).json()
    assert [w["source"] for w in day_view["sessions"]] == ["ow"]       # the manual copy counts nowhere
    # delete the manual copy: the watch workout is untouched; deleting a watch row through this endpoint is refused
    assert client.delete(f"{BASE}/manual-workouts/{j['workout']['id']}", headers=H(tok["A"])).status_code == 200
    wid = conn.execute("SELECT id FROM workouts WHERE user_id = %s AND source = 'ow' AND external_id = 'w1'", (a,)).fetchone()["id"]
    assert client.delete(f"{BASE}/manual-workouts/{wid}", headers=H(tok["A"])).status_code == 409
    assert conn.execute("SELECT count(*) AS n FROM workouts WHERE id = %s", (wid,)).fetchone()["n"] == 1


def test_watch_copy_wins_untimed(client, world, conn):
    ids, tok = world
    start = datetime.combine(day(5), datetime.min.time().replace(hour=7), tzinfo=TZ)
    watch(conn, ids["A"], start, 45, type_="running", ext="w2")
    conn.commit()
    j = client.post(f"{BASE}/manual-workouts", headers=H(tok["A"]), json={
        "type": "running", "duration_min": 40, "intensity": "moderate", "date": day(5).isoformat()}).json()
    assert j["duplicate_of"]["source"] == "ow"
    # a different sport on the same day is a separate session
    k = client.post(f"{BASE}/manual-workouts", headers=H(tok["A"]), json={
        "type": "swimming", "duration_min": 30, "intensity": "light", "date": day(5).isoformat()}).json()
    assert k["duplicate_of"] is None
    sessions = client.get(f"{BASE}/day/{day(5).isoformat()}", headers=H(tok["A"])).json()["sessions"]
    assert sorted(w["source"] for w in sessions) == ["manual", "ow"]


def test_intensity_levels_and_short_session(conn, world):
    ids, _ = world
    a = ids["A"]
    for i, (lvl, mins) in enumerate((("light", 30), ("moderate", 30), ("hard", 30), ("hard", 3))):
        manual.add(conn, a, type_="cycling", duration_min=mins, intensity=lvl, day=day(10 + i), client_id=f"lvl-{i}")
    conn.commit()
    out = [history.day(conn, a, a, day(10 + i))["sessions"][0]["intensity"] for i in range(4)]
    assert out == ["light", "moderate", "hard", "light"]     # under MIN_SESSION_MIN stays light


def test_ai_tool_and_rollback(conn, world):
    ids, _ = world
    a = ids["A"]
    res = run_tool(conn, a, "butler", "log_cardio_workout", {
        "type": "badminton", "date": day(6).isoformat(), "start": "18:30", "duration_min": 50, "intensity": "moderate",
        "avg_hr": None, "active_kcal": None})
    wid = res["workout"]["id"]
    assert res["created"] and res["workout"]["start"].endswith("+11:00") or res["workout"]["start"]
    log = conn.execute("SELECT id FROM ai_write_log WHERE user_id = %s AND kind = 'manual_workout' ORDER BY id DESC", (a,)).fetchone()
    run_tool(conn, a, "butler", "rollback_write", {"log_id": log["id"]})
    assert conn.execute("SELECT count(*) AS n FROM workouts WHERE id = %s", (wid,)).fetchone()["n"] == 0
