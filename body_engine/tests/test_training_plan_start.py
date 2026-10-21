"""Training acceptance fixes:
- #65: a plan starts on any day (start_date, training_days); the start week counts what was already trained this week
  (SparkyFitness log + watch workouts) and fills the remaining days only with what is missing; nothing is planned before
  the start; the AI is told what to settle with the user first (ask_user_first, tool descriptions).
- #66 FB-3: the 4-day template is 4 days; mobility / rehab is an opt-in add-on suggested from the user's own restrictions.
- #66 FB-4: training log (SparkyFitness) not connected / unreadable: link state on the training views, GET / connect
  endpoints (self only), save_plan keeps the plan and says what to tell the user, get_briefing tells the AI.
"""
import os
from collections import Counter
from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.assistant.tools import TOOLS_BY_NAME
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.training import catalog_data, params as P, plan_start, plans
from body_engine.training.catalog_data import MUSCLE_GROUP

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_TRAINING_PLAN_START", "body_test_training_plan_start")


# ---------------------------------------------------------------- no database
def test_four_day_template_is_four_days_without_rehab():
    tpl = P.SPLIT_TEMPLATES["4_day_upper_lower"]
    assert tpl["days_per_week"] == 4 and len(tpl["days"]) == 4
    slugs = {e["slug"]: e for e in catalog_data.EXERCISES}
    freq = Counter()
    for _dow, _name, exs in tpl["days"]:
        cats = {slugs[x[0]]["category"] for x in exs}
        assert cats <= {"strength"}, cats                     # no stretching / mobility day hard-coded in the template
        freq.update({MUSCLE_GROUP[m] for x in exs for m in slugs[x[0]]["primary_muscles"] if m in MUSCLE_GROUP})
    assert freq["back"] == 2 and freq["legs"] == 2 and freq["chest"] == 2   # each group twice a week


def test_mobility_addons_use_catalog_exercises():
    slugs = {e["slug"] for e in catalog_data.EXERCISES}
    for key, a in P.MOBILITY_ADDONS.items():
        assert a["name"] and a["name_en"] and a["joints"] and a["groups"] <= set(P.WEEKLY_SET_TARGETS), key
        assert all(row[0] in slugs for row in a["exercises"]), key


def test_remap_days_and_start_checks():
    raw = P.SPLIT_TEMPLATES["4_day_upper_lower"]["days"]
    out = plan_start.remap_days(raw, [7, 2, 4, 6])
    assert [d for d, _n, _e in out] == [2, 4, 6, 7] and [n for _d, n, _e in out] == [n for _d, n, _e in raw]
    with pytest.raises(ValueError, match="每周 4 天"):
        plan_start.remap_days(raw, [1, 3, 5])
    today = date(2026, 10, 22)
    assert plan_start.check_start(None, today) == today
    with pytest.raises(ValueError, match="不能早于今天"):
        plan_start.check_start(today - timedelta(days=1), today)


def test_plan_tools_say_ask_first():
    assert "先问用户" in TOOLS_BY_NAME["propose_plan"].description
    assert "用户确认" in TOOLS_BY_NAME["save_plan"].description
    for name in ("propose_plan", "save_plan"):
        assert {"training_days", "addons"} <= set(TOOLS_BY_NAME[name].input_schema["properties"])
    assert "start_date" in TOOLS_BY_NAME["propose_plan"].input_schema["properties"]


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
    """A: shoulder restriction (upright row banned), not linked to SparkyFitness; B: A shares training with B;
    C: no share; D: linked by the admin, but the key file is missing (unreadable)."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, role) VALUES (%s, %s) RETURNING id",
                               (n, "owner" if n == "A" else "member")).fetchone()["id"] for n in "ABCD"}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in "ABCD"}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["training"]))
        conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, reason)
                        VALUES (%s, 'upright_row', '直立划船', 'banned', '肩峰撞击')""", (ids["A"],))
        conn.execute("UPDATE users SET sparky_key_name = 'missing-test-key', sparky_user_id = 'x' WHERE id = %s", (ids["D"],))
    return ids, tok


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


class FakeLog:
    """SparkyFitness exercise history: Monday legs (squat, leg press), Tuesday pull (lat pulldown, seated row)."""

    def __init__(self, monday: date):
        self.monday = monday

    def get(self, path, **params):
        assert path == "/v2/exercise-entries/history"
        def ex(name):
            return {"id": name, "exercise_id": name, "name": name, "sets": [{"set_number": 1, "reps": 8, "weight": 40}]}
        sessions = [
            {"type": "preset", "id": 2, "entry_date": (self.monday + timedelta(days=1)).isoformat(), "name": "拉",
             "exercises": [ex("高位下拉"), ex("坐姿划船")]},
            {"type": "preset", "id": 1, "entry_date": self.monday.isoformat(), "name": "腿",
             "exercises": [ex("高脚杯深蹲"), ex("腿举")]},
        ]
        return {"sessions": sessions, "pagination": {"hasMore": False}}


def test_addons_suggested_from_own_restrictions_only(conn, world):
    ids, _tok = world
    a, b = ids["A"], ids["B"]
    pa = plans.propose_plan(conn, a, split_type="4_day_upper_lower")
    assert [s["key"] for s in pa["addon_suggestions"]] == ["shoulder"] and pa["addon_suggestions"][0]["because"]
    assert plans.propose_plan(conn, b, split_type="4_day_upper_lower")["addon_suggestions"] == []
    assert len(pa["days"]) == 4 and pa["ask_user_first"] == P.PLAN_QUESTIONS

    with_addon = plans.propose_plan(conn, a, split_type="4_day_upper_lower", addons=["shoulder"])
    assert with_addon["addons"] == ["shoulder"] and with_addon["addon_suggestions"] == []
    upper = [d for d in with_addon["days"] if "上肢" in d["name"]]
    lower = [d for d in with_addon["days"] if "下肢" in d["name"]]
    assert all(d["exercises"][0].get("addon") == "shoulder" for d in upper)
    assert not any(x.get("addon") for d in lower for x in d["exercises"])
    # warm-ups do not change the weekly working sets
    assert {k: v["direct_sets"] for k, v in with_addon["volume_by_group"].items()} == \
        {k: v["direct_sets"] for k, v in pa["volume_by_group"].items()}
    with pytest.raises(ValueError, match="没有附加项"):
        plans.propose_plan(conn, a, split_type="4_day_upper_lower", addons=["knee-x"])


def test_training_days_and_start_date(conn, world):
    ids, _tok = world
    a = ids["A"]
    today = plans._user_today(conn, a)
    p = plans.propose_plan(conn, a, split_type="4_day_upper_lower", training_days=[2, 4, 6, 7],
                           start_date=today + timedelta(days=3))
    assert [d["day_of_week"] for d in p["days"]] == [2, 4, 6, 7] and p["start_date"] == (today + timedelta(days=3)).isoformat()
    with pytest.raises(ValueError, match="不能早于今天"):
        plans.propose_plan(conn, a, split_type="4_day_upper_lower", start_date=today - timedelta(days=1))


def test_first_week_counts_what_was_trained_and_fills_the_gaps(conn, world):
    ids, _tok = world
    a = ids["A"]
    monday = date(2026, 11, 2)
    thursday = monday + timedelta(days=3)
    days = plans.propose_plan(conn, a, split_type="4_day_upper_lower")["days"]   # Mon / Tue / Thu / Fri
    fw = plan_start.first_week(conn, a, days, thursday, thursday, FakeLog(monday))
    by_date = {d["date"]: d for d in fw["days"]}
    assert by_date[monday.isoformat()]["done"] == "腿" and "legs" in by_date[monday.isoformat()]["groups"]
    assert fw["trained_groups"]["legs"] == 1 and fw["trained_groups"]["back"] == 1
    thu, fri = by_date[thursday.isoformat()], by_date[(thursday + timedelta(days=1)).isoformat()]
    assert "上肢" in thu["session"] and "下肢" in fri["session"]               # upper first: legs and back were already done
    sat = by_date[(thursday + timedelta(days=2)).isoformat()]
    assert sat["session"] == "休息" and sat["dow_session"] is None            # not a training day
    assert fw["full_cycle_from"] == (monday + timedelta(days=7)).isoformat() and fw["sparky"] == "connected"

    # watch only (SparkyFitness not linked): a strength workout counts as trained, groups unknown
    conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min)
                    VALUES (%s, 'shortcut', 'w-test-1', 'strength_training', '力量训练', %s, %s, %s, 60)""",
                 (a, monday, f"{monday}T18:00:00+11:00", f"{monday}T19:00:00+11:00"))
    fw2 = plan_start.first_week(conn, a, days, thursday, thursday)
    assert fw2["sparky"] == "not_linked"
    assert {d["date"]: d for d in fw2["days"]}[monday.isoformat()]["done"] == "力量训练"
    conn.rollback()


def test_save_plan_without_training_log_keeps_the_plan(conn, world):
    ids, _tok = world
    a = ids["A"]
    today = plans._user_today(conn, a)
    saved = plans.save_plan(conn, a, split_type="4_day_upper_lower", sync_sparky=True, start_date=today, addons=["shoulder"])
    assert saved["saved"]["active"] and saved["addons"] == ["shoulder"]
    sync = saved["sparky_sync"]
    assert sync["synced"] is False and sync["link"] == "not_linked" and "计划已保存在 Body Engine" in sync["tell_user"]
    assert "first_week" in saved
    wk = plans.get_week_plan(conn, a, a, today)
    for d in wk["days"]:
        if d["date"] < today:
            assert d.get("before_start") and d["planned"] == [] and not d["rest_day"]
    nxt = plans.get_week_plan(conn, a, a, today + timedelta(days=7))
    assert sum(1 for d in nxt["days"] if d["planned"]) == 4 and not any(d.get("before_start") for d in nxt["days"])
    conn.commit()


def test_link_state(conn, world):
    ids, _tok = world
    st = sparky_client.link_state(conn, ids["A"])
    assert st["state"] == "not_linked" and st["label"] == "训练记录未连接" and st["label_en"] and st["hint_en"]
    st = sparky_client.link_state(conn, ids["D"])
    assert st["state"] == "unreadable" and "reason" in st and st["hint"]


def test_training_log_endpoints_and_views(client, world, monkeypatch):
    ids, tok = world
    a = ids["A"]
    me = client.get("/v1/users/me/training/sparky", headers=H(tok["A"])).json()
    assert me["state"] == "not_linked" and me["can_connect"] is True
    other = client.get(f"/v1/users/{a}/training/sparky", headers=H(tok["B"])).json()
    assert other["state"] == "not_linked" and other["can_connect"] is False and "reason" not in other
    assert client.get(f"/v1/users/{a}/training/sparky", headers=H(tok["C"])).status_code == 403
    assert client.post(f"/v1/users/{a}/training/sparky/connect", headers=H(tok["B"])).status_code == 403

    from body_engine.account import provision
    monkeypatch.setattr(provision, "connect_sparky", lambda conn, uid, sparky=None: {"ok": False, "error": "RuntimeError: sign-up closed"})
    r = client.post("/v1/users/me/training/sparky/connect", headers=H(tok["A"])).json()
    assert r["state"] == "not_linked" and r["error"] and r["error_en"] and r["can_connect"] is True
    r = client.post("/v1/users/me/training/sparky/connect", headers=H(tok["D"])).json()
    assert r["state"] == "unreadable" and "管理员" in r["error"]

    # views: the Body Engine plan with the link state; no plan and no log -> an empty week instead of an error
    wk = client.get("/v1/users/me/training/week", headers=H(tok["A"])).json()
    assert wk["plan"] and wk["sparky"]["state"] == "not_linked" and wk["sparky"]["can_connect"]
    td = client.get("/v1/users/me/today", headers=H(tok["A"])).json()["training"]
    assert td["sparky"]["state"] == "not_linked"
    empty = client.get("/v1/users/me/training/week", headers=H(tok["C"])).json()
    assert empty["plan"] is None and len(empty["days"]) == 7 and empty["sparky"]["state"] == "not_linked" and empty["available_splits"]
    assert client.get("/v1/users/me/today", headers=H(tok["C"])).json()["training"]["sparky"]["state"] == "not_linked"


def test_connect_sparky_runs_only_the_sparky_step(conn, world, tmp_path, monkeypatch):
    ids, _tok = world
    from body_engine.account import provision
    from body_engine.ai import crypto
    monkeypatch.setenv("BODY_AI_MASTER_KEY_FILE", str(tmp_path / "ai_master_key"))   # never secrets/
    crypto.init_master_key()

    class FakeSignup:
        def ensure_account(self, email, password, name):
            return "sp-user-c"

        def api_key(self, perm, name):
            return f"key-{perm}"

    res = provision.connect_sparky(conn, ids["C"], sparky=FakeSignup())
    assert res == {"ok": True}
    row = conn.execute("SELECT steps FROM user_provisioning WHERE user_id = %s", (ids["C"],)).fetchone()
    assert row["steps"] == {"sparky": "done"}
    assert conn.execute("SELECT sparky_user_id FROM users WHERE id = %s", (ids["C"],)).fetchone()["sparky_user_id"] == "sp-user-c"
    assert provision.connect_sparky(conn, ids["D"]) == {"ok": False, "error": "admin_linked"}
    conn.execute("UPDATE users SET sparky_user_id = NULL WHERE id = %s", (ids["C"],))   # back to not linked for other tests
    conn.execute("DELETE FROM user_provisioning WHERE user_id = %s", (ids["C"],))
    conn.commit()


def test_briefing_tells_the_ai_about_the_training_log(conn, world):
    from body_engine.assistant import briefing
    ids, _tok = world
    b = briefing.get(conn, ids["A"], ids["A"])
    tl = b["training"].get("training_log")
    assert tl and tl["state"] == "not_linked" and "我 → 训练记录" in tl["tell_user"]
