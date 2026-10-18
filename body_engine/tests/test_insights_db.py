"""insights end to end: synthesize a person -> compute-scores scores + body age / stress -> API and butler tools (including shares authorization).
Uses a separate test database body_test_insights (configurable via BODY_TEST_INSIGHTS_DB), reset on every run; created if missing; skipped if it cannot connect."""
import dataclasses
import os
from datetime import date, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine import auth
from body_engine.config import settings
from body_engine.db import migrate
from body_engine.insights import jobs, views
from body_engine.scores import engine
from test_scores import SYD, make_inputs
from test_scores_db import FakeOW, seed

TEST_DB = os.environ.get("BODY_TEST_INSIGHTS_DB", "body_test_insights")
TEST_SETTINGS = dataclasses.replace(settings, db_name=TEST_DB)
NIGHTS = 45          # 45 nights from 2026-09-01 (each body-age item needs >= 30 days)
TODAY = datetime.now(SYD).date()


@pytest.fixture
def conn():
    try:
        with psycopg.connect(dataclasses.replace(settings, db_name="postgres").dsn, autocommit=True) as admin:
            if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone():
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
        c = psycopg.connect(TEST_SETTINGS.dsn, row_factory=dict_row, autocommit=True)
    except (psycopg.OperationalError, RuntimeError) as e:
        pytest.skip(f"test DB {TEST_DB} unreachable: {e}")
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    migrate(c)
    yield c
    c.close()


class FakeOW2(FakeOW):
    """Adds steps and workout records (stress must exclude walking and workouts)."""

    def __init__(self, inp, steps=(), workouts=()):
        super().__init__(inp)
        for ts, v in steps:
            self.raw.append({"timestamp": ts.isoformat(), "type": "steps", "value": v, "unit": "count",
                             "source": {"source": "healthkit", "device_type": "watch", "device": "Watch7,9"}})
        self.workouts = [{"start_time": a.isoformat(), "end_time": b.isoformat()} for a, b in workouts]

    def paginate(self, path, **params):
        if path.endswith("/events/workouts"):
            yield from self.workouts
        else:
            yield from super().paginate(path, **params)


@pytest.fixture
def person(conn):
    inp = make_inputs(NIGHTS)
    uid = seed(conn, inp)
    conn.execute("UPDATE users SET sex = 'male', height_cm = 184 WHERE id = %s", (uid,))
    for k in range(NIGHTS):
        d = date(2026, 9, 1) + timedelta(days=k)
        for metric, value in (("step_count", 6000.0), ("vo2_max", 44.0 if k % 10 == 0 else None), ("body_mass", 89.0 if k == NIGHTS - 1 else None)):
            if value is not None:
                conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                                VALUES (%s, %s, %s, %s, '', 'avg', 'ow')""", (uid, d, metric, value))
    # Trains 1 hour at 18:00 every day (heart rate from make_inputs), walking during the workout
    workouts = [(datetime.combine(date(2026, 9, 1) + timedelta(days=k), datetime.min.time(), SYD).replace(hour=18),
                 datetime.combine(date(2026, 9, 1) + timedelta(days=k), datetime.min.time(), SYD).replace(hour=19))
                for k in range(NIGHTS)]
    # Resting awake heart rate: every day 9:00-16:50 one 70 bpm sample every 10 minutes (slight variation), on the last day it is 95 at 14:00 (tense)
    for k in range(NIGHTS):
        day = datetime.combine(date(2026, 9, 1) + timedelta(days=k), datetime.min.time(), SYD)
        for i in range(48):
            t = day + timedelta(hours=9, minutes=10 * i)
            stressed = k == NIGHTS - 1 and t.hour == 14
            inp.hr.append((t, 95.0 if stressed else 68.0 + i % 5))
    inp.hr.sort()
    client = FakeOW2(inp, workouts=workouts)
    engine.run(conn, client, uid, end=date(2026, 9, 1) + timedelta(days=NIGHTS))
    out = jobs.run(conn, client, uid, stress_days=(TODAY - date(2026, 9, 1)).days + 1)
    return uid, out


def test_jobs_write_healthspan_and_stress(conn, person):
    uid, out = person
    assert "healthspan_error" not in out and "stress_error" not in out, out
    assert out["healthspan_weeks"] > 0 and out["stress_days_with_score"] > 0
    h = views.healthspan(conn, uid)
    assert h["chrono_age"] == pytest.approx((TODAY - date(2001, 6, 15)).days / 365.2425, abs=0.01)
    assert h["body_age"] == pytest.approx(h["chrono_age"] + h["delta_years"], abs=0.06)
    keys = {c["key"] for c in h["contributors"]} | {m["key"] for m in h["insufficient"]}
    assert keys == {"sleep_duration", "sleep_regularity", "steps", "mvpa", "strength", "vo2max", "rhr", "body_comp"}
    by = {c["key"]: c for c in h["contributors"]}
    assert by["steps"]["value"] == 6000 and by["steps"]["direction"] == "older"
    assert by["body_comp"]["measure"] == "bmi" and by["vo2max"]["readings"] >= 2
    assert h["history"][-1]["as_of"] == h["as_of"]
    # Stress: 18:00-19:00 is training (excluded), nights are sleep (excluded); daytime 9-16 has 6 samples per hour
    last = date(2026, 9, 1) + timedelta(days=NIGHTS - 1)
    days = views.stress_days(conn, uid, last - timedelta(days=1), last)["days"]
    normal, tense = days
    assert [h["hour"] for h in normal["hours"]] == list(range(9, 17)) and normal["level"] == "low"
    assert 68 <= normal["baseline"]["calm_hr"] <= 75     # readings of 68-72 + make_inputs adds one 75 per hour
    peak = next(h for h in tense["hours"] if h["hour"] == 14)
    assert tense["peak_hour"] == 14 and peak["level"] == "high" and tense["high_h"] == 1
    gone = views.stress_days(conn, uid, last + timedelta(days=1), last + timedelta(days=1))["days"][0]
    assert gone["score"] is None and "no_raw_hr" in gone["flags"]


def test_coaches_monitor_and_details(conn, person):
    uid, _ = person
    d = date(2026, 9, 30)
    sc = views.strain_coach(conn, uid, d)
    assert sc["zone"] in ("green", "yellow", "red") and sc["current_strain"] is not None
    assert sc["status"] in ("below", "in_range", "above") and sc["target_low"] <= sc["target_high"]
    sl = views.sleep_coach(conn, uid, d)
    assert sl["source"] == "need" and sl["wake_time"] == "07:00" and sl["recommended_bedtime"] == sl["bedtime_for_need"]
    assert sl["need_min"] >= 480
    hm = views.health_monitor(conn, uid, d)
    rhr = next(m for m in hm["metrics"] if m["key"] == "rhr")
    assert rhr["status"] == "in_range" and rhr["low"] < rhr["value"] < rhr["high"]
    assert next(m for m in hm["metrics"] if m["key"] == "wrist_temp")["missing_reason"]
    det = views.sleep_details(conn, uid, d - timedelta(days=6), d)
    assert det["consistency_pct_7d"] == 100 and len(det["nights"]) == 7 and det["nights"][0]["awakenings"] == 1
    z = views.hr_zones(conn, uid, d, d)
    assert sum(z["days"][0]["zone_minutes"]) > 50 and z["workouts"] in (None, [])  # workouts table merged and returned as a list (the test DB has no workouts)
    v = views.vo2max(conn, uid, d)
    assert v["current"] == 44.0 and v["level"] == "average" and 40 < v["percentile"] < 45
    rep = views.report(conn, uid, "week", d)
    assert rep["current"]["recovery"]["days"] == 7 and rep["previous"]["end"] == d - timedelta(days=7)
    assert rep["current"]["training"]["error"] and "change" in rep


def test_api_and_tools_respect_shares(conn, person, monkeypatch):
    from fastapi.testclient import TestClient

    from body_engine import api
    from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool

    uid, _ = person
    other = conn.execute("INSERT INTO users (display_name) VALUES ('家人') RETURNING id").fetchone()["id"]
    t_owner, t_other = auth.create_token(conn, uid, "t", ["read"]), auth.create_token(conn, other, "t", ["read"])
    monkeypatch.setattr(api, "settings", TEST_SETTINGS)
    paths = ["healthspan", "stress", "strain-coach?date=2026-09-30", "sleep-coach?date=2026-09-30",
             "health-monitor?date=2026-09-30", "sleep/details?end=2026-09-30", "hr-zones?end=2026-09-30",
             "vo2max?end=2026-09-30", "reports?period=month&end=2026-09-30"]
    with TestClient(api.app) as http:
        own = {"Authorization": f"Bearer {t_owner}"}
        fam = {"Authorization": f"Bearer {t_other}"}
        for p in paths:
            assert http.get(f"/v1/users/me/{p}", headers=own).status_code == 200, p
            assert http.get(f"/v1/users/{uid}/{p}", headers=fam).status_code == 403, p
        res = http.get("/v1/users/me/healthspan", headers=own).json()
        assert set(res) >= {"body_age", "chrono_age", "delta_years", "pace_of_aging", "pace_raw", "window_weeks", "as_of",
                            "history", "contributors",
                            "insufficient"}
        assert http.get("/v1/users/me/reports?period=year", headers=own).status_code == 422
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, '{scores}')", (uid, other))
        assert http.get(f"/v1/users/{uid}/healthspan", headers=fam).status_code == 200
        assert http.get(f"/v1/users/{uid}/sleep/details", headers=fam).status_code == 403   # sleep details need sleep
    names = ["get_healthspan", "get_stress", "get_strain_coach", "get_sleep_coach", "get_health_monitor",
             "get_sleep_details", "get_hr_zones", "get_vo2max", "get_report"]
    assert all(n in TOOLS_BY_NAME and not TOOLS_BY_NAME[n].writes for n in names)
    r = run_tool(conn, other, "mcp", "get_healthspan", {"user": str(uid), "weeks": 4})
    assert len(r["history"]) <= 4
    with pytest.raises(PermissionError):
        run_tool(conn, other, "mcp", "get_sleep_details", {"user": str(uid), "start": None, "end": None})
    r = run_tool(conn, uid, "mcp", "get_report", {"user": "me", "period": "week", "end": "2026-09-30"})
    assert r["period"] == "week"
