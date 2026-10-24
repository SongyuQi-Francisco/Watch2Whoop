"""Muscle load map (rollout flag muscle_load): primary 100% / secondary 50% bookkeeping, the three time ranges (week starts Monday),
colour status against the weekly range, who may look (own data, a friend with the training share, nobody else), and nothing
changes while the flag is off (no endpoint, no tool, no card).
Watch workouts are the sports layer: minutes per muscle plus equivalent sets on the logged-set scale (they count for the colours and
ranges); duplicates and excluded / hidden ones are left out, a strength workout on a day with logged sets is not counted twice; the body
follows the profile's sex (none = male + a hint, no manual switch). Coverage of every workout type: test_muscle_coverage.py."""
import os
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.ai import cards
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags
from body_engine.training import goals, loads, muscle_load as ML, params as P, plans

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_MUSCLE_LOAD", "body_test_muscle_load")
STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"
CSRF = {"X-Body-Web": "1"}


# ---------------------------------------------------------------- pure: ranges and colours
def test_week_starts_on_monday_and_windows():
    wed = date(2026, 10, 21)   # a Wednesday
    assert ML.week_start(wed) == date(2026, 10, 19)
    assert ML.week_start(date(2026, 10, 25)) == date(2026, 10, 19)   # Sunday still belongs to that week
    assert ML.week_start(date(2026, 10, 26)) == date(2026, 10, 26)
    w = ML.windows(wed)
    assert w["today"] == (wed, wed)
    assert w["week"] == (date(2026, 10, 19), wed)
    assert w["last7"] == (date(2026, 10, 15), wed)
    assert w["prev_week"] == (date(2026, 10, 12), date(2026, 10, 18))


def test_status_against_the_weekly_range():
    low, high = P.WEEKLY_SETS_RANGE_DEFAULT
    assert (low, high) == (10, 20)
    assert ML.status_for("chest", 0) == "none"
    assert ML.status_for("chest", 0.5) == "low"
    assert ML.status_for("chest", low - 0.5) == "low"
    assert ML.status_for("chest", low) == "ok"
    assert ML.status_for("chest", high) == "ok"
    assert ML.status_for("chest", high + 0.5) == "high"
    lo2, hi2 = P.WEEKLY_SETS_RANGE["calves"]   # a muscle with its own range
    assert ML.status_for("calves", lo2) == "ok" and ML.status_for("calves", hi2 + 1) == "high"
    assert all(m in ML.CD.MUSCLE_ZH for m in P.WEEKLY_SETS_RANGE)   # every override names a real muscle


# ---------------------------------------------------------------- pure: primary 100 % / secondary 50 %
class StubIndex:
    rows = {"bench": {"slug": "bench", "name_zh": "卧推", "name_en": "Bench press", "primary_muscles": ["chest"],
                      "secondary_muscles": ["triceps", "shoulders"], "category": "strength"},
            "plank": {"slug": "plank", "name_zh": "平板支撑", "name_en": "Plank", "primary_muscles": ["abdominals"],
                      "secondary_muscles": [], "category": "strength"},
            "bike": {"slug": "bike", "name_zh": "单车", "name_en": "Bike", "primary_muscles": ["quadriceps"],
                     "secondary_muscles": [], "category": "cardio"}}

    def slug_for(self, app_id, *names):
        return next((s for s, r in self.rows.items() if r["name_zh"] in names), None)


def _w(day: date, name: str, sets: list[dict], **kw) -> dict:
    return {"date": day.isoformat(), "exercises": [{"name": name, "sets": sets, **kw}]}


def test_primary_full_secondary_half_and_volume():
    today = date(2026, 10, 21)
    sets = [{"reps": 10, "weight_kg": 60, "type": "warmup"}, {"reps": 8, "weight_kg": 80}, {"reps": 8, "weight_kg": 80},
            {"reps": 6, "weight_kg": 85}]
    a = ML.aggregate([_w(today, "卧推", sets)], StubIndex(), today)["stats"]
    assert a["chest"]["today"] == {"sets": 3.0, "volume_kg": 8 * 80 * 2 + 6 * 85}    # warm-up out, primary x 1
    assert a["triceps"]["today"]["sets"] == 1.5 and a["shoulders"]["today"]["sets"] == 1.5   # secondary x 0.5
    assert a["triceps"]["today"]["volume_kg"] == (8 * 80 * 2 + 6 * 85) * 0.5
    assert a["biceps"]["today"]["sets"] == 0


def test_bodyweight_sets_count_sets_but_no_kg_and_empty_or_cardio_does_not_count():
    today = date(2026, 10, 21)
    ws = [_w(today, "平板支撑", [{"duration_s": 60}, {"duration_s": 45}]),                   # timed sets: sets, no kg
          _w(today, "卧推", []),                                                             # no sets at all
          _w(today, "卧推", [{"reps": None, "weight_kg": 50}, {"reps": 0}]),                # nothing done
          _w(today, "单车", [{"duration_s": 1800}], category="cardio")]
    a = ML.aggregate(ws, StubIndex(), today)["stats"]
    assert a["abdominals"]["today"] == {"sets": 2.0, "volume_kg": 0.0}
    assert a["chest"]["week"]["sets"] == 0 and a["quadriceps"]["week"]["sets"] == 0


def test_ranges_and_last_trained():
    today = date(2026, 10, 21)   # Wednesday; the week began Monday 10-19
    days = {"today": today, "mon": date(2026, 10, 19), "sun_before": date(2026, 10, 18), "thu_before": date(2026, 10, 15),
            "wed_before": date(2026, 10, 14)}
    ws = [_w(d, "卧推", [{"reps": 10, "weight_kg": 50}]) for d in days.values()] + [_w(today + timedelta(days=1), "卧推", [{"reps": 10, "weight_kg": 50}])]
    agg = ML.aggregate(ws, StubIndex(), today)
    c = agg["stats"]["chest"]
    assert c["today"]["sets"] == 1                    # tomorrow's (future) workout is ignored
    assert c["week"]["sets"] == 2                     # Mon + Wed (today)
    assert c["last7"]["sets"] == 4                    # 10-15 .. 10-21: Thu + Sun + Mon + Wed
    assert c["prev_week"]["sets"] == 3                # 10-12 .. 10-18: Wed 14, Thu 15, Sun 18
    assert agg["last"]["chest"]["date"] == today
    ws2 = [_w(date(2026, 10, 17), "卧推", [{"reps": 5, "weight_kg": 100}])]
    last = ML.aggregate(ws2, StubIndex(), today)["last"]["chest"]
    assert last["date"] == date(2026, 10, 17) and last["exercises"][0]["top"] == "100×5"


def test_unmatched_exercise_is_reported_not_counted():
    today = date(2026, 10, 21)
    agg = ML.aggregate([_w(today, "神秘动作", [{"reps": 10, "weight_kg": 20}])], StubIndex(), today)
    assert agg["unmatched"] == ["神秘动作"] and agg["stats"]["chest"]["week"]["sets"] == 0
    # SparkyFitness's own primary muscle is the fallback when the catalog does not know the exercise
    agg = ML.aggregate([_w(today, "神秘动作", [{"reps": 10, "weight_kg": 20}], primary_muscles=["Biceps"])], StubIndex(), today)
    assert agg["stats"]["biceps"]["week"]["sets"] == 1 and not agg["unmatched"]


# ---------------------------------------------------------------- pure: sports layer (#189)
def test_sport_mapping_uses_catalog_muscles():
    for t, (prim, sec, res) in P.SPORT_MUSCLES.items():
        assert prim and set(prim) <= set(ML.CD.MUSCLE_ZH) and set(sec) <= set(ML.CD.MUSCLE_ZH), t
        assert not set(prim) & set(sec), t
        assert 0 < res <= 1, t
        if t in P.TYPE_NAMES:
            assert t in P.TYPE_NAMES_EN, t
    for t in ("basketball", "swimming", "running", "cycling", "hiking", "rowing_machine", "soccer", "tennis", "walking"):
        assert t in P.SPORT_MUSCLES   # the sports named in the issue
    assert (P.SPORT_PRIMARY_FACTOR, P.SPORT_SECONDARY_FACTOR) == (1.0, 0.5)


def test_sports_counted_by_minutes_and_equivalent_sets():
    today = date(2026, 10, 21)   # Wednesday; the week starts on the 19th
    sports = [{"date": today, "type": "basketball", "min": 74}, {"date": today - timedelta(days=5), "type": "swimming", "min": 40},
              {"date": today, "type": "core_training", "min": 30}, {"date": today - timedelta(days=1), "type": "basketball", "min": 60}]
    sp = ML.aggregate_sports(sports, today)
    prim, sec, res = P.SPORT_MUSCLES["basketball"]
    assert sp["stats"]["quadriceps"]["today"] == 74 and sp["stats"]["quadriceps"]["week"] == 134   # primary x 1
    assert sp["stats"]["hamstrings"]["today"] == 37 and "hamstrings" in sec                         # secondary x 0.5
    assert sp["stats"]["lats"]["week"] == 0 and sp["stats"]["lats"]["last7"] == 40                  # swimming last Friday: last 7 days only
    assert sp["stats"]["biceps"]["last7"] == 0
    # equivalent sets: minutes / 3 x resistance x intensity multiplier (no heart rate: 74 and 60 minutes of a sport are "moderate", x 1.0)
    assert sp["sets"]["quadriceps"]["today"] == pytest.approx(74 / 3 * res)
    assert sp["sets"]["hamstrings"]["today"] == pytest.approx(74 / 3 * res / 2)                      # secondary x 0.5
    assert sp["sets"]["quadriceps"]["week"] == pytest.approx(134 / 3 * res) and sp["sets"]["lats"]["prev_week"] == pytest.approx(40 / 3 * 0.30)   # last Friday is last week
    today_list = sp["overall"]["today"]
    assert [(x["type"], x["min"], x["mapped"]) for x in today_list] == [("basketball", 74, True), ("core_training", 30, True)]   # every type is mapped
    bm = sp["by_muscle"]["quadriceps"]["week"]
    assert [(x["type"], x["name"], x["min"], x["primary"]) for x in bm] == [("basketball", "篮球", 134, True)]
    assert sp["by_muscle"]["hamstrings"]["today"][0]["primary"] is False


def test_intensity_level_and_multiplier():
    hard = {"type": "cycling", "min": 60, "trimp": 150}
    easy = {"type": "cycling", "min": 60, "trimp": 20}
    assert ML.intensity_level(hard) == "hard" and ML.intensity_level(easy) == "light"
    assert ML.sport_load(hard)["muscles"]["quadriceps"][0] == pytest.approx(60 / 3 * 0.25 * P.INTENSITY_MULT["hard"])
    assert ML.sport_load(easy)["muscles"]["quadriceps"][0] == pytest.approx(60 / 3 * 0.25 * P.INTENSITY_MULT["light"])
    assert ML.intensity_level({"type": "cycling", "min": 3, "trimp": 150}) == "light"           # a 3-minute record is always light
    est = {"type": "walking", "min": 40, "avg_hr": 150, "hr_max": 190}                           # no TRIMP: estimated from the average heart rate
    assert ML.intensity_level(est) in ("moderate", "hard")


def test_strength_without_logged_sets_is_spread_over_the_target():
    today = date(2026, 10, 21)
    w = {"date": today, "type": "strength_training", "min": 60}
    whole = ML.aggregate_sports([w], today)                                   # no plan: the whole-body mapping
    sets = 60 / P.SET_CYCLE_MIN
    per = sets * P.STRENGTH_PRIMARIES_PER_SET / 7
    assert whole["sets"]["chest"]["today"] == pytest.approx(per) and whole["sets"]["biceps"]["today"] == pytest.approx(per / 2)
    assert all(whole["sets"][m]["today"] > 0 for m in ("chest", "lats", "quadriceps", "glutes", "abdominals", "calves"))
    legs = ML.aggregate_sports([w], today, strength_targets=lambda d: (["quadriceps", "glutes"], ["calves"]))   # the day's plan: legs
    assert legs["sets"]["quadriceps"]["today"] == pytest.approx(sets * 1.5 / 2) and legs["sets"]["calves"]["today"] == pytest.approx(sets * 1.5 / 4)
    assert legs["sets"]["chest"]["today"] == 0
    logged = ML.aggregate_sports([w], today, logged_days={today})            # its sets are logged: not counted again
    assert all(logged["sets"][m]["today"] == 0 for m in ML.CD.MUSCLE_ZH) and logged["overall"]["today"] == {} or logged["overall"]["today"] == []
    other = ML.aggregate_sports([{**w, "type": "cycling"}], today, logged_days={today})   # a cycle ride that day still counts
    assert other["sets"]["quadriceps"]["today"] > 0


# ---------------------------------------------------------------- database: endpoints, permissions, flag
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
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, role, sex) VALUES (%s, %s, %s) RETURNING id",
                               (n, "owner" if n == "A" else "member", "female" if n == "A" else None)).fetchone()["id"] for n in "ABC"}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["training"]))
        conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, status, reason)
                        VALUES (%s, 'upright_row', '直立划船', 'banned', '肩峰撞击')""", (ids["A"],))
        today = plans._user_today(conn, ids["A"])
        for u in ids.values():
            flags.set_flag(conn, "muscle_load", "all", actor="test")
    flags.invalidate()
    return ids, tok, today


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture()
def sessions(monkeypatch, world):
    """SparkyFitness history: bench-type sets today, a lat pulldown last week."""
    _, _, today = world
    data = [
        {"id": "1", "date": today.isoformat(), "name": "上肢", "exercises": [
            {"exercise_id": None, "name": "x", "slug": "machine_chest_press", "category": "strength",
             "sets": [{"reps": 10, "weight_kg": 25, "type": "warmup"}, {"reps": 10, "weight_kg": 40}, {"reps": 8, "weight_kg": 45}]}]},
        {"id": "2", "date": (ML.week_start(today) - timedelta(days=3)).isoformat(), "name": "背", "exercises": [
            {"exercise_id": None, "name": "x", "slug": "lat_pulldown", "category": "strength",
             "sets": [{"reps": 10, "weight_kg": 50}, {"reps": 10, "weight_kg": 50}]}]},
    ]
    monkeypatch.setattr(ML, "_sparky", lambda conn, uid, start, end: ([s for s in data if start.isoformat() <= s["date"] <= end.isoformat()], "ok"))
    return data


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", **CSRF}


def _set_flag(dsn, mode):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        flags.set_flag(c, "muscle_load", mode, actor="test")
    flags.invalidate()


def test_compute_from_the_catalog(conn, world, sessions):
    ids, _, today = world
    m = {x["key"]: x for x in ML.compute(conn, ids["A"], today)["muscles"]}
    idx = loads.Index(conn, ids["A"])
    row = idx.rows["machine_chest_press"]
    assert "chest" in row["primary_muscles"]
    working = 2   # the warm-up is out
    for k in row["primary_muscles"]:
        assert m[k]["ranges"]["today"]["sets"] == working
    for k in row["secondary_muscles"]:
        if k not in row["primary_muscles"]:
            assert m[k]["ranges"]["today"]["sets"] == working * 0.5
    assert m["chest"]["ranges"]["today"]["volume_kg"] == 10 * 40 + 8 * 45
    assert m["chest"]["trained_today"] and m["chest"]["status"]["week"] == "low" and m["chest"]["last"]["date"] == today.isoformat()
    lats = m["lats"]
    assert lats["prev_week"]["sets"] == 2 and lats["ranges"]["today"]["sets"] == 0 and not lats["trained_today"]
    assert lats["delta_sets"] == lats["ranges"]["week"]["sets"] - 2


def test_endpoint_permissions_and_sex(client, world, sessions, dsn):
    ids, tok, today = world
    _set_flag(dsn, "all")
    own = client.get("/v1/users/me/training/muscle-load", headers=H(tok["A"]))
    assert own.status_code == 200
    body = own.json()
    assert body["sex"] == "female" and body["goals"]["max_focus"] == goals.MAX_FOCUS
    # a friend with the training share sees the numbers but not the goals; a stranger sees nothing
    friend = client.get(f"/v1/users/{ids['A']}/training/muscle-load", headers=H(tok["B"]))
    assert friend.status_code == 200 and "goals" not in friend.json()
    assert friend.json()["muscles"] == body["muscles"]
    assert client.get(f"/v1/users/{ids['A']}/training/muscle-load", headers=H(tok["C"])).status_code in (403, 404)
    assert client.get(f"/v1/users/{ids['A']}/training/muscle-load/chest", headers=H(tok["C"])).status_code in (403, 404)
    assert client.get(f"/v1/users/{ids['A']}/training/goals", headers=H(tok["B"])).status_code == 403
    assert client.put(f"/v1/users/{ids['A']}/training/goals", json={"focus_muscles": ["chest"]}, headers=H(tok["B"])).status_code in (403, 404)


def test_detail_recommends_without_banned_exercises(client, world, sessions, dsn):
    ids, tok, _ = world
    _set_flag(dsn, "all")
    r = client.get("/v1/users/me/training/muscle-load/shoulders", headers=H(tok["A"]))
    assert r.status_code == 200
    d = r.json()
    assert d["key"] == "shoulders" and d["recommended"] and "upright_row" not in [x["slug"] for x in d["recommended"]]
    assert all(x["status"] != "banned" for x in d["recommended"])
    assert client.get("/v1/users/me/training/muscle-load/nope", headers=H(tok["A"])).status_code == 404


def test_goals_saved_and_written_to_memory(client, world, dsn):
    ids, tok, _ = world
    _set_flag(dsn, "all")
    r = client.put("/v1/users/me/training/goals", json={"focus_muscles": ["lats", "shoulders"], "physique_goal": "改善圆肩，背更厚"}, headers=H(tok["A"]))
    assert r.status_code == 200 and r.json()["focus_muscles"] == ["lats", "shoulders"]
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        mem = c.execute("SELECT value FROM assistant_profile WHERE user_id = %s AND key = 'physique_goal'", (ids["A"],)).fetchone()
    assert mem["value"] == "改善圆肩，背更厚"
    assert client.put("/v1/users/me/training/goals", json={"focus_muscles": ["nope"]}, headers=H(tok["A"])).status_code == 422
    too_many = list(goals.CD.MUSCLE_ZH)[: goals.MAX_FOCUS + 1]
    assert client.put("/v1/users/me/training/goals", json={"focus_muscles": too_many}, headers=H(tok["A"])).status_code == 422


def test_ai_tool_and_card(conn, world, sessions, dsn):
    ids, tok, today = world
    _set_flag(dsn, "all")
    out = run_tool(conn, ids["A"], "test", "get_muscle_load", {"range": "week"})
    rows = {r["key"]: r for r in out["muscles"]}
    assert rows["chest"]["status"] == "low" and out["focus_muscles"] == ["lats", "shoulders"] and out["physique_goal"]
    # a friend asking about A's muscles gets the numbers but not A's goals
    friend = run_tool(conn, ids["B"], "test", "get_muscle_load", {"user": str(ids["A"])})
    assert "focus_muscles" not in friend and friend["muscles"]
    card = cards.build(conn, ids["A"], {"type": "muscle", "range": "today"})
    assert card["type"] == "muscle" and card["sex"] == "female" and {m["key"] for m in card["muscles"]} == set(ML.CD.MUSCLE_ZH)
    assert "muscle" in cards.summary(card) or "肌肉" in cards.summary(card)


def test_flag_off_changes_nothing(client, conn, world, sessions, dsn):
    ids, tok, _ = world
    _set_flag(dsn, "off")
    for url in ("/muscle-load", "/muscle-load/chest", "/goals"):
        assert client.get(f"/v1/users/me/training{url}", headers=H(tok["A"])).status_code == 404
    assert client.put("/v1/users/me/training/goals", json={"focus_muscles": []}, headers=H(tok["A"])).status_code == 404
    with pytest.raises(flags.FlagOff):
        run_tool(conn, ids["A"], "test", "get_muscle_load", {})
    with pytest.raises(ValueError):
        cards.build(conn, ids["A"], {"type": "muscle"})
    # the plain training endpoints still answer as before
    assert client.get("/v1/users/me/training/calendar", headers=H(tok["A"])).status_code == 200
    _set_flag(dsn, "all")


def test_page_and_imported_rows_counted_once(conn, world, monkeypatch):
    """#161: a day can hold the page's own row (its submitted sets reach SparkyFitness, a draft is not counted) and a Hevy-imported
    row. Imported sets are counted once — also when loads.load_history (last session / recommended weights) has just filled the
    shared SparkyFitness cache for the very same window, which must therefore never contain the imported sets."""
    import json
    from body_engine import sparky_client
    ids, _, today = world
    c = ids["C"]
    submitted = [{"id": "s1", "date": today.isoformat(), "name": "推", "exercises": [
        {"exercise_id": None, "name": "x", "slug": "machine_chest_press", "category": "strength",
         "sets": [{"reps": 10, "weight_kg": 40}, {"reps": 8, "weight_kg": 45}]}]}]
    monkeypatch.setattr(sparky_client, "client_for_user", lambda conn, uid: object())
    monkeypatch.setattr(sparky_client, "training_log", lambda client, start, end: [dict(s) for s in submitted])
    conn.execute("""INSERT INTO training_session_logs (user_id, date, source, exercises, status) VALUES (%s, %s, 'page', %s::jsonb, 'draft')""",
                 (c, today, json.dumps([{"slug": "barbell_back_squat", "sets": [{"reps": 5, "weight_kg": 100, "done": True}]}])))
    conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min)
                    VALUES (%s, 'hevy', 'h1', 'strength_training', 'Pull', %s, now() - interval '1 hour', now(), 50)""", (c, today))
    conn.execute("""INSERT INTO training_session_logs (user_id, date, source, exercises, status) VALUES (%s, %s, 'hevy', %s::jsonb, 'imported')""",
                 (c, today, json.dumps([{"workout": "h1", "slug": "lat_pulldown", "name": "Lat Pulldown (Cable)", "category": "strength",
                                         "sets": [{"set": 1, "type": "warmup", "reps": 12, "weight_kg": 30},
                                                  {"set": 2, "type": "normal", "reps": 10, "weight_kg": 50},
                                                  {"set": 3, "type": "dropset", "reps": 10, "weight_kg": 40}]}])))
    try:
        start = min(today - timedelta(days=P.MUSCLE_LOAD_BASELINE_DAYS - 1), ML.windows(today)["prev_week"][0])
        monkeypatch.setattr(P, "LOAD_LOOKBACK_DAYS", (today - start).days)   # same cache key as the muscle map
        loads.clear_history_cache(c)
        hist, err, linked = loads.load_history(conn, c, today)
        assert err is None and linked and sorted(s["id"] for s in hist) == ["hevy:h1", "s1"]
        assert all(s.get("source") != "hevy" for s in loads._HISTORY_CACHE[(conn.info.dbname, c, start, today)][1])
        m = {x["key"]: x for x in ML.compute(conn, c, today)["muscles"]}
        assert m["lats"]["ranges"]["today"]["sets"] == 2                       # warm-up out, counted once
        assert m["lats"]["ranges"]["today"]["volume_kg"] == 10 * 50 + 10 * 40
        assert m["chest"]["ranges"]["today"]["sets"] == 2                      # the submitted page sets (via SparkyFitness)
        assert m["quadriceps"]["ranges"]["today"]["sets"] == 0                 # the unsubmitted page draft
        # read again from the warm cache: still once
        assert {x["key"]: x for x in ML.compute(conn, c, today)["muscles"]}["lats"]["ranges"]["today"]["sets"] == 2
    finally:
        conn.execute("DELETE FROM training_session_logs WHERE user_id = %s", (c,))
        conn.execute("DELETE FROM workouts WHERE user_id = %s", (c,))
        conn.commit()
        loads.clear_history_cache(c)


# ---------------------------------------------------------------- sports from the workouts table, sex from the profile (#189)
def _workout(conn, uid, ext, type_, day, minutes, **kw):
    cols = {"user_id": uid, "source": "ow", "external_id": ext, "type": type_, "name_zh": P.TYPE_NAMES.get(type_, type_),
            "local_date": day, "duration_min": minutes, **kw}
    names = ", ".join(cols)
    conn.execute(f"""INSERT INTO workouts ({names}, start_at, end_at)
                     VALUES ({", ".join(["%s"] * len(cols))}, now() - interval '2 hour', now() - interval '1 hour') RETURNING id""",
                 list(cols.values()))
    return conn.execute("SELECT id FROM workouts WHERE user_id = %s AND external_id = %s", (uid, ext)).fetchone()["id"]


def test_sports_layer_from_workouts(conn, world, sessions):
    """Sport minutes and equivalent sets come from workouts (not duplicates, not excluded); a strength workout on a day with logged sets
    is not counted again, one on a day without logged sets is."""
    ids, _, today = world
    a = ids["A"]
    before = {x["key"]: x for x in ML.compute(conn, a, today)["muscles"]}
    try:
        kept = _workout(conn, a, "bb1", "basketball", today, 74)
        _workout(conn, a, "bb1-copy", "basketball", today, 74, dup_of=kept)            # the export.xml copy
        _workout(conn, a, "sw1", "swimming", today, 40, excluded="user_hidden")        # hidden by the user
        _workout(conn, a, "st1", "strength_training", today, 60)                       # today has logged sets: counted through them
        _workout(conn, a, "yg1", "core_training", today, 30)
        full = ML.compute(conn, a, today)
        m = {x["key"]: x for x in full["muscles"]}
        assert m["quadriceps"]["sport_min"] == {"today": 74, "week": 74, "last7": 74}
        assert m["hamstrings"]["sport_min"]["week"] == 37 and m["lats"]["sport_min"]["week"] == 0
        res = P.SPORT_MUSCLES["basketball"][2]
        q = m["quadriceps"]["ranges"]["today"]
        assert q["logged_sets"] == before["quadriceps"]["ranges"]["today"]["logged_sets"] and q["sport_sets"] == pytest.approx(74 / 3 * res, abs=0.06)
        assert q["sets"] == pytest.approx(q["logged_sets"] + q["sport_sets"], abs=0.06)   # one scale: the colours count both
        assert m["quadriceps"]["trained_today"] and m["quadriceps"]["status"]["week"] != "none"
        c = m["chest"]["ranges"]["today"]   # the chest press sets of the fixture stay as they were; the strength watch workout adds nothing
        assert c["sets"] == before["chest"]["ranges"]["today"]["sets"] and c["sport_sets"] == 0
        assert [(x["type"], x["min"], x["mapped"]) for x in full["sports"]["week"]] == [("basketball", 74, True), ("core_training", 30, True)]
        d = ML.detail(conn, a, "quadriceps", today)
        assert [(x["type"], x["name"], x["min"], x["primary"]) for x in d["sports"]["today"]] == [("basketball", "篮球", 74, True)]
        out = run_tool(conn, a, "test", "get_muscle_load", {"range": "today"})
        rows = {r["key"]: r for r in out["muscles"]}
        assert rows["quadriceps"]["sport_min"] == 74 and rows["lats"]["sport_min"] is None
        assert {"name": "篮球", "min": 74, "estimated": False} in out["sports"] and "sport_min" in out["note"]
        card = cards.build(conn, a, {"type": "muscle", "range": "week"})
        assert {x["key"]: x["sport_min"] for x in card["muscles"]}["quadriceps"] == 74
        assert {"type": "basketball", "name": "篮球", "min": 74} in card["sports"] and "篮球 74 分钟" in cards.summary(card)
        # a watch strength workout on a day without logged sets counts (whole body, no plan)
        day = today - timedelta(days=2) if ML.week_start(today) <= today - timedelta(days=2) else today
        if day != today:
            _workout(conn, a, "st2", "strength_training", day, 45)
            m2 = {x["key"]: x for x in ML.compute(conn, a, today)["muscles"]}
            assert m2["chest"]["ranges"]["week"]["sport_sets"] > 0 and m2["lats"]["ranges"]["week"]["sport_sets"] > 0
            assert m2["chest"]["ranges"]["today"]["sport_sets"] == 0
    finally:
        conn.execute("DELETE FROM workouts WHERE user_id = %s", (a,))
        conn.commit()


def test_sex_follows_the_profile(client, conn, world, sessions, dsn):
    """No manual switch: A's profile says female; B has none -> null (the page draws the male body with a hint) and the
    Jarvis card offers 个人资料."""
    ids, tok, _ = world
    _set_flag(dsn, "all")
    assert client.get("/v1/users/me/training/muscle-load", headers=H(tok["A"])).json()["sex"] == "female"
    assert client.get("/v1/users/me/training/muscle-load", headers=H(tok["B"])).json()["sex"] is None
    assert cards.build(conn, ids["B"], {"type": "muscle"})["actions"][-1] == {"kind": "open", "page": "profile"}
    assert {"kind": "open", "page": "profile"} not in cards.build(conn, ids["A"], {"type": "muscle"})["actions"]
    js = (STATIC / "muscle-load.js").read_text(encoding="utf-8")
    assert "data-ms" not in js and 'tr("男")' not in js and "S.sex" not in js             # the 男 | 女 switch is gone
    assert "BodyProfile?.open()" in js and 'tr("在个人资料里设置性别")' in js
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'if (page === "profile") { window.BodyProfile?.open(); return; }' in app
