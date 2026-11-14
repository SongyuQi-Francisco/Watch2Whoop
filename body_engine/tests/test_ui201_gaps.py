"""UI 2.0.1 phase 4: the backend fields the redesigned app needs (training timer, 4-week muscle range, library size,
starred exercises, calendar zones / muscles, per-day session counts in /trends, fallback AI amounts, who can see my data,
who the unread messages are from). Synthetic people only.

Test database body_test_ui201_gaps (BODY_TEST_DB_NAME_UI201), recreated on every run; the pure parts run without Postgres."""
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.ai import choice, fallback
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.inbox import store as inbox
from body_engine.rollout import flags
from body_engine.training import history, muscle_load as ML, params as P, plans, session_log
from body_engine.web import views

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_UI201", "body_test_ui201_gaps")
CSRF = {"X-Body-Web": "1"}


# ---------------------------------------------------------------- pure parts
def test_day_zone_min_sums_sessions():
    assert history.day_zone_min([]) is None
    assert history.day_zone_min([{"hr_zone_min": None}, {"hr_zone_min": []}]) is None
    got = history.day_zone_min([{"hr_zone_min": [1.0, 2.5, 3.0, 0.0, 0.0]}, {"hr_zone_min": None},
                                {"hr_zone_min": [0.5, 0.5, 10.25, 2.0, 1.0]}])
    assert got == [1.5, 3.0, 13.2, 2.0, 1.0]


def test_range_4w_is_the_weekly_range_times_four():
    assert P.MUSCLE_LOAD_4W_DAYS == 28
    assert ML.range_4w("chest") == (40, 80)
    assert ML.range_4w("calves") == (32, 64)
    assert ML.status_for("chest", 30, ML.range_4w("chest")) == "low"
    assert ML.status_for("chest", 30) == "high"   # the same sets against one week's range


def test_muscle_load_last28_window():
    today = date(2026, 11, 12)   # a Thursday
    data = {"index": {"rows": {}, "by_name": {}, "by_app": {}}, "sessions": [], "imported": [],
            "sports": [{"date": (today - timedelta(days=20)).isoformat(), "type": "basketball", "min": 60.0,
                        "trimp": None, "avg_hr": None, "hr_max": None},
                       {"date": (today - timedelta(days=30)).isoformat(), "type": "basketball", "min": 60.0,
                        "trimp": None, "avg_hr": None, "hr_max": None}]}
    doc, _ = ML.compute_from(data, today)
    assert doc["last28_start"] == (today - timedelta(days=27)).isoformat()
    q = next(m for m in doc["muscles"] if m["key"] == "quadriceps")
    assert q["sport_min"] == {"today": 0, "week": 0, "last7": 0, "last28": 60}   # the day-30 game is outside
    assert q["ranges"]["last28"]["sets"] > 0 and q["ranges"]["last7"]["sets"] == 0
    assert q["target_4w"] == {"low": q["target"]["low"] * 4, "high": q["target"]["high"] * 4}
    assert q["status"]["last28"] == "low" and q["status"]["last7"] == "none"
    assert [x["min"] for x in doc["sports"]["last28"]] == [60]


def test_unread_from_one_entry_per_source():
    items = [
        {"type": "notice", "read": False},
        {"type": "circle", "_actor": None, "read": False},          # an interaction without a person: counted as system
        {"type": "circle_invite", "read": False, "invite": {"circle_name": "night runners", "avatar": None}},
        {"type": "whatsnew", "read": True},
    ]

    class NoDB:   # no person to look up: faces is never asked
        pass
    out = inbox.unread_from(NoDB(), 1, items)
    assert out == [{"type": "system"}, {"type": "circle_invite", "name": "night runners", "initial": "N"}]


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
        for key in ("training_day_v2", "muscle_load"):
            flags.set_flag(conn, key, "all", actor="test")
    flags.invalidate()
    return dsn


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def world(dsn):
    """A (owner) shares training with B; C has an expired grant from A; D shares nothing."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, role) VALUES (%s, %s) RETURNING id",
                               (name, "owner" if n == "A" else "member")).fetchone()["id"]
               for n, name in (("A", "Alex"), ("B", "bea"), ("C", "Cy"), ("D", "Dee"))}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        tok["A_ro"] = auth.create_token(conn, ids["A"], "ro", ["read"])
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["training"]))
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes, expires_at) VALUES (%s, %s, %s, now() - interval '1 day')",
                     (ids["A"], ids["C"], ["training"]))
        today = plans._user_today(conn, ids["A"])
    return ids, tok, today


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", **CSRF}


def _workout(conn, uid: int, ext: str, type_: str, day: date, minutes: float, zones=None) -> int:
    tz = ZoneInfo("Australia/Sydney")
    start = datetime.combine(day, time(18, 0), tz)
    return conn.execute(
        """INSERT INTO workouts (user_id, source, external_id, type, name_zh, start_at, end_at, local_date, duration_min,
                                 hr_zone_min) VALUES (%s, 'shortcut', %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (uid, ext, type_, type_, start, start + timedelta(minutes=minutes), day, minutes, zones)).fetchone()["id"]


# 1. training timer
def test_session_started_at(client, world, conn):
    ids, tok, today = world
    url = f"/v1/users/me/training/session/{today}"
    ex = lambda done: {"exercises": [{"slug": "lat_pulldown", "sets": [{"reps": 10, "weight_kg": 40, "done": done}]}]}
    log = client.put(url, headers=H(tok["A"]), json=ex(False)).json()["log"]
    assert log["started_from"] == "draft" and log["first_set_at"] is None and log["started_at"] == log["created_at"]
    tz = ZoneInfo("Australia/Sydney")
    now = datetime.now(timezone.utc)
    sent = max(now - timedelta(minutes=12), datetime.combine(today, time(0, 1), tz))
    log = client.put(url, headers=H(tok["A"]), json={**ex(True), "started_at": sent.isoformat()}).json()["log"]
    assert log["started_from"] == "first_set"
    assert abs(datetime.fromisoformat(log["started_at"]) - sent) < timedelta(seconds=1)
    later = client.put(url, headers=H(tok["A"]), json={**ex(True), "started_at": now.isoformat()}).json()["log"]
    assert later["first_set_at"] == log["first_set_at"]                     # kept from the first tick
    view = client.get(f"/v1/users/me/training/session?date={today}", headers=H(tok["A"])).json()
    assert view["log"]["started_at"] == log["started_at"]
    # B (training share) reads it too; old clients without started_at keep working (first tick = now)
    assert client.get(f"/v1/users/{ids['A']}/training/session?date={today}", headers=H(tok["B"])).json()["log"]["started_at"]


def test_first_set_time_rules(conn, world):
    ids, _, today = world
    now = datetime.now(timezone.utc)
    assert session_log.first_set_time(conn, ids["A"], today, None, now) == now
    assert session_log.first_set_time(conn, ids["A"], today, now + timedelta(hours=2), now) == now   # future: ignored
    assert session_log.first_set_time(conn, ids["A"], today - timedelta(days=3), None, now) is None  # backfill, unknown
    old = datetime.combine(today - timedelta(days=3), time(18, 30), ZoneInfo("Australia/Sydney"))
    assert session_log.first_set_time(conn, ids["A"], today - timedelta(days=3), old, now) == old


# 3. + 4. library size and favourites
def test_library_total_and_favorites(client, world):
    ids, tok, _ = world
    base = "/v1/users/me/training/exercises"
    all_ = client.get(base, headers=H(tok["A"]), params={"limit": 2}).json()
    assert all_["total"] == 2 and all_["matched"] == all_["library_total"] > 2
    some = client.get(base, headers=H(tok["A"]), params={"q": "bench", "limit": 1}).json()
    assert some["total"] == 1 and 1 <= some["matched"] < some["library_total"] == all_["library_total"]
    slug = some["exercises"][0]["slug"]
    assert some["exercises"][0]["favorite"] is False and some["favorites_count"] == 0

    r = client.put(f"{base}/{slug}/favorite", headers=H(tok["A"]))
    assert r.status_code == 200 and r.json() == {"slug": slug, "favorite": True, "favorites_count": 1}
    assert client.put(f"{base}/{slug}/favorite", headers=H(tok["A"])).json()["favorites_count"] == 1   # idempotent
    fav = client.get(base, headers=H(tok["A"]), params={"favorites": "true"}).json()
    assert [x["slug"] for x in fav["exercises"]] == [slug] and fav["exercises"][0]["favorite"] is True
    assert fav["matched"] == 1 and fav["library_total"] == all_["library_total"]
    assert client.get(f"{base}/{slug}", headers=H(tok["A"])).json()["favorite"] is True

    # a friend with the training share sees the library, never the stars, and cannot star for A
    theirs = client.get(f"/v1/users/{ids['A']}/training/exercises", headers=H(tok["B"]), params={"q": "bench"}).json()
    assert "favorites_count" not in theirs and all("favorite" not in x for x in theirs["exercises"])
    assert "favorite" not in client.get(f"/v1/users/{ids['A']}/training/exercises/{slug}", headers=H(tok["B"])).json()
    assert client.put(f"/v1/users/{ids['A']}/training/exercises/{slug}/favorite", headers=H(tok["B"])).status_code == 403
    assert client.put(f"{base}/{slug}/favorite", headers=H(tok["A_ro"])).status_code == 403          # read-only token
    assert client.put(f"{base}/no-such-exercise-xyz/favorite", headers=H(tok["A"])).status_code == 404

    r = client.delete(f"{base}/{slug}/favorite", headers=H(tok["A"]))
    assert r.json() == {"slug": slug, "favorite": False, "favorites_count": 0}
    assert client.get(base, headers=H(tok["A"]), params={"favorites": "true"}).json()["exercises"] == []


# 5. calendar zones and muscles
def test_calendar_zones_and_muscles(client, world, conn):
    ids, tok, today = world
    day = today - timedelta(days=1)
    _workout(conn, ids["A"], "cal-1", "basketball", day, 60, [5.0, 10.0, 20.0, 15.0, 10.0])
    _workout(conn, ids["A"], "cal-2", "running", day, 30, [0.0, 5.5, 20.0, 4.5, 0.0])
    conn.commit()
    cal = client.get("/v1/users/me/training/calendar", headers=H(tok["A"]),
                     params={"start": (day - timedelta(days=1)).isoformat(), "end": day.isoformat()}).json()
    d = next(x for x in cal["days"] if x["date"] == day.isoformat())
    assert d["hr_zone_min"] == [5.0, 15.5, 40.0, 19.5, 10.0]
    assert d["muscle_groups"] and d["muscle_groups"][0] == "legs" and "quadriceps" in d["muscles"]
    rest = next(x for x in cal["days"] if x["date"] != day.isoformat())
    assert rest["hr_zone_min"] is None and rest["muscles"] == [] and rest["muscle_groups"] == []
    # the friend with the training share gets the same
    theirs = client.get(f"/v1/users/{ids['A']}/training/calendar", headers=H(tok["B"]),
                        params={"start": day.isoformat(), "end": day.isoformat()}).json()
    assert theirs["days"][0]["muscle_groups"] == d["muscle_groups"]


# 6. /trends per-day sessions
def test_trends_training_sessions(client, world, conn):
    ids, tok, today = world
    _workout(conn, ids["A"], "tr-1", "walking", today, 20)
    conn.commit()
    doc = client.get("/v1/users/me/trends", headers=H(tok["A"]), params={"days": 7}).json()
    ts = doc["training_sessions"]
    assert len(ts["workouts"]) == len(doc["dates"]) == 7
    assert ts["workouts"][-1] >= 1 and ts["workouts_total"] == sum(ts["workouts"])
    assert ts["values"] is None or (len(ts["values"]) == 7 and ts["total"] == sum(ts["values"]))
    # D has no share from A: 403 as before; B sees it (training share)
    assert client.get(f"/v1/users/{ids['A']}/trends", headers=H(tok["D"])).status_code == 403
    assert "training_sessions" in client.get(f"/v1/users/{ids['A']}/trends", headers=H(tok["B"]), params={"days": 7}).json()


# 7. fallback AI amounts on the AI switcher
def test_choice_option_has_amounts(conn, world, monkeypatch):
    ids, _, _ = world
    monkeypatch.setattr(fallback, "read_key", lambda: "k")
    monkeypatch.setattr(fallback, "summary", lambda c, uid, refresh=False: {
        "display_name": "Jarvis", "available": True, "reason": None, "consent": True, "spent_cny": 1.2, "quota_cny": 3.0,
        "remaining_cny": 1.8, "remaining_pct": 60, "reset_on": "2026-12-01"})
    opt = next(o for o in choice.options(conn, ids["D"]) if o["key"] == "fallback")
    assert (opt["spent_cny"], opt["quota_cny"], opt["remaining_cny"], opt["remaining_pct"]) == (1.2, 3.0, 1.8, 60)


# 8. who can see my data
def test_audience(client, world, conn):
    ids, tok, _ = world
    a = views.audience(conn, {"id": ids["A"]})
    assert a["count"] == 1 and a["more"] == 0
    assert [(p["id"], p["initial"], p["scopes"]) for p in a["people"]] == [(ids["B"], "B", ["training"])]   # C's grant expired
    assert views.audience(conn, {"id": ids["D"]}) == {"count": 0, "people": [], "more": 0}
    me = client.get("/v1/web/me", headers=H(tok["A"])).json()
    assert me["audience"]["count"] == 1
    assert client.get("/v1/users/me/audience", headers=H(tok["A"])).json()["people"][0]["name"] == "bea"
    assert client.get(f"/v1/users/{ids['A']}/audience", headers=H(tok["B"])).status_code == 403   # owner-only
