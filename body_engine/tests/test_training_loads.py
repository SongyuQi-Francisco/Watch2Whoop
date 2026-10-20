"""Issue #41: recommended working weights (training/loads.py), free-exercise-db import merged into the curated catalog with
restricted-variant families, self-hosted demo images (training/media.py, /app/media) and the muscle map's coverage."""
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, sparky_client
from body_engine.assistant.tools import run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.records import md_seed
from body_engine.training import catalog, catalog_data, loads, media, params as P, plans

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_TRAINING_LOADS", "body_test_training_loads")
STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"

BENCH = {"slug": "dumbbell_bench_press", "modality": "weight_reps", "category": "strength", "equipment": ["dumbbell"],
         "movement_pattern": "horizontal_push", "default_increment_kg": 2.5}


def sets(*pairs, rir=None):
    return [{"set": i + 1, "type": "Working Set", "reps": r, "weight_kg": w, "rir": rir} for i, (w, r) in enumerate(pairs)]


# ---------------------------------------------------------------- pure functions
def test_epley_round_trip_and_rounding():
    one_rm = loads.e1rm(20, 10, rir=2)              # 20 × (1 + 12/30)
    assert one_rm == pytest.approx(28.0)
    assert loads.weight_for(one_rm, 10, 2) == pytest.approx(20.0)
    assert loads.e1rm(20, 10) == loads.e1rm(20, 10, rir=P.LOAD_RIR_ASSUMED)
    assert loads.round_down(21.9, 2.5) == 20.0 and loads.round_down(22.5, 2.5) == 22.5
    assert loads.target_reps(8, 12) == 10 and loads.target_reps(None, 15) == 15


def test_recommend_order_progression_history_conversion_estimate():
    today = date(2026, 10, 21)
    hist = [{"date": "2026-10-18", "sets": sets((20, 10), (20, 10), (20, 9))}]
    # 1. the progression reminder wins, unchanged
    up = loads.recommend(BENCH, 8, 12, hist, {"next_weight_kg": 22.5}, day=today)
    assert up["kg"] == 22.5 and up["basis"] == "progression" and up["direction"] == "up" and up["per"] == "单只"
    # 2a. same rep range -> keep the last working weight (double progression)
    same = loads.recommend(BENCH, 8, 12, hist, day=today)
    assert same["kg"] == 20 and same["basis"] == "history" and same["last"]["reps"] == [10, 10, 9]
    # 2b. a heavier range -> convert through the e1RM, rounded down to the increment
    heavy = loads.recommend(BENCH, 4, 6, hist, day=today)
    expect = loads.round_down(loads.weight_for(loads.e1rm(20, 10, None), 5), 2.5)
    assert heavy["basis"] == "e1rm" and heavy["kg"] == expect and heavy["kg"] > 20
    # 2c. same range but last logged > 4 weeks ago: e1RM × 0.9 and never above the last weight
    stale = loads.recommend(BENCH, 8, 12, hist, day=today + timedelta(days=40))
    assert stale["basis"] == "e1rm" and stale["stale"] and stale["kg"] <= 20
    # 3. no history -> conservative estimate by body weight; sex unknown uses the female (lower) factors
    est_m = loads.recommend(BENCH, 8, 12, None, bw=90, sex="male", day=today)
    est_u = loads.recommend(BENCH, 8, 12, None, bw=90, sex=None, day=today)
    assert est_m["basis"] == "estimate" and est_m["label"] == "估算"
    assert est_m["kg"] == loads.round_down(90 * 0.5 * P.LOAD_START_FRACTION * 0.4, 2.5) == 10.0
    assert est_u["kg"] < est_m["kg"]
    assert loads.recommend(BENCH, 8, 12, None, bw=None, day=today) is None          # no body weight -> no guess
    # barbell estimates never go below the empty bar
    bar = {**BENCH, "slug": "x", "equipment": ["barbell"], "movement_pattern": "elbow_flexion"}
    assert loads.recommend(bar, 8, 12, None, bw=50, sex="female", day=today)["kg"] == 20.0
    # bodyweight / timed exercises
    assert loads.recommend({"modality": "bodyweight_reps"}, 10, 10, None)["basis"] == "bodyweight"
    assert loads.recommend({"modality": "duration"}, None, None, None) is None
    assert loads.recommend({"modality": "weight_reps", "category": "cardio"}, None, None, None) is None


def test_readiness_adjustment_and_short_text():
    ld = {"kg": 40.0, "basis": "history", "label": "同上次"}
    red = loads.apply_readiness(ld, "red")
    assert red["kg"] == 35.0 and red["planned_kg"] == 40.0 and red["readiness"] == "red"
    up = {"kg": 42.5, "basis": "progression", "direction": "up", "last": {"kg": 40.0}, "label": "按进阶"}
    assert loads.apply_readiness(up, "yellow")["kg"] == 40.0
    assert loads.apply_readiness(ld, "yellow") is ld and loads.apply_readiness(ld, "green") is ld
    assert loads.short_text({"kg": 12.0, "basis": "estimate"}) == "≈ 12 kg 估算"
    assert loads.short_text({"kg": None, "basis": "bodyweight", "label": "自重"}) == "自重"


def test_convert_free_db_equipment_and_hazard_families():
    conv = catalog.convert_free_exercise_db_entry
    assert conv({"id": "EZ", "name": "EZ-Bar Curl", "equipment": "e-z curl bar", "primaryMuscles": ["biceps"]})["equipment"] == ["ez curl bar"]
    assert conv({"id": "KB", "name": "Kettlebell Swing", "equipment": "kettlebells"})["equipment"] == ["kettlebell"]
    assert conv({"id": "B", "name": "Band Pull Apart", "equipment": "bands"})["modality"] == "reps_only"
    assert conv({"id": "Upright_Cable_Row", "name": "Upright Cable Row"})["family_slug"] == "upright_row"
    assert conv({"id": "x", "name": "Wide-Grip Pulldown Behind The Neck"})["family_slug"] == "behind_neck_pulldown"
    assert conv({"id": "x", "name": "Push Press - Behind the Neck"})["family_slug"] == "behind_neck_press"
    assert conv({"id": "x", "name": "Ring Dips"})["family_slug"] == "dips"
    assert conv({"id": "x", "name": "Jerk Dip Squat"})["family_slug"] is None
    assert conv({"id": "x", "name": "Seated Cable Rows"})["family_slug"] is None


def test_muscle_map_covers_every_catalog_muscle():
    js = (STATIC / "muscle-map.js").read_text(encoding="utf-8")
    regions = re.search(r"const REGIONS = \{(.*?)\};", js, re.S).group(1)
    keys = {k.strip() for k in re.findall(r'"?([a-z][a-z ]*)"?:\s*\[', regions)}
    assert set(catalog_data.MUSCLE_ZH) <= keys
    data = json.loads(re.search(r"const DATA = (\{.*?\});\n", js, re.S).group(1))
    drawn = {m for view in ("front", "back") for m, _pts in data[view]}
    assert {r for rs in re.findall(r"\[([^\]]*)\]", regions) for r in re.findall(r'"([a-z-]+)"', rs)} <= drawn
    assert "MIT" in js and (Path(__file__).resolve().parents[2] / "docs" / "LICENSES.md").read_text().count("react-body-highlighter")
    assert "muscle-map.js" in (STATIC / "index.html").read_text() and "muscle-map.js" in (STATIC / "sw.js").read_text()


def test_media_fetch_is_pinned_safe_and_resumable(tmp_path, monkeypatch):
    payload = json.dumps([{"id": "A", "name": "A", "images": ["A/0.jpg", "A/1.jpg", "../0.jpg"]}]).encode()
    calls = []

    def fake_get(path, timeout=60):
        calls.append(path)
        if path == "dist/exercises.json":
            return payload
        return b"\xff\xd8jpeg" if path.endswith("0.jpg") else b"<html>"

    monkeypatch.setattr(media, "_get", fake_get)
    with pytest.raises(ValueError, match="sha256"):        # upstream changed -> refuse
        media.fetch_json(tmp_path)
    monkeypatch.setattr(P, "FREE_DB_JSON_SHA256", __import__("hashlib").sha256(payload).hexdigest())
    src = media.fetch_json(tmp_path)
    assert src == tmp_path / "free-exercise-db" / P.FREE_DB_COMMIT / "exercises.json"
    res = media.fetch_images(src, tmp_path)
    assert res["downloaded"] == 1 and res["failed_count"] == 1 and res["skipped_unsafe_names"] == ["../0.jpg"]
    assert (tmp_path / "exercises" / "A" / "0.jpg").read_bytes().startswith(b"\xff\xd8")
    assert not (tmp_path / "exercises" / "A" / "1.jpg").exists() and not (tmp_path / "0.jpg").exists()
    calls.clear()
    media.fetch_json(tmp_path)
    assert media.fetch_images(src, tmp_path)["already_there"] == 1 and "exercises/A/0.jpg" not in calls


def test_image_urls_only_for_downloaded_files(tmp_path, monkeypatch):
    monkeypatch.setenv("BODY_MEDIA_DIR", str(tmp_path))
    monkeypatch.delenv("BODY_EXERCISE_IMAGE_BASE_URL", raising=False)
    (tmp_path / "exercises" / "X").mkdir(parents=True)
    (tmp_path / "exercises" / "X" / "0.jpg").write_bytes(b"\xff\xd8")
    assert catalog.image_urls(["X/0.jpg", "X/1.jpg", "../secret.jpg"]) == ["/app/media/exercises/X/0.jpg"]
    assert "github" not in catalog.image_base_url()
    monkeypatch.setenv("BODY_EXERCISE_IMAGE_BASE_URL", "https://cdn.example.com/ex")
    assert catalog.image_urls(["X/1.jpg"]) == ["https://cdn.example.com/ex/X/1.jpg"]


# ---------------------------------------------------------------- database
UPSTREAM = [
    {"id": "Dumbbell_Bench_Press", "name": "Dumbbell Bench Press", "equipment": "dumbbell", "primaryMuscles": ["chest"],
     "secondaryMuscles": ["triceps"], "instructions": ["Lie down."], "category": "strength",
     "images": ["Dumbbell_Bench_Press/0.jpg", "Dumbbell_Bench_Press/1.jpg"]},
    {"id": "Upright_Cable_Row", "name": "Upright Cable Row", "equipment": "cable", "primaryMuscles": ["traps"],
     "secondaryMuscles": ["shoulders"], "instructions": ["Pull up."], "category": "strength", "images": ["Upright_Cable_Row/0.jpg"]},
    {"id": "Spider_Curl", "name": "Spider Curl", "equipment": "dumbbell", "mechanic": "isolation", "force": "pull",
     "primaryMuscles": ["biceps"], "secondaryMuscles": [], "instructions": ["Curl."], "category": "strength",
     "images": ["Spider_Curl/0.jpg"]},
]


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
def world(dsn, tmp_path_factory):
    """A: owner (md_seed restrictions: upright_row banned), 90 kg, male, with an active 4-day plan;
    B: sees A's training + scores but not weight / exercises."""
    up = tmp_path_factory.mktemp("fedb") / "exercises.json"
    up.write_text(json.dumps(UPSTREAM), encoding="utf-8")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        res = catalog.seed_catalog(conn, up)
        assert res["merged_free_db"] == 1 and res["imported_free_db"] == 2
        ids = {n: conn.execute("INSERT INTO users (display_name, role, sex) VALUES (%s, %s, %s) RETURNING id",
                               (n, "owner" if n == "A" else "member", "male" if n == "A" else None)).fetchone()["id"]
               for n in ("A", "B")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids["B"], ["training", "scores"]))
        for ref, ex in md_seed.EXERCISES:
            conn.execute("""INSERT INTO exercise_meta (user_id, exercise_ref, name_zh, name_en, status, aliases, cues, reason)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                         (ids["A"], ref, ex["name_zh"], ex.get("name_en"), ex.get("status", "active"),
                          ex.get("aliases") or [], ex.get("cues"), ex.get("reason")))
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, agg, source)
                        VALUES (%s, %s, 'body_mass', 90, 'kg', 'avg', 'shortcut')""", (ids["A"], date.today() - timedelta(days=2)))
        conn.execute("""INSERT INTO reminders (user_id, date, rule_key, subject, level, title, message, evidence, rules_version)
                        VALUES (%s, %s, 'progression', 'lat:up', 'info', '下次加重：高位下拉 39 kg → 41.5 kg', '达标',
                                '{"exercise": "高位下拉（正手，略宽于肩）", "next_weight_kg": 41.5}'::jsonb, 'test')""",
                     (ids["A"], date.today()))
        plans.save_plan(conn, ids["A"], "4_day_upper_lower", sync_sparky=False)
    return ids, tok


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture()
def history(monkeypatch):
    """A's SparkyFitness history: dumbbell bench 20 kg × 10 (same range as the plan), leg press 100 kg × 5 (heavier range)."""
    loads.clear_history_cache()
    day = (date.today() - timedelta(days=3)).isoformat()
    sessions = [{"date": day, "name": "上肢", "notes": None, "exercises": [
        {"exercise_id": "sp-bench", "name": "哑铃平板卧推", "sets": [{"set": 0, "type": "Warm-up", "reps": 12, "weight_kg": 10}]
         + sets((20, 10), (20, 10), (20, 9))},
        {"exercise_id": "sp-legpress", "name": "Leg Press", "sets": sets((100, 5), (100, 5))},
    ]}]
    monkeypatch.setattr(sparky_client, "client_for_user", lambda conn, uid: object())
    monkeypatch.setattr(sparky_client, "training_log", lambda client, start, end: [s for s in sessions if start.isoformat() <= s["date"] <= end.isoformat()])
    monkeypatch.setattr(sparky_client, "weight", lambda client, start, end: [])
    yield sessions
    loads.clear_history_cache()


def _items(week):
    return {x["slug"]: x for d in week["days"] for p in d["planned"] for x in p["exercises"]}


def test_import_merges_curated_and_inherits_restrictions(conn, world):
    ids, _ = world
    bench = catalog.resolve(conn, "Dumbbell_Bench_Press")
    assert bench["slug"] == "dumbbell_bench_press" and bench["source"] == "curated"
    assert conn.execute("SELECT source_ref FROM exercise_catalog WHERE slug = 'dumbbell_bench_press'").fetchone()["source_ref"] == "Dumbbell_Bench_Press"
    row = conn.execute("SELECT * FROM exercise_catalog WHERE slug = 'upright_cable_row'").fetchone()
    assert row["family_slug"] == "upright_row" and "shoulder_ir" in row["joint_stress"]
    # the owner banned upright rows -> the imported variant is banned for them too; for someone without a restriction it is caution
    v = catalog.get_exercise(conn, ids["A"], "Upright Cable Row")
    assert v["status"] == "banned" and v["restriction_from"] == "upright_row"
    assert catalog.get_exercise(conn, ids["B"], "upright_cable_row")["status"] == "caution"
    # alternatives come from the curated overlay only
    alts = catalog.recommend_alternatives(conn, "dumbbell_bench_press", user_id=ids["A"], limit=10)
    assert alts and all(conn.execute("SELECT source FROM exercise_catalog WHERE slug = %s", (a["slug"],)).fetchone()["source"] == "curated"
                        for a in alts)
    # browsing without a query lists curated exercises before imported ones
    first = catalog.search(conn, user_id=ids["A"], limit=5)
    assert all(x["source"] == "curated" for x in first)
    assert catalog.search(conn, user_id=ids["A"], query="spider")[0]["slug"] == "spider_curl"


def test_week_plan_and_propose_plan_carry_loads(conn, world, history):
    ids, _ = world
    wk = plans.get_week_plan(conn, ids["A"], ids["A"], date.today())
    items = _items(wk)
    bench, lat, goblet, plank = items["dumbbell_bench_press"], items["lat_pulldown"], items["goblet_squat"], items["plank"]
    assert bench["load"]["basis"] == "history" and bench["load"]["kg"] == 20 and bench["load_text"] == "20 kg"
    assert lat["load"]["basis"] == "progression" and lat["load"]["kg"] == 41.5
    assert goblet["load"]["basis"] == "estimate" and goblet["load"]["kg"] == loads.round_down(90 * 0.15, 2.5)
    assert plank["load"] is None
    leg = items["leg_press"]   # 100 × 5 logged, plan asks 10–12 -> converted down
    assert leg["load"]["basis"] == "e1rm" and leg["load"]["kg"] < 100
    slim = plans.get_week_plan(conn, ids["A"], ids["A"], date.today(), detail=False)
    assert any(x.get("load") == "20 kg" for d in slim["days"] for p in d["planned"] for x in p["exercises"])
    prop = plans.propose_plan(conn, ids["A"], "3_day_ppl")
    assert prop["days"][0]["exercises"][0]["load"]["kg"] == 20   # dumbbell bench press
    stored = conn.execute("SELECT days FROM training_plans WHERE user_id = %s AND active", (ids["A"],)).fetchone()["days"]
    assert all("load" not in x for d in stored for x in d["exercises"])   # loads are computed, never stored


def test_viewer_without_weight_scope_gets_no_estimate(conn, world, history):
    ids, _ = world
    items = _items(plans.get_week_plan(conn, ids["B"], ids["A"], date.today()))
    assert items["dumbbell_bench_press"]["load"]["kg"] == 20
    assert items["goblet_squat"]["load"] is None


def test_history_unreadable_means_no_estimate(conn, world, monkeypatch):
    ids, _ = world
    loads.clear_history_cache()
    monkeypatch.setattr(sparky_client, "client_for_user", lambda conn, uid: object())

    def down(*_a, **_k):
        raise sparky_client.SparkyError("连不上 SparkyFitness（test）")
    monkeypatch.setattr(sparky_client, "training_log", down)
    wk = plans.get_week_plan(conn, ids["A"], ids["A"], date.today())
    items = _items(wk)
    assert "连不上" in wk["load_history_error"] and items["goblet_squat"]["load"] is None
    assert items["lat_pulldown"]["load"]["kg"] == 41.5   # the progression reminder still applies
    loads.clear_history_cache()


def test_exercise_detail_tool_and_http(dsn, world, history, tmp_path, monkeypatch):
    ids, tok = world
    monkeypatch.setenv("BODY_MEDIA_DIR", str(tmp_path))
    monkeypatch.delenv("BODY_EXERCISE_IMAGE_BASE_URL", raising=False)
    (tmp_path / "exercises" / "Dumbbell_Bench_Press").mkdir(parents=True)
    (tmp_path / "exercises" / "Dumbbell_Bench_Press" / "0.jpg").write_bytes(b"\xff\xd8demo")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        out = run_tool(conn, ids["A"], "butler", "get_exercise", {"user": "me", "exercise": "哑铃卧推"})
    assert out["load"]["kg"] == 20 and out["rep_range"] == [8, 12] and "image_urls" not in out
    assert out["recent"][0]["sets"] == ["20×10", "20×10", "20×9"]
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=2, open=True)
    try:
        c = TestClient(api.app, base_url="https://testserver")
        r = c.get("/v1/users/me/training/exercises/dumbbell_bench_press", headers={"Authorization": f"Bearer {tok['A']}"})
        assert r.status_code == 200
        body = r.json()
        assert body["image_urls"] == ["/app/media/exercises/Dumbbell_Bench_Press/0.jpg"] and body["load_text"] == "20 kg"
        img = c.get(body["image_urls"][0])
        assert img.status_code == 200 and img.content == b"\xff\xd8demo" and "immutable" in img.headers["cache-control"]
        assert c.get("/app/media/exercises/Nope/0.jpg").status_code == 404
        assert c.get("/app/index.html").status_code == 200
    finally:
        api.pool.close()


def test_today_card_applies_red_readiness(conn, world, history, monkeypatch):
    """Runs last: replaces A's plan with one bench-press day on every weekday and logs a red recovery score for today."""
    from body_engine.web import views
    ids, _ = world
    monkeypatch.setattr(sparky_client, "exercises", lambda client: [])
    monkeypatch.setattr(sparky_client, "workout_presets", lambda client: {})
    monkeypatch.setattr(sparky_client, "workout_plans", lambda client: [])
    every_day = [{"day_of_week": d, "name": "卧推日", "exercises": [{"slug": "dumbbell_bench_press", "sets": 3, "rep_low": 8, "rep_high": 12}]}
                 for d in range(1, 8)]
    plans.save_plan(conn, ids["A"], "custom", days=every_day, sync_sparky=False)
    today = views.local_today(conn, ids["A"])
    conn.execute("""INSERT INTO daily_scores (user_id, date, recovery, sleep_score, algo_version) VALUES (%s, %s, 20, 50, 'test')
                    ON CONFLICT (user_id, date) DO UPDATE SET recovery = 20""", (ids["A"], today))
    card = views.training_today(conn, ids["A"], ids["A"], today)
    ld = card["planned"][0]["exercises"][0]["load"]
    assert card["readiness"]["tier"] == "red" and ld["readiness"] == "red" and ld["planned_kg"] == 20 and ld["kg"] == 17.5
    # another day of the week: the plan weight, no readiness cut
    other = views.training_today(conn, ids["A"], ids["A"], today + timedelta(days=1))
    assert other["planned"][0]["exercises"][0]["load"]["kg"] == 20
