"""Rule engine end to end: create tables -> fake SparkyFitness -> idempotent evaluate-rules -> API / assistant queries and authorization;
idempotent exercise ID linking (link-exercises); next-day recovery score in journal analysis; muscle_load of the scoring engine.

Uses a separate test database body_test_rules (configurable via BODY_TEST_DB_NAME), recreated on every run; skipped if it cannot connect."""
import json
import os
from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.records import analysis, exercise_link
from body_engine.rules import engine
from body_engine.scores import engine as scores_engine
from body_engine.scores.engine import score_days
from test_scores import make_inputs

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_RULES", "body_test_rules")
FRI = date(2026, 10, 16)


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


@pytest.fixture
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def world(dsn):
    """A shares reminders with B; C shares nothing. A has 4 exercise metadata rows (SparkyFitness ID not filled in yet)."""
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"]
               for n in ("A", "B", "C")}
        tokens = {"A": auth.create_token(conn, ids["A"], "t", ["read", "write"]),
                  "A_ro": auth.create_token(conn, ids["A"], "t", ["read"]),
                  "B": auth.create_token(conn, ids["B"], "t", ["read", "write"]),
                  "C": auth.create_token(conn, ids["C"], "t", ["read", "write"])}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["A"], ids["B"], ["reminders"]))
        for ref, name, status, aliases, reason in (
                ("lat_pulldown", "高位下拉（正手，略宽于肩）", "active", [], None),
                ("upright_row", "杠铃提拉 / 直立划船", "banned", ["直立划船"], "练大上斜方"),
                ("cable_face_pull", "绳索面拉 + 外旋", "caution", ["面拉"], "大重量不做"),
                ("incline_dumbbell_press", "上斜哑铃卧推（30°）", "caution", ["上斜卧推"], "肩")):
            conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, aliases, reason)
                            VALUES (%s, %s, %s, %s, %s, %s)""", (ids["A"], ref, name, status, aliases, reason))
        for key, kind in (("low_blood_sugar", "bool"), ("red_meat_g", "number"), ("sauna", "bool")):
            conn.execute("INSERT INTO journal_items (user_id, key, label_zh, kind) VALUES (%s, %s, %s, %s)",
                         (ids["A"], key, key, kind))
    return ids, tokens


# ---------------------------------------------------------------- fake SparkyFitness
def _ex(i, name, muscles, desc="", category="strength", modality="weight_reps", source_id=None):
    return {"id": i, "name": name, "category": category, "modality": modality, "source_id": source_id,
            "primary_muscles": json.dumps(muscles), "description": desc}


EXERCISES = [_ex("p1", "高位下拉（正手，略宽于肩）", ["lats"], source_id="bm:lat_pulldown"),
             _ex("u1", "杠铃提拉 / 直立划船（禁用）", ["traps"], "【禁用】永久不做", source_id="bm:barbell_upright_row"),
             _ex("f1", "绳索面拉 + 外旋（轻重量）", ["shoulders"], "【注意】轻重量", source_id="bm:cable_face_pull"),
             _ex("i1", "上斜哑铃卧推（30°）", ["chest"], "【注意】", source_id="bm:incline_db_press"),
             _ex("i2", "上斜卧推（器械未注明）", ["chest"], "【注意】", source_id="bm:incline_press_unspecified"),
             _ex("h1", "杠铃臀推", ["glutes"]),
             _ex("w1", "爬坡（跑步机）", [], category="cardio", modality="duration")]


def _raw_session(d, name, exercises, notes=None):
    return {"type": "preset", "entry_date": d, "name": name, "notes": notes, "source": "manual", "total_duration_minutes": 0,
            "exercises": [{"exercise_id": ex_id, "exercise_snapshot": {"name": n, "category": "strength",
                                                                       "primary_muscles": m},
                           "sets": [{"set_number": k + 1, "set_type": "Working Set", "reps": r, "weight": w}
                                    for k, (r, w) in enumerate(sets)]} for ex_id, n, m, sets in exercises]}


def sparky_world():
    return {
        "/exercises": {"exercises": EXERCISES, "totalCount": len(EXERCISES)},
        "/workout-presets": {"total": 2, "presets": [
            {"id": 1, "name": "上肢 A", "exercises": [
                {"exercise_id": "p1", "exercise_name": "高位下拉", "category": "strength", "progression_mode": "rep_goal",
                 "rep_goal": 12, "increment_value": 2.5,
                 "sets": [{"set_number": k, "set_type": "Working Set", "reps": 8, "weight": 39} for k in (1, 2, 3, 4)]}]},
            {"id": 2, "name": "下肢 B", "exercises": [
                {"exercise_id": "h1", "exercise_name": "杠铃臀推", "category": "strength", "progression_mode": "rep_goal",
                 "rep_goal": 12, "increment_value": 5,
                 "sets": [{"set_number": k, "set_type": "Working Set", "reps": 8, "weight": 40} for k in (1, 2, 3, 4)]}]}]},
        "/workout-plan-templates": [{"id": 1, "plan_name": "V3.0", "start_date": "2026-10-15", "end_date": None,
                                     "is_active": True, "schedule_type": "weekly", "assignments": [
                                         {"day_of_week": 1, "sort_order": 0, "workout_preset_id": 1, "workout_preset_name": "上肢 A"},
                                         {"day_of_week": 5, "sort_order": 0, "workout_preset_id": 2, "workout_preset_name": "下肢 B"}]}],
        "/v2/exercise-entries/history": {"pagination": {"hasMore": False}, "sessions": [
            _raw_session("2026-10-12", "肩", [("u1", "直立划船", ["traps"], [(12, 7)] * 4),
                                             ("f1", "面拉", ["shoulders"], [(15, 32)] * 4)]),
            _raw_session("2026-10-10", "背", [("p1", "高位下拉", ["lats"], [(12, 39)] * 4)])]},
        "/reports/nutrition-trends-with-goals": [],
        "/food-entries/range/": [{"entry_date": FRI.isoformat(), "entry_time": "14:30:00", "meal_type": "breakfast",
                                  "food_name": "吐司、煎蛋、培根 2 片", "quantity": 1, "serving_size": 1, "unit": "份",
                                  "calories": 700, "protein": 40, "carbs": 45, "fat": 40}],
        "/goals/by-date/": {"preset_name": "训练日", "calories": 2400, "protein": 160, "carbs": 255, "fat": 62},
        "/measurements/check-in-measurements-range/": [{"entry_date": "2026-10-14", "weight": 89}],
    }


class FakeSparky:
    def __init__(self, world=None, fail=()):
        self.world, self.fail = world or sparky_world(), set(fail)

    def get(self, path, **params):
        for prefix, resp in self.world.items():
            if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
                if prefix in self.fail:
                    raise RuntimeError(f"{prefix} 挂了")
                return resp
        raise KeyError(path)


def reminders(conn, uid, day=FRI):
    return {(r["rule_key"], r["subject"]): r for r in conn.execute(
        "SELECT * FROM reminders WHERE user_id = %s AND date = %s", (uid, day))}


# ---------------------------------------------------------------- exercise ID linking
def test_link_exercises_matches_and_is_idempotent(conn, world):
    ids, _ = world
    a = ids["A"]
    dry = exercise_link.run(conn, a, FakeSparky(), dry_run=True)
    assert len(dry["set"]) == 4 and conn.execute(
        "SELECT count(*) AS n FROM exercise_meta WHERE user_id = %s AND app_exercise_id IS NOT NULL", (a,)).fetchone()["n"] == 0
    r = exercise_link.run(conn, a, FakeSparky())
    got = {x["exercise_ref"]: (x["sparky_id"], x["matched_by"]) for x in r["set"]}
    assert got == {"lat_pulldown": ("p1", "source_id"), "cable_face_pull": ("f1", "source_id"),
                   "upright_row": ("u1", "去掉括号注释后的名称"), "incline_dumbbell_press": ("i1", "名称 / 别名")}
    # "上斜卧推（器械未注明）" also matches an alias of incline_dumbbell_press once the parentheses are stripped, but the stronger match already took it -> don't steal
    assert {x["name"] for x in r["unmatched_sparky"]} == {"上斜卧推（器械未注明）", "杠铃臀推", "爬坡（跑步机）"}
    again = exercise_link.run(conn, a, FakeSparky())
    assert again["set"] == [] and len(again["unchanged"]) == 4
    # manually edited IDs are not overwritten
    conn.execute("UPDATE exercise_meta SET app_exercise_id = 'manual' WHERE user_id = %s AND exercise_ref = 'lat_pulldown'", (a,))
    r = exercise_link.run(conn, a, FakeSparky())
    assert [c["exercise_ref"] for c in r["conflicts"]] == ["lat_pulldown"]
    conn.execute("UPDATE exercise_meta SET app_exercise_id = 'p1' WHERE user_id = %s AND exercise_ref = 'lat_pulldown'", (a,))
    assert "核对" not in exercise_link.format_report(again)   # the test DB has no known IDs, no cross-check


def test_link_match_ambiguous():
    meta = [{"exercise_ref": "x", "name_zh": "划船", "aliases": []}]
    sparky = [{"id": "1", "name": "划船（宽）"}, {"id": "2", "name": "划船（窄）"}]
    res = exercise_link.match(meta, sparky)
    assert res["pairs"] == [] and len(res["ambiguous"]) == 2


# ---------------------------------------------------------------- evaluate-rules
def test_evaluate_rules_idempotent_and_keeps_status(conn, world):
    ids, _ = world
    a = ids["A"]
    exercise_link.run(conn, a, FakeSparky())
    conn.execute("INSERT INTO journal_entries (user_id, date, key, value) VALUES (%s, '2026-10-15', 'low_blood_sugar', 1)", (a,))
    res = engine.run(conn, a, FRI, FakeSparky())
    status = {r["rule_key"]: r["status"] for r in res["rules"]}
    assert status == {"progression": "triggered", "calorie_adjust": "insufficient_data", "pre_workout_fuel": "triggered",
                      "frequency_caps": "not_triggered", "sugar_caps": "not_triggered", "exercise_restrictions": "triggered",
                      "readiness": "insufficient_data", "sleep_goal": "insufficient_data",
                      "water_halfway": "insufficient_data"}   # the water flag is off here
    rem = reminders(conn, a)
    assert set(rem) == {("progression", "p1:39:up"), ("pre_workout_fuel", ""), ("pre_workout_fuel", "carbs"),
                        ("exercise_restrictions", f"log:u1:2026-10-12"),
                        ("exercise_restrictions", f"weight:f1:2026-10-12")}
    assert rem[("pre_workout_fuel", "")]["level"] == "warning"           # hypoglycemia symptoms on 10-15
    assert rem[("progression", "p1:39:up")]["evidence"]["next_weight_kg"] == 41.5
    # run again: everything unchanged
    again = engine.run(conn, a, FRI, FakeSparky())
    assert (again["inserted"], again["updated"], again["deleted"]) == (0, 0, 0) and again["unchanged"] == 5
    # seen / no-longer-remind states are kept after a rerun
    rid = rem[("progression", "p1:39:up")]["id"]
    conn.execute("UPDATE reminders SET status = 'seen' WHERE id = %s", (rid,))
    engine.run(conn, a, FRI, FakeSparky())
    assert reminders(conn, a)[("progression", "p1:39:up")]["status"] == "seen"
    # data changed: enough carbs now -> that one is removed
    world2 = sparky_world()
    world2["/food-entries/range/"][0]["carbs"] = 80
    assert engine.run(conn, a, FRI, FakeSparky(world2))["deleted"] == 1
    assert ("pre_workout_fuel", "carbs") not in reminders(conn, a)
    # workout records unreadable -> rules depending on them error, old reminders are kept
    res = engine.run(conn, a, FRI, FakeSparky(world2, fail={"/v2/exercise-entries/history"}))
    assert res["deleted"] == 0 and {r["rule_key"]: r["status"] for r in res["rules"]}["progression"] == "error"
    assert ("progression", "p1:39:up") in reminders(conn, a) and res["sources_failed"]["sessions"]
    run = conn.execute("SELECT * FROM rule_runs WHERE user_id = %s AND date = %s", (a, FRI)).fetchone()
    assert {r["rule_key"]: r["status"] for r in run["results"]}["progression"] == "error"


def test_sticky_dismiss_carries_to_next_day(conn, world):
    ids, _ = world
    a = ids["A"]
    engine.run(conn, a, FRI, FakeSparky())
    rem = reminders(conn, a)
    conn.execute("UPDATE reminders SET status = 'dismissed' WHERE id = ANY(%s)",
                 ([rem[("progression", "p1:39:up")]["id"], rem[("pre_workout_fuel", "")]["id"]],))
    sat = FRI + timedelta(days=1)
    engine.run(conn, a, sat, FakeSparky())
    nxt = reminders(conn, a, sat)
    assert nxt[("progression", "p1:39:up")]["status"] == "dismissed"     # the same weight-increase suggestion: inherited
    assert ("pre_workout_fuel", "") not in nxt                           # no training plan on Saturday
    assert nxt[("exercise_restrictions", "log:u1:2026-10-12")]["status"] == "new"


def test_user_without_sparky(conn, world):
    ids, _ = world
    res = engine.run(conn, ids["C"], FRI)
    assert res["sources_missing"]["sparky"]
    # no diet constraints (issue #76): the caps rule has nothing to check; every other rule lacks data
    assert all(r["status"] == ("not_triggered" if r["rule_key"] == "frequency_caps" else "insufficient_data") for r in res["rules"])


# ---------------------------------------------------------------- API / assistant
@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def h(t):
    return {"Authorization": f"Bearer {t}"}


def test_api_reminders(client, world, conn):
    ids, tok = world
    a = ids["A"]
    engine.run(conn, a, FRI, FakeSparky())
    q = {"start": "2026-10-10", "end": "2026-10-16"}
    r = client.get("/v1/users/me/reminders", params=q, headers=h(tok["A"]))
    assert r.status_code == 200 and r.json() and all(x["date"] == "2026-10-16" for x in r.json())
    assert r.json()[0]["level"] == "warning" and "evidence" in r.json()[0]
    assert client.get(f"/v1/users/{a}/reminders", params=q, headers=h(tok["B"])).status_code == 200   # has the share
    assert client.get(f"/v1/users/{a}/reminders", params=q, headers=h(tok["C"])).status_code == 403
    runs = client.get("/v1/users/me/reminders/runs", params=q, headers=h(tok["A"])).json()
    assert {x["rule_key"] for x in runs[0]["results"]} == set(engine.RULE_KEYS)
    rid = r.json()[0]["id"]
    assert client.patch(f"/v1/users/{a}/reminders/{rid}", json={"status": "seen"}, headers=h(tok["B"])).status_code == 403
    assert client.patch(f"/v1/users/me/reminders/{rid}", json={"status": "seen"}, headers=h(tok["A_ro"])).status_code == 403
    assert client.patch(f"/v1/users/me/reminders/{rid}", json={"status": "bad"}, headers=h(tok["A"])).status_code == 422
    p = client.patch(f"/v1/users/me/reminders/{rid}", json={"status": "dismissed"}, headers=h(tok["A"]))
    assert p.status_code == 200 and p.json()["status"] == "dismissed" and p.json()["status_changed_at"]
    assert rid not in {x["id"] for x in client.get("/v1/users/me/reminders", params=q, headers=h(tok["A"])).json()}
    assert rid in {x["id"] for x in client.get("/v1/users/me/reminders", headers=h(tok["A"]),
                                               params={**q, "status": "dismissed"}).json()}
    assert client.patch("/v1/users/me/reminders/999999", json={"status": "seen"}, headers=h(tok["A"])).status_code == 404


def test_assistant_tools(world, conn):
    ids, _ = world
    a, b = ids["A"], ids["B"]
    engine.run(conn, a, FRI, FakeSparky())
    assert TOOLS_BY_NAME["dismiss_reminder"].writes and not TOOLS_BY_NAME["get_reminders"].writes
    out = run_tool(conn, a, "mcp", "get_reminders", {"user": "me", "start": "2026-10-16", "end": "2026-10-16",
                                                     "include_dismissed": False})
    assert out["reminders"] and "evidence" not in out["reminders"][0] and isinstance(out["rule_runs"][0], str)
    full = run_tool(conn, a, "mcp", "get_reminders", {"user": "me", "start": "2026-10-16", "end": "2026-10-16",
                                                      "include_dismissed": False, "detail": True})
    assert full["rule_runs"][0]["date"] == FRI and "evidence" in full["reminders"][0]
    assert [r["id"] for r in full["reminders"]] == [r["id"] for r in out["reminders"]]
    shared = run_tool(conn, b, "mcp", "get_reminders", {"user": str(a), "start": "2026-10-16", "end": "2026-10-16",
                                                        "include_dismissed": True})
    assert len(shared["reminders"]) >= len(out["reminders"])
    with pytest.raises(PermissionError):
        run_tool(conn, ids["C"], "mcp", "get_reminders", {"user": str(a), "start": None, "end": None,
                                                         "include_dismissed": False})
    rid = out["reminders"][0]["id"]
    with pytest.raises(ValueError):   # B cannot modify A's (dismiss_reminder has no user parameter, it only changes by one's own user_id)
        run_tool(conn, b, "mcp", "dismiss_reminder", {"reminder_id": rid})
    assert run_tool(conn, a, "mcp", "dismiss_reminder", {"reminder_id": rid})["status"] == "dismissed"


# ---------------------------------------------------------------- journal analysis: next-day recovery score
def test_journal_analysis_next_recovery(conn, world):
    ids, _ = world
    a = ids["A"]
    start = date(2026, 8, 1)
    for k in range(30):
        d = start + timedelta(days=k)
        sauna = k % 2
        conn.execute("INSERT INTO journal_entries (user_id, date, key, value) VALUES (%s, %s, 'sauna', %s)", (a, d, sauna))
        conn.execute("INSERT INTO daily_scores (user_id, date, recovery, algo_version) VALUES (%s, %s, %s, 'test')",
                     (a, d + timedelta(days=1), 70 + k % 3 if sauna else 50 + k % 3))
    res = analysis.analyze(conn, a, "sauna", end=start + timedelta(days=29), days=30)
    rec = res["outcomes"]["next_recovery"]
    assert list(res["outcomes"])[0] == "next_recovery"
    assert rec["status"] == "ok" and rec["yes"]["n"] == 15 and rec["no"]["n"] == 15 and rec["reliable"]
    assert rec["direction"] == "better" and 19 < rec["diff_yes_minus_no"] < 21
    assert res["outcomes"]["next_rhr"]["status"] == "insufficient"   # no daily_metrics


# ---------------------------------------------------------------- scoring engine: muscle_load
def test_score_days_writes_muscle_load():
    inp = make_inputs(nights=20)
    days = sorted({n.wake_date for n in inp.nights})
    d = days[5]
    inp.muscle = {d: {"volume_kg": 1350.0, "working_sets": 8, "bodyweight_sets": 0, "srpe": None, "sessions": ["胸 + 腿"],
                      "sets_by_muscle": {"chest": 4}}}
    rows = {r["date"]: r for r in score_days(inp, days[0], days[-1], today=days[-1] + timedelta(days=5))}
    assert rows[d]["muscle_load"] == 1350.0 and rows[d]["details"]["muscle"]["working_sets"] == 8
    assert rows[days[6]]["muscle_load"] is None and "muscle_load_unavailable" not in rows[days[6]]["flags"]
    assert rows[d]["algo_version"] >= "v1.1"          # muscle_load came with v1.1
    inp.muscle, inp.muscle_error = {}, "连不上"
    rows = score_days(inp, days[0], days[1], today=days[-1])
    assert all("muscle_load_unavailable" in r["flags"] for r in rows)


def test_load_muscle_from_sparky(conn, world):
    ids, _ = world
    inp = make_inputs(nights=3)
    note = scores_engine.load_muscle(conn, inp, ids["A"], date(2026, 10, 1), FRI, FakeSparky())
    assert inp.muscle[date(2026, 10, 10)]["volume_kg"] == 4 * 12 * 39 and "2 天" in note
    assert inp.muscle[date(2026, 10, 12)]["volume_kg"] == 4 * 12 * 7 + 4 * 15 * 32
    inp2 = make_inputs(nights=3)
    scores_engine.load_muscle(conn, inp2, ids["A"], date(2026, 10, 1), FRI,
                              FakeSparky(fail={"/v2/exercise-entries/history"}))
    assert inp2.muscle_error and not inp2.muscle
    inp3 = make_inputs(nights=3)
    assert "没连" in scores_engine.load_muscle(conn, inp3, ids["C"], date(2026, 10, 1), FRI) and inp3.muscle_error is None
