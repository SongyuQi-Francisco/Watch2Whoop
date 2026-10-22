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
    conn.execute("UPDATE users SET sex = 'male', height_cm = 180 WHERE id = %s", (uid,))
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
    # v1.2 (production since 2026-10-23): steps, exercise and strength are one activity domain
    assert keys - {"strength"} == {"sleep_duration", "sleep_regularity", "activity", "vo2max", "rhr", "body_comp"}
    by = {c["key"]: c for c in h["contributors"]}
    parts = {p["key"]: p for p in by["activity"]["parts"]}
    assert parts["steps"]["value"] == 6000 and parts["steps"]["years"] > 0 and h["algo_version"].startswith("hs-v1.2")
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


def test_report_blocks_follow_each_scope(conn, person, monkeypatch):
    """Issue #111: a report read by someone else carries only the blocks their shares cover, the rest named in `hidden`;
    the owner and a viewer with every scope get the same report. Same through the API, the AI tool and circle grants."""
    from fastapi.testclient import TestClient

    from body_engine import api
    from body_engine.assistant.tools import run_tool
    from body_engine.rollout import flags

    uid, _ = person
    viewer = conn.execute("INSERT INTO users (display_name) VALUES ('朋友') RETURNING id").fetchone()["id"]
    d = date(2026, 9, 30)

    def share(*scopes, circle=()):
        conn.execute("""INSERT INTO shares (owner_id, viewer_id, scopes, circle_scopes) VALUES (%s, %s, %s, %s)
                        ON CONFLICT (owner_id, viewer_id) DO UPDATE SET scopes = EXCLUDED.scopes,
                        circle_scopes = EXCLUDED.circle_scopes""", (uid, viewer, list(scopes), list(circle)))

    own = views.report(conn, uid, "week", d)
    assert own == views.report(conn, uid, "week", d, viewer_id=uid) and "hidden" not in own
    assert own["current"]["sleep"]["avg_bedtime"] and own["current"]["sleep"]["consistency_pct"] is not None
    assert {"training", "weight", "nutrition"} <= set(own["current"]) and "journal_findings" in own

    share("scores")
    r = views.report(conn, uid, "week", d, viewer_id=viewer)
    assert r["hidden"] == ["sleep", "training", "weight", "nutrition", "journal"]
    for part in (r["current"], r["previous_values"], r["change"]):
        assert not {"training", "weight", "nutrition"} & set(part)
    assert not set(r["current"]["sleep"]) & {"avg_bedtime", "avg_wake", "consistency_pct"}
    assert "consistency_pct" not in r["previous_values"]["sleep"] and "consistency_pct" not in r["change"]["sleep"]
    assert "journal_findings" not in r
    assert r["current"]["recovery"] == own["current"]["recovery"] and r["current"]["load"] == own["current"]["load"]
    assert r["current"]["sleep"]["avg_actual_min"] == own["current"]["sleep"]["avg_actual_min"]   # in the scores row

    share("scores", "sleep")
    r = views.report(conn, uid, "week", d, viewer_id=viewer)
    assert r["hidden"] == ["training", "weight", "nutrition", "journal"]
    assert r["current"]["sleep"] == own["current"]["sleep"] and r["change"]["sleep"] == own["change"]["sleep"]
    assert not {"training", "weight", "nutrition"} & set(r["current"])

    share("scores", "nutrition_summary")
    r = views.report(conn, uid, "week", d, viewer_id=viewer)
    assert "nutrition" in r["hidden"] and set(r["current"]["nutrition"]) <= {
        "days_logged", "calories_on_target_days", "calories_judged_days", "protein_on_target_days",
        "protein_judged_days", "error"}

    share("scores", "sleep", "training", "weight", "nutrition", "journal")
    assert views.report(conn, uid, "week", d, viewer_id=viewer) == own

    # circle grants count like hand-made ones while the Friends flag is on for both
    flags.set_flag(conn, "friends", "all", actor="test")
    share(circle=("scores", "training"))
    r = views.report(conn, uid, "week", d, viewer_id=viewer)
    assert r["hidden"] == ["sleep", "weight", "nutrition", "journal"] and "training" in r["current"]
    flags.set_flag(conn, "friends", "off", actor="test")

    # the HTTP endpoint and the AI tool pass the viewer through
    share("scores")
    tok = auth.create_token(conn, viewer, "t", ["read"])
    monkeypatch.setattr(api, "settings", TEST_SETTINGS)
    with TestClient(api.app) as http:
        res = http.get(f"/v1/users/{uid}/reports?period=week&end=2026-09-30",
                       headers={"Authorization": f"Bearer {tok}"}).json()
    assert res["hidden"] == ["sleep", "training", "weight", "nutrition", "journal"]
    assert "training" not in res["current"] and "avg_bedtime" not in res["current"]["sleep"]
    r = run_tool(conn, viewer, "mcp", "get_report", {"user": str(uid), "period": "week", "end": "2026-09-30"})
    assert r["hidden"] == ["sleep", "training", "weight", "nutrition", "journal"] and "weight" not in r["current"]
    r = run_tool(conn, uid, "mcp", "get_report", {"user": "me", "period": "week", "end": "2026-09-30"})
    assert "hidden" not in r and "weight" in r["current"]


def test_other_aggregates_follow_each_scope(conn, person, monkeypatch):
    """Issue #111, the same check on the other aggregates: a scores-only viewer gets no sleep-session details, workout
    lists, weight / metrics behind body age, or status cards built from data they may not see. The owner is unchanged."""
    from body_engine.assistant import status
    from body_engine.assistant.tools import run_tool
    from body_engine.cardio import views as cardio_views
    from body_engine.explain import personal
    from body_engine.training import plans
    from body_engine.trends import series

    uid, _ = person
    viewer = conn.execute("INSERT INTO users (display_name) VALUES ('朋友') RETURNING id").fetchone()["id"]
    d = date(2026, 9, 30)

    def share(*scopes):
        conn.execute("""INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)
                        ON CONFLICT (owner_id, viewer_id) DO UPDATE SET scopes = EXCLUDED.scopes""",
                     (uid, viewer, list(scopes)))

    share("scores", "memory")
    # scores?details: the night's bedtime / stages are sleep data
    args = {"user": str(uid), "start": "2026-09-29", "end": "2026-09-30", "details": True}
    assert all("sleep" in r["details"] for r in run_tool(conn, uid, "mcp", "get_scores", {**args, "user": "me"}))
    assert not any("sleep" in r["details"] for r in run_tool(conn, viewer, "mcp", "get_scores", args))
    # heart-rate zones without the workout list, cardio without per-workout recovery
    z = views.hr_zones(conn, uid, d, d, viewer_id=viewer)
    assert "workouts" not in z and z["hidden"] == ["training"] and z["days"] == views.hr_zones(conn, uid, d, d)["days"]
    c = cardio_views.cardio(conn, uid, None, d, viewer_id=viewer)
    assert "hrr" not in c and c["hidden"] == ["training"] and "hrr" in cardio_views.cardio(conn, uid, None, d)
    # sleep coach: tonight's need only
    sc = views.sleep_coach(conn, uid, d, viewer_id=viewer)
    assert sc["hidden"] == ["sleep"] and sc["need_min"] == views.sleep_coach(conn, uid, d)["need_min"]
    assert "wake_time" not in sc and "recommended_bedtime" not in sc
    # body age: items behind other scopes are left out (steps / VO2max / weight / regularity / strength)
    own, h = views.healthspan(conn, uid), views.healthspan(conn, uid, viewer_id=viewer)
    assert h["body_age"] == own["body_age"] and h["hidden"] == ["metrics", "sleep", "training", "weight"]
    keys = {i["key"] for i in h["contributors"] + h["insufficient"]}
    assert keys <= {"sleep_duration"} and "activity" in {i["key"] for i in own["contributors"]}   # activity: metrics + training
    # trends: regularity, deep + REM and wake-ups are sleep-session data
    tz = views.user_tz(conn, uid)
    t = series.build(conn, {"id": viewer}, uid, d, 7, d, tz)
    assert not {"sleep_consistency", "awakenings", "restorative_hours", "sleep_perf_whoop"} & set(t["series"])
    assert "sleep_hours" in t["series"] and "sleep_perf_whoop" not in t["status"]
    assert "sleep_consistency" in series.build(conn, {"id": uid}, uid, d, 7, d, tz)["series"]
    # status cards (memory): an auto card needs its own data's scope, the butler's own cards need memory only
    for key, label, group, source in (("weight", "体重", "身体", "auto"), ("bedtime", "入睡时间", "作息", "auto"),
                                      ("recovery", "恢复", "身体", "auto"), ("phase", "阶段", "目标", "assistant")):
        conn.execute("""INSERT INTO assistant_status (user_id, key, label, value, tone, "group", source, sort)
                        VALUES (%s, %s, %s, '1', 'neutral', %s, %s, 1)""", (uid, key, label, group, source))
    assert {i["key"] for i in status.list_status(conn, uid)} == {"weight", "bedtime", "recovery", "phase"}
    assert {i["key"] for i in status.list_status(conn, uid, viewer)} == {"recovery", "phase"}
    assert personal.explain_status(conn, viewer, uid, "weight", d) is None
    assert personal.explain_status(conn, uid, uid, "weight", d) is not None
    r = run_tool(conn, viewer, "mcp", "get_memory", {"user": str(uid), "detail": False})
    assert {s["key"] for s in r["status"]} == {"recovery", "phase"}
    share("scores", "memory", "weight", "sleep")
    assert {i["key"] for i in status.list_status(conn, uid, viewer)} == {"weight", "bedtime", "recovery", "phase"}
    # a training-only viewer planning for someone gets no readiness (recovery / sleep debt)
    monkeypatch.setattr(plans, "propose_plan", lambda conn, **kw: {"readiness": {"recovery": 80}})
    share("training")
    args = {"user": str(uid), "split_type": "4_day_upper_lower"}
    assert run_tool(conn, viewer, "mcp", "propose_plan", args)["readiness"] is None
    assert run_tool(conn, uid, "mcp", "propose_plan", {**args, "user": "me"})["readiness"] == {"recovery": 80}
