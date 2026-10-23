"""Scene context tools (assistant/scenes.py, issue #100): get_training_context and get_week_review_context with data,
without data, and across users (sections by shares, like get_briefing). Uses its own test database BODY_TEST_DB_NAME
(default body_test_scenes), dropped and recreated on every run; skipped without Postgres. SparkyFitness is not configured:
the sections that need it report that in place and in `needs`, which is what a user without SparkyFitness sees.
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.ai import params as P
from body_engine.assistant import compact
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.training import plans

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_scenes")
TZ = ZoneInfo("Australia/Sydney")


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
    """A: three weeks of scores and sleep, workouts, a banned exercise, a 4-day plan, equipment on the profile card,
    journal entries and an event this week. B: nothing at all. C: A shares profile + scores with C. D: no share."""
    today = datetime.now(TZ).date()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, birth_date, sex, height_cm, timezone)
                                  VALUES (%s, '1990-01-01', 'male', 180, 'Australia/Sydney') RETURNING id""",
                               (n,)).fetchone()["id"] for n in "ABCD"}
        a = ids["A"]
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (a, ids["C"], ["profile", "scores"]))
        for k in range(21):
            d = today - timedelta(days=k)
            conn.execute("""INSERT INTO daily_scores (user_id, date, recovery, sleep_actual_min, sleep_debt_min, hrv_night_ln,
                                rhr_night, strain, trimp, ctl, atl, tsb, algo_version)
                            VALUES (%s, %s, %s, %s, 30, 3.8, 52, %s, 60, 40, 45, -5, 'test')""",
                         (a, d, 70 if k < 7 else 50, 450 if k < 7 else 400, 12 + k % 4))
        for k in range(0, 14, 2):
            d = today - timedelta(days=k)
            start = datetime.combine(d, datetime.min.time(), TZ) + timedelta(hours=18)
            conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at,
                                duration_min, active_kcal, total_kcal, avg_hr, max_hr, trimp)
                            VALUES (%s, 'shortcut', %s, 'basketball', '篮球', %s, %s, %s, 60, 450, 520, 135, 175, 90)""",
                         (a, f"w{k}", d, start, start + timedelta(minutes=60)))
        conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, reason)
                        VALUES (%s, 'upright_row', '直立划船', 'banned', '肩峰撞击')""", (a,))
        conn.execute("""INSERT INTO assistant_profile (user_id, key, label, value, source)
                        VALUES (%s, %s, '器械', '家里：哑铃 2–24kg、可调凳、引体杆', 'user')""", (a, P.EQUIPMENT_CARD_KEY))
        conn.execute("INSERT INTO journal_items (user_id, key, label_zh, kind) VALUES (%s, 'alcohol', '喝酒', 'bool')", (a,))
        for k, v in ((1, 1), (2, 0), (3, 1)):
            conn.execute("INSERT INTO journal_entries (user_id, date, key, value, note) VALUES (%s, %s, 'alcohol', %s, %s)",
                         (a, today - timedelta(days=k), v, "两瓶啤酒" if v else None))
        conn.execute("""INSERT INTO events (user_id, occurred_at, category, body_part, severity, title)
                        VALUES (%s, %s, 'pain', '膝', 2, '打球后膝盖疼')""",
                     (a, datetime.combine(today - timedelta(days=2), datetime.min.time(), TZ) + timedelta(hours=20)))
        conn.commit()
        plans.save_plan(conn, a, split_type="4_day_upper_lower", sync_sparky=False)
        # A plan saved on a Friday / Saturday / Sunday starts today (issue #65): the rest of that week holds no session, and the
        # test below looks at the current week. Let the plan have started two weeks ago so it passes on every weekday.
        conn.execute("UPDATE training_plans SET start_date = start_date - 14 WHERE user_id = %s AND active", (a,))
        conn.commit()
    return ids


def test_training_context_with_data(conn, world):
    ctx = run_tool(conn, world["A"], "test", "get_training_context", {})
    plan = ctx["plan"]
    assert plan["plan"]["split_type"] == "4_day_upper_lower" and len(plan["days"]) == 7
    assert plan["training_days"] and all(1 <= d <= 7 for d in plan["training_days"])
    assert any(d.get("sessions") for d in plan["days"]) and plan["equipment_in_plan"]
    assert ctx["readiness"]["tier"] == "green" and ctx["readiness"]["load"]["tsb"] == -5
    assert ctx["exercises"]["banned"] == [{"name": "直立划船", "reason": "肩峰撞击"}]
    assert ctx["recent"]["summary"]["sessions"] >= 7 and len(ctx["recent"]["days"]) <= P.TRAINING_RECENT_DAYS
    assert "哑铃" in ctx["equipment"] and ctx["rules"]
    assert not any("周计划" in n or "器械" in n for n in ctx["needs"])
    assert len(compact.dumps(ctx)) < P.READ_RESULT_MAX_CHARS


def test_training_context_without_data(conn, world):
    ctx = run_tool(conn, world["B"], "test", "get_training_context", {})
    assert ctx["plan"]["plan"] is None and ctx["readiness"]["tier"] == "unknown"
    assert ctx["exercises"] == {"banned": [], "caution": []} and ctx["equipment"] is None
    needs = " ".join(ctx["needs"])
    assert "还没有周计划" in needs and "器械" in needs and "恢复分" in needs


def test_week_review_with_data(conn, world):
    ctx = run_tool(conn, world["A"], "test", "get_week_review_context", {})
    rec = ctx["scores"]["recovery"]
    assert rec["week"]["days"] == 7 and rec["week"]["avg"] == 70 and rec["vs_previous"]["avg"] == 20   # 70 vs 50
    assert ctx["scores"]["sleep"]["week"]["avg_actual_min"] == 450 and ctx["previous"]["end"] == ctx["start"] - timedelta(days=1)
    assert ctx["training"]["week"]["workouts"] == 4 and ctx["training"]["week"]["by_type"] == {"篮球": 4}
    assert "error" in ctx["nutrition"]["week"] and "error" in ctx["weight"]                # no SparkyFitness here
    alcohol = ctx["journal"]["items"][0]
    assert alcohol == {"item": "喝酒", "logged_days": 3, "yes_days": 2, "notes": ["两瓶啤酒", "两瓶啤酒"]}
    assert ctx["events"] == [{"date": f"{ctx['end'] - timedelta(days=2):%m-%d}", "title": "打球后膝盖疼", "body_part": "膝",
                              "severity": 2}]
    needs = " ".join(ctx["needs"])
    assert "饮食记录读不到" in needs and "体重读不到" in needs and "恢复分" not in needs
    assert ctx["rules"] and len(compact.dumps(ctx)) < P.READ_RESULT_MAX_CHARS


def test_week_review_without_data_and_other_end(conn, world):
    ctx = run_tool(conn, world["B"], "test", "get_week_review_context", {})
    assert ctx["scores"]["recovery"]["week"]["days"] == 0 and ctx["journal"]["items"] == [] and ctx["events"] == []
    assert "只有 0 天恢复分" in " ".join(ctx["needs"])
    last = run_tool(conn, world["A"], "test", "get_week_review_context", {"end": (ctx["end"] - timedelta(days=7)).isoformat()})
    assert last["scores"]["recovery"]["week"]["avg"] == 50 and last["events"] == []


def test_cross_user_by_shares(conn, world):
    a, c, d = world["A"], world["C"], world["D"]
    for name in ("get_training_context", "get_week_review_context"):
        with pytest.raises(PermissionError):
            run_tool(conn, d, "test", name, {"user": str(a)})                  # no share at all
        with pytest.raises(PermissionError):
            run_tool(conn, a, "test", name, {"user": str(world["B"])})         # B shares nothing with A
    t = run_tool(conn, c, "test", "get_training_context", {"user": str(a)})   # profile + scores only
    assert set(t["hidden"]) == {"plan", "exercises", "recent"} and "plan" not in t and "equipment" not in t
    assert t["readiness"]["tier"] == "green" and "直立划船" not in compact.dumps(t)
    r = run_tool(conn, c, "test", "get_week_review_context", {"user": str(a)})
    assert set(r["hidden"]) == {"training", "nutrition", "weight", "journal", "events"}
    assert r["scores"]["recovery"]["week"]["days"] == 7 and "膝盖" not in compact.dumps(r) and "啤酒" not in compact.dumps(r)
    # issue #111: bedtime / wake time / regularity are sleep data, not part of a scores-only viewer's sleep block
    timing = {"avg_bedtime", "avg_wake", "consistency_pct"}
    assert not timing & set(r["scores"]["sleep"]["week"]) and not timing & set(r["scores"]["sleep"]["vs_previous"] or {})
    own = run_tool(conn, a, "test", "get_week_review_context", {})
    assert {"avg_bedtime", "avg_wake", "consistency_pct"} <= set(own["scores"]["sleep"]["week"])
