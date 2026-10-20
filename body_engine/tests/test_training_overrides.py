"""Day-level changes to the weekly training plan (training/overrides.py, migration 230):
- resolving what the user asked for (plan session name / weekday / rest / free activity / ambiguous)
- reschedule_day + re-planning the rest of the week (each plan session once, no muscle group two days in a row), swap_days,
  undo_day_changes, save_plan clearing old changes
- both plan sources: Body Engine training_plans and the SparkyFitness weekly plan (rules Ctx.planned_for, Today page views)
- tools: writes only for oneself, family with the training share see the changed week; HTTP /training/week shows the change.
Tests use the week after this one (Monday to Sunday), so they do not depend on today's weekday.
"""
import os
from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rules import context as rules_context
from body_engine.rules.context import Ctx
from body_engine.training import overrides as OV, plans
from body_engine.web import views

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_TRAINING_OVERRIDES", "body_test_training_overrides")
CSRF = {"X-Body-Web": "1"}


# ---------------------------------------------------------------- no database
def _model() -> OV.WeekModel:
    W = OV.Weekday
    return OV.WeekModel("body_engine", "4 练", {
        1: W(1, ["上肢 A（拉为主）"], {"back", "arms"}),
        2: W(2, ["下肢 A（深蹲为主）"], {"legs"}),
        4: W(4, ["上肢 B（推为主）"], {"chest", "shoulders", "arms"}),
        5: W(5, ["下肢 B（髋铰链为主）"], {"legs"}),
    })


def test_resolve_session_by_name_weekday_rest_and_free_activity():
    m, tue = _model(), date(2026, 10, 20)
    assert OV.resolve_session(m, "下肢B（髋铰链为主）", tue) == ([{"dow": 5}], "name")
    assert OV.resolve_session(m, "下肢 B", tue) == ([{"dow": 5}], "name")
    assert OV.resolve_session(m, "周四的", tue) == ([{"dow": 4}], "weekday")
    assert OV.resolve_session(m, "周六", tue) == ([], "weekday")          # the plan rests on Saturday
    assert OV.resolve_session(m, "休息", tue) == ([], "rest")
    assert OV.resolve_session(m, "篮球", tue) == ([{"label": "篮球"}], "custom")
    with pytest.raises(ValueError, match="多个训练"):
        OV.resolve_session(m, "下肢", tue)


def test_day_change_tools_registered():
    for name in ("reschedule_day", "swap_days", "replan_week", "undo_day_changes"):
        assert TOOLS_BY_NAME[name].writes and TOOLS_BY_NAME[name].scope == "training"
        assert "user" not in TOOLS_BY_NAME[name].input_schema["properties"]   # writes are only for oneself
    from body_engine.ai.chat import ALWAYS_CONFIRM
    assert "undo_day_changes" in ALWAYS_CONFIRM and not {"reschedule_day", "swap_days", "replan_week"} & ALWAYS_CONFIRM


def test_ctx_planned_for_applies_day_changes():
    mon = date(2026, 10, 26)
    ctx = Ctx(user_id=1, day=mon)
    ctx.presets = {1: {"id": 1, "name": "推", "exercises": []}, 3: {"id": 3, "name": "腿", "exercises": []}}
    ctx.plans = [{"id": 7, "name": "PPL", "start_date": "2026-01-01", "end_date": None, "is_active": True, "schedule_type": "weekly",
                  "assignments": [{"day_of_week": 1, "sort_order": 0, "workout_preset_id": 1},
                                  {"day_of_week": 5, "sort_order": 0, "workout_preset_id": 3}]}]
    assert [i["preset"]["name"] for i in ctx.planned_for(mon)] == ["推"]
    ctx.overrides = {mon: {"sessions": [{"dow": 5}, {"label": "篮球"}], "source": "user"},
                     mon + timedelta(days=4): {"sessions": [], "source": "replan"}}
    items = ctx.planned_for(mon)
    assert items[0]["preset"]["name"] == "腿" and items[1] == {"kind": "label", "name": "篮球"}
    assert ctx.planned_for(mon + timedelta(days=4)) == []
    assert ctx.planned_exercises(mon) == []          # labels carry no exercises


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


@pytest.fixture(scope="module")
def world(dsn):
    """A: Body Engine 4-day upper / lower plan; B: A shares training with B; C: no share; D: SparkyFitness weekly plan only."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, role) VALUES (%s, %s) RETURNING id",
                               (n, "owner" if n == "A" else "member")).fetchone()["id"] for n in "ABCD"}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in "ABCD"}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["training"]))
        plans.save_plan(conn, ids["A"], split_type="4_day_upper_lower", sync_sparky=False)
        mon = plans._user_today(conn, ids["A"])
        mon += timedelta(days=7 - mon.weekday())   # next Monday
    return ids, tok, mon


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _names(wk: dict) -> list[str]:
    return [" + ".join(p["name"] for p in d["planned"]) or "休息" for d in wk["days"]]


def test_reschedule_today_and_replan_rest_of_week(conn, world):
    ids, _tok, mon = world
    a = ids["A"]
    base = plans.get_week_plan(conn, a, a, mon)
    assert _names(base)[:5] == ["上肢 A（拉为主）", "下肢 A（深蹲为主）", "肩部康复 / 灵活性 routine", "上肢 B（推为主）",
                                "下肢 B（髋铰链为主）"]

    res = OV.reschedule_day(conn, a, mon, "下肢 B", reason="今天想练腿")
    assert res["before"] == "上肢 A（拉为主）" and res["after"] == "下肢 B（髋铰链为主）" and res["matched"] == "name"
    wk = plans.get_week_plan(conn, a, a, mon)
    names = _names(wk)
    assert names[0] == "下肢 B（髋铰链为主）"
    assert wk["days"][0]["change"] == {"from": ["上肢 A（拉为主）"], "source": "user", "reason": "今天想练腿"}
    # every plan session still once this week, legs not again on Tuesday, no muscle group two days in a row
    assert sorted(n for n in names if n != "休息") == sorted(n for n in _names(base) if n != "休息")
    assert "下肢" not in names[1]
    assert res["rest_of_week"]["warnings"] == [] and res["rest_of_week"]["changes"] and "skipped" not in res["rest_of_week"]
    assert {k: v["direct_sets"] for k, v in wk["volume_by_group"].items()} == \
        {k: v["direct_sets"] for k, v in base["volume_by_group"].items()}
    assert {d["change"]["source"] for d in wk["days"][1:] if d["change"]} <= {"replan"}

    # the week strip and the Today page show the change
    td = views.training_today(conn, a, a, mon)
    assert td["planned"][0]["name"] == "下肢 B（髋铰链为主）" and td["change"]["from"] == ["上肢 A（拉为主）"]


def test_swap_rest_custom_and_undo(conn, world):
    ids, _tok, mon = world
    a = ids["A"]
    sat, sun = mon + timedelta(days=5), mon + timedelta(days=6)
    before = _names(plans.get_week_plan(conn, a, a, mon))
    sw = OV.swap_days(conn, a, mon + timedelta(days=1), mon + timedelta(days=2))
    after = _names(plans.get_week_plan(conn, a, a, mon))
    assert after[1] == before[2] and after[2] == before[1] and len(sw["swapped"]) == 2

    r = OV.reschedule_day(conn, a, sat, "篮球", replan=False)
    assert r["matched"] == "custom" and r["plan_sessions"] and r["after"] == "篮球"
    assert _names(plans.get_week_plan(conn, a, a, mon))[5] == "篮球"
    with pytest.raises(ValueError, match="多个训练"):
        OV.reschedule_day(conn, a, sun, "下肢")
    with pytest.raises(ValueError, match="只能调整"):
        OV.reschedule_day(conn, a, mon + timedelta(days=60), "休息")

    # the plan's own content again = no row
    OV.reschedule_day(conn, a, sat, "休息", replan=False)
    assert conn.execute("SELECT 1 FROM training_day_overrides WHERE user_id = %s AND date = %s", (a, sat)).fetchone() is None

    undone = OV.undo_day_changes(conn, a, mon, sun)
    assert undone["restored"] and _names(plans.get_week_plan(conn, a, a, mon)) == _names(plans.get_week_plan(conn, a, a, mon - timedelta(days=7)))


def test_replan_preview_and_recovery_red(conn, world):
    ids, _tok, mon = world
    a = ids["A"]
    OV.reschedule_day(conn, a, mon, "休息", replan=False)
    preview = OV.replan_week(conn, a, mon, apply=False)
    assert preview["applied"] is False and preview["changes"]
    assert conn.execute("SELECT count(*) AS n FROM training_day_overrides WHERE user_id = %s AND date > %s",
                        (a, mon)).fetchone()["n"] == 0                     # preview writes nothing
    sched = [d["sessions"] for d in preview["schedule"]]
    assert sched[0] == "休息" and "上肢 A（拉为主）" in sched                # the missed session moves into the week
    OV.undo_day_changes(conn, a, mon, mon + timedelta(days=6))


def test_save_plan_clears_future_day_changes(conn, world):
    ids, _tok, mon = world
    a = ids["A"]
    OV.reschedule_day(conn, a, mon, "下肢 A", replan=False)
    saved = plans.save_plan(conn, a, split_type="4_day_upper_lower", sync_sparky=False)
    assert saved["day_changes_cleared"] == 1
    assert conn.execute("SELECT 1 FROM training_day_overrides WHERE user_id = %s", (a,)).fetchone() is None


def test_tools_write_only_for_oneself_and_share_reads(conn, world, client):
    ids, tok, mon = world
    a, b, c = ids["A"], ids["B"], ids["C"]
    res = run_tool(conn, a, "butler", "reschedule_day", {"date": mon.isoformat(), "session": "上肢 B", "reason": None})
    assert res["after"] == "上肢 B（推为主）" and "rest_of_week" in res
    with pytest.raises(PermissionError):
        run_tool(conn, b, "butler", "reschedule_day", {"user": str(a), "date": mon.isoformat(), "session": "休息", "reason": None})
    # tools without a user argument always write the caller's own week: B's call changes nothing of A's
    run_tool(conn, b, "butler", "swap_days", {"date_a": mon.isoformat(), "date_b": (mon + timedelta(days=1)).isoformat()})
    assert _names(plans.get_week_plan(conn, a, a, mon))[0] == "上肢 B（推为主）"
    # B (training share) sees the changed week, C does not
    wk_b = run_tool(conn, b, "butler", "get_week_plan", {"user": str(a), "date": mon.isoformat(), "detail": False})
    assert wk_b["days"][0]["change"]["from"] == ["上肢 A（拉为主）"]
    with pytest.raises(PermissionError):
        run_tool(conn, c, "butler", "get_week_plan", {"user": str(a), "date": mon.isoformat(), "detail": False})
    conn.commit()

    r = client.get(f"/v1/users/{a}/training/week?date={mon}", headers=H(tok["A"])).json()
    assert r["days"][0]["planned"][0]["name"] == "上肢 B（推为主）" and r["days"][0]["change"]["source"] == "user"
    assert client.get(f"/v1/users/{a}/training/week?date={mon}", headers=H(tok["B"])).json()["days"][0]["change"]
    assert client.get(f"/v1/users/{a}/training/week?date={mon}", headers=H(tok["C"])).status_code == 403
    OV.undo_day_changes(conn, a, mon, mon + timedelta(days=6))
    OV.undo_day_changes(conn, b, mon, mon + timedelta(days=6))
    conn.commit()


class FakeSparky:
    """SparkyFitness with a push / pull / legs weekly plan (Monday / Wednesday / Friday)."""

    def get(self, path, **params):
        if path == "/exercises":
            ex = [{"id": "e1", "name": "杠铃深蹲", "category": "strength", "primary_muscles": ["quadriceps"]},
                  {"id": "e2", "name": "卧推", "category": "strength", "primary_muscles": ["chest"]},
                  {"id": "e3", "name": "引体向上", "category": "strength", "primary_muscles": ["lats"]}]
            return {"exercises": ex, "totalCount": len(ex)}
        if path == "/workout-presets":
            pr = [{"id": i, "name": n, "exercises": [{"exercise_id": e, "exercise_name": n, "sets": [{"set_number": 1, "reps": 8}]}]}
                  for i, n, e in ((1, "推", "e2"), (2, "拉", "e3"), (3, "腿", "e1"))]
            return {"presets": pr, "total": len(pr)}
        if path == "/workout-plan-templates":
            return [{"id": 7, "plan_name": "PPL", "start_date": "2026-01-01", "end_date": None, "is_active": True,
                     "schedule_type": "weekly",
                     "assignments": [{"day_of_week": d, "sort_order": 0, "workout_preset_id": p, "workout_preset_name": n}
                                     for d, p, n in ((1, 1, "推"), (3, 2, "拉"), (5, 3, "腿"))]}]
        raise sparky_client.SparkyError(f"not in the fake: {path}")


def test_sparkyfitness_weekly_plan_day_changes(conn, world, client, monkeypatch):
    ids, tok, mon = world
    d = ids["D"]
    fake = FakeSparky()
    res = OV.reschedule_day(conn, d, mon, "腿", client=fake)
    assert res["before"] == "推" and res["after"] == "腿"
    sched = [x["sessions"] for x in res["rest_of_week"]["schedule"]]
    assert sched[0] == "腿" and sorted(s for s in sched if s != "休息") == sorted(["推", "拉", "腿"])
    assert res["rest_of_week"]["warnings"] == []
    conn.commit()

    monkeypatch.setattr(sparky_client, "client_for_user", lambda _conn, _uid: fake)
    wk = client.get(f"/v1/users/me/training/week?date={mon}", headers=H(tok["D"])).json()
    assert wk["days"][0]["planned"][0]["name"] == "腿" and wk["days"][0]["change"]["from"] == ["推"]
    assert [p["name"] for d_ in wk["days"] for p in d_["planned"]].count("腿") == 1
    ctx = rules_context.load(conn, d, mon, client=fake)                        # the rules see the same day
    assert [i["preset"]["name"] for i in ctx.planned_for(mon)] == ["腿"]
    OV.undo_day_changes(conn, d, mon, mon + timedelta(days=6))
    conn.commit()
