"""Issue #154: the exercise library's way back and "add to plan" (rollout flag exercise_add_to_plan).
- Library navigation state (exercise-plan.js createNav, run in node): the filters and scroll survive opening a detail and coming back;
  app.js hands the detail a back function and the back arrow is not behind the flag.
- Adding to a session of the weekly plan (every week) and to one date only; defaults = the recommendation; duplicates, dates
  outside the window and banned exercises (exercise_meta) are refused and write nothing.
- HTTP: 404 with the flag off, self only, 409 for a banned exercise.
"""
import json
import os
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.training import plan_add, plans

STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_PLAN_ADD", "body_test_plan_add")
CSRF = {"X-Body-Web": "1"}
FLAG = "exercise_add_to_plan"


# ---------------------------------------------------------------- page scripts (node)
def node_json(expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = (f"globalThis.I18N = {{ t: (k) => k, lang: 'zh' }};\n"
             f"const P = require({json.dumps(str(STATIC / 'exercise-plan.js'))});\n"
             f"console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)


def test_library_filters_and_scroll_survive_the_detail():
    out = node_json("""(() => {
      const nav = P.createNav();
      nav.set({ q: "卧推", grp: "chest" });        // typing / picking a muscle
      nav.set({ scroll: 640 });                     // leaving for a detail saves the scroll position
      const back = nav.get();                       // 「返回」 re-opens the list from this
      nav.reset();                                  // opening the library from the page starts fresh
      return { back, fresh: nav.get() };
    })()""")
    assert out["back"] == {"q": "卧推", "grp": "chest", "scroll": 640}
    assert out["fresh"] == {"q": "", "grp": "", "scroll": 0}


def test_app_js_wires_the_back_arrow_without_the_flag():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    lib = app[app.index("async function exerciseLibrarySheet"):app.index("function splitPickerSheet")]
    assert "exerciseDetailSheet(b.dataset.exOpen, () => exerciseLibrarySheet(true))" in lib   # detail knows the way back
    assert "exNav.set({ q, grp, scroll:" in lib and "if (!keep) exNav.reset()" in lib          # state saved on leaving, kept on coming back
    detail = app[app.index("async function exerciseDetailSheet"):app.index("async function exerciseLibrarySheet")]
    assert "setBack(back)" in detail
    assert "addPlanOn()" in detail and detail.count("addPlanOn()") == 1                         # only the add button is gated
    assert "exercise_add_to_plan" in app and 'id="sheet-back"' in (STATIC / "index.html").read_text(encoding="utf-8")


def test_add_request_body_and_validation():
    out = node_json("""[
      P.addRequest({ slug: "lat_pulldown", scope: "template", session: { day_of_week: 2, name: "上肢 B" }, sets: "3", repLow: "8", repHigh: "12" }),
      P.addRequest({ slug: "lat_pulldown", scope: "day", day: "2026-10-26", sets: 4, repLow: 6, repHigh: 6 }),
      P.addRequest({ slug: "plank", scope: "day", day: "2026-10-26", timed: true, sets: 3, durationSec: 45 }),
      P.addRequest({ slug: "x", scope: "template", sets: 3, repLow: 8, repHigh: 12 }),
      P.addRequest({ slug: "x", scope: "day", sets: 0, repLow: 8, repHigh: 12 }),
      P.addRequest({ slug: "x", scope: "day", day: "2026-10-26", sets: 3, repLow: 12, repHigh: 8 }),
    ]""")
    assert out[0]["body"] == {"exercise": "lat_pulldown", "scope": "template", "sets": 3, "rep_low": 8, "rep_high": 12,
                              "day_of_week": 2, "session_name": "上肢 B"}
    assert out[1]["body"]["day"] == "2026-10-26" and out[1]["body"]["rep_low"] == 6
    assert out[2]["body"]["duration_sec"] == 45 and "rep_low" not in out[2]["body"]
    assert all("error" in o and "body" not in o for o in out[3:])


# ---------------------------------------------------------------- server
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


def _plan_days(dow: int, other: int) -> list[dict]:
    ex = lambda slug, sets=3, lo=8, hi=12: {"exercise": slug, "sets": sets, "rep_low": lo, "rep_high": hi, "rest_sec": 90,
                                            "duration_sec": None, "weight_kg": None, "note": None}
    return [{"day_of_week": dow, "name": "上肢 B（推）", "exercises": [ex("machine_chest_press", 4)]},
            {"day_of_week": other, "name": "下肢 A", "exercises": [ex("seated_leg_extension")]}]


@pytest.fixture(scope="module")
def world(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, role) VALUES (%s, %s) RETURNING id",
                               (n, "owner" if n == "A" else "member")).fetchone()["id"] for n in ("A", "B", "C")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["training"]))
        conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, reason)
                        VALUES (%s, 'upright_row', '直立划船', 'banned', '肩峰撞击')""", (ids["A"],))
        today = plans._user_today(conn, ids["A"])
        dow = today.isoweekday()
        plans.save_plan(conn, ids["A"], split_type="custom", days=_plan_days(dow, dow % 7 + 1), name="加入计划测试",
                        sync_sparky=False)
    return ids, tok, today


def _session(conn, uid, name):
    row = plans._active_plan_row(conn, uid)
    return next(d for d in row["days"] if d["name"] == name)


def test_add_to_session_uses_the_recommendation_and_every_week(conn, world):
    ids, _, today = world
    res = plan_add.add(conn, ids["A"], "template", "lat_pulldown", day_of_week=today.isoweekday(), session_name="上肢 B（推）",
                       sync_sparky=False)
    assert res["added"] and res["session_name"] == "上肢 B（推）"
    item = _session(conn, ids["A"], "上肢 B（推）")["exercises"][-1]
    assert (item["slug"], item["sets"], item["rep_low"], item["rep_high"]) == ("lat_pulldown", 3, 8, 12)   # the defaults
    # it is in the plan next week too
    wk = plans.get_week_plan(conn, ids["A"], ids["A"], today + timedelta(days=7))
    day = next(d for d in wk["days"] if d["day_of_week"] == today.isoweekday())
    assert "lat_pulldown" in [e["slug"] for p in day["planned"] for e in p["exercises"]]
    # the other session is untouched
    assert [e["slug"] for e in _session(conn, ids["A"], "下肢 A")["exercises"]] == ["seated_leg_extension"]
    # explicit numbers win, a second add of the same exercise to the same session is refused
    plan_add.add(conn, ids["A"], "template", "seated_cable_row_close", day_of_week=today.isoweekday(), sets=5, rep_low=6, rep_high=8,
                 sync_sparky=False)
    last = _session(conn, ids["A"], "上肢 B（推）")["exercises"][-1]
    assert (last["sets"], last["rep_low"], last["rep_high"]) == (5, 6, 8)
    with pytest.raises(ValueError):
        plan_add.add(conn, ids["A"], "template", "lat_pulldown", day_of_week=today.isoweekday(), sync_sparky=False)


def test_banned_exercise_is_refused_everywhere_and_writes_nothing(conn, world):
    ids, _, today = world
    before = plans._active_plan_row(conn, ids["A"])["days"]
    with pytest.raises(PermissionError, match="禁用"):
        plan_add.add(conn, ids["A"], "template", "upright_row", day_of_week=today.isoweekday(), sync_sparky=False)
    with pytest.raises(PermissionError):
        plan_add.add(conn, ids["A"], "day", "upright_row", day=today + timedelta(days=1))
    assert plans._active_plan_row(conn, ids["A"])["days"] == before
    assert not conn.execute("SELECT 1 FROM training_day_extras WHERE user_id = %s", (ids["A"],)).fetchall()
    o = plan_add.options(conn, ids["A"], "upright_row")
    assert o["blocked"] and o["sessions"] == [] and o["exercise"]["reason"] == "肩峰撞击"


def test_add_to_one_day_only(conn, world):
    ids, _, today = world
    day = today + timedelta(days=2)
    res = plan_add.add(conn, ids["A"], "day", "dumbbell_lateral_raise", day=day, sets=4, rep_low=12, rep_high=15)
    assert res["date"] == day.isoformat()
    wk = plans.get_week_plan(conn, ids["A"], ids["A"], day)
    entry = next(d for d in wk["days"] if d["date"] == day)
    extra = [p for p in entry["planned"] if p.get("added_for_day")]
    assert extra and [e["slug"] for e in extra[0]["exercises"]] == ["dumbbell_lateral_raise"] and not entry["rest_day"]
    # only that date: not the same weekday next week, and the plan itself is unchanged
    nxt = plans.get_week_plan(conn, ids["A"], ids["A"], day + timedelta(days=7))
    assert not any(p.get("added_for_day") for d in nxt["days"] for p in d["planned"])
    assert all(e["slug"] != "dumbbell_lateral_raise" for d in plans._active_plan_row(conn, ids["A"])["days"] for e in d["exercises"])
    # training day v2 reads the same plan
    from body_engine.training import session_log
    v = session_log.view(conn, ids["A"], ids["A"], day)
    assert "dumbbell_lateral_raise" in [x["slug"] for s in v["sessions"] for x in s["exercises"]]
    with pytest.raises(ValueError):                                   # same exercise, same day
        plan_add.add(conn, ids["A"], "day", "dumbbell_lateral_raise", day=day)
    with pytest.raises(ValueError):                                   # outside the window of day changes
        plan_add.add(conn, ids["A"], "day", "lat_pulldown", day=today + timedelta(days=400))
    with pytest.raises(ValueError):
        plan_add.add(conn, ids["A"], "day", "lat_pulldown", day=day, sets=0)


def test_options_lists_sessions_days_and_defaults(conn, world):
    ids, _, today = world
    o = plan_add.options(conn, ids["A"], "machine_chest_press")
    assert o["plan"]["name"] == "加入计划测试" and not o["blocked"]
    assert o["defaults"]["sets"] == 3 and (o["defaults"]["rep_low"], o["defaults"]["rep_high"]) == (8, 12)
    chest = next(s for s in o["sessions"] if s["name"] == "上肢 B（推）")
    assert chest["has_exercise"] and chest["exercise_count"] >= 1
    assert len(o["days"]) == 7 and o["days"][0]["date"] == today.isoformat()
    # a user without a plan gets defaults and nothing to pick
    empty = plan_add.options(conn, auth_user(conn, "C"), "lat_pulldown")
    assert empty["plan"] is None and empty["sessions"] == [] and empty["defaults"]["sets"] == 3
    timed = plan_add.options(conn, ids["A"], "plank")
    assert timed["defaults"]["duration_sec"] and timed["defaults"]["rep_low"] is None


def auth_user(conn, name):
    return conn.execute("SELECT id FROM users WHERE display_name = %s", (name,)).fetchone()["id"]


# ---------------------------------------------------------------- HTTP + flag
@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token: str, lang: str = "zh") -> dict:
    return {"Authorization": f"Bearer {token}", "X-Body-Lang": lang, **CSRF}


def set_flag(dsn, mode):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        flags.set_flag(c, FLAG, mode, actor="test")
    flags.invalidate()


def test_http_behind_flag_self_only_and_banned(client, world, dsn):
    ids, tok, today = world
    base = "/v1/users/me/training/plan"
    body = {"exercise": "face_pull", "scope": "day", "day": (today + timedelta(days=3)).isoformat()}
    set_flag(dsn, "off")
    assert client.get(f"{base}/add-options?exercise=lat_pulldown", headers=H(tok["A"])).status_code == 404
    assert client.post(f"{base}/add-exercise", headers=H(tok["A"]), json=body).status_code == 404
    set_flag(dsn, "all")
    try:
        r = client.get(f"{base}/add-options?exercise=lat_pulldown", headers=H(tok["A"]))
        assert r.status_code == 200 and r.json()["sessions"] and r.json()["days"]
        assert client.get(f"{base}/add-options?exercise=nope_nothing_xyz", headers=H(tok["A"])).status_code == 404
        r = client.post(f"{base}/add-exercise", headers=H(tok["A"]), json=body)
        assert r.status_code == 200 and r.json()["added"], r.text
        r = client.post(f"{base}/add-exercise", headers=H(tok["A"]), json={**body, "scope": "template", "exercise": "face_pull",
                        "day_of_week": today.isoweekday(), "session_name": "上肢 B（推）", "sets": 2, "rep_low": 15, "rep_high": 15})
        assert r.status_code == 200 and r.json()["sets_text"]
        # banned: 409 with the reason, in English when asked
        r = client.post(f"{base}/add-exercise", headers=H(tok["A"], "en"), json={**body, "exercise": "upright_row"})
        assert r.status_code == 409 and "banned" in r.json()["detail"]
        r = client.post(f"{base}/add-exercise", headers=H(tok["A"]), json={**body, "exercise": "upright_row", "scope": "template",
                        "day_of_week": today.isoweekday()})
        assert r.status_code == 409 and "禁用" in r.json()["detail"]
        # bad numbers: 422; a viewer with the training share cannot write another user's plan
        assert client.post(f"{base}/add-exercise", headers=H(tok["A"]), json={**body, "sets": 99}).status_code == 422
        assert client.post(f"/v1/users/{ids['A']}/training/plan/add-exercise", headers=H(tok["B"]), json=body).status_code == 403
        # no plan: a clear 422
        r = client.post(f"{base}/add-exercise", headers=H(tok["C"]), json=body)
        assert r.status_code == 422
    finally:
        set_flag(dsn, "off")
