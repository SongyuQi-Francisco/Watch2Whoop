"""Today page: cookie login, rejection when not logged in, writes only to oneself, aggregate endpoints, family-member view (shares), static pages.

Uses a separate test database BODY_TEST_DB_NAME (default body_test_web), dropped and recreated on every run, never touching the production DB; skipped if it cannot connect.
SparkyFitness is faked with monkeypatch; no real service is contacted.
"""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.web import login_limit, session, views

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_web")
TZ = ZoneInfo("Australia/Sydney")
TODAY = datetime.now(TZ).date()
YDAY = TODAY - timedelta(days=1)
CSRF = {"X-Body-Web": "1"}


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # no password file, or the database is not running
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
    """A: has scores, sleep, reminders and journal items; B is shared scores and sleep by A; C has no shares at all."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"]
               for n in ("A", "B", "C")}
        tok = {"A": auth.create_token(conn, ids["A"], "web", ["read", "write"]),
               "A_ro": auth.create_token(conn, ids["A"], "web-ro", ["read"]),
               "B": auth.create_token(conn, ids["B"], "web", ["read", "write"]),
               "C": auth.create_token(conn, ids["C"], "web", ["read", "write"])}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["A"], ids["B"], ["scores", "sleep"]))
        conn.execute("""INSERT INTO journal_items (user_id, key, label_zh, kind, category) VALUES
                        (%s, 'alcohol', '喝酒', 'bool', 'diet'), (%s, 'alcohol', '喝酒', 'bool', 'diet')""",
                     (ids["A"], ids["B"]))
        a = ids["A"]
        # Scores: the day before yesterday has a recovery score; yesterday has strain but no muscle load; today has no sleep
        rows = [
            (TODAY - timedelta(days=2), 80, 85, 480, 500, 20, 9.5, 3000.0, 50, 40, 10, {}),
            (YDAY, 30, 70, 400, 520, 40, 12.3, None, 52, 48, 4, {"hr_coverage_low": "只有 2 小时有心率"}),
            (TODAY, None, None, None, None, None, None, None, 52, 48, 4,
             {"no_sleep": "没有手表睡眠数据", "no_raw_hr": "没有原始心率", "load_missing": "沿用前一天"}),
        ]
        for d, rec, ss, act, need, debt, strain, ml, ctl, atl, tsb, flags in rows:
            conn.execute("""INSERT INTO daily_scores (user_id, date, recovery, sleep_score, sleep_actual_min, sleep_need_min,
                              sleep_debt_min, strain, muscle_load, ctl, atl, tsb, flags, algo_version)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'test')""",
                         (a, d, rec, ss, act, need, debt, strain, ml, ctl, atl, tsb, Jsonb(flags)))
        # Sleep: the night ending with yesterday's wake-up (main sleep + nap)
        start = datetime.combine(YDAY, datetime.min.time(), TZ) + timedelta(hours=1, minutes=30)
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, deep_min, core_min,
                          rem_min, awake_min, is_main, source) VALUES
                        (%s, %s, %s, %s, 400, 60, 250, 90, 20, true, 'ow'),
                        (%s, %s, %s, %s, 30, null, null, null, null, false, 'ow')""",
                     (a, YDAY, start, start + timedelta(hours=7),
                      a, YDAY, start + timedelta(hours=13), start + timedelta(hours=13, minutes=30)))
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source) VALUES
                        (%s, %s, 'resting_heart_rate', 55, 'count/min', 'avg', 1, 'ow'),
                        (%s, %s, 'body_mass', 90, 'kg', 'avg', 1, 'ow')""", (a, YDAY, a, YDAY))
        conn.execute("""INSERT INTO reminders (user_id, date, rule_key, subject, level, title, message, evidence, rules_version)
                        VALUES (%s, %s, 'progression', 'ex1:40:up', 'info', '下次加重：卧推 40 kg → 42.5 kg', 'm',
                                %s, 'test'),
                               (%s, %s, 'readiness', 'red', 'warning', '恢复分红', 'm', '{}', 'test')""",
                     (a, TODAY, Jsonb({"exercise_id": "ex1", "next_weight_kg": 42.5}), a, TODAY))
        conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, app_exercise_id, reason, cues)
                        VALUES (%s, 'upright_row', '直立划船', 'banned', 'ex2', '练上斜方', '换侧平举')""", (a,))
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")  # https: only then is the Secure cookie sent back
    api.pool.close()


def login(client, token) -> TestClient:
    client.cookies.clear()
    r = client.post("/v1/web/login", json={"token": token})
    assert r.status_code == 200, r.text
    return client


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- login ----------

def test_login_sets_strict_cookie(client, world):
    ids, tok = world
    client.cookies.clear()
    assert client.post("/v1/web/login", json={"token": "be_nope_nope"}).status_code == 401
    r = client.post("/v1/web/login", json={"token": tok["A"]})
    assert r.status_code == 200 and r.json()["id"] == ids["A"]
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{session.COOKIE}=")
    for attr in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", f"Max-Age={session.SESSION_DAYS * 86400}"):
        assert attr.lower() in cookie.lower(), attr
    assert tok["A"] not in cookie  # the cookie holds a session ID, not the token
    me = client.get("/v1/web/me")
    assert me.status_code == 200 and me.json()["me"]["id"] == ids["A"]
    assert "set-cookie" in me.headers  # renewal
    assert [v["id"] for v in me.json()["viewable"]] == [ids["A"]]  # nobody shares with A


def test_unauthenticated_is_rejected(client, world):
    client.cookies.clear()
    for path in ("/v1/web/me", "/v1/users/me/today", "/v1/users/me/sleep/nights", "/v1/users/me/trends",
                 "/v1/users/me/journal", "/v1/users/me/reminders", "/v1/me"):
        assert client.get(path).status_code == 401, path
    client.cookies.set(session.COOKIE, "made-up-session")
    assert client.get("/v1/users/me/today").status_code == 401
    assert client.put(f"/v1/users/me/journal/{TODAY}/alcohol", json={"value": 1}, headers=CSRF).status_code == 401
    client.cookies.clear()


def test_cookie_writes_need_csrf_header_and_only_to_self(client, world):
    ids, tok = world
    login(client, tok["A"])
    path = f"/v1/users/me/journal/{TODAY}/alcohol"
    assert client.put(path, json={"value": 1}).status_code == 403          # no X-Body-Web
    r = client.put(path, json={"value": 1}, headers=CSRF)
    assert r.status_code == 200, r.text
    assert client.get(f"/v1/users/me/journal?start={TODAY}&end={TODAY}").json()[0]["value"] == 1
    assert client.delete(path, headers=CSRF).status_code == 204            # undo = back to no record
    assert client.get(f"/v1/users/me/journal?start={TODAY}&end={TODAY}").json() == []
    # writing to someone else: not allowed (regardless of shares)
    assert client.put(f"/v1/users/{ids['B']}/journal/{TODAY}/alcohol", json={"value": 1}, headers=CSRF).status_code == 403
    assert client.post(f"/v1/users/{ids['C']}/events", json={"occurred_at": "2026-10-01T10:00", "title": "x"},
                       headers=CSRF).status_code == 403
    # events: user's time zone
    r = client.post("/v1/users/me/events", json={"occurred_at": f"{TODAY}T08:30:00", "title": "测试事件",
                                                 "category": "symptom", "severity": 2}, headers=CSRF)
    assert r.status_code == 201, r.text
    assert datetime.fromisoformat(r.json()["occurred_at"].replace("Z", "+00:00")).astimezone(TZ).hour == 8
    # reminders: seen / restored
    rid = client.get("/v1/users/me/reminders").json()[0]["id"]
    assert client.patch(f"/v1/users/me/reminders/{rid}", json={"status": "seen"}, headers=CSRF).json()["status"] == "seen"
    assert client.patch(f"/v1/users/me/reminders/{rid}", json={"status": "new"}, headers=CSRF).json()["status"] == "new"


def test_read_only_token_session_cannot_write(client, world):
    _, tok = world
    login(client, tok["A_ro"])
    assert client.get("/v1/users/me/today").status_code == 200
    r = client.put(f"/v1/users/me/journal/{TODAY}/alcohol", json={"value": 1}, headers=CSRF)
    assert r.status_code == 403 and "write" in r.json()["detail"]


def test_logout_and_revoked_token_end_session(client, world, dsn):
    ids, tok = world
    login(client, tok["A"])
    sid = client.cookies.get(session.COOKIE)
    assert client.post("/v1/web/logout").status_code == 204
    client.cookies.set(session.COOKIE, sid)  # retry with the old cookie: the session is deleted
    assert client.get("/v1/users/me/today").status_code == 401
    # once the token is revoked, sessions exchanged with it become invalid immediately
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        extra = auth.create_token(conn, ids["C"], "to-revoke", ["read", "write"])
    login(client, extra)
    assert client.get("/v1/web/me").status_code == 200
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("UPDATE api_tokens SET revoked_at = now() WHERE name = 'to-revoke'")
    assert client.get("/v1/web/me").status_code == 401
    client.cookies.clear()


def test_bearer_still_works(client, world):
    client.cookies.clear()
    assert client.get("/v1/users/me/today", headers=bearer(world[1]["A"])).status_code == 200


# ---------- aggregate endpoints ----------

def test_today_scores_reasons_and_load_text(client, world):
    ids, tok = world
    d = client.get("/v1/users/me/today", headers=bearer(tok["A"])).json()
    assert d["date"] == TODAY.isoformat() and d["self"] is True
    sc = d["scores"]
    # Today's scores are all empty: the reason comes from flags, not blank or 0
    assert sc["today"]["recovery"] is None and sc["today_reasons"]["recovery"] == "没有手表睡眠数据"
    assert sc["today_reasons"]["strain"] == "没有原始心率"
    assert sc["recovery_zone"] is None
    assert sc["last_recovery"]["recovery"] == 30 and sc["last_recovery"]["zone"] == "red"
    assert sc["last_sleep"]["sleep_score"] == 70
    # Yesterday: strain present; muscle load empty with no flag -> default explanation
    assert sc["yesterday"]["strain"] == 12.3
    assert "SparkyFitness" in sc["yesterday_reasons"]["muscle_load"]
    assert "负荷平衡" in sc["load_text"] and "TSB +4" in sc["load_text"]
    assert sc["load_missing_days"] == 0   # The Today page shows it as a "N days missing" (「缺 N 天」) label; load_text is passed verbatim to the butler
    # The Today page only shows short statuses: recovery status word (no score today -> None), strain label
    assert sc["recovery_word"] is None and sc["load_status"] == {"key": "balanced", "label": "平衡"}
    # reminders: today's
    assert {r["rule_key"] for r in d["reminders"]} == {"progression", "readiness"}
    # SparkyFitness not connected: nutrition errors on its own; training (no plan) is an empty day that says why (#66 FB-4)
    assert d["nutrition"]["code"] == "sparky"
    assert d["training"]["planned"] == [] and d["training"]["sparky"]["state"] == "not_linked"


def test_reasons_and_zones():
    assert views.recovery_zone(67) == "green" and views.recovery_zone(66) == "yellow"
    assert views.recovery_zone(34) == "yellow" and views.recovery_zone(33) == "red"
    assert views.reasons(None, ("recovery",))["recovery"].startswith("这天还没有计算评分")
    r = views.reasons({"recovery": None, "flags": {"baseline_insufficient": "前 60 天只有 3 晚"}}, ("recovery",))
    assert r == {"recovery": "前 60 天只有 3 晚"}
    assert "疲劳偏高" in views.load_text({"ctl": 40, "atl": 80, "tsb": -40}, 0)
    assert "状态新鲜" in views.load_text({"ctl": 60, "atl": 40, "tsb": 20}, 2) and "2 天没有负荷数据" in views.load_text(
        {"ctl": 60, "atl": 40, "tsb": 20}, 2)


def test_short_status_labels():
    assert views.RECOVERY_WORD[views.recovery_zone(80)] == "适合高强度"
    assert views.RECOVERY_WORD[views.recovery_zone(20)] == "以恢复为主"
    assert views.load_status(None) is None and views.load_status({"tsb": None}) is None
    assert [views.load_status({"tsb": t})["label"] for t in (15, 0, -20, -40)] == ["新鲜", "平衡", "积累疲劳", "疲劳"]


def test_journal_frequent(client, world, dsn):
    ids, tok = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        u = ids["C"]
        for i in range(1, 9):
            conn.execute("INSERT INTO journal_items (user_id, key, label_zh, kind, category, sort) VALUES (%s, %s, %s, 'bool', 'diet', %s)",
                         (u, f"k{i}", f"项{i}", i))
        conn.execute("INSERT INTO journal_items (user_id, key, label_zh, kind, active, sort) VALUES (%s, 'off', '停用', 'bool', false, 0)", (u,))
        for k, days_ago, v in [("k3", 1, 1), ("k3", 2, 1), ("k3", 3, 1), ("k5", 1, 1), ("k5", 4, 1),
                               ("k7", 2, 0),            # logged as "no": not counted as frequent
                               ("k1", 40, 1),           # older than 30 days: not counted
                               ("off", 1, 1)]:          # disabled items don't appear
            conn.execute("INSERT INTO journal_entries (user_id, date, key, value, source) VALUES (%s, %s, %s, %s, 'test')",
                         (u, TODAY - timedelta(days=days_ago), k, v))
    login(client, tok["C"])
    r = client.get("/v1/users/me/journal/frequent")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [x["key"] for x in rows] == ["k3", "k5", "k1", "k2", "k4", "k6"]   # By count first, padded in default order when fewer than 6
    assert [x["count"] for x in rows[:3]] == [3, 2, 0]
    assert [x["key"] for x in client.get("/v1/users/me/journal/frequent?limit=8&days=60").json()][:3] == ["k3", "k5", "k1"]
    assert len(client.get("/v1/users/me/journal/frequent?limit=3").json()) == 3
    # Someone else's: needs a journal share (B only has scores / sleep from A)
    login(client, tok["B"])
    assert client.get(f"/v1/users/{ids['A']}/journal/frequent").status_code == 403
    assert client.get(f"/v1/users/{ids['C']}/journal/frequent").status_code == 403
    client.cookies.clear()
    assert client.get("/v1/users/me/journal/frequent").status_code == 401


def test_assistant_link_and_version(client, world, monkeypatch):
    """The link of the "Ask the butler" (「问管家」) button comes from BODY_ASSISTANT_URL (https only); None when not configured, and the page shows "butler not connected" (「管家未连接」)."""
    login(client, world[1]["A"])
    monkeypatch.delenv("BODY_ASSISTANT_URL", raising=False)
    me = client.get("/v1/web/me").json()
    assert me["assistant_url"] is None and me["version"]["web"] and me["version"]["api"]
    monkeypatch.setenv("BODY_ASSISTANT_URL", " https://claude.ai/code/session_abc ")
    assert client.get("/v1/web/me").json()["assistant_url"] == "https://claude.ai/code/session_abc"
    for bad in ("javascript:alert(1)", "http://claude.ai/code/x", "claude.ai/code/x"):
        monkeypatch.setenv("BODY_ASSISTANT_URL", bad)
        assert client.get("/v1/web/me").json()["assistant_url"] is None, bad
    client.cookies.clear()


def test_today_training_and_nutrition_with_fake_sparky(client, world, monkeypatch):
    ids, tok = world
    dow = TODAY.isoweekday() % 7
    monkeypatch.setattr(sparky_client, "client_for_user", lambda conn, uid: object())
    monkeypatch.setattr(sparky_client, "exercises", lambda c: [
        {"id": "ex1", "name": "卧推", "category": "strength", "primary_muscles": ["chest"], "description": None},
        {"id": "ex2", "name": "直立划船", "category": "strength", "primary_muscles": ["shoulders"], "description": None},
        {"id": "ex3", "name": "游泳", "category": "cardio", "primary_muscles": [], "description": "【注意】肩卡压换蛙泳"}])
    monkeypatch.setattr(sparky_client, "workout_presets", lambda c: {7: {"id": 7, "name": "上肢 A", "exercises": [
        {"exercise_id": "ex1", "name": "卧推", "rep_goal": 12, "sets": [
            {"set": i, "type": "Working Set", "reps": 8, "weight_kg": 40} for i in (1, 2, 3)]},
        {"exercise_id": "ex2", "name": "直立划船", "rep_goal": None, "sets": [{"set": 1, "type": "Working Set", "reps": 12}]}]}})
    monkeypatch.setattr(sparky_client, "workout_plans", lambda c: [{
        "id": 1, "name": "V3", "start_date": None, "end_date": None, "is_active": True, "schedule_type": "weekly",
        "assignments": [{"day_of_week": dow, "sort_order": 0, "workout_preset_id": 7, "workout_preset_name": "上肢 A"},
                        {"day_of_week": dow, "sort_order": 1, "workout_preset_id": None, "exercise_id": "ex3",
                         "exercise_name": "游泳"}]}])
    monkeypatch.setattr(sparky_client, "training_log", lambda c, s, e: [])
    monkeypatch.setattr(sparky_client, "food_entries", lambda c, s, e: [
        {"id": "f1", "date": TODAY.isoformat(), "time": "08:00", "food": "鸡蛋", "calories": 1200, "protein_g": 90,
         "carbs_g": 100, "fat_g": 30, "sugar_g": None}])
    monkeypatch.setattr(sparky_client, "goal_for_date", lambda c, d: {
        "name": "训练日", "calories": 2400, "protein_g": 160, "carbs_g": 255, "fat_g": 62})
    d = client.get("/v1/users/me/today", headers=bearer(tok["A"])).json()
    t = d["training"]
    assert [p["name"] for p in t["planned"]] == ["上肢 A", "游泳"]
    bench, upright = t["planned"][0]["exercises"]
    assert bench["sets_text"] == "3 × 8–12 · 40 kg"
    assert bench["suggestions"][0]["next_weight_kg"] == 42.5            # the weight-increase suggestion comes from the progression reminder
    assert upright["status"] == "banned" and upright["reason"] == "练上斜方"  # exercise_meta
    assert t["planned"][1]["exercises"][0]["status"] == "caution"       # no meta match: look at the SparkyFitness description
    assert "sparky_url" not in t and "sparky_url" not in d   # Single entry point: the front end doesn't jump to SparkyFitness
    n = d["nutrition"]
    assert n["intake"]["calories"] == 1200 and n["goal"]["calories"] == 2400 and n["logged"] is True
    assert n["intake"]["sugar_g"] is None and n["known"]["sugar_g"] == 0   # unknown, not 0
    assert n["goal"]["source"] == "sparkyfitness" and n["goal"]["added_sugar_max_g"] == 25   # no version yet: fallback
    # Record tab, training page: this week Monday–Sunday, today's plan in the right slot, suggestions and caution exercises
    w = client.get(f"/v1/users/me/training/week?date={TODAY}", headers=bearer(tok["A"])).json()
    mon = TODAY - timedelta(days=TODAY.weekday())
    assert w["start"] == mon.isoformat() and [x["date"] for x in w["days"]] == [(mon + timedelta(days=k)).isoformat() for k in range(7)]
    today_plan = w["days"][TODAY.weekday()]
    assert [p["name"] for p in today_plan["planned"]] == ["上肢 A", "游泳"]
    assert all(x["rest_day"] for k, x in enumerate(w["days"]) if k != TODAY.weekday())
    assert w["plan"]["name"] == "V3"
    assert [(x["name"], x["next_weight_kg"]) for x in w["suggestions"]] == [("卧推", 42.5)]
    assert {(x["name"], x["status"]) for x in w["cautions"]} == {("直立划船", "banned"), ("游泳", "caution")}
    assert client.get(f"/v1/users/{ids['A']}/training/week", headers=bearer(tok["B"])).json()["code"] == "forbidden"
    # B viewing A: no training / nutrition / reminders share -> those blocks are forbidden, scores as usual
    d = client.get(f"/v1/users/{ids['A']}/today", headers=bearer(tok["B"])).json()
    assert d["self"] is False and d["scores"]["today"] is not None
    for k in ("training", "nutrition", "reminders"):
        assert d[k]["code"] == "forbidden", k


def test_family_view_needs_share(client, world):
    ids, tok = world
    login(client, tok["B"])
    me = client.get("/v1/web/me").json()
    assert [(v["id"], v["self"]) for v in me["viewable"]] == [(ids["B"], True), (ids["A"], False)]
    assert me["viewable"][1]["scopes"] == ["scores", "sleep"]
    assert client.get(f"/v1/users/{ids['A']}/sleep/nights").status_code == 200
    assert client.get(f"/v1/users/{ids['A']}/journal").status_code == 403       # no journal share
    # family members can view but not write
    assert client.patch(f"/v1/users/{ids['A']}/reminders/1", json={"status": "seen"}, headers=CSRF).status_code == 403
    login(client, tok["C"])
    assert client.get("/v1/web/me").json()["viewable"] == [{"id": ids["C"], "display_name": "C", "self": True, "scopes": None}]
    for path in ("today", "sleep/nights", "trends"):
        assert client.get(f"/v1/users/{ids['A']}/{path}").status_code == 403, path
    client.cookies.clear()


def test_sleep_nights(client, world):
    _, tok = world
    d = client.get("/v1/users/me/sleep/nights?days=3", headers=bearer(tok["A"])).json()
    assert [n["date"] for n in d["nights"]] == [(TODAY - timedelta(days=k)).isoformat() for k in (2, 1, 0)]
    y = d["nights"][1]
    assert y["main"]["start"] == "01:30" and y["main"]["end"] == "08:30"      # user's time zone
    assert y["main"]["deep_min"] == 60 and y["nap_min"] == 30 and y["naps"][0]["start"] == "14:30"
    assert y["sleep_score"] == 70
    assert d["nights"][2]["main"] is None and d["nights"][2]["reason"] == "没有手表睡眠数据"


def _segs(start_local, parts):
    """Segments laid out consecutively from start_local as [(stage, minutes)], with UTC times (like OW returns), to test time zone conversion."""
    from datetime import timezone
    from body_engine.importers.common import Segment
    out, t = [], start_local.astimezone(timezone.utc)
    for stage, minutes in parts:
        out.append(Segment(t, t + timedelta(minutes=minutes), stage, "Apple Watch"))
        t += timedelta(minutes=minutes)
    return out


def test_shortcut_segments_reads_only_this_user_and_night(dsn, world):
    """Stage fallback reads shortcut_samples for this user and this night only (it used to re-parse the whole archive,
    ~2 s per Today page, and ignored the user)."""
    ids, _ = world
    night = datetime.combine(YDAY, datetime.min.time(), TZ) + timedelta(hours=1)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for who, stage, off in (("A", "Deep", 0), ("B", "REM", 0), ("A", "Core", -24 * 60)):   # B's night; A's night a day earlier
            s0 = night + timedelta(minutes=off)
            conn.execute("""INSERT INTO shortcut_samples (user_id, type, start_at, end_at, value_text, source, agg, upload)
                            VALUES (%s, 'Sleep', %s, %s, %s, 'Apple Watch', 'raw', 'test') ON CONFLICT DO NOTHING""",
                         (ids[who], s0, s0 + timedelta(minutes=60), stage))
        segs = views.shortcut_segments(conn, ids["A"], night - timedelta(minutes=30), night + timedelta(hours=7))
        assert [x.stage for x in segs] == ["deep"]
        conn.execute("DELETE FROM shortcut_samples WHERE upload = 'test'")


def test_today_sleep_hypnogram(client, world, monkeypatch):
    ids, tok = world
    start = datetime.combine(YDAY, datetime.min.time(), TZ) + timedelta(hours=1, minutes=30)   # main sleep 01:30-08:30
    calls = []

    def ow_fail(conn, owner_id, tz, a, b):
        calls.append("ow")
        raise RuntimeError("OW 没开")

    # Segments sticking out of the main sleep are clipped; adjacent same-stage segments (gap < 90 seconds) are merged
    sc = _segs(start - timedelta(minutes=20), [("awake", 20), ("core", 30), ("core", 15), ("deep", 60), ("rem", 25),
                                                    ("awake", 5), ("core", 265), ("awake", 30)])
    monkeypatch.setattr(views, "ow_segments", ow_fail)
    monkeypatch.setattr(views, "shortcut_segments", lambda *a: sc)
    d = client.get("/v1/users/me/today", headers=bearer(tok["A"])).json()["sleep"]
    assert calls == ["ow"] and d["stages_source"] == "shortcut"           # OW fails, fall back to the iOS Shortcut
    assert d["latest"] is True and d["date"] == YDAY.isoformat()           # no sleep yet today: use the latest night
    m = d["main"]
    assert (m["start"], m["end"], m["in_bed_min"], m["deep_min"]) == ("01:30", "08:30", 420, 60)
    st = d["stages"]
    assert [x["stage"] for x in st] == ["core", "deep", "rem", "awake", "core", "awake"]
    assert st[0] == {"stage": "core", "start": "01:30", "end": "02:15", "offset_min": 0.0, "minutes": 45.0}
    assert st[-1]["end"] == "08:30" and sum(x["minutes"] for x in st) == 420  # clipped to between falling asleep and waking up
    # Use OW when it has data (times are UTC, returned in the local time zone)
    monkeypatch.setattr(views, "ow_segments", lambda conn, o, tz, a, b: _segs(start, [("deep", 100), ("core", 320)]))
    d = client.get("/v1/users/me/today", headers=bearer(tok["A"])).json()["sleep"]
    assert d["stages_source"] == "ow" and [x["start"] for x in d["stages"]] == ["01:30", "03:10"]
    # neither has data: stages is empty, with the reason
    monkeypatch.setattr(views, "ow_segments", lambda *a: [])
    monkeypatch.setattr(views, "shortcut_segments", lambda *a: [])
    d = client.get("/v1/users/me/today", headers=bearer(tok["A"])).json()["sleep"]
    assert d["stages"] == [] and d["stages_source"] is None and d["stages_reason"]
    # Bedtime of the last 7 nights: night = wake date - 1, offset counted from 18:00 that evening (01:30 -> 450 minutes)
    assert d["recent"][-1] == {"date": YDAY.isoformat(), "night": (YDAY - timedelta(days=1)).isoformat(),
                               "bedtime": "01:30", "wake": "08:30", "bed_offset_min": 450}
    # B has the sleep share and can view; C has no shares -> 403
    assert client.get(f"/v1/users/{ids['A']}/today", headers=bearer(tok["B"])).json()["sleep"]["main"]["start"] == "01:30"
    assert client.get(f"/v1/users/{ids['A']}/today", headers=bearer(tok["C"])).status_code == 403


def test_hypnogram_overlap_and_tz():
    """When segments from different devices overlap, drawing continues from the end of the previous one; across DST it is still shown in the user's time zone."""
    from body_engine.importers.common import Segment
    tz = ZoneInfo("Australia/Sydney")
    from datetime import timezone
    a = datetime(2026, 10, 4, 1, 0, tzinfo=tz)   # the day DST starts (02:00 -> 03:00)
    at = lambda m: (a.astimezone(timezone.utc) + timedelta(minutes=m)).astimezone(tz)   # m minutes really elapsed, with the same ZoneInfo
    segs = [Segment(at(0), at(90), "core", "w"), Segment(at(60), at(120), "deep", "p")]
    out = views.hypnogram(segs, at(0), at(120), tz)
    assert [(x["stage"], x["start"], x["end"], x["minutes"]) for x in out] == [("core", "01:00", "03:30", 90.0), ("deep", "03:30", "04:00", 30.0)]


def test_trends(client, world):
    _, tok = world
    d = client.get("/v1/users/me/trends?days=7", headers=bearer(tok["A"])).json()
    assert len(d["dates"]) == 7 and d["dates"][-1] == TODAY.isoformat()
    rec = d["series"]["recovery"]
    assert rec["values"][-3:] == [80, 30, None] and rec["n"] == 2
    assert d["series"]["resting_hr"]["values"][-2] == 55
    # Without SparkyFitness, weight uses body_mass; the 7-day mean carries forward
    w = d["series"]["weight"]
    assert w["values"][-2] == 90 and w["avg7"][-1] == 90 and w["avg7"][0] is None


def test_trends_baseline_and_volume(client, world, dsn):
    """Resting heart rate / HRV carry a baseline of the previous 60 days (mean ± 1 standard deviation, none under 14 days); the muscle load series gives the "training volume"."""
    ids, tok = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        u = ids["B"]
        for k in range(1, 81):   # 80 days of resting heart rate: alternating between 50 / 54 early on, mean 52, standard deviation about 2
            d = TODAY - timedelta(days=k)
            conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                            VALUES (%s, %s, 'resting_heart_rate', %s, 'count/min', 'avg', 1, 'ow')""", (u, d, 50 if k % 2 else 54))
        conn.execute("""INSERT INTO daily_scores (user_id, date, muscle_load, flags, algo_version) VALUES (%s, %s, 4200, '{}', 'test')""",
                     (u, TODAY - timedelta(days=1)))
    d = client.get("/v1/users/me/trends?days=7", headers=bearer(tok["B"])).json()
    b = d["series"]["resting_hr"]["baseline"]
    assert len(b["mean"]) == 7 and b["mean"][-1] == 52.0
    assert 49.5 < b["lo"][-1] < 50.5 and 53.5 < b["hi"][-1] < 54.5
    assert d["series"]["muscle_load"]["values"][-2] == 4200
    assert "baseline" not in d["series"]["recovery"]
    # no baseline under 14 days
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("DELETE FROM daily_metrics WHERE user_id = %s AND date < %s", (ids["B"], TODAY - timedelta(days=10)))
    d = client.get("/v1/users/me/trends?days=7", headers=bearer(tok["B"])).json()
    assert d["series"]["resting_hr"]["baseline"]["mean"][-1] is None


def test_trends_sleep_quality_zones_vo2(client, world, dsn):
    """Sleep regularity (a sub-item in the score details), sleep efficiency (asleep / in bed), VO2max, heart rate zone distribution (weekly totals)."""
    ids, tok = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        a = ids["A"]
        conn.execute("""UPDATE daily_scores SET details = %s WHERE user_id = %s AND date = %s""",
                     (Jsonb({"sleep": {"consistency": 62}, "load": {"zone_minutes": [30, 20, 10, 5, 1]}}), a, YDAY))
        conn.execute("""UPDATE daily_scores SET details = %s WHERE user_id = %s AND date = %s""",
                     (Jsonb({"load": {"zone_minutes": [10, 0, 0, 0, 0]}}), a, TODAY - timedelta(days=2)))
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                        VALUES (%s, %s, 'vo2_max', 43.2, 'mL/min·kg', 'avg', 1, 'ow')""", (a, YDAY))
    d = client.get("/v1/users/me/trends?days=7", headers=bearer(tok["A"])).json()
    se = d["series"]
    assert se["sleep_consistency"]["values"][-2] == 62
    assert se["sleep_efficiency"]["values"][-2] == round(100 * 400 / 420, 1)   # main sleep 400 minutes / 7 hours in bed
    assert se["vo2max"]["values"][-2] == 43.2
    z = d["hr_zones"]
    assert z["total_min"] == [40, 20, 10, 5, 1] and len(z["labels"]) == 5
    assert sum(w["minutes"][0] for w in z["weeks"]) == 40
    # B only has scores / sleep shares: VO2max (metrics) isn't visible, regularity / efficiency are
    d = client.get(f"/v1/users/{ids['A']}/trends?days=7", headers=bearer(tok["B"])).json()
    assert "vo2max" not in d["series"] and "sleep_efficiency" in d["series"] and "sleep_consistency" in d["series"]


def test_trends_family_scope(client, world):
    ids, tok = world
    d = client.get(f"/v1/users/{ids['A']}/trends?days=7", headers=bearer(tok["B"])).json()
    assert "recovery" in d["series"] and "weight" not in d["series"] and "resting_hr" not in d["series"]
    assert "没有授权" in d["unavailable"]["weight"]


# ---------- static pages ----------

def test_month_days_calendar(client, world, dsn):
    """Look-back calendar: whether each day has scores / sleep / workouts / metrics, recovery score color, earliest day; authorized per block by shares."""
    ids, tok = world
    a = ids["A"]
    old = date(2019, 6, 12)   # Very old data, like from an Apple Health history import, must be reachable too
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, n, source)
                        VALUES (%s, %s, 'step_count', 8000, 'count', 'sum', 1, 'apple_export')""", (a, old))
        conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min)
                        VALUES (%s, 'ow', 'w-days-1', 'walking', '步行', %s, %s, %s, 30),
                               (%s, 'ow', 'w-days-2', 'walking', '步行', %s, %s, %s, 30)""",
                     (a, YDAY, datetime.combine(YDAY, datetime.min.time(), TZ), datetime.combine(YDAY, datetime.min.time(), TZ) + timedelta(minutes=30),
                      a, TODAY - timedelta(days=2), datetime.combine(TODAY, datetime.min.time(), TZ), datetime.combine(TODAY, datetime.min.time(), TZ)))
        conn.execute("UPDATE workouts SET excluded = 'implausible_duration' WHERE external_id = 'w-days-2'")
    login(client, tok["A"])
    r = client.get("/v1/users/me/days", params={"month": YDAY.strftime("%Y-%m")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["first"] == str(old) and d["hidden"] == [] and d["today"] == str(TODAY)
    by = {x["date"]: x for x in d["days"]}
    assert len(d["days"]) in (28, 29, 30, 31) and d["days"][0]["date"].endswith("-01")
    y = by[str(YDAY)]
    assert y["scores"] and y["sleep"] and y["training"] and y["metrics"] and y["has_data"]
    assert y["recovery"] == 30 and y["zone"] == "red"
    if str(TODAY - timedelta(days=2)) in by:   # excluded workouts don't count
        assert by[str(TODAY - timedelta(days=2))]["training"] is False and by[str(TODAY - timedelta(days=2))]["zone"] == "green"
    if str(TODAY + timedelta(days=1)) in by:
        assert by[str(TODAY + timedelta(days=1))]["future"] and not by[str(TODAY + timedelta(days=1))]["has_data"]
    o = {x["date"]: x for x in client.get("/v1/users/me/days", params={"month": "2019-06"}).json()["days"]}
    assert o["2019-06-12"]["metrics"] and o["2019-06-12"]["has_data"] and o["2019-06-12"]["recovery"] is None
    assert not o["2019-06-11"]["has_data"]
    assert client.get("/v1/users/me/days").json()["month"] == TODAY.strftime("%Y-%m")   # defaults to this month
    for bad in ("2026-13", "2026-1", "abc"):
        assert client.get("/v1/users/me/days", params={"month": bad}).status_code == 422, bad
    # Family member B only has scores + sleep: workouts and metrics are null, and the earliest day only counts what is visible
    login(client, tok["B"])
    d = client.get(f"/v1/users/{a}/days", params={"month": YDAY.strftime("%Y-%m")}).json()
    assert sorted(d["hidden"]) == ["metrics", "training"]
    y = {x["date"]: x for x in d["days"]}[str(YDAY)]
    assert y["scores"] and y["sleep"] and y["training"] is None and y["metrics"] is None
    assert d["first"] == str(min(TODAY - timedelta(days=2), YDAY))
    login(client, tok["C"])
    assert client.get(f"/v1/users/{a}/days").status_code == 403
    client.cookies.clear()
    assert client.get("/v1/users/me/days").status_code == 401


def test_static_pages(client):
    client.cookies.clear()
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/app/"
    r = client.get("/app/")
    assert r.status_code == 200 and "今天" in r.text
    # Today / Trends / Nutrition / Records / Me, plus the hidden Friends tab that replaces Nutrition when the flag
    # `friends` is on (app.js applyTabs): five are shown either way
    assert r.text.count('data-tab="') == 6 and r.text.count('class="tab" hidden>') == 1
    js = client.get("/app/app.js").text
    assert "sparky_url" not in js and "sparkyfitness" not in js.lower() and ":8444" not in js   # Single entry point: the front end doesn't jump to SparkyFitness (sparky_error of the read-only endpoints)
    assert "Jarvis" in r.text and 'id="ask"' in r.text           # a permanent "Jarvis" (AI assistant) entry
    assert 'id="chat"' not in r.text and 'type="file"' not in r.text  # No chat box or attachment upload of its own (a subscription can only be used in the official client)
    assert 'id="rec-cal"' in js and "/training/week?date=" in js and 'data-rsub' in js   # Record tab: training page (week, suggestions, history calendar)
    assert 'id="hdr-pick"' in r.text and 'id="back-today"' in r.text  # Look-back: the top date opens the month calendar, "back to today" (「回到今天」)
    assert "/days?month=" in js and "history.replaceState" in js
    assert 'DATED = new Set(["today", "trends", "food"])' in js       # the date picker also drives Trends (range end) and Food (that day's intake)
    assert "trends?days=${days}${q}" in js and "&end=${end}" in js and "foodDayHtml" in js
    # Issue #43: a recipe is logged to the day the 饮食 page shows (looking back at 10/18 used to log to today: no visible change),
    # and that day's cached intake / per-meal plan are dropped so the page redraws with the new entry
    assert "const day = viewDay();" in js and "body: { date: day, meal, servings" in js and "date: S.todayDate, meal, servings" not in js
    assert "const dropFoodDay = (day) =>" in js and "todayKey(day)" in js and "/meal-plan?date=" in js
    assert '[["diet", tr("饮食")], ["shopping", tr("采购")]]' in js and 'id="food-recipes"' in js   # 饮食 mode; recipes are a sub-page at the bottom
    assert "[365, tr(\"1年\")]" in js and "statusTiles" in js and "behaviorHtml" in js   # Trends: 1 week – 1 year, status cards, behavior insights
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    for f in ("app.js", "app.css", "sw.js", "manifest.webmanifest", "apple-touch-icon.png", "icon-192.png"):
        assert client.get(f"/app/{f}").status_code == 200, f
    assert client.get("/v1/users/me/today").headers["cache-control"] == "private, no-store"


def test_insecure_local_cookie_only_with_flag_and_localhost(monkeypatch):
    """WKWebView doesn't store Secure cookies on http://localhost: Secure is only dropped with the dev switch + localhost."""
    from starlette.requests import Request

    def req(host):
        return Request({"type": "http", "method": "GET", "path": "/", "scheme": "http",
                        "headers": [(b"host", host.encode())], "query_string": b""})

    monkeypatch.delenv("BODY_WEB_INSECURE_LOCAL_COOKIE", raising=False)
    assert session.cookie_secure(req("localhost:8130")) is True
    monkeypatch.setenv("BODY_WEB_INSECURE_LOCAL_COOKIE", "1")
    assert session.cookie_secure(req("localhost:8130")) is False
    assert session.cookie_secure(req("127.0.0.1:8100")) is False
    assert session.cookie_secure(req("mac.example.ts.net")) is True
    assert session.cookie_secure(None) is True


@pytest.fixture(autouse=True)
def _fresh_login_limit():
    """Every test counts login failures from zero (the test client's IP is always testclient)."""
    login_limit.reset()
    yield
    login_limit.reset()


def test_login_failures_are_rate_limited(client, world, monkeypatch):
    """After the same IP fails repeatedly up to the limit it gets 429 (with Retry-After), even a correct token is not allowed to try; recovers when the window passes; success resets the count."""
    login_limit.reset()
    monkeypatch.setattr(login_limit, "MAX_FAILURES", 3)
    for _ in range(3):
        assert client.post("/v1/web/login", json={"token": "be_wrong_wrong"}).status_code == 401
    r = client.post("/v1/web/login", json={"token": world[1]["A"]})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # Once the window has passed (shift the recorded time back) it can be tried again
    with login_limit._failures._lock:
        for q in login_limit._failures._events.values():
            for i in range(len(q)):
                q[i] -= login_limit.WINDOW_SECONDS + 1
    assert client.post("/v1/web/login", json={"token": world[1]["A"]}).status_code == 200
    assert login_limit._failures._events == {}   # reset after success
    login_limit.reset()


def test_login_limit_unit():
    login_limit.reset()
    for i in range(login_limit.MAX_FAILURES):
        assert login_limit.retry_after("1.2.3.4", now=100.0 + i) == 0
        login_limit.record_failure("1.2.3.4", now=100.0 + i)
    assert login_limit.retry_after("1.2.3.4", now=110.0) > 0
    assert login_limit.retry_after("5.6.7.8", now=110.0) == 0          # other IPs are unaffected
    assert login_limit.retry_after("1.2.3.4", now=100.0 + login_limit.WINDOW_SECONDS + login_limit.MAX_FAILURES) == 0
    login_limit.reset()


def test_login_code_one_time_device_token(client, world, dsn):
    """One-time login code: exchanged for a device-specific token + the same cookie; single use; expired / garbled gives 401; revoking the device token logs out."""
    from body_engine.web import login_codes
    ids, _ = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        code = login_codes.create(conn, ids["C"], "测试 iPhone", hours=1, scopes=["read"])
        expired = login_codes.create(conn, ids["C"], "旧的", hours=1)
        conn.execute("UPDATE web_login_codes SET expires_at = now() - interval '1 minute' WHERE code_sha256 = %s",
                     (login_codes._sha(expired),))
        with pytest.raises(ValueError):
            login_codes.create(conn, ids["C"], scopes=["admin"])
    assert len(code) == login_codes.LENGTH and set(code) <= set(login_codes.ALPHABET)

    client.cookies.clear()
    pretty = f"{code[:4].lower()}-{code[4:8]} {code[8:]}"  # case, spaces and - are all tolerated
    r = client.post("/v1/web/login-code", json={"code": pretty})
    assert r.status_code == 200, r.text
    assert "Secure" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    me = client.get("/v1/web/me")
    assert me.status_code == 200 and me.json()["me"]["id"] == ids["C"]
    assert me.json()["me"]["token_scopes"] == ["read"]

    client.cookies.clear()
    assert client.post("/v1/web/login-code", json={"code": code}).status_code == 401  # already used
    assert client.post("/v1/web/login-code", json={"code": expired}).status_code == 401
    assert client.post("/v1/web/login-code", json={"code": "ABCDEFGHJKLM"}).status_code == 401

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute("""SELECT t.name, t.scopes, t.id FROM web_login_codes c JOIN api_tokens t ON t.id = c.token_id
                              WHERE c.code_sha256 = %s""", (login_codes._sha(code),)).fetchone()
        assert row["name"] == "app:测试 iPhone" and row["scopes"] == ["read"]
        code2 = login_codes.create(conn, ids["C"], "x")
    assert client.post("/v1/web/login-code", json={"code": code2}).status_code == 200
    assert client.get("/v1/web/me").status_code == 200
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE api_tokens SET revoked_at = now() WHERE name = 'app:x'")
    assert client.get("/v1/web/me").status_code == 401
