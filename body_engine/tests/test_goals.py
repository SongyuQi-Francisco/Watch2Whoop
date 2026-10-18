"""Bedtime goal: cross-midnight conversion, plan generation, tonight's target and progress, rule boundaries (pure functions); table / API / assistant authorization (test DB body_test_goals)."""
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.goals import params as G, sleep as S, store
from body_engine.rules import sleep_goal
from body_engine.rules.context import Ctx

SYD = ZoneInfo("Australia/Sydney")
TONIGHT = date(2026, 10, 17)


def at(d: date, hhmm: str) -> datetime:
    return datetime.combine(d, S.parse_hhmm(hhmm), SYD)


def nights(*bedtimes, end=TONIGHT, wake_after_min=420):
    """bedtimes[-1] is last night; None = no data that night."""
    out = []
    for k, b in enumerate(reversed(bedtimes), start=1):
        if b is None:
            continue
        n = end - timedelta(days=k)
        off = S.to_off(b)
        out.append({"night": n, "bed_off": off, "wake_off": off + wake_after_min, "bedtime": b,
                    "wake": S.fmt(off + wake_after_min), "asleep_min": wake_after_min})
    return sorted(out, key=lambda x: x["night"])


def goal(start_off, target="00:00", start=TONIGHT, pace=20, step_days=3, wake=None, status="active"):
    g = {"target_bedtime": target, "target_wake": wake, "target_duration_min": None, "pace_min_per_step": pace,
         "step_days": step_days, "start_date": start, "start_bedtime_off": start_off, "status": status}
    g["plan_rows"] = S.build_plan(start_off, S.to_off(target), start, pace, step_days)
    return g


# ================= cross-midnight =================
def test_sleep_day_offsets():
    assert [S.to_off(x) for x in ("00:00", "06:30", "23:00", "11:59", "12:00", "21:15")] == [0, 390, -60, 719, -720, -165]
    assert [S.fmt(x) for x in (0, 390, -60, -720, 1440 + 30)] == ["00:00", "06:30", "23:00", "12:00", "00:30"]
    assert S.to_off("06:30") - S.to_off("00:00") == 390          # 06:30 is 6.5 hours later than 00:00
    with pytest.raises(ValueError):
        S.parse_hhmm("25")


def test_nights_from_sessions_groups_by_evening():
    sess = lambda s, e, mins, main=True: {"start_at": s, "end_at": e, "asleep_min": mins, "is_main": main}
    out = S.nights_from_sessions([
        sess(at(date(2026, 10, 17), "07:39"), at(date(2026, 10, 17), "15:01"), 441),   # falls asleep at 7am -> counts as the night of 10-16
        sess(at(date(2026, 10, 15), "23:30"), at(date(2026, 10, 16), "07:00"), 400),   # falls asleep at 23:30 -> the night of 10-15
        sess(at(date(2026, 10, 16), "01:00"), at(date(2026, 10, 16), "02:00"), 50),    # a shorter segment on the same night: ignored
        sess(at(date(2026, 10, 17), "16:00"), at(date(2026, 10, 17), "17:00"), 60, main=False),
    ], SYD)
    assert [(x["night"], x["bed_off"], x["bedtime"], x["wake"]) for x in out] == [
        (date(2026, 10, 15), -30, "23:30", "07:00"), (date(2026, 10, 16), 459, "07:39", "15:01")]
    assert out[1]["wake_off"] == 15 * 60 + 1


def test_average_across_midnight():
    ns = nights("23:30", "00:30", "23:30", "00:30")
    assert S.fmt(S.avg(ns, "bed_off")) == "00:00"            # not 12:00


def test_tonight_before_noon_is_previous_evening():
    assert store.tonight(SYD, at(date(2026, 10, 18), "03:00")) == date(2026, 10, 17)
    assert store.tonight(SYD, at(date(2026, 10, 18), "11:59")) == date(2026, 10, 17)
    assert store.tonight(SYD, at(date(2026, 10, 18), "12:00")) == date(2026, 10, 18)


# ================= plan =================
def test_build_plan_steps_to_target():
    plan = S.build_plan(425, 0, TONIGHT, 20, 3)
    assert len(plan) == 22 and plan[0] == {"from_date": TONIGHT, "bed_off": 405}
    assert plan[1]["from_date"] == TONIGHT + timedelta(days=3) and plan[-1]["bed_off"] == 0
    assert plan[-1]["from_date"] == TONIGHT + timedelta(days=63)
    assert S.build_plan(10, 0, TONIGHT, 20, 3) == [{"from_date": TONIGHT, "bed_off": 0}]
    assert S.build_plan(-60, 0, TONIGHT, 20, 3) == [{"from_date": TONIGHT, "bed_off": 0}]   # already earlier than the target
    assert S.stage_for(plan, TONIGHT - timedelta(days=1)) is None
    assert S.stage_for(plan, TONIGHT + timedelta(days=5))["bed_off"] == 385


def test_tonight_target_never_more_than_one_step_ahead_of_reality():
    g = goal(start_off=60, start=TONIGHT - timedelta(days=30))       # the plan reached 00:00 long ago
    ns = nights(*["06:30"] * 7)                                      # actually still at 06:30
    assert S.target_for(g["plan_rows"], ns, TONIGHT, 20) == 370       # 06:10, not 00:00
    ns = nights(*["00:05"] * 7)
    assert S.target_for(g["plan_rows"], ns, TONIGHT, 20) == 0         # caught up: use the plan's step
    assert S.target_for(g["plan_rows"], [], TONIGHT, 20) == 0         # no data: use the plan


# ================= progress =================
def test_progress_on_target_gap_trend_eta():
    g = goal(start_off=120, start=TONIGHT - timedelta(days=7), pace=20, step_days=3)
    # Step 1 01:40 (from 7 days ago), step 2 01:20 (4 days ago), step 3 01:00 (1 day ago); with no earlier data the target = the plan
    ns = nights("01:55", "01:56", "01:40", "01:40", "01:35", "01:30", "01:10")
    p = S.progress(g, ns, TONIGHT)
    assert p["nights_7d"] == 7 and p["avg_bedtime_7d"] == "01:38"
    assert p["trend_min_per_week"] < 0 and p["gap_min"] == 98
    assert p["eta_date"] > TONIGHT
    assert 0 < p["on_target_nights_7d"] < 7
    # Tolerance boundary: target + 15 minutes counts as met, +16 does not
    g2 = goal(start_off=20, start=TONIGHT - timedelta(days=10))
    assert S.progress(g2, nights(*["00:15"] * 7), TONIGHT)["on_target_nights_7d"] == 7
    assert S.progress(g2, nights(*["00:16"] * 7), TONIGHT)["on_target_nights_7d"] == 0
    # already reached: gap 0, eta tonight
    done = S.progress(g2, nights(*["23:50"] * 7), TONIGHT)
    assert done["gap_min"] == 0 and done["eta_date"] == TONIGHT
    # sleeping later and later: no eta
    worse = S.progress(g2, nights("02:00", "02:30", "03:00", "03:30", "04:00", "04:30", "05:00"), TONIGHT)
    assert worse["trend_min_per_week"] > 0 and worse["eta_date"] is None
    # too little data
    few = S.progress(g2, nights("02:00", "02:30"), TONIGHT)
    assert few["avg_bedtime_7d"] is None and few["trend_min_per_week"] is None and few["gap_min"] is None


def test_compute_without_goal():
    v = store.compute(None, nights(*["06:30"] * 7), TONIGHT, 480)
    assert v["target_bedtime"] is None and v["tonight_target_bedtime"] is None and v["plan"] == []
    assert v["progress"]["avg_bedtime_7d"] == "06:30" and v["progress"]["on_target_nights_7d"] is None


def test_compute_with_goal_fields():
    v = store.compute(goal(425), nights(*["07:05"] * 7), TONIGHT, 506)
    assert v["tonight_target_bedtime"] == "06:45" and v["tonight_target_wake"] == S.fmt(405 + 506)
    assert v["plan"][0] == {"from_date": TONIGHT, "bedtime": "06:45"} and v["plan_end_date"] == TONIGHT + timedelta(days=63)
    assert v["start_bedtime"] == "07:05" and v["target_duration_min"] == 506
    assert store.compute(goal(425, wake="08:00"), [], TONIGHT, 506)["tonight_target_wake"] == "08:00"
    assert store.compute(goal(425, status="paused"), [], TONIGHT, 506)["tonight_target_bedtime"] is None


# ================= rules =================
def ctx(g, ns, day=TONIGHT):
    return Ctx(user_id=1, day=day, sleep_goal=g, sleep_nights=ns, sleep_duration_min=480)


def test_rule_without_goal_or_paused():
    assert sleep_goal.evaluate(ctx(None, [])).status == "insufficient_data"
    assert sleep_goal.evaluate(ctx(goal(425, status="paused"), [])).status == "not_triggered"
    assert sleep_goal.evaluate(ctx(goal(425, start=TONIGHT + timedelta(days=1)), [])).status == "not_triggered"


def test_rule_tonight_wind_down():
    r = sleep_goal.evaluate(ctx(goal(20), nights(*["00:00"] * 7, wake_after_min=510)))   # up from 08:30
    rem = r.reminders[0]
    assert rem.title == "今晚 00:00 前睡" and rem.level == "info"
    assert rem.message.splitlines() == ["23:00 起停屏、调暗灯光", "咖啡 12:00 前", "晚饭、训练 21:00 前结束",
                                        f"08:00 起床；{G.MORNING_LIGHT}"]
    # gets up in the afternoon: 12:00 has passed -> no more drinking today
    late = sleep_goal.evaluate(ctx(goal(425), nights(*["07:05"] * 7, wake_after_min=420)))
    assert "今天别再喝咖啡" in late.reminders[0].message
    # when the target is very early (before 20:00), use 8 hours before the target
    assert S.fmt(sleep_goal._caffeine_cutoff(S.to_off("19:00"))) == "11:00"


def test_rule_deviation_boundaries():
    g = goal(20, start=TONIGHT - timedelta(days=10))           # the plan is already 00:00
    subj = lambda ns: {x.subject for x in sleep_goal.evaluate(ctx(g, ns)).reminders}
    assert "deviation" in subj(nights("00:00", "00:00", "00:00", "00:00", "00:31", "00:31", "00:31"))
    assert "deviation" not in subj(nights("00:00", "00:00", "00:00", "00:00", "00:30", "00:31", "00:31"))   # 30 minutes does not count
    assert "deviation" not in subj(nights("00:00", "00:00", "00:00", "00:00", "00:31", None, "00:31"))     # one night missing
    assert "deviation" not in subj(nights("00:00", "00:00", "00:00", "00:00", "00:00", "00:31", "00:31"))  # only 2 nights


def test_rule_stage_and_final_encouragement():
    g = goal(425, start=TONIGHT - timedelta(days=6))            # tonight's step 3 06:05
    rems = {x.subject: x for x in sleep_goal.evaluate(ctx(g, nights(*["06:15"] * 7))).reminders}
    assert "stage:06:05" in rems and rems["stage:06:05"].sticky
    assert "stage:06:05" not in {x.subject for x in sleep_goal.evaluate(ctx(g, nights(*["06:25"] * 7))).reminders}
    # The first step does not count as "achieved" (the starting point is already there)
    g0 = goal(425, start=TONIGHT)
    assert not any(x.subject.startswith("stage") for x in sleep_goal.evaluate(ctx(g0, nights(*["06:45"] * 7))).reminders)
    gf = goal(20, start=TONIGHT - timedelta(days=10))
    rems = {x.subject for x in sleep_goal.evaluate(ctx(gf, nights(*["00:10"] * 7))).reminders}
    assert "final:00:00" in rems and not any(s.startswith("stage") for s in rems)


# ================= table / API / assistant =================
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_GOALS", "body_test_goals")
CSRF = {"X-Body-Web": "1"}


@pytest.fixture(scope="module")
def dsn():
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


@pytest.fixture(scope="module")
def world(dsn):
    """A fell asleep at 06:30 for 7 nights; A shares sleep with B; C shares nothing."""
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"]
               for n in ("A", "B", "C")}
        tok = {"A": auth.create_token(conn, ids["A"], "t", ["read", "write"]),
               "A_ro": auth.create_token(conn, ids["A"], "t", ["read"]),
               "B": auth.create_token(conn, ids["B"], "t", ["read", "write"]),
               "C": auth.create_token(conn, ids["C"], "t", ["read", "write"])}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["sleep"]))
        today = store.tonight(SYD)
        for k in range(1, 8):
            n = today - timedelta(days=k)
            start = datetime.combine(n + timedelta(days=1), time(6, 30), SYD)
            conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, is_main, source)
                            VALUES (%s, %s, %s, %s, 420, TRUE, 'ow')""",
                         (ids["A"], start.date(), start, start + timedelta(hours=7)))
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def h(t):
    return {"Authorization": f"Bearer {t}"}


def test_api_get_without_goal_then_put(client, world):
    ids, tok = world
    r = client.get("/v1/users/me/goals/sleep", headers=h(tok["A"]))
    assert r.status_code == 200 and r.json()["target_bedtime"] is None
    assert r.json()["progress"]["avg_bedtime_7d"] == "06:30" and r.json()["plan"] == []
    r = client.put("/v1/users/me/goals/sleep", json={"target_bedtime": "00:00"}, headers=h(tok["A"]))
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["start_bedtime"] == "06:30" and v["tonight_target_bedtime"] == "06:10" and v["pace_min_per_step"] == 20
    assert v["plan"][-1]["bedtime"] == "00:00" and len(v["plan"]) == 20 and v["progress"]["gap_min"] == 390
    # Changing the pace: keep the old values other than the target
    v = client.put("/v1/users/me/goals/sleep", json={"target_bedtime": "00:30", "pace_min_per_step": 30, "step_days": 2},
                   headers=h(tok["A"])).json()
    assert (v["target_bedtime"], v["pace_min_per_step"], v["step_days"], v["tonight_target_bedtime"]) == ("00:30", 30, 2, "06:00")
    v = client.put("/v1/users/me/goals/sleep", json={"target_bedtime": "00:00"}, headers=h(tok["A"])).json()
    assert (v["pace_min_per_step"], v["step_days"]) == (30, 2)


def test_api_auth(client, world):
    ids, tok = world
    a = ids["A"]
    body = {"target_bedtime": "00:00"}
    assert client.get(f"/v1/users/{a}/goals/sleep", headers=h(tok["B"])).status_code == 200
    assert client.get(f"/v1/users/{a}/goals/sleep", headers=h(tok["C"])).status_code == 403
    assert client.put(f"/v1/users/{a}/goals/sleep", json=body, headers=h(tok["B"])).status_code == 403
    assert client.put("/v1/users/me/goals/sleep", json=body, headers=h(tok["A_ro"])).status_code == 403
    for bad in ({"target_bedtime": "24:00"}, {"target_bedtime": "0:0"}, {}, {"target_bedtime": "00:00", "pace_min_per_step": 90},
                {"target_bedtime": "00:00", "extra": 1}):
        assert client.put("/v1/users/me/goals/sleep", json=bad, headers=h(tok["A"])).status_code == 422, bad
    assert client.get("/v1/users/me/goals/sleep").status_code == 401


def test_api_cookie_put_needs_csrf_header(client, world):
    ids, tok = world
    assert client.post("/v1/web/login", json={"token": tok["A"]}).status_code == 200
    assert client.get("/v1/users/me/goals/sleep").status_code == 200
    assert client.put("/v1/users/me/goals/sleep", json={"target_bedtime": "00:00"}).status_code == 403
    assert client.put("/v1/users/me/goals/sleep", json={"target_bedtime": "00:00"}, headers=CSRF).status_code == 200
    client.post("/v1/web/logout", headers=CSRF)
    client.cookies.clear()


def test_assistant_tools(dsn, world):
    ids, _ = world
    assert TOOLS_BY_NAME["set_sleep_goal"].writes and not TOOLS_BY_NAME["get_sleep_goal"].writes
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        v = run_tool(conn, ids["C"], "mcp", "set_sleep_goal", {"target_bedtime": "23:30", "target_wake": "07:30",
                                                               "pace_min_per_step": None, "step_days": None})
        assert v["target_bedtime"] == "23:30" and v["tonight_target_wake"] == "07:30" and v["start_bedtime"] == "23:30"
        got = run_tool(conn, ids["B"], "mcp", "get_sleep_goal", {"user": str(ids["A"]), "night": None})
        assert got["target_bedtime"] == "00:00"
        with pytest.raises(PermissionError):
            run_tool(conn, ids["C"], "mcp", "get_sleep_goal", {"user": str(ids["A"]), "night": None})
        with pytest.raises(ValueError):
            run_tool(conn, ids["C"], "mcp", "set_sleep_goal", {"target_bedtime": "7pm", "target_wake": None,
                                                               "pace_min_per_step": None, "step_days": None})


def test_evaluate_rules_uses_goal_from_db(dsn, world):
    from body_engine.rules import engine
    ids, _ = world
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        night = store.tonight(SYD)
        res = engine.run(conn, ids["A"], night)          # A has no SparkyFitness connected: other rules lack data, the bedtime goal works as usual
        st = {r["rule_key"]: r["status"] for r in res["rules"]}
        assert st["sleep_goal"] == "triggered"
        row = conn.execute("SELECT title, evidence FROM reminders WHERE user_id = %s AND date = %s AND rule_key = 'sleep_goal'",
                           (ids["A"], night)).fetchone()
        assert row["title"] == f"今晚 {row['evidence']['tonight_target']} 前睡"
        assert engine.run(conn, ids["A"], night)["unchanged"] >= 1
